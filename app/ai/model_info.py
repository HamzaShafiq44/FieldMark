"""Describe the configured AI models for logs (never includes credentials)."""

from app.core.config import settings


def describe_models() -> str:
    """Summarize which models the inspection pipeline will use."""
    if settings.vision_provider == "gemini":
        vision = settings.gemini_vision_model
        localization = settings.gemini_localization_model
    else:
        vision = settings.groq_vision_model
        localization = settings.groq_vision_model

    return (
        f"provider={settings.vision_provider}, vision={vision}, "
        f"localization={localization}, report={settings.groq_report_model}, "
        f"transcription={settings.gemini_transcription_model}"
    )
