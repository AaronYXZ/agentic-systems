"""Read the local candidate resume without involving a model."""

from pathlib import Path

from openai_agent_sdk.contracts import RESUME_PATH


def read_resume(*, resume_path: Path = RESUME_PATH) -> str:
    """Return exact UTF-8 resume text, or a concise actionable error."""
    try:
        content = resume_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "Error: Resume is missing or empty. Add data/resume.md."
    except (OSError, UnicodeError):
        return "Error: Resume could not be read. Check data/resume.md."

    if not content.strip():
        return "Error: Resume is missing or empty. Add content to data/resume.md."
    return content
