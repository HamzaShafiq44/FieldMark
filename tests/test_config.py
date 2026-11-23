"""Tests for default application configuration."""

from app.core.config import (
    DEFAULT_GEMINI_REPORT_MODEL,
    DEFAULT_GEMINI_VISION_MODEL,
    Settings,
)


def test_gemini_model_defaults() -> None:
    """Use the configured Gemini defaults for vision and reporting."""
    config = Settings.model_validate({})

    assert config.gemini_vision_model == DEFAULT_GEMINI_VISION_MODEL
    assert config.gemini_report_model == DEFAULT_GEMINI_REPORT_MODEL
