"""Turn a captured negative into a positive file.

The same inversion the live preview uses, applied to what the camera actually
wrote. Non-destructive: the original is never touched or overwritten, and the
positive is written alongside it with a ``-positive`` suffix.

Which decoder is used depends on the file, not on a setting:

- **RAW** (``.CR3``/``.CR2``, and so CRAW too -- Canon's compressed RAW is a
  variant *inside* the CR3 container, which LibRaw handles with no separate
  code here) goes through :mod:`cefs.processing.raw`, because EDSDK will not
  decode CR3 -- measured, see :mod:`cefs.edsdk.decode`. It arrives 16-bit and
  linear, which is what the inversion wants, and leaves as a 16-bit TIFF.
- **HEIF** (``.HIF``/``.heif``/``.heic``) goes through pillow-heif at its full
  10-bit depth. A Canon body writes HEIF only in HDR PQ mode, so the samples
  are PQ-encoded, not sRGB -- the file's own NCLX profile says which, and it is
  read rather than assumed. PQ files take the 16-bit path and leave as a 16-bit
  TIFF; anything else is treated as 8-bit sRGB.

  Do not expect a HEIF and a RAW of the same frame to develop identically. A
  HEIF is *rendered* in the camera -- picture style and all -- where a RAW is
  the sensor's own linear data. Measured on one frame shot both ways on an R7,
  the two positives agree closely on overall level (mean 0.455 against 0.445)
  and the HEIF comes out with about a quarter more tonal spread (std 0.292
  against 0.237), which is consistent with the camera's tone curve already
  being baked into it. RAW remains the better master; HEIF is a good deal more
  than a proof.
- **JPEG** is read with OpenCV. It is 8-bit and already tone-mapped, so the
  result cannot match a RAW development; it is written back as a high-quality
  JPEG and is best treated as a proof.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from cefs.processing.film import (
    FilmParams,
    analyse,
    invert_pq,
    invert_preview,
    invert_raw,
    linear16_to_linear,
    pq16_to_linear_lut,
    sample_base,
    srgb_to_linear,
)
from cefs.processing.raw import RawUnavailable, decode_raw, is_raw

logger = logging.getLogger(__name__)

#: Formats OpenCV reads directly, all of them 8-bit by the time we see them.
_DIRECT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

#: What a Canon body calls HEIF, plus the names everything else uses.
HEIF_EXTENSIONS = {".hif", ".heif", ".heic"}

#: libtiff compression codes, as ``cv2.IMWRITE_TIFF_COMPRESSION`` wants them.
#: All three are lossless -- verified by reading each back and comparing every
#: pixel, not assumed from the format. Measured writing a real 6960x4640 16-bit
#: positive: none 193.8 MB in 0.13 s, LZW 103.0 MB in 1.89 s, deflate 75.5 MB
#: in 4.86 s. LZW is the default: it halves the file for under two seconds,
#: where deflate's further 27% costs another three.
TIFF_COMPRESSION = {"none": 1, "lzw": 5, "deflate": 8}

#: How the positive is chosen when the format is left on "auto".
POSITIVE_FORMATS = ("auto", "tiff", "jpeg")


class DevelopError(RuntimeError):
    """The capture could not be developed into a positive."""


@dataclass(frozen=True)
class OutputOptions:
    """How the positive is written. Never affects the inversion itself."""

    #: ``"auto"`` keeps the depth the source had: TIFF for RAW and 10-bit HEIF,
    #: JPEG for the 8-bit formats. The other two force one container.
    format: str = "auto"

    #: See :data:`TIFF_COMPRESSION`. Lossless whichever is chosen.
    tiff_compression: str = "lzw"

    jpeg_quality: int = 95

    def validate(self) -> None:
        """Check the options before anything expensive is decoded."""
        if self.format not in POSITIVE_FORMATS:
            raise DevelopError(
                f"positive_format must be one of {', '.join(POSITIVE_FORMATS)}, "
                f"got {self.format!r}"
            )
        if self.tiff_compression not in TIFF_COMPRESSION:
            raise DevelopError(
                f"tiff_compression must be one of {', '.join(TIFF_COMPRESSION)}, "
                f"got {self.tiff_compression!r}"
            )
        if not 1 <= int(self.jpeg_quality) <= 100:
            raise DevelopError(f"jpeg_quality must be 1-100, got {self.jpeg_quality}")


def is_heif(path: Path | str) -> bool:
    return Path(path).suffix.lower() in HEIF_EXTENSIONS


def _keeps_16_bits(source: Path) -> bool:
    """Whether this source carries more than 8 bits worth preserving.

    Decided from the extension, because the destination has to be chosen before
    the file is decoded. A HEIF that turns out to be 8-bit sRGB therefore lands
    in an 8-bit TIFF -- larger than it needs to be, but never a lie about depth.
    """
    return is_raw(source) or is_heif(source)


def positive_path(
    source: Path,
    output_dir: Path | None = None,
    suffix: str = "-positive",
    output: OutputOptions | None = None,
) -> Path:
    """Where the positive for ``source`` should be written.

    16-bit TIFF for RAW and HEIF so the extra depth survives; JPEG for
    everything else, which was 8-bit to begin with.
    """
    output = output or OutputOptions()
    if output.format == "tiff":
        extension = ".tif"
    elif output.format == "jpeg":
        extension = ".jpg"
    else:
        extension = ".tif" if _keeps_16_bits(source) else ".jpg"
    directory = output_dir or source.parent
    candidate = directory / f"{source.stem}{suffix}{extension}"
    if not candidate.exists():
        return candidate
    for n in range(1, 10000):
        alternative = directory / f"{source.stem}{suffix}-{n}{extension}"
        if not alternative.exists():
            return alternative
    raise DevelopError(f"Could not find a free filename beside {candidate}")


def _read_heif(source: Path) -> tuple[np.ndarray, bool]:
    """Decode a HEIF file at its native depth.

    Returns:
        ``(rgb, is_pq)``. ``rgb`` is uint16 with 10-bit codes left-shifted into
        16 bits when the file is PQ, and uint8 sRGB otherwise.

    Raises:
        DevelopError: If pillow-heif is missing or the file will not decode.
    """
    try:
        import pillow_heif
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise DevelopError(
            f"Cannot read {source.name}: pillow-heif is not installed.\n"
            f"  pip install pillow-heif\n"
            f"  OpenCV alone cannot read HEIF in this build."
        ) from exc

    try:
        # HDR is kept: converting to 8 bits here would throw away two of the
        # camera's ten bits before the inversion, which is the one step that
        # stretches the range hard enough to need them.
        heif = pillow_heif.open_heif(str(source), convert_hdr_to_8bit=False)
        rgb = np.asarray(heif)
    except Exception as exc:  # pillow-heif raises several unrelated types
        raise DevelopError(f"Could not decode {source.name}: {exc}") from exc

    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise DevelopError(f"{source.name} is not a 3-channel image ({rgb.shape}).")
    rgb = rgb[:, :, :3]  # drop alpha if the file has one

    # Transfer characteristic 16 is SMPTE ST 2084 (PQ). Read from the file
    # rather than inferred from the depth: an 8-bit sRGB HEIF exists and must
    # not be run through the PQ curve, which would wreck it.
    nclx = heif.info.get("nclx_profile") or {}
    is_pq = nclx.get("transfer_characteristics") == 16

    if is_pq and rgb.dtype != np.uint16:
        raise DevelopError(
            f"{source.name} declares PQ but decoded as {rgb.dtype}, not 16-bit."
        )
    if not is_pq and rgb.dtype == np.uint16:
        # Non-PQ and deep: no transfer function we can name, so drop to 8-bit
        # sRGB rather than guess one.
        logger.warning(
            "%s is %s-bit but not PQ (transfer=%s); treating it as 8-bit sRGB.",
            source.name,
            heif.info.get("bit_depth"),
            nclx.get("transfer_characteristics"),
        )
        rgb = (rgb >> 8).astype(np.uint8)

    # Copy, deliberately. ``np.asarray`` on a HeifFile is a *view* into
    # libheif's own buffer, which is freed with the HeifFile at the end of this
    # function -- and reading it afterwards is not an exception but an access
    # violation that takes the whole process down with no traceback.
    return np.array(rgb, copy=True, order="C"), is_pq


def _measure(linear: np.ndarray, params: FilmParams, base_region) -> dict:
    """Measure the film base from the captured file, never from the preview.

    The base is a linear value, and "linear" is not one scale. A live-view
    frame is sRGB-linear where 1.0 is display white; a CR3 is sensor-linear
    where 1.0 is saturation; a PQ HEIF is absolute, where 1.0 is 10000 cd/m^2
    and a negative on a light table sits near 0.02. Carrying a number sampled
    in one of those into another is comparing a rebate against nothing in
    particular. Measured on a real .HIF, a preview-sampled base collapsed the
    positive's tonal spread from 0.300 to 0.111 standard deviations and left
    nothing outside 0.32-0.74 -- flat, and plausible enough to keep.

    So what travels from the preview is the *region* the user pointed at, in
    normalised coordinates. The framing is the same, so the same rebate is
    re-measured here in the file's own scale, and at full resolution.
    """
    if base_region is not None:
        params = params.replace(base=sample_base(linear, region=tuple(base_region)))
    else:
        params = params.without_base()
    return analyse(linear, params)


def _invert_to_rgb(source: Path, params: FilmParams, base_region) -> tuple[np.ndarray, str]:
    """Decode and invert ``source``. Returns ``(rgb, how)`` for the log line."""
    if is_raw(source):
        try:
            linear16 = decode_raw(source)
        except RawUnavailable as exc:
            raise DevelopError(str(exc)) from exc
        measured = _measure(linear16_to_linear(linear16), params, base_region)
        return invert_raw(linear16, params, measured), "RAW, 16-bit linear"

    if is_heif(source):
        rgb, is_pq = _read_heif(source)
        if is_pq:
            measured = _measure(pq16_to_linear_lut()[rgb], params, base_region)
            return invert_pq(rgb, params, measured), "HEIF, 10-bit PQ"
        measured = _measure(srgb_to_linear(rgb), params, base_region)
        return (
            invert_preview(rgb[:, :, ::-1], params, measured)[:, :, ::-1],
            "HEIF, 8-bit sRGB",
        )

    if source.suffix.lower() not in _DIRECT_EXTENSIONS:
        raise DevelopError(
            f"Do not know how to read {source.suffix or 'this file'}.\n"
            f"  RAW goes through LibRaw, HEIF through pillow-heif, and\n"
            f"  {', '.join(sorted(_DIRECT_EXTENSIONS))} through OpenCV."
        )

    bgr = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if bgr is None:
        raise DevelopError(f"Could not read {source.name}. OpenCV would not decode it.")
    measured = _measure(srgb_to_linear(bgr[:, :, ::-1]), params, base_region)
    return invert_preview(bgr, params, measured)[:, :, ::-1], "8-bit sRGB"


def _write(destination: Path, rgb: np.ndarray, output: OutputOptions) -> None:
    """Write the finished positive. cv2 wants BGR."""
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    if destination.suffix.lower() in (".tif", ".tiff"):
        params = [int(cv2.IMWRITE_TIFF_COMPRESSION), TIFF_COMPRESSION[output.tiff_compression]]
    else:
        if bgr.dtype == np.uint16:
            # JPEG is 8-bit only. Asked for explicitly, so honour it rather
            # than silently writing a TIFF the user did not want.
            bgr = (bgr >> 8).astype(np.uint8)
        params = [int(cv2.IMWRITE_JPEG_QUALITY), int(output.jpeg_quality)]
    if not cv2.imwrite(str(destination), bgr, params):
        raise DevelopError(f"Could not write {destination}")


def develop(
    source: Path | str,
    params: FilmParams,
    output_dir: Path | str | None = None,
    output: OutputOptions | None = None,
    base_region: tuple[float, float, float, float] | None = None,
) -> Path:
    """Invert a captured negative and write the positive beside it.

    Args:
        source: The captured file, exactly as the camera wrote it.
        params: The same :class:`~cefs.processing.film.FilmParams` the preview
            uses, so what you judged on screen is what you get. Its ``base`` is
            deliberately *not* used -- see :func:`_measure`.
        output_dir: Where to write. Defaults to the source's own directory.
        output: Container, compression and quality. See :class:`OutputOptions`.
        base_region: Where the user pointed at the rebate, ``(x, y, w, h)`` in
            normalised coordinates. Re-measured from this file rather than
            carried over as a value. ``None`` estimates the base automatically.

    Returns:
        Path to the positive that was written.

    Raises:
        DevelopError: If the file cannot be read or written.
    """
    source = Path(source)
    output = output or OutputOptions()
    # Before decoding 30 MB of RAW, not after.
    output.validate()
    if not source.is_file():
        raise DevelopError(f"No such capture: {source}")

    destination = positive_path(source, Path(output_dir) if output_dir else None, output=output)
    destination.parent.mkdir(parents=True, exist_ok=True)

    rgb, how = _invert_to_rgb(source, params, base_region)
    _write(destination, rgb, output)
    logger.info("Developed %s -> %s (%s)", source.name, destination.name, how)
    return destination


def developable(source: Path | str) -> bool:
    """Whether :func:`develop` has any chance with this file."""
    source = Path(source)
    return is_raw(source) or is_heif(source) or source.suffix.lower() in _DIRECT_EXTENSIONS
