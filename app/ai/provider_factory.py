"""Create the Gemini vision detector used by the inspection pipeline."""

from app.ai.produce_gemini import GeminiVisionDetector
from app.ai.providers import VisionDetector
from app.core.config import Settings, settings


def create_vision_detector(
    config: Settings | None = None,
    supported_types: list[str] | None = None,
) -> VisionDetector:
    """Build the configured Gemini detector without performing network requests.

    Args:
        config: Application settings. If None, uses default settings.
        supported_types: Product types supported by the inspection pipeline.
            If None, the detector uses the default category list.
    """
    selected = config if config is not None else settings
    return GeminiVisionDetector(
        selected.gemini_api_key,
        selected.gemini_vision_model,
        localization_model=selected.gemini_localization_model,
        fallback_models=[
            item.strip() for item in selected.gemini_fallback_models.split(",") if item.strip()
        ],
        supported_types=supported_types,
    )
