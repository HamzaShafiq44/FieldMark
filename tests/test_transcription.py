"""Tests for Gemini 3.5 Transcribe voice-note handling."""

import logging
from unittest.mock import Mock, patch

import pytest
from pydantic import SecretStr

from app.ai import gemini_transcribe, transcription
from app.ai.errors import ReportAIError
from app.core.config import settings


def _mock_client(transcript: str = "The tomato looks rotten.") -> Mock:
    """Build a fake Gemini client that returns a transcript."""
    uploaded = Mock()
    uploaded.name = "files/uploaded-123"
    uploaded.uri = "https://example.com/files/uploaded-123"
    response = Mock()
    response.output_text = transcript
    client = Mock()
    client.files.upload.return_value = uploaded
    client.interactions.create.return_value = response
    return client


def test_gemini_transcription_returns_output_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Call the transcribe model with the uploaded audio and return its text."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("test-key"))
    client = _mock_client()
    with patch.object(gemini_transcribe.genai, "Client", return_value=client) as factory:
        result = gemini_transcribe.transcribe_voice_note(b"audio", "note.wav")
    factory.assert_called_once()
    assert factory.call_args.kwargs["api_key"] == "test-key"
    options = factory.call_args.kwargs["http_options"]
    assert options.api_version == "v1beta"
    assert options.timeout == settings.gemini_timeout_ms
    request = client.interactions.create.call_args.kwargs
    assert request["model"] == "gemini-3.5-transcribe"
    assert "Voice transcription using model gemini-3.5-transcribe" in caplog.text
    assert request["generation_config"] == {"transcription_config": {"mode": "verbatim"}}
    assert request["input"] == [
        {
            "type": "audio",
            "uri": "https://example.com/files/uploaded-123",
            "mime_type": "audio/wav",
        }
    ]
    assert result == "The tomato looks rotten."


def test_gemini_transcription_uses_file_mime_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Map the audio suffix to a supported MIME type."""
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("test-key"))
    client = _mock_client()
    with patch.object(gemini_transcribe.genai, "Client", return_value=client):
        gemini_transcribe.transcribe_voice_note(b"audio", "note.mp3")
    config = client.files.upload.call_args.kwargs["config"]
    assert config.mime_type == "audio/mpeg"


@pytest.mark.parametrize(
    ("filename", "expected_mime"),
    [
        ("note.m4a", "audio/m4a"),
        ("note.mp4", "audio/mp4"),
        ("note.aac", "audio/x-aac"),
    ],
)
def test_gemini_transcription_maps_m4a_and_container_formats(
    monkeypatch: pytest.MonkeyPatch, filename: str, expected_mime: str
) -> None:
    """Send M4A and other container formats with a Gemini-supported MIME type."""
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("test-key"))
    client = _mock_client()
    with patch.object(gemini_transcribe.genai, "Client", return_value=client):
        gemini_transcribe.transcribe_voice_note(b"audio", filename)
    config = client.files.upload.call_args.kwargs["config"]
    assert config.mime_type == expected_mime


def test_gemini_transcription_deletes_uploaded_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clean up the temporary uploaded file after transcription."""
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("test-key"))
    client = _mock_client()
    with patch.object(gemini_transcribe.genai, "Client", return_value=client):
        gemini_transcribe.transcribe_voice_note(b"audio", "note.webm")
    client.files.delete.assert_called_once_with(name="files/uploaded-123")


def test_gemini_transcription_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail clearly when Gemini credentials are missing."""
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(""))
    with pytest.raises(ReportAIError, match="GEMINI_API_KEY is not configured"):
        gemini_transcribe.transcribe_voice_note(b"audio", "note.wav")


def test_gemini_transcription_rejects_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail clearly when the transcriber returns no text; there is no fallback provider."""
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("test-key"))
    client = _mock_client("   ")
    with (
        patch.object(gemini_transcribe.genai, "Client", return_value=client),
        pytest.raises(ReportAIError, match="No speech could be detected"),
    ):
        gemini_transcribe.transcribe_voice_note(b"audio", "note.wav")


def test_transcription_entry_point_uses_gemini() -> None:
    """Expose Gemini as the single transcription entry point for inspections."""
    assert transcription.transcribe_voice_note is gemini_transcribe.transcribe_voice_note


def test_voice_inspection_uses_dispatcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """Voice-only inspection transcribes through the configured provider."""
    from pathlib import Path

    from app.services.inspection_service import inspect_from_voice

    monkeypatch.setattr(settings, "inspection_db_path", Path(str(tmp_path)) / "inspections.sqlite3")
    with patch.object(
        transcription, "transcribe_voice_note", return_value="A bruised apple"
    ) as transcribe:
        result = inspect_from_voice(b"audio", "note.wav")
    transcribe.assert_called_once_with(b"audio", "note.wav")
    assert result.voice_note_transcript == "A bruised apple"
    assert result.verdict == "needs_review"


def test_voice_inspection_grades_spoken_product(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """A described rotten apple receives a normal grade, verdict, and record."""
    from pathlib import Path

    from app.services import inspection_service

    monkeypatch.setattr(settings, "inspection_db_path", Path(str(tmp_path)) / "inspections.sqlite3")
    transcript = "My apple is rotten and has cracks."
    with patch.object(transcription, "transcribe_voice_note", return_value=transcript):
        result = inspection_service.inspect_from_voice(b"audio", "note.m4a")
    assert result.produce_type == "apple"
    assert result.item_is_fruit is True
    assert result.grade == "Reject"
    assert result.verdict == "defect"
    assert result.bbox is None
    assert result.voice_note_transcript == transcript
    assert "Spoken description:" in " ".join(result.verdict_notes)


def test_voice_inspection_generates_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """Voice inspections generate their PDF in the background after the response."""
    from pathlib import Path

    from app.services import inspection_service

    monkeypatch.setattr(settings, "inspection_db_path", Path(str(tmp_path)) / "inspections.sqlite3")
    report_ids: list[str] = []
    monkeypatch.setattr(
        inspection_service,
        "generate_automatic_report",
        lambda inspection_id: report_ids.append(inspection_id) or True,
    )
    with patch.object(transcription, "transcribe_voice_note", return_value="My apple is rotten."):
        result = inspection_service.inspect_from_voice(b"audio", "note.m4a")
    assert result.report_available is False
    assert report_ids == []

    inspection_service.finalize_inspection(result.inspection_id)
    assert report_ids == [result.inspection_id]
