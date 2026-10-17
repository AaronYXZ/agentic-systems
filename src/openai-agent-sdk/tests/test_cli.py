"""Test terminal behavior without an API key or live model calls."""

import pytest
from openai_agent_sdk.agent import AgentTurn
from openai_agent_sdk.cli import ConfigurationError, _run_live_turn, run_cli
from openai_agent_sdk.filtering import CriteriaError


@pytest.mark.parametrize("enabled", [False, True])
def test_local_trace_switch_selects_stderr_sink(monkeypatch, enabled):
    from openai_agent_sdk.cli import _print_trace

    captured = []

    def fake_run(message, history, *, trace_sink):
        captured.append(trace_sink)
        return AgentTurn("Done.", [])

    monkeypatch.setattr("openai_agent_sdk.cli.load_dotenv", lambda path: None)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("JOB_AGENT_LOCAL_TRACING", str(enabled).lower())
    monkeypatch.setattr("openai_agent_sdk.cli.run_agent", fake_run)
    _run_live_turn("Find jobs", None)
    assert captured == [_print_trace if enabled else None]


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


def test_main_selects_mcp_from_configuration(monkeypatch):
    from openai_agent_sdk.cli import main

    calls = []

    async def cli(*, api_key, agent_fn):
        calls.append(api_key)

    monkeypatch.setattr("openai_agent_sdk.cli.load_dotenv", lambda path: None)
    monkeypatch.setenv("JOB_SEARCH_PROVIDER", "mcp")
    monkeypatch.setenv("OPENAI_API_KEY", "test-model-key")
    monkeypatch.setenv("OPENWEBNINJA_API_KEY", "test-provider-key")
    monkeypatch.setattr("openai_agent_sdk.cli.run_mcp_cli", cli)
    monkeypatch.setattr(
        "openai_agent_sdk.cli.run_cli", lambda: pytest.fail("No fallback")
    )
    main([])
    assert calls == ["test-provider-key"]


@pytest.mark.parametrize("arguments", [["--session", " "], ["--session-db", "x.db"]])
def test_session_arguments_reject_invalid_selection(arguments):
    from openai_agent_sdk.cli import main

    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_persistent_cli_continues_after_process_restart(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from textwrap import dedent

    # Exercise real SQLite and SDK execution in separate CLI processes, without APIs.
    script = dedent('''
        import os
        import sys
        from pathlib import Path
        from openai_agent_sdk import cli
        from openai_agent_sdk.agent import build_agent, run_agent
        from test_agent import ScriptedModel, _answer, _call

        phase, database, results, session_id = sys.argv[1:]
        os.environ["JOB_AGENT_SDK_TRACING"] = "false"
        os.environ["JOB_SEARCH_PROVIDER"] = "mock"
        cli.load_dotenv = lambda path: None
        if phase == "resume":
            steps = [[_call("save_results", {"content": "job-001: Example"}, "denied")],
                     [_answer("Not saved")]]
            message = "Find jobs. Do not save"
        else:
            steps, message = [[_answer("Ready")]], "Save job-001"
        model = ScriptedModel(steps)
        agent = build_agent(model=model, provider="mock",
                            results_path=Path(results))
        def turn(message, history, *, session):
            assert history is None
            result = run_agent(message, session=session, agent=agent)
            assert len(model.inputs[0]) == (3 if phase == "resume" else 1)
            if phase == "resume":
                assert "Saving requires an explicit request" in repr(result.history)
            return result
        cli._run_live_turn = turn
        original = cli.run_cli
        inputs = iter([message, "quit"])
        cli.run_cli = lambda **options: original(
            input_fn=lambda prompt: next(inputs), **options)
        cli.main(["--session", session_id, "--session-db", database])
    ''')
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parent)}
    for phase, session_id in [("first", "A"), ("resume", "A"), ("first", "B")]:
        result = subprocess.run(
            [sys.executable, "-c", script, phase, str(tmp_path / "history.db"),
             str(tmp_path / "results.md"), session_id],
            env=env, capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stderr
        assert f"Conversation: {session_id}" in result.stdout
        assert "Goodbye." in result.stdout
    assert not (tmp_path / "results.md").exists()


def test_database_open_failure_does_not_start_ephemeral_cli(
    tmp_path, monkeypatch, capsys
):
    from openai_agent_sdk.cli import main

    monkeypatch.setattr("openai_agent_sdk.cli.run_cli",
                        lambda **options: pytest.fail("No ephemeral fallback"))
    main(["--session", "A", "--session-db", str(tmp_path / "missing" / "db")])
    assert "database could not be opened" in capsys.readouterr().err


def test_recovery_error_stops_cli_before_next_request():
    from openai_agent_sdk.agent import SessionRecoveryError

    calls, outputs = [], []
    def broken(message, history):
        calls.append(message)
        raise SessionRecoveryError("Synthetic failure")
    run_cli(agent_fn=broken, persistent=True,
            input_fn=_input_from("Fail", "Must not execute", "quit"),
            output_fn=outputs.append)
    assert calls == ["Fail"]
    assert "Stop and check the database" in outputs[0]


@pytest.mark.parametrize("provider", ["mock", "mcp"])
@pytest.mark.parametrize("ending", ["quit", EOFError, KeyboardInterrupt])
def test_main_closes_persistent_session_and_mcp_connection(
    tmp_path, monkeypatch, provider, ending
):
    from contextlib import asynccontextmanager

    from agents import SQLiteSession
    from openai_agent_sdk import cli

    sessions, events = [], []
    def create_session(*args):
        session = SQLiteSession(*args)
        sessions.append(session)
        return session
    @asynccontextmanager
    async def connection(key):
        events.append("open")
        try:
            yield object()
        finally:
            events.append("close")
    def read_input(prompt):
        if isinstance(ending, type):
            raise ending()
        return ending
    monkeypatch.setattr(cli, "SQLiteSession", create_session)
    monkeypatch.setattr(cli, "load_dotenv", lambda path: None)
    monkeypatch.setattr(cli, "open_search_session", connection)
    # Input callbacks are bound at definition time, so supply them explicitly.
    original_cli, original_mcp = cli.run_cli, cli.run_mcp_cli
    monkeypatch.setattr(
        cli, "run_cli", lambda **kw: original_cli(input_fn=read_input, **kw)
    )
    async def mcp_cli(**kw):
        await original_mcp(input_fn=read_input, **kw)
    monkeypatch.setattr(cli, "run_mcp_cli", mcp_cli)
    monkeypatch.setenv("JOB_SEARCH_PROVIDER", provider)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENWEBNINJA_API_KEY", "test")
    cli.main(["--session", "A", "--session-db", str(tmp_path / "history.db")])
    assert sessions[0]._closed
    assert events == (["open", "close"] if provider == "mcp" else [])


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("tracing", [False, True])
def test_guardrail_rejections_are_safe_and_cli_continues(
    stage, tracing, monkeypatch, capsys
):
    import json

    from openai_agent_sdk.agent import build_agent, run_agent
    from openai_agent_sdk.guardrails import RESTRICTED_MARKER
    from test_agent import ScriptedModel, _answer

    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    monkeypatch.setenv("JOB_AGENT_LOCAL_TRACING", str(tracing).lower())
    steps = [[_answer("Before")]]
    if stage == "output":
        steps.append([_answer("PRIVATE-ANSWER " + RESTRICTED_MARKER)])
    steps.append([_answer("After")])
    model = ScriptedModel(steps)
    agent = build_agent(model=model, provider="mock")
    outputs = []
    bad_message = RESTRICTED_MARKER if stage == "input" else "Generate a result"
    run_cli(input_fn=_input_from("Hello", bad_message, "Continue", "quit"),
            output_fn=outputs.append,
            agent_fn=lambda message, history: run_agent(message, history, agent=agent))
    rejection = (
        "Agent > Input rejected by policy. Revise your request." if stage == "input"
        else "Agent > Response withheld by policy. "
        "Earlier tool actions may already have completed."
    )
    assert outputs == ["Agent > Before", rejection, "Agent > After", "Goodbye."]
    captured = capsys.readouterr()
    assert RESTRICTED_MARKER not in repr(outputs) + captured.err
    assert "PRIVATE-ANSWER" not in repr(outputs) + captured.err
    if tracing:
        assert json.loads(captured.err) == {
            "event": "guardrail_rejected", "stage": stage, "reason": "policy_tripwire"
        }
    else:
        assert captured.err == ""
    assert "Before" in repr(model.inputs[-1])
    assert "PRIVATE-ANSWER" not in repr(model.inputs[-1])
    assert bad_message not in repr(model.inputs[-1])
