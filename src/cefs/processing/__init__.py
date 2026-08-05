"""Image processing: pure, stateless functions over numpy arrays.

Nothing here touches the SDK, the network or the UI, so it can be tested against
synthetic fixtures with no camera. Frames are ``uint8`` ``(H, W, 3)`` in **BGR**
unless a function says otherwise.

Speed matters here: EDSDK delivers ~60 fps, a ~17 ms budget, and the pipeline
measures 13-19 ms. It is close to the bottleneck, not comfortably clear of it.
"""

from cefs.processing.codec import decode_jpeg, encode_jpeg
from cefs.processing.invert import invert_linear
from cefs.processing.loupe import crop_zoom
from cefs.processing.peaking import apply_peaking, peaking_coverage, peaking_mask
from cefs.processing.sharpness import corner_sharpness, sharpness, sharpness_of_region

__all__ = [
    "apply_peaking",
    "corner_sharpness",
    "crop_zoom",
    "decode_jpeg",
    "encode_jpeg",
    "invert_linear",
    "peaking_coverage",
    "peaking_mask",
    "sharpness",
    "sharpness_of_region",
]
