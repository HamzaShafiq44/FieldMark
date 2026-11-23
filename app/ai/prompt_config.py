"""Load and validate the YAML prompt templates used by the AI providers."""

import string
from functools import lru_cache
from pathlib import Path
from typing import Literal, overload

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import settings


class _Template(BaseModel):
    """Common fields shared by every prompt template."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1]


class InspectionPrompt(_Template):
    """Prompt used for photo analysis and localization context."""

    supported_products: list[str] = Field(min_length=1)
    system: str = Field(min_length=1)
    user: str = Field(min_length=1)


class LocalizationPrompt(_Template):
    """Prompt used for the second-stage defect box call."""

    system: str = Field(min_length=1)
    user: str = Field(min_length=1)


class ReportPrompt(_Template):
    """Prompt used to write the PDF report narrative."""

    system: str = Field(min_length=1)
    user: str = Field(min_length=1)
    farmer_audience_guidance: str = Field(min_length=1)


class VoiceAnalysisPrompt(_Template):
    """Prompt used to turn a transcript into a validated finding."""

    system: str = Field(min_length=1)


_PROMPT_SPECS = {
    "inspection": ("inspection.yaml", InspectionPrompt),
    "localization": ("localization.yaml", LocalizationPrompt),
    "report": ("report.yaml", ReportPrompt),
    "voice_analysis": ("voice_analysis.yaml", VoiceAnalysisPrompt),
}
_REQUIRED_PLACEHOLDERS = {
    "inspection": ("supported_products", "produce_type_values"),
    "localization": ("defects", "evidence"),
    "report": (),
    "voice_analysis": (),
}


@lru_cache(maxsize=16)
def _load(directory: Path, name: str) -> BaseModel:
    """Parse and validate one template from the configured directory."""
    filename, model = _PROMPT_SPECS[name]
    path = directory / filename
    if not path.is_file():
        raise RuntimeError(f"Prompt configuration is missing: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Prompt configuration could not be read: {path}") from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise RuntimeError(f"Prompt configuration is invalid: {path} ({exc})") from exc


@overload
def load_prompt(name: Literal["inspection"]) -> InspectionPrompt: ...


@overload
def load_prompt(name: Literal["localization"]) -> LocalizationPrompt: ...


@overload
def load_prompt(name: Literal["report"]) -> ReportPrompt: ...


@overload
def load_prompt(name: Literal["voice_analysis"]) -> VoiceAnalysisPrompt: ...


@overload
def load_prompt(name: str) -> BaseModel: ...


def load_prompt(name: str) -> BaseModel:
    """Return a validated prompt template, using the configured directory."""
    return _load(settings.prompt_templates_dir, name)


def render(template: str, **values: str) -> str:
    """Substitute `${placeholders}` and reject anything left unresolved."""
    try:
        rendered = string.Template(template).substitute(values)
    except KeyError as exc:
        raise RuntimeError(f"Unresolved prompt placeholder: {exc.args[0]}") from exc
    if "${" in rendered:
        raise RuntimeError("Prompt template contains an unresolved placeholder.")
    return rendered


def validate_prompt_templates() -> None:
    """Fail fast when a template is missing, malformed, or under-specified."""
    for name in _PROMPT_SPECS:
        template = load_prompt(name)
        text = getattr(template, "system", "")
        render(
            text,
            **{key: "validation-value" for key in _REQUIRED_PLACEHOLDERS[name]},
        )
