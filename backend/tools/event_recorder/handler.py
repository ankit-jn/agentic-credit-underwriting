"""EventBridge consumer: writes every workflow event to the review timeline (audit trail shown to reviewers and audit).

Items are stored in the reviews table as sk = EVT#<time>#<type>#<event id>. They carry no `status` attribute,
so they never appear in the sparse `status-sla` queue index. Conditional put => safe under at-least-once delivery.
"""
import json
import os

import boto3
from botocore.exceptions import ClientError
from obs import log, observed

_ddb = boto3.resource("dynamodb")
REVIEWS = os.environ.get("REVIEWS_TABLE", "reviews")


@observed("event_recorder")
def handler(event, context=None):
    detail = event.get("detail") or {}
    app_id = detail.get("application_id")
    if not app_id:
        return {"recorded": False, "reason": "no application_id"}
    sk = f"EVT#{event['time']}#{event['detail-type']}#{event['id'][:8]}"
    try:
        _ddb.Table(REVIEWS).put_item(
            Item={"application_id": app_id, "sk": sk, "event_type": event["detail-type"], "event_time": event["time"],
                  "event_id": event["id"], "detail": json.dumps(detail, default=str)[:3500]},
            ConditionExpression="attribute_not_exists(sk)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return {"recorded": False, "reason": "duplicate"}
    log("INFO", "timeline event stored", application_id=app_id, event_type=event["detail-type"])
    return {"recorded": True}
