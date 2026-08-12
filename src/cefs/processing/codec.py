"""JPEG encode/decode, so the rest of the processing layer deals only in arrays."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

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
    try:
        with Image.open(io.BytesIO(payload)) as image:
            rgb = np.asarray(image.convert("RGB"))
    except Exception as exc:  # Pillow raises several unrelated types
        raise DecodeError(f"Could not decode {len(payload)} bytes as an image.") from exc
    # BGR, because that is what the rest of this layer has always dealt in and
    # what every downstream index assumes. Pillow hands back RGB.
    return np.ascontiguousarray(rgb[:, :, ::-1])


def encode_jpeg(frame: np.ndarray, quality: int = PREVIEW_JPEG_QUALITY) -> bytes:
    """Encode a BGR array as JPEG bytes."""
    if frame.ndim == 3:
        frame = frame[:, :, ::-1]  # BGR in, RGB for the encoder
    image = Image.fromarray(np.ascontiguousarray(frame))
    buffer = io.BytesIO()
    # 4:2:0 by default in Pillow at this quality, which is what OpenCV writes
    # too; subsampling=0 would change the file for no visible gain.
    image.save(buffer, format="JPEG", quality=int(quality))
    return buffer.getvalue()
