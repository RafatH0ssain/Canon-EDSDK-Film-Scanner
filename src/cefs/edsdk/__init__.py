"""Native EDSDK binding and the camera thread. Hands back JPEG bytes and paths.

Every ctypes signature and SDK constant lives in :mod:`cefs.edsdk.bindings` and
nowhere else. One thread owns the SDK -- see :mod:`cefs.edsdk.camera`.
"""
