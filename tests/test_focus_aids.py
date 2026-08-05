"""Focus aids: stepping, peaking overlay, sharpness readout."""

from __future__ import annotations

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from cefs.app.server import create_app
from cefs.app.session import FOCUS_STEPS, Session
from cefs.config import Config
from cefs.mock.frames import make_negative
from cefs.processing.codec import decode_jpeg, encode_jpeg
from cefs.processing.peaking import DEFAULT_PEAK_COLOR


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
        client.post("/api/connect")
        yield client
        client.post("/api/disconnect")


@pytest.fixture
def payload() -> bytes:
    return encode_jpeg(make_negative(width=320, height=213, color=False, seed=4))


# --- peaking overlay ---------------------------------------------------------


def test_peaking_marks_are_red_not_cyan(config, payload):
    """The overlay colour must survive inversion.

    Peaking is computed before the loupe (native resolution) but painted after
    inversion. Get the order wrong and every red mark inverts to cyan, which
    looks like a working feature until you notice the colour.
    """
    session = Session(config)
    session.view.peaking = True
    session.view.invert = True
    session.view.loupe = False
    out = decode_jpeg(session.process(payload))

    # DEFAULT_PEAK_COLOR is BGR red. Its inverse would be cyan (255, 255, 0).
    b, g, r = DEFAULT_PEAK_COLOR
    red_pixels = np.all(np.abs(out.astype(int) - [b, g, r]) < 40, axis=-1).sum()
    cyan_pixels = np.all(np.abs(out.astype(int) - [255, 255, 0]) < 40, axis=-1).sum()
    assert red_pixels > 0, "no peaking marks found at all"
    assert red_pixels > cyan_pixels


def test_peaking_changes_the_frame(config, payload):
    session = Session(config)
    session.view.invert = False
    session.view.loupe = False
    session.view.peaking = False
    plain = decode_jpeg(session.process(payload))
    session.view.peaking = True
    marked = decode_jpeg(session.process(payload))
    assert not np.array_equal(plain, marked)


def test_peaking_survives_the_loupe(config, payload):
    """Marks must still be present, and still red, when magnified."""
    session = Session(config)
    session.view.peaking = True
    session.view.invert = True
    session.view.loupe = True
    session.view.zoom = 4.0
    out = decode_jpeg(session.process(payload))
    b, g, r = DEFAULT_PEAK_COLOR
    assert np.all(np.abs(out.astype(int) - [b, g, r]) < 40, axis=-1).sum() > 0


def test_peaking_off_leaves_no_marks(config, payload):
    session = Session(config)
    session.view.peaking = False
    session.view.invert = False
    session.view.loupe = False
    out = decode_jpeg(session.process(payload))
    b, g, r = DEFAULT_PEAK_COLOR
    assert np.all(np.abs(out.astype(int) - [b, g, r]) < 10, axis=-1).sum() == 0


# --- focus stepping ----------------------------------------------------------


def test_fine_is_the_smallest_step_the_sdk_offers():
    """Fine must be one step of size 1 -- there is nothing finer to reach.

    This assertion used to be the opposite: every tier had to send more than one
    step, because a single step measured below the frame-noise floor. That
    reasoning came from a whole-frame difference metric, which turned out to be
    blind at the fine end -- everything from 1x1 to 2x8 measured the same. A
    person judging focus through an 8x loupe with a sharpness readout resolves
    far smaller moves than that average can, and asked for finer steps.
    """
    assert FOCUS_STEPS["fine"] == (1, 1)


def test_coarser_tiers_send_several_steps():
    """Medium and coarse must accumulate, or they would not differ from fine."""
    for name in ("medium", "coarse"):
        size, steps = FOCUS_STEPS[name]
        assert steps > 1, f"{name} sends only {steps} step"
        assert 1 <= size <= 3


def test_tiers_are_ordered_by_travel():
    """Each tier must move strictly further than the one below it."""
    travel = [size * steps for size, steps in
              (FOCUS_STEPS[k] for k in ("fine", "medium", "coarse"))]
    assert travel[0] < travel[1] < travel[2], travel


def test_focus_endpoint_drives(client):
    body = client.post("/api/focus", json={"direction": "near", "coarseness": "coarse"}).json()
    assert body["direction"] == "near"
    assert body["steps"] == FOCUS_STEPS["coarse"][1]


def test_focus_rejects_bad_direction(client):
    assert client.post("/api/focus", json={"direction": "up"}).status_code == 422


def test_focus_rejects_bad_coarseness(client):
    response = client.post("/api/focus", json={"direction": "near", "coarseness": "enormous"})
    assert response.status_code == 422


def test_focus_actually_changes_the_image(client):
    """Not just a 200 -- the picture has to change.

    The v0.0 spike got EDS_ERR_OK from a focus command that moved nothing, so
    a test asserting only on the status code would have passed while focus was
    doing nothing at all.
    """
    before = _sharpness_now(client)
    for _ in range(6):
        client.post("/api/focus", json={"direction": "near", "coarseness": "coarse"})

    # Wait for the change rather than sleeping a fixed time. The mock renders
    # on its own thread, so under load the new frame can take far longer than
    # any constant chosen here -- which made this test fail intermittently
    # while other work was running on the machine.
    after = _wait_for_change(client, before)
    assert after != pytest.approx(before, rel=0.05)


def test_focus_requires_a_connection(config):
    with TestClient(create_app(config)) as client:
        assert client.post("/api/focus", json={"direction": "near"}).status_code == 409


# --- sharpness readout -------------------------------------------------------


def test_sharpness_reports_now_and_best(client):
    body = _sharpness_body(client)
    assert body["sharpness"] >= 0.0
    assert body["best"] >= body["sharpness"] - 1e-9
    assert 0.0 <= body["fraction_of_best"] <= 1.0


def test_sharpness_best_is_sticky(client):
    """Best must not fall when focus worsens -- that is what makes it useful."""
    start = _sharpness_now(client)
    for _ in range(6):
        client.post("/api/focus", json={"direction": "near", "coarseness": "coarse"})
    _wait_for_change(client, start)
    best_after_sharp = _sharpness_body(client)["best"]

    moved = _sharpness_now(client)
    for _ in range(8):
        client.post("/api/focus", json={"direction": "far", "coarseness": "coarse"})
    _wait_for_change(client, moved)
    body = _sharpness_body(client)
    assert body["best"] >= best_after_sharp - 1e-9
    assert body["sharpness"] <= body["best"] + 1e-9


def test_sharpness_reset_clears_best(client):
    _sharpness_body(client)
    assert client.post("/api/sharpness/reset").json()["best"] == 0.0


def test_sharpness_requires_a_connection(config):
    with TestClient(create_app(config)) as client:
        assert client.get("/api/sharpness").status_code == 503


def test_sharpness_follows_the_loupe(client):
    """With the loupe up, the reading must describe the magnified region."""
    client.post("/api/view", json={"loupe": True, "zoom": 8.0, "center_x": 0.1, "center_y": 0.1})
    corner = _sharpness_now(client)
    client.post("/api/view", json={"center_x": 0.5, "center_y": 0.5})
    centre = _sharpness_now(client)
    # Different regions of a synthetic negative have different detail, so the
    # two readings should not be identical.
    assert corner != pytest.approx(centre, rel=1e-6)


# --- view state --------------------------------------------------------------


def test_peaking_sensitivity_round_trips(client):
    body = client.post("/api/view", json={"peaking": True, "peaking_sensitivity": 0.8}).json()
    assert body["peaking"] is True
    assert body["peaking_sensitivity"] == 0.8


def test_peaking_sensitivity_is_clamped(client):
    assert client.post("/api/view", json={"peaking_sensitivity": 5.0}).json()[
        "peaking_sensitivity"
    ] == 1.0
    assert client.post("/api/view", json={"peaking_sensitivity": -2.0}).json()[
        "peaking_sensitivity"
    ] == 0.0


# --- helpers -----------------------------------------------------------------


def _sharpness_body(client, timeout_s: float = 5.0) -> dict:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        response = client.get("/api/sharpness")
        if response.status_code == 200:
            return response.json()
        time.sleep(0.02)
    raise AssertionError("No sharpness reading within the time limit.")


def _sharpness_now(client) -> float:
    return _sharpness_body(client)["sharpness"]


def _wait_for_change(client, baseline: float, timeout_s: float = 8.0) -> float:
    """Poll until the reading moves away from ``baseline``, or time out.

    Returns the last reading either way, so the caller's assertion still
    reports the real numbers rather than a timeout message.
    """
    deadline = time.perf_counter() + timeout_s
    value = baseline
    while time.perf_counter() < deadline:
        value = _sharpness_now(client)
        if value != pytest.approx(baseline, rel=0.05):
            return value
        time.sleep(0.05)
    return value
