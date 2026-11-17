"""Image validation and deterministic produce grading logic."""

import logging
from io import BytesIO
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from app.ai.provider_factory import create_vision_detector
from app.ai.providers import DetectionError, VisionDetector
from app.ai.schemas import (
    Condition,
    DefectType,
    Finding,
    Grade,
    InspectionHistoryResponse,
    InspectionResult,
    ProduceType,
    Severity,
    Verdict,
)
from app.core.config import settings
from app.storage.inspection_repository import HistoryStorageError, InspectionRepository

LOGGER = logging.getLogger(__name__)
Image.MAX_IMAGE_PIXELS = settings.max_image_pixels
FRESH_GRADE_A_MIN = 0.70
FRESH_REVIEW_BELOW = 0.40
ROTTEN_REJECT_MIN = 0.60
REVIEW: tuple[Grade, str] = ("Needs manual review", "Inspect manually before sale.")
PREMIUM_ACTION = "Sell as premium produce."
STANDARD_ACTION = "Sell soon, discount, or re-inspect."
ROTTEN_ACTION = "Possible spoilage detected. Separate from batch; discard or compost."
INVALID_IMAGE_MESSAGE = "Invalid or unreadable image."
NON_VISUAL_OBSERVATION_TERMS = ("confidence", "threshold", "percent", "grade")


class InvalidImageError(ValueError):
    """Raised when an uploaded image is invalid or unsupported."""


class InspectionDetectionError(RuntimeError):
    """Raised when the configured inspection provider fails."""


class InspectionStorageError(RuntimeError):
    """Raised when inspection history cannot be persisted or retrieved."""


def load_and_validate_image(data: bytes) -> Image.Image:
    """Validate image size and format, then return an RGB image."""
    if len(data) > settings.max_image_bytes:
        raise InvalidImageError(INVALID_IMAGE_MESSAGE)
    try:
        with Image.open(BytesIO(data)) as image:
            image_format = image.format
            image.verify()
        if image_format not in settings.allowed_formats:
            raise InvalidImageError(INVALID_IMAGE_MESSAGE)
        with Image.open(BytesIO(data)) as image:
            return image.convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        LOGGER.warning("Image validation failed (%s).", type(exc).__name__)
        raise InvalidImageError(INVALID_IMAGE_MESSAGE) from exc


def _grade_fresh(finding: Finding) -> tuple[Grade, str]:
    """Grade fresh produce based on confidence and visible defects."""
    if finding.confidence < FRESH_REVIEW_BELOW:
        return REVIEW
    if finding.severity is Severity.HIGH or DefectType.MOLD in finding.defect_types:
        return REVIEW
    if finding.confidence >= FRESH_GRADE_A_MIN and finding.severity is Severity.NONE:
        return "A", PREMIUM_ACTION
    return "B", STANDARD_ACTION


def grade(finding: Finding) -> tuple[Grade, str]:
    """Return a deterministic grade and recommended action."""
    if finding.produce_type is ProduceType.UNKNOWN or finding.condition is Condition.UNSURE:
        return REVIEW
    if finding.condition is Condition.ROTTEN:
        if finding.confidence >= ROTTEN_REJECT_MIN:
            return "Reject", ROTTEN_ACTION
        return REVIEW
    return _grade_fresh(finding)


def _verdict(grade_value: Grade, finding: Finding) -> Verdict:
    """Map a grade and visible defects to the API verdict."""
    if grade_value == "Needs manual review":
        return "needs_review"
    if grade_value == "Reject" or finding.defect_types:
        return "defect"
    return "pass"


def _rotten_grade_note(grade_value: Grade) -> str:
    """Explain the possible-spoilage decision without exposing grading thresholds."""
    if grade_value == "Reject":
        return "The model reported possible spoilage; the grading policy assigned Reject."
    return "Possible spoilage was reported, but the result is uncertain; manual review is required."


def _fresh_grade_notes(finding: Finding, grade_value: Grade) -> list[str]:
    """Explain the fresh-produce grade using its confidence and reported defects."""
    confidence = finding.confidence
    notes = ["The model reported the condition as fresh."]
    if confidence < FRESH_REVIEW_BELOW:
        notes.append("The model's assessment was too uncertain for automatic grading.")
    if finding.severity is Severity.HIGH:
        notes.append("High reported defect severity requires manual review.")
    if DefectType.MOLD in finding.defect_types:
        notes.append("Mold was reported; this triggers manual review under the grading rules.")
    if grade_value == "A":
        notes.append("No defect severity was reported; the fruit meets the premium-grade criteria.")
    elif grade_value == "B" and confidence < FRESH_GRADE_A_MIN:
        notes.append("The fruit did not meet the premium-grade criteria, so it was graded B.")
    elif grade_value == "B":
        notes.append(
            f"Reported severity ({finding.severity.value}) prevents Grade A; the result is B."
        )
    return notes


def _grade_notes(finding: Finding, grade_value: Grade) -> list[str]:
    """Explain the conditions that selected the grade."""
    if finding.produce_type is ProduceType.UNKNOWN:
        return ["The produce type is unsupported or unclear, so manual review is required."]
    if finding.condition is Condition.UNSURE:
        return ["The produce condition is unclear, so manual review is required."]
    if finding.condition is Condition.ROTTEN:
        return [_rotten_grade_note(grade_value)]
    return _fresh_grade_notes(finding, grade_value)


def _verdict_note(finding: Finding, grade_value: Grade, verdict_value: Verdict) -> str:
    """Explain why the final verdict follows from the grade and reported defects."""
    if verdict_value == "defect" and grade_value != "Reject":
        defect_names = ", ".join(defect.value for defect in finding.defect_types)
        return f"Reported visible defects ({defect_names}) make the verdict defect."
    if verdict_value == "defect":
        return "The Reject grade results in a defect verdict."
    if verdict_value == "pass":
        return "No visible defect types were reported, so the verdict is pass."
    return "The grade requires manual review, so the verdict is needs review."


def _verdict_notes(finding: Finding, grade_value: Grade, verdict_value: Verdict) -> list[str]:
    """Explain which model-reported factors and grading rules drove the result."""
    visual_observation = finding.visual_observation.strip()
    lowered_observation = visual_observation.lower()
    has_grading_detail = "%" in visual_observation or any(
        term in lowered_observation for term in NON_VISUAL_OBSERVATION_TERMS
    )
    visual_note = (
        f"Visual observation: {visual_observation}"
        if visual_observation and not has_grading_detail
        else "The model did not provide a visual-only observation."
    )
    return [
        visual_note,
        *_grade_notes(finding, grade_value),
        _verdict_note(finding, grade_value, verdict_value),
    ]


def inspect_produce(
    data: bytes,
    detect: VisionDetector | None = None,
) -> InspectionResult:
    """Validate, analyze, and grade an uploaded piece of produce."""
    image = load_and_validate_image(data)
    detector = detect if detect is not None else create_vision_detector()
    try:
        finding = detector(image)
    except DetectionError as exc:
        raise InspectionDetectionError("Vision detection failed.") from exc
    grade_value, action = grade(finding)
    verdict_value = _verdict(grade_value, finding)
    result = InspectionResult(
        inspection_id=str(uuid4()),
        produce_type=finding.produce_type,
        verdict=verdict_value,
        confidence=finding.confidence,
        bbox=finding.bbox,
        defect_types=finding.defect_types,
        severity=finding.severity,
        grade=grade_value,
        verdict_notes=_verdict_notes(finding, grade_value, verdict_value),
        recommended_action=action,
    )
    save_inspection_history(result, image)
    return result


def save_inspection_history(result: InspectionResult, image: Image.Image) -> None:
    """Persist an inspection result and its thumbnail."""
    try:
        InspectionRepository(settings.inspection_db_path).save(result, image)
    except HistoryStorageError as exc:
        raise InspectionStorageError(str(exc)) from exc


def list_inspection_history(limit: int, offset: int) -> InspectionHistoryResponse:
    """Return a paginated list of previously completed inspections."""
    try:
        items = InspectionRepository(settings.inspection_db_path).list_history(limit, offset)
    except HistoryStorageError as exc:
        raise InspectionStorageError(str(exc)) from exc
    return InspectionHistoryResponse(items=items)


def get_inspection_thumbnail(inspection_id: str) -> bytes | None:
    """Return the thumbnail for a prior inspection when it exists."""
    try:
        return InspectionRepository(settings.inspection_db_path).get_thumbnail(inspection_id)
    except HistoryStorageError as exc:
        raise InspectionStorageError(str(exc)) from exc
