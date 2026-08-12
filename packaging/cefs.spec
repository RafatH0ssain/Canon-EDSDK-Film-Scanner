# PyInstaller spec for a double-clickable Canon EDSDK Film Scanner.
#
# Build with `python packaging/build_app.py`, not by invoking PyInstaller
# directly -- the script checks the toolchain first and reports what is
# missing, which PyInstaller does not.
#
# **Canon's SDK is deliberately not bundled.** It is licensed per developer and
# may not be redistributed, so the app ships without it and the user points
# `config.yaml` at their own copy. A build that swept the SDK in would be
# undistributable, so the datas below are listed explicitly rather than by
# directory glob -- an accidental include should be impossible, not unlikely.

import sys
from pathlib import Path

REPO = Path(SPECPATH).resolve().parent          # noqa: F821 - PyInstaller global
APP_NAME = "Canon EDSDK Film Scanner"

datas = [
    # The browser UI. Without this the app starts and serves nothing.
    (str(REPO / "src" / "cefs" / "app" / "static"), "cefs/app/static"),
    # Seeds the user's config on first launch, comments and all.
    (str(REPO / "config.example.yaml"), "."),
]

# Native extensions PyInstaller cannot always see through. rawpy carries
# LibRaw and pillow_heif carries libheif; both load their binaries at import,
# so a missed hook shows up as a working app that cannot open a RAW.
#
# OpenCV is deliberately absent: its wheel bundled a GPL FFmpeg (x264, x265)
# that nothing here called.
hiddenimports = [
    "rawpy",
    "pillow_heif",
    "tifffile",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

analysis = Analysis(                             # noqa: F821
    [str(REPO / "packaging" / "entry.py")],
    pathex=[str(REPO / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Nothing here is needed at runtime, and each drags in a lot.
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "PySide6", "PyQt5"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)                         # noqa: F821

exe = EXE(                                       # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,
    # Keep a console on Windows: the app prints where its folder is, and a
    # startup failure with no window is indistinguishable from nothing
    # happening at all.
    console=True,
)

collect = COLLECT(                               # noqa: F821
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(                                # noqa: F821
        collect,
        name=f"{APP_NAME}.app",
        icon=None,
        bundle_identifier="com.rafathossain.cefs",
        info_plist={
            "NSHighResolutionCapable": True,
            # It drives a camera over USB; macOS asks the user on first access.
            "NSCameraUsageDescription": "Controls a connected Canon camera.",
            "LSMinimumSystemVersion": "11.0",
        },
    )
