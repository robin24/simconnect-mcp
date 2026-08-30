"""Every ToolError an agent can receive should carry actionable advice.

Before this task, suggestion was populated at 25 of 31 ToolError call
sites; all six omissions were in tools/pmdg.py (CATALOG_NOT_FOUND,
NOT_A_DATA_FIELD, both INVALID_CDU branches, the second NO_CDU_DATA site,
and send_pmdg_event's MOBIFLIGHT_NOT_AVAILABLE branch) -- two of those
codes had a sibling elsewhere that DID carry a suggestion, so the same
failure advised the agent or not depending on which branch it hit.

This is a static, whole-surface AST scan rather than six individual
behavioural tests: it catches the exact class of omission B6 found
wherever it occurs, including at a call site nobody has written a
targeted test for yet, rather than only the six sites known today.

A call site that passes `suggestion=` some other expression (e.g.
require_connection forwarding a manager's own error dict, which may or
may not carry one) is deliberately not flagged -- this checks whether the
CODE tried to give advice, not whether an upstream value happens to be
None at runtime. Only an omitted `suggestion=` keyword, or one hardcoded
to the literal `None`, counts as a miss.
"""
from __future__ import annotations

import ast
from pathlib import Path

import simconnect_mcp

TOOLS_DIR = Path(simconnect_mcp.__file__).parent / "tools"

# Sanity floor so a refactor that silently stops the scan from matching
# anything (a rename, a moved directory) fails loudly instead of passing
# at 0-and-0 -- the same failure mode test_registration.py's own floors
# guard against. Measured at 35 sites across tools/*.py; set comfortably
# below that.
_MINIMUM_EXPECTED_SITES = 25


def _is_literal_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _tool_error_call_sites() -> list[tuple[Path, int, bool]]:
    """(path, lineno, has_real_suggestion) for every ToolError(...) call
    under tools/*.py."""
    sites: list[tuple[Path, int, bool]] = []
    for path in sorted(TOOLS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else None
            )
            if name != "ToolError":
                continue
            suggestion_kw = next(
                (kw for kw in node.keywords if kw.arg == "suggestion"), None
            )
            has_real_suggestion = suggestion_kw is not None and not _is_literal_none(
                suggestion_kw.value
            )
            sites.append((path, node.lineno, has_real_suggestion))
    return sites


def test_the_scan_actually_covers_something():
    sites = _tool_error_call_sites()
    assert len(sites) >= _MINIMUM_EXPECTED_SITES, (
        f"expected to find at least {_MINIMUM_EXPECTED_SITES} ToolError(...) "
        f"call sites under {TOOLS_DIR}, found only {len(sites)} -- did tools/ "
        "get renamed or moved, or did ToolError get constructed some other way?"
    )


def test_every_tool_error_carries_a_suggestion():
    sites = _tool_error_call_sites()
    offenders = [f"{path}:{lineno}" for path, lineno, has_suggestion in sites if not has_suggestion]
    assert not offenders, (
        "these ToolError(...) sites omit suggestion (or hardcode it to None), "
        "leaving an agent with no actionable next step:\n" + "\n".join(offenders)
    )
