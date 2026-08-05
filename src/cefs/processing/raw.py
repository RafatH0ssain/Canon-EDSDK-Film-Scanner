"""RAW decoding via rawpy/LibRaw, because EDSDK cannot do it.

Measured, not assumed: ``EdsGetImage`` returns ``NOT_SUPPORTED`` for every
target on a CR3 while working on JPEG. See :mod:`cefs.edsdk.decode`.

The postprocessing options matter more than they look. Inversion takes a
reciprocal, which is only meaningful on linear data, so every default that
would make a RAW look nice -- auto brightness, gamma, colour conversion --
destroys the relationship it depends on and is turned off.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

#: Checked before opening, so an unsupported file gives a clear message
#: rather than a LibRaw error. CRAW lives inside .cr3 and needs nothing extra.
RAW_EXTENSIONS = {".cr2", ".cr3", ".crw"}


class RawUnavailable(RuntimeError):
    """rawpy is not installed, or cannot read this file."""


def is_raw(path: Path | str) -> bool:
    return Path(path).suffix.lower() in RAW_EXTENSIONS


def decode_raw(path: Path | str, half: bool = False) -> np.ndarray:
    """Decode a Canon RAW to a 16-bit **linear** RGB array.

    No gamma, no auto-brightness, camera white balance applied. ``half`` decodes
    at half resolution, roughly 4x faster.
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
                # Camera WB, not auto: auto would try to correct away the film
                # base, which is the very thing the mask division measures.
                use_camera_wb=True,
                # No auto-brightness: it rescales per image, so two frames of
                # one roll would invert to different densities.
                no_auto_bright=True,
                gamma=(1, 1),
                output_color=rawpy.ColorSpace.raw,
                half_size=half,
            )
    except Exception as exc:  # LibRaw raises several unrelated types
        raise RawUnavailable(f"Could not decode {path.name}: {exc}") from exc
