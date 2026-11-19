"""Image conversion helpers shared by vision providers."""

import base64
import io

from PIL import Image

MAX_SIDE_PX = 1024
JPEG_QUALITY = 85


def encode_image_bytes(image: Image.Image) -> bytes:
    """Resize and JPEG-encode an image as raw bytes."""
    resized = image.copy()
    resized.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
    buffer = io.BytesIO()
    resized.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def encode_image_base64(image: Image.Image) -> str:
    """Resize and JPEG-encode an image as a base64 string."""
    return base64.b64encode(encode_image_bytes(image)).decode("ascii")
