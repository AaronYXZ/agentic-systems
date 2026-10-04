"""Per-call metadata only. Never retain tool arguments or result content."""

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Literal, Protocol

from agents.tool_context import ToolContext

TraceSource = Literal[
    "mock", "jsearch", "unknown", "local_resume", "local_results", "local_matching"
]


@dataclass(frozen=True)
class ToolTrace:
    tool_name: str
    call_id: str
    duration_ms: float
    outcome: Literal["success", "error"]
    source: TraceSource
    error_class: str | None


class TraceContext(Protocol):
    tool_traces: list[ToolTrace]


def traced_call(
    ctx: ToolContext[TraceContext],
    *,
    source: TraceSource,
    operation: Callable[[], str],
) -> str:
    """Record success, reported failure, or exception without changing tool output."""
    started = perf_counter()
    outcome: Literal["success", "error"] = "success"
    error_class = None
    try:
        output = operation()
        if output.startswith("Error:"):
            outcome = "error"
            error_class = "ToolReportedError"
        return output
    except Exception as exc:
        outcome = "error"
        error_class = type(exc).__name__
        raise
    finally:
        ctx.context.tool_traces.append(ToolTrace(
            tool_name=ctx.tool_name,
            call_id=ctx.tool_call_id,
            duration_ms=(perf_counter() - started) * 1000,
            outcome=outcome,
            source=source,
            error_class=error_class,
        ))
