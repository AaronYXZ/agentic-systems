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


@pytest.fixture(autouse=True)
def default_to_mock_provider(monkeypatch):
    """Keep scripted-model tests independent of a developer's local .env."""
    monkeypatch.setenv("JOB_SEARCH_PROVIDER", "mock")


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


def test_jsearch_mode_keeps_three_tools_and_blocks_live_saving(tmp_path):
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

    assert len(agent.tools) == 3
    assert "JSearch mode is active" in agent.instructions
    turn = run_agent("Save this job", agent=agent)
    assert turn.text == "I could not save that live job."
    assert not results.exists()


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
