"""Groq vision integration for product image findings."""

import logging

import groq
from groq import Groq
from PIL import Image
from pydantic import SecretStr, ValidationError

from app.ai.image_encoding import encode_image_base64
from app.ai.prompts import DEFAULT_SUPPORTED_TYPES, INSPECTION_TEXT, get_system_prompt
from app.ai.providers import DetectionError
from app.ai.schemas import Finding

LOGGER = logging.getLogger(__name__)


class GroqVisionDetector:
    """Use Groq's hosted vision model to inspect a product."""

    def __init__(
        self,
        api_key: SecretStr,
        model: str,
        supported_types: list[str] | None = None,
    ) -> None:
        """Configure the Groq API key and vision model.

        Args:
            api_key: Groq API key.
            model: Groq model name.
            supported_types: Product types supported by the inspection pipeline.
                If None, defaults to tomato, apple, banana, and orange.
        """
        self.api_key = api_key
        self.model = model
        self.supported_types = supported_types or list(DEFAULT_SUPPORTED_TYPES)

    def __call__(self, image: Image.Image) -> Finding:
        """Analyze an image and validate the model's structured finding."""
        try:
            prompt = get_system_prompt(self.supported_types)
            return Finding.model_validate_json(self._ask_groq(image, prompt))
        except (groq.APIError, ValidationError) as exc:
            LOGGER.error("Vision detection failed (%s).", type(exc).__name__)
            raise DetectionError("Vision detection failed.") from exc

    def _ask_groq(self, image: Image.Image, system_prompt: str) -> str:
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
                {"role": "system", "content": system_prompt},
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
