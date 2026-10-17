"""Direct SDK tool calls must enforce access without relying on the runner."""

import asyncio
import json

import pytest
from agents.tool_context import ToolContext
from openai_agent_sdk.agent import JobAgentContext, build_agent
from openai_agent_sdk.identity import Identity, UserResources

TOOLS = [
    ("search_jobs", "mock", {"query": "Python"}),
    ("search_jobs", "jsearch", {"query": "Python"}),
    ("search_jobs", "mcp", {"query": "Python"}),
    ("read_resume", "mock", {}),
    ("search_documents", "mock", {"query": "Python"}),
    ("save_results", "mock", {"content": "job-001: Python"}),
    ("assess_fit", "jsearch", {"job_id": "jsearch:guessed"}),
]


@pytest.fixture
def blocked_io(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Protected operation ran before access was checked")

    for name in ("search_job_records", "read_resume_file", "save_results_file",
                 "search_document_records", "assess_live_fit"):
        monkeypatch.setattr(f"openai_agent_sdk.agent.{name}", unexpected)

    class Provider:
        async def search(self, *args, **kwargs):
            unexpected()

    return Provider()


def invoke(agent, name, arguments, identity):
    payload = json.dumps(arguments)
    context = JobAgentContext(save_allowed=True, identity=identity)
    ctx = ToolContext(context=context, tool_name=name,
                      tool_call_id="access-test", tool_arguments=payload)
    tool = next(tool for tool in agent.tools if tool.name == name)
    output = asyncio.run(tool.on_invoke_tool(ctx, payload))
    return output, context


@pytest.mark.parametrize("name,provider,arguments", TOOLS)
@pytest.mark.parametrize(
    "violation", ["other-user", "other-tenant", "missing", "extra"]
)
def test_each_tool_rejects_access_before_io(
    tmp_path, blocked_io, name, provider, arguments, violation
):
    owner = Identity("demo", "alice")
    agent = build_agent(resources=UserResources(owner, tmp_path),
                        provider=provider, mcp_adapter=blocked_io)
    identity = {
        "other-user": Identity("demo", "bob"),
        "other-tenant": Identity("other", "alice"),
        "missing": None,
        "extra": owner,
    }[violation]
    if violation == "extra":
        arguments = {**arguments, "tenant_id": "other", "user_id": "bob",
                     "path": "/private/other/resume.md"}
    _, context = invoke(agent, name, arguments, identity)
    trace, = context.tool_traces
    assert (trace.outcome, trace.error_class) == ("error", "PermissionError")
    assert context.searched_jobs == {}


@pytest.mark.parametrize("provider", ["mock", "jsearch", "mcp"])
def test_unscoped_search_cannot_serve_scoped_context(blocked_io, provider):
    agent = build_agent(provider=provider, mcp_adapter=blocked_io)
    _, context = invoke(agent, "search_jobs", {"query": "Python"},
                        Identity("demo", "alice"))
    assert context.tool_traces[0].error_class == "PermissionError"


@pytest.mark.parametrize("name,filename,arguments", [
    ("read_resume", "resume.md", {}),
    ("search_documents", "documents.json", {"query": "Python"}),
    ("save_results", "results.md", {"content": "job-001: Python"}),
])
def test_private_paths_are_checked_when_tool_runs(tmp_path, name, filename, arguments):
    owner = UserResources(Identity("demo", "alice"), tmp_path)
    other = UserResources(Identity("demo", "bob"), tmp_path)
    for resources in (owner, other):
        resources.file(filename).parent.mkdir(parents=True, exist_ok=True)
    other.file(filename).write_text("OTHER-OWNER-CANARY")
    agent = build_agent(resources=owner, provider="mock")
    owner.file(filename).symlink_to(other.file(filename))
    output, context = invoke(agent, name, arguments, owner.identity)
    assert context.tool_traces[0].error_class == "PermissionError"
    assert "OTHER-OWNER-CANARY" not in output
    assert other.file(filename).read_text() == "OTHER-OWNER-CANARY"


def test_guessed_fit_id_does_not_read_resume(tmp_path, blocked_io):
    owner = Identity("demo", "alice")
    agent = build_agent(resources=UserResources(owner, tmp_path), provider="jsearch")
    output, context = invoke(agent, "assess_fit", {"job_id": "jsearch:guessed"}, owner)
    assert "Search for this live job in the current turn first" in output
    assert context.searched_jobs == {}
