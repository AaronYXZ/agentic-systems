"""Exercise synthetic CLI identity across turns, failures, and reopening."""

import asyncio
from contextlib import asynccontextmanager, closing

import pytest
from openai_agent_sdk import cli
from openai_agent_sdk.agent import build_agent
from openai_agent_sdk.identity import Identity, UserResources
from test_agent import ScriptedModel, _answer, _call


# Reject incomplete or unsafe scoped-mode selection before opening resources.
@pytest.mark.parametrize(
    "arguments",
    [
        ["--tenant", "demo"],
        ["--user", "alice"],
        ["--tenant", "demo", "--user", "alice"],
        ["--tenant", "../demo", "--user", "alice", "--session", "chat"],
        [
            "--tenant",
            "demo",
            "--user",
            "alice",
            "--session",
            "chat",
            "--session-db",
            "someone-else.sqlite3",
        ],
    ],
)
def test_invalid_identity_selection(arguments):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2


# Both CLI modes preserve owner boundaries during failures and later CLI invocations.
@pytest.mark.parametrize("provider", ["mock", "mcp"])
def test_cli_owner_scoping_recovery_restart_and_cleanup(
    tmp_path, monkeypatch, provider
):
    monkeypatch.setenv("JOB_SEARCH_PROVIDER", provider)
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    monkeypatch.setenv("OPENWEBNINJA_API_KEY", "synthetic")
    monkeypatch.setattr(cli, "load_dotenv", lambda path: None)
    monkeypatch.setattr(
        cli, "UserResources", lambda identity: UserResources(identity, tmp_path)
    )
    events, sessions, pending = [], [], []
    original_open = UserResources.open_session

    def tracked_open(self, conversation):
        session = original_open(self, conversation)
        sessions.append(session)
        return session

    monkeypatch.setattr(UserResources, "open_session", tracked_open)

    @asynccontextmanager
    async def connection(key):
        events.append("open")
        try:
            yield object()
        finally:
            events.append("close")

    def scripted_agent(**options):
        return build_agent(model=pending.pop(0), **options)

    original_cli, original_mcp = cli.run_cli, cli.run_mcp_cli
    messages = iter(())
    monkeypatch.setattr(cli, "build_agent", scripted_agent)
    monkeypatch.setattr(cli, "open_search_session", connection)
    monkeypatch.setattr(
        cli,
        "run_cli",
        lambda **kw: original_cli(input_fn=lambda prompt: next(messages), **kw),
    )

    async def mcp_cli(**kw):
        await original_mcp(input_fn=lambda prompt: next(messages), **kw)

    monkeypatch.setattr(cli, "run_mcp_cli", mcp_cli)
    snapshots = {}
    for user in ("bob", "alice"):
        resources = UserResources(Identity("demo", user), tmp_path)
        with closing(resources.open_session("same-name")):
            resources.file("resume.md").write_text(f"CANARY-{user}")
        initial = [[_call("read_resume", {}, "read")]]
        if provider == "mock":
            initial.append([_call("save_results", {"content": "job-001: Fit"}, "save")])
        initial.append([_answer("Done")])
        models = [
            ScriptedModel(initial),
            ScriptedModel([[_call("read_resume", {}, "before-failure")]]),
            ScriptedModel([[_answer("Recovered")]]),
        ]
        pending.extend(models)
        messages = iter(["Read resume and save job-001", "Fail", "Retry", "quit"])
        args = ["--tenant", "demo", "--user", user, "--session", "same-name"]
        cli.main(args)
        other = "alice" if user == "bob" else "bob"
        assert f"CANARY-{user}" in repr(models[0].inputs)
        assert f"CANARY-{other}" not in repr([model.inputs for model in models])
        assert resources.file("results.md").exists() is (provider == "mock")
        with closing(resources.open_session("same-name")) as session:
            history = asyncio.run(resources.review(session))
        assert [item["content"] for item in history if item.get("role") == "user"] == [
            "Read resume and save job-001",
            "Retry",
        ]
        resumed = ScriptedModel([[_answer("Continued")]])
        pending.append(resumed)
        messages = iter(["Continue", "quit"])
        cli.main(args)
        assert resumed.inputs == [[*history, {"role": "user", "content": "Continue"}]]
        with closing(resources.open_session("same-name")) as session:
            snapshots[user] = asyncio.run(resources.review(session))

    bob = UserResources(Identity("demo", "bob"), tmp_path)
    with closing(bob.open_session("same-name")) as session:
        assert asyncio.run(bob.review(session)) == snapshots["bob"]
    assert all(session._closed for session in sessions)
    assert events == (["open", "close"] * 4 if provider == "mcp" else [])
