"""Voice-note transcription through the single configured provider: Gemini 3.5 Transcribe."""

from app.ai.gemini_transcribe import transcribe_voice_note

__all__ = ["transcribe_voice_note"]
