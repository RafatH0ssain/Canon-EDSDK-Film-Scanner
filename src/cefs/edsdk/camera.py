"""The camera thread: one thread owns EDSDK, everything else asks it to work.

On Windows EDSDK delivers events through COM, so the thread that called
``EdsInitializeSDK`` must sit in a single-threaded apartment with a running
Windows message pump. That decides the shape of the whole application: every
SDK call happens here, everything else submits a callable and waits, and the
thread must keep pumping or events -- including capture-complete -- never
arrive. Getting it wrong gives hangs and missing events, not clean errors.

macOS has no message queue, so ``pump_messages`` calls ``EdsGetEvent`` and
spins this thread's CFRunLoop instead. The shape is unchanged; only the
dispatch differs. That path is written but **not yet confirmed on a camera**.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
import time
from concurrent.futures import Future
from ctypes import byref, c_char, c_uint32, c_uint64, c_void_p

if sys.platform == "win32":
    # Not pulled in by `import ctypes`. Without it the pump raises
    # AttributeError on first call and the thread dies silently -- which
    # presents as "connected, no frames".
    from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

from cefs.backend import Capabilities, CameraError, CameraInfo
from cefs.edsdk import bindings as b
from cefs.edsdk.errors import (
    EDS_ERR_DEVICE_BUSY,
    EDS_ERR_OBJECT_NOTREADY,
    EDS_ERR_OK,
    EDS_ERR_PTP_DEVICE_BUSY,
    EdsError,
    check,
    error_name,
)

logger = logging.getLogger(__name__)

COINIT_APARTMENTTHREADED = 0x2
PM_REMOVE = 0x0001

#: How long a queued command may wait before the caller gives up. Generous:
#: a capture includes the settle delay plus a RAW download.
DEFAULT_COMMAND_TIMEOUT_S = 120.0

#: Pause between individual focus steps within one request.
#:
#: A keypress sends several steps, so this multiplies straight into how long the
#: control feels. Measured on an EOS R7 with an RF85mm, 10 steps of size 3:
#:
#: ===========  ============  =======
#: pause        wall / press  travel
#: ===========  ============  =======
#: 50 ms        0.81 s        27.5
#: 0 ms         **0.24 s**    21.1
#: ===========  ============  =======
#:
#: Zero is 3.4x more responsive and still moves the lens well clear of the
#: frame noise. It is not free: travel per step drops about 23%, so the body
#: does partly absorb steps sent back to back. The step counts in
#: ``cefs.app.session.FOCUS_STEPS`` are calibrated with this pause, so changing
#: one means re-measuring the other.
FOCUS_STEP_PAUSE_S = 0.0

#: How long to keep listening for further files after the first one arrives.
#:
#: A RAW+JPEG release produces two transfer events, and the second lands a
#: moment after the first. Returning as soon as one arrives silently keeps half
#: the shot.
_EXTRA_TRANSFER_GRACE_S = 1.5

#: How long to keep retrying a call the body rejects as busy.
#:
#: DEVICE_BUSY is routinely transient rather than fatal: with live view running
#: the camera is mid-transfer for much of each frame, and a property write
#: landing in that window is refused even though nothing is wrong. Canon's own
#: sample code retries on this code for the same reason.
_BUSY_RETRY_S = 4.0


#: CFRunLoopRunInMode's "I handled something, there may be more" result.
#: The others (1 Finished, 2 Stopped, 3 TimedOut) all mean the queue is empty.
_CF_RUN_LOOP_HANDLED_SOURCE = 4


def _load_core_foundation() -> tuple[Any, Any] | None:
    """CoreFoundation and kCFRunLoopDefaultMode, or None if unavailable."""
    if sys.platform != "darwin":
        return None
    try:
        cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        # CFRunLoopRunInMode(CFStringRef mode, CFTimeInterval seconds,
        #                    Boolean returnAfterSourceHandled) -> SInt32
        cf.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_bool]
        cf.CFRunLoopRunInMode.restype = ctypes.c_int32
        return cf, ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")
    except (OSError, ValueError) as exc:  # pragma: no cover - macOS always has it
        logger.warning("CoreFoundation unavailable, EDSDK events may not arrive: %s", exc)
        return None


#: Resolved once. None off macOS, and off a Mac that somehow lacks CoreFoundation.
_CORE_FOUNDATION = _load_core_foundation()


def pump_messages(dll: Any = None) -> None:
    """Dispatch any EDSDK events waiting for this thread.

    Nothing pumps for us, and an undispatched event never fires -- the symptom
    is a capture that hangs forever rather than an error, so every wait loop in
    this file calls this.

    **Windows** drains the COM message queue: EDSDK posts its callbacks there.

    **macOS** has no message queue, so two things stand in for it, because the
    SDK may use either and both are cheap:

    - ``EdsGetEvent``, which Canon documents as the call a console application
      makes regularly to collect events from the camera. This is the mechanism
      that matters; it is fetched by name rather than assumed -- see
      ``bindings._declare``.
    - a non-blocking spin of this thread's CFRunLoop, for anything the SDK
      registered as a run-loop source. Measured at 0.0011 ms per call when
      idle, against a 17 ms frame budget.

    Args:
        dll: The loaded EDSDK library, needed for ``EdsGetEvent`` off Windows.
            Optional so Windows callers need not thread it through.
    """
    if sys.platform == "win32":
        msg = wintypes.MSG()
        user32 = ctypes.windll.user32
        while user32.PeekMessageW(byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))
        return

    if dll is not None:
        get_event = getattr(dll, "EdsGetEvent", None)
        if get_event is not None:
            get_event()

    if _CORE_FOUNDATION is not None:
        cf, mode = _CORE_FOUNDATION
        # Drain, do not block: seconds=0 returns immediately, and the loop
        # continues only while a source actually fired. This mirrors the
        # PeekMessage drain above rather than parking the camera thread.
        for _ in range(64):
            if cf.CFRunLoopRunInMode(mode, 0.0, True) != _CF_RUN_LOOP_HANDLED_SOURCE:
                return


class _Command:
    """One unit of work for the camera thread, with somewhere to put the result."""

    __slots__ = ("fn", "future", "label")

    def __init__(self, fn: Callable[[], Any], label: str) -> None:
        self.fn = fn
        self.label = label
        self.future: Future = Future()


class EdsdkCamera:
    """Camera backend driving a real body over EDSDK/USB.

    Public methods are safe from any thread; they enqueue and wait. Anything
    prefixed ``_on_thread`` runs on the camera thread only.
    """

    def __init__(
        self,
        library_dir: str | Path,
        library_path: str | Path | None = None,
        target_fps: int = 30,
        settle_delay_s: float = 1.5,
        delete_after_download: bool = False,
    ) -> None:
        self._library_dir = library_dir
        self._library_path = library_path
        self._frame_interval = 1.0 / max(target_fps, 1)
        self._settle_delay_s = settle_delay_s
        self._delete_after_download = delete_after_download

        self._commands: queue.Queue[_Command] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._thread_error: BaseException | None = None

        self._frame_lock = threading.Lock()
        self._frame: bytes | None = None
        self._frame_seq = 0

        # SDK state, touched only on the camera thread.
        self._dll: Any = None
        self._camera: c_void_p | None = None
        self._streaming = False
        self._info = CameraInfo(model="(not connected)", backend="edsdk")
        self._capabilities = Capabilities()

        # Load-bearing: if Python collects the trampoline, the SDK calls freed
        # memory and the process dies with no traceback.
        self._object_handler: Any = None
        self._pending_transfers: list[int] = []

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start the camera thread, connect, and begin live view."""
        # Refuse where there is no event dispatch at all, rather than part-way
        # through a capture. Everything up to the shutter would appear to work
        # -- the library loads, the session opens, live view streams, because
        # frames are polled rather than delivered. Only the callbacks would be
        # missing, and the first thing needing one is the capture-complete that
        # never arrives: 60 s of waiting, then an error blaming the memory card.
        if sys.platform not in ("win32", "darwin"):
            raise CameraError(
                f"Driving a real camera over EDSDK is not supported on "
                f"{sys.platform} yet -- Windows and macOS are.\n"
                "  EDSDK's events would never be dispatched here, so no capture\n"
                "  would ever complete.\n"
                "  Set camera.use_mock: true to run everything except the camera."
            )
        if sys.platform == "darwin":
            # Say this out loud every time until a real body has confirmed it.
            # The design now matches what Canon documents -- a console
            # application polls EdsGetEvent -- so the earlier worry about
            # needing the main thread's run loop does not apply. That is a
            # reason for confidence, not evidence: nothing here has met a
            # camera, and live view is polled, so it working proves nothing
            # about whether events arrive.
            logger.warning(
                "macOS EDSDK support has not been verified against a camera. "
                "If a capture times out, event dispatch is the suspect -- "
                "see pump_messages()."
            )
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping.clear()
        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(target=self._run, name="cefs-camera", daemon=True)
        self._thread.start()

        # Surface failures here, not as an app that looks fine until no frame
        # ever arrives.
        if not self._ready.wait(timeout=30.0):
            self._stopping.set()
            raise CameraError("Camera thread did not become ready within 30 s.")
        if self._startup_error is not None:
            raise self._startup_error

    def stop(self) -> None:
        """Stop live view, close the session, and join the thread."""
        self._stopping.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=15.0)
            if thread.is_alive():
                logger.error("Camera thread did not stop; the message pump may have stalled.")
        self._thread = None

    @property
    def info(self) -> CameraInfo:
        return self._info

    @property
    def capabilities(self) -> Capabilities:
        return self._capabilities

    @property
    def settle_delay_s(self) -> float:
        return self._settle_delay_s

    @settle_delay_s.setter
    def settle_delay_s(self, seconds: float) -> None:
        """Safe from any thread: one float, read once at the top of a capture.
        A capture in flight keeps the value it started with."""
        self._settle_delay_s = max(0.0, float(seconds))

    # --- public operations (any thread) -------------------------------------

    def latest_frame(self) -> bytes | None:
        """Newest live-view frame, or None. Does not touch the SDK."""
        if self._thread_error is not None:
            raise CameraError(f"Camera thread stopped: {self._thread_error}")
        with self._frame_lock:
            return self._frame

    def _fail_pending(self, error: BaseException | None) -> None:
        """Complete queued commands, so no caller waits on a dead thread."""
        failure = error or CameraError("Camera thread stopped.")
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            if not command.future.done():
                command.future.set_exception(failure)

    def drive_focus(self, direction: str, steps: int = 1, size: int = 2) -> None:
        if direction not in ("near", "far"):
            raise ValueError(f"direction must be 'near' or 'far', got {direction!r}")
        if not self._capabilities.focus_drive:
            raise CameraError(
                "This lens cannot be driven from the computer. "
                f"{self._capabilities.note_for('focus_drive')}"
            )
        self._submit(lambda: self._on_thread_drive_focus(direction, steps, size), "drive_focus")

    def capture(self, destination_dir: Path) -> list[Path]:
        """Fire the shutter and download every file it produced."""
        return self._submit(
            lambda: self._on_thread_capture(Path(destination_dir)),
            "capture",
            timeout=DEFAULT_COMMAND_TIMEOUT_S,
        )

    def _submit(
        self, fn: Callable[[], Any], label: str, timeout: float = 30.0
    ) -> Any:
        """Queue work for the camera thread and wait for its result."""
        if self._thread is None or not self._thread.is_alive():
            raise CameraError("Camera is not connected.")
        command = _Command(fn, label)
        self._commands.put(command)
        try:
            return command.future.result(timeout=timeout)
        except TimeoutError as exc:
            raise CameraError(f"Camera command '{label}' timed out after {timeout:.0f} s.") from exc

    # --- the thread ---------------------------------------------------------

    def _run(self) -> None:
        try:
            self._on_thread_setup()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            self._on_thread_teardown()
            return

        self._ready.set()
        try:
            self._on_thread_loop()
        except BaseException as exc:
            # Losing the loop means no frames and no commands ever complete.
            # Record it so callers get the real cause instead of a silent stall.
            self._thread_error = exc
            logger.exception("Camera thread failed")
        finally:
            self._on_thread_teardown()
            self._fail_pending(self._thread_error)

    def _on_thread_setup(self) -> None:
        if sys.platform == "win32":
            hr = ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            if hr < 0:
                raise CameraError(f"CoInitializeEx failed: 0x{hr & 0xFFFFFFFF:08X}")

        self._dll = b.load_edsdk(self._library_dir, self._library_path)
        check("EdsInitializeSDK", self._dll.EdsInitializeSDK())

        camera_list = c_void_p()
        check("EdsGetCameraList", self._dll.EdsGetCameraList(byref(camera_list)))
        try:
            count = c_uint32()
            check("EdsGetChildCount", self._dll.EdsGetChildCount(camera_list, byref(count)))
            if count.value == 0:
                raise CameraError(
                    "No camera found.\n"
                    "  Check the USB cable is a data cable, the camera is on and awake,\n"
                    "  and that no other program (EOS Utility, Lightroom) holds it."
                )
            camera = c_void_p()
            check(
                "EdsGetChildAtIndex",
                self._dll.EdsGetChildAtIndex(camera_list, 0, byref(camera)),
            )
            self._camera = camera
        finally:
            self._dll.EdsRelease(camera_list)

        check("EdsOpenSession", self._dll.EdsOpenSession(self._camera))
        self._info = CameraInfo(
            model=self._read_string(b.kEdsPropID_ProductName) or "(unknown)",
            lens=self._read_string(b.kEdsPropID_LensName) or "",
            backend="edsdk",
        )
        logger.info("Connected to %s (%s)", self._info.model, self._info.lens or "no lens")

        self._register_handlers()
        self._detect_capabilities()
        self._on_thread_start_liveview()

    def _on_thread_loop(self) -> None:
        next_frame = time.perf_counter()
        while not self._stopping.is_set():
            pump_messages(self._dll)
            self._drain_commands()

            now = time.perf_counter()
            if self._streaming and now >= next_frame:
                self._on_thread_pump_frame()
                next_frame = now + self._frame_interval
            else:
                # Short sleep: long enough not to spin a core, short enough that
                # a queued command is picked up promptly.
                time.sleep(0.002)

    def _drain_commands(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            if not command.future.set_running_or_notify_cancel():
                continue
            try:
                command.future.set_result(command.fn())
            except BaseException as exc:  # returned to the caller, not swallowed
                command.future.set_exception(exc)

    def _on_thread_teardown(self) -> None:
        if self._dll is None:
            return
        try:
            if self._camera is not None:
                if self._streaming:
                    try:
                        self._on_thread_stop_liveview()
                    except EdsError as exc:
                        logger.warning("Could not stop live view cleanly: %s", exc)
                self._dll.EdsCloseSession(self._camera)
                self._dll.EdsRelease(self._camera)
                self._camera = None
            self._dll.EdsTerminateSDK()
        finally:
            self._dll = None
            if sys.platform == "win32":
                ctypes.windll.ole32.CoUninitialize()
            logger.info("Camera thread stopped.")

    # --- properties ---------------------------------------------------------

    def _read_uint(self, prop_id: int) -> int | None:
        value = c_uint32()
        code = self._dll.EdsGetPropertyData(self._camera, prop_id, 0, 4, byref(value))
        return value.value if code == EDS_ERR_OK else None

    def _read_string(self, prop_id: int) -> str | None:
        buf = (c_char * b.EDS_MAX_NAME)()
        code = self._dll.EdsGetPropertyData(self._camera, prop_id, 0, b.EDS_MAX_NAME, buf)
        if code != EDS_ERR_OK:
            return None
        return buf.value.decode("ascii", "replace")

    def _retry_while_busy(self, label: str, call) -> int:
        """Run an SDK call, retrying while the camera reports itself busy.

        Returns the final return code. Only DEVICE_BUSY and PTP_DEVICE_BUSY are
        retried -- every other failure is real and is returned immediately, so
        this cannot mask a genuine error by grinding away at it.
        """
        deadline = time.perf_counter() + _BUSY_RETRY_S
        attempts = 0
        while True:
            code = call()
            attempts += 1
            if code not in (EDS_ERR_DEVICE_BUSY, EDS_ERR_PTP_DEVICE_BUSY):
                if attempts > 1 and code == EDS_ERR_OK:
                    logger.debug("%s succeeded after %d attempts", label, attempts)
                return code
            if time.perf_counter() >= deadline:
                logger.warning("%s still busy after %.0f s", label, _BUSY_RETRY_S)
                return code
            # Pump and yield: the busy state usually clears once the in-flight
            # live-view frame completes.
            pump_messages(self._dll)
            time.sleep(0.05)

    def _write_uint(self, prop_id: int, value: int, label: str) -> None:
        payload = c_uint32(value)
        check(
            f"EdsSetPropertyData({label})",
            self._retry_while_busy(
                f"EdsSetPropertyData({label})",
                lambda: self._dll.EdsSetPropertyData(
                    self._camera, prop_id, 0, ctypes.sizeof(payload), byref(payload)
                ),
            ),
        )

    def _settable_values(self, prop_id: int) -> list[int]:
        """Values the camera will currently accept for a property."""
        desc = b.EdsPropertyDesc()
        code = self._dll.EdsGetPropertyDesc(self._camera, prop_id, byref(desc))
        if code != EDS_ERR_OK:
            return []
        return [desc.propDesc[i] for i in range(max(0, min(desc.numElements, 128)))]

    def _detect_capabilities(self) -> None:
        """Ask the body what it supports, and record why anything is missing."""
        notes: list[tuple[str, str]] = []

        lens_attached = self._read_uint(b.kEdsPropID_LensStatus)
        af_mode = self._read_uint(b.kEdsPropID_AFMode)
        focus_drive = bool(lens_attached)
        if not lens_attached:
            notes.append(("focus_drive", "No lens is attached."))
        elif af_mode == 3:
            # The body reports manual focus; the lens switch is the usual cause.
            focus_drive = False
            notes.append(
                ("focus_drive", "The lens is in manual focus. Set its AF/MF switch to AF.")
            )

        # Evf_Zoom is NOT_SUPPORTED on an EOS R7, so the software loupe is the
        # only magnification available there. Ask rather than assume: bodies
        # differ, and a body that does support it is a real gain.
        zoom_values = self._settable_values(b.kEdsPropID_Evf_Zoom)
        liveview_zoom = len(zoom_values) > 0
        if not liveview_zoom:
            notes.append(
                (
                    "liveview_zoom",
                    "This body does not offer camera-side live-view magnification "
                    "over EDSDK; the software loupe enlarges pixels the camera "
                    "already discarded.",
                )
            )

        # EDSDK exposes no shutter-mode property, so electronic shutter cannot
        # be selected from here. Saying so plainly beats reporting success for
        # something we never did.
        notes.append(
            (
                "electronic_shutter",
                "EDSDK does not expose shutter mode. Set Shutter mode to "
                "Electronic in the camera menu to avoid shutter shock.",
            )
        )

        self._capabilities = Capabilities(
            focus_drive=focus_drive,
            liveview_zoom=liveview_zoom,
            electronic_shutter=False,
            notes=tuple(notes),
        )
        logger.info(
            "Capabilities: focus_drive=%s liveview_zoom=%s",
            focus_drive,
            liveview_zoom,
        )

    # --- live view ----------------------------------------------------------

    def _on_thread_start_liveview(self) -> None:
        self._write_uint(b.kEdsPropID_Evf_OutputDevice, b.kEdsEvfOutputDevice_PC, "Evf_OutputDevice")
        self._streaming = True

    def _on_thread_stop_liveview(self) -> None:
        self._streaming = False
        # Hand the view back to the camera's own screen, or the body looks
        # broken after the app exits.
        self._write_uint(
            b.kEdsPropID_Evf_OutputDevice, b.kEdsEvfOutputDevice_TFT, "Evf_OutputDevice"
        )

    def _on_thread_pump_frame(self) -> None:
        """Pull one live-view frame into the shared slot."""
        stream = c_void_p()
        if self._dll.EdsCreateMemoryStream(0, byref(stream)) != EDS_ERR_OK:
            return
        try:
            evf = c_void_p()
            if self._dll.EdsCreateEvfImageRef(stream, byref(evf)) != EDS_ERR_OK:
                return
            try:
                code = self._dll.EdsDownloadEvfImage(self._camera, evf)
                if code == EDS_ERR_OBJECT_NOTREADY:
                    return  # normal: the camera has no new frame yet
                if code != EDS_ERR_OK:
                    logger.debug("EdsDownloadEvfImage: %s", error_name(code))
                    return

                length = c_uint64()
                pointer = c_void_p()
                if self._dll.EdsGetLength(stream, byref(length)) != EDS_ERR_OK:
                    return
                if self._dll.EdsGetPointer(stream, byref(pointer)) != EDS_ERR_OK:
                    return
                if not pointer.value or not length.value:
                    return
                data = ctypes.string_at(pointer, length.value)
            finally:
                self._dll.EdsRelease(evf)
        finally:
            self._dll.EdsRelease(stream)

        with self._frame_lock:
            self._frame = data
            self._frame_seq += 1

    # --- focus --------------------------------------------------------------

    def _on_thread_drive_focus(self, direction: str, steps: int, size: int) -> None:
        size = max(1, min(int(size), 3))
        table = b.kEdsEvfDriveLens_Near if direction == "near" else b.kEdsEvfDriveLens_Far
        param = table[size]
        for _ in range(max(1, steps)):
            code = self._dll.EdsSendCommand(
                self._camera, b.kEdsCameraCommand_DriveLensEvf, param
            )
            if code != EDS_ERR_OK:
                raise EdsError("EdsSendCommand(DriveLensEvf)", code)
            if FOCUS_STEP_PAUSE_S:
                time.sleep(FOCUS_STEP_PAUSE_S)
            pump_messages(self._dll)
            self._on_thread_pump_frame()

    # --- capture ------------------------------------------------------------

    def _register_handlers(self) -> None:
        """Register the object-event handler that tells us a file is ready."""

        def on_object_event(event: int, ref: int, context: int) -> int:
            if event in (
                b.kEdsObjectEvent_DirItemRequestTransfer,
                b.kEdsObjectEvent_DirItemCreated,
            ):
                # Record and return; downloading from inside the callback would
                # re-enter the SDK while it is dispatching to us.
                self._pending_transfers.append(ref)
            elif ref:
                self._dll.EdsRelease(c_void_p(ref))
            return EDS_ERR_OK

        self._object_handler = b.EdsObjectEventHandler(on_object_event)
        check(
            "EdsSetObjectEventHandler",
            self._dll.EdsSetObjectEventHandler(
                self._camera, b.kEdsObjectEvent_All, self._object_handler, None
            ),
        )

    def _on_thread_capture(self, destination_dir: Path) -> Path:
        destination_dir.mkdir(parents=True, exist_ok=True)
        self._pending_transfers.clear()

        # Route to this computer, and claim the destination has room -- the
        # camera refuses to shoot otherwise, whatever the real disk has.
        self._write_uint(b.kEdsPropID_SaveTo, b.kEdsSaveTo_Host, "SaveTo")
        capacity = b.EdsCapacity(numberOfFreeClusters=0x7FFFFFFF, bytesPerSector=512, reset=1)
        check(
            "EdsSetCapacity",
            self._retry_while_busy(
                "EdsSetCapacity", lambda: self._dll.EdsSetCapacity(self._camera, capacity)
            ),
        )

        # On a copy stand, residual vibration is the main cause of soft scans.
        if self._settle_delay_s > 0:
            deadline = time.perf_counter() + self._settle_delay_s
            while time.perf_counter() < deadline:
                pump_messages(self._dll)
                self._on_thread_pump_frame()
                time.sleep(0.005)

        self._send_shutter()

        # Pump while waiting: the event arrives through the message queue, so a
        # plain sleep would wait forever.
        deadline = time.perf_counter() + 60.0
        while not self._pending_transfers and time.perf_counter() < deadline:
            pump_messages(self._dll)
            self._on_thread_pump_frame()
            time.sleep(0.005)

        if not self._pending_transfers:
            raise CameraError(
                "The camera never reported a captured file within 60 s.\n"
                "  If it did shoot, the file may have gone to the card instead."
            )

        # RAW+JPEG sends one transfer per file, and the second lands slightly
        # after the first. Returning as soon as one arrives silently keeps half
        # the shot, so wait a moment for stragglers before draining the queue.
        settle = time.perf_counter() + _EXTRA_TRANSFER_GRACE_S
        while time.perf_counter() < settle:
            pump_messages(self._dll)
            self._on_thread_pump_frame()
            time.sleep(0.005)

        paths: list[Path] = []
        errors: list[str] = []
        while self._pending_transfers:
            item = c_void_p(self._pending_transfers.pop(0))
            try:
                paths.append(self._download(item, destination_dir))
            except EdsError as exc:
                # One failed file must not lose the ones that did arrive.
                errors.append(str(exc))
                logger.error("Download failed: %s", exc)
        if not paths:
            joined = "; ".join(errors) or "no transfer events arrived"
            raise CameraError(f"Every file from this capture failed: {joined}")
        if errors:
            logger.warning("%d file(s) failed, %d saved", len(errors), len(paths))
        return paths

    def _send_shutter(self) -> None:
        """Release the shutter without autofocus.

        Non-AF: on a copy stand focus is set deliberately and must not change
        between frames of a roll. Falls back to TakePicture on bodies that
        refuse the press/release form.
        """
        code = self._retry_while_busy(
            "PressShutterButton",
            lambda: self._dll.EdsSendCommand(
                self._camera,
                b.kEdsCameraCommand_PressShutterButton,
                b.kEdsCameraCommand_ShutterButton_Completely_NonAF,
            ),
        )
        if code == EDS_ERR_OK:
            release = self._dll.EdsSendCommand(
                self._camera,
                b.kEdsCameraCommand_PressShutterButton,
                b.kEdsCameraCommand_ShutterButton_OFF,
            )
            if release != EDS_ERR_OK:
                logger.warning("Shutter release returned %s", error_name(release))
            return

        logger.info("PressShutterButton refused (%s); using TakePicture.", error_name(code))
        check(
            "EdsSendCommand(TakePicture)",
            self._dll.EdsSendCommand(self._camera, b.kEdsCameraCommand_TakePicture, 0),
        )

    def _download(self, item: c_void_p, destination_dir: Path) -> Path:
        info = b.EdsDirectoryItemInfo()
        check("EdsGetDirectoryItemInfo", self._dll.EdsGetDirectoryItemInfo(item, byref(info)))
        name = info.szFileName.decode("ascii", "replace")
        destination = _unique_path(destination_dir / name)

        stream = c_void_p()
        if sys.platform == "win32":
            check(
                "EdsCreateFileStreamEx",
                self._dll.EdsCreateFileStreamEx(
                    str(destination),
                    b.kEdsFileCreateDisposition_CreateAlways,
                    b.kEdsAccess_ReadWrite,
                    byref(stream),
                ),
            )
        else:
            check(
                "EdsCreateFileStream",
                self._dll.EdsCreateFileStream(
                    str(destination).encode("utf-8"),
                    b.kEdsFileCreateDisposition_CreateAlways,
                    b.kEdsAccess_ReadWrite,
                    byref(stream),
                ),
            )

        try:
            started = time.perf_counter()
            code = self._dll.EdsDownload(item, info.size, stream)
            if code != EDS_ERR_OK:
                self._dll.EdsDownloadCancel(item)
                raise EdsError("EdsDownload", code)
            check("EdsDownloadComplete", self._dll.EdsDownloadComplete(item))
            elapsed = time.perf_counter() - started
            logger.info(
                "Downloaded %s (%.1f MB in %.1f s, %.1f MB/s)",
                destination.name,
                info.size / 1e6,
                elapsed,
                info.size / 1e6 / max(elapsed, 1e-6),
            )
        finally:
            self._dll.EdsRelease(stream)

        if self._delete_after_download:
            code = self._dll.EdsDeleteDirectoryItem(item)
            if code != EDS_ERR_OK:
                logger.warning("Could not delete from card: %s", error_name(code))
        else:
            self._dll.EdsRelease(item)

        return destination


def _unique_path(path: Path) -> Path:
    """A path that does not exist yet, by adding ``-1``, ``-2`` ... if needed.

    Never overwrite a capture: the original is the one thing that cannot be
    regenerated.
    """
    if not path.exists():
        return path
    for n in range(1, 10000):
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise CameraError(f"Could not find a free filename beside {path}")
