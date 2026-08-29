"""Tests for the HubHop API client (no network calls)."""


import pytest

from simconnect_mcp.data.hubhop import HubHopClient

# -- Fixtures --

SAMPLE_PRESETS = [
    {
        "id": "aaa",
        "vendor": "FenixSim",
        "aircraft": "A320",
        "system": "Engines",
        "label": "ENG_MODE_CRANK",
        "code": "0 (>L:S_ENG_MODE)",
        "presetType": "Input",
    },
    {
        "id": "bbb",
        "vendor": "FenixSim",
        "aircraft": "A320",
        "system": "Engines",
        "label": "ENG_MODE_NORM",
        "code": "1 (>L:S_ENG_MODE)",
        "presetType": "Input",
    },
    {
        "id": "ccc",
        "vendor": "FenixSim",
        "aircraft": "A320",
        "system": "Autopilot",
        "label": "AP1_READ",
        "code": "(L:I_FCU_AP1)",
        "presetType": "Output",
    },
    {
        "id": "ddd",
        "vendor": "FenixSim",
        "aircraft": "A320",
        "system": "EFIS",
        "label": "BARO_SET",
        "code": "(L:E_FCU_EFIS1_BARO) 1 + (>L:E_FCU_EFIS1_BARO)",
        "presetType": "Input",
    },
    {
        "id": "eee",
        "vendor": "PMDG",
        "aircraft": "B737-700",
        "system": "Engines",
        "label": "ENG1_START",
        "code": "1 (>L:PMDG_ENG1_START)",
        "presetType": "Input",
    },
]


@pytest.fixture
def client():
    c = HubHopClient(cache=False)
    # Inject sample data instead of fetching
    c._cache = SAMPLE_PRESETS
    c._use_cache = True
    return c


# -- Tests --


class TestFetchPresets:
    def test_filter_by_vendor(self, client):
        result = client.fetch_presets(vendor="FenixSim")
        assert len(result) == 4
        assert all(p["vendor"] == "FenixSim" for p in result)

    def test_filter_by_vendor_and_system(self, client):
        result = client.fetch_presets(vendor="FenixSim", system="Engines")
        assert len(result) == 2

    def test_filter_by_preset_type(self, client):
        result = client.fetch_presets(vendor="FenixSim", preset_type="Output")
        assert len(result) == 1
        assert result[0]["label"] == "AP1_READ"

    def test_no_filter_returns_all(self, client):
        result = client.fetch_presets()
        assert len(result) == 5


class TestExtractLvars:
    def test_extracts_unique_vars(self, client):
        presets = client.fetch_presets(vendor="FenixSim")
        lvars = client.extract_lvars(presets)
        assert "S_ENG_MODE" in lvars
        assert "I_FCU_AP1" in lvars
        assert "E_FCU_EFIS1_BARO" in lvars
        assert len(lvars) == 3

    def test_detects_writable(self, client):
        presets = client.fetch_presets(vendor="FenixSim")
        lvars = client.extract_lvars(presets)
        assert lvars["S_ENG_MODE"]["writable"] is True
        assert lvars["E_FCU_EFIS1_BARO"]["writable"] is True
        assert lvars["I_FCU_AP1"]["writable"] is False

    def test_collects_systems(self, client):
        presets = client.fetch_presets(vendor="FenixSim")
        lvars = client.extract_lvars(presets)
        assert "Engines" in lvars["S_ENG_MODE"]["systems"]
        assert "Autopilot" in lvars["I_FCU_AP1"]["systems"]

    def test_excludes_other_vendors(self, client):
        presets = client.fetch_presets(vendor="FenixSim")
        lvars = client.extract_lvars(presets)
        assert "PMDG_ENG1_START" not in lvars


class TestBuildCatalog:
    def test_builds_complete_catalog(self, client):
        presets = client.fetch_presets(vendor="FenixSim")
        catalog = client.build_catalog(
            presets, aircraft="Fenix A320", title_pattern="Fenix"
        )
        assert catalog["aircraft"] == "Fenix A320"
        assert catalog["title_pattern"] == "Fenix"
        assert len(catalog["variables"]) == 3
        assert "panels" in catalog

    def test_variable_structure(self, client):
        presets = client.fetch_presets(vendor="FenixSim")
        catalog = client.build_catalog(
            presets, aircraft="Fenix A320", title_pattern="Fenix"
        )
        var_map = {v["name"]: v for v in catalog["variables"]}

        eng = var_map["S_ENG_MODE"]
        assert eng["prefix"] == "S"
        assert eng["writable"] is True
        assert eng["display_name"] == "Eng Mode"
        assert eng["category"] == "ENGINES"

        ap = var_map["I_FCU_AP1"]
        assert ap["prefix"] == "I"
        assert ap["writable"] is False

    def test_panels_match_categories(self, client):
        presets = client.fetch_presets(vendor="FenixSim")
        catalog = client.build_catalog(
            presets, aircraft="Fenix A320", title_pattern="Fenix"
        )
        for var in catalog["variables"]:
            cat = var["category"]
            assert cat in catalog["panels"]
            assert var["name"] in catalog["panels"][cat]


class TestMergeCatalog:
    def test_merge_adds_new_vars(self, client):
        existing = {
            "aircraft": "Fenix A320",
            "title_pattern": "Fenix",
            "variables": [
                {"name": "S_ENG_MODE", "category": "ENGINES", "prefix": "S", "writable": True},
            ],
            "panels": {"ENGINES": ["S_ENG_MODE"]},
        }
        presets = client.fetch_presets(vendor="FenixSim")
        merged = client.merge_catalog(presets, existing)

        names = {v["name"] for v in merged["variables"]}
        assert "S_ENG_MODE" in names  # kept
        assert "I_FCU_AP1" in names  # added
        assert "E_FCU_EFIS1_BARO" in names  # added
        assert len(merged["variables"]) == 3

    def test_merge_does_not_duplicate(self, client):
        existing = {
            "aircraft": "Fenix A320",
            "title_pattern": "Fenix",
            "variables": [
                {"name": "S_ENG_MODE", "category": "ENGINES", "prefix": "S", "writable": True},
                {"name": "I_FCU_AP1", "category": "AUTOPILOT", "prefix": "I", "writable": False},
                {"name": "E_FCU_EFIS1_BARO", "category": "EFIS", "prefix": "E", "writable": True},
            ],
            "panels": {},
        }
        presets = client.fetch_presets(vendor="FenixSim")
        merged = client.merge_catalog(presets, existing)
        assert len(merged["variables"]) == 3  # no duplicates

    def test_merge_does_not_mutate_original(self, client):
        existing = {
            "aircraft": "Fenix A320",
            "title_pattern": "Fenix",
            "variables": [
                {"name": "S_ENG_MODE", "category": "ENGINES", "prefix": "S", "writable": True},
            ],
            "panels": {"ENGINES": ["S_ENG_MODE"]},
        }
        presets = client.fetch_presets(vendor="FenixSim")
        merged = client.merge_catalog(presets, existing)
        assert len(existing["variables"]) == 1  # original unchanged


class TestListMethods:
    def test_list_vendors(self, client):
        vendors = client.list_vendors()
        names = [v["vendor"] for v in vendors]
        assert "FenixSim" in names
        assert "PMDG" in names
        fenix = next(v for v in vendors if v["vendor"] == "FenixSim")
        assert fenix["presets"] == 4

    def test_list_aircraft(self, client):
        aircraft = client.list_aircraft(vendor="FenixSim")
        assert len(aircraft) == 1
        assert aircraft[0]["aircraft"] == "A320"

    def test_list_systems(self, client):
        systems = client.list_systems(vendor="FenixSim")
        sys_names = [s["system"] for s in systems]
        assert "Engines" in sys_names
        assert "Autopilot" in sys_names
        assert "EFIS" in sys_names


class TestDisplayName:
    @pytest.mark.parametrize("var_name,expected", [
        ("S_ENG_MODE", "Eng Mode"),
        ("I_FCU_AP1", "Fcu Ap1"),
        ("CB_A01_HP_FUEL_SOV_1", "CB A01 Hp Fuel Sov 1"),
        ("A_ASP_VHF_1_VOLUME", "Asp Vhf 1 Volume"),
        ("SOME_UNKNOWN_VAR", "Some Unknown Var"),
    ])
    def test_make_display_name(self, var_name, expected):
        assert HubHopClient._make_display_name(var_name) == expected
