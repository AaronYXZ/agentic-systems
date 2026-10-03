"""V1 job record and project-local data paths."""

from pathlib import Path
from typing import TypedDict

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
LOCAL_JOBS_PATH = PROJECT_ROOT / "data" / "jobs.json"
JOBS_PATH = (
    LOCAL_JOBS_PATH
    if LOCAL_JOBS_PATH.is_file()
    else PACKAGE_ROOT / "data" / "jobs.json"
)
RESUME_PATH = PROJECT_ROOT / "data" / "resume.md"
RESULTS_PATH = PROJECT_ROOT / "data" / "results.md"


class JobPosting(TypedDict):
    """Fields required in every fictional job record."""

    id: str
    title: str
    company: str
    location: str
    description: str
    skills: list[str]
    url: str


class LiveJobPosting(TypedDict):
    """Normalized provider record. Missing optional data is explicit."""

    id: str
    provider: str
    provider_job_id: str
    url: str
    title: str
    company: str
    location: str | None
    description: str
    posted_at: str | None
    retrieved_at: str
    employment_type: str | None
    is_remote: bool | None
    country: str | None
