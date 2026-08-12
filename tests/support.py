"""Test helpers for reading images the way OpenCV used to.

``cv2.imread`` returned **BGR**, and a lot of assertions in this suite index
channels directly. Pillow and tifffile return RGB, so swapping readers without
swapping order would flip the meaning of those assertions -- and a test that
checks "is the red channel higher" would start passing for the wrong reason.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from cefs.processing.tiffio import read_tiff


def read_image_bgr(path: Path | str, *, as_8bit: bool = False) -> np.ndarray:
    """Read an image and return it BGR, like ``cv2.imread`` did.

    Args:
        path: The file to read.
        as_8bit: Match ``cv2.IMREAD_COLOR``, which always gave 8-bit colour.
            Otherwise the file's own depth is kept, like ``IMREAD_UNCHANGED``.
    """
    path = Path(path)
    if path.suffix.lower() in (".tif", ".tiff"):
        rgb = read_tiff(path)
    else:
        with Image.open(path) as handle:
            rgb = np.asarray(handle.convert("RGB"))

    if rgb.ndim == 2:
        return rgb
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    if as_8bit and bgr.dtype == np.uint16:
        bgr = (bgr >> 8).astype(np.uint8)
    return bgr
