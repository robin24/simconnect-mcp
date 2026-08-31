"""Unit tests for the release version rewriter.

The release workflow runs this against the real repo, so a bug here ships a
wrong version number to PyPI. Tests operate on a temp copy of the two files
it touches.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    """Load scripts/set_version.py, which is a build tool and not importable
    as part of the installed package."""
    path = REPO_ROOT / "scripts" / "set_version.py"
    spec = importlib.util.spec_from_file_location("set_version", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["set_version"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copy(REPO_ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copy(REPO_ROOT / "server.json", tmp_path / "server.json")
    return tmp_path


def test_rewrites_pyproject_version(repo: Path):
    set_version = _load_module()
    set_version.set_version("9.8.7", repo)
    text = (repo / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "9.8.7"' in text


def test_rewrites_both_server_json_versions(repo: Path):
    set_version = _load_module()
    set_version.set_version("9.8.7", repo)
    doc = json.loads((repo / "server.json").read_text(encoding="utf-8"))
    assert doc["version"] == "9.8.7"
    assert doc["packages"][0]["version"] == "9.8.7"


def test_does_not_touch_the_requires_python_floor(repo: Path):
    """A naive regex over `version` would corrupt other pinned values."""
    set_version = _load_module()
    set_version.set_version("9.8.7", repo)
    text = (repo / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.10"' in text
    assert '"SimConnect>=0.4.26"' in text
    assert 'target-version = "py310"' in text


def test_reports_the_files_it_changed(repo: Path):
    set_version = _load_module()
    changed = set_version.set_version("9.8.7", repo)
    assert sorted(p.name for p in changed) == ["pyproject.toml", "server.json"]


def test_rejects_a_version_that_is_not_a_version(repo: Path):
    set_version = _load_module()
    with pytest.raises(ValueError, match="not a valid version"):
        set_version.set_version("v9.8.7", repo)


def test_is_idempotent(repo: Path):
    set_version = _load_module()
    set_version.set_version("9.8.7", repo)
    first = (repo / "pyproject.toml").read_text(encoding="utf-8")
    set_version.set_version("9.8.7", repo)
    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == first
