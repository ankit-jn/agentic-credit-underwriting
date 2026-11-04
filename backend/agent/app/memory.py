"""AgentCore Memory: long-term (semantic/summary) records + short-term events."""
from bedrock_agentcore.memory import MemoryClient

from .config import get_settings


def _client() -> MemoryClient:
    """Create an AgentCore Memory client for the configured region."""
    return MemoryClient(region_name=get_settings().region)


def recall(applicant_id: str, query: str, top_k: int = 5) -> list[str]:
    """Fetch the most relevant prior-review facts for an applicant (empty when no Memory store is configured)."""
    s = get_settings()
    if not s.memory_id:
        return []
    records = _client().retrieve_memories(
        memory_id=s.memory_id, namespace=f"/applicants/{applicant_id}/facts/", query=query, top_k=top_k
    )
    return [r.get("content", {}).get("text", "") for r in records if r]


def remember(applicant_id: str, session_id: str, turns: list[tuple[str, str]]) -> None:
    """Write this review (request + outcome + reviewer rationale) as a short-term event; Memory strategies distil it into long-term facts.
    """
    s = get_settings()
    if not s.memory_id:
        return
    _client().create_event(memory_id=s.memory_id, actor_id=applicant_id, session_id=session_id, messages=turns)
