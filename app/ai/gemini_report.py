"""Structured inspection-report writing with Gemini."""

import json
import logging

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.ai.client_factory import get_gemini_client
from app.ai.errors import ReportAIError
from app.ai.gemini_errors import describe_api_error
from app.ai.prompt_config import load_prompt
from app.ai.schemas import InspectionReportText, InspectionResult, ReportUserType, VoiceNoteRelation
from app.core.config import settings

LOGGER = logging.getLogger(__name__)
_report_prompt = load_prompt("report")
FARMER_AUDIENCE_GUIDANCE: str = _report_prompt.farmer_audience_guidance
REPORT_SYSTEM_PROMPT = _report_prompt.system
REPORT_USER_PROMPT = _report_prompt.user
INVALID_REPORT_RESPONSE_MESSAGE = "The report model returned an invalid response format."

REPORT_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "summary": types.Schema(type=types.Type.STRING),
        "findings": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
        "recommendations": types.Schema(
            type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)
        ),
        "voice_note_relation": types.Schema(
            type=types.Type.STRING,
            enum=[item.value for item in VoiceNoteRelation],
        ),
        "voice_note_conflict": types.Schema(type=types.Type.STRING),
    },
    required=[
        "summary",
        "findings",
        "recommendations",
        "voice_note_relation",
        "voice_note_conflict",
    ],
)


def write_inspection_report(
    result: InspectionResult,
    user_type: ReportUserType,
    transcript: str,
) -> InspectionReportText:
    """Generate and validate audience-specific report text."""
    if not settings.gemini_api_key.get_secret_value():
        raise ReportAIError("GEMINI_API_KEY is not configured for report generation.")
    model = settings.gemini_report_model
    LOGGER.info("Report writing using model %s.", model)
    client = get_gemini_client(settings.gemini_api_key)
    config = types.GenerateContentConfig(
        system_instruction=REPORT_SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=REPORT_SCHEMA,
        temperature=0,
    )
    contents = types.Content(
        role="user",
        parts=[types.Part(text=_report_request(result, user_type, transcript))],
    )
    try:
        response = client.models.generate_content(model=model, contents=contents, config=config)
        report_text = InspectionReportText.model_validate_json(response.text or "")
    except genai.errors.APIError as exc:
        raise ReportAIError(describe_api_error(exc, "Report writing")) from exc
    except ValidationError as exc:
        LOGGER.error("Report writing failed (%s).", type(exc).__name__)
        raise ReportAIError(INVALID_REPORT_RESPONSE_MESSAGE) from exc
    if transcript and report_text.voice_note_relation is VoiceNoteRelation.NOT_PROVIDED:
        raise ReportAIError("Report did not account for the supplied voice note.")
    if not transcript and report_text.voice_note_relation is not VoiceNoteRelation.NOT_PROVIDED:
        raise ReportAIError("Report referenced a voice note that was not provided.")
    return report_text


def _report_request(
    result: InspectionResult,
    user_type: ReportUserType,
    transcript: str,
) -> str:
    """Serialize report evidence separately from its untrusted transcript."""
    return json.dumps(
        {
            "user_type": user_type.value,
            "farmer_audience_guidance": FARMER_AUDIENCE_GUIDANCE,
            "inspection": result.model_dump(mode="json"),
            "inspection_source": "voice_note" if result.voice_note_transcript else "photo",
            "voice_note_transcript": transcript or None,
            "voice_note_is_untrusted_observation": bool(transcript),
            "low_confidence_requires_manual_review": (
                result.grade == "Needs manual review" or result.verdict == "needs_review"
            ),
        },
        ensure_ascii=False,
    )
