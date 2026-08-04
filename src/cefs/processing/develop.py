"""Turn a captured negative into a positive file.

The same inversion the live preview uses, applied to what the camera actually
wrote. Non-destructive: the original is never touched or overwritten, and the
positive is written alongside it with a ``-positive`` suffix.

Which decoder is used depends on the file, not on a setting:

- **RAW** goes through :mod:`cefs.processing.raw` (LibRaw), because EDSDK will
  not decode CR3 -- measured, see :mod:`cefs.edsdk.decode`. It arrives 16-bit
  and linear, which is what the inversion wants, and leaves as a 16-bit TIFF.
- **JPEG/HEIF** is read with OpenCV. It is 8-bit and already tone-mapped, so the
  result cannot match a RAW development; it is written back as a high-quality
  JPEG and is best treated as a proof.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from cefs.processing.film import FilmParams, invert_preview, invert_raw
from cefs.processing.raw import RawUnavailable, decode_raw, is_raw

logger = logging.getLogger(__name__)

#: Formats OpenCV can read directly. HEIF depends on the OpenCV build, so it is
#: attempted and reported honestly rather than promised.
_DIRECT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".hif", ".heif", ".heic"}


class DevelopError(RuntimeError):
    """The capture could not be developed into a positive."""


def positive_path(source: Path, output_dir: Path | None = None, suffix: str = "-positive") -> Path:
    """Where the positive for ``source`` should be written.

    16-bit TIFF for RAW so the extra depth survives; JPEG for everything else,
    which was 8-bit to begin with.
    """
    extension = ".tif" if is_raw(source) else ".jpg"
    directory = output_dir or source.parent
    candidate = directory / f"{source.stem}{suffix}{extension}"
    if not candidate.exists():
        return candidate
    for n in range(1, 10000):
        alternative = directory / f"{source.stem}{suffix}-{n}{extension}"
        if not alternative.exists():
            return alternative
    raise DevelopError(f"Could not find a free filename beside {candidate}")


def develop(
    source: Path | str,
    params: FilmParams,
    output_dir: Path | str | None = None,
) -> Path:
    """Invert a captured negative and write the positive beside it.

    Args:
        source: The captured file, exactly as the camera wrote it.
        params: The same :class:`~cefs.processing.film.FilmParams` the preview
            uses, so what you judged on screen is what you get.
        output_dir: Where to write. Defaults to the source's own directory.

    Returns:
        Path to the positive that was written.

    Raises:
        DevelopError: If the file cannot be read or written.
    """
    source = Path(source)
    if not source.is_file():
        raise DevelopError(f"No such capture: {source}")
    destination = positive_path(source, Path(output_dir) if output_dir else None)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if is_raw(source):
        try:
            linear16 = decode_raw(source)
        except RawUnavailable as exc:
            raise DevelopError(str(exc)) from exc
        positive = invert_raw(linear16, params)
        # cv2 writes BGR; the pipeline works in RGB.
        if not cv2.imwrite(str(destination), positive[:, :, ::-1]):
            raise DevelopError(f"Could not write {destination}")
        logger.info("Developed %s -> %s (16-bit)", source.name, destination.name)
        return destination

    if source.suffix.lower() not in _DIRECT_EXTENSIONS:
        raise DevelopError(
            f"Do not know how to read {source.suffix or 'this file'}.\n"
            f"  RAW goes through LibRaw; everything else through OpenCV."
        )

    bgr = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if bgr is None:
        raise DevelopError(
            f"Could not read {source.name}.\n"
            f"  If this is HEIF, this OpenCV build may not support it -- shoot RAW\n"
            f"  or JPEG instead."
        )
    positive = invert_preview(bgr, params)
    if not cv2.imwrite(str(destination), positive, [int(cv2.IMWRITE_JPEG_QUALITY), 95]):
        raise DevelopError(f"Could not write {destination}")
    logger.info("Developed %s -> %s (8-bit)", source.name, destination.name)
    return destination


def developable(source: Path | str) -> bool:
    """Whether :func:`develop` has any chance with this file."""
    source = Path(source)
    return is_raw(source) or source.suffix.lower() in _DIRECT_EXTENSIONS
