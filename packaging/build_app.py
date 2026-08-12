"""Build the double-clickable app.

    python packaging/build_app.py

Produces ``dist/Canon EDSDK Film Scanner.app`` on macOS and
``dist/Canon EDSDK Film Scanner/`` with an ``.exe`` inside on Windows.

**Canon's SDK is never included** -- it is licensed per developer and may not
be redistributed. The build refuses to finish if it finds any of it in the
output, because that is the one mistake here that cannot be taken back once
uploaded.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "packaging" / "cefs.spec"
DIST = REPO / "dist"
APP_NAME = "Canon EDSDK Film Scanner"

#: Canon's actual shipped artefacts, by exact filename.
#:
#: Matched precisely rather than by substring. The app is *called* "Canon EDSDK
#: Film Scanner", so its own executable contains the string "EDSDK" -- a
#: substring check flags the binary it just built and blocks every release,
#: which trains you to ignore the guard. A guard nobody trusts is worse than
#: none.
FORBIDDEN_FILES = {
    "edsdk.dll",
    "edsimage.dll",
    "dpp.dll",
    "edsdk.h",
    "edsdktypes.h",
    "edsdkerrors.h",
}

#: Canon's bundles and frameworks, matched anywhere in the path.
FORBIDDEN_DIRS = (
    "EDSDK.framework",
    "DPP.framework",
    "DppCore.bundle",
    "EdsImage.bundle",
    "ipCodec.bundle",
    "ipCommonProp.bundle",
    "ipMWGPolicy.bundle",
    "ipDSPolicy.bundle",
    "CHHLLite.bundle",
)


#: Native libraries THIRD-PARTY-NOTICES.md accounts for.
#:
#: Checked after every build. A dependency that quietly starts bundling
#: something new shows up here as a failure rather than as an undocumented
#: library in someone else's download.
NOTICED_LIBRARIES = {
    "libxau", "libavif", "libcrypto", "libde265", "libheif", "libjasper",
    "libjpeg", "liblcms2", "liblzma", "libmpdec", "libopenjp2", "libraw_r",
    "libsharpyuv", "libssl", "libtiff", "libwebp", "libwebpdemux",
    "libwebpmux", "libx265", "libxcb", "libz", "libpython", "python",
}


def _library_stem(name: str) -> str:
    """'libwebpmux.3.dylib' -> 'libwebpmux'."""
    return name.split(".")[0].lower()


def audit_notices(root: Path) -> list[str]:
    """Native libraries in the build that the notices do not mention."""
    seen = set()
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix not in (".dylib", ".so", ".dll"):
            continue
        # Python extension modules, not standalone libraries: the stdlib's are
        # covered by the PSF licence and a package's own are covered by that
        # package. What needs attribution here is the shared libraries a wheel
        # carries alongside them.
        if ".cpython-" in path.name or ".abi3." in path.name:
            continue
        stem = _library_stem(path.name)
        if stem and stem not in NOTICED_LIBRARIES and not stem.startswith("_"):
            seen.add(path.name)
    return sorted(seen)


def check_toolchain() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit(
            "PyInstaller is not installed.\n"
            "  pip install pyinstaller\n"
            "It is a build tool, so it is deliberately not in requirements.txt."
        )


def audit_output(root: Path) -> list[Path]:
    """Find anything of Canon's that ended up in the build."""
    offenders = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.lower() in FORBIDDEN_FILES:
            offenders.append(path)
            continue
        if any(part in FORBIDDEN_DIRS for part in path.parts):
            offenders.append(path)
            continue
        # Canon's reference PDFs, whatever they are named.
        if path.suffix.lower() == ".pdf" and "edsdk" in path.name.lower():
            offenders.append(path)
    return offenders


def main() -> int:
    check_toolchain()

    for stale in (DIST, REPO / "build"):
        if stale.exists():
            shutil.rmtree(stale)

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)],
        cwd=REPO,
    )
    if result.returncode != 0:
        return result.returncode

    built = DIST / (f"{APP_NAME}.app" if sys.platform == "darwin" else APP_NAME)
    if not built.exists():
        sys.exit(f"Build reported success but {built} is missing.")

    offenders = audit_output(DIST)
    if offenders:
        for path in offenders[:20]:
            print(f"  {path.relative_to(DIST)}", file=sys.stderr)
        sys.exit(
            f"\nRefusing to finish: {len(offenders)} file(s) above look like Canon's "
            "SDK.\nIt may not be redistributed. Remove them from the spec's datas "
            "and rebuild."
        )

    # Skip symlinks. PyInstaller points Contents/Resources at the files in
    # Contents/Frameworks, and following those counts the same bytes twice --
    # it reported 428 MB for a bundle that occupies 169 MB on disk.
    # Compliance material has to be *in* the bundle, not just in the repo.
    # A build that quietly stops shipping it is a build that cannot legally be
    # handed to anyone, and nothing else would notice.
    required = ["THIRD-PARTY-NOTICES.md", "pillow-heif-BUNDLED-LICENSES.txt"]
    missing = [name for name in required if not list(built.rglob(name))]
    if missing:
        sys.exit(
            f"\nRefusing to finish: {', '.join(missing)} not in the bundle.\n"
            "The GPL and LGPL components here cannot be redistributed without them."
        )

    undocumented = audit_notices(DIST)
    if undocumented:
        for name in undocumented[:20]:
            print(f"  {name}", file=sys.stderr)
        sys.exit(
            f"\nRefusing to finish: {len(undocumented)} native librar(y/ies) above are "
            "not in\nTHIRD-PARTY-NOTICES.md. Check their licences, add them, and rebuild."
        )

    size_mb = (
        sum(
            f.stat().st_size
            for f in built.rglob("*")
            if f.is_file() and not f.is_symlink()
        )
        / 1e6
    )
    print(f"\nBuilt {built}  ({size_mb:.0f} MB)")
    print("No Canon SDK files in the output.")
    if sys.platform == "darwin":
        print(
            "\nUnsigned, so Gatekeeper will block it on another Mac. To share it:\n"
            "  codesign --deep --force --sign <Developer ID> "
            f'"{built}"\n'
            "  then notarise with notarytool. Locally, right-click > Open."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
