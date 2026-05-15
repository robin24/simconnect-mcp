#!/usr/bin/env python3
"""Parse a PMDG SDK header and generate a variable catalog JSON.

Reads any PMDG SDK header (777X, NG3, etc.) and extracts:
  - All struct PMDG_<X>_Data fields (state variables)
  - All EVT_* event defines (control events)
  - Section comments (panel/system groupings)

Then optionally merges with HubHop community presets to produce a
comprehensive catalog JSON compatible with simconnect-mcp's catalog system.

The struct name and data-area constants are auto-detected from the header.
Pass the corresponding CLI flags to override.

Usage:
    python scripts/parse_pmdg_sdk.py <sdk_header_path> [-o output.json] [--hubhop]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# SDK Header Parsing
# ---------------------------------------------------------------------------

# Regex for struct fields:  type  name[array];  // comment
_FIELD_RE = re.compile(
    r"^\s+"
    r"(?P<type>bool|unsigned\s+char|unsigned\s+short|unsigned\s+int|short|int|float|char)"
    r"\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\[(?P<array>\d+)\])?"      # optional array size
    r"(?:\[(?P<array2>\d+)\])?"     # optional second dimension (CDU)
    r"\s*;"
    r"(?:\s*//\s*(?P<comment>.*))?$"
)

# Regex for event defines:  #define EVT_NAME  (THIRD_PARTY_EVENT_ID_MIN + offset)
_EVENT_RE = re.compile(
    r"^\s*#define\s+(?P<name>EVT_\w+)\s+"
    r"\(THIRD_PARTY_EVENT_ID_MIN\s*\+\s*(?P<offset>\d+)\s*\)"
    r"(?:\s*//\s*(?P<comment>.*?))?\s*$"
)

# Regex for offset-based event defines:  #define EVT_NAME  (CDU_EVT_OFFSET_R + EVT_CDU_L_XXX)
_EVENT_OFFSET_RE = re.compile(
    r"^\s*#define\s+(?P<name>EVT_\w+)\s+"
    r"\((?P<base>\w+)\s*\+\s*(?P<ref>EVT_\w+)\)"
)

# Regex for section comments like:  // Overhead Panel
_SECTION_RE = re.compile(
    r"^\s*//\s*(?P<section>(?:Overhead|Forward|Glareshield|Pedestal|Control Stand|"
    r"Aft Aisle|Aisle Stand|CDU|Door|Additional|Center|Left|Right|Standby|"
    r"Master Warning|Display Select|Chronometer|Oxygen|Custom|Window|"
    r"CA and FO|MCP Direct|Panel system|miscellaneous).*?)\s*$",
    re.IGNORECASE,
)

# Section comment lines in the struct that define panels
_PANEL_SECTION_RE = re.compile(
    r"^\s*//\s*(?P<panel>.+?)\s*$"
)

# Value enum pattern in comments like: 0: OFF  1: AUTO  2: ON
_VALUES_RE = re.compile(r"(\d+)\s*:\s*([A-Za-z0-9_/\.\(\) -]+)")

# Array index labels in comments like: left/right, L-Side/L-Fwd/R-Fwd/R-Side
_INDEX_LABELS_RE = re.compile(
    r"(?:left|right|fwd|aft|upper|lower|center|ctr|capt|f/o|observer|"
    r"L-Side|L-Fwd|R-Fwd|R-Side|Left|Right|Nose|primary|secondary|"
    r"FWD|AFT|ENG\s*\d|left\s+fwd|right\s+fwd|left\s+aft|right\s+aft|"
    r"ctr\s+left|ctr\s+right|bulk)",
    re.IGNORECASE,
)

# Data field type to catalog info mapping
_TYPE_MAP = {
    "bool": ("bool", None),
    "unsigned char": ("uint8", "0-255"),
    "unsigned short": ("uint16", "0-65535"),
    "unsigned int": ("uint32", None),
    "short": ("int16", None),
    "int": ("int32", None),
    "float": ("float", None),
    "char": ("char", None),
}

# Panel category mapping from SDK section comments
# Uses prefix-based matching: if a comment line starts with the key, use that category
# Matching is sorted longest-first, so specific 777 keys win over shorter NG3 aliases.
_SECTION_TO_CATEGORY = {
    # --- PMDG 777 subsections ---
    # Overhead Maintenance Panel subsections
    "Backup Window Heat": "OVERHEAD_MAINTENANCE",
    "Standby Power": "ELECTRICAL",
    "APU MAINT": "OVERHEAD_MAINTENANCE",
    "EEC MAINT": "OVERHEAD_MAINTENANCE",
    "ELECTRICAL": "ELECTRICAL",
    "CARGO TEMP SELECTORS": "AIR_CONDITIONING",
    # Overhead Panel subsections
    "ADIRU": "AVIONICS",
    "Electrical": "ELECTRICAL",
    "Wiper Selectors": "MISCELLANEOUS",
    "Emergency Lights": "LIGHTS",
    "Service Interphone": "COMMUNICATION",
    "Passenger Oxygen": "OXYGEN",
    "Window Heat": "ANTI_ICE",
    "Hydraulics": "HYDRAULIC",
    "Passenger Signs": "SIGNS",
    "Flight Deck Lights": "LIGHTS",
    "Exterior Lights": "LIGHTS",
    "Engine, APU and Cargo Fire": "FIRE_PROTECTION",
    "Engine": "ENGINES",
    "Fuel": "FUEL",
    "Anti-Ice": "ANTI_ICE",
    "Air Conditioning": "AIR_CONDITIONING",
    "Bleed Air": "BLEED_AIR",
    "Pressurization": "AIR_CONDITIONING",
    # Forward Panel subsections
    "Center": "FORWARD_PANEL",
    "Standby - ISFD": "FORWARD_PANEL",
    "Left & Right Sidewalls": "LIGHTS",
    "Left": "FORWARD_PANEL",
    "Right": "FORWARD_PANEL",
    "Chronometers": "FORWARD_PANEL",
    # Glareshield subsections
    "EFIS switches": "EFIS",
    "EFIS MOMENTARY action": "EFIS",
    "MCP - Variables": "MCP",
    "MCP - Switches": "MCP",
    "MCP - MOMENTARY action switches": "MCP",
    "MCP - Annunciator lights": "MCP",
    "Display Select Panel": "DISPLAY_SELECT",
    "Master Warning/Caution": "WARNING",
    # Forward Aisle Stand subsections
    "CDU": "CDU",
    # Aft Aisle Stand subsections
    "Audio Control Panels": "COMMUNICATION",
    "Radio Control Panels": "COMMUNICATION",
    "TCAS Panel": "TRANSPONDER",
    "Engine Fire": "FIRE_PROTECTION",
    "Aileron & Rudder Trim": "FLIGHT_CONTROLS",
    "Evacuation Panel": "SAFETY",
    "Aisle Stand PNL/FLOOD & Floor lights": "LIGHTS",
    # Other
    "Door state": "DOORS",
    "Additional variables": "FMC",
    # Major panel header overrides
    "Overhead Maintenance Panel": "OVERHEAD_MAINTENANCE",
    "Overhead Panel": "ELECTRICAL",  # default for OH, subsections override
    "Forward panel": "FORWARD_PANEL",
    "Glareshield": "EFIS",
    "Forward Aisle Stand Panel": "CDU",
    "Control Stand": "CONTROL_STAND",
    "Aft Aisle Stand Panel": "COMMUNICATION",
    "Flight Controls": "FLIGHT_CONTROLS",
    # --- PMDG NG3 (737) subsections ---
    "Aft overhead": "ELECTRICAL",          # default until subsection overrides
    "Forward overhead": "ELECTRICAL",
    "Center overhead": "ELECTRICAL",
    "Bottom overhead": "LIGHTS",
    "AFS": "AUTOPILOT",
    "PSEU": "WARNING",
    "Lights": "LIGHTS",
    "Oxygen": "OXYGEN",
    "Gear": "FORWARD_PANEL",
    "Gear panel": "FORWARD_PANEL",
    "Flight recorder": "MISCELLANEOUS",
    "Navigation/Displays": "AVIONICS",
    "APU": "ENGINES",
    "Wipers": "MISCELLANEOUS",
    "Air systems": "AIR_CONDITIONING",
    "Anti-ice": "ANTI_ICE",  # NG3 lowercase variant
    "Doors": "DOORS",
    "Warnings": "WARNING",
    "EFIS control panels": "EFIS",
    "Mode control panel": "MCP",
    "Lower forward panel": "FORWARD_PANEL",
    "FMS": "FMC",
    "General and misc": "MISCELLANEOUS",
}

# Event section to category mapping
# Matching is sorted longest-first; NG3 short aliases are added at the bottom.
_EVT_SECTION_TO_CATEGORY = {
    # --- PMDG 777 event sections ---
    "Overhead - Hydraulic": "HYDRAULIC",
    "Overhead - Electric": "ELECTRICAL",
    "Overhead - FUEL Panel": "FUEL",
    "Overhead Air": "AIR_CONDITIONING",
    "Overhead - Cabin Press": "AIR_CONDITIONING",
    "Overhead - ANTI-ICE": "ANTI_ICE",
    "Overhead Lights Panel": "LIGHTS",
    "Overhead - APU & Cargo Fire Panel": "FIRE_PROTECTION",
    "Overhead - Engine control": "ENGINES",
    "Overhead - Miscellaneous": "MISCELLANEOUS",
    "Forward Panel - Instrument Source Select": "FORWARD_PANEL",
    "Forward Panel - Display Selectors": "FORWARD_PANEL",
    "Forward Panel - Heading Reference": "EFIS",
    "Forward Panel - Gear": "FORWARD_PANEL",
    "Forward Panel - GPWS": "FORWARD_PANEL",
    "Forward Panel - Autobrakes": "FORWARD_PANEL",
    "Forward Panel - ISFD": "FORWARD_PANEL",
    "Forward Panel - non-ISFD standby instruments": "FORWARD_PANEL",
    "Forward Panel - Chronometers": "FORWARD_PANEL",
    "Forward Panel - Left Sidewall": "LIGHTS",
    "Forward Panel - Right Sidewall": "LIGHTS",
    "Glareshield - EFIS Captain control panel": "EFIS",
    "Glareshield - EFIS F/O control panels": "EFIS",
    "Glareshield - MCP": "MCP",
    "Glareshield - Display Select Panel": "DISPLAY_SELECT",
    "Glareshield - Master Warning/caution": "WARNING",
    "Glareshield - Data Link Switches": "COMMUNICATION",
    "Glareshield - Map/Chart/Worktable Lights, MIC & Clock Switches": "LIGHTS",
    "Pedestal - Fwd Aisle Stand - CDUs": "CDU",
    "Pedestal - Fwd Aisle Stand": "FORWARD_PANEL",
    "Pedestal - Control Stand - Fire protection panel": "FIRE_PROTECTION",
    "Pedestal - Control Stand - Flaps": "CONTROL_STAND",
    "Pedestal - Control Stand - Fuel Control": "CONTROL_STAND",
    "Pedestal - Aft Aisle Stand - COMM Panels": "COMMUNICATION",
    "Pedestal - Aft Aisle Stand - ACP Captain": "COMMUNICATION",
    "Pedestal - Aft Aisle Stand - ACP F/O": "COMMUNICATION",
    "Pedestal - Aft Aisle Stand - ACP Observer": "COMMUNICATION",
    "Pedestal - Aft Aisle Stand - WX RADAR panel": "WEATHER_RADAR",
    "Pedestal - Aft Aisle Stand - TCAS panel": "TRANSPONDER",
    "Pedestal - Aft Aisle Stand - CALL Panel": "COMMUNICATION",
    "Pedestal - Aft Aisle Stand - Various": "MISCELLANEOUS",
    "Pedestal - Control Stand": "CONTROL_STAND",
    "Oxygen Panels": "OXYGEN",
    "miscellaneous": "MISCELLANEOUS",
    "CA and FO Armrests": "MISCELLANEOUS",
    "Custom shortcut special events": "MISCELLANEOUS",
    "Window handle, crank and clipboard events": "DOORS",
    "MCP Direct Control": "MCP",
    "Panel system events": "MISCELLANEOUS",
    # --- PMDG NG3 (737) event sections ---
    "Overhead - LIGHTS Panel": "LIGHTS",
    "Overhead - Center Part": "ELECTRICAL",
    "Overhead - NAVDSP": "AVIONICS",
    "Overhead - FLTCTRL": "FLIGHT_CONTROLS",
    "Overhead - CVR": "MISCELLANEOUS",
    "Overhead - HYD": "HYDRAULIC",
    "Overhead - ICE": "ANTI_ICE",
    "Overhead - PNEU": "BLEED_AIR",
    "Overhead - Cabin Alt": "AIR_CONDITIONING",
    "Overhead - test gauge": "MISCELLANEOUS",
    "Aft Overhead - LE Devices": "FLIGHT_CONTROLS",
    "Aft Overhead - Service Interphone Switch": "COMMUNICATION",
    "Aft Overhead - Dome Switch": "LIGHTS",
    "Aft Overhead - ISDU panel": "AVIONICS",
    "Aft Overhead - Engine control": "ENGINES",
    "Aft Overhead - Oxygen": "OXYGEN",
    "Aft Overhead - Flt Rec & Warning": "WARNING",
    "Integrated Standby Flight Display - ISFD": "FORWARD_PANEL",
    "Analog standby instruments": "FORWARD_PANEL",
    "MCP": "MCP",
    "EFIS Captain control panel": "EFIS",
    "EFIS F/O control panels": "EFIS",
    "Display select panels": "DISPLAY_SELECT",
    "Main panel misc": "FORWARD_PANEL",
    "Aux Fuel Cockpit Display": "FUEL",
    "Nose Wheel Steering": "FORWARD_PANEL",
    "Warning/caution": "WARNING",
    "Lower Main": "LIGHTS",
    "GPWS": "FORWARD_PANEL",
    "Chronometers": "FORWARD_PANEL",
    "Control Stand": "CONTROL_STAND",
    "FLT  DK DOOR Panel": "DOORS",
    "FLT DK DOOR Panel": "DOORS",
    "VHF Panels": "COMMUNICATION",
    "MMR Panels": "COMMUNICATION",
    "ADF Panel": "COMMUNICATION",
    "SELCAL Panel": "COMMUNICATION",
    "COMM Panels": "COMMUNICATION",
    "Audio Control Panels": "COMMUNICATION",
    "WX RADAR panel": "WEATHER_RADAR",
    "TCAS": "TRANSPONDER",
    "HUD control panel": "FORWARD_PANEL",
    "HUD Annunciator Panel": "FORWARD_PANEL",
    "CDU": "CDU",
    "Fire protection panel": "FIRE_PROTECTION",
    "Cargo Fire": "FIRE_PROTECTION",
    "Lav/Supernumerary Smoke": "FIRE_PROTECTION",
    "Flight controls - pedestal": "FLIGHT_CONTROLS",
    "Pedestal Lights Controls": "LIGHTS",
    "EFB": "MISCELLANEOUS",
    "Various": "MISCELLANEOUS",
    "Grimes light": "LIGHTS",
    "Yoke Animations": "MISCELLANEOUS",
    "Pressurization Direct Control": "AIR_CONDITIONING",
}


def _parse_values_from_comment(comment: str) -> dict[str, str] | None:
    """Extract value enum from SDK comment like '0: OFF  1: AUTO  2: ON'."""
    if not comment:
        return None
    matches = _VALUES_RE.findall(comment)
    if len(matches) >= 2:
        return {k: v.strip() for k, v in matches}
    return None


def _parse_index_labels(comment: str, size: int) -> list[str]:
    """Extract array index labels from comment like 'left/right'."""
    if not comment:
        return [str(i + 1) for i in range(size)]

    # Try splitting by /
    parts = [p.strip() for p in comment.split("/")]
    if len(parts) == size:
        return parts

    # Try common patterns
    labels = _INDEX_LABELS_RE.findall(comment)
    if len(labels) == size:
        return labels

    # Fallback to numbers
    return [str(i + 1) for i in range(size)]


def _make_display_name(field_name: str) -> str:
    """Convert SDK field name to human-readable display name."""
    # Remove common prefixes for display
    name = field_name

    # Split on underscores and capitalize
    parts = name.split("_")
    display_parts = []
    for part in parts:
        if part.isupper() and len(part) <= 4:
            display_parts.append(part)  # Keep short acronyms
        elif part.isupper():
            display_parts.append(part.capitalize())
        else:
            display_parts.append(part)

    return " ".join(display_parts)


def _get_prefix(field_name: str) -> str:
    """Extract system prefix from field name."""
    parts = field_name.split("_")
    if parts:
        return parts[0]
    return ""


def _is_writable(field_name: str, field_type: str) -> bool:
    """Determine if a field is writable based on naming conventions."""
    name_lower = field_name.lower()
    # Annunciators and indicators are read-only
    if "annun" in name_lower:
        return False
    # Needles and display values are read-only
    if "needle" in name_lower or "illuminated" in name_lower:
        return False
    # State indicators
    if "isunlocked" in name_lower or "isopen" in name_lower:
        return False
    # Running/aligned/available status
    if name_lower.endswith("running") or name_lower.endswith("aligned"):
        return False
    if "available" in name_lower or "complete" in name_lower:
        return False
    # Display format readouts
    if field_name.startswith("EFIS_Display"):
        return False
    if field_name.startswith("FMC_") and field_type in ("unsigned char", "unsigned short", "short", "float", "char", "bool"):
        # FMC data is generally read-only
        if any(x in field_name for x in ("TakeoffFlaps", "V1", "VR", "V2", "ThrustRedAlt",
                                          "AccelerationAlt", "EOAccelerationAlt", "LandingFlaps",
                                          "LandingVREF", "CruiseAlt", "LandingAltitude",
                                          "TransitionAlt", "TransitionLevel", "PerfInputComplete",
                                          "DistanceToTOD", "DistanceToDest", "flightNumber",
                                          "ThrustLimitMode")):
            return False
    # Model info is read-only
    if field_name in ("AircraftModel", "WeightInKg", "GPWS_V1CallEnabled",
                       "GroundConnAvailable", "WheelChocksSet", "APURunning"):
        return False
    # ECL is read-only
    if field_name.startswith("ECL_"):
        return False
    # Door state is read-only (controlled via events)
    if field_name.startswith("DOOR_state"):
        return False
    # Fuel qty, duct press, start valve are read-only sensors
    if field_name.startswith("FUEL_Qty") or field_name.startswith("AIR_DuctPress"):
        return False
    if field_name == "ENG_StartValve":
        return False
    if field_name.startswith("IRS_"):
        return False
    if field_name.startswith("EFIS_Baro") and "Set" in field_name:
        return False
    if field_name.startswith("EFIS_Radio") and "Set" in field_name:
        return False
    # reserved
    if field_name == "reserved":
        return False

    # Switches, selectors, knobs, levers are generally writable
    return True


# Detects:  struct PMDG_777X_Data { ... },  struct PMDG_NG3_Data { ... }, etc.
_STRUCT_DECL_RE = re.compile(r"^\s*struct\s+(?P<name>PMDG_[A-Za-z0-9_]+_Data)\b")


def detect_struct_name(lines: list[str]) -> str | None:
    """Find the data struct name (e.g. PMDG_777X_Data, PMDG_NG3_Data)."""
    for line in lines:
        m = _STRUCT_DECL_RE.match(line)
        if m:
            return m.group("name")
    return None


def parse_data_struct(lines: list[str], struct_name: str) -> list[dict]:
    """Parse a PMDG_<X>_Data struct's fields."""
    in_struct = False
    current_section = "MISCELLANEOUS"
    current_subsection = ""
    fields = []
    pending_comment_lines: list[str] = []
    struct_decl = f"struct {struct_name}"

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Detect struct start
        if struct_decl in line:
            in_struct = True
            continue
        if not in_struct:
            continue

        # Detect struct end
        if stripped.startswith("};") and in_struct:
            break

        # Track section headers
        if stripped.startswith("//") and not stripped.startswith("///"):
            comment_text = stripped.lstrip("/").strip()

            # Skip divider lines and generic headers
            if comment_text.startswith("---") or comment_text.startswith("==="):
                continue
            if comment_text in ("Controls and indicators",):
                continue

            # Check for known panel headers (try longest match first)
            matched = False
            for section_key, category in sorted(
                _SECTION_TO_CATEGORY.items(), key=lambda x: -len(x[0])
            ):
                if comment_text == section_key or comment_text.startswith(section_key):
                    current_section = category
                    current_subsection = comment_text
                    matched = True
                    break

            pending_comment_lines.append(comment_text)
            continue

        # Parse struct field
        m = _FIELD_RE.match(line)
        if m:
            field_type = m.group("type")
            field_name = m.group("name")
            array_size = int(m.group("array")) if m.group("array") else None
            comment = m.group("comment") or ""

            # Skip reserved fields
            if field_name == "reserved":
                pending_comment_lines = []
                continue

            # Also check preceding comment lines for context
            full_comment = comment
            if pending_comment_lines:
                # Check if pending comments have array docs or value enums
                for pc in pending_comment_lines:
                    if _VALUES_RE.search(pc) or "array" in pc.lower() or ":" in pc:
                        full_comment = pc + "  " + full_comment

            values = _parse_values_from_comment(full_comment)
            prefix = _get_prefix(field_name)
            writable = _is_writable(field_name, field_type)
            type_info = _TYPE_MAP.get(field_type, (field_type, None))

            if array_size and array_size > 1 and field_type != "char":
                # Expand array into individual entries
                labels = _parse_index_labels(comment, array_size)
                for idx in range(array_size):
                    label = labels[idx] if idx < len(labels) else str(idx + 1)
                    label_clean = label.strip().replace(" ", "_").replace("/", "_")
                    entry_name = f"{field_name}_{label_clean}"
                    display = _make_display_name(field_name) + f" ({label.strip()})"
                    entry = {
                        "name": entry_name,
                        "display_name": display,
                        "category": current_section,
                        "prefix": prefix,
                        "writable": writable,
                        "sdk_field": field_name,
                        "sdk_type": type_info[0],
                        "sdk_index": idx,
                        "sdk_array_size": array_size,
                    }
                    if values:
                        entry["values"] = values
                    fields.append(entry)
            else:
                display = _make_display_name(field_name)
                entry = {
                    "name": field_name,
                    "display_name": display,
                    "category": current_section,
                    "prefix": prefix,
                    "writable": writable,
                    "sdk_field": field_name,
                    "sdk_type": type_info[0],
                }
                if values:
                    entry["values"] = values
                if field_type == "char" and array_size:
                    entry["sdk_type"] = f"char[{array_size}]"
                    entry["writable"] = False
                fields.append(entry)

            pending_comment_lines = []
        elif stripped and not stripped.startswith("//"):
            pending_comment_lines = []

    return fields


def parse_events(lines: list[str]) -> list[dict]:
    """Parse all EVT_* event defines."""
    events = []
    current_section = "MISCELLANEOUS"
    # First pass: collect direct defines to resolve offset references
    direct_events: dict[str, int] = {}  # name -> event_id
    offset_constants: dict[str, int] = {}  # CDU_EVT_OFFSET_R etc

    for line in lines:
        m = _EVENT_RE.match(line)
        if m:
            name = m.group("name")
            offset = int(m.group("offset"))
            event_id = 0x00011000 + offset
            direct_events[name] = event_id

    # Compute offset constants from defines like:
    # #define CDU_EVT_OFFSET_R  (EVT_CDU_R_L1-EVT_CDU_L_L1)
    _OFFSET_DEF_RE = re.compile(
        r"#define\s+(\w+)\s+\((\w+?)\s*-\s*(\w+)\)"
    )
    for line in lines:
        m = _OFFSET_DEF_RE.search(line)
        if m:
            const_name = m.group(1)
            a = m.group(2)
            b = m.group(3)
            if a in direct_events and b in direct_events:
                offset_constants[const_name] = direct_events[a] - direct_events[b]

    # Second pass: parse all events with resolved IDs
    for line in lines:
        stripped = line.strip()

        # Track section comments
        if stripped.startswith("//"):
            comment_text = stripped.lstrip("/").strip()
            for section_key, category in sorted(
                _EVT_SECTION_TO_CATEGORY.items(), key=lambda x: -len(x[0])
            ):
                if comment_text.startswith(section_key) or comment_text == section_key:
                    current_section = category
                    break
            continue

        # Parse direct event define
        m = _EVENT_RE.match(line)
        if m:
            name = m.group("name")
            offset = int(m.group("offset"))
            comment = (m.group("comment") or "").strip()
            event_id = 0x00011000 + offset

            events.append({
                "name": name,
                "offset": offset,
                "event_id": event_id,
                "category": current_section,
                "comment": comment,
            })
            continue

        # Parse offset-based defines (CDU R/C copies) and resolve IDs
        m = _EVENT_OFFSET_RE.match(line)
        if m:
            name = m.group("name")
            base_name = m.group("base")
            ref_name = m.group("ref")

            # Try to resolve the event ID
            event_id = None
            base_offset = offset_constants.get(base_name)
            ref_id = direct_events.get(ref_name)
            if base_offset is not None and ref_id is not None:
                event_id = base_offset + ref_id

            events.append({
                "name": name,
                "offset": None,
                "event_id": event_id,
                "category": current_section,
                "comment": "",
                "derived_from": ref_name,
            })

    return events


# ---------------------------------------------------------------------------
# Catalog Building
# ---------------------------------------------------------------------------

def _build_event_lookup(events: list[dict]) -> dict[int, dict]:
    """Build offset -> event lookup."""
    return {e["offset"]: e for e in events if e.get("offset") is not None}


def _match_events_to_fields(
    fields: list[dict], events: list[dict]
) -> dict[str, list[dict]]:
    """Try to match events to data fields by naming conventions."""
    field_events: dict[str, list[dict]] = {}

    for evt in events:
        evt_name = evt["name"]
        # Skip derived/alias events
        if evt.get("derived_from"):
            continue

        # Try to find matching field by partial name matching
        # e.g., EVT_OH_ELEC_BATTERY_SWITCH -> ELEC_Battery_Sw_ON
        evt_words = evt_name.replace("EVT_", "").lower().split("_")

        best_match = None
        best_score = 0
        for field in fields:
            field_words = field["name"].lower().split("_")
            # Count matching words
            score = sum(1 for w in evt_words if w in field_words)
            if score > best_score and score >= 2:
                best_score = score
                best_match = field["name"]

        if best_match:
            field_events.setdefault(best_match, []).append(evt)

    return field_events


def build_catalog(
    fields: list[dict],
    events: list[dict],
    aircraft_name: str = "PMDG 777",
    title_pattern: str = "PMDG 777",
    data_area: str = "PMDG_777X_Data",
    control_area: str = "PMDG_777X_Control",
    cdu_areas: list[str] | None = None,
    event_base: int = 0x00011000,
) -> dict:
    """Build the final catalog dict."""
    if cdu_areas is None:
        cdu_areas = [
            "PMDG_777X_CDU_0",
            "PMDG_777X_CDU_1",
            "PMDG_777X_CDU_2",
        ]
    # Build event index
    event_lookup = _build_event_lookup(events)
    field_event_map = _match_events_to_fields(fields, events)

    # Add event info to fields
    for field in fields:
        matched_events = field_event_map.get(field["name"], [])
        if matched_events:
            field["events"] = [
                {"name": e["name"], "id": e["event_id"]}
                for e in matched_events
                if e.get("event_id")
            ]

    # Build panels
    panels: dict[str, list[str]] = {}
    for field in fields:
        cat = field["category"]
        panels.setdefault(cat, []).append(field["name"])

    # Build event-only entries (events without matching data fields)
    event_only_entries = []
    matched_event_names = set()
    for evts in field_event_map.values():
        for e in evts:
            matched_event_names.add(e["name"])

    for evt in events:
        if evt["name"] not in matched_event_names and evt.get("event_id"):
            # Create event-only catalog entry
            entry = {
                "name": evt["name"],
                "display_name": _make_display_name(
                    evt["name"].replace("EVT_", "")
                ),
                "category": evt["category"],
                "prefix": "EVT",
                "writable": True,
                "sdk_type": "event",
                "events": [{"name": evt["name"], "id": evt["event_id"]}],
            }
            if evt.get("comment"):
                entry["description"] = evt["comment"]
            event_only_entries.append(entry)
            panels.setdefault(evt["category"], []).append(evt["name"])

    all_variables = fields + event_only_entries

    catalog = {
        "aircraft": aircraft_name,
        "title_pattern": title_pattern,
        "sdk_info": {
            "data_area": data_area,
            "control_area": control_area,
            "cdu_areas": cdu_areas,
            "event_base": event_base,
        },
        "variables": all_variables,
        "panels": panels,
    }

    return catalog


def clean_catalog_for_output(catalog: dict) -> dict:
    """Remove internal-only fields and clean up for JSON output."""
    clean_vars = []
    for var in catalog["variables"]:
        clean = {
            "name": var["name"],
            "display_name": var["display_name"],
            "category": var["category"],
            "prefix": var["prefix"],
            "writable": var.get("writable", False),
        }
        if "values" in var:
            clean["values"] = var["values"]
        if "events" in var:
            clean["events"] = var["events"]
        if "sdk_type" in var:
            clean["sdk_type"] = var["sdk_type"]
        if "sdk_field" in var:
            clean["sdk_field"] = var["sdk_field"]
        if "sdk_index" in var:
            clean["sdk_index"] = var["sdk_index"]
        if "description" in var:
            clean["description"] = var["description"]
        clean_vars.append(clean)

    return {
        "aircraft": catalog["aircraft"],
        "title_pattern": catalog["title_pattern"],
        "sdk_info": catalog["sdk_info"],
        "variables": clean_vars,
        "panels": catalog["panels"],
    }


# ---------------------------------------------------------------------------
# HubHop Merge
# ---------------------------------------------------------------------------

def merge_hubhop(catalog: dict, hubhop_vars: dict[str, dict]) -> dict:
    """Merge HubHop L-var data into the catalog.

    hubhop_vars: dict of {lvar_name: {systems, labels, writable}} from
    HubHopClient.extract_lvars()
    """
    existing_names = {v["name"] for v in catalog["variables"]}

    added = 0
    for lvar_name, info in hubhop_vars.items():
        if lvar_name in existing_names:
            continue

        # Determine category from HubHop system
        systems = info.get("systems", set())
        category = "MISCELLANEOUS"
        for sys_name in systems:
            sys_upper = sys_name.upper()
            if "ELEC" in sys_upper:
                category = "ELECTRICAL"
            elif "FUEL" in sys_upper:
                category = "FUEL"
            elif "HYD" in sys_upper:
                category = "HYDRAULIC"
            elif "ENGINE" in sys_upper or "ENG" in sys_upper:
                category = "ENGINES"
            elif "LIGHT" in sys_upper:
                category = "LIGHTS"
            elif "AUTOPILOT" in sys_upper:
                category = "MCP"
            elif "EFIS" in sys_upper:
                category = "EFIS"
            elif "RADIO" in sys_upper or "COMM" in sys_upper:
                category = "COMMUNICATION"
            elif "ANTI" in sys_upper or "ICE" in sys_upper:
                category = "ANTI_ICE"
            elif "AIR" in sys_upper or "PRESS" in sys_upper:
                category = "AIR_CONDITIONING"
            elif "FIRE" in sys_upper:
                category = "FIRE_PROTECTION"
            elif "GEAR" in sys_upper:
                category = "FORWARD_PANEL"
            elif "SAFETY" in sys_upper:
                category = "SAFETY"
            elif "NAV" in sys_upper:
                category = "AVIONICS"
            elif "WARN" in sys_upper:
                category = "WARNING"
            elif "CONTROL" in sys_upper:
                category = "CONTROL_STAND"
            elif "MCDU" in sys_upper or "FMS" in sys_upper:
                category = "CDU"
            elif "AVIONICS" in sys_upper:
                category = "AVIONICS"
            elif "TCAS" in sys_upper or "TRANSPONDER" in sys_upper:
                category = "TRANSPONDER"
            break

        # Build display name
        display = lvar_name
        if display.startswith("switch_"):
            labels = info.get("labels", set())
            if labels:
                display = next(iter(labels))
            else:
                display = display.replace("switch_", "Switch ").replace("_73x", "")
        else:
            parts = display.split("_")
            display = " ".join(p.capitalize() for p in parts)

        prefix = lvar_name.split("_")[0] if "_" in lvar_name else ""

        entry = {
            "name": lvar_name,
            "display_name": display,
            "category": category,
            "prefix": prefix,
            "writable": info.get("writable", False),
            "sdk_type": "lvar",
            "source": "hubhop",
        }
        catalog["variables"].append(entry)
        catalog["panels"].setdefault(category, []).append(lvar_name)
        added += 1

    print(f"Merged {added} HubHop L-vars into catalog")
    return catalog


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _derive_areas(struct_name: str) -> tuple[str, str, str]:
    """Derive area-name prefix from struct name.

    Example: ``PMDG_777X_Data`` → ``PMDG_777X``. The prefix is used to
    construct the data/control/CDU area names when not overridden.
    """
    suffix = "_Data"
    if struct_name.endswith(suffix):
        prefix = struct_name[: -len(suffix)]
    else:
        prefix = struct_name
    return (
        f"{prefix}_Data",
        f"{prefix}_Control",
        prefix,
    )


def main():
    parser = argparse.ArgumentParser(description="Parse a PMDG SDK header")
    parser.add_argument("header", help="Path to PMDG_*_SDK.h header")
    parser.add_argument("-o", "--output", help="Output JSON file path")
    parser.add_argument("--struct-name",
                        help="Data struct name (default: auto-detect)")
    parser.add_argument("--data-area",
                        help="Client data area name (default: derived from struct)")
    parser.add_argument("--control-area",
                        help="Control data area name (default: derived from struct)")
    parser.add_argument("--cdu-count", type=int, default=None,
                        help="Number of CDUs (default: 3 for 777X, 2 for NG3)")
    parser.add_argument("--cdu-areas", nargs="*",
                        help="CDU data area names (default: derived)")
    parser.add_argument("--hubhop", action="store_true",
                        help="Fetch and merge HubHop presets")
    parser.add_argument("--hubhop-vendor", default="PMDG",
                        help="HubHop vendor name (default: PMDG)")
    parser.add_argument("--hubhop-aircraft",
                        help="HubHop aircraft model (e.g. 'B777 300ER', 'B737-800')")
    parser.add_argument("--hubhop-match",
                        help="Substring match against HubHop aircraft field "
                             "if exact aircraft lookup returns nothing")
    parser.add_argument("--aircraft-name", default="PMDG 777",
                        help="Aircraft name for catalog")
    parser.add_argument("--title-pattern", default="PMDG 777",
                        help="Title pattern for auto-detection")
    parser.add_argument("--stats", action="store_true",
                        help="Print statistics only")
    args = parser.parse_args()

    header_path = Path(args.header)
    if not header_path.exists():
        print(f"Error: {header_path} not found", file=sys.stderr)
        sys.exit(1)

    lines = header_path.read_text(encoding="utf-8").splitlines()

    # Resolve struct name
    struct_name = args.struct_name or detect_struct_name(lines)
    if struct_name is None:
        print("Error: could not auto-detect PMDG data struct. "
              "Pass --struct-name explicitly.", file=sys.stderr)
        sys.exit(1)

    # Resolve area names
    default_data, default_control, prefix = _derive_areas(struct_name)
    data_area = args.data_area or default_data
    control_area = args.control_area or default_control

    if args.cdu_areas:
        cdu_areas = list(args.cdu_areas)
    else:
        cdu_count = args.cdu_count
        if cdu_count is None:
            # Heuristic: NG3 has 2 CDUs, everything else has 3
            cdu_count = 2 if "NG3" in struct_name else 3
        cdu_areas = [f"{prefix}_CDU_{i}" for i in range(cdu_count)]

    print(f"Parsing {header_path.name}...")
    print(f"  Struct:        {struct_name}")
    print(f"  Data area:     {data_area}")
    print(f"  Control area:  {control_area}")
    print(f"  CDU areas:     {cdu_areas}")

    fields = parse_data_struct(lines, struct_name)
    events = parse_events(lines)

    print(f"  Data fields: {len(fields)}")
    print(f"  Events: {len(events)}")

    # Build catalog
    catalog = build_catalog(
        fields, events,
        aircraft_name=args.aircraft_name,
        title_pattern=args.title_pattern,
        data_area=data_area,
        control_area=control_area,
        cdu_areas=cdu_areas,
    )

    # HubHop merge
    if args.hubhop:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
            from simconnect_mcp.data.hubhop import HubHopClient

            client = HubHopClient()
            presets: list[dict] = []
            if args.hubhop_aircraft:
                presets = client.fetch_presets(
                    vendor=args.hubhop_vendor, aircraft=args.hubhop_aircraft,
                )
            if not presets:
                all_for_vendor = client.fetch_presets(vendor=args.hubhop_vendor)
                if args.hubhop_match:
                    needle = args.hubhop_match.lower()
                    presets = [
                        p for p in all_for_vendor
                        if needle in p.get("aircraft", "").lower()
                    ]
                else:
                    presets = all_for_vendor
            print(f"  HubHop presets: {len(presets)}")
            hubhop_vars = client.extract_lvars(presets)
            print(f"  HubHop L-vars: {len(hubhop_vars)}")
            catalog = merge_hubhop(catalog, hubhop_vars)
        except Exception as e:
            print(f"  Warning: HubHop merge failed: {e}", file=sys.stderr)

    # Statistics
    categories = {}
    for var in catalog["variables"]:
        cat = var["category"]
        categories[cat] = categories.get(cat, 0) + 1

    print(f"\nCatalog summary:")
    print(f"  Total variables: {len(catalog['variables'])}")
    print(f"  Panels: {len(catalog['panels'])}")
    print(f"\n  By category:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"    {cat}: {count}")

    if args.stats:
        return

    # Clean and output
    clean = clean_catalog_for_output(catalog)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(
            json.dumps(clean, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"\nWritten to {output_path}")
    else:
        print(json.dumps(clean, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
