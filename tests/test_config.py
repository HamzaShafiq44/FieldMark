"""Tests for default application configuration."""

from app.core.config import Settings


def test_groq_vision_model_defaults_to_qwen() -> None:
    """Use Qwen for image detection when no model override is configured."""
    config = Settings.model_validate({})

    assert config.groq_vision_model == "qwen/qwen3.8-27b"
