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
