"""Deterministic, excerpt-backed checks for explicit job requirements."""

import re
from typing import Literal, TypedDict

from .contracts import LiveJobPosting

SKILLS = (
    "Python", "SQL", "ranking", "machine learning", "LLM", "PyTorch",
    "TensorFlow", "AWS", "Kubernetes", "Java", "Rust",
)
_REQUIREMENT_CUE = re.compile(
    r"\b(?:requirements?|required|must have|experience with|proficien\w* in|"
    r"knowledge of|skills?)\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"\b(?:not required|no experience|without experience|do not need|don't need)\b",
    re.IGNORECASE,
)


class MatchedEvidence(TypedDict):
    skill: str
    job_excerpt: str
    resume_excerpt: str


class UnverifiedEvidence(TypedDict):
    skill: str
    job_excerpt: str


class FitAssessment(TypedDict):
    job_id: str
    status: Literal["strong", "partial", "unverified"]
    matched: list[MatchedEvidence]
    unverified: list[UnverifiedEvidence]
    note: str


def _clauses(text: str) -> list[str]:
    return [
        part.strip() for part in re.split(r"\n+|(?<=[.!?])\s+", text)
        if part.strip()
    ]


def _mentions(text: str, skill: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(skill)}(?!\w)", text, re.IGNORECASE))


def requirement_excerpt(description: str, skill: str) -> str | None:
    """Return an explicit positive requirement mentioning a skill, if any."""
    for clause in _clauses(description):
        if _mentions(clause, skill) and _REQUIREMENT_CUE.search(clause):
            if not _NEGATION.search(clause):
                return clause
    return None


def skill_mention_excerpt(description: str, skill: str) -> str | None:
    """Find a positive mention for preference ordering, not hard matching."""
    for clause in _clauses(description):
        if _mentions(clause, skill) and not _NEGATION.search(clause):
            return clause
    return None


def _resume_excerpt(resume: str, skill: str) -> str | None:
    for clause in _clauses(resume):
        if _mentions(clause, skill) and not _NEGATION.search(clause):
            return clause
    return None


def assess_fit(job: LiveJobPosting, resume: str) -> FitAssessment:
    """Match only explicit skill requirements to literal resume evidence."""
    matched: list[MatchedEvidence] = []
    unverified: list[UnverifiedEvidence] = []
    for skill in SKILLS:
        job_excerpt = requirement_excerpt(job["description"], skill)
        if job_excerpt is None:
            continue
        resume_excerpt = _resume_excerpt(resume, skill)
        if resume_excerpt is None:
            unverified.append({"skill": skill, "job_excerpt": job_excerpt})
        else:
            matched.append({
                "skill": skill,
                "job_excerpt": job_excerpt,
                "resume_excerpt": resume_excerpt,
            })
    if not matched:
        status = "unverified"
    elif unverified:
        status = "partial"
    else:
        status = "strong"
    return {
        "job_id": job["id"],
        "status": status,
        "matched": matched,
        "unverified": unverified,
        "note": (
            "Only explicit listed skills were checked. Missing resume evidence "
            "does not prove the candidate lacks a skill. This is not a complete "
            "fit assessment."
        ),
    }
