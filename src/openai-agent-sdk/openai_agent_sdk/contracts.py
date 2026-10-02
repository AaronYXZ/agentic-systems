"""V1 job data and contracts for the remaining file tools."""

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


def read_resume() -> str:
    """Return the complete UTF-8 text from ``data/resume.md``.

    A missing or empty file must return ``Error: Resume is missing or empty.``
    Reading must not expose a traceback or unrelated filesystem details.
    """
    raise NotImplementedError("read_resume is planned for Step 4")


def save_results(content: str) -> str:
    """Append requested recommendations to ``data/results.md``.

    Content must be nonblank and identify catalog jobs by stable ID. Return
    which IDs were saved and which were skipped as duplicates. Invalid content
    must return ``Error: Provide job IDs and recommendation text to save.`` A
    write failure must return ``Error: Results could not be saved.``
    """
    raise NotImplementedError("save_results is planned for Step 5")
