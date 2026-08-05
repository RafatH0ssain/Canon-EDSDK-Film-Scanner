"""Structured capture names, and the sidecar that says what the roll was.

A naming bug is quiet and expensive: you notice at the end of the roll, and by
then every frame is called the wrong thing or, worse, the same thing. So these
tests are mostly about the ways a template can be wrong rather than the way it
is right.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path, PurePosixPath

import pytest

from cefs import naming, sidecar

WHEN = datetime(2026, 8, 4, 21, 30, 15)


def render(template, **kwargs):
    kwargs.setdefault("roll", "Roll014")
    kwargs.setdefault("frame", 7)
    kwargs.setdefault("extension", ".CR3")
    kwargs.setdefault("when", WHEN)
    return str(naming.render(template, **kwargs))


# --- rendering ---------------------------------------------------------------


def test_the_default_template_matches_the_roadmap():
    assert render(naming.DEFAULT_TEMPLATE) == "Roll014/Roll014_Frame07.CR3"


def test_every_field_renders():
    # {roll} needs a varying field beside it; on its own it is refused, which
    # test_a_template_with_no_varying_field_is_refused covers.
    assert render("{roll}-{frame}") == "Roll014-7.CR3"
    assert render("{frame}") == "7.CR3"
    assert render("{frame:03d}") == "007.CR3"
    assert render("{original}", original="IMG_0001") == "IMG_0001.CR3"
    assert render("{date}-{frame}") == "2026-08-04-7.CR3"
    assert render("{time}") == "213015.CR3"
    assert render("{stock}_{frame}", stock="Portra 400") == "Portra 400_7.CR3"


def test_slashes_make_folders():
    assert render("{roll}/{date}/{frame:02d}") == "Roll014/2026-08-04/07.CR3"


def test_the_camera_extension_is_preserved_exactly():
    """The bytes are what the camera wrote, so the name should say so."""
    assert render("{frame}", extension=".CR3").endswith(".CR3")
    assert render("{frame}", extension=".HIF").endswith(".HIF")
    assert render("{frame}", extension=".jpg").endswith(".jpg")


def test_a_dot_in_the_roll_does_not_eat_the_frame_number():
    """`with_suffix` would read "_Frame07" as the extension and replace it,
    turning every frame of roll "R2.5" into the same file."""
    assert render("{roll}_Frame{frame:02d}", roll="R2.5") == "R2.5_Frame07.CR3"


# --- refusing to write outside the save location -----------------------------


@pytest.mark.parametrize(
    "template",
    ["/etc/{frame}", "//server/share/{frame}", "C:/Windows/{frame}", "{roll}/../../{frame}"],
)
def test_absolute_and_escaping_templates_are_refused(template):
    with pytest.raises(naming.NamingError):
        naming.validate_template(template)


@pytest.mark.parametrize("label", ["../../etc", "..", "/", "C:", "a/b/c", "\\\\server\\share"])
def test_a_roll_label_cannot_climb_out(label):
    """The label is typed into a web form and becomes a path component."""
    rendered = PurePosixPath(render("{roll}/{frame:02d}", roll=label))
    assert ".." not in rendered.parts
    assert not rendered.is_absolute()
    assert len(rendered.parts) == 2


def test_reserved_windows_names_are_defused():
    """NUL.CR3 cannot be opened or deleted on Windows, and says nothing useful
    when it fails."""
    assert naming.sanitise("NUL") != "NUL"
    assert naming.sanitise("con") != "con"


def test_a_label_with_nothing_usable_left_is_an_error():
    assert naming.sanitise("///") == ""
    assert naming.sanitise("///", fallback="Roll") == "Roll"


# --- templates that would collide --------------------------------------------


def test_a_template_with_no_varying_field_is_refused():
    """Otherwise every frame of the roll renders to one name, and you get
    Roll014-1, Roll014-2 with no idea which frame is which."""
    with pytest.raises(naming.NamingError, match="at least one of"):
        naming.validate_template("{roll}/{roll}")
    with pytest.raises(naming.NamingError, match="at least one of"):
        naming.validate_template("{stock}-{date}")


@pytest.mark.parametrize("varying", ["{frame}", "{original}", "{time}"])
def test_any_varying_field_is_enough(varying):
    naming.validate_template("{roll}/" + varying)


def test_unknown_fields_are_refused_with_the_valid_list():
    with pytest.raises(naming.NamingError, match="Valid fields"):
        naming.validate_template("{rol}/{frame}")


def test_attribute_access_is_refused():
    with pytest.raises(naming.NamingError):
        naming.validate_template("{frame.__class__}/{frame}")


def test_a_bad_format_spec_is_caught_at_validation():
    """Not at the shutter, with a frame already exposed."""
    with pytest.raises(naming.NamingError):
        naming.validate_template("{frame:02q}")


def test_empty_template_is_refused():
    with pytest.raises(naming.NamingError):
        naming.validate_template("   ")


def test_a_blank_field_does_not_leave_an_empty_folder():
    """{stock} is empty until it is recorded, and "//Roll014" is not a path."""
    with pytest.raises(naming.NamingError):
        naming.render("{stock}/{frame}", roll="R", frame=1, extension=".CR3", stock="")


# --- never overwriting -------------------------------------------------------


def test_unique_steps_aside(tmp_path):
    first = tmp_path / "Roll014_Frame07.CR3"
    assert naming.unique(first) == first
    first.write_bytes(b"x")
    second = naming.unique(first)
    assert second != first and not second.exists()
    assert second.name == "Roll014_Frame07-1.CR3"


def test_example_shows_what_the_template_does():
    assert naming.example(naming.DEFAULT_TEMPLATE) == "Roll014/Roll014_Frame07.CR3"


# --- the sidecar -------------------------------------------------------------


def test_sidecar_records_a_frame(tmp_path):
    path = tmp_path / sidecar.SIDECAR_NAME
    meta = sidecar.RollMetadata(roll="Roll014", stock="HP5", developer="DD-X")
    assert sidecar.record_capture(path, meta, 7, ["Roll014_Frame07.CR3"])
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["roll"] == "Roll014" and body["stock"] == "HP5"
    assert body["frames"][0]["frame"] == 7
    assert body["frames"][0]["files"] == ["Roll014_Frame07.CR3"]


def test_sidecar_accumulates_frames_rather_than_replacing(tmp_path):
    """A restarted server mid-roll must not truncate the log."""
    path = tmp_path / sidecar.SIDECAR_NAME
    meta = sidecar.RollMetadata(roll="Roll014")
    for frame in (1, 2, 3):
        sidecar.record_capture(path, meta, frame, [f"f{frame}.CR3"])
    body = json.loads(path.read_text(encoding="utf-8"))
    assert [f["frame"] for f in body["frames"]] == [1, 2, 3]


def test_reshooting_a_frame_replaces_its_entry(tmp_path):
    path = tmp_path / sidecar.SIDECAR_NAME
    meta = sidecar.RollMetadata(roll="Roll014")
    sidecar.record_capture(path, meta, 7, ["first.CR3"])
    sidecar.record_capture(path, meta, 7, ["second.CR3"])
    body = json.loads(path.read_text(encoding="utf-8"))
    assert len(body["frames"]) == 1
    assert body["frames"][0]["files"] == ["second.CR3"]


def test_metadata_typed_later_lands_on_earlier_frames(tmp_path):
    """You often only fill the stock in once the roll is under way."""
    path = tmp_path / sidecar.SIDECAR_NAME
    sidecar.record_capture(path, sidecar.RollMetadata(roll="Roll014"), 1, ["a.CR3"])
    sidecar.record_capture(
        path, sidecar.RollMetadata(roll="Roll014", stock="Portra 400"), 2, ["b.CR3"]
    )
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["stock"] == "Portra 400"
    assert len(body["frames"]) == 2


def test_an_unreadable_sidecar_is_left_alone(tmp_path):
    """Better a stale file a human can salvage than a fresh one that lost it."""
    path = tmp_path / sidecar.SIDECAR_NAME
    path.write_text("{ this is not json", encoding="utf-8")
    assert not sidecar.record_capture(path, sidecar.RollMetadata(), 1, ["a.CR3"])
    assert path.read_text(encoding="utf-8") == "{ this is not json"


def test_sidecar_failure_is_reported_not_raised(tmp_path):
    """It is metadata about scans; it must never take a capture down with it."""
    blocked = tmp_path / "a-file"
    blocked.write_text("x", encoding="utf-8")
    assert not sidecar.record_capture(
        blocked / "nested" / sidecar.SIDECAR_NAME, sidecar.RollMetadata(), 1, ["a.CR3"]
    )
