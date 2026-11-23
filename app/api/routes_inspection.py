"""HTTP routes for product inspections."""

import logging
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.ai.schemas import (
    InspectionDetail,
    InspectionHistoryResponse,
    InspectionJob,
    InspectionResult,
    JobKind,
    ReportUserType,
)
from app.core.config import settings
from app.services import job_service
from app.services.inspection_service import (
    DefectRelocationError,
    InspectionDetectionError,
    InspectionStorageError,
    InvalidImageError,
    RelocationNotApplicableError,
    RelocationNotFoundError,
    RelocationStorageError,
    RelocationVisionError,
    clear_inspection_history,
    finalize_inspection,
    get_inspection_annotated,
    get_inspection_detail,
    get_inspection_original,
    get_inspection_thumbnail,
    inspect_from_voice,
    inspect_produce,
    list_inspection_history,
    relocate_defect,
)
from app.services.report_service import (
    InspectionReportAIError,
    InspectionReportError,
    InspectionReportInputError,
    InspectionReportUnavailableError,
    generate_inspection_report,
    get_inspection_report,
)

router = APIRouter(prefix="/inspections")
LOGGER = logging.getLogger(__name__)
PAYLOAD_TOO_LARGE = "Image exceeds the maximum allowed size."
DEFAULT_HISTORY_PAGE_SIZE = 20
MAX_HISTORY_PAGE_SIZE = 100
THUMBNAIL_NOT_FOUND = "Inspection thumbnail not found."
ORIGINAL_NOT_FOUND = "Inspection photo not found."
ANNOTATED_NOT_FOUND = "No annotated photo is available for this inspection."
REPORT_NOT_FOUND = "Inspection report not found."
INSPECTION_NOT_FOUND = "Inspection not found."
VOICE_NOTE_TOO_LARGE = "Voice note exceeds the maximum allowed size."
INVALID_VOICE_NOTE = "Choose a supported audio file for the optional voice note."
VOICE_NOTE_SUFFIXES = frozenset(
    {".flac", ".m4a", ".mp3", ".mp4", ".mpga", ".aac", ".ogg", ".wav", ".webm"}
)
VOICE_NOTE_MEDIA_TYPES = frozenset(
    {
        "audio/aac",
        "audio/flac",
        "audio/m4a",
        "audio/mp3",
        "audio/mp4",
        "audio/mpeg",
        "audio/mpga",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "audio/x-aac",
        "audio/x-flac",
        "audio/x-m4a",
        "audio/x-wav",
    }
)
# Browsers sometimes leave a known extension without a useful MIME type.
VOICE_NOTE_GENERIC_MEDIA_TYPES = frozenset({"", "application/octet-stream"})
JOB_NOT_FOUND = "Inspection job not found."


@router.post("/jobs", response_model=InspectionJob, status_code=status.HTTP_202_ACCEPTED)
async def submit_inspection_job_route(
    background_tasks: BackgroundTasks,
    image: Annotated[UploadFile | None, File()] = None,
    voice_note: Annotated[UploadFile | None, File()] = None,
) -> InspectionJob:
    """Queue an image or voice note for background processing.

    The response returns immediately with a job ID; analysis, refinement, and
    reporting continue in a worker regardless of what the browser does next.
    """
    if (image is None and voice_note is None) or (image is not None and voice_note is not None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either an image or a voice note, not both.",
        )
    if image is not None:
        data = await image.read(settings.max_image_bytes + 1)
        if len(data) > settings.max_image_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=PAYLOAD_TOO_LARGE
            )
        job = job_service.create_job(JobKind.IMAGE, data)
    else:
        voice_data, voice_filename = await _read_voice_note(voice_note)
        if voice_data is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Voice note could not be read."
            )
        job = job_service.create_job(JobKind.VOICE, voice_data, voice_filename)
    background_tasks.add_task(job_service.process_job, job.job_id)
    LOGGER.info("Queued inspection job %s (%s).", job.job_id, job.kind.value)
    return job


@router.get("/jobs", response_model=list[InspectionJob])
async def list_inspection_jobs_route() -> list[InspectionJob]:
    """Return recent jobs so clients can restore progress after a refresh."""
    return job_service.list_jobs()


@router.get("/jobs/{job_id}", response_model=InspectionJob)
async def get_inspection_job_route(job_id: UUID) -> InspectionJob:
    """Return the status and result of one background job."""
    job = job_service.get_job(str(job_id))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=JOB_NOT_FOUND)
    return job


@router.post("/tomato", response_model=InspectionResult, include_in_schema=False)
@router.post("/produce", response_model=InspectionResult, include_in_schema=False)
@router.post("/product", response_model=InspectionResult)
async def inspect_product_route(
    background_tasks: BackgroundTasks,
    image: Annotated[UploadFile | None, File()] = None,
    voice_note: Annotated[UploadFile | None, File()] = None,
) -> InspectionResult:
    """Analyze either an uploaded image **or** a voice note.

    Exactly one of `image` or `voice_note` must be provided.
    """
    if (image is None and voice_note is None) or (image is not None and voice_note is not None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either an image or a voice note, not both.",
        )
    if image is not None:
        LOGGER.info(
            "Received inspection request: filename='%s', content_type='%s'",
            image.filename,
            image.content_type,
        )
        data = await image.read(settings.max_image_bytes + 1)
        if len(data) > settings.max_image_bytes:
            LOGGER.warning(
                "Uploaded image exceeds size limit: %d > %d bytes",
                len(data),
                settings.max_image_bytes,
            )
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=PAYLOAD_TOO_LARGE
            )
        try:
            result = await run_in_threadpool(inspect_produce, data)
            LOGGER.info(
                "Inspection %s succeeded: produce_type='%s', detected='%s', "
                "verdict='%s', grade='%s', bbox=%s",
                result.inspection_id,
                result.produce_type,
                result.detected_item,
                result.verdict,
                result.grade,
                result.bbox,
            )
            # The verdict is ready now; the optional second vision call and PDF
            # report generation run after the response is sent.
            background_tasks.add_task(finalize_inspection, result.inspection_id)
            return result
        except InvalidImageError as exc:
            LOGGER.warning("Invalid image uploaded: %s", exc)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except InspectionDetectionError as exc:
            LOGGER.error("Inspection detection failed: %s", exc)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        except InspectionStorageError as exc:
            LOGGER.error("Inspection storage failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Inspection completed but could not be saved.",
            ) from exc
    else:
        # Voice note path
        voice_data, voice_filename = await _read_voice_note(voice_note)
        if voice_data is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Voice note could not be read."
            )
        try:
            result = await run_in_threadpool(inspect_from_voice, voice_data, voice_filename)
            background_tasks.add_task(finalize_inspection, result.inspection_id)
            return result
        except InspectionStorageError as exc:
            LOGGER.error("Inspection storage failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Voice‑note inspection completed but could not be saved.",
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


@router.get("/history/{inspection_id}", response_model=InspectionDetail)
async def inspection_detail_route(inspection_id: UUID) -> InspectionDetail:
    """Return the full stored result and report narrative for one past inspection."""
    try:
        detail = await run_in_threadpool(get_inspection_detail, str(inspection_id))
    except InspectionStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Inspection details could not be loaded.",
        ) from exc
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=INSPECTION_NOT_FOUND)
    return detail


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


@router.get("/history/{inspection_id}/original")
async def inspection_original_route(inspection_id: UUID) -> Response:
    """Return the original photo of a past inspection."""
    try:
        photo = await run_in_threadpool(get_inspection_original, str(inspection_id))
    except InspectionStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Inspection photo could not be loaded.",
        ) from exc
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ORIGINAL_NOT_FOUND)
    return Response(content=photo, media_type="image/jpeg")


@router.get("/history/{inspection_id}/annotated")
async def inspection_annotated_route(inspection_id: UUID) -> Response:
    """Return the original photo with the model-reported defect area marked."""
    try:
        photo = await run_in_threadpool(get_inspection_annotated, str(inspection_id))
    except InspectionStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Annotated photo could not be loaded.",
        ) from exc
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ANNOTATED_NOT_FOUND)
    return Response(content=photo, media_type="image/jpeg")


@router.post("/{inspection_id}/relocate", response_model=InspectionResult)
async def relocate_defect_route(inspection_id: UUID) -> InspectionResult:
    """Locate and save the defect box for an inspection that has none."""
    LOGGER.info("Defect relocation endpoint called for inspection_id=%s", inspection_id)
    try:
        result = await run_in_threadpool(relocate_defect, str(inspection_id))
        LOGGER.info("Relocation completed for %s: bbox=%s", inspection_id, result.bbox)
        return result
    except RelocationNotFoundError as exc:
        LOGGER.warning("Relocation not found: %s", exc)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RelocationNotApplicableError as exc:
        LOGGER.warning("Relocation not applicable for %s: %s", inspection_id, exc)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RelocationVisionError as exc:
        LOGGER.error("Relocation vision failed for %s: %s", inspection_id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except (RelocationStorageError, DefectRelocationError) as exc:
        LOGGER.error("Relocation storage failed for %s: %s", inspection_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


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
    except InspectionReportInputError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InspectionReportAIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except InspectionReportError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return _pdf_response(pdf, str(inspection_id))


@router.get("/{inspection_id}/report")
async def download_inspection_report_route(
    inspection_id: UUID,
    download: Annotated[bool, Query()] = True,
) -> Response:
    """Serve a previously generated report, as an attachment by default."""
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
    return _pdf_response(pdf, str(inspection_id), download=download)


async def _read_voice_note(voice_note: UploadFile | None) -> tuple[bytes | None, str]:
    """Validate an optional audio upload before sending it to transcription."""
    if voice_note is None:
        return None, "voice-note.webm"
    suffix = Path(voice_note.filename or "").suffix.lower()
    content_type = (voice_note.content_type or "").split(";")[0].strip().lower()
    # Accept a known extension with either a supported MIME type or a generic one.
    # Some browsers report .m4a as audio/x-m4a and others leave the type blank.
    if suffix not in VOICE_NOTE_SUFFIXES or (
        content_type not in VOICE_NOTE_MEDIA_TYPES
        and content_type not in VOICE_NOTE_GENERIC_MEDIA_TYPES
    ):
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


def _pdf_response(pdf: bytes, inspection_id: str, *, download: bool = True) -> Response:
    """Return a PDF as a browser-downloadable attachment or an inline document."""
    disposition = "attachment" if download else "inline"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="inspection-{inspection_id}.pdf"'
        },
    )
