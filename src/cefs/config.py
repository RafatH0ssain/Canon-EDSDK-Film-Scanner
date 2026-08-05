"""Configuration loading.

Values come from three places, in increasing order of priority:

1. Built-in defaults (below) -- so the app runs against the mock with no setup.
2. ``config.yaml`` in the repository root -- git-ignored, holds your SDK path.
3. Environment variables named ``CEFS_<SECTION>_<KEY>`` -- highest priority.

The env-var layer exists so tests can point the app at the mock backend without
writing a file, and so you can try a different SDK directory for one run without
editing anything.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from functools import cache
from pathlib import Path
from typing import Any, get_type_hints

import yaml

# The repository root: this file is <root>/src/cefs/config.py.
REPO_ROOT = Path(__file__).resolve().parents[2]

_ENV_PREFIX = "CEFS"


def _resolve(value: str) -> Path | None:
    """Absolute path for a config value, or None when unset."""
    if not value:
        return None
    p = Path(value).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p)


@dataclass
class EdsdkConfig:
    """Where the user's own copy of Canon's SDK lives."""

    # Deliberately not a real path: a committed default that happened to exist
    # on someone's machine would silently load the wrong SDK.
    library_dir: str = ""
    library_path: str = ""

    def resolved_library_dir(self) -> Path | None:
        """Absolute SDK directory, resolving relative paths against the repo.

        Relative paths are convenient when the SDK sits beside the code, but
        resolving them against the working directory would make the app work
        from the repo root and fail everywhere else.
        """
        return _resolve(self.library_dir)

    def resolved_library_path(self) -> Path | None:
        return _resolve(self.library_path)


@dataclass
class LiveViewConfig:
    """The live-view frame pump."""

    # Caps how hard the pump polls. The measured ceiling on an EOS R7 is the
    # camera's own ~60 fps output, not the USB link, so a higher number here
    # buys nothing and only burns CPU.
    target_fps: int = 30


@dataclass
class CameraConfig:
    """Which backend to drive."""

    # Defaults to the mock so a fresh clone runs with no SDK and no camera.
    use_mock: bool = True


@dataclass
class CaptureConfig:
    """Shutter behaviour and where captured files land."""

    settle_delay_s: float = 1.5
    prefer_electronic_shutter: bool = True
    output_dir: str = "captures"
    delete_from_camera_after_download: bool = False

    # Write an inverted positive beside each capture, using the same settings
    # the preview shows. Never modifies or replaces the original.
    develop_positives: bool = True

    # How that positive is written. "auto" keeps the depth the source had:
    # 16-bit TIFF from RAW and HEIF, JPEG from JPEG. See
    # cefs.processing.develop.OutputOptions.
    positive_format: str = "auto"

    # Lossless whichever is chosen. Deflate, not LZW: measured on two real
    # 32 MP positives, LZW managed only 1.1-1.2x where deflate gave 2.4-2.6x.
    # LZW is also what OpenCV writes when told nothing, so it was never the
    # improvement it looked like.
    tiff_compression: str = "deflate"

    jpeg_quality: int = 95

    def resolved_output_dir(self) -> Path:
        """Absolute output directory, resolving relative paths against the repo."""
        p = Path(self.output_dir).expanduser()
        return p if p.is_absolute() else (REPO_ROOT / p)


@dataclass
class RollConfig:
    """How captures are named, and what is recorded about the roll.

    These are starting values. The UI edits them for the session -- the roll
    label and frame number in particular change constantly while scanning, and
    are not written back here.
    """

    #: See :mod:`cefs.naming` for the fields. Empty disables renaming entirely
    #: and leaves the camera's own filenames alone.
    template: str = "{roll}/{roll}_Frame{frame:02d}"

    roll: str = "Roll001"
    #: Next frame number. One per shutter release, not per file.
    frame: int = 1

    #: Written to the roll's sidecar. Free text.
    stock: str = ""
    developer: str = ""
    notes: str = ""
    date: str = ""

    #: Write roll.json beside the frames.
    sidecar: bool = True


@dataclass
class FilmConfig:
    """Which inversion pipeline the preview starts in."""

    mode: str = "bw"  # "bw" or "color"; both are first-class


@dataclass
class ServerConfig:
    """The local web UI."""

    # Loopback by default: this server can fire your shutter, so it should not
    # be reachable from a network you do not control.
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class Config:
    edsdk: EdsdkConfig = field(default_factory=EdsdkConfig)
    liveview: LiveViewConfig = field(default_factory=LiveViewConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    roll: RollConfig = field(default_factory=RollConfig)
    film: FilmConfig = field(default_factory=FilmConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


@cache
def _field_types(cls: type) -> dict[str, Any]:
    """Real types for a config dataclass's fields.

    ``dataclasses.fields()`` reports annotations as strings under
    ``from __future__ import annotations``, so comparing them to ``bool`` never
    matches and values silently stay strings.
    """
    return get_type_hints(cls)


def _coerce(value: Any, target_type: Any) -> Any:
    """Convert a raw YAML/env value to the declared field type.

    Environment variables arrive as strings, and ``bool("false")`` is ``True``.
    """
    if target_type is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if target_type is float:
        return float(value)
    if target_type is int:
        return int(value)
    if target_type is str:
        return str(value)
    return value


def _apply_mapping(section: Any, values: dict[str, Any], where: str) -> None:
    """Overlay a dict of values onto a config dataclass instance, in place."""
    known = _field_types(type(section))
    for key, value in values.items():
        if key not in known:
            raise ValueError(
                f"Unknown setting '{key}' in {where}. "
                f"Valid settings here: {', '.join(sorted(known))}."
            )
        setattr(section, key, _coerce(value, known[key]))


def _apply_env(config: Config) -> None:
    """Overlay ``CEFS_<SECTION>_<KEY>`` environment variables onto the config."""
    for section_field in fields(config):
        section = getattr(config, section_field.name)
        if not is_dataclass(section):
            continue
        types = _field_types(type(section))
        for f in fields(section):
            env_name = f"{_ENV_PREFIX}_{section_field.name}_{f.name}".upper()
            if env_name in os.environ:
                setattr(section, f.name, _coerce(os.environ[env_name], types[f.name]))


def load_config(path: Path | str | None = None) -> Config:
    """Load configuration from defaults, then ``config.yaml``, then the environment.

    Args:
        path: Explicit path to a YAML config file. If omitted, ``config.yaml``
            in the repository root is used when it exists. A missing default
            file is not an error: the defaults select the mock backend, so a
            fresh clone runs with no configuration at all.

    Raises:
        FileNotFoundError: If an explicit ``path`` was given but does not exist.
        ValueError: On an unknown section or setting. Strict on purpose -- a
            silently ignored typo in the SDK path is worse to debug than an
            error at startup.
    """
    config = Config()

    config_path = Path(path) if path is not None else REPO_ROOT / "config.yaml"
    if path is not None and not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{config_path} must contain a YAML mapping at the top level.")
        known_sections = {f.name for f in fields(config)}
        for section_name, values in raw.items():
            if section_name not in known_sections:
                raise ValueError(
                    f"Unknown section '{section_name}' in {config_path}. "
                    f"Valid sections: {', '.join(sorted(known_sections))}."
                )
            if values is None:
                continue
            if not isinstance(values, dict):
                raise ValueError(f"Section '{section_name}' in {config_path} must be a mapping.")
            _apply_mapping(getattr(config, section_name), values, f"{config_path} [{section_name}]")

    _apply_env(config)
    return config
