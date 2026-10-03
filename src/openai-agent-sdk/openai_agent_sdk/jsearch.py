"""Small JSearch HTTP adapter and strict provider-boundary normalization."""

import json
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .contracts import LiveJobPosting

HOST = "jsearch.p.rapidapi.com"
SEARCH_URL = f"https://{HOST}/search-v2"
TIMEOUT_SECONDS = 8
MAX_RESULTS = 10


class JSearchError(Exception):
    """A safe, user-facing search failure without credentials or raw payloads."""


def _required(record: dict, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing or invalid {field}.")
    return value.strip()


def normalize_job(record: object, retrieved_at: str) -> LiveJobPosting:
    """Convert one JSearch result into the stable internal job schema."""
    if not isinstance(record, dict):
        raise ValueError("Job record must be an object.")
    job_id = _required(record, "job_id")
    url = _required(record, "job_apply_link")
    if urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
        raise ValueError("Job URL must be HTTP or HTTPS.")

    optional = record.get("job_posted_at_datetime_utc")
    if optional is not None and not isinstance(optional, str):
        raise ValueError("Posting date must be text or null.")
    location = record.get("job_location")
    if location is not None and not isinstance(location, str):
        raise ValueError("Location must be text or null.")
    employment_type = record.get("job_employment_type")
    if employment_type is not None and not isinstance(employment_type, str):
        raise ValueError("Employment type must be text or null.")
    is_remote = record.get("job_is_remote")
    if is_remote is not None and not isinstance(is_remote, bool):
        raise ValueError("Remote status must be boolean or null.")
    country = record.get("job_country")
    if country is not None and not isinstance(country, str):
        raise ValueError("Country must be text or null.")
    return LiveJobPosting(
        id=f"jsearch:{job_id}",
        provider="jsearch",
        provider_job_id=job_id,
        url=url,
        title=_required(record, "job_title"),
        company=_required(record, "employer_name"),
        location=location.strip() or None if location is not None else None,
        description=_required(record, "job_description"),
        posted_at=optional.strip() or None if optional is not None else None,
        retrieved_at=retrieved_at,
        employment_type=(
            employment_type.strip() or None if employment_type is not None else None
        ),
        is_remote=is_remote,
        country=country.strip() or None if country is not None else None,
    )


def search_jsearch(
    query: str,
    *,
    api_key: str,
    opener: Callable = urlopen,
    retrieved_at: str | None = None,
    country: str | None = None,
    remote_only: bool = False,
) -> dict[str, object]:
    """Fetch one page, normalize up to ten jobs, and fail closed on bad data."""
    if not api_key.strip():
        raise JSearchError("JSearch API key is missing. Set JSEARCH_API_KEY.")
    if not query.strip():
        raise JSearchError("Search query cannot be empty.")
    if len(query) > 200:
        raise JSearchError("Search query must be 200 characters or fewer.")

    parameters: dict[str, str | int] = {"query": query, "page": 1, "num_pages": 1}
    if country:
        parameters["country"] = country
    if remote_only:
        parameters["work_from_home"] = "true"
    url = f"{SEARCH_URL}?{urlencode(parameters)}"
    request = Request(
        url,
        headers={"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": HOST},
    )
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise JSearchError(
                "JSearch authentication failed. Check your API key."
            ) from exc
        if exc.code == 429:
            raise JSearchError("JSearch rate limit reached. Try again later.") from exc
        raise JSearchError("JSearch is unavailable. Try again later.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise JSearchError(
            "JSearch is unavailable. Check connectivity and retry."
        ) from exc
    except (ValueError, UnicodeError) as exc:
        raise JSearchError("JSearch returned an invalid response.") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise JSearchError("JSearch returned an invalid response.")
    raw_jobs = payload["data"].get("jobs")
    if not isinstance(raw_jobs, list):
        raise JSearchError("JSearch returned an invalid response.")
    timestamp = retrieved_at or datetime.now(timezone.utc).isoformat()  # noqa: UP017
    try:
        jobs = [
            normalize_job(item, timestamp)
            for item in raw_jobs[:MAX_RESULTS]
        ]
    except ValueError as exc:
        raise JSearchError("JSearch returned a malformed job record.") from exc
    return {"source": "jsearch", "retrieved_at": timestamp, "jobs": jobs}
