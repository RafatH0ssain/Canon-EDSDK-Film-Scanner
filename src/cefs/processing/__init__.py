"""Image processing: pure functions over numpy arrays.

Nothing in this package touches the SDK, the network or the UI, and nothing here
holds state. That is what lets the hard parts -- colour inversion above all --
be developed and tested against synthetic fixtures with no camera attached.

Frames are OpenCV-convention ``uint8`` arrays of shape ``(H, W, 3)`` in **BGR**
order unless a function says otherwise.

A note on speed, which newly matters here. Over Wi-Fi the sibling project
received 3.98 fps, so its 14.3 ms pipeline was free. EDSDK delivers ~60 fps --
a ~17 ms budget per frame -- so this layer is now close to being the bottleneck
rather than comfortably clear of it.
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
