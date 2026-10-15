"""Offline checks of SDK history ownership before CLI integration."""

import asyncio
import sqlite3
from contextlib import closing

import pytest
from agents import Agent, RunConfig, Runner, SQLiteSession, function_tool
from openai_agent_sdk.agent import BudgetHooks, RunBudgetExceeded
from test_agent import ScriptedModel, _answer, _call


@function_tool
def echo(text: str) -> str:
    """Return synthetic evidence."""
    return text


async def run(session, model, message, **options):
    return await Runner.run(
        Agent(name="Session test", model=model, tools=[echo]), message,
        session=session, run_config=RunConfig(tracing_disabled=True), **options,
    )


def test_two_turns_and_reopen_preserve_exact_history(tmp_path):
    async def check():
        path = tmp_path / "history.db"
        call = _call("echo", {"text": "Python"}, "evidence")
        model = ScriptedModel([[call], [_answer("First")], [_answer("Second")]])
        with closing(SQLiteSession("A", path)) as session:
            first = await run(session, model, "Find jobs")
            second = await run(session, model, "Explain")
            history = second.to_input_list()
            assert await session.get_items() == history
            assert model.inputs[2] == [
                *first.to_input_list(), {"role": "user", "content": "Explain"}
            ]
            assert history[1] == call.model_dump(exclude_unset=True)
            assert history[2] == {
                "type": "function_call_output",
                "call_id": "evidence", "output": "Python",
            }
        with closing(SQLiteSession("A", path)) as session:
            assert await session.get_items() == history
            model = ScriptedModel([[_answer("Third")]])
            result = await run(session, model, "Continue")
            assert model.inputs == [[*history, {"role": "user", "content": "Continue"}]]
            assert await session.get_items() == result.to_input_list()

    asyncio.run(check())


def test_conversation_ids_separate_model_history(tmp_path):
    async def check():
        path = tmp_path / "history.db"
        for name in ("A", "B"):
            with closing(SQLiteSession(name, path)) as session:
                await run(session, ScriptedModel([[_answer(name)]]), name)
        for name in ("A", "B"):
            with closing(SQLiteSession(name, path)) as session:
                history = await session.get_items()
                assert history[0] == {"role": "user", "content": name}
                assert len(history) == 2
                model = ScriptedModel([[_answer("Continued")]])
                await run(session, model, "Continue")
                assert model.inputs == [[
                    *history, {"role": "user", "content": "Continue"}
                ]]

        with closing(SQLiteSession("A", path)) as a:
            await a.clear_session()
            assert await a.get_items() == []
        with closing(SQLiteSession("B", path)) as b:
            assert len(await b.get_items()) == 4

    asyncio.run(check())


@pytest.mark.parametrize("failure", ["model", "budget", "cancel", "storage"])
def test_failed_turn_retains_completed_tool_exchange(tmp_path, monkeypatch, failure):
    async def check():
        entered = asyncio.Event()

        class FailingModel(ScriptedModel):
            async def get_response(self, *args, **kwargs):
                if self.inputs:
                    if failure == "cancel":
                        entered.set()
                        await asyncio.Future()
                    raise RuntimeError("Synthetic model failure")
                return await super().get_response(*args, **kwargs)

        with closing(SQLiteSession("A", tmp_path / "history.db")) as session:
            await run(session, ScriptedModel([[_answer("Baseline")]]), "Before")
            before = await session.get_items()
            call = _call("echo", {"text": "Python"}, "partial")
            model = FailingModel([[call]])
            if failure == "storage":
                async def unavailable(items):
                    raise sqlite3.OperationalError("Synthetic write failure")
                monkeypatch.setattr(session, "add_items", unavailable)
            options = {"hooks": BudgetHooks(1, 1)} if failure == "budget" else {}
            if failure == "cancel":
                task = asyncio.create_task(run(session, model, "Fail"))
                try:
                    await asyncio.wait_for(entered.wait(), timeout=2)
                finally:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
            else:
                error = {"model": RuntimeError, "budget": RunBudgetExceeded,
                         "storage": sqlite3.OperationalError}[failure]
                with pytest.raises(error):
                    await run(session, model, "Fail", **options)
            expected = before if failure == "storage" else [
                *before, {"role": "user", "content": "Fail"},
                call.model_dump(exclude_unset=True),
                {"type": "function_call_output",
                 "call_id": "partial", "output": "Python"},
            ]
            assert await session.get_items() == expected

    asyncio.run(check())


def test_application_session_reopens_without_replaying_history(tmp_path, monkeypatch):
    from openai_agent_sdk.agent import build_agent, run_agent

    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    path = tmp_path / "history.db"
    results = tmp_path / "results.md"
    model = ScriptedModel([[_answer("Ready")]])
    with closing(SQLiteSession("A", path)) as session:
        first = run_agent("Save job-001", session=session,
                          agent=build_agent(model=model, provider="mock"),
                          max_model_calls=2)
    model = ScriptedModel([
        [_call("save_results", {"content": "job-001: Python"}, "denied")],
        [_answer("Not saved")],
    ])
    with closing(SQLiteSession("A", path)) as session:
        turn = run_agent("Find jobs. Do not save", session=session,
                         agent=build_agent(model=model, provider="mock",
                                           results_path=results), max_model_calls=2)
        assert model.inputs[0] == [
            *first.history, {"role": "user", "content": "Find jobs. Do not save"}
        ]
        assert asyncio.run(session.get_items()) == turn.history
        assert "Saving requires an explicit request" in repr(turn.history)
        assert not results.exists()
        with pytest.raises(ValueError, match="session or explicit history"):
            run_agent("Again", turn.history, session=session)


@pytest.mark.parametrize("failure", ["model", "budget", "timeout", "cancel", "storage"])
def test_application_restores_history_after_failure(tmp_path, monkeypatch, failure):
    from openai_agent_sdk.agent import build_agent, run_agent_async

    async def check():
        monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
        entered = asyncio.Event()

        class FailingModel(ScriptedModel):
            async def get_response(self, *args, **kwargs):
                if self.inputs:
                    entered.set()
                    if failure in {"timeout", "cancel"}:
                        await asyncio.Future()
                    raise RuntimeError("Synthetic failure")
                return await super().get_response(*args, **kwargs)

        with closing(SQLiteSession("A", tmp_path / "history.db")) as session:
            await session.add_items([{"role": "user", "content": "Before"}])
            before = await session.get_items()
            records = []
            model = FailingModel([[_call("read_resume", {}, "partial")]])
            agent = build_agent(model=model, provider="mock",
                                resume_path=tmp_path / "missing")
            options = {"session": session, "agent": agent, "trace_sink": records.append}
            if failure == "budget":
                options["max_model_calls"] = 1
            if failure == "timeout":
                options["run_timeout_seconds"] = 0.05
            if failure == "storage":
                async def unavailable(items):
                    raise sqlite3.OperationalError("Synthetic storage failure")
                monkeypatch.setattr(session, "add_items", unavailable)
            if failure == "cancel":
                task = asyncio.create_task(run_agent_async("Fail", **options))
                try:
                    await asyncio.wait_for(entered.wait(), timeout=2)
                finally:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
            else:
                error = {"model": RuntimeError, "budget": RunBudgetExceeded,
                         "timeout": TimeoutError, "storage": sqlite3.OperationalError}
                with pytest.raises(error[failure]):
                    await run_agent_async("Fail", **options)
            assert await session.get_items() == before
            if failure != "storage":
                assert [record.call_id for record in records] == ["partial"]
            monkeypatch.undo()
            monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
            model = ScriptedModel([[_answer("Recovered")]])
            await run_agent_async("Retry", session=session,
                                  agent=build_agent(model=model, provider="mock"))
            assert model.inputs == [[*before, {"role": "user", "content": "Retry"}]]

    asyncio.run(check())


def test_application_surfaces_recovery_failure(tmp_path, monkeypatch):
    from openai_agent_sdk.agent import build_agent, run_agent_async

    async def check():
        monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
        with closing(SQLiteSession("A", tmp_path / "history.db")) as session:
            async def broken_pop():
                raise sqlite3.OperationalError("Cannot restore history")
            monkeypatch.setattr(session, "pop_item", broken_pop)
            model = ScriptedModel([[_call("read_resume", {}, "partial")]])
            records = []
            with pytest.raises(RuntimeError, match="Conversation recovery failed"):
                await run_agent_async(
                    "Read resume", session=session, max_model_calls=1,
                    agent=build_agent(model=model, provider="mock",
                                      resume_path=tmp_path / "missing"),
                    trace_sink=records.append,
                )
            assert [record.call_id for record in records] == ["partial"]

    asyncio.run(check())


def test_session_does_not_restore_search_evidence_or_criteria(tmp_path, monkeypatch):
    from openai_agent_sdk.agent import build_agent, run_agent_async
    from openai_agent_sdk.jsearch import normalize_job

    async def check():
        monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
        requests = []
        stamp = "2026-10-14T00:00:00Z"
        job = normalize_job({
            "job_id": "one", "job_title": "Python Engineer",
            "employer_name": "Example", "job_description": "Required: Python.",
            "job_country": "US", "job_is_remote": True,
            "job_apply_link": "https://example.com/job",
        }, stamp)

        class Provider:
            async def search(self, query, **options):
                requests.append(options)
                return {"source": "jsearch", "retrieved_at": stamp, "jobs": [job]}

        model = ScriptedModel([
            [_call("search_jobs", {"query": "Python"}, "search-1")],
            [_answer("Found")],
            [_call("assess_fit", {"job_id": "jsearch:one"}, "old-job")],
            [_answer("Search again first")],
            [_call("search_jobs", {"query": "Python"}, "search-2")],
            [_answer("Found again")],
        ])
        agent = build_agent(model=model, provider="mcp", mcp_adapter=Provider())
        with closing(SQLiteSession("A", tmp_path / "history.db")) as session:
            first = await run_agent_async("Find remote Python jobs in the US",
                                          agent=agent, session=session)
            assert "jsearch:one" in repr(first.history)
            second = await run_agent_async("Assess the previous job",
                                           agent=agent, session=session)
            assert "Search for this live job in the current turn first" in repr(
                second.history
            )
            await run_agent_async("Find Python jobs", agent=agent, session=session)
            assert requests[0]["remote_only"] is True
            assert requests[1]["remote_only"] is False

    asyncio.run(check())
