"""Shared detector contract and provider error."""

from collections.abc import Callable

from PIL import Image

from app.ai.schemas import Finding

VisionDetector = Callable[[Image.Image], Finding]


class DetectionError(RuntimeError):
    """Raised when a configured vision provider cannot return a valid finding."""
