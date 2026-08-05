"""The plain linear flip.

Kept as the preview's "Linear" method so the real inversion in
:mod:`cefs.processing.film` can be compared against it side by side. On colour
negatives it comes out cyan: it inverts the orange mask along with the image.
"""

from __future__ import annotations

import numpy as np

_MAX_8BIT = 255


def invert_linear(frame: np.ndarray) -> np.ndarray:
    """Flip every channel around the 8-bit midpoint, returning a new array.

    uint8 only: treating 16-bit data as 8-bit would be wildly wrong rather than
    obviously wrong.
    """
    if frame.dtype != np.uint8:
        raise TypeError(
            f"invert_linear expects a uint8 image, got {frame.dtype}."
        )
    return (_MAX_8BIT - frame.astype(np.uint8)).astype(np.uint8)
