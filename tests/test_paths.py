"""Where the app looks for things, from a checkout and from a bundle.

The frozen half never runs during normal development, so it is the half most
likely to be wrong when someone finally double-clicks the app.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cefs import paths


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Pretend to be a PyInstaller bundle unpacked at tmp_path."""
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path), raising=False)
    return tmp_path


def test_a_checkout_uses_the_repository_root():
    assert not paths.is_frozen()
    assert paths.resource_dir() == paths.REPO_ROOT
    assert paths.user_data_dir() == paths.REPO_ROOT


def test_static_files_exist_where_a_checkout_expects_them():
    assert (paths.static_dir() / "index.html").is_file()


def test_a_bundle_reads_resources_from_the_unpack_dir(frozen):
    assert paths.is_frozen()
    assert paths.resource_dir() == frozen
    assert paths.static_dir() == frozen / "cefs" / "app" / "static"


def test_a_bundle_never_writes_into_itself(frozen):
    """Captures and config must not land in the bundle.

    macOS mounts a downloaded .app read-only, and on Windows the install
    directory is not somewhere a normal user should be writing. Either way the
    failure would arrive at the worst moment -- after a capture, holding a file
    it cannot save.
    """
    data = paths.user_data_dir()
    assert data != frozen
    assert frozen not in data.parents and data != frozen
    assert data == Path.home() / "Documents" / paths.APP_DIR_NAME


def test_config_and_captures_follow_the_user_data_root(frozen, monkeypatch):
    from cefs.config import Config

    monkeypatch.setattr(paths, "user_data_dir", lambda: frozen / "userdata")
    import cefs.config as config_module

    monkeypatch.setattr(config_module, "user_data_dir", lambda: frozen / "userdata")

    cfg = Config()
    assert cfg.capture.resolved_output_dir() == frozen / "userdata" / "captures"
