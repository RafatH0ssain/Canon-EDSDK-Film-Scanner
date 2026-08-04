"""The web API, driven against the mock backend."""

from __future__ import annotations

import time

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
