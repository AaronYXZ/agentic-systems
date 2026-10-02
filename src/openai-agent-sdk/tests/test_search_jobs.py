"""Behavioral checks for deterministic mock search."""

import json

from openai_agent_sdk.contracts import JOBS_PATH
from openai_agent_sdk.tools import search_jobs


def _jobs(query: str) -> list[dict[str, object]]:
    result = json.loads(search_jobs(query))
    assert result["source"] == "mock"
    return result["jobs"]


def test_role_match_is_case_insensitive():
    assert [job["id"] for job in _jobs("MACHINE LEARNING")] == ["job-001"]


def test_skill_match():
    assert [job["id"] for job in _jobs("ranking")] == ["job-001"]


def test_location_match():
    assert [job["id"] for job in _jobs("Oakland")] == ["job-002"]


def test_empty_query_is_rejected():
    assert search_jobs(" \t ") == "Error: Search query cannot be empty."


def test_no_match_is_clear():
    result = json.loads(search_jobs("quantum cryptography"))
    assert result == {
        "source": "mock",
        "jobs": [],
        "message": "No mock jobs matched the query.",
    }


def test_results_are_complete_catalog_records_in_stable_order():
    catalog = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    first = _jobs("Python")
    second = _jobs("Python")

    assert first == second == catalog
    assert len({job["id"] for job in first}) == len(first)
    assert {job["url"] for job in first} == {job["url"] for job in catalog}


def test_unavailable_or_invalid_catalog_has_clear_error(tmp_path):
    missing = tmp_path / "missing.json"
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not JSON", encoding="utf-8")

    assert search_jobs("Python", catalog_path=missing) == (
        "Error: Mock job catalog is unavailable."
    )
    assert search_jobs("Python", catalog_path=invalid) == (
        "Error: Mock job catalog is unavailable."
    )
