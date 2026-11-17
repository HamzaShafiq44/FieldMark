"""Application settings loaded from environment variables and a local .env file."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
DEFAULT_GROQ_TRANSCRIPTION_MODEL = "whisper-large-v3-turbo"
DEFAULT_OLLAMA_VISION_MODEL = "qwen2.5vl:7b"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_URL_SCHEMES = frozenset({"http", "https"})
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VOICE_NOTE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")
DEFAULT_INSPECTION_DB_PATH = BASE_DIR / "data" / "inspections.sqlite3"


class Settings(BaseSettings):
    """Runtime configuration for image analysis, report writing, and transcription."""

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    vision_provider: Literal["groq", "ollama"] = "groq"
    groq_api_key: SecretStr = SecretStr("")
    groq_vision_model: str = DEFAULT_GROQ_VISION_MODEL
    groq_report_model: str = ""
    groq_transcription_model: str = DEFAULT_GROQ_TRANSCRIPTION_MODEL
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    ollama_vision_model: str = DEFAULT_OLLAMA_VISION_MODEL
    inspection_db_path: Path = DEFAULT_INSPECTION_DB_PATH
    max_image_bytes: int = MAX_IMAGE_BYTES
    max_voice_note_bytes: int = MAX_VOICE_NOTE_BYTES
    max_image_pixels: int = MAX_IMAGE_PIXELS
    allowed_formats: tuple[str, ...] = ALLOWED_FORMATS

    @field_validator("ollama_base_url")
    @classmethod
    def validate_ollama_base_url(cls, value: str) -> str:
        """Require an absolute HTTP or HTTPS URL for the local provider."""
        parsed_url = urlsplit(value)
        if parsed_url.scheme not in OLLAMA_URL_SCHEMES or not parsed_url.netloc:
            raise ValueError("OLLAMA_BASE_URL must be an absolute HTTP or HTTPS URL.")
        return value.rstrip("/")


settings = Settings()
