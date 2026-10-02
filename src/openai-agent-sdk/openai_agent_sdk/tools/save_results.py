"""Append validated recommendations without duplicating saved job IDs."""

import re
from datetime import datetime, timezone
from pathlib import Path

from openai_agent_sdk.catalog import load_catalog
from openai_agent_sdk.contracts import JOBS_PATH, RESULTS_PATH

_LINE = re.compile(r"(job-[0-9]+):[ \t]*(\S.*)")
_SAVED_ID = re.compile(r"^## (job-[0-9]+) \| ", re.MULTILINE)
_INVALID_CONTENT = "Error: Provide job IDs and recommendation text to save."
_WRITE_ERROR = "Error: Results could not be saved."


def save_results(
    content: str,
    *,
    results_path: Path = RESULTS_PATH,
    catalog_path: Path = JOBS_PATH,
) -> str:
    """Save one ``job-ID: recommendation`` line per catalog job.

    All lines and job IDs are validated before the results file is changed.
    Existing IDs are skipped. This function performs the write when called;
    the agent must enforce the user's explicit save intent before calling it.
    """
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return _INVALID_CONTENT

    recommendations: dict[str, str] = {}
    for line in lines:
        match = _LINE.fullmatch(line)
        if match is None:
            return _INVALID_CONTENT
        job_id, recommendation = match.groups()
        if job_id in recommendations:
            return f"Error: Duplicate job ID in request: {job_id}."
        recommendations[job_id] = recommendation.strip()

    catalog = load_catalog(catalog_path)
    if catalog is None:
        return "Error: Mock job catalog is unavailable."
    known_ids = {job["id"] for job in catalog}
    unknown_ids = [job_id for job_id in recommendations if job_id not in known_ids]
    if unknown_ids:
        return f"Error: Unknown job ID: {unknown_ids[0]}."

    try:
        existing = results_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    except (OSError, UnicodeError):
        return _WRITE_ERROR

    saved_ids = set(_SAVED_ID.findall(existing))
    new_ids = [job_id for job_id in recommendations if job_id not in saved_ids]
    skipped_ids = [job_id for job_id in recommendations if job_id in saved_ids]

    if new_ids:
        # datetime.UTC is unavailable on the package's minimum Python 3.10.
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017
        timestamp = timestamp.replace("+00:00", "Z")
        sections = [
            f"## {job_id} | {timestamp}\n\n{recommendations[job_id]}\n"
            for job_id in new_ids
        ]
        separator = "" if not existing else "\n" if existing.endswith("\n") else "\n\n"
        try:
            with results_path.open("a", encoding="utf-8") as output:
                output.write(separator + "\n".join(sections))
        except OSError:
            return _WRITE_ERROR

    saved = ", ".join(new_ids) or "none"
    skipped = ", ".join(skipped_ids) or "none"
    return f"Saved: {saved}. Skipped duplicates: {skipped}."
