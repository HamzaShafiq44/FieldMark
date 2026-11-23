"""Reusable AI SDK clients with bounded request timeouts.

Building a client per request forces a fresh TLS handshake, and leaving the SDK
timeout unset lets a stalled provider hold an inspection open indefinitely.
One cached client per credential keeps connections warm and caps how long any
single call can block a worker thread.
"""

from functools import lru_cache

from google import genai
from google.genai import types
from groq import Groq
from pydantic import SecretStr

from app.core.config import settings


@lru_cache(maxsize=8)
def _groq_client(api_key: str) -> Groq:
    """Create one authenticated Groq client per key."""
    return Groq(
        api_key=api_key,
        timeout=settings.groq_timeout_seconds,
        max_retries=1,
    )


@lru_cache(maxsize=8)
def _gemini_client(api_key: str) -> genai.Client:
    """Create one authenticated Gemini client per key.

    The Google SDK expresses its timeout in milliseconds.
    """
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(settings.gemini_timeout_ms)),
    )


@lru_cache(maxsize=8)
def _gemini_transcription_client(api_key: str) -> genai.Client:
    """Create the v1beta client used by Gemini transcription."""
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            api_version="v1beta",
            timeout=int(settings.gemini_timeout_ms),
        ),
    )


def get_groq_client(api_key: SecretStr) -> Groq:
    """Return a reusable Groq client for the configured credential."""
    return _groq_client(api_key.get_secret_value())


def get_gemini_client(api_key: SecretStr) -> genai.Client:
    """Return a reusable Gemini client for the configured credential."""
    return _gemini_client(api_key.get_secret_value())


def get_gemini_transcription_client(api_key: SecretStr) -> genai.Client:
    """Return a reusable v1beta Gemini client for voice-note transcription."""
    return _gemini_transcription_client(api_key.get_secret_value())


def clear_client_cache() -> None:
    """Drop cached clients (used by tests and when credentials change)."""
    _groq_client.cache_clear()
    _gemini_client.cache_clear()
    _gemini_transcription_client.cache_clear()
