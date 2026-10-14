"""Grade agent tool order, then optionally exercise a real model."""

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from openai_agent_sdk.agent import AgentTurn, build_agent, run_agent
from openai_agent_sdk.contracts import PROJECT_ROOT
from openai_agent_sdk.jsearch import normalize_job
from test_agent import ScriptedModel, _answer, _call

CASE_PATH = Path(__file__).parents[1] / "evals" / "tool-order.json"
JOB_ID = "jsearch:remote-001"


def run_case(case, tmp_path, model=None):
    stamp = "2026-10-10T00:00:00Z"

    class FixedProvider:
        async def search(self, query, **options):
            return {
                "source": "jsearch", "retrieved_at": stamp,
                "jobs": [normalize_job(job, stamp) for job in case["jobs"]],
            }

    resume = tmp_path / "resume.md"
    resume.write_text(case["resume"], encoding="utf-8")
    results = tmp_path / "results.md"
    agent = build_agent(
        model=model, provider="mcp", mcp_adapter=FixedProvider(),
        resume_path=resume, results_path=results,
    )
    return run_agent(case["request"], agent=agent), results, agent.model


def grade_tool_order(turn: AgentTurn, expected_id: str) -> None:
    search_calls = set()
    returned_jobs = set()
    fit_ids = []
    for item in turn.history:
        if item.get("type") == "function_call":
            if item["name"] == "search_jobs":
                search_calls.add(item["call_id"])
            elif item["name"] == "assess_fit":
                job_id = json.loads(item["arguments"])["job_id"]
                assert job_id in returned_jobs
                fit_ids.append(job_id)
            else:
                assert item["name"] != "save_results", "The request forbids saving."
        elif (item.get("type") == "function_call_output"
              and item["call_id"] in search_calls):
            output = json.loads(item["output"])
            returned_jobs.update(job["id"] for job in output["jobs"])
    assert returned_jobs == {expected_id}
    assert set(fit_ids) == {expected_id}
    assert expected_id in turn.text


SEARCH = {"type": "function_call", "name": "search_jobs", "call_id": "search"}
RESULT = {
    "type": "function_call_output", "call_id": "search",
    "output": json.dumps({"jobs": [{"id": JOB_ID}]}),
}
FIT = {
    "type": "function_call", "name": "assess_fit",
    "arguments": json.dumps({"job_id": JOB_ID}),
}


@pytest.mark.parametrize(
    ("history", "valid"),
    [
        pytest.param([SEARCH, RESULT, FIT], True, id="valid"),
        pytest.param([SEARCH, FIT, RESULT], False, id="fit-before-result"),
        pytest.param([
            SEARCH, RESULT,
            {**FIT, "arguments": json.dumps({"job_id": "jsearch:office-002"})},
        ], False, id="excluded-job"),
        pytest.param([SEARCH, RESULT], False, id="missing-fit"),
        pytest.param([
            SEARCH, RESULT, FIT,
            {"type": "function_call", "name": "save_results"},
        ], False, id="forbidden-save"),
    ],
)
def test_tool_order_grader(history, valid):
    turn = AgentTurn(text=f"{JOB_ID} fits the resume.", history=history)
    if valid:
        grade_tool_order(turn, JOB_ID)
    else:
        with pytest.raises(AssertionError):
            grade_tool_order(turn, JOB_ID)


def test_tool_order_with_scripted_model(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    case = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    model = ScriptedModel([
        [_call("search_jobs", {"query": "Python"}, "search")],
        [_call("assess_fit", {"job_id": JOB_ID}, "fit")],
        [_answer(f"{JOB_ID} fits Python experience.")],
    ])
    turn, results, _ = run_case(case, tmp_path, model=model)
    grade_tool_order(turn, case["expected_job_ids"][0])
    assert not results.exists()


@pytest.mark.integration
def test_tool_order_with_real_model(tmp_path, monkeypatch):
    if os.getenv("RUN_LIVE_AGENT_EVAL") != "1":
        pytest.skip("Set RUN_LIVE_AGENT_EVAL=1 to allow paid model calls.")
    load_dotenv(PROJECT_ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        pytest.fail("OPENAI_API_KEY is required for the live model eval.")
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    case = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    turn, results, model_name = run_case(case, tmp_path)
    grade_tool_order(turn, case["expected_job_ids"][0])
    assert not results.exists()
    print(f"Case {case['id']} / {model_name}: passed. Answer: {turn.text}")
