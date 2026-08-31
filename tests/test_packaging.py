"""Packaging metadata consistency.

The release workflow rewrites the version in more than one file. These tests
are what stop those files from drifting apart -- they are the reason
scripts/set_version.py can be trusted to keep a release coherent.
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_pyproject() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _read_pyproject_version() -> str:
    return _read_pyproject()["project"]["version"]


def test_init_version_matches_pyproject():
    import simconnect_mcp

    assert simconnect_mcp.__version__ == _read_pyproject_version()


def test_runtime_deps_do_not_pull_the_cli_extra():
    """The `cli` extra is typer/rich, needed only by `mcp dev`.

    Nothing under src/ imports it, and it costs every `uvx simconnect-mcp`
    user an extra download, so it belongs in the dev group instead.
    """
    deps = _read_pyproject()["project"]["dependencies"]
    assert not any("mcp[cli]" in d.replace(" ", "") for d in deps), deps
    assert any(re.match(r"^mcp\b", d) for d in deps), deps


def test_pypi_metadata_is_populated():
    """A bare PyPI page has no author, no links sidebar and no search terms."""
    project = _read_pyproject()["project"]
    assert project.get("authors"), "authors missing"
    assert project.get("keywords"), "keywords missing"
    urls = project.get("urls", {})
    for key in ("Homepage", "Repository", "Issues"):
        assert key in urls, f"project.urls missing {key}"
    classifiers = project.get("classifiers", [])
    assert any(c.startswith("Operating System :: Microsoft :: Windows") for c in classifiers)
    # PEP 639: License-Expression and legacy license classifiers are mutually
    # exclusive, and hatchling rejects the combination.
    assert not any(c.startswith("License ::") for c in classifiers), classifiers
