"""Tests for configurable vision providers."""

import json
from io import BytesIO
from unittest.mock import Mock, patch
from urllib.error import URLError

import pytest
from PIL import Image
from pydantic import SecretStr, ValidationError

from app.ai.produce_groq import GroqVisionDetector
from app.ai.produce_ollama import MAX_RESPONSE_BYTES, OllamaVisionDetector
from app.ai.provider_factory import create_vision_detector
from app.ai.providers import DetectionError
from app.core.config import Settings


def _image() -> Image.Image:
    """Create an in-memory image for provider request tests."""
    return Image.new("RGB", (8, 8), color="orange")


def test_factory_selects_groq_from_configuration() -> None:
    """Select Groq when the provider setting is groq."""
    config = Settings.model_validate({"vision_provider": "groq"})
    assert isinstance(create_vision_detector(config), GroqVisionDetector)


def test_factory_selects_ollama_from_configuration() -> None:
    """Select the local provider and use its configured endpoint and model."""
    config = Settings.model_validate(
        {
            "vision_provider": "ollama",
            "ollama_base_url": "http://ollama.local:11434",
            "ollama_vision_model": "vision-test",
        }
    )
    detector = create_vision_detector(config)
    assert isinstance(detector, OllamaVisionDetector)
    assert detector.base_url == "http://ollama.local:11434"
    assert detector.model == "vision-test"


def test_settings_reject_invalid_ollama_url() -> None:
    """Reject non-HTTP endpoint configuration before serving requests."""
    with pytest.raises(ValidationError, match="OLLAMA_BASE_URL"):
        Settings.model_validate({"ollama_base_url": "file:///tmp/ollama"})


def test_groq_without_api_key_fails_clearly() -> None:
    """Reject missing Groq credentials before attempting an API request."""
    detector = GroqVisionDetector(SecretStr(""), "vision-test")
    with pytest.raises(DetectionError, match="GROQ_API_KEY is not configured"):
        detector(_image())


def test_groq_passes_configured_key_and_parses_finding() -> None:
    """Pass the configured secret explicitly to Groq and parse its response."""
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
    with patch("app.ai.produce_groq.Groq", return_value=client) as groq_client:
        result = detector(_image())
    groq_client.assert_called_once_with(api_key="test-key")
    request_payload = client.chat.completions.create.call_args.kwargs
    image_url = request_payload["messages"][1]["content"][1]["image_url"]["url"]
    assert request_payload["model"] == "vision-test"
    assert image_url.startswith("data:image/jpeg;base64,")
    assert result.produce_type.value == "tomato"


def test_ollama_sends_image_and_parses_finding() -> None:
    """Send a JSON-mode image request and validate the model finding."""
    finding = {
        "produce_type": "orange",
        "condition": "fresh",
        "confidence": 0.9,
        "visual_observation": "The rind appears evenly colored.",
    }
    response_body = json.dumps({"message": {"content": json.dumps(finding)}}).encode()
    detector = OllamaVisionDetector("http://localhost:11434", "vision-test")
    with patch("app.ai.produce_ollama.urlopen", return_value=BytesIO(response_body)) as request:
        result = detector(_image())
    request_payload = json.loads(request.call_args.args[0].data)
    assert request_payload["model"] == "vision-test"
    assert request_payload["messages"][1]["images"]
    assert result.produce_type.value == "orange"


def test_ollama_network_failure_is_a_detection_error() -> None:
    """Translate a local provider connection failure to the shared provider error."""
    detector = OllamaVisionDetector("http://localhost:11434", "vision-test")
    with (
        patch("app.ai.produce_ollama.urlopen", side_effect=URLError("offline")),
        pytest.raises(DetectionError, match="Local vision detection failed"),
    ):
        detector(_image())


def test_ollama_rejects_oversized_response() -> None:
    """Reject unusually large local model responses."""
    detector = OllamaVisionDetector("http://localhost:11434", "vision-test")
    large_response = BytesIO(b"x" * (MAX_RESPONSE_BYTES + 1))
    with (
        patch("app.ai.produce_ollama.urlopen", return_value=large_response),
        pytest.raises(DetectionError, match="response exceeded the size limit"),
    ):
        detector(_image())
