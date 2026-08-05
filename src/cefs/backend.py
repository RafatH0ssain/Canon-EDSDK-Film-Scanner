"""The contract every camera backend implements.

Defined before either implementation and imported by both, so the mock cannot
quietly grow into whatever shape the SDK code happened to need.

A mock proves internal consistency, never correctness: in the sibling CCAPI
project every significant bug passed a green suite first, because the mock had
been built from the same misunderstanding as the code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CameraInfo:
    """What we are connected to."""

    model: str
    backend: str
    lens: str = ""
    serial_withheld: bool = True  # never surface a body serial in the UI or logs


@dataclass(frozen=True)
class Capabilities:
    """What this body supports, asked rather than assumed.

    A capability reported missing is diagnosable; one silently bound to the
    wrong thing is not.
    """

    focus_drive: bool = False
    liveview_zoom: bool = False
    electronic_shutter: bool = False
    #: Why a capability is off, keyed by field name. Shown in the UI so an
    #: absent control explains itself instead of just vanishing.
    notes: tuple[tuple[str, str], ...] = ()

    def note_for(self, name: str) -> str:
        return dict(self.notes).get(name, "")


class CameraError(RuntimeError):
    """A backend-independent failure, already phrased for a person."""


@runtime_checkable
class CameraBackend(Protocol):
    """A camera, real or fake. Safe to call from any thread.

    The EDSDK backend achieves that by owning a thread and forwarding work to
    it; the mock has no thread affinity to respect.
    """

    def start(self) -> None:
        """Connect and begin streaming live view. Idempotent."""

    def stop(self) -> None:
        """Stop live view and disconnect. Idempotent and safe after a failure."""

    @property
    def info(self) -> CameraInfo:
        """What is connected."""

    @property
    def capabilities(self) -> Capabilities:
        """What it supports."""

    @property
    def settle_delay_s(self) -> float:
        """Seconds waited before firing, so the rig can stop vibrating.

        Settable while connected -- it is read at the moment of capture, so
        lengthening it should not need a reconnect.
        """

    @settle_delay_s.setter
    def settle_delay_s(self, seconds: float) -> None: ...

    def latest_frame(self) -> bytes | None:
        """Newest live-view frame as JPEG bytes, or ``None`` if none yet."""

    def drive_focus(self, direction: str, steps: int = 1, size: int = 2) -> None:
        """Drive focus ``"near"`` or ``"far"``; ``size`` 1 finest, 3 coarsest.

        More than one step is usually needed: a single one can move the lens by
        less than the frame noise.
        """

    def capture(self, destination_dir: Path) -> list[Path]:
        """Fire the shutter and download everything it produced.

        A list because one release can write several files -- RAW+JPEG sends a
        transfer for each. Originals are always kept; processing writes beside.
        """
