"""JPEG encode/decode helpers.

Isolated here so the rest of the processing layer deals only in arrays, and so
the live-view pipeline has exactly one place where compression settings live.
"""

from __future__ import annotations

import cv2
import numpy as np

# Quality for frames sent to the browser. High enough that the loupe is honest
# about focus -- compression artefacts at low quality look a lot like grain,
# which would defeat the point of the magnified view.
PREVIEW_JPEG_QUALITY = 88


class DecodeError(ValueError):
    """Raised when bytes could not be decoded as an image."""


def decode_jpeg(payload: bytes) -> np.ndarray:
    """Decode JPEG bytes to a BGR array.

    Raises:
        DecodeError: If the payload is not a decodable image. Live-view streams
            do occasionally deliver a partial frame; callers should skip those
            rather than tearing down the session.
    """
    if not payload:
        raise DecodeError("Empty payload.")
    buffer = np.frombuffer(payload, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise DecodeError(f"Could not decode {len(payload)} bytes as an image.")
    return image


def encode_jpeg(frame: np.ndarray, quality: int = PREVIEW_JPEG_QUALITY) -> bytes:
    """Encode a BGR array as JPEG bytes.

    Raises:
        ValueError: If encoding fails.
    """
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise ValueError("JPEG encoding failed.")
    return buffer.tobytes()
