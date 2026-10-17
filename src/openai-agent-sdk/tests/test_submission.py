"""Submission contract/storage checks and a real SDK approval pause, all offline."""

import asyncio
import json

import pytest
from agents import Agent, RunConfig, Runner
from agents.tool_context import ToolContext
from openai_agent_sdk.agent import JobAgentContext, build_agent
from openai_agent_sdk.identity import Identity, UserResources
from openai_agent_sdk.tools.submit_application import (
    build_submission_tool,
    record_submission,
)
from test_agent import ScriptedModel, _call


@pytest.fixture
def owner(tmp_path):
    return UserResources(Identity("demo", "alice"), tmp_path)


def test_records_append_only_in_owner_log(owner):
    result = record_submission(owner, "job-001", "Synthetic learning application.")
    record_submission(owner, "job-002", "Second synthetic application.")
    records = [json.loads(line) for line in owner.file("submissions.jsonl").read_text()
               .splitlines()]
    assert result == "Simulated application recorded locally."
    assert records == [
        {"job_id": "job-001", "cover_letter": "Synthetic learning application.",
         "status": "simulated"},
        {"job_id": "job-002", "cover_letter": "Second synthetic application.",
         "status": "simulated"},
    ]
    other = UserResources(Identity("demo", "bob"), owner.root)
    assert not other.file("submissions.jsonl").exists()


@pytest.mark.parametrize("job_id,letter", [
    ("jsearch:live", "Synthetic"), ("job-999", "Synthetic"),
    ("job-001", " "), ("job-001", "x" * 8001),
])
def test_invalid_submission_cannot_create_records(owner, job_id, letter):
    with pytest.raises(ValueError):
        record_submission(owner, job_id, letter)
    assert not owner.file("submissions.jsonl").exists()


def test_submission_log_cannot_link_to_another_owner(owner):
    other = UserResources(Identity("other", "bob"), owner.root)
    other.file("submissions.jsonl").parent.mkdir(parents=True)
    other.file("submissions.jsonl").write_text("OTHER-OWNER-CANARY\n")
    owner.file("submissions.jsonl").parent.mkdir(parents=True)
    owner.file("submissions.jsonl").symlink_to(other.file("submissions.jsonl"))
    with pytest.raises(PermissionError):
        record_submission(owner, "job-001", "Synthetic")
    assert other.file("submissions.jsonl").read_text() == "OTHER-OWNER-CANARY\n"


def test_sdk_pauses_before_submission_body_runs(owner):
    async def check():
        tool = build_submission_tool(owner)
        assert tool.needs_approval is True
        assert set(tool.params_json_schema["properties"]) == {"job_id", "cover_letter"}
        model = ScriptedModel([[
            _call("submit_application",
                  {"job_id": "job-001", "cover_letter": "Synthetic"}, "submit-1")
        ]])
        context = JobAgentContext(save_allowed=False, identity=owner.identity)
        result = await Runner.run(
            Agent(name="Approval lesson", model=model, tools=[tool]),
            "Simulate an application to job-001", context=context,
            run_config=RunConfig(tracing_disabled=True),
        )
        assert len(result.interruptions) == 1
        assert result.interruptions[0].raw_item.call_id == "submit-1"
        assert result.to_state() is not None
        assert context.tool_traces == []
        assert not owner.file("submissions.jsonl").exists()

    asyncio.run(check())
    assert "submit_application" not in {tool.name for tool in build_agent(
        resources=owner, provider="mock"
    ).tools}


@pytest.mark.parametrize("violation", ["owner", "arguments"])
def test_tool_body_rechecks_identity_and_arguments(owner, violation):
    tool = build_submission_tool(owner)
    args = {"job_id": "job-001", "cover_letter": "Synthetic"}
    identity = owner.identity
    if violation == "owner":
        identity = Identity("demo", "bob")
    else:
        args["user_id"] = "bob"
    payload = json.dumps(args)
    context = JobAgentContext(save_allowed=False, identity=identity)
    ctx = ToolContext(context=context, tool_name="submit_application",
                      tool_call_id="denied", tool_arguments=payload)
    # Trusted direct invocation tests the body, not SDK approval enforcement.
    asyncio.run(tool.on_invoke_tool(ctx, payload))
    assert context.tool_traces[0].error_class == "PermissionError"
    assert not owner.file("submissions.jsonl").exists()
