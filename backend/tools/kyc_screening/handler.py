"""Tool 1 (Lambda): KYC status + sanctions/watchlist screening.

Invoked by AgentCore Gateway. `event` is the tool-argument object.
"""
import difflib
import os
import re

import boto3
from obs import observed

_ddb = boto3.resource("dynamodb")
WATCHLIST = os.environ.get("WATCHLIST_TABLE", "watchlist")
APPLICANTS = os.environ.get("APPLICANTS_TABLE", "applicants")
THRESHOLD = float(os.environ.get("MATCH_THRESHOLD", "0.85"))


def normalise(name: str) -> str:
    """Lower-case and strip a name so watchlist matching is stable."""
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


@observed("kyc_screening")
def handler(event, context=None):
    """Return KYC status and watchlist result (CLEAR, POTENTIAL_MATCH). Failure or timeout is treated by the agent as 'screening unavailable' -> Compliance.
    """
    applicant_id = event["applicant_id"]
    legal_name = event["legal_name"]
    key = normalise(legal_name)

    applicant = _ddb.Table(APPLICANTS).get_item(Key={"applicant_id": applicant_id}).get("Item", {})
    kyc_status = applicant.get("kyc_status", "NOT_FOUND")

    matches = []
    # Demo-scale scan. Production: licensed screening vendor / indexed search.
    for item in _ddb.Table(WATCHLIST).scan().get("Items", []):
        score = difflib.SequenceMatcher(None, key, item["name_key"]).ratio()
        if score >= THRESHOLD:
            matches.append({"list": item.get("list"), "reason": item.get("reason"), "score": round(score, 3)})

    if matches:
        status = "POTENTIAL_MATCH"
    elif kyc_status != "VERIFIED":
        status = "KYC_INCOMPLETE"
    else:
        status = "CLEAR"
    return {"applicant_id": applicant_id, "screening_status": status, "kyc_status": kyc_status, "matches": matches}
