"""Backend of the REST API (API Gateway proxy integration). Synthetic bureau data."""
import hashlib
import json

from obs import observed


@observed("credit_bureau_mock")
def handler(event, context=None):
    """API Gateway proxy handler: GET /score/{applicant_id} returns a deterministic synthetic bureau score (OAuth scope bureau/read enforced upstream).
    """
    qs = event.get("queryStringParameters") or {}
    applicant_id = qs.get("applicant_id")
    if not applicant_id:
        return _resp(400, {"error": "applicant_id is required"})
    h = int(hashlib.sha256(applicant_id.encode()).hexdigest(), 16)
    return _resp(200, {
        "applicant_id": applicant_id,
        "bureau_score": 450 + h % 450,
        "delinquencies_24m": h % 3 if h % 5 == 0 else 0,
        "credit_utilisation_pct": 20 + h % 60,
        "enquiries_6m": h % 6,
        "bureau_reference": f"BUR-{h % 10**8:08d}",
    })


def _resp(code, body):
    """Build a JSON proxy response."""
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}
