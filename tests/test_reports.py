"""Focused tests for report evidence, validation, and PDF persistence."""

import json
import re
import sqlite3
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

import groq
import pytest
from httpx import Request, Response
from PIL import Image
from pydantic import SecretStr, ValidationError

from app.ai import groq_report
from app.ai.groq_report import MODEL_UNAVAILABLE_MESSAGE, RATE_LIMITED_MESSAGE, _provider_failure
from app.ai.schemas import (
    Condition,
    DefectType,
    Finding,
    InspectionReportText,
    InspectionResult,
    ProduceType,
    ReportUserType,
    Severity,
    VoiceNoteRelation,
)
from app.core.config import settings
from app.reports.pdf_renderer import annotate_photo
from app.services import inspection_service, report_service
from app.storage.inspection_repository import InspectionRepository


@pytest.fixture(autouse=True)
def isolate_history_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep report persistence tests isolated from local application data."""
    monkeypatch.setattr(settings, "inspection_db_path", tmp_path / "inspections.sqlite3")


def _jpeg_bytes() -> bytes:
    """Create a small source photo for inspection and PDF tests."""
    buffer = BytesIO()
    Image.new("RGB", (100, 80), color="white").save(buffer, format="JPEG")
    return buffer.getvalue()


def _report_text() -> InspectionReportText:
    """Return a valid narrative for deterministic report tests."""
    return InspectionReportText(
        summary="The image suggests a possible surface defect.",
        findings=["A small bruise is reported on the visible side."],
        recommendations=["Re-inspect the affected area."],
        voice_note_relation=VoiceNoteRelation.NOT_PROVIDED,
    )


def test_report_source_and_pdf_are_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retain the original photo and make the generated PDF downloadable."""
    image_data = _jpeg_bytes()
    finding = Finding(
        produce_type=ProduceType.APPLE,
        condition=Condition.FRESH,
        confidence=0.3,
        visual_observation="A possible brown spot is visible.",
        severity=Severity.LOW,
        bbox=(0.2, 0.2, 0.8, 0.8),
    )
    result = inspection_service.inspect_produce(image_data, detect=lambda _: finding)
    repository = InspectionRepository(settings.inspection_db_path)
    source = repository.get_report_source(result.inspection_id)
    assert source is not None
    assert source.original_image == image_data
    assert source.result.inspection_id == result.inspection_id

    monkeypatch.setattr(report_service, "write_inspection_report", lambda *_: _report_text())
    pdf = report_service.generate_inspection_report(result.inspection_id, ReportUserType.FARMER)

    assert pdf.startswith(b"%PDF-")
    assert len(re.findall(rb"/Type\s*/Page\b", pdf)) == 1
    assert len(re.findall(rb"/Subtype\s*/Image\b", pdf)) == 1
    assert b"/Title (Inspection Report" in pdf
    assert repository.get_report_pdf(result.inspection_id) == pdf
    assert repository.list_history(10, 0)[0].report_available


def test_annotated_photo_is_included_for_reported_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Include the second photo only when a defect and its location are reported."""
    result = inspection_service.inspect_produce(
        _jpeg_bytes(),
        detect=lambda _: Finding(
            produce_type=ProduceType.APPLE,
            condition=Condition.FRESH,
            confidence=0.8,
            defect_types=[DefectType.BRUISE],
            severity=Severity.LOW,
            bbox=(0.2, 0.2, 0.8, 0.8),
        ),
    )
    monkeypatch.setattr(report_service, "write_inspection_report", lambda *_: _report_text())

    pdf = report_service.generate_inspection_report(result.inspection_id, ReportUserType.FARMER)

    assert len(re.findall(rb"/Subtype\s*/Image\b", pdf)) == 2


def test_voice_note_is_transcribed_and_included_in_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass the transcription into validated report writing."""
    image_data = _jpeg_bytes()
    result = inspection_service.inspect_produce(
        image_data,
        detect=lambda _: Finding(
            produce_type=ProduceType.ORANGE,
            condition=Condition.FRESH,
            confidence=0.8,
        ),
    )
    transcripts: list[str] = []
    monkeypatch.setattr(
        report_service,
        "transcribe_voice_note",
        lambda _data, _filename: "Soft spot near the stem after transport.",
    )

    def write_report(
        _: InspectionResult,
        user_type: ReportUserType,
        transcript: str,
    ) -> InspectionReportText:
        assert user_type is ReportUserType.FARMER
        transcripts.append(transcript)
        return InspectionReportText(
            summary="The voice note adds context to the visual check.",
            findings=["A soft spot was noted by the operator."],
            recommendations=["Re-inspect the stem area."],
            voice_note_relation=VoiceNoteRelation.ADDS_CONTEXT,
        )

    monkeypatch.setattr(report_service, "write_inspection_report", write_report)
    pdf = report_service.generate_inspection_report(
        result.inspection_id,
        ReportUserType.FARMER,
        b"voice bytes",
        "voice-note.wav",
    )

    assert pdf.startswith(b"%PDF-")
    assert transcripts == ["Soft spot near the stem after transport."]


def test_report_with_long_voice_note_fits_on_one_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the maximum supported transcript and narrative within one readable page."""
    result = inspection_service.inspect_produce(
        _jpeg_bytes(),
        detect=lambda _: Finding(
            produce_type=ProduceType.ORANGE,
            condition=Condition.FRESH,
            confidence=0.8,
        ),
    )
    transcript = ("Operator note about the fruit. " * 25)[
        : report_service.MAX_TRANSCRIPT_CHARACTERS
    ]
    narrative = InspectionReportText(
        summary=("The image does not show a clear defect. " * 10)[:300],
        findings=[
            ("A possible mark is visible on the fruit. " * 10)[:180],
            ("The affected area should be checked carefully. " * 10)[:180],
            ("The image alone cannot confirm the cause. " * 10)[:180],
        ],
        recommendations=[
            ("Check the affected area before sorting. " * 10)[:140],
            ("Keep this fruit separate until checked. " * 10)[:140],
            ("Re-inspect the fruit before sale. " * 10)[:140],
        ],
        voice_note_relation=VoiceNoteRelation.CONFLICTS,
        voice_note_conflict=("The note differs from what is visible in the photo. " * 10)[:240],
    )
    monkeypatch.setattr(report_service, "transcribe_voice_note", lambda *_: transcript)
    monkeypatch.setattr(report_service, "write_inspection_report", lambda *_: narrative)

    pdf = report_service.generate_inspection_report(
        result.inspection_id,
        ReportUserType.FARMER,
        b"voice bytes",
    )

    page_count = len(re.findall(rb"/Type\s*/Page\b", pdf))
    assert page_count == 1


def test_transcript_too_long_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject voice-note transcripts that exceed the character cap."""
    result = inspection_service.inspect_produce(
        _jpeg_bytes(),
        detect=lambda _: Finding(
            produce_type=ProduceType.ORANGE,
            condition=Condition.FRESH,
            confidence=0.8,
        ),
    )
    monkeypatch.setattr(
        report_service,
        "transcribe_voice_note",
        lambda *_: "x" * (report_service.MAX_TRANSCRIPT_CHARACTERS + 1),
    )
    monkeypatch.setattr(
        report_service,
        "write_inspection_report",
        Mock(
            side_effect=AssertionError("Long transcript should be rejected before report writing.")
        ),
    )

    with pytest.raises(report_service.InspectionReportInputError, match="too long"):
        report_service.generate_inspection_report(
            result.inspection_id,
            ReportUserType.FARMER,
            b"voice bytes",
        )


def test_annotated_photo_marks_only_the_reported_region() -> None:
    """Draw a visible red box on a copy of the source photo."""
    annotated = annotate_photo(_jpeg_bytes(), (0.2, 0.2, 0.8, 0.8))
    with Image.open(BytesIO(annotated)) as image:
        pixel = image.convert("RGB").getpixel((20, 40))
    assert isinstance(pixel, tuple) and len(pixel) == 3
    red, green, blue = pixel
    assert red > green * 1.5
    assert red > blue * 1.5


def test_conflicting_voice_note_requires_an_explanation() -> None:
    """Reject model output that marks a conflict without explaining it."""
    with pytest.raises(ValidationError):
        InspectionReportText(
            summary="The sources disagree.",
            findings=["The visual check differs from the voice note."],
            recommendations=["Inspect manually."],
            voice_note_relation=VoiceNoteRelation.CONFLICTS,
        )


def test_report_text_rejects_fields_outside_its_schema() -> None:
    """Reject unexpected AI output rather than silently accepting it."""
    with pytest.raises(ValidationError):
        InspectionReportText.model_validate(
            {
                "summary": "A possible mark is visible.",
                "findings": ["A mark was reported."],
                "recommendations": ["Re-inspect the fruit."],
                "voice_note_relation": "not_provided",
                "voice_note_conflict": "",
                "unsupported_claim": "invented",
            }
        )


def test_groq_report_writer_passes_context_and_validates_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use GPT OSS by default without changing the configured vision model."""
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("report-test-key"))
    monkeypatch.setattr(settings, "groq_report_model", "")
    monkeypatch.setattr(settings, "groq_vision_model", "qwen/qwen3.8-27b")
    response = Mock()
    response.choices = [
        Mock(
            message=Mock(
                content=json.dumps(
                    {
                        "summary": "The photo suggests a possible bruise.",
                        "findings": ["A bruise was reported in the image."],
                        "recommendations": ["Re-inspect the affected area."],
                        "voice_note_relation": "adds_context",
                        "voice_note_conflict": "",
                    }
                )
            )
        )
    ]
    client = Mock()
    client.chat.completions.create.return_value = response
    with patch("app.ai.groq_report.Groq", return_value=client) as groq_client:
        report = groq_report.write_inspection_report(
            inspection_service.inspect_produce(
                _jpeg_bytes(),
                detect=lambda _: Finding(
                    produce_type=ProduceType.APPLE,
                    condition=Condition.FRESH,
                    confidence=0.8,
                ),
            ),
            ReportUserType.FARMER,
            "I noticed a soft spot.",
        )

    groq_client.assert_called_once_with(api_key="report-test-key")
    request = client.chat.completions.create.call_args.kwargs
    context = json.loads(request["messages"][1]["content"])
    assert request["model"] == "openai/gpt-oss-20b"
    assert context["user_type"] == "farmer"
    assert "everyday, practical language" in context["farmer_audience_guidance"]
    assert context["voice_note_transcript"] == "I noticed a soft spot."
    assert report.voice_note_relation is VoiceNoteRelation.ADDS_CONTEXT


def test_report_request_instructs_plain_farmer_friendly_language() -> None:
    """Keep the report instructions focused on farmers and easy-to-understand wording."""
    result = inspection_service.inspect_produce(
        _jpeg_bytes(),
        detect=lambda _: Finding(
            produce_type=ProduceType.APPLE,
            condition=Condition.FRESH,
            confidence=0.8,
        ),
    )
    farmer = json.loads(groq_report._report_request(result, ReportUserType.FARMER, ""))

    assert farmer["user_type"] == "farmer"
    assert "everyday, practical language" in farmer["farmer_audience_guidance"]


def test_groq_transcribes_optional_voice_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """Send audio bytes to the configured Groq transcription model."""
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("report-test-key"))
    client = Mock()
    client.audio.transcriptions.create.return_value = Mock(text="Soft spot near the stem.")
    with patch("app.ai.groq_report.Groq", return_value=client):
        transcript = groq_report.transcribe_voice_note(b"voice bytes", "voice-note.wav")

    request = client.audio.transcriptions.create.call_args.kwargs
    assert request["file"] == ("voice-note.wav", b"voice bytes")
    assert transcript == "Soft spot near the stem."


@pytest.mark.parametrize(
    ("error_factory", "message"),
    [
        (
            lambda: groq.NotFoundError(
                "provider details stay private",
                response=Response(404, request=Request("POST", "https://api.groq.com")),
                body=None,
            ),
            MODEL_UNAVAILABLE_MESSAGE,
        ),
        (
            lambda: groq.RateLimitError(
                "provider details stay private",
                response=Response(429, request=Request("POST", "https://api.groq.com")),
                body=None,
            ),
            RATE_LIMITED_MESSAGE,
        ),
    ],
)
def test_groq_report_errors_are_actionable(
    error_factory: Callable[[], groq.APIError],
    message: str,
) -> None:
    """Translate common provider errors without exposing response details."""
    translated = _provider_failure(error_factory(), "Report writing")

    assert str(translated) == message
    assert "provider details" not in str(translated)


def test_legacy_history_migrates_without_fabricating_source_images(tmp_path: Path) -> None:
    """Keep old history readable while marking its original photo unavailable."""
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE inspections (
                inspection_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                produce_type TEXT NOT NULL,
                verdict TEXT NOT NULL,
                grade TEXT NOT NULL,
                thumbnail BLOB NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO inspections VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-id", "2026-01-01T00:00:00+00:00", "apple", "pass", "A", b"thumbnail"),
        )
    repository = InspectionRepository(database_path)

    history_item = repository.list_history(10, 0)[0]
    assert history_item.report_available is False
    assert history_item.report_source_available is False
    assert repository.get_report_source("legacy-id") is None
    assert repository.get_thumbnail("legacy-id") == b"thumbnail"
    assert (tmp_path / "images" / "legacy-id_thumbnail.jpg").is_file()


def test_legacy_image_blobs_are_moved_to_files(tmp_path: Path) -> None:
    """Move legacy original photos and thumbnails out of SQLite during migration."""
    image_data = _jpeg_bytes()
    result = inspection_service.inspect_produce(
        image_data,
        detect=lambda _: Finding(
            produce_type=ProduceType.APPLE,
            condition=Condition.FRESH,
            confidence=0.9,
        ),
    )
    database_path = tmp_path / "legacy-source.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE inspections (
                inspection_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                produce_type TEXT NOT NULL,
                verdict TEXT NOT NULL,
                grade TEXT NOT NULL,
                thumbnail BLOB NOT NULL,
                original_image BLOB,
                result_json TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO inspections VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.inspection_id,
                "2026-01-01T00:00:00+00:00",
                result.produce_type,
                result.verdict,
                result.grade,
                b"thumbnail",
                image_data,
                result.model_dump_json(),
            ),
        )
    repository = InspectionRepository(database_path)

    history_item = repository.list_history(10, 0)[0]
    source = repository.get_report_source(result.inspection_id)

    assert history_item.report_source_available is True
    assert repository.get_thumbnail(result.inspection_id) == b"thumbnail"
    assert source is not None
    assert source.original_image == image_data
    assert (tmp_path / "images" / f"{result.inspection_id}_original.bin").is_file()
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(inspections)")}
    assert "thumbnail" not in columns
    assert "original_image" not in columns
    assert "thumbnail_path" in columns
    assert "original_image_path" in columns


def test_automatic_report_is_persisted_for_an_edible_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generate the default PDF during inspection without a user action."""
    monkeypatch.setattr(
        inspection_service, "generate_automatic_report", report_service.generate_automatic_report
    )
    monkeypatch.setattr(report_service, "write_inspection_report", lambda *_: _report_text())
    result = inspection_service.inspect_produce(
        _jpeg_bytes(),
        detect=lambda _: Finding(
            produce_type=ProduceType.APPLE,
            detected_item="apple",
            item_is_fruit=True,
            condition=Condition.FRESH,
            confidence=0.9,
        ),
    )
    assert result.report_available is True
    pdf = InspectionRepository(settings.inspection_db_path).get_report_pdf(result.inspection_id)
    assert pdf is not None
    assert pdf.startswith(b"%PDF-")


def test_automatic_report_uses_the_normal_layout_for_out_of_category_food(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Give edible products outside the categories the same report as other products."""
    monkeypatch.setattr(
        inspection_service, "generate_automatic_report", report_service.generate_automatic_report
    )
    monkeypatch.setattr(report_service, "write_inspection_report", lambda *_: _report_text())
    result = inspection_service.inspect_produce(
        _jpeg_bytes(),
        detect=lambda _: Finding(
            produce_type=ProduceType.UNKNOWN,
            detected_item="guava",
            item_is_fruit=True,
            condition=Condition.FRESH,
            confidence=0.9,
        ),
    )
    assert result.in_inspection_categories is False
    assert result.report_available is True
    pdf = InspectionRepository(settings.inspection_db_path).get_report_pdf(result.inspection_id)
    assert pdf is not None
    assert pdf.startswith(b"%PDF-")


def test_automatic_report_falls_back_when_ai_writing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep a downloadable PDF when the AI narrative cannot be produced."""
    monkeypatch.setattr(
        inspection_service, "generate_automatic_report", report_service.generate_automatic_report
    )

    def fail_report(*_: object) -> InspectionReportText:
        raise groq_report.ReportAIError("rate limited")

    monkeypatch.setattr(report_service, "write_inspection_report", fail_report)
    result = inspection_service.inspect_produce(
        _jpeg_bytes(),
        detect=lambda _: Finding(
            produce_type=ProduceType.ORANGE,
            condition=Condition.FRESH,
            confidence=0.9,
        ),
    )
    assert result.report_available is True
    pdf = InspectionRepository(settings.inspection_db_path).get_report_pdf(result.inspection_id)
    assert pdf is not None
    assert pdf.startswith(b"%PDF-")


def test_non_edible_item_gets_no_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave pdf_path empty for an item outside the food categories."""
    monkeypatch.setattr(
        inspection_service, "generate_automatic_report", report_service.generate_automatic_report
    )
    result = inspection_service.inspect_produce(
        _jpeg_bytes(),
        detect=lambda _: Finding(
            produce_type=ProduceType.UNKNOWN,
            detected_item="car",
            item_is_fruit=False,
            condition=Condition.UNSURE,
            confidence=0.9,
        ),
    )
    assert result.report_available is False
    repository = InspectionRepository(settings.inspection_db_path)
    assert repository.get_report_pdf(result.inspection_id) is None
