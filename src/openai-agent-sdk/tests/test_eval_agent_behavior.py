"""Grade agent tool behavior, then optionally exercise a real model."""

import asyncio
import json
import os
import re
from pathlib import Path

import pytest
from agents import OpenAIResponsesModel
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai_agent_sdk.agent import AgentTurn, build_agent, run_agent_async
from openai_agent_sdk.contracts import PROJECT_ROOT
from openai_agent_sdk.filtering import filter_jobs, parse_criteria
from openai_agent_sdk.jsearch import normalize_job
from openai_agent_sdk.tools.search_jobs import provider_request
from test_agent import ScriptedModel, _answer, _call

CASE_DIR = Path(__file__).parents[1] / "evals"
CASE_PATH = CASE_DIR / "tool-order.json"
SEARCH_ARGUMENT_CASES = json.loads(
    (CASE_DIR / "search-arguments.json").read_text(encoding="utf-8")
)["cases"]
JOB_ID = "jsearch:remote-001"


def run_case(case, tmp_path, model=None):
    stamp = "2026-10-10T00:00:00Z"
    provider_calls = []

    class FixedProvider:
        async def search(self, query, **options):
            provider_calls.append({"query": query, **options})
            return {
                "source": "jsearch", "retrieved_at": stamp,
                "jobs": [normalize_job(job, stamp) for job in case["jobs"]],
            }

    resume = tmp_path / "resume.md"
    resume.write_text(case["resume"], encoding="utf-8")
    results = tmp_path / "results.md"
    async def execute():
        if model is None:
            async with AsyncOpenAI() as client:
                model_name = os.getenv("OPENAI_MODEL", "").strip() or "gpt-5-mini"
                live_model = OpenAIResponsesModel(model_name, client)
                agent = build_agent(
                    model=live_model, provider="mcp", mcp_adapter=FixedProvider(),
                    resume_path=resume, results_path=results,
                )
                return await run_agent_async(case["request"], agent=agent), model_name
        agent = build_agent(
            model=model, provider="mcp", mcp_adapter=FixedProvider(),
            resume_path=resume, results_path=results,
        )
        return await run_agent_async(case["request"], agent=agent), agent.model

    turn, model_name = asyncio.run(execute())
    return turn, results, model_name, provider_calls


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


def grade_search_arguments(
    turn: AgentTurn, provider_calls: list[dict],
    expected_term: str, expected_options: dict,
) -> None:
    queries = [
        json.loads(item["arguments"])["query"]
        for item in turn.history
        if item.get("type") == "function_call" and item["name"] == "search_jobs"
    ]
    assert queries, "The model must call search_jobs."
    assert any(
        re.search(rf"\b{re.escape(expected_term)}\b", query, re.IGNORECASE)
        for query in queries
    ), (
        f"The model must include {expected_term} in a search query."
    )
    assert provider_calls, "The provider must receive a search request."
    assert all(
        all(call.get(key) == value for key, value in expected_options.items())
        for call in provider_calls
    ), "The provider must receive the user's hard filters."


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
    """Model: none. Check five made-up tool histories for the tool-order rule.

    `history` holds the calls and results. `valid` says whether grading should pass.
    Cases cover correct order, early fit, excluded job, missing fit, and saving.
    """
    turn = AgentTurn(text=f"{JOB_ID} fits the resume.", history=history)
    if valid:
        grade_tool_order(turn, JOB_ID)
    else:
        with pytest.raises(AssertionError):
            grade_tool_order(turn, JOB_ID)


def test_tool_order_with_scripted_model(tmp_path, monkeypatch):
    """Model: ScriptedModel. Run the tool-order case through the SDK runner.

    The script searches Python, assesses `JOB_ID`, and answers. `tmp_path` keeps
    the fictional resume and results local; the check requires no saved file.
    """
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    case = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    model = ScriptedModel([
        [_call("search_jobs", {"query": "Python"}, "search")],
        [_call("assess_fit", {"job_id": JOB_ID}, "fit")],
        [_answer(f"{JOB_ID} fits Python experience.")],
    ])
    turn, results, _, _ = run_case(case, tmp_path, model=model)
    grade_tool_order(turn, case["expected_job_ids"][0])
    assert not results.exists()


@pytest.mark.integration
def test_tool_order_with_real_model(tmp_path, monkeypatch):
    """Model: configured OpenAI model. Run `tool-order.json` when opted in.

    `tmp_path` holds the resume and results. The grader checks search-before-fit,
    the expected job ID, and no save; `monkeypatch` disables trace export.
    """
    if os.getenv("RUN_LIVE_AGENT_EVAL") != "1":
        pytest.skip("Set RUN_LIVE_AGENT_EVAL=1 to allow paid model calls.")
    load_dotenv(PROJECT_ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        pytest.fail("OPENAI_API_KEY is required for the live model eval.")
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    case = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    turn, results, model_name, _ = run_case(case, tmp_path)
    grade_tool_order(turn, case["expected_job_ids"][0])
    assert not results.exists()
    print(f"Case {case['id']} / {model_name}: passed. Answer: {turn.text}")


@pytest.mark.parametrize(
    ("query", "provider_options", "valid"),
    [
        pytest.param(
            "Python engineer", {"country": "us", "remote_only": True}, True,
            id="valid",
        ),
        pytest.param(
            "SQL engineer", {"country": "us", "remote_only": True}, False,
            id="wrong-model-query",
        ),
        pytest.param(
            "Pythonista", {"country": "us", "remote_only": True}, False,
            id="partial-term",
        ),
        pytest.param(
            "Python engineer", {"country": None, "remote_only": True}, False,
            id="missing-country",
        ),
        pytest.param(
            "Python engineer", {"country": "us", "remote_only": False}, False,
            id="missing-remote",
        ),
    ],
)
def test_search_arguments_grader(query, provider_options, valid):
    """Model: none. Check the grader against the Python search case.

    `query` is the model's raw tool argument; `provider_options` holds country
    and remote filters. `valid` marks the one passing and four failing inputs.
    """
    case = SEARCH_ARGUMENT_CASES[0]
    turn = AgentTurn(text="", history=[{
        "type": "function_call", "name": "search_jobs",
        "arguments": json.dumps({"query": query}),
    }])
    provider_calls = [{"query": f"{query} Python", **provider_options}]
    if valid:
        grade_search_arguments(
            turn, provider_calls,
            case["expected_query_term"], case["expected_provider_options"],
        )
    else:
        with pytest.raises(AssertionError):
            grade_search_arguments(
                turn, provider_calls,
                case["expected_query_term"], case["expected_provider_options"],
            )


@pytest.mark.parametrize("case", SEARCH_ARGUMENT_CASES, ids=lambda case: case["id"])
def test_search_argument_case_filters(case):
    """Model: none. Check each of the six search-argument fixtures offline.

    `case` supplies the request, expected provider options, and fictional jobs.
    Python, SQL, Java, Rust, Kubernetes, and TypeScript cases must retain a job.
    """
    criteria = parse_criteria(case["request"])
    _, options = provider_request(case["expected_query_term"], criteria)
    assert options == case["expected_provider_options"]
    jobs = [normalize_job(job, "2026-10-10T00:00:00Z") for job in case["jobs"]]
    assert filter_jobs(jobs, criteria)["jobs"]


@pytest.mark.integration
@pytest.mark.parametrize("case", SEARCH_ARGUMENT_CASES, ids=lambda case: case["id"])
def test_search_arguments_with_real_model(case, tmp_path, monkeypatch):
    """Model: configured OpenAI model. Run each search-argument case when opted in.

    `case` sets the expected raw query term and provider filters; `tmp_path`
    isolates files. The check also requires that no results file was written.
    """
    if os.getenv("RUN_LIVE_AGENT_EVAL") != "1":
        pytest.skip("Set RUN_LIVE_AGENT_EVAL=1 to allow paid model calls.")
    load_dotenv(PROJECT_ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        pytest.fail("OPENAI_API_KEY is required for the live model eval.")
    monkeypatch.setenv("JOB_AGENT_SDK_TRACING", "false")
    turn, results, model_name, provider_calls = run_case(case, tmp_path)
    grade_search_arguments(
        turn, provider_calls,
        case["expected_query_term"], case["expected_provider_options"],
    )
    assert not results.exists()
    print(f"Case {case['id']} / {model_name}: passed. Provider calls: {provider_calls}")
