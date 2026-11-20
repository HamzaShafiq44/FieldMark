"""Voice-note transcription provider selection."""

from app.ai.gemini_transcribe import transcribe_voice_note as transcribe_with_gemini
from app.ai.groq_report import transcribe_voice_note as transcribe_with_groq
from app.core.config import settings


def transcribe_voice_note(data: bytes, filename: str) -> str:
    """Transcribe a voice note using the configured provider."""
    if settings.transcription_provider == "gemini":
        return transcribe_with_gemini(data, filename)
    return transcribe_with_groq(data, filename)
