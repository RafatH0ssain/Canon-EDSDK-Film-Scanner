"""The main-thread SDK loop, which macOS forces on us.

None of this touches EDSDK -- it is the plumbing around it, and the plumbing
is where the deadlocks live.
"""

from __future__ import annotations

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
