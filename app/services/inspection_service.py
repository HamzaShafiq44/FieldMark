"""Image validation and deterministic product grading logic."""

import logging
from io import BytesIO
from typing import cast
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from app.ai.provider_factory import create_vision_detector
from app.ai.providers import DetectionError, VisionDetector
from app.ai.schemas import (
    DEFAULT_DISCLAIMER,
    BBox,
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
from app.reports.pdf_renderer import annotate_photo
from app.services.category_service import list_active_category_slugs
from app.services.report_service import generate_automatic_report
from app.storage.inspection_repository import HistoryStorageError, InspectionRepository

LOGGER = logging.getLogger(__name__)
Image.MAX_IMAGE_PIXELS = settings.max_image_pixels
FRESH_GRADE_A_MIN = 0.70
FRESH_REVIEW_BELOW = 0.40
ROTTEN_REJECT_MIN = 0.60
REVIEW: tuple[Grade, str] = ("Needs manual review", "Inspect manually before sale.")
PREMIUM_ACTION = "Sell as premium product."
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


class DefectRelocationError(RuntimeError):
    """Raised when defect coordinates cannot be produced for an existing inspection."""


class RelocationNotFoundError(DefectRelocationError):
    """The inspection or its source photo does not exist."""


class RelocationNotApplicableError(DefectRelocationError):
    """The inspection has no defect to locate."""


class RelocationVisionError(DefectRelocationError):
    """The vision provider could not supply coordinates."""


class RelocationStorageError(DefectRelocationError):
    """The located coordinates could not be saved."""


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
    """Grade a fresh product based on confidence and visible defects."""
    if finding.confidence < FRESH_REVIEW_BELOW:
        return REVIEW
    if finding.severity is Severity.HIGH or DefectType.MOLD in finding.defect_types:
        return REVIEW
    if finding.confidence >= FRESH_GRADE_A_MIN and finding.severity is Severity.NONE:
        return "A", PREMIUM_ACTION
    return "B", STANDARD_ACTION


def grade(finding: Finding) -> tuple[Grade, str]:
    """Return a deterministic grade and recommended action.

    Edible products are graded with the same rules whether or not they belong to
    the configured inspection categories. Anything that is not an edible product
    is kept for manual review and gets no quality grade.
    """
    in_categories = finding.produce_type != ProduceType.UNKNOWN
    if not (finding.item_is_fruit or in_categories):
        return REVIEW
    if finding.condition is Condition.UNSURE:
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
    """Explain the fresh-product grade using its confidence and reported defects."""
    confidence = finding.confidence
    notes = ["The model reported the condition as fresh."]
    if confidence < FRESH_REVIEW_BELOW:
        notes.append("The model's assessment was too uncertain for automatic grading.")
    if finding.severity is Severity.HIGH:
        notes.append("High reported defect severity requires manual review.")
    if DefectType.MOLD in finding.defect_types:
        notes.append("Mold was reported; this triggers manual review under the grading rules.")
    if grade_value == "A":
        notes.append(
            "No defect severity was reported; the product meets the premium-grade criteria."
        )
    elif grade_value == "B" and confidence < FRESH_GRADE_A_MIN:
        notes.append("The product did not meet the premium-grade criteria, so it was graded B.")
    elif grade_value == "B":
        notes.append(
            f"Reported severity ({finding.severity.value}) prevents Grade A; the result is B."
        )
    return notes


def _grade_notes(finding: Finding, grade_value: Grade) -> list[str]:
    """Explain the conditions that selected the grade."""
    if finding.produce_type == ProduceType.UNKNOWN and not finding.item_is_fruit:
        return ["The photo does not show an edible product, so no quality grade applies."]
    if finding.condition is Condition.UNSURE:
        return ["The product condition is unclear, so manual review is required."]
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


def _in_inspection_categories(finding: Finding, active_slugs: list[str]) -> bool:
    """Return whether the detected product belongs to the active categories."""
    return finding.produce_type in active_slugs


def _is_edible_product(result: InspectionResult) -> bool:
    """Return whether the result can receive a normal product report."""
    return result.item_is_fruit or result.produce_type != ProduceType.UNKNOWN


def _normalize_category(finding: Finding, active_slugs: list[str]) -> Finding:
    """Map inactive or unknown categories to the generic unknown slug."""
    if finding.produce_type == ProduceType.UNKNOWN or finding.produce_type in active_slugs:
        return finding
    detected_item = finding.detected_item.strip() or finding.produce_type
    return finding.model_copy(
        update={"produce_type": ProduceType.UNKNOWN, "detected_item": detected_item}
    )


def _localize_defect(finding: Finding, detector: VisionDetector, image: Image.Image) -> Finding:
    """Supply a defect box with a second call only when the analysis has none.

    The analysis call already returns a validated box in the normal case. A
    focused second call is reserved for the rare case where a defect was found
    but no coordinates came back, so a standard inspection never pays for two
    vision round trips. The finding is left untouched when that call fails.
    """
    has_defect = bool(finding.defect_types) or finding.condition == Condition.ROTTEN
    if not finding.item_is_fruit or not has_defect:
        return finding
    if finding.bbox is not None:
        LOGGER.debug("Analysis returned a bbox; skipping defect localization.")
        return finding
    locate = getattr(detector, "locate_defect", None)
    if not callable(locate):
        return finding
    try:
        bbox = locate(image, finding)
    except DetectionError:
        LOGGER.warning("Defect localization failed; keeping the analysis box.")
        return finding
    return finding if bbox is None else finding.model_copy(update={"bbox": bbox})


def _finding_from_result(result: InspectionResult) -> Finding:
    """Rebuild the finding used to re-localize a stored inspection result."""
    observation = next(
        (
            note.removeprefix("Visual observation: ")
            for note in result.verdict_notes
            if note.startswith("Visual observation: ")
        ),
        "",
    )
    return Finding(
        produce_type=result.produce_type,
        detected_item=result.detected_item,
        item_is_fruit=result.item_is_fruit,
        condition=Condition.ROTTEN if result.grade == "Reject" else Condition.FRESH,
        confidence=result.confidence or 0.0,
        defect_types=result.defect_types,
        severity=result.severity,
        visual_observation=observation,
        bbox=result.bbox,
    )


def finalize_inspection(inspection_id: str) -> None:
    """Refine a missing defect box and build the report after the response is sent.

    This runs as a FastAPI background task for both photo and voice inspections.
    The response only waits for the evidence-gathering calls and persistence;
    optional localization, report writing, and PDF rendering happen here.
    """
    repository = InspectionRepository(settings.inspection_db_path)
    try:
        source = repository.get_report_source(inspection_id)
    except HistoryStorageError as exc:
        LOGGER.warning("Inspection could not be loaded for finalization: %s", exc)
        return
    if source is None:
        return

    result = source.result
    if source.original_image is not None:
        original_image = source.original_image
        try:
            detector = create_vision_detector(supported_types=list_active_category_slugs())
            refined = _localize_defect(
                _finding_from_result(result), detector, load_and_validate_image(original_image)
            )
            if refined.bbox is not None and refined.bbox != result.bbox:
                updated = result.model_copy(update={"bbox": refined.bbox})
                repository.update_result(inspection_id, updated.model_dump_json())
                repository.save_annotated(
                    inspection_id, annotate_photo(original_image, refined.bbox)
                )
                result = updated
        except (DetectionError, InvalidImageError, HistoryStorageError) as exc:
            LOGGER.warning("Defect refinement during finalization failed (%s).", type(exc).__name__)

    if _is_edible_product(result) or _should_report_voice(result):
        try:
            generate_automatic_report(inspection_id)
        except Exception:
            LOGGER.exception("Automatic report generation failed unexpectedly.")


def relocate_defect(inspection_id: str) -> InspectionResult:
    """Locate the defect on a saved inspection and persist its coordinates.

    This repairs older inspections whose analysis never returned a box, using
    a dedicated second-stage call and the stored photo and defect description.
    """
    repository = InspectionRepository(settings.inspection_db_path)
    try:
        source = repository.get_report_source(inspection_id)
    except HistoryStorageError as exc:
        raise RelocationStorageError("Inspection source could not be loaded.") from exc
    if source is None:
        raise RelocationNotFoundError("Inspection source not found.")
    if source.original_image is None:
        raise RelocationNotApplicableError("This voice-note inspection has no photo.")

    result = source.result
    has_defect = bool(result.defect_types) or result.grade == "Reject"
    if not result.item_is_fruit or not has_defect:
        raise RelocationNotApplicableError("This inspection does not report a defect to locate.")

    detector = create_vision_detector(supported_types=list_active_category_slugs())
    locate = getattr(detector, "locate_defect", None)
    if not callable(locate):
        raise RelocationVisionError("The configured vision provider cannot locate defects.")
    finding = _finding_from_result(result)
    try:
        bbox = cast("BBox | None", locate(load_and_validate_image(source.original_image), finding))
    except DetectionError as exc:
        raise RelocationVisionError(
            "The vision service could not locate the defect. Try again later."
        ) from exc
    if bbox is None:
        raise RelocationVisionError("The defect could not be located in this photo. Try again.")

    updated = result.model_copy(update={"bbox": bbox})
    try:
        repository.update_result(inspection_id, updated.model_dump_json())
        repository.save_annotated(inspection_id, annotate_photo(source.original_image, bbox))
    except HistoryStorageError as exc:
        raise RelocationStorageError("The located defect could not be saved.") from exc
    return updated


def _voice_verdict_notes(finding: Finding, grade_value: Grade, verdict_value: Verdict) -> list[str]:
    """Explain a voice-only decision without claiming anything was seen."""
    notes: list[str] = []
    observation = finding.visual_observation.strip()
    if observation:
        notes.append(f"Spoken description: {observation}")
    notes.extend(
        "The voice note does not identify an edible product, so no quality grade applies."
        if note.startswith("The photo does not show an edible product")
        else note
        for note in _grade_notes(finding, grade_value)
    )
    if verdict_value == "defect" and grade_value != "Reject":
        defects = ", ".join(defect.value for defect in finding.defect_types)
        notes.append(f"The described defects ({defects}) make the verdict defect.")
    elif verdict_value == "defect":
        notes.append("The Reject grade results in a defect verdict.")
    elif verdict_value == "pass":
        notes.append("No defect was described, so the verdict is pass.")
    else:
        notes.append(
            "The spoken description requires manual review, so the verdict is needs review."
        )
    return notes


def _should_report_voice(result: InspectionResult) -> bool:
    """Decide whether a voice-only inspection can receive a PDF report."""
    if _is_edible_product(result):
        return True
    # No product was identified, but the note still deserves a manual-review record.
    return bool(result.voice_note_transcript) and not result.detected_item.strip()


def inspect_from_voice(audio_data: bytes, filename: str) -> InspectionResult:
    """Transcribe a voice note, grade what was said, and save it like a photo inspection.

    The transcript is the only evidence, so the spoken product and condition are
    extracted into a Finding and passed through the same grading rules used for
    photos. Voice-only records have no image files but do receive PDF reports,
    generated later by ``finalize_inspection`` after the response is sent.
    """
    from app.ai.transcription import transcribe_voice_note
    from app.ai.voice_analysis import analyze_transcript

    transcript = transcribe_voice_note(audio_data, filename)
    supported = list_active_category_slugs()
    finding = _normalize_category(analyze_transcript(transcript, supported), supported)
    grade_value, action = grade(finding)
    verdict_value = _verdict(grade_value, finding)
    result = InspectionResult(
        inspection_id=str(uuid4()),
        produce_type=finding.produce_type,
        detected_item=finding.detected_item.strip(),
        item_is_fruit=finding.item_is_fruit,
        in_inspection_categories=_in_inspection_categories(finding, supported),
        verdict=verdict_value,
        confidence=finding.confidence,
        bbox=None,
        defect_types=finding.defect_types,
        severity=finding.severity,
        grade=grade_value,
        verdict_notes=_voice_verdict_notes(finding, grade_value, verdict_value),
        recommended_action=action,
        disclaimer=DEFAULT_DISCLAIMER,
        report_available=False,
        voice_note_transcript=transcript,
    )
    repository = InspectionRepository(settings.inspection_db_path)
    try:
        repository.save_voice_inspection(result)
    except HistoryStorageError as exc:
        raise InspectionStorageError(str(exc)) from exc

    return result


def inspect_produce(
    data: bytes,
    detect: VisionDetector | None = None,
    supported_types: list[str] | None = None,
) -> InspectionResult:
    """Validate, analyze, and grade an uploaded product image.

    Only the analysis call is on the response path. Defect refinement and PDF
    report generation are deferred to ``finalize_inspection`` so the farmer sees
    a verdict without waiting for two more hosted-model round trips.
    """
    supported = supported_types or list_active_category_slugs()
    image = load_and_validate_image(data)
    detector = detect
    if detector is None:
        detector = create_vision_detector(supported_types=supported)
    try:
        finding = detector(image)
    except DetectionError as exc:
        raise InspectionDetectionError("Vision detection failed.") from exc
    finding = _normalize_category(finding, supported)
    grade_value, action = grade(finding)
    verdict_value = _verdict(grade_value, finding)
    result = InspectionResult(
        inspection_id=str(uuid4()),
        produce_type=finding.produce_type,
        detected_item=finding.detected_item.strip(),
        item_is_fruit=finding.item_is_fruit,
        in_inspection_categories=_in_inspection_categories(finding, supported),
        verdict=verdict_value,
        confidence=finding.confidence,
        bbox=finding.bbox,
        defect_types=finding.defect_types,
        severity=finding.severity,
        grade=grade_value,
        verdict_notes=_verdict_notes(finding, grade_value, verdict_value),
        recommended_action=action,
    )
    save_inspection_history(result, image, data)
    return result


def save_inspection_history(
    result: InspectionResult, image: Image.Image, original_image: bytes
) -> None:
    """Persist an inspection result, original photo, thumbnail, and defect annotation."""
    repository = InspectionRepository(settings.inspection_db_path)
    try:
        repository.save(result, image, original_image)
    except HistoryStorageError as exc:
        raise InspectionStorageError(str(exc)) from exc
    # ---------------------------------------------------------------------
    # Create an annotated image only when a bounding box is present.
    # If the model supplied a bbox we annotate it; otherwise we skip
    # annotation (the endpoint will return 404).  No automatic fallback
    # full‑image annotation is performed.
    # ---------------------------------------------------------------------
    if result.bbox is not None:
        # Use the model‑provided bbox.
        bbox_to_use = result.bbox
    else:
        LOGGER.info("Result has no bbox; skipping annotation.")
        return
    try:
        repository.save_annotated(result.inspection_id, annotate_photo(original_image, bbox_to_use))
    except Exception as exc:
        LOGGER.warning("Annotated photo could not be saved: %s", exc)


def list_inspection_history(limit: int, offset: int) -> InspectionHistoryResponse:
    """Return a paginated list of previously completed inspections."""
    try:
        items = InspectionRepository(settings.inspection_db_path).list_history(limit, offset)
    except HistoryStorageError as exc:
        raise InspectionStorageError(str(exc)) from exc
    return InspectionHistoryResponse(items=items)


def clear_inspection_history() -> None:
    """Delete all saved inspection data and generated PDF reports."""
    try:
        InspectionRepository(settings.inspection_db_path).clear_history()
    except HistoryStorageError as exc:
        raise InspectionStorageError("Inspection history could not be cleared.") from exc


def get_inspection_thumbnail(inspection_id: str) -> bytes | None:
    """Return the thumbnail for a prior inspection when it exists."""
    try:
        return InspectionRepository(settings.inspection_db_path).get_thumbnail(inspection_id)
    except HistoryStorageError as exc:
        raise InspectionStorageError(str(exc)) from exc


def get_inspection_original(inspection_id: str) -> bytes | None:
    """Return the original photo normalized to a browser-friendly JPEG."""
    try:
        data = InspectionRepository(settings.inspection_db_path).get_original(inspection_id)
    except HistoryStorageError as exc:
        raise InspectionStorageError(str(exc)) from exc
    if data is None:
        return None
    # annotate_photo with no box orients and re-encodes the upload as JPEG.
    return annotate_photo(data, None)


def get_inspection_annotated(inspection_id: str) -> bytes | None:
    """Return the saved annotated photo, or draw one from stored coordinates."""
    try:
        repository = InspectionRepository(settings.inspection_db_path)
        saved = repository.get_annotated(inspection_id)
        if saved is not None:
            return saved
        source = repository.get_report_source(inspection_id)
    except HistoryStorageError as exc:
        raise InspectionStorageError(str(exc)) from exc
    if source is None or source.result.bbox is None or source.original_image is None:
        return None
    return annotate_photo(source.original_image, source.result.bbox)
