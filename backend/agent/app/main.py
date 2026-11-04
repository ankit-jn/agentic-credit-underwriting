"""AgentCore Runtime entrypoint (HTTP protocol, port 8080, /invocations).

Actions: submit | review (APPROVE / DECLINE / REQUEST_INFO by underwriters; EXPIRE only by the approval workflow) | get (read case state for the reviewer UI)."""
import logging
import time
from functools import lru_cache

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langgraph.types import Command

from .claims import decode_claims, is_workflow, review_authorization_error
from .config import get_settings
from .graph import build_graph, make_checkpointer
from .mcp_tools import GatewayTools
from .observability import bind, emit_metric, setup_logging, tracer

setup_logging()
log = logging.getLogger("uw-agent")
app = BedrockAgentCoreApp()


@lru_cache
def graph():
    """Compile the underwriting graph once per process (with the AgentCore Memory checkpointer)."""
    return build_graph(make_checkpointer())


def _summary(values: dict, pending: list) -> dict:
    """Reduce the full graph state to what the UI needs, and derive the case status (PENDING_REVIEW, REFERRED, COMPLETED).
    """
    keys = ("application_id", "decision", "risk_band", "recommendation", "memo", "cited_sources", "confidence",
            "confidence_reasons", "review_tier", "review_round", "qa_sampled", "kyc", "bureau", "cashflow", "errors", "trace")
    status = "PENDING_REVIEW" if pending else ("REFERRED" if values.get("decision") == "REFERRED" else "COMPLETED")
    return {"status": status, "pending": pending, **{k: values.get(k) for k in keys}}


async def _state(g, cfg):
    """Load the checkpointed state of a session; returns the snapshot and any pending human-approval interrupt."""
    snap = await g.aget_state(cfg)
    return snap, [i.value for t in snap.tasks for i in t.interrupts]


@app.entrypoint
async def invoke(payload: dict, context):
    """HTTP entrypoint. Authenticates via JWT claims, then dispatches: submit (new case), review (reviewer decision or workflow EXPIRE,
    checked by review_authorization_error and four-eyes) or get (case state). Resuming a paused case uses the same session id.
    """
    t0 = time.perf_counter()
    headers = context.request_headers or {}
    bearer = headers.get("Authorization") or headers.get("authorization")
    if not bearer:
        return {"error": "missing Authorization header (check runtime request-header allowlist)"}
    claims = decode_claims(bearer)
    s = get_settings()
    action = payload.get("action", "submit")
    system = is_workflow(claims, s.workflow_client_id)
    if system and action != "review":
        return {"error": "forbidden: the approval workflow may only expire pending reviews"}
    bind(session_id=context.session_id, user=claims["sub"], action=action)

    with tracer.start_as_current_span(f"invoke.{action}") as span:
        span.set_attribute("uw.session_id", context.session_id)
        span.set_attribute("enduser.id", claims["sub"])
        tools = await GatewayTools(s.gateway_url, bearer).load()
        cfg = {"configurable": {"thread_id": context.session_id, "actor_id": claims["sub"], "tools": tools}}
        g = graph()

        if action == "get":
            snap, pending = await _state(g, cfg)
            return {**_summary(snap.values, pending), "session_id": context.session_id}

        if action == "submit":
            a = payload["application"]
            bind(application_id=a["application_id"])
            init = {
                "application_id": a["application_id"], "applicant_id": a["applicant_id"], "legal_name": a["legal_name"],
                "country": a.get("country", "IN"), "requested_amount": float(a["requested_amount"]),
                "tenor_months": int(a["tenor_months"]), "purpose": a.get("purpose", "working capital"),
                "submitted_by": claims["sub"], "session_id": context.session_id, "review_round": 0,
                "errors": [], "trace": [f"submitted by {claims.get('username', claims['sub'])}"],
            }
            await g.ainvoke(init, cfg)
        elif action == "review":
            decision = str(payload.get("action_type", "")).upper()
            if err := review_authorization_error(claims, decision, s.workflow_client_id):
                return {"error": err}
            snap, pending = await _state(g, cfg)
            if not pending:
                return {"error": "no review pending for this session"}
            if decision not in ("REQUEST_INFO", "EXPIRE") and not payload.get("reason_codes"):
                return {"error": "reason_codes are required for APPROVE/DECLINE (feeds the feedback loop)"}
            if decision == "REQUEST_INFO" and not payload.get("additional_info"):
                return {"error": "additional_info is required for REQUEST_INFO"}
            if s.enforce_four_eyes and snap.values.get("submitted_by") == claims["sub"] and decision != "REQUEST_INFO":
                emit_metric("FourEyesBlocked", 1)
                return {"error": "four-eyes control: the submitter cannot decide their own case"}
            bind(application_id=snap.values.get("application_id"))
            resume = {"action": decision, "reason_codes": payload.get("reason_codes", []),
                      "comment": payload.get("comment", ""), "additional_info": payload.get("additional_info", ""),
                      "approver": "system:approval-workflow" if system else claims.get("username", claims["sub"]),
                      "approver_sub": claims["sub"]}
            await g.ainvoke(Command(resume=resume), cfg)
        else:
            return {"error": f"unknown action {action}"}

        snap, pending = await _state(g, cfg)
        result = _summary(snap.values, pending)
        emit_metric("TimeToRecommendationMs", (time.perf_counter() - t0) * 1000, "Milliseconds",
                    Tier=str(snap.values.get("review_tier", "NA")))
        log.info("invocation complete status=%s tier=%s", result["status"], result.get("review_tier"))
        return {**result, "session_id": context.session_id}


if __name__ == "__main__":
    app.run()
