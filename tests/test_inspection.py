"""Deterministic tests for produce grading and inspection service behavior."""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.ai.schemas import Condition, DefectType, Finding, ProduceType, Severity
from app.core.config import settings
from app.services.inspection_service import InvalidImageError, grade, inspect_produce

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
    """Preserve each supported produce type in the inspection result."""
    finding = Finding(produce_type=produce_type, condition=Condition.FRESH, confidence=0.9)
    result = inspect_produce(_jpeg_bytes(), detect=lambda _: finding)
    assert result.grade == "A"
    assert result.verdict == "pass"
    assert result.produce_type is produce_type
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


def test_invalid_image_rejected() -> None:
    """Reject bytes that cannot be decoded as an image."""
    with pytest.raises(InvalidImageError):
        inspect_produce(b"not an image")
