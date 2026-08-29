from simconnect_mcp.data import simvar_catalog


def test_catalog_loads_bundled_json():
    catalog = simvar_catalog.load_catalog()
    assert len(catalog) >= 20
    assert sum(len(v) for v in catalog.values()) >= 1000


def test_resolve_unit_prefers_explicit_argument():
    assert simvar_catalog.resolve_unit("PLANE_ALTITUDE", "meters") == "meters"


def test_resolve_unit_falls_back_to_catalog():
    # PLANE_ALTITUDE is in the bundled catalog with a length unit.
    assert simvar_catalog.resolve_unit("PLANE_ALTITUDE", None) not in ("", None)


def test_resolve_unit_defaults_to_number_for_unknown_var():
    assert simvar_catalog.resolve_unit("NOT_A_REAL_SIMVAR_XYZ", None) == "number"


def test_is_string_var_detects_title():
    assert simvar_catalog.is_string_var("TITLE") is True
    assert simvar_catalog.is_string_var("PLANE_ALTITUDE") is False


def test_search_is_not_capped_at_fifty():
    """The old implementation hard-sliced [:50]; callers must paginate instead."""
    results = simvar_catalog.search_catalog("e", None)
    assert len(results) > 50


def test_suggest_names_finds_close_match_for_typo():
    suggestions = simvar_catalog.suggest_names("PLANE_ALTITUDE_XX")
    assert "PLANE_ALTITUDE" in suggestions


def test_lookup_finds_indexed_catalog_entries():
    """231 of 1081 catalog entries are stored as 'NAME:index'. Stripping the
    index from the query alone never matches them."""
    assert simvar_catalog.lookup("ENG_N1_RPM") is not None
    assert simvar_catalog.lookup("GENERAL_ENG_THROTTLE_LEVER_POSITION") is not None


def test_resolve_unit_uses_the_catalog_unit_for_indexed_vars():
    assert simvar_catalog.resolve_unit("ENG_N1_RPM", None) != "number"


def test_lookup_accepts_a_concrete_index_in_the_query():
    """Callers pass 'ENG_N1_RPM' with index=1, but may also pass 'ENG_N1_RPM:1'."""
    assert simvar_catalog.lookup("ENG_N1_RPM:1") is not None


def test_plain_entries_still_resolve():
    assert simvar_catalog.resolve_unit("PLANE_ALTITUDE", None).lower() == "feet"


def test_unknown_name_still_falls_back_to_number():
    assert simvar_catalog.resolve_unit("NOT_A_REAL_SIMVAR_XYZ", None) == "number"


def test_no_catalog_entry_is_unreachable_by_lookup():
    """Every shipped catalog entry must be reachable through the public API."""
    unreachable = [
        v["name"] for v in simvar_catalog.flat_simvars()
        if simvar_catalog.lookup(v["name"]) is None
    ]
    assert unreachable == []
