"""Validation and orchestration for product categories."""

import re
from pathlib import Path

import yaml

from app.core.config import settings
from app.storage.category_repository import (
    CategoryRepository,
    ProductCategory,
)
from app.storage.category_repository import (
    CategoryStorageError as CategoryRepositoryError,
)

MAX_DISPLAY_NAME_LENGTH = 40
MIN_DISPLAY_NAME_LENGTH = 2
MAX_EMOJI_LENGTH = 8
MAX_ACTIVE_CATEGORIES = 20
RESERVED_SLUGS = frozenset({"unknown"})
SLUG_PATTERN = re.compile(r"[^a-z0-9]+")

FRUITS_FILE = Path(__file__).with_name("fruits.yaml")


def _load_allowed_fruits() -> frozenset[str]:
    """Load the allowed fruit slugs from the packaged YAML file."""
    with FRUITS_FILE.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    fruits = data.get("fruits") if isinstance(data, dict) else None
    if (
        not isinstance(fruits, list)
        or not fruits
        or not all(isinstance(item, str) and item for item in fruits)
    ):
        raise RuntimeError("fruits.yaml must define a non-empty 'fruits' list of strings.")
    return frozenset(fruits)


# Edible fruits users are allowed to add as inspection categories.
ALLOWED_FRUITS = _load_allowed_fruits()


class CategoryError(RuntimeError):
    """Base class for category service errors."""


class CategoryValidationError(CategoryError):
    """Raised when category input is invalid."""


class CategoryConflictError(CategoryError):
    """Raised when a category slug already exists."""


class CategoryNotFoundError(CategoryError):
    """Raised when a category slug does not exist."""


class CategoryInUseError(CategoryError):
    """Raised when a category is referenced by existing inspections."""


class CategoryLimitError(CategoryError):
    """Raised when the active category limit has been reached."""


class CategoryStorageError(CategoryError):
    """Raised when category persistence fails."""


def slugify(name: str) -> str:
    """Convert a display name into a stable, prompt-safe category slug."""
    raw = name.strip().lower()
    slug = SLUG_PATTERN.sub("-", raw).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        raise CategoryValidationError("Category name must include letters or numbers.")
    if slug[0].isdigit():
        slug = f"c-{slug}"
    return slug[:MAX_DISPLAY_NAME_LENGTH].rstrip("-")


def list_categories() -> list[ProductCategory]:
    """Return all categories, including disabled ones."""
    try:
        return _repository().list_all()
    except CategoryRepositoryError as exc:
        raise CategoryStorageError(str(exc)) from exc


def list_active_category_slugs() -> list[str]:
    """Return the category slugs that should be used by the inspection prompt."""
    try:
        return _repository().list_active_slugs()
    except CategoryRepositoryError as exc:
        raise CategoryStorageError(str(exc)) from exc


def create_category(name: str, emoji: str | None = None) -> ProductCategory:
    """Validate and create a new active category."""
    display_name = _validate_name(name)
    _validate_fruit(display_name)
    clean_emoji = _validate_emoji(emoji)
    slug = slugify(display_name)
    if slug in RESERVED_SLUGS:
        raise CategoryValidationError("That category name is reserved.")

    repository = _repository()
    try:
        if repository.get(slug) is not None:
            raise CategoryConflictError("A category with that name already exists.")
        active_slugs = repository.list_active_slugs()
        if len(active_slugs) >= MAX_ACTIVE_CATEGORIES:
            raise CategoryLimitError(
                f"Only {MAX_ACTIVE_CATEGORIES} active categories are supported."
            )
        return repository.create(slug, display_name, clean_emoji)
    except CategoryRepositoryError as exc:
        raise CategoryStorageError(str(exc)) from exc


def update_category(
    slug: str,
    *,
    name: str | None = None,
    emoji: str | None = None,
    is_active: bool | None = None,
) -> ProductCategory:
    """Rename, re-emoji, enable, or disable a category."""
    repository = _repository()
    try:
        existing = repository.get(slug)
        if existing is None:
            raise CategoryNotFoundError("Category not found.")

        display_name = _validate_name(name) if name is not None else None
        clean_emoji = _validate_emoji(emoji) if emoji is not None else None
        if display_name is not None:
            _validate_fruit(display_name)
        if is_active is False and existing.is_active:
            active_slugs = repository.list_active_slugs()
            if len(active_slugs) <= 1:
                raise CategoryValidationError("At least one active category must remain available.")

        updated = repository.update(
            slug,
            display_name=display_name,
            emoji=clean_emoji,
            is_active=is_active,
        )
        if updated is None:
            raise CategoryNotFoundError("Category not found.")
        return updated
    except CategoryRepositoryError as exc:
        raise CategoryStorageError(str(exc)) from exc


def delete_category(slug: str) -> None:
    """Delete an unused category; refuse when historical inspections reference it."""
    repository = _repository()
    try:
        existing = repository.get(slug)
        if existing is None:
            raise CategoryNotFoundError("Category not found.")
        if repository.count_inspections(slug) > 0:
            raise CategoryInUseError(
                "This category is used by existing inspections. Disable it instead."
            )
        if existing.is_active and len(repository.list_active_slugs()) <= 1:
            raise CategoryValidationError("At least one active category must remain available.")
        if not repository.delete(slug):
            raise CategoryNotFoundError("Category not found.")
    except CategoryRepositoryError as exc:
        raise CategoryStorageError(str(exc)) from exc


def _validate_name(name: str) -> str:
    """Normalize and validate a display name."""
    display_name = " ".join(name.split())
    if not MIN_DISPLAY_NAME_LENGTH <= len(display_name) <= MAX_DISPLAY_NAME_LENGTH:
        raise CategoryValidationError(
            f"Category name must be {MIN_DISPLAY_NAME_LENGTH}-{MAX_DISPLAY_NAME_LENGTH} characters."
        )
    return display_name


def _validate_emoji(emoji: str | None) -> str:
    """Normalize and validate an optional emoji."""
    clean_emoji = (emoji or "").strip()
    if len(clean_emoji) > MAX_EMOJI_LENGTH:
        raise CategoryValidationError("Emoji is too long.")
    return clean_emoji


def _validate_fruit(display_name: str) -> None:
    """Reject category names that are not edible fruits.

    A name is accepted when any word in it matches an allowed fruit, so
    variety names like "Alphonso Mango" or "Hass Avocado" are allowed.
    """
    slug = slugify(display_name)
    words = slug.split("-")
    if not any(word in ALLOWED_FRUITS for word in words):
        raise CategoryValidationError(
            "Only edible fruit categories are supported (for example: apple,"
            " banana, orange, tomato)."
        )


def _repository() -> CategoryRepository:
    """Create a repository for the configured database."""
    return CategoryRepository(settings.inspection_db_path)
