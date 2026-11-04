"""The LangGraph state shared by every node: one dict per underwriting case, checkpointed so a case can pause for days."""
import operator
from typing import Annotated, Any, Literal, TypedDict

RiskBand = Literal["LOW", "MEDIUM", "HIGH"]
ReviewTier = Literal["AUTO", "STANDARD", "SENIOR", "COMMITTEE"]


class UnderwritingState(TypedDict, total=False):
    # input
    """Case state. Inputs (application), evidence (kyc, bureau, cashflow, policy_context, memory_context), reasoning output
    (risk_band, recommendation, memo, confidence, review_tier), human step (approval, review_round, additional_info) and outcome
    (decision, decided_by, trace). `trace` and `errors` are append-only lists (operator.add reducer) so parallel nodes can write safely.
    """
    application_id: str
    applicant_id: str
    legal_name: str
    country: str
    requested_amount: float
    tenor_months: int
    purpose: str
    submitted_by: str          # Cognito sub of the submitter (four-eyes)
    session_id: str
    # evidence
    memory_context: list[str]
    kyc: dict[str, Any]
    bureau: dict[str, Any]
    cashflow: dict[str, Any]
    policy_context: list[dict[str, str]]
    additional_info: list[str]  # reviewer-supplied clarifications
    # reasoning
    risk_band: RiskBand
    recommendation: Literal["APPROVE", "DECLINE", "REFER"]
    memo: str
    cited_sources: list[str]
    llm_confidence: float
    confidence: float           # final = min(LLM self-report, rule-based score)
    confidence_reasons: list[str]
    review_tier: ReviewTier
    review_round: int
    qa_sampled: bool
    # outcome
    approval: dict[str, Any] | None
    decision: str
    decided_by: str
    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[str], operator.add]
