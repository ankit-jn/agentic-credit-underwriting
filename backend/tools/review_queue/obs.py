"""Shared Lambda observability helper (identical copy in every tool dir; a unit test enforces this).
JSON logs + CloudWatch Embedded Metric Format. Logs only allow-listed ids - never names or free text."""
import json
import os
import time
from functools import wraps

NAMESPACE = "UWAgent"
SERVICE = "tool"
_SAFE_KEYS = ("application_id", "applicant_id", "event_type", "tier")


def log(level: str, msg: str, **fields) -> None:
    """Print one JSON log line (ids only, never free text)."""
    print(json.dumps({"level": level, "msg": msg, "service": SERVICE, **fields}, default=str), flush=True)


def emit_metric(name: str, value: float = 1, unit: str = "Count", service: str = SERVICE, **dims: str) -> None:
    """Emit a CloudWatch EMF metric via stdout; dimension sets are [Service] and [Service, ...extra dims]."""
    sets = [["Service"]] + ([["Service", *dims]] if dims else [])
    print(json.dumps({
        "_aws": {"Timestamp": int(time.time() * 1000),
                 "CloudWatchMetrics": [{"Namespace": NAMESPACE, "Dimensions": sets, "Metrics": [{"Name": name, "Unit": unit}]}]},
        "Service": service, **dims, name: value,
    }, default=str), flush=True)


def observed(tool: str):
    """Decorator for Lambda handlers: structured logs, invocation/error/latency metrics and the X-Ray trace id."""
    def deco(fn):
        @wraps(fn)
        def wrapper(event, context=None):
            ids = {k: event[k] for k in _SAFE_KEYS if isinstance(event, dict) and k in event}
            req = getattr(context, "aws_request_id", "local")
            t0 = time.perf_counter()
            try:
                out = fn(event, context)
                log("INFO", "tool ok", tool=tool, request_id=req, trace_id=os.getenv("_X_AMZN_TRACE_ID", ""), **ids)
                return out
            except Exception as exc:
                emit_metric("ToolErrors", 1, Tool=tool)
                log("ERROR", "tool failed", tool=tool, request_id=req, error=f"{type(exc).__name__}: {exc}"[:300], **ids)
                raise
            finally:
                emit_metric("ToolInvocations", 1, Tool=tool)
                emit_metric("ToolLatencyMs", (time.perf_counter() - t0) * 1000, "Milliseconds", Tool=tool)
        return wrapper
    return deco
