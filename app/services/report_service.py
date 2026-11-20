"""Orchestrate voice transcription, validated report writing, and PDF persistence."""

import logging
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError
from reportlab.platypus.doctemplate import LayoutError

from app.ai.groq_report import ReportAIError, transcribe_voice_note, write_inspection_report
from app.ai.schemas import (
    InspectionReportText,
    InspectionResult,
    ReportUserType,
    VoiceNoteRelation,
)
from app.core.config import settings
from app.reports.pdf_renderer import build_inspection_pdf
from app.storage.inspection_repository import (
    HistoryStorageError,
    InspectionReportSource,
    InspectionRepository,
)

LOGGER = logging.getLogger(__name__)
MAX_TRANSCRIPT_CHARACTERS = 800
NO_REPORT_SOURCE_MESSAGE = "The original photo is unavailable for this inspection report."


class InspectionReportError(RuntimeError):
    """Raised when an inspection report cannot be generated or retrieved."""


class InspectionReportUnavailableError(InspectionReportError):
    """Raised when an inspection lacks the source data required for a report."""


class InspectionReportInputError(InspectionReportError):
    """Raised when report input cannot fit the one-page report format."""


class InspectionReportAIError(InspectionReportError):
    """Raised when transcription or AI report writing fails."""


def generate_automatic_report(inspection_id: str) -> bool:
    """Persist the default report narrative, falling back to deterministic text.

    Voice-only inspections reuse their stored transcript as report evidence.
    Returns True when report data was saved. AI or rendering failures never fail
    the inspection itself; the interface then shows the report as unavailable.
    """
    try:
        repository = InspectionRepository(settings.inspection_db_path)
        source = _load_report_source(repository, inspection_id)
        transcript = source.result.voice_note_transcript or None
        generate_inspection_report(inspection_id, ReportUserType.FARMER, transcript=transcript)
        return True
    except InspectionReportError as exc:
        LOGGER.warning(
            "Automatic report generation failed (%s); trying the deterministic fallback.",
            type(exc).__name__,
        )
    return _generate_deterministic_report(inspection_id)


def _generate_deterministic_report(inspection_id: str) -> bool:
    """Render the normal report layout without the AI narrative."""
    repository = InspectionRepository(settings.inspection_db_path)
    try:
        source = _load_report_source(repository, inspection_id)
        report_text = _deterministic_report_text(source.result)
        report_id = str(uuid4())
        _render_report(
            report_id,
            source,
            ReportUserType.FARMER,
            report_text,
            "",
        )
        _save_report(
            repository,
            inspection_id,
            report_id,
            ReportUserType.FARMER,
            "",
            report_text,
        )
    except (InspectionReportError, ValidationError) as exc:
        LOGGER.error("Deterministic report fallback failed (%s).", type(exc).__name__)
        return False
    return True


def _deterministic_report_text(result: InspectionResult) -> InspectionReportText:
    """Build valid report text directly from the inspection result."""
    product = result.detected_item.strip().title() or result.produce_type.title()
    verdict = result.verdict.replace("_", " ")
    summary = f"{product}: grade {result.grade}, verdict {verdict}."
    findings = [_shorten(note, 180) for note in result.verdict_notes if note.strip()][:3]
    if not findings:
        findings = [
            "The spoken description was recorded without additional notes."
            if result.voice_note_transcript
            else "The visual inspection completed without additional notes."
        ]
    recommendations = [
        item
        for item in dict.fromkeys(
            [
                _shorten(result.recommended_action, 140),
                "Inspect manually before acting.",
            ]
        )
        if item
    ][:3]
    return InspectionReportText(
        summary=_shorten(summary, 300),
        findings=findings,
        recommendations=recommendations,
        voice_note_relation=VoiceNoteRelation.NOT_PROVIDED,
    )


def _shorten(text: str, limit: int) -> str:
    """Trim text to a validated report-field length."""
    cleaned = " ".join(text.split())
    return cleaned[:limit].rstrip()


def generate_inspection_report(
    inspection_id: str,
    user_type: ReportUserType,
    voice_note: bytes | None = None,
    voice_note_filename: str = "voice-note.webm",
    transcript: str | None = None,
) -> bytes:
    """Generate a PDF, persist its narrative, and return the PDF bytes.

    A transcript already stored on a voice-only inspection can be supplied
    directly instead of re-transcribing the original audio.
    """
    repository = InspectionRepository(settings.inspection_db_path)
    source = _load_report_source(repository, inspection_id)
    if transcript is not None:
        transcript = _bound_transcript(transcript)
    elif voice_note is not None:
        transcript = _transcribe(voice_note, voice_note_filename)
    else:
        # Voice-only inspections carry their evidence on the stored result.
        transcript = _bound_transcript(source.result.voice_note_transcript or "")
    report_text = _write_report(source.result, user_type, transcript)
    report_id = str(uuid4())
    pdf = _render_report(report_id, source, user_type, report_text, transcript)
    _save_report(repository, inspection_id, report_id, user_type, transcript, report_text)
    return pdf


def get_inspection_report(inspection_id: str) -> bytes | None:
    """Rebuild the saved report as a PDF, or return None when no report exists."""
    repository = InspectionRepository(settings.inspection_db_path)
    try:
        data = repository.get_report_data(inspection_id)
        if data is None:
            return None
        source = repository.get_report_source(inspection_id)
    except HistoryStorageError as exc:
        raise InspectionReportError("Inspection report could not be loaded.") from exc
    if source is None:
        raise InspectionReportUnavailableError(NO_REPORT_SOURCE_MESSAGE)
    try:
        report_text = InspectionReportText.model_validate_json(data.report_text_json)
        user_type = ReportUserType(data.user_type)
    except (ValidationError, ValueError) as exc:
        raise InspectionReportError("Inspection report data is invalid.") from exc
    return _render_report(data.report_id, source, user_type, report_text, data.transcript)


def _load_report_source(
    repository: InspectionRepository,
    inspection_id: str,
) -> InspectionReportSource:
    """Load original inspection evidence, rejecting legacy entries without a source image."""
    try:
        source = repository.get_report_source(inspection_id)
    except HistoryStorageError as exc:
        raise InspectionReportError("Inspection report data could not be loaded.") from exc
    if source is None:
        raise InspectionReportUnavailableError(NO_REPORT_SOURCE_MESSAGE)
    return source


def _render_report(
    report_id: str,
    source: InspectionReportSource,
    user_type: ReportUserType,
    report_text: InspectionReportText,
    transcript: str,
) -> bytes:
    """Render a report and expose layout or image failures clearly."""
    try:
        return build_inspection_pdf(
            report_id,
            datetime.now(UTC).isoformat(),
            source.created_at,
            user_type,
            source.result,
            report_text,
            transcript,
            source.original_image,
        )
    except (OSError, ValueError, TypeError, LayoutError) as exc:
        LOGGER.error("Inspection PDF rendering failed (%s).", type(exc).__name__)
        raise InspectionReportError("Inspection PDF could not be rendered.") from exc


def _bound_transcript(transcript: str) -> str:
    """Reject transcripts too long for a one-page report."""
    if len(transcript) > MAX_TRANSCRIPT_CHARACTERS:
        raise InspectionReportInputError(
            "Voice note is too long for the one-page report. Please use a shorter voice note."
        )
    return transcript


def _transcribe(voice_note: bytes | None, filename: str) -> str:
    """Transcribe optional audio and bound the text sent to the report model."""
    if voice_note is None:
        return ""
    try:
        transcript = transcribe_voice_note(voice_note, filename)
    except ReportAIError as exc:
        raise InspectionReportAIError(str(exc)) from exc
    return _bound_transcript(transcript)


def _write_report(
    result: InspectionResult,
    user_type: ReportUserType,
    transcript: str,
) -> InspectionReportText:
    """Generate report text and translate provider failures to service errors."""
    try:
        return write_inspection_report(result, user_type, transcript)
    except ReportAIError as exc:
        raise InspectionReportAIError(str(exc)) from exc


def _save_report(
    repository: InspectionRepository,
    inspection_id: str,
    report_id: str,
    user_type: ReportUserType,
    transcript: str,
    report_text: InspectionReportText,
) -> None:
    """Persist only the validated narrative used to rebuild the PDF."""
    try:
        repository.save_report(
            inspection_id,
            report_id,
            user_type,
            transcript,
            report_text.model_dump_json(),
        )
    except HistoryStorageError as exc:
        raise InspectionReportError("Inspection report could not be saved.") from exc
