"""Check duplicate-safe writes using only temporary result files."""

import re

from openai_agent_sdk.tools import save_results


def test_first_save_appends_id_recommendation_and_utc_timestamp(tmp_path):
    results = tmp_path / "results.md"

    response = save_results("job-001: Strong ranking fit.", results_path=results)
    content = results.read_text(encoding="utf-8")

    assert response == "Saved: job-001. Skipped duplicates: none."
    assert re.search(r"^## job-001 \| \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", content, re.M)
    assert "Strong ranking fit." in content


def test_repeat_save_skips_existing_id_without_changing_file(tmp_path):
    results = tmp_path / "results.md"
    save_results("job-001: Strong ranking fit.", results_path=results)
    original = results.read_text(encoding="utf-8")

    response = save_results("job-001: Revised note.", results_path=results)

    assert response == "Saved: none. Skipped duplicates: job-001."
    assert results.read_text(encoding="utf-8") == original


def test_mixed_batch_saves_new_id_and_reports_duplicate(tmp_path):
    results = tmp_path / "results.md"
    save_results("job-001: Ranking fit.", results_path=results)

    response = save_results(
        "job-001: Ranking fit.\njob-002: LLM fit.", results_path=results
    )
    content = results.read_text(encoding="utf-8")

    assert response == "Saved: job-002. Skipped duplicates: job-001."
    assert content.count("## job-001 |") == 1
    assert content.count("## job-002 |") == 1
    assert "LLM fit." in content


def test_invalid_input_does_not_create_or_change_file(tmp_path):
    results = tmp_path / "results.md"

    assert save_results(" \n", results_path=results).startswith("Error:")
    assert save_results("job-001", results_path=results).startswith("Error:")
    assert save_results("job-999: Unknown.", results_path=results) == (
        "Error: Unknown job ID: job-999."
    )
    assert save_results(
        "job-001: One.\njob-001: Two.", results_path=results
    ) == "Error: Duplicate job ID in request: job-001."
    assert not results.exists()


def test_invalid_batch_does_not_partially_append(tmp_path):
    results = tmp_path / "results.md"
    save_results("job-001: Existing.", results_path=results)
    original = results.read_text(encoding="utf-8")

    response = save_results(
        "job-002: Valid.\njob-999: Unknown.", results_path=results
    )

    assert response == "Error: Unknown job ID: job-999."
    assert results.read_text(encoding="utf-8") == original


def test_write_failure_returns_clear_error(tmp_path):
    results_directory = tmp_path / "results.md"
    results_directory.mkdir()

    assert save_results(
        "job-001: Ranking fit.", results_path=results_directory
    ) == "Error: Results could not be saved."


def test_unavailable_catalog_prevents_write(tmp_path):
    results = tmp_path / "results.md"

    assert save_results(
        "job-001: Ranking fit.",
        results_path=results,
        catalog_path=tmp_path / "missing.json",
    ) == "Error: Mock job catalog is unavailable."
    assert not results.exists()
