"""MobiFlight HubHop API client for extending L-var catalogs.

Fetches community-sourced presets from the HubHop database
(https://hubhop.mobiflight.com) and extracts L-var names to
build or extend aircraft-specific variable catalogs.

Usage as a library::

    from simconnect_mcp.data.hubhop import HubHopClient

    client = HubHopClient()
    presets = client.fetch_presets(vendor="FenixSim")
    lvars = client.extract_lvars(presets)
    catalog = client.build_catalog(
        presets,
        aircraft="Fenix A320/A321",
        title_pattern="Fenix",
    )
    client.save_catalog(catalog, "fenix_a320.json")

Usage from the command line::

    # List available vendors and aircraft
    python -m simconnect_mcp.data.hubhop --list-vendors
    python -m simconnect_mcp.data.hubhop --list-aircraft --vendor FenixSim

    # Preview what a catalog would contain
    python -m simconnect_mcp.data.hubhop --vendor FenixSim --dry-run

    # Generate a new catalog file
    python -m simconnect_mcp.data.hubhop --vendor FenixSim \\
        --aircraft-name "Fenix A320/A321" \\
        --title-pattern Fenix \\
        --output fenix_a320.json

    # Merge new vars into an existing catalog
    python -m simconnect_mcp.data.hubhop --vendor FenixSim \\
        --merge fenix_a320.json
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

API_URL = "https://hubhop-api-mgtm.azure-api.net/api/v1/msfs2020/presets"
USER_AGENT = "simconnect-mcp/0.1"

# L-var extraction patterns from RPN calculator code
_LVAR_READ = re.compile(r"\(L:([A-Za-z0-9_]+)")
_LVAR_WRITE = re.compile(r"\(>L:([A-Za-z0-9_]+)")

# Fenix prefix conventions — used to infer writability and type
_WRITABLE_PREFIXES = {"S", "A", "E"}

# Category normalization — maps HubHop system names to cleaner categories
_SYSTEM_ALIASES: dict[str, str] = {
    "Anti-Ice": "ANTI-ICE",
    "Air Conditioning": "AIR CONDITIONING",
    "Calls": "COMMUNICATION",
    "Cargo": "CARGO",
    "Clock": "MISCELLANEOUS",
    "Communication": "COMMUNICATION",
    "Doors": "DOORS",
    "ECAM": "ECAM",
    "EWD/SD": "ECAM",
    "EFIS": "EFIS",
    "Electrical": "ELECTRICAL",
    "Engines": "ENGINES",
    "Evacuation": "SAFETY",
    "Fire Protection": "FIRE",
    "Flight Controls": "FLIGHT CONTROLS",
    "Fuel": "FUEL",
    "Hydraulics": "HYDRAULIC",
    "Landing Gear": "GEAR",
    "Lights": "LIGHTS",
    "MCDU": "MCDU",
    "Maintenance": "MISCELLANEOUS",
    "Navigation": "NAVIGATION",
    "Oxygen": "SAFETY",
    "Pressurization": "AIR CONDITIONING",
    "Recording": "MISCELLANEOUS",
    "Warnings": "WARNING",
    "Wipers": "MISCELLANEOUS",
    "ATC/TCAS": "ATC/TCAS",
    "Autopilot": "AUTOPILOT",
    "Avionics": "AVIONICS",
    "Radio": "RADIO",
    "Miscellaneous": "MISCELLANEOUS",
}


class HubHopClient:
    """Client for the MobiFlight HubHop preset API."""

    def __init__(self, api_url: str = API_URL, cache: bool = True) -> None:
        self._api_url = api_url
        self._cache: list[dict] | None = None
        self._use_cache = cache

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def fetch_all(self) -> list[dict]:
        """Fetch the complete preset database (~31K presets, ~17 MB).

        Results are cached in memory for the lifetime of the client.
        """
        if self._use_cache and self._cache is not None:
            return self._cache

        logger.info("Fetching presets from %s …", self._api_url)
        req = urllib.request.Request(
            self._api_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data: list[dict] = json.loads(resp.read())

        logger.info("Fetched %d presets", len(data))
        if self._use_cache:
            self._cache = data
        return data

    def fetch_presets(
        self,
        vendor: str | None = None,
        aircraft: str | None = None,
        system: str | None = None,
        preset_type: str | None = None,
    ) -> list[dict]:
        """Fetch and filter presets. Filtering is client-side.

        Args:
            vendor: Filter by vendor name (e.g. "FenixSim", "PMDG").
            aircraft: Filter by aircraft model (e.g. "A320", "B737-700").
            system: Filter by system (e.g. "Engines", "Autopilot").
            preset_type: Filter by type ("Input", "Output", "Input (Potentiometer)").
        """
        data = self.fetch_all()
        if vendor:
            data = [p for p in data if p.get("vendor") == vendor]
        if aircraft:
            data = [p for p in data if p.get("aircraft") == aircraft]
        if system:
            data = [p for p in data if p.get("system") == system]
        if preset_type:
            data = [p for p in data if p.get("presetType") == preset_type]
        return data

    # ------------------------------------------------------------------
    # Listing / discovery
    # ------------------------------------------------------------------

    def list_vendors(self) -> list[dict[str, Any]]:
        """List all vendors with preset counts."""
        counts: dict[str, int] = defaultdict(int)
        for p in self.fetch_all():
            counts[p.get("vendor", "Unknown")] += 1
        return sorted(
            [{"vendor": v, "presets": c} for v, c in counts.items()],
            key=lambda x: -x["presets"],
        )

    def list_aircraft(self, vendor: str | None = None) -> list[dict[str, Any]]:
        """List aircraft models, optionally filtered by vendor."""
        counts: dict[str, set[str]] = defaultdict(set)
        aircraft_vendors: dict[str, str] = {}
        for p in self.fetch_all():
            v = p.get("vendor", "Unknown")
            if vendor and v != vendor:
                continue
            a = p.get("aircraft", "Unknown")
            counts[a].add(p.get("system", ""))
            aircraft_vendors[a] = v
        return sorted(
            [
                {
                    "aircraft": a,
                    "vendor": aircraft_vendors[a],
                    "systems": len(systems),
                }
                for a, systems in counts.items()
            ],
            key=lambda x: x["aircraft"],
        )

    def list_systems(
        self, vendor: str | None = None, aircraft: str | None = None
    ) -> list[dict[str, Any]]:
        """List systems with preset counts."""
        counts: dict[str, int] = defaultdict(int)
        for p in self.fetch_presets(vendor=vendor, aircraft=aircraft):
            counts[p.get("system", "Unknown")] += 1
        return sorted(
            [{"system": s, "presets": c} for s, c in counts.items()],
            key=lambda x: -x["presets"],
        )

    # ------------------------------------------------------------------
    # L-var extraction
    # ------------------------------------------------------------------

    def extract_lvars(self, presets: list[dict]) -> dict[str, dict[str, Any]]:
        """Extract unique L-var names from preset RPN code.

        Returns:
            Dict mapping var name to metadata:
            ``{"systems": set, "labels": set, "writable": bool}``
        """
        lvars: dict[str, dict[str, Any]] = {}

        for p in presets:
            code = p.get("code", "")
            system = p.get("system", "Unknown")
            label = p.get("label", "")

            read_vars = _LVAR_READ.findall(code)
            write_vars = _LVAR_WRITE.findall(code)
            all_vars = set(read_vars) | set(write_vars)

            for var_name in all_vars:
                if var_name not in lvars:
                    lvars[var_name] = {
                        "systems": set(),
                        "labels": set(),
                        "writable": False,
                    }
                lvars[var_name]["systems"].add(system)
                lvars[var_name]["labels"].add(label)
                if var_name in write_vars:
                    lvars[var_name]["writable"] = True

        return lvars

    # ------------------------------------------------------------------
    # Catalog building
    # ------------------------------------------------------------------

    @staticmethod
    def _make_display_name(var_name: str) -> str:
        """Generate a human-readable display name from a variable name."""
        parts = var_name.split("_")
        # Strip known prefix
        if len(parts) > 1 and parts[0] in ("S", "A", "E", "N", "I", "B", "CB", "OH"):
            prefix = parts[0]
            rest = parts[1:]
            if prefix == "CB":
                return "CB " + " ".join(p.capitalize() for p in rest)
            return " ".join(p.capitalize() for p in rest)
        return " ".join(p.capitalize() for p in parts)

    @staticmethod
    def _classify_category(systems: set[str]) -> str:
        """Map HubHop system names to a normalized category."""
        if not systems:
            return "MISCELLANEOUS"
        primary = sorted(systems)[0]
        return _SYSTEM_ALIASES.get(primary, primary.upper())

    def build_catalog(
        self,
        presets: list[dict],
        aircraft: str,
        title_pattern: str,
        category_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Build a complete catalog dict from presets.

        Args:
            presets: Filtered preset list (e.g. from ``fetch_presets``).
            aircraft: Human-readable aircraft name.
            title_pattern: Substring matched against the TITLE SimVar.
            category_map: Optional overrides for system->category mapping.

        Returns:
            Catalog dict ready to save as JSON.
        """
        aliases = {**_SYSTEM_ALIASES, **(category_map or {})}
        lvars = self.extract_lvars(presets)

        variables: list[dict[str, Any]] = []
        panels: dict[str, list[str]] = defaultdict(list)

        for var_name in sorted(lvars):
            info = lvars[var_name]
            prefix = var_name.split("_")[0] if "_" in var_name else ""

            # Determine category
            systems = info["systems"]
            primary = sorted(systems)[0] if systems else "Miscellaneous"
            category = aliases.get(primary, primary.upper())

            # Determine writability — trust the write pattern, or prefix convention
            writable = info["writable"] or prefix in _WRITABLE_PREFIXES

            entry: dict[str, Any] = {
                "name": var_name,
                "display_name": self._make_display_name(var_name),
                "category": category,
                "prefix": prefix,
                "writable": writable,
            }
            variables.append(entry)
            panels[category].append(var_name)

        catalog = {
            "aircraft": aircraft,
            "title_pattern": title_pattern,
            "variables": variables,
            "panels": dict(sorted(panels.items())),
        }

        logger.info(
            "Built catalog: %d variables, %d panels",
            len(variables),
            len(panels),
        )
        return catalog

    def merge_catalog(
        self,
        presets: list[dict],
        existing: dict[str, Any],
        category_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Merge new variables from presets into an existing catalog.

        Variables already present (by name) are skipped.
        New panels are created as needed; existing panels are extended.

        Args:
            presets: Filtered preset list.
            existing: The existing catalog dict (will not be mutated).
            category_map: Optional overrides for system->category mapping.

        Returns:
            New catalog dict with merged variables.
        """
        aliases = {**_SYSTEM_ALIASES, **(category_map or {})}
        lvars = self.extract_lvars(presets)

        # Deep copy existing
        catalog = json.loads(json.dumps(existing))
        existing_names = {v["name"] for v in catalog.get("variables", [])}

        # Rebuild panels from variables for consistency
        panels: dict[str, list[str]] = defaultdict(list)
        for v in catalog.get("variables", []):
            panels[v.get("category", "MISCELLANEOUS")].append(v["name"])

        added = 0
        for var_name in sorted(lvars):
            if var_name in existing_names:
                continue

            info = lvars[var_name]
            prefix = var_name.split("_")[0] if "_" in var_name else ""
            systems = info["systems"]
            primary = sorted(systems)[0] if systems else "Miscellaneous"
            category = aliases.get(primary, primary.upper())
            writable = info["writable"] or prefix in _WRITABLE_PREFIXES

            entry: dict[str, Any] = {
                "name": var_name,
                "display_name": self._make_display_name(var_name),
                "category": category,
                "prefix": prefix,
                "writable": writable,
            }
            catalog["variables"].append(entry)
            panels[category].append(var_name)
            added += 1

        catalog["panels"] = dict(sorted(panels.items()))

        logger.info(
            "Merged %d new variables (total: %d)", added, len(catalog["variables"])
        )
        return catalog

    # ------------------------------------------------------------------
    # I/O helpers
    # ------------------------------------------------------------------

    @staticmethod
    def save_catalog(
        catalog: dict[str, Any],
        path: str | Path,
    ) -> Path:
        """Save a catalog dict to a JSON file.

        If ``path`` is a filename without a directory, it is saved to the
        default data directory (``src/simconnect_mcp/data/``).
        """
        dest = Path(path)
        if not dest.is_absolute() and dest.parent == Path("."):
            dest = Path(__file__).parent / dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2)
        logger.info("Saved catalog to %s", dest)
        return dest

    @staticmethod
    def load_catalog(path: str | Path) -> dict[str, Any]:
        """Load a catalog dict from a JSON file."""
        src = Path(path)
        if not src.is_absolute() and src.parent == Path("."):
            src = Path(__file__).parent / src
        with open(src, encoding="utf-8") as f:
            return json.load(f)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------


def _main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="MobiFlight HubHop -> L-var catalog generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  %(prog)s --list-vendors\n"
            "  %(prog)s --list-aircraft --vendor FenixSim\n"
            "  %(prog)s --vendor FenixSim --dry-run\n"
            "  %(prog)s --vendor FenixSim --aircraft-name \"Fenix A320\" "
            "--title-pattern Fenix -o fenix_a320.json\n"
            "  %(prog)s --vendor FenixSim --merge fenix_a320.json\n"
        ),
    )

    # Discovery
    parser.add_argument("--list-vendors", action="store_true",
                        help="List all vendors with preset counts")
    parser.add_argument("--list-aircraft", action="store_true",
                        help="List aircraft models (optionally filtered by --vendor)")
    parser.add_argument("--list-systems", action="store_true",
                        help="List systems (optionally filtered by --vendor/--aircraft)")

    # Filtering
    parser.add_argument("--vendor", help="Filter by vendor (e.g. FenixSim)")
    parser.add_argument("--aircraft", help="Filter by HubHop aircraft model (e.g. A320)")

    # Catalog generation
    parser.add_argument("--aircraft-name",
                        help="Human-readable aircraft name for the catalog")
    parser.add_argument("--title-pattern",
                        help="Substring to match against TITLE SimVar")
    parser.add_argument("-o", "--output", help="Output JSON file path")
    parser.add_argument("--merge", metavar="FILE",
                        help="Merge new vars into an existing catalog file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be generated without writing")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    client = HubHopClient()

    # --- Discovery commands ---
    if args.list_vendors:
        vendors = client.list_vendors()
        print(f"{'Vendor':<35} Presets")
        print("-" * 50)
        for v in vendors:
            print(f"{v['vendor']:<35} {v['presets']:>6}")
        print(f"\nTotal: {len(vendors)} vendors, "
              f"{sum(v['presets'] for v in vendors)} presets")
        return

    if args.list_aircraft:
        aircraft = client.list_aircraft(vendor=args.vendor)
        header = f"Aircraft for {args.vendor}" if args.vendor else "All aircraft"
        print(f"{header}\n")
        print(f"{'Aircraft':<35} {'Vendor':<25} Systems")
        print("-" * 70)
        for a in aircraft:
            print(f"{a['aircraft']:<35} {a['vendor']:<25} {a['systems']:>4}")
        print(f"\nTotal: {len(aircraft)} aircraft")
        return

    if args.list_systems:
        systems = client.list_systems(vendor=args.vendor, aircraft=args.aircraft)
        print(f"{'System':<35} Presets")
        print("-" * 50)
        for s in systems:
            print(f"{s['system']:<35} {s['presets']:>6}")
        print(f"\nTotal: {len(systems)} systems")
        return

    # --- Catalog commands ---
    if not args.vendor and not args.merge:
        parser.print_help()
        sys.exit(1)

    presets = client.fetch_presets(vendor=args.vendor, aircraft=args.aircraft)
    if not presets:
        print(f"No presets found for vendor={args.vendor}, aircraft={args.aircraft}")
        sys.exit(1)

    lvars = client.extract_lvars(presets)
    print(f"Found {len(presets)} presets -> {len(lvars)} unique L-vars")

    # Show prefix breakdown
    prefix_counts: dict[str, int] = defaultdict(int)
    for name in lvars:
        prefix = name.split("_")[0] if "_" in name else "?"
        prefix_counts[prefix] += 1
    print("\nBy prefix:")
    for prefix, count in sorted(prefix_counts.items(), key=lambda x: -x[1]):
        print(f"  {prefix}_: {count}")

    # Merge mode
    if args.merge:
        existing = client.load_catalog(args.merge)
        existing_names = {v["name"] for v in existing.get("variables", [])}
        new_count = sum(1 for n in lvars if n not in existing_names)

        print(f"\nExisting catalog: {len(existing_names)} vars")
        print(f"New vars to add:  {new_count}")

        if args.dry_run:
            new_vars = sorted(n for n in lvars if n not in existing_names)
            for v in new_vars[:30]:
                info = lvars[v]
                w = "W" if info["writable"] or v.split("_")[0] in _WRITABLE_PREFIXES else "R"
                print(f"  [{w}] {v}  ({', '.join(sorted(info['systems']))})")
            if len(new_vars) > 30:
                print(f"  … and {len(new_vars) - 30} more")
            return

        if new_count == 0:
            print("Nothing to add — catalog is up to date.")
            return

        catalog = client.merge_catalog(presets, existing)
        dest = client.save_catalog(catalog, args.merge)
        print(f"\nSaved merged catalog to {dest}")
        print(f"Total: {len(catalog['variables'])} vars, "
              f"{len(catalog['panels'])} panels")
        return

    # Build new catalog
    aircraft_name = args.aircraft_name or args.vendor or "Unknown"
    title_pattern = args.title_pattern or args.vendor or ""

    if args.dry_run:
        print("\nWould create catalog:")
        print(f"  aircraft:      {aircraft_name}")
        print(f"  title_pattern: {title_pattern}")
        print(f"  variables:     {len(lvars)}")

        # Show category breakdown
        categories: dict[str, int] = defaultdict(int)
        for info in lvars.values():
            cat = HubHopClient._classify_category(info["systems"])
            categories[cat] += 1
        print(f"  panels:        {len(categories)}")
        print("\n  Categories:")
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            print(f"    {cat}: {count}")
        return

    catalog = client.build_catalog(
        presets,
        aircraft=aircraft_name,
        title_pattern=title_pattern,
    )
    output = args.output or f"{args.vendor.lower().replace(' ', '_')}.json"
    dest = client.save_catalog(catalog, output)
    print(f"\nSaved catalog to {dest}")
    print(f"Total: {len(catalog['variables'])} vars, "
          f"{len(catalog['panels'])} panels")


if __name__ == "__main__":
    _main()
