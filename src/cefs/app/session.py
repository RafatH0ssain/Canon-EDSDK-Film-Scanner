"""Wires a camera backend to the processing pipeline for the web UI.

Owns the transport and the view state: the backend hands over JPEG bytes, this
decodes, applies the view options, re-encodes and yields the MJPEG stream.

The pipeline runs on whichever thread serves the stream, never the camera
thread, which must stay free to pump messages or events stop arriving.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import numpy as np

from cefs import naming, sidecar
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

    #: "film" is the real pipeline, "linear" the plain flip kept for comparison,
    #: "off" shows the negative.
    inversion: str = "film"


#: Focus per keypress, as ``(SDK step size, how many)``. Latency 0.03 / 0.12 /
#: 0.49 s. Confirmed on hardware; the user calls the fine step "phenomenal".
#:
#: Do not re-tune these against a whole-frame difference metric. That is how
#: they were first calibrated, and it went blind at the fine end -- every option
#: from 1x1 to 2x8 measured within noise of the others. ``fine`` is the SDK
#: minimum because that is what is useful under an 8x loupe, not because a
#: metric said so.
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
        # ~30 ms, and it wanders with grain, so it is measured on demand
        # rather than per frame.
        self._measured: dict | None = None
        # The region, not the value sampled from it: live view and a capture
        # are on different linear scales. See develop._measure.
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
                "roll": self.roll_status(),
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
            "roll": self.roll_status(),
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
        """Change capture settings from the UI, for this session only.

        Validated here, not at the shutter: an uncreatable save location or a
        typo in a compression name should fail while you are looking at the
        control, not halfway through developing a 32 MP frame.

        ``config.yaml`` is never rewritten -- it holds the path to a licensed
        SDK, and a UI that edited it could corrupt it.
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
            # Capped only so a stray keystroke cannot appear to hang the app.
            capture.settle_delay_s = max(0.0, min(float(changes["settle_delay_s"]), 60.0))
            backend = self._backend
            if backend is not None:
                backend.settle_delay_s = capture.settle_delay_s

        if "develop_positives" in changes:
            capture.develop_positives = bool(changes["develop_positives"])

        # Validated together by the code that will use them, rolled back as a
        # group if any is bad.
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

    # --- the roll -----------------------------------------------------------

    def roll_status(self) -> dict:
        """Roll label, frame number, metadata, and what the template will do."""
        roll = self._config.roll
        status = {
            "roll": roll.roll,
            "frame": roll.frame,
            "template": roll.template,
            "stock": roll.stock,
            "developer": roll.developer,
            "notes": roll.notes,
            "date": roll.date,
            "sidecar": roll.sidecar,
            "fields": list(naming.FIELDS),
        }
        # Show the answer, not the rule: a typo should be visible before the roll.
        try:
            status["example"] = (
                naming.example(roll.template, roll=roll.roll, frame=roll.frame,
                               stock=roll.stock)
                if roll.template.strip()
                else "IMG_0001.CR3 (the camera's own name)"
            )
            status["template_error"] = ""
        except naming.NamingError as exc:
            status["example"] = ""
            status["template_error"] = str(exc)
        return status

    def update_roll(self, **changes) -> dict:
        """Change the roll's label, frame number or metadata.

        Validated here, so a bad template is refused while you are looking at
        the field rather than at the shutter with a frame already exposed.
        """
        roll = self._config.roll
        changes = {k: v for k, v in changes.items() if v is not None}

        if "template" in changes:
            template = str(changes["template"])
            if template.strip():
                naming.validate_template(template)  # NamingError is a ValueError
            roll.template = template

        if "roll" in changes:
            label = naming.sanitise(changes["roll"])
            if not label:
                raise ValueError(
                    "That roll name has no usable characters left once path "
                    "separators and reserved characters are removed."
                )
            roll.roll = label

        if "frame" in changes:
            frame = int(changes["frame"])
            if not 0 <= frame <= 99999:
                raise ValueError(f"Frame number must be 0-99999, got {frame}.")
            roll.frame = frame

        for key in ("stock", "developer", "notes", "date"):
            if key in changes:
                setattr(roll, key, str(changes[key]))
        if "sidecar" in changes:
            roll.sidecar = bool(changes["sidecar"])

        return self.roll_status()

    def next_roll(self, label: str | None = None) -> dict:
        """Start a new roll: reset the frame counter and clear the notes.

        Without a label the trailing number increments, ``Roll014`` to
        ``Roll015`` -- the point of a button rather than fields to edit by hand.
        """
        roll = self._config.roll
        if label is None:
            match = re.search(r"^(.*?)(\d+)(\D*)$", roll.roll)
            if match:
                head, digits, tail = match.groups()
                label = f"{head}{int(digits) + 1:0{len(digits)}d}{tail}"
            else:
                label = f"{roll.roll}-2"
        # Stock and developer carry over; the notes were about the last roll.
        return self.update_roll(roll=label, frame=1, notes="")

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

        Order matters. Peaking is *measured* before the loupe crops, because
        nearest-neighbour magnification leaves flat blocks whose only edges are
        block boundaries. It is *applied* after inversion, because marking
        pixels red and then inverting would turn them all cyan.
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
        """Apply whichever inversion the view asks for."""
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

        Pointing at the rebate beats the automatic estimate, which assumes the
        densest part of the image approaches base density.
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
            # `replace(base=None)` cannot do this: it drops None and would
            # keep the base it claims to clear.
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
        # only reshape the curve built from it.
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
        """Relative sharpness of whatever is on screen.

        The loupe's region when the loupe is up, so the number matches the view.
        Meaningful while turning focus, not between sessions.
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
                    # Partial frames happen; skip one rather than tear the stream down.
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
        output_dir = self._config.capture.resolved_output_dir()
        paths = [Path(p) for p in backend.capture(output_dir)]
        output = self.output_options()

        # One shutter release is one frame, whatever number of files it wrote.
        frame = self._config.roll.frame
        when = datetime.now()
        paths = [self._file_into_roll(p, output_dir, frame, when) for p in paths]

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
                    # A failed development must not lose the capture itself.
                    entry["error"] = str(exc)
                    logger.error("Could not develop %s: %s", path.name, exc)
            files.append(entry)

        self._write_sidecar(paths, frame, files, when)
        # Advance even if renaming or the sidecar failed: the shutter fired,
        # so that frame number is spent.
        self._config.roll.frame = frame + 1

        record = {
            "name": files[0]["name"],
            "files": files,
            "count": len(files),
            "bytes": sum(f["bytes"] for f in files),
            "seconds": round(time.perf_counter() - started, 1),
            "roll": self._config.roll.roll,
            "frame": frame,
        }
        self._captures.insert(0, record)
        del self._captures[24:]
        return record

    def _file_into_roll(self, path: Path, output_dir: Path, frame: int, when) -> Path:
        """Rename one downloaded capture into the roll's structure.

        After the download, not by telling the camera where to write: that code
        pumps messages and marshals transfers and is the last place for a string
        template. A failure leaves the file where it is -- an unhelpful name is
        a nuisance, a lost capture is not recoverable.
        """
        roll = self._config.roll
        if not roll.template.strip():
            return path
        try:
            relative = naming.render(
                roll.template,
                roll=roll.roll,
                frame=frame,
                extension=path.suffix,
                original=path.stem,
                stock=roll.stock,
                when=when,
            )
            destination = naming.unique(output_dir / Path(relative))
            destination.parent.mkdir(parents=True, exist_ok=True)
            path.replace(destination)
        except (naming.NamingError, OSError) as exc:
            logger.error("Could not file %s into the roll: %s", path.name, exc)
            self._last_error = f"Kept the camera's name for {path.name}: {exc}"
            return path
        logger.info("Filed %s -> %s", path.name, destination.relative_to(output_dir))
        return destination

    def _write_sidecar(self, paths: list[Path], frame: int, files: list[dict], when) -> None:
        """Record the frame in roll.json. Never raises: it is only metadata."""
        roll = self._config.roll
        if not roll.sidecar or not paths:
            return
        metadata = sidecar.RollMetadata(
            roll=roll.roll,
            stock=roll.stock,
            developer=roll.developer,
            notes=roll.notes,
            date=roll.date,
        )
        # A template can spread frames over folders; each gets its own sidecar.
        by_folder: dict[Path, list[Path]] = {}
        for path in paths:
            by_folder.setdefault(path.parent, []).append(path)
        positives = {f["name"]: f["positive"] for f in files if f.get("positive")}
        for folder, group in by_folder.items():
            sidecar.record_capture(
                folder / sidecar.SIDECAR_NAME,
                metadata,
                frame,
                [p.name for p in group],
                [positives[p.name] for p in group if p.name in positives],
                when=when,
            )
