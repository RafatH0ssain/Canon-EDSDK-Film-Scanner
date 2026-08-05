"""Wires a camera backend to the processing pipeline for the web UI.

This is the layer that owns the transport. The backend hands it JPEG bytes; it
decodes, applies the view options, re-encodes, and yields frames for the MJPEG
stream. It holds the view state the UI manipulates.

The pipeline runs on whichever thread is serving the stream, never on the camera
thread -- the camera thread must stay free to pump messages, and blocking it on
image processing would stall event delivery.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

from cefs.backend import CameraBackend, CameraError
from cefs.config import Config
from cefs.processing.codec import DecodeError, decode_jpeg, encode_jpeg
from cefs.processing.develop import (
    POSITIVE_FORMATS,
    TIFF_COMPRESSION,
    DevelopError,
    OutputOptions,
    develop,
)
from cefs.processing.film import FilmParams, analyse, invert_preview, srgb_to_linear
from cefs.processing.invert import invert_linear
from cefs.processing.loupe import crop_zoom
from cefs.processing.peaking import DEFAULT_PEAK_COLOR, peaking_mask
from cefs.processing.sharpness import sharpness_of_region

logger = logging.getLogger(__name__)


@dataclass
class ViewState:
    """What the browser is currently asking to see."""

    invert: bool = True
    loupe: bool = False
    zoom: float = 4.0
    center_x: float = 0.5
    center_y: float = 0.5
    peaking: bool = False
    peaking_sensitivity: float = 0.5

    #: "film" is the real v0.3 pipeline; "linear" is the v0.1 flip, kept so the
    #: difference can be seen side by side; "off" shows the negative.
    inversion: str = "film"


#: Focus steps sent per keypress, as ``(SDK step size, how many)``.
#:
#: ``fine`` is one step of the SDK's smallest size -- the finest move EDSDK
#: offers, so nothing below it is reachable.
#:
#: A note on how these were arrived at, because the obvious method failed.
#: Larger steps were calibrated by measuring mean absolute frame difference per
#: press against the frame-to-frame noise floor, which correctly caught a first
#: attempt that moved the lens only 1.1x the floor -- invisible. But that metric
#: is blind at the fine end: on an EOS R7 every option from 1x1 to 2x8 measured
#: between 1.0x and 1.7x the floor, indistinguishable from each other and from
#: noise.
#:
#: The metric averages the whole frame; a person judging focus is looking
#: through an 8x loupe at a sharpness readout, and can resolve moves far smaller
#: than that average can. So the fine end is set by what is useful in the hand,
#: not by what the difference metric can see, and ``fine`` is simply the
#: smallest step the SDK has.
#:
#: Measured, for the sizes the metric *can* resolve (noise floor ~2.3-3.6):
#:
#: ========  =============  ========  =========  ========
#: name      size x steps   travel    vs floor   latency
#: ========  =============  ========  =========  ========
#: fine      1 x 1          --        below the metric   0.03 s
#: medium    2 x 6          ~6        ~2.5x      0.12 s
#: coarse    3 x 20         23.0      6.4x       0.49 s
#: ========  =============  ========  =========  ========
#:
#: Coarse travels 2.5x further than medium while measuring only 1.3x higher:
#: the difference metric saturates once the image is thoroughly defocused, just
#: as it goes blind at the other end.
FOCUS_STEPS = {
    "fine": (1, 1),
    "medium": (2, 6),
    "coarse": (3, 20),
}


class Session:
    """The application's single live session with a camera."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._backend: CameraBackend | None = None
        self._lock = threading.Lock()
        self.view = ViewState(invert=True)
        self._captures: list[dict] = []
        self._last_error: str = ""
        self._best_sharpness: float = 0.0
        self.film = FilmParams(mode=config.film.mode)
        # Measuring the film base costs ~30 ms and wanders slightly with grain,
        # so it is measured on demand rather than per frame. Re-measured when
        # the user asks, or when a parameter that changes its meaning changes.
        self._measured: dict | None = None
        # Where the user pointed at the rebate, in normalised coordinates. The
        # region is what travels to the developed file, not the value sampled
        # from it -- live view and a capture are on different linear scales.
        # See cefs.processing.develop._measure.
        self._base_region: tuple[float, float, float, float] | None = None

    # --- lifecycle ----------------------------------------------------------

    def connect(self) -> dict:
        """Create and start the configured backend."""
        with self._lock:
            if self._backend is not None:
                return self.status()
            backend = self._build_backend()
            try:
                backend.start()
            except Exception as exc:
                self._last_error = str(exc)
                logger.error("Could not connect: %s", exc)
                raise
            self._backend = backend
            self._last_error = ""
            return self.status()

    def disconnect(self) -> dict:
        with self._lock:
            if self._backend is not None:
                try:
                    self._backend.stop()
                finally:
                    self._backend = None
            return self.status()

    def _build_backend(self) -> CameraBackend:
        if self._config.camera.use_mock:
            from cefs.mock.camera import MockCamera

            return MockCamera(
                color=self._config.film.mode == "color",
                target_fps=self._config.liveview.target_fps,
                settle_delay_s=self._config.capture.settle_delay_s,
            )

        from cefs.edsdk.camera import EdsdkCamera

        if not self._config.edsdk.library_dir and not self._config.edsdk.library_path:
            raise CameraError(
                "No EDSDK location configured.\n"
                "  Copy config.example.yaml to config.yaml and set\n"
                "  edsdk.library_dir to the folder holding the 64-bit EDSDK library,\n"
                "  or set camera.use_mock to true to work without a camera."
            )
        return EdsdkCamera(
            library_dir=self._config.edsdk.resolved_library_dir() or "",
            library_path=self._config.edsdk.resolved_library_path(),
            target_fps=self._config.liveview.target_fps,
            settle_delay_s=self._config.capture.settle_delay_s,
            delete_after_download=self._config.capture.delete_from_camera_after_download,
        )

    @property
    def connected(self) -> bool:
        return self._backend is not None

    # --- state --------------------------------------------------------------

    def status(self) -> dict:
        backend = self._backend
        if backend is None:
            return {
                "connected": False,
                "backend": "mock" if self._config.camera.use_mock else "edsdk",
                "error": self._last_error,
                "view": asdict(self.view),
                "capture": self.capture_status(),
                "captures": self._captures,
                "film": self.film_status(),
            }
        caps = backend.capabilities
        return {
            "connected": True,
            "backend": backend.info.backend,
            "model": backend.info.model,
            "lens": backend.info.lens,
            "error": "",
            "view": asdict(self.view),
            "capture": self.capture_status(),
            "capabilities": {
                "focus_drive": caps.focus_drive,
                "liveview_zoom": caps.liveview_zoom,
                "electronic_shutter": caps.electronic_shutter,
                "notes": dict(caps.notes),
            },
            "captures": self._captures,
            "film": self.film_status(),
        }

    # --- capture settings ---------------------------------------------------

    def capture_status(self) -> dict:
        """The capture settings, and the choices the UI may offer for each."""
        capture = self._config.capture
        return {
            "settle_delay_s": capture.settle_delay_s,
            "output_dir": capture.output_dir,
            "resolved_output_dir": str(capture.resolved_output_dir()),
            "develop_positives": capture.develop_positives,
            "positive_format": capture.positive_format,
            "tiff_compression": capture.tiff_compression,
            "jpeg_quality": capture.jpeg_quality,
            "formats": list(POSITIVE_FORMATS),
            "compressions": list(TIFF_COMPRESSION),
        }

    def output_options(self) -> OutputOptions:
        capture = self._config.capture
        return OutputOptions(
            format=capture.positive_format,
            tiff_compression=capture.tiff_compression,
            jpeg_quality=capture.jpeg_quality,
        )

    def update_capture(self, **changes) -> dict:
        """Change capture settings from the UI.

        Every setting is validated here rather than at the shutter. A save
        location that cannot be created, or a compression name with a typo in
        it, should fail while you are still looking at the control -- not
        halfway through developing a 32 MP frame.

        These changes last for the session. ``config.yaml`` is not rewritten:
        the file holds the path to a licensed SDK, and a UI that edited it
        would be a UI that could corrupt it.
        """
        capture = self._config.capture
        changes = {k: v for k, v in changes.items() if v is not None}

        if "output_dir" in changes:
            directory = str(changes["output_dir"]).strip()
            if not directory:
                raise ValueError("Save location cannot be empty.")
            previous = capture.output_dir
            capture.output_dir = directory
            try:
                capture.resolved_output_dir().mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                capture.output_dir = previous
                raise ValueError(f"Cannot use that save location: {exc}") from exc

        if "settle_delay_s" in changes:
            # 60 s is already far longer than any copy stand needs; the cap is
            # only there so a stray keystroke cannot appear to hang the app.
            capture.settle_delay_s = max(0.0, min(float(changes["settle_delay_s"]), 60.0))
            backend = self._backend
            if backend is not None:
                backend.settle_delay_s = capture.settle_delay_s

        if "develop_positives" in changes:
            capture.develop_positives = bool(changes["develop_positives"])

        # The three output settings are validated together, by the same code
        # that will use them, and rolled back as a group if any is bad.
        output_keys = ("positive_format", "tiff_compression", "jpeg_quality")
        if any(key in changes for key in output_keys):
            snapshot = {key: getattr(capture, key) for key in output_keys}
            for key in output_keys:
                if key in changes:
                    setattr(capture, key, changes[key])
            try:
                capture.jpeg_quality = int(capture.jpeg_quality)
                self.output_options().validate()
            except (DevelopError, TypeError, ValueError) as exc:
                for key, value in snapshot.items():
                    setattr(capture, key, value)
                raise ValueError(str(exc)) from exc

        return self.capture_status()

    def update_view(self, **changes) -> dict:
        """Apply view changes from the UI, ignoring unknown keys."""
        previous_inversion = self.view.inversion
        for key, value in changes.items():
            if value is None or not hasattr(self.view, key):
                continue
            current = getattr(self.view, key)
            setattr(self.view, key, type(current)(value) if current is not None else value)
        self.view.zoom = max(1.0, min(float(self.view.zoom), 16.0))
        self.view.center_x = max(0.0, min(float(self.view.center_x), 1.0))
        self.view.center_y = max(0.0, min(float(self.view.center_y), 1.0))
        self.view.peaking_sensitivity = max(
            0.0, min(float(self.view.peaking_sensitivity), 1.0)
        )
        if self.view.inversion not in ("off", "linear", "film"):
            bad, self.view.inversion = self.view.inversion, previous_inversion
            raise ValueError(f"inversion must be 'off', 'linear' or 'film', got {bad!r}")
        return asdict(self.view)

    # --- frames -------------------------------------------------------------

    def process(self, payload: bytes) -> bytes:
        """Apply the current view options to one JPEG frame.

        Order matters, and not obviously:

        Peaking is *measured* on the full-resolution frame before the loupe
        crops it, because nearest-neighbour magnification leaves flat blocks
        whose only edges are block boundaries -- peaking computed after zoom
        gets sparser the further you zoom in, which is exactly backwards.

        It is *applied* after inversion, because the overlay is a fixed colour.
        Marking pixels red and then inverting would turn every one of them
        cyan. So the mask is carried through the same crop as the image and
        painted on at the end.
        """
        frame = decode_jpeg(payload)
        view = self.view

        mask = peaking_mask(frame, view.peaking_sensitivity) if view.peaking else None

        if view.loupe and view.zoom > 1.0:
            frame = crop_zoom(frame, view.center_x, view.center_y, view.zoom)
            if mask is not None:
                # Same crop, nearest-neighbour, so marks stay on their pixels.
                cropped = crop_zoom(
                    mask.astype(np.uint8) * 255, view.center_x, view.center_y, view.zoom
                )
                mask = cropped > 127

        frame = self._apply_inversion(frame)
        if mask is not None:
            frame[mask] = DEFAULT_PEAK_COLOR
        return encode_jpeg(frame)

    def _apply_inversion(self, frame):
        """Apply whichever inversion the view asks for.

        Both the preview and any saved output go through
        :func:`cefs.processing.film.invert_preview`, so they cannot drift apart.
        """
        view = self.view
        if not view.invert or view.inversion == "off":
            return frame
        if view.inversion == "linear":
            return invert_linear(frame)
        if self._measured is None:
            self._measured = analyse(srgb_to_linear(frame[:, :, ::-1]), self.film)
        return invert_preview(frame, self.film, self._measured)

    def remeasure_film_base(self, region=None) -> dict:
        """Re-measure the film base, optionally from a region of the frame.

        Pointing at the rebate is far more reliable than the automatic estimate,
        which assumes the densest part of the image approaches base density.
        """
        backend = self._backend
        if backend is None:
            raise CameraError("Not connected.")
        payload = backend.latest_frame()
        if payload is None:
            raise CameraError("No frame available yet.")
        linear = srgb_to_linear(decode_jpeg(payload)[:, :, ::-1])
        if region is not None:
            from cefs.processing.film import sample_base

            self._base_region = tuple(float(v) for v in region)
            self.film = self.film.replace(base=sample_base(linear, region=self._base_region))
        else:
            # A reset has to clear both, and `replace(base=None)` cannot: it
            # drops None, so it would keep the base it claims to be clearing.
            self._base_region = None
            self.film = self.film.without_base()
        self._measured = analyse(linear, self.film)
        return self.film_status()

    def update_film(self, **changes) -> dict:
        """Change inversion parameters. Re-analyses when it must."""
        changes = {k: v for k, v in changes.items() if v is not None}
        if "channel_gain" in changes:
            changes["channel_gain"] = tuple(float(v) for v in changes["channel_gain"])
        self.film = self.film.replace(**changes)
        # Mode and base change what the measurement means; the tonal controls
        # only change the curve built from it, so they need no re-analysis.
        if {"mode", "base", "auto_balance", "highlight_percentile"} & set(changes):
            self._measured = None
        return self.film_status()

    def film_status(self) -> dict:
        f = self.film
        return {
            "mode": f.mode,
            "exposure": round(f.exposure, 3),
            "contrast": round(f.contrast, 3),
            "black_point": round(f.black_point, 4),
            "white_point": round(f.white_point, 4),
            "auto_balance": f.auto_balance,
            "channel_gain": [round(v, 3) for v in f.channel_gain],
            "base": [round(v, 5) for v in f.base] if f.base else None,
            # What actually reaches a developed file. Reported so the UI can
            # say the saved positive uses the rebate you pointed at, and not
            # leave you guessing whether it survived the capture.
            "base_region": list(self._base_region) if self._base_region else None,
            "measured": {
                k: [round(x, 5) for x in v] for k, v in (self._measured or {}).items()
            }
            or None,
        }

    def measure_sharpness(self) -> dict:
        """Relative sharpness of whatever the user is currently looking at.

        Measures the loupe's region when the loupe is up, so the number matches
        the magnified view rather than the whole frame. Relative only: the
        absolute value depends on subject and exposure, so it is meaningful
        while turning focus back and forth and not between sessions.
        """
        backend = self._backend
        if backend is None:
            raise CameraError("Not connected.")
        payload = backend.latest_frame()
        if payload is None:
            raise CameraError("No frame available yet.")

        frame = decode_jpeg(payload)
        view = self.view
        if view.loupe and view.zoom > 1.0:
            value = sharpness_of_region(
                frame, view.center_x, view.center_y, size=1.0 / view.zoom
            )
        else:
            value = sharpness_of_region(frame, view.center_x, view.center_y, size=0.5)

        with self._lock:
            if value > self._best_sharpness:
                self._best_sharpness = value
            best = self._best_sharpness
        return {
            "sharpness": round(value, 3),
            "best": round(best, 3),
            # How close to the best seen, for a meter the eye can follow. The
            # raw number moves in single digits and is hard to read at a glance.
            "fraction_of_best": round(value / best, 3) if best > 0 else 0.0,
        }

    def reset_sharpness_best(self) -> dict:
        with self._lock:
            self._best_sharpness = 0.0
        return {"best": 0.0}

    def drive_focus(self, direction: str, coarseness: str = "medium") -> dict:
        """Step focus. Returns the step actually sent, for the UI to report."""
        backend = self._backend
        if backend is None:
            raise CameraError("Not connected.")
        if coarseness not in FOCUS_STEPS:
            raise ValueError(
                f"coarseness must be one of {', '.join(FOCUS_STEPS)}, got {coarseness!r}"
            )
        size, steps = FOCUS_STEPS[coarseness]
        backend.drive_focus(direction, steps=steps, size=size)
        return {"direction": direction, "coarseness": coarseness, "size": size, "steps": steps}

    def mjpeg(self, boundary: str = "frame") -> Iterator[bytes]:
        """Yield an endless multipart MJPEG stream for the browser."""
        interval = 1.0 / max(self._config.liveview.target_fps, 1)
        last: bytes | None = None
        while True:
            backend = self._backend
            if backend is None:
                return
            started = time.perf_counter()
            payload = backend.latest_frame()
            if payload is not None and payload is not last:
                last = payload
                try:
                    processed = self.process(payload)
                except DecodeError:
                    # Partial frames happen; skipping one beats tearing the
                    # stream down.
                    processed = None
                if processed is not None:
                    yield (
                        f"--{boundary}\r\nContent-Type: image/jpeg\r\n"
                        f"Content-Length: {len(processed)}\r\n\r\n".encode("ascii")
                        + processed
                        + b"\r\n"
                    )
            elapsed = time.perf_counter() - started
            time.sleep(max(0.0, interval - elapsed))

    # --- actions ------------------------------------------------------------

    def capture(self) -> dict:
        """Fire the shutter, download everything, and optionally develop it."""
        backend = self._backend
        if backend is None:
            raise CameraError("Not connected.")
        started = time.perf_counter()
        paths = [Path(p) for p in backend.capture(self._config.capture.resolved_output_dir())]
        output = self.output_options()

        files = []
        for path in paths:
            entry = {
                "name": path.name,
                "path": str(path),
                "bytes": path.stat().st_size if path.exists() else 0,
                "positive": None,
                "positive_bytes": 0,
                "error": "",
            }
            if self._config.capture.develop_positives:
                try:
                    positive = develop(
                        path, self.film, output=output, base_region=self._base_region
                    )
                    entry["positive"] = positive.name
                    entry["positive_bytes"] = positive.stat().st_size
                except DevelopError as exc:
                    # A failed development must not lose the capture itself --
                    # the original is the one thing that cannot be regenerated.
                    entry["error"] = str(exc)
                    logger.error("Could not develop %s: %s", path.name, exc)
            files.append(entry)

        record = {
            "name": files[0]["name"],
            "files": files,
            "count": len(files),
            "bytes": sum(f["bytes"] for f in files),
            "seconds": round(time.perf_counter() - started, 1),
        }
        self._captures.insert(0, record)
        del self._captures[24:]
        return record
