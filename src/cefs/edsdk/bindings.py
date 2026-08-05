"""The only place EDSDK ctypes signatures and constants live.

Quarantined here so a wrong assumption is corrected in one file. Two rules:

**Never guess a signature** -- every declaration was read from the headers in
the user's own SDK copy, and a wrong one corrupts the stack, crashing somewhere
unrelated. **Always declare argtypes and restype**, or ctypes guesses the
marshalling from whatever Python values it is handed, which works until it
does not.

Nothing Canon-owned is reproduced: only names and numeric values, which any
code calling the SDK must contain. ``EdsError``/``EdsUInt32`` are ``c_uint32``,
``EdsInt32``/``EdsBool`` are ``c_int32``, and every ``Eds*Ref`` is the same
opaque ``c_void_p``.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import POINTER, c_char, c_int32, c_uint32, c_uint64, c_void_p
from pathlib import Path

from cefs.edsdk.errors import SdkNotFoundError

# --- Reference-counted handle ------------------------------------------------
EdsBaseRef = c_void_p

# --- Constants: property IDs -------------------------------------------------
kEdsPropID_ProductName = 0x00000002
kEdsPropID_BodyIDEx = 0x00000015
kEdsPropID_SaveTo = 0x0000000B
kEdsPropID_FocusInfo = 0x00000104
kEdsPropID_ImageQuality = 0x00000100
kEdsPropID_AEMode = 0x00000400
kEdsPropID_DriveMode = 0x00000401
kEdsPropID_AFMode = 0x00000404
kEdsPropID_LensName = 0x0000040D
kEdsPropID_LensStatus = 0x00000416
kEdsPropID_AEModeSelect = 0x00000436

kEdsPropID_Evf_OutputDevice = 0x00000500
kEdsPropID_Evf_Mode = 0x00000501
kEdsPropID_Evf_DepthOfFieldPreview = 0x00000504
kEdsPropID_Evf_Zoom = 0x00000507
kEdsPropID_Evf_ZoomPosition = 0x00000508
kEdsPropID_Evf_AFMode = 0x0000050E
kEdsPropID_Evf_CoordinateSystem = 0x00000540

# --- Constants: enumerated values --------------------------------------------
kEdsEvfOutputDevice_TFT = 1
kEdsEvfOutputDevice_PC = 2
kEdsEvfOutputDevice_PC_Small = 8

kEdsSaveTo_Camera = 1
kEdsSaveTo_Host = 2
kEdsSaveTo_Both = 3

kEdsAccess_Read = 0
kEdsAccess_Write = 1
kEdsAccess_ReadWrite = 2

kEdsFileCreateDisposition_CreateNew = 0
kEdsFileCreateDisposition_CreateAlways = 1
kEdsFileCreateDisposition_OpenExisting = 2
kEdsFileCreateDisposition_OpenAlways = 3

# --- Constants: camera commands ----------------------------------------------
kEdsCameraCommand_TakePicture = 0x00000000
kEdsCameraCommand_PressShutterButton = 0x00000004
kEdsCameraCommand_DriveLensEvf = 0x00000103

kEdsCameraCommand_ShutterButton_OFF = 0x00000000
kEdsCameraCommand_ShutterButton_Halfway = 0x00000001
kEdsCameraCommand_ShutterButton_Completely = 0x00000003
kEdsCameraCommand_ShutterButton_Halfway_NonAF = 0x00010001
kEdsCameraCommand_ShutterButton_Completely_NonAF = 0x00010003

#: Focus drive steps, near and far, from finest (1) to coarsest (3).
#:
#: A single step can be smaller than the frame-to-frame sensor noise -- measured
#: on an RF85mm F2 MACRO IS STM, where even the coarsest step is invisible on
#: its own. Callers wanting a perceptible move must send several.
kEdsEvfDriveLens_Near = {1: 0x00000001, 2: 0x00000002, 3: 0x00000003}
kEdsEvfDriveLens_Far = {1: 0x00008001, 2: 0x00008002, 3: 0x00008003}

# --- Constants: image decoding -----------------------------------------------
# EDSDK can decode a camera file to RGB itself, which removes any need for a
# separate RAW library. RGB16 is the one that matters for film: inverting a
# colour negative stretches a compressed range hard, and 8 bits band visibly.
kEdsImageSrc_FullView = 0
kEdsImageSrc_Thumbnail = 1
kEdsImageSrc_Preview = 2
kEdsImageSrc_RAWThumbnail = 3
kEdsImageSrc_RAWFullView = 4

kEdsTargetImageType_Unknown = 0x00000000
kEdsTargetImageType_Jpeg = 0x00000001
kEdsTargetImageType_TIFF = 0x00000007
kEdsTargetImageType_TIFF16 = 0x00000008
kEdsTargetImageType_RGB = 0x00000009
kEdsTargetImageType_RGB16 = 0x0000000A
kEdsTargetImageType_DIB = 0x0000000B

# --- Constants: events -------------------------------------------------------
kEdsObjectEvent_All = 0x00000200
kEdsObjectEvent_DirItemCreated = 0x00000204
kEdsObjectEvent_DirItemRequestTransfer = 0x00000208

kEdsStateEvent_All = 0x00000300
kEdsStateEvent_Shutdown = 0x00000301
kEdsStateEvent_WillSoonShutDown = 0x00000303

kEdsPropertyEvent_All = 0x00000100

EDS_MAX_NAME = 256


# --- Structures --------------------------------------------------------------
class EdsPropertyDesc(ctypes.Structure):
    """Values a property will currently accept, plus its access level.

    ``numElements`` of 0 means the property cannot be set right now. That is the
    camera telling us a capability is unavailable in its current state, which is
    exactly what capability detection should key off rather than a model name.
    """

    _fields_ = [
        ("form", c_int32),
        ("access", c_int32),
        ("numElements", c_int32),
        ("propDesc", c_int32 * 128),
    ]


class EdsDirectoryItemInfo(ctypes.Structure):
    """Metadata for one file on the camera."""

    _fields_ = [
        ("size", c_uint64),
        ("isFolder", c_int32),
        ("groupID", c_uint32),
        ("option", c_uint32),
        ("szFileName", c_char * EDS_MAX_NAME),
        ("format", c_uint32),
        ("dateTime", c_uint32),
    ]


class EdsPoint(ctypes.Structure):
    _fields_ = [("x", c_int32), ("y", c_int32)]


class EdsSize(ctypes.Structure):
    _fields_ = [("width", c_int32), ("height", c_int32)]


class EdsRect(ctypes.Structure):
    _fields_ = [("point", EdsPoint), ("size", EdsSize)]


class EdsImageInfo(ctypes.Structure):
    """Dimensions and bit depth of a decoded image.

    ``effectiveRect`` is the part of the frame that carries picture, which is
    not always the whole sensor readout -- crop to it rather than assuming
    width x height.
    """

    _fields_ = [
        ("width", c_uint32),
        ("height", c_uint32),
        ("numOfComponents", c_uint32),
        ("componentDepth", c_uint32),
        ("effectiveRect", EdsRect),
        ("reserved1", c_uint32),
        ("reserved2", c_uint32),
    ]


class EdsCapacity(ctypes.Structure):
    """Free space we claim to have, when the host is the save destination.

    The camera refuses to shoot unless it believes the destination has room, so
    this must be set after choosing host storage even though the host's real
    free space is not what the camera is describing.
    """

    _fields_ = [
        ("numberOfFreeClusters", c_int32),
        ("bytesPerSector", c_int32),
        ("reset", c_int32),
    ]


# --- Callback types ----------------------------------------------------------
# EDSCALLBACK is __stdcall, so WINFUNCTYPE. Keep references to any instance you
# register alive for as long as the SDK holds it: if Python garbage-collects the
# trampoline, the SDK calls freed memory and the process dies without a
# traceback.
EdsObjectEventHandler = ctypes.WINFUNCTYPE(c_uint32, c_uint32, c_void_p, c_void_p)
EdsPropertyEventHandler = ctypes.WINFUNCTYPE(c_uint32, c_uint32, c_uint32, c_uint32, c_void_p)
EdsStateEventHandler = ctypes.WINFUNCTYPE(c_uint32, c_uint32, c_uint32, c_void_p)


def _declare(dll: ctypes.WinDLL) -> None:
    """Declare argtypes and restype for every function the project calls."""
    err = c_uint32
    ref = EdsBaseRef

    # Lifecycle
    dll.EdsInitializeSDK.argtypes = []
    dll.EdsInitializeSDK.restype = err
    dll.EdsTerminateSDK.argtypes = []
    dll.EdsTerminateSDK.restype = err

    # Reference counting. These return the new count, not an error code.
    dll.EdsRetain.argtypes = [ref]
    dll.EdsRetain.restype = c_uint32
    dll.EdsRelease.argtypes = [ref]
    dll.EdsRelease.restype = c_uint32

    # Enumeration and sessions
    dll.EdsGetCameraList.argtypes = [POINTER(ref)]
    dll.EdsGetCameraList.restype = err
    dll.EdsGetChildCount.argtypes = [ref, POINTER(c_uint32)]
    dll.EdsGetChildCount.restype = err
    dll.EdsGetChildAtIndex.argtypes = [ref, c_int32, POINTER(ref)]
    dll.EdsGetChildAtIndex.restype = err
    dll.EdsOpenSession.argtypes = [ref]
    dll.EdsOpenSession.restype = err
    dll.EdsCloseSession.argtypes = [ref]
    dll.EdsCloseSession.restype = err

    # Properties
    dll.EdsGetPropertyData.argtypes = [ref, c_uint32, c_int32, c_uint32, c_void_p]
    dll.EdsGetPropertyData.restype = err
    dll.EdsSetPropertyData.argtypes = [ref, c_uint32, c_int32, c_uint32, c_void_p]
    dll.EdsSetPropertyData.restype = err
    dll.EdsGetPropertyDesc.argtypes = [ref, c_uint32, POINTER(EdsPropertyDesc)]
    dll.EdsGetPropertyDesc.restype = err

    # Commands
    dll.EdsSendCommand.argtypes = [ref, c_uint32, c_int32]
    dll.EdsSendCommand.restype = err
    dll.EdsSetCapacity.argtypes = [ref, EdsCapacity]
    dll.EdsSetCapacity.restype = err

    # Streams
    dll.EdsCreateMemoryStream.argtypes = [c_uint64, POINTER(ref)]
    dll.EdsCreateMemoryStream.restype = err
    dll.EdsCreateFileStream.argtypes = [POINTER(c_char), c_uint32, c_uint32, POINTER(ref)]
    dll.EdsCreateFileStream.restype = err
    dll.EdsGetPointer.argtypes = [ref, POINTER(c_void_p)]
    dll.EdsGetPointer.restype = err
    dll.EdsGetLength.argtypes = [ref, POINTER(c_uint64)]
    dll.EdsGetLength.restype = err

    # EdsCreateFileStreamEx takes a wide string on Windows, so it is the only
    # one of the two that can write to a path containing non-ASCII characters.
    if sys.platform == "win32":
        dll.EdsCreateFileStreamEx.argtypes = [ctypes.c_wchar_p, c_uint32, c_uint32, POINTER(ref)]
        dll.EdsCreateFileStreamEx.restype = err

    # Live view
    dll.EdsCreateEvfImageRef.argtypes = [ref, POINTER(ref)]
    dll.EdsCreateEvfImageRef.restype = err
    dll.EdsDownloadEvfImage.argtypes = [ref, ref]
    dll.EdsDownloadEvfImage.restype = err

    # Image decoding. These need EdsInitializeSDK but no camera session, so a
    # captured file can be decoded with the camera unplugged.
    dll.EdsCreateImageRef.argtypes = [ref, POINTER(ref)]
    dll.EdsCreateImageRef.restype = err
    dll.EdsGetImageInfo.argtypes = [ref, c_uint32, POINTER(EdsImageInfo)]
    dll.EdsGetImageInfo.restype = err
    # EdsRect and EdsSize are passed by value, not by pointer.
    dll.EdsGetImage.argtypes = [ref, c_uint32, c_uint32, EdsRect, EdsSize, ref]
    dll.EdsGetImage.restype = err

    # Capture download
    dll.EdsGetDirectoryItemInfo.argtypes = [ref, POINTER(EdsDirectoryItemInfo)]
    dll.EdsGetDirectoryItemInfo.restype = err
    dll.EdsDownload.argtypes = [ref, c_uint64, ref]
    dll.EdsDownload.restype = err
    dll.EdsDownloadComplete.argtypes = [ref]
    dll.EdsDownloadComplete.restype = err
    dll.EdsDownloadCancel.argtypes = [ref]
    dll.EdsDownloadCancel.restype = err
    dll.EdsDeleteDirectoryItem.argtypes = [ref]
    dll.EdsDeleteDirectoryItem.restype = err

    # Event handlers
    dll.EdsSetObjectEventHandler.argtypes = [ref, c_uint32, EdsObjectEventHandler, c_void_p]
    dll.EdsSetObjectEventHandler.restype = err
    dll.EdsSetPropertyEventHandler.argtypes = [ref, c_uint32, EdsPropertyEventHandler, c_void_p]
    dll.EdsSetPropertyEventHandler.restype = err
    dll.EdsSetCameraStateEventHandler.argtypes = [ref, c_uint32, EdsStateEventHandler, c_void_p]
    dll.EdsSetCameraStateEventHandler.restype = err


def _library_name() -> str:
    if sys.platform == "win32":
        return "EDSDK.dll"
    if sys.platform == "darwin":
        return "EDSDK.framework/EDSDK"
    return "libEDSDK.so"


def load_edsdk(library_dir: Path | str, library_path: Path | str | None = None) -> ctypes.CDLL:
    """Load the EDSDK shared library and declare every signature.

    Args:
        library_dir: Directory holding the SDK's 64-bit shared library.
        library_path: Full path to the library, when it is not the expected
            default name inside ``library_dir``.

    Returns:
        The loaded library, with argtypes and restype set on every function.

    Raises:
        SdkNotFoundError: If the library is missing or will not load. Every
            realistic cause is user-fixable, so the message names them rather
            than surfacing the loader's own text alone.
    """
    directory = Path(library_dir).expanduser()
    path = Path(library_path).expanduser() if library_path else directory / _library_name()

    if not path.is_file():
        raise SdkNotFoundError(
            f"EDSDK library not found at {path}\n"
            "\n"
            "This project cannot ship Canon's SDK; you obtain your own copy.\n"
            "  1. Register with Canon's Developer Community and request EDSDK.\n"
            "  2. Unpack it outside the repository, or into ./edsdk_sdk/.\n"
            "  3. Point 'edsdk.library_dir' in config.yaml at the 64-bit library\n"
            "     directory (the 64-bit build, not the 32-bit one).\n"
        )

    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise SdkNotFoundError(
            "This is 32-bit Python. Use 64-bit Python with the 64-bit EDSDK "
            "libraries; mixing the two fails with an error that never mentions "
            "bitness."
        )

    try:
        if sys.platform == "win32":
            # The SDK library loads its siblings (EdsImage.dll) from its own
            # directory. Without this the load fails naming a different file,
            # which sends you looking in the wrong place.
            os.add_dll_directory(str(directory))
            dll = ctypes.WinDLL(str(path))
        else:
            dll = ctypes.CDLL(str(path))
    except OSError as exc:
        raise SdkNotFoundError(
            f"Could not load {path}\n"
            f"  {exc}\n"
            "\n"
            "Most likely causes:\n"
            "  - 32-bit SDK libraries with 64-bit Python, or the reverse.\n"
            "  - A partial unpack: the main library needs its siblings beside it.\n"
        ) from exc

    _declare(dll)
    return dll
