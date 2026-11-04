"""Deterministic confidence scoring and review-tier routing.

The LLM's self-reported confidence is only one input - it is never sufficient on its own.
Everything here is explainable: each penalty adds a human-readable reason shown to the reviewer.
"""
from .config import Settings

PRIORITY = {"STANDARD": 2, "SENIOR": 1, "COMMITTEE": 1, "AUTO": 3}
SLA_HOURS = {"STANDARD": 24, "SENIOR": 8, "COMMITTEE": 72, "AUTO": 0}


def assess_confidence(state: dict, s: Settings) -> tuple[float, list[str]]:
    """Start from the model's self-reported confidence and subtract fixed, explainable penalties (tool errors, missing data, KYC not clear,
    borderline DSCR/bureau, recommendation contradicting policy). Returns (score 0-1, human-readable reasons shown to the reviewer).
    """
    score = float(state.get("llm_confidence", 0.5))
    reasons: list[str] = []

    def penalise(amount: float, reason: str):
        nonlocal score
        score -= amount
        reasons.append(reason)

    bureau, cash, kyc = state.get("bureau") or {}, state.get("cashflow") or {}, state.get("kyc") or {}

    if state.get("errors"):
        penalise(0.30, f"{len(state['errors'])} tool/node error(s) during evidence gathering")
    if not bureau.get("bureau_score"):
        penalise(0.30, "bureau score unavailable")
    if not cash.get("data_available"):
        penalise(0.30, "no bank-transaction data")
    elif cash.get("months_analysed", 0) < 6:
        penalise(0.10, f"only {cash.get('months_analysed')} month(s) of transactions")
    if not state.get("policy_context"):
        penalise(0.25, "no policy excerpts retrieved")
    if not state.get("cited_sources"):
        penalise(0.15, "memo cites no policy source")
    if kyc.get("screening_status") != "CLEAR":
        penalise(0.30, f"KYC status {kyc.get('screening_status')}")

    dscr, bscore = cash.get("dscr_proxy"), bureau.get("bureau_score")
    if dscr is not None and abs(dscr - s.dscr_floor) <= 0.10 * s.dscr_floor:
        penalise(0.15, f"DSCR {dscr} is within 10% of the {s.dscr_floor} floor")
    if bscore is not None and abs(bscore - s.bureau_score_floor) <= 20:
        penalise(0.15, f"bureau score {bscore} is within 20 points of the {s.bureau_score_floor} floor")
    if state.get("recommendation") == "APPROVE" and (
        (dscr is not None and dscr < s.dscr_floor) or (bscore is not None and bscore < s.bureau_score_floor)
    ):
        penalise(0.40, "APPROVE recommendation conflicts with policy thresholds")

    return round(max(0.0, min(score, 1.0)), 2), reasons


def decide_tier(state: dict, confidence: float, s: Settings) -> str:
    """Pick the review route deterministically: COMMITTEE above the hard cap, AUTO only for confident LOW-risk APPROVE within the auto limit,
    SENIOR for HIGH risk or low confidence, otherwise STANDARD. The model never chooses its own autonomy level.
    """
    amount = float(state["requested_amount"])
    if amount > s.hard_cap_amount:
        return "COMMITTEE"  # outside agent authority regardless of confidence
    if (
        state.get("recommendation") == "APPROVE"
        and state.get("risk_band") == "LOW"
        and amount <= s.hitl_amount_threshold
        and confidence >= s.confidence_auto_min
    ):
        return "AUTO"
    if confidence < s.confidence_review_min or state.get("risk_band") == "HIGH":
        return "SENIOR"
    return "STANDARD"
