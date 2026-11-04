"""Tool (Lambda): human-review task + structured reviewer feedback + workflow hand-off.

TASK_CREATED         -> upsert a review task (idempotent), start the durable approval workflow (Step Functions)
                        and publish ReviewRequested on EventBridge
FEEDBACK             -> append immutable feedback row, close/advance the task, complete the workflow's wait (callback)
EXPIRED              -> (workflow principal only, via the agent) mark the task expired; NOT a human label
COMPLIANCE_REFERRAL  -> publish ComplianceReferred so a case-management system can subscribe (phase-2 hook)

Workflow and event failures never fail the tool call: the task row is the source of truth and the review_monitor
raises an alarm for tasks that have no workflow (metric ReviewTasksWithoutWorkflow).
"""
import json
import os
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError
from obs import emit_metric, log, observed

_ddb = boto3.resource("dynamodb")
_sfn = boto3.client("stepfunctions")
_events = boto3.client("events")
REVIEWS = os.environ.get("REVIEWS_TABLE", "reviews")
FEEDBACK = os.environ.get("FEEDBACK_TABLE", "feedback")
STATE_MACHINE = os.environ.get("APPROVAL_STATE_MACHINE_ARN", "")  # empty => workflow disabled, monitor-only fallback
BUS = os.environ.get("EVENT_BUS_NAME", "")
OVERRIDE_PAIRS = {("APPROVE", "DECLINE"), ("DECLINE", "APPROVE")}
TOKEN_ERRORS = ("TaskTimedOut", "InvalidToken", "TaskDoesNotExist")


def _now():
    return datetime.now(UTC)


def _publish(detail_type: str, detail: dict) -> None:
    if not BUS:
        return
    try:
        res = _events.put_events(Entries=[{"EventBusName": BUS, "Source": "uw.underwriting", "DetailType": detail_type,
                                           "Detail": json.dumps(detail, default=str)}])
        if res.get("FailedEntryCount"):
            raise RuntimeError("EventBridge rejected the entry")
    except Exception as exc:  # noqa: BLE001 - events must never break the review itself
        emit_metric("EventPublishErrors", 1, service="review")
        log("ERROR", "event publish failed", event_type=detail_type, error=f"{type(exc).__name__}: {exc}"[:200])


def _start_workflow(app_id: str, event: dict, tier: str, due: datetime) -> bool:
    if not STATE_MACHINE or tier == "QA":  # QA samples are non-blocking: nothing is waiting on them
        return False
    name = re.sub(r"[^A-Za-z0-9_-]", "-", f"{app_id}-{str(event.get('session_id', ''))[:8]}-r{int(event.get('round', 0))}")[:80]
    task = {"application_id": app_id, "tier": tier, "session_id": event.get("session_id", ""),
            "round": int(event.get("round", 0)), "sla_hours": int(event.get("sla_hours", 24)),
            "confidence": float(event.get("confidence", 0))}
    try:
        _sfn.start_execution(stateMachineArn=STATE_MACHINE, name=name, input=json.dumps({"task": task}))
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ExecutionAlreadyExists":  # retried TASK_CREATED: already running
            return True
        emit_metric("WorkflowStartErrors", 1, service="review")
        log("ERROR", "workflow start failed", application_id=app_id, error=exc.response["Error"]["Code"])
        return False


def _complete_wait(token: str | None, outcome: dict) -> None:
    """Complete the workflow's wait. Stale tokens are fine: the next REGISTER_TOKEN step sees the decision on the row."""
    if not token:
        return
    try:
        _sfn.send_task_success(taskToken=token, output=json.dumps(outcome, default=str))
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in TOKEN_ERRORS:
            emit_metric("WorkflowCallbackErrors", 1, service="review")
            raise
        log("INFO", "task token already closed; workflow will pick the outcome up from the row")


@observed("review_queue")
def handler(event, context=None):
    et = event["event_type"]
    app_id = event["application_id"]
    now = _now()
    if et == "TASK_CREATED":
        tier = event.get("tier", "STANDARD")
        due = now + timedelta(hours=int(event.get("sla_hours", 24)))
        _ddb.Table(REVIEWS).update_item(
            Key={"application_id": app_id, "sk": "TASK"},
            UpdateExpression=("SET #s = :s, tier = :t, priority = :p, sla_due = :d, reasons = :r, summary = :m, "
                              "session_id = :sid, confidence = :c, review_round = :rd, escalated = :f, escalation_level = :z, "
                              "reminders_sent = :z, workflow_state = :ws, "
                              "created_at = if_not_exists(created_at, :now), updated_at = :now "
                              "REMOVE task_token, execution_arn, outcome, last_action_idx, original_tier, escalated_at, "
                              "expiring_at, expired_at, workflow_deadline, wait_segment"),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "PENDING", ":t": tier, ":p": int(event.get("priority", 2)),
                ":d": due.isoformat(), ":r": event.get("reasons", "[]"), ":m": event.get("summary", "")[:500],
                ":sid": event.get("session_id", ""), ":c": Decimal(str(event.get("confidence", 0))),
                ":rd": int(event.get("round", 0)), ":f": False, ":z": 0, ":ws": "NONE", ":now": now.isoformat(),
            },
        )
        started = _start_workflow(app_id, event, tier, due)
        _publish("ReviewRequested", {"application_id": app_id, "tier": tier, "sla_due": due.isoformat(),
                                     "confidence": float(event.get("confidence", 0)), "round": int(event.get("round", 0)),
                                     "workflow": started})
        return {"recorded": True, "event_type": et, "sla_due": due.isoformat(), "workflow_started": started}

    if et == "FEEDBACK":
        agent_rec, human = event.get("agent_recommendation", ""), event.get("human_decision", "")
        row = {
            "application_id": app_id, "feedback_ts": now.isoformat(), "reviewer": event.get("reviewer", "unknown"),
            "agent_recommendation": agent_rec, "human_decision": human,
            "agreement": agent_rec == human, "override": (agent_rec, human) in OVERRIDE_PAIRS,
            "reason_codes": [c for c in event.get("reason_codes", "").split(",") if c],
            "comment": event.get("comment", ""), "confidence": Decimal(str(event.get("confidence", 0))),
            "tier": event.get("tier", ""), "risk_band": event.get("risk_band", ""),
        }
        _ddb.Table(FEEDBACK).put_item(Item=row)
        status = "IN_REVISION" if human == "REQUEST_INFO" else "COMPLETED"
        outcome = {"action": human, "reviewer": row["reviewer"], "reason_codes": row["reason_codes"],
                   "completed_at": now.isoformat()}
        old = _ddb.Table(REVIEWS).update_item(
            Key={"application_id": app_id, "sk": "TASK"},
            UpdateExpression=("SET #s = :s, handled_by = :h, completed_at = :now, updated_at = :now, outcome = :o, "
                              "workflow_state = :w REMOVE task_token"),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":h": row["reviewer"], ":now": now.isoformat(), ":o": outcome,
                                       ":w": "COMPLETED"},
            ReturnValues="ALL_OLD",
        ).get("Attributes", {})
        _complete_wait(old.get("task_token"), outcome)
        if old.get("created_at"):
            emit_metric("ReviewTimeToDecisionMinutes",
                        round((now - datetime.fromisoformat(old["created_at"])).total_seconds() / 60, 1), "None",
                        service="review", Tier=old.get("tier", "NA"))
        late = old.get("status") in ("EXPIRING", "EXPIRED")
        if late:
            emit_metric("ReviewLateDecisions", 1, service="review")
        return {"recorded": True, "event_type": et, "agreement": row["agreement"], "override": row["override"],
                "late": late}

    if et == "EXPIRED":
        _ddb.Table(REVIEWS).update_item(
            Key={"application_id": app_id, "sk": "TASK"},
            UpdateExpression="SET #s = :s, expired_at = :now, updated_at = :now, workflow_state = :w, outcome = :o REMOVE task_token",
            ConditionExpression="#s IN (:p, :x, :s)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "EXPIRED", ":p": "PENDING", ":x": "EXPIRING", ":now": now.isoformat(),
                                       ":w": "EXPIRED", ":o": {"action": "EXPIRE", "reviewer": "system:approval-workflow"}},
        )
        return {"recorded": True, "event_type": et}

    if et == "COMPLIANCE_REFERRAL":
        _publish("ComplianceReferred", {"application_id": app_id, "reason": event.get("reasons", ""),
                                        "session_id": event.get("session_id", "")})
        return {"recorded": True, "event_type": et}
    raise ValueError(f"unknown event_type {et}")
