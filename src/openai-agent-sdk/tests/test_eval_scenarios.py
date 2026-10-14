"""Versioned cases with deterministic tool graders and opt-in model runs."""

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from openai_agent_sdk.agent import build_agent, run_agent
from openai_agent_sdk.contracts import PROJECT_ROOT
from openai_agent_sdk.jsearch import normalize_job
from test_agent import ScriptedModel, _answer, _call

CASE_DIR = Path(__file__).parents[1] / "evals"
CASES = [
    json.loads((CASE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    for name in ("duplicate-results", "missing-filter-evidence", "partial-fit")
]
names = ["cat", "dog", "tiger"]
print(lambda x:len(x), names)

@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize(
    "model_kind",
    ["scripted", pytest.param("live", marks=pytest.mark.integration)],
)
def test_job_search_eval(case, model_kind, tmp_path, monkeypatch):
    if model_kind == "live":
        if os.getenv("RUN_LIVE_AGENT_EVAL") != "1":
            pytest.skip("Set RUN_LIVE_AGENT_EVAL=1 to allow paid model calls.")
        load_dotenv(PROJECT_ROOT / ".env")
        if not os.getenv("OPENAI_API_KEY", "").strip():
            pytest.fail("OPENAI_API_KEY is required for the live model eval.")
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
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
    resume.write_text(case["resume"], encoding="utf-8")
    results = tmp_path / "results.md"
    model = None
    if model_kind == "scripted":
        steps = [[_call("search_jobs", {"query": "Python"}, "search")]]
        for job_id in case["expected_job_ids"]:
            steps.append([_call("assess_fit", {"job_id": job_id}, "fit")])
        answer = "No matching jobs"
        if case["expected_job_ids"]:
            answer = " ".join([
                *case["expected_job_ids"], case["resume"],
                case["jobs"][0]["job_description"],
                "SQL is unverified" if case["expected_fit"]["unverified"] else "",
            ])
        steps.append([_answer(answer)])
        model = ScriptedModel(steps)
    turn = run_agent(case["request"], agent=build_agent(
        model=model, provider="mcp", mcp_adapter=FixedProvider(),
        resume_path=resume, results_path=results,
    ))
    tool_calls = {
        item["call_id"]: item["name"] for item in turn.history
        if item.get("type") == "function_call"
    }
    outputs = [
        (tool_calls[item["call_id"]], json.loads(item["output"]))
        for item in turn.history
        if item.get("type") == "function_call_output"
        and tool_calls[item["call_id"]] in {"search_jobs", "assess_fit"}
    ]
    searches = [output for name, output in outputs if name == "search_jobs"]
    assert searches, "The agent must search before answering."
    assert all(options == {"country": "us", "remote_only": True} for options in calls)
    for output in searches:
        assert [job["id"] for job in output["jobs"]] == case["expected_job_ids"]
        assert [job["id"] for job in output["duplicates"]] == (
            case["expected_duplicates"]
        )
        assert [job["id"] for job in output["excluded"]] == case["expected_excluded"]
    fits = [output for name, output in outputs if name == "assess_fit"]
    assert {fit["job_id"] for fit in fits} == set(case["expected_job_ids"])
    for fit in fits:
        expected = case["expected_fit"]
        assert fit["status"] == expected["status"]
        for field in ("matched", "unverified"):
            assert [item["skill"] for item in fit[field]] == expected[field]
        for evidence in fit["matched"]:
            assert evidence["resume_excerpt"] in case["resume"]
            assert evidence["job_excerpt"] in case["jobs"][0]["job_description"]
            assert evidence["resume_excerpt"] in turn.text
            assert evidence["job_excerpt"] in turn.text
        if fit["unverified"]:
            assert "sql" in turn.text.lower() and "unverified" in turn.text.lower()
    # shortcut: literal answer checks, add semantic grading for broader answer evals.
    if not case["expected_job_ids"]:
        assert "no matching jobs" in turn.text.lower()
    for job_id in case["expected_job_ids"]:
        assert job_id in turn.text
    assert "save_results" not in tool_calls.values()
    assert not results.exists(), "No case authorizes writing results."
    print(f"Case {case['id']} / {model_kind}: passed. Answer: {turn.text}")
