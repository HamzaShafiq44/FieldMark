"""Voice-note transcription using Gemini 3.5 Transcribe."""

import logging
import tempfile
from pathlib import Path

from google import genai
from google.genai import types

from app.ai.errors import ReportAIError
from app.core.config import settings

LOGGER = logging.getLogger(__name__)

MIME_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".mpga": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/m4a",
    ".mp4": "audio/mp4",
    ".aac": "audio/x-aac",
    ".webm": "audio/webm",
}
DEFAULT_MIME_TYPE = "audio/mpeg"


def _response_text(response: object) -> str:
    """Read the transcript from the interaction's text fields."""
    output = getattr(response, "output_text", None)
    if isinstance(output, str) and output.strip():
        return output.strip()
    steps = getattr(response, "steps", None)
    if not isinstance(steps, list):
        return ""
    for step in steps:
        content_items = getattr(step, "content", None)
        if not isinstance(content_items, list):
            continue
        for content in content_items:
            text = getattr(content, "text", None)
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def transcribe_voice_note(data: bytes, filename: str) -> str:
    """Transcribe voice audio with Gemini 3.5 Transcribe."""
    key = settings.gemini_api_key.get_secret_value()
    if not key:
        raise ReportAIError("GEMINI_API_KEY is not configured.")
    mime_type = MIME_TYPES.get(Path(filename).suffix.lower(), DEFAULT_MIME_TYPE)
    client = genai.Client(api_key=key, http_options={"api_version": "v1beta"})
    uploaded = None
    try:
        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix) as temp:
            temp.write(data)
            temp.flush()
            uploaded = client.files.upload(
                file=temp.name, config=types.UploadFileConfig(mime_type=mime_type)
            )
        response = client.interactions.create(
            model=settings.gemini_transcription_model,
            input=[{"type": "audio", "uri": uploaded.uri, "mime_type": mime_type}],
            # Verbatim keeps the farmer's exact words, which the report treats as evidence.
            generation_config={"transcription_config": {"mode": "verbatim"}},
        )
        transcript = _response_text(response)
        if transcript:
            return transcript
        LOGGER.warning(
            "Gemini transcription returned no text (status=%s).",
            getattr(response, "status", None),
        )
    except genai.errors.APIError as exc:
        LOGGER.error("Gemini transcription failed (%s).", type(exc).__name__)
        raise ReportAIError(
            "Speech transcription is unavailable. Check the Gemini configuration and try again."
        ) from exc
    finally:
        if uploaded is not None and uploaded.name:
            try:
                client.files.delete(name=uploaded.name)
            except (genai.errors.APIError, AttributeError, TypeError):
                LOGGER.warning("Could not delete uploaded transcription file.")

    raise ReportAIError(
        "No speech could be detected in the voice note. Record a longer, clear message."
    )
