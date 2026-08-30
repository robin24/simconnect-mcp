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

import pytest

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
