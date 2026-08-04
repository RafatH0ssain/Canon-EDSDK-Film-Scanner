"""Synthetic film negatives for the mock camera.

These are the fixtures the inversion pipeline is developed against, so they
model what makes film inversion hard: a film rebate (the unexposed border, and
the reference v0.3 samples to neutralise the cast), an orange mask across the
whole colour frame, density inversion, grain, and a compressed density range --
real negatives never span 0-255, which is why naive inversion looks milky.
"""

from __future__ import annotations

import numpy as np

# A typical colour-negative mask measured as an approximate RGB transmission
# ratio. Orange means the blue channel is heavily attenuated and red barely at
# all, which is why an un-neutralised colour negative inverts to strong cyan.
_MASK_RGB = (255.0, 150.0, 78.0)

# Real negatives occupy a compressed slice of the available range: base+fog sets
# a floor, and highlight density sets a ceiling well short of white.
_DENSITY_FLOOR = 0.12
_DENSITY_CEILING = 0.78


def _base_scene(height: int, width: int, phase: float) -> np.ndarray:
    """A synthetic positive 'photograph' in float [0, 1], shape (H, W).

    Contains a range of tones and some hard edges, so both the tone-curve work
    and the focus aids have something to measure.
    """
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    xn = xx / max(width - 1, 1)
    yn = yy / max(height - 1, 1)

    # A sky-to-ground gradient gives a smooth tonal ramp.
    scene = 0.25 + 0.55 * (1.0 - yn)

    # A bright disc: a specular highlight that must survive inversion without
    # clipping. It drifts with `phase` so the stream is visibly live.
    cx = 0.30 + 0.06 * np.sin(phase)
    cy = 0.32 + 0.04 * np.cos(phase * 0.7)
    disc = ((xn - cx) ** 2 + (yn - cy) ** 2) < 0.012
    scene[disc] = 0.95

    # Hard-edged dark bars: high-contrast edges for focus peaking to latch onto.
    bar = ((xx.astype(int) // 24) % 3 == 0) & (yn > 0.62)
    scene[bar] = 0.08

    # A mid-tone block, for checking that mid-tones do not shift during inversion.
    block = (xn > 0.68) & (xn < 0.88) & (yn > 0.15) & (yn < 0.40)
    scene[block] = 0.47

    return np.clip(scene, 0.0, 1.0)


def _apply_grain(image: np.ndarray, rng: np.random.Generator, strength: float) -> np.ndarray:
    """Add per-pixel noise standing in for film grain."""
    noise = rng.normal(0.0, strength, image.shape).astype(np.float32)
    return np.clip(image + noise, 0.0, 1.0)


def _blur(image: np.ndarray, radius: int) -> np.ndarray:
    """Cheap separable box blur simulating defocus.

    Cumulative sums rather than cv2, so this module needs only numpy.
    """
    if radius < 1:
        return image
    kernel = 2 * radius + 1
    padded = np.pad(image, ((radius, radius), (radius, radius), (0, 0)), mode="edge")
    out = padded.cumsum(axis=0)
    out = out[kernel - 1 :] - np.vstack([np.zeros_like(out[:1]), out[: -kernel]])
    out = out.cumsum(axis=1)
    out = out[:, kernel - 1 :] - np.hstack([np.zeros_like(out[:, :1]), out[:, : -kernel]])
    return (out / (kernel * kernel)).astype(np.float32)


def make_negative(
    width: int = 960,
    height: int = 640,
    *,
    color: bool = True,
    phase: float = 0.0,
    focus_error: float = 0.0,
    grain: float = 0.02,
    seed: int | None = None,
) -> np.ndarray:
    """Render a synthetic film negative as an 8-bit BGR array.

    BGR, not RGB, because that is what OpenCV expects and the processing layer
    is built on OpenCV.

    Args:
        width: Frame width in pixels.
        height: Frame height in pixels.
        color: ``True`` for a colour negative with an orange mask, ``False`` for
            a black-and-white negative on a neutral base.
        phase: Animation phase in radians; advance it between frames so the mock
            live view visibly moves.
        focus_error: 0.0 is sharp; higher values blur, simulating misfocus. Used
            to test the focus-peaking overlay and the sharpness readout.
        grain: Standard deviation of the grain noise, in [0, 1] units.
        seed: RNG seed. Fixed values make deterministic test fixtures.

    Returns:
        ``uint8`` array of shape ``(height, width, 3)`` in BGR order.
    """
    rng = np.random.default_rng(seed)
    scene = _base_scene(height, width, phase)

    # Invert to a negative and compress into a realistic density range: this
    # compression is precisely what makes naive linear inversion look wrong.
    negative = 1.0 - scene
    negative = _DENSITY_FLOOR + negative * (_DENSITY_CEILING - _DENSITY_FLOOR)

    if color:
        # Per-channel gamma differences: colour layers do not respond
        # identically, so channels need independent curves when inverting.
        channels = [
            np.power(negative, 1.00),  # R layer
            np.power(negative, 0.92),  # G layer
            np.power(negative, 0.85),  # B layer
        ]
        stacked = np.stack(channels, axis=-1)
        mask = np.array(_MASK_RGB, dtype=np.float32) / 255.0
        frame = stacked * mask
    else:
        frame = np.repeat(negative[:, :, None], 3, axis=2)
        # B&W film base is not pure neutral; it carries a faint warm tint.
        frame = frame * np.array([1.0, 0.985, 0.96], dtype=np.float32)

    # The rebate: unexposed film base framing the image. This is the reference
    # patch the colour pipeline samples, so it must be the mask colour at full
    # transmission, with no image content.
    border_y = max(int(height * 0.06), 2)
    border_x = max(int(width * 0.05), 2)
    if color:
        base_color = np.array(_MASK_RGB, dtype=np.float32) / 255.0
    else:
        base_color = np.array([0.94, 0.93, 0.91], dtype=np.float32)

    mask_area = np.ones((height, width), dtype=bool)
    mask_area[border_y : height - border_y, border_x : width - border_x] = False
    frame[mask_area] = np.broadcast_to(base_color, (height, width, 3))[mask_area]

    # Grain then defocus, both after the rebate is in place: they are properties
    # of the whole frame. Compositing the rebate afterwards would leave the
    # border permanently sharp and make any focus aid look like it worked.
    frame = _apply_grain(frame, rng, grain)

    if focus_error > 0:
        frame = _blur(frame, int(round(focus_error)))

    # Convert RGB -> BGR at the last moment, so the maths above stays readable.
    bgr = np.clip(frame, 0.0, 1.0)[:, :, ::-1]
    return (bgr * 255.0).astype(np.uint8)
