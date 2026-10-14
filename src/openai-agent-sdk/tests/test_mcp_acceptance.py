"""Acceptance opt-in and safe reporting without provider calls."""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from openai_agent_sdk import mcp_acceptance


def test_acceptance_requires_explicit_live_flag(monkeypatch):
    monkeypatch.setattr("sys.argv", ["mcp_acceptance"])
    monkeypatch.setattr(
        mcp_acceptance, "load_dotenv", lambda *args: pytest.fail("No opt-in")
    )
    with pytest.raises(SystemExit) as error:
        mcp_acceptance.main()
    assert error.value.code == 2


@pytest.mark.parametrize("failure", [False, True])
def test_acceptance_records_safe_metadata_after_cleanup(monkeypatch, tmp_path, failure):
    closed = []

    class Adapter:
        async def search(self, query):
            if failure:
                raise RuntimeError("private-key")
            return {"jobs": [{"description": "private job content"}]}

    @asynccontextmanager
    async def session(key):
        try:
            yield Adapter()
        finally:
            closed.append(True)

    monkeypatch.setenv("OPENWEBNINJA_API_KEY", "private-key")
    monkeypatch.setattr(mcp_acceptance, "open_search_session", session)
    report = asyncio.run(mcp_acceptance.check_mcp("private query"))
    assert closed == [True]
    assert report["outcome"] == ("error" if failure else "success")
    assert "private" not in json.dumps(report)
    if not failure:
        assert report["connection_closed"] is True


@pytest.mark.parametrize("compare", [False, True])
def test_live_command_records_report_and_direct_requires_separate_opt_in(
    monkeypatch, tmp_path, compare
):
    output = tmp_path / "report.json"
    args = ["mcp_acceptance", "--live", "--output", str(output)]
    if compare:
        args.append("--compare-direct")
    monkeypatch.setattr("sys.argv", args)
    monkeypatch.setattr(mcp_acceptance, "load_dotenv", lambda *args: None)
    monkeypatch.setenv("OPENWEBNINJA_API_KEY", "private-key")
    monkeypatch.setenv("JSEARCH_API_KEY", "private-direct-key")
    calls = []

    async def mcp(query):
        calls.append("mcp")
        return {"outcome": "success", "jobs": 0, "connection_closed": True}

    def direct(query, **kwargs):
        calls.append("direct")
        return {"jobs": []}

    monkeypatch.setattr(mcp_acceptance, "check_mcp", mcp)
    monkeypatch.setattr(mcp_acceptance, "search_jsearch", direct)
    mcp_acceptance.main()
    assert calls == (["mcp", "direct"] if compare else ["mcp"])
    report = json.loads(output.read_text())
    assert report["mcp"]["outcome"] == "success"
    assert "private" not in output.read_text()
