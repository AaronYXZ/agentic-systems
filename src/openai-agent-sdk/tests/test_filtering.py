"""Table-driven hard-filter and preference checks without a model or API."""

import pytest
from openai_agent_sdk.filtering import (
    CriteriaError,
    SearchCriteria,
    filter_jobs,
    parse_criteria,
)
from openai_agent_sdk.jsearch import normalize_job
from openai_agent_sdk.tools import search_jobs

STAMP = "2026-10-03T00:00:00+00:00"


def _job(**changes):
    raw = {
        "job_id": "one",
        "job_title": "Python Engineer",
        "employer_name": "Example Co",
        "job_location": "San Francisco, CA",
        "job_country": "US",
        "job_is_remote": True,
        "job_description": "Requirements: Python and SQL experience.",
        "job_apply_link": "https://example.com/one",
    }
    raw.update(changes)
    return normalize_job(raw, STAMP)


@pytest.mark.parametrize(
    ("change", "criteria", "expected_reason"),
    [
        ({}, SearchCriteria(location="US", remote_only=True), None),
        ({"job_country": None}, SearchCriteria(location="US"), "location"),
        ({"job_is_remote": None}, SearchCriteria(remote_only=True), "remote"),
        ({"job_is_remote": False}, SearchCriteria(remote_only=True), "remote"),
        ({}, SearchCriteria(role="Data Scientist"), "role"),
        ({}, SearchCriteria(required_skills=("Kubernetes",)), "skill"),
        ({}, SearchCriteria(required_skills=("SQL",)), None),
    ],
)
def test_hard_filters_include_or_explain_exclusion(change, criteria, expected_reason):
    result = filter_jobs([_job(**change)], criteria)

    if expected_reason is None:
        assert len(result["jobs"]) == 1
        assert result["excluded"] == []
    else:
        assert result["jobs"] == []
        assert expected_reason in " ".join(result["excluded"][0]["reasons"])


def test_preferences_change_order_but_do_not_exclude():
    plain = _job(job_id="plain", job_apply_link="https://example.com/plain",
                 job_description="Requirements: Python experience.")
    preferred = _job(job_id="preferred",
                     job_apply_link="https://example.com/preferred",
                     job_description="Requirements: Python. Preferred: SQL.")

    result = filter_jobs([plain, preferred], SearchCriteria(preferred_skills=("SQL",)))

    assert [job["id"] for job in result["jobs"]] == [
        "jsearch:preferred", "jsearch:plain"
    ]
    assert not result["excluded"]


def test_plain_request_infers_only_unambiguous_remote_and_us():
    assert parse_criteria("Find remote Python jobs in the US") == SearchCriteria(
        location="US", remote_only=True
    )
    assert parse_criteria("Find Python jobs") == SearchCriteria()


def test_explicit_filter_clause_keeps_user_owned_criteria():
    assert parse_criteria(
        "Find jobs filters: location=Chicago; remote=true; "
        "role=Engineer; skills=Python,SQL; prefer_skills=AWS"
    ) == SearchCriteria(
        location="Chicago", remote_only=True, role="Engineer",
        required_skills=("Python", "SQL"), preferred_skills=("AWS",),
    )


@pytest.mark.parametrize(
    "user_text",
    [
        "Find remote jobs filters: remote=false",
        "Find jobs in US filters: location=Canada",
        "Find jobs filters: remote=true; remote=false",
        "Find jobs filters: salary=100000",
        "Find jobs filters: remote=maybe",
    ],
)
def test_conflicting_or_invalid_criteria_are_rejected(user_text):
    with pytest.raises(CriteriaError):
        parse_criteria(user_text)


def test_user_owned_role_and_city_reach_provider_even_if_model_omits_them():
    observed = {}

    def fake_search(query, *, api_key, country, remote_only):
        observed["query"] = query
        return {"source": "jsearch", "retrieved_at": STAMP, "jobs": []}

    search_jobs(
        "Python jobs", provider="jsearch", api_key="test-key",
        criteria=SearchCriteria(role="Engineer", location="Chicago"),
        live_search=fake_search,
    )

    assert observed["query"] == "Python jobs Engineer in Chicago"
