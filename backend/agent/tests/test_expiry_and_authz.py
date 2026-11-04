"""Agent side of the durable approval workflow: who may EXPIRE, and what expiry does to a paused case."""
import pytest
from app.claims import is_workflow, review_authorization_error
from app.config import get_settings
from app.graph import build_graph
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from . import test_graph_hitl as base  # reuse FakeTools / FakeLLM / fixtures

WF = "wf-client"
HUMAN = {"sub": "u2", "cognito:groups": ["underwriters"], "client_id": "spa"}
ANALYST = {"sub": "u3", "cognito:groups": ["analysts"], "client_id": "spa"}
SYSTEM = {"sub": WF, "client_id": WF, "scope": "underwriting/system.resume"}


def test_only_the_workflow_principal_may_expire():
    assert review_authorization_error(SYSTEM, "EXPIRE", WF) is None
    assert "reserved" in review_authorization_error(HUMAN, "EXPIRE", WF)          # a reviewer cannot expire a case
    assert "reserved" in review_authorization_error(ANALYST, "EXPIRE", WF)


def test_the_workflow_principal_can_never_decide():
    for action in ("APPROVE", "DECLINE", "REQUEST_INFO"):
        assert "only EXPIRE" in review_authorization_error(SYSTEM, action, WF)


def test_humans_keep_their_existing_rules():
    assert review_authorization_error(HUMAN, "APPROVE", WF) is None
    assert review_authorization_error(HUMAN, "REQUEST_INFO", WF) is None
    assert "underwriter group" in review_authorization_error(ANALYST, "APPROVE", WF)
    assert "action_type" in review_authorization_error(HUMAN, "MAYBE", WF)


def test_workflow_identity_needs_both_client_and_scope():
    assert is_workflow(SYSTEM, WF)
    assert not is_workflow({**SYSTEM, "scope": "underwriting/agent.invoke"}, WF)    # right client, wrong scope
    assert not is_workflow({**SYSTEM, "client_id": "someone-else"}, WF)              # right scope, wrong client
    assert not is_workflow(SYSTEM, "")                                               # workflow not configured => nobody qualifies


@pytest.mark.asyncio
async def test_expiry_refers_the_case_and_writes_no_human_label(monkeypatch):
    monkeypatch.setattr(base.rs, "get_llm", lambda: base.FakeLLM(rec="APPROVE", band="MEDIUM", conf=0.9))
    tools = base.FakeTools()
    g = build_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "x1", "tools": tools}}
    await g.ainvoke(base.case(500000), cfg)
    assert (await g.aget_state(cfg)).next == ("human_approval",)
    out = await g.ainvoke(Command(resume={"action": "EXPIRE", "approver": "system:approval-workflow", "reason_codes": ["SLA_EXPIRED"]}), cfg)
    assert out["decision"] == "REFERRED" and out["decided_by"] == "system:sla-expiry"
    events = [a["event_type"] for n, a in tools.calls if n == "record_review_event"]
    assert "EXPIRED" in events and "FEEDBACK" not in events                           # not a label for calibration
    recorded = [a for n, a in tools.calls if n == "record_decision"]
    assert recorded and recorded[-1]["decision"] == "REFERRED"                        # never APPROVED/DECLINED


@pytest.mark.asyncio
async def test_task_creation_reports_whether_the_workflow_took_over(monkeypatch):
    monkeypatch.setattr(base.rs, "get_llm", lambda: base.FakeLLM(rec="REFER", band="MEDIUM", conf=0.5))

    class WithWorkflow(base.FakeTools):
        async def call(self, name, args, **kw):
            if name == "record_review_event" and args["event_type"] == "TASK_CREATED":
                self.calls.append((name, args))
                return {"recorded": True, "workflow_started": True}
            return await super().call(name, args, **kw)

    g = build_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "x2", "tools": WithWorkflow()}}
    await g.ainvoke(base.case(500000), cfg)
    assert any("durable approval workflow started" in t for t in (await g.aget_state(cfg)).values["trace"])


@pytest.mark.asyncio
async def test_compliance_event_failure_never_blocks_the_referral(monkeypatch):
    monkeypatch.setattr(base.rs, "get_llm", lambda: base.FakeLLM())

    class SanctionsHit(base.FakeTools):
        async def call(self, name, args, **kw):
            if name == "screen_entity":
                return {"screening_status": "POTENTIAL_MATCH"}
            if name == "record_review_event":
                raise TimeoutError("events down")
            return await super().call(name, args, **kw)

    g = build_graph(MemorySaver())
    out = await g.ainvoke(base.case(50000), {"configurable": {"thread_id": "x3", "tools": SanctionsHit()}})
    assert out["decision"] == "REFERRED" and out["decided_by"] == "agent:compliance-escalation"


def test_settings_default_has_no_workflow_client():
    get_settings.cache_clear()
    assert get_settings().workflow_client_id == ""
