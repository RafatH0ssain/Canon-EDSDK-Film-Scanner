"""Developing a captured negative into a positive file."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from cefs.processing.codec import encode_jpeg
from cefs.processing.develop import (
    DevelopError,
    OutputOptions,
    develop,
    developable,
    positive_path,
)
from cefs.processing.film import FilmParams, pq_to_linear
from cefs.mock.frames import make_negative


@pytest.fixture
def negative_jpeg(tmp_path):
    path = tmp_path / "IMG_0001.jpg"
    path.write_bytes(encode_jpeg(make_negative(width=320, height=213, color=True, seed=3), 95))
    return path


#: Linear range a real negative on a light table occupies, measured from an
#: EOS R7 .HIF: 160-660 cd/m^2, which is 0.016-0.066 of PQ's 10000 nit peak.
#: The synthetic scene is placed in the same window so the tests exercise the
#: part of the curve the camera actually uses.
_REAL_RANGE = (0.016, 0.066)


@pytest.fixture
def pq_scene():
    """One scene in linear RGB, 0-1, scaled the way a real negative sits."""
    bgr = make_negative(width=320, height=213, color=True, seed=5)
    linear = (bgr[:, :, ::-1] / 255.0).astype(np.float64) ** 2.2
    low, high = _REAL_RANGE
    return low + linear * (high - low)


@pytest.fixture
def negative_heif(tmp_path, pq_scene):
    """A 10-bit PQ HEIF, written the way an EOS body writes one.

    Encoded through the PQ curve and saved with the same NCLX profile an R7
    writes, so the file genuinely carries the transfer characteristic the
    reader has to detect rather than one asserted in a stub.
    """
    pillow_heif = pytest.importorskip("pillow_heif")

    # Invert the EOTF by searching the table rather than restating the maths,
    # so a sign error in this encoder cannot cancel one in the decoder.
    table = pq_to_linear(np.arange(1024) / 1023.0)
    pq10 = np.clip(np.searchsorted(table, pq_scene), 0, 1023)
    pq16 = (pq10 << 6).astype(np.uint16)

    path = tmp_path / "IMG_0002.HIF"
    heif = pillow_heif.from_bytes(
        mode="RGB;16",
        size=(pq16.shape[1], pq16.shape[0]),
        data=np.ascontiguousarray(pq16).tobytes(),
    )
    heif.save(
        str(path),
        quality=-1,
        chroma=444,
        # 16 = SMPTE ST 2084 (PQ), 9 = BT.2020, exactly what an R7 records.
        nclx_profile={
            "color_primaries": 9,
            "transfer_characteristics": 16,
            "matrix_coefficients": 9,
            "full_range_flag": 1,
        },
    )
    return path


def test_writes_a_positive_beside_the_original(negative_jpeg):
    out = develop(negative_jpeg, FilmParams(mode="color"))
    assert out.exists()
    assert out.parent == negative_jpeg.parent
    assert "positive" in out.name


def test_original_is_untouched(negative_jpeg):
    """Non-destructive: the capture is the one irreplaceable artefact."""
    before = negative_jpeg.read_bytes()
    develop(negative_jpeg, FilmParams(mode="color"))
    assert negative_jpeg.read_bytes() == before


def test_positive_is_actually_inverted(negative_jpeg):
    """Dark negative must give a bright positive, not merely a new file."""
    original = cv2.imread(str(negative_jpeg), cv2.IMREAD_COLOR)
    out = cv2.imread(str(develop(negative_jpeg, FilmParams(mode="bw"))), cv2.IMREAD_COLOR)
    dark = original.mean(axis=2) < np.percentile(original.mean(axis=2), 10)
    assert out.mean(axis=2)[dark].mean() > out.mean(axis=2).mean()


def test_never_overwrites_an_existing_positive(negative_jpeg):
    a = develop(negative_jpeg, FilmParams())
    b = develop(negative_jpeg, FilmParams())
    assert a != b and a.exists() and b.exists()


def test_raw_would_go_to_tiff(tmp_path):
    """RAW keeps 16 bits, so it must not be written as JPEG."""
    assert positive_path(tmp_path / "IMG_0001.CR3").suffix == ".tif"
    assert positive_path(tmp_path / "IMG_0001.HIF").suffix == ".tif"
    assert positive_path(tmp_path / "IMG_0001.jpg").suffix == ".jpg"


def test_format_can_be_forced_either_way(tmp_path):
    assert positive_path(
        tmp_path / "IMG_0001.CR3", output=OutputOptions(format="jpeg")
    ).suffix == ".jpg"
    assert positive_path(
        tmp_path / "IMG_0001.jpg", output=OutputOptions(format="tiff")
    ).suffix == ".tif"


# --- HEIF --------------------------------------------------------------------


def test_heif_develops_to_a_16_bit_tiff(negative_heif):
    out = develop(negative_heif, FilmParams(mode="color"))
    assert out.suffix == ".tif"
    image = cv2.imread(str(out), cv2.IMREAD_UNCHANGED)
    assert image is not None and image.dtype == np.uint16


def test_heif_positive_is_actually_inverted(negative_heif, pq_scene):
    """Dense parts of the negative must come out bright, as for any other source."""
    out = cv2.imread(str(develop(negative_heif, FilmParams(mode="bw"))), cv2.IMREAD_UNCHANGED)
    scene = pq_scene.mean(axis=2)
    dark = scene < np.percentile(scene, 10)
    assert out.mean(axis=2)[dark].mean() > out.mean(axis=2).mean()


def test_heif_develops_like_the_same_scene_shot_raw(negative_heif, pq_scene):
    """The decisive test: PQ is decoded as PQ, not as sRGB.

    A HEIF and a RAW carrying the same linear scene must develop to the same
    positive -- that is the whole claim. Reading the samples as sRGB instead
    still produces something that looks like a photograph, which is exactly why
    this compares against a linear reference rather than eyeballing it.

    Measured, on the density range a real negative occupies: PQ path 1.0% mean
    absolute error, sRGB misreading 3.1%. The 1.0% is the 10-bit quantisation
    floor -- encoding and decoding the scene with no file in between gives
    0.98% -- so the transfer contributes essentially none of it. The margin is
    only 3x here because the inversion normalises per frame and absorbs most of
    a wrong curve; on a scene with a 30x density range it is 31x.
    """
    from cefs.processing.film import invert_preview, invert_raw

    reference = invert_raw((pq_scene * 65535).astype(np.uint16), FilmParams(mode="color"))

    developed = cv2.imread(str(develop(negative_heif, FilmParams(mode="color"))),
                           cv2.IMREAD_UNCHANGED)[:, :, ::-1]
    pq_error = np.abs(developed.astype(float) - reference).mean() / 65535

    # What decoding the same file as 8-bit sRGB would have produced.
    import pillow_heif

    # .copy(), and the handle held in a local: an array from a HeifFile is a
    # view into libheif's buffer, and outliving it is an access violation.
    handle = pillow_heif.open_heif(str(negative_heif), convert_hdr_to_8bit=False)
    codes = np.asarray(handle).copy()
    as_srgb = invert_preview((codes >> 8).astype(np.uint8)[:, :, ::-1], FilmParams(mode="color"))
    srgb_error = np.abs(as_srgb[:, :, ::-1].astype(float) * 257 - reference).mean() / 65535

    assert pq_error < 0.015, f"PQ path disagrees with the linear reference: {pq_error:.3%}"
    assert srgb_error > pq_error * 2.5, (
        f"This test cannot tell the two decodings apart: PQ {pq_error:.3%} "
        f"vs sRGB {srgb_error:.3%}"
    )


def test_heif_needs_pillow_heif(negative_heif, monkeypatch):
    """A missing decoder must say so, not fail as an unreadable file."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "pillow_heif":
            raise ImportError("no pillow_heif")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(DevelopError, match="pillow-heif"):
        develop(negative_heif, FilmParams())


# --- the film base, which lives on a different scale in every format ---------


def test_a_preview_scale_base_cannot_reach_the_file(negative_heif):
    """The bug this guards: live view and a HEIF are not the same linear scale.

    A base sampled in the preview reads 0.5-0.9 (sRGB-linear, 1.0 is display
    white). The same rebate in a PQ HEIF reads about 0.065 (absolute, 1.0 is
    10000 cd/m^2). Carrying the number across collapsed a real .HIF's positive
    from 0.300 to 0.111 standard deviations. develop() must ignore it.
    """
    plain = cv2.imread(str(develop(negative_heif, FilmParams(mode="color"))),
                       cv2.IMREAD_UNCHANGED)
    with_preview_base = cv2.imread(
        str(develop(negative_heif, FilmParams(mode="color", base=(0.8, 0.75, 0.7)))),
        cv2.IMREAD_UNCHANGED,
    )
    assert np.array_equal(plain, with_preview_base)


def test_the_base_region_is_measured_from_the_file(negative_heif):
    """Pointing at a region must change the result, or it is being ignored."""
    auto = cv2.imread(str(develop(negative_heif, FilmParams(mode="color"))),
                      cv2.IMREAD_UNCHANGED)
    # A patch of the frame that is not the brightest, so its density differs
    # from the automatic 99th-percentile estimate.
    region = cv2.imread(
        str(develop(negative_heif, FilmParams(mode="color"),
                    base_region=(0.4, 0.4, 0.2, 0.2))),
        cv2.IMREAD_UNCHANGED,
    )
    assert not np.array_equal(auto, region)


def test_a_region_over_the_rebate_beats_the_automatic_guess(negative_heif, pq_scene):
    """Sampling the brightest area must land near the true base.

    The rebate is the brightest part of a negative, so a region over it should
    give a base close to the frame's maximum -- and much closer than a region
    over a dense part of the picture.
    """
    from cefs.processing.develop import _read_heif
    from cefs.processing.film import pq16_to_linear_lut, sample_base

    codes, _ = _read_heif(negative_heif)
    linear = pq16_to_linear_lut()[codes]

    brightest = float(np.percentile(linear, 99.9))
    y, x = np.unravel_index(np.argmax(linear.mean(axis=2)), linear.shape[:2])
    h, w = linear.shape[:2]
    on_the_bright_part = sample_base(
        linear, region=(max(0, x / w - 0.02), max(0, y / h - 0.02), 0.04, 0.04)
    )
    assert max(on_the_bright_part) > brightest * 0.8


# --- output options ----------------------------------------------------------


def test_lzw_is_smaller_and_lossless(negative_heif):
    """Task: a 16-bit positive is far too large uncompressed."""
    plain = develop(negative_heif, FilmParams(), output=OutputOptions(tiff_compression="none"))
    lzw = develop(negative_heif, FilmParams(), output=OutputOptions(tiff_compression="lzw"))
    assert lzw.stat().st_size < plain.stat().st_size
    assert np.array_equal(
        cv2.imread(str(plain), cv2.IMREAD_UNCHANGED),
        cv2.imread(str(lzw), cv2.IMREAD_UNCHANGED),
    )


def test_forcing_jpeg_gives_an_8_bit_file(negative_heif):
    out = develop(negative_heif, FilmParams(), output=OutputOptions(format="jpeg"))
    assert out.suffix == ".jpg"
    assert cv2.imread(str(out), cv2.IMREAD_UNCHANGED).dtype == np.uint8


def test_jpeg_quality_changes_the_file_size(negative_jpeg):
    low = develop(negative_jpeg, FilmParams(), output=OutputOptions(jpeg_quality=50))
    high = develop(negative_jpeg, FilmParams(), output=OutputOptions(jpeg_quality=100))
    assert high.stat().st_size > low.stat().st_size


@pytest.mark.parametrize(
    "options",
    [
        OutputOptions(format="webp"),
        OutputOptions(tiff_compression="jpeg"),
        OutputOptions(jpeg_quality=0),
        OutputOptions(jpeg_quality=101),
    ],
)
def test_bad_options_are_rejected_before_anything_is_decoded(tmp_path, options):
    """Fails while the control is still on screen, not mid-development."""
    with pytest.raises(DevelopError):
        develop(tmp_path / "does-not-exist.CR3", FilmParams(), output=options)


def test_rejects_unknown_format(tmp_path):
    odd = tmp_path / "notes.txt"
    odd.write_text("not an image")
    with pytest.raises(DevelopError):
        develop(odd, FilmParams())


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(DevelopError):
        develop(tmp_path / "nope.jpg", FilmParams())


def test_developable_reports_support(tmp_path):
    assert developable(tmp_path / "a.CR3")
    assert developable(tmp_path / "a.jpg")
    # CRAW is a compression mode inside the CR3 container, not a format of its
    # own, so LibRaw handles it with nothing extra here.
    assert developable(tmp_path / "a.CR2")
    assert developable(tmp_path / "a.HIF")
    assert developable(tmp_path / "a.heic")
    assert not developable(tmp_path / "a.txt")
