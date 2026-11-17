"""SQLite repository for inspection summaries and image thumbnails."""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.ai.schemas import InspectionHistoryItem, InspectionResult

LOGGER = logging.getLogger(__name__)
THUMBNAIL_SIZE = (192, 192)
THUMBNAIL_QUALITY = 75
HISTORY_THUMBNAIL_PATH = "/inspections/history/{inspection_id}/thumbnail"
CREATE_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS inspections (
    inspection_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    produce_type TEXT NOT NULL,
    verdict TEXT NOT NULL,
    grade TEXT NOT NULL,
    thumbnail BLOB NOT NULL
)
"""
INSERT_HISTORY_ITEM = """
INSERT INTO inspections (
    inspection_id, created_at, produce_type, verdict, grade, thumbnail
) VALUES (?, ?, ?, ?, ?, ?)
"""
SELECT_HISTORY_ITEMS = """
SELECT inspection_id, created_at, produce_type, verdict, grade
FROM inspections
ORDER BY created_at DESC, inspection_id DESC
LIMIT ? OFFSET ?
"""
SELECT_THUMBNAIL = "SELECT thumbnail FROM inspections WHERE inspection_id = ?"


class HistoryStorageError(RuntimeError):
    """Raised when inspection history cannot be read or written."""


class InspectionRepository:
    """Persist inspection history using a local SQLite database."""

    def __init__(self, database_path: Path) -> None:
        """Configure the SQLite database location."""
        self.database_path = database_path

    def save(self, result: InspectionResult, image: Image.Image) -> None:
        """Persist a result summary and a small preview image."""
        try:
            thumbnail = _encode_thumbnail(image)
            created_at = datetime.now(UTC).isoformat()
            with self._connection() as connection:
                connection.execute(
                    INSERT_HISTORY_ITEM,
                    (
                        result.inspection_id,
                        created_at,
                        result.produce_type.value,
                        result.verdict,
                        result.grade,
                        thumbnail,
                    ),
                )
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection history write failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection history could not be saved.") from exc

    def list_history(self, limit: int, offset: int) -> list[InspectionHistoryItem]:
        """Return recent inspections in reverse chronological order."""
        try:
            with self._connection() as connection:
                rows = connection.execute(SELECT_HISTORY_ITEMS, (limit, offset)).fetchall()
            return [
                InspectionHistoryItem(
                    inspection_id=row["inspection_id"],
                    created_at=row["created_at"],
                    produce_type=row["produce_type"],
                    verdict=row["verdict"],
                    grade=row["grade"],
                    thumbnail_url=HISTORY_THUMBNAIL_PATH.format(inspection_id=row["inspection_id"]),
                )
                for row in rows
            ]
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection history read failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection history could not be loaded.") from exc

    def get_thumbnail(self, inspection_id: str) -> bytes | None:
        """Return a stored thumbnail, or None when the inspection does not exist."""
        try:
            with self._connection() as connection:
                row = connection.execute(SELECT_THUMBNAIL, (inspection_id,)).fetchone()
            return bytes(row["thumbnail"]) if row is not None else None
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection thumbnail read failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection thumbnail could not be loaded.") from exc

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Open a database connection and ensure the history table exists."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(CREATE_HISTORY_TABLE)
            with connection:
                yield connection


def _encode_thumbnail(image: Image.Image) -> bytes:
    """Resize and JPEG-encode a private history preview."""
    thumbnail = image.copy()
    thumbnail.thumbnail(THUMBNAIL_SIZE)
    buffer = BytesIO()
    thumbnail.save(buffer, format="JPEG", quality=THUMBNAIL_QUALITY)
    return buffer.getvalue()
