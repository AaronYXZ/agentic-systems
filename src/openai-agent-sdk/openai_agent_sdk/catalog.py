"""Load and validate the versioned mock job catalog."""

import json
from pathlib import Path
from typing import cast

from .contracts import JobPosting


def load_catalog(path: Path) -> list[JobPosting] | None:
    """Return complete, uniquely identified jobs or ``None`` for invalid data."""
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
