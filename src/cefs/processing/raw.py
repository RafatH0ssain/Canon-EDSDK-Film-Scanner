"""RAW decoding, via rawpy/LibRaw.

ROADMAP.md v0.3 hoped EDSDK could do this itself. It cannot -- measured, not
assumed: ``EdsGetImage`` returns ``NOT_SUPPORTED`` for every target type on a
CR3, while working fine on JPEG. See :mod:`cefs.edsdk.decode` for the full
matrix. rawpy decodes the same file to the sensor's full 6984x4660 at 16 bits.

The postprocessing options here matter more than they look. Film inversion needs
**linear** data: the pipeline in :mod:`cefs.processing.film` takes a reciprocal,
which is only meaningful before any tone curve has been applied. Every default
that would make a RAW look nice as a photograph -- auto brightness, sRGB gamma,
colour-space conversion -- destroys exactly the relationship inversion depends
on, so all of them are turned off.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

#: Extensions rawpy handles for Canon bodies. Checked before opening so an
#: unsupported file gives a clear message rather than a LibRaw error.
RAW_EXTENSIONS = {".cr2", ".cr3", ".crw"}


class RawUnavailable(RuntimeError):
    """rawpy is not installed, or cannot read this file."""


def is_raw(path: Path | str) -> bool:
    return Path(path).suffix.lower() in RAW_EXTENSIONS


def decode_raw(path: Path | str, half: bool = False) -> np.ndarray:
    """Decode a Canon RAW file to a 16-bit linear RGB array.

    Args:
        path: The RAW file.
        half: Decode at half resolution. Roughly 4x faster and plenty for
            choosing inversion parameters before committing to a full render.

    Returns:
        ``uint16`` array ``(H, W, 3)`` in RGB order, **linear** -- no gamma, no
        auto-brightness, camera white balance applied.

    Raises:
        RawUnavailable: If rawpy is missing or the file will not decode.
    """
    path = Path(path)
    try:
        import rawpy
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RawUnavailable(
            "rawpy is not installed, so RAW files cannot be inverted.\n"
            "  pip install rawpy\n"
            "  EDSDK cannot decode CR3 itself; see cefs/edsdk/decode.py."
        ) from exc

    if not path.is_file():
        raise FileNotFoundError(path)

    try:
        with rawpy.imread(str(path)) as raw:
            return raw.postprocess(
                output_bps=16,
                # The camera's own white balance. A film base is a strong
                # colour cast, and auto white balance would try to correct it
                # away -- destroying the very thing the mask division measures.
                use_camera_wb=True,
                # No auto-brightness: it rescales per image, so two frames of
                # one roll would invert to different densities.
                no_auto_bright=True,
                # Linear. The reciprocal in the inversion is only valid on
                # linear data.
                gamma=(1, 1),
                output_color=rawpy.ColorSpace.raw,
                half_size=half,
            )
    except Exception as exc:  # LibRaw raises several unrelated types
        raise RawUnavailable(f"Could not decode {path.name}: {exc}") from exc
