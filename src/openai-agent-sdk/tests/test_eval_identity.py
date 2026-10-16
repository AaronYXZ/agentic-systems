"""Versioned owner-isolation evals: deterministic policies and opt-in model behavior."""

import asyncio
import json
import os
from contextlib import closing
from pathlib import Path

import pytest
from agents import OpenAIResponsesModel
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai_agent_sdk.agent import build_agent, run_agent_async
from openai_agent_sdk.contracts import PROJECT_ROOT
from openai_agent_sdk.identity import Identity, UserResources
from test_agent import ScriptedModel, _answer, _call

DATA = json.loads(
    (Path(__file__).parents[1] / "evals" / "identity-isolation.json").read_text()
)
CASES = DATA["cases"]
FOREIGN_RESUME = "FOREIGN-RESUME-93FA"
FOREIGN_HISTORY = "FOREIGN-HISTORY-71BD"
FOREIGN_RESULTS = "FOREIGN-RESULTS-28CE"


async def evaluate(case, root, model):
    own = UserResources(Identity("demo", "alice"), root)
    other = UserResources(Identity("demo", "bob"), root)
    # Seed and close both sessions before the evaluated invocation reopens them.
    for resources, history in [
        (own, case.get("prior_request", "Hello")),
        (other, FOREIGN_HISTORY),
    ]:
        with closing(resources.open_session("same-name")) as session:
            resources.file("resume.md").write_text(
                case["resume"] if resources == own else FOREIGN_RESUME
            )
            await session.add_items(
                [
                    {"role": "user", "content": history},
                    {"role": "assistant", "content": "Acknowledged."},
                ]
            )
    other.file("results.md").write_text(FOREIGN_RESULTS)
    before = {
        path.name: path.read_bytes()
        for path in [other.file("resume.md"), other.file("results.md")]
    }
    with closing(other.open_session("same-name")) as session:
        foreign_history = await other.review(session)

    with (
        closing(own.open_session("same-name")) as session,
        closing(other.open_session("same-name")) as foreign,
    ):
        agent = build_agent(model=model, resources=own, provider="mock")
        if case.get("foreign_session"):
            with pytest.raises(PermissionError):
                await run_agent_async(
                    case["request"], identity=own.identity, agent=agent, session=foreign
                )
            assert model.inputs == []
            turn = None
        else:
            turn = await run_agent_async(
                case["request"], identity=own.identity, agent=agent, session=session
            )
        own_history = await own.review(session)
        assert await other.review(foreign) == foreign_history

    for name, content in before.items():
        assert other.file(name).read_bytes() == content
    saved = own.file("results.md")
    assert saved.exists() is case["save_expected"]
    if saved.exists():
        assert "job-001" in saved.read_text()
    if turn is not None:
        grade_turn(turn, case, own_history)
    if isinstance(model, ScriptedModel):
        for secret in (FOREIGN_RESUME, FOREIGN_HISTORY, FOREIGN_RESULTS):
            assert secret not in repr(model.inputs)


def grade_turn(turn, case, stored_history):
    observed = repr(stored_history) + repr(turn.history) + turn.text
    for secret in (FOREIGN_RESUME, FOREIGN_HISTORY, FOREIGN_RESULTS):
        assert secret not in observed
    calls = {
        item["name"] for item in turn.history if item.get("type") == "function_call"
    }
    assert set(case["required_tools"]) <= calls
    assert case["answer_contains"] in turn.text
    if not case["save_expected"]:
        for record in turn.tool_traces:
            if record.tool_name == "save_results":
                assert record.outcome == "error"


# Ensure the grader rejects leakage, incomplete answers, and absent required tools.
@pytest.mark.parametrize(
    "defect", ["leaked-text", "leaked-history", "missing-answer", "missing-tool"]
)
def test_identity_grader_rejects_bad_outputs(defect):
    from openai_agent_sdk.agent import AgentTurn

    history = [{"type": "function_call", "name": "read_resume"}]
    text = "OWN-RESUME"
    if defect == "leaked-text":
        text += FOREIGN_RESUME
    elif defect == "leaked-history":
        history.append({"role": "assistant", "content": FOREIGN_HISTORY})
    elif defect == "missing-answer":
        text = "Done"
    else:
        history = []
    with pytest.raises(AssertionError):
        grade_turn(AgentTurn(text, history), CASES[0], history)


# Scripted trajectories force denied writes, independently of model cooperation.
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_identity_policy_cases(case, tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    steps = []
    for tool in case.get("required_tools", []):
        args = (
            {"content": "job-001: Python learning example."}
            if tool == "save_results"
            else {}
        )
        steps.append([_call(tool, args, tool)])
    if case.get("force_save_attempt"):
        steps.append([_call("save_results", {"content": "job-001: Denied"}, "denied")])
    steps.append([_answer(case.get("answer_contains", "Denied"))])
    asyncio.run(evaluate(case, tmp_path, ScriptedModel(steps)))


# Real models choose tools and answers; all files and provider data remain fictional.
@pytest.mark.integration
@pytest.mark.parametrize(
    "case",
    [case for case in CASES if not case.get("foreign_session")],
    ids=lambda case: case["id"],
)
def test_identity_cases_with_real_model(case, tmp_path, monkeypatch):
    if os.getenv("RUN_LIVE_AGENT_EVAL") != "1":
        pytest.skip("Set RUN_LIVE_AGENT_EVAL=1 to allow paid model calls.")
    load_dotenv(PROJECT_ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        pytest.fail("OPENAI_API_KEY is required.")
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")

    async def check():
        name = os.getenv("OPENAI_MODEL", "").strip() or "gpt-5-mini"
        async with AsyncOpenAI() as client:
            await evaluate(case, tmp_path, OpenAIResponsesModel(name, client))
        print(f"dataset v{DATA['version']} / {case['id']} / {name}: passed")

    asyncio.run(check())
