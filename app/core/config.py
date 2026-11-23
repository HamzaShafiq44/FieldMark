"""Application settings loaded from environment variables and a local .env file."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_GROQ_VISION_MODEL = "qwen/qwen3.8-27b"
DEFAULT_GROQ_REPORT_MODEL = "openai/gpt-oss-20b"
DEFAULT_GEMINI_VISION_MODEL = "gemini-4-hydrogen"
DEFAULT_GEMINI_LOCALIZATION_MODEL = "gemini-3.5-flash-lite"
DEFAULT_GEMINI_FALLBACK_MODELS = "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite"
DEFAULT_GEMINI_TRANSCRIPTION_MODEL = "gemini-3.5-transcribe"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VOICE_NOTE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")
GROQ_TIMEOUT_SECONDS = 20.0
GEMINI_TIMEOUT_MS = 20_000
GEMINI_MAX_FALLBACKS = 2
GEMINI_FALLBACK_BACKOFF_SECONDS = 0.5
DEFAULT_INSPECTION_DB_PATH = BASE_DIR / "data" / "inspections.sqlite3"
DEFAULT_PROMPT_TEMPLATES_DIR = BASE_DIR / "app" / "ai" / "prompt_templates"
DEFAULT_FRONTEND_ORIGIN = "http://localhost:3000"


class Settings(BaseSettings):
    """Runtime configuration for image analysis, report writing, and transcription."""

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    vision_provider: Literal["groq", "gemini"] = "groq"
    groq_api_key: SecretStr = SecretStr("")
    groq_vision_model: str = DEFAULT_GROQ_VISION_MODEL
    groq_report_model: str = DEFAULT_GROQ_REPORT_MODEL
    groq_timeout_seconds: float = GROQ_TIMEOUT_SECONDS
    gemini_api_key: SecretStr = SecretStr("")
    gemini_vision_model: str = DEFAULT_GEMINI_VISION_MODEL
    gemini_localization_model: str = DEFAULT_GEMINI_LOCALIZATION_MODEL
    gemini_fallback_models: str = DEFAULT_GEMINI_FALLBACK_MODELS
    gemini_max_fallbacks: int = GEMINI_MAX_FALLBACKS
    gemini_fallback_backoff_seconds: float = GEMINI_FALLBACK_BACKOFF_SECONDS
    gemini_timeout_ms: int = GEMINI_TIMEOUT_MS
    gemini_transcription_model: str = DEFAULT_GEMINI_TRANSCRIPTION_MODEL
    inspection_db_path: Path = DEFAULT_INSPECTION_DB_PATH
    prompt_templates_dir: Path = DEFAULT_PROMPT_TEMPLATES_DIR
    max_image_bytes: int = MAX_IMAGE_BYTES
    max_voice_note_bytes: int = MAX_VOICE_NOTE_BYTES
    max_image_pixels: int = MAX_IMAGE_PIXELS
    allowed_formats: tuple[str, ...] = ALLOWED_FORMATS
    frontend_origin: str = DEFAULT_FRONTEND_ORIGIN
    log_level: str = "INFO"


settings = Settings()
