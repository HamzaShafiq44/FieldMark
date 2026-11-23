"""Describe the configured AI models for logs (never includes credentials)."""

from app.core.config import settings


def describe_models() -> str:
    """Summarize which Gemini models the inspection pipeline will use."""
    return (
        "provider=gemini, "
        f"vision={settings.gemini_vision_model}, "
        f"localization={settings.gemini_localization_model}, "
        f"report={settings.gemini_report_model}, "
        f"transcription={settings.gemini_transcription_model}"
    )
