"""Why does DriveLensEvf return OK without moving the lens?

    python spike/probe_focus.py

READ-ONLY. Opens a session, starts live view, reads camera state, and stops.
It changes no setting, moves no lens, and fires nothing. The one thing it
alters is live-view output routing, which it restores on the way out.

The R7 accepts kEdsCameraCommand_DriveLensEvf and returns EDS_ERR_OK while the
lens visibly does not move. A return code alone cannot distinguish "the body
refused silently" from "the lens is not in a state that can be driven", so this
dumps the properties that would explain it, and asks the camera which ones it
will currently let us set via EdsGetPropertyDesc.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import byref, c_char, c_uint32, c_void_p
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from v0_0_liveview import (  # noqa: E402
    COINIT_APARTMENTTHREADED,
    EdsPropertyDesc,
    _ERROR_NAMES,
    check,
    kEdsEvfOutputDevice_PC,
    kEdsEvfOutputDevice_TFT,
    kEdsPropID_Evf_Mode,
    kEdsPropID_Evf_OutputDevice,
    load_edsdk,
    pump_messages,
)

DLL_DIR = Path("EDSDK_v13.20.21_Windows/EDSDK_64/Dll")

kEdsPropID_FocusInfo = 0x00000104
kEdsPropID_AEMode = 0x00000400
kEdsPropID_DriveMode = 0x00000401
kEdsPropID_AFMode = 0x00000404
kEdsPropID_LensName = 0x0000040D
kEdsPropID_LensStatus = 0x00000416
kEdsPropID_AEModeSelect = 0x00000436
kEdsPropID_AfLockState = 0x00000480
kEdsPropID_Evf_DepthOfFieldPreview = 0x00000504
kEdsPropID_Evf_Zoom = 0x00000507
kEdsPropID_Evf_AFMode = 0x0000050E

# Body AF mode. The header does not name these, so they are reported raw with
# the conventional reading noted -- an assumption, flagged as one.
_AFMODE_HINT = {0: "OneShot", 1: "AI Servo", 2: "AI Focus", 3: "Manual focus"}
_EVF_AFMODE_HINT = {0: "Quick", 1: "Live", 2: "LiveFace", 0x0E: "WholeArea"}

UINT_PROPS = [
    ("AEMode", kEdsPropID_AEMode, None),
    ("AEModeSelect", kEdsPropID_AEModeSelect, None),
    ("DriveMode", kEdsPropID_DriveMode, None),
    ("AFMode", kEdsPropID_AFMode, _AFMODE_HINT),
    ("LensStatus", kEdsPropID_LensStatus, {0: "no lens attached", 1: "lens attached"}),
    ("AfLockState", kEdsPropID_AfLockState, None),
    ("Evf_Mode", kEdsPropID_Evf_Mode, {0: "off", 1: "on"}),
    ("Evf_OutputDevice", kEdsPropID_Evf_OutputDevice, {1: "TFT", 2: "PC", 8: "PC_Small"}),
    ("Evf_AFMode", kEdsPropID_Evf_AFMode, _EVF_AFMODE_HINT),
    ("Evf_DepthOfFieldPreview", kEdsPropID_Evf_DepthOfFieldPreview, None),
    ("Evf_Zoom", kEdsPropID_Evf_Zoom, {1: "fit (x1)", 5: "x5", 10: "x10"}),
]


def read_uint(dll, camera, prop_id):
    value = c_uint32()
    code = dll.EdsGetPropertyData(camera, prop_id, 0, 4, byref(value))
    if code != 0:
        return None, _ERROR_NAMES.get(code, f"0x{code:08X}")
    return value.value, None


def read_string(dll, camera, prop_id):
    buf = (c_char * 256)()
    code = dll.EdsGetPropertyData(camera, prop_id, 0, 256, buf)
    if code != 0:
        return None, _ERROR_NAMES.get(code, f"0x{code:08X}")
    return buf.value.decode("ascii", "replace"), None


def describe(dll, camera, prop_id):
    """Ask the camera whether a property is settable right now."""
    desc = EdsPropertyDesc()
    code = dll.EdsGetPropertyDesc(camera, prop_id, byref(desc))
    if code != 0:
        return None, _ERROR_NAMES.get(code, f"0x{code:08X}")
    values = [desc.propDesc[i] for i in range(min(desc.numElements, 128))]
    return {"access": desc.access, "count": desc.numElements, "values": values}, None


def run(sweep_steps: int = 0):
    hr = ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    if hr < 0:
        raise RuntimeError(f"CoInitializeEx failed: 0x{hr & 0xFFFFFFFF:08X}")
    dll = load_edsdk(DLL_DIR.resolve())
    check("EdsInitializeSDK", dll.EdsInitializeSDK())
    try:
        camera_list = c_void_p()
        check("EdsGetCameraList", dll.EdsGetCameraList(byref(camera_list)))
        count = c_uint32()
        check("EdsGetChildCount", dll.EdsGetChildCount(camera_list, byref(count)))
        if count.value == 0:
            raise RuntimeError("No camera found.")
        camera = c_void_p()
        check("EdsGetChildAtIndex", dll.EdsGetChildAtIndex(camera_list, 0, byref(camera)))
        check("EdsOpenSession", dll.EdsOpenSession(camera))
        try:
            _dump(dll, camera, sweep_steps)
        finally:
            dll.EdsCloseSession(camera)
        dll.EdsRelease(camera)
        dll.EdsRelease(camera_list)
    finally:
        dll.EdsTerminateSDK()
        ctypes.windll.ole32.CoUninitialize()


def _dump(dll, camera, sweep_steps: int = 0):
    name, err = read_string(dll, camera, 0x00000002)
    print(f"body : {name or err}")
    lens, err = read_string(dll, camera, kEdsPropID_LensName)
    print(f"lens : {lens or err}")

    print("\n--- BEFORE live view -------------------------------------------")
    _properties(dll, camera)

    print("\nstarting live view (output -> PC) ...")
    value = c_uint32(kEdsEvfOutputDevice_PC)
    check(
        "EdsSetPropertyData(Evf_OutputDevice)",
        dll.EdsSetPropertyData(camera, kEdsPropID_Evf_OutputDevice, 0, 4, byref(value)),
    )
    try:
        # Give live view time to come up before believing anything it reports.
        for _ in range(40):
            pump_messages()
            time.sleep(0.05)

        print("\n--- DURING live view -------------------------------------------")
        _properties(dll, camera)

        print("\n--- SETTABILITY (EdsGetPropertyDesc) ---------------------------")
        print("  access 0=read-only 1=read/write; count 0 means not settable now")
        for label, prop_id in (
            ("AFMode", kEdsPropID_AFMode),
            ("Evf_AFMode", kEdsPropID_Evf_AFMode),
            ("Evf_Zoom", kEdsPropID_Evf_Zoom),
            ("Evf_Mode", kEdsPropID_Evf_Mode),
            ("DriveMode", kEdsPropID_DriveMode),
            ("AEMode", kEdsPropID_AEMode),
        ):
            desc, err = describe(dll, camera, prop_id)
            if err:
                print(f"  {label:<26}: ERROR {err}")
            else:
                shown = desc["values"][:12]
                more = "" if desc["count"] <= 12 else f" (+{desc['count'] - 12} more)"
                print(
                    f"  {label:<26}: access={desc['access']} count={desc['count']} "
                    f"values={shown}{more}"
                )

        if sweep_steps:
            sweep(dll, camera, sweep_steps)
    finally:
        value = c_uint32(kEdsEvfOutputDevice_TFT)
        dll.EdsSetPropertyData(camera, kEdsPropID_Evf_OutputDevice, 0, 4, byref(value))
        print("\nlive view returned to the camera screen")


def _properties(dll, camera):
    for label, prop_id, hints in UINT_PROPS:
        value, err = read_uint(dll, camera, prop_id)
        if err:
            print(f"  {label:<26}: -- ({err})")
            continue
        hint = ""
        if hints and value in hints:
            hint = f"  ({hints[value]})"
        elif hints:
            hint = "  (value not in the expected set)"
        print(f"  {label:<26}: {value} / 0x{value:X}{hint}")


def _grab_gray(dll, camera):
    """Newest live-view frame as greyscale, or None."""
    import cv2
    import numpy as np

    deadline = time.perf_counter() + 3.0
    while time.perf_counter() < deadline:
        pump_messages()
        stream = c_void_p()
        if dll.EdsCreateMemoryStream(0, byref(stream)) != 0:
            continue
        try:
            evf = c_void_p()
            if dll.EdsCreateEvfImageRef(stream, byref(evf)) != 0:
                continue
            try:
                if dll.EdsDownloadEvfImage(camera, evf) != 0:
                    time.sleep(0.005)
                    continue
                length = ctypes.c_uint64()
                pointer = c_void_p()
                dll.EdsGetLength(stream, byref(length))
                dll.EdsGetPointer(stream, byref(pointer))
                if not pointer.value or not length.value:
                    continue
                data = ctypes.string_at(pointer, length.value)
            finally:
                dll.EdsRelease(evf)
        finally:
            dll.EdsRelease(stream)
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is not None:
            return image
    return None


def sweep(dll, camera, steps: int) -> None:
    """Drive many coarse steps one way, then back, measuring the image.

    A single step can be smaller than the frame noise. If focus drive works at
    all, this many coarse steps defocuses the image unmistakably -- so a flat
    result here is conclusive where a single step was not.
    """
    import cv2
    import numpy as np

    def sharpness(image) -> float:
        gx = cv2.Sobel(image, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(image, cv2.CV_32F, 0, 1, ksize=3)
        return float(np.mean(gx * gx + gy * gy))

    def difference(a, b) -> float:
        return float(np.mean(np.abs(a.astype(np.float32) - b.astype(np.float32))))

    def settle(frames=20):
        time.sleep(0.4)
        for _ in range(frames):
            pump_messages()
            _grab_gray(dll, camera)

    near3, far3 = 0x00000003, 0x00008003

    print(f"\n--- FOCUS SWEEP: {steps} coarse steps near, then {steps} back ----")
    base = _grab_gray(dll, camera)
    if base is None:
        print("  no frame available; aborting sweep")
        return
    other = _grab_gray(dll, camera)
    floor = difference(base, other) if other is not None else 0.0
    print(f"  noise floor      : {floor:.2f}")
    print(f"  sharpness start  : {sharpness(base):.0f}")

    codes = set()
    for _ in range(steps):
        codes.add(dll.EdsSendCommand(camera, 0x00000103, near3))
        time.sleep(0.12)
        pump_messages()
    settle()
    after_near = _grab_gray(dll, camera)
    names = ", ".join(sorted(_ERROR_NAMES.get(c, f"0x{c:08X}") for c in codes))
    print(f"  after {steps} x near3 : return codes {{{names}}}")
    if after_near is not None:
        print(
            f"                     diff vs start {difference(base, after_near):.2f}, "
            f"sharpness {sharpness(after_near):.0f}"
        )

    codes = set()
    for _ in range(steps):
        codes.add(dll.EdsSendCommand(camera, 0x00000103, far3))
        time.sleep(0.12)
        pump_messages()
    settle()
    back = _grab_gray(dll, camera)
    names = ", ".join(sorted(_ERROR_NAMES.get(c, f"0x{c:08X}") for c in codes))
    print(f"  after {steps} x far3  : return codes {{{names}}}")
    if back is not None:
        print(
            f"                     diff vs start {difference(base, back):.2f}, "
            f"sharpness {sharpness(back):.0f}"
        )

    if after_near is not None:
        moved = difference(base, after_near) > max(floor * 4.0, 2.0)
        print(
            f"\n  VERDICT: {'focus drive WORKS' if moved else 'focus drive DOES NOTHING'}"
            f" -- {steps} coarse steps "
            f"{'changed' if moved else 'did not change'} the image."
        )


def main() -> int:
    if sys.platform != "win32":
        print("Windows only.")
        return 2

    steps = 0
    if "--sweep" in sys.argv:
        steps = int(sys.argv[sys.argv.index("--sweep") + 1])

    error: list = []

    def target():
        try:
            run(steps)
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=target, name="camera", daemon=True)
    thread.start()
    thread.join(timeout=90)
    if thread.is_alive():
        print("camera thread hung")
        return 1
    if error:
        print(f"\nFAILED\n\n{error[0]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
