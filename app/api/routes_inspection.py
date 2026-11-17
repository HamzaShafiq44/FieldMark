"""HTTP routes for produce inspections."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.ai.schemas import InspectionHistoryResponse, InspectionResult, ReportUserType
from app.core.config import settings
from app.services.inspection_service import (
    InspectionDetectionError,
    InspectionStorageError,
    InvalidImageError,
    clear_inspection_history,
    get_inspection_thumbnail,
    inspect_produce,
    list_inspection_history,
)
from app.services.report_service import (
    InspectionReportAIError,
    InspectionReportError,
    InspectionReportUnavailableError,
    generate_inspection_report,
    get_inspection_report,
)

router = APIRouter(prefix="/inspections")
PAYLOAD_TOO_LARGE = "Image exceeds the maximum allowed size."
DEFAULT_HISTORY_PAGE_SIZE = 20
MAX_HISTORY_PAGE_SIZE = 100
THUMBNAIL_NOT_FOUND = "Inspection thumbnail not found."
REPORT_NOT_FOUND = "Inspection report not found."
VOICE_NOTE_TOO_LARGE = "Voice note exceeds the maximum allowed size."
INVALID_VOICE_NOTE = "Choose a supported audio file for the optional voice note."
VOICE_NOTE_SUFFIXES = frozenset({".flac", ".m4a", ".mp3", ".mpga", ".ogg", ".wav", ".webm"})
VOICE_NOTE_MEDIA_TYPES = frozenset(
    {
        "audio/flac",
        "audio/mp4",
        "audio/mpeg",
        "audio/mpga",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "audio/x-flac",
        "audio/x-m4a",
        "audio/x-wav",
    }
)


@router.post("/tomato", response_model=InspectionResult, include_in_schema=False)
@router.post("/produce", response_model=InspectionResult)
async def inspect_produce_route(
    image: Annotated[UploadFile, File()],
) -> InspectionResult:
    """Analyze an uploaded tomato, apple, banana, or orange image."""
    data = await image.read(settings.max_image_bytes + 1)
    if len(data) > settings.max_image_bytes:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=PAYLOAD_TOO_LARGE)
    try:
        return await run_in_threadpool(inspect_produce, data)
    except InvalidImageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InspectionDetectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except InspectionStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Inspection completed but could not be saved.",
        ) from exc


@router.get("/history", response_model=InspectionHistoryResponse)
async def inspection_history_route(
    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY_PAGE_SIZE)] = DEFAULT_HISTORY_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InspectionHistoryResponse:
    """Return a paginated list of prior inspections."""
    try:
        return await run_in_threadpool(list_inspection_history, limit, offset)
    except InspectionStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Inspection history could not be loaded.",
        ) from exc


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
async def clear_inspection_history_route() -> Response:
    """Delete all saved inspections, original photos, and generated reports."""
    try:
        await run_in_threadpool(clear_inspection_history)
    except InspectionStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/history/{inspection_id}/thumbnail")
async def inspection_thumbnail_route(inspection_id: UUID) -> Response:
    """Return a stored preview image for a past inspection."""
    try:
        thumbnail = await run_in_threadpool(get_inspection_thumbnail, str(inspection_id))
    except InspectionStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Inspection thumbnail could not be loaded.",
        ) from exc
    if thumbnail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=THUMBNAIL_NOT_FOUND,
        )
    return Response(content=thumbnail, media_type="image/jpeg")


@router.post("/{inspection_id}/report")
async def generate_inspection_report_route(
    inspection_id: UUID,
    user_type: Annotated[ReportUserType, Form()],
    voice_note: Annotated[UploadFile | None, File()] = None,
) -> Response:
    """Generate and save a tailored PDF report for a prior inspection."""
    voice_data, voice_filename = await _read_voice_note(voice_note)
    try:
        pdf = await run_in_threadpool(
            generate_inspection_report,
            str(inspection_id),
            user_type,
            voice_data,
            voice_filename,
        )
    except InspectionReportUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InspectionReportAIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except InspectionReportError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return _pdf_response(pdf, str(inspection_id))


@router.get("/{inspection_id}/report")
async def download_inspection_report_route(inspection_id: UUID) -> Response:
    """Download a previously generated report from inspection history."""
    try:
        pdf = await run_in_threadpool(get_inspection_report, str(inspection_id))
    except InspectionReportError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    if pdf is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=REPORT_NOT_FOUND,
        )
    return _pdf_response(pdf, str(inspection_id))


async def _read_voice_note(voice_note: UploadFile | None) -> tuple[bytes | None, str]:
    """Validate an optional audio upload before sending it to transcription."""
    if voice_note is None:
        return None, "voice-note.webm"
    suffix = Path(voice_note.filename or "").suffix.lower()
    if suffix not in VOICE_NOTE_SUFFIXES or voice_note.content_type not in VOICE_NOTE_MEDIA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_VOICE_NOTE,
        )
    data = await voice_note.read(settings.max_voice_note_bytes + 1)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_VOICE_NOTE)
    if len(data) > settings.max_voice_note_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=VOICE_NOTE_TOO_LARGE
        )
    return data, f"voice-note{suffix}"


def _pdf_response(pdf: bytes, inspection_id: str) -> Response:
    """Return a PDF as a browser-downloadable attachment."""
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="inspection-{inspection_id}.pdf"'},
    )
