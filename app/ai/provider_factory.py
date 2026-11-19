"""Create a vision detector from application configuration."""

from app.ai.produce_gemini import GeminiVisionDetector
from app.ai.produce_groq import GroqVisionDetector
from app.ai.produce_ollama import OllamaVisionDetector
from app.ai.providers import VisionDetector
from app.core.config import Settings, settings


def create_vision_detector(
    config: Settings | None = None,
    supported_types: list[str] | None = None,
) -> VisionDetector:
    """Build the configured provider without performing network requests.

    Args:
        config: Application settings. If None, uses default settings.
        supported_types: Product types supported by the inspection pipeline.
            If None, uses the default tomato, apple, banana, and orange list.
    """
    selected_settings = config if config is not None else settings
    if selected_settings.vision_provider == "groq":
        return GroqVisionDetector(
            selected_settings.groq_api_key,
            selected_settings.groq_vision_model,
            supported_types=supported_types,
        )
    if selected_settings.vision_provider == "ollama":
        return OllamaVisionDetector(
            selected_settings.ollama_base_url,
            selected_settings.ollama_vision_model,
            supported_types=supported_types,
        )
    if selected_settings.vision_provider == "gemini":
        return GeminiVisionDetector(
            selected_settings.gemini_api_key,
            selected_settings.gemini_vision_model,
            localization_model=selected_settings.gemini_localization_model,
            fallback_models=[
                item.strip()
                for item in selected_settings.gemini_fallback_models.split(",")
                if item.strip()
            ],
            supported_types=supported_types,
        )
    raise ValueError(f"Unsupported vision provider: {selected_settings.vision_provider}")
