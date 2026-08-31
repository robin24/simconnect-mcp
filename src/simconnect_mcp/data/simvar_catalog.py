"""SimVar catalog loading, unit resolution and search.

Lives in `data/` rather than `tools/` because `simvar_access` needs unit
resolution and must not import from the tools package.

SimConnect requires a unit string on every data definition, so the catalog's
`unit` field is the default-unit source for reads and writes.  That makes
search results and actual reads agree on units by construction.
"""
from __future__ import annotations

import difflib
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).parent / "simvars_catalog.json"

_catalog: dict[str, list[dict]] | None = None
_flat: list[dict] | None = None
_by_name: dict[str, dict] | None = None


def load_catalog() -> dict[str, list[dict]]:
    """Load the bundled SimVar catalog, or fall back to a minimal builtin set."""
    global _catalog, _flat, _by_name
    if _catalog is not None:
        return _catalog

    catalog: dict[str, list[dict]] = {}
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        for category, entries in raw.items():
            parsed = [
                {
                    "name": e["name"],
                    "category": category,
                    "description": e.get("description", ""),
                    "units": e.get("unit", ""),
                    "settable": e.get("settable", False),
                }
                for e in entries
            ]
            if parsed:
                catalog[category] = parsed
    except Exception:
        log.warning("Could not read %s; using builtin catalog", CATALOG_PATH, exc_info=True)
        catalog = {}

    if not catalog:
        catalog = _builtin_catalog()

    _catalog = catalog
    _flat = [v for entries in catalog.values() for v in entries]
    _by_name = {v["name"]: v for v in _flat}
    return _catalog


def _builtin_catalog() -> dict[str, list[dict]]:
    """Last-resort catalog if the bundled JSON is missing or unreadable."""
    return {
        "Aircraft Position": [
            {"name": "PLANE_LATITUDE", "category": "Aircraft Position", "units": "degrees",
             "settable": True, "description": "Latitude of aircraft"},
            {"name": "PLANE_LONGITUDE", "category": "Aircraft Position", "units": "degrees",
             "settable": True, "description": "Longitude of aircraft"},
            {"name": "PLANE_ALTITUDE", "category": "Aircraft Position", "units": "feet",
             "settable": True, "description": "Altitude of aircraft"},
            {"name": "SIM_ON_GROUND", "category": "Aircraft Position", "units": "bool",
             "settable": False, "description": "Whether aircraft is on the ground"},
        ],
        "Miscellaneous": [
            {"name": "TITLE", "category": "Miscellaneous", "units": "string",
             "settable": False, "description": "Aircraft title"},
        ],
    }


def flat_simvars() -> list[dict]:
    load_catalog()
    assert _flat is not None
    return _flat


def lookup(name: str) -> dict | None:
    """Catalog entry for a SimVar name.

    The bundled catalog stores indexed variables under keys carrying a
    literal ':index' suffix ("ENG_N1_RPM:index"), while callers pass the bare
    name plus a separate index argument -- or occasionally a concrete
    "NAME:1".  Both forms must resolve to the same entry.
    """
    load_catalog()
    assert _by_name is not None
    base = name.split(":", 1)[0].strip().upper()
    return _by_name.get(base) or _by_name.get(f"{base}:index")


def _sanitize_catalog_unit(unit_str: str) -> str:
    """Strip prose suffixes from catalog unit strings to yield valid SimConnect units.

    The bundled catalog contains descriptive prose like "Rpm (0 to 16384 = 0 to 100%)"
    that SimConnect's AddToDataDefinition rejects. This strips the parenthetical suffix
    and handles special cases. Only applied to catalog units, never to caller-supplied
    explicit units.
    """
    # Special case: Bool/String is genuinely ambiguous; pick Bool
    if unit_str.strip().lower() == "bool/string":
        return "Bool"

    # Strip everything from the first '(' onward
    if "(" in unit_str:
        unit_str = unit_str.split("(", 1)[0]

    # Strip trailing whitespace
    unit_str = unit_str.strip()

    # If completely empty after stripping, fall back to "number"
    if not unit_str:
        return "number"

    return unit_str


def resolve_unit(name: str, explicit: str | None) -> str:
    """Resolve the unit for a data definition: explicit, then catalog, then number."""
    if explicit:
        return explicit.strip()
    entry = lookup(name)
    if entry and entry.get("units"):
        raw_unit = str(entry["units"]).strip()
        return _sanitize_catalog_unit(raw_unit)
    return "number"


def is_string_var(name: str) -> bool:
    """True when the catalog says this variable is a string (TITLE, ATC_ID, ...)."""
    entry = lookup(name)
    return bool(entry) and str(entry.get("units", "")).strip().lower() == "string"


def search_catalog(keyword: str, category: str | None = None) -> list[dict]:
    """Match keyword against name and description. Uncapped — callers paginate."""
    keyword_lower = keyword.lower()
    results = []
    for var in flat_simvars():
        if category and var.get("category", "").lower() != category.lower():
            continue
        haystack = f"{var.get('name', '')} {var.get('description', '')}".lower()
        if keyword_lower in haystack:
            results.append(var)
    return results


def suggest_names(name: str, limit: int = 5) -> list[str]:
    """Close catalog matches for a mistyped SimVar name."""
    candidates = [v["name"] for v in flat_simvars()]
    return difflib.get_close_matches(
        name.strip().upper().replace(" ", "_"), candidates, n=limit, cutoff=0.6
    )
