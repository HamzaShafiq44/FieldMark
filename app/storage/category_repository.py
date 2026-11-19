"""SQLite repository for product categories."""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

LOGGER = logging.getLogger(__name__)
DEFAULT_CATEGORIES = (
    ("tomato", "Tomato", "🍅"),
    ("apple", "Apple", "🍎"),
    ("banana", "Banana", "🍌"),
    ("orange", "Orange", "🍊"),
)
CREATE_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""
CREATE_CATEGORIES_TABLE = """
CREATE TABLE IF NOT EXISTS product_categories (
    slug TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
)
"""
INSERT_CATEGORY = """
INSERT INTO product_categories (slug, display_name, emoji, is_active, created_at)
VALUES (?, ?, ?, 1, ?)
ON CONFLICT(slug) DO NOTHING
"""
SELECT_CATEGORIES = """
SELECT slug, display_name, emoji, is_active, created_at
FROM product_categories
ORDER BY rowid ASC
"""
SELECT_ACTIVE_SLUGS = """
SELECT slug FROM product_categories
WHERE is_active = 1
ORDER BY rowid ASC
"""
SELECT_CATEGORY = """
SELECT slug, display_name, emoji, is_active, created_at
FROM product_categories
WHERE slug = ?
"""
UPDATE_CATEGORY = "UPDATE product_categories SET {fields} WHERE slug = ?"
DELETE_CATEGORY = "DELETE FROM product_categories WHERE slug = ?"
COUNT_INSPECTIONS = "SELECT COUNT(*) FROM inspections WHERE produce_type = ?"
CATEGORIES_SEEDED_KEY = "categories_seeded"


class CategoryStorageError(RuntimeError):
    """Raised when category data cannot be read or written."""


@dataclass(frozen=True)
class ProductCategory:
    """A product category stored in SQLite."""

    slug: str
    display_name: str
    emoji: str
    is_active: bool
    created_at: str


class CategoryRepository:
    """Persist product categories in the same SQLite database as inspections."""

    def __init__(self, database_path: Path) -> None:
        """Configure the SQLite database location."""
        self.database_path = database_path

    def list_all(self) -> list[ProductCategory]:
        """Return every category, including disabled ones."""
        try:
            with self._connection() as connection:
                rows = connection.execute(SELECT_CATEGORIES).fetchall()
            return [_category_from_row(row) for row in rows]
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Category read failed (%s).", type(exc).__name__)
            raise CategoryStorageError("Categories could not be loaded.") from exc

    def list_active_slugs(self) -> list[str]:
        """Return the slugs currently used by the inspection prompt."""
        try:
            with self._connection() as connection:
                rows = connection.execute(SELECT_ACTIVE_SLUGS).fetchall()
            return [str(row["slug"]) for row in rows]
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Active category read failed (%s).", type(exc).__name__)
            raise CategoryStorageError("Categories could not be loaded.") from exc

    def get(self, slug: str) -> ProductCategory | None:
        """Return one category by slug."""
        try:
            with self._connection() as connection:
                row = connection.execute(SELECT_CATEGORY, (slug,)).fetchone()
            return _category_from_row(row) if row is not None else None
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Category read failed (%s).", type(exc).__name__)
            raise CategoryStorageError("Category could not be loaded.") from exc

    def create(self, slug: str, display_name: str, emoji: str) -> ProductCategory:
        """Insert a category and return its stored representation."""
        created_at = datetime.now(UTC).isoformat()
        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    INSERT_CATEGORY,
                    (slug, display_name, emoji, created_at),
                )
                if cursor.rowcount == 0:
                    raise CategoryStorageError("Category already exists.")
                row = connection.execute(SELECT_CATEGORY, (slug,)).fetchone()
            if row is None:
                raise CategoryStorageError("Category could not be created.")
            return _category_from_row(row)
        except CategoryStorageError:
            raise
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Category write failed (%s).", type(exc).__name__)
            raise CategoryStorageError("Category could not be created.") from exc

    def update(
        self,
        slug: str,
        *,
        display_name: str | None = None,
        emoji: str | None = None,
        is_active: bool | None = None,
    ) -> ProductCategory | None:
        """Update mutable category fields, returning None when the slug is unknown."""
        fields: list[str] = []
        values: list[object] = []
        if display_name is not None:
            fields.append("display_name = ?")
            values.append(display_name)
        if emoji is not None:
            fields.append("emoji = ?")
            values.append(emoji)
        if is_active is not None:
            fields.append("is_active = ?")
            values.append(int(is_active))
        if not fields:
            return self.get(slug)

        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    UPDATE_CATEGORY.format(fields=", ".join(fields)),
                    (*values, slug),
                )
                if cursor.rowcount == 0:
                    return None
                row = connection.execute(SELECT_CATEGORY, (slug,)).fetchone()
            return _category_from_row(row) if row is not None else None
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Category update failed (%s).", type(exc).__name__)
            raise CategoryStorageError("Category could not be updated.") from exc

    def delete(self, slug: str) -> bool:
        """Delete a category, returning False when the slug is unknown."""
        try:
            with self._connection() as connection:
                cursor = connection.execute(DELETE_CATEGORY, (slug,))
            return cursor.rowcount > 0
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Category delete failed (%s).", type(exc).__name__)
            raise CategoryStorageError("Category could not be deleted.") from exc

    def count_inspections(self, slug: str) -> int:
        """Count stored inspections that reference a category slug."""
        try:
            with self._connection() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'inspections'"
                ).fetchone()
                if exists is None:
                    return 0
                row = connection.execute(COUNT_INSPECTIONS, (slug,)).fetchone()
            return int(row[0]) if row is not None else 0
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Category usage read failed (%s).", type(exc).__name__)
            raise CategoryStorageError("Category usage could not be checked.") from exc

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Open a connection, create the category schema, and seed defaults once."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                connection.execute(CREATE_METADATA_TABLE)
                connection.execute(CREATE_CATEGORIES_TABLE)
                _seed_default_categories(connection)
            with connection:
                yield connection


def _seed_default_categories(connection: sqlite3.Connection) -> None:
    """Insert the original four categories exactly once per database."""
    seeded = connection.execute(
        "SELECT value FROM app_metadata WHERE key = ?",
        (CATEGORIES_SEEDED_KEY,),
    ).fetchone()
    if seeded is not None:
        return
    created_at = datetime.now(UTC).isoformat()
    for slug, display_name, emoji in DEFAULT_CATEGORIES:
        connection.execute(INSERT_CATEGORY, (slug, display_name, emoji, created_at))
    connection.execute(
        "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
        (CATEGORIES_SEEDED_KEY, "1"),
    )


def _category_from_row(row: sqlite3.Row) -> ProductCategory:
    """Build a category record from a SQLite row."""
    return ProductCategory(
        slug=str(row["slug"]),
        display_name=str(row["display_name"]),
        emoji=str(row["emoji"]),
        is_active=bool(row["is_active"]),
        created_at=str(row["created_at"]),
    )
