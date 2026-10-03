"""Search the versioned mock job catalog without a model or network call."""

import json
import re
from collections.abc import Callable
from pathlib import Path

from openai_agent_sdk.catalog import load_catalog
from openai_agent_sdk.contracts import JOBS_PATH
from openai_agent_sdk.jsearch import JSearchError, search_jsearch

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


def search_jobs(
    query: str,
    *,
    catalog_path: Path = JOBS_PATH,
    provider: str = "mock",
    api_key: str = "",
    live_search: Callable[..., dict[str, object]] = search_jsearch,
) -> str:
    """Return complete mock jobs matching any meaningful query term.

    Matching is case-insensitive across title, location, description, and
    skills. Results retain catalog order. Blank queries and unavailable or
    invalid catalogs return concise error text.
    """
    if not query.strip():
        return "Error: Search query cannot be empty."

    if provider == "jsearch":
        try:
            result = live_search(query, api_key=api_key)
        except JSearchError as exc:
            return f"Error: {exc}"
        return json.dumps(result, ensure_ascii=False)
    if provider != "mock":
        return "Error: Unsupported job search provider."

    jobs = load_catalog(catalog_path)
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
