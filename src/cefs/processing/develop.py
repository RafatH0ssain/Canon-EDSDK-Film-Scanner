"""Turn a captured negative into a positive file.

The preview's inversion, applied to what the camera wrote. Non-destructive: the
original is never touched, and the positive lands beside it with a
``-positive`` suffix. The decoder is chosen by the file, not by a setting:

- **RAW** via LibRaw, because EDSDK cannot decode CR3 (measured -- see
  :mod:`cefs.edsdk.decode`). CRAW needs nothing extra: it is a compression mode
  inside the same container. 16-bit linear in, 16-bit TIFF out.
- **HEIF** via pillow-heif at its full 10 bits. Canon writes HEIF only in HDR
  PQ mode, so the samples are PQ, not sRGB -- read from the file's NCLX profile
  rather than assumed. Out as a 16-bit TIFF.
- **JPEG** via OpenCV; 8-bit and already tone-mapped, so it is a proof.

A HEIF and a RAW of the same frame do not develop identically: a HEIF is
rendered in-camera where a RAW is sensor-linear. Measured on one frame shot both
ways, the positives agree on level (mean 0.455 vs 0.445) but the HEIF carries
~25% more tonal spread (std 0.292 vs 0.237). RAW is still the better master.
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

#: libtiff codes for ``cv2.IMWRITE_TIFF_COMPRESSION``. All lossless -- verified
#: by reading back and comparing every pixel. Measured on two real 32 MP
#: positives: none 194 MB / 0.15 s, lzw 162-178 MB (1.1-1.2x) / 2.3 s, deflate
#: 74-81 MB (2.4-2.6x) / 5 s. Deflate is the default because LZW barely touches
#: 16-bit continuous tone -- and is what OpenCV writes anyway when told nothing.
TIFF_COMPRESSION = {"none": 1, "lzw": 5, "deflate": 8}

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
    tiff_compression: str = "deflate"

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
    """From the extension, since the destination is chosen before decoding. An
    8-bit HEIF therefore lands in an 8-bit TIFF -- wasteful, never a lie."""
    return is_raw(source) or is_heif(source)


def positive_path(
    source: Path,
    output_dir: Path | None = None,
    suffix: str = "-positive",
    output: OutputOptions | None = None,
) -> Path:
    """Where the positive goes: TIFF for RAW and HEIF, JPEG for the rest."""
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
    """Decode a HEIF at native depth. Returns ``(rgb, is_pq)`` -- uint16 PQ
    codes, or uint8 sRGB."""
    try:
        import pillow_heif
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise DevelopError(
            f"Cannot read {source.name}: pillow-heif is not installed.\n"
            f"  pip install pillow-heif\n"
            f"  OpenCV alone cannot read HEIF in this build."
        ) from exc

    try:
        # Keep HDR: dropping to 8 bits here would discard two of the camera's
        # ten before the one step that stretches the range hard enough to need them.
        heif = pillow_heif.open_heif(str(source), convert_hdr_to_8bit=False)
        rgb = np.asarray(heif)
    except Exception as exc:  # pillow-heif raises several unrelated types
        raise DevelopError(f"Could not decode {source.name}: {exc}") from exc

    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise DevelopError(f"{source.name} is not a 3-channel image ({rgb.shape}).")
    rgb = rgb[:, :, :3]  # drop alpha if the file has one

    # 16 is SMPTE ST 2084 (PQ). Read, not inferred from depth: an 8-bit sRGB
    # HEIF exists and running it through the PQ curve would wreck it.
    nclx = heif.info.get("nclx_profile") or {}
    is_pq = nclx.get("transfer_characteristics") == 16

    if is_pq and rgb.dtype != np.uint16:
        raise DevelopError(
            f"{source.name} declares PQ but decoded as {rgb.dtype}, not 16-bit."
        )
    if not is_pq and rgb.dtype == np.uint16:
        # Deep but not PQ: no transfer we can name, so drop to 8 bits rather
        # than guess one.
        logger.warning(
            "%s is %s-bit but not PQ (transfer=%s); treating it as 8-bit sRGB.",
            source.name,
            heif.info.get("bit_depth"),
            nclx.get("transfer_characteristics"),
        )
        rgb = (rgb >> 8).astype(np.uint8)

    # Copy, deliberately: np.asarray on a HeifFile is a view into libheif's
    # buffer, freed with it, and touching it later is an access violation that
    # kills the process with no traceback.
    return np.array(rgb, copy=True, order="C"), is_pq


def _measure(linear: np.ndarray, params: FilmParams, base_region) -> dict:
    """Measure the base from the file, never from the preview.

    "Linear" is not one scale: live view is sRGB-linear (1.0 = display white),
    a CR3 sensor-linear (1.0 = saturation), a PQ HEIF absolute (a negative sits
    near 0.02). Carrying a value across collapsed a real .HIF's positive from
    std 0.300 to 0.111. So the *region* travels instead, and the same rebate is
    re-measured here in the file's own scale.
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

    ``params`` is what the preview used, so the file matches the screen -- but
    its ``base`` is ignored in favour of ``base_region``, the rebate the user
    pointed at, re-measured here in this file's own scale. See :func:`_measure`.
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
