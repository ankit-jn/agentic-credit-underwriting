"""Tool 3 (Lambda): idempotent decision write + immutable audit item."""
import os
from datetime import UTC, datetime
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError
from obs import observed

_ddb = boto3.resource("dynamodb")
APPLICATIONS = os.environ.get("APPLICATIONS_TABLE", "applications")
ALLOWED = {"APPROVED", "DECLINED", "REFERRED"}


@observed("decision_recorder")
def handler(event, context=None):
    """Write the decision once (conditional put keyed by application id) plus an immutable audit item with model, prompt and policy versions and trace id.
    """
    decision = event["decision"]
    if decision not in ALLOWED:
        raise ValueError(f"decision must be one of {sorted(ALLOWED)}")
    now = datetime.now(UTC).isoformat()
    table = _ddb.Table(APPLICATIONS)
    record = {
        "application_id": event["application_id"],
        "sk": "DECISION",
        "applicant_id": event["applicant_id"],
        "decision": decision,
        "amount": Decimal(str(event["amount"])),
        "risk_band": event.get("risk_band", "UNKNOWN"),
        "rationale": event.get("rationale", "")[:4000],
        "decided_by": event.get("decided_by", "agent"),
        "model_id": event.get("model_id", "n/a"),
        "policy_sources": event.get("policy_sources", []),
        "tier": event.get("tier", "NA"),
        "confidence": Decimal(str(event.get("confidence", 0))),
        "trace_id": event.get("trace_id", ""),
        "prompt_version": event.get("prompt_version", ""),
        "policy_version": event.get("policy_version", ""),
        "decided_at": now,
    }
    try:
        table.put_item(Item=record, ConditionExpression="attribute_not_exists(application_id)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {"recorded": False, "reason": "decision already exists", "application_id": event["application_id"]}
        raise
    table.put_item(Item={**record, "sk": f"AUDIT#{now}"})
    return {"recorded": True, "application_id": event["application_id"], "decision": decision, "decided_at": now}
