"""Configuration loading: defaults, file, then environment."""

from __future__ import annotations

import pytest

from cefs.config import Config, load_config


def test_defaults_select_the_mock():
    """A fresh clone must run with no SDK, no camera and no config file."""
    config = Config()
    assert config.camera.use_mock is True
    assert config.edsdk.library_dir == ""


def test_loads_a_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "edsdk:\n  library_dir: 'C:/EDSDK/Dll'\ncapture:\n  settle_delay_s: 2.5\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.edsdk.library_dir == "C:/EDSDK/Dll"
    assert config.capture.settle_delay_s == 2.5


def test_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_unknown_section_is_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("nonsense:\n  a: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown section"):
        load_config(path)


def test_unknown_setting_is_rejected(tmp_path):
    """Strict on purpose: a silently ignored typo is worse to debug."""
    path = tmp_path / "config.yaml"
    path.write_text("capture:\n  settle_delay: 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown setting"):
        load_config(path)


def test_env_overrides_file(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("server:\n  port: 8000\n", encoding="utf-8")
    monkeypatch.setenv("CEFS_SERVER_PORT", "9123")
    assert load_config(path).server.port == 9123


def test_env_booleans_are_parsed_not_truthy(monkeypatch, tmp_path):
    """bool('false') is True, which would silently invert this setting."""
    path = tmp_path / "config.yaml"
    path.write_text("camera:\n  use_mock: true\n", encoding="utf-8")
    monkeypatch.setenv("CEFS_CAMERA_USE_MOCK", "false")
    assert load_config(path).camera.use_mock is False


def test_output_dir_resolves_relative_to_the_repo():
    config = Config()
    assert config.capture.resolved_output_dir().is_absolute()


def test_positive_output_defaults_are_valid():
    """The defaults must be values develop() will accept, not merely strings."""
    from cefs.processing.develop import OutputOptions

    capture = Config().capture
    OutputOptions(
        format=capture.positive_format,
        tiff_compression=capture.tiff_compression,
        jpeg_quality=capture.jpeg_quality,
    ).validate()


def test_the_example_config_loads(tmp_path):
    """config.example.yaml is what a new user copies; it must actually parse.

    Loading is strict about unknown settings, so this also catches a field
    renamed in code and left stale in the example.
    """
    from cefs.config import REPO_ROOT

    config = load_config(REPO_ROOT / "config.example.yaml")
    assert config.capture.positive_format in ("auto", "tiff", "jpeg")
