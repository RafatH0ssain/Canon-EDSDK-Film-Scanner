"""v0.0 spike -- does EDSDK live view over USB actually beat Wi-Fi?

    python spike/v0_0_liveview.py --seconds 20

Throwaway measurement code. It exists to answer one question and then be
deleted; nothing should be built on top of it. See ROADMAP.md section 8, v0.0.

THE GATE
    The CCAPI baseline over 2.4 GHz Wi-Fi is 3.98 fps at 960x640, or 11.15 fps
    at 640x424. Note that the baseline's quoted "latency" of 251 ms / 90 ms is
    the inter-frame period -- exactly 1/fps -- not glass-to-glass lag. This
    script therefore reports the same quantity, so the two are comparable.
    Genuine end-to-end lag (photon to pixel on screen) is a different figure
    that neither project has measured; doing so needs a camera pointed at a
    running millisecond clock.

    EDSDK must clearly win -- 20+ fps at comparable or better resolution -- or
    the honest recommendation is to abandon this project and go back to CCAPI
    with its "small" live-view size.

WHAT IT DOES TO YOUR CAMERA
    Opens a session, switches live view output to the PC, pulls frames, then
    restores live view and closes the session. It does NOT fire the shutter.
    It does not touch focus unless you pass --focus-test.

WHY A DEDICATED THREAD
    EDSDK delivers its events through COM, so the thread that calls
    EdsInitializeSDK must be a single-threaded apartment running a Windows
    message pump. Every SDK call below happens on that one thread. This is the
    part of the design most worth de-risking early, which is why the spike
    already does it properly even though nothing else here is production code.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import statistics
import sys
import threading
import time
from ctypes import POINTER, byref, c_char, c_uint32, c_uint64, c_void_p
from ctypes import wintypes
from pathlib import Path

# --- SDK constants, transcribed from the local headers -----------------------
# Values are read from EDSDK.h / EDSDKTypes.h / EDSDKErrors.h in the user's own
# SDK copy. Names and numbers only -- Canon's binaries, headers and reference
# text stay out of this repository.

EDS_ERR_OK = 0x00000000
EDS_ERR_OBJECT_NOTREADY = 0x0000A102

_ERROR_NAMES = {
    0x00000000: "OK",
    0x00000002: "INTERNAL_ERROR",
    0x00000007: "NOT_SUPPORTED",
    0x00000061: "INVALID_HANDLE",
    0x00000080: "DEVICE_NOT_FOUND",
    0x00000081: "DEVICE_BUSY",
    0x00000082: "DEVICE_INVALID",
    0x00000085: "DEVICE_INTERNAL_ERROR",
    0x00000086: "DEVICE_INVALID_PARAMETER",
    0x000000C0: "COMM_PORT_IS_IN_USE",
    0x000000C1: "COMM_DISCONNECTED",
    0x000000C2: "COMM_DEVICE_INCOMPATIBLE",
    0x000000C3: "COMM_BUFFER_FULL",
    0x000000C4: "COMM_USB_BUS_ERR",
    0x00002003: "SESSION_NOT_OPEN",
    0x0000200A: "DEVICEPROP_NOT_SUPPORTED",
    0x00002019: "PTP_DEVICE_BUSY",
    0x0000201E: "SESSION_ALREADY_OPEN",
    0x0000A102: "OBJECT_NOTREADY",
}

# Translations for the codes a first run actually hits. An error code alone
# sends people to a search engine; naming the physical cause does not.
_ERROR_CAUSES = {
    0x00000080: "No camera found. Check the USB cable and that the camera is on.",
    0x00000081: "Camera busy. Close EOS Utility / Lightroom tethering and retry.",
    0x000000C0: "The USB port is in use -- another program holds the camera.",
    0x000000C1: "Camera disconnected mid-session. Suspect the cable.",
    0x0000201E: "A session is already open, usually from a crashed earlier run.",
    0x00002003: "Session is not open.",
    0x00000007: "The camera does not support this operation.",
    0x0000200A: "This body does not expose that property.",
}

kEdsPropID_ProductName = 0x00000002
kEdsPropID_Evf_OutputDevice = 0x00000500
kEdsPropID_Evf_Mode = 0x00000501

kEdsEvfOutputDevice_TFT = 1
kEdsEvfOutputDevice_PC = 2

kEdsCameraCommand_DriveLensEvf = 0x00000103
# Three step sizes in each direction, 1 finest and 3 coarsest. A fine step on a
# long macro lens can be smaller than the frame noise, so a "did it move?" test
# needs the coarse one to be conclusive.
_DRIVE_NEAR = {1: 0x00000001, 2: 0x00000002, 3: 0x00000003}
_DRIVE_FAR = {1: 0x00008001, 2: 0x00008002, 3: 0x00008003}

# COM / message-pump constants from the Windows SDK, not Canon's.
COINIT_APARTMENTTHREADED = 0x2
PM_REMOVE = 0x0001


class EdsError(RuntimeError):
    """An EDSDK call returned something other than EDS_ERR_OK."""

    def __init__(self, call: str, code: int) -> None:
        name = _ERROR_NAMES.get(code, "UNKNOWN")
        cause = _ERROR_CAUSES.get(code)
        message = f"{call} failed: 0x{code:08X} {name}"
        if cause:
            message += f"\n  -> {cause}"
        super().__init__(message)
        self.call = call
        self.code = code


def load_edsdk(dll_dir: Path) -> ctypes.WinDLL:
    """Load EDSDK.dll and declare every signature this spike uses.

    Two things matter here and both bite silently if wrong.

    Bitness: 64-bit Python needs the 64-bit library, and a mismatch fails at
    load time with an error that never mentions bitness.

    Signatures: every bound function gets an explicit argtypes and restype. If
    you omit them ctypes guesses, and a wrong guess corrupts the stack -- the
    crash then surfaces somewhere unrelated and looks like a different bug.
    """
    dll_path = dll_dir / "EDSDK.dll"
    if not dll_path.is_file():
        raise FileNotFoundError(f"No EDSDK.dll in {dll_dir}")

    # EDSDK.dll loads EdsImage.dll from beside itself; without this the load
    # fails with a bare "module not found" naming the wrong file.
    os.add_dll_directory(str(dll_dir))
    dll = ctypes.WinDLL(str(dll_path))

    # EdsError is EdsUInt32. EdsBaseRef (and every Eds*Ref alias) is an opaque
    # struct pointer, so c_void_p. EdsInt32/EdsUInt32 are 32-bit on Windows.
    err = c_uint32
    ref = c_void_p

    dll.EdsInitializeSDK.argtypes = []
    dll.EdsInitializeSDK.restype = err

    dll.EdsTerminateSDK.argtypes = []
    dll.EdsTerminateSDK.restype = err

    dll.EdsRelease.argtypes = [ref]
    dll.EdsRelease.restype = c_uint32  # returns the new reference count

    dll.EdsGetCameraList.argtypes = [POINTER(ref)]
    dll.EdsGetCameraList.restype = err

    dll.EdsGetChildCount.argtypes = [ref, POINTER(c_uint32)]
    dll.EdsGetChildCount.restype = err

    dll.EdsGetChildAtIndex.argtypes = [ref, ctypes.c_int32, POINTER(ref)]
    dll.EdsGetChildAtIndex.restype = err

    dll.EdsOpenSession.argtypes = [ref]
    dll.EdsOpenSession.restype = err

    dll.EdsCloseSession.argtypes = [ref]
    dll.EdsCloseSession.restype = err

    dll.EdsGetPropertyData.argtypes = [ref, c_uint32, ctypes.c_int32, c_uint32, c_void_p]
    dll.EdsGetPropertyData.restype = err

    dll.EdsSetPropertyData.argtypes = [ref, c_uint32, ctypes.c_int32, c_uint32, c_void_p]
    dll.EdsSetPropertyData.restype = err

    dll.EdsSendCommand.argtypes = [ref, c_uint32, ctypes.c_int32]
    dll.EdsSendCommand.restype = err

    dll.EdsCreateMemoryStream.argtypes = [c_uint64, POINTER(ref)]
    dll.EdsCreateMemoryStream.restype = err

    dll.EdsCreateEvfImageRef.argtypes = [ref, POINTER(ref)]
    dll.EdsCreateEvfImageRef.restype = err

    dll.EdsDownloadEvfImage.argtypes = [ref, ref]
    dll.EdsDownloadEvfImage.restype = err

    dll.EdsGetPointer.argtypes = [ref, POINTER(c_void_p)]
    dll.EdsGetPointer.restype = err

    dll.EdsGetLength.argtypes = [ref, POINTER(c_uint64)]
    dll.EdsGetLength.restype = err

    return dll


def check(call: str, code: int) -> None:
    """Raise unless an SDK call succeeded.

    EDSDK reports failure by return value, never by exception, so an unchecked
    return code turns into a mystery several steps later.
    """
    if code != EDS_ERR_OK:
        raise EdsError(call, code)


def pump_messages() -> None:
    """Drain the thread's Windows message queue.

    EDSDK's event delivery rides on COM, which posts to this thread's message
    queue. If nothing pumps it, events -- including capture-complete -- simply
    never arrive, and the symptom is a hang rather than an error.
    """
    msg = wintypes.MSG()
    while ctypes.windll.user32.PeekMessageW(byref(msg), None, 0, 0, PM_REMOVE):
        ctypes.windll.user32.TranslateMessage(byref(msg))
        ctypes.windll.user32.DispatchMessageW(byref(msg))


class Spike:
    """Runs the whole v0.0 measurement on one STA thread."""

    def __init__(
        self, dll_dir: Path, seconds: float, warmup: int, focus_test: bool, focus_step: int = 1
    ) -> None:
        self.dll_dir = dll_dir
        self.seconds = seconds
        self.warmup = warmup
        self.focus_test = focus_test
        self.focus_step = focus_step
        self.result: dict | None = None
        self.error: BaseException | None = None

    def run(self) -> None:
        """Entry point for the camera thread."""
        try:
            self.result = self._run()
        except BaseException as exc:  # reported on the main thread
            self.error = exc

    def _run(self) -> dict:
        hr = ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        if hr < 0:
            raise RuntimeError(f"CoInitializeEx failed: 0x{hr & 0xFFFFFFFF:08X}")

        dll = load_edsdk(self.dll_dir)
        print(f"  loaded {self.dll_dir / 'EDSDK.dll'}")

        check("EdsInitializeSDK", dll.EdsInitializeSDK())
        print("  SDK initialised on an STA thread with a message pump")
        try:
            return self._with_sdk(dll)
        finally:
            dll.EdsTerminateSDK()
            ctypes.windll.ole32.CoUninitialize()

    def _with_sdk(self, dll) -> dict:
        camera_list = c_void_p()
        check("EdsGetCameraList", dll.EdsGetCameraList(byref(camera_list)))
        try:
            count = c_uint32()
            check("EdsGetChildCount", dll.EdsGetChildCount(camera_list, byref(count)))
            print(f"  cameras detected: {count.value}")
            if count.value == 0:
                raise RuntimeError(
                    "No camera found.\n"
                    "  Check: USB cable seated, camera powered on and awake,\n"
                    "  and no other program (EOS Utility, Lightroom) holding it."
                )

            camera = c_void_p()
            check("EdsGetChildAtIndex", dll.EdsGetChildAtIndex(camera_list, 0, byref(camera)))
            try:
                return self._with_camera(dll, camera)
            finally:
                dll.EdsRelease(camera)
        finally:
            # EDSDK is reference counted; every ref obtained must be released.
            dll.EdsRelease(camera_list)

    def _with_camera(self, dll, camera) -> dict:
        check("EdsOpenSession", dll.EdsOpenSession(camera))
        print("  session open")
        try:
            name = (c_char * 256)()
            check(
                "EdsGetPropertyData(ProductName)",
                dll.EdsGetPropertyData(camera, kEdsPropID_ProductName, 0, 256, name),
            )
            product = name.value.decode("ascii", "replace")
            print(f"  body: {product}")

            return self._measure(dll, camera, product)
        finally:
            dll.EdsCloseSession(camera)
            print("  session closed")

    def _set_output_device(self, dll, camera, device: int) -> None:
        value = c_uint32(device)
        check(
            "EdsSetPropertyData(Evf_OutputDevice)",
            dll.EdsSetPropertyData(
                camera, kEdsPropID_Evf_OutputDevice, 0, ctypes.sizeof(value), byref(value)
            ),
        )

    def _grab_frame(self, dll, camera) -> tuple[bytes | None, float]:
        """Pull one live-view frame. Returns (jpeg_bytes, download_seconds).

        Returns (None, elapsed) when the camera has no frame ready yet, which
        is normal and not an error.
        """
        stream = c_void_p()
        check("EdsCreateMemoryStream", dll.EdsCreateMemoryStream(0, byref(stream)))
        try:
            evf = c_void_p()
            check("EdsCreateEvfImageRef", dll.EdsCreateEvfImageRef(stream, byref(evf)))
            try:
                started = time.perf_counter()
                code = dll.EdsDownloadEvfImage(camera, evf)
                elapsed = time.perf_counter() - started
                if code == EDS_ERR_OBJECT_NOTREADY:
                    return None, elapsed
                check("EdsDownloadEvfImage", code)

                length = c_uint64()
                check("EdsGetLength", dll.EdsGetLength(stream, byref(length)))
                pointer = c_void_p()
                check("EdsGetPointer", dll.EdsGetPointer(stream, byref(pointer)))
                if not pointer.value or not length.value:
                    return None, elapsed
                return ctypes.string_at(pointer, length.value), elapsed
            finally:
                dll.EdsRelease(evf)
        finally:
            dll.EdsRelease(stream)

    def _measure(self, dll, camera, product: str) -> dict:
        print("  starting live view (output -> PC) ...")
        self._set_output_device(dll, camera, kEdsEvfOutputDevice_PC)
        try:
            # The first frames after enabling live view are not representative:
            # the camera is still spinning the stream up.
            print(f"  warming up ({self.warmup} frames) ...")
            warmed = 0
            deadline = time.perf_counter() + 15.0
            while warmed < self.warmup and time.perf_counter() < deadline:
                pump_messages()
                frame, _ = self._grab_frame(dll, camera)
                if frame:
                    warmed += 1
                else:
                    time.sleep(0.005)
            if warmed == 0:
                raise RuntimeError(
                    "Live view produced no frames in 15 s.\n"
                    "  Take the camera off any menu screen and make sure it is not\n"
                    "  in a mode that forbids live view, then retry."
                )

            print(f"  measuring for {self.seconds:.0f} s ...")
            timestamps: list[float] = []
            downloads: list[float] = []
            sizes: list[int] = []
            not_ready = 0
            samples: list[bytes] = []

            start = time.perf_counter()
            end = start + self.seconds
            while time.perf_counter() < end:
                pump_messages()
                frame, elapsed = self._grab_frame(dll, camera)
                now = time.perf_counter()
                if frame is None:
                    not_ready += 1
                    time.sleep(0.002)
                    continue
                timestamps.append(now)
                downloads.append(elapsed)
                sizes.append(len(frame))
                if len(samples) < 3:
                    samples.append(frame)

            elapsed_total = time.perf_counter() - start
            focus = self._focus_check(dll, camera) if self.focus_test else None

            return {
                "product": product,
                "elapsed_s": elapsed_total,
                "frames": len(timestamps),
                "timestamps": timestamps,
                "downloads": downloads,
                "sizes": sizes,
                "not_ready": not_ready,
                "samples": samples,
                "focus": focus,
            }
        finally:
            # Hand live view back to the camera's own screen. Leaving it routed
            # to the PC makes the body look broken after the script exits.
            try:
                self._set_output_device(dll, camera, kEdsEvfOutputDevice_TFT)
                print("  live view returned to the camera screen")
            except EdsError as exc:
                print(f"  warning: could not restore live view: {exc}")

    def _focus_check(self, dll, camera) -> dict:
        """Drive focus one step near, then one step back, and prove it moved.

        Only runs with --focus-test. It moves an autofocus lens; a manual lens
        will report NOT_SUPPORTED, which is a useful answer in itself.

        A return code of OK only means the SDK accepted the command. To show
        the lens actually moved, the frame is compared before and after against
        a noise floor measured from two untouched consecutive frames. Without
        that floor a difference is meaningless -- live view frames never repeat
        exactly, so any two of them differ somewhat.
        """
        import cv2
        import numpy as np

        def sample():
            """Newest decodable frame as greyscale, or None."""
            deadline = time.perf_counter() + 3.0
            while time.perf_counter() < deadline:
                pump_messages()
                data, _ = self._grab_frame(dll, camera)
                if data:
                    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
                    if image is not None:
                        return image
                time.sleep(0.005)
            return None

        def settle(frames=15):
            """Discard frames so the lens has moved and stale ones are flushed."""
            time.sleep(0.5)
            for _ in range(frames):
                pump_messages()
                self._grab_frame(dll, camera)

        def difference(a, b) -> float:
            return float(np.mean(np.abs(a.astype(np.float32) - b.astype(np.float32))))

        def sharpness(image) -> float:
            # Tenengrad. The sibling project found variance-of-Laplacian
            # separated sharp from soft by only 1.22x on real frames.
            gx = cv2.Sobel(image, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(image, cv2.CV_32F, 0, 1, ksize=3)
            return float(np.mean(gx * gx + gy * gy))

        print("  focus test: measuring frame noise floor ...")
        out: dict = {}

        floor = 0.0
        base = sample()
        if base is None:
            return {"error": "no frame available for the focus test"}
        for _ in range(3):
            pump_messages()
            other = sample()
            if other is not None:
                floor = max(floor, difference(base, other))
        out["noise_floor"] = floor
        out["sharpness_before"] = sharpness(base)

        step = self.focus_step
        print(f"  focus test: driving one step near, then one step far (size {step}) ...")
        for label, value in ((f"near{step}", _DRIVE_NEAR[step]), (f"far{step}", _DRIVE_FAR[step])):
            code = dll.EdsSendCommand(camera, kEdsCameraCommand_DriveLensEvf, value)
            out[f"{label}_code"] = _ERROR_NAMES.get(code, f"0x{code:08X}")
            settle()
            after = sample()
            if after is None:
                out[f"{label}_moved"] = None
                continue
            out[f"{label}_diff"] = difference(base, after)
            out[f"{label}_sharpness"] = sharpness(after)
            # Comfortably clear of the floor, not merely above it.
            out[f"{label}_moved"] = out[f"{label}_diff"] > max(floor * 3.0, 1.0)
            base = after

        return out


def report(data: dict, seconds: float) -> bool:
    """Print the numbers and apply the v0.0 gate. Returns True if it passes."""
    frames = data["frames"]
    elapsed = data["elapsed_s"]

    print("\n" + "=" * 68)
    print("v0.0 RESULTS")
    print("=" * 68)
    print(f"  body                 : {data['product']}")
    print(f"  measured over        : {elapsed:.1f} s")
    print(f"  frames decoded       : {frames}")
    print(f"  'not ready' polls    : {data['not_ready']}")

    if frames < 2:
        print("\n  Too few frames to measure. Live view is not usable on this path.")
        return False

    fps = frames / elapsed
    period_ms = 1000.0 / fps

    gaps = [
        (b - a) * 1000.0
        for a, b in zip(data["timestamps"], data["timestamps"][1:])
    ]
    downloads_ms = [d * 1000.0 for d in data["downloads"]]
    sizes = data["sizes"]

    print(f"\n  fps                  : {fps:.2f}")
    print(f"  frame period (1/fps) : {period_ms:.0f} ms   <- comparable to the baseline")
    print(
        f"  inter-frame gap      : median {statistics.median(gaps):.0f} ms, "
        f"p95 {sorted(gaps)[int(len(gaps) * 0.95)]:.0f} ms, max {max(gaps):.0f} ms"
    )
    print(
        f"  download call        : median {statistics.median(downloads_ms):.1f} ms, "
        f"max {max(downloads_ms):.1f} ms"
    )
    print(
        f"  frame size           : median {statistics.median(sizes) / 1024:.0f} KB, "
        f"total {sum(sizes) / 1024 / 1024:.1f} MB"
    )
    print(f"  throughput           : {sum(sizes) / elapsed / 1024 / 1024:.2f} MB/s")

    resolution = None
    try:
        import cv2
        import numpy as np

        for sample in data["samples"]:
            image = cv2.imdecode(np.frombuffer(sample, np.uint8), cv2.IMREAD_COLOR)
            if image is not None:
                resolution = (image.shape[1], image.shape[0])
                break
    except Exception as exc:  # decoding is a nicety, not the measurement
        print(f"  (could not decode a frame for resolution: {exc})")

    if resolution:
        print(f"  frame resolution     : {resolution[0]}x{resolution[1]}")

    # Stability: split the run into 5 s windows. A number that only holds for
    # the first second is not a number worth building on.
    print("\n  STABILITY (fps per 5 s window)")
    t0 = data["timestamps"][0]
    span = data["timestamps"][-1] - t0
    windows: dict[int, int] = {}
    for t in data["timestamps"]:
        windows[int((t - t0) // 5)] = windows.get(int((t - t0) // 5), 0) + 1
    for index in sorted(windows):
        # A trailing partial window must be divided by its real duration, or a
        # short run reports a fps far below what it actually achieved.
        duration = min(5.0, span - index * 5) or 5.0
        print(f"    {index * 5:>3}-{index * 5 + 5:<3}s : {windows[index] / duration:.2f} fps")

    focus = data["focus"]
    if focus:
        print("\n  FOCUS TEST")
        if "error" in focus:
            print(f"    {focus['error']}")
        else:
            print(f"    frame noise floor    : {focus['noise_floor']:.2f} (two untouched frames)")
            print(f"    sharpness before     : {focus['sharpness_before']:.0f}")
            steps = [k[:-6] for k in focus if k.endswith("_moved")]
            for step in steps:
                moved = focus.get(f"{step}_moved")
                if moved is None:
                    print(f"    drive {step:<6}: {focus.get(f'{step}_code')}, no frame to compare")
                    continue
                verdict = "frame CHANGED" if moved else "frame unchanged -- lens did NOT move"
                print(
                    f"    drive {step:<6}: {focus[f'{step}_code']}, "
                    f"diff {focus[f'{step}_diff']:.2f}, "
                    f"sharpness {focus[f'{step}_sharpness']:.0f}  -> {verdict}"
                )

    print("\n" + "-" * 68)
    print("  GATE  (ROADMAP.md section 8, v0.0)")
    print("-" * 68)
    print("    CCAPI baseline : 3.98 fps / 251 ms @ 960x640 (Wi-Fi)")
    print("                     11.15 fps / 90 ms @ 640x424 (Wi-Fi)")
    print(f"    EDSDK measured : {fps:.2f} fps / {period_ms:.0f} ms"
          + (f" @ {resolution[0]}x{resolution[1]}" if resolution else ""))

    passed = fps >= 20.0
    if passed:
        print(f"\n    PASS -- {fps / 3.98:.1f}x the 960x640 baseline. Continue to v0.1.")
    else:
        print(f"\n    FAIL -- {fps:.2f} fps is below the 20 fps bar.")
        print("    The honest recommendation is to stop and return to the CCAPI")
        print("    project using its 'small' live-view size. Record these numbers")
        print("    in the README either way.")
    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dll-dir",
        type=Path,
        default=Path("EDSDK_v13.20.21_Windows/EDSDK_64/Dll"),
        help="Directory holding the 64-bit EDSDK.dll.",
    )
    parser.add_argument("--seconds", type=float, default=20.0, help="Measurement duration.")
    parser.add_argument("--warmup", type=int, default=10, help="Frames to discard first.")
    parser.add_argument(
        "--focus-test",
        action="store_true",
        help="Also drive focus one step near and back. Moves an autofocus lens.",
    )
    parser.add_argument(
        "--focus-step",
        type=int,
        choices=(1, 2, 3),
        default=1,
        help="Focus step size: 1 finest, 3 coarsest. Use 3 to test whether it moves at all.",
    )
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        print("This spike is Windows-only: it uses COM and a Windows message pump.")
        return 2
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        print("Run this under 64-bit Python, to match the 64-bit EDSDK libraries.")
        return 2

    dll_dir = args.dll_dir.resolve()
    print("v0.0 spike -- EDSDK live view over USB")
    print(f"  python  : {sys.version.split()[0]} ({ctypes.sizeof(ctypes.c_void_p) * 8}-bit)")
    print(f"  dll dir : {dll_dir}")
    print("  this will NOT fire the shutter\n")

    spike = Spike(dll_dir, args.seconds, args.warmup, args.focus_test, args.focus_step)

    # The SDK is initialised, used and torn down entirely on this one thread.
    thread = threading.Thread(target=spike.run, name="camera", daemon=True)
    thread.start()
    thread.join(timeout=args.seconds + 90)

    if thread.is_alive():
        print("\nThe camera thread hung. That usually means the message pump stopped.")
        return 1
    if spike.error is not None:
        print(f"\nFAILED\n\n{spike.error}", file=sys.stderr)
        return 1
    assert spike.result is not None
    return 0 if report(spike.result, args.seconds) else 1


if __name__ == "__main__":
    raise SystemExit(main())
