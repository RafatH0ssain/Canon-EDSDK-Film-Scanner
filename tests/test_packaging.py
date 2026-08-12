"""The build's refusal to ship Canon's SDK.

Canon licenses EDSDK per developer and forbids redistribution, so this guard
is the last thing standing between a build and an upload that cannot be taken
back. It gets tested like anything else load-bearing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from cefs.paths import REPO_ROOT


@pytest.fixture(scope="module")
def build_app():
    spec = importlib.util.spec_from_file_location(
        "build_app", REPO_ROOT / "packaging" / "build_app.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "relative",
    [
        "app/EDSDK.dll",
        "app/EdsImage.dll",
        "app/EDSDK.h",
        "app/EDSDKTypes.h",
        "app/EDSDK.framework/Versions/A/EDSDK",
        "app/DPP.framework/DPP",
        "app/Contents/Resources/DppCore.bundle/anything.bin",
        "app/EDSDK_API_EN.pdf",
    ],
)
def test_canon_artefacts_are_caught(build_app, tmp_path, relative):
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"")
    assert build_app.audit_output(tmp_path), f"{relative} slipped through"


@pytest.mark.parametrize(
    "relative",
    [
        # The app's own executable. It is *called* "Canon EDSDK Film Scanner",
        # so a substring match on "EDSDK" flags the binary the build just
        # produced -- which blocked a real build and would train anyone to
        # ignore the guard entirely.
        "Canon EDSDK Film Scanner.app/Contents/MacOS/Canon EDSDK Film Scanner",
        "Canon EDSDK Film Scanner/Canon EDSDK Film Scanner",
        # Our own source, which legitimately carries the name.
        "app/cefs/edsdk/bindings.py",
        "app/cefs/edsdk/camera.py",
        # Ordinary payload.
        "app/cefs/app/static/index.html",
        "app/config.example.yaml",
    ],
)
def test_our_own_files_are_not_mistaken_for_canons(build_app, tmp_path, relative):
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"")
    assert build_app.audit_output(tmp_path) == [], f"{relative} wrongly flagged"


def test_a_clean_tree_reports_nothing(build_app, tmp_path):
    (tmp_path / "empty").mkdir()
    assert build_app.audit_output(tmp_path) == []


def test_the_spec_never_globs_the_sdk_directory(build_app):
    """The spec lists data files one by one, on purpose.

    A directory glob over the repo root would sweep in edsdk_sdk/ on any
    machine that has it -- which is every machine that can drive a camera.
    """
    spec = (REPO_ROOT / "packaging" / "cefs.spec").read_text()
    assert "edsdk_sdk" not in spec
    assert "config.example.yaml" in spec, "the seeded config must stay bundled"
    assert "cefs/app/static" in spec, "the browser UI must stay bundled"
