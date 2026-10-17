"""Synthetic local retrieval checks. No model or provider calls."""

import json
from datetime import date

import pytest
from openai_agent_sdk.identity import Identity, UserResources
from openai_agent_sdk.retrieval import load_documents, search_documents


@pytest.fixture
def catalog(tmp_path):
    resources = UserResources(Identity("demo", "alice"), tmp_path)
    resources.file("documents.json").parent.mkdir(parents=True)
    records = [
        {"document_id": key, "owner": {"tenant_id": "demo", "user_id": "alice"},
         "kind": kind, "reference": "fictional-company", "source": f"fixture:{key}",
         "updated_on": "2026-10-01", "text": text}
        for key, kind, text in [
            ("b", "job", "Python Python engineer."),
            ("a", "company", "Our Python team works remotely."),
            ("c", "job", "Java engineer."),
        ]
    ]
    resources.file("documents.json").write_text(json.dumps(records))
    return resources, records


def test_keyword_search_returns_bounded_source_text(catalog):
    resources, records = catalog
    records[0]["text"] = "Intro. Python " + "engineer " * 100
    records.extend(dict(records[0], document_id=f"extra-{i}") for i in range(6))
    resources.file("documents.json").write_text(json.dumps(records))
    hits = search_documents(resources, "PYTHON remotely")
    assert len(hits) == 5
    assert [hit.document_id for hit in hits[:2]] == ["b", "a"]
    assert hits[0].source == "fixture:b"
    assert hits[0].updated_on == date(2026, 10, 1)
    assert hits[0].owner == resources.identity
    assert hits[0].reference == "fictional-company"
    assert hits[0].kind == "job"
    assert len(hits[0].text) == 400
    for hit in hits:
        original = next(
            r["text"] for r in records if r["document_id"] == hit.document_id
        )
        assert hit.text in original
        assert len(hit.text) <= 400
    assert search_documents(resources, "py") == []
    assert search_documents(resources, "   ") == []


@pytest.mark.parametrize("tenant,user", [("demo", "bob"), ("other", "alice")])
def test_other_owner_not_searched_and_forged_catalog_denied(catalog, tenant, user):
    resources, records = catalog
    other = UserResources(Identity(tenant, user), resources.root)
    other.file("documents.json").parent.mkdir(parents=True)
    other.file("documents.json").write_text(json.dumps([
        dict(records[0], owner={"tenant_id": tenant, "user_id": user}, text="CANARY")
    ]))
    assert search_documents(resources, "CANARY") == []
    records.append(json.loads(other.file("documents.json").read_text())[0])
    resources.file("documents.json").write_text(json.dumps(records))
    with pytest.raises(PermissionError):
        search_documents(resources, "python")


def test_linked_catalog_denied(catalog):
    resources, _ = catalog
    path = resources.file("documents.json")
    outside = resources.root / "private.json"
    outside.write_text("[]")
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(PermissionError):
        load_documents(resources)


def test_missing_and_invalid_catalog(catalog):
    resources, records = catalog
    path = resources.file("documents.json")
    path.unlink()
    assert load_documents(resources) == []
    for invalid in [{}, [dict(records[0], kind="unknown")],
                    [dict(records[0], updated_on="yesterday")],
                    [dict(records[0], text="")], [records[0], records[0]]]:
        path.write_text(json.dumps(invalid))
        with pytest.raises(ValueError):
            load_documents(resources)


def test_retrieval_tool_schema_output_and_trace(catalog, monkeypatch):
    from openai_agent_sdk.agent import build_agent, run_agent
    from test_agent import ScriptedModel, _answer, _call

    resources, _ = catalog
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    model = ScriptedModel([
        [_call("search_documents", {"query": "Python"}, "docs-1")],
        [_answer("Done")],
    ])
    agent = build_agent(model=model, resources=resources, provider="mock")
    tool = next(tool for tool in agent.tools if tool.name == "search_documents")
    assert set(tool.params_json_schema["properties"]) == {"query"}
    turn = run_agent("Read my Python notes", agent=agent, identity=resources.identity)
    output = next(item["output"] for item in turn.history
                  if item.get("type") == "function_call_output")
    hits = json.loads(output)
    assert [hit["document_id"] for hit in hits] == ["b", "a"]
    assert "owner" not in hits[0]
    assert hits[0]["source"] == "fixture:b"
    assert hits[0]["updated_on"] == "2026-10-01"
    assert hits[0]["text"] == "Python Python engineer."
    trace, = turn.tool_traces
    assert (trace.call_id, trace.source, trace.outcome) == (
        "docs-1", "local_documents", "success"
    )
    assert "Python" not in repr(trace)
    assert "search_documents" not in {
        tool.name for tool in build_agent(provider="mock").tools
    }


def test_retrieval_tool_checks_context_before_read(catalog, monkeypatch):
    import asyncio

    from agents.tool_context import ToolContext
    from openai_agent_sdk.agent import JobAgentContext, build_agent

    resources, _ = catalog
    agent = build_agent(resources=resources, provider="mock")
    tool = next(tool for tool in agent.tools if tool.name == "search_documents")

    def unexpected_read(*args):
        pytest.fail("Retrieval ran before checking identity")

    monkeypatch.setattr(
        "openai_agent_sdk.agent.search_document_records", unexpected_read
    )
    context = JobAgentContext(save_allowed=False, identity=Identity("demo", "bob"))
    ctx = ToolContext(context=context, tool_name="search_documents",
                      tool_call_id="denied", tool_arguments='{"query":"Python"}')
    asyncio.run(tool.on_invoke_tool(ctx, '{"query":"Python"}'))
    assert context.tool_traces[0].error_class == "PermissionError"


def test_retrieval_tool_respects_budget(catalog, monkeypatch):
    from openai_agent_sdk.agent import RunBudgetExceeded, build_agent, run_agent
    from test_agent import ScriptedModel, _call

    resources, _ = catalog
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")

    def unexpected_read(*args):
        pytest.fail("Retrieval ran after budget exhaustion")

    monkeypatch.setattr(
        "openai_agent_sdk.agent.search_document_records", unexpected_read
    )
    model = ScriptedModel([
        [_call("read_resume", {}, "first")],
        [_call("search_documents", {"query": "Python"}, "d")],
    ])
    with pytest.raises(RunBudgetExceeded):
        run_agent(
            "Read notes", identity=resources.identity, max_tool_calls=1,
            agent=build_agent(resources=resources, provider="mock", model=model),
        )
