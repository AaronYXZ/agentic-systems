"""User-owned hard filters and deterministic job selection."""

import re
from dataclasses import dataclass
from typing import TypedDict

from .contracts import LiveJobPosting
from .matching import requirement_excerpt, skill_mention_excerpt

_FILTER_MARKER = re.compile(r"\bfilters:\s*", re.IGNORECASE)
_US_LOCATION = re.compile(r"\bin (?:the )?(?:US|USA|United States)\b", re.IGNORECASE)
_REMOTE = re.compile(r"\bremote\b|\bwork from home\b", re.IGNORECASE)
_NOT_REMOTE = re.compile(r"\b(?:not|non[- ]|no)\s*remote\b", re.IGNORECASE)
_US_NAMES = frozenset({"us", "usa", "united states"})


class CriteriaError(ValueError):
    """A safe explanation of an invalid explicit filter request."""


@dataclass(frozen=True)
class SearchCriteria:
    location: str | None = None
    remote_only: bool = False
    role: str | None = None
    required_skills: tuple[str, ...] = ()
    preferred_skills: tuple[str, ...] = ()


class Exclusion(TypedDict):
    id: str
    reasons: list[str]


class FilterResult(TypedDict):
    jobs: list[LiveJobPosting]
    excluded: list[Exclusion]


def parse_criteria(message: str) -> SearchCriteria:
    """Read exact filters from user text, never from model tool arguments."""
    marker = _FILTER_MARKER.search(message)
    plain_text = message[:marker.start()] if marker else message
    inferred_remote = bool(
        _REMOTE.search(plain_text) and not _NOT_REMOTE.search(plain_text)
    )
    inferred_us = bool(_US_LOCATION.search(plain_text))
    if not marker:
        return SearchCriteria(
            location="US" if inferred_us else None,
            remote_only=inferred_remote,
        )

    values: dict[str, str] = {}
    for entry in message[marker.end():].split(";"):
        if "=" not in entry:
            raise CriteriaError(
                "Filters must use key=value entries separated by semicolons."
            )
        key, value = (part.strip() for part in entry.split("=", 1))
        key = key.casefold()
        if key not in {"location", "remote", "role", "skills", "prefer_skills"}:
            raise CriteriaError(f"Unsupported filter: {key}.")
        if key in values or not value:
            raise CriteriaError(f"Filter {key} must appear once with a value.")
        values[key] = value
    remote_text = values.get("remote", "true" if inferred_remote else "false")
    if remote_text.casefold() not in {"true", "false"}:
        raise CriteriaError("Filter remote must be true or false.")
    remote_only = remote_text.casefold() == "true"
    if inferred_remote and not remote_only:
        raise CriteriaError("The remote filter conflicts with the user request.")
    location = values.get("location", "US" if inferred_us else None)
    if inferred_us and location and location.casefold() not in _US_NAMES:
        raise CriteriaError("The location filter conflicts with the user request.")

    def skills(key: str) -> tuple[str, ...]:
        return tuple(part.strip() for part in values.get(key, "").split(",")
                     if part.strip())

    return SearchCriteria(
        location=location,
        remote_only=remote_only,
        role=values.get("role"),
        required_skills=skills("skills"),
        preferred_skills=skills("prefer_skills"),
    )


def _location_matches(job: LiveJobPosting, location: str) -> bool:
    if location.casefold() in _US_NAMES:
        return bool(job["country"] and job["country"].casefold() in _US_NAMES)
    actual = job["location"]
    return bool(actual and re.search(
        rf"(?<!\w){re.escape(location)}(?!\w)", actual, re.IGNORECASE
    ))


def filter_jobs(jobs: list[LiveJobPosting], criteria: SearchCriteria) -> FilterResult:
    """Exclude missing-field failures and rank remaining preferences stably."""
    included: list[LiveJobPosting] = []
    excluded: list[Exclusion] = []
    for job in jobs:
        reasons: list[str] = []
        if criteria.location and not _location_matches(job, criteria.location):
            reasons.append(f"location is not confirmed as {criteria.location}")
        if criteria.remote_only and job["is_remote"] is not True:
            reasons.append("remote status is not confirmed true")
        if criteria.role and not re.search(
            rf"(?<!\w){re.escape(criteria.role)}(?!\w)", job["title"], re.IGNORECASE
        ):
            reasons.append(f"title does not confirm role {criteria.role}")
        for skill in criteria.required_skills:
            if requirement_excerpt(job["description"], skill) is None:
                reasons.append(f"description does not confirm required skill {skill}")
        if reasons:
            excluded.append({"id": job["id"], "reasons": reasons})
        else:
            included.append(job)
    if criteria.preferred_skills:
        included.sort(key=lambda job: -sum(
            skill_mention_excerpt(job["description"], skill) is not None
            for skill in criteria.preferred_skills
        ))
    return {"jobs": included, "excluded": excluded}
