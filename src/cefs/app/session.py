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

from cefs.backend import CameraBackend, CameraError
from cefs.config import Config
from cefs.processing.codec import DecodeError, decode_jpeg, encode_jpeg
from cefs.processing.invert import invert_linear
from cefs.processing.loupe import crop_zoom

logger = logging.getLogger(__name__)


@dataclass
class ViewState:
    """What the browser is currently asking to see."""

    invert: bool = True
    loupe: bool = False
    zoom: float = 4.0
    center_x: float = 0.5
    center_y: float = 0.5


class Session:
    """The application's single live session with a camera."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._backend: CameraBackend | None = None
        self._lock = threading.Lock()
        self.view = ViewState(invert=True)
        self._captures: list[dict] = []
        self._last_error: str = ""

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
        capture = self._config.capture
        if backend is None:
            return {
                "connected": False,
                "backend": "mock" if self._config.camera.use_mock else "edsdk",
                "error": self._last_error,
                "view": asdict(self.view),
                "settle_delay_s": capture.settle_delay_s,
                "output_dir": str(capture.resolved_output_dir()),
                "captures": self._captures,
            }
        caps = backend.capabilities
        return {
            "connected": True,
            "backend": backend.info.backend,
            "model": backend.info.model,
            "lens": backend.info.lens,
            "error": "",
            "view": asdict(self.view),
            "settle_delay_s": capture.settle_delay_s,
            "output_dir": str(capture.resolved_output_dir()),
            "capabilities": {
                "focus_drive": caps.focus_drive,
                "liveview_zoom": caps.liveview_zoom,
                "electronic_shutter": caps.electronic_shutter,
                "notes": dict(caps.notes),
            },
            "captures": self._captures,
        }

    def update_view(self, **changes) -> dict:
        """Apply view changes from the UI, ignoring unknown keys."""
        for key, value in changes.items():
            if value is None or not hasattr(self.view, key):
                continue
            current = getattr(self.view, key)
            setattr(self.view, key, type(current)(value) if current is not None else value)
        self.view.zoom = max(1.0, min(float(self.view.zoom), 16.0))
        self.view.center_x = max(0.0, min(float(self.view.center_x), 1.0))
        self.view.center_y = max(0.0, min(float(self.view.center_y), 1.0))
        return asdict(self.view)

    # --- frames -------------------------------------------------------------

    def process(self, payload: bytes) -> bytes:
        """Apply the current view options to one JPEG frame.

        The loupe is applied before inversion so magnification never changes
        what inversion sees, and both preview and any future saved output run
        through this same function.
        """
        frame = decode_jpeg(payload)
        view = self.view
        if view.loupe and view.zoom > 1.0:
            frame = crop_zoom(frame, view.center_x, view.center_y, view.zoom)
        if view.invert:
            frame = invert_linear(frame)
        return encode_jpeg(frame)

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
        """Fire the shutter and record the downloaded file."""
        backend = self._backend
        if backend is None:
            raise CameraError("Not connected.")
        started = time.perf_counter()
        path = backend.capture(self._config.capture.resolved_output_dir())
        entry = {
            "name": Path(path).name,
            "path": str(path),
            "bytes": Path(path).stat().st_size if Path(path).exists() else 0,
            "seconds": round(time.perf_counter() - started, 1),
        }
        self._captures.insert(0, entry)
        del self._captures[24:]
        return entry
