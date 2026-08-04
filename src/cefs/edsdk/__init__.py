"""Native EDSDK binding and the camera thread.

This package knows nothing about images or the user interface. It speaks to
Canon's SDK and hands back JPEG bytes and file paths.

Two rules hold throughout:

- Every ctypes signature and SDK constant lives in :mod:`cefs.edsdk.bindings`,
  and nowhere else, so a wrong assumption is corrected in one file.
- One thread owns the SDK. See :mod:`cefs.edsdk.camera`.
"""
