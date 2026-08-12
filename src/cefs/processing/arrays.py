"""Small array operations that used to come from OpenCV."""

from __future__ import annotations

import numpy as np


def add_saturating(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Add two 8-bit images, clipping at 255 rather than wrapping.

    numpy's ``+`` on uint8 wraps, so 200 + 100 becomes 44. On the mock's grain
    that turns the brightest specks black -- a plausible-looking texture that
    is nothing like film.
    """
    return np.clip(a.astype(np.int16) + b.astype(np.int16), 0, 255).astype(np.uint8)
