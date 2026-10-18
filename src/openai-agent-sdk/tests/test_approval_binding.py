"""Approval decisions must match the immutable review and remain unexpired."""

import asyncio
import json
from contextlib import closing
from dataclasses import FrozenInstanceError, replace

import pytest
from agents import Agent, RunConfig, Runner
from agents.tool_context import ToolContext
from openai_agent_sdk.agent import JobAgentContext, build_agent, run_agent
from openai_agent_sdk.approvals import (
    ApprovalDecision,
    ApprovalError,
    ApprovalRequest,
    canonical_arguments,
)
from openai_agent_sdk.identity import Identity, UserResources
from openai_agent_sdk.tools.submit_application import build_submission_tool
from test_agent import ScriptedModel, _answer, _call

ARGS = {"job_id": "job-001", "cover_letter": "Synthetic"}


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    return UserResources(Identity("demo", "alice"), tmp_path)


def model():
    return ScriptedModel([
        [_call("submit_application", ARGS, "submit")], [_answer("Finished")],
    ])


@pytest.mark.parametrize("field,value", [
    ("identity", Identity("demo", "bob")),
    ("identity", Identity("other", "alice")),
    ("run_id", "other-run"), ("call_id", "other-call"),
    ("tool_name", "save_results"), ("arguments", '{"job_id":"job-002"}'),
    ("expires_at", float("inf")),
])
def test_altered_decision_is_denied_before_resume(owner, field, value):
    scripted = model()
    with pytest.raises(ApprovalError, match="pending approval request"):
        run_agent("Apply to job-001", identity=owner.identity,
                  agent=build_agent(model=scripted, resources=owner, provider="mock",
                                    enable_submissions=True),
                  approval_handler=lambda request: ApprovalDecision(
                      replace(request, **{field: value}), True))
    assert len(scripted.inputs) == 1
    assert not owner.file("submissions.jsonl").exists()


def test_review_request_cannot_be_edited(owner):
    def edit(request):
        request.arguments = '{"job_id":"job-002"}'

    with pytest.raises(FrozenInstanceError):
        run_agent("Apply to job-001", identity=owner.identity,
                  agent=build_agent(model=model(), resources=owner, provider="mock",
                                    enable_submissions=True), approval_handler=edit)
    assert not owner.file("submissions.jsonl").exists()


def test_old_decision_cannot_authorize_another_run(owner):
    decisions = []

    def approve(request):
        decision = ApprovalDecision(request, True)
        decisions.append(decision)
        return decision

    for handler in (approve, lambda request: decisions[0]):
        agent = build_agent(model=model(), resources=owner, provider="mock",
                            enable_submissions=True)
        if handler is approve:
            run_agent("Apply to job-001", identity=owner.identity,
                      agent=agent, approval_handler=handler)
        else:
            with pytest.raises(ApprovalError):
                run_agent("Apply to job-001", identity=owner.identity,
                          agent=agent, approval_handler=handler)
    assert len(owner.file("submissions.jsonl").read_text().splitlines()) == 1


def test_expired_review_cannot_resume_and_history_is_restored(owner, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("openai_agent_sdk.agent.monotonic", lambda: clock[0])
    monkeypatch.setattr("openai_agent_sdk.approvals.monotonic", lambda: clock[0])

    def approve_late(request):
        clock[0] = request.expires_at
        return ApprovalDecision(request, True)

    with closing(owner.open_session("expiry")) as session:
        with pytest.raises(ApprovalError, match="expired"):
            run_agent("Apply to job-001", identity=owner.identity, session=session,
                      agent=build_agent(model=model(), resources=owner, provider="mock",
                                        enable_submissions=True),
                      approval_handler=approve_late, approval_timeout_seconds=10)
        assert asyncio.run(owner.review(session)) == []
    assert not owner.file("submissions.jsonl").exists()


@pytest.mark.parametrize("change", ["arguments", "run", "expiry", "missing"])
def test_tool_checks_grant_again_before_storage(owner, monkeypatch, change):
    monkeypatch.setattr("openai_agent_sdk.approvals.monotonic", lambda: 1)
    context = JobAgentContext(save_allowed=False, identity=owner.identity)
    request = ApprovalRequest(
        owner.identity, context.run_id, "submit", "submit_application",
        canonical_arguments(json.dumps(ARGS)), 0 if change == "expiry" else 10,
    )
    context.approved_actions["submit"] = request
    args = dict(ARGS)
    if change == "arguments":
        args["cover_letter"] = "Changed after approval"
    elif change == "run":
        context.run_id = "other-run"
    elif change == "missing":
        context.approved_actions.clear()

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid grant reached storage")

    monkeypatch.setattr(
        "openai_agent_sdk.tools.submit_application.record_submission", forbidden
    )
    payload = json.dumps(args)
    ctx = ToolContext(context=context, tool_name="submit_application",
                      tool_call_id="submit", tool_arguments=payload)
    asyncio.run(build_submission_tool(owner).on_invoke_tool(ctx, payload))
    assert context.tool_traces[0].outcome == "error"
    assert not owner.file("submissions.jsonl").exists()


def test_sdk_approval_alone_cannot_bypass_application_grant(owner):
    async def check():
        context = JobAgentContext(save_allowed=False, identity=owner.identity)
        agent = Agent(name="Direct SDK", model=model(),
                      tools=[build_submission_tool(owner)])
        config = RunConfig(tracing_disabled=True)
        result = await Runner.run(agent, "Apply", context=context, run_config=config)
        state = result.to_state()
        state.approve(result.interruptions[0])
        await Runner.run(agent, state, run_config=config)
        assert context.tool_traces[0].error_class == "PermissionError"
        assert not owner.file("submissions.jsonl").exists()

    asyncio.run(check())


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_approval_timeout_must_be_finite_and_positive(timeout):
    with pytest.raises(ValueError, match="finite and positive"):
        run_agent("Apply", approval_timeout_seconds=timeout)
