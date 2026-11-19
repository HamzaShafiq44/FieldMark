"""Validated schemas for vision findings and inspection results."""

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Grade = Literal["A", "B", "Reject", "Needs manual review"]
Verdict = Literal["pass", "defect", "needs_review"]
BBox = tuple[float, float, float, float]
DEFAULT_DISCLAIMER = (
    "This tool does not assess food safety. Results are indicative; inspect manually when in doubt."
)


class Condition(StrEnum):
    """High-level visual condition reported for a product."""

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
    """Supported visible product defect categories."""

    BRUISE = "bruise"
    ROT = "rot"
    MOLD = "mold"
    DEFORMITY = "deformity"


class ProduceType(StrEnum):
    """Built-in product category slugs retained for compatibility."""

    TOMATO = "tomato"
    APPLE = "apple"
    BANANA = "banana"
    ORANGE = "orange"
    UNKNOWN = "unknown"


class ReportUserType(StrEnum):
    """Supported audience for inspection reports."""

    FARMER = "farmer"


class VoiceNoteRelation(StrEnum):
    """How an optional voice note relates to the visual inspection."""

    NOT_PROVIDED = "not_provided"
    SUPPORTS = "supports"
    ADDS_CONTEXT = "adds_context"
    CONFLICTS = "conflicts"


class Finding(BaseModel):
    """Validated, untrusted vision-model output."""

    produce_type: str = Field(min_length=1, max_length=40)
    detected_item: str = Field(default="", max_length=40)
    item_is_fruit: bool = False
    condition: Condition
    confidence: float = Field(ge=0.0, le=1.0)
    visual_observation: str = Field(default="", max_length=240)
    defect_types: list[DefectType] = Field(default_factory=list)
    severity: Severity = Severity.NONE
    bbox: BBox | None = Field(
        default=None,
        description="Normalized bounding box around the most prominent visible defect.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_detected_type(cls, data: object) -> object:
        """Normalize a model-reported category into a lowercase slug."""
        if not isinstance(data, dict):
            return data
        raw_type = str(data.get("produce_type", "")).strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", raw_type).strip("-")
        updated = dict(data)
        updated["produce_type"] = slug or "unknown"
        return updated

    @model_validator(mode="after")
    def clear_invalid_bbox(self) -> "Finding":
        """Discard boxes that are malformed or not tied to a real defect."""
        if self.bbox is None:
            return self
        x1, y1, x2, y2 = self.bbox
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            self.bbox = None
            return self
        # Keep a box only on edible products that the model reported as either
        # spoiling by condition or carrying a localized defect. Reject grades
        # can come from a rotten condition without a labeled defect type, and
        # Grade B defects can carry "none" severity. Non-fruit items, and fresh
        # or unclear products with no defect, stay unboxed.
        if not self.item_is_fruit or (self.condition != Condition.ROTTEN and not self.defect_types):
            self.bbox = None
        return self


class DefectLocalization(BaseModel):
    """Coordinates returned by the dedicated second-stage localization call."""

    bbox: BBox | None = None

    @model_validator(mode="after")
    def clear_invalid_bbox(self) -> "DefectLocalization":
        """Discard coordinate sets that are not normalized and ordered."""
        if self.bbox is None:
            return self
        x1, y1, x2, y2 = self.bbox
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            self.bbox = None
        return self


class InspectionResult(BaseModel):
    """Final deterministic product grade and action for an inspected image."""

    inspection_id: str
    produce_type: str = Field(min_length=1, max_length=40)
    detected_item: str = Field(default="", max_length=40)
    item_is_fruit: bool = False
    in_inspection_categories: bool = True
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
    report_available: bool = False


class InspectionHistoryItem(BaseModel):
    """Public summary of a persisted inspection for the history list."""

    inspection_id: str
    created_at: str
    produce_type: str = Field(min_length=1, max_length=40)
    detected_item: str = ""
    item_is_fruit: bool = False
    in_inspection_categories: bool = True
    verdict: Verdict
    grade: Grade
    thumbnail_url: str
    report_available: bool = False
    report_source_available: bool = False


class InspectionHistoryResponse(BaseModel):
    """Paginated inspection history response."""

    items: list[InspectionHistoryItem]


class InspectionReportText(BaseModel):
    """Validated narrative returned by the report-writing model."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=300)
    findings: list[Annotated[str, Field(min_length=1, max_length=180)]] = Field(
        min_length=1,
        max_length=3,
    )
    recommendations: list[Annotated[str, Field(min_length=1, max_length=140)]] = Field(
        min_length=1,
        max_length=3,
    )
    voice_note_relation: VoiceNoteRelation
    voice_note_conflict: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def require_conflict_explanation(self) -> "InspectionReportText":
        """Require a plain-language explanation when the sources conflict."""
        if self.voice_note_relation is VoiceNoteRelation.CONFLICTS and not self.voice_note_conflict:
            raise ValueError("A conflicting voice note requires an explanation.")
        if self.voice_note_relation is not VoiceNoteRelation.CONFLICTS and self.voice_note_conflict:
            raise ValueError("A voice-note conflict explanation requires a conflict relation.")
        return self
