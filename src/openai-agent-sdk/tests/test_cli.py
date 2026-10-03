"""Test terminal behavior without an API key or live model calls."""

import pytest
from openai_agent_sdk.agent import AgentTurn
from openai_agent_sdk.cli import ConfigurationError, _run_live_turn, run_cli
from openai_agent_sdk.filtering import CriteriaError


def _input_from(*items):
    responses = iter(items)
    return lambda prompt: next(responses)


def test_request_response_and_follow_up_history():
    outputs = []
    calls = []
    first_history = [{"role": "user", "content": "Find ranking jobs"}]

    def agent(message, history):
        calls.append((message, history))
        if len(calls) == 1:
            return AgentTurn("job-001 is a match.", first_history)
        return AgentTurn(
            "Saved job-001.", [*first_history, {"role": "user", "content": message}]
        )

    run_cli(
        input_fn=_input_from("Find ranking jobs", "Save the first result", "quit"),
        output_fn=outputs.append,
        agent_fn=agent,
    )

    assert calls == [
        ("Find ranking jobs", None),
        ("Save the first result", first_history),
    ]
    assert outputs == [
        "Agent > job-001 is a match.",
        "Agent > Saved job-001.",
        "Goodbye.",
    ]


def test_blank_input_does_not_call_agent():
    outputs = []
    calls = []

    def agent(message, history):
        calls.append(message)
        return AgentTurn("Hello.", [])

    run_cli(
        input_fn=_input_from("", "   ", "Hello", "exit"),
        output_fn=outputs.append,
        agent_fn=agent,
    )

    assert calls == ["Hello"]
    assert outputs == ["Agent > Hello.", "Goodbye."]


@pytest.mark.parametrize("command", ["exit", "quit", " EXIT ", "Quit"])
def test_exit_commands(command):
    outputs = []

    def unexpected_agent(message, history):
        pytest.fail("Exit must not call the agent")

    run_cli(
        input_fn=_input_from(command),
        output_fn=outputs.append,
        agent_fn=unexpected_agent,
    )

    assert outputs == ["Goodbye."]


@pytest.mark.parametrize("interruption", [EOFError, KeyboardInterrupt])
def test_input_interruption_exits_cleanly(interruption):
    outputs = []

    def interrupted_input(prompt):
        raise interruption

    run_cli(input_fn=interrupted_input, output_fn=outputs.append)

    assert outputs == ["Goodbye."]


@pytest.mark.parametrize(
    "error", [ConfigurationError("Bad configuration"), RuntimeError("secret")]
)
def test_failed_turn_recovers_and_does_not_advance_history(error):
    outputs = []
    calls = []
    first_history = [{"role": "user", "content": "First"}]

    def agent(message, history):
        calls.append((message, history))
        if len(calls) == 1:
            return AgentTurn("First answer.", first_history)
        if len(calls) == 2:
            raise error
        return AgentTurn("Recovered.", first_history)

    run_cli(
        input_fn=_input_from("First", "Fail", "Retry", "quit"),
        output_fn=outputs.append,
        agent_fn=agent,
    )

    assert calls == [
        ("First", None),
        ("Fail", first_history),
        ("Retry", first_history),
    ]
    assert outputs[-2:] == ["Agent > Recovered.", "Goodbye."]
    if isinstance(error, ConfigurationError):
        assert outputs[1] == "Agent > Bad configuration"
    else:
        assert "secret" not in outputs[1]
        assert "Could not complete that turn" in outputs[1]


def test_missing_api_key_has_actionable_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("openai_agent_sdk.cli.load_dotenv", lambda path: None)

    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY is missing"):
        _run_live_turn("Find jobs", None)


def test_invalid_explicit_filter_is_shown_and_cli_recovers():
    outputs = []
    calls = []

    def agent(message, history):
        calls.append((message, history))
        if len(calls) == 1:
            raise CriteriaError("Filter remote must be true or false.")
        return AgentTurn("Recovered.", [])

    run_cli(
        input_fn=_input_from("Find jobs filters: remote=maybe", "Find jobs", "quit"),
        output_fn=outputs.append,
        agent_fn=agent,
    )

    assert outputs == [
        "Agent > Filter remote must be true or false.",
        "Agent > Recovered.",
        "Goodbye.",
    ]
    assert calls[1][1] is None
