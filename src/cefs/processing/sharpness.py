"""A number that rises as focus improves.

Peaking shows where the sharp edges are; this says how sharp, which is what you
want when creeping focus back and forth to find the peak.

The metric is a gated Tenengrad: gradient magnitudes below a noise floor are
zeroed and the mean of the squares reported. Variance of the Laplacian is the
more common choice and was tried first, but on real live-view frames it
separated a sharp frame from a completely defocused one by only 1.22x -- a
compressed stream has a high noise floor and a second derivative amplifies it.
Gating before squaring gives 5.66x on the same pair.

Those separation figures were measured on CCAPI frames. Re-measured on real
EDSDK frames from an EOS R7 -- a focus sweep across best focus, five candidate
metrics scored on the same frames -- this one held up best by a wide margin:

===========================  =========
metric                       max/min
===========================  =========
gated Tenengrad (floor 100)  **11.1x**
gated Tenengrad (floor 40)   2.4x
plain Tenengrad, no gate     2.4x
variance of the Laplacian    2.1x
===========================  =========

So the noise floor of 100 carries across unchanged, and the gating is doing the
work rather than the gradient operator.

Values are relative only, and small in absolute terms on EDSDK frames -- single
digits is normal. Display them with decimals; rounding to integers throws the
signal away. The absolute number depends on subject, exposure and region size,
so comparisons are meaningful within one focusing session and nowhere else.
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
    # Gate before squaring, or many small noise gradients accumulate into a
    # misleadingly high score.
    gated = np.where(strength >= _NOISE_FLOOR, strength, 0.0)
    return float((gated.astype(np.float64) ** 2).mean() / _SCALE)


def sharpness_of_region(
    frame: np.ndarray,
    center_x: float = 0.5,
    center_y: float = 0.5,
    size: float = 0.25,
) -> float:
    """Sharpness of a sub-region given in normalised coordinates.

    Measuring a region matters on a copy stand: a negative sharp in the middle
    and soft at the edges is a levelling problem, and averaging hides it.

    Args:
        frame: BGR or grayscale image.
        center_x: Region centre, 0.0-1.0.
        center_y: Region centre, 0.0-1.0.
        size: Region side length as a fraction of the frame.

    Raises:
        ValueError: If ``size`` is not positive.
    """
    if size <= 0:
        raise ValueError(f"size must be positive, got {size}")

    height, width = frame.shape[:2]
    size = float(np.clip(size, 0.0, 1.0))
    half_w = max(int(width * size / 2), 1)
    half_h = max(int(height * size / 2), 1)

    cx = int(np.clip(center_x, 0.0, 1.0) * width)
    cy = int(np.clip(center_y, 0.0, 1.0) * height)

    # Clamp so a region near an edge slides inward rather than shrinking, which
    # would drop the value simply because fewer pixels were measured.
    left = max(0, min(cx - half_w, width - 2 * half_w))
    top = max(0, min(cy - half_h, height - 2 * half_h))
    return sharpness(frame[top : top + 2 * half_h, left : left + 2 * half_w])


def corner_sharpness(frame: np.ndarray, size: float = 0.18) -> dict[str, float]:
    """Sharpness at each corner and the centre.

    A sensor plane not parallel to the film shows up as corners that differ.
    This is the raw measurement the v0.4 alignment checker builds on.
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
