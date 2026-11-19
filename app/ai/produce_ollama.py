"""Local Ollama vision provider for product inspection."""

import json
import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

from PIL import Image
from pydantic import BaseModel, ValidationError

from app.ai.image_encoding import encode_image_base64
from app.ai.prompts import DEFAULT_SUPPORTED_TYPES, INSPECTION_TEXT, get_system_prompt
from app.ai.providers import DetectionError
from app.ai.schemas import Finding

LOGGER = logging.getLogger(__name__)
OLLAMA_TIMEOUT_SECONDS = 120
MAX_RESPONSE_BYTES = 64_000


class _OllamaMessage(BaseModel):
    """Ollama chat message containing the model response."""

    content: str


class _OllamaResponse(BaseModel):
    """Validated envelope returned by the Ollama chat API."""

    message: _OllamaMessage


class OllamaVisionDetector:
    """Use a locally hosted Ollama vision model to inspect a product."""

    def __init__(
        self,
        base_url: str,
        model: str,
        supported_types: list[str] | None = None,
    ) -> None:
        """Configure the local Ollama endpoint and vision model."""
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.supported_types = supported_types or list(DEFAULT_SUPPORTED_TYPES)

    def __call__(self, image: Image.Image) -> Finding:
        """Analyze an image and validate the model's structured finding."""
        try:
            response = _OllamaResponse.model_validate_json(self._request(image))
            return Finding.model_validate_json(response.message.content)
        except (OSError, TimeoutError, URLError, ValidationError) as exc:
            LOGGER.error("Local vision detection failed (%s).", type(exc).__name__)
            raise DetectionError("Local vision detection failed.") from exc

    def _request(self, image: Image.Image) -> bytes:
        """Send an image to Ollama and return the response body."""
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": get_system_prompt(self.supported_types)},
                {
                    "role": "user",
                    "content": INSPECTION_TEXT,
                    "images": [encode_image_base64(image)],
                },
            ],
        }
        request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise DetectionError("Local vision response exceeded the size limit.")
        return body
