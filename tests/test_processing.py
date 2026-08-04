"""Tests for the pure processing functions.

No SDK, no camera, no network. These run anywhere.
"""

from __future__ import annotations

import numpy as np
import pytest

from cefs.mock.frames import make_negative
from cefs.processing.codec import DecodeError, decode_jpeg, encode_jpeg
from cefs.processing.invert import invert_linear
from cefs.processing.loupe import crop_zoom
from cefs.processing.peaking import apply_peaking, peaking_coverage, threshold_for
from cefs.processing.sharpness import corner_sharpness, sharpness, sharpness_of_region


@pytest.fixture
def frame() -> np.ndarray:
    return make_negative(width=320, height=213, color=False, seed=1)


# --- codec ------------------------------------------------------------------


def test_encode_decode_roundtrip(frame):
    decoded = decode_jpeg(encode_jpeg(frame))
    assert decoded.shape == frame.shape
    assert decoded.dtype == np.uint8


def test_decode_rejects_empty():
    with pytest.raises(DecodeError):
        decode_jpeg(b"")


def test_decode_rejects_garbage():
    with pytest.raises(DecodeError):
        decode_jpeg(b"definitely not a jpeg")


# --- inversion --------------------------------------------------------------


def test_invert_flips_around_midpoint():
    frame = np.array([[[0, 128, 255]]], dtype=np.uint8)
    assert invert_linear(frame).tolist() == [[[255, 127, 0]]]


def test_invert_is_its_own_inverse(frame):
    assert np.array_equal(invert_linear(invert_linear(frame)), frame)


def test_invert_does_not_modify_input(frame):
    before = frame.copy()
    invert_linear(frame)
    assert np.array_equal(frame, before)


def test_invert_rejects_non_8bit():
    with pytest.raises(TypeError):
        invert_linear(np.zeros((4, 4, 3), dtype=np.uint16))


def test_invert_treats_channels_identically():
    """Colour negatives are first-class; inversion must not assume greyscale."""
    frame = np.dstack([
        np.full((4, 4), 10, np.uint8),
        np.full((4, 4), 20, np.uint8),
        np.full((4, 4), 30, np.uint8),
    ])
    out = invert_linear(frame)
    assert (out[..., 0] == 245).all()
    assert (out[..., 1] == 235).all()
    assert (out[..., 2] == 225).all()


# --- loupe ------------------------------------------------------------------


def test_crop_zoom_keeps_output_size(frame):
    out = crop_zoom(frame, 0.5, 0.5, zoom=4.0)
    assert out.shape == frame.shape


def test_crop_zoom_custom_output_size(frame):
    out = crop_zoom(frame, 0.5, 0.5, zoom=2.0, output_size=(100, 50))
    assert out.shape[:2] == (50, 100)


def test_crop_zoom_at_one_returns_whole_frame(frame):
    assert np.array_equal(crop_zoom(frame, 0.5, 0.5, zoom=1.0), frame)


@pytest.mark.parametrize("cx,cy", [(0.0, 0.0), (1.0, 1.0), (0.0, 1.0), (-5.0, 9.0)])
def test_crop_zoom_clamps_at_edges(frame, cx, cy):
    """A centre near an edge slides inward rather than shrinking the result.

    Checking corner sharpness is one of the loupe's main uses, so an edge crop
    must still be full size.
    """
    out = crop_zoom(frame, cx, cy, zoom=4.0)
    assert out.shape == frame.shape


def test_crop_zoom_rejects_bad_zoom(frame):
    with pytest.raises(ValueError):
        crop_zoom(frame, zoom=0.0)


# --- sharpness --------------------------------------------------------------


def test_sharpness_rises_with_focus():
    sharp = make_negative(width=320, height=213, focus_error=0.0, seed=3)
    soft = make_negative(width=320, height=213, focus_error=8.0, seed=3)
    assert sharpness(sharp) > sharpness(soft)


def test_sharpness_separates_clearly():
    """The metric must do more than order them -- it has to be readable."""
    sharp = make_negative(width=480, height=320, focus_error=0.0, seed=5)
    soft = make_negative(width=480, height=320, focus_error=10.0, seed=5)
    assert sharpness(sharp) > sharpness(soft) * 3


def test_sharpness_of_region_clamps(frame):
    assert sharpness_of_region(frame, -1.0, 2.0, 0.25) >= 0.0


def test_sharpness_of_region_rejects_bad_size(frame):
    with pytest.raises(ValueError):
        sharpness_of_region(frame, size=0.0)


def test_corner_sharpness_reports_all_five(frame):
    corners = corner_sharpness(frame)
    assert set(corners) == {
        "top_left", "top_right", "bottom_left", "bottom_right", "center",
    }


# --- peaking ----------------------------------------------------------------


def test_peaking_threshold_is_monotonic():
    assert threshold_for(0.0) > threshold_for(0.5) > threshold_for(1.0)


def test_peaking_marks_more_when_sensitive(frame):
    assert peaking_coverage(frame, 1.0) >= peaking_coverage(frame, 0.0)


def test_peaking_marks_less_on_a_soft_frame():
    """Absolute thresholds, not percentiles.

    A percentile would mark a fixed fraction of pixels by construction and so
    report the same coverage on mush as on a sharp frame.
    """
    sharp = make_negative(width=320, height=213, focus_error=0.0, seed=9)
    soft = make_negative(width=320, height=213, focus_error=10.0, seed=9)
    assert peaking_coverage(sharp, 0.5) > peaking_coverage(soft, 0.5)


def test_apply_peaking_preserves_shape(frame):
    assert apply_peaking(frame, 0.5).shape == frame.shape


def test_apply_peaking_rejects_grayscale():
    with pytest.raises(ValueError):
        apply_peaking(np.zeros((8, 8), dtype=np.uint8))


# --- fixtures themselves ----------------------------------------------------


def test_negative_is_bgr_uint8():
    frame = make_negative(width=64, height=48)
    assert frame.shape == (48, 64, 3)
    assert frame.dtype == np.uint8


def test_negative_is_deterministic_with_a_seed():
    a = make_negative(width=64, height=48, seed=42)
    b = make_negative(width=64, height=48, seed=42)
    assert np.array_equal(a, b)


def test_colour_negative_has_an_orange_mask():
    """Blue is heavily attenuated -- which is why naive inversion goes cyan."""
    frame = make_negative(width=64, height=48, color=True, seed=2, grain=0.0)
    blue, green, red = frame[..., 0].mean(), frame[..., 1].mean(), frame[..., 2].mean()
    assert red > green > blue


def test_bw_negative_is_near_neutral():
    frame = make_negative(width=64, height=48, color=False, seed=2, grain=0.0)
    channels = [frame[..., i].mean() for i in range(3)]
    assert max(channels) - min(channels) < 20
