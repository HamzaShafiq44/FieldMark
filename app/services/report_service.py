"""Orchestrate voice transcription, validated report writing, and PDF persistence."""

import logging
from datetime import UTC, datetime
from uuid import uuid4

from reportlab.platypus.doctemplate import LayoutError

from app.ai.groq_report import ReportAIError, transcribe_voice_note, write_inspection_report
from app.ai.schemas import InspectionReportText, InspectionResult, ReportUserType
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


def generate_inspection_report(
    inspection_id: str,
    user_type: ReportUserType,
    voice_note: bytes | None = None,
    voice_note_filename: str = "voice-note.webm",
) -> bytes:
    """Generate, persist, and return a PDF for a saved inspection."""
    repository = InspectionRepository(settings.inspection_db_path)
    source = _load_report_source(repository, inspection_id)
    transcript = _transcribe(voice_note, voice_note_filename)
    report_text = _write_report(source.result, user_type, transcript)
    report_id = str(uuid4())
    pdf = _render_report(report_id, source, user_type, report_text, transcript)
    _save_report(repository, inspection_id, report_id, user_type, transcript, report_text, pdf)
    return pdf


def get_inspection_report(inspection_id: str) -> bytes | None:
    """Return a previously generated PDF, or None when no report exists."""
    try:
        return InspectionRepository(settings.inspection_db_path).get_report_pdf(inspection_id)
    except HistoryStorageError as exc:
        raise InspectionReportError("Inspection report could not be loaded.") from exc


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


def _transcribe(voice_note: bytes | None, filename: str) -> str:
    """Transcribe optional audio and bound the text sent to the report model."""
    if voice_note is None:
        return ""
    try:
        transcript = transcribe_voice_note(voice_note, filename)
    except ReportAIError as exc:
        raise InspectionReportAIError(str(exc)) from exc
    if len(transcript) > MAX_TRANSCRIPT_CHARACTERS:
        raise InspectionReportInputError(
            "Voice note is too long for the one-page report. Please use a shorter voice note."
        )
    return transcript


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
    pdf: bytes,
    *,
    save_pdf: bool = True,
) -> None:
    """Persist the validated narrative and its downloadable PDF."""
    try:
        repository.save_report(
            inspection_id,
            report_id,
            user_type,
            transcript,
            report_text.model_dump_json(),
            pdf if save_pdf else b"",
        )
    except HistoryStorageError as exc:
        raise InspectionReportError("Inspection report could not be saved.") from exc
