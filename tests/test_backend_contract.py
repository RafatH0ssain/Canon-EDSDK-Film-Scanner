"""The mock must satisfy the same contract as the real backend.

This is a consistency check, not a correctness one. It cannot tell us the EDSDK
backend behaves like the mock -- only real hardware can. Its value is catching
the case where the two drift apart in shape.
"""

from __future__ import annotations

import inspect

import pytest

from cefs.backend import CameraBackend, Capabilities, CameraInfo
from cefs.mock.camera import MockCamera
from cefs.processing.codec import decode_jpeg


@pytest.fixture
def camera():
    cam = MockCamera(width=160, height=120, target_fps=60)
    cam.start()
    yield cam
    cam.stop()


def test_mock_satisfies_the_protocol(camera):
    assert isinstance(camera, CameraBackend)


def test_edsdk_backend_matches_the_protocol_signatures():
    """Both backends expose the same methods with the same parameters.

    Imported lazily and inspected only -- this never loads the SDK, so it runs
    on a machine that has none.
    """
    from cefs.edsdk.camera import EdsdkCamera

    for name in ("start", "stop", "latest_frame", "drive_focus", "capture"):
        protocol_sig = inspect.signature(getattr(CameraBackend, name))
        for impl in (EdsdkCamera, MockCamera):
            assert hasattr(impl, name), f"{impl.__name__} is missing {name}"
            impl_sig = inspect.signature(getattr(impl, name))
            assert list(impl_sig.parameters) == list(protocol_sig.parameters), (
                f"{impl.__name__}.{name}{impl_sig} does not match "
                f"the contract {protocol_sig}"
            )

    for name in ("info", "capabilities"):
        assert isinstance(getattr(EdsdkCamera, name), property)
        assert isinstance(getattr(MockCamera, name), property)


def test_info_and_capabilities_types(camera):
    assert isinstance(camera.info, CameraInfo)
    assert isinstance(camera.capabilities, Capabilities)
    assert camera.info.backend == "mock"


def test_produces_decodable_frames(camera):
    frame = _wait_for_frame(camera)
    image = decode_jpeg(frame)
    assert image.shape == (120, 160, 3)


def test_frames_advance(camera):
    """The stream must actually move, or 'live view works' proves nothing."""
    first = _wait_for_frame(camera)
    deadline = time_limit()
    while camera.latest_frame() is first and not deadline():
        pass
    assert camera.latest_frame() is not first


def test_start_is_idempotent(camera):
    camera.start()
    camera.start()
    assert _wait_for_frame(camera)


def test_stop_is_idempotent(camera):
    camera.stop()
    camera.stop()


def test_drive_focus_rejects_bad_direction(camera):
    with pytest.raises(ValueError):
        camera.drive_focus("sideways")


def test_focus_changes_sharpness(camera):
    """Driving focus must change the image, not merely return successfully.

    The v0.0 spike returned EDS_ERR_OK from a focus command that moved nothing
    detectable, so a backend test that only checks for no exception would have
    passed on a lens that never moved.
    """
    from cefs.processing.sharpness import sharpness

    _wait_for_frame(camera)
    before = sharpness(decode_jpeg(camera.latest_frame()))
    camera.drive_focus("near", steps=20, size=3)
    after = sharpness(decode_jpeg(_next_frame(camera)))
    assert after != pytest.approx(before, rel=0.05)


def test_single_step_is_nearly_invisible(camera):
    """One step moves far less than many, matching a real RF macro lens.

    A mock that snapped into focus in one step would hide why v0.2 has to
    accumulate steps per keypress.
    """
    from cefs.processing.sharpness import sharpness

    _wait_for_frame(camera)
    start = sharpness(decode_jpeg(camera.latest_frame()))
    camera.drive_focus("near", steps=1, size=1)
    one = abs(sharpness(decode_jpeg(_next_frame(camera))) - start)
    camera.drive_focus("near", steps=20, size=3)
    many = abs(sharpness(decode_jpeg(_next_frame(camera))) - start)
    assert many > one * 5


def test_capture_returns_a_list_of_files(camera, tmp_path):
    """One release can write several files -- RAW+JPEG sends a transfer each."""
    paths = camera.capture(tmp_path)
    assert isinstance(paths, list) and paths
    for path in paths:
        assert path.exists()
        assert path.stat().st_size > 0
        assert path.parent == tmp_path


def test_capture_never_overwrites(camera, tmp_path):
    """The original capture is the one thing that cannot be regenerated."""
    first = camera.capture(tmp_path)[0]
    first.write_bytes(b"sentinel")
    second = camera.capture(tmp_path)[0]
    assert second != first
    assert first.read_bytes() == b"sentinel"


# --- helpers ----------------------------------------------------------------


def time_limit(seconds: float = 5.0):
    import time

    end = time.perf_counter() + seconds
    return lambda: time.perf_counter() > end


def _wait_for_frame(camera, seconds: float = 5.0) -> bytes:
    expired = time_limit(seconds)
    while not expired():
        frame = camera.latest_frame()
        if frame is not None:
            return frame
    raise AssertionError("No frame within the time limit.")


def _next_frame(camera, seconds: float = 5.0, skip: int = 3) -> bytes:
    """Wait for a frame generated *after* the current one.

    Skips several frames rather than one: the generator may already have been
    midway through rendering when focus changed, so the immediately following
    frame can still carry the old focus and the test would compare two
    identical states.
    """
    frame = camera.latest_frame()
    for _ in range(skip):
        current = frame
        expired = time_limit(seconds)
        while not expired():
            frame = camera.latest_frame()
            if frame is not None and frame is not current:
                break
        else:
            raise AssertionError("No new frame within the time limit.")
    return frame
