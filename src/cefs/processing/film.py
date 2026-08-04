"""Negative-to-positive inversion, done properly.

This is the hardest part of the project, and the part where a plausible-looking
shortcut gives visibly wrong results. The v0.1 linear flip (255 - x) is honest
about being only a preview toggle; this module is the real thing.

WHY A LINEAR FLIP IS WRONG
--------------------------
Two independent reasons, and colour film suffers both.

*The orange mask.* Colour negative film carries a dye mask across the whole
frame that attenuates blue heavily and red barely. Flipping that along with the
image inverts the mask too, which is why an un-neutralised colour negative comes
out strongly cyan. The mask is a constant multiplicative attenuation, so
dividing by it removes it -- but only if you know what it is, which means
measuring the film base.

*Density is logarithmic, brightness is not.* Film records a scene as optical
density, roughly proportional to the log of exposure, and the camera captures
transmittance. Getting back to something proportional to scene brightness is a
reciprocal, not a subtraction. Subtracting gives the familiar milky positive
with compressed midtones.

THE PIPELINE
------------
1. Linearise. Live-view JPEG is sRGB-encoded; RAW is already linear.
2. Sample the film base -- the unexposed rebate, which is the *brightest* part
   of a negative because nothing exposed it.
3. Divide by the base, per channel. Neutralises the mask and normalises
   exposure in one step.
4. Take the reciprocal: ``base / pixel`` is proportional to scene exposure.
5. Scale each channel so real highlights reach white, then fit a per-channel
   curve so the midtones agree -- the three emulsion layers differ in contrast,
   not just in attenuation, so matching highlights alone leaves a cast.
6. Set exposure from the *median*, not the highlight. Scaling to the brightest
   percentile sets a good clipping point but a terrible exposure: one specular
   reflection can leave the whole frame bunched near black. Darkroom printing
   meters the midtones, and so does this.
7. Black/white points, contrast, and sRGB encoding.

Both film types are first class. Black-and-white takes the same path with the
per-channel steps collapsed, rather than being a special case bolted on.

HOW IT IS FAST ENOUGH
---------------------
Every per-pixel step above is a *scalar function of one input value*: the base,
the scale and the fitted gamma are per-channel constants for a given frame. So
the whole chain collapses into one lookup table per channel, built once per
frame from a subsample and applied with a single gather.

That is not only an optimisation. It is what lets the live preview and the saved
16-bit file share one implementation -- the tables come from the same function,
differing only in how many entries they have.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# Below this a pixel is noise or a clear scratch, and the reciprocal explodes.
_EPS = 1e-5

# sRGB transfer function constants.
_SRGB_THRESHOLD = 0.04045
_SRGB_LINEAR_SCALE = 12.92
_SRGB_ALPHA = 0.055

#: Linear value the typical scene tone should land on. 18% grey is the
#: photographic convention, and the eye agrees with it.
_MIDGREY = 0.18

#: Pixels sampled when estimating base, scale and balance. A percentile over
#: 32 million pixels gives the same answer as one over 200k, far more slowly.
_ANALYSIS_SAMPLES = 200_000


@dataclass
class FilmParams:
    """Everything that controls the inversion.

    The defaults give a reasonable positive from a typical colour negative with
    nothing adjusted. All of them are exposed in the UI, because film stocks
    differ and no single set of numbers is right for every one.
    """

    mode: str = "color"  # "color" or "bw"; both first class

    #: Film base (unexposed rebate) in linear RGB, 0-1. ``None`` samples it from
    #: the frame. The single most important value in the pipeline.
    base: tuple[float, float, float] | None = None

    #: Overall exposure of the positive, as a multiplier.
    exposure: float = 1.0

    #: Contrast gamma. Negative film has a gamma near 0.6, so the positive needs
    #: roughly its inverse to regain normal contrast.
    contrast: float = 1.65

    #: Clip points on the normalised positive, before the contrast curve.
    black_point: float = 0.0
    white_point: float = 1.0

    #: Per-channel trim, for a cast left after the automatic balance.
    channel_gain: tuple[float, float, float] = (1.0, 1.0, 1.0)

    #: Percentile treated as the highlight. Below 100 so one dust speck or
    #: scratch cannot define white for the whole frame.
    highlight_percentile: float = 99.5

    #: Fit a per-channel gamma so the channels' midtones agree. See the module
    #: docstring: matching highlights alone leaves a visible cast.
    auto_balance: bool = True

    #: Set exposure from the median so the midtones land on 18% grey. Without
    #: it, one specular highlight defines white and the whole frame sits dark.
    #: ``exposure`` still applies on top, as a manual trim.
    auto_exposure: bool = True

    def replace(self, **changes) -> "FilmParams":
        """A copy with some fields changed. ``None`` values are ignored."""
        merged = {**self.__dict__, **{k: v for k, v in changes.items() if v is not None}}
        return FilmParams(**merged)


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
    """Measure the film base from a negative.

    The unexposed rebate is the brightest part of a negative -- nothing exposed
    it, so it has minimum density and passes the most light. A high percentile
    is therefore a usable automatic estimate even when the rebate is out of
    frame, because the densest part of the image approaches base density anyway.

    Args:
        linear: Linear float image ``(H, W, 3)``.
        region: Optional ``(x, y, w, h)`` in normalised 0-1 coordinates, to
            sample a rebate the user has pointed at. Much more reliable than the
            automatic estimate whenever the rebate is actually visible.
        percentile: Percentile treated as base when no region is given. Not 100:
            a dust speck or light leak would otherwise define it.

    Returns:
        Per-channel base values, linear.
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
    """Reciprocal of transmittance, with the film base at zero.

    ``base / pixel`` is proportional to scene exposure; subtracting one puts the
    base -- unexposed, so black in the positive -- at zero.
    """
    return np.maximum(base / np.maximum(x, _EPS) - 1.0, 0.0)


def analyse(linear: np.ndarray, params: FilmParams) -> dict:
    """Measure the per-channel constants the inversion needs.

    Runs on a subsample, so it costs about the same on a 32 MP RAW as on a
    live-view frame. Returns everything :func:`build_luts` needs.
    """
    base = params.base if params.base is not None else sample_base(linear)
    base_arr = np.asarray(base, dtype=np.float64)
    if params.mode == "bw":
        # One base for all channels. A per-channel base on a monochrome negative
        # would invent a colour cast out of sensor noise -- exactly the kind of
        # plausible-looking wrongness this module exists to avoid.
        base_arr = np.full(3, float(base_arr.mean()))

    flat = _subsample(linear).astype(np.float64)
    positives = [_positive_scalar(flat[:, c], base_arr[c]) for c in range(3)]

    scales = np.array(
        [max(float(np.percentile(p, params.highlight_percentile)), _EPS) for p in positives]
    )
    gammas = np.ones(3)

    if params.mode != "bw" and params.auto_balance:
        # Match each channel's median to green's. Green is the reference because
        # it is the least extreme channel of a colour negative: red passes the
        # mask nearly untouched, blue is heavily attenuated.
        medians = np.array([float(np.median(p / s)) for p, s in zip(positives, scales)])
        if np.all((medians > 1e-3) & (medians < 1 - 1e-3)):
            reference = medians[1]
            for c in (0, 2):
                # Solve m ** g = reference. Clamped: a wild exponent means the
                # assumption behind the fit does not hold for this frame, and a
                # mild correction beats a violent wrong one.
                gammas[c] = float(np.clip(np.log(reference) / np.log(medians[c]), 0.4, 2.5))

    # Place the midtones, not the highlight.
    #
    # Scaling so the 99.5th percentile reaches white sets a sensible *clipping*
    # point, but it is a poor exposure: one specular highlight or a bright sky
    # can sit far above everything else, leaving the whole scene bunched near
    # black once the contrast gamma is applied. On a synthetic negative the
    # median landed at 0.09 of the highlight, and the finished positive came out
    # at a mean of 48/255 -- visibly dark, and wrong in the same way an
    # enlarger set from a specular reflection would be.
    #
    # So exposure is set from the median, as darkroom printing does. Solving
    # ``(median * k) ** contrast == MIDGREY`` for k puts the typical tone where
    # the eye expects it, and the highlight scaling still governs clipping.
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


def build_luts(measured: dict, params: FilmParams, in_bits: int, out_bits: int) -> np.ndarray:
    """Build one lookup table per channel covering the whole transfer.

    Args:
        measured: Output of :func:`analyse`.
        params: See :class:`FilmParams`.
        in_bits: 8 for a live-view frame, 16 for decoded RAW.
        out_bits: 8 for preview, 16 for a saved file.

    Returns:
        ``(3, 2 ** in_bits)`` table of the output dtype.
    """
    if in_bits == 8:
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
            # x ** contrast, not x ** (1 / contrast). Film records
            # D = gamma * log(E) with gamma near 0.6, so base/pixel recovers
            # E ** gamma, and getting back to E means raising to 1 / gamma --
            # which is what `contrast` holds. Inverting this exponent silently
            # flattens every image instead of restoring its contrast.
            x = np.power(x, float(params.contrast))
        out[c] = np.clip(_srgb_encode(x) * peak + 0.5, 0, peak).astype(dtype)
    return out


def _apply_luts(image: np.ndarray, luts: np.ndarray, mode: str) -> np.ndarray:
    """Map each channel through its own table."""
    if mode == "bw":
        # Collapse first, not last. In "bw" every channel shares one base and
        # the output is neutral by definition, so mapping three channels and
        # then averaging does the same work three times. Averaging the input
        # and mapping once is the same result for a monochrome negative, and
        # measurably faster on every frame.
        # cv2's conversion is SIMD and several times faster than a numpy mean
        # plus a cast. It weights the channels for luma rather than averaging
        # them equally, which makes no practical difference here: a monochrome
        # negative's three channels carry the same image.
        grey = cv2.cvtColor(np.ascontiguousarray(image), cv2.COLOR_RGB2GRAY)
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

    Args:
        bgr8: Live-view frame, BGR uint8.
        params: See :class:`FilmParams`.
        measured: Reuse a previous :func:`analyse` result. Worth passing while
            focusing: re-measuring every frame makes the preview shimmer as the
            estimated base wanders with the grain.
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
