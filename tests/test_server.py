"""The web API, driven against the mock backend."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from support import read_image_bgr
import pytest
from fastapi.testclient import TestClient

from cefs.app.server import create_app
from cefs.config import Config
from cefs.processing.codec import decode_jpeg


@pytest.fixture
def config(tmp_path) -> Config:
    config = Config()
    config.camera.use_mock = True
    config.capture.output_dir = str(tmp_path)
    config.capture.settle_delay_s = 0.0
    return config


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as client:
        yield client


@pytest.fixture
def connected(client):
    client.post("/api/connect")
    yield client
    client.post("/api/disconnect")


def test_index_serves(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Canon EDSDK Film Scanner" in response.text


def test_status_before_connecting(client):
    body = client.get("/api/status").json()
    assert body["connected"] is False
    assert body["backend"] == "mock"


def test_connect_and_disconnect(client):
    body = client.post("/api/connect").json()
    assert body["connected"] is True
    assert body["model"]
    assert client.post("/api/disconnect").json()["connected"] is False


def test_connect_is_idempotent(connected):
    assert connected.post("/api/connect").json()["connected"] is True


def test_stream_requires_a_connection(client):
    assert client.get("/api/stream").status_code == 503


def test_frame_requires_a_connection(client):
    assert client.get("/api/frame").status_code == 503


def test_capture_requires_a_connection(client):
    assert client.post("/api/capture").status_code == 409


def test_frame_is_a_decodable_jpeg(connected):
    response = _frame_when_ready(connected)
    assert response.headers["content-type"] == "image/jpeg"
    assert decode_jpeg(response.content).shape[2] == 3


def test_view_updates_round_trip(connected):
    body = connected.post("/api/view", json={"invert": False, "zoom": 6.5}).json()
    assert body["invert"] is False
    assert body["zoom"] == 6.5
    assert connected.get("/api/status").json()["view"]["invert"] is False


def test_view_clamps_out_of_range_values(connected):
    body = connected.post(
        "/api/view", json={"zoom": 999.0, "center_x": -4.0, "center_y": 7.0}
    ).json()
    assert body["zoom"] == 16.0
    assert body["center_x"] == 0.0
    assert body["center_y"] == 1.0


def test_invert_actually_changes_the_frame(connected):
    connected.post("/api/view", json={"invert": False, "loupe": False})
    plain = decode_jpeg(_frame_when_ready(connected).content)
    connected.post("/api/view", json={"invert": True})
    inverted = decode_jpeg(_frame_when_ready(connected).content)
    # Inverting must move the mean to the far side of the midpoint, not merely
    # return a different-looking array.
    assert abs((255 - plain.mean()) - inverted.mean()) < 12


def captured(entry, index: int = 0) -> Path:
    """The capture's real path. Follow it rather than assuming the layout --
    the naming template decides which folder a frame lands in."""
    return Path(entry["files"][index]["path"])


def developed(entry, index: int = 0) -> Path:
    """The positive, which is written beside its capture."""
    return captured(entry, index).with_name(entry["files"][index]["positive"])


def test_capture_writes_a_file_and_lists_it(connected, tmp_path):
    entry = connected.post("/api/capture").json()
    assert captured(entry).exists()
    assert captured(entry).is_relative_to(tmp_path)
    assert connected.get("/api/status").json()["captures"][0]["name"] == entry["name"]


def test_capture_keeps_the_original(connected, tmp_path):
    """Captures are never overwritten or modified after the fact."""
    first = connected.post("/api/capture").json()
    original = captured(first).read_bytes()
    connected.post("/api/capture")
    assert captured(first).read_bytes() == original


# --- the roll -----------------------------------------------------------------


def test_captures_are_filed_into_the_roll(connected, tmp_path):
    client = connected
    client.post("/api/roll", json={"roll": "Roll014", "frame": 7})
    entry = client.post("/api/capture").json()
    path = captured(entry)
    assert path.exists()
    assert path.parent == tmp_path / "Roll014"
    assert path.stem == "Roll014_Frame07"
    assert entry["roll"] == "Roll014" and entry["frame"] == 7


def test_the_frame_advances_once_per_release(connected):
    """Not once per file. RAW+JPEG is two files of one photograph."""
    client = connected
    client.post("/api/roll", json={"roll": "Roll001", "frame": 1})
    frames = [client.post("/api/capture").json()["frame"] for _ in range(3)]
    assert frames == [1, 2, 3]
    assert client.get("/api/roll").json()["frame"] == 4


def test_two_files_from_one_release_share_a_frame(connected, tmp_path):
    """RAW+JPEG is two renderings of one photograph, so it is one frame.

    Advancing per file instead of per release would double every frame number
    on a roll and there would be no frame 2, 4, 6 at all. Nothing else in the
    suite catches it: the mock writes a single file unless asked otherwise, and
    the camera this was tested against is set to RAW only.
    """
    client = connected
    client.app.state.session._backend.dual_format = True
    client.post("/api/roll", json={"roll": "Roll014", "frame": 7})

    entry = client.post("/api/capture").json()
    assert entry["count"] == 2, entry
    assert entry["frame"] == 7

    names = sorted(Path(f["path"]).name for f in entry["files"])
    assert names == ["Roll014_Frame07.jpg", "Roll014_Frame07.png"]
    assert all(captured(entry, i).parent == tmp_path / "Roll014" for i in range(2))

    # The next release is the next frame, not the frame after next.
    assert client.post("/api/capture").json()["frame"] == 8

    body = json.loads((tmp_path / "Roll014" / "roll.json").read_text(encoding="utf-8"))
    assert [f["frame"] for f in body["frames"]] == [7, 8]
    assert sorted(body["frames"][0]["files"]) == names


def test_both_files_of_a_release_get_their_own_positive(connected, tmp_path):
    client = connected
    client.app.state.session._backend.dual_format = True
    client.post("/api/roll", json={"roll": "Roll015", "frame": 1})
    entry = client.post("/api/capture").json()
    assert all(f["positive"] and not f["error"] for f in entry["files"]), entry
    # Two positives, distinct files, both beside their captures.
    positives = {f["positive"] for f in entry["files"]}
    assert len(positives) == 2
    for name in positives:
        assert (tmp_path / "Roll015" / name).exists()


def test_the_positive_follows_its_capture_into_the_roll(connected, tmp_path):
    client = connected
    client.post("/api/roll", json={"roll": "Roll014", "frame": 3})
    entry = client.post("/api/capture").json()
    assert developed(entry).exists()
    assert developed(entry).parent == tmp_path / "Roll014"
    assert developed(entry).name.startswith("Roll014_Frame03")


def test_next_roll_increments_and_resets(connected):
    client = connected
    client.post("/api/roll", json={"roll": "Roll014", "frame": 12, "notes": "over-developed"})
    body = client.post("/api/roll/next", json={}).json()
    assert body["roll"] == "Roll015"
    assert body["frame"] == 1
    # The notes described the roll that just finished.
    assert body["notes"] == ""


def test_next_roll_keeps_the_padding(connected):
    client = connected
    client.post("/api/roll", json={"roll": "Roll009"})
    assert client.post("/api/roll/next", json={}).json()["roll"] == "Roll010"


def test_next_roll_handles_a_label_with_no_number(connected):
    client = connected
    client.post("/api/roll", json={"roll": "kitchen-sink"})
    assert client.post("/api/roll/next", json={}).json()["roll"] == "kitchen-sink-2"


def test_a_named_next_roll_is_used_as_given(connected):
    client = connected
    body = client.post("/api/roll/next", json={"roll": "Portra-2026-08"}).json()
    assert body["roll"] == "Portra-2026-08" and body["frame"] == 1


def test_the_sidecar_lands_beside_the_frames(connected, tmp_path):
    client = connected
    client.post("/api/roll", json={"roll": "Roll014", "frame": 1, "stock": "Portra 400",
                                   "developer": "C-41", "notes": "test roll"})
    client.post("/api/capture")
    client.post("/api/capture")
    path = tmp_path / "Roll014" / "roll.json"
    assert path.is_file()
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["stock"] == "Portra 400" and body["developer"] == "C-41"
    assert [f["frame"] for f in body["frames"]] == [1, 2]
    # Every file it names must actually be there.
    for recorded in body["frames"]:
        for name in recorded["files"] + recorded["positives"]:
            assert (path.parent / name).exists(), name


def test_the_sidecar_can_be_turned_off(connected, tmp_path):
    client = connected
    client.post("/api/roll", json={"roll": "Roll020", "sidecar": False})
    client.post("/api/capture")
    assert not (tmp_path / "Roll020" / "roll.json").exists()


def test_an_empty_template_keeps_the_camera_names(connected, tmp_path):
    client = connected
    client.post("/api/roll", json={"template": ""})
    entry = client.post("/api/capture").json()
    assert captured(entry).parent == tmp_path
    assert captured(entry).name.startswith("MOCK_")


def test_a_bad_template_is_refused_and_capture_still_works(connected, tmp_path):
    client = connected
    assert client.post("/api/roll", json={"template": "{nope}/{frame}"}).status_code == 422
    assert client.get("/api/roll").json()["template"] == "{roll}/{roll}_Frame{frame:02d}"
    assert captured(client.post("/api/capture").json()).exists()


def test_a_roll_label_cannot_escape_the_save_location(connected, tmp_path):
    client = connected
    client.post("/api/roll", json={"roll": "../../escaped"})
    entry = client.post("/api/capture").json()
    assert captured(entry).is_relative_to(tmp_path), captured(entry)


def test_the_status_carries_the_roll_and_an_example(client):
    roll = client.get("/api/status").json()["roll"]
    assert roll["roll"] and roll["frame"] >= 0
    assert roll["example"] and not roll["template_error"]


def test_refiring_a_frame_never_overwrites(connected):
    """Set the counter back and shoot again: the first file must survive."""
    client = connected
    client.post("/api/roll", json={"roll": "Roll030", "frame": 5})
    first = captured(client.post("/api/capture").json())
    original = first.read_bytes()
    client.post("/api/roll", json={"frame": 5})
    second = captured(client.post("/api/capture").json())
    assert second != first
    assert first.read_bytes() == original


# --- the film base across the preview/file boundary ---------------------------


def test_sampling_the_base_records_the_region(connected):
    _frame_when_ready(connected)
    body = connected.post("/api/film/base", json={"region": [0.1, 0.1, 0.2, 0.2]}).json()
    assert body["base"] is not None
    # The region is what reaches a developed file; the value cannot.
    assert body["base_region"] == [0.1, 0.1, 0.2, 0.2]


def test_resetting_the_base_actually_clears_it(connected):
    """`replace(base=None)` drops the None and keeps the old base, so this
    reset used to report success and change nothing."""
    _frame_when_ready(connected)
    connected.post("/api/film/base", json={"region": [0.1, 0.1, 0.2, 0.2]})
    body = connected.post("/api/film/base", json={}).json()
    assert body["base"] is None
    assert body["base_region"] is None


def test_the_sampled_region_reaches_the_saved_positive(connected, tmp_path):
    """The whole point of carrying the region instead of the value.

    The written positive must be what re-measuring that region in the captured
    file gives -- not what the preview's sampled number gives, and not the
    automatic estimate either.
    """

    from cefs.processing.develop import develop
    from cefs.processing.film import FilmParams

    region = [0.05, 0.05, 0.1, 0.1]
    _frame_when_ready(connected)
    connected.post("/api/film/base", json={"region": region})
    entry = connected.post("/api/capture").json()

    session = connected.app.state.session
    assert session._base_region == tuple(region)
    written = read_image_bgr(str(developed(entry)))

    source = captured(entry)
    expected = read_image_bgr(str(develop(source, session.film, output_dir=tmp_path, base_region=tuple(region))))
    assert np.array_equal(written, expected)

    # And the region is genuinely in play, not quietly dropped for the default.
    automatic = read_image_bgr(str(develop(source, FilmParams(mode=session.film.mode), output_dir=tmp_path)))
    assert not np.array_equal(written, automatic)


# --- capture settings --------------------------------------------------------


def test_capture_settings_are_in_the_status(client):
    capture = client.get("/api/status").json()["capture"]
    assert set(capture) >= {
        "settle_delay_s", "output_dir", "resolved_output_dir", "develop_positives",
        "positive_format", "tiff_compression", "jpeg_quality",
    }
    # The UI builds its controls from these, so an empty list would leave the
    # user with a section of dead buttons.
    assert capture["formats"] and capture["compressions"]


def test_settings_round_trip(client):
    body = client.post(
        "/api/capture/settings",
        json={"settle_delay_s": 2.5, "positive_format": "tiff", "jpeg_quality": 80},
    ).json()
    assert body["settle_delay_s"] == 2.5
    assert body["positive_format"] == "tiff"
    assert client.get("/api/capture/settings").json()["jpeg_quality"] == 80


def test_changing_the_save_location_actually_moves_the_files(connected, tmp_path):
    """A setting that reports success and changes nothing is the recurring bug."""
    elsewhere = tmp_path / "roll-2"
    client = connected
    assert client.post(
        "/api/capture/settings", json={"output_dir": str(elsewhere)}
    ).status_code == 200
    entry = client.post("/api/capture").json()
    assert captured(entry).exists()
    assert captured(entry).is_relative_to(elsewhere), captured(entry)


def test_a_new_save_location_is_created(client, tmp_path):
    fresh = tmp_path / "does" / "not" / "exist" / "yet"
    client.post("/api/capture/settings", json={"output_dir": str(fresh)})
    assert fresh.is_dir()


def test_settle_delay_reaches_a_connected_backend(connected):
    """Otherwise it takes effect only after a reconnect, silently."""
    session = connected.app.state.session
    connected.post("/api/capture/settings", json={"settle_delay_s": 0.25})
    assert session._backend.settle_delay_s == pytest.approx(0.25)


def test_develop_positives_can_be_turned_off(connected, tmp_path):
    connected.post("/api/capture/settings", json={"develop_positives": False})
    assert connected.post("/api/capture").json()["files"][0]["positive"] is None
    connected.post("/api/capture/settings", json={"develop_positives": True})
    entry = connected.post("/api/capture").json()
    assert entry["files"][0]["positive"]
    assert developed(entry).exists()


def test_the_chosen_format_is_the_format_written(connected, tmp_path):
    connected.post("/api/capture/settings", json={"positive_format": "tiff"})
    entry = connected.post("/api/capture").json()
    assert entry["files"][0]["positive"].endswith(".tif")


@pytest.mark.parametrize(
    "bad",
    [
        {"positive_format": "webp"},
        {"tiff_compression": "rle"},
        {"jpeg_quality": 0},
        {"jpeg_quality": 500},
        {"output_dir": "   "},
    ],
)
def test_bad_settings_are_refused_and_change_nothing(client, bad):
    before = client.get("/api/capture/settings").json()
    assert client.post("/api/capture/settings", json=bad).status_code == 422
    assert client.get("/api/capture/settings").json() == before


def test_settle_delay_is_clamped(client):
    assert client.post(
        "/api/capture/settings", json={"settle_delay_s": -5}
    ).json()["settle_delay_s"] == 0.0
    assert client.post(
        "/api/capture/settings", json={"settle_delay_s": 9999}
    ).json()["settle_delay_s"] == 60.0


def test_capabilities_are_reported(connected):
    caps = connected.get("/api/status").json()["capabilities"]
    assert set(caps) >= {"focus_drive", "liveview_zoom", "electronic_shutter", "notes"}
    # Anything reported unavailable must say why, or the UI hides a control
    # with no explanation.
    for name in ("liveview_zoom", "electronic_shutter"):
        if not caps[name]:
            assert caps["notes"].get(name)


def _frame_when_ready(client, timeout_s: float = 5.0):
    """Poll /api/frame until the backend has produced a frame.

    Time-bounded rather than attempt-bounded: request round trips are far
    quicker than the frame interval, so a fixed number of attempts can elapse
    before the first frame exists at all.
    """
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        response = client.get("/api/frame")
        if response.status_code == 200:
            return response
        time.sleep(0.02)
    raise AssertionError(f"No frame from /api/frame within {timeout_s} s.")
