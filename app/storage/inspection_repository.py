"""SQLite repository for inspection metadata and on-disk image/report files."""

import logging
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from PIL import Image
from pydantic import ValidationError

from app.ai.schemas import (
    InspectionDetail,
    InspectionHistoryItem,
    InspectionReportDetail,
    InspectionReportText,
    InspectionResult,
    ReportUserType,
)

LOGGER = logging.getLogger(__name__)
THUMBNAIL_SIZE = (192, 192)
THUMBNAIL_QUALITY = 75
HISTORY_THUMBNAIL_PATH = "/inspections/history/{inspection_id}/thumbnail"
IMAGES_DIRNAME = "images"
REPORTS_DIRNAME = "reports"
CREATE_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS inspections (
    inspection_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    produce_type TEXT NOT NULL,
    detected_item TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL,
    grade TEXT NOT NULL,
    thumbnail_path TEXT,
    original_image_path TEXT,
    result_json TEXT
)
"""
CREATE_HISTORY_TABLE_REBUILD = """
CREATE TABLE inspections_new (
    inspection_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    produce_type TEXT NOT NULL,
    detected_item TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL,
    grade TEXT NOT NULL,
    thumbnail_path TEXT,
    original_image_path TEXT,
    result_json TEXT
)
"""
INSERT_HISTORY_ITEM = """
INSERT INTO inspections (
    inspection_id, created_at, produce_type, detected_item, verdict, grade,
    thumbnail_path, original_image_path, result_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
INSERT_HISTORY_ITEM_REBUILD = """
INSERT INTO inspections_new (
    inspection_id, created_at, produce_type, detected_item, verdict, grade,
    thumbnail_path, original_image_path, result_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
SELECT_HISTORY_ITEMS = """
SELECT inspection_id, created_at, produce_type, detected_item, verdict, grade, result_json,
       EXISTS(
           SELECT 1 FROM inspection_reports
           WHERE inspection_reports.inspection_id = inspections.inspection_id
       ) AS report_available,
       result_json IS NOT NULL AS report_source_available
FROM inspections
ORDER BY created_at DESC, inspection_id DESC
LIMIT ? OFFSET ?
"""
SELECT_THUMBNAIL_PATH = "SELECT thumbnail_path FROM inspections WHERE inspection_id = ?"
SELECT_ORIGINAL_PATH = "SELECT original_image_path FROM inspections WHERE inspection_id = ?"
UPDATE_RESULT_JSON = "UPDATE inspections SET result_json = ? WHERE inspection_id = ?"
SELECT_REPORT_SOURCE = """
SELECT created_at, original_image_path, result_json
FROM inspections
WHERE inspection_id = ?
"""
SELECT_INSPECTION_DETAIL = "SELECT created_at, result_json FROM inspections WHERE inspection_id = ?"
CREATE_REPORTS_TABLE = """
CREATE TABLE IF NOT EXISTS inspection_reports (
    inspection_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    user_type TEXT NOT NULL,
    transcript TEXT NOT NULL,
    report_text_json TEXT NOT NULL
)
"""
CREATE_REPORTS_TABLE_REBUILD = """
CREATE TABLE inspection_reports_new (
    inspection_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    user_type TEXT NOT NULL,
    transcript TEXT NOT NULL,
    report_text_json TEXT NOT NULL
)
"""
INSERT_REPORT = """
INSERT INTO inspection_reports (
    inspection_id, report_id, created_at, user_type, transcript, report_text_json
) VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(inspection_id) DO UPDATE SET
    report_id = excluded.report_id,
    created_at = excluded.created_at,
    user_type = excluded.user_type,
    transcript = excluded.transcript,
    report_text_json = excluded.report_text_json
"""
INSERT_REPORT_REBUILD = """
INSERT INTO inspection_reports_new (
    inspection_id, report_id, created_at, user_type, transcript, report_text_json
) VALUES (?, ?, ?, ?, ?, ?)
"""
SELECT_REPORT_DATA = """
SELECT report_id, created_at, user_type, transcript, report_text_json
FROM inspection_reports WHERE inspection_id = ?
"""
DELETE_REPORTS = "DELETE FROM inspection_reports"
DELETE_INSPECTIONS = "DELETE FROM inspections"


class HistoryStorageError(RuntimeError):
    """Raised when inspection history cannot be read or written."""


@dataclass(frozen=True)
class InspectionReportSource:
    """Persisted evidence and finding needed to build an inspection report.

    ``original_image`` is None for voice-only inspections, which have no photo.
    """

    created_at: str
    original_image: bytes | None
    result: InspectionResult


@dataclass(frozen=True)
class InspectionReportData:
    """Persisted narrative used to rebuild a report PDF on demand."""

    report_id: str
    created_at: str
    user_type: str
    transcript: str
    report_text_json: str


class InspectionRepository:
    """Persist inspection history using a local SQLite database."""

    def __init__(self, database_path: Path) -> None:
        """Configure the SQLite database location."""
        self.database_path = database_path

    def save(self, result: InspectionResult, image: Image.Image, original_image: bytes) -> None:
        """Persist metadata and write the original photo and thumbnail to disk."""
        images_dir = self.database_path.parent / IMAGES_DIRNAME
        original_path = images_dir / f"{result.inspection_id}_original.bin"
        thumbnail_path = images_dir / f"{result.inspection_id}_thumbnail.jpg"
        try:
            images_dir.mkdir(parents=True, exist_ok=True)
            thumbnail = _encode_thumbnail(image)
            original_path.write_bytes(original_image)
            thumbnail_path.write_bytes(thumbnail)
            created_at = datetime.now(UTC).isoformat()
            with self._connection() as connection:
                connection.execute(
                    INSERT_HISTORY_ITEM,
                    (
                        result.inspection_id,
                        created_at,
                        result.produce_type,
                        result.detected_item,
                        result.verdict,
                        result.grade,
                        _stored_path(self.database_path.parent, thumbnail_path),
                        _stored_path(self.database_path.parent, original_path),
                        result.model_dump_json(),
                    ),
                )
        except (OSError, sqlite3.Error) as exc:
            original_path.unlink(missing_ok=True)
            thumbnail_path.unlink(missing_ok=True)
            LOGGER.error("Inspection history write failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection history could not be saved.") from exc

    def save_voice_inspection(self, result: InspectionResult) -> None:
        """Persist a voice‑note‑only inspection result.

        No image files are saved; thumbnail_path and original_image_path are left NULL.
        """
        created_at = datetime.now(UTC).isoformat()
        try:
            with self._connection() as connection:
                connection.execute(
                    INSERT_HISTORY_ITEM,
                    (
                        result.inspection_id,
                        created_at,
                        result.produce_type,
                        result.detected_item,
                        result.verdict,
                        result.grade,
                        None,  # thumbnail_path
                        None,  # original_image_path
                        result.model_dump_json(),
                    ),
                )
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Voice‑only inspection history write failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection history could not be saved.") from exc

    def get_thumbnail(self, inspection_id: str) -> bytes | None:
        """Return a stored thumbnail, or None when the inspection does not exist."""
        try:
            with self._connection() as connection:
                row = connection.execute(SELECT_THUMBNAIL_PATH, (inspection_id,)).fetchone()
            if row is None:
                return None
            path = _resolve_path(self.database_path.parent, row["thumbnail_path"])
            return path.read_bytes() if path is not None and path.is_file() else None
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection thumbnail read failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection thumbnail could not be loaded.") from exc

    def get_original(self, inspection_id: str) -> bytes | None:
        """Return the stored original photo, or None when it is unavailable."""
        try:
            with self._connection() as connection:
                row = connection.execute(SELECT_ORIGINAL_PATH, (inspection_id,)).fetchone()
            if row is None:
                return None
            path = _resolve_path(self.database_path.parent, row["original_image_path"])
            return path.read_bytes() if path is not None and path.is_file() else None
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection photo read failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection photo could not be loaded.") from exc

    def save_annotated(self, inspection_id: str, photo: bytes) -> None:
        """Persist an annotated preview alongside the original photo."""
        path = self.database_path.parent / IMAGES_DIRNAME / f"{inspection_id}_annotated.jpg"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(photo)
        except OSError as exc:
            LOGGER.error("Annotated photo write failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Annotated photo could not be saved.") from exc

    def get_annotated(self, inspection_id: str) -> bytes | None:
        """Return a stored annotated photo, or None when it is unavailable."""
        path = self.database_path.parent / IMAGES_DIRNAME / f"{inspection_id}_annotated.jpg"
        try:
            return path.read_bytes() if path.is_file() else None
        except OSError as exc:
            LOGGER.error("Annotated photo read failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Annotated photo could not be loaded.") from exc

    def update_result(self, inspection_id: str, result_json: str) -> bool:
        """Replace the stored inspection result payload."""
        try:
            with self._connection() as connection:
                cursor = connection.execute(UPDATE_RESULT_JSON, (result_json, inspection_id))
            return cursor.rowcount > 0
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection result update failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection result could not be updated.") from exc

    def get_report_source(self, inspection_id: str) -> InspectionReportSource | None:
        """Return persisted inspection inputs, or None when unavailable.

        Voice-only inspections have no image path and are still valid report
        sources. Photo inspections with a missing original file stay unavailable.
        """
        try:
            with self._connection() as connection:
                row = connection.execute(SELECT_REPORT_SOURCE, (inspection_id,)).fetchone()
            if row is None or row["result_json"] is None:
                return None
            original_image: bytes | None = None
            stored_path = row["original_image_path"]
            if stored_path is not None:
                original_path = _resolve_path(self.database_path.parent, stored_path)
                if original_path is None or not original_path.is_file():
                    return None
                original_image = original_path.read_bytes()
            return InspectionReportSource(
                created_at=row["created_at"],
                original_image=original_image,
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
    ) -> None:
        """Persist only the report narrative; PDFs are rebuilt on demand."""
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
                    ),
                )
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection report write failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection report could not be saved.") from exc

    def list_history(self, limit: int, offset: int) -> list[InspectionHistoryItem]:
        """Return recent inspections in reverse chronological order.

        Parameters:
            limit: maximum number of items to return.
            offset: number of items to skip.
        """
        try:
            with self._connection() as connection:
                rows = connection.execute(SELECT_HISTORY_ITEMS, (limit, offset)).fetchall()
            return [_history_item(row) for row in rows]
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection history read failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection history could not be loaded.") from exc

    def get_report_data(self, inspection_id: str) -> InspectionReportData | None:
        """Return the stored narrative used to rebuild a report PDF.

        Parameters:
            inspection_id: The ID of the inspection.
        """
        try:
            with self._connection() as connection:
                row = connection.execute(SELECT_REPORT_DATA, (inspection_id,)).fetchone()
            if row is None:
                return None
            return InspectionReportData(
                report_id=str(row["report_id"]),
                created_at=str(row["created_at"]),
                user_type=str(row["user_type"]),
                transcript=str(row["transcript"]),
                report_text_json=str(row["report_text_json"]),
            )
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection report read failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection report could not be loaded.") from exc

    def get_detail(self, inspection_id: str) -> InspectionDetail | None:
        """Return the full stored result and report narrative for one inspection.

        A report with an unreadable narrative is treated as unavailable so the
        review view can still show the inspection result.
        """
        try:
            with self._connection() as connection:
                row = connection.execute(SELECT_INSPECTION_DETAIL, (inspection_id,)).fetchone()
                if row is None:
                    return None
                report_row = connection.execute(SELECT_REPORT_DATA, (inspection_id,)).fetchone()
            result = _stored_result(_row_text(row, "result_json"))
            if result is None:
                return None
            report: InspectionReportDetail | None = None
            if report_row is not None:
                try:
                    report_text = InspectionReportText.model_validate_json(
                        str(report_row["report_text_json"])
                    )
                except ValidationError:
                    LOGGER.warning("Ignoring an unreadable stored inspection report narrative.")
                else:
                    report = InspectionReportDetail(
                        report_id=str(report_row["report_id"]),
                        created_at=str(report_row["created_at"]),
                        user_type=str(report_row["user_type"]),
                        transcript=str(report_row["transcript"]),
                        report_text=report_text,
                    )
            return InspectionDetail(
                inspection_id=inspection_id,
                created_at=str(row["created_at"]),
                result=result,
                report=report,
            )
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection detail read failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection detail could not be loaded.") from exc

    def clear_history(self) -> None:
        """Delete all saved inspection records, images, and generated reports."""
        try:
            with self._connection() as connection:
                connection.execute(DELETE_REPORTS)
                connection.execute(DELETE_INSPECTIONS)
            for dirname in (IMAGES_DIRNAME, REPORTS_DIRNAME):
                directory = self.database_path.parent / dirname
                if directory.exists():
                    shutil.rmtree(directory)
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("Inspection history clear failed (%s).", type(exc).__name__)
            raise HistoryStorageError("Inspection history could not be cleared.") from exc

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Open a connection and migrate any pre-file-storage schema."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                connection.execute(CREATE_HISTORY_TABLE)
                # Backfill before the legacy BLOB rebuild so it copies real values.
                detected_item_migrated = _migrate_detected_item_schema(connection)
                migrated = _migrate_history_schema(connection, self.database_path.parent)
                connection.execute(CREATE_REPORTS_TABLE)
                reports_migrated = _migrate_reports_schema(connection)
            if migrated or detected_item_migrated or reports_migrated:
                try:
                    connection.execute("VACUUM")
                except sqlite3.Error as exc:
                    LOGGER.warning(
                        "SQLite VACUUM after image migration failed (%s).",
                        type(exc).__name__,
                    )
            with connection:
                yield connection


def _history_item(row: sqlite3.Row) -> InspectionHistoryItem:
    """Build a public history item, tolerating older stored results."""
    result = _stored_result(row["result_json"])
    return InspectionHistoryItem(
        inspection_id=row["inspection_id"],
        created_at=row["created_at"],
        produce_type=row["produce_type"],
        detected_item=_row_text(row, "detected_item") or (result.detected_item if result else ""),
        item_is_fruit=result.item_is_fruit if result else False,
        in_inspection_categories=result.in_inspection_categories if result else True,
        voice_note_transcript=result.voice_note_transcript if result else None,
        verdict=row["verdict"],
        grade=row["grade"],
        thumbnail_url=HISTORY_THUMBNAIL_PATH.format(inspection_id=row["inspection_id"]),
        report_available=bool(row["report_available"]),
        report_source_available=bool(row["report_source_available"]),
    )


def _stored_result(raw: str | None) -> InspectionResult | None:
    """Parse a stored result, returning None when older data cannot be read."""
    if not raw:
        return None
    try:
        return InspectionResult.model_validate_json(raw)
    except ValidationError:
        LOGGER.warning("Ignoring an unreadable stored inspection result.")
        return None


def _migrate_detected_item_schema(connection: sqlite3.Connection) -> bool:
    """Add the detected_item column and backfill it from stored results."""
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(inspections)")}
    if "detected_item" in columns:
        return False

    LOGGER.info("Adding detected_item to inspection history.")
    connection.execute("ALTER TABLE inspections ADD COLUMN detected_item TEXT NOT NULL DEFAULT ''")
    # Very old schemas have no result_json yet; their BLOB rebuild will copy
    # the empty column and the result itself is unavailable for backfilling.
    if "result_json" not in columns:
        return True
    rows = connection.execute("SELECT inspection_id, result_json FROM inspections").fetchall()
    for row in rows:
        result = _stored_result(row["result_json"])
        if result is not None and result.detected_item:
            connection.execute(
                "UPDATE inspections SET detected_item = ? WHERE inspection_id = ?",
                (result.detected_item, row["inspection_id"]),
            )
    return True


def _migrate_history_schema(connection: sqlite3.Connection, database_dir: Path) -> bool:
    """Move legacy image BLOBs to files and replace the old table schema."""
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(inspections)")}
    if "thumbnail" not in columns and "original_image" not in columns:
        return False

    LOGGER.info("Migrating legacy inspection images out of SQLite.")
    images_dir = database_dir / IMAGES_DIRNAME
    images_dir.mkdir(parents=True, exist_ok=True)
    rows = connection.execute("SELECT * FROM inspections").fetchall()
    connection.execute("DROP TABLE IF EXISTS inspections_new")
    connection.execute(CREATE_HISTORY_TABLE_REBUILD)

    for row in rows:
        inspection_id = row["inspection_id"]
        thumbnail_path = _row_text(row, "thumbnail_path")
        original_image_path = _row_text(row, "original_image_path")

        if thumbnail_path is None and "thumbnail" in columns:
            thumbnail = row["thumbnail"]
            if thumbnail is not None:
                path = images_dir / f"{inspection_id}_thumbnail.jpg"
                path.write_bytes(bytes(thumbnail))
                thumbnail_path = _stored_path(database_dir, path)

        if original_image_path is None and "original_image" in columns:
            original_image = row["original_image"]
            if original_image is not None:
                path = images_dir / f"{inspection_id}_original.bin"
                path.write_bytes(bytes(original_image))
                original_image_path = _stored_path(database_dir, path)

        connection.execute(
            INSERT_HISTORY_ITEM_REBUILD,
            (
                inspection_id,
                row["created_at"],
                row["produce_type"],
                _row_text(row, "detected_item") or "",
                row["verdict"],
                row["grade"],
                thumbnail_path,
                original_image_path,
                _row_text(row, "result_json"),
            ),
        )

    connection.execute("DROP TABLE inspections")
    connection.execute("ALTER TABLE inspections_new RENAME TO inspections")
    return True


def _migrate_reports_schema(connection: sqlite3.Connection) -> bool:
    """Drop the legacy pdf_path column from the reports table."""
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(inspection_reports)")}
    if "pdf_path" not in columns:
        return False

    LOGGER.info("Migrating inspection reports to drop stored PDF paths.")
    rows = connection.execute(
        "SELECT inspection_id, report_id, created_at, user_type, transcript, report_text_json "
        "FROM inspection_reports"
    ).fetchall()
    connection.execute("DROP TABLE IF EXISTS inspection_reports_new")
    connection.execute(CREATE_REPORTS_TABLE_REBUILD)
    for row in rows:
        connection.execute(
            INSERT_REPORT_REBUILD,
            (
                row["inspection_id"],
                row["report_id"],
                row["created_at"],
                row["user_type"],
                row["transcript"],
                row["report_text_json"],
            ),
        )
    connection.execute("DROP TABLE inspection_reports")
    connection.execute("ALTER TABLE inspection_reports_new RENAME TO inspection_reports")
    return True


def _row_text(row: sqlite3.Row, name: str) -> str | None:
    """Read an optional text column from a row that may predate that column."""
    if name not in row.keys():
        return None
    value = row[name]
    return value if isinstance(value, str) else None


def _stored_path(base: Path, path: Path) -> str:
    """Store paths relative to the data directory so the database stays portable."""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _resolve_path(base: Path, stored: str | None) -> Path | None:
    """Resolve a stored path, accepting both relative and legacy absolute values."""
    if not stored:
        return None
    path = Path(stored)
    return path if path.is_absolute() else base / path


def _encode_thumbnail(image: Image.Image) -> bytes:
    """Resize and JPEG-encode a private history preview."""
    thumbnail = image.copy()
    thumbnail.thumbnail(THUMBNAIL_SIZE)
    buffer = BytesIO()
    thumbnail.save(buffer, format="JPEG", quality=THUMBNAIL_QUALITY)
    return buffer.getvalue()
