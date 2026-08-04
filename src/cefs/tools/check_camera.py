"""Check the real camera through the actual application code.

    python -m cefs.tools.check_camera

Drives :class:`~cefs.edsdk.camera.EdsdkCamera` -- the same class the web app
uses -- rather than a bespoke script, so a pass here means the app's own path
works, not merely that the SDK does.

**What it does to your camera.** Opens a session, routes live view to the PC
(your camera's screen goes blank), measures the stream, reports capabilities,
and optionally nudges focus. It restores live view and closes the session on the
way out.

**It does not fire the shutter.** Capture is exercised separately with
``--capture``, which is opt-in precisely because it writes a file and makes a
noise.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from cefs.backend import CameraError
from cefs.config import load_config
from cefs.processing.codec import decode_jpeg
from cefs.processing.sharpness import sharpness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=10.0, help="Measurement duration.")
    parser.add_argument(
        "--focus", action="store_true", help="Nudge focus and confirm the frame changes."
    )
    parser.add_argument(
        "--capture",
        action="store_true",
        help="FIRES THE SHUTTER once and downloads the file.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    config = load_config()

    if not config.edsdk.library_dir and not config.edsdk.library_path:
        print(
            "No EDSDK location configured.\n"
            "  Copy config.example.yaml to config.yaml and set edsdk.library_dir\n"
            "  to the folder holding the 64-bit EDSDK library.",
            file=sys.stderr,
        )
        return 2

    from cefs.edsdk.camera import EdsdkCamera

    camera = EdsdkCamera(
        library_dir=config.edsdk.resolved_library_dir() or "",
        library_path=config.edsdk.resolved_library_path(),
        target_fps=config.liveview.target_fps,
        settle_delay_s=config.capture.settle_delay_s,
    )

    print("Connecting ...")
    try:
        camera.start()
    except (CameraError, RuntimeError) as exc:
        print(f"\nFAILED\n\n{exc}", file=sys.stderr)
        return 1

    try:
        print(f"  body : {camera.info.model}")
        print(f"  lens : {camera.info.lens or '(none reported)'}")

        caps = camera.capabilities
        print("\nCAPABILITIES")
        for name in ("focus_drive", "liveview_zoom", "electronic_shutter"):
            mark = "yes" if getattr(caps, name) else " no"
            note = caps.note_for(name)
            print(f"  [{mark}] {name}")
            if note:
                print(f"        {note}")

        if not _measure(camera, args.seconds):
            return 1
        if args.focus:
            _focus(camera, caps)
        if args.capture:
            _capture(camera, config.capture.resolved_output_dir())
    finally:
        print("\nDisconnecting ...")
        camera.stop()
    return 0


def _measure(camera, seconds: float) -> bool:
    print(f"\nLIVE VIEW ({seconds:.0f} s)")
    deadline = time.perf_counter() + 10.0
    while camera.latest_frame() is None and time.perf_counter() < deadline:
        time.sleep(0.01)
    first = camera.latest_frame()
    if first is None:
        print("  No frames in 10 s. Take the camera off any menu screen and retry.")
        return False

    image = decode_jpeg(first)
    print(f"  resolution : {image.shape[1]}x{image.shape[0]}")
    print(f"  frame size : {len(first) / 1024:.0f} KB")

    seen = 0
    current = first
    start = time.perf_counter()
    end = start + seconds
    while time.perf_counter() < end:
        frame = camera.latest_frame()
        if frame is not None and frame is not current:
            current = frame
            seen += 1
        time.sleep(0.001)
    elapsed = time.perf_counter() - start
    fps = seen / elapsed
    print(f"  delivered  : {seen} frames in {elapsed:.1f} s -> {fps:.2f} fps")
    print(f"  period     : {1000.0 / fps:.0f} ms" if fps > 0 else "  period     : n/a")

    # The stream is capped by config.liveview.target_fps, so this is what the
    # app actually delivers, not the camera's ceiling. The v0.0 spike measured
    # the ceiling at ~60 fps.
    print("  (capped by liveview.target_fps in config; the body can do ~60)")
    return True


def _focus(camera, caps) -> None:
    print("\nFOCUS")
    if not caps.focus_drive:
        print(f"  unavailable: {caps.note_for('focus_drive')}")
        return

    def settled_sharpness() -> float:
        # Let the lens finish moving and the stream flush stale frames.
        time.sleep(0.6)
        return sharpness(decode_jpeg(camera.latest_frame()))

    before = settled_sharpness()
    camera.drive_focus("near", steps=15, size=3)
    after = settled_sharpness()
    camera.drive_focus("far", steps=15, size=3)
    back = settled_sharpness()

    # Two decimal places, not zero: this metric reads in single digits on
    # EDSDK frames, so rounding to integers hides the whole signal.
    print(f"  sharpness  : {before:.2f} -> {after:.2f} (near) -> {back:.2f} (back)")

    # Relative, because the absolute value depends entirely on the subject.
    # And measured from the image rather than the return code: the v0.0 spike
    # got EDS_ERR_OK from a focus command that moved nothing detectable.
    change = abs(after - before) / max(before, 1e-6)
    print(f"  change     : {change * 100:.0f}%")
    print(f"  verdict    : {'focus drive WORKS' if change > 0.25 else 'NO detectable movement'}")
    if change <= 0.25:
        print("               Aim at something with texture -- a flat, low-contrast")
        print("               subject changes little however far focus travels.")


def _capture(camera, output_dir: Path) -> None:
    print("\nCAPTURE -- this fires the shutter")
    started = time.perf_counter()
    paths = camera.capture(output_dir)
    elapsed = time.perf_counter() - started
    # A list: one release can write several files, e.g. RAW+JPEG.
    print(f"  files      : {len(paths)}")
    for path in paths:
        print(f"    {path.name:<24} {path.stat().st_size / 1e6:6.1f} MB")
    total = sum(p.stat().st_size for p in paths)
    print(f"  total      : {total / 1e6:.1f} MB in {elapsed:.1f} s")


if __name__ == "__main__":
    raise SystemExit(main())
