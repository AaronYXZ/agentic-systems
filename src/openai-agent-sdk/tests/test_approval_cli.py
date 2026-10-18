"""Exercise interactive approval through the real SDK, with a scripted model."""

import asyncio
import json
from contextlib import closing

import pytest
from openai_agent_sdk import cli
from openai_agent_sdk.agent import RunBudgetExceeded, build_agent, run_agent
from openai_agent_sdk.approvals import ApprovalDecision, ApprovalError
from openai_agent_sdk.identity import Identity, UserResources
from test_agent import ScriptedModel, _answer, _call


def submission(job_id="job-001", call_id="submit"):
    return _call("submit_application", {"job_id": job_id, "cover_letter": "Synthetic"},
                 call_id)


@pytest.mark.parametrize("decisions,expected", [
    (["approve"], True), (["reject"], False), (["yes", "approve"], True),
])
def test_cli_reviews_then_resumes(tmp_path, monkeypatch, capsys, decisions, expected):
    owner = UserResources(Identity("demo", "alice"), tmp_path)
    model = ScriptedModel([[submission()], [_answer("Finished")]])
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    monkeypatch.setenv("JOB_SEARCH_PROVIDER", "mock")
    monkeypatch.setattr(cli, "load_dotenv", lambda path: None)
    monkeypatch.setattr(cli, "UserResources", lambda identity: owner)
    monkeypatch.setattr(cli, "build_agent", lambda **kwargs: build_agent(
        model=model, **kwargs))
    message = "Apply to job-001 with cover letter Synthetic"
    answers = iter([message, *decisions, "quit"])

    def human_input(prompt):
        if prompt == "Type approve or reject: ":
            assert not owner.file("submissions.jsonl").exists()
        return next(answers)

    monkeypatch.setattr("builtins.input", human_input)
    cli.main(["--approvals", "--tenant", "demo", "--user", "alice",
              "--session", "approval-test"])
    output = capsys.readouterr().out
    assert "Approval required: simulated application" in output
    assert '"job_id": "job-001"' in output
    assert '"cover_letter": "Synthetic"' in output
    assert "Agent > Finished" in output
    log = owner.file("submissions.jsonl")
    assert log.exists() is expected
    if expected:
        assert len(log.read_text().splitlines()) == 1
        assert json.loads(log.read_text())["job_id"] == "job-001"
    with closing(owner.open_session("approval-test")) as session:
        history = asyncio.run(owner.review(session))
    assert sum(item.get("role") == "user" for item in history) == 1
    assert any(item.get("type") == "function_call_output" for item in history)


def test_missing_handler_fails_closed_and_restores_history(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    owner = UserResources(Identity("demo", "alice"), tmp_path)
    model = ScriptedModel([[submission()]])
    with closing(owner.open_session("pending")) as session:
        with pytest.raises(ValueError, match="approval handler"):
            run_agent("Apply to job-001", identity=owner.identity, session=session,
                      agent=build_agent(model=model, provider="mock", resources=owner,
                                        enable_submissions=True))
        assert asyncio.run(owner.review(session)) == []
    assert not owner.file("submissions.jsonl").exists()


def test_tool_budget_is_shared_across_approval_resumes(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    owner = UserResources(Identity("demo", "alice"), tmp_path)
    model = ScriptedModel([[submission()], [submission("job-002", "second")]])
    with pytest.raises(RunBudgetExceeded):
        run_agent("Apply to job-001 and job-002", identity=owner.identity,
                  max_tool_calls=1,
                  approval_handler=lambda pending: ApprovalDecision(pending, True),
                  agent=build_agent(model=model, provider="mock", resources=owner,
                                    enable_submissions=True))
    records = owner.file("submissions.jsonl").read_text().splitlines()
    assert len(records) == 1
    assert json.loads(records[0])["job_id"] == "job-001"


def test_cli_eof_rejects_pending_action(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    owner = UserResources(Identity("demo", "alice"), tmp_path)
    model = ScriptedModel([[submission()], [_answer("Rejected")]])

    def disconnected(prompt):
        raise EOFError

    turn = run_agent("Apply to job-001", identity=owner.identity,
                     agent=build_agent(model=model, resources=owner, provider="mock",
                                       enable_submissions=True),
                     approval_handler=lambda pending: cli.request_approval(
                         pending, input_fn=disconnected, output_fn=lambda text: None))
    assert turn.text == "Rejected"
    assert not owner.file("submissions.jsonl").exists()


@pytest.mark.parametrize("arguments", [["--approvals"],
                                      ["--approvals", "--session", "demo"]])
def test_approval_cli_requires_identity(arguments):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2


def test_non_boolean_decision_cannot_approve(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    owner = UserResources(Identity("demo", "alice"), tmp_path)
    with pytest.raises(ApprovalError, match="pending approval request"):
        run_agent("Apply to job-001", identity=owner.identity,
                  approval_handler=lambda pending: "approve",
                  agent=build_agent(model=ScriptedModel([[submission()]]),
                                    resources=owner, provider="mock",
                                    enable_submissions=True))
    assert not owner.file("submissions.jsonl").exists()
