"""Verify metadata correlation, failure recording, and content exclusion."""

import asyncio
from dataclasses import asdict

import pytest
from agents.tool_context import ToolContext
from openai_agent_sdk.agent import JobAgentContext
from openai_agent_sdk.cli import _print_trace
from openai_agent_sdk.tool_tracing import traced_call


def context():
    return ToolContext(
        context=JobAgentContext(save_allowed=False),
        tool_name="read_resume", tool_call_id="call-test",
        tool_arguments='{"secret": "private-key"}',
    )


@pytest.mark.parametrize("output,outcome,error_class", [
    ("Private resume text", "success", None),
    ("Error: Private resume text", "error", "ToolReportedError"),
])
def test_records_metadata_without_content(output, outcome, error_class, capsys):
    ctx = context()
    assert traced_call(ctx, source="local_resume", operation=lambda: output) == output
    record, = ctx.context.tool_traces
    assert record.call_id == "call-test"
    assert record.tool_name == "read_resume"
    assert record.duration_ms >= 0
    assert record.outcome == outcome
    assert record.error_class == error_class
    assert record.source == "local_resume"
    _print_trace(record)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "call-test" in captured.err
    assert "Private resume text" not in captured.err
    assert "private-key" not in captured.err
    assert set(asdict(record)) == {
        "tool_name", "call_id", "duration_ms", "outcome", "source", "error_class"
    }


def test_exception_is_recorded_and_reraised_without_its_message():
    ctx = context()

    def fail():
        raise ValueError("private-key")

    with pytest.raises(ValueError, match="private-key"):
        traced_call(ctx, source="local_resume", operation=fail)
    record, = ctx.context.tool_traces
    assert record.error_class == "ValueError"
    assert record.outcome == "error"
    assert "private-key" not in repr(record)


@pytest.mark.parametrize("failure", [None, ValueError, asyncio.CancelledError])
def test_async_trace_awaits_and_records_safe_metadata(failure, monkeypatch):
    from openai_agent_sdk.tool_tracing import traced_call_async

    ctx = context()
    ctx.tool_name = "search_jobs"
    ticks = iter([1.0, 1.25])
    monkeypatch.setattr(
        "openai_agent_sdk.tool_tracing.perf_counter", lambda: next(ticks)
    )

    async def operation():
        await asyncio.sleep(0)
        assert not ctx.context.tool_traces
        if failure:
            raise failure("private-key")
        return "Private job description"

    if failure:
        with pytest.raises(failure):
            asyncio.run(traced_call_async(ctx, source="mcp", operation=operation))
    else:
        assert asyncio.run(traced_call_async(
            ctx, source="mcp", operation=operation
        )) == "Private job description"
    record, = ctx.context.tool_traces
    assert record.duration_ms == 250
    assert record.call_id == "call-test"
    assert record.source == "mcp"
    assert record.outcome == ("error" if failure else "success")
    assert record.error_class == (failure.__name__ if failure else None)
    assert "private-key" not in repr(record)
    assert "Private job description" not in repr(record)


def test_async_reported_error_keeps_output():
    from openai_agent_sdk.tool_tracing import traced_call_async

    ctx = context()

    async def operation():
        return "Error: private job content"

    assert asyncio.run(traced_call_async(
        ctx, source="mcp", operation=operation
    )) == "Error: private job content"
    record, = ctx.context.tool_traces
    assert (record.outcome, record.error_class) == ("error", "ToolReportedError")
    assert "private job content" not in repr(record)
