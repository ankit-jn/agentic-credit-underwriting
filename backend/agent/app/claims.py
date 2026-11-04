"""Who is calling, and what they may do. Claims come from the JWT already validated by the AgentCore Runtime authorizer.

Holds the review authorization matrix: underwriters decide, the approval workflow (system principal) may only EXPIRE."""
import base64
import json


def decode_claims(authorization: str) -> dict:
    """Decode (NOT verify) a JWT. Verification is done by AgentCore Runtime's JWT authorizer."""
    token = authorization.removeprefix("Bearer ").strip()
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def is_underwriter(claims: dict) -> bool:
    """True if the caller belongs to the `underwriters` Cognito group (required to approve, decline or request info).
    """
    return "underwriters" in (claims.get("cognito:groups") or [])


SYSTEM_SCOPE = "underwriting/system.resume"


def is_workflow(claims: dict, workflow_client_id: str) -> bool:
    """The Step Functions approval workflow (client-credentials token). Must match the configured client AND hold the scope."""
    return bool(workflow_client_id) and claims.get("client_id") == workflow_client_id and \
        SYSTEM_SCOPE in str(claims.get("scope", "")).split()


HUMAN_ACTIONS = {"APPROVE", "DECLINE", "REQUEST_INFO"}
SYSTEM_ACTIONS = {"EXPIRE"}  # only the approval workflow may send these; humans may not


def review_authorization_error(claims: dict, action_type: str, workflow_client_id: str) -> str | None:
    """Who may send which review action. Humans: underwriters decide or ask for info. Workflow: EXPIRE only."""
    if is_workflow(claims, workflow_client_id):
        return None if action_type in SYSTEM_ACTIONS else "forbidden: the approval workflow can only EXPIRE a pending review"
    if action_type in SYSTEM_ACTIONS:
        return "forbidden: EXPIRE is reserved for the approval workflow"
    if not is_underwriter(claims):
        return "forbidden: underwriter group required"
    if action_type not in HUMAN_ACTIONS:
        return f"action_type must be one of {sorted(HUMAN_ACTIONS)}"
    return None
