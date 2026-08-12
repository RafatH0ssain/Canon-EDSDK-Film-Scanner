"""Hold the image backend to what OpenCV produced.

OpenCV is being removed because its wheel bundles a GPL-licensed FFmpeg (x264
and x265) that this project never calls -- 49 MB of video codecs and a copyleft
obligation, for JPEG encoding and a Scharr gradient.

The replacement has to behave like the thing it replaces. Some of it can be
exact; interpolation and edge kernels cannot be, because different
implementations round differently. Where exactness is impossible the tolerance
is stated and justified rather than left to drift.

Fixtures come from ``tests/fixtures/regenerate_golden.py``, recorded while
OpenCV was still installed. Regenerate them only to abandon OpenCV's behaviour
deliberately -- never to make a failing test pass.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent / "fixtures"))
from regenerate_golden import HERE, source_16bit, source_frame  # noqa: E402


def golden(name: str) -> np.ndarray:
    path = HERE / name
    if not path.is_file():
        pytest.skip(f"{name} missing; run tests/fixtures/regenerate_golden.py")
    return np.load(path)


@pytest.fixture
def frame() -> np.ndarray:
    return source_frame()


# --- JPEG ------------------------------------------------------------------

def test_jpeg_decode_matches_opencv_exactly(frame):
    """Decoding is deterministic: same file, same pixels, or the pipeline shifts."""
    from cefs.processing.codec import decode_jpeg

    data = (HERE / "jpeg_q90.jpg").read_bytes()
    assert np.array_equal(decode_jpeg(data), golden("jpeg_q90_decoded.npy"))


def test_jpeg_encode_round_trips_within_one_generation(frame):
    """Re-encoding must not visibly degrade or shift colour.

    Not byte-identical to OpenCV's file: both use libjpeg but may differ in
    subsampling defaults. What matters is that a decode of our encode matches a
    decode of theirs closely enough to be invisible.
    """
    from cefs.processing.codec import decode_jpeg, encode_jpeg

    ours = decode_jpeg(encode_jpeg(frame, quality=90)).astype(np.int16)
    theirs = golden("jpeg_q90_decoded.npy").astype(np.int16)
    assert ours.shape == theirs.shape
    assert np.abs(ours - theirs).mean() < 2.0, "JPEG encode drifted from OpenCV's"


# --- greyscale --------------------------------------------------------------

@pytest.mark.parametrize("swap_rb,name", [(False, "gray_bgr2gray.npy"), (True, "gray_rgb2gray.npy")])
def test_greyscale_matches_opencv(frame, swap_rb, name):
    """Luma weights must match, and so must channel order.

    BGR2GRAY and RGB2GRAY use the same weights against reversed channels, so
    getting the order wrong shifts every reading slightly -- enough to move the
    sharpness metric without ever looking broken.
    """
    from cefs.processing.grey import to_grey

    source = frame[:, :, ::-1] if swap_rb else frame
    assert np.abs(to_grey(source).astype(int) - golden(name).astype(int)).max() <= 1


# --- edges ------------------------------------------------------------------

def test_scharr_magnitude_tracks_opencv(frame):
    """The sharpness metric is built on this, and its scale is load-bearing.

    A gated Tenengrad separating focus by 11.1x on real frames, and a refusal
    threshold of 0.05, both assume this magnitude's scale. Correlation is not
    enough; the values have to stay put.
    """
    from cefs.processing.edges import gradient_magnitude, to_grey_for_edges

    mag = gradient_magnitude(to_grey_for_edges(frame))
    expected = golden("scharr_magnitude.npy")

    assert mag.shape == expected.shape

    # Borders differ by construction: OpenCV reflects, and any replacement has
    # to pick something. Interiors are what the metric actually integrates.
    inner = (slice(2, -2), slice(2, -2))
    a, b = mag[inner], expected[inner]

    # Not bit-exact, and it cannot be. OpenCV's 8-bit luma does not match even
    # its own documented fixed-point formula -- it takes SIMD paths that round
    # differently -- so 57 pixels in 19200 land a count apart, and the kernel
    # multiplies that by up to 10. Measured, that is a mean absolute difference
    # of 0.0996 against gradients in the hundreds: 0.0046% on the mean. What
    # must not move is the scale the metric is calibrated against.
    assert np.abs(a - b).mean() < 0.5, "gradient magnitude diverged pixel to pixel"
    assert abs(a.mean() - b.mean()) / b.mean() < 0.001, "gradient scale shifted"


def test_the_sharpness_readout_does_not_move(frame):
    """The number the UI, the docs and the refusal threshold all depend on.

    Everything above is machinery; this is the observable. A drift here would
    silently recalibrate the focus bar and the 0.05 floor that stops
    check_camera reporting success from noise.
    """
    from cefs.processing.sharpness import sharpness

    expected_sharp, expected_blur = golden("sharpness_scalars.npy")
    blurred = golden("blurred_frame.npy")

    got_sharp, got_blur = sharpness(frame), sharpness(blurred)

    assert abs(got_sharp - expected_sharp) / expected_sharp < 0.001
    assert abs(got_blur - expected_blur) / expected_blur < 0.001
    # The separation is what makes the metric useful at all.
    assert abs((got_sharp / got_blur) - (expected_sharp / expected_blur)) < 0.05


# --- resize -----------------------------------------------------------------

def test_downscale_is_close_to_inter_area(frame):
    """Used by the loupe when shrinking. Area-averaging, not nearest."""
    from cefs.processing.loupe import resize_frame

    ours = resize_frame(frame, 80, 60).astype(np.int16)
    theirs = golden("resize_down_area.npy").astype(np.int16)
    assert ours.shape == theirs.shape
    assert np.abs(ours - theirs).mean() < 1.5


def test_upscale_is_exactly_nearest(frame):
    """Enlarging must stay nearest-neighbour: the loupe exists to show pixels.

    Any smoothing here invents detail the camera never sent, which is the one
    thing a focus aid must not do.
    """
    from cefs.processing.loupe import resize_frame

    assert np.array_equal(resize_frame(frame, 320, 240), golden("resize_up_nearest.npy"))


# --- saturating add ---------------------------------------------------------

def test_saturating_add_matches_opencv(frame):
    """Must clip at 255, not wrap. Wrapping turns bright grain black."""
    from cefs.processing.arrays import add_saturating

    other = np.roll(frame, 7, axis=1)
    assert np.array_equal(add_saturating(frame, other), golden("add_saturated.npy"))


# --- 16-bit TIFF ------------------------------------------------------------

@pytest.mark.parametrize("compression", ["none", "deflate"])
def test_16bit_tiff_round_trips_losslessly(tmp_path, compression):
    """16-bit RGB TIFF is the whole point of the develop path.

    Pillow cannot represent it at all -- it raises on a 3-channel uint16 array
    -- which is why the TIFF writer is tifffile rather than Pillow.
    """
    from cefs.processing.tiffio import read_tiff, write_tiff

    source = source_16bit()
    path = tmp_path / f"x_{compression}.tif"
    write_tiff(path, source, compression=compression)
    back = read_tiff(path)
    assert back.dtype == np.uint16
    assert np.array_equal(back, source), "TIFF round trip was not lossless"


def test_channel_order_survives_a_tiff_round_trip(tmp_path):
    """The trap this refactor exists to avoid.

    OpenCV is BGR; tifffile and Pillow are RGB. A mechanical swap transposes
    red and blue in every saved file, which on a colour-negative inverter looks
    entirely plausible and is entirely wrong. A red-dominant input must come
    back red-dominant.
    """
    from cefs.processing.tiffio import read_tiff, write_tiff

    red = np.zeros((8, 8, 3), np.uint16)
    red[..., 0] = 60000
    path = tmp_path / "red.tif"
    write_tiff(path, red, compression="none")
    back = read_tiff(path)
    assert back[..., 0].mean() > back[..., 2].mean(), "red and blue were swapped"
    assert np.array_equal(back, red)
