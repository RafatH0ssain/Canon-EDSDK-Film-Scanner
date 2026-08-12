"""Where things live, which is not the same once the app is a bundle.

Running from a checkout, everything hangs off the repository root: the config
beside the source, captures in ``captures/``, the web assets in the package.
A packaged app has neither a repository nor a writable install directory --
macOS mounts an ``.app`` read-only from a quarantined download, and Windows
puts binaries somewhere a normal user should not be writing to.

So two roots, not one:

``resource_dir()``
    Read-only things shipped inside the bundle -- the browser UI, the example
    config. PyInstaller unpacks these to a temporary directory it names in
    ``sys._MEIPASS``, which changes every launch. Never write here.

``user_data_dir()``
    Writable things belonging to the person running it -- their ``config.yaml``
    and their scans. Somewhere they can find without being told twice, because
    they will need to open it to point the app at their own copy of EDSDK.

From a checkout both are the repository root, so nothing about developing
changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: The repository root: this file is ``<root>/src/cefs/paths.py``.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Folder name used under the user's Documents when packaged.
APP_DIR_NAME = "Canon EDSDK Film Scanner"


def is_frozen() -> bool:
    """Whether this is running from a PyInstaller bundle rather than a checkout."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_dir() -> Path:
    """Root for read-only files shipped with the app."""
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return REPO_ROOT


def user_data_dir() -> Path:
    """Root for the user's own config and captures.

    Documents rather than Application Support or AppData: this is a scanning
    tool, the captures are the point, and a folder nobody can find is a folder
    nobody puts their negatives in. Same shape on Windows and macOS.
    """
    if is_frozen():
        return Path.home() / "Documents" / APP_DIR_NAME
    return REPO_ROOT


def static_dir() -> Path:
    """The browser UI's files."""
    if is_frozen():
        return resource_dir() / "cefs" / "app" / "static"
    return Path(__file__).resolve().parent / "app" / "static"


def example_config_path() -> Path:
    """The bundled ``config.example.yaml``, used to seed a first run."""
    return resource_dir() / "config.example.yaml"
