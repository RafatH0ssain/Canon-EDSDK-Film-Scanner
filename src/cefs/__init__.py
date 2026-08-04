"""Canon EDSDK Film Scanner.

Remote-control film scanning for Canon cameras over EDSDK/USB, with a live
positive preview.

The package is split into layers that stay independently testable:

- ``cefs.edsdk``      native binding and the camera thread. No images, no UI.
- ``cefs.processing`` pure functions over numpy arrays. No SDK, no network.
- ``cefs.mock``       a camera-free backend, so most work needs no hardware.
- ``cefs.app``        the local web server that wires them together.
"""

__version__ = "0.1.0.dev0"
