"""Negative-to-positive inversion.

Currently only the v0.1 linear flip. It gives a usable positive preview for
black-and-white, but it is not a good film inversion:

- Colour negatives come out cyan, because the orange mask in the film base is
  inverted along with the image. Neutralising it means sampling the unexposed
  rebate and normalising against it -- a different pipeline, not a tweak.
- Positives look flat, because a negative never spans the full 0-255 range and
  flipping a compressed range gives a compressed result.

Both are v0.3. ``invert_linear`` stays as the shared primitive underneath.
"""

from __future__ import annotations

import numpy as np

_MAX_8BIT = 255


def invert_linear(frame: np.ndarray) -> np.ndarray:
    """Invert an image by flipping every channel around the 8-bit midpoint.

    Applies identically to all channels, so it makes no black-and-white
    assumption; it simply does not correct for anything colour negatives need.

    Args:
        frame: ``uint8`` array, usually ``(H, W, 3)`` BGR.

    Returns:
        A new inverted array. The input is not modified.

    Raises:
        TypeError: If the frame is not 8-bit. Higher bit depths need a different
            maximum, and treating 16-bit data as 8-bit would be wildly wrong
            rather than obviously wrong.
    """
    if frame.dtype != np.uint8:
        raise TypeError(
            f"invert_linear expects a uint8 image, got {frame.dtype}."
        )
    return (_MAX_8BIT - frame.astype(np.uint8)).astype(np.uint8)
