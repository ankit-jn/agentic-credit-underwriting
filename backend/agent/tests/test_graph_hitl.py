"""End-to-end graph test with fake tools and a stubbed LLM: auto path, HITL pause/resume, request-info loop."""

import pytest
from app.config import get_settings
from app.graph import build_graph
from app.nodes import evidence as ev
from app.nodes import reasoning as rs
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command


class FakeTools:
    def __init__(self, bureau_score=780, dscr=2.1):
        self.calls, self.bureau_score, self.dscr = [], bureau_score, dscr

    async def call(self, name, args, **_):
        self.calls.append((name, args))
        return {
            "screen_entity": {"screening_status": "CLEAR"},
            "getCreditScore": {"bureau_score": self.bureau_score},
            "analyze_cashflow": {"data_available": True, "months_analysed": 12, "dscr_proxy": self.dscr},
        }.get(name, {"recorded": True})


class FakeLLM:
    def __init__(self, rec="APPROVE", band="LOW", conf=0.95):
        self.out = rs.Assessment(risk_band=band, recommendation=rec, confidence=conf, memo="ok", cited_sources=["s3://p"])

    def with_structured_output(self, *_a, **_k):
        return self

    async def ainvoke(self, _msgs):
        return {"parsed": self.out, "raw": None}


@pytest.fixture(autouse=True)
def patch(monkeypatch):
    monkeypatch.setenv("HITL_AMOUNT_THRESHOLD", "100000")
    monkeypatch.setenv("QA_SAMPLE_RATE", "0")
    get_settings.cache_clear()
    monkeypatch.setattr(ev, "_retrieve", lambda q: [{"text": "t", "source": "s3://p"}])
    monkeypatch.setattr(rs.memory, "remember", lambda *a, **k: None)
    monkeypatch.setattr(ev.memory, "recall", lambda *a, **k: [])
    yield
    get_settings.cache_clear()


def case(amount):
    return {"application_id": "A1", "applicant_id": "X", "legal_name": "N", "requested_amount": amount,
            "tenor_months": 12, "purpose": "wc", "submitted_by": "u1", "session_id": "s", "review_round": 0,
            "errors": [], "trace": []}


@pytest.mark.asyncio
async def test_auto_path(monkeypatch):
    monkeypatch.setattr(rs, "get_llm", lambda: FakeLLM())
    tools = FakeTools()
    g = build_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "t1", "tools": tools}}
    out = await g.ainvoke(case(50000), cfg)
    assert out["decision"] == "APPROVED" and out["decided_by"] == "agent:auto"
    assert not any(n == "record_review_event" and a["event_type"] == "TASK_CREATED" for n, a in tools.calls)


@pytest.mark.asyncio
async def test_low_confidence_pauses_then_human_decides(monkeypatch):
    monkeypatch.setattr(rs, "get_llm", lambda: FakeLLM())
    tools = FakeTools(bureau_score=655, dscr=1.27)  # borderline => review
    g = build_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "t2", "tools": tools}}
    await g.ainvoke(case(50000), cfg)
    snap = await g.aget_state(cfg)
    assert snap.next == ("human_approval",)
    assert any(a["event_type"] == "TASK_CREATED" for n, a in tools.calls if n == "record_review_event")
    out = await g.ainvoke(Command(resume={"action": "DECLINE", "reason_codes": ["DSCR_TOO_LOW"], "approver": "bob"}), cfg)
    assert out["decision"] == "DECLINED" and out["decided_by"] == "bob"
    assert any(a.get("event_type") == "FEEDBACK" and a["human_decision"] == "DECLINE" for n, a in tools.calls)


@pytest.mark.asyncio
async def test_request_info_loops_back_then_limit(monkeypatch):
    monkeypatch.setattr(rs, "get_llm", lambda: FakeLLM(rec="REFER", band="MEDIUM", conf=0.5))
    monkeypatch.setenv("MAX_REVIEW_ROUNDS", "1")
    get_settings.cache_clear()
    tools = FakeTools()
    g = build_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "t3", "tools": tools}}
    await g.ainvoke(case(500000), cfg)
    await g.ainvoke(Command(resume={"action": "REQUEST_INFO", "additional_info": "GST returns attached", "approver": "bob"}), cfg)
    assert (await g.aget_state(cfg)).next == ("human_approval",)  # re-assessed, paused again
    out = await g.ainvoke(Command(resume={"action": "REQUEST_INFO", "additional_info": "more", "approver": "bob"}), cfg)
    assert out["decision"] == "REFERRED"  # round limit reached


@pytest.mark.asyncio
async def test_tool_failure_degrades_to_human_review(monkeypatch):
    monkeypatch.setattr(rs, "get_llm", lambda: FakeLLM())

    class Flaky(FakeTools):
        async def call(self, name, args, **kw):
            if name == "getCreditScore":
                raise TimeoutError("bureau down")
            return await super().call(name, args, **kw)

    g = build_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "t4", "tools": Flaky()}}
    await g.ainvoke(case(50000), cfg)
    snap = await g.aget_state(cfg)
    assert snap.next == ("human_approval",) and snap.values["review_tier"] in ("SENIOR", "STANDARD")
    assert any("getCreditScore" in e or "bureau_check" in e for e in snap.values["errors"])
