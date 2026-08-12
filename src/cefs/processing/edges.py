"""Scharr gradients, which the focus aids are built on.

The magnitude's *scale* is load-bearing, not just its shape: the sharpness
readout is a gated Tenengrad whose 11.1x separation on real frames, and whose
refusal threshold, both assume these numbers. So this reproduces OpenCV's
kernels and its default border handling rather than approximating them.
"""

from __future__ import annotations

import numpy as np

from cefs.processing.grey import to_grey


def to_grey_for_edges(frame: np.ndarray, *, order: str = "bgr") -> np.ndarray:
    """Luma for gradient work, as an array the kernels can run over."""
    return to_grey(frame, order=order)


def scharr(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Horizontal and vertical Scharr derivatives, as float32.

    Border handling is ``reflect`` without repeating the edge pixel, which is
    numpy's ``reflect`` and OpenCV's ``BORDER_REFLECT_101`` -- its default, and
    not the same as numpy's ``symmetric``.
    """
    padded = np.pad(gray.astype(np.float32), 1, mode="reflect")

    top_left, top_mid, top_right = padded[:-2, :-2], padded[:-2, 1:-1], padded[:-2, 2:]
    mid_left, mid_right = padded[1:-1, :-2], padded[1:-1, 2:]
    low_left, low_mid, low_right = padded[2:, :-2], padded[2:, 1:-1], padded[2:, 2:]

    # [[-3, 0, 3], [-10, 0, 10], [-3, 0, 3]]
    dx = (
        -3.0 * top_left + 3.0 * top_right
        - 10.0 * mid_left + 10.0 * mid_right
        - 3.0 * low_left + 3.0 * low_right
    )
    # [[-3, -10, -3], [0, 0, 0], [3, 10, 3]]
    dy = (
        -3.0 * top_left - 10.0 * top_mid - 3.0 * top_right
        + 3.0 * low_left + 10.0 * low_mid + 3.0 * low_right
    )
    return dx.astype(np.float32), dy.astype(np.float32)


def gradient_magnitude(gray: np.ndarray) -> np.ndarray:
    """Edge strength per pixel, as float32."""
    dx, dy = scharr(gray)
    return np.sqrt(dx * dx + dy * dy, dtype=np.float32)
