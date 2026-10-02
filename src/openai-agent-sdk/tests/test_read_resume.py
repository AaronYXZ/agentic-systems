"""Read only temporary fictional resumes during tests."""

from openai_agent_sdk.tools import read_resume


def test_read_resume_returns_exact_utf8_content(tmp_path):
    resume = tmp_path / "resume.md"
    content = "# Candidate\n\nPython and ranking. 中文 résumé.\n"
    resume.write_text(content, encoding="utf-8")

    assert read_resume(resume_path=resume) == content


def test_missing_resume_has_actionable_error(tmp_path):
    result = read_resume(resume_path=tmp_path / "missing.md")

    assert result.startswith("Error: Resume is missing or empty.")
    assert "data/resume.md" in result
    assert str(tmp_path) not in result


def test_empty_resume_has_actionable_error(tmp_path):
    resume = tmp_path / "resume.md"
    resume.write_text(" \n\t", encoding="utf-8")

    assert read_resume(resume_path=resume) == (
        "Error: Resume is missing or empty. Add content to data/resume.md."
    )


def test_unreadable_resume_does_not_expose_path_or_traceback(tmp_path):
    result = read_resume(resume_path=tmp_path)

    assert result == "Error: Resume could not be read. Check data/resume.md."
    assert str(tmp_path) not in result
