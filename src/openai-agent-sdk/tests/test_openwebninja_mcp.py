"""Offline checks for the owned MCP search boundary."""

import asyncio
import json

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from openai_agent_sdk.jsearch import JSearchError
from openai_agent_sdk.openwebninja_mcp import OpenWebNinjaAdapter, decode_result

SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {"type": "string", "enum": ["search_v2"]},
        "args": {"type": "object"},
    },
    "required": ["operation"],
}
JOB = {
    "job_id": "one",
    "job_title": "Python Engineer",
    "employer_name": "Example",
    "job_description": "Python required.",
    "job_apply_link": "https://example.com/one",
}


class Session:
    def __init__(self, payload, schema=SCHEMA):
        self.payload = payload
        self.schema = schema
        self.calls = []

    async def list_tools(self):
        return ListToolsResult(tools=[Tool(name="jsearch", input_schema=self.schema)])

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(self.payload))]
        )


def search(session):
    async def run():
        adapter = await OpenWebNinjaAdapter.discover(session)
        assert not session.calls
        return await adapter.search("Python", country="US", remote_only=True)

    return asyncio.run(run())


def test_search_contract_and_cap():
    session = Session({"data": {"jobs": [JOB] * 12}})
    output = search(session)
    assert session.calls == [
        (
            "jsearch",
            {
                "operation": "search_v2",
                "args": {
                    "query": "Python",
                    "num_pages": 1,
                    "country": "us",
                    "work_from_home": True,
                },
            },
        ),
    ]
    assert len(output["jobs"]) == 10
    assert output["source"] == "jsearch"
    assert output["jobs"][0]["id"] == "jsearch:one"
    assert output["jobs"][0]["retrieved_at"] == output["retrieved_at"]
    assert search(Session({"data": {"jobs": []}}))["jobs"] == []


def test_incompatible_schema_prevents_search():
    session = Session({}, schema={"type": "object"})
    with pytest.raises(JSearchError, match="schema"):
        search(session)
    assert not session.calls


@pytest.mark.parametrize("payload", [{}, {"data": {"jobs": [{"job_id": "bad"}]}}])
def test_invalid_jobs_rejected(payload):
    with pytest.raises(JSearchError):
        search(Session(payload))


def test_structured_content_and_safe_provider_errors():
    payload = {"data": {"jobs": []}}
    assert (
        decode_result(CallToolResult(content=[], structured_content=payload)) == payload
    )
    with pytest.raises(JSearchError) as error:
        decode_result(
            CallToolResult(
                content=[TextContent(type="text", text="secret provider details")],
                is_error=True,
            )
        )
    assert "secret" not in str(error.value)
    with pytest.raises(JSearchError):
        decode_result(
            CallToolResult(
                content=[TextContent(type="text", text="{}")],
                structured_content=payload,
            )
        )
