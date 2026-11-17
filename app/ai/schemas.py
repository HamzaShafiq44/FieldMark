"""Validated schemas for vision findings and inspection results."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Grade = Literal["A", "B", "Reject", "Needs manual review"]
Verdict = Literal["pass", "defect", "needs_review"]
BBox = tuple[float, float, float, float]
DEFAULT_DISCLAIMER = (
    "This tool does not assess food safety. Results are indicative; inspect manually when in doubt."
)


class Condition(StrEnum):
    """High-level visual condition reported for a piece of produce."""

    FRESH = "fresh"
    ROTTEN = "rotten"
    UNSURE = "unsure"


class Severity(StrEnum):
    """Severity level for visible defects."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DefectType(StrEnum):
    """Supported visible produce defect categories."""

    BRUISE = "bruise"
    ROT = "rot"
    MOLD = "mold"
    DEFORMITY = "deformity"


class ProduceType(StrEnum):
    """Produce categories supported by the inspection pipeline."""

    TOMATO = "tomato"
    APPLE = "apple"
    BANANA = "banana"
    ORANGE = "orange"
    UNKNOWN = "unknown"


class Finding(BaseModel):
    """Validated, untrusted vision-model output."""

    produce_type: ProduceType
    condition: Condition
    confidence: float = Field(ge=0.0, le=1.0)
    visual_observation: str = Field(default="", max_length=240)
    defect_types: list[DefectType] = Field(default_factory=list)
    severity: Severity = Severity.NONE
    bbox: BBox | None = Field(
        default=None,
        description="Normalized bounding box around the most prominent visible defect.",
    )

    @model_validator(mode="after")
    def clear_invalid_bbox(self) -> "Finding":
        """Discard bounding boxes that are not normalized and ordered."""
        if self.bbox is None:
            return self
        x1, y1, x2, y2 = self.bbox
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            self.bbox = None
        return self


class InspectionResult(BaseModel):
    """Final deterministic produce grade and action for an inspected image."""

    inspection_id: str
    produce_type: ProduceType
    verdict: Verdict
    confidence: float | None
    bbox: BBox | None = Field(
        description="Normalized bounding box around the most prominent visible defect.",
    )
    defect_types: list[DefectType]
    severity: Severity
    grade: Grade
    verdict_notes: list[str]
    recommended_action: str
    disclaimer: str = DEFAULT_DISCLAIMER


class InspectionHistoryItem(BaseModel):
    """Public summary of a persisted inspection for the history list."""

    inspection_id: str
    created_at: str
    produce_type: ProduceType
    verdict: Verdict
    grade: Grade
    thumbnail_url: str


class InspectionHistoryResponse(BaseModel):
    """Paginated inspection history response."""

    items: list[InspectionHistoryItem]
