"""Developing a captured negative into a positive file."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from cefs.processing.codec import encode_jpeg
from cefs.processing.develop import DevelopError, develop, developable, positive_path
from cefs.processing.film import FilmParams
from cefs.mock.frames import make_negative


@pytest.fixture
def negative_jpeg(tmp_path):
    path = tmp_path / "IMG_0001.jpg"
    path.write_bytes(encode_jpeg(make_negative(width=320, height=213, color=True, seed=3), 95))
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
    assert positive_path(tmp_path / "IMG_0001.jpg").suffix == ".jpg"


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
    assert not developable(tmp_path / "a.txt")
