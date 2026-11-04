"""Evidence-gathering nodes. All external access goes through AgentCore Gateway (MCP)."""
import asyncio

import boto3

from .. import memory
from ..config import get_settings


def _tools(config):
    """Get the Gateway tool client the request injected into the LangGraph config."""
    return config["configurable"]["tools"]


async def load_memory(state, config):
    """Recall prior reviews of this applicant so the assessment starts informed."""
    ctx = await asyncio.to_thread(memory.recall, state["applicant_id"], f"prior reviews of {state['legal_name']}")
    return {"memory_context": ctx, "trace": [f"load_memory: {len(ctx)} prior record(s)"]}


async def kyc_screen(state, config):
    """Tool 1: watchlist/KYC screening; its status drives the compliance gate."""
    res = await _tools(config).call("screen_entity", {"applicant_id": state["applicant_id"], "legal_name": state["legal_name"]})
    return {"kyc": res, "trace": [f"kyc_screen: {res.get('screening_status')}"]}


async def bureau_check(state, config):
    """Tool 2: external credit-bureau score (OAuth-protected REST behind the Gateway)."""
    res = await _tools(config).call("getCreditScore", {"applicant_id": state["applicant_id"]})
    return {"bureau": res, "trace": [f"bureau_check: score={res.get('bureau_score')}"]}


async def cashflow_analysis(state, config):
    """Tool 3: bank-transaction analysis (cash flow, DSCR proxy, months of data)."""
    res = await _tools(config).call(
        "analyze_cashflow",
        {"applicant_id": state["applicant_id"], "requested_amount": state["requested_amount"], "tenor_months": state["tenor_months"]},
    )
    return {"cashflow": res, "trace": [f"cashflow_analysis: dscr={res.get('dscr_proxy')}"]}


def _retrieve(query: str) -> list[dict]:
    """Query the Bedrock Knowledge Base (credit, AML, risk-appetite policy on S3) and keep each chunk's source URI for citation.
    """
    s = get_settings()
    client = boto3.client("bedrock-agent-runtime", region_name=s.region)
    out = client.retrieve(
        knowledgeBaseId=s.kb_id, retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 5}},
    )
    return [
        {"text": r["content"]["text"], "source": r.get("location", {}).get("s3Location", {}).get("uri", "unknown")}
        for r in out["retrievalResults"]
    ]


async def retrieve_policy(state, config):
    """Fetch the policy clauses relevant to this request so the memo can cite them."""
    q = f"SME working capital loan {state['requested_amount']} tenor {state['tenor_months']} months DSCR bureau score eligibility risk appetite"
    docs = await asyncio.to_thread(_retrieve, q)
    return {"policy_context": docs, "trace": [f"retrieve_policy: {len(docs)} chunk(s)"]}
