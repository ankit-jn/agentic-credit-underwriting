"""Offline regression evals: golden cases + canned tool outputs + the REAL model and Knowledge Base.

Usage (needs AWS creds, BEDROCK_MODEL_ID, KNOWLEDGE_BASE_ID):  python -m evals.run_evals [--min-pass 0.8]
Checks: recommendation, routing tier, confidence ceiling, policy citation present. Exit 1 below the pass threshold.
Grow the file from reviewer feedback with scripts/export_feedback_to_golden.py."""
import argparse
import asyncio
import json
import os
import pathlib
import sys

os.environ.setdefault("CHECKPOINTER", "memory")
os.environ.setdefault("QA_SAMPLE_RATE", "0")
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.graph import build_graph
from app.nodes import evidence as ev
from langgraph.checkpoint.memory import MemorySaver


class CannedTools:
    """Replays fixed tool outputs per golden case so only the model and Knowledge Base vary."""
    def __init__(self, tools):
        self.tools = tools

    async def call(self, name, args, **_):
        return self.tools.get(name, {"recorded": True})


async def run_case(c) -> tuple[bool, list[str]]:
    """Run one golden case through the real graph (no human step) and compare recommendation and tier with the expected label.
    """
    g = build_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": c["id"], "tools": CannedTools(c["tools"])}}
    await g.ainvoke({"application_id": c["id"], "applicant_id": "EVAL", "legal_name": "Eval Co",
                     "requested_amount": c["amount"], "tenor_months": c["tenor"], "purpose": "working capital",
                     "submitted_by": "eval", "session_id": c["id"], "review_round": 0, "errors": [], "trace": []}, cfg)
    v = (await g.aget_state(cfg)).values
    e, fails = c["expect"], []
    if "recommendation" in e and v.get("recommendation") != e["recommendation"]:
        fails.append(f"recommendation {v.get('recommendation')} != {e['recommendation']}")
    if "tier" in e and v.get("review_tier") != e["tier"]:
        fails.append(f"tier {v.get('review_tier')} != {e['tier']}")
    if "tier_in" in e and v.get("review_tier") not in e["tier_in"]:
        fails.append(f"tier {v.get('review_tier')} not in {e['tier_in']}")
    if "max_confidence" in e and v.get("confidence", 1) > e["max_confidence"]:
        fails.append(f"confidence {v.get('confidence')} > {e['max_confidence']}")
    if v.get("review_tier") != "COMMITTEE" and not v.get("cited_sources"):
        fails.append("no policy citation")
    return not fails, fails


async def main(min_pass: float, path: str):
    """Run all golden cases, print agreement, and exit non-zero below the threshold (the deployment gate)."""
    ev.get_settings()
    cases = [json.loads(line) for line in pathlib.Path(path).read_text().splitlines() if line.strip()]
    results = [(c["id"], *await run_case(c)) for c in cases]
    for cid, ok, fails in results:
        print(("PASS " if ok else "FAIL ") + cid, "; ".join(fails))
    rate = sum(ok for _, ok, _ in results) / len(results)
    print(f"pass rate {rate:.0%} (min {min_pass:.0%})")
    sys.exit(0 if rate >= min_pass else 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-pass", type=float, default=0.8)
    ap.add_argument("--cases", default=str(pathlib.Path(__file__).with_name("golden_cases.jsonl")))
    a = ap.parse_args()
    asyncio.run(main(a.min_pass, a.cases))
