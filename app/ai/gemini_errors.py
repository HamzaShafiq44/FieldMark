"""Shared helpers for interpreting Gemini API errors."""

import re

from google import genai

QUOTA_EXHAUSTED_MESSAGE = (
    "Gemini daily quota exhausted. Enable billing or use a different API key, then try again later."
)
RATE_LIMITED_MESSAGE = "The Gemini service is rate-limited; try again later."
MODEL_UNAVAILABLE_MESSAGE = "The configured Gemini model is unavailable."
_LONG_RETRY_SECONDS = 60 * 60


def is_quota_exhausted(exc: genai.errors.APIError) -> bool:
    """Tell a spent daily quota from a short-lived rate limit.

    All Gemini models share the free-tier allowance, so retrying other models
    only adds delay once the daily quota is gone.
    """
    if exc.code != 429:
        return False
    payload = str(getattr(exc, "details", "")).lower()
    status = str(getattr(exc, "status", "")).lower()
    if "resource_exhausted" not in status and "resource_exhausted" not in payload:
        return False
    match = re.search(r"retrydelay['\"]?\s*[:=]\s*['\"]?(\d+)s", payload)
    if match and int(match.group(1)) >= _LONG_RETRY_SECONDS:
        return True
    return "freetier" in payload or "perday" in payload


def describe_api_error(exc: genai.errors.APIError, operation: str) -> str:
    """Return an actionable, credential-free message for a failed Gemini call."""
    if is_quota_exhausted(exc):
        return QUOTA_EXHAUSTED_MESSAGE
    if exc.code == 429:
        return RATE_LIMITED_MESSAGE
    if exc.code == 404:
        return MODEL_UNAVAILABLE_MESSAGE
    return f"{operation} failed."
