"""Step Functions worker: expire a review nobody decided (NOT a Gateway tool).

When every reminder and escalation has been exhausted, the paused LangGraph case must be closed. This function
resumes the agent's session as the *workflow principal* (Cognito client-credentials, scope underwriting/system.resume):
  * the agent only accepts action EXPIRE from that principal, and humans cannot send EXPIRE;
  * Cedar lets that principal record only decision REFERRED (policies 06/07): expiry can never approve or decline.

Idempotent and race-safe: an atomic claim (PENDING -> EXPIRING) beats a late human decision; if a decision already
exists the function reports skipped=true and the workflow publishes that outcome instead.
"""
import base64
import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError
from obs import emit_metric, log, observed

_ddb = boto3.resource("dynamodb")
_secrets = boto3.client("secretsmanager")
REVIEWS = os.environ.get("REVIEWS_TABLE", "reviews")
REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNTIME_ARN = os.environ.get("RUNTIME_ARN", "")
TOKEN_URL = os.environ.get("TOKEN_URL", "")
SECRET_ID = os.environ.get("CLIENT_SECRET_ID", "")
SCOPE = os.environ.get("TOKEN_SCOPE", "underwriting/system.resume")
_cache: dict = {}


def _client_credentials() -> tuple[str, str]:
    if "cred" not in _cache:
        s = json.loads(_secrets.get_secret_value(SecretId=SECRET_ID)["SecretString"])
        _cache["cred"] = (s["client_id"], s["client_secret"])
    return _cache["cred"]


def _bearer() -> str:
    cid, secret = _client_credentials()
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request(
        TOKEN_URL, method="POST", data=urllib.parse.urlencode({"grant_type": "client_credentials", "scope": SCOPE}).encode(),
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return "Bearer " + json.loads(r.read())["access_token"]


def _invoke_runtime(session_id: str, payload: dict) -> dict:
    url = (f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/"
           f"{urllib.parse.quote(RUNTIME_ARN, safe='')}/invocations?qualifier=DEFAULT")
    req = urllib.request.Request(url, method="POST", data=json.dumps(payload).encode(), headers={
        "Authorization": _bearer(), "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id})
    with urllib.request.urlopen(req, timeout=50) as r:
        return json.loads(r.read())


def _claim(app_id: str) -> dict | None:
    """Atomically move PENDING -> EXPIRING (re-entrant for retries). Returns the row, or None if it was already decided."""
    try:
        return _ddb.Table(REVIEWS).update_item(
            Key={"application_id": app_id, "sk": "TASK"},
            UpdateExpression="SET #s = :e, expiring_at = :n, workflow_state = :w",
            ConditionExpression="#s IN (:p, :e)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":e": "EXPIRING", ":p": "PENDING", ":n": datetime.now(UTC).isoformat(), ":w": "EXPIRING"},
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return None


@observed("workflow_resume")
def handler(event, context=None):
    app_id = event["task"]["application_id"]
    row = _claim(app_id)
    if row is None:
        current = _ddb.Table(REVIEWS).get_item(Key={"application_id": app_id, "sk": "TASK"}, ConsistentRead=True).get("Item", {})
        log("INFO", "expiry skipped: already decided", application_id=app_id, status=current.get("status"))
        return {"skipped": True, "status": current.get("status", "UNKNOWN"),
                "outcome": current.get("outcome") or {"action": current.get("status", "UNKNOWN")}}
    result = _invoke_runtime(row["session_id"], {
        "action": "review", "action_type": "EXPIRE", "reason_codes": ["SLA_EXPIRED"],
        "comment": f"No reviewer decision before the workflow deadline (escalation level {int(row.get('escalation_level', 0))}).",
    })
    if result.get("error"):
        raise RuntimeError(f"agent refused expiry: {str(result['error'])[:200]}")  # Step Functions retries, then alerts
    emit_metric("ReviewExpirations", 1, service="review", Tier=row["tier"])
    log("WARN", "review expired and referred", application_id=app_id, tier=row["tier"])
    return {"skipped": False, "decision": result.get("decision"), "status": result.get("status")}
