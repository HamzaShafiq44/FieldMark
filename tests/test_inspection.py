"""Deterministic tests for product grading and inspection service behavior."""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.ai.providers import DetectionError, QuotaExhaustedError
from app.ai.schemas import Condition, DefectType, Finding, ProduceType, Severity
from app.core.config import settings
from app.services import inspection_service
from app.services.inspection_service import (
    InvalidImageError,
    finalize_inspection,
    grade,
    inspect_produce,
)
from app.storage.inspection_repository import InspectionRepository

GRADE_CASES = [
    (
        Finding(produce_type=ProduceType.TOMATO, condition=Condition.FRESH, confidence=0.85),
        "A",
    ),
    (
        Finding(produce_type=ProduceType.TOMATO, condition=Condition.FRESH, confidence=0.55),
        "B",
    ),
    (
        Finding(
            produce_type=ProduceType.TOMATO,
            condition=Condition.FRESH,
            confidence=0.85,
            severity=Severity.LOW,
        ),
        "B",
    ),
    (
        Finding(produce_type=ProduceType.TOMATO, condition=Condition.FRESH, confidence=0.30),
        "Needs manual review",
    ),
    (
        Finding(
            produce_type=ProduceType.TOMATO,
            condition=Condition.FRESH,
            confidence=0.90,
            severity=Severity.HIGH,
        ),
        "Needs manual review",
    ),
    (
        Finding(
            produce_type=ProduceType.TOMATO,
            condition=Condition.FRESH,
            confidence=0.90,
            defect_types=[DefectType.MOLD],
        ),
        "Needs manual review",
    ),
    (
        Finding(produce_type=ProduceType.TOMATO, condition=Condition.ROTTEN, confidence=0.80),
        "Reject",
    ),
    (
        Finding(produce_type=ProduceType.TOMATO, condition=Condition.ROTTEN, confidence=0.50),
        "Needs manual review",
    ),
    (
        Finding(produce_type=ProduceType.TOMATO, condition=Condition.UNSURE, confidence=0.90),
        "Needs manual review",
    ),
    (
        Finding(produce_type=ProduceType.UNKNOWN, condition=Condition.FRESH, confidence=0.90),
        "Needs manual review",
    ),
]


def _jpeg_bytes() -> bytes:
    """Create a small in-memory JPEG fixture."""
    buffer = BytesIO()
    Image.new("RGB", (16, 16), color="red").save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def isolate_history_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep service tests isolated from local application data."""
    monkeypatch.setattr(settings, "inspection_db_path", tmp_path / "inspections.sqlite3")


@pytest.mark.parametrize(("finding", "expected"), GRADE_CASES)
def test_grade(finding: Finding, expected: str) -> None:
    """Check deterministic grades for supported finding combinations."""
    assert grade(finding)[0] == expected


@pytest.mark.parametrize(
    "produce_type",
    [ProduceType.TOMATO, ProduceType.APPLE, ProduceType.BANANA, ProduceType.ORANGE],
)
def test_inspect_supported_produce(produce_type: ProduceType) -> None:
    """Preserve each supported product type in the inspection result."""
    finding = Finding(produce_type=produce_type, condition=Condition.FRESH, confidence=0.9)
    result = inspect_produce(_jpeg_bytes(), detect=lambda _: finding)
    assert result.grade == "A"
    assert result.verdict == "pass"
    assert result.produce_type == produce_type.value
    assert any("premium-grade criteria" in note for note in result.verdict_notes)
    assert all("%" not in note and "threshold" not in note.lower() for note in result.verdict_notes)
    assert any("verdict is pass" in note for note in result.verdict_notes)


def test_inspection_notes_explain_manual_review() -> None:
    """Explain that high reported severity forces manual review."""
    finding = Finding(
        produce_type=ProduceType.APPLE,
        condition=Condition.FRESH,
        confidence=0.9,
        severity=Severity.HIGH,
    )
    result = inspect_produce(_jpeg_bytes(), detect=lambda _: finding)
    assert result.grade == "Needs manual review"
    assert result.verdict == "needs_review"
    assert any("High reported defect severity" in note for note in result.verdict_notes)
    assert any("verdict is needs review" in note for note in result.verdict_notes)


def test_inspection_notes_explain_defect_verdict() -> None:
    """Explain when model-reported defect types change the verdict."""
    finding = Finding(
        produce_type=ProduceType.ORANGE,
        condition=Condition.FRESH,
        confidence=0.8,
        visual_observation="A small brown bruise is visible on the side.",
        defect_types=[DefectType.BRUISE],
        severity=Severity.LOW,
    )
    result = inspect_produce(_jpeg_bytes(), detect=lambda _: finding)
    assert result.grade == "B"
    assert result.verdict == "defect"
    assert any("small brown bruise is visible" in note for note in result.verdict_notes)
    assert any("visible defects (bruise)" in note for note in result.verdict_notes)
    assert all("%" not in note and "threshold" not in note.lower() for note in result.verdict_notes)


def test_visual_observation_does_not_repeat_confidence_percentages() -> None:
    """Keep confidence and grade details out of user-facing verdict notes."""
    finding = Finding(
        produce_type=ProduceType.APPLE,
        condition=Condition.FRESH,
        confidence=0.9,
        visual_observation="A brown spot is visible; confidence is 90%.",
    )
    result = inspect_produce(_jpeg_bytes(), detect=lambda _: finding)
    assert any("visual-only observation" in note for note in result.verdict_notes)
    assert all(
        "%" not in note and "confidence" not in note.lower() for note in result.verdict_notes
    )


def test_reject_notes_omit_confidence_percentages_and_thresholds() -> None:
    """Keep numeric grading thresholds out of verdict explanations."""
    finding = Finding(
        produce_type=ProduceType.APPLE,
        condition=Condition.ROTTEN,
        confidence=0.9,
        defect_types=[DefectType.ROT],
    )
    result = inspect_produce(_jpeg_bytes(), detect=lambda _: finding)
    assert result.grade == "Reject"
    assert all("%" not in note and "threshold" not in note.lower() for note in result.verdict_notes)


def test_quota_error_keeps_its_actionable_message() -> None:
    """A quota failure is not replaced by the generic vision error."""

    def fail(_image: Image.Image) -> Finding:
        raise QuotaExhaustedError("Gemini daily quota exhausted.")

    with pytest.raises(QuotaExhaustedError, match="daily quota exhausted"):
        inspect_produce(_jpeg_bytes(), detect=fail)


def test_invalid_image_rejected() -> None:
    """Reject bytes that cannot be decoded as an image."""
    with pytest.raises(InvalidImageError):
        inspect_produce(b"not an image")


def test_out_of_category_fruit_gets_the_same_fresh_grade() -> None:
    """Grade an edible product outside the categories with the normal fresh rules."""
    finding = Finding(
        produce_type=ProduceType.UNKNOWN,
        detected_item="guava",
        item_is_fruit=True,
        condition=Condition.FRESH,
        confidence=0.9,
    )
    result = inspect_produce(_jpeg_bytes(), detect=lambda _: finding)
    assert result.grade == "A"
    assert result.verdict == "pass"
    assert result.detected_item == "guava"
    assert result.item_is_fruit is True
    assert result.in_inspection_categories is False


def test_out_of_category_rotten_fruit_is_rejected() -> None:
    """Apply the rotten-product rule to an edible product outside the categories."""
    finding = Finding(
        produce_type=ProduceType.UNKNOWN,
        detected_item="grapes",
        item_is_fruit=True,
        condition=Condition.ROTTEN,
        confidence=0.8,
        defect_types=[DefectType.ROT],
    )
    result = inspect_produce(_jpeg_bytes(), detect=lambda _: finding)
    assert result.grade == "Reject"
    assert result.verdict == "defect"
    assert result.in_inspection_categories is False


def test_non_fruit_is_an_unsupported_item_without_a_grade() -> None:
    """Keep a non-edible product out of grading and mark it unsupported."""
    finding = Finding(
        produce_type=ProduceType.UNKNOWN,
        detected_item="car",
        item_is_fruit=False,
        condition=Condition.UNSURE,
        confidence=0.95,
    )
    result = inspect_produce(_jpeg_bytes(), detect=lambda _: finding)
    assert result.grade == "Needs manual review"
    assert result.verdict == "needs_review"
    assert result.in_inspection_categories is False
    assert any("does not show an edible product" in note for note in result.verdict_notes)


def test_out_of_category_type_from_model_is_kept_as_unknown() -> None:
    """Accept any fruit slug and normalize inactive categories for grading."""
    finding = Finding.model_validate(
        {
            "produce_type": "guava",
            "detected_item": "guava",
            "item_is_fruit": True,
            "condition": "fresh",
            "confidence": 0.9,
        }
    )
    assert finding.produce_type == "guava"
    assert finding.detected_item == "guava"
    result = inspect_produce(_jpeg_bytes(), detect=lambda _: finding)
    assert result.produce_type == ProduceType.UNKNOWN
    assert result.detected_item == "guava"
    assert result.grade == "A"
    assert result.in_inspection_categories is False


class _FakeLocator:
    """Stand-in for a provider that supports second-stage localization."""

    def __init__(self, finding: Finding, bbox: tuple[float, float, float, float] | None) -> None:
        self.finding = finding
        self.bbox = bbox
        self.calls = 0

    def __call__(self, _image: Image.Image) -> Finding:
        return self.finding

    def locate_defect(
        self, _image: Image.Image, _finding: Finding
    ) -> tuple[float, float, float, float] | None:
        self.calls += 1
        return self.bbox


def test_analysis_box_skips_localization() -> None:
    """Keep the analysis box without paying for a second vision call."""
    finding = Finding(
        produce_type=ProduceType.TOMATO,
        item_is_fruit=True,
        condition=Condition.FRESH,
        confidence=0.9,
        defect_types=[DefectType.BRUISE],
        severity=Severity.LOW,
        bbox=(0.1, 0.1, 0.2, 0.2),
    )
    detector = _FakeLocator(finding, (0.3, 0.4, 0.6, 0.7))
    result = inspect_produce(_jpeg_bytes(), detect=detector)
    assert detector.calls == 0
    assert result.bbox == (0.1, 0.1, 0.2, 0.2)


def test_localization_is_skipped_for_fresh_produce() -> None:
    """Never spend a localization call when the analysis found no defect."""
    finding = Finding(
        produce_type=ProduceType.TOMATO,
        item_is_fruit=True,
        condition=Condition.FRESH,
        confidence=0.9,
        bbox=(0.1, 0.1, 0.2, 0.2),
    )
    detector = _FakeLocator(finding, (0.3, 0.4, 0.6, 0.7))
    result = inspect_produce(_jpeg_bytes(), detect=detector)
    assert detector.calls == 0
    assert result.bbox is None


def test_finalization_localizes_when_analysis_has_no_box(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the second-stage call in the background only when a box is missing."""
    finding = Finding(
        produce_type=ProduceType.TOMATO,
        item_is_fruit=True,
        condition=Condition.FRESH,
        confidence=0.9,
        defect_types=[DefectType.BRUISE],
        severity=Severity.LOW,
    )
    detector = _FakeLocator(finding, (0.3, 0.4, 0.6, 0.7))
    result = inspect_produce(_jpeg_bytes(), detect=detector)
    assert result.bbox is None

    monkeypatch.setattr(inspection_service, "create_vision_detector", lambda **_: detector)
    finalize_inspection(result.inspection_id)

    stored = InspectionRepository(settings.inspection_db_path).get_report_source(
        result.inspection_id
    )
    assert stored is not None
    assert stored.result.bbox == (0.3, 0.4, 0.6, 0.7)
    assert detector.calls == 1
    annotated = (
        settings.inspection_db_path.parent / "images" / f"{result.inspection_id}_annotated.jpg"
    )
    assert annotated.is_file()


def test_localization_failure_keeps_original_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave the stored result untouched when background localization fails."""

    class _FailingLocator(_FakeLocator):
        def locate_defect(
            self, _image: Image.Image, _finding: Finding
        ) -> tuple[float, float, float, float] | None:
            self.calls += 1
            raise DetectionError("localization unavailable")

    finding = Finding(
        produce_type=ProduceType.TOMATO,
        item_is_fruit=True,
        condition=Condition.ROTTEN,
        confidence=0.9,
    )
    detector = _FailingLocator(finding, None)
    result = inspect_produce(_jpeg_bytes(), detect=detector)
    assert result.grade == "Reject"

    monkeypatch.setattr(inspection_service, "create_vision_detector", lambda **_: detector)
    finalize_inspection(result.inspection_id)

    stored = InspectionRepository(settings.inspection_db_path).get_report_source(
        result.inspection_id
    )
    assert stored is not None
    assert stored.result.bbox is None
    assert detector.calls == 1
