"""Standalone JSearch MCP adapter. The caller owns the initialized session."""

import asyncio
import json
import os
from datetime import datetime, timezone

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from mcp import ClientSession, MCPError, StdioServerParameters
from mcp.types import CallToolResult

from .jsearch import MAX_RESULTS, TIMEOUT_SECONDS, JSearchError, normalize_job

SERVER_PACKAGE = "@openwebninja/mcp-server@0.1.1"


def server_parameters(api_key: str = "") -> StdioServerParameters:
    """An empty key supports discovery only. Never log these parameters."""
    env = {
        name: os.environ[name]
        for name in ("PATH", "HOME", "TMPDIR", "SystemRoot")
        if name in os.environ
    }
    env["OPENWEBNINJA_API_KEY"] = api_key
    return StdioServerParameters(command="npx", args=["--yes", SERVER_PACKAGE], env=env)


def decode_result(result: CallToolResult) -> dict:
    """Decode the provider envelope without displaying provider error content."""
    if result.is_error:
        raise JSearchError("JSearch failed. Check OpenWeb Ninja access and quota.")
    try:
        payload = result.structured_content
        if result.content:
            if len(result.content) != 1 or result.content[0].type != "text":
                raise ValueError("Expected JSON text.")
            text_payload = json.loads(result.content[0].text)
            if payload is not None and payload != text_payload:
                raise ValueError("Conflicting results.")
            payload = text_payload
        if not isinstance(payload, dict):
            raise ValueError("Expected object.")
        return payload
    except ValueError as exc:
        raise JSearchError("JSearch returned an invalid response.") from exc


class OpenWebNinjaAdapter:
    """Expose only read-only search, never the server's subscription tools."""

    def __init__(self, session: ClientSession):
        self.session = session

    @classmethod
    async def discover(cls, session: ClientSession) -> "OpenWebNinjaAdapter":
        # The pinned server advertises all tools in a single response.
        try:
            page = await asyncio.wait_for(session.list_tools(), TIMEOUT_SECONDS)
        except (MCPError, OSError, TimeoutError) as exc:
            raise JSearchError("JSearch MCP discovery failed.") from exc
        tools = [tool for tool in page.tools if tool.name == "jsearch"]
        if len(tools) != 1 or page.next_cursor:
            raise JSearchError("JSearch MCP tool schema is incompatible.")
        schema = tools[0].input_schema
        try:
            Draft202012Validator.check_schema(schema)
            props = schema.get("properties", {})
            if (
                schema.get("type") != "object"
                or props.get("args", {}).get("type") != "object"
                or "search_v2" not in props.get("operation", {}).get("enum", [])
            ):
                raise ValueError("Missing search operation.")
            Draft202012Validator(schema).validate(
                {"operation": "search_v2", "args": {"query": "check", "num_pages": 1}}
            )
        except (ValueError, SchemaError, ValidationError) as exc:
            raise JSearchError("JSearch MCP tool schema is incompatible.") from exc
        return cls(session)

    async def search(
        self, query: str, *, country: str | None = None, remote_only: bool = False
    ) -> dict[str, object]:
        if not query.strip() or len(query) > 200:
            raise JSearchError("Search query must contain 1–200 characters.")
        args: dict[str, object] = {"query": query, "num_pages": 1}
        if country:
            args["country"] = country.lower()
        if remote_only:
            args["work_from_home"] = True
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(
                    "jsearch", {"operation": "search_v2", "args": args}
                ),
                TIMEOUT_SECONDS,
            )
        except (MCPError, OSError, TimeoutError) as exc:
            raise JSearchError(
                "JSearch MCP search failed. Check connectivity."
            ) from exc
        payload = decode_result(result)
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise JSearchError("JSearch returned an invalid response.")
        timestamp = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        try:
            jobs = [normalize_job(job, timestamp) for job in data["jobs"][:MAX_RESULTS]]
        except ValueError as exc:
            raise JSearchError("JSearch returned a malformed job record.") from exc
        return {"source": "jsearch", "retrieved_at": timestamp, "jobs": jobs}
