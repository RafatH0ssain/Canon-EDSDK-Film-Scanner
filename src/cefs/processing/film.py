"""Negative-to-positive inversion.

A linear flip (255 - x) is wrong twice over. Colour negative film carries an
orange dye mask, which flipping inverts along with the image -- hence the cyan
cast -- and it is a multiplicative attenuation, so it is removed by *dividing*
by the measured film base. And density is logarithmic: recovering scene
brightness from transmittance is a reciprocal, not a subtraction.

So: linearise, divide by the base, take ``base / pixel``, scale each channel to
its highlight, fit a per-channel gamma so the midtones agree, then set exposure
from the *median* (a specular highlight makes a terrible exposure reference),
apply black/white points and contrast, and sRGB-encode.

Every step is a scalar function of one input, so the whole chain collapses into
one lookup table per channel. That is what makes it fast, and what lets the live
preview and the saved 16-bit file share an implementation instead of drifting.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from functools import cache

import numpy as np

from cefs.processing.grey import to_grey

# Below this a pixel is noise or a clear scratch, and the reciprocal explodes.
_EPS = 1e-5

_SRGB_THRESHOLD = 0.04045
_SRGB_LINEAR_SCALE = 12.92
_SRGB_ALPHA = 0.055

_MIDGREY = 0.18

# A percentile over 32 million pixels gives the same answer as one over 200k.
_ANALYSIS_SAMPLES = 200_000


@dataclass
class FilmParams:
    """Everything that controls the inversion. All of it is exposed in the UI."""

    mode: str = "color"  # "color" or "bw"; both first class

    #: Film base (unexposed rebate), linear RGB 0-1. None measures it from the
    #: frame. The single most important value in the pipeline.
    base: tuple[float, float, float] | None = None

    exposure: float = 1.0

    #: Negative film has a gamma near 0.6, so the positive needs its inverse.
    contrast: float = 1.65

    black_point: float = 0.0
    white_point: float = 1.0
    channel_gain: tuple[float, float, float] = (1.0, 1.0, 1.0)

    #: Below 100 so a dust speck cannot define white for the whole frame.
    highlight_percentile: float = 99.5

    #: Fit a per-channel gamma so midtones agree; matching highlights alone
    #: leaves a visible cast.
    auto_balance: bool = True

    #: Place the median on 18% grey. Without it one specular highlight defines
    #: white and the frame sits dark. ``exposure`` still applies on top.
    auto_exposure: bool = True

    def replace(self, **changes) -> "FilmParams":
        """A copy with some fields changed. ``None`` values are ignored."""
        merged = {**self.__dict__, **{k: v for k, v in changes.items() if v is not None}}
        return FilmParams(**merged)

    def without_base(self) -> "FilmParams":
        """A copy with the base cleared.

        :meth:`replace` cannot express this -- it drops ``None``, so
        ``replace(base=None)`` keeps the base it claims to clear.
        """
        return dataclasses.replace(self, base=None)


# --- transfer functions ------------------------------------------------------

_V = np.arange(256, dtype=np.float64) / 255.0
_SRGB_TO_LINEAR_LUT = np.where(
    _V <= _SRGB_THRESHOLD,
    _V / _SRGB_LINEAR_SCALE,
    ((_V + _SRGB_ALPHA) / (1 + _SRGB_ALPHA)) ** 2.4,
).astype(np.float32)


def srgb_to_linear(image: np.ndarray) -> np.ndarray:
    """Undo sRGB encoding on an 8-bit image, giving linear float32 in 0-1."""
    if image.dtype != np.uint8:
        raise TypeError(f"srgb_to_linear expects uint8, got {image.dtype}")
    return _SRGB_TO_LINEAR_LUT[image]


def _srgb_encode(x: np.ndarray) -> np.ndarray:
    """Encode linear 0-1 to sRGB 0-1."""
    x = np.clip(x, 0.0, 1.0)
    return np.where(
        x <= 0.0031308,
        x * _SRGB_LINEAR_SCALE,
        (1 + _SRGB_ALPHA) * np.power(x, 1 / 2.4) - _SRGB_ALPHA,
    )


def linear16_to_linear(image: np.ndarray) -> np.ndarray:
    """Scale a 16-bit linear array, as RAW decoding gives, to float 0-1."""
    if image.dtype != np.uint16:
        raise TypeError(f"linear16_to_linear expects uint16, got {image.dtype}")
    return image.astype(np.float32) / 65535.0


# --- PQ, for HEIF ------------------------------------------------------------
#
# A Canon body writes HEIF only in HDR PQ mode: measured on an R7 .HIF, the NCLX
# profile says transfer characteristic 16 (SMPTE ST 2084) over BT.2020 at 10
# bits. Reading those codes as sRGB costs 3.1% mean error against 0.9% for the
# correct path -- and that 0.9% is the 10-bit quantisation floor, so the
# transfer itself contributes nothing. On a wider-range original the gap is 31x.
#
# Primaries are deliberately not converted. The RAW path stays in the camera's
# own primaries too (``output_color=raw``), and the base division that follows
# normalises colour far harder than a primaries matrix would. Converting on one
# path and not the other would be worse than both being approximate.

_PQ_M1 = 2610 / 16384
_PQ_M2 = 2523 / 4096 * 128
_PQ_C1 = 3424 / 4096
_PQ_C2 = 2413 / 4096 * 32
_PQ_C3 = 2392 / 4096 * 32


def pq_to_linear(pq: np.ndarray) -> np.ndarray:
    """Decode PQ 0-1 to linear 0-1, where 1.0 is ST 2084's 10000 cd/m^2 peak.

    An absolute reference, so every frame of a roll decodes on one scale.
    """
    p = np.power(np.clip(np.asarray(pq, dtype=np.float64), 0.0, 1.0), 1.0 / _PQ_M2)
    return np.power(np.maximum(p - _PQ_C1, 0.0) / (_PQ_C2 - _PQ_C3 * p), 1.0 / _PQ_M1)


@cache
def pq16_to_linear_lut() -> np.ndarray:
    """Linear value of every 16-bit PQ code. 256 KB, built once.

    Canon's 10-bit samples arrive left-shifted into 16 bits -- measured, the gap
    between adjacent values is exactly 64 -- so this is lossless.
    """
    return pq_to_linear(np.arange(65536, dtype=np.float64) / 65535.0).astype(np.float32)


# --- film base ---------------------------------------------------------------


def _subsample(linear: np.ndarray) -> np.ndarray:
    flat = linear.reshape(-1, 3)
    if flat.shape[0] > _ANALYSIS_SAMPLES:
        flat = flat[:: flat.shape[0] // _ANALYSIS_SAMPLES]
    return flat


def sample_base(
    linear: np.ndarray,
    region: tuple[float, float, float, float] | None = None,
    percentile: float = 99.0,
) -> tuple[float, float, float]:
    """Measure the film base, per channel, from a linear ``(H, W, 3)`` image.

    The unexposed rebate is the *brightest* part of a negative, so a high
    percentile works even when the rebate is out of frame. Pass ``region`` --
    ``(x, y, w, h)`` normalised -- to sample a rebate the user pointed at, which
    is far more reliable when one is visible.
    """
    if linear.ndim != 3 or linear.shape[2] != 3:
        raise ValueError("sample_base expects a 3-channel image.")

    if region is not None:
        h, w = linear.shape[:2]
        x0, y0, rw, rh = region
        left = int(np.clip(x0, 0.0, 1.0) * w)
        top = int(np.clip(y0, 0.0, 1.0) * h)
        right = max(int(np.clip(x0 + rw, 0.0, 1.0) * w), left + 1)
        bottom = max(int(np.clip(y0 + rh, 0.0, 1.0) * h), top + 1)
        patch = linear[top:bottom, left:right]
        # Median, not mean: robust to a dust speck inside the selection.
        return tuple(float(np.median(patch[..., c])) for c in range(3))

    flat = _subsample(linear)
    return tuple(float(np.percentile(flat[:, c], percentile)) for c in range(3))


# --- analysis ----------------------------------------------------------------


def _positive_scalar(x: np.ndarray, base: float) -> np.ndarray:
    """``base / pixel`` is proportional to scene exposure; -1 puts base at zero."""
    return np.maximum(base / np.maximum(x, _EPS) - 1.0, 0.0)


def analyse(linear: np.ndarray, params: FilmParams) -> dict:
    """Measure the per-channel constants :func:`build_luts` needs.

    Runs on a subsample, so a 32 MP RAW costs about what a live-view frame does.
    """
    base = params.base if params.base is not None else sample_base(linear)
    base_arr = np.asarray(base, dtype=np.float64)
    if params.mode == "bw":
        # One base for all channels: a per-channel base on a monochrome negative
        # would invent a colour cast out of sensor noise.
        base_arr = np.full(3, float(base_arr.mean()))

    flat = _subsample(linear).astype(np.float64)
    positives = [_positive_scalar(flat[:, c], base_arr[c]) for c in range(3)]

    scales = np.array(
        [max(float(np.percentile(p, params.highlight_percentile)), _EPS) for p in positives]
    )
    gammas = np.ones(3)

    if params.mode != "bw" and params.auto_balance:
        # Match each channel's median to green's -- the least extreme channel of
        # a colour negative, since red passes the mask nearly untouched and blue
        # is heavily attenuated. Solve m ** g = reference, clamped, because a
        # wild exponent means the fit's assumption does not hold for this frame.
        medians = np.array([float(np.median(p / s)) for p, s in zip(positives, scales)])
        if np.all((medians > 1e-3) & (medians < 1 - 1e-3)):
            reference = medians[1]
            for c in (0, 2):
                gammas[c] = float(np.clip(np.log(reference) / np.log(medians[c]), 0.4, 2.5))

    # Exposure from the median, as darkroom printing does. Scaling to the 99.5th
    # percentile clips well but exposes badly: one specular highlight left a test
    # frame at a mean of 48/255. Solving (median * k) ** contrast == MIDGREY puts
    # the typical tone where the eye expects it; highlights still govern clipping.
    auto_exposure = 1.0
    if params.auto_exposure:
        greys = np.concatenate([p / s for p, s in zip(positives, scales)])
        median = float(np.median(greys))
        if median > 1e-6:
            target = _MIDGREY ** (1.0 / max(float(params.contrast), 1e-3))
            auto_exposure = float(np.clip(target / median, 0.05, 50.0))

    return {
        "base": tuple(float(v) for v in base_arr),
        "scales": tuple(float(v) for v in scales),
        "gammas": tuple(float(v) for v in gammas),
        "auto_exposure": (auto_exposure,) * 3,
    }


# --- lookup tables -----------------------------------------------------------


def build_luts(
    measured: dict,
    params: FilmParams,
    in_bits: int,
    out_bits: int,
    input_linear: np.ndarray | None = None,
) -> np.ndarray:
    """One ``(3, 2 ** in_bits)`` table covering the whole transfer.

    ``input_linear`` overrides what each input code means -- an sRGB decode at 8
    bits, a straight scale at 16. HEIF passes :func:`pq16_to_linear_lut`, which
    folds the PQ EOTF into the same gather instead of decoding twice.
    """
    if input_linear is not None:
        inputs = np.asarray(input_linear, dtype=np.float64)
        if inputs.shape != (1 << in_bits,):
            raise ValueError(
                f"input_linear must have {1 << in_bits} entries for in_bits={in_bits}, "
                f"got {inputs.shape}"
            )
    elif in_bits == 8:
        inputs = _SRGB_TO_LINEAR_LUT.astype(np.float64)
    elif in_bits == 16:
        inputs = np.arange(65536, dtype=np.float64) / 65535.0
    else:
        raise ValueError(f"in_bits must be 8 or 16, got {in_bits}")

    black, white = float(params.black_point), float(params.white_point)
    if white <= black:
        raise ValueError(f"white_point ({white}) must exceed black_point ({black}).")

    peak = 255 if out_bits == 8 else 65535
    dtype = np.uint8 if out_bits == 8 else np.uint16
    out = np.empty((3, inputs.size), dtype=dtype)

    for c in range(3):
        x = _positive_scalar(inputs, measured["base"][c]) / measured["scales"][c]
        if measured["gammas"][c] != 1.0:
            x = np.power(x, measured["gammas"][c])
        x = x * float(params.channel_gain[c]) * float(params.exposure)
        x = x * float(measured.get("auto_exposure", (1.0, 1.0, 1.0))[c])
        x = np.clip((x - black) / (white - black), 0.0, 1.0)
        if params.contrast != 1.0:
            # x ** contrast, NOT x ** (1 / contrast). base/pixel recovers
            # E ** gamma, so getting back to E raises to 1 / gamma -- which is
            # what `contrast` already holds. Inverting it flattens every image.
            x = np.power(x, float(params.contrast))
        out[c] = np.clip(_srgb_encode(x) * peak + 0.5, 0, peak).astype(dtype)
    return out


def _apply_luts(image: np.ndarray, luts: np.ndarray, mode: str) -> np.ndarray:
    """Map each channel through its own table."""
    if mode == "bw":
        # Collapse first, not last: in "bw" all channels share one base and the
        # output is neutral anyway, so mapping three and averaging does the work
        # three times. cv2's conversion is SIMD and much faster than numpy here.
        grey = to_grey(np.ascontiguousarray(image), order="rgb")
        mapped = luts[1][grey]
        return np.repeat(mapped[:, :, None], 3, axis=2)

    out = np.empty(image.shape, dtype=luts.dtype)
    for c in range(3):
        out[..., c] = luts[c][image[..., c]]
    return out


# --- the two entry points ----------------------------------------------------


def invert_preview(
    bgr8: np.ndarray, params: FilmParams, measured: dict | None = None
) -> np.ndarray:
    """Invert an 8-bit BGR frame, returning 8-bit BGR.

    Pass ``measured`` to reuse a previous :func:`analyse` -- re-measuring every
    frame makes the preview shimmer as the estimated base wanders with grain.
    """
    if bgr8.dtype != np.uint8:
        raise TypeError(f"invert_preview expects uint8, got {bgr8.dtype}")
    rgb = bgr8[:, :, ::-1]
    if measured is None:
        measured = analyse(srgb_to_linear(rgb), params)
    luts = build_luts(measured, params, in_bits=8, out_bits=8)
    return _apply_luts(rgb, luts, params.mode)[:, :, ::-1]


def invert_raw(
    linear_rgb16: np.ndarray, params: FilmParams, measured: dict | None = None
) -> np.ndarray:
    """Invert a 16-bit linear RGB array, returning 16-bit sRGB RGB.

    Stays 16-bit throughout: inverting a negative stretches a compressed range
    hard, and doing that in 8 bits bands visibly in smooth tones.
    """
    if linear_rgb16.dtype != np.uint16:
        raise TypeError(f"invert_raw expects uint16, got {linear_rgb16.dtype}")
    if measured is None:
        measured = analyse(linear16_to_linear(linear_rgb16), params)
    luts = build_luts(measured, params, in_bits=16, out_bits=16)
    return _apply_luts(linear_rgb16, luts, params.mode)


def invert_pq(
    pq_rgb16: np.ndarray, params: FilmParams, measured: dict | None = None
) -> np.ndarray:
    """Invert a 16-bit PQ RGB array -- a Canon HEIF -- to 16-bit sRGB.

    :func:`invert_raw` with a PQ input transfer instead of a linear one.
    """
    if pq_rgb16.dtype != np.uint16:
        raise TypeError(f"invert_pq expects uint16, got {pq_rgb16.dtype}")
    table = pq16_to_linear_lut()
    if measured is None:
        measured = analyse(table[pq_rgb16], params)
    luts = build_luts(measured, params, in_bits=16, out_bits=16, input_linear=table)
    return _apply_luts(pq_rgb16, luts, params.mode)
