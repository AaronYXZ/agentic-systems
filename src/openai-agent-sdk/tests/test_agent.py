"""Exercise the real SDK loop with a scripted model and no API calls."""

import asyncio
import json

import pytest
from agents import MaxTurnsExceeded, Model
from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from openai_agent_sdk.agent import (
    RunBudgetExceeded,
    build_agent,
    explicit_save_requested,
    run_agent,
)
from openai_agent_sdk.jsearch import normalize_job
from openai_agent_sdk.tools import search_jobs as search_jobs_file


def test_trace_ids_match_history_for_search_and_resume_failure(tmp_path):
    model = ScriptedModel([
        [_call("search_jobs", {"query": "ranking"}, "search-trace"),
         _call("read_resume", {}, "resume-trace")],
        [_answer("Resume missing.")],
    ])
    records = []
    turn = run_agent(
        "Find jobs", agent=build_agent(model=model, resume_path=tmp_path / "missing"),
        trace_sink=records.append,
    )
    outputs = {item["call_id"] for item in turn.history
               if item.get("type") == "function_call_output"}
    assert {record.call_id for record in records} == outputs
    assert tuple(records) == turn.tool_traces
    assert {(r.tool_name, r.outcome, r.error_class) for r in records} == {
        ("search_jobs", "success", None),
        ("read_resume", "error", "ToolReportedError"),
    }
    for tool in build_agent(model=model).tools:
        assert "ctx" not in tool.params_json_schema.get("properties", {})


def test_failed_run_delivers_collected_traces(tmp_path):
    model = ScriptedModel([[_call("read_resume", {}, "failed-turn")]])
    records = []
    with pytest.raises(MaxTurnsExceeded):
        run_agent("Find jobs", max_turns=1, trace_sink=records.append,
                  agent=build_agent(model=model, resume_path=tmp_path / "missing"))
    assert records[0].call_id == "failed-turn"
    assert records[0].outcome == "error"


@pytest.mark.parametrize("enabled", [False, True])
def test_sdk_tracing_config_excludes_sensitive_data(monkeypatch, enabled):
    from types import SimpleNamespace

    configs = []

    async def fake_run(*args, **kwargs):
        configs.append(kwargs["run_config"])
        return SimpleNamespace(final_output="done", to_input_list=lambda: [])

    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", str(enabled).lower())
    monkeypatch.setattr("openai_agent_sdk.agent.Runner.run", fake_run)
    run_agent("Hello", agent=build_agent(model="fake"))
    assert configs[0].tracing_disabled is not enabled
    assert configs[0].trace_include_sensitive_data is False


def test_trace_sink_failure_does_not_replace_answer(tmp_path):
    def broken_sink(record):
        raise OSError("unavailable")

    model = ScriptedModel([
        [_call("read_resume", {}, "sink-failure")], [_answer("Resume missing.")]
    ])
    turn = run_agent("Find jobs", trace_sink=broken_sink,
                     agent=build_agent(model=model, resume_path=tmp_path / "missing"))
    assert turn.text == "Resume missing."
    assert turn.tool_traces[0].call_id == "sink-failure"


@pytest.fixture(autouse=True)
def default_to_mock_provider(monkeypatch):
    """Keep scripted-model tests independent of a developer's local .env."""
    monkeypatch.setenv("JOB_SEARCH_PROVIDER", "mock")
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")


def _call(name: str, arguments: dict[str, str], call_id: str):
    return ResponseFunctionToolCall(
        arguments=json.dumps(arguments),
        call_id=call_id,
        name=name,
        type="function_call",
    )


def _answer(text: str):
    return ResponseOutputMessage(
        id="message-1",
        content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
        role="assistant",
        status="completed",
        type="message",
    )


class ScriptedModel(Model):
    def __init__(self, steps):
        self.steps = iter(steps)
        self.inputs = []
        self.instructions = []

    async def get_response(
        self,
        system_instructions,
        input,
        model_settings,
        tools,
        output_schema,
        handoffs,
        tracing,
        *,
        previous_response_id,
        conversation_id,
        prompt,
    ):
        self.inputs.append(input)
        self.instructions.append(system_instructions)
        step = next(self.steps)
        output = step(input) if callable(step) else step
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError
        yield  # pragma: no cover


def test_model_call_budget_stops_before_next_model_request():
    model = ScriptedModel([
        [_call("read_resume", {}, "resume")], [_answer("Done")]
    ])
    with pytest.raises(RunBudgetExceeded, match="Model call budget exhausted"):
        run_agent("Read my resume", agent=build_agent(model=model), max_model_calls=1)
    assert len(model.inputs) == 1


def test_tool_call_budget_stops_before_next_tool(tmp_path, monkeypatch):
    model = ScriptedModel([[
        _call("read_resume", {}, "first"),
        _call("save_results", {"content": "job-002"}, "second"),
    ]])
    results = tmp_path / "results.md"
    def unexpected_save(*args, **kwargs):
        pytest.fail("A tool ran after the call budget was exhausted")

    monkeypatch.setattr("openai_agent_sdk.agent.save_results_file", unexpected_save)
    with pytest.raises(RunBudgetExceeded, match="Tool call budget exhausted"):
        run_agent("Save these jobs", agent=build_agent(
            model=model, results_path=results
        ), max_tool_calls=1)
    assert not results.exists()


def test_run_deadline_cancels_slow_model():
    cancelled = False
    records = []

    class SlowModel(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            nonlocal cancelled
            if not self.inputs:
                return await super().get_response(*args, **kwargs)
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                cancelled = True
                raise
            return await super().get_response(*args, **kwargs)

    model = SlowModel([
        [_call("read_resume", {}, "before-timeout")], [_answer("Too late")]
    ])
    with pytest.raises(TimeoutError):
        run_agent("Hello", agent=build_agent(model=model),
                  run_timeout_seconds=0.01, trace_sink=records.append)
    assert cancelled
    assert len(model.inputs) == 1
    assert [record.call_id for record in records] == ["before-timeout"]


def test_agent_registers_only_three_tools_and_loads_versioned_prompt():
    agent = build_agent(model="scripted-model")

    assert agent.name == "Job Search Agent"
    assert [tool.name for tool in agent.tools] == [
        "search_jobs", "read_resume", "save_results"
    ]
    assert agent.tools[2].params_json_schema["required"] == ["content"]
    assert "fictional mock data" in agent.instructions


def test_jsearch_mode_adds_fit_tool_and_blocks_live_saving(tmp_path):
    results = tmp_path / "results.md"

    def report_denial(items):
        assert "Saving live jobs is not supported yet" in repr(items)
        return [_answer("I could not save that live job.")]

    model = ScriptedModel(
        [
            [_call("save_results", {"content": "jsearch:abc: Good fit."}, "save-1")],
            report_denial,
        ]
    )
    agent = build_agent(model=model, provider="jsearch", results_path=results)

    assert [tool.name for tool in agent.tools] == [
        "search_jobs", "read_resume", "save_results", "assess_fit"
    ]
    assert "JSearch mode is active" in agent.instructions
    turn = run_agent("Save this job", agent=agent)
    assert turn.text == "I could not save that live job."
    assert not results.exists()
    record, = turn.tool_traces
    assert record.tool_name == "save_results"
    assert record.source == "local_results"
    assert record.outcome == "error"


def test_live_agent_search_filters_then_checks_fit_with_local_evidence(
    tmp_path, monkeypatch
):
    resume = tmp_path / "resume.md"
    resume.write_text(
        "Built Python services.\nUsed SQL for analysis.", encoding="utf-8"
    )
    stamp = "2026-10-03T00:00:00+00:00"

    def job(job_id, remote):
        return normalize_job({
            "job_id": job_id,
            "job_title": "Python Engineer",
            "employer_name": "Example Co",
            "job_location": "Chicago, IL",
            "job_country": "US",
            "job_is_remote": remote,
            "job_description": "Requirements: Python and SQL experience.",
            "job_apply_link": f"https://example.com/{job_id}",
        }, stamp)

    def fake_provider(_query, *, api_key, country, remote_only):
        assert api_key == "test-key"
        assert country == "us"
        assert remote_only is True
        return {"source": "jsearch", "retrieved_at": stamp,
                "jobs": [job("keep", True), job("keep", True),
                         job("exclude", False)]}

    def search_with_fake(query, **kwargs):
        return search_jobs_file(query, live_search=fake_provider, **kwargs)

    monkeypatch.setattr("openai_agent_sdk.agent.search_job_records", search_with_fake)

    def after_search(items):
        observed = repr(items)
        assert "jsearch:keep" in observed
        assert "remote status is not confirmed true" in observed
        assert "provider_id" in observed
        return [_call("assess_fit", {"job_id": "jsearch:keep"}, "fit-1")]

    def after_fit(items):
        observed = repr(items)
        assert "Built Python services." in observed
        assert "Requirements: Python and SQL experience." in observed
        return [_answer(
            'jsearch:keep. Job: "Requirements: Python and SQL experience." '
            'Resume: "Built Python services." and "Used SQL for analysis."'
        )]

    model = ScriptedModel([
        [_call("search_jobs", {"query": "Python"}, "search-1")],
        after_search,
        after_fit,
    ])
    agent = build_agent(
        model=model, provider="jsearch", jsearch_api_key="test-key",
        resume_path=resume,
    )

    turn = run_agent("Find remote Python jobs in the US", agent=agent)

    assert "Requirements: Python and SQL experience." in turn.text
    assert "Built Python services." in turn.text
    assert [(trace.call_id, trace.source) for trace in turn.tool_traces] == [
        ("search-1", "jsearch"), ("fit-1", "local_matching")
    ]


def test_live_fit_requires_a_job_searched_this_turn(tmp_path):
    def after_error(items):
        assert "Search for this live job in the current turn first" in repr(items)
        return [_answer("Search first.")]

    model = ScriptedModel([
        [_call("assess_fit", {"job_id": "jsearch:unknown"}, "fit-1")],
        after_error,
    ])
    agent = build_agent(model=model, provider="jsearch",
                        resume_path=tmp_path / "missing.md")

    assert run_agent("Does this job fit?", agent=agent).text == "Search first."


def test_model_name_comes_from_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    agent = build_agent()

    assert agent.model == "test-model"


def test_search_turn_observes_resume_and_mock_jobs_without_saving(tmp_path):
    resume = tmp_path / "resume.md"
    resume.write_text("Python and ranking experience.\n", encoding="utf-8")
    results = tmp_path / "results.md"

    def grounded_reply(items):
        observed = repr(items)
        assert "Python and ranking experience." in observed
        assert "job-001" in observed
        return [_answer("job-001 fits your Python ranking experience.")]

    model = ScriptedModel(
        [
            [
                _call("read_resume", {}, "resume-1"),
                _call("search_jobs", {"query": "ranking"}, "search-1"),
            ],
            grounded_reply,
        ]
    )
    agent = build_agent(model=model, resume_path=resume, results_path=results)

    turn = run_agent("Find ranking roles", agent=agent)

    assert turn.text == "job-001 fits your Python ranking experience."
    assert len(model.inputs) == 2
    assert "fictional mock data" in model.instructions[0]
    assert turn.history
    assert not results.exists()


def test_greeting_needs_no_tools_or_local_files(tmp_path):
    model = ScriptedModel([[_answer("Hello. Tell me what roles interest you.")]])
    agent = build_agent(
        model=model,
        resume_path=tmp_path / "missing.md",
        results_path=tmp_path / "results.md",
    )

    turn = run_agent("Hello", agent=agent)

    assert turn.text.startswith("Hello.")
    assert len(model.inputs) == 1
    assert not (tmp_path / "results.md").exists()


@pytest.mark.parametrize(
    "message",
    ["Find ranking jobs", "Find ranking jobs and do not save", "Save nothing"],
)
def test_save_call_is_denied_without_affirmative_current_turn_request(
    tmp_path, message
):
    results = tmp_path / "results.md"

    def report_denial(items):
        assert "Saving requires an explicit request" in repr(items)
        return [_answer("I did not save anything.")]

    model = ScriptedModel(
        [
            [_call("save_results", {"content": "job-001: Ranking fit."}, "save-1")],
            report_denial,
        ]
    )
    agent = build_agent(model=model, results_path=results)

    turn = run_agent(message, agent=agent)

    assert turn.text == "I did not save anything."
    assert not results.exists()


def test_explicit_save_request_runs_tool_and_records_result(tmp_path):
    results = tmp_path / "results.md"

    def report_save(items):
        assert "Saved: job-001." in repr(items)
        return [_answer("Saved job-001.")]

    model = ScriptedModel(
        [
            [_call("save_results", {"content": "job-001: Ranking fit."}, "save-1")],
            report_save,
        ]
    )
    agent = build_agent(model=model, results_path=results)

    turn = run_agent("Save job-001", agent=agent)

    assert turn.text == "Saved job-001."
    assert "## job-001 |" in results.read_text(encoding="utf-8")


def test_missing_resume_tool_error_can_be_explained(tmp_path):
    def explain_error(items):
        assert "Resume is missing or empty" in repr(items)
        return [_answer("Add data/resume.md before I compare jobs with your profile.")]

    model = ScriptedModel([[_call("read_resume", {}, "resume-1")], explain_error])
    agent = build_agent(model=model, resume_path=tmp_path / "missing.md")

    turn = run_agent("Find a role that fits me", agent=agent)

    assert "Add data/resume.md" in turn.text


def test_replay_history_supports_follow_up_turn(tmp_path):
    model = ScriptedModel([[_answer("First answer.")], [_answer("Follow-up answer.")]])
    agent = build_agent(model=model, results_path=tmp_path / "results.md")

    first = run_agent("First request", agent=agent)
    second = run_agent("Follow-up request", history=first.history, agent=agent)

    assert second.text == "Follow-up answer."
    assert "First request" in repr(model.inputs[1])
    assert "First answer." in repr(model.inputs[1])


def test_max_turns_stops_repeated_tool_calls(tmp_path):
    model = ScriptedModel(
        [
            [_call("search_jobs", {"query": "ranking"}, "search-1")],
            [_call("search_jobs", {"query": "ranking"}, "search-2")],
            [_answer("Too late.")],
        ]
    )
    agent = build_agent(model=model, results_path=tmp_path / "results.md")

    with pytest.raises(MaxTurnsExceeded):
        run_agent("Find ranking jobs", agent=agent, max_turns=2)

    assert len(model.inputs) == 2


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Save the first result", True),
        ("Find roles and save the top two", True),
        ("Can you save job-001?", True),
        ("Find roles without saving", False),
        ("Do not save anything", False),
        ("Tell me how to save results", False),
    ],
)
def test_explicit_save_intent_is_conservative(message, expected):
    assert explicit_save_requested(message) is expected


def test_async_turn_preserves_history_and_turn_limit():
    import asyncio

    from openai_agent_sdk.agent import run_agent_async

    async def run():
        model = ScriptedModel([[_answer("First")], [_answer("Second")]])
        agent = build_agent(model=model)
        first = await run_agent_async("Hello", agent=agent)
        second = await run_agent_async("Again", first.history, agent=agent)
        assert second.text == "Second"
        assert second.history[:len(first.history)] == first.history
        with pytest.raises(MaxTurnsExceeded):
            await run_agent_async("Find jobs", max_turns=1, agent=build_agent(
                model=ScriptedModel([
                    [_call("search_jobs", {"query": "Python"}, "one")]
                ])
            ))

    asyncio.run(run())


def test_mcp_search_preserves_filters_fit_and_save_boundary(tmp_path, monkeypatch):
    from openai_agent_sdk.jsearch import JSearchError

    resume = tmp_path / "resume.md"
    resume.write_text("Built Python services. Used SQL for analysis.")
    stamp = "2026-10-10T00:00:00Z"

    def job(job_id, remote):
        return normalize_job({
            "job_id": job_id, "job_title": "Python Engineer",
            "employer_name": "Example", "job_country": "US",
            "job_is_remote": remote,
            "job_description": "Requirements: Python and SQL experience.",
            "job_apply_link": f"https://example.com/{job_id}",
        }, stamp)

    class Adapter:
        async def search(self, query, **options):
            assert options == {"country": "us", "remote_only": True}
            return {"source": "jsearch", "retrieved_at": stamp,
                    "jobs": [job("keep", True), job("keep", True),
                             job("exclude", False)]}

    def forbidden(*args, **kwargs):
        pytest.fail("MCP mode must not call mock or direct search")

    monkeypatch.setattr("openai_agent_sdk.agent.search_job_records", forbidden)

    def after_search(items):
        output = json.loads(items[-1]["output"])
        assert [job["id"] for job in output["jobs"]] == ["jsearch:keep"]
        assert output["duplicates"][0]["reason"] == "provider_id"
        assert output["excluded"][0]["id"] == "jsearch:exclude"
        return [_call("assess_fit", {"job_id": "jsearch:keep"}, "fit")]

    def after_fit(items):
        assert "Built Python services." in repr(items)
        return [_call("save_results", {"content": "jsearch:keep"}, "save")]

    def after_save(items):
        assert "Saving live jobs is not supported yet" in repr(items)
        return [_answer("Checked without saving.")]

    def after_old_fit(items):
        assert "Search for this live job in the current turn first" in repr(items)
        return [_answer("Search again.")]

    model = ScriptedModel([
        [_call("search_jobs", {"query": "Python"}, "search")],
        after_search, after_fit, after_save,
        [_call("assess_fit", {"job_id": "jsearch:keep"}, "old-fit")],
        after_old_fit,
    ])
    agent = build_agent(model=model, provider="mcp", mcp_adapter=Adapter(),
                        resume_path=resume, results_path=tmp_path / "results.md")
    assert {tool.name for tool in agent.tools} == {
        "search_jobs", "read_resume", "save_results", "assess_fit"
    }
    first = run_agent("Find remote Python jobs in the US and save results", agent=agent)
    search_trace = next(t for t in first.tool_traces if t.call_id == "search")
    assert search_trace.source == "mcp"
    assert search_trace.outcome == "success"
    second = run_agent("Assess the previous job", first.history, agent=agent)
    assert second.text == "Search again."
    assert not (tmp_path / "results.md").exists()

    class FailedAdapter:
        async def search(self, *args, **kwargs):
            raise JSearchError("Provider unavailable.")

    def after_failure(items):
        assert "Error: Provider unavailable." in repr(items)
        return [_answer("Search failed.")]

    failed_agent = build_agent(
        provider="mcp", mcp_adapter=FailedAdapter(),
        model=ScriptedModel([
            [_call("search_jobs", {"query": "Python"}, "failed")], after_failure,
        ]),
    )
    failed_turn = run_agent("Find Python jobs", agent=failed_agent)
    assert failed_turn.text == "Search failed."
    trace, = failed_turn.tool_traces
    assert (trace.call_id, trace.source, trace.error_class) == (
        "failed", "mcp", "JSearchError"
    )
    assert "Provider unavailable" not in repr(trace)


def test_mcp_requires_owned_session():
    with pytest.raises(ValueError, match="open search session"):
        build_agent(model="fake", provider="mcp")
