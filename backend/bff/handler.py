"""Backend-for-frontend. Forwards the caller's Cognito JWT to AgentCore Runtime
so the end-user identity is preserved end-to-end (Runtime -> Gateway -> Cedar)."""
import json
import os
import urllib.parse
import urllib.request
import uuid

import boto3
from boto3.dynamodb.conditions import Key

REGION = os.environ["AWS_REGION"]
RUNTIME_ARN = os.environ["RUNTIME_ARN"]
APPLICATIONS = os.environ["APPLICATIONS_TABLE"]
REVIEWS = os.environ["REVIEWS_TABLE"]
_ddb = boto3.resource("dynamodb")
# Allow-list: the review row also holds the Step Functions task token (a capability) and execution ARN. Never send those to a browser.
TASK_FIELDS = ("application_id", "tier", "priority", "status", "sla_due", "created_at", "updated_at", "confidence", "reasons",
               "summary", "session_id", "review_round", "escalated", "escalation_level", "reminders_sent", "original_tier",
               "escalated_at", "workflow_state", "workflow_deadline", "handled_by", "completed_at", "expired_at")


def _public(item: dict) -> dict:
    """Project a review row onto the allow-listed fields the browser may see (hides task token and execution ARN)."""
    return {k: item[k] for k in TASK_FIELDS if k in item}


def _json(code, body):
    """Build an API Gateway proxy response with a JSON body."""
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def handler(event, context=None):
    """Backend-for-frontend router (HTTP API, JWT authorizer): POST /agent, GET /applications, GET /reviews, GET /reviews/{id}/timeline.
    Reads DynamoDB directly for lists; forwards agent actions to the AgentCore Runtime with the caller's token.
    """
    route = event["routeKey"]
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if route == "POST /agent":
        return _invoke(event, headers)
    if route == "GET /applications":
        items = _ddb.Table(APPLICATIONS).scan(
            FilterExpression="sk = :s", ExpressionAttributeValues={":s": "DECISION"}, Limit=200
        )["Items"]
        items.sort(key=lambda i: i.get("decided_at", ""), reverse=True)
        return _json(200, {"items": items[:50]})
    if route == "GET /reviews":  # reviewer work queue: PENDING tasks, soonest SLA first
        items = _ddb.Table(REVIEWS).query(
            IndexName="status-sla", KeyConditionExpression=Key("status").eq("PENDING"), Limit=100)["Items"]
        return _json(200, {"items": [_public(i) for i in items]})
    if route == "GET /reviews/{application_id}/timeline":  # reminders, escalations, decisions, expiry: from EventBridge
        app_id = (event.get("pathParameters") or {}).get("application_id", "")
        rows = _ddb.Table(REVIEWS).query(
            KeyConditionExpression=Key("application_id").eq(app_id) & Key("sk").begins_with("EVT#"), Limit=200)["Items"]
        return _json(200, {"items": [{"time": r["event_time"], "type": r["event_type"], "detail": json.loads(r["detail"])}
                                     for r in rows]})
    return _json(404, {"error": "not found"})


def _invoke(event, headers):
    """Call the AgentCore Runtime /invocations endpoint on behalf of the user (same session id continues a paused case).
    """
    session_id = headers.get("x-session-id") or (uuid.uuid4().hex + uuid.uuid4().hex)[:40]
    url = (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/"
        f"{urllib.parse.quote(RUNTIME_ARN, safe='')}/invocations?qualifier=DEFAULT"
    )
    req = urllib.request.Request(
        url,
        data=(event.get("body") or "{}").encode(),
        method="POST",
        headers={
            "Authorization": headers["authorization"],
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=28) as r:  # HTTP API max integration timeout is 30s
            payload = json.loads(r.read())
        payload.setdefault("session_id", session_id)
        return _json(200, payload)
    except urllib.error.HTTPError as e:
        return _json(e.code, {"error": e.read().decode()[:2000]})
