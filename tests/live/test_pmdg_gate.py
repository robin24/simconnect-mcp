"""Unit tests for the PMDG live-suite skip gate (see conftest.py's "PMDG
gate" section).

CATEGORY NOTE, same shape as test_live_hubhop.py's: everything else under
tests/live/ needs a running MSFS instance. This file does not -- it
exercises _is_pmdg_title/_skip_unless_pmdg directly, with fake TITLE
strings, so it lives here (next to the fixture it tests, importable via a
plain relative import) rather than under `-m live`. Not marked `live`: it
always runs as part of the ordinary default suite.

Why this file exists: test_live_pmdg.py's four PMDG-only tests now depend
on `require_pmdg`, which skips them unless the loaded aircraft looks like a
PMDG. Skipping the tests when it should NOT (the Cessna Citation Longitude
this project's live suite normally runs against, or any other non-PMDG) is
the fix; that half is exercised live, automatically, every time the live
suite runs with a non-PMDG loaded -- see the four now-skip-instead-of-fail
tests themselves. What live testing can't cheaply prove on its own is the
other half: that a PMDG-looking title does NOT skip, i.e. this fix did not
quietly turn those four tests into a permanent no-op that would never fail
again even on a genuine regression. That is what a fake title, here, is
for.
"""
from __future__ import annotations

import pytest

from .conftest import _is_pmdg_title, _skip_unless_pmdg


@pytest.mark.parametrize("title", [
    "PMDG 737-800NG3",
    "PMDG 777-300ER",
    "pmdg 737-600",  # case-insensitive
])
def test_is_pmdg_title_matches_known_pmdg_titles(title):
    assert _is_pmdg_title(title) is True


@pytest.mark.parametrize("title", [
    "Asobo Cessna Citation Longitude Passengers",  # this project's actual live aircraft
    "Boeing 747-8i",
    "737-600 PAX TC",  # live-verified: a real, unbranded PMDG 737-600's TITLE --
    "",                # this gate is documented to (acceptably) miss it, see conftest.py
    None,
])
def test_is_pmdg_title_rejects_non_pmdg_titles(title):
    assert _is_pmdg_title(title) is False


def test_skip_unless_pmdg_does_not_skip_for_a_pmdg_title():
    """The regression this fix must not introduce. Under a title the gate
    considers a PMDG, _skip_unless_pmdg must return normally -- not call
    pytest.skip -- so the calling test's own body still runs, and can
    still fail on a real regression exactly as before this fix."""
    _skip_unless_pmdg("PMDG 737-800NG3")  # must not raise


def test_skip_unless_pmdg_skips_for_a_non_pmdg_title():
    """The other half: an obviously-wrong aircraft must actually skip, with
    a message naming both what was loaded and what was needed."""
    with pytest.raises(pytest.skip.Exception) as exc_info:
        _skip_unless_pmdg("Asobo Cessna Citation Longitude Passengers")

    message = str(exc_info.value)
    assert "PMDG" in message
    assert "Asobo Cessna Citation Longitude Passengers" in message
