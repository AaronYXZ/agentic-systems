"""Search the versioned mock job catalog without a model or network call."""

import json
import re
from pathlib import Path
from typing import cast

from openai_agent_sdk.contracts import JOBS_PATH, JobPosting

_SEARCH_FIELDS = ("title", "location", "description")
_REQUEST_WORDS = frozenset(
    {
        "a", "an", "and", "find", "for", "in", "job", "jobs", "of",
        "or", "role", "roles", "the", "to", "with",
    }
)
_CATALOG_ERROR = "Error: Mock job catalog is unavailable."


def _terms(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold())) - _REQUEST_WORDS


def _load_catalog(path: Path) -> list[JobPosting] | None:
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    if not isinstance(records, list):
        return None

    required_text = ("id", "title", "company", "location", "description", "url")
    seen_ids: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            return None
        if any(
            not isinstance(record.get(field), str) or not record[field]
            for field in required_text
        ):
            return None
        if not isinstance(record.get("skills"), list) or any(
            not isinstance(skill, str) for skill in record["skills"]
        ):
            return None
        if record["id"] in seen_ids:
            return None
        seen_ids.add(record["id"])

    return cast(list[JobPosting], records)


def search_jobs(query: str, *, catalog_path: Path = JOBS_PATH) -> str:
    """Return complete mock jobs matching any meaningful query term.

    Matching is case-insensitive across title, location, description, and
    skills. Results retain catalog order. Blank queries and unavailable or
    invalid catalogs return concise error text.
    """
    if not query.strip():
        return "Error: Search query cannot be empty."

    jobs = _load_catalog(catalog_path)
    if jobs is None:
        return _CATALOG_ERROR

    query_terms = _terms(query)
    matches = []
    for job in jobs:
        searchable = " ".join(
            [*(job[field] for field in _SEARCH_FIELDS), *job["skills"]]
        )
        if query_terms & _terms(searchable):
            matches.append(job)

    result: dict[str, object] = {"source": "mock", "jobs": matches}
    if not matches:
        result["message"] = "No mock jobs matched the query."
    return json.dumps(result, ensure_ascii=False)
