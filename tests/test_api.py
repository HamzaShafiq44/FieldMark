"""HTTP API tests using deterministic fake produce detections."""

from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from app.ai.providers import DetectionError
from app.ai.schemas import Condition, Finding, InspectionResult, ProduceType, ReportUserType
from app.api import routes_inspection
from app.core.config import settings
from app.main import app
from app.services import inspection_service
from app.storage.inspection_repository import InspectionRepository

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_history_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep API history tests isolated from local application data."""
    monkeypatch.setattr(settings, "inspection_db_path", tmp_path / "inspections.sqlite3")


def _jpeg_bytes() -> bytes:
    """Create a small in-memory JPEG fixture."""
    buffer = BytesIO()
    Image.new("RGB", (16, 16), color="red").save(buffer, format="JPEG")
    return buffer.getvalue()


def _patch_detector(
    monkeypatch: pytest.MonkeyPatch,
    detector: Callable[[Image.Image], Finding],
) -> None:
    """Inject a detector into the route's inspection service call."""

    def inspect(data: bytes) -> InspectionResult:
        return inspection_service.inspect_produce(data, detect=detector)

    monkeypatch.setattr(routes_inspection, "inspect_produce", inspect)


def test_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return a graded inspection when an image upload is valid."""
    finding = Finding(
        produce_type=ProduceType.APPLE,
        condition=Condition.FRESH,
        confidence=0.9,
    )
    _patch_detector(monkeypatch, lambda _: finding)
    response = client.post("/inspections/produce", files={"image": ("apple.jpg", _jpeg_bytes())})
    assert response.status_code == 200
    assert response.json()["grade"] == "A"
    assert response.json()["produce_type"] == "apple"
    assert response.json()["confidence"] == 0.9
    assert all(
        "%" not in note and "threshold" not in note.lower()
        for note in response.json()["verdict_notes"]
    )
    history = client.get("/inspections/history")
    assert history.status_code == 200
    history_item = history.json()["items"][0]
    assert history_item["inspection_id"] == response.json()["inspection_id"]
    assert history_item["produce_type"] == "apple"
    assert history_item["verdict"] == "pass"
    assert history_item["report_source_available"] is True
    assert history_item["report_available"] is False
    thumbnail = client.get(history_item["thumbnail_url"])
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"] == "image/jpeg"
    assert thumbnail.content


def test_history_starts_empty_and_validates_pagination() -> None:
    """Return an empty list and reject invalid history pagination values."""
    response = client.get("/inspections/history")
    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert client.get("/inspections/history?limit=0").status_code == 422
    assert client.get("/inspections/history?offset=-1").status_code == 422


def test_clear_history_deletes_inspections_and_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clear saved inspection data and generated PDF reports."""
    finding = Finding(
        produce_type=ProduceType.APPLE,
        condition=Condition.FRESH,
        confidence=0.9,
    )
    _patch_detector(monkeypatch, lambda _: finding)
    inspection = client.post(
        "/inspections/produce",
        files={"image": ("apple.jpg", _jpeg_bytes())},
    ).json()
    inspection_id = inspection["inspection_id"]
    repository = InspectionRepository(settings.inspection_db_path)
    repository.save_report(
        inspection_id,
        "report-1",
        ReportUserType.FARMER,
        "",
        "{}",
        b"%PDF-test",
    )

    response = client.delete("/inspections/history")

    assert response.status_code == 204
    assert client.get("/inspections/history").json() == {"items": []}
    assert repository.get_report_pdf(inspection_id) is None
    assert repository.get_thumbnail(inspection_id) is None


def test_unknown_history_thumbnail_returns_404() -> None:
    """Return not found when a history thumbnail does not exist."""
    response = client.get("/inspections/history/00000000-0000-0000-0000-000000000000/thumbnail")
    assert response.status_code == 404


def test_tomato_route_remains_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the original tomato endpoint as a compatibility alias."""
    finding = Finding(
        produce_type=ProduceType.TOMATO,
        condition=Condition.FRESH,
        confidence=0.9,
    )
    _patch_detector(monkeypatch, lambda _: finding)
    response = client.post("/inspections/tomato", files={"image": ("tomato.jpg", _jpeg_bytes())})
    assert response.status_code == 200
    assert response.json()["produce_type"] == "tomato"


def test_non_image_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return a client error for an unreadable image upload."""
    _patch_detector(
        monkeypatch,
        lambda _: Finding(
            produce_type=ProduceType.TOMATO,
            condition=Condition.FRESH,
            confidence=0.9,
        ),
    )
    response = client.post("/inspections/produce", files={"image": ("not-image.jpg", b"no image")})
    assert response.status_code == 400


def test_oversized_upload_returns_413(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject uploads that exceed the configured size limit."""
    _patch_detector(
        monkeypatch,
        lambda _: Finding(
            produce_type=ProduceType.TOMATO,
            condition=Condition.FRESH,
            confidence=0.9,
        ),
    )
    oversized = b"x" * (settings.max_image_bytes + 1)
    response = client.post("/inspections/produce", files={"image": ("large.jpg", oversized)})
    assert response.status_code == 413


def test_detection_failure_returns_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return a gateway error when vision detection fails."""

    def fail_detection(_: Image.Image) -> Finding:
        raise DetectionError("Vision detection failed.")

    _patch_detector(monkeypatch, fail_detection)
    response = client.post("/inspections/produce", files={"image": ("tomato.jpg", _jpeg_bytes())})
    assert response.status_code == 502


def test_finding_bbox_and_defect_validation() -> None:
    """Clear invalid boxes and reject unknown defect labels."""
    finding = Finding.model_validate(
        {
            "produce_type": "tomato",
            "condition": "fresh",
            "confidence": 0.8,
            "bbox": [0.8, 0.1, 0.2, 0.9],
        }
    )
    assert finding.bbox is None
    with pytest.raises(ValidationError):
        Finding.model_validate(
            {
                "produce_type": "tomato",
                "condition": "fresh",
                "confidence": 0.8,
                "defect_types": ["unknown"],
            }
        )


def test_finding_limits_visual_observation_length() -> None:
    """Reject model observations that exceed the response length limit."""
    with pytest.raises(ValidationError):
        Finding.model_validate(
            {
                "produce_type": "apple",
                "condition": "fresh",
                "confidence": 0.9,
                "visual_observation": "x" * 241,
            }
        )


def test_generate_report_route_returns_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return generated report bytes as an attachment."""
    monkeypatch.setattr(
        routes_inspection,
        "generate_inspection_report",
        lambda *_: b"%PDF-test",
    )
    inspection_id = "00000000-0000-0000-0000-000000000001"

    response = client.post(
        f"/inspections/{inspection_id}/report",
        data={"user_type": "farmer"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.content == b"%PDF-test"


def test_report_route_rejects_unsupported_voice_note() -> None:
    """Reject voice-note formats that the transcription provider cannot accept."""
    response = client.post(
        "/inspections/00000000-0000-0000-0000-000000000001/report",
        data={"user_type": "farmer"},
        files={"voice_note": ("note.txt", b"audio", "text/plain")},
    )
    assert response.status_code == 400


def test_report_route_rejects_oversized_voice_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject audio uploads above the configured limit before transcription."""
    monkeypatch.setattr(settings, "max_voice_note_bytes", 3)
    response = client.post(
        "/inspections/00000000-0000-0000-0000-000000000001/report",
        data={"user_type": "farmer"},
        files={"voice_note": ("note.wav", b"audio", "audio/wav")},
    )
    assert response.status_code == 413


def test_download_report_returns_404_when_missing() -> None:
    """Return not found when no PDF has been generated for the inspection."""
    response = client.get(
        "/inspections/00000000-0000-0000-0000-000000000001/report",
    )
    assert response.status_code == 404


def test_generate_report_rejects_inspection_without_saved_source() -> None:
    """Explain that reports cannot be reconstructed without the original photo."""
    response = client.post(
        "/inspections/00000000-0000-0000-0000-000000000001/report",
        data={"user_type": "farmer"},
    )
    assert response.status_code == 409
    assert "original photo is unavailable" in response.json()["detail"]
