"""SQLite repository for inspection summaries and image thumbnails."""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from PIL import Image
from pydantic import ValidationError

from app.ai.schemas import InspectionHistoryItem, InspectionResult, ReportUserType

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
    thumbnail BLOB NOT NULL,
    original_image BLOB,
    result_json TEXT
)
"""
INSERT_HISTORY_ITEM = """
INSERT INTO inspections (
    inspection_id, created_at, produce_type, verdict, grade, thumbnail, original_image, result_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""
SELECT_HISTORY_ITEMS = """
SELECT inspection_id, created_at, produce_type, verdict, grade,
       EXISTS(
           SELECT 1 FROM inspection_reports
           WHERE inspection_reports.inspection_id = inspections.inspection_id
       ) AS report_available,
       original_image IS NOT NULL AND result_json IS NOT NULL AS report_source_available
FROM inspections
ORDER BY created_at DESC, inspection_id DESC
LIMIT ? OFFSET ?
"""
SELECT_THUMBNAIL = "SELECT thumbnail FROM inspections WHERE inspection_id = ?"
SELECT_REPORT_SOURCE = """
SELECT created_at, original_image, result_json
FROM inspections
WHERE inspection_id = ?
"""
CREATE_REPORTS_TABLE = """
CREATE TABLE IF NOT EXISTS inspection_reports (
    inspection_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    user_type TEXT NOT NULL,
    transcript TEXT NOT NULL,
    report_text_json TEXT NOT NULL,
    pdf BLOB NOT NULL
)
"""
INSERT_REPORT = """
INSERT INTO inspection_reports (
    inspection_id, report_id, created_at, user_type, transcript, report_text_json, pdf
) VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(inspection_id) DO UPDATE SET
    report_id = excluded.report_id,
    created_at = excluded.created_at,
    user_type = excluded.user_type,
    transcript = excluded.transcript,
    report_text_json = excluded.report_text_json,
    pdf = excluded.pdf
"""
SELECT_REPORT_PDF = "SELECT pdf FROM inspection_reports WHERE inspection_id = ?"
DELETE_REPORTS = "DELETE FROM inspection_reports"
DELETE_INSPECTIONS = "DELETE FROM inspections"


class HistoryStorageError(RuntimeError):
    """Raised when inspection history cannot be read or written."""


@dataclass(frozen=True)
class InspectionReportSource:
    """Persisted source image and finding needed to build an inspection report."""

    created_at: str
    original_image: bytes
    result: InspectionResult


class InspectionRepository:
    """Persist inspection history using a local SQLite database."""

    def __init__(self, database_path: Path) -> None:
        """Configure the SQLite database location."""
        self.database_path = database_path

    def save(self, result: InspectionResult, image: Image.Image, original_image: bytes) -> None:
        """Persist a result summary, original photo, and small preview image."""
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
                        original_image,
                        result.model_dump_json(),
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
                    report_available=bool(row["report_available"]),
                    report_source_available=bool(row["report_source_available"]),
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

    def get_report_source(self, inspection_id: str) -> InspectionReportSource | None:
        """Return persisted inspection inputs, or None when unavailable."""
        try:
            with self._connection() as connection:
                row = connection.execute(SELECT_REPORT_SOURCE, (inspection_id,)).fetchone()
            if row is None or row["original_image"] is None or row["result_json"] is None:
                return None
            return InspectionReportSource(
                created_at=row["created_at"],
                original_image=bytes(row["original_image"]),
                result=InspectionResult.model_validate_json(row["result_json"]),
            )
        except (OSError, sqlite3.Error, ValidationError) as exc:
            LOGGER.error("Inspection report source read failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection report source could not be loaded.") from exc

    def save_report(
        self,
        inspection_id: str,
        report_id: str,
        user_type: ReportUserType,
        transcript: str,
        report_text_json: str,
        pdf: bytes,
    ) -> None:
        """Persist a generated PDF and its validated source narrative."""
        try:
            created_at = datetime.now(UTC).isoformat()
            with self._connection() as connection:
                connection.execute(
                    INSERT_REPORT,
                    (
                        inspection_id,
                        report_id,
                        created_at,
                        user_type.value,
                        transcript,
                        report_text_json,
                        pdf,
                    ),
                )
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection report write failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection report could not be saved.") from exc

    def get_report_pdf(self, inspection_id: str) -> bytes | None:
        """Return a previously generated inspection report."""
        try:
            with self._connection() as connection:
                row = connection.execute(SELECT_REPORT_PDF, (inspection_id,)).fetchone()
            return bytes(row["pdf"]) if row is not None else None
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection report read failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection report could not be loaded.") from exc

    def clear_history(self) -> None:
        """Delete all saved inspection records and generated reports atomically."""
        try:
            with self._connection() as connection:
                connection.execute(DELETE_REPORTS)
                connection.execute(DELETE_INSPECTIONS)
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection history clear failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection history could not be cleared.") from exc

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Open a database connection and ensure the history table exists."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(CREATE_HISTORY_TABLE)
            _add_missing_history_columns(connection)
            connection.execute(CREATE_REPORTS_TABLE)
            with connection:
                yield connection


def _add_missing_history_columns(connection: sqlite3.Connection) -> None:
    """Migrate databases created before original report sources were stored."""
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(inspections)")}
    if "original_image" not in columns:
        connection.execute("ALTER TABLE inspections ADD COLUMN original_image BLOB")
    if "result_json" not in columns:
        connection.execute("ALTER TABLE inspections ADD COLUMN result_json TEXT")


def _encode_thumbnail(image: Image.Image) -> bytes:
    """Resize and JPEG-encode a private history preview."""
    thumbnail = image.copy()
    thumbnail.thumbnail(THUMBNAIL_SIZE)
    buffer = BytesIO()
    thumbnail.save(buffer, format="JPEG", quality=THUMBNAIL_QUALITY)
    return buffer.getvalue()
