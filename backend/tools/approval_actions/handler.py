"""Step Functions worker for the durable human-approval workflow (NOT a Gateway tool).

The state machine owns *time*; this function owns the small amount of state each step must touch.

PLAN            build the wait/remind/escalate/expire schedule for a review task
REGISTER_TOKEN  store the current task token so a reviewer decision can complete the wait (race-safe)
REMIND          nudge the current reviewers (EventBridge event -> SNS)
ESCALATE        raise the task one level (tier, priority, audience) and notify

Every step is idempotent: Step Functions retries Lambda errors, and EventBridge is at-least-once.
Task tokens are credentials for the workflow: they are never logged and never returned to a client.
"""
import json
import os
from datetime import UTC, datetime, timedelta

import boto3
from botocore.exceptions import ClientError
from obs import emit_metric, log, observed

_ddb = boto3.resource("dynamodb")
_sfn = boto3.client("stepfunctions")
_events = boto3.client("events")

REVIEWS = os.environ.get("REVIEWS_TABLE", "reviews")
BUS = os.environ.get("EVENT_BUS_NAME", "default")
TIME_SCALE = float(os.environ.get("WORKFLOW_TIME_SCALE", "1.0"))  # <1 compresses time for dev/smoke tests
FINAL_GRACE_HOURS = float(os.environ.get("EXPIRY_GRACE_HOURS", "24"))
SOURCE = "uw.underwriting"

SLA_HOURS = {"STANDARD": 24, "SENIOR": 8, "COMMITTEE": 72}
ESCALATION_PATH = {"STANDARD": "SENIOR", "SENIOR": "SENIOR", "COMMITTEE": "COMMITTEE"}  # never down, never auto-up past the committee
AUDIENCE = {1: "tier-lead", 2: "head-of-credit"}


def build_schedule(tier: str, sla_hours: float, scale: float = 1.0, final_grace_hours: float = 24.0) -> list[dict]:
    """Ladder: remind at 50% of the SLA, escalate at 100%, remind and escalate again within the next tier's SLA,
    then expire after a final grace period. Expiry never approves or declines: it refers the case."""
    next_sla = SLA_HOURS.get(ESCALATION_PATH.get(tier, tier), sla_hours)
    steps = [(0.5 * sla_hours, "REMIND"), (0.5 * sla_hours, "ESCALATE"),
             (0.5 * next_sla, "REMIND"), (0.5 * next_sla, "ESCALATE"), (final_grace_hours, "EXPIRE")]
    return [{"wait_seconds": max(1, int(h * 3600 * scale)), "then": a} for h, a in steps]


def _now() -> datetime:
    return datetime.now(UTC)


def _table():
    return _ddb.Table(REVIEWS)


def _get_task(app_id: str) -> dict | None:
    return _table().get_item(Key={"application_id": app_id, "sk": "TASK"}, ConsistentRead=True).get("Item")


def _live(task: dict) -> dict | None:
    """The task row if it is still waiting for *this* round; otherwise None (decided, expired or superseded)."""
    row = _get_task(task["application_id"])
    if not row or row.get("status") != "PENDING" or int(row.get("review_round", 0)) != int(task.get("round", 0)):
        return None
    return row


def put_event(detail_type: str, detail: dict) -> None:
    res = _events.put_events(Entries=[{"EventBusName": BUS, "Source": SOURCE, "DetailType": detail_type,
                                       "Detail": json.dumps(detail, default=str)}])
    if res.get("FailedEntryCount"):
        emit_metric("EventPublishErrors", res["FailedEntryCount"], service="review")
        raise RuntimeError(f"EventBridge rejected {res['FailedEntryCount']} entr(ies) for {detail_type}")


def _finish(token: str, row: dict | None) -> None:
    """A decision was recorded before/while we were about to wait: complete the wait immediately."""
    outcome = (row or {}).get("outcome") or {"action": (row or {}).get("status", "SUPERSEDED")}
    try:
        _sfn.send_task_success(taskToken=token, output=json.dumps(outcome, default=str))
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("TaskTimedOut", "InvalidToken", "TaskDoesNotExist"):
            raise


def plan(event: dict) -> dict:
    task = event["task"]
    schedule = build_schedule(task["tier"], float(task.get("sla_hours", SLA_HOURS.get(task["tier"], 24))),
                              TIME_SCALE, FINAL_GRACE_HOURS)
    deadline = _now() + timedelta(seconds=sum(s["wait_seconds"] for s in schedule))
    if _live(task):
        _table().update_item(
            Key={"application_id": task["application_id"], "sk": "TASK"},
            UpdateExpression="SET execution_arn = :e, workflow_deadline = :d, workflow_state = :w",
            ExpressionAttributeValues={":e": event.get("execution_id", ""), ":d": deadline.isoformat(), ":w": "PLANNED"},
        )
    return {"schedule": schedule, "deadline": deadline.isoformat()}


def register_token(event: dict) -> dict:
    task, token = event["task"], event["token"]
    row = _live(task)
    if row is None:
        _finish(token, _get_task(task["application_id"]))
        return {"completed_early": True}
    try:
        _table().update_item(
            Key={"application_id": task["application_id"], "sk": "TASK"},
            UpdateExpression="SET task_token = :t, execution_arn = :e, workflow_state = :w, wait_segment = :i, updated_at = :n",
            ConditionExpression="#s = :p",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":t": token, ":e": event.get("execution_id", ""), ":w": "WAITING",
                                       ":i": int(event.get("idx", 0)), ":n": _now().isoformat(), ":p": "PENDING"},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        _finish(token, _get_task(task["application_id"]))  # decided between our read and write
        return {"completed_early": True}
    return {"registered": True}


def _applied(row: dict, idx: int) -> bool:
    return row.get("last_action_idx") is not None and int(row["last_action_idx"]) >= idx


def remind(event: dict) -> dict:
    task, idx = event["task"], int(event.get("idx", 0))
    row = _live(task)
    if row is None:
        return {"sent": False}
    if not _applied(row, idx):
        _table().update_item(
            Key={"application_id": task["application_id"], "sk": "TASK"},
            UpdateExpression="SET reminders_sent = if_not_exists(reminders_sent, :z) + :one, last_action_idx = :i, last_reminder_at = :n",
            ExpressionAttributeValues={":z": 0, ":one": 1, ":i": idx, ":n": _now().isoformat()},
        )
        emit_metric("ReviewRemindersSent", 1, service="review", Tier=row["tier"])
    level = int(row.get("escalation_level", 0))
    put_event("ReviewReminder", {
        "application_id": task["application_id"], "tier": row["tier"], "level": level,
        "audience": AUDIENCE.get(level, "reviewers"), "sla_due": row["sla_due"], "round": int(task.get("round", 0)),
    })
    return {"sent": True}


def escalate(event: dict) -> dict:
    task, idx = event["task"], int(event.get("idx", 0))
    row = _live(task)
    if row is None:
        return {"escalated": False}
    from_tier = row["tier"]
    level = int(row.get("escalation_level", 0))
    to_tier = ESCALATION_PATH.get(from_tier, from_tier)
    if not _applied(row, idx):
        level += 1
        _table().update_item(
            Key={"application_id": task["application_id"], "sk": "TASK"},
            UpdateExpression=("SET escalation_level = :l, tier = :t, priority = :p, escalated = :true, escalated_at = :n, "
                              "last_action_idx = :i, original_tier = if_not_exists(original_tier, :from)"),
            ExpressionAttributeValues={":l": level, ":t": to_tier, ":p": 1, ":true": True, ":n": _now().isoformat(),
                                       ":i": idx, ":from": from_tier},
        )
        emit_metric("ReviewEscalations", 1, service="review", Level=str(level))
        log("WARN", "review escalated", application_id=task["application_id"], tier=to_tier, escalation_level=level)
    overdue_h = max(0.0, (_now() - datetime.fromisoformat(row["sla_due"])).total_seconds() / 3600)
    put_event("ReviewEscalated", {
        "application_id": task["application_id"], "from_tier": row.get("original_tier", from_tier), "to_tier": to_tier,
        "level": level, "audience": AUDIENCE.get(min(level, 2), "head-of-credit"), "hours_overdue": round(overdue_h, 1),
        "sla_due": row["sla_due"], "round": int(task.get("round", 0)),
    })
    return {"escalated": True, "level": level}


ACTIONS = {"PLAN": plan, "REGISTER_TOKEN": register_token, "REMIND": remind, "ESCALATE": escalate}


@observed("approval_actions")
def handler(event, context=None):
    action = event["action"]
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action}")
    log("INFO", "approval step", action=action, application_id=event.get("task", {}).get("application_id"))
    return ACTIONS[action](event)
