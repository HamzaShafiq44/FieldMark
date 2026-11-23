"""Tests for configurable vision providers."""

import json
import logging
from unittest.mock import Mock, patch

import pytest
from google.genai import errors
from PIL import Image
from pydantic import SecretStr

from app.ai.client_factory import get_gemini_transcription_client, get_groq_client
from app.ai.produce_gemini import FINDING_SCHEMA, LOCALIZATION_SCHEMA, GeminiVisionDetector
from app.ai.produce_groq import GroqVisionDetector
from app.ai.prompts import SYSTEM_PROMPT, get_localization_prompt, get_system_prompt
from app.ai.provider_factory import create_vision_detector
from app.ai.providers import DetectionError
from app.ai.schemas import Condition, DefectType, Finding, ProduceType
from app.core.config import Settings, settings


def _image() -> Image.Image:
    """Create an in-memory image for provider request tests."""
    return Image.new("RGB", (8, 8), color="orange")


def test_system_prompt_distinguishes_unripe_from_spoiled() -> None:
    """Include visible ripeness guidance without treating unripe fruit as defective."""
    assert "some ripe varieties keep green areas" in SYSTEM_PROMPT
    assert "unripe fruit may still be fresh" in SYSTEM_PROMPT


def test_prompts_request_coordinates_in_analysis_and_localization() -> None:
    """Ask for a box in analysis, then a focused refinement with the defect details."""
    assert '"bbox": [x1,y1,x2,y2] or null' in SYSTEM_PROMPT
    assert "bbox must tightly enclose" in SYSTEM_PROMPT
    localization = get_localization_prompt("bruise", "A dark sunken patch is visible.")
    assert "bruise" in localization
    assert "A dark sunken patch is visible." in localization
    assert '"bbox": [x1, y1, x2, y2]' in localization


def test_factory_selects_groq_from_configuration() -> None:
    """Select Groq when the provider setting is groq."""
    config = Settings.model_validate({"vision_provider": "groq"})
    assert isinstance(create_vision_detector(config), GroqVisionDetector)


def test_factory_selects_gemini_from_configuration() -> None:
    """Select Gemini with separate analysis and localization models."""
    config = Settings.model_validate(
        {
            "vision_provider": "gemini",
            "gemini_api_key": "test-key",
            "gemini_vision_model": "gemini-4-hydrogen",
            "gemini_localization_model": "gemini-3.5-flash-lite",
        }
    )
    detector = create_vision_detector(config)
    assert isinstance(detector, GeminiVisionDetector)
    assert detector.vision_model == "gemini-4-hydrogen"
    assert detector.localization_model == "gemini-3.5-flash-lite"


def test_groq_without_api_key_fails_clearly() -> None:
    """Reject missing Groq credentials before attempting an API request."""
    detector = GroqVisionDetector(SecretStr(""), "vision-test")
    with pytest.raises(DetectionError, match="GROQ_API_KEY is not configured"):
        detector(_image())


def test_groq_passes_configured_key_and_parses_finding(caplog: pytest.LogCaptureFixture) -> None:
    """Pass the configured secret explicitly to Groq and parse its response."""
    caplog.set_level(logging.INFO)
    finding = {
        "produce_type": "tomato",
        "condition": "fresh",
        "confidence": 0.9,
    }
    response = Mock()
    response.choices = [Mock(message=Mock(content=json.dumps(finding)))]
    client = Mock()
    client.chat.completions.create.return_value = response
    detector = GroqVisionDetector(SecretStr("test-key"), "vision-test")
    with patch("app.ai.client_factory.Groq", return_value=client) as groq_client:
        result = detector(_image())
    groq_client.assert_called_once_with(
        api_key="test-key",
        timeout=settings.groq_timeout_seconds,
        max_retries=1,
    )
    assert "Groq vision analysis using model vision-test" in caplog.text
    request_payload = client.chat.completions.create.call_args.kwargs
    image_url = request_payload["messages"][1]["content"][1]["image_url"]["url"]
    assert request_payload["model"] == "vision-test"
    assert image_url.startswith("data:image/jpeg;base64,")
    assert result.produce_type == "tomato"


def test_gemini_without_api_key_fails_clearly() -> None:
    """Reject missing Gemini credentials before attempting a request."""
    detector = GeminiVisionDetector(SecretStr(""), "gemini-4-hydrogen")
    with pytest.raises(DetectionError, match="GEMINI_API_KEY is not configured"):
        detector(_image())


def test_gemini_sends_structured_config_and_parses_finding() -> None:
    """Force JSON mode with the finding schema and validate the response."""
    finding = {
        "produce_type": "tomato",
        "item_is_fruit": True,
        "condition": "fresh",
        "confidence": 0.9,
        "bbox": None,
    }
    response = Mock()
    response.text = json.dumps(finding)
    client = Mock()
    client.models.generate_content.return_value = response
    detector = GeminiVisionDetector(SecretStr("test-key"), "gemini-4-hydrogen")
    with patch("app.ai.client_factory.genai.Client", return_value=client) as factory:
        result = detector(_image())
    factory.assert_called_once()
    assert factory.call_args.kwargs["api_key"] == "test-key"
    assert factory.call_args.kwargs["http_options"].timeout == settings.gemini_timeout_ms
    request = client.models.generate_content.call_args.kwargs
    assert request["model"] == "gemini-4-hydrogen"
    assert request["config"].response_mime_type == "application/json"
    assert request["config"].response_schema is FINDING_SCHEMA
    assert request["contents"].parts[1].inline_data.mime_type == "image/jpeg"
    assert result.produce_type == "tomato"


def test_gemini_reuses_a_live_client_across_calls() -> None:
    """Keep the SDK client alive instead of letting it close mid-request."""
    response = Mock()
    response.text = json.dumps(
        {
            "produce_type": "tomato",
            "item_is_fruit": True,
            "condition": "fresh",
            "confidence": 0.9,
            "bbox": None,
        }
    )
    client = Mock()
    client.models.generate_content.return_value = response
    detector = GeminiVisionDetector(SecretStr("test-key"), "gemini-4-hydrogen")
    with patch("app.ai.client_factory.genai.Client", return_value=client) as factory:
        detector(_image())
        detector(_image())
    assert factory.call_count == 1


def test_gemini_falls_back_to_other_model_on_capacity_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Retry with fallback models when the primary is overloaded."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(settings, "gemini_fallback_backoff_seconds", 0)
    response = Mock()
    response.text = json.dumps(
        {
            "produce_type": "tomato",
            "item_is_fruit": True,
            "condition": "fresh",
            "confidence": 0.9,
            "bbox": None,
        }
    )
    busy = errors.ServerError(503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}})
    client = Mock()
    client.models.generate_content.side_effect = [busy, response]
    detector = GeminiVisionDetector(
        SecretStr("test-key"),
        "gemini-4-hydrogen",
        fallback_models=["gemini-3.8-flash"],
    )
    with patch("app.ai.client_factory.genai.Client", return_value=client):
        result = detector(_image())
    calls = client.models.generate_content.call_args_list
    assert [call.kwargs["model"] for call in calls] == [
        "gemini-4-hydrogen",
        "gemini-3.8-flash",
    ]
    assert "Gemini request using model gemini-3.8-flash" in caplog.text
    assert result.produce_type == "tomato"


def test_gemini_does_not_fall_back_on_permanent_error() -> None:
    """Keep a bad-request error from burning through fallback models."""
    bad_request = errors.ClientError(
        400, {"error": {"message": "bad request", "status": "INVALID_ARGUMENT"}}
    )
    client = Mock()
    client.models.generate_content.side_effect = bad_request
    detector = GeminiVisionDetector(
        SecretStr("test-key"),
        "gemini-4-hydrogen",
        fallback_models=["gemini-3.8-flash"],
    )
    with (
        patch("app.ai.client_factory.genai.Client", return_value=client),
        pytest.raises(DetectionError, match="Gemini vision detection failed"),
    ):
        detector(_image())
    assert client.models.generate_content.call_count == 1


def test_gemini_fallback_attempts_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bound the number of sequential retry calls on a rate-limited provider."""
    monkeypatch.setattr(settings, "gemini_max_fallbacks", 2)
    monkeypatch.setattr(settings, "gemini_fallback_backoff_seconds", 0)
    busy = errors.ServerError(503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}})
    client = Mock()
    client.models.generate_content.side_effect = busy
    detector = GeminiVisionDetector(
        SecretStr("test-key"),
        "gemini-4-hydrogen",
        fallback_models=["flash-a", "flash-b", "flash-c", "flash-d"],
    )
    with (
        patch("app.ai.client_factory.genai.Client", return_value=client),
        pytest.raises(DetectionError),
    ):
        detector(_image())
    calls = client.models.generate_content.call_args_list
    assert [call.kwargs["model"] for call in calls] == [
        "gemini-4-hydrogen",
        "flash-a",
        "flash-b",
    ]


def test_groq_clients_are_reused_with_bounded_timeout() -> None:
    """Share one time-bounded Groq client across calls instead of rebuilding it."""
    with patch("app.ai.client_factory.Groq") as factory:
        first = get_groq_client(SecretStr("shared-key"))
        second = get_groq_client(SecretStr("shared-key"))
    assert first is second
    factory.assert_called_once_with(
        api_key="shared-key",
        timeout=settings.groq_timeout_seconds,
        max_retries=1,
    )


def test_gemini_transcription_client_is_cached_with_v1beta() -> None:
    """Share one bounded v1beta client for all voice-note transcription."""
    with patch("app.ai.client_factory.genai.Client") as factory:
        first = get_gemini_transcription_client(SecretStr("transcribe-key"))
        second = get_gemini_transcription_client(SecretStr("transcribe-key"))
    assert first is second
    factory.assert_called_once()
    options = factory.call_args.kwargs["http_options"]
    assert options.api_version == "v1beta"
    assert options.timeout == settings.gemini_timeout_ms


def test_gemini_locate_defect_uses_localization_model_and_schema() -> None:
    """Use the cheaper localization model for the second-stage box request."""
    response = Mock()
    response.text = '{"bbox": [0.1, 0.2, 0.6, 0.7]}'
    client = Mock()
    client.models.generate_content.return_value = response
    detector = GeminiVisionDetector(
        SecretStr("test-key"), "gemini-4-hydrogen", "gemini-3.5-flash-lite"
    )
    with patch("app.ai.client_factory.genai.Client", return_value=client):
        bbox = detector.locate_defect(_image(), _defective_finding())
    request = client.models.generate_content.call_args.kwargs
    assert request["model"] == "gemini-3.5-flash-lite"
    assert request["config"].response_schema is LOCALIZATION_SCHEMA
    assert bbox == (0.1, 0.2, 0.6, 0.7)


def _defective_finding() -> Finding:
    """Build a finding with a localized defect for provider tests."""
    return Finding(
        produce_type=ProduceType.TOMATO,
        item_is_fruit=True,
        condition=Condition.FRESH,
        confidence=0.9,
        defect_types=[DefectType.BRUISE],
        visual_observation="A dark sunken patch is visible.",
    )


def test_groq_locate_defect_returns_parsed_bbox() -> None:
    """Parse the second-stage Groq response into normalized coordinates."""
    response = Mock()
    response.choices = [Mock(message=Mock(content='{"bbox": [0.1, 0.2, 0.5, 0.6]}'))]
    client = Mock()
    client.chat.completions.create.return_value = response
    detector = GroqVisionDetector(SecretStr("test-key"), "vision-test")
    with patch("app.ai.client_factory.Groq", return_value=client):
        bbox = detector.locate_defect(_image(), _defective_finding())
    assert bbox == (0.1, 0.2, 0.5, 0.6)
    user_text = client.chat.completions.create.call_args.kwargs["messages"][1]["content"][0]["text"]
    assert "locate" in user_text.lower()


def test_localization_ignores_malformed_bbox() -> None:
    """Discard coordinate sets that are not normalized and ordered."""
    from app.ai.schemas import DefectLocalization

    assert DefectLocalization.model_validate({"bbox": [0.9, 0.1, 0.2, 0.8]}).bbox is None
    assert DefectLocalization.model_validate({"bbox": [1.2, 0.1, 1.4, 0.8]}).bbox is None


def test_prompt_is_built_from_configured_categories() -> None:
    """Build the prompt dynamically and request any fruit, not only the categories."""
    prompt = get_system_prompt(["guava", "mango"])
    assert "guava, and mango" in prompt
    assert '"guava"|"mango"' in prompt
    assert "item_is_fruit" in prompt
    assert "detected_item" in prompt
