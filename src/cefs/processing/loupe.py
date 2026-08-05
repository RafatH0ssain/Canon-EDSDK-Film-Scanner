"""The magnified focus loupe: crop a region and scale it up.

Only enlarges pixels the camera already discarded downscaling the live-view
stream. Camera-side zoom would give real sensor detail, but an R7 does not
offer it over EDSDK.
"""

from __future__ import annotations

import cv2
import numpy as np


def crop_zoom(
    frame: np.ndarray,
    center_x: float = 0.5,
    center_y: float = 0.5,
    zoom: float = 2.0,
    output_size: tuple[int, int] | None = None,
) -> np.ndarray:
    """Crop around a normalised point and scale up to ``output_size``.

    The crop slides inward near an edge rather than shrinking -- checking corner
    sharpness is one of the loupe's main uses.
    """
    if zoom <= 0:
        raise ValueError(f"zoom must be positive, got {zoom}")

    height, width = frame.shape[:2]
    target_w, target_h = output_size if output_size is not None else (width, height)

    if zoom <= 1.0:
        return _resize(frame, target_w, target_h)

    crop_w = max(round(width / zoom), 1)
    crop_h = max(round(height / zoom), 1)

    left = round(np.clip(center_x, 0.0, 1.0) * width - crop_w / 2)
    top = round(np.clip(center_y, 0.0, 1.0) * height - crop_h / 2)
    left = max(0, min(left, width - crop_w))
    top = max(0, min(top, height - crop_h))

    region = frame[top : top + crop_h, left : left + crop_w]
    return _resize(region, target_w, target_h)


def _resize(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    """Nearest-neighbour when enlarging: smooth interpolation invents
    detail and makes an out-of-focus frame look sharp."""
    if frame.shape[1] == width and frame.shape[0] == height:
        return frame.copy()
    enlarging = width > frame.shape[1] or height > frame.shape[0]
    interpolation = cv2.INTER_NEAREST if enlarging else cv2.INTER_AREA
    return cv2.resize(frame, (width, height), interpolation=interpolation)
