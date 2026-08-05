"""A number that rises as focus improves.

A gated Tenengrad: gradient magnitudes below a noise floor are zeroed, then the
mean of the squares. Measured on an R7 focus sweep it separated best from worst
focus by **11.1x**, against 2.4x ungated and 2.1x for variance of the Laplacian
-- the gating does the work, not the gradient operator.

Relative only, and single digits is normal, so display with decimals. The value
depends on subject, exposure and region size, so it means something within one
focusing session and nowhere else.
"""

from __future__ import annotations

import numpy as np

from cefs.processing.peaking import edge_strength

_NOISE_FLOOR = 100.0

# Readability only; has no effect on comparisons.
_SCALE = 1000.0


def sharpness(frame: np.ndarray) -> float:
    """Relative sharpness of a frame. Higher is sharper."""
    strength = edge_strength(frame)
    # Gate before squaring, or small noise gradients add up to a high score.
    gated = np.where(strength >= _NOISE_FLOOR, strength, 0.0)
    return float((gated.astype(np.float64) ** 2).mean() / _SCALE)


def sharpness_of_region(
    frame: np.ndarray,
    center_x: float = 0.5,
    center_y: float = 0.5,
    size: float = 0.25,
) -> float:
    """Sharpness of a sub-region, ``size`` being its side as a fraction of the frame.

    Regions matter on a copy stand: sharp in the middle and soft at the edges is
    a levelling problem, and averaging hides it.
    """
    if size <= 0:
        raise ValueError(f"size must be positive, got {size}")

    height, width = frame.shape[:2]
    size = float(np.clip(size, 0.0, 1.0))
    half_w = max(int(width * size / 2), 1)
    half_h = max(int(height * size / 2), 1)

    cx = int(np.clip(center_x, 0.0, 1.0) * width)
    cy = int(np.clip(center_y, 0.0, 1.0) * height)

    # Slide inward near an edge rather than shrinking, which would drop the
    # value just because fewer pixels were measured.
    left = max(0, min(cx - half_w, width - 2 * half_w))
    top = max(0, min(cy - half_h, height - 2 * half_h))
    return sharpness(frame[top : top + 2 * half_h, left : left + 2 * half_w])


def corner_sharpness(frame: np.ndarray, size: float = 0.18) -> dict[str, float]:
    """Sharpness at each corner and the centre.

    A sensor plane not parallel to the film shows up as corners that differ.
    """
    inset = size / 2 + 0.02
    points = {
        "top_left": (inset, inset),
        "top_right": (1.0 - inset, inset),
        "bottom_left": (inset, 1.0 - inset),
        "bottom_right": (1.0 - inset, 1.0 - inset),
        "center": (0.5, 0.5),
    }
    return {
        name: sharpness_of_region(frame, x, y, size) for name, (x, y) in points.items()
    }
