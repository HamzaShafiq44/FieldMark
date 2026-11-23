"""Google Gemini vision provider using structured JSON output."""

import logging
import time
from collections.abc import Sequence

from google import genai
from google.genai import types
from PIL import Image
from pydantic import SecretStr, ValidationError

from app.ai.client_factory import get_gemini_client
from app.ai.image_encoding import encode_image_bytes
from app.ai.prompts import (
    DEFAULT_SUPPORTED_TYPES,
    INSPECTION_TEXT,
    LOCALIZATION_TEXT,
    get_localization_prompt,
    get_system_prompt,
)
from app.ai.providers import DetectionError, defect_summary
from app.ai.schemas import BBox, DefectLocalization, Finding
from app.core.config import settings

LOGGER = logging.getLogger(__name__)
TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _is_transient(exc: genai.errors.APIError) -> bool:
    """Return whether a provider error is a temporary capacity/rate issue."""
    return exc.code in TRANSIENT_STATUS_CODES


def _enum(*values: str) -> types.Schema:
    """Build a constrained string schema for a closed set of labels."""
    return types.Schema(type=types.Type.STRING, enum=list(values))


_BBOX = types.Schema(
    type=types.Type.ARRAY,
    items=types.Schema(type=types.Type.NUMBER),
    min_items=4,
    max_items=4,
    nullable=True,
)
FINDING_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "produce_type": types.Schema(type=types.Type.STRING),
        "detected_item": types.Schema(type=types.Type.STRING),
        "item_is_fruit": types.Schema(type=types.Type.BOOLEAN),
        "condition": _enum("fresh", "rotten", "unsure"),
        "confidence": types.Schema(type=types.Type.NUMBER),
        "visual_observation": types.Schema(type=types.Type.STRING),
        "defect_types": types.Schema(
            type=types.Type.ARRAY,
            items=_enum("bruise", "rot", "mold", "deformity"),
        ),
        "severity": _enum("none", "low", "medium", "high"),
        "bbox": _BBOX,
    },
    required=[
        "produce_type",
        "detected_item",
        "item_is_fruit",
        "condition",
        "confidence",
        "visual_observation",
        "defect_types",
        "severity",
        "bbox",
    ],
)
LOCALIZATION_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={"bbox": _BBOX},
    required=["bbox"],
)


class GeminiVisionDetector:
    """Use a Gemini model with a JSON response schema for reliable findings."""

    def __init__(
        self,
        api_key: SecretStr,
        vision_model: str,
        localization_model: str | None = None,
        supported_types: list[str] | None = None,
        fallback_models: Sequence[str] = (),
    ) -> None:
        """Configure the API key, models, and inspection categories."""
        self.api_key = api_key
        self.vision_model = vision_model
        self.localization_model = localization_model or vision_model
        self.supported_types = supported_types or list(DEFAULT_SUPPORTED_TYPES)
        self.fallback_models = [item for item in fallback_models if item]
        # The SDK closes its HTTP transport in __del__, so the client must be
        # kept alive for as long as requests can be made.
        self._client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        """Return a live client, creating it once per detector instance."""
        key = self.api_key.get_secret_value()
        if not key:
            raise DetectionError("GEMINI_API_KEY is not configured.")
        if self._client is None:
            self._client = get_gemini_client(self.api_key)
        return self._client

    def __call__(self, image: Image.Image) -> Finding:
        """Analyze an image and validate the structured Gemini response."""
        try:
            content = self._ask(
                image,
                self.vision_model,
                get_system_prompt(self.supported_types),
                INSPECTION_TEXT,
                FINDING_SCHEMA,
            )
            return Finding.model_validate_json(content)
        except (genai.errors.APIError, ValidationError, ValueError) as exc:
            LOGGER.error("Gemini vision detection failed (%s).", type(exc).__name__)
            raise DetectionError("Gemini vision detection failed.") from exc

    def locate_defect(self, image: Image.Image, finding: Finding) -> BBox | None:
        """Locate the reported defect in a dedicated second Gemini call."""
        prompt = get_localization_prompt(defect_summary(finding), finding.visual_observation)
        try:
            content = self._ask(
                image,
                self.localization_model,
                prompt,
                LOCALIZATION_TEXT,
                LOCALIZATION_SCHEMA,
            )
            return DefectLocalization.model_validate_json(content).bbox
        except (genai.errors.APIError, ValidationError, ValueError) as exc:
            LOGGER.error("Gemini defect localization failed (%s).", type(exc).__name__)
            raise DetectionError("Gemini defect localization failed.") from exc

    def _ask(
        self,
        image: Image.Image,
        model: str,
        system_prompt: str,
        user_text: str,
        schema: types.Schema,
    ) -> str:
        """Send a structured vision request, retrying other models on capacity errors."""
        client = self._get_client()
        image_data = encode_image_bytes(image)
        content = types.Content(
            role="user",
            parts=[
                types.Part(text=user_text),
                types.Part(inline_data=types.Blob(data=image_data, mime_type="image/jpeg")),
            ],
        )
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0,
        )
        # Cap fallback attempts so a rate-limited primary cannot turn one
        # inspection into a long chain of sequential network calls.
        max_fallbacks = max(0, settings.gemini_max_fallbacks)
        candidates = [model, *(item for item in self.fallback_models if item != model)][
            : 1 + max_fallbacks
        ]
        last_error: genai.errors.APIError | None = None
        for index, candidate in enumerate(candidates):
            LOGGER.info("Gemini request using model %s.", candidate)
            try:
                response = client.models.generate_content(
                    model=candidate, contents=content, config=config
                )
                return response.text or ""
            except genai.errors.APIError as exc:
                if not _is_transient(exc):
                    raise
                last_error = exc
                LOGGER.warning(
                    "Gemini model %s unavailable (HTTP %s); trying next model.",
                    candidate,
                    exc.code,
                )
                if index + 1 < len(candidates) and settings.gemini_fallback_backoff_seconds > 0:
                    time.sleep(settings.gemini_fallback_backoff_seconds)
        if last_error is not None:
            raise last_error
        return ""
