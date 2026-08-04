"""The magnified focus loupe.

Judging whether film grain is sharp is impossible on a preview scaled down to
fit a browser window, so the loupe crops a region and scales it up.

This only enlarges pixels the camera already discarded when it downscaled the
live-view stream. Where a body supports camera-side live-view zoom, drive that
instead for real sensor detail; the two compose.
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
    """Crop a region around a point and scale it up.

    Args:
        frame: Source image, ``(H, W, 3)``.
        center_x: Horizontal centre, normalised 0.0-1.0.
        center_y: Vertical centre, normalised 0.0-1.0.
        zoom: Magnification. 1.0 returns the whole frame.
        output_size: ``(width, height)`` of the result; defaults to the input's.

    Returns:
        A new array. The crop is clamped to stay inside the frame, so a centre
        near an edge slides inward rather than producing a smaller result --
        checking corner sharpness is one of the loupe's main uses.

    Raises:
        ValueError: If ``zoom`` is not positive.
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
    """Resize to exactly ``width`` x ``height``.

    Nearest-neighbour when enlarging: smooth interpolation would invent detail
    and make an out-of-focus frame look acceptably sharp.
    """
    if frame.shape[1] == width and frame.shape[0] == height:
        return frame.copy()
    enlarging = width > frame.shape[1] or height > frame.shape[0]
    interpolation = cv2.INTER_NEAREST if enlarging else cv2.INTER_AREA
    return cv2.resize(frame, (width, height), interpolation=interpolation)
