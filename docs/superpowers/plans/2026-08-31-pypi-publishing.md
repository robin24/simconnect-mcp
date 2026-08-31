# PyPI Publishing and MCP Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish `simconnect-mcp` to PyPI so AI harnesses can run it with `uvx simconnect-mcp`, relicensed to AGPL-3.0, with CI running the mocked test suite and a tag-triggered workflow that sets the version from the tag and publishes to both PyPI and the official MCP Registry.

**Architecture:** Three strands, kept independent. (1) Package metadata — `pyproject.toml` becomes the single source of truth for version and license; `__init__.py` reads its version from installed metadata so drift is structurally impossible. (2) Registry presence — a `server.json` plus an `mcp-name:` marker in the README, which is how the MCP Registry verifies PyPI ownership. (3) Automation — `.github/workflows/ci.yml` runs the mocked suite on every push, `.github/workflows/release.yml` fires on `v*` tags and drives version → build → PyPI → registry → commit-back. A small `scripts/set_version.py` owns all version rewriting so the release workflow contains no inline `sed`/`jq`, and so the rewriting is unit-testable.

**Tech Stack:** Python 3.10+, hatchling, uv, pytest, GitHub Actions, PyPI Trusted Publishing (OIDC), `mcp-publisher` CLI.

## Global Constraints

- **Version 0.2.0 is authoritative** and already tagged `v0.2.0` in the repo. Do not bump it in this work — only make it single-sourced.
- **License becomes `AGPL-3.0-or-later`** (SPDX expression). The relicense is deliberate: the runtime dependency `SimConnect` (odwdinc/Python-SimConnect) is AGPL-3.0.
- **Do not modify `src/simconnect_mcp/vendor/`.** `MOBIFLIGHT_LICENSE` stays MIT — it is upstream's license for vendored code and is unaffected by relicensing this project.
- **CI must run on `windows-latest`.** `dispatch.py:37` and `facilities.py:61` do `from ctypes.wintypes import DWORD` at module level, which fails to import on Linux. The mocked suite therefore cannot run on Ubuntu.
- **Mocked suite only in CI.** `pyproject.toml` already sets `addopts = "-m 'not live'"`, so a plain `pytest` deselects the 28 live tests. Do not add `-m live` anywhere in CI.
- **Baseline to preserve:** `uv run pytest` = 719 passed, 28 deselected. Every task must leave this green (count may rise as tasks add tests).
- **PyPI package name:** `simconnect-mcp` (verified unclaimed).
- **MCP server name:** `io.github.robin24/simconnect-mcp` (GitHub-auth namespace must match the repo owner `robin24`).
- **`server.json` `description` has `maxLength: 100`** in the registry schema. Keep it short.
- **Do not add a `License ::` trove classifier.** PEP 639 `License-Expression` metadata and legacy license classifiers are mutually exclusive; hatchling rejects the combination.
- **Pin `astral-sh/setup-uv@v10.0.1` exactly** — that project publishes no floating `v10` major tag (verified: floating tags stop at `v7`). `actions/checkout@v7` floating major does exist and is fine.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `LICENSE.txt` | Verbatim AGPL-3.0 text | Replace |
| `pyproject.toml` | Single source of version, license, PyPI metadata, dep split | Modify |
| `src/simconnect_mcp/__init__.py` | Version read from installed metadata | Modify |
| `server.json` | MCP Registry metadata | Create |
| `README.md` | `mcp-name:` marker, uvx install docs, AGPL section, release runbook | Modify |
| `scripts/set_version.py` | Sole owner of version rewriting for releases | Create |
| `tests/test_packaging.py` | Version/name consistency across pyproject, `__init__`, server.json, README | Create |
| `tests/test_set_version.py` | Unit tests for the version rewriter | Create |
| `.github/workflows/ci.yml` | Mocked suite + lint on push/PR | Create |
| `.github/workflows/release.yml` | Tag → version → build → PyPI → registry → commit-back | Create |

---

### Task 1: Relicense to AGPL-3.0

**Files:**
- Modify: `LICENSE.txt` (full replacement)
- Modify: `pyproject.toml:11`
- Modify: `README.md:406-408`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `pyproject.toml` carries `license = "AGPL-3.0-or-later"`; wheel METADATA carries `License-Expression: AGPL-3.0-or-later`. Task 2 adds classifiers alongside this and must not add a `License ::` classifier.

- [ ] **Step 1: Replace the license text with verbatim AGPL-3.0**

The FSF requires the license text be reproduced unmodified — including the "How to Apply These Terms" appendix with its `<year>` / `<name of author>` placeholders. Do not fill those in; the actual copyright notice goes in the README (Step 3).

```bash
gh api licenses/agpl-3.0 --jq '.body' > LICENSE.txt
```

- [ ] **Step 2: Verify the file is the real AGPL and not an error payload**

```bash
head -2 LICENSE.txt
wc -l LICENSE.txt
```

Expected: first line `                    GNU AFFERO GENERAL PUBLIC LICENSE`, and 662 lines.

- [ ] **Step 3: Set the SPDX expression in `pyproject.toml`**

Replace line 11:

```toml
license = "AGPL-3.0-or-later"
```

`-or-later` matches the standard AGPL boilerplate ("either version 3 of the License, or (at your option) any later version") carried in the appendix of the text fetched in Step 1.

- [ ] **Step 4: Rewrite the README license section**

Replace the two lines at `README.md:406-408` (`## License` and the MIT sentence) with:

```markdown
## License

Copyright (C) 2025-2026 Robin Kipp

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version. See [LICENSE.txt](LICENSE.txt) for the full text.

This project is AGPL-3.0 because it depends on
[Python-SimConnect](https://github.com/odwdinc/Python-SimConnect), which is
itself AGPL-3.0 licensed.

The vendored MobiFlight bridge in `src/simconnect_mcp/vendor/` is a separate
work, MIT licensed by Koseng — see
[`src/simconnect_mcp/vendor/MOBIFLIGHT_LICENSE`](src/simconnect_mcp/vendor/MOBIFLIGHT_LICENSE).
```

- [ ] **Step 5: Verify the built metadata carries the new expression**

```bash
uv build 2>&1 | tail -2
uv run python -c "
import zipfile, glob
w = sorted(glob.glob('dist/*.whl'))[-1]
m = zipfile.ZipFile(w).read('simconnect_mcp-0.2.0.dist-info/METADATA').decode('utf-8')
for line in m.splitlines():
    if line.startswith(('License', 'Classifier: License')):
        print(line)
"
```

Expected: `License-Expression: AGPL-3.0-or-later` and `License-File: LICENSE.txt`. No `Classifier: License ::` line.

- [ ] **Step 6: Confirm the suite is still green**

```bash
uv run pytest -q 2>&1 | tail -3
```

Expected: `719 passed, 28 deselected`.

- [ ] **Step 7: Commit**

```bash
git add LICENSE.txt pyproject.toml README.md
git commit -m "license: relicense project to AGPL-3.0-or-later

The runtime dependency Python-SimConnect is AGPL-3.0, so distributing this
project on PyPI under MIT was not tenable. The vendored MobiFlight bridge
keeps its own MIT license, which is unaffected."
```

---

### Task 2: PyPI metadata, dependency split, and single-source version

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/simconnect_mcp/__init__.py`
- Create: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `license = "AGPL-3.0-or-later"` from Task 1.
- Produces: `simconnect_mcp.__version__` (str) resolved from installed metadata; `tests/test_packaging.py::test_init_version_matches_pyproject`; a `_read_pyproject_version()` helper in the test module reused by Task 3's tests. Runtime dependency is plain `mcp` (no `[cli]` extra); the `dev` group gains `mcp[cli]` and `tomli`.

- [ ] **Step 1: Write the failing consistency test**

Create `tests/test_packaging.py`:

```python
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
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
uv run pytest tests/test_packaging.py -v 2>&1 | tail -15
```

Expected: `test_init_version_matches_pyproject` FAILS (`'0.1.0' != '0.2.0'`), `test_runtime_deps_do_not_pull_the_cli_extra` FAILS, `test_pypi_metadata_is_populated` FAILS on `authors missing`.

- [ ] **Step 3: Make `__init__.py` read the installed version**

Replace the whole of `src/simconnect_mcp/__init__.py`:

```python
"""SimConnect MCP Server — MSFS add-on development companion."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("simconnect-mcp")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
```

This makes `pyproject.toml` the only place a version number is written by hand, so the release workflow has exactly one file to rewrite.

- [ ] **Step 4: Add metadata and split the dependencies in `pyproject.toml`**

Replace the `[project]` block's `dependencies` and add the new keys, and replace `[dependency-groups]`:

```toml
[project]
name = "simconnect-mcp"
version = "0.2.0"
description = "MCP server for MSFS SimConnect — add-on development companion"
readme = "README.md"
requires-python = ">=3.10"
license = "AGPL-3.0-or-later"
authors = [{ name = "Robin Kipp", email = "robin@robin-kipp.net" }]
keywords = [
    "mcp",
    "model-context-protocol",
    "msfs",
    "flight-simulator",
    "simconnect",
    "flightsim",
    "mobiflight",
    "pmdg",
]
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "Operating System :: Microsoft :: Windows",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Games/Entertainment :: Simulation",
    "Topic :: Software Development :: Libraries :: Python Modules",
]
dependencies = [
    "mcp>=1.26,<2",
    "SimConnect>=0.4.26",
]

[project.urls]
Homepage = "https://github.com/robin24/simconnect-mcp"
Repository = "https://github.com/robin24/simconnect-mcp"
Issues = "https://github.com/robin24/simconnect-mcp/issues"

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "ruff>=0.6",
    "mcp[cli]>=1.26,<2",
    "tomli>=2.0; python_version < '3.11'",
]
```

Leave `[build-system]`, `[project.scripts]`, `[tool.hatch.build.targets.wheel]`, `[tool.pytest.ini_options]` and the `[tool.ruff]` blocks exactly as they are.

- [ ] **Step 5: Re-sync and run the tests**

```bash
uv sync
uv run pytest tests/test_packaging.py -v 2>&1 | tail -10
```

Expected: all 3 tests PASS.

- [ ] **Step 6: Confirm the full suite and the uvx path still work**

```bash
uv run pytest -q 2>&1 | tail -3
uv build 2>&1 | tail -2
```

Expected: `722 passed, 28 deselected`, and a clean build.

- [ ] **Step 7: Verify the metadata actually landed in the wheel**

```bash
uv run python -c "
import zipfile, glob
w = sorted(glob.glob('dist/*.whl'))[-1]
m = zipfile.ZipFile(w).read('simconnect_mcp-0.2.0.dist-info/METADATA').decode('utf-8')
for line in m.splitlines():
    if line.startswith(('Author', 'Project-URL', 'Keywords', 'Requires-Dist', 'Classifier: Operating')):
        print(line)
"
```

Expected: `Author-email`, three `Project-URL` lines, a `Keywords` line, `Requires-Dist: mcp<2,>=1.26` (with **no** `[cli]` extra), and the Windows classifier.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/simconnect_mcp/__init__.py tests/test_packaging.py
git commit -m "build: populate PyPI metadata and single-source the version

__init__.py read 0.1.0 while pyproject read 0.2.0; it now resolves from
installed metadata so pyproject is the only hand-written version. Drops the
mcp[cli] extra from runtime deps -- nothing under src/ imports typer or rich,
and it resolved 104 packages against 86 for plain mcp."
```

---

### Task 3: MCP Registry files

**Files:**
- Create: `server.json`
- Modify: `README.md` (add `mcp-name:` marker near the top)
- Modify: `tests/test_packaging.py` (append registry consistency tests)

**Interfaces:**
- Consumes: `_read_pyproject_version()` and `REPO_ROOT` from `tests/test_packaging.py` (Task 2).
- Produces: `server.json` at repo root with `name`, `version`, and `packages[0].version`; `SERVER_NAME = "io.github.robin24/simconnect-mcp"` as the value asserted in both `server.json` and the README marker. Task 6's `scripts/set_version.py` rewrites `server.json`'s two version fields.

- [ ] **Step 1: Write the failing registry consistency tests**

Append to `tests/test_packaging.py`:

```python
SERVER_NAME = "io.github.robin24/simconnect-mcp"


def _read_server_json() -> dict:
    import json

    return json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))


def test_server_json_version_matches_pyproject():
    server = _read_server_json()
    expected = _read_pyproject_version()
    assert server["version"] == expected
    assert server["packages"][0]["version"] == expected


def test_server_json_identifies_the_pypi_package():
    package = _read_server_json()["packages"][0]
    assert package["registryType"] == "pypi"
    assert package["identifier"] == "simconnect-mcp"
    assert package["transport"]["type"] == "stdio"


def test_server_json_description_fits_registry_schema():
    """The registry schema caps description at 100 characters."""
    description = _read_server_json()["description"]
    assert 1 <= len(description) <= 100, len(description)


def test_readme_carries_the_registry_ownership_marker():
    """The MCP Registry verifies PyPI ownership by finding this string in the
    package description -- which is README.md, via `readme = "README.md"`.

    The marker must be followed by a boundary (newline, whitespace, HTML tag
    or `-->`); gluing it to trailing punctuation prevents the match.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert f"mcp-name: {SERVER_NAME}\n" in readme or f"mcp-name: {SERVER_NAME} " in readme
    assert _read_server_json()["name"] == SERVER_NAME
```

- [ ] **Step 2: Run to confirm they fail**

```bash
uv run pytest tests/test_packaging.py -v 2>&1 | tail -12
```

Expected: the four new tests ERROR/FAIL because `server.json` does not exist.

- [ ] **Step 3: Create `server.json`**

Field constraints come from the `2025-12-11` schema: `ServerDetail.required` is `["name", "description", "version"]`; `name` must match `^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$`; `description` is `maxLength: 100`; `Package.required` is `["registryType", "identifier", "transport"]`.

```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.github.robin24/simconnect-mcp",
  "title": "SimConnect MCP",
  "description": "MCP server for MSFS SimConnect - full read/write access to SimVars, L-vars and events",
  "repository": {
    "url": "https://github.com/robin24/simconnect-mcp",
    "source": "github"
  },
  "version": "0.2.0",
  "packages": [
    {
      "registryType": "pypi",
      "identifier": "simconnect-mcp",
      "version": "0.2.0",
      "runtimeHint": "uvx",
      "transport": {
        "type": "stdio"
      }
    }
  ]
}
```

The `description` here is deliberately the ASCII-hyphen variant and shorter than `pyproject.toml`'s — it must fit 100 characters.

- [ ] **Step 4: Add the ownership marker to the README**

Insert as the third line of `README.md`, immediately after the `# SimConnect MCP Server` heading and its blank line, before the intro paragraph:

```markdown
<!-- mcp-name: io.github.robin24/simconnect-mcp -->
```

An HTML comment renders as nothing on both GitHub and PyPI, and `-->` is an accepted boundary after the name.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/test_packaging.py -v 2>&1 | tail -12
```

Expected: all 7 tests PASS.

- [ ] **Step 6: Validate `server.json` against the published schema**

```bash
uv run --with jsonschema --with requests python -c "
import json, requests, jsonschema
schema = requests.get('https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json').json()
doc = json.load(open('server.json', encoding='utf-8'))
jsonschema.validate(doc, schema)
print('server.json is valid against the 2025-12-11 schema')
"
```

Expected: the success line, no traceback.

- [ ] **Step 7: Confirm the marker survives into the built package description**

```bash
uv build 2>&1 | tail -2
uv run python -c "
import zipfile, glob
w = sorted(glob.glob('dist/*.whl'))[-1]
m = zipfile.ZipFile(w).read('simconnect_mcp-0.2.0.dist-info/METADATA').decode('utf-8')
print('marker in PyPI description:', 'mcp-name: io.github.robin24/simconnect-mcp' in m)
"
```

Expected: `marker in PyPI description: True`. This is the check that matters — the registry reads the marker from PyPI, not from GitHub.

- [ ] **Step 8: Commit**

```bash
git add server.json README.md tests/test_packaging.py
git commit -m "feat: add MCP Registry metadata

server.json plus the mcp-name marker in README.md, which is how the registry
verifies PyPI package ownership (it reads the marker from the package
description, so the marker has to survive into wheel METADATA)."
```

---

### Task 4: Document uvx installation

**Files:**
- Modify: `README.md:26-36` (Installation), `README.md:37-115` (client configs), `README.md:19-24` (Prerequisites)

**Interfaces:**
- Consumes: the published package name `simconnect-mcp` from Task 2.
- Produces: no code interface. Documentation only.

- [ ] **Step 1: Replace the Installation section**

Replace the `## Installation` section (currently the `git clone` + `uv sync` block) with:

````markdown
## Installation

Once [uv](https://docs.astral.sh/uv/) is installed, no separate install step is
needed — `uvx` fetches and runs the server on demand:

```bash
uvx simconnect-mcp
```

That starts the server on stdio, which is what an MCP client does for you. Run
it by hand only to check that it starts; press Ctrl+C to stop.

To pin a version, use `uvx simconnect-mcp@0.2.0`.

### From source (for development)

```bash
git clone https://github.com/robin24/simconnect-mcp.git
cd simconnect-mcp

# Creates the virtual environment and installs the dev group too
uv sync
```
````

- [ ] **Step 2: Update the Prerequisites list**

In `README.md:19-24`, replace the `**Python 3.10+**` and `**[uv](https://docs.astral.sh/uv/)** (recommended) or pip` bullets with:

```markdown
- **[uv](https://docs.astral.sh/uv/)** — provides `uvx`, which fetches the server and its Python runtime for you
```

Drop the standalone Python bullet: `uvx` provisions a suitable interpreter itself, so requiring the user to install Python 3.10+ separately is no longer accurate.

- [ ] **Step 3: Rewrite the Claude Code config block**

Replace the two `claude mcp add` commands and the JSON block under `### Claude Code` with:

````markdown
**Via CLI:**

```bash
claude mcp add --transport stdio simconnect -- uvx simconnect-mcp
```

Or to make it available across all projects:

```bash
claude mcp add --transport stdio --scope user simconnect -- uvx simconnect-mcp
```

**Via JSON** (`~/.claude/settings.json` or project-level `.claude/settings.json`):

```json
{
  "mcpServers": {
    "simconnect": {
      "command": "uvx",
      "args": ["simconnect-mcp"]
    }
  }
}
```
````

- [ ] **Step 4: Rewrite the Codex CLI config block**

Replace the contents of `### OpenAI Codex CLI` with:

````markdown
**Via CLI:**

```bash
codex mcp add simconnect -- uvx simconnect-mcp
```

**Via JSON:**

```json
{
  "mcpServers": {
    "simconnect": {
      "command": "uvx",
      "args": ["simconnect-mcp"]
    }
  }
}
```
````

- [ ] **Step 5: Rewrite the Gemini CLI config block**

Replace the contents of `### Gemini CLI` with:

````markdown
**Via CLI:**

```bash
gemini mcp add --transport stdio simconnect -- uvx simconnect-mcp
```

**Via JSON** (`~/.gemini/settings.json`):

```json
{
  "mcpServers": {
    "simconnect": {
      "command": "uvx",
      "args": ["simconnect-mcp"]
    }
  }
}
```
````

- [ ] **Step 6: Replace the trailing path note with a from-source note**

The note after the MCP Inspector block currently reads:

> **Note:** Replace `/path/to/simconnect-mcp` with the actual absolute path to your clone of this repository.

No config above references a path any more, so replace it with:

```markdown
> **Note:** To run a local checkout instead of the published package, replace
> `uvx simconnect-mcp` with `uv run --directory /path/to/simconnect-mcp simconnect-mcp`,
> using the absolute path to your clone.
```

- [ ] **Step 7: Verify no stale path references survive**

```bash
grep -n "path/to/simconnect-mcp" README.md
```

Expected: exactly one hit — the note added in Step 6.

- [ ] **Step 8: Commit**

```bash
git add README.md
git commit -m "docs: document uvx installation

Every client config example used 'uv run --directory /path/to/...', which
only worked from a clone. They now use the published package."
```

---

### Task 5: CI workflow for the mocked suite

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the `dev` dependency group from Task 2 (`uv sync` installs it by default).
- Produces: a reusable job shape (checkout → setup-uv → sync → ruff → pytest) that Task 6's release workflow repeats for its gating test job.

- [ ] **Step 1: Create the workflow**

`windows-latest` is required, not a preference — see Global Constraints.

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

jobs:
  test:
    name: Tests (Python ${{ matrix.python-version }})
    # Windows is required, not preferred: dispatch.py and facilities.py import
    # ctypes.wintypes at module level, which does not import on Linux.
    runs-on: windows-latest
    strategy:
      fail-fast: false
      matrix:
        # Floor from requires-python, plus a current release.
        python-version: ["3.10", "3.13"]

    steps:
      - name: Checkout
        uses: actions/checkout@v7

      - name: Install uv
        uses: astral-sh/setup-uv@v10.0.1
        with:
          python-version: ${{ matrix.python-version }}
          enable-cache: true

      - name: Install dependencies
        run: uv sync --locked

      - name: Lint
        run: uv run ruff check src tests

      - name: Run mocked test suite
        # pyproject sets addopts = "-m 'not live'", so the 28 live tests that
        # need a running MSFS are deselected here.
        run: uv run pytest -q
```

- [ ] **Step 2: Verify the workflow is valid YAML**

```bash
uv run --with pyyaml python -c "
import yaml
d = yaml.safe_load(open('.github/workflows/ci.yml', encoding='utf-8'))
print('jobs:', list(d['jobs']))
print('runner:', d['jobs']['test']['runs-on'])
print('matrix:', d['jobs']['test']['strategy']['matrix']['python-version'])
print('steps:', [s['name'] for s in d['jobs']['test']['steps']])
"
```

Expected: `jobs: ['test']`, `runner: windows-latest`, `matrix: ['3.10', '3.13']`, and the five step names.

- [ ] **Step 3: Reproduce the CI commands locally on the floor version**

CI tests Python 3.10 while the dev venv is 3.14; this is the step that catches a 3.10-only failure before it reaches CI.

```bash
uv run --locked --python 3.10 ruff check src tests
uv run --locked --python 3.10 pytest -q 2>&1 | tail -3
```

Expected: ruff clean; the suite passes. If `uv sync --locked` would fail because `uv.lock` is stale, run `uv lock` and include the updated lockfile in the commit.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run the mocked test suite on push and PR

Windows runner is mandatory -- dispatch.py and facilities.py import
ctypes.wintypes at module level. Live tests stay deselected via the
addopts already in pyproject."
```

---

### Task 6: Tag-triggered release to PyPI and the MCP Registry

**Files:**
- Create: `scripts/set_version.py`
- Create: `tests/test_set_version.py`
- Create: `.github/workflows/release.yml`
- Modify: `README.md` (append a "Releasing" section)

**Interfaces:**
- Consumes: `server.json` (Task 3), the metadata layout of `pyproject.toml` (Task 2), the job shape from Task 5.
- Produces: `scripts/set_version.py` exposing `set_version(version: str, repo_root: Path) -> list[Path]` (returns the files it rewrote) and a `main(argv: list[str] | None = None) -> int` CLI entry point invoked as `python scripts/set_version.py <version>`.

- [ ] **Step 1: Write the failing tests for the version rewriter**

Create `tests/test_set_version.py`:

```python
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
```

- [ ] **Step 2: Run to confirm they fail**

```bash
uv run pytest tests/test_set_version.py -v 2>&1 | tail -12
```

Expected: all 6 FAIL/ERROR — `scripts/set_version.py` does not exist.

- [ ] **Step 3: Implement the rewriter**

Create `scripts/set_version.py`:

```python
#!/usr/bin/env python
"""Set the project version across every file that carries it.

Called by .github/workflows/release.yml with the git tag minus its leading
"v". pyproject.toml is the authoritative version; server.json repeats it in
two places for the MCP Registry. src/simconnect_mcp/__init__.py deliberately
is not touched -- it resolves its version from installed metadata.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# PEP 440-ish: enough to catch a tag that still has its "v", or a typo,
# without reimplementing the whole grammar.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[.-]?(?:a|b|rc|alpha|beta|post|dev)\.?\d*)?$")

# Anchored to the [project] table's own key so that dependency pins,
# requires-python and tool.ruff's target-version are never touched.
PYPROJECT_VERSION_RE = re.compile(r'(?m)^(version = ")[^"]*(")')


def set_version(version: str, repo_root: Path) -> list[Path]:
    """Write `version` into pyproject.toml and server.json.

    Returns the files whose content changed.
    """
    if not VERSION_RE.match(version):
        raise ValueError(f"{version!r} is not a valid version")

    changed: list[Path] = []

    pyproject = repo_root / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    updated, count = PYPROJECT_VERSION_RE.subn(rf"\g<1>{version}\g<2>", original, count=1)
    if count != 1:
        raise RuntimeError(f"no 'version = \"...\"' line found in {pyproject}")
    if updated != original:
        pyproject.write_text(updated, encoding="utf-8")
    changed.append(pyproject)

    server_json = repo_root / "server.json"
    doc = json.loads(server_json.read_text(encoding="utf-8"))
    doc["version"] = version
    for package in doc.get("packages", []):
        package["version"] = version
    server_json.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    changed.append(server_json)

    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help='version without a leading "v", e.g. 0.3.0')
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root (default: the parent of scripts/)",
    )
    args = parser.parse_args(argv)

    for path in set_version(args.version, args.repo_root):
        print(f"set version {args.version} in {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_set_version.py -v 2>&1 | tail -12
```

Expected: 6 passed.

- [ ] **Step 5: Confirm the rewriter is a no-op at the current version**

Running it with the version already in the file must leave the working tree clean — proof it will not corrupt `pyproject.toml` in CI.

```bash
uv run python scripts/set_version.py 0.2.0
git diff --stat
```

Expected: two "set version" lines printed, and `git diff --stat` shows nothing.

- [ ] **Step 6: Create the release workflow**

```yaml
name: Release

on:
  push:
    tags: ["v*"]

jobs:
  test:
    name: Tests (Python ${{ matrix.python-version }})
    runs-on: windows-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.10", "3.13"]

    steps:
      - name: Checkout
        uses: actions/checkout@v7

      - name: Install uv
        uses: astral-sh/setup-uv@v10.0.1
        with:
          python-version: ${{ matrix.python-version }}
          enable-cache: true

      - name: Install dependencies
        run: uv sync --locked

      - name: Lint
        run: uv run ruff check src tests

      - name: Run mocked test suite
        run: uv run pytest -q

  publish:
    name: Publish to PyPI and the MCP Registry
    needs: test
    runs-on: ubuntu-latest
    permissions:
      # PyPI trusted publishing and `mcp-publisher login github-oidc` both
      # mint short-lived OIDC tokens; neither uses a stored secret.
      id-token: write
      contents: read

    steps:
      - name: Checkout the tagged commit
        uses: actions/checkout@v7

      - name: Install uv
        uses: astral-sh/setup-uv@v10.0.1
        with:
          enable-cache: true

      - name: Derive the version from the tag
        run: echo "VERSION=${GITHUB_REF_NAME#v}" >> "$GITHUB_ENV"

      - name: Set the version in pyproject.toml and server.json
        run: python scripts/set_version.py "$VERSION"

      - name: Build the distributions
        # Pure-Python py3-none-any wheel, so building on Linux is fine even
        # though the package only runs on Windows.
        run: uv build

      - name: Publish to PyPI
        run: uv publish --trusted-publishing always

      - name: Install mcp-publisher
        run: |
          curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" | tar xz mcp-publisher

      - name: Authenticate to the MCP Registry
        run: ./mcp-publisher login github-oidc

      - name: Publish to the MCP Registry
        run: ./mcp-publisher publish

  sync-version:
    name: Commit the version bump back to main
    needs: publish
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - name: Checkout main
        uses: actions/checkout@v7
        with:
          ref: main

      - name: Install uv
        uses: astral-sh/setup-uv@v10.0.1

      - name: Derive the version from the tag
        run: echo "VERSION=${GITHUB_REF_NAME#v}" >> "$GITHUB_ENV"

      - name: Set the version in pyproject.toml and server.json
        run: python scripts/set_version.py "$VERSION"

      - name: Sync the lockfile
        run: uv lock

      - name: Commit and push
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add pyproject.toml server.json uv.lock
          if git diff --staged --quiet; then
            echo "main is already at $VERSION; nothing to commit"
          else
            git commit -m "chore: set version to $VERSION"
            git push origin HEAD:main
          fi
```

This runs as three jobs on purpose. `sync-version` is separated from `publish` so that a branch-protection rule blocking the push shows as a failure in its own job rather than marking an otherwise successful release as failed.

- [ ] **Step 7: Verify the workflow is valid YAML and wired correctly**

```bash
uv run --with pyyaml python -c "
import yaml
d = yaml.safe_load(open('.github/workflows/release.yml', encoding='utf-8'))
print('jobs:', list(d['jobs']))
print('trigger:', d[True]['push']['tags'])
print('publish needs:', d['jobs']['publish']['needs'])
print('publish perms:', d['jobs']['publish']['permissions'])
print('sync needs:', d['jobs']['sync-version']['needs'])
print('sync perms:', d['jobs']['sync-version']['permissions'])
"
```

Expected: `jobs: ['test', 'publish', 'sync-version']`, trigger `['v*']`, `publish needs: test` with `id-token: write`, `sync-version needs: publish` with `contents: write`. (`d[True]` is not a typo — YAML parses the bare key `on` as the boolean `True`.)

- [ ] **Step 8: Document the one-time manual setup in the README**

Append to `README.md`, after the `## Development` section and before `## License`:

````markdown
## Releasing

Releases are automated by [`.github/workflows/release.yml`](.github/workflows/release.yml).
Pushing a `v*` tag runs the mocked test suite, rewrites the version in
`pyproject.toml` and `server.json` to the tag minus its `v`, publishes to PyPI,
publishes to the MCP Registry, and commits the version bump back to `main`.

```bash
git tag v0.3.0
git push origin v0.3.0
```

### One-time setup

Both publishing steps use OIDC, so there are no tokens to store. PyPI needs a
trusted publisher configured once, via the PyPI web UI — this cannot be done
from the CLI:

1. Go to <https://pypi.org/manage/account/publishing/>.
2. Add a **pending publisher** (or, once the project exists, a publisher under
   the project's settings) with:
   - **PyPI Project Name:** `simconnect-mcp`
   - **Owner:** `robin24`
   - **Repository name:** `simconnect-mcp`
   - **Workflow name:** `release.yml`
   - **Environment name:** leave blank
3. Leave the MCP Registry alone — `mcp-publisher login github-oidc` authorises
   itself from the workflow's OIDC token, and the server name
   `io.github.robin24/simconnect-mcp` already matches the repository owner.
````

- [ ] **Step 9: Run the full suite**

```bash
uv run pytest -q 2>&1 | tail -3
uv run ruff check src tests scripts
```

Expected: `732 passed, 28 deselected` (719 baseline + 3 from Task 2 + 4 from Task 3 + 6 here), ruff clean.

- [ ] **Step 10: Commit**

```bash
git add scripts/set_version.py tests/test_set_version.py .github/workflows/release.yml README.md
git commit -m "ci: publish to PyPI and the MCP Registry on a v* tag

The version is taken from the tag rather than hand-maintained, rewritten by
scripts/set_version.py into pyproject.toml and server.json, then committed
back to main in a separate job so a blocked push cannot fail the release."
```

---

## Post-Implementation Verification

Run before reporting completion:

- [ ] `uv run pytest -q` → all pass, 28 deselected
- [ ] `uv run ruff check src tests scripts` → clean
- [ ] `uv build` → sdist + wheel build
- [ ] Wheel METADATA contains `License-Expression: AGPL-3.0-or-later`, `Author-email`, three `Project-URL` lines, `Requires-Dist: mcp<2,>=1.26` with no `[cli]`, and the `mcp-name:` marker
- [ ] `uvx --from dist/<wheel> simconnect-mcp` completes an MCP `initialize` and lists 32 tools
- [ ] `git status` clean

## Manual steps that remain for the user

These cannot be automated from this repo and must be done by the user:

1. Configure the PyPI pending publisher (README "One-time setup" above).
2. Push the branch and merge it.
3. Push a tag to trigger the first release. Note `v0.2.0` already exists — the
   first automated release will need a new tag (e.g. `v0.2.1`), or the existing
   tag deleted and re-pushed.
