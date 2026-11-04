"""MCP client for AgentCore Gateway (streamable HTTP). The caller's JWT is propagated so
Gateway + Cedar policy see the *end user*, not a shared service identity."""
import asyncio
import json
import logging
import time
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from .observability import emit_metric, tracer

log = logging.getLogger("uw-agent.tools")


def _parse(raw: Any) -> Any:
    """Normalise an MCP tool result (text blocks, JSON string or dict) into a Python object."""
    if isinstance(raw, list):
        raw = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in raw)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"text": raw}
    return raw


class GatewayTools:
    """Thin client over AgentCore Gateway. Exposes each tool by suffix name and propagates the user's bearer token so Cedar sees the real caller.
    """
    def __init__(self, url: str, bearer: str):
        """Create the MCP client pointing at the Gateway URL with the caller's Authorization header."""
        self._client = MultiServerMCPClient(
            {"gateway": {"url": url, "transport": "streamable_http", "headers": {"Authorization": bearer}}}
        )
        self._tools: dict[str, Any] = {}

    async def load(self) -> "GatewayTools":
        """Discover the tools the Gateway exposes to this caller (Cedar-filtered)."""
        self._tools = {t.name: t for t in await self._client.get_tools()}
        return self

    def names(self) -> list[str]:
        """Sorted names of the discovered tools (used in error messages)."""
        return sorted(self._tools)

    async def call(self, tool_suffix: str, args: dict, retries: int = 2, timeout: float = 20.0) -> Any:
        """Gateway names tools '<target>___<tool>'; match on the tool part. Retries with backoff; timed + traced."""
        for name, tool in self._tools.items():
            if name == tool_suffix or name.endswith(f"___{tool_suffix}"):
                break
        else:
            raise KeyError(f"tool {tool_suffix!r} not exposed by gateway; have {self.names()}")
        last: Exception | None = None
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            with tracer.start_as_current_span(f"tool.{tool_suffix}") as span:
                span.set_attribute("uw.tool", tool_suffix)
                span.set_attribute("uw.attempt", attempt)
                try:
                    out = _parse(await asyncio.wait_for(tool.ainvoke(args), timeout))
                    emit_metric("GatewayToolLatencyMs", (time.perf_counter() - t0) * 1000, "Milliseconds", Tool=tool_suffix)
                    return out
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    emit_metric("GatewayToolErrors", 1, Tool=tool_suffix)
                    log.warning("tool %s attempt %d failed: %s", tool_suffix, attempt, exc)
                    span.record_exception(exc)
            await asyncio.sleep(0.5 * 2**attempt)
        raise RuntimeError(f"tool {tool_suffix} failed after {retries + 1} attempts: {last}")
