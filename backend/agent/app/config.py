"""Runtime settings for the agent, read once from environment variables (set by Terraform on the AgentCore Runtime).

Every business threshold (caps, confidence bars, DSCR/bureau floors, QA sample rate) lives here so routing rules are
changed by configuration + pull request, never by the model."""
import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    """Immutable bundle of all configuration: AWS ids (model, Knowledge Base, Memory, Gateway, Guardrail), business thresholds,
    four-eyes switch, workflow client id and version labels stamped on every decision for audit.
    """
    region: str
    model_id: str
    kb_id: str
    memory_id: str
    gateway_url: str
    guardrail_id: str
    guardrail_version: str
    hitl_amount_threshold: float      # above this, a human must decide
    hard_cap_amount: float            # above this, outside agent authority (credit committee)
    confidence_auto_min: float        # >= this (and LOW risk, small ticket) => straight-through
    confidence_review_min: float      # < this => senior underwriter queue
    qa_sample_rate: float             # share of auto-approvals sent for post-hoc QA review
    max_review_rounds: int            # reviewer "request more info" loops before referral
    enforce_four_eyes: bool           # submitter cannot approve own case
    dscr_floor: float
    bureau_score_floor: int
    prompt_version: str
    policy_version: str
    workflow_client_id: str           # Cognito client of the approval workflow (system principal; may only EXPIRE)
    checkpointer: str                 # "agentcore" | "memory" (local dev / tests)


@lru_cache
def get_settings() -> Settings:
    """Build (and cache) Settings from the environment with safe defaults; call this instead of reading os.environ elsewhere.
    """
    e = os.getenv
    return Settings(
        region=e("AWS_REGION", "us-east-1"),
        model_id=e("BEDROCK_MODEL_ID", ""),
        kb_id=e("KNOWLEDGE_BASE_ID", ""),
        memory_id=e("MEMORY_ID", ""),
        gateway_url=e("GATEWAY_URL", ""),
        guardrail_id=e("GUARDRAIL_ID", ""),
        guardrail_version=e("GUARDRAIL_VERSION", "DRAFT"),
        hitl_amount_threshold=float(e("HITL_AMOUNT_THRESHOLD", "250000")),
        hard_cap_amount=float(e("HARD_CAP_AMOUNT", "1000000")),
        confidence_auto_min=float(e("CONFIDENCE_AUTO_MIN", "0.85")),
        confidence_review_min=float(e("CONFIDENCE_REVIEW_MIN", "0.60")),
        qa_sample_rate=float(e("QA_SAMPLE_RATE", "0.10")),
        max_review_rounds=int(e("MAX_REVIEW_ROUNDS", "2")),
        enforce_four_eyes=e("ENFORCE_FOUR_EYES", "true").lower() == "true",
        dscr_floor=float(e("DSCR_FLOOR", "1.25")),
        bureau_score_floor=int(e("BUREAU_SCORE_FLOOR", "650")),
        prompt_version=e("PROMPT_VERSION", "dev"),
        policy_version=e("POLICY_VERSION", "dev"),
        workflow_client_id=e("WORKFLOW_CLIENT_ID", ""),
        checkpointer=e("CHECKPOINTER", "agentcore"),
    )
