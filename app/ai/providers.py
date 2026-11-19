"""Shared detector contract and provider error."""

from collections.abc import Callable

from PIL import Image

from app.ai.schemas import Condition, Finding

VisionDetector = Callable[[Image.Image], Finding]


def defect_summary(finding: Finding) -> str:
    """Describe the reported defect for the second-stage localization prompt."""
    labels = ", ".join(item.value for item in finding.defect_types)
    if not labels and finding.condition == Condition.ROTTEN:
        return "visible spoilage"
    return labels


class DetectionError(RuntimeError):
    """Raised when a configured vision provider cannot return a valid finding."""
