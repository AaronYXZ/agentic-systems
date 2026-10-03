"""Keep distinct openings and record high-confidence duplicate reasons."""

from openai_agent_sdk.deduplication import canonical_url, deduplicate_jobs
from openai_agent_sdk.jsearch import normalize_job

STAMP = "2026-10-03T00:00:00+00:00"


def _job(job_id, url, *, title="Engineer", description="Build Python services."):
    return normalize_job({
        "job_id": job_id,
        "job_title": title,
        "employer_name": "Example Co",
        "job_location": "Chicago, IL",
        "job_description": description,
        "job_apply_link": url,
    }, STAMP)


def test_repeated_provider_id_is_a_duplicate_even_with_changed_url():
    jobs = [
        _job("same", "https://example.com/old"),
        _job("same", "https://example.com/new"),
    ]

    result = deduplicate_jobs(jobs)

    assert len(result["jobs"]) == 1
    assert result["duplicates"][0]["reason"] == "provider_id"


def test_tracking_url_variants_are_duplicates_but_identity_params_remain():
    assert canonical_url("https://www.example.com/job/1?utm_source=x&jobId=1") == (
        "https://example.com/job/1?jobId=1"
    )
    jobs = [
        _job("one", "https://example.com/job/1?jobId=1"),
        _job("two", "https://www.example.com/job/1/?jobId=1&utm_source=x"),
        _job("three", "https://example.com/job/1?jobId=2"),
    ]

    result = deduplicate_jobs(jobs)

    assert [job["id"] for job in result["jobs"]] == ["jsearch:one", "jsearch:three"]
    assert result["duplicates"][0]["reason"] == "canonical_url"


def test_repost_with_same_content_uses_conservative_fallback():
    jobs = [
        _job("old", "https://example.com/old"),
        _job("new", "https://example.com/new"),
    ]

    result = deduplicate_jobs(jobs)

    assert len(result["jobs"]) == 1
    assert result["duplicates"][0]["reason"] == "same_content"


def test_exact_content_can_match_across_sources_without_merging_uncertain_jobs():
    first = _job("first", "https://example.com/first")
    second = _job("second", "https://other.example/jobs/second")
    second["provider"] = "other"
    second["id"] = "other:second"

    result = deduplicate_jobs([first, second])

    assert len(result["jobs"]) == 1
    assert result["duplicates"] == [{
        "id": "other:second", "duplicate_of": "jsearch:first",
        "reason": "same_content",
    }]


def test_same_title_and_company_but_different_openings_remain_distinct():
    jobs = [
        _job("first", "https://example.com/first", description="Build search."),
        _job("second", "https://example.com/second", description="Build payments."),
    ]

    result = deduplicate_jobs(jobs)

    assert len(result["jobs"]) == 2
    assert not result["duplicates"]


def test_shared_generic_apply_url_does_not_merge_different_roles():
    jobs = [
        _job("engineer", "https://example.com/apply", title="Engineer"),
        _job("scientist", "https://example.com/apply", title="Scientist"),
    ]

    assert len(deduplicate_jobs(jobs)["jobs"]) == 2
