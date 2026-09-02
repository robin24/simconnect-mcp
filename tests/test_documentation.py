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
    "weather": "simconnect://docs/weather",
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
# The weather doc exists specifically to carry the documented-vs-measured
# distinction (see weather.py's module docstring and the design spec's "Two
# sources of truth") to an agent that only ever sees the served resource, not
# the code comments. A doc that dropped either the enforced ranges or the
# advisory ones would defeat the reason this task exists.
# ---------------------------------------------------------------------------


async def test_weather_doc_is_served_and_separates_documented_from_measured():
    from simconnect_mcp.server import mcp

    uris = {str(r.uri) for r in await mcp.list_resources()}
    assert "simconnect://docs/weather" in uris

    text = _read_doc("weather")
    # The whole point of the doc: an agent must be able to tell an enforced
    # bound from an advisory one.
    assert "msfs_write_weather_preset" in text
    assert "150" in text and "95000" in text
    assert "AMBIENT_PRECIP_STATE" in text


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
# Served docs and prompts must not misrepresent msfs_list_lvars in either
# direction.
#
# Phase 1 Task 5 made msfs_list_lvars honest -- its old canned success was
# an instance of the fabricated-success pattern this project exists to
# remove -- by making it return NOT_IMPLEMENTED, and docs/prompts were
# updated to say so:
#
#   docs/lvars.md           "msfs_list_lvars() returns NOT_IMPLEMENTED..."
#   docs/best_practices.md  "msfs_list_lvars() returns NOT_IMPLEMENTED..."
#
# Phase 2 Task 4 then made it real (it asks the MobiFlight WASM module for
# its L-var list and collects the response), which made THOSE claims false
# in the other direction: a doc still telling an agent the tool always
# fails is just as dishonest as the fabricated success it replaced. The
# tests below were themselves deliberately self-invalidating for exactly
# this moment (see git history for their Phase-1-era wording) -- this is
# that revisit, not a loosening of the underlying rule.
#
# The rule that survives unchanged: the MobiFlight WASM module caps
# MF.LVars.List at 1000 names and still reports the list as complete (see
# task-3-4-addendum.md and LVarList.truncated), so a doc that sends an
# agent to msfs_list_lvars must still disclose that cap, and none may claim
# it returns "all"/"every" L-var -- that was wrong before Task 4 and stays
# wrong after it.
# ---------------------------------------------------------------------------

# Words that overclaim regardless of implementation status: the MobiFlight
# WASM module caps its response at 1000 names while still sending its
# end-of-list sentinel, so "every"/"all" is always wrong here.
_COMPLETENESS_CLAIMS = ("all ", "every ", "ALL ")

# A doc that sends an agent to msfs_list_lvars must disclose the cap
# somewhere in the same file -- not necessarily the same line, since prose
# describing a working tool reasonably spreads the caveat across a
# sentence or two.
_CAP_DISCLOSURE_MARKERS = ("1000", "1,000")


def _agent_facing_doc_files() -> list[pathlib.Path]:
    pkg_dir = pathlib.Path(simconnect_mcp.__file__).parent
    files = sorted((pkg_dir / "docs").glob("*.md"))
    assert len(files) >= 8, f"only {len(files)} docs found -- did docs/ move?"
    files.append(pkg_dir / "prompts" / "templates.py")
    return files


def test_list_lvars_no_longer_a_stub():
    """Pins the fact that Phase 2 Task 4 replaced the canned NOT_IMPLEMENTED
    response with a real WASM list request.

    The behavioural coverage lives in tests/test_lvar_listing.py; this just
    guards against a regression back to the Phase 1 stub slipping in
    silently (e.g. a bad merge), which would leave every doc/prompt this
    file checks describing a capability that quietly stopped working again.
    """
    source = pathlib.Path(
        pathlib.Path(simconnect_mcp.__file__).parent / "tools" / "lvars.py"
    ).read_text(encoding="utf-8")
    assert 'error="NOT_IMPLEMENTED"' not in source, (
        "msfs_list_lvars appears to return NOT_IMPLEMENTED again -- if that's "
        "deliberate, every doc/prompt this file checks needs the opposite "
        "revisit Phase 2 Task 4 just gave them"
    )


def test_every_doc_mentioning_list_lvars_discloses_the_cap():
    """A doc that tells an agent to call msfs_list_lvars must not leave it
    thinking a capped response is exhaustive.

    Replaces this suite's old "must say it fails" check, whose premise
    (the tool always errors) Task 4 removed. The tool works now, but the
    MobiFlight WASM module still caps MF.LVars.List at 1000 names while
    reporting the list as complete -- see LVarList.truncated -- so the
    remaining honesty obligation is disclosing that cap, not disclosing
    unavailability.
    """
    offenders = []
    for path in _agent_facing_doc_files():
        text = path.read_text(encoding="utf-8")
        if "list_lvars" not in text:
            continue
        if not any(marker in text for marker in _CAP_DISCLOSURE_MARKERS):
            offenders.append(path.name)
    assert not offenders, (
        "these mention msfs_list_lvars but never disclose its ~1000-name cap:\n"
        + "\n".join(offenders)
    )


def test_no_doc_claims_list_lvars_returns_everything():
    """"every" and "all" stay wrong even with real enumeration -- the
    MobiFlight WASM module caps its reply at 1000 names and still sends its
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
