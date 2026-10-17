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


@pytest.mark.parametrize("parallel", [False, True], ids=["blocking", "parallel"])
def test_input_guardrail_execution_order(tmp_path, parallel):
    from agents import (
        Agent,
        GuardrailFunctionOutput,
        RunConfig,
        Runner,
        function_tool,
        input_guardrail,
    )

    async def check():
        entered = asyncio.Event()
        release = asyncio.Event()
        written = asyncio.Event()
        events = []
        path = tmp_path / "simulated-action.txt"

        @input_guardrail(run_in_parallel=parallel)
        async def delayed_check(ctx, agent, input):
            events.append("check_started")
            entered.set()
            await release.wait()
            events.append("check_rejected")
            return GuardrailFunctionOutput(
                output_info={"reason": "demonstration"}, tripwire_triggered=True
            )

        @function_tool
        async def simulated_write() -> str:
            """Write one synthetic marker in this test's temporary directory."""
            path.write_text("SIMULATED-ACTION")
            events.append("tool_written")
            written.set()
            return "Written"

        class ObservedModel(ScriptedModel):
            async def get_response(self, *args, **kwargs):
                await entered.wait()
                events.append("model_started")
                return await super().get_response(*args, **kwargs)

        model = ObservedModel([
            [_call("simulated_write", {}, "write")], [_answer("Finished")],
        ])
        # Test-only agent. Production restricted_input stays blocking.
        agent = Agent(name="Guardrail ordering demo", model=model,
                      tools=[simulated_write], input_guardrails=[delayed_check])
        task = asyncio.create_task(Runner.run(
            agent, "Perform the simulated action",
            run_config=RunConfig(tracing_disabled=True),
        ))
        try:
            await entered.wait()
            if parallel:
                await written.wait()
                assert path.read_text() == "SIMULATED-ACTION"
                assert events == ["check_started", "model_started", "tool_written"]
            else:
                assert model.inputs == []
                assert not path.exists()
                assert events == ["check_started"]
        finally:
            release.set()
            with pytest.raises(InputGuardrailTripwireTriggered):
                await task

        assert events[-1] == "check_rejected"
        assert len(model.inputs) == int(parallel)
        assert path.exists() is parallel
        print(f"parallel={parallel}: {' -> '.join(events)}")

    # This is a deadlock limit, not a timing-based ordering assertion.
    asyncio.run(asyncio.wait_for(check(), timeout=5))


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("answer", ["[RESTRICTED-DEMO]", "Result [restricted-demo]"])
def test_output_tripwire_does_not_return_rejected_answer(tmp_path, scoped, answer):
    from agents.exceptions import OutputGuardrailTripwireTriggered

    resources = UserResources(Identity("demo", "alice"), tmp_path) if scoped else None
    model = ScriptedModel([[_answer(answer)]])
    with pytest.raises(OutputGuardrailTripwireTriggered) as error:
        run_agent("Return the result", identity=resources.identity if scoped else None,
                  agent=build_agent(model=model, resources=resources, provider="mock"))
    assert len(model.inputs) == 1
    assert error.value.guardrail_result.output.output_info == {
        "reason": "restricted_marker"
    }


def test_output_rejection_recovers_history_but_does_not_undo_save(tmp_path):
    from agents.exceptions import OutputGuardrailTripwireTriggered

    async def check():
        resources = UserResources(Identity("demo", "alice"), tmp_path)
        model = ScriptedModel([
            [_answer("Before")],
            [_call("save_results", {"content": "job-001: Synthetic fit"}, "save")],
            [_answer(RESTRICTED_MARKER)],
            [_answer("After")],
        ])
        agent = build_agent(model=model, resources=resources, provider="mock")
        records = []
        with closing(resources.open_session("output-check")) as session:
            await run_agent_async("Hello", identity=resources.identity,
                                  agent=agent, session=session)
            before = await resources.review(session)
            with pytest.raises(OutputGuardrailTripwireTriggered):
                await run_agent_async("Save job-001", identity=resources.identity,
                                      agent=agent, session=session,
                                      trace_sink=records.append)
            assert await resources.review(session) == before
            assert "job-001" in resources.file("results.md").read_text()
            trace, = records
            assert (trace.tool_name, trace.outcome) == ("save_results", "success")
            assert RESTRICTED_MARKER not in repr(records)
            turn = await run_agent_async("Continue", identity=resources.identity,
                                         agent=agent, session=session)
            assert turn.text == "After"
            assert model.inputs[-1] == [
                *before, {"role": "user", "content": "Continue"}
            ]
        with closing(resources.open_session("output-check")) as reopened:
            assert await resources.review(reopened) == turn.history

    asyncio.run(check())
