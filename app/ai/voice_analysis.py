"""Turn a voice-note transcript into structured inspection findings.

The spoken description is the only evidence available for a voice-only
inspection, so this module extracts the product and the reported condition,
then lets the shared grading rules decide the grade and verdict. The model
is untrusted: its output is validated, and a deterministic keyword reader is
used when the provider is unavailable or returns unusable JSON.
"""

import json
import logging
import re

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.ai.client_factory import get_gemini_client
from app.ai.errors import ReportAIError
from app.ai.produce_gemini import FINDING_SCHEMA
from app.ai.prompt_config import load_prompt
from app.ai.schemas import Condition, DefectType, Finding, Severity
from app.core.config import settings
from app.services.category_service import ALLOWED_FRUITS

LOGGER = logging.getLogger(__name__)

ANALYSIS_SYSTEM_PROMPT = load_prompt("voice_analysis").system

PLURAL_FORMS = {
    "blackberry": "blackberries",
    "cherry": "cherries",
    "clementine": "clementines",
    "date": "dates",
    "dragonfruit": "dragonfruit",
    "grape": "grapes",
    "kiwi": "kiwis",
    "mango": "mangoes",
    "peach": "peaches",
    "persimmon": "persimmons",
    "plum": "plums",
    "strawberry": "strawberries",
}
ROT_TERMS = (
    "rotten",
    "rotting",
    "rot",
    "spoiled",
    "spoilt",
    "moldy",
    "mouldy",
    "mold",
    "mould",
    "decayed",
    "overripe",
    "bad",
    "soft spot",
)
FRESH_TERMS = ("fresh", "good", "firm", "ripe", "healthy", "perfect")
MOLD_TERMS = ("mold", "mould", "moldy", "mouldy")
BRUISE_TERMS = ("bruise", "bruised", "damaged", "soft spot")
CRACK_TERMS = ("crack", "cracked", "split", "cut", "deformed")


def analyze_transcript(transcript: str, supported_types: list[str]) -> Finding:
    """Analyze a transcript, falling back to keyword reading on provider failure."""
    cleaned = " ".join(transcript.split())
    if not cleaned:
        return _unknown_finding("")
    try:
        finding = _analyze_with_gemini(cleaned, supported_types)
    except (ReportAIError, ValidationError, ValueError, TypeError, genai.errors.APIError):
        LOGGER.warning("Voice transcript analysis failed; using keyword fallback.")
        finding = _analyze_with_keywords(cleaned, supported_types)
    # Voice notes have no coordinates.
    return finding.model_copy(update={"bbox": None})


def _analyze_with_gemini(transcript: str, supported_types: list[str]) -> Finding:
    """Ask the configured Gemini model for validated structured findings."""
    if not settings.gemini_api_key.get_secret_value():
        raise ReportAIError("GEMINI_API_KEY is not configured for voice analysis.")
    model = settings.gemini_report_model
    LOGGER.info("Voice transcript analysis using model %s.", model)
    client = get_gemini_client(settings.gemini_api_key)
    config = types.GenerateContentConfig(
        system_instruction=ANALYSIS_SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=FINDING_SCHEMA,
        temperature=0,
    )
    contents = types.Content(
        role="user",
        parts=[
            types.Part(
                text=json.dumps(
                    {"transcript": transcript, "active_categories": supported_types},
                    ensure_ascii=False,
                )
            )
        ],
    )
    response = client.models.generate_content(model=model, contents=contents, config=config)
    return Finding.model_validate_json(response.text or "")


def _unknown_finding(observation: str) -> Finding:
    """Build the conservative finding used when no product can be read."""
    return Finding(
        produce_type="unknown",
        detected_item="",
        item_is_fruit=False,
        condition=Condition.UNSURE,
        confidence=0.2,
        visual_observation=observation[:240],
        defect_types=[],
        severity=Severity.NONE,
        bbox=None,
    )


def _find_fruit(text: str) -> str | None:
    """Find the longest edible-fruit name mentioned in the transcript."""
    matches: list[tuple[int, str]] = []
    for slug in ALLOWED_FRUITS:
        forms = {slug.replace("-", " "), slug.replace("-", ""), PLURAL_FORMS.get(slug, f"{slug}s")}
        for form in forms:
            if re.search(rf"\b{re.escape(form)}\b", text):
                matches.append((len(form), slug))
    if not matches:
        return None
    return sorted(matches, key=lambda item: (-item[0], item[1]))[0][1]


def _analyze_with_keywords(transcript: str, supported_types: list[str]) -> Finding:
    """Read obvious product and condition words when the AI provider is unavailable."""
    lowered = transcript.lower()
    fruit = _find_fruit(lowered)
    if fruit is None:
        return _unknown_finding(transcript)

    defects: list[DefectType] = []
    if any(term in lowered for term in MOLD_TERMS):
        defects.append(DefectType.MOLD)
    if any(term in lowered for term in BRUISE_TERMS):
        defects.append(DefectType.BRUISE)
    if any(term in lowered for term in CRACK_TERMS):
        defects.append(DefectType.DEFORMITY)
    if any(term in lowered for term in ("rotten", "rotting", "rot", "spoiled", "spoilt")):
        defects.append(DefectType.ROT)

    if any(term in lowered for term in ROT_TERMS):
        condition = Condition.ROTTEN
        severity = Severity.HIGH
    elif any(term in lowered for term in FRESH_TERMS):
        condition = Condition.FRESH
        severity = Severity.HIGH if defects else Severity.NONE
    else:
        condition = Condition.UNSURE
        severity = Severity.MEDIUM if defects else Severity.NONE

    confidence = 0.8 if condition != Condition.UNSURE else 0.55
    return Finding(
        produce_type=fruit if fruit in supported_types else "unknown",
        detected_item=fruit.replace("-", " ").title(),
        item_is_fruit=True,
        condition=condition,
        confidence=confidence,
        visual_observation=transcript[:240],
        defect_types=list(dict.fromkeys(defects)),
        severity=severity,
        bbox=None,
    )
