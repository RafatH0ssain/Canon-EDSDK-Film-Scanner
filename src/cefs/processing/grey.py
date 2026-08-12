"""Luma, matching OpenCV's exactly.

OpenCV does not use floating-point weights here: it uses fixed-point integers
and a specific rounding, so a float implementation drifts by a count on some
pixels. That drift would be invisible in a preview and would still move the
sharpness metric, which this project reads to four decimal places. So the
fixed-point form is reproduced rather than approximated.
"""

from __future__ import annotations

import numpy as np

#: OpenCV's Rec.601 luma weights in Q14 fixed point: B, G, R.
_B, _G, _R = 1868, 9617, 4899
_HALF = 1 << 13
_SHIFT = 14


def to_grey(image: np.ndarray, *, order: str = "bgr") -> np.ndarray:
    """8-bit luma from a colour image.

    The input dtype is preserved. That is not cosmetic: in the 16-bit develop
    path this result indexes a 65536-entry lookup table, so narrowing it to
    uint8 quietly collapses the image to 256 levels -- which still looks like a
    photograph.

    Args:
        image: ``(h, w, 3)`` of any unsigned integer type. Already 2-D input is
            returned unchanged.
        order: ``"bgr"`` (OpenCV's convention, and this pipeline's for anything
            that came through a JPEG) or ``"rgb"`` (what rawpy hands back).
            Getting this wrong swaps the red and blue weights, which shifts
            every reading slightly without ever looking wrong.

    Returns:
        ``(h, w)`` uint8.
    """
    if image.ndim == 2:
        return image
    if order not in ("bgr", "rgb"):
        raise ValueError(f"order must be 'bgr' or 'rgb', got {order!r}")

    # uint64 so 16-bit inputs cannot overflow the fixed-point multiply.
    channels = image.astype(np.uint64)
    if order == "rgb":
        channels = channels[:, :, ::-1]

    blue, green, red = channels[:, :, 0], channels[:, :, 1], channels[:, :, 2]
    luma = (blue * _B + green * _G + red * _R + _HALF) >> _SHIFT
    return luma.astype(image.dtype)
