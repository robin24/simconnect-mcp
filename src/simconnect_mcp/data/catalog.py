"""Aircraft L-var catalog loader and search engine."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent

# Loaded catalogs: aircraft_key → catalog dict
_catalogs: dict[str, dict] = {}

# Map title substrings to catalog keys
_TITLE_PATTERNS: dict[str, str] = {}


def _load_all_catalogs() -> None:
    """Load all JSON catalog files from the data directory."""
    if _catalogs:
        return

    for path in DATA_DIR.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            key = path.stem  # e.g., "fenix_a320"
            _catalogs[key] = data

            # Register title pattern for auto-detection
            pattern = data.get("title_pattern", "")
            if pattern:
                _TITLE_PATTERNS[pattern.lower()] = key

            logger.info("Loaded L-var catalog: %s (%d variables)",
                        key, len(data.get("variables", [])))
        except Exception as e:
            logger.warning("Failed to load catalog %s: %s", path, e)


def detect_catalog(aircraft_title: str) -> str | None:
    """Detect which catalog matches the given aircraft title."""
    _load_all_catalogs()
    title_lower = aircraft_title.lower()
    for pattern, key in _TITLE_PATTERNS.items():
        if pattern in title_lower:
            return key
    return None


def get_catalog(key: str) -> dict | None:
    """Get a specific catalog by key."""
    _load_all_catalogs()
    return _catalogs.get(key)


def list_catalogs() -> list[dict[str, Any]]:
    """List all available catalogs."""
    _load_all_catalogs()
    result = []
    for key, data in _catalogs.items():
        result.append({
            "key": key,
            "aircraft": data.get("aircraft", key),
            "title_pattern": data.get("title_pattern", ""),
            "variable_count": len(data.get("variables", [])),
            "panel_count": len(data.get("panels", {})),
        })
    return result


def search_catalog(
    keyword: str,
    catalog_key: str | None = None,
    category: str | None = None,
    writable_only: bool = False,
    prefix: str | None = None,
) -> list[dict]:
    """Search L-var catalogs by keyword.

    Args:
        keyword: Search term (matched against name, display_name, category)
        catalog_key: Specific catalog to search (None = all catalogs)
        category: Filter by panel/category name
        writable_only: Only return writable variables
        prefix: Filter by prefix (S, N, E, I, A, B)

    Returns:
        List of matching variable dicts, each with an added 'catalog' field.
    """
    _load_all_catalogs()
    results = []

    # Build search variants for fuzzy matching:
    # "seatbelt" → also match "seat belt", "seat_belt"
    # "autopilot" → also match "auto pilot", "auto_pilot", "ap"
    keyword_lower = keyword.lower()
    search_terms = [keyword_lower]

    # Add variant without spaces/underscores (for "seat belt" → "seatbelt")
    collapsed = keyword_lower.replace(" ", "").replace("_", "")
    if collapsed != keyword_lower:
        search_terms.append(collapsed)

    # Add variant with spaces between word boundaries (for "seatbelt" → "seat belt")
    # Split on common word boundaries in aviation terms
    _EXPANSIONS = {
        "seatbelt": "seat belt", "autopilot": "auto pilot",
        "nosmoking": "no smoking", "antiice": "anti ice",
        "aircon": "air conditioning",
    }
    if collapsed in _EXPANSIONS:
        search_terms.append(_EXPANSIONS[collapsed])

    # Add underscore variant (for matching L-var names like SEAT_BELT)
    search_terms.append(keyword_lower.replace(" ", "_"))

    catalogs_to_search = (
        {catalog_key: _catalogs[catalog_key]}
        if catalog_key and catalog_key in _catalogs
        else _catalogs
    )

    for cat_key, data in catalogs_to_search.items():
        for var in data.get("variables", []):
            # Apply filters
            if writable_only and not var.get("writable", False):
                continue
            if prefix and var.get("prefix", "").upper() != prefix.upper():
                continue
            if category:
                var_cat = var.get("category", "").lower()
                if category.lower() not in var_cat:
                    continue

            # Keyword match — check all search term variants
            searchable = " ".join([
                var.get("name", ""),
                var.get("display_name", ""),
                var.get("category", ""),
                " ".join(var.get("values", {}).values()),
            ]).lower()

            # Match: any search variant matches, OR all individual words match
            matched = any(term in searchable for term in search_terms)
            if not matched and " " in keyword_lower:
                words = keyword_lower.split()
                matched = all(w in searchable for w in words)
            if matched:
                result = {**var, "catalog": cat_key}
                results.append(result)

            if len(results) >= 50:
                return results

    return results


def get_panel_variables(
    panel_name: str,
    catalog_key: str | None = None,
) -> dict[str, Any] | None:
    """Get all variables in a specific panel/category.

    Args:
        panel_name: Panel name (case-insensitive substring match)
        catalog_key: Specific catalog (None = first match)

    Returns:
        Dict with panel info and variables, or None.
    """
    _load_all_catalogs()
    panel_lower = panel_name.lower()

    catalogs_to_search = (
        {catalog_key: _catalogs[catalog_key]}
        if catalog_key and catalog_key in _catalogs
        else _catalogs
    )

    for cat_key, data in catalogs_to_search.items():
        panels = data.get("panels", {})
        for pname, var_names in panels.items():
            if panel_lower in pname.lower():
                # Enrich with full variable info
                var_map = {v["name"]: v for v in data.get("variables", [])}
                variables = []
                for vn in var_names:
                    if vn in var_map:
                        variables.append({**var_map[vn], "catalog": cat_key})
                    else:
                        variables.append({"name": vn, "catalog": cat_key})
                return {
                    "panel": pname,
                    "catalog": cat_key,
                    "count": len(variables),
                    "variables": variables,
                }
    return None


def list_panels(catalog_key: str | None = None) -> list[dict]:
    """List all panels/categories in a catalog."""
    _load_all_catalogs()

    catalogs_to_search = (
        {catalog_key: _catalogs[catalog_key]}
        if catalog_key and catalog_key in _catalogs
        else _catalogs
    )

    results = []
    for cat_key, data in catalogs_to_search.items():
        for pname, var_names in data.get("panels", {}).items():
            results.append({
                "panel": pname,
                "catalog": cat_key,
                "variable_count": len(var_names),
            })
    return results
