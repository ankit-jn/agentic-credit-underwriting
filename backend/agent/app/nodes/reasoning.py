"""Reasoning and decision nodes: the model's risk assessment, tier routing, auto-decision, human approval pause, decision recording, memory write.

The model proposes; deterministic code (confidence.py, Cedar at the Gateway) decides who is allowed to finalise."""
import asyncio
import hashlib
import json
import logging
from typing import Literal

from langgraph.types import interrupt
from pydantic import BaseModel, Field

from .. import memory
from ..confidence import PRIORITY, SLA_HOURS, assess_confidence, decide_tier
from ..config import get_settings
from ..llm import get_llm
from ..observability import current_trace_id, emit_metric

log = logging.getLogger("uw-agent.reasoning")

SYSTEM = """You are a credit-risk analyst assistant for an SME lender.
Use ONLY the evidence supplied (tool outputs, policy excerpts, reviewer clarifications). Never invent numbers.
Cite policy excerpts by their source URI. If evidence is missing or contradictory, recommend REFER and say what is missing.
Report your own confidence honestly (0-1): lower it when evidence is thin, borderline or conflicting.
Do not consider or infer protected attributes. Be concise and factual."""


class Assessment(BaseModel):
    """Structured output the model must return: risk band, recommendation, self-confidence, evidence gaps, memo and the policy sources it relied on.
    """
    risk_band: Literal["LOW", "MEDIUM", "HIGH"]
    recommendation: Literal["APPROVE", "DECLINE", "REFER"]
    confidence: float = Field(ge=0, le=1, description="Self-assessed confidence in the recommendation")
    evidence_gaps: list[str] = Field(default_factory=list, description="Missing or conflicting evidence")
    memo: str = Field(description="Credit memo, <=250 words, with figures taken from evidence")
    cited_sources: list[str] = Field(description="Policy source URIs relied upon")


async def risk_assessment(state, config):
    """Ask the model for a structured assessment using ONLY gathered evidence, then compute deterministic confidence and the review tier.
    Unparseable output becomes REFER / HIGH / confidence 0 so a person looks at it.
    """
    s = get_settings()
    evidence = {
        "request": {k: state.get(k) for k in ("requested_amount", "tenor_months", "purpose")},
        "kyc": state.get("kyc"), "bureau": state.get("bureau"), "cashflow": state.get("cashflow"),
        "prior_reviews": state.get("memory_context"),
        "policy_excerpts": state.get("policy_context"),
        "reviewer_clarifications": state.get("additional_info"),
        "tool_errors": state.get("errors"),
    }
    llm = get_llm().with_structured_output(Assessment, include_raw=True)
    res = await llm.ainvoke([("system", SYSTEM), ("human", json.dumps(evidence, default=str))])
    out: Assessment | None = res.get("parsed")
    usage = getattr(res.get("raw"), "usage_metadata", None) or {}
    emit_metric("LlmInputTokens", usage.get("input_tokens", 0), "Count")
    emit_metric("LlmOutputTokens", usage.get("output_tokens", 0), "Count")
    if getattr(res.get("raw"), "response_metadata", {}).get("stopReason") == "guardrail_intervened":
        emit_metric("GuardrailInterventions", 1)
    if out is None:  # unparseable output -> never guess; send to a human
        merged = {**state, "recommendation": "REFER", "risk_band": "HIGH", "llm_confidence": 0.0, "cited_sources": []}
        memo, gaps = "Model output could not be parsed; manual review required.", ["unparseable model output"]
    else:
        merged = {**state, "recommendation": out.recommendation, "risk_band": out.risk_band,
                  "llm_confidence": out.confidence, "cited_sources": out.cited_sources}
        memo, gaps = out.memo, out.evidence_gaps

    confidence, reasons = assess_confidence(merged, s)
    tier = decide_tier(merged, confidence, s)
    emit_metric("RoutingTier", 1, Tier=tier)
    emit_metric("Confidence", confidence, "None", Tier=tier)
    return {
        "risk_band": merged["risk_band"], "recommendation": merged["recommendation"], "memo": memo,
        "cited_sources": merged["cited_sources"], "llm_confidence": merged["llm_confidence"],
        "confidence": confidence, "confidence_reasons": gaps + reasons, "review_tier": tier,
        "trace": [f"risk_assessment: {merged['risk_band']}/{merged['recommendation']} conf={confidence} tier={tier}"],
    }


def route_after_risk(state) -> str:
    """AUTO tier goes straight to auto_decide; everything else creates a human review task."""
    return "auto_decide" if state.get("review_tier") == "AUTO" else "create_review_task"


def _qa_sampled(application_id: str, rate: float) -> bool:
    """Deterministic sampling (replayable, auditable): same id always gives the same answer."""
    return int(hashlib.sha256(application_id.encode()).hexdigest(), 16) % 1000 < rate * 1000


async def escalate_compliance(state, config):
    """Sanctions match or screening outage: stop, mark REFERRED, publish a ComplianceReferred event (best effort) and leave the case to Compliance.
    """
    unavailable = not (state.get("kyc") or {}).get("screening_status")
    memo = ("KYC/sanctions screening unavailable. Workflow stopped; referred to Compliance."
            if unavailable else "Potential sanctions/watchlist match. Workflow stopped; referred to Compliance.")
    emit_metric("ComplianceReferrals", 1, Reason="screening_unavailable" if unavailable else "potential_match")
    try:  # EventBridge hook for a compliance case-management system; best effort, must not block the referral
        await config["configurable"]["tools"].call("record_review_event", {
            "application_id": state["application_id"], "event_type": "COMPLIANCE_REFERRAL",
            "reasons": "screening_unavailable" if unavailable else "potential_match", "session_id": state.get("session_id", "")})
    except Exception as exc:  # noqa: BLE001
        log.warning("compliance referral event not published: %s", exc)
    return {"decision": "REFERRED", "decided_by": "agent:compliance-escalation", "memo": memo,
            "risk_band": "HIGH", "recommendation": "REFER", "cited_sources": [], "review_tier": "SENIOR",
            "confidence": 0.0, "confidence_reasons": [memo], "trace": ["escalate_compliance: referred"]}


async def auto_decide(state, config):
    """Straight-through APPROVED for confident, low-risk, small cases; a deterministic sample is also queued for non-blocking post-hoc QA.
    """
    s = get_settings()
    sampled = _qa_sampled(state["application_id"], s.qa_sample_rate)
    if sampled:  # non-blocking post-hoc QA review of a straight-through approval
        await config["configurable"]["tools"].call("record_review_event", {
            "application_id": state["application_id"], "event_type": "TASK_CREATED", "tier": "QA",
            "priority": 3, "sla_hours": 72, "confidence": state["confidence"], "session_id": state.get("session_id", ""),
            "reasons": json.dumps(["post-hoc QA sample of auto-approved case"]),
            "summary": state.get("memo", "")[:500],
        })
    return {"decision": "APPROVED", "decided_by": "agent:auto", "qa_sampled": sampled,
            "trace": [f"auto_decide: within appetite (qa_sampled={sampled})"]}


async def create_review_task(state, config):
    """Side effect lives in its own node: nodes that call interrupt() re-run on resume."""
    tier = state["review_tier"]
    res = await config["configurable"]["tools"].call("record_review_event", {
        "application_id": state["application_id"], "event_type": "TASK_CREATED", "tier": tier,
        "priority": PRIORITY[tier], "sla_hours": SLA_HOURS[tier], "confidence": state["confidence"],
        "session_id": state.get("session_id", ""), "round": state.get("review_round", 0),
        "reasons": json.dumps(state.get("confidence_reasons", [])),
        "summary": state.get("memo", "")[:500],
    })
    wf = "durable approval workflow started" if (res or {}).get("workflow_started") else "no workflow (monitor fallback)"
    return {"trace": [f"create_review_task: tier={tier} sla={SLA_HOURS[tier]}h; {wf}"]}


async def human_approval(state, config):
    """Pause until a reviewer responds. Checkpointed in AgentCore Memory, so it can wait hours/days."""
    s = get_settings()
    rnd = state.get("review_round", 0)
    answer = interrupt({
        "type": "REVIEW_REQUIRED", "application_id": state["application_id"], "tier": state["review_tier"],
        "confidence": state["confidence"], "confidence_reasons": state.get("confidence_reasons", []),
        "recommendation": state["recommendation"], "risk_band": state["risk_band"],
        "amount": state["requested_amount"], "memo": state["memo"], "cited_sources": state.get("cited_sources", []),
        "round": rnd, "allowed_actions": ["APPROVE", "DECLINE", "REQUEST_INFO"],
    })
    action = answer.get("action", "DECLINE")
    reviewer = answer.get("approver", "unknown")
    if action == "EXPIRE":  # the approval workflow ran out of reminders and escalations: refer, never decide
        await config["configurable"]["tools"].call("record_review_event", {
            "application_id": state["application_id"], "event_type": "EXPIRED", "session_id": state.get("session_id", "")})
        emit_metric("ReviewActions", 1, Action="EXPIRE", Tier=state["review_tier"])
        return {"approval": answer, "decision": "REFERRED", "decided_by": "system:sla-expiry",
                "trace": ["human_approval: EXPIRED (no reviewer decision before the workflow deadline) -> REFERRED"]}
    await config["configurable"]["tools"].call("record_review_event", {
        "application_id": state["application_id"], "event_type": "FEEDBACK", "reviewer": reviewer,
        "agent_recommendation": state["recommendation"], "human_decision": action,
        "reason_codes": ",".join(answer.get("reason_codes", [])), "comment": answer.get("comment", "")[:2000],
        "confidence": state["confidence"], "tier": state["review_tier"], "risk_band": state["risk_band"],
        "session_id": state.get("session_id", ""),
    })
    emit_metric("ReviewActions", 1, Action=action, Tier=state["review_tier"])

    if action == "REQUEST_INFO":
        if rnd + 1 > s.max_review_rounds:  # stop endless loops
            return {"approval": answer, "decision": "REFERRED", "decided_by": reviewer, "review_round": rnd + 1,
                    "trace": ["human_approval: REQUEST_INFO limit reached -> REFERRED"]}
        return {"approval": answer, "review_round": rnd + 1,
                "additional_info": [answer.get("additional_info", "")],
                "trace": [f"human_approval: REQUEST_INFO (round {rnd + 1})"]}
    decision = "APPROVED" if action == "APPROVE" else "DECLINED"
    return {"approval": answer, "decision": decision, "decided_by": reviewer, "trace": [f"human_approval: {action}"]}


def route_after_human(state) -> str:
    """A decision ends the human step; no decision (REQUEST_INFO) loops back to risk_assessment with the reviewer's input.
    """
    return "record_decision" if state.get("decision") else "risk_assessment"


async def record_decision(state, config):
    """Write the immutable audit record (decision, model/prompt/policy versions, trace id, sources) through the Gateway, where Cedar enforces the hard limits.
    """
    s = get_settings()
    res = await config["configurable"]["tools"].call("record_decision", {
        "application_id": state["application_id"], "applicant_id": state["applicant_id"],
        "decision": state["decision"], "amount": state["requested_amount"],
        "risk_band": state.get("risk_band", "UNKNOWN"), "rationale": state.get("memo", ""),
        "decided_by": state.get("decided_by", "agent"), "model_id": s.model_id,
        "tier": state.get("review_tier", "NA"), "confidence": state.get("confidence", 0),
        "trace_id": current_trace_id(), "prompt_version": s.prompt_version, "policy_version": s.policy_version,
        "policy_sources": state.get("cited_sources", []),
    })
    emit_metric("Decisions", 1, Decision=state["decision"], Tier=state.get("review_tier", "NA"))
    return {"trace": [f"record_decision: {res}"]}


async def save_memory(state, config):
    """Store the outcome and the reviewer's reasoning in AgentCore Memory for the applicant's next review."""
    a = state.get("approval") or {}
    note = f" Reviewer {a.get('approver')}: {a.get('action')} [{','.join(a.get('reason_codes', []))}] {a.get('comment', '')}" if a else ""
    summary = (f"Application {state['application_id']} for {state['requested_amount']}: {state['decision']} "
               f"({state.get('risk_band')}, conf {state.get('confidence')}). {state.get('memo', '')[:400]}{note}")
    await asyncio.to_thread(
        memory.remember, state["applicant_id"], config["configurable"]["thread_id"],
        [(f"Review request {state['application_id']}", "USER"), (summary, "ASSISTANT")],
    )
    return {"trace": ["save_memory: ok"]}
