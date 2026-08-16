"""The main-thread SDK loop, which macOS forces on us.

None of this touches EDSDK -- it is the plumbing around it, and the plumbing
is where the deadlocks live.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

from cefs.edsdk.mainthread import (
    EXECUTOR,
    REQUIRES_MAIN_THREAD,
    MainThreadExecutor,
    run_with_sdk_loop,
)


def _run_loop(ex: MainThreadExecutor) -> threading.Thread:
    t = threading.Thread(target=ex.run_forever, daemon=True)
    t.start()
    assert ex.wait_until_running(timeout=5.0), "loop did not start"
    return t


def test_submit_runs_on_the_servicing_thread():
    ex = MainThreadExecutor()
    t = _run_loop(ex)
    try:
        where = ex.submit(threading.get_ident, "ident")
        assert where == t.ident, "work must run on the loop's thread, not the caller's"
    finally:
        ex.stop()
        t.join(timeout=5.0)


def test_submit_from_the_loop_itself_does_not_deadlock():
    """Re-entrant submit must run inline.

    Queueing would wait on a loop that cannot reach the queue until the caller
    returns -- a deadlock that shows up only as an unexplained stall.
    """
    ex = MainThreadExecutor()
    t = _run_loop(ex)
    try:
        outer = ex.submit(lambda: ex.submit(lambda: "inner", "inner"), "outer")
        assert outer == "inner"
    finally:
        ex.stop()
        t.join(timeout=5.0)


def test_exceptions_travel_back_to_the_caller():
    ex = MainThreadExecutor()
    t = _run_loop(ex)
    try:
        def boom():
            raise ValueError("from the loop")

        with pytest.raises(ValueError, match="from the loop"):
            ex.submit(boom, "boom")
    finally:
        ex.stop()
        t.join(timeout=5.0)


def test_stop_makes_the_loop_return():
    ex = MainThreadExecutor()
    t = _run_loop(ex)
    ex.stop()
    t.join(timeout=5.0)
    assert not t.is_alive(), "the loop ignored stop()"


def test_run_with_sdk_loop_returns_its_result_and_terminates():
    """Regression for a hang, so it is run enough times to lose the race.

    ``run_with_sdk_loop`` starts its worker *before* the main thread reaches
    the loop. A worker that does not wait reaches the SDK first, fails because
    nothing is servicing the queue, and calls stop() on its way out -- which
    ``run_forever`` then cleared on entry, leaving it spinning over an empty
    queue with no worker left. No error was raised anywhere; it simply hung.

    One iteration would win the race most times and prove nothing.
    """
    # Bounded: the failure mode is a hang, and a hanging test is barely better
    # than no test. Run it on a thread and assert it finished, so the bug shows
    # up as a failure with a message instead of a stalled suite.
    # Only macOS runs a loop at all. Everywhere else run_with_sdk_loop is a
    # passthrough and there is no queue to submit to -- which is the point of
    # the platform check, and what this test got wrong until CI ran it on
    # Linux and Windows.
    if REQUIRES_MAIN_THREAD:
        def work() -> int:
            return EXECUTOR.submit(lambda: 42, "answer")
    else:
        def work() -> int:
            return 42

    box: dict[str, object] = {}

    def attempt() -> None:
        try:
            for _ in range(25):
                box["result"] = run_with_sdk_loop(work)
        except BaseException as exc:
            box["error"] = exc
        finally:
            box["done"] = True

    t = threading.Thread(target=attempt, daemon=True)
    t.start()
    t.join(timeout=30.0)

    assert box.get("done"), (
        "run_with_sdk_loop hung: the worker raced ahead of the loop, failed, "
        "and its stop() was cleared on entry"
    )
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    assert box["result"] == 42


def test_run_with_sdk_loop_propagates_failure():
    def boom():
        raise ValueError("inside the loop")

    with pytest.raises(ValueError, match="inside the loop"):
        run_with_sdk_loop(boom)


def test_wait_until_running_is_false_when_nothing_services_the_queue():
    ex = MainThreadExecutor()
    assert ex.wait_until_running(timeout=0.05) is False


def test_submit_without_a_loop_refuses_rather_than_blocking():
    ex = MainThreadExecutor()
    started = time.perf_counter()
    with pytest.raises(RuntimeError, match="not running"):
        ex.submit(lambda: None, "work")
    assert time.perf_counter() - started < 1.0, "must fail fast, not wait out a timeout"


def test_off_macos_the_helper_is_a_passthrough():
    """Windows and Linux keep their own threading; there is no loop to start.

    Asserting this stops the macOS-shaped assumption creeping back in.
    """
    marker = object()
    assert run_with_sdk_loop(lambda: marker) is marker
    if not REQUIRES_MAIN_THREAD:
        assert not EXECUTOR.running, "no loop should be left running off macOS"


# --- a failed connect must not strand the SDK --------------------------------


class _FakeDll:
    """Enough of EDSDK to walk ``_on_thread_setup``, refusing the session.

    ``EdsInitializeSDK`` refuses a second call the way the real SDK does, so a
    test that forgets to terminate fails on the *symptom* the user reported
    rather than on a bookkeeping assertion.
    """

    INTERNAL_ERROR = 0x02
    COMM_PORT_IS_IN_USE = 0xC0

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.live = False

    def EdsInitializeSDK(self) -> int:
        self.calls.append("init")
        if self.live:
            return self.INTERNAL_ERROR
        self.live = True
        return 0

    def EdsTerminateSDK(self) -> int:
        self.calls.append("terminate")
        self.live = False
        return 0

    def EdsGetCameraList(self, out) -> int:
        return 0

    def EdsGetChildCount(self, _list, out) -> int:
        out._obj.value = 1
        return 0

    def EdsGetChildAtIndex(self, _list, _index, out) -> int:
        return 0

    def EdsOpenSession(self, _camera) -> int:
        self.calls.append("open")
        return self.COMM_PORT_IS_IN_USE

    def EdsCloseSession(self, _camera) -> int:
        return 0

    def EdsRelease(self, _handle) -> int:
        return 0


def test_a_refused_session_releases_the_sdk_for_the_next_attempt(monkeypatch):
    """A failed connect must call EdsTerminateSDK before it gives up.

    EdsInitializeSDK succeeds long before EdsOpenSession is tried. On the
    main-thread path, ``start()`` used to let the setup exception escape before
    ``_started = True``, and ``stop()`` returns early while ``_started`` is
    False -- so nothing ever terminated the SDK. Every later connect then died
    on EdsInitializeSDK returning INTERNAL_ERROR, for the life of the process:
    one transient COMM_PORT_IS_IN_USE meant restarting the app.

    Found on an EOS R7, 2026-08-16, while sweeping the UI redesign for
    regressions.
    """
    from cefs.edsdk import bindings as bindings_mod
    from cefs.edsdk import camera as camera_mod
    from cefs.edsdk.camera import EdsdkCamera

    dll = _FakeDll()
    # This is a claim about the macOS path, so say so rather than inheriting
    # whatever the runner happens to be. ``start()`` refuses outright anywhere
    # that is not Windows or macOS, and on Linux CI that guard fired before the
    # SDK was ever touched -- leaving the test asserting nothing at all.
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(camera_mod, "REQUIRES_MAIN_THREAD", True)
    monkeypatch.setattr(bindings_mod, "load_edsdk", lambda *a, **k: dll)

    executor = camera_mod.EXECUTOR
    thread = _run_loop(executor)
    try:
        cam = EdsdkCamera(library_dir="unused")

        with pytest.raises(Exception) as first:
            cam.start()
        # Fail loudly if the connect died before reaching the SDK: without this
        # any new early guard in start() would make everything below vacuous,
        # which is how the Linux failure hid.
        assert "EdsOpenSession" in str(first.value), (
            f"start() failed before it reached the SDK, so this test proves "
            f"nothing: {first.value}"
        )
        assert dll.calls == ["init", "open", "terminate"], (
            f"a failed connect must initialise, try the session, then release "
            f"the SDK -- got {dll.calls}"
        )
        assert not dll.live

        # The point of terminating: a retry gets a clean SDK rather than
        # INTERNAL_ERROR. It still fails, but on the real cause.
        with pytest.raises(Exception) as second:
            cam.start()
        assert "EdsInitializeSDK" not in str(second.value), (
            f"retry died on a stranded SDK rather than the real fault: {second.value}"
        )
        assert dll.calls.count("open") == 2, "the retry must reach EdsOpenSession again"
    finally:
        executor.stop()
        thread.join(timeout=5.0)
