"""Fake-provider tests. These never make a network request."""

import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest
from openai_agent_sdk.jsearch import JSearchError, normalize_job, search_jsearch
from openai_agent_sdk.tools import search_jobs

STAMP = "2026-10-03T00:00:00+00:00"
RAW_JOB = {
    "job_id": "abc-123",
    "job_title": "Python Engineer",
    "employer_name": "Example Co",
    "job_location": "Remote, US",
    "job_description": "Build Python services.",
    "job_apply_link": "https://example.com/apply/123",
    "job_posted_at_datetime_utc": "2026-10-01T12:00:00Z",
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_normalization_maps_required_and_optional_fields():
    job = normalize_job(RAW_JOB, STAMP)

    assert job == {
        "id": "jsearch:abc-123",
        "provider": "jsearch",
        "provider_job_id": "abc-123",
        "url": "https://example.com/apply/123",
        "title": "Python Engineer",
        "company": "Example Co",
        "location": "Remote, US",
        "description": "Build Python services.",
        "posted_at": "2026-10-01T12:00:00Z",
        "retrieved_at": STAMP,
        "employment_type": None,
    }
    without_optional = normalize_job(
        {key: value for key, value in RAW_JOB.items() if key not in {
            "job_location", "job_posted_at_datetime_utc"
        }},
        STAMP,
    )
    assert without_optional["location"] is None
    assert without_optional["posted_at"] is None


@pytest.mark.parametrize(
    "bad_job",
    [
        None,
        {},
        {**RAW_JOB, "job_title": ""},
        {**RAW_JOB, "job_apply_link": "javascript:alert(1)"},
    ],
)
def test_normalization_rejects_bad_records(bad_job):
    with pytest.raises(ValueError):
        normalize_job(bad_job, STAMP)


def test_adapter_uses_bounded_request_and_normalizes_response():
    observed = {}

    def fake_open(request, *, timeout):
        observed["request"] = request
        observed["timeout"] = timeout
        return FakeResponse(
            json.dumps({"data": {"jobs": [RAW_JOB] * 12, "cursor": "next"}}).encode()
        )

    result = search_jsearch(
        "remote Python jobs in US",
        api_key="secret-test-key",
        opener=fake_open,
        retrieved_at=STAMP,
    )

    request = observed["request"]
    assert urlparse(request.full_url).path == "/search-v2"
    assert request.get_header("X-rapidapi-key") == "secret-test-key"
    assert request.get_header("X-rapidapi-host") == "jsearch.p.rapidapi.com"
    assert parse_qs(urlparse(request.full_url).query) == {
        "query": ["remote Python jobs in US"],
        "page": ["1"],
        "num_pages": ["1"],
    }
    assert observed["timeout"] == 8
    assert result["source"] == "jsearch"
    assert result["retrieved_at"] == STAMP
    assert len(result["jobs"]) == 10


def test_adapter_does_not_expose_key_in_errors():
    def unauthorized(request, *, timeout):
        raise HTTPError(request.full_url, 401, "secret-test-key", {}, None)

    with pytest.raises(JSearchError, match="authentication failed") as error:
        search_jsearch("Python", api_key="secret-test-key", opener=unauthorized)
    assert "secret-test-key" not in str(error.value)


def test_adapter_rejects_malformed_payload_without_partial_results():
    def malformed(_request, *, timeout):
        return FakeResponse(json.dumps({"data": {"jobs": [RAW_JOB, {}]}}).encode())

    with pytest.raises(JSearchError, match="malformed job record"):
        search_jsearch("Python", api_key="test-key", opener=malformed)


def test_adapter_rejects_wrong_v2_response_shape():
    def old_shape(_request, *, timeout):
        return FakeResponse(json.dumps({"data": [RAW_JOB]}).encode())

    with pytest.raises(JSearchError, match="invalid response"):
        search_jsearch("Python", api_key="test-key", opener=old_shape)


def test_tool_contract_works_with_fake_live_adapter():
    def fake_search(query, *, api_key):
        assert query == "Python"
        assert api_key == "test-key"
        return {"source": "jsearch", "retrieved_at": STAMP, "jobs": [
            normalize_job(RAW_JOB, STAMP)
        ]}

    result = json.loads(search_jobs(
        "Python", provider="jsearch", api_key="test-key", live_search=fake_search
    ))
    assert result["source"] == "jsearch"
    assert result["jobs"][0]["id"] == "jsearch:abc-123"


def test_tool_reports_missing_key_without_mock_fallback():
    assert search_jobs("Python", provider="jsearch", api_key="") == (
        "Error: JSearch API key is missing. Set JSEARCH_API_KEY."
    )
