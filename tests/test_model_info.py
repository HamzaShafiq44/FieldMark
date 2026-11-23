"""Tests for the model-description log helper."""

import pytest
from pydantic import SecretStr

from app.ai.model_info import describe_models
from app.core.config import settings


def test_describe_models_for_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    """The log summary lists all configured Gemini models and no credentials."""
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("secret-key-value"))
    description = describe_models()
    assert "provider=gemini" in description
    assert f"vision={settings.gemini_vision_model}" in description
    assert f"localization={settings.gemini_localization_model}" in description
    assert f"report={settings.gemini_report_model}" in description
    assert "secret-key-value" not in description
