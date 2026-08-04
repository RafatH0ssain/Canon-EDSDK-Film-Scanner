"""A camera-free backend and the synthetic frames it serves."""

from cefs.mock.camera import MockCamera
from cefs.mock.frames import make_negative

__all__ = ["MockCamera", "make_negative"]
