"""In-memory registry and worker for asynchronous inspection jobs.

Jobs survive page navigation and browser refresh because their state lives in
the API process, not in the browser. They are intentionally not durable across
server restarts, matching the single-instance MVP design.
"""

import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.ai.model_info import describe_models
from app.ai.providers import DetectionError
from app.ai.schemas import InspectionJob, JobKind, JobStatus
from app.core.config import settings
from app.services.inspection_service import (
    InvalidImageError,
    finalize_inspection,
    inspect_from_voice,
    inspect_produce,
)
from app.storage.inspection_repository import InspectionRepository

LOGGER = logging.getLogger(__name__)
MAX_RECORDED_JOBS = 50
MAX_CONCURRENT_JOBS = 2
MAX_CONCURRENT_REPORTS = 1

_registry: dict[str, InspectionJob] = {}
_lock = threading.RLock()
_slots = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)
_report_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REPORTS)


def uploads_dir() -> Path:
    """Return the directory holding raw uploads for in-flight jobs."""
    return settings.inspection_db_path.parent / "uploads"


def create_job(kind: JobKind, data: bytes, filename: str = "") -> InspectionJob:
    """Store an upload, register a job, and return its initial status."""
    job_id = str(uuid4())
    directory = uploads_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{job_id}.bin").write_bytes(data)
    job = InspectionJob(
        job_id=job_id,
        kind=kind,
        status=JobStatus.QUEUED,
        stage="queued",
        created_at=datetime.now(UTC).isoformat(),
        inspection_id=str(uuid4()),
        filename=filename,
    )
    with _lock:
        _registry[job_id] = job
        _trim_registry()
    return job


def get_job(job_id: str) -> InspectionJob | None:
    """Return one job, or None when it is unknown or has been discarded."""
    with _lock:
        return _registry.get(job_id)


def list_jobs() -> list[InspectionJob]:
    """Return recorded jobs, newest first."""
    with _lock:
        return sorted(_registry.values(), key=lambda job: job.created_at, reverse=True)


def clear_jobs() -> None:
    """Drop all job records (used by tests and at startup)."""
    with _lock:
        _registry.clear()


def cleanup_uploads() -> None:
    """Remove leftover upload files; no live job can own one at startup."""
    directory = uploads_dir()
    if not directory.is_dir():
        return
    for path in directory.glob("*.bin"):
        try:
            path.unlink()
        except OSError as exc:
            LOGGER.warning("Could not remove stale upload (%s).", type(exc).__name__)


def process_job(job_id: str) -> None:
    """Run the analysis, then finish the report without holding up the verdict."""
    job = get_job(job_id)
    if job is None:
        return
    LOGGER.info("Job %s: starting with %s", job_id, describe_models())
    started = time.monotonic()
    try:
        with _slots:
            _update(job_id, status=JobStatus.PROCESSING, stage="validating")
            data = _read_upload(job_id)
            _update(job_id, stage="analyzing")
            if job.kind is JobKind.VOICE:
                result = inspect_from_voice(
                    data, job.filename or "voice-note.webm", inspection_id=job.inspection_id
                )
            else:
                result = inspect_produce(data, inspection_id=job.inspection_id)
        # Release the analysis slot before the (much slower) report work so a
        # stalled report cannot keep the next upload in the queue.
        LOGGER.info("Job %s: verdict ready in %.2fs.", job_id, time.monotonic() - started)
        _update(job_id, status=JobStatus.COMPLETED, stage="report", result=result)
        with _report_slots:
            _finish_report(job_id, result.inspection_id)
        LOGGER.info("Job %s: all work finished in %.2fs.", job_id, time.monotonic() - started)
    except Exception as exc:  # a failed job never takes down the worker pool
        LOGGER.exception("Inspection job %s failed.", job_id)
        _update(job_id, status=JobStatus.FAILED, stage="failed", error=_safe_error(exc))
    finally:
        _delete_upload(job_id)


def _finish_report(job_id: str, inspection_id: str) -> None:
    """Generate the report narrative; never turn a delivered verdict into a failed job."""
    started = time.monotonic()
    try:
        finalize_inspection(inspection_id)
        has_report = (
            InspectionRepository(settings.inspection_db_path).get_report_data(inspection_id)
            is not None
        )
        current = get_job(job_id)
        result = current.result if current and current.result else None
        LOGGER.info("Job %s: report ready in %.2fs.", job_id, time.monotonic() - started)
        _update(
            job_id,
            stage="done",
            report_ready=has_report,
            result=result.model_copy(update={"report_available": has_report}) if result else None,
        )
    except Exception:
        LOGGER.exception("Job %s: reporting failed after the verdict was delivered.", job_id)
        _update(job_id, stage="done", report_ready=False)


def _update(job_id: str, **fields: object) -> None:
    with _lock:
        current = _registry.get(job_id)
        if current is not None:
            _registry[job_id] = current.model_copy(update=fields)


def _trim_registry() -> None:
    if len(_registry) <= MAX_RECORDED_JOBS:
        return
    ordered = sorted(_registry.values(), key=lambda job: job.created_at)
    for old in ordered[: len(_registry) - MAX_RECORDED_JOBS]:
        _registry.pop(old.job_id, None)


def _upload_path(job_id: str) -> Path:
    return uploads_dir() / f"{job_id}.bin"


def _read_upload(job_id: str) -> bytes:
    return _upload_path(job_id).read_bytes()


def _delete_upload(job_id: str) -> None:
    try:
        _upload_path(job_id).unlink(missing_ok=True)
    except OSError as exc:
        LOGGER.warning("Could not remove job upload (%s).", type(exc).__name__)


def _safe_error(exc: Exception) -> str:
    """Expose an actionable message without leaking keys or personal data."""
    if isinstance(exc, InvalidImageError):
        return "The uploaded image could not be read."
    if isinstance(exc, DetectionError):
        return "The vision service could not complete this inspection."
    return f"Inspection failed ({type(exc).__name__})."
