"""HTTP routes for produce inspections."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.ai.schemas import InspectionHistoryResponse, InspectionResult
from app.core.config import settings
from app.services.inspection_service import (
    InspectionDetectionError,
    InspectionStorageError,
    InvalidImageError,
    get_inspection_thumbnail,
    inspect_produce,
    list_inspection_history,
)

router = APIRouter(prefix="/inspections")
PAYLOAD_TOO_LARGE = "Image exceeds the maximum allowed size."
DEFAULT_HISTORY_PAGE_SIZE = 20
MAX_HISTORY_PAGE_SIZE = 100
THUMBNAIL_NOT_FOUND = "Inspection thumbnail not found."


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
