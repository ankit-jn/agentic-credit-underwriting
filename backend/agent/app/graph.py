"""Assembles the underwriting graph (the workflow definition) and its checkpointer.

Flow: load_memory -> kyc_screen -> (compliance stop | bureau + cashflow in parallel) -> retrieve_policy -> risk_assessment ->
(auto_decide | create_review_task -> human_approval -> record_decision; REQUEST_INFO loops back to risk_assessment) -> save_memory."""
from langgraph.graph import END, START, StateGraph

from .config import get_settings
from .nodes import evidence as ev
from .nodes import reasoning as rs
from .observability import instrument
from .state import UnderwritingState


def route_after_kyc(state) -> list[str] | str:
    """Sanctions gate. A potential match or an unavailable screening service stops the workflow (compliance referral);
    otherwise fan out to the bureau and cash-flow tools in parallel.
    """
    status = (state.get("kyc") or {}).get("screening_status")
    if status in (None, "POTENTIAL_MATCH"):  # match OR screening unavailable -> never proceed
        return "escalate_compliance"
    return ["bureau_check", "cashflow_analysis"]  # parallel fan-out


route_after_risk = rs.route_after_risk
route_after_human = rs.route_after_human


def build_graph(checkpointer=None):
    """Wire nodes and edges. 'Soft' nodes record their error and let confidence scoring route the case to a human;
    'hard' nodes (reasoning, decisions) fail loudly. Pass a checkpointer so human_approval can pause and resume.
    """
    g = StateGraph(UnderwritingState)
    nodes = [  # (name, fn, soft): soft nodes record errors and let confidence scoring route to a human
        ("load_memory", ev.load_memory, True), ("kyc_screen", ev.kyc_screen, True),
        ("bureau_check", ev.bureau_check, True), ("cashflow_analysis", ev.cashflow_analysis, True),
        ("retrieve_policy", ev.retrieve_policy, True), ("risk_assessment", rs.risk_assessment, False),
        ("escalate_compliance", rs.escalate_compliance, False), ("auto_decide", rs.auto_decide, False),
        ("create_review_task", rs.create_review_task, False), ("human_approval", rs.human_approval, False),
        ("record_decision", rs.record_decision, False), ("save_memory", rs.save_memory, True),
    ]
    for name, fn, soft in nodes:
        g.add_node(name, instrument(name, soft=soft)(fn))

    g.add_edge(START, "load_memory")
    g.add_edge("load_memory", "kyc_screen")
    g.add_conditional_edges("kyc_screen", route_after_kyc, ["escalate_compliance", "bureau_check", "cashflow_analysis"])
    g.add_edge(["bureau_check", "cashflow_analysis"], "retrieve_policy")  # join
    g.add_edge("retrieve_policy", "risk_assessment")
    g.add_conditional_edges("risk_assessment", route_after_risk, ["auto_decide", "create_review_task"])
    g.add_edge("create_review_task", "human_approval")
    g.add_conditional_edges("human_approval", route_after_human, ["record_decision", "risk_assessment"])  # loop on REQUEST_INFO
    g.add_edge("escalate_compliance", "record_decision")
    g.add_edge("auto_decide", "record_decision")
    g.add_edge("record_decision", "save_memory")
    g.add_edge("save_memory", END)
    return g.compile(checkpointer=checkpointer)


def make_checkpointer():
    """In-memory saver for local tests; AgentCore Memory saver in AWS so a paused case survives runtime recycling for as long as the review takes.
    """
    s = get_settings()
    if s.checkpointer == "memory":
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()
    from langgraph_checkpoint_aws import AgentCoreMemorySaver
    return AgentCoreMemorySaver(s.memory_id, region_name=s.region)
