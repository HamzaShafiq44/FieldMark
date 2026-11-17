"""Groq vision integration for produce image findings."""

import logging

import groq
from groq import Groq
from PIL import Image
from pydantic import SecretStr, ValidationError

from app.ai.image_encoding import encode_image_base64
from app.ai.prompts import INSPECTION_TEXT, SYSTEM_PROMPT
from app.ai.providers import DetectionError
from app.ai.schemas import Finding

LOGGER = logging.getLogger(__name__)


class GroqVisionDetector:
    """Use Groq's hosted vision model to inspect produce."""

    def __init__(self, api_key: SecretStr, model: str) -> None:
        """Configure the Groq API key and vision model."""
        self.api_key = api_key
        self.model = model

    def __call__(self, image: Image.Image) -> Finding:
        """Analyze an image and validate the model's structured finding."""
        try:
            return Finding.model_validate_json(self._ask_groq(image))
        except (groq.APIError, ValidationError) as exc:
            LOGGER.error("Vision detection failed (%s).", type(exc).__name__)
            raise DetectionError("Vision detection failed.") from exc

    def _ask_groq(self, image: Image.Image) -> str:
        """Submit an image to Groq and return its JSON response content."""
        key = self.api_key.get_secret_value()
        if not key:
            raise DetectionError("GROQ_API_KEY is not configured.")
        image_data = f"data:image/jpeg;base64,{encode_image_base64(image)}"
        response = Groq(api_key=key).chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": INSPECTION_TEXT},
                        {"type": "image_url", "image_url": {"url": image_data}},
                    ],
                },
            ],
        )
        return response.choices[0].message.content or ""
