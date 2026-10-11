"""Connection ownership, cancellation, and CLI recovery without provider calls."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from openai_agent_sdk.agent import AgentTurn
from openai_agent_sdk.cli import run_mcp_cli
from openai_agent_sdk.jsearch import JSearchError
from openai_agent_sdk.openwebninja_mcp import OpenWebNinjaAdapter, open_search_session


@pytest.mark.parametrize("failure,during_initialization", [
    (None, False), (RuntimeError, False), (RuntimeError, True),
    (asyncio.CancelledError, False), (asyncio.CancelledError, True),
])
def test_session_closes_on_exit_failure_and_cancellation(
    monkeypatch, failure, during_initialization
):
    events = []

    @asynccontextmanager
    async def transport(params):
        events.append("process opened")
        try:
            yield (None, None)
        finally:
            events.append("process closed")

    class Session:
        def __init__(self, read, write):
            pass

        async def __aenter__(self):
            events.append("session opened")
            return self

        async def __aexit__(self, *args):
            events.append("session closed")

        async def initialize(self):
            events.append("initialized")
            if during_initialization and failure:
                raise failure()

    async def discover(cls, session):
        return cls(session)

    monkeypatch.setattr("openai_agent_sdk.openwebninja_mcp.stdio_client", transport)
    monkeypatch.setattr("openai_agent_sdk.openwebninja_mcp.ClientSession", Session)
    monkeypatch.setattr(OpenWebNinjaAdapter, "discover", classmethod(discover))

    async def run():
        async with open_search_session("test-key"):
            if failure:
                raise failure()

    if failure:
        with pytest.raises(failure):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert events == [
        "process opened",
        "session opened",
        "initialized",
        "session closed",
        "process closed",
    ]


@pytest.mark.parametrize("ending", ["quit", EOFError, KeyboardInterrupt])
def test_cli_reuses_session_and_preserves_history_after_failed_turn(
    monkeypatch, ending
):
    events = []
    adapter = object()
    history = [{"role": "user", "content": "First"}]
    calls = []
    messages = iter(["First", "Fail", "Retry", ending])

    @asynccontextmanager
    async def connection(key):
        events.append("opened")
        try:
            yield adapter
        finally:
            events.append("closed")

    async def turn(message, prior, connection):
        calls.append((message, prior, connection))
        if message == "Fail":
            raise RuntimeError("secret")
        return AgentTurn("Done", history)

    monkeypatch.setattr("openai_agent_sdk.cli.open_search_session", connection)

    def read_input(prompt):
        item = next(messages)
        if isinstance(item, type):
            raise item()
        return item

    outputs = []
    asyncio.run(
        run_mcp_cli(
            api_key="test", agent_fn=turn, input_fn=read_input, output_fn=outputs.append
        )
    )
    assert events == ["opened", "closed"]
    assert calls == [
        ("First", None, adapter),
        ("Fail", history, adapter),
        ("Retry", history, adapter),
    ]
    assert outputs[-1] == "Goodbye."
    assert all("secret" not in output for output in outputs)


def test_search_timeout_does_not_retry(monkeypatch):
    calls = []

    class SlowSession:
        async def call_tool(self, name, arguments):
            calls.append(name)
            await asyncio.sleep(1)

    monkeypatch.setattr("openai_agent_sdk.openwebninja_mcp.TIMEOUT_SECONDS", 0.01)
    with pytest.raises(JSearchError, match="connectivity"):
        asyncio.run(OpenWebNinjaAdapter(SlowSession()).search("Python"))
    assert calls == ["jsearch"]
