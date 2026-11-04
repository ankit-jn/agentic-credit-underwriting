"""Unit tests for the deterministic parts: KYC gate, confidence penalties, tier routing, hard cap. No AWS, no model."""
import pytest
from app.confidence import assess_confidence, decide_tier
from app.config import get_settings
from app.graph import route_after_human, route_after_kyc, route_after_risk


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv("HITL_AMOUNT_THRESHOLD", "100000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


GOOD = {
    "requested_amount": 50000, "recommendation": "APPROVE", "risk_band": "LOW", "llm_confidence": 0.95,
    "kyc": {"screening_status": "CLEAR"}, "bureau": {"bureau_score": 780},
    "cashflow": {"data_available": True, "months_analysed": 12, "dscr_proxy": 2.1},
    "policy_context": [{"text": "x", "source": "s3://x"}], "cited_sources": ["s3://x"], "errors": [],
}


def test_kyc_routing():
    assert route_after_kyc({"kyc": {"screening_status": "POTENTIAL_MATCH"}}) == "escalate_compliance"
    assert route_after_kyc({}) == "escalate_compliance"  # screening unavailable => never proceed
    assert set(route_after_kyc({"kyc": {"screening_status": "CLEAR"}})) == {"bureau_check", "cashflow_analysis"}


def test_clean_small_case_is_auto():
    conf, reasons = assess_confidence(GOOD, get_settings())
    assert conf >= 0.85 and not reasons
    assert decide_tier(GOOD, conf, get_settings()) == "AUTO"


def test_borderline_metrics_lower_confidence_and_force_review():
    s = {**GOOD, "cashflow": {**GOOD["cashflow"], "dscr_proxy": 1.27}, "bureau": {"bureau_score": 655}}
    conf, reasons = assess_confidence(s, get_settings())
    assert conf < 0.85 and any("DSCR" in r for r in reasons) and any("bureau score" in r for r in reasons)
    assert decide_tier(s, conf, get_settings()) in ("STANDARD", "SENIOR")


def test_missing_evidence_goes_to_senior():
    s = {**GOOD, "errors": ["bureau_check: Timeout"], "bureau": {}}
    conf, _ = assess_confidence(s, get_settings())
    assert conf < 0.60 and decide_tier(s, conf, get_settings()) == "SENIOR"


def test_conflicting_recommendation_is_penalised():
    s = {**GOOD, "cashflow": {**GOOD["cashflow"], "dscr_proxy": 0.8}}
    conf, reasons = assess_confidence(s, get_settings())
    assert any("conflicts" in r for r in reasons) and decide_tier(s, conf, get_settings()) != "AUTO"


def test_over_hard_cap_is_committee_regardless_of_confidence():
    s = {**GOOD, "requested_amount": 2_000_000}
    assert decide_tier(s, 0.99, get_settings()) == "COMMITTEE"


def test_decline_never_auto():
    s = {**GOOD, "recommendation": "DECLINE"}
    assert decide_tier(s, 0.99, get_settings()) != "AUTO"


def test_route_after_risk_and_human():
    assert route_after_risk({"review_tier": "AUTO"}) == "auto_decide"
    assert route_after_risk({"review_tier": "STANDARD"}) == "create_review_task"
    assert route_after_human({"decision": "APPROVED"}) == "record_decision"
    assert route_after_human({}) == "risk_assessment"  # REQUEST_INFO loops back
