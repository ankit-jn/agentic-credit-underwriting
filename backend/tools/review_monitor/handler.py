"""Scheduled (every 5 min) Lambda: review-queue health, SLA escalation and feedback-quality metrics.

Emits: ReviewQueueDepth(Tier), ReviewOldestAgeMinutes, ReviewSlaBreaches, ReviewEscalatedOpen, ReviewTasksWithoutWorkflow,
AgreementRate, OverrideRate (by confidence bucket).

Escalation is owned by the Step Functions approval workflow. This monitor only escalates (SNS, once) tasks that have NO
workflow (workflow disabled or failed to start) - a safety net - and alarms on them.
Feedback stats scan the last 7 days - fine at pilot scale; use a GSI / Athena export beyond that."""
import os
from datetime import UTC, datetime, timedelta

import boto3
from boto3.dynamodb.conditions import Attr, Key
from obs import emit_metric, log, observed

_ddb = boto3.resource("dynamodb")
_sns = boto3.client("sns")
REVIEWS = os.environ.get("REVIEWS_TABLE", "reviews")
FEEDBACK = os.environ.get("FEEDBACK_TABLE", "feedback")
TOPIC = os.environ.get("ALARM_TOPIC_ARN", "")
GRACE_MINUTES = float(os.environ.get("WORKFLOW_GRACE_MINUTES", "10"))


def _bucket(conf: float) -> str:
    """Map a confidence value to its calibration bucket (e.g. 0.8-0.9)."""
    return "high" if conf >= 0.85 else "mid" if conf >= 0.60 else "low"


def queue_health(now: datetime) -> None:
    """Emit queue depth, oldest age, SLA breaches, escalated-open and tasks-without-workflow; fallback-escalate tasks that have no workflow.
    """
    items = _ddb.Table(REVIEWS).query(
        IndexName="status-sla", KeyConditionExpression=Key("status").eq("PENDING"))["Items"]
    by_tier: dict[str, int] = {}
    oldest, overdue, fallback, orphans, escalated_open = 0.0, 0, [], 0, 0
    for i in items:
        by_tier[i["tier"]] = by_tier.get(i["tier"], 0) + 1
        age_min = (now - datetime.fromisoformat(i["created_at"])).total_seconds() / 60
        oldest = max(oldest, age_min)
        if i["tier"] == "QA":
            continue
        is_overdue = datetime.fromisoformat(i["sla_due"]) < now
        overdue += is_overdue
        escalated_open += int(i.get("escalation_level", 0)) > 0
        if not i.get("execution_arn"):
            if age_min > GRACE_MINUTES:  # the workflow registers itself within seconds of task creation
                orphans += 1
            if is_overdue and not i.get("escalated"):
                fallback.append(i)
    for tier in ("STANDARD", "SENIOR", "COMMITTEE", "QA"):
        emit_metric("ReviewQueueDepth", by_tier.get(tier, 0), service="review", Tier=tier)
    emit_metric("ReviewOldestAgeMinutes", round(oldest, 1), "None", service="review")
    emit_metric("ReviewSlaBreaches", overdue, service="review")
    emit_metric("ReviewEscalatedOpen", escalated_open, service="review")
    emit_metric("ReviewTasksWithoutWorkflow", orphans, service="review")
    for b in fallback:  # legacy / safety-net escalation, once per task
        if TOPIC:
            _sns.publish(TopicArn=TOPIC, Subject=f"SLA breach: {b['application_id']} ({b['tier']})",
                         Message=f"Review task {b['application_id']} tier={b['tier']} due {b['sla_due']} is overdue (no workflow attached).")
        _ddb.Table(REVIEWS).update_item(Key={"application_id": b["application_id"], "sk": "TASK"},
                                        UpdateExpression="SET escalated = :t", ExpressionAttributeValues={":t": True})
        log("WARN", "sla breach escalated by monitor fallback", application_id=b["application_id"], tier=b["tier"])


def feedback_quality(now: datetime) -> None:
    """Emit agreement and override rates per confidence bucket and reviewer-quality signals from the feedback table."""
    cutoff = (now - timedelta(days=7)).isoformat()
    rows = _ddb.Table(FEEDBACK).scan(FilterExpression=Attr("feedback_ts").gte(cutoff))["Items"]
    rows = [r for r in rows if r["human_decision"] != "REQUEST_INFO"]
    groups = {"all": rows}
    for r in rows:
        groups.setdefault(_bucket(float(r["confidence"])), []).append(r)
    for name, grp in groups.items():
        if grp:
            emit_metric("AgreementRate", sum(r["agreement"] for r in grp) / len(grp), "None", service="review", ConfidenceBucket=name)
            emit_metric("OverrideRate", sum(r["override"] for r in grp) / len(grp), "None", service="review", ConfidenceBucket=name)


@observed("review_monitor")
def handler(event=None, context=None):
    """Scheduled entrypoint (every 5 minutes): run queue_health and feedback_quality."""
    now = datetime.now(UTC)
    queue_health(now)
    feedback_quality(now)
    return {"ok": True}
