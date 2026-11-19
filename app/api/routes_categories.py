"""HTTP routes for product category management."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.services.category_service import (
    CategoryConflictError,
    CategoryInUseError,
    CategoryLimitError,
    CategoryNotFoundError,
    CategoryStorageError,
    CategoryValidationError,
    create_category,
    delete_category,
    list_categories,
    update_category,
)
from app.storage.category_repository import ProductCategory

router = APIRouter(prefix="/api/categories", tags=["categories"])


class CategoryCreate(BaseModel):
    """Request body for a new category."""

    name: str = Field(min_length=2, max_length=40)
    emoji: str | None = Field(default=None, max_length=8)


class CategoryUpdate(BaseModel):
    """Request body for updating a category."""

    name: str | None = Field(default=None, min_length=2, max_length=40)
    emoji: str | None = Field(default=None, max_length=8)
    is_active: bool | None = None


class CategoryResponse(BaseModel):
    """Public category representation."""

    slug: str
    display_name: str
    emoji: str
    is_active: bool
    created_at: str


class CategoryListResponse(BaseModel):
    """List of all configured categories."""

    items: list[CategoryResponse]


@router.get("", response_model=CategoryListResponse)
async def list_categories_route() -> CategoryListResponse:
    """Return every configured product category."""
    try:
        items = await run_in_threadpool(list_categories)
    except CategoryStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Categories could not be loaded.",
        ) from exc
    return CategoryListResponse(items=[_response(item) for item in items])


@router.post("", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category_route(payload: CategoryCreate) -> CategoryResponse:
    """Add a category to future inspection prompts."""
    try:
        category = await run_in_threadpool(
            create_category,
            payload.name,
            payload.emoji,
        )
    except CategoryValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except CategoryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CategoryLimitError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CategoryStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Category could not be saved.",
        ) from exc
    return _response(category)


@router.patch("/{slug}", response_model=CategoryResponse)
async def update_category_route(slug: str, payload: CategoryUpdate) -> CategoryResponse:
    """Rename, re-emoji, enable, or disable a category."""
    try:
        category = await run_in_threadpool(
            update_category,
            slug,
            name=payload.name,
            emoji=payload.emoji,
            is_active=payload.is_active,
        )
    except CategoryValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CategoryLimitError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CategoryStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Category could not be updated.",
        ) from exc
    return _response(category)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category_route(slug: str) -> None:
    """Delete an unused category; disable it when history references it."""
    try:
        await run_in_threadpool(delete_category, slug)
    except CategoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CategoryInUseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CategoryValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except CategoryStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Category could not be deleted.",
        ) from exc


def _response(category: ProductCategory) -> CategoryResponse:
    """Convert a stored category into its API representation."""
    return CategoryResponse(
        slug=category.slug,
        display_name=category.display_name,
        emoji=category.emoji,
        is_active=category.is_active,
        created_at=category.created_at,
    )
