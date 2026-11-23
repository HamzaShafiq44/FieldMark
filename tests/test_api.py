"""HTTP API tests using deterministic fake produce detections."""

import sqlite3
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from app.ai.providers import DetectionError
from app.ai.schemas import (
    Condition,
    DefectType,
    Finding,
    InspectionResult,
    ProduceType,
    ReportUserType,
    Severity,
)
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


def test_cors_allows_frontend_origin() -> None:
    """Allow the separately hosted SPA to call the API."""
    response = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


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
    response = client.post("/inspections/product", files={"image": ("apple.jpg", _jpeg_bytes())})
    assert response.status_code == 200
    assert response.json()["grade"] == "A"
    assert response.json()["produce_type"] == "apple"
    assert response.json()["detected_item"] == ""
    assert response.json()["in_inspection_categories"] is True
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


def test_inspection_schedules_background_finalization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return the verdict immediately and defer refinement and reporting."""
    finalized: list[str] = []
    monkeypatch.setattr(
        routes_inspection,
        "finalize_inspection",
        lambda inspection_id: finalized.append(inspection_id),
    )
    finding = Finding(produce_type=ProduceType.APPLE, condition=Condition.FRESH, confidence=0.9)
    _patch_detector(monkeypatch, lambda _: finding)

    response = client.post("/inspections/product", files={"image": ("apple.jpg", _jpeg_bytes())})

    assert response.status_code == 200
    assert response.json()["report_available"] is False
    assert finalized == [response.json()["inspection_id"]]


def test_voice_inspection_schedules_background_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return the voice verdict immediately and defer PDF generation."""
    finalized: list[str] = []
    monkeypatch.setattr(
        routes_inspection,
        "finalize_inspection",
        lambda inspection_id: finalized.append(inspection_id),
    )
    result = _voice_inspection_result("A soft spot on the tomato")
    monkeypatch.setattr(routes_inspection, "inspect_from_voice", lambda *_: result)

    response = client.post(
        "/inspections/product",
        files={"voice_note": ("note.m4a", b"audio", "audio/m4a")},
    )

    assert response.status_code == 200
    assert response.json()["report_available"] is False
    assert finalized == [result.inspection_id]


def test_detected_item_column_is_backfilled_from_result_json() -> None:
    """Upgrade older databases and recover product names from stored results."""
    database_path = settings.inspection_db_path
    result = _voice_inspection_result("A soft spot on the tomato").model_copy(
        update={"detected_item": "peach"}
    )
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE inspections (
                inspection_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                produce_type TEXT NOT NULL,
                verdict TEXT NOT NULL,
                grade TEXT NOT NULL,
                thumbnail_path TEXT,
                original_image_path TEXT,
                result_json TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO inspections (
                inspection_id, created_at, produce_type, verdict, grade,
                thumbnail_path, original_image_path, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.inspection_id,
                "2026-01-01T00:00:00+00:00",
                result.produce_type,
                result.verdict,
                result.grade,
                None,
                None,
                result.model_dump_json(),
            ),
        )

    item = InspectionRepository(database_path).list_history(10, 0)[0]
    assert item.detected_item == "peach"
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(inspections)")}
        assert "detected_item" in columns
        assert connection.execute("SELECT detected_item FROM inspections").fetchone()[0] == "peach"


def test_history_starts_empty_and_validates_pagination() -> None:
    """Return an empty list and reject invalid history pagination values."""
    response = client.get("/inspections/history")
    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert client.get("/inspections/history?limit=0").status_code == 422
    assert client.get("/inspections/history?offset=-1").status_code == 422


def test_images_are_saved_to_files_not_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep original photos and thumbnails outside the SQLite database."""
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
    data_dir = settings.inspection_db_path.parent

    assert (data_dir / "images" / f"{inspection_id}_original.bin").is_file()
    assert (data_dir / "images" / f"{inspection_id}_thumbnail.jpg").is_file()
    with sqlite3.connect(settings.inspection_db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(inspections)")}
    assert "thumbnail" not in columns
    assert "original_image" not in columns
    assert "thumbnail_path" in columns
    assert "original_image_path" in columns
    assert "detected_item" in columns


def test_out_of_category_detected_item_is_persisted_in_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the real product name in SQL when produce_type is normalized to unknown."""
    finding = Finding(
        produce_type=ProduceType.UNKNOWN,
        detected_item="grapes",
        item_is_fruit=True,
        condition=Condition.FRESH,
        confidence=0.9,
    )
    _patch_detector(monkeypatch, lambda _: finding)

    response = client.post("/inspections/product", files={"image": ("grapes.jpg", _jpeg_bytes())})

    with sqlite3.connect(settings.inspection_db_path) as connection:
        row = connection.execute(
            "SELECT produce_type, detected_item FROM inspections WHERE inspection_id = ?",
            (response.json()["inspection_id"],),
        ).fetchone()
    assert row == ("unknown", "grapes")
    history_item = client.get("/inspections/history").json()["items"][0]
    assert history_item["detected_item"] == "grapes"
    assert history_item["in_inspection_categories"] is False


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
    )

    response = client.delete("/inspections/history")

    assert response.status_code == 204
    assert client.get("/inspections/history").json() == {"items": []}
    assert repository.get_report_data(inspection_id) is None
    assert repository.get_thumbnail(inspection_id) is None


def test_unknown_history_thumbnail_returns_404() -> None:
    """Return not found when a history thumbnail does not exist."""
    response = client.get("/inspections/history/00000000-0000-0000-0000-000000000000/thumbnail")
    assert response.status_code == 404


def test_original_and_annotated_photo_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve the original photo for any inspection and an annotated one only for defects."""
    clean = Finding(produce_type=ProduceType.APPLE, condition=Condition.FRESH, confidence=0.9)
    _patch_detector(monkeypatch, lambda _: clean)
    clean_id = client.post(
        "/inspections/product", files={"image": ("apple.jpg", _jpeg_bytes())}
    ).json()["inspection_id"]

    original = client.get(f"/inspections/history/{clean_id}/original")
    assert original.status_code == 200
    assert original.headers["content-type"] == "image/jpeg"
    assert original.content.startswith(b"\xff\xd8")
    assert client.get(f"/inspections/history/{clean_id}/annotated").status_code == 404

    defective = Finding(
        produce_type=ProduceType.APPLE,
        item_is_fruit=True,
        condition=Condition.FRESH,
        confidence=0.9,
        defect_types=[DefectType.BRUISE],
        severity=Severity.LOW,
        bbox=(0.2, 0.2, 0.8, 0.8),
    )

    class _Locator:
        def __call__(self, _image: object) -> Finding:
            return defective

        def locate_defect(
            self, _image: object, _finding: Finding
        ) -> tuple[float, float, float, float]:
            return (0.2, 0.2, 0.8, 0.8)

    _patch_detector(monkeypatch, _Locator())
    defect_id = client.post(
        "/inspections/product", files={"image": ("apple.jpg", _jpeg_bytes())}
    ).json()["inspection_id"]

    annotated_path = settings.inspection_db_path.parent / "images" / f"{defect_id}_annotated.jpg"
    assert annotated_path.is_file()
    assert annotated_path.read_bytes().startswith(b"\xff\xd8")

    annotated = client.get(f"/inspections/history/{defect_id}/annotated")
    assert annotated.status_code == 200
    assert annotated.headers["content-type"] == "image/jpeg"
    assert annotated.content.startswith(b"\xff\xd8")
    assert (
        client.get(
            "/inspections/history/00000000-0000-0000-0000-000000000000/annotated"
        ).status_code
        == 404
    )


class _RelocatingDetector:
    """Stand-in for a provider used only for second-stage localization."""

    def __init__(self, bbox: tuple[float, float, float, float] | None) -> None:
        self.bbox = bbox

    def __call__(self, _image: object) -> Finding:
        raise AssertionError("analysis should not run during relocation")

    def locate_defect(
        self, _image: object, _finding: Finding
    ) -> tuple[float, float, float, float] | None:
        return self.bbox


def test_relocate_defect_saves_box_and_annotated_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repair an inspection that was saved without coordinates."""
    finding = Finding(
        produce_type=ProduceType.APPLE,
        item_is_fruit=True,
        condition=Condition.FRESH,
        confidence=0.9,
        defect_types=[DefectType.BRUISE],
        severity=Severity.LOW,
    )
    _patch_detector(monkeypatch, lambda _: finding)
    inspection_id = client.post(
        "/inspections/product", files={"image": ("apple.jpg", _jpeg_bytes())}
    ).json()["inspection_id"]
    assert client.get(f"/inspections/history/{inspection_id}/annotated").status_code == 404

    monkeypatch.setattr(
        inspection_service,
        "create_vision_detector",
        lambda **_: _RelocatingDetector((0.2, 0.3, 0.6, 0.7)),
    )
    response = client.post(f"/inspections/{inspection_id}/relocate")
    assert response.status_code == 200
    assert tuple(response.json()["bbox"]) == (0.2, 0.3, 0.6, 0.7)
    annotated = client.get(f"/inspections/history/{inspection_id}/annotated")
    assert annotated.status_code == 200
    assert annotated.content.startswith(b"\xff\xd8")


def test_relocate_defect_rejects_clean_inspection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse to localize a defect on a fresh product."""
    finding = Finding(produce_type=ProduceType.APPLE, condition=Condition.FRESH, confidence=0.9)
    _patch_detector(monkeypatch, lambda _: finding)
    inspection_id = client.post(
        "/inspections/product", files={"image": ("apple.jpg", _jpeg_bytes())}
    ).json()["inspection_id"]
    response = client.post(f"/inspections/{inspection_id}/relocate")
    assert response.status_code == 409


def test_relocate_unknown_inspection_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return not found when there is no inspection to relocate."""
    monkeypatch.setattr(
        inspection_service,
        "create_vision_detector",
        lambda **_: _RelocatingDetector((0.2, 0.3, 0.6, 0.7)),
    )
    response = client.post("/inspections/00000000-0000-0000-0000-000000000000/relocate")
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


def test_bbox_is_kept_only_for_defective_edible_products() -> None:
    """Clear stray boxes on fresh produce or non-fruit, but keep real defect boxes."""
    base = {
        "produce_type": "tomato",
        "condition": "fresh",
        "confidence": 0.8,
        "bbox": [0.2, 0.2, 0.8, 0.8],
        "item_is_fruit": True,
    }
    # Fresh product with no defect -> no box.
    assert Finding.model_validate(base).bbox is None
    # A reported defect keeps its box even with "none" severity (Grade B case).
    no_severity = {**base, "defect_types": ["bruise"], "severity": "none"}
    assert Finding.model_validate(no_severity).bbox == (0.2, 0.2, 0.8, 0.8)
    # A non-fruit item with a hallucinated defect -> no box.
    non_fruit = {
        **base,
        "item_is_fruit": False,
        "defect_types": ["bruise"],
        "severity": "low",
    }
    assert Finding.model_validate(non_fruit).bbox is None
    # A rotten product without a labeled defect keeps its box (Reject case).
    rotten = {**base, "condition": "rotten", "confidence": 0.9}
    assert Finding.model_validate(rotten).bbox == (0.2, 0.2, 0.8, 0.8)
    # A normal localized defect keeps its box, including needs-review cases.
    real_defect = {**base, "defect_types": ["bruise"], "severity": "low"}
    assert Finding.model_validate(real_defect).bbox == (0.2, 0.2, 0.8, 0.8)


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


def test_download_report_route_serves_attachment_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Download a stored PDF as an attachment when the client asks for it."""
    monkeypatch.setattr(
        routes_inspection,
        "get_inspection_report",
        lambda _: b"%PDF-stored",
    )
    inspection_id = "00000000-0000-0000-0000-000000000001"

    response = client.get(f"/inspections/{inspection_id}/report?download=true")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.content == b"%PDF-stored"


def test_report_route_rejects_transcript_that_cannot_fit_one_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return a client error with guidance when a transcript is too long."""

    def reject_report(*_: object) -> bytes:
        raise routes_inspection.InspectionReportInputError("Use a shorter note.")

    monkeypatch.setattr(
        routes_inspection,
        "generate_inspection_report",
        reject_report,
    )
    response = client.post(
        "/inspections/00000000-0000-0000-0000-000000000001/report",
        data={"user_type": "farmer"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Use a shorter note."


def test_report_route_rejects_non_farmer_user_type() -> None:
    """Keep report generation limited to the farmer audience."""
    response = client.post(
        "/inspections/00000000-0000-0000-0000-000000000001/report",
        data={"user_type": "qc_operator"},
    )
    assert response.status_code == 422


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


def _voice_inspection_result(transcript: str) -> InspectionResult:
    """Build a voice-only inspection result for route tests."""
    return InspectionResult(
        inspection_id="00000000-0000-0000-0000-0000000000aa",
        produce_type="unknown",
        detected_item="",
        item_is_fruit=False,
        in_inspection_categories=False,
        verdict="needs_review",
        confidence=None,
        bbox=None,
        defect_types=[],
        severity=Severity.NONE,
        grade="Needs manual review",
        voice_note_transcript=transcript,
        verdict_notes=[],
        recommended_action="Add a product photo for a visual grade.",
    )


@pytest.mark.parametrize(
    "content_type",
    ["audio/x-m4a", "audio/m4a", "audio/mp4", "application/octet-stream"],
)
def test_voice_inspection_accepts_m4a(monkeypatch: pytest.MonkeyPatch, content_type: str) -> None:
    """Accept .m4a uploads regardless of which MIME type the browser reports."""
    received: list[str] = []

    def fake_inspect(_data: bytes, filename: str) -> InspectionResult:
        received.append(filename)
        return _voice_inspection_result("A soft spot on the tomato")

    monkeypatch.setattr(routes_inspection, "inspect_from_voice", fake_inspect)
    response = client.post(
        "/inspections/product",
        files={"voice_note": ("note.m4a", b"audio", content_type)},
    )
    assert response.status_code == 200
    assert received == ["voice-note.m4a"]
    assert response.json()["voice_note_transcript"] == "A soft spot on the tomato"


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
