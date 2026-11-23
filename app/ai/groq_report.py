"""Structured inspection-report writing using the configured Groq model."""

import json
import logging

import groq
from groq import Groq
from pydantic import SecretStr, ValidationError

from app.ai.client_factory import get_groq_client
from app.ai.errors import ReportAIError
from app.ai.prompt_config import load_prompt
from app.ai.schemas import InspectionReportText, InspectionResult, ReportUserType
from app.core.config import DEFAULT_GROQ_REPORT_MODEL, settings

LOGGER = logging.getLogger(__name__)
_report_prompt = load_prompt("report")
FARMER_AUDIENCE_GUIDANCE: str = _report_prompt.farmer_audience_guidance
REPORT_SYSTEM_PROMPT = _report_prompt.system
REPORT_USER_PROMPT = _report_prompt.user
MODEL_UNAVAILABLE_MESSAGE = "The configured Groq model is unavailable."
RATE_LIMITED_MESSAGE = "The Groq service is rate-limited; try again later."
INVALID_REPORT_RESPONSE_MESSAGE = "The report model returned an invalid response format."


def write_inspection_report(
    result: InspectionResult,
    user_type: ReportUserType,
    transcript: str,
) -> InspectionReportText:
    """Generate and validate audience-specific report text."""
    model = settings.groq_report_model or DEFAULT_GROQ_REPORT_MODEL
    LOGGER.info("Report writing using model %s.", model)
    client = _groq_client(settings.groq_api_key)
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": REPORT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _report_request(result, user_type, transcript),
                },
            ],
        )
        content = response.choices[0].message.content or ""
        report_text = InspectionReportText.model_validate_json(content)
    except groq.APIError as exc:
        raise _provider_failure(exc, "Report writing") from exc
    except ValidationError as exc:
        LOGGER.error("Report writing failed (%s).", type(exc).__name__)
        raise ReportAIError(INVALID_REPORT_RESPONSE_MESSAGE) from exc
    except IndexError as exc:
        LOGGER.error("Report writing failed (%s).", type(exc).__name__)
        raise ReportAIError("The report model returned no response.") from exc
    if transcript and report_text.voice_note_relation.value == "not_provided":
        raise ReportAIError("Report did not account for the supplied voice note.")
    if not transcript and report_text.voice_note_relation.value != "not_provided":
        raise ReportAIError("Report referenced a voice note that was not provided.")
    return report_text


def _groq_client(api_key: SecretStr) -> Groq:
    """Return a reusable, time-bounded Groq client for report writing."""
    key = api_key.get_secret_value()
    if not key:
        raise ReportAIError("GROQ_API_KEY is not configured for report generation.")
    return get_groq_client(api_key)


def _provider_failure(exc: groq.APIError, operation: str) -> ReportAIError:
    """Translate Groq failures into actionable messages without exposing provider details."""
    LOGGER.error("%s failed (%s).", operation, type(exc).__name__)
    if isinstance(exc, groq.NotFoundError):
        return ReportAIError(MODEL_UNAVAILABLE_MESSAGE)
    if isinstance(exc, groq.RateLimitError):
        return ReportAIError(RATE_LIMITED_MESSAGE)
    return ReportAIError(f"{operation} failed.")


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
