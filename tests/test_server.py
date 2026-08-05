"""The web API, driven against the mock backend."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
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


def test_capture_writes_a_file_and_lists_it(connected, tmp_path):
    entry = connected.post("/api/capture").json()
    assert (tmp_path / entry["name"]).exists()
    assert connected.get("/api/status").json()["captures"][0]["name"] == entry["name"]


def test_capture_keeps_the_original(connected, tmp_path):
    """Captures are never overwritten or modified after the fact."""
    first = connected.post("/api/capture").json()
    original = (tmp_path / first["name"]).read_bytes()
    connected.post("/api/capture")
    assert (tmp_path / first["name"]).read_bytes() == original


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
    import cv2

    from cefs.processing.develop import develop
    from cefs.processing.film import FilmParams

    region = [0.05, 0.05, 0.1, 0.1]
    _frame_when_ready(connected)
    connected.post("/api/film/base", json={"region": region})
    entry = connected.post("/api/capture").json()

    session = connected.app.state.session
    assert session._base_region == tuple(region)
    written = cv2.imread(str(tmp_path / entry["files"][0]["positive"]), cv2.IMREAD_UNCHANGED)

    source = tmp_path / entry["files"][0]["name"]
    expected = cv2.imread(
        str(develop(source, session.film, output_dir=tmp_path, base_region=tuple(region))),
        cv2.IMREAD_UNCHANGED,
    )
    assert np.array_equal(written, expected)

    # And the region is genuinely in play, not quietly dropped for the default.
    automatic = cv2.imread(
        str(develop(source, FilmParams(mode=session.film.mode), output_dir=tmp_path)),
        cv2.IMREAD_UNCHANGED,
    )
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
    assert (elsewhere / entry["name"]).exists()
    assert Path(entry["files"][0]["path"]).parent == elsewhere


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
    assert (tmp_path / entry["files"][0]["positive"]).exists()


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
