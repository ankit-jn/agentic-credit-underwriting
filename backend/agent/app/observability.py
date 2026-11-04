"""Tracing (OpenTelemetry -> AgentCore Observability / X-Ray), JSON logs, and EMF metrics.

Metrics are emitted as CloudWatch Embedded Metric Format lines on stdout - no agent-side API calls.
Never log personal data: only ids (application_id, applicant_id, session_id), never names or amounts of PII.
"""
import contextvars
import json
import logging
import sys
import time
from functools import wraps

from langgraph.errors import GraphInterrupt
from opentelemetry import trace

NAMESPACE = "UWAgent"
SERVICE = "agent"
tracer = trace.get_tracer("uw-agent")
_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("uw_ctx", default=None)


def bind(**fields) -> None:
    """Attach request-scoped ids (application, session, user) to every log line of this request."""
    _ctx.set({**(_ctx.get() or {}), **{k: v for k, v in fields.items() if v is not None}})


def current_trace_id() -> str:
    """Return the active OpenTelemetry trace id so decisions can be tied to X-Ray traces."""
    sc = trace.get_current_span().get_span_context()
    return format(sc.trace_id, "032x") if sc.is_valid else ""


class JsonFormatter(logging.Formatter):
    """Log formatter producing one JSON object per line (CloudWatch Logs Insights friendly); carries bound ids, never free text from applicants.
    """
    def format(self, r: logging.LogRecord) -> str:
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r.created)), "level": r.levelname,
             "logger": r.name, "msg": r.getMessage(), "service": SERVICE, **(_ctx.get() or {})}
        if tid := current_trace_id():
            d["trace_id"] = tid
        if r.exc_info:
            d["exc"] = self.formatException(r.exc_info)
        return json.dumps(d, default=str)


def setup_logging() -> None:
    """Install the JSON formatter once at process start."""
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(logging.INFO)


def emit_metric(name: str, value: float = 1, unit: str = "Count", **dims: str) -> None:
    """CloudWatch EMF. Two dimension sets: aggregate (Service) and per-dimension."""
    sets = [["Service"]] + ([["Service", *dims]] if dims else [])
    print(json.dumps({
        "_aws": {"Timestamp": int(time.time() * 1000),
                 "CloudWatchMetrics": [{"Namespace": NAMESPACE, "Dimensions": sets, "Metrics": [{"Name": name, "Unit": unit}]}]},
        "Service": SERVICE, **dims, name: value,
    }), flush=True)


def instrument(name: str, soft: bool = False):
    """Wrap a LangGraph node: span + latency/error metrics + structured log.
    soft=True: swallow exceptions and record them in state['errors'] so the router can lower confidence
    and send the case to a human instead of failing the whole run."""
    def deco(fn):
        @wraps(fn)
        async def wrapper(state, config):
            bind(node=name, application_id=state.get("application_id"))
            log = logging.getLogger("uw-agent.node")
            t0 = time.perf_counter()
            with tracer.start_as_current_span(f"node.{name}") as span:
                span.set_attribute("uw.application_id", str(state.get("application_id", "")))
                span.set_attribute("uw.node", name)
                try:
                    out = await fn(state, config)
                    log.info("node ok")
                    return out
                except GraphInterrupt:
                    raise  # human-in-the-loop pause is not an error
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(trace.Status(trace.StatusCode.ERROR))
                    emit_metric("NodeErrors", 1, Node=name)
                    log.exception("node failed")
                    if not soft:
                        raise
                    return {"errors": [f"{name}: {type(exc).__name__}: {exc}"[:300]], "trace": [f"{name}: FAILED"]}
                finally:
                    emit_metric("NodeLatencyMs", (time.perf_counter() - t0) * 1000, "Milliseconds", Node=name)
        return wrapper
    return deco
