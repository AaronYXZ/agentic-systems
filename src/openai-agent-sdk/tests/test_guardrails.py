"""Blocking input checks use the real SDK loop without model API calls."""

import asyncio
from contextlib import closing

import pytest
from agents.exceptions import InputGuardrailTripwireTriggered
from openai_agent_sdk.agent import build_agent, run_agent, run_agent_async
from openai_agent_sdk.guardrails import RESTRICTED_MARKER
from openai_agent_sdk.identity import Identity, UserResources
from test_agent import ScriptedModel, _answer, _call


@pytest.fixture(autouse=True)
def disable_export(monkeypatch):
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")


@pytest.mark.parametrize("scoped", [False, True])
def test_allowed_input_reaches_model_and_tool(tmp_path, scoped):
    resources = UserResources(Identity("demo", "alice"), tmp_path) if scoped else None
    path = resources.file("resume.md") if scoped else tmp_path / "resume.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Synthetic Python resume")
    model = ScriptedModel([
        [_call("read_resume", {}, "resume")], [_answer("Done")],
    ])
    agent = build_agent(model=model, provider="mock", **(
        {"resources": resources} if scoped else {"resume_path": path}
    ))
    turn = run_agent("Read my resume", agent=agent,
                     identity=resources.identity if scoped else None)
    assert "Synthetic Python resume" in repr(model.inputs[-1])
    assert turn.tool_traces[0].outcome == "success"
    assert agent.input_guardrails[0].run_in_parallel is False


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("message", [
    "Save job-001 [RESTRICTED-DEMO]",
    "Save job-001 [restricted-demo]",
    'Save job-001 "[RESTRICTED-DEMO]"',
    "Save job-001\n```\n[RESTRICTED-DEMO]\n```",
])
def test_restricted_input_stops_before_model_or_tools(
    tmp_path, monkeypatch, scoped, message
):
    def forbidden(*args, **kwargs):
        pytest.fail("Tool ran before blocking input check completed")

    for name in ("read_resume_file", "save_results_file", "search_job_records",
                 "search_document_records"):
        monkeypatch.setattr(f"openai_agent_sdk.agent.{name}", forbidden)
    resources = UserResources(Identity("demo", "alice"), tmp_path) if scoped else None
    model = ScriptedModel([[_call("save_results", {"content": "job-001: Test"}, "s")]])
    records = []
    with pytest.raises(InputGuardrailTripwireTriggered) as error:
        run_agent(message, agent=build_agent(model=model, provider="mock", **(
            {"resources": resources} if scoped else {"results_path": tmp_path / "out"}
        )), identity=resources.identity if scoped else None, trace_sink=records.append)
    assert model.inputs == []
    assert records == []
    result = error.value.guardrail_result.output
    assert result.output_info == {"reason": "restricted_marker"}
    assert RESTRICTED_MARKER not in repr(result.output_info)
    assert not (resources.file("results.md") if scoped else tmp_path / "out").exists()


def test_tripwire_restores_session_and_allows_next_turn(tmp_path):
    async def check():
        resources = UserResources(Identity("demo", "alice"), tmp_path)
        model = ScriptedModel([[_answer("Before")], [_answer("After")]])
        agent = build_agent(model=model, resources=resources, provider="mock")
        with closing(resources.open_session("guardrails")) as session:
            await run_agent_async("Hello", identity=resources.identity,
                                  agent=agent, session=session)
            before = await resources.review(session)
            with pytest.raises(InputGuardrailTripwireTriggered):
                await run_agent_async(RESTRICTED_MARKER, identity=resources.identity,
                                      agent=agent, session=session)
            assert await resources.review(session) == before
            assert len(model.inputs) == 1
            turn = await run_agent_async("Continue", identity=resources.identity,
                                         agent=agent, session=session)
            assert turn.text == "After"
            assert RESTRICTED_MARKER not in repr(model.inputs)
        with closing(resources.open_session("guardrails")) as reopened:
            assert await resources.review(reopened) == turn.history

    asyncio.run(check())


def test_only_latest_user_message_is_checked():
    model = ScriptedModel([[_answer("Allowed")]])
    history = [
        {"role": "user", "content": RESTRICTED_MARKER},
        {"role": "assistant", "content": "Earlier synthetic history"},
    ]
    turn = run_agent("Continue", history,
                     agent=build_agent(model=model, provider="mock"))
    assert turn.text == "Allowed"
    assert RESTRICTED_MARKER in repr(model.inputs)
