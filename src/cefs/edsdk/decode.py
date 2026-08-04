"""Decode camera files to RGB using EDSDK itself.

ROADMAP.md v0.3 asked whether EDSDK can decode RAW to RGB, because if it could
it would remove a separate RAW dependency and directly solve inverting CRAW.

**Measured answer: no, not for CR3.** On EDSDK 13.20.21 with real R7 files:

==========  ===================  =========================================
file        ``EdsGetImageInfo``  ``EdsGetImage`` (RGB / RGB16 / Jpeg / DIB)
==========  ===================  =========================================
JPEG        OK, 8-bit            **all OK** -- RGB16 returns w*h*3*2 bytes
CR3         OK, "1620x1080/16"   **NOT_SUPPORTED for every target**
==========  ===================  =========================================

So ``EdsGetImage`` works; it simply refuses CR3. Worse, ``EdsGetImageInfo``
answers happily for a CR3 and reports 1620x1080 at 16-bit -- metadata for an
embedded preview, not a decodable image, and nowhere near the sensor's 32.5 MP.
Trusting that call alone would have produced a confident wrong answer.

RAW therefore goes through :mod:`cefs.processing.raw` (rawpy/LibRaw), which
decodes the same file to the full 6984x4660 at 16 bits in about 3 seconds.

This module remains useful for the formats EDSDK does handle, and as the
executable record of the finding above. It needs ``EdsInitializeSDK`` but no
camera session, so files decode with nothing plugged in.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import byref, c_uint64, c_void_p
from pathlib import Path
from typing import Any

import numpy as np

from cefs.edsdk import bindings as b
from cefs.edsdk.errors import EDS_ERR_OK, EdsError, check, error_name

logger = logging.getLogger(__name__)

COINIT_APARTMENTTHREADED = 0x2


class DecodeUnavailable(RuntimeError):
    """EDSDK could not decode this file.

    Raised rather than silently falling back, so an unsupported format is
    diagnosable instead of quietly producing something wrong.
    """


class EdsdkDecoder:
    """Decodes camera files to numpy arrays via EDSDK.

    Use as a context manager, or call :meth:`close` when done. Initialising the
    SDK twice in one process is not safe, so a single instance should be shared.
    """

    def __init__(self, library_dir: str | Path, library_path: str | Path | None = None) -> None:
        self._dll: Any = None
        self._lock = threading.Lock()
        self._com_initialised = False
        self._library_dir = library_dir
        self._library_path = library_path

    def __enter__(self) -> EdsdkDecoder:
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def open(self) -> None:
        if self._dll is not None:
            return
        if sys.platform == "win32":
            hr = ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            self._com_initialised = hr >= 0
        self._dll = b.load_edsdk(self._library_dir, self._library_path)
        check("EdsInitializeSDK", self._dll.EdsInitializeSDK())

    def close(self) -> None:
        if self._dll is None:
            return
        try:
            self._dll.EdsTerminateSDK()
        finally:
            self._dll = None
            if self._com_initialised and sys.platform == "win32":
                ctypes.windll.ole32.CoUninitialize()
                self._com_initialised = False

    # --- decoding -----------------------------------------------------------

    def info(self, path: Path | str) -> dict:
        """Dimensions and depth of a file, without decoding the pixels."""
        with self._lock, self._image_ref(path) as (image, source):
            info = b.EdsImageInfo()
            check("EdsGetImageInfo", self._dll.EdsGetImageInfo(image, source, byref(info)))
            rect = info.effectiveRect
            return {
                "width": info.width,
                "height": info.height,
                "components": info.numOfComponents,
                "depth": info.componentDepth,
                "effective": (
                    rect.point.x,
                    rect.point.y,
                    rect.size.width,
                    rect.size.height,
                ),
                "source": "RAWFullView" if source == b.kEdsImageSrc_RAWFullView else "FullView",
            }

    def decode(self, path: Path | str, half: bool = False) -> np.ndarray:
        """Decode a camera file to a 16-bit RGB array.

        Args:
            path: The file to decode.
            half: Render at half dimensions. Much faster, and plenty for
                sampling the film base or previewing inversion parameters.

        Returns:
            ``uint16`` array of shape ``(H, W, 3)`` in **RGB** order. Note that
            is RGB, not OpenCV's BGR -- this is what EDSDK produces, and it is
            converted at the boundary rather than here.

        Raises:
            DecodeUnavailable: If EDSDK will not decode this file.
        """
        with self._lock, self._image_ref(path) as (image, source):
            info = b.EdsImageInfo()
            check("EdsGetImageInfo", self._dll.EdsGetImageInfo(image, source, byref(info)))

            rect = info.effectiveRect
            # Render the effective rectangle, not the full readout: the extra
            # margin is sensor overscan, not picture.
            src = b.EdsRect(
                b.EdsPoint(rect.point.x, rect.point.y),
                b.EdsSize(rect.size.width, rect.size.height),
            )
            out_w = rect.size.width // 2 if half else rect.size.width
            out_h = rect.size.height // 2 if half else rect.size.height
            dst = b.EdsSize(out_w, out_h)

            stream = c_void_p()
            check("EdsCreateMemoryStream", self._dll.EdsCreateMemoryStream(0, byref(stream)))
            try:
                code = self._dll.EdsGetImage(
                    image, source, b.kEdsTargetImageType_RGB16, src, dst, stream
                )
                if code != EDS_ERR_OK:
                    raise DecodeUnavailable(
                        f"EDSDK could not render {Path(path).name} to 16-bit RGB "
                        f"({error_name(code)})."
                    )
                length = c_uint64()
                pointer = c_void_p()
                check("EdsGetLength", self._dll.EdsGetLength(stream, byref(length)))
                check("EdsGetPointer", self._dll.EdsGetPointer(stream, byref(pointer)))
                if not pointer.value or not length.value:
                    raise DecodeUnavailable(f"EDSDK returned no pixels for {Path(path).name}.")

                expected = out_w * out_h * 3 * 2  # 3 channels, 2 bytes each
                if length.value < expected:
                    raise DecodeUnavailable(
                        f"Short buffer from EdsGetImage: got {length.value} bytes, "
                        f"expected {expected} for {out_w}x{out_h} RGB16."
                    )
                raw = ctypes.string_at(pointer, expected)
            finally:
                self._dll.EdsRelease(stream)

        return np.frombuffer(raw, dtype=np.uint16).reshape(out_h, out_w, 3).copy()

    # --- internals ----------------------------------------------------------

    class _ImageRef:
        """Opens a file as an EDSDK image, releasing both refs on exit."""

        def __init__(self, decoder: EdsdkDecoder, path: Path) -> None:
            self._decoder = decoder
            self._path = path
            self._stream: c_void_p | None = None
            self._image: c_void_p | None = None

        def __enter__(self):
            dll = self._decoder._dll
            if dll is None:
                raise RuntimeError("Decoder is not open.")
            if not self._path.is_file():
                raise FileNotFoundError(self._path)

            stream = c_void_p()
            if sys.platform == "win32":
                check(
                    "EdsCreateFileStreamEx",
                    dll.EdsCreateFileStreamEx(
                        str(self._path),
                        b.kEdsFileCreateDisposition_OpenExisting,
                        b.kEdsAccess_Read,
                        byref(stream),
                    ),
                )
            else:
                check(
                    "EdsCreateFileStream",
                    dll.EdsCreateFileStream(
                        str(self._path).encode("utf-8"),
                        b.kEdsFileCreateDisposition_OpenExisting,
                        b.kEdsAccess_Read,
                        byref(stream),
                    ),
                )
            self._stream = stream

            image = c_void_p()
            code = dll.EdsCreateImageRef(stream, byref(image))
            if code != EDS_ERR_OK:
                raise DecodeUnavailable(
                    f"EDSDK will not open {self._path.name} as an image "
                    f"({error_name(code)}). Canon's decoder handles its own "
                    f"formats; anything else needs a different reader."
                )
            self._image = image

            # RAW files expose their full image under RAWFullView; rendered
            # formats use FullView. Ask for the RAW view and fall back, rather
            # than deciding from the file extension.
            info = b.EdsImageInfo()
            source = b.kEdsImageSrc_RAWFullView
            if dll.EdsGetImageInfo(image, source, byref(info)) != EDS_ERR_OK:
                source = b.kEdsImageSrc_FullView
                check("EdsGetImageInfo", dll.EdsGetImageInfo(image, source, byref(info)))
            return image, source

        def __exit__(self, *exc_info):
            dll = self._decoder._dll
            if dll is None:
                return
            if self._image is not None:
                dll.EdsRelease(self._image)
            if self._stream is not None:
                dll.EdsRelease(self._stream)

    def _image_ref(self, path: Path | str):
        return EdsdkDecoder._ImageRef(self, Path(path))


def to_bgr8(rgb16: np.ndarray) -> np.ndarray:
    """Convert a 16-bit RGB array to 8-bit BGR for display.

    Only for preview and JPEG output. Keep the 16-bit array for any actual
    processing: this throws away exactly the headroom inversion needs.
    """
    return (rgb16 >> 8).astype(np.uint8)[:, :, ::-1]
