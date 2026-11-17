"""Validated schemas for vision findings and inspection results."""

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


class ReportUserType(StrEnum):
    """Report audience that controls the report's language and emphasis."""

    FARMER = "farmer"
    QC_OPERATOR = "qc_operator"


class VoiceNoteRelation(StrEnum):
    """How an optional voice note relates to the visual inspection."""

    NOT_PROVIDED = "not_provided"
    SUPPORTS = "supports"
    ADDS_CONTEXT = "adds_context"
    CONFLICTS = "conflicts"


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
    report_available: bool = False
    report_source_available: bool = False


class InspectionHistoryResponse(BaseModel):
    """Paginated inspection history response."""

    items: list[InspectionHistoryItem]


class InspectionReportText(BaseModel):
    """Validated narrative returned by the report-writing model."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=600)
    findings: str = Field(min_length=1, max_length=800)
    recommendations: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(
        min_length=1,
        max_length=4,
    )
    voice_note_relation: VoiceNoteRelation
    voice_note_conflict: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def require_conflict_explanation(self) -> "InspectionReportText":
        """Require a plain-language explanation when the sources conflict."""
        if self.voice_note_relation is VoiceNoteRelation.CONFLICTS and not self.voice_note_conflict:
            raise ValueError("A conflicting voice note requires an explanation.")
        if self.voice_note_relation is not VoiceNoteRelation.CONFLICTS and self.voice_note_conflict:
            raise ValueError("A voice-note conflict explanation requires a conflict relation.")
        return self
