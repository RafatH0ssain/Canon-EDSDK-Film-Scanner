"""JPEG encode/decode, so the rest of the processing layer deals only in arrays."""

from __future__ import annotations

import cv2
import numpy as np

# High enough that the loupe stays honest: low-quality artefacts look like
# grain, which would defeat the point of the magnified view.
PREVIEW_JPEG_QUALITY = 88


class DecodeError(ValueError):
    """Raised when bytes could not be decoded as an image."""


def decode_jpeg(payload: bytes) -> np.ndarray:
    """Decode JPEG bytes to a BGR array.

    Live view occasionally delivers a partial frame; skip those rather than
    tearing the session down.
    """
    if not payload:
        raise DecodeError("Empty payload.")
    buffer = np.frombuffer(payload, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise DecodeError(f"Could not decode {len(payload)} bytes as an image.")
    return image


def encode_jpeg(frame: np.ndarray, quality: int = PREVIEW_JPEG_QUALITY) -> bytes:
    """Encode a BGR array as JPEG bytes."""
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise ValueError("JPEG encoding failed.")
    return buffer.tobytes()
