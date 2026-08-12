"""Record what OpenCV currently produces, so a replacement can be held to it.

Run once, with OpenCV still installed:

    .venv/bin/python tests/fixtures/regenerate_golden.py

Everything is derived from a seeded generator, so the inputs are reproducible
and only the *outputs* need storing. These files are the contract the
non-OpenCV implementation has to meet -- regenerate them only if OpenCV's
behaviour is deliberately being abandoned, never to make a failing test pass.
"""

from __future__ import annotations

import numpy as np

from pathlib import Path

HERE = Path(__file__).resolve().parent / "golden"


def source_frame() -> np.ndarray:
    """A deterministic 8-bit colour frame with structure worth measuring.

    Gradients give the resize something to interpolate, the disc and bars give
    the edge detector real edges, and the noise keeps anything from being
    accidentally exact.
    """
    rng = np.random.default_rng(20260812)
    h, w = 120, 160
    yy, xx = np.mgrid[0:h, 0:w]
    frame = np.zeros((h, w, 3), np.float64)
    frame[..., 0] = xx / w * 255
    frame[..., 1] = yy / h * 255
    frame[..., 2] = 128
    frame[(yy - 60) ** 2 + (xx - 80) ** 2 < 25**2] = [240, 30, 30]
    frame[:, ::20] = [10, 240, 10]
    frame[::15, :] = [250, 250, 10]
    frame += rng.normal(0, 4, frame.shape)
    return np.clip(frame, 0, 255).astype(np.uint8)


def source_16bit() -> np.ndarray:
    rng = np.random.default_rng(31337)
    base = (source_frame().astype(np.uint32) * 257).astype(np.uint16)
    noise = rng.integers(-300, 300, base.shape)
    return np.clip(base.astype(np.int32) + noise, 0, 65535).astype(np.uint16)


def main() -> None:
    import cv2

    HERE.mkdir(parents=True, exist_ok=True)
    frame = source_frame()
    img16 = source_16bit()

    # --- JPEG encode/decode -------------------------------------------------
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    assert ok
    (HERE / "jpeg_q90.jpg").write_bytes(buf.tobytes())
    np.save(HERE / "jpeg_q90_decoded.npy", cv2.imdecode(buf, cv2.IMREAD_COLOR))

    # --- resize -------------------------------------------------------------
    np.save(HERE / "resize_down_area.npy",
            cv2.resize(frame, (80, 60), interpolation=cv2.INTER_AREA))
    np.save(HERE / "resize_up_nearest.npy",
            cv2.resize(frame, (320, 240), interpolation=cv2.INTER_NEAREST))

    # --- greyscale, both conventions the codebase uses -----------------------
    np.save(HERE / "gray_bgr2gray.npy", cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    np.save(HERE / "gray_rgb2gray.npy", cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY))

    # --- edge magnitude, what peaking and sharpness are built on ------------
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    dx = cv2.Scharr(gray, cv2.CV_32F, 1, 0)
    dy = cv2.Scharr(gray, cv2.CV_32F, 0, 1)
    np.save(HERE / "scharr_dx.npy", dx)
    np.save(HERE / "scharr_dy.npy", dy)
    np.save(HERE / "scharr_magnitude.npy", cv2.magnitude(dx, dy))

    # --- saturating 8-bit add, used by the mock ------------------------------
    other = np.roll(frame, 7, axis=1)
    np.save(HERE / "add_saturated.npy", cv2.add(frame, other, dtype=cv2.CV_8U))

    # --- 16-bit TIFF round trip ---------------------------------------------
    np.save(HERE / "tiff16_source.npy", img16)

    print(f"wrote {len(list(HERE.iterdir()))} golden files to {HERE}")


if __name__ == "__main__":
    main()
