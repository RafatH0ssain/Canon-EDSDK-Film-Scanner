"""Focus peaking: highlight the sharpest edges.

In software, not the camera's own: EDSDK's stream carries no overlays, and the
body's peaking needs manual focus while EDSDK's focus drive needs autofocus.
Doing it here means remote focus and peaking work at once.

Runs at native resolution, before the loupe. Nearest-neighbour enlargement
leaves flat blocks whose only edges are block boundaries, so peaking after zoom
would get sparser the further you zoom in.
"""

from __future__ import annotations

import cv2
import numpy as np

DEFAULT_PEAK_COLOR = (0, 0, 255)  # BGR

# Scharr-magnitude thresholds at each end of the sensitivity range. Absolute,
# not percentiles of the frame's own edges: a percentile marks a fixed fraction
# of pixels by construction, reporting the same coverage on mush as on a sharp
# frame. Calibrated on synthetic negatives and real R7 frames.
_MAX_THRESHOLD = 1200.0
_MIN_THRESHOLD = 150.0


def edge_strength(frame: np.ndarray) -> np.ndarray:
    """Per-pixel edge magnitude as float32.

    Scharr rather than Sobel: the more accurate 3x3 approximation, which matters
    for film grain, which is fine and has no preferred orientation.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    gray = gray.astype(np.float32)
    dx = cv2.Scharr(gray, cv2.CV_32F, 1, 0)
    dy = cv2.Scharr(gray, cv2.CV_32F, 0, 1)
    return cv2.magnitude(dx, dy)


def threshold_for(sensitivity: float) -> float:
    """Edge-strength threshold for a sensitivity in 0.0-1.0.

    Interpolated geometrically: edge strength spans about an order of magnitude,
    so a linear sweep would spend most of the slider doing nothing visible.
    """
    sensitivity = float(np.clip(sensitivity, 0.0, 1.0))
    return _MAX_THRESHOLD * (_MIN_THRESHOLD / _MAX_THRESHOLD) ** sensitivity


def peaking_mask(frame: np.ndarray, sensitivity: float = 0.5) -> np.ndarray:
    """Boolean mask of the pixels considered in focus.

    Args:
        frame: BGR or grayscale image.
        sensitivity: 0.0 marks only the sharpest edges, 1.0 marks far more.
    """
    return edge_strength(frame) >= threshold_for(sensitivity)


def apply_peaking(
    frame: np.ndarray,
    sensitivity: float = 0.5,
    color: tuple[int, int, int] = DEFAULT_PEAK_COLOR,
    opacity: float = 1.0,
) -> np.ndarray:
    """Return a copy of ``frame`` with in-focus edges highlighted.

    Args:
        frame: BGR image.
        sensitivity: See :func:`peaking_mask`.
        color: Overlay colour as BGR.
        opacity: 1.0 replaces the pixel; lower values blend.

    Raises:
        ValueError: If the frame is not colour.
    """
    if frame.ndim != 3:
        raise ValueError("apply_peaking expects a colour (BGR) image.")

    mask = peaking_mask(frame, sensitivity)
    out = frame.copy()
    opacity = float(np.clip(opacity, 0.0, 1.0))
    if opacity >= 1.0:
        out[mask] = color
    else:
        overlay = np.array(color, dtype=np.float32)
        blended = out[mask].astype(np.float32) * (1.0 - opacity) + overlay * opacity
        out[mask] = blended.astype(np.uint8)
    return out


def peaking_coverage(frame: np.ndarray, sensitivity: float = 0.5) -> float:
    """Fraction of the frame marked in focus, 0.0-1.0."""
    return float(peaking_mask(frame, sensitivity).mean())
