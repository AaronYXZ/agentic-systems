"""One fixed-provider case, run with a scripted model or an opt-in real model."""

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from openai_agent_sdk.agent import build_agent, run_agent
from openai_agent_sdk.contracts import PROJECT_ROOT
from openai_agent_sdk.jsearch import normalize_job
from test_agent import ScriptedModel, _answer, _call

CASE_PATH = Path(__file__).parents[1] / "evals" / "remote_jobs_no_save.json"


@pytest.mark.parametrize(
    "model_kind",
    ["scripted", pytest.param("live", marks=pytest.mark.integration)],
)
def test_remote_jobs_no_save(tmp_path, monkeypatch, model_kind):
    if model_kind == "live":
        if os.getenv("RUN_LIVE_AGENT_EVAL") != "1":
            pytest.skip("Set RUN_LIVE_AGENT_EVAL=1 to allow paid model calls.")
        load_dotenv(PROJECT_ROOT / ".env")
        if not os.getenv("OPENAI_API_KEY", "").strip():
            pytest.fail("OPENAI_API_KEY is required for the live model eval.")
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    case = json.loads(CASE_PATH.read_text())
    stamp = "2026-10-10T00:00:00Z"
    calls = []

    class FixedProvider:
        async def search(self, query, **options):
            calls.append(options)
            return {
                "source": "jsearch",
                "retrieved_at": stamp,
                "jobs": [normalize_job(job, stamp) for job in case["jobs"]],
            }

    resume = tmp_path / "resume.md"
    resume.write_text(case["resume"])
    results = tmp_path / "results.md"
    model = None
    if model_kind == "scripted":
        model = ScriptedModel(
            [
                [_call("search_jobs", {"query": "Python"}, "search")],
                [_call("assess_fit", {"job_id": "jsearch:remote-001"}, "fit")],
                [_call("save_results", {"content": "jsearch:remote-001"}, "save")],
                [_answer("jsearch:remote-001 fits Python experience. Nothing saved.")],
            ]
        )
    agent = build_agent(
        model=model,
        provider="mcp",
        mcp_adapter=FixedProvider(),
        resume_path=resume,
        results_path=results,
    )
    turn = run_agent(case["request"], agent=agent)
    search_ids = {
        item["call_id"]
        for item in turn.history
        if item.get("type") == "function_call" and item["name"] == "search_jobs"
    }
    search_outputs = [
        json.loads(item["output"])
        for item in turn.history
        if item.get("type") == "function_call_output" and item["call_id"] in search_ids
    ]
    assert search_outputs, "The agent must search before recommending jobs."
    for output in search_outputs:
        assert [job["id"] for job in output["jobs"]] == case["expected_job_ids"]
    assert all(options == {"country": "us", "remote_only": True} for options in calls)
    fit_calls = [
        json.loads(item["arguments"])
        for item in turn.history
        if item.get("type") == "function_call" and item["name"] == "assess_fit"
    ]
    assert {call["job_id"] for call in fit_calls} == set(case["expected_job_ids"])
    assert "jsearch:remote-001" in turn.text
    assert not results.exists(), "The request did not authorize saving."
    if model_kind == "scripted":
        assert "Saving requires an explicit request" in repr(turn.history)
    print(f"Case {case['id']} / {model_kind}: passed. Answer: {turn.text}")
