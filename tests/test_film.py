"""v0.3 inversion: the orange mask, tone response, and both film types.

These tests exist because "looks about right" is not a check. A wrong inversion
still produces a plausible picture -- that is exactly what made the v0.1 linear
flip acceptable for so long -- so the assertions here are about measurable
properties: is the cast gone, is the response monotonic, is black-and-white
actually neutral.
"""

from __future__ import annotations

import numpy as np
import pytest

from cefs.mock.frames import make_negative
from cefs.processing.film import (
    FilmParams,
    analyse,
    build_luts,
    invert_preview,
    invert_raw,
    linear16_to_linear,
    sample_base,
    srgb_to_linear,
)
from cefs.processing.invert import invert_linear


def spread(image: np.ndarray) -> float:
    """How far apart the channel means are. Zero means neutral."""
    means = [float(image[..., c].mean()) for c in range(3)]
    return max(means) - min(means)


@pytest.fixture
def colour_negative() -> np.ndarray:
    return make_negative(width=320, height=213, color=True, seed=3, grain=0.01)


@pytest.fixture
def bw_negative() -> np.ndarray:
    return make_negative(width=320, height=213, color=False, seed=3, grain=0.01)


# --- the orange mask ---------------------------------------------------------


def test_removes_the_orange_mask(colour_negative):
    """The headline claim of v0.3, stated as a number.

    A linear flip inverts the mask along with the image, which is why an
    un-neutralised colour negative previews cyan.
    """
    linear = spread(invert_linear(colour_negative))
    film = spread(invert_preview(colour_negative, FilmParams(mode="color")))
    assert film < linear / 5, f"cast only improved from {linear:.1f} to {film:.1f}"


def test_midtone_matching_helps(colour_negative):
    """Matching highlights alone leaves a cast; the per-channel fit removes it."""
    without = spread(invert_preview(colour_negative, FilmParams(mode="color", auto_balance=False)))
    with_ = spread(invert_preview(colour_negative, FilmParams(mode="color", auto_balance=True)))
    assert with_ < without


def test_base_sampling_sees_the_mask(colour_negative):
    """The film base must come out orange: red passes, blue is attenuated."""
    linear = srgb_to_linear(colour_negative[:, :, ::-1])
    r, g, b = sample_base(linear)
    assert r > g > b


def test_rebate_region_agrees_with_the_automatic_estimate(colour_negative):
    """The two ways of finding the base should not disagree wildly."""
    linear = srgb_to_linear(colour_negative[:, :, ::-1])
    auto = np.array(sample_base(linear))
    rebate = np.array(sample_base(linear, region=(0.0, 0.0, 0.04, 1.0)))
    assert np.allclose(auto, rebate, atol=0.06)


# --- black and white ---------------------------------------------------------


def test_bw_output_is_neutral(bw_negative):
    """B&W is first class, not colour with the saturation turned down."""
    assert spread(invert_preview(bw_negative, FilmParams(mode="bw"))) < 0.01


def test_bw_mode_neutralises_even_a_colour_input(colour_negative):
    """Choosing B&W must give grey, whatever it is handed."""
    out = invert_preview(colour_negative, FilmParams(mode="bw"))
    assert spread(out) < 0.01


def test_bw_is_not_just_a_desaturated_colour_path(bw_negative):
    """A monochrome negative must not gain a cast from per-channel handling."""
    colour_path = invert_preview(bw_negative, FilmParams(mode="color"))
    bw_path = invert_preview(bw_negative, FilmParams(mode="bw"))
    assert spread(bw_path) <= spread(colour_path)


# --- tone response -----------------------------------------------------------


def test_inversion_is_monotonic_decreasing():
    """Denser negative must mean brighter positive, everywhere.

    A non-monotonic transfer would produce solarised patches that still look
    like a photograph at a glance.
    """
    ramp = np.linspace(8, 250, 64, dtype=np.uint8)
    frame = np.repeat(ramp[None, :, None], 3, axis=2)
    frame = np.repeat(frame, 8, axis=0).astype(np.uint8)
    out = invert_preview(frame, FilmParams(mode="bw"))
    row = out[4, :, 0].astype(int)
    assert np.all(np.diff(row) <= 0), "positive must fall as the negative brightens"


def test_darkest_negative_gives_brightest_positive(colour_negative):
    params = FilmParams(mode="bw")
    out = invert_preview(colour_negative, params)
    grey_in = colour_negative.mean(axis=2)
    grey_out = out.mean(axis=2)
    darkest = np.unravel_index(np.argmin(grey_in), grey_in.shape)
    brightest = np.unravel_index(np.argmax(grey_in), grey_in.shape)
    assert grey_out[darkest] > grey_out[brightest]


def test_uses_most_of_the_output_range(colour_negative):
    out = invert_preview(colour_negative, FilmParams(mode="color"))
    p1, p99 = np.percentile(out, 1), np.percentile(out, 99)
    assert p99 - p1 > 140, f"positive only spans {p99 - p1:.0f} of 255"


def test_contrast_increases_spread(colour_negative):
    low = invert_preview(colour_negative, FilmParams(mode="bw", contrast=1.0))
    high = invert_preview(colour_negative, FilmParams(mode="bw", contrast=2.2))
    assert high.std() > low.std()


def test_exposure_brightens(colour_negative):
    dim = invert_preview(colour_negative, FilmParams(mode="bw", exposure=0.5))
    bright = invert_preview(colour_negative, FilmParams(mode="bw", exposure=2.0))
    assert bright.mean() > dim.mean()


# --- parameters --------------------------------------------------------------


def test_white_point_must_exceed_black_point(colour_negative):
    with pytest.raises(ValueError):
        invert_preview(colour_negative, FilmParams(black_point=0.9, white_point=0.2))


def test_params_replace_ignores_none():
    params = FilmParams(contrast=1.9)
    assert params.replace(contrast=None).contrast == 1.9
    assert params.replace(contrast=1.2).contrast == 1.2


def test_explicit_base_is_respected(colour_negative):
    """A base the user pointed at must override the automatic estimate."""
    a = invert_preview(colour_negative, FilmParams(base=(0.9, 0.4, 0.2)))
    b = invert_preview(colour_negative, FilmParams(base=(0.5, 0.5, 0.5)))
    assert not np.array_equal(a, b)


def test_preview_rejects_wrong_dtype():
    with pytest.raises(TypeError):
        invert_preview(np.zeros((4, 4, 3), np.uint16), FilmParams())


# --- 16-bit path -------------------------------------------------------------


def test_raw_path_returns_16_bit(colour_negative):
    linear16 = (srgb_to_linear(colour_negative[:, :, ::-1]) * 65535).astype(np.uint16)
    out = invert_raw(linear16, FilmParams(mode="color"))
    assert out.dtype == np.uint16
    assert out.shape == colour_negative.shape


def test_16_bit_keeps_more_levels_than_8():
    """The reason the file path stays 16-bit rather than reusing the preview.

    The source must be genuinely 16-bit. Deriving it from an 8-bit image caps
    the input at 256 distinct values per channel, so the output could never
    exceed that however good the pipeline is -- the measurement would be of the
    fixture, not the code.
    """
    # A smooth negative-like ramp with far more than 256 distinct levels.
    ramp = np.linspace(2000, 60000, 2048, dtype=np.uint16)
    linear16 = np.repeat(np.repeat(ramp[None, :, None], 3, axis=2), 16, axis=0)
    assert len(np.unique(linear16)) > 1000, "fixture is not really 16-bit"

    out16 = invert_raw(linear16, FilmParams(mode="bw"))
    out8 = invert_preview(
        (linear16 >> 8).astype(np.uint8)[:, :, ::-1], FilmParams(mode="bw")
    )
    assert len(np.unique(out16)) > 256
    assert len(np.unique(out16)) > len(np.unique(out8))


def test_raw_rejects_wrong_dtype():
    with pytest.raises(TypeError):
        invert_raw(np.zeros((4, 4, 3), np.uint8), FilmParams())


def test_both_paths_agree(colour_negative):
    """Preview and file must apply the same curve.

    They share :func:`analyse` and :func:`build_luts`; this checks that sharing
    actually holds end to end, since a preview that lies about the saved result
    is worse than no preview.
    """
    params = FilmParams(mode="color")
    linear8 = srgb_to_linear(colour_negative[:, :, ::-1])
    linear16 = (linear8 * 65535).astype(np.uint16)

    measured = analyse(linear8, params)
    preview = invert_preview(colour_negative, params, measured)[:, :, ::-1]
    full = invert_raw(linear16, params, measured)

    # Compare on the same scale. Quantisation of the 8-bit input makes them
    # differ slightly, but the tone curve must be the same shape.
    a = preview.astype(np.float64) / 255.0
    b = full.astype(np.float64) / 65535.0
    assert np.abs(a - b).mean() < 0.02


# --- lookup tables -----------------------------------------------------------


def test_luts_have_the_right_shape():
    params = FilmParams()
    measured = {"base": (0.9, 0.5, 0.3), "scales": (2.0, 2.0, 2.0), "gammas": (1.0, 1.0, 1.0)}
    assert build_luts(measured, params, 8, 8).shape == (3, 256)
    assert build_luts(measured, params, 16, 16).shape == (3, 65536)


def test_luts_reject_unknown_input_depth():
    measured = {"base": (0.9, 0.5, 0.3), "scales": (2.0, 2.0, 2.0), "gammas": (1.0, 1.0, 1.0)}
    with pytest.raises(ValueError):
        build_luts(measured, FilmParams(), 12, 8)


def test_analysis_is_reusable(colour_negative):
    """Reusing a measurement must give the same image, or caching would lie."""
    params = FilmParams(mode="color")
    measured = analyse(srgb_to_linear(colour_negative[:, :, ::-1]), params)
    a = invert_preview(colour_negative, params, measured)
    b = invert_preview(colour_negative, params, measured)
    assert np.array_equal(a, b)


def test_linear16_conversion_roundtrips():
    values = np.array([[[0, 32768, 65535]]], dtype=np.uint16)
    linear = linear16_to_linear(values)
    assert linear[0, 0, 0] == pytest.approx(0.0)
    assert linear[0, 0, 2] == pytest.approx(1.0)
