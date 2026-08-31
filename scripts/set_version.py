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
