"""Exercise the real SDK loop with a scripted model and no API calls."""

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

    def fake_run(*args, **kwargs):
        configs.append(kwargs["run_config"])
        return SimpleNamespace(final_output="done", to_input_list=lambda: [])

    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", str(enabled).lower())
    monkeypatch.setattr("openai_agent_sdk.agent.Runner.run_sync", fake_run)
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
        [_call("search_jobs", {"query": "Python engineer"}, "search-1")],
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
