"""EDSDK error codes, translated into causes a person can act on.

EDSDK reports failure by return value rather than by exception, so every call
site checks its code and raises through :func:`check`. A code that goes
unchecked becomes a mystery several steps later, when some unrelated call fails
because the camera was never in the state we assumed.

The names below are Canon's constant names with the ``EDS_ERR_`` prefix
stripped. The plain-language causes are ours.
"""

from __future__ import annotations

EDS_ERR_OK = 0x00000000
EDS_ERR_OBJECT_NOTREADY = 0x0000A102
EDS_ERR_DEVICE_BUSY = 0x00000081
EDS_ERR_PTP_DEVICE_BUSY = 0x00002019

#: Canon's constant names, keyed by code.
ERROR_NAMES: dict[int, str] = {
    0x00000000: "OK",
    0x00000001: "UNIMPLEMENTED",
    0x00000002: "INTERNAL_ERROR",
    0x00000003: "MEM_ALLOC_FAILED",
    0x00000004: "MEM_FREE_FAILED",
    0x00000005: "OPERATION_CANCELLED",
    0x00000006: "INCOMPATIBLE_VERSION",
    0x00000007: "NOT_SUPPORTED",
    0x00000008: "UNEXPECTED_EXCEPTION",
    0x00000009: "PROTECTION_VIOLATION",
    0x0000000A: "MISSING_SUBCOMPONENT",
    0x0000000B: "SELECTION_UNAVAILABLE",
    0x00000020: "FILE_IO_ERROR",
    0x00000021: "FILE_TOO_MANY_OPEN",
    0x00000022: "FILE_NOT_FOUND",
    0x00000023: "FILE_OPEN_ERROR",
    0x00000024: "FILE_CLOSE_ERROR",
    0x00000025: "FILE_SEEK_ERROR",
    0x00000026: "FILE_TELL_ERROR",
    0x00000027: "FILE_READ_ERROR",
    0x00000028: "FILE_WRITE_ERROR",
    0x00000029: "FILE_PERMISSION_ERROR",
    0x0000002A: "FILE_DISK_FULL_ERROR",
    0x0000002B: "FILE_ALREADY_EXISTS",
    0x0000002C: "FILE_FORMAT_UNRECOGNIZED",
    0x0000002D: "FILE_DATA_CORRUPT",
    0x0000002E: "FILE_NAMING_NA",
    0x00000040: "DIR_NOT_FOUND",
    0x00000041: "DIR_IO_ERROR",
    0x00000042: "DIR_ENTRY_NOT_FOUND",
    0x00000043: "DIR_ENTRY_EXISTS",
    0x00000044: "DIR_NOT_EMPTY",
    0x00000050: "PROPERTIES_UNAVAILABLE",
    0x00000051: "PROPERTIES_MISMATCH",
    0x00000053: "PROPERTIES_NOT_LOADED",
    0x00000060: "INVALID_PARAMETER",
    0x00000061: "INVALID_HANDLE",
    0x00000062: "INVALID_POINTER",
    0x00000063: "INVALID_INDEX",
    0x00000064: "INVALID_LENGTH",
    0x00000065: "INVALID_FN_POINTER",
    0x00000066: "INVALID_SORT_FN",
    0x00000080: "DEVICE_NOT_FOUND",
    0x00000081: "DEVICE_BUSY",
    0x00000082: "DEVICE_INVALID",
    0x00000083: "DEVICE_EMERGENCY",
    0x00000084: "DEVICE_MEMORY_FULL",
    0x00000085: "DEVICE_INTERNAL_ERROR",
    0x00000086: "DEVICE_INVALID_PARAMETER",
    0x00000087: "DEVICE_NO_DISK",
    0x00000088: "DEVICE_DISK_ERROR",
    0x00000089: "DEVICE_CF_GATE_CHANGED",
    0x0000008A: "DEVICE_DIAL_CHANGED",
    0x0000008B: "DEVICE_NOT_INSTALLED",
    0x0000008C: "DEVICE_STAY_AWAKE",
    0x0000008D: "DEVICE_NOT_RELEASED",
    0x000000A0: "STREAM_IO_ERROR",
    0x000000A1: "STREAM_NOT_OPEN",
    0x000000A2: "STREAM_ALREADY_OPEN",
    0x000000A3: "STREAM_OPEN_ERROR",
    0x000000A4: "STREAM_CLOSE_ERROR",
    0x000000A5: "STREAM_SEEK_ERROR",
    0x000000A6: "STREAM_TELL_ERROR",
    0x000000A7: "STREAM_READ_ERROR",
    0x000000A8: "STREAM_WRITE_ERROR",
    0x000000A9: "STREAM_PERMISSION_ERROR",
    0x000000AA: "STREAM_COULDNT_BEGIN_THREAD",
    0x000000AB: "STREAM_BAD_OPTIONS",
    0x000000AC: "STREAM_END_OF_STREAM",
    0x000000C0: "COMM_PORT_IS_IN_USE",
    0x000000C1: "COMM_DISCONNECTED",
    0x000000C2: "COMM_DEVICE_INCOMPATIBLE",
    0x000000C3: "COMM_BUFFER_FULL",
    0x000000C4: "COMM_USB_BUS_ERR",
    0x000000D0: "USB_DEVICE_LOCK_ERROR",
    0x000000D1: "USB_DEVICE_UNLOCK_ERROR",
    0x000000E0: "STI_UNKNOWN_ERROR",
    0x000000E1: "STI_INTERNAL_ERROR",
    0x000000E2: "STI_DEVICE_CREATE_ERROR",
    0x000000E3: "STI_DEVICE_RELEASE_ERROR",
    0x000000E4: "DEVICE_NOT_LAUNCHED",
    0x000000F0: "ENUM_NA",
    0x000000F1: "INVALID_FN_CALL",
    0x000000F2: "HANDLE_NOT_FOUND",
    0x000000F3: "INVALID_ID",
    0x000000F4: "WAIT_TIMEOUT_ERROR",
    0x00002003: "SESSION_NOT_OPEN",
    0x00002004: "INVALID_TRANSACTIONID",
    0x00002007: "INCOMPLETE_TRANSFER",
    0x00002008: "INVALID_STRAGEID",
    0x0000200A: "DEVICEPROP_NOT_SUPPORTED",
    0x0000200B: "INVALID_OBJECTFORMATCODE",
    0x00002011: "SELF_TEST_FAILED",
    0x00002012: "PARTIAL_DELETION",
    0x00002014: "SPECIFICATION_BY_FORMAT_UNSUPPORTED",
    0x00002015: "NO_VALID_OBJECTINFO",
    0x00002016: "INVALID_CODE_FORMAT",
    0x00002017: "UNKNOWN_VENDOR_CODE",
    0x00002018: "CAPTURE_ALREADY_TERMINATED",
    0x00002019: "PTP_DEVICE_BUSY",
    0x0000201A: "INVALID_PARENTOBJECT",
    0x0000201B: "INVALID_DEVICEPROP_FORMAT",
    0x0000201C: "INVALID_DEVICEPROP_VALUE",
    0x0000201E: "SESSION_ALREADY_OPEN",
    0x0000201F: "TRANSACTION_CANCELLED",
    0x00002020: "SPECIFICATION_OF_DESTINATION_UNSUPPORTED",
    0x00002021: "NOT_CAMERA_SUPPORT_SDK_VERSION",
    0x00008D01: "TAKE_PICTURE_AF_NG",
    0x00008D02: "TAKE_PICTURE_RESERVED",
    0x00008D03: "TAKE_PICTURE_MIRROR_UP_NG",
    0x00008D04: "TAKE_PICTURE_SENSOR_CLEANING_NG",
    0x00008D05: "TAKE_PICTURE_SILENCE_NG",
    0x00008D06: "TAKE_PICTURE_NO_CARD_NG",
    0x00008D07: "TAKE_PICTURE_CARD_NG",
    0x00008D08: "TAKE_PICTURE_CARD_PROTECT_NG",
    0x00008D09: "TAKE_PICTURE_MOVIE_CROP_NG",
    0x00008D0A: "TAKE_PICTURE_STROBO_CHARGE_NG",
    0x00008D0B: "TAKE_PICTURE_NO_LENS_NG",
    0x00008D0C: "TAKE_PICTURE_SPECIAL_MOVIE_MODE_NG",
    0x00008D0D: "TAKE_PICTURE_LV_REL_PROHIBIT_MODE_NG",
    0x00008D0E: "TAKE_PICTURE_MOVIE_MODE_NG",
    0x00008D0F: "TAKE_PICTURE_RETRUCTED_LENS_NG",
    0x0000A001: "UNKNOWN_COMMAND",
    0x0000A005: "OPERATION_REFUSED",
    0x0000A006: "LENS_COVER_CLOSE",
    0x0000A101: "LOW_BATTERY",
    0x0000A102: "OBJECT_NOTREADY",
    0x0000A104: "CANNOT_MAKE_OBJECT",
    0x0000A106: "MEMORYSTATUS_NOTREADY",
}

#: Real-world causes, for the codes a user actually meets. A code that names
#: the physical problem is diagnosable; a bare hex value sends them to a search
#: engine.
ERROR_CAUSES: dict[int, str] = {
    0x00000007: "The camera does not support this operation.",
    0x0000000B: "That setting is not selectable in the camera's current mode.",
    0x0000002A: "The disk is full.",
    0x00000061: "Stale camera handle -- the session was probably closed underneath us.",
    0x00000080: "No camera found. Check the USB cable, and that the camera is on and awake.",
    0x00000081: (
        "The camera reported itself busy. This is usually transient -- live view "
        "keeps the body busy for much of each frame -- and is retried "
        "automatically. If it persists, close EOS Utility or Lightroom tethering."
    ),
    0x00000083: "The camera reported an emergency. Power-cycle it.",
    0x00000084: "The camera's card is full.",
    0x00000087: "No card in the camera.",
    0x000000C0: "The USB port is already in use -- another program holds the camera.",
    0x000000C1: "The camera disconnected. Suspect the USB cable or a sleeping body.",
    0x000000C2: "This camera is not compatible with this EDSDK version.",
    0x000000C4: "USB bus error. Try a different port, and avoid hubs.",
    0x00002003: "No session is open with the camera.",
    0x0000200A: "This camera body does not expose that property.",
    0x00002019: "The camera is busy with another PTP transaction.",
    0x0000201E: "A session is already open, usually left by a process that crashed.",
    0x00002021: "This EDSDK version does not support this camera body.",
    0x00008D01: "Autofocus could not lock. Focus manually, or aim at more contrast.",
    0x00008D06: "No card in the camera, so it refused to shoot.",
    0x00008D07: "Card error. The camera refused to shoot.",
    0x00008D08: "The card is write-protected.",
    0x00008D0B: "No lens attached, so the camera refused to shoot.",
    0x00008D0E: "The camera is in movie mode and refused to take a still.",
    0x0000A005: "The camera refused the operation in its current state.",
    0x0000A006: "The lens cover is closed.",
    0x0000A101: "Camera battery is too low.",
    0x0000A102: "Not ready yet. Normal for live view; retry.",
}


class EdsError(RuntimeError):
    """An EDSDK call returned something other than ``EDS_ERR_OK``."""

    def __init__(self, call: str, code: int) -> None:
        self.call = call
        self.code = code
        self.name = ERROR_NAMES.get(code, "UNKNOWN")
        message = f"{call} failed: 0x{code:08X} {self.name}"
        cause = ERROR_CAUSES.get(code)
        if cause:
            message += f"\n  {cause}"
        super().__init__(message)


class SdkNotFoundError(RuntimeError):
    """The EDSDK shared library could not be found or loaded.

    Raised with setup guidance rather than a bare OSError, because every cause
    is something the user can fix and none of them is obvious from the
    underlying loader message.
    """


def check(call: str, code: int) -> None:
    """Raise :class:`EdsError` unless ``code`` is ``EDS_ERR_OK``."""
    if code != EDS_ERR_OK:
        raise EdsError(call, code)


def error_name(code: int) -> str:
    """Canon's constant name for a code, or ``UNKNOWN_0x...``."""
    return ERROR_NAMES.get(code, f"UNKNOWN_0x{code:08X}")
