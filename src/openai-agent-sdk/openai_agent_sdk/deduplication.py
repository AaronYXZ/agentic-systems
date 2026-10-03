"""Conservative identity rules for normalized live job records."""

import re
from typing import Literal, TypedDict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .contracts import LiveJobPosting

_TRACKING_KEYS = frozenset({"gclid", "fbclid", "msclkid"})


class Duplicate(TypedDict):
    id: str
    duplicate_of: str
    reason: Literal["provider_id", "canonical_url", "same_content"]


class DeduplicationResult(TypedDict):
    jobs: list[LiveJobPosting]
    duplicates: list[Duplicate]


def canonical_url(url: str) -> str:
    """Discard only known tracking parameters, retaining identity parameters."""
    parsed = urlsplit(url)
    query = sorted(
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_KEYS
    )
    return urlunsplit((
        parsed.scheme.casefold(), parsed.netloc.casefold().removeprefix("www."),
        parsed.path.rstrip("/") or "/", urlencode(query), "",
    ))


def _normalized(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def deduplicate_jobs(jobs: list[LiveJobPosting]) -> DeduplicationResult:
    """Retain first-seen jobs and explain each high-confidence duplicate."""
    kept: list[LiveJobPosting] = []
    duplicates: list[Duplicate] = []
    by_provider_id: dict[tuple[str, str], str] = {}
    by_url: dict[tuple[str, str, str], str] = {}
    by_content: dict[tuple[str, str, str, str], str] = {}
    for job in jobs:
        provider_id = (job["provider"], job["provider_job_id"])
        url = canonical_url(job["url"])
        url_identity = (url, _normalized(job["title"]), _normalized(job["company"]))
        content = (
            _normalized(job["title"]), _normalized(job["company"]),
            _normalized(job["location"]), _normalized(job["description"]),
        )
        duplicate_of: str | None = None
        reason: Literal["provider_id", "canonical_url", "same_content"] | None = None
        if all(provider_id) and provider_id in by_provider_id:
            duplicate_of, reason = by_provider_id[provider_id], "provider_id"
        elif url_identity in by_url:
            duplicate_of, reason = by_url[url_identity], "canonical_url"
        elif all(content) and not urlsplit(url).query and content in by_content:
            duplicate_of, reason = by_content[content], "same_content"
        if duplicate_of is not None and reason is not None:
            duplicates.append({"id": job["id"], "duplicate_of": duplicate_of,
                               "reason": reason})
            continue
        kept.append(job)
        if all(provider_id):
            by_provider_id[provider_id] = job["id"]
        by_url[url_identity] = job["id"]
        if all(content) and not urlsplit(url).query:
            by_content[content] = job["id"]
    return {"jobs": kept, "duplicates": duplicates}
