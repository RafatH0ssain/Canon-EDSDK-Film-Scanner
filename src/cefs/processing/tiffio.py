"""TIFF reading and writing, in the depths this project actually needs.

Pillow is not an option here: it has no 16-bit-per-channel colour mode and
raises on a ``(h, w, 3)`` uint16 array outright. A 16-bit positive is the whole
point of the develop path, so the writer is ``tifffile`` -- BSD-licensed, numpy
only, and no bundled codecs.

**Arrays are written exactly as given, channel order included.** OpenCV used to
hide this by converting BGR to RGB on the way out; nothing converts for you
here. Callers hand over RGB, or the red and blue in every saved file are
swapped -- which on a colour negative looks entirely plausible and is entirely
wrong.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile

#: Supported compressions, mapped to tifffile's names.
#:
#: LZW is deliberately absent. It needs the ``imagecodecs`` package, which
#: bundles a pile of codecs and reintroduces the dependency weight this module
#: exists to shed -- and it was never worth having: measured on real 32 MP
#: positives it managed 1.1-1.2x where deflate holds 2.4-2.6x.
COMPRESSIONS: dict[str, str | None] = {
    "none": None,
    "deflate": "zlib",
}


class TiffError(RuntimeError):
    """A TIFF could not be written or read."""


def write_tiff(path: Path | str, image: np.ndarray, *, compression: str = "deflate") -> Path:
    """Write ``image`` to ``path``. RGB in, RGB on disk.

    Raises:
        TiffError: If the compression is unknown, which is worth failing on
            before spending a minute encoding 32 megapixels.
    """
    if compression not in COMPRESSIONS:
        raise TiffError(
            f"Unknown TIFF compression {compression!r}. "
            f"Choose one of: {', '.join(COMPRESSIONS)}."
        )
    path = Path(path)
    try:
        tifffile.imwrite(path, image, compression=COMPRESSIONS[compression])
    except Exception as exc:  # tifffile raises several unrelated types
        raise TiffError(f"Could not write {path.name}: {exc}") from exc
    return path


def read_tiff(path: Path | str) -> np.ndarray:
    """Read a TIFF back, unchanged. RGB on disk, RGB out."""
    try:
        return np.asarray(tifffile.imread(str(path)))
    except Exception as exc:
        raise TiffError(f"Could not read {Path(path).name}: {exc}") from exc
