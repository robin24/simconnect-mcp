"""Tests for the documentation resource module.

Covers two defects:

1. `pmdg_777.md` and `pmdg_737.md` shipped in the package with no resource
   serving them.
2. The section-filter logic (duplicated between the simvars and events
   resources) re-entered a section when a later heading also matched the
   category -- e.g. category="engine" matching both "## Engines" and a
   later "## Engine Limits".
"""

from __future__ import annotations

import pathlib

import pytest

import simconnect_mcp
from simconnect_mcp.resources.documentation import _DOC_FILES, _read_doc, extract_section

SAMPLE = """# Doc

## Engines
engine content

## Engine Limits
limits content

## Autopilot
autopilot content
"""


def test_extract_section_stops_at_the_next_heading():
    """Bug: 'Engine Limits' also matches 'engine', so the old loop re-entered
    the section instead of terminating it."""
    section = extract_section(SAMPLE, "engine")
    assert "engine content" in section
    assert "limits content" not in section
    assert "autopilot content" not in section


def test_extract_section_returns_everything_for_all():
    assert extract_section(SAMPLE, "all") == SAMPLE


_SAMPLE_WITH_ALL_SUBSTRING = """# Doc

## Stall Warnings
stall content

## Autopilot
autopilot content
"""


@pytest.mark.parametrize("category", ["All", "ALL", "aLL"])
def test_extract_section_all_sentinel_is_case_insensitive(category):
    """B10: the rest of the matcher lowercases both sides (`needle in
    line.lower()`), but the 'all' sentinel compared `category` verbatim --
    an agent passing 'All' fell through to a substring search instead.

    SAMPLE's own headings ('Engines', 'Engine Limits', 'Autopilot') don't
    happen to contain the substring "all", so asserting against SAMPLE
    alone would pass even pre-fix (falling through to the "nothing
    matched, return everything" branch by coincidence -- the same result
    as the correct "all" sentinel, for the wrong reason). "Stall Warnings"
    does contain it, so the pre-fix code wrongly narrows to just that
    section instead of returning the whole document.
    """
    assert extract_section(SAMPLE, category) == SAMPLE
    assert extract_section(_SAMPLE_WITH_ALL_SUBSTRING, category) == _SAMPLE_WITH_ALL_SUBSTRING


def test_extract_section_falls_back_to_full_doc_when_unmatched():
    assert extract_section(SAMPLE, "nonexistent") == SAMPLE


# ---------------------------------------------------------------------------
# Addendum #2: parametrize over _DOC_FILES itself, not a hardcoded list that
# can drift from it -- that exact drift is how pmdg_777.md / pmdg_737.md
# ended up shipped but unreachable in the first place.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_DOC_FILES))
def test_every_registered_doc_file_exists(name):
    content = _read_doc(name)
    # Addendum #5: keep this assertion. "not yet available" is a
    # success-shaped response for a missing file; if this ever fails, fix
    # the missing file, not the assertion.
    assert "not yet available" not in content
    assert len(content) > 500


async def test_pmdg_docs_are_registered_as_resources():
    from simconnect_mcp.server import mcp

    uris = {str(t.uriTemplate) for t in await mcp.list_resource_templates()}
    uris |= {str(r.uri) for r in await mcp.list_resources()}
    assert any("pmdg" in u for u in uris)


# ---------------------------------------------------------------------------
# Addendum #3: reachability. Go through the actual resource layer for every
# _DOC_FILES entry and confirm the served content matches the on-disk file --
# asserting a URI merely *contains* "pmdg" would still pass if the template
# existed but returned nothing useful.
# ---------------------------------------------------------------------------

_KEY_TO_URI = {
    "overview": "simconnect://docs/overview",
    "simvars": "simconnect://docs/simvars/all",
    "events": "simconnect://docs/events/all",
    "rpn": "simconnect://docs/rpn",
    "lvars": "simconnect://docs/lvars",
    "best-practices": "simconnect://docs/best-practices",
    "pmdg-777": "simconnect://docs/pmdg/777",
    "pmdg-737": "simconnect://docs/pmdg/737",
}


@pytest.mark.parametrize("key", sorted(_DOC_FILES))
async def test_every_doc_file_is_reachable_through_a_resource(key):
    """A key with no serving URI must fail this test, not be silently
    skipped -- so the URI lookup itself is asserted, not just indexed."""
    from simconnect_mcp.server import mcp

    assert key in _KEY_TO_URI, f"{key!r} has no known serving URI -- add one to _KEY_TO_URI"
    uri = _KEY_TO_URI[key]

    contents = await mcp.read_resource(uri)
    served = contents[0].content

    assert served == _read_doc(key)


# ---------------------------------------------------------------------------
# B10: docs/pmdg_737.md listed variant_source's values as explicit/detected/
# name_match/fallback and omitted "probed" -- skipping the resolution step
# Task 7 added and live-verified (a client-data probe of each SDK, which is
# what actually identifies a PMDG 737-600 whose TITLE/ATC_MODEL carry no
# PMDG branding at all). A served MCP resource stated the resolution order
# wrongly. Checked against the doc content directly since variant_source is
# free-text across several docstrings, not a single Literal to import.
# ---------------------------------------------------------------------------

_VARIANT_SOURCE_VALUES = ("explicit", "detected", "probed", "name_match", "fallback")


def test_pmdg_737_doc_lists_every_variant_source_value():
    content = _read_doc("pmdg-737")
    missing = [v for v in _VARIANT_SOURCE_VALUES if v not in content]
    assert not missing, (
        f"docs/pmdg_737.md is missing variant_source value(s) {missing} -- "
        "probably stale against tools/pmdg.py's _resolve_pmdg_catalog"
    )


# ---------------------------------------------------------------------------
# Addendum #1: docs_pmdg key computation. str.lstrip is case-sensitive, so
# lstrip('b') before lower() never strips an uppercase B -- and "B737" /
# "B777" is exactly how PMDG names its own airframes. Must lower() first.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["737", "b737", "B737", "777", "B777"])
async def test_pmdg_variant_resolves_regardless_of_case(variant):
    from simconnect_mcp.server import mcp

    contents = await mcp.read_resource(f"simconnect://docs/pmdg/{variant}")
    content = contents[0].content

    assert "not yet available" not in content
    assert "No PMDG documentation" not in content
    assert len(content) > 500


async def test_pmdg_unknown_variant_reports_available_variants_not_a_crash():
    from simconnect_mcp.server import mcp

    contents = await mcp.read_resource("simconnect://docs/pmdg/747")
    content = contents[0].content

    assert "No PMDG documentation" in content
    assert "777" in content
    assert "737" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ---------------------------------------------------------------------------
# Served docs and prompts must not tell an agent to call a tool that always
# fails.
#
# Task 5 made msfs_list_lvars honest -- its old canned success was an
# instance of the fabricated-success pattern this phase exists to remove --
# and Task 8's rename sweep then edited these same four lines and left the
# false claim standing, so the lie moved out of the return value and into
# the documentation the same phase was rewriting:
#
#   docs/lvars.md           "Returns ALL active L-var names on current aircraft"
#   docs/lvars.md           "Use msfs_list_lvars() first"
#   docs/best_practices.md  "Use msfs_list_lvars() to see what's available"
#   prompts/templates.py    "Use msfs_list_lvars() to get every L-var registered"
#
# The analyze_aircraft_vars prompt was a five-step procedure whose steps
# 3-5 all depended on step 2, which could not succeed.
# ---------------------------------------------------------------------------

_UNAVAILABLE_MARKERS = ("not available", "not implemented", "NOT_IMPLEMENTED")

# Words that would overclaim even once Phase 2 lands: the MobiFlight WASM
# module caps its response at 1000 names while still sending its
# end-of-list sentinel, so "every"/"all" will still be wrong afterwards.
_COMPLETENESS_CLAIMS = ("all ", "every ", "ALL ")


def _agent_facing_doc_files() -> list[pathlib.Path]:
    pkg_dir = pathlib.Path(simconnect_mcp.__file__).parent
    files = sorted((pkg_dir / "docs").glob("*.md"))
    assert len(files) >= 8, f"only {len(files)} docs found -- did docs/ move?"
    files.append(pkg_dir / "prompts" / "templates.py")
    return files


def test_list_lvars_still_returns_not_implemented():
    """Pins the fact the docs now assert.

    Deliberately self-invalidating: when Phase 2 makes live enumeration
    real, this test fails and the four doc sites have to be revisited
    rather than quietly keeping a caveat that has become wrong in the
    other direction.
    """
    source = pathlib.Path(
        pathlib.Path(simconnect_mcp.__file__).parent / "tools" / "lvars.py"
    ).read_text(encoding="utf-8")
    assert 'error="NOT_IMPLEMENTED"' in source, (
        "msfs_list_lvars no longer returns NOT_IMPLEMENTED -- re-check every "
        "doc and prompt that currently says live enumeration is unavailable"
    )


def test_no_doc_instructs_calling_list_lvars_without_saying_it_fails():
    """Every mention must sit next to a disclaimer.

    A doc that names the tool as the discovery step sends an agent into a
    guaranteed NOT_IMPLEMENTED, and in the prompt's case into a procedure
    whose remaining steps have nothing to work from.
    """
    offenders = []
    for path in _agent_facing_doc_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "list_lvars" not in line:
                continue
            if not any(marker in line for marker in _UNAVAILABLE_MARKERS):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "these name msfs_list_lvars without saying it is unavailable:\n"
        + "\n".join(offenders)
    )


def test_no_doc_claims_list_lvars_returns_everything():
    """"every" and "all" stay wrong even after Phase 2 -- the MobiFlight
    WASM module caps its reply at 1000 names and still sends its
    end-of-list sentinel, so a complete listing is not on offer."""
    offenders = []
    for path in _agent_facing_doc_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "list_lvars" not in line:
                continue
            for claim in _COMPLETENESS_CLAIMS:
                if claim in line:
                    offenders.append(f"{path.name}:{lineno} claims {claim!r}: {line.strip()}")
    assert not offenders, "overclaimed completeness:\n" + "\n".join(offenders)
