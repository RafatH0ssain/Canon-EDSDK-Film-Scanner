"""A camera-free backend, so most development needs neither SDK nor hardware.

It implements the same :class:`~cefs.backend.CameraBackend` contract as the
EDSDK one, generating synthetic negatives and tracking a pretend focus position.

**What this cannot tell you.** A mock encodes its author's assumptions. It shows
the app is internally consistent; it can never show the app matches a real
camera. In the sibling CCAPI project every significant bug passed a green suite
first, because the mock had been built from the same misunderstanding as the
code under test. Anything protocol- or performance-shaped must be checked
against real hardware.

One deliberate piece of realism: focus moves by a small amount per step, so a
single step is nearly invisible, exactly as on a real RF macro lens. A mock that
snapped instantly into focus would hide the reason v0.2 has to accumulate steps.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
import numpy as np

from cefs.backend import Capabilities, CameraError, CameraInfo
from cefs.mock.frames import make_negative
from cefs.processing.codec import encode_jpeg

#: Focus units from perfectly sharp to fully defocused.
_FOCUS_RANGE = 24.0

#: How far one step moves focus, per step size. Small on purpose.
_STEP_SIZES = {1: 0.25, 2: 0.75, 3: 2.0}


class MockCamera:
    """A fake camera producing synthetic film negatives."""

    def __init__(
        self,
        width: int = 960,
        height: int = 640,
        color: bool = False,
        target_fps: int = 30,
        settle_delay_s: float = 0.0,
        focus_error: float = 6.0,
        seed: int | None = 7,
    ) -> None:
        self._width = width
        self._height = height
        self._color = color
        self._interval = 1.0 / max(target_fps, 1)
        self._settle_delay_s = settle_delay_s
        self._seed = seed

        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._frame: bytes | None = None
        self._focus_error = float(np.clip(focus_error, 0.0, _FOCUS_RANGE))
        self._shots = 0
        self._render_base: np.ndarray | None = None
        self._render_key: tuple | None = None
        self._noise_bank: list[np.ndarray] = []
        self._noise_index = 0

        self._info = CameraInfo(model="Mock EOS (no camera)", backend="mock", lens="Mock 85mm Macro")
        self._capabilities = Capabilities(
            focus_drive=True,
            liveview_zoom=False,
            electronic_shutter=False,
            notes=(
                ("liveview_zoom", "The mock has no camera-side magnification, matching an EOS R7."),
                ("electronic_shutter", "EDSDK does not expose shutter mode; the mock matches."),
            ),
        )

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="cefs-mock", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None

    @property
    def info(self) -> CameraInfo:
        return self._info

    @property
    def capabilities(self) -> Capabilities:
        return self._capabilities

    # --- operations ---------------------------------------------------------

    def latest_frame(self) -> bytes | None:
        with self._lock:
            return self._frame

    def drive_focus(self, direction: str, steps: int = 1, size: int = 2) -> None:
        if direction not in ("near", "far"):
            raise ValueError(f"direction must be 'near' or 'far', got {direction!r}")
        size = max(1, min(int(size), 3))
        delta = _STEP_SIZES[size] * max(1, int(steps))
        with self._lock:
            # "near" walks towards sharp, "far" away from it. Which direction is
            # sharp is arbitrary in a mock; what matters is that it is monotonic
            # and small per step.
            self._focus_error += -delta if direction == "near" else delta
            self._focus_error = float(np.clip(self._focus_error, 0.0, _FOCUS_RANGE))

    def capture(self, destination_dir: Path) -> Path:
        destination_dir = Path(destination_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)
        if self._settle_delay_s > 0:
            time.sleep(self._settle_delay_s)

        with self._lock:
            self._shots += 1
            index = self._shots
            focus_error = self._focus_error

        # Captures are full resolution, unlike the live-view stream.
        frame = make_negative(
            width=self._width * 4,
            height=self._height * 4,
            color=self._color,
            phase=0.0,
            focus_error=focus_error,
            seed=self._seed,
        )
        destination = _unique_path(destination_dir / f"MOCK_{index:04d}.jpg")
        destination.write_bytes(encode_jpeg(frame, quality=95))
        return destination

    # --- the frame generator ------------------------------------------------

    def _render(self, focus_error: float) -> np.ndarray:
        """The scene without grain, re-rendered only when focus changes.

        Rendering a synthetic negative costs ~110 ms, which would cap the mock
        near 9 fps against a real camera's 60. A mock that slow is not merely
        inconvenient: it invites tuning the stream loop against timings that do
        not exist on hardware.

        The scene is deliberately static, which is also what a negative on a
        copy stand actually is. Only grain changes frame to frame -- enough to
        show the stream is live, and true to the subject.
        """
        key = round(focus_error, 2)
        if self._render_key != key:
            self._render_base = make_negative(
                width=self._width,
                height=self._height,
                color=self._color,
                phase=0.0,
                focus_error=focus_error,
                grain=0.0,
                seed=self._seed,
            )
            self._render_key = key
        return self._render_base

    def _grain_field(self) -> np.ndarray:
        """One of a few precomputed noise fields, cycled.

        Drawing 1.8 M fresh normals per frame costs ~32 ms. Cycling a small bank
        looks live and costs nothing.
        """
        if not self._noise_bank:
            rng = np.random.default_rng(self._seed)
            shape = (self._height, self._width, 3)
            self._noise_bank = [
                rng.normal(0.0, 5.0, shape).astype(np.int16) for _ in range(6)
            ]
        self._noise_index = (self._noise_index + 1) % len(self._noise_bank)
        return self._noise_bank[self._noise_index]

    def _run(self) -> None:
        while self._running:
            started = time.perf_counter()
            with self._lock:
                focus_error = self._focus_error
            frame = cv2.add(
                self._render(focus_error), self._grain_field(), dtype=cv2.CV_8U
            )
            data = encode_jpeg(frame)
            with self._lock:
                self._frame = data
            elapsed = time.perf_counter() - started
            time.sleep(max(0.0, self._interval - elapsed))


def _unique_path(path: Path) -> Path:
    """A path that does not exist yet. Captures are never overwritten."""
    if not path.exists():
        return path
    for n in range(1, 10000):
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise CameraError(f"Could not find a free filename beside {path}")
