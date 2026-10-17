"""Application exposure checks with fake secrets and a scripted model."""

import asyncio
import json
from contextlib import closing

import pytest
from openai_agent_sdk.agent import build_agent, run_agent
from openai_agent_sdk.contracts import JOBS_PATH
from openai_agent_sdk.identity import Identity, UserResources
from test_agent import ScriptedModel, _answer, _call


@pytest.fixture
def owners(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "MODEL-KEY-CANARY")
    monkeypatch.setenv("OPENWEBNINJA_API_KEY", "MCP-KEY-CANARY")
    a = UserResources(Identity("demo", "alice"), tmp_path)
    b = UserResources(Identity("demo", "bob"), tmp_path)
    for owner, text in [(a, "ALICE-RESUME: Built Python services."),
                        (b, "BOB-PRIVATE-CANARY")]:
        owner.file("resume.md").parent.mkdir(parents=True)
        owner.file("resume.md").write_text(text)
    return a, b


def test_only_task_evidence_reaches_model_and_replayed_history(owners, tmp_path):
    a, b = owners
    jobs = json.loads(JOBS_PATH.read_text())
    for job in jobs:
        job["internal_notes"] = "CATALOG-SECRET-CANARY"
    catalog = tmp_path / "jobs.json"
    catalog.write_text(json.dumps(jobs))
    with closing(b.open_session("chat")) as session:
        asyncio.run(session.add_items([
            {"role": "user", "content": "BOB-PRIVATE-CANARY"}
        ]))
    model = ScriptedModel([
        [_call("search_jobs", {"query": "Python"}, "jobs")],
        [_call("read_resume", {}, "resume")],
        [_answer("Done")],
        [_answer("Continued")],
    ])
    agent = build_agent(model=model, provider="mock", resources=a,
                        catalog_path=catalog, jsearch_api_key="PROVIDER-KEY-CANARY")
    with closing(a.open_session("chat")) as session:
        first = run_agent("Find Python jobs", agent=agent,
                          identity=a.identity, session=session)
        second = run_agent("Continue", agent=agent,
                           identity=a.identity, session=session)
    exposed = repr((model.instructions, model.inputs, first.history, second.history))
    job_output = next(item["output"] for item in first.history
                      if item.get("type") == "function_call_output"
                      and item["call_id"] == "jobs")
    assert json.loads(job_output)["jobs"]
    assert "ALICE-RESUME" in exposed
    assert "Python" in exposed
    for secret in ("MODEL-KEY-CANARY", "MCP-KEY-CANARY", "PROVIDER-KEY-CANARY",
                   "CATALOG-SECRET-CANARY", "BOB-PRIVATE-CANARY"):
        assert secret not in exposed
        assert secret not in repr(first.tool_traces)
    assert "save_allowed" not in exposed
    assert "searched_jobs" not in exposed
    assert "ALICE-RESUME" not in repr(first.tool_traces)


@pytest.mark.parametrize("operation", ["search_document_records", "read_resume_file"])
def test_raw_tool_exceptions_do_not_reach_model_or_traces(
    owners, monkeypatch, operation
):
    a, _ = owners
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")

    def fail(*args, **kwargs):
        raise RuntimeError("EXCEPTION-SECRET-CANARY /private/internal/path")

    monkeypatch.setattr(f"openai_agent_sdk.agent.{operation}", fail)
    tool = (
        "search_documents" if operation == "search_document_records" else "read_resume"
    )
    arguments = {"query": "Python"} if tool == "search_documents" else {}
    model = ScriptedModel([[_call(tool, arguments, "failed")], [_answer("Failed")]])
    turn = run_agent("Read my evidence", identity=a.identity,
                     agent=build_agent(model=model, resources=a, provider="mock"))
    exposed = repr((model.instructions, model.inputs, turn.history, turn.tool_traces))
    assert "EXCEPTION-SECRET-CANARY" not in exposed
    assert "/private/internal/path" not in exposed
    trace, = turn.tool_traces
    assert (trace.outcome, trace.error_class) == ("error", "RuntimeError")
