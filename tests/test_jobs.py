"""Tests for asynchronous, background inspection jobs."""

import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.ai.providers import DetectionError, QuotaExhaustedError
from app.ai.schemas import InspectionResult, JobKind, JobStatus, Severity
from app.core.config import settings
from app.main import app
from app.services import job_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_job_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep job tests isolated from local application data."""
    monkeypatch.setattr(settings, "inspection_db_path", tmp_path / "inspections.sqlite3")


def _jpeg_bytes() -> bytes:
    """Create a small in-memory JPEG fixture."""
    buffer = BytesIO()
    Image.new("RGB", (16, 16), color="red").save(buffer, format="JPEG")
    return buffer.getvalue()


def _result(inspection_id: str) -> InspectionResult:
    """Build a completed result linked to a job."""
    return InspectionResult(
        inspection_id=inspection_id,
        produce_type="apple",
        detected_item="Apple",
        item_is_fruit=True,
        in_inspection_categories=True,
        verdict="pass",
        confidence=0.9,
        bbox=None,
        defect_types=[],
        severity=Severity.NONE,
        grade="A",
        verdict_notes=[],
        recommended_action="Sell as premium product.",
    )


def _fake_inspect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the image worker with an offline deterministic function."""
    monkeypatch.setattr(
        job_service, "inspect_produce", lambda _data, **kwargs: _result(kwargs["inspection_id"])
    )


def _await_job(job_id: str) -> dict[str, Any]:
    """Wait for a background worker to finish both the verdict and the report."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/inspections/jobs/{job_id}")
        if response.status_code == 200 and response.json()["stage"] in ("done", "failed"):
            return response.json()
        time.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not finish")


def test_submit_returns_job_id_before_worker_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return immediately with a queued job instead of waiting for the analysis."""
    seen: list[str] = []
    monkeypatch.setattr(job_service, "process_job", lambda job_id: seen.append(job_id))

    response = client.post("/inspections/jobs", files={"image": ("apple.jpg", _jpeg_bytes())})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["kind"] == "image"
    assert seen == [body["job_id"]]


def test_image_job_completes_with_linked_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the worker and expose its result on the job record."""
    _fake_inspect(monkeypatch)

    response = client.post("/inspections/jobs", files={"image": ("apple.jpg", _jpeg_bytes())})

    assert response.status_code == 202
    body = _await_job(response.json()["job_id"])
    assert body["status"] == "completed"
    assert body["stage"] == "done"
    assert body["inspection_id"] == body["result"]["inspection_id"]
    assert body["result"]["grade"] == "A"


def test_report_failure_keeps_the_delivered_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reporting happens after completion and cannot fail a finished verdict."""
    job = job_service.create_job(JobKind.IMAGE, _jpeg_bytes())
    job_service._update(
        job.job_id,
        status=JobStatus.COMPLETED,
        stage="report",
        result=_result(job.inspection_id or ""),
    )

    def fail_report(_inspection_id: str) -> None:
        raise RuntimeError("report service down")

    monkeypatch.setattr(job_service, "finalize_inspection", fail_report)
    job_service._finish_report(job.job_id, job.inspection_id or "")

    finished = job_service.get_job(job.job_id)
    assert finished is not None
    assert finished.status is JobStatus.COMPLETED
    assert finished.stage == "done"
    assert finished.report_ready is False
    assert finished.result is not None
    assert finished.result.grade == "A"


def test_reporting_does_not_hold_the_analysis_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow report must leave the analysis semaphore free for new uploads."""
    _fake_inspect(monkeypatch)
    monkeypatch.setattr(job_service, "_slots", threading.BoundedSemaphore(1))
    checks: list[bool] = []

    def check_slot(_inspection_id: str) -> None:
        acquired = job_service._slots.acquire(blocking=False)
        checks.append(acquired)
        if acquired:
            job_service._slots.release()

    monkeypatch.setattr(job_service, "finalize_inspection", check_slot)
    job_id = client.post("/inspections/jobs", files={"image": ("apple.jpg", _jpeg_bytes())}).json()[
        "job_id"
    ]

    assert _await_job(job_id)["stage"] == "done"
    assert checks == [True]


def test_voice_job_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Process a voice note through the same job pipeline."""
    monkeypatch.setattr(
        job_service,
        "inspect_from_voice",
        lambda _data, _filename, **kwargs: _result(kwargs["inspection_id"]),
    )

    response = client.post(
        "/inspections/jobs",
        files={"voice_note": ("note.m4a", b"audio", "audio/m4a")},
    )

    assert response.status_code == 202
    assert response.json()["kind"] == "voice"
    assert _await_job(response.json()["job_id"])["status"] == "completed"


def test_job_status_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read one job and the recent job list from any page of the app."""
    _fake_inspect(monkeypatch)
    job_id = client.post("/inspections/jobs", files={"image": ("apple.jpg", _jpeg_bytes())}).json()[
        "job_id"
    ]

    _await_job(job_id)
    single = client.get(f"/inspections/jobs/{job_id}")
    assert single.status_code == 200
    assert single.json()["status"] == "completed"

    listed = client.get("/inspections/jobs")
    assert [item["job_id"] for item in listed.json()] == [job_id]

    missing = client.get("/inspections/jobs/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


def test_failed_job_records_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a failure on the job record instead of breaking the worker."""

    def fail(*_args: object, **_kwargs: object) -> InspectionResult:
        raise DetectionError("vision unavailable")

    monkeypatch.setattr(job_service, "inspect_produce", fail)
    job_id = client.post("/inspections/jobs", files={"image": ("apple.jpg", _jpeg_bytes())}).json()[
        "job_id"
    ]
    body = _await_job(job_id)
    assert body["status"] == "failed"
    assert body["stage"] == "failed"
    assert "vision service" in body["error"]


def test_uploaded_file_is_removed_after_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not leave raw uploads behind once a job finishes or fails."""
    _fake_inspect(monkeypatch)
    job_id = client.post("/inspections/jobs", files={"image": ("apple.jpg", _jpeg_bytes())}).json()[
        "job_id"
    ]
    _await_job(job_id)
    assert not (job_service.uploads_dir() / f"{job_id}.bin").exists()


def test_quota_error_is_surfaced_to_the_user() -> None:
    """The job error carries the actionable quota message."""
    message = "Gemini daily quota exhausted. Enable billing or set VISION_PROVIDER=groq."
    assert job_service._safe_error(QuotaExhaustedError(message)) == message


def test_job_submission_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply the same file guards as the synchronous inspection route."""
    assert client.post("/inspections/jobs").status_code == 400
    both = client.post(
        "/inspections/jobs",
        files={
            "image": ("apple.jpg", _jpeg_bytes()),
            "voice_note": ("note.wav", b"audio", "audio/wav"),
        },
    )
    assert both.status_code == 400

    monkeypatch.setattr(settings, "max_image_bytes", 4)
    oversized = client.post("/inspections/jobs", files={"image": ("big.jpg", b"12345")})
    assert oversized.status_code == 413
