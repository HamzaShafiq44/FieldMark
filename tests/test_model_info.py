"""Tests for the model-description log helper."""

import pytest
from pydantic import SecretStr

from app.ai.model_info import describe_models
from app.core.config import settings


def test_describe_models_for_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini logs its separate analysis and localization models."""
    monkeypatch.setattr(settings, "vision_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("secret-key-value"))
    description = describe_models()
    assert "provider=gemini" in description
    assert f"vision={settings.gemini_vision_model}" in description
    assert f"localization={settings.gemini_localization_model}" in description
    assert "secret-key-value" not in description


def test_describe_models_for_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    """Groq uses one vision model for both analysis and localization."""
    monkeypatch.setattr(settings, "vision_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("secret-key-value"))
    description = describe_models()
    assert "provider=groq" in description
    assert f"vision={settings.groq_vision_model}" in description
    assert f"localization={settings.groq_vision_model}" in description
    assert f"report={settings.groq_report_model}" in description
    assert "secret-key-value" not in description
