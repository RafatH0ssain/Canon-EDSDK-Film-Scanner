"""Giving EDSDK the main thread, because on macOS it will not work anywhere else.

Windows is happy for any thread to own the SDK so long as it is a
single-threaded apartment pumping messages. macOS is not: **camera discovery
only works from the main thread**. Measured in one process, one camera, the
same moment -- ``EdsGetCameraList`` returns ``EDS_ERR_OK`` either way, and then
reports 1 camera on the main thread and 0 on any other. A worker thread also
hangs in ``EdsTerminateSDK``.

It is not a run-loop problem, which was the obvious guess: giving the worker a
running CFRunLoop changes nothing, and 3.6 million non-blocking spins return
immediately every time, so the SDK registers no sources on that thread at all.

So the architecture is unchanged in shape -- one thread owns the SDK, everyone
else submits a callable and waits -- and changes only in *which* thread. Here
that is the main one, which means the web server has to move off it. See
``cefs.app.server.main``.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: Whether this platform forces the SDK onto the main thread.
#:
#: Only macOS. Windows keeps its dedicated camera thread, which is well tested
#: and avoids handing the process's main thread to the camera for no reason.
REQUIRES_MAIN_THREAD = sys.platform == "darwin"

#: How long a caller waits for the main thread to pick its work up.
DEFAULT_TIMEOUT_S = 30.0


class MainThreadExecutor:
    """A command queue serviced by whichever thread calls :meth:`run_forever`.

    One instance, module-level, because there is only one main thread and only
    one SDK. Callers submit work and block; the loop runs it in order and, once
    a camera is attached, calls a per-iteration ``tick`` to dispatch SDK events
    and pull live-view frames.
    """

    def __init__(self) -> None:
        self._commands: queue.Queue[tuple[Callable[[], Any], str, Future]] = queue.Queue()
        self._tick: Callable[[], bool] | None = None
        self._stopping = threading.Event()
        self._running = threading.Event()
        self._owner_ident: int | None = None

    @property
    def running(self) -> bool:
        """Whether a thread is currently servicing the queue."""
        return self._running.is_set()

    def wait_until_running(self, timeout: float = 10.0) -> bool:
        """Block until the loop is servicing the queue. True if it is.

        Callers are started before :meth:`run_forever` gets going -- uvicorn in
        a thread, or the worker in :func:`run_with_sdk_loop` -- so without this
        the first command can lose a race it did not know it was in, and fail
        claiming the main thread is not running the SDK.
        """
        return self._running.wait(timeout=timeout)

    def set_tick(self, tick: Callable[[], bool] | None) -> None:
        """Set the per-iteration callable, or None to idle.

        It returns True when it did something worth not sleeping after -- a
        live-view frame -- so an idle loop does not spin a core.
        """
        self._tick = tick

    def submit(
        self, fn: Callable[[], Any], label: str, timeout: float = DEFAULT_TIMEOUT_S
    ) -> Any:
        """Run ``fn`` on the servicing thread and return its result.

        Called from the main thread itself -- from inside a command or a tick --
        it runs ``fn`` inline instead. Queueing there would wait on a loop that
        cannot reach the queue until the caller returns, which is a deadlock
        that presents as an unexplained 30-second stall.
        """
        if self._owner_ident is not None and threading.get_ident() == self._owner_ident:
            return fn()
        if not self._running.is_set():
            raise RuntimeError(
                f"Cannot run '{label}': the main-thread SDK loop is not running. "
                "Start the server through cefs.app.server.main, which reserves "
                "the main thread for it."
            )
        future: Future = Future()
        self._commands.put((fn, label, future))
        try:
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            raise TimeoutError(f"'{label}' timed out after {timeout:.0f} s.") from exc

    def run_forever(self) -> None:
        """Service the queue until :meth:`stop`. Call this on the main thread."""
        self._owner_ident = threading.get_ident()
        # Clear before announcing we are running, so a stop() can only arrive
        # after the clear. Defence in depth, not the fix: what actually stops
        # the hang is callers waiting via wait_until_running, and a mutation
        # test confirmed reordering these two alone changes nothing.
        self._stopping.clear()
        self._running.set()
        logger.info("Main-thread SDK loop running.")
        try:
            while not self._stopping.is_set():
                did_work = self._drain()
                tick = self._tick
                if tick is not None:
                    try:
                        did_work = tick() or did_work
                    except Exception:
                        # A failing tick must not kill the loop, or every later
                        # command blocks forever with no explanation.
                        logger.exception("Camera tick failed")
                if not did_work:
                    time.sleep(0.002)
        finally:
            self._running.clear()
            self._owner_ident = None
            self._fail_pending(RuntimeError("The main-thread SDK loop stopped."))
            logger.info("Main-thread SDK loop stopped.")

    def _drain(self) -> bool:
        ran = False
        while True:
            try:
                fn, _label, future = self._commands.get_nowait()
            except queue.Empty:
                return ran
            ran = True
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn())
            except BaseException as exc:  # returned to the caller, not swallowed
                future.set_exception(exc)

    def _fail_pending(self, error: BaseException) -> None:
        while True:
            try:
                _fn, _label, future = self._commands.get_nowait()
            except queue.Empty:
                return
            if not future.done():
                future.set_exception(error)

    def stop(self) -> None:
        """Ask :meth:`run_forever` to return."""
        self._stopping.set()


#: The one executor. Module-level for the same reason the SDK is a singleton.
EXECUTOR = MainThreadExecutor()


def run_with_sdk_loop(fn: Callable[[], Any]) -> Any:
    """Run ``fn`` with the main thread servicing the SDK, and return its result.

    For anything with work to do and no event loop of its own -- the CLI tools.
    ``fn`` runs on a worker while the main thread stays in
    :meth:`MainThreadExecutor.run_forever`, which is the only place macOS will
    let the SDK find a camera. Off macOS this simply calls ``fn``.
    """
    if not REQUIRES_MAIN_THREAD:
        return fn()

    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            if not EXECUTOR.wait_until_running():
                raise RuntimeError("The main-thread SDK loop never started.")
            box["value"] = fn()
        except BaseException as exc:  # re-raised on the caller's thread below
            box["error"] = exc
        finally:
            EXECUTOR.stop()

    thread = threading.Thread(target=worker, name="cefs-sdk-client", daemon=True)
    thread.start()
    EXECUTOR.run_forever()
    thread.join(timeout=10.0)

    if "error" in box:
        raise box["error"]
    return box.get("value")
