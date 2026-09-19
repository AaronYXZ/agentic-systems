import pytest

from agentic_systems.first_agent import build_model


def test_model_uses_responses_api_and_runtime_settings(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_MODEL", "openai:gpt-5.6-terra")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "0")

    model = build_model()

    assert model.model_name == "gpt-5.6-terra"
    assert model.use_responses_api is True
    assert model.request_timeout == 12.5
    assert model.max_retries == 0


def test_openai_model_is_supported_as_a_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_MODEL", "")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-mini")

    model = build_model()

    assert model.model_name == "gpt-5-mini"


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("OPENAI_TIMEOUT_SECONDS", "0", "must be greater than zero"),
        ("OPENAI_MAX_RETRIES", "-1", "must be zero or greater"),
    ],
)
def test_invalid_runtime_setting_has_a_clear_error(
    monkeypatch, name, value, message
):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        build_model()
