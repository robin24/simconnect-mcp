# SimConnect MCP Modernization — Phase 2: Capability

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the two `NOT_IMPLEMENTED` tool groups real, reach the HubHop client that already exists but is unreachable, and add flight/scenario tools for scripted test setup.

**Architecture:** Facilities are parsed by handlers installed on the Phase 0 dispatcher, so no data ever reaches the library's printing `dump()` path. L-var enumeration exploits a discovery made during the audit: the MobiFlight WASM module's response strings **already arrive** at `client_data_callback_handler`, but definition ID 0 is never in `sim_vars`, so every one is logged as a warning and dropped. Routing definition 0 to a string handler makes `MF.LVars.List` work. HubHop and flight tools are thin wrappers over existing, working code.

**Tech Stack:** Python 3.10+, `mcp[cli]>=1.26` (FastMCP), Pydantic v2, ctypes, pytest, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-08-29-mcp-modernization-design.md`
**Depends on:** Phase 0 and Phase 1 complete.

## Global Constraints

- Flat `Annotated[T, Field(...)]` parameters; return `SomeModel | ToolError`. Never `params: SomeModel`.
- Every new tool is registered explicitly in `server.py` with `ToolAnnotations`, `msfs_`-prefixed.
- Every search/list tool takes `limit`/`offset` and returns a `Page`.
- HubHop is HTTP, not SimConnect: its calls go through `run_in_executor` directly, **not** through `run_sync` — holding the sim lock for a network round trip would stall every other tool.
- Nothing may be written to stdout.
- The final tool count is 32: the 26 from Phase 1 plus the six registered in Task 7.
- Run tests with `uv run pytest`.

---

### Task 1: Parse facility lists on the dispatcher

Phase 0 made the facilities dispatch branch a no-op loop over `facility_handlers` so the library's printing `dump()` could not run. This task fills it in. Each `*_LIST` message carries a `SIMCONNECT_RECV_FACILITIES_LIST` header immediately followed by `dwArraySize` facility structs, and arrives in `dwOutOf` chunks that must be accumulated.

**Files:**
- Create: `src/simconnect_mcp/facilities.py`
- Modify: `src/simconnect_mcp/dispatch.py` (route `*_LIST` to the collector)
- Create: `tests/test_facilities_parsing.py`

**Interfaces:**
- Consumes: `dispatch.SimConnectDispatcher.facility_handlers`
- Produces:
  - `FacilityKind` enum: `AIRPORT`, `WAYPOINT`, `NDB`, `VOR`
  - `FacilityCollector` with `handle(kind, header, entries)`, `results(kind) -> list[dict]`, `is_complete(kind) -> bool`, `reset(kind)`
  - `parse_facility_message(pData) -> tuple[FacilityKind, header, list[dict]]`
  - `great_circle_nm(lat1, lon1, lat2, lon2) -> float`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_facilities_parsing.py`:

```python
import pytest

from simconnect_mcp.facilities import (
    FacilityCollector,
    FacilityKind,
    great_circle_nm,
)


class FakeHeader:
    def __init__(self, entry_number, out_of, array_size):
        self.dwEntryNumber = entry_number
        self.dwOutOf = out_of
        self.dwArraySize = array_size


def test_collector_accumulates_a_single_chunk():
    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 1, 2),
                     [{"icao": "KSEA"}, {"icao": "KBFI"}])

    assert collector.is_complete(FacilityKind.AIRPORT) is True
    assert [a["icao"] for a in collector.results(FacilityKind.AIRPORT)] == ["KSEA", "KBFI"]


def test_collector_accumulates_multiple_chunks():
    """SimConnect chops long lists into dwOutOf transmissions."""
    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 3, 1), [{"icao": "A"}])
    assert collector.is_complete(FacilityKind.AIRPORT) is False

    collector.handle(FacilityKind.AIRPORT, FakeHeader(1, 3, 1), [{"icao": "B"}])
    collector.handle(FacilityKind.AIRPORT, FakeHeader(2, 3, 1), [{"icao": "C"}])

    assert collector.is_complete(FacilityKind.AIRPORT) is True
    assert len(collector.results(FacilityKind.AIRPORT)) == 3


def test_a_new_first_chunk_starts_a_fresh_list():
    """A re-request must not append to the previous result."""
    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 1, 1), [{"icao": "OLD"}])
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 1, 1), [{"icao": "NEW"}])

    assert [a["icao"] for a in collector.results(FacilityKind.AIRPORT)] == ["NEW"]


def test_kinds_are_kept_separate():
    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 1, 1), [{"icao": "KSEA"}])
    collector.handle(FacilityKind.VOR, FakeHeader(0, 1, 1), [{"icao": "SEA"}])

    assert len(collector.results(FacilityKind.AIRPORT)) == 1
    assert len(collector.results(FacilityKind.VOR)) == 1


def test_great_circle_distance_seattle_to_portland():
    # KSEA to KPDX is roughly 129 nautical miles.
    nm = great_circle_nm(47.4502, -122.3088, 45.5898, -122.5951)
    assert nm == pytest.approx(129, abs=5)


def test_great_circle_zero_distance():
    assert great_circle_nm(47.0, -122.0, 47.0, -122.0) == pytest.approx(0.0, abs=0.01)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_facilities_parsing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simconnect_mcp.facilities'`

- [ ] **Step 3: Create the module**

Create `src/simconnect_mcp/facilities.py`:

```python
"""Facility list parsing and accumulation.

SimConnect answers RequestFacilitiesList with one or more *_LIST messages.
Each carries a SIMCONNECT_RECV_FACILITIES_LIST header immediately followed by
dwArraySize facility structs, and long lists are chopped into dwOutOf
transmissions that must be accumulated.

The SimConnect library's own handler for these messages calls dump(), which
print()s to stdout -- fatal on a stdio MCP server -- so this module replaces
it entirely.
"""
from __future__ import annotations

import ctypes
import enum
import logging
import math
import threading

from SimConnect.Enum import (
    SIMCONNECT_DATA_FACILITY_AIRPORT,
    SIMCONNECT_DATA_FACILITY_NDB,
    SIMCONNECT_DATA_FACILITY_VOR,
    SIMCONNECT_DATA_FACILITY_WAYPOINT,
    SIMCONNECT_RECV_FACILITIES_LIST,
    SIMCONNECT_RECV_ID,
)

log = logging.getLogger(__name__)

EARTH_RADIUS_NM = 3440.065


class FacilityKind(str, enum.Enum):
    AIRPORT = "airport"
    WAYPOINT = "waypoint"
    NDB = "ndb"
    VOR = "vor"


_RECV_ID_TO_KIND = {
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_AIRPORT_LIST: FacilityKind.AIRPORT,
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_WAYPOINT_LIST: FacilityKind.WAYPOINT,
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_NDB_LIST: FacilityKind.NDB,
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_VOR_LIST: FacilityKind.VOR,
}

_KIND_TO_STRUCT = {
    FacilityKind.AIRPORT: SIMCONNECT_DATA_FACILITY_AIRPORT,
    FacilityKind.WAYPOINT: SIMCONNECT_DATA_FACILITY_WAYPOINT,
    FacilityKind.NDB: SIMCONNECT_DATA_FACILITY_NDB,
    FacilityKind.VOR: SIMCONNECT_DATA_FACILITY_VOR,
}

# Metres to feet, for the Altitude field.
_M_TO_FT = 3.280839895


def great_circle_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))


def _entry_to_dict(kind: FacilityKind, entry) -> dict:
    """Convert one facility struct to a plain dict, decoding the ICAO."""
    icao = entry.Icao
    result = {
        "icao": icao.decode("ascii", errors="replace").strip() if isinstance(icao, bytes)
        else str(icao).strip(),
        "kind": kind.value,
        "latitude": entry.Latitude,
        "longitude": entry.Longitude,
        "altitude_ft": round(entry.Altitude * _M_TO_FT, 1),
    }
    if kind in (FacilityKind.WAYPOINT, FacilityKind.NDB, FacilityKind.VOR):
        result["magvar"] = round(entry.fMagVar, 2)
    if kind in (FacilityKind.NDB, FacilityKind.VOR):
        result["frequency_hz"] = int(entry.fFrequency)
    if kind is FacilityKind.VOR:
        result["localizer_deg"] = round(entry.fLocalizer, 2)
        result["glide_slope_deg"] = round(entry.fGlideSlopeAngle, 2)
    return result


def parse_facility_message(pData) -> tuple[FacilityKind, object, list[dict]]:
    """Parse a *_LIST dispatch message into (kind, header, entries)."""
    recv_id = pData.contents.dwID
    kind = _RECV_ID_TO_KIND[recv_id]
    header = ctypes.cast(
        pData, ctypes.POINTER(SIMCONNECT_RECV_FACILITIES_LIST)
    ).contents

    struct_type = _KIND_TO_STRUCT[kind]
    base = ctypes.addressof(header) + ctypes.sizeof(SIMCONNECT_RECV_FACILITIES_LIST)
    array = (struct_type * header.dwArraySize).from_address(base)
    return kind, header, [_entry_to_dict(kind, entry) for entry in array]


class FacilityCollector:
    """Accumulates chunked facility lists, one buffer per kind."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffers: dict[FacilityKind, list[dict]] = {}
        self._complete: dict[FacilityKind, bool] = {}

    def handle(self, kind: FacilityKind, header, entries: list[dict]) -> None:
        with self._lock:
            # dwEntryNumber 0 begins a new transmission; do not append to the
            # results of a previous request.
            if header.dwEntryNumber == 0:
                self._buffers[kind] = []
                self._complete[kind] = False
            self._buffers.setdefault(kind, []).extend(entries)
            self._complete[kind] = header.dwEntryNumber >= (header.dwOutOf - 1)

    def results(self, kind: FacilityKind) -> list[dict]:
        with self._lock:
            return list(self._buffers.get(kind, []))

    def is_complete(self, kind: FacilityKind) -> bool:
        with self._lock:
            return self._complete.get(kind, False)

    def reset(self, kind: FacilityKind) -> None:
        with self._lock:
            self._buffers[kind] = []
            self._complete[kind] = False
```

- [ ] **Step 4: Route facility messages on the dispatcher**

In `src/simconnect_mcp/dispatch.py`, add `self.facilities = FacilityCollector()` to `__init__` (before `super().__init__`), import `FacilityCollector` and `parse_facility_message`, and replace the facility branch body:

```python
        if dwID in FACILITY_RECV_IDS:
            try:
                kind, header, entries = parse_facility_message(pData)
                self.facilities.handle(kind, header, entries)
            except Exception:
                log.debug("Could not parse facility message", exc_info=True)
            for handler in self.facility_handlers:
                handler(pData)
            return
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_facilities_parsing.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q && uv run ruff check src/ tests/`
Expected: PASS, clean

- [ ] **Step 7: Commit**

```bash
git add src/simconnect_mcp/facilities.py src/simconnect_mcp/dispatch.py tests/test_facilities_parsing.py
git commit -m "feat: parse SimConnect facility lists on the dispatcher

Replaces the library's handler, which only print()s results to stdout.
Accumulates the chunked dwOutOf transmissions."
```

---

### Task 2: Implement the facilities tools

**Files:**
- Modify: `src/simconnect_mcp/tools/facilities.py` (full rewrite)
- Modify: `src/simconnect_mcp/tools/models.py` (add `FacilityList`, `FacilityInfo`)
- Create: `tests/test_facilities_tools.py`

**Interfaces:**
- Consumes: `manager.sm.facilities` (the collector), `manager.sm.dll.SubscribeToFacilities`, `facilities.great_circle_nm`
- Produces: `get_nearby_airports(latitude, longitude, radius_nm, limit, offset, response_format)`, `get_facility_info(icao, facility_type)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_facilities_tools.py`:

```python
import pytest

from simconnect_mcp.facilities import FacilityKind
from simconnect_mcp.tools.formatting import ResponseFormat


@pytest.fixture
def facility_sim(mock_simconnect):
    """Give the mock a populated facility collector."""
    from simconnect_mcp.facilities import FacilityCollector

    class Header:
        dwEntryNumber, dwOutOf, dwArraySize = 0, 1, 3

    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, Header(), [
        {"icao": "KSEA", "kind": "airport", "latitude": 47.4502,
         "longitude": -122.3088, "altitude_ft": 433.0},
        {"icao": "KBFI", "kind": "airport", "latitude": 47.5300,
         "longitude": -122.3020, "altitude_ft": 21.0},
        {"icao": "KPDX", "kind": "airport", "latitude": 45.5898,
         "longitude": -122.5951, "altitude_ft": 31.0},
    ])
    mock_simconnect["sm"].facilities = collector
    return mock_simconnect


async def test_nearby_airports_filters_by_radius(facility_sim):
    from simconnect_mcp.tools.facilities import get_nearby_airports

    result = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=25,
        response_format=ResponseFormat.JSON,
    )
    icaos = [a["icao"] for a in result.results]
    assert "KSEA" in icaos
    assert "KBFI" in icaos
    assert "KPDX" not in icaos, "KPDX is ~129 nm away"


async def test_nearby_airports_are_sorted_by_distance(facility_sim):
    from simconnect_mcp.tools.facilities import get_nearby_airports

    result = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    )
    distances = [a["distance_nm"] for a in result.results]
    assert distances == sorted(distances)


async def test_nearby_airports_paginate(facility_sim):
    from simconnect_mcp.tools.facilities import get_nearby_airports

    result = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200, limit=1,
        response_format=ResponseFormat.JSON,
    )
    assert result.page.count == 1
    assert result.page.has_more is True


async def test_facility_info_finds_an_airport_case_insensitively(facility_sim):
    from simconnect_mcp.tools.facilities import get_facility_info

    result = await get_facility_info("ksea")
    assert result.facility["icao"] == "KSEA"


async def test_facility_info_reports_a_miss(facility_sim):
    from simconnect_mcp.tools.facilities import get_facility_info

    result = await get_facility_info("ZZZZ")
    assert result.error == "FACILITY_NOT_FOUND"
    assert "radius" in result.suggestion.lower() or "loaded" in result.suggestion.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_facilities_tools.py -v`
Expected: FAIL — the tools still return `NOT_IMPLEMENTED`

- [ ] **Step 3: Add the models**

Append to `src/simconnect_mcp/tools/models.py`:

```python
class FacilityInfo(OkModel):
    facility: dict[str, Any]


class FacilityList(OkModel):
    page: Page
    center: dict[str, float]
    radius_nm: float
    results: list[dict[str, Any]] | None = None
    markdown: str | None = None
```

- [ ] **Step 4: Rewrite the tools**

Replace `src/simconnect_mcp/tools/facilities.py`:

```python
"""Facilities tools -- nearby airports and facility lookup.

Data arrives through the dispatcher's FacilityCollector.  The SimConnect
library's FacilitiesRequests is unusable here: its get() returns None and its
results only ever reach dump(), which prints to stdout.
"""
from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from pydantic import Field
from SimConnect.Enum import SIMCONNECT_FACILITY_LIST_TYPE

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.facilities import FacilityKind, great_circle_nm
from simconnect_mcp.tools import handle_simconnect_errors, require_connection
from simconnect_mcp.tools.formatting import DEFAULT_LIMIT, ResponseFormat, paginate, render_table
from simconnect_mcp.tools.models import FacilityInfo, FacilityList, ToolError

_LIST_TYPES = {
    FacilityKind.AIRPORT: SIMCONNECT_FACILITY_LIST_TYPE.SIMCONNECT_FACILITY_LIST_TYPE_AIRPORT,
    FacilityKind.WAYPOINT: SIMCONNECT_FACILITY_LIST_TYPE.SIMCONNECT_FACILITY_LIST_TYPE_WAYPOINT,
    FacilityKind.NDB: SIMCONNECT_FACILITY_LIST_TYPE.SIMCONNECT_FACILITY_LIST_TYPE_NDB,
    FacilityKind.VOR: SIMCONNECT_FACILITY_LIST_TYPE.SIMCONNECT_FACILITY_LIST_TYPE_VOR,
}

AIRPORT_COLUMNS = [
    ("icao", "ICAO"),
    ("distance_nm", "Distance (nm)"),
    ("latitude", "Latitude"),
    ("longitude", "Longitude"),
    ("altitude_ft", "Elevation (ft)"),
]

_COLLECT_TIMEOUT = 5.0
_POLL_INTERVAL = 0.1


async def _collect(kind: FacilityKind) -> list[dict] | ToolError:
    """Subscribe to a facility type and wait for the full list."""
    manager = SimConnectManager()
    collector = getattr(manager.sm, "facilities", None)
    if collector is None:
        return ToolError(
            error="FACILITIES_NOT_AVAILABLE",
            message="Facility data requires the SimConnect dispatcher.",
            suggestion="Reconnect with msfs_connect; the plain SimConnect "
                       "fallback cannot deliver facility data.",
        )

    def _subscribe() -> None:
        collector.reset(kind)
        manager.sm.dll.SubscribeToFacilities(
            manager.sm.hSimConnect,
            _LIST_TYPES[kind],
            manager.sm.new_request_id().value,
        )

    await manager.run_sync(_subscribe)

    waited = 0.0
    while waited < _COLLECT_TIMEOUT:
        if collector.is_complete(kind):
            break
        await asyncio.sleep(_POLL_INTERVAL)
        waited += _POLL_INTERVAL

    return collector.results(kind)


@handle_simconnect_errors
@require_connection
async def get_nearby_airports(
    latitude: Annotated[
        float | None,
        Field(description="Centre latitude. Defaults to the aircraft's position.",
              ge=-90, le=90),
    ] = None,
    longitude: Annotated[
        float | None,
        Field(description="Centre longitude. Defaults to the aircraft's position.",
              ge=-180, le=180),
    ] = None,
    radius_nm: Annotated[
        float, Field(description="Search radius in nautical miles", gt=0, le=500)
    ] = 50.0,
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=200)] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description="Results to skip", ge=0)] = 0,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' for a table, 'json' for rows")
    ] = ResponseFormat.MARKDOWN,
) -> FacilityList | ToolError:
    """List airports the sim has loaded, nearest first.

    The sim only publishes facilities within its own loaded area, so distant
    airports will not appear regardless of the radius.
    """
    manager = SimConnectManager()

    if latitude is None or longitude is None:
        pos = await manager.run_sync(
            lambda: manager.accessor.read_many([
                ("PLANE_LATITUDE", "degrees", None),
                ("PLANE_LONGITUDE", "degrees", None),
            ])
        )
        latitude = latitude if latitude is not None else pos["PLANE_LATITUDE"].get("value")
        longitude = longitude if longitude is not None else pos["PLANE_LONGITUDE"].get("value")
        if latitude is None or longitude is None:
            return ToolError(
                error="POSITION_UNAVAILABLE",
                message="Could not read the aircraft position for the search centre.",
                suggestion="Pass latitude and longitude explicitly.",
            )

    airports = await _collect(FacilityKind.AIRPORT)
    if isinstance(airports, ToolError):
        return airports

    nearby = []
    for airport in airports:
        distance = great_circle_nm(
            latitude, longitude, airport["latitude"], airport["longitude"]
        )
        if distance <= radius_nm:
            nearby.append({**airport, "distance_nm": round(distance, 1)})
    nearby.sort(key=lambda a: a["distance_nm"])

    window, page = paginate(nearby, offset, limit)
    center = {"latitude": latitude, "longitude": longitude}

    if response_format is ResponseFormat.JSON:
        return FacilityList(page=page, center=center, radius_nm=radius_nm, results=window)

    markdown = render_table(
        window, AIRPORT_COLUMNS, title=f"Airports within {radius_nm} nm"
    )
    if page.has_more:
        markdown += f"\n\n_Call again with offset={page.next_offset} for more._"
    return FacilityList(page=page, center=center, radius_nm=radius_nm, markdown=markdown)


@handle_simconnect_errors
@require_connection
async def get_facility_info(
    icao: Annotated[
        str, Field(description="ICAO identifier, e.g. 'KJFK', 'EGLL', 'SEA'",
                   min_length=2, max_length=8),
    ],
    facility_type: Annotated[
        Literal["airport", "waypoint", "ndb", "vor"],
        Field(description="Kind of facility to look up"),
    ] = "airport",
) -> FacilityInfo | ToolError:
    """Look up one airport, waypoint, NDB or VOR by ICAO identifier.

    Only facilities the sim has loaded are visible, so this generally finds
    facilities near the aircraft.
    """
    kind = FacilityKind(facility_type)
    entries = await _collect(kind)
    if isinstance(entries, ToolError):
        return entries

    needle = icao.strip().upper()
    for entry in entries:
        if entry["icao"].upper() == needle:
            return FacilityInfo(facility=entry)

    return ToolError(
        error="FACILITY_NOT_FOUND",
        message=f"No {facility_type} '{icao}' among the {len(entries)} loaded.",
        suggestion="The sim only publishes facilities it has loaded. Fly closer, "
                   "or widen the radius with msfs_get_nearby_airports.",
    )
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_facilities_tools.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Add live coverage**

Create `tests/live/test_live_facilities.py`:

```python
import pytest

pytestmark = pytest.mark.live


async def test_nearby_airports_returns_real_data(live_manager):
    from simconnect_mcp.tools.facilities import get_nearby_airports
    from simconnect_mcp.tools.formatting import ResponseFormat

    result = await get_nearby_airports(radius_nm=100, response_format=ResponseFormat.JSON)
    assert result.page.total > 0, "expected at least one loaded airport"
    assert all("icao" in a and "distance_nm" in a for a in result.results)


async def test_facility_lookup_round_trips(live_manager):
    """Take an ICAO from the nearby list and look it up directly."""
    from simconnect_mcp.tools.facilities import get_facility_info, get_nearby_airports
    from simconnect_mcp.tools.formatting import ResponseFormat

    nearby = await get_nearby_airports(radius_nm=100, limit=1,
                                       response_format=ResponseFormat.JSON)
    icao = nearby.results[0]["icao"]
    found = await get_facility_info(icao)
    assert found.facility["icao"] == icao
```

- [ ] **Step 7: Commit**

```bash
git add src/simconnect_mcp/tools/facilities.py src/simconnect_mcp/tools/models.py tests/test_facilities_tools.py tests/live/test_live_facilities.py
git commit -m "feat: implement the facilities tools on the new dispatcher

Replaces the NOT_IMPLEMENTED stubs with radius search and ICAO lookup."
```

---

### Task 3: Read the MobiFlight response channel

The audit found that the WASM module's response strings **already arrive** at `client_data_callback_handler`. The handler checks `if client_data.dwDefineID in self.sim_vars`, but variable IDs start at 1 (`id = len(self.sim_vars) + 1`) while the response channel uses `DATA_STRING_DEFINITION_ID = 0`. Every response therefore falls to the `else` branch and is logged as `DefinitionID 0 not found!` — the data is being thrown away.

**Files:**
- Modify: `src/simconnect_mcp/vendor/mobiflight_variable_requests.py`
- Create: `tests/test_mobiflight_responses.py`

**Interfaces:**
- Consumes: nothing
- Produces: `MobiFlightVariableRequests.response_handlers: list[Callable[[str], None]]`, `add_response_handler(fn)`, `remove_response_handler(fn)`. `client_data_callback_handler` routes definition ID 0 to them.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mobiflight_responses.py`:

```python
import struct
from unittest.mock import MagicMock

from simconnect_mcp.vendor.mobiflight_variable_requests import MobiFlightVariableRequests


def _bridge():
    sm = MagicMock()
    bridge = MobiFlightVariableRequests(sm)
    return bridge


def _string_message(text: str):
    """A client-data message carrying an ASCII string in the 256-byte area."""
    payload = text.encode("ascii").ljust(256, b"\x00")
    words = struct.unpack(f"<{len(payload) // 4}I", payload)

    message = MagicMock()
    message.dwDefineID = 0  # DATA_STRING_DEFINITION_ID
    message.dwData = words
    return message


def test_response_strings_reach_a_registered_handler():
    """Regression: definition 0 is never in sim_vars, so every response
    string was logged as 'not found' and dropped."""
    bridge = _bridge()
    seen = []
    bridge.add_response_handler(seen.append)

    bridge.client_data_callback_handler(_string_message("A32NX_AUTOPILOT_1_ACTIVE"))

    assert seen == ["A32NX_AUTOPILOT_1_ACTIVE"]


def test_response_string_is_trimmed_of_padding():
    bridge = _bridge()
    seen = []
    bridge.add_response_handler(seen.append)
    bridge.client_data_callback_handler(_string_message("SHORT"))
    assert seen == ["SHORT"]


def test_removing_a_handler_stops_delivery():
    bridge = _bridge()
    seen = []
    bridge.add_response_handler(seen.append)
    bridge.remove_response_handler(seen.append)
    bridge.client_data_callback_handler(_string_message("IGNORED"))
    assert seen == []


def test_a_failing_handler_does_not_break_the_dispatch_thread():
    bridge = _bridge()
    good = []

    def boom(_):
        raise RuntimeError("handler exploded")

    bridge.add_response_handler(boom)
    bridge.add_response_handler(good.append)
    bridge.client_data_callback_handler(_string_message("STILL_DELIVERED"))

    assert good == ["STILL_DELIVERED"]


def test_variable_values_still_work():
    """Numeric variable updates must be unaffected."""
    bridge = _bridge()
    bridge.get("(L:TEST_VAR)")

    message = MagicMock()
    message.dwDefineID = 1
    message.dwData = struct.unpack("<I", struct.pack("<f", 42.0))
    bridge.client_data_callback_handler(message)
    bridge.client_data_callback_handler(message)  # first zero-check pass

    assert bridge.sim_vars[1].float_value == 42.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_mobiflight_responses.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'add_response_handler'`

- [ ] **Step 3: Add response handling to the bridge**

In `src/simconnect_mcp/vendor/mobiflight_variable_requests.py`, extend the local-changes note at the top:

```python
# Vendored from Koseng/MSFSPythonSimConnectMobiFlightExtension.
# Local changes:
#   * per-call logging demoted from INFO to DEBUG
#   * response-channel strings (definition ID 0) are routed to registered
#     handlers instead of being logged as "DefinitionID not found" and
#     dropped -- this is what makes MF.LVars.List usable
```

Add to `__init__`, before `self.sm.register_client_data_handler(...)`:

```python
        self.response_handlers = []
```

Add the two methods:

```python
    def add_response_handler(self, handler):
        """Register a callback for strings from the WASM response channel."""
        if handler not in self.response_handlers:
            self.response_handlers.append(handler)

    def remove_response_handler(self, handler):
        if handler in self.response_handlers:
            self.response_handlers.remove(handler)
```

Replace `client_data_callback_handler`:

```python
    def client_data_callback_handler(self, client_data):
        # Definition 0 is the response channel: a 256-byte ASCII string.
        # Variable definition IDs start at 1, so this never matched sim_vars
        # and every response was previously logged away.
        if client_data.dwDefineID == self.DATA_STRING_DEFINITION_ID:
            self._deliver_response(client_data)
            return

        if client_data.dwDefineID in self.sim_vars:
            data_bytes = struct.pack("I", client_data.dwData[0])
            float_data = struct.unpack('<f', data_bytes)[0]
            float_value = round(float_data, 5)
            sim_var = self.sim_vars[client_data.dwDefineID]
            if not sim_var.initialized and float_value == 0.0:
                sim_var.initialized = True
            else:
                self.sim_vars[client_data.dwDefineID].float_value = float_value
            logging.debug("client_data_callback_handler %s, raw=%s", sim_var, float_value)
        else:
            logging.warning("client_data_callback_handler DefinitionID %s not found!",
                            client_data.dwDefineID)

    def _deliver_response(self, client_data):
        """Decode the response string and fan it out to handlers."""
        try:
            words = list(client_data.dwData)[: self.DATA_STRING_SIZE // 4]
            raw = struct.pack(f"<{len(words)}I", *words)
            text = raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
        except Exception:
            logging.debug("Could not decode WASM response payload", exc_info=True)
            return

        if not text:
            return
        logging.debug("WASM response: %s", text)
        for handler in list(self.response_handlers):
            try:
                handler(text)
            except Exception:
                # One bad handler must not kill the dispatch thread.
                logging.debug("Response handler raised", exc_info=True)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_mobiflight_responses.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/vendor/mobiflight_variable_requests.py tests/test_mobiflight_responses.py
git commit -m "feat: route WASM response-channel strings to handlers

Definition ID 0 was never in sim_vars, so every response the module
sent back was logged as 'not found' and discarded."
```

---

### Task 4: Implement `msfs_list_lvars`

**Files:**
- Modify: `src/simconnect_mcp/tools/lvars.py` (`list_lvars`)
- Modify: `tests/test_search.py` or create `tests/test_lvar_listing.py`

**Interfaces:**
- Consumes: `manager.mobiflight.add_response_handler`, `send_command`
- Produces: `list_lvars(filter_prefix=None, limit, offset)` returning `LVarList`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lvar_listing.py`:

```python
import asyncio

import pytest

from simconnect_mcp.tools.lvars import list_lvars


@pytest.fixture
def mobiflight_sim(mock_simconnect):
    """A MobiFlight bridge that answers MF.LVars.List with three names."""
    manager = mock_simconnect["manager"]
    manager._mobiflight_available = True

    class FakeBridge:
        def __init__(self):
            self.response_handlers = []
            self.commands = []

        def add_response_handler(self, fn):
            self.response_handlers.append(fn)

        def remove_response_handler(self, fn):
            if fn in self.response_handlers:
                self.response_handlers.remove(fn)

        def send_command(self, command):
            self.commands.append(command)
            for handler in list(self.response_handlers):
                for name in ("A32NX_AUTOPILOT_1_ACTIVE", "A32NX_FCU_HDG", "XMLVAR_Baro"):
                    handler(name)
                handler("MF.LVars.List.End")

    bridge = FakeBridge()
    manager.mobiflight = bridge
    mock_simconnect["bridge"] = bridge
    return mock_simconnect


async def test_list_lvars_returns_real_names(mobiflight_sim):
    result = await list_lvars()
    assert result.lvars == ["A32NX_AUTOPILOT_1_ACTIVE", "A32NX_FCU_HDG", "XMLVAR_Baro"]
    assert result.page.total == 3


async def test_list_lvars_sends_the_list_command(mobiflight_sim):
    await list_lvars()
    assert "MF.LVars.List" in mobiflight_sim["bridge"].commands


async def test_list_lvars_filters_by_prefix(mobiflight_sim):
    result = await list_lvars(filter_prefix="A32NX")
    assert all(name.startswith("A32NX") for name in result.lvars)
    assert result.page.total == 2


async def test_list_lvars_paginates(mobiflight_sim):
    result = await list_lvars(limit=2, offset=0)
    assert result.page.count == 2
    assert result.page.has_more is True


async def test_list_lvars_unregisters_its_handler(mobiflight_sim):
    """A leaked handler would accumulate on every call."""
    await list_lvars()
    assert mobiflight_sim["bridge"].response_handlers == []


async def test_list_lvars_without_mobiflight_errors(mock_simconnect):
    mock_simconnect["manager"]._mobiflight_available = False
    result = await list_lvars()
    assert result.error == "MOBIFLIGHT_NOT_AVAILABLE"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_lvar_listing.py -v`
Expected: FAIL — `list_lvars` returns `NOT_IMPLEMENTED`

- [ ] **Step 3: Implement the tool**

Replace `list_lvars` in `src/simconnect_mcp/tools/lvars.py`:

```python
_LIST_TERMINATORS = ("MF.LVars.List.End", "MF.LVars.List.Complete")
_LIST_SETTLE_S = 1.5
_LIST_TIMEOUT_S = 10.0


@handle_simconnect_errors
@require_connection
async def list_lvars(
    filter_prefix: Annotated[
        str | None,
        Field(description="Only return names starting with this prefix, e.g. "
                          "'A32NX', 'WT_CJ4', 'XMLVAR'. Case-insensitive."),
    ] = None,
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=200)] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description="Results to skip", ge=0)] = 0,
) -> LVarList | ToolError:
    """Enumerate the L-vars registered by the currently loaded aircraft.

    Asks the MobiFlight WASM module for its L-var list and collects the
    response.  Use this to discover variables on aircraft that have no
    bundled catalog; use msfs_search_lvars for aircraft that do.
    """
    err = _require_mobiflight()  # returns ToolError | None (Phase 1 Task 5)
    if err:
        return err

    manager = SimConnectManager()
    bridge = manager.mobiflight

    names: list[str] = []
    finished = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_response(text: str) -> None:
        # Called from the SimConnect dispatch thread.
        if text in _LIST_TERMINATORS:
            loop.call_soon_threadsafe(finished.set)
            return
        if text.startswith("MF."):
            return  # command echo, not an L-var name
        names.append(text)

    bridge.add_response_handler(_on_response)
    try:
        await manager.run_sync(lambda: bridge.send_command("MF.LVars.List"))
        try:
            await asyncio.wait_for(finished.wait(), timeout=_LIST_TIMEOUT_S)
        except asyncio.TimeoutError:
            # Some WASM builds send no terminator. Accept what arrived, as
            # long as something did.
            pass
        # Let any trailing names land.
        await asyncio.sleep(_LIST_SETTLE_S if not finished.is_set() else 0)
    finally:
        bridge.remove_response_handler(_on_response)

    if not names:
        return ToolError(
            error="NO_LVARS_RETURNED",
            message="The MobiFlight WASM module returned no L-var names.",
            suggestion="Ensure an aircraft is fully loaded and that the "
                       "MobiFlight WASM module supports MF.LVars.List. "
                       "Use msfs_search_lvars for catalogued aircraft.",
        )

    if filter_prefix:
        needle = filter_prefix.strip().upper()
        names = [n for n in names if n.upper().startswith(needle)]

    names = sorted(dict.fromkeys(names))
    window, page = paginate([{"name": n} for n in names], offset, limit)
    return LVarList(page=page, lvars=[row["name"] for row in window])
```

Add `import asyncio` and the `paginate`, `DEFAULT_LIMIT`, `LVarList` imports.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_lvar_listing.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Add live coverage**

Create `tests/live/test_live_lvars.py`:

```python
import pytest

pytestmark = pytest.mark.live


async def test_list_lvars_enumerates_the_loaded_aircraft(live_manager):
    """Requires the MobiFlight WASM module in the Community folder."""
    from simconnect_mcp.tools.lvars import list_lvars

    result = await list_lvars(limit=200)
    if getattr(result, "error", None) == "MOBIFLIGHT_NOT_AVAILABLE":
        pytest.skip("MobiFlight WASM module not installed")

    assert result.page.total > 0
    assert all(isinstance(name, str) and name for name in result.lvars)
```

- [ ] **Step 6: Commit**

```bash
git add src/simconnect_mcp/tools/lvars.py tests/test_lvar_listing.py tests/live/test_live_lvars.py
git commit -m "feat: msfs_list_lvars really enumerates aircraft L-vars

Replaces the canned success string with a real WASM list request."
```

---

### Task 5: Expose HubHop as a tool

`data/hubhop.py` is 591 lines of tested, working client code that the MCP server never touches. It is the answer to "this aircraft has no catalog" — the community database covers far more aircraft than the three bundled catalogs.

**Files:**
- Create: `src/simconnect_mcp/tools/hubhop.py`
- Modify: `src/simconnect_mcp/tools/models.py` (add `HubHopResult`)
- Create: `tests/test_hubhop_tool.py`

**Interfaces:**
- Consumes: `data.hubhop.HubHopClient`
- Produces: `search_hubhop(query=None, vendor=None, aircraft=None, system=None, limit, offset, response_format)`, `list_hubhop_aircraft(vendor=None, limit, offset, response_format)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hubhop_tool.py`:

```python
from unittest.mock import patch

import pytest

from simconnect_mcp.tools.formatting import ResponseFormat

PRESETS = [
    {"label": "Autopilot 1 On", "vendor": "FenixSim", "aircraft": "A320",
     "system": "Autopilot", "presetType": "Input", "code": "1 (>L:S_FCU_AP1)"},
    {"label": "Landing Light On", "vendor": "FenixSim", "aircraft": "A320",
     "system": "Lights", "presetType": "Input", "code": "1 (>L:S_OH_EXT_LT_LAND_L)"},
    {"label": "Battery 1", "vendor": "PMDG", "aircraft": "B737-800",
     "system": "Electrical", "presetType": "Input", "code": "1 (>L:switch_1)"},
]


@pytest.fixture
def hubhop_offline():
    with patch("simconnect_mcp.data.hubhop.HubHopClient.fetch_all", return_value=PRESETS):
        yield


async def test_search_matches_on_label(hubhop_offline):
    from simconnect_mcp.tools.hubhop import search_hubhop

    result = await search_hubhop(query="autopilot", response_format=ResponseFormat.JSON)
    assert result.page.total == 1
    assert result.results[0]["label"] == "Autopilot 1 On"


async def test_search_filters_by_vendor(hubhop_offline):
    from simconnect_mcp.tools.hubhop import search_hubhop

    result = await search_hubhop(vendor="PMDG", response_format=ResponseFormat.JSON)
    assert result.page.total == 1
    assert result.results[0]["vendor"] == "PMDG"


async def test_search_defaults_to_markdown(hubhop_offline):
    from simconnect_mcp.tools.hubhop import search_hubhop

    result = await search_hubhop(query="light")
    assert result.markdown is not None
    assert "Landing Light On" in result.markdown


async def test_search_requires_at_least_one_filter(hubhop_offline):
    from simconnect_mcp.tools.hubhop import search_hubhop

    result = await search_hubhop()
    assert result.error == "NO_FILTER"


async def test_network_failure_is_reported_actionably():
    from simconnect_mcp.tools.hubhop import search_hubhop

    with patch(
        "simconnect_mcp.data.hubhop.HubHopClient.fetch_all",
        side_effect=OSError("getaddrinfo failed"),
    ):
        result = await search_hubhop(query="autopilot")
    assert result.error == "HUBHOP_UNAVAILABLE"
    assert "internet" in result.suggestion.lower() or "offline" in result.suggestion.lower()


async def test_list_aircraft_groups_by_vendor(hubhop_offline):
    from simconnect_mcp.tools.hubhop import list_hubhop_aircraft

    result = await list_hubhop_aircraft(vendor="FenixSim", response_format=ResponseFormat.JSON)
    assert [a["aircraft"] for a in result.results] == ["A320"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_hubhop_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simconnect_mcp.tools.hubhop'`

- [ ] **Step 3: Create the tool module**

Create `src/simconnect_mcp/tools/hubhop.py`:

```python
"""HubHop community preset search.

Wraps the existing data/hubhop.py client so agents can look up events and
L-vars for aircraft that have no bundled catalog.

HubHop is an HTTP API, not SimConnect, so these calls go through an executor
directly rather than SimConnectManager.run_sync -- holding the sim lock for a
network round trip would stall every other tool.
"""
from __future__ import annotations

import asyncio
from typing import Annotated

from pydantic import Field

from simconnect_mcp.tools import handle_simconnect_errors
from simconnect_mcp.tools.formatting import (
    DEFAULT_LIMIT,
    ResponseFormat,
    build_search_result,
)
from simconnect_mcp.tools.models import SearchResult, ToolError

PRESET_COLUMNS = [
    ("label", "Preset"),
    ("vendor", "Vendor"),
    ("aircraft", "Aircraft"),
    ("system", "System"),
    ("presetType", "Type"),
    ("code", "RPN Code"),
]

AIRCRAFT_COLUMNS = [
    ("aircraft", "Aircraft"),
    ("vendor", "Vendor"),
    ("systems", "Systems"),
]

_TIMEOUT_S = 20.0

_UNAVAILABLE = ToolError(
    error="HUBHOP_UNAVAILABLE",
    message="Could not reach the HubHop API.",
    suggestion="HubHop needs internet access. Check your connection, or work "
               "offline with msfs_search_lvars against the bundled catalogs.",
)


async def _run(fn, *args, **kwargs):
    """Run a blocking HubHop call off the event loop, with a timeout."""
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, lambda: fn(*args, **kwargs)), timeout=_TIMEOUT_S
    )


@handle_simconnect_errors
async def search_hubhop(
    query: Annotated[
        str | None,
        Field(description="Text to match against preset labels and RPN code, "
                          "e.g. 'autopilot', 'landing light'"),
    ] = None,
    vendor: Annotated[
        str | None, Field(description="Vendor name, e.g. 'FenixSim', 'PMDG', 'Asobo'")
    ] = None,
    aircraft: Annotated[
        str | None, Field(description="Aircraft model, e.g. 'A320', 'B737-800'")
    ] = None,
    system: Annotated[
        str | None, Field(description="System, e.g. 'Autopilot', 'Lights', 'Electrical'")
    ] = None,
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=200)] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description="Results to skip", ge=0)] = 0,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' for a table, 'json' for rows")
    ] = ResponseFormat.MARKDOWN,
) -> SearchResult | ToolError:
    """Search the MobiFlight HubHop community preset database.

    HubHop covers far more aircraft than the bundled catalogs, so this is the
    place to look when msfs_search_lvars finds nothing for the loaded aircraft.
    Each result carries RPN code you can pass to msfs_execute_calculator_code.

    Requires internet access. Supply at least one of query, vendor, aircraft
    or system.
    """
    if not any((query, vendor, aircraft, system)):
        return ToolError(
            error="NO_FILTER",
            message="Supply at least one of: query, vendor, aircraft, system.",
            suggestion="The database holds tens of thousands of presets. "
                       "Try vendor='FenixSim' or query='autopilot'.",
        )

    from simconnect_mcp.data.hubhop import HubHopClient

    client = HubHopClient()
    try:
        presets = await _run(
            client.fetch_presets, vendor=vendor, aircraft=aircraft, system=system
        )
    except (OSError, asyncio.TimeoutError):
        return _UNAVAILABLE

    if query:
        needle = query.lower()
        presets = [
            p for p in presets
            if needle in str(p.get("label", "")).lower()
            or needle in str(p.get("code", "")).lower()
        ]

    return build_search_result(
        presets, offset, limit, response_format, PRESET_COLUMNS,
        title="HubHop presets",
        query=query,
        filters={"vendor": vendor, "aircraft": aircraft, "system": system},
    )


@handle_simconnect_errors
async def list_hubhop_aircraft(
    vendor: Annotated[
        str | None, Field(description="Restrict to one vendor, e.g. 'FenixSim'")
    ] = None,
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=200)] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description="Results to skip", ge=0)] = 0,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' for a table, 'json' for rows")
    ] = ResponseFormat.MARKDOWN,
) -> SearchResult | ToolError:
    """List the aircraft that HubHop has presets for.

    Use this to find the exact vendor and aircraft spelling to pass to
    msfs_search_hubhop. Requires internet access.
    """
    from simconnect_mcp.data.hubhop import HubHopClient

    client = HubHopClient()
    try:
        aircraft = await _run(client.list_aircraft, vendor=vendor)
    except (OSError, asyncio.TimeoutError):
        return _UNAVAILABLE

    return build_search_result(
        aircraft, offset, limit, response_format, AIRCRAFT_COLUMNS,
        title="Aircraft with HubHop presets",
        filters={"vendor": vendor},
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_hubhop_tool.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/tools/hubhop.py tests/test_hubhop_tool.py
git commit -m "feat: expose the HubHop client as MCP tools

591 lines of tested client code the server could not reach."
```

---

### Task 6: Flight and scenario tools

For scripted test setup: load a saved flight, save the current one, load a flight plan, and spawn AI objects.

**Files:**
- Create: `src/simconnect_mcp/tools/flight.py`
- Modify: `src/simconnect_mcp/tools/models.py` (add `FlightResult`, `AiObjectResult`)
- Create: `tests/test_flight.py`

**Interfaces:**
- Consumes: `manager.sm.load_flight`, `save_flight`, `load_flight_plan`, `createSimulatedObject`
- Produces: `load_flight(path)`, `save_flight(path, title, description)`, `load_flight_plan(path)`, `create_ai_object(...)`

**Note on `save_flight`:** the library's implementation ends with an unconditional `return False`, so its return value says nothing about success. Verify by checking the file exists afterwards instead.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_flight.py`:

```python
import pytest


async def test_load_flight_rejects_a_relative_path(mock_simconnect):
    from simconnect_mcp.tools.flight import load_flight

    result = await load_flight("flights/test.FLT")
    assert result.error == "INVALID_PATH"
    assert "absolute" in result.suggestion.lower()


async def test_load_flight_rejects_a_missing_file(mock_simconnect, tmp_path):
    from simconnect_mcp.tools.flight import load_flight

    result = await load_flight(str(tmp_path / "nope.FLT"))
    assert result.error == "FILE_NOT_FOUND"


async def test_load_flight_rejects_the_wrong_extension(mock_simconnect, tmp_path):
    from simconnect_mcp.tools.flight import load_flight

    wrong = tmp_path / "test.txt"
    wrong.write_text("x")
    result = await load_flight(str(wrong))
    assert result.error == "INVALID_PATH"
    assert ".FLT" in result.suggestion


async def test_load_flight_calls_the_library(mock_simconnect, tmp_path):
    from simconnect_mcp.tools.flight import load_flight

    flt = tmp_path / "test.FLT"
    flt.write_text("[Main]")
    mock_simconnect["sm"].load_flight.return_value = True

    result = await load_flight(str(flt))
    assert result.status == "ok"
    mock_simconnect["sm"].load_flight.assert_called_once_with(str(flt))


async def test_save_flight_verifies_the_file_rather_than_the_return_value(
    mock_simconnect, tmp_path
):
    """The library's save_flight ends with an unconditional `return False`."""
    from simconnect_mcp.tools.flight import save_flight

    target = tmp_path / "saved.FLT"

    def _fake_save(path, title, description, *a, **k):
        (tmp_path / "saved.FLT").write_text("[Main]")
        return False  # what the library actually returns

    mock_simconnect["sm"].save_flight.side_effect = _fake_save

    result = await save_flight(str(target), title="T", description="D")
    assert result.status == "ok"


async def test_save_flight_reports_a_genuine_failure(mock_simconnect, tmp_path):
    from simconnect_mcp.tools.flight import save_flight

    mock_simconnect["sm"].save_flight.return_value = False  # writes nothing
    result = await save_flight(str(tmp_path / "never.FLT"), title="T", description="D")
    assert result.error == "SAVE_FAILED"


async def test_create_ai_object_validates_coordinates(mock_simconnect):
    from simconnect_mcp.tools.flight import create_ai_object

    result = await create_ai_object(title="Boeing 747-8i", latitude=200.0, longitude=0.0)
    assert result.error is not None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_flight.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Add the models**

Append to `src/simconnect_mcp/tools/models.py`:

```python
class FlightResult(OkModel):
    action: str
    path: str
    message: str


class AiObjectResult(OkModel):
    title: str
    latitude: float
    longitude: float
    message: str
```

- [ ] **Step 4: Create the tool module**

Create `src/simconnect_mcp/tools/flight.py`:

```python
"""Flight and scenario tools -- load/save flights, flight plans, AI objects.

Aimed at scripted test-scenario setup: put the aircraft into a known state,
capture it, and replay it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection
from simconnect_mcp.tools.models import AiObjectResult, FlightResult, ToolError


def _validate_path(path: str, suffix: str, must_exist: bool) -> ToolError | Path:
    """Check a sim file path. Returns a Path or the ToolError to send back."""
    candidate = Path(path)
    if not candidate.is_absolute():
        return ToolError(
            error="INVALID_PATH",
            message=f"'{path}' is not an absolute path.",
            suggestion="MSFS resolves these paths itself, so give an absolute "
                       r"path such as C:\Users\you\Documents\flight.FLT",
        )
    if candidate.suffix.upper() != suffix.upper():
        return ToolError(
            error="INVALID_PATH",
            message=f"'{path}' does not end in {suffix}.",
            suggestion=f"Flight files use {suffix}.",
        )
    if must_exist and not candidate.exists():
        return ToolError(
            error="FILE_NOT_FOUND",
            message=f"No file at '{path}'.",
            suggestion="Check the path. Saved flights usually live under "
                       "Documents or the MSFS package folder.",
        )
    return candidate


@handle_simconnect_errors
@require_connection
async def load_flight(
    path: Annotated[
        str,
        Field(description=r"Absolute path to a .FLT file, e.g. "
                          r"'C:\Users\you\Documents\approach-test.FLT'",
              min_length=4),
    ],
) -> FlightResult | ToolError:
    """Load a saved flight, replacing the current one.

    Use this to restore a known starting state before a test run. The current
    flight is discarded without prompting.
    """
    validated = _validate_path(path, ".FLT", must_exist=True)
    if isinstance(validated, ToolError):
        return validated

    manager = SimConnectManager()
    ok = await manager.run_sync(lambda: manager.sm.load_flight(str(validated)))
    if not ok:
        return ToolError(
            error="LOAD_FAILED",
            message=f"MSFS refused to load '{path}'.",
            suggestion="Check the file is a valid .FLT for this MSFS version, "
                       "and that the sim is not mid-load.",
        )
    return FlightResult(
        action="load_flight", path=str(validated), message=f"Loaded flight '{validated.name}'"
    )


@handle_simconnect_errors
@require_connection
async def save_flight(
    path: Annotated[str, Field(description="Absolute path for the .FLT file to write",
                               min_length=4)],
    title: Annotated[str, Field(description="Flight title shown in MSFS",
                                min_length=1, max_length=128)],
    description: Annotated[
        str, Field(description="Flight description", max_length=512)
    ] = "",
) -> FlightResult | ToolError:
    """Save the current flight to a .FLT file.

    Capture a known state so msfs_load_flight can restore it later.
    """
    validated = _validate_path(path, ".FLT", must_exist=False)
    if isinstance(validated, ToolError):
        return validated

    manager = SimConnectManager()
    # The library's save_flight ends with an unconditional `return False`, so
    # its return value is meaningless. Check the file instead.
    await manager.run_sync(
        lambda: manager.sm.save_flight(str(validated), title, description)
    )

    if not validated.exists():
        return ToolError(
            error="SAVE_FAILED",
            message=f"MSFS did not write '{path}'.",
            suggestion="Check the directory exists and is writable, and that "
                       "a flight is currently loaded.",
        )
    return FlightResult(
        action="save_flight", path=str(validated), message=f"Saved flight to '{validated.name}'"
    )


@handle_simconnect_errors
@require_connection
async def load_flight_plan(
    path: Annotated[str, Field(description="Absolute path to a .PLN flight plan",
                               min_length=4)],
) -> FlightResult | ToolError:
    """Load a .PLN flight plan into the aircraft's GPS or FMS.

    The aircraft is not repositioned; only the plan is loaded.
    """
    validated = _validate_path(path, ".PLN", must_exist=True)
    if isinstance(validated, ToolError):
        return validated

    manager = SimConnectManager()
    ok = await manager.run_sync(lambda: manager.sm.load_flight_plan(str(validated)))
    if not ok:
        return ToolError(
            error="LOAD_FAILED",
            message=f"MSFS refused to load flight plan '{path}'.",
            suggestion="Check the file is a valid .PLN for this MSFS version.",
        )
    return FlightResult(
        action="load_flight_plan", path=str(validated),
        message=f"Loaded flight plan '{validated.name}'",
    )


@handle_simconnect_errors
@require_connection
async def create_ai_object(
    title: Annotated[
        str,
        Field(description="Exact aircraft or object title as MSFS knows it, "
                          "e.g. 'Boeing 747-8i Asobo'", min_length=1, max_length=128),
    ],
    latitude: Annotated[float, Field(description="Latitude", ge=-90, le=90)],
    longitude: Annotated[float, Field(description="Longitude", ge=-180, le=180)],
    altitude_ft: Annotated[
        float, Field(description="Altitude in feet", ge=-2000, le=275000)
    ] = 0.0,
    heading: Annotated[float, Field(description="Heading in degrees true", ge=0, lt=360)] = 0.0,
    on_ground: Annotated[bool, Field(description="Place the object on the ground")] = True,
    airspeed: Annotated[int, Field(description="Airspeed in knots", ge=0, le=2000)] = 0,
) -> AiObjectResult | ToolError:
    """Spawn an AI aircraft or object at a position.

    Useful for building traffic or collision-avoidance test scenarios. The
    title must match an installed aircraft exactly.
    """
    manager = SimConnectManager()

    def _create() -> None:
        manager.sm.createSimulatedObject(
            title,
            latitude,
            longitude,
            manager.sm.new_request_id(),
            hdg=heading,
            gnd=1 if on_ground else 0,
            alt=altitude_ft,
            speed=airspeed,
        )

    await manager.run_sync(_create)
    return AiObjectResult(
        title=title, latitude=latitude, longitude=longitude,
        message=f"Requested AI object '{title}'. MSFS ignores the request "
                "silently if the title does not match an installed aircraft.",
    )
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_flight.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add src/simconnect_mcp/tools/flight.py src/simconnect_mcp/tools/models.py tests/test_flight.py
git commit -m "feat: flight and scenario tools for scripted test setup

save_flight verifies the written file, since the library's
implementation returns False unconditionally."
```

---

### Task 7: Register the Phase 2 tools

**Files:**
- Modify: `src/simconnect_mcp/server.py`
- Modify: `tests/test_registration.py`

**Interfaces:**
- Consumes: the Phase 2 tool functions
- Produces: 32 registered tools.

- [ ] **Step 1: Update the registration tests**

In `tests/test_registration.py`, change the expected count and add the new names:

```python
async def test_expected_tool_count():
    assert len(await _tools()) == 32


async def test_phase_two_tools_are_registered():
    names = await _tools()
    for name in ("msfs_search_hubhop", "msfs_list_hubhop_aircraft", "msfs_load_flight",
                 "msfs_save_flight", "msfs_load_flight_plan", "msfs_create_ai_object"):
        assert name in names
```

Add `msfs_load_flight` and `msfs_create_ai_object` to `WRITE_TOOLS`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_registration.py -v`
Expected: FAIL — count is 26

- [ ] **Step 3: Register the new tools**

Append to the registration block in `src/simconnect_mcp/server.py`:

```python
# --- HubHop (community database, network) ---
_register(search_hubhop, "msfs_search_hubhop", "Search HubHop Presets",
          read_only=True, idempotent=True)
_register(list_hubhop_aircraft, "msfs_list_hubhop_aircraft", "List HubHop Aircraft",
          read_only=True, idempotent=True)

# --- Flight and scenario ---
_register(load_flight, "msfs_load_flight", "Load Saved Flight", read_only=False)
_register(save_flight, "msfs_save_flight", "Save Current Flight",
          read_only=False, destructive=False, idempotent=True)
_register(load_flight_plan, "msfs_load_flight_plan", "Load Flight Plan",
          read_only=False, destructive=False, idempotent=True)
_register(create_ai_object, "msfs_create_ai_object", "Create AI Object",
          read_only=False)
```

with the matching imports.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_registration.py -v`
Expected: PASS

- [ ] **Step 5: Inspect the final surface**

Run:
```bash
uv run python -c "
import asyncio
from simconnect_mcp.server import mcp
async def m():
    tools = sorted(await mcp.list_tools(), key=lambda x: x.name)
    print(len(tools), 'tools')
    for t in tools:
        a = t.annotations
        print(f'{t.name:32} ro={a.readOnlyHint!s:5} dest={a.destructiveHint!s:5} out={t.outputSchema is not None}')
asyncio.run(m())"
```
Expected: 32 rows, every one with `out=True`.

- [ ] **Step 6: Commit**

```bash
git add src/simconnect_mcp/server.py tests/test_registration.py
git commit -m "feat: register HubHop and flight tools (32 total)"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: the final tool inventory
- Produces: documentation matching the shipped server.

- [ ] **Step 1: Generate the authoritative tool table**

Run:
```bash
uv run python -c "
import asyncio
from simconnect_mcp.server import mcp
async def m():
    for t in sorted(await mcp.list_tools(), key=lambda x: x.name):
        kind = 'read' if t.annotations.readOnlyHint else 'write'
        print(f'| \`{t.name}\` | {kind} | {t.annotations.title} |')
asyncio.run(m())" > /tmp/tools.md && cat /tmp/tools.md
```

- [ ] **Step 2: Update README.md**

Replace the tool listing with the generated table. Update:
- Every tool name in examples to its `msfs_` form.
- The install command: `uv sync` now installs the dev group, so `uv pip install -e ".[dev]"` becomes `uv sync`.
- The features list: facilities, L-var enumeration, HubHop search and flight/scenario tools are now real.
- Add a short "Units" note: `msfs_get_simvar` accepts a `unit` and reports the unit actually used; the bundled catalog supplies the default.
- Add a "Running the live tests" section: `uv run pytest -m live` with MSFS running.

- [ ] **Step 3: Update CLAUDE.md**

Update the architecture diagram to include `dispatch.py`, `simvar_access.py` and `facilities.py`. Replace the "Key Design Decisions" entries that no longer hold:

- SimVar access no longer goes through `AircraftRequests`; `SimVarAccessor` builds data definitions directly, which is what makes unit selection, unlisted variables, string variables and honest write failures possible.
- `SimConnectDispatcher` owns `my_dispatch_proc`. Anything added to the dispatch loop must never fall through to the library's `SYSTEM_STATE` or `*_LIST` branches, both of which `print()` to stdout and would corrupt the JSON-RPC stream.
- The vendored bridge has two documented local changes (log levels, response-channel handlers). Re-syncing from upstream must preserve them.
- Note the tool naming convention (`msfs_*`) and that every new tool needs explicit `ToolAnnotations` and a `Model | ToolError` return type.
- Keep the `uv run pytest` section, and add `uv run pytest -m live`.

- [ ] **Step 4: Verify the documented commands actually work**

Run: `uv run pytest -q && uv run ruff check src/ tests/`
Expected: PASS, clean

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: update README and CLAUDE.md for the modernized server"
```

---

### Task 9: Phase 2 live verification

- [ ] **Step 1: Ask the user to launch MSFS 2024**

Stop and ask the user to start MSFS 2024 and load an aircraft at an airport with other airports nearby (Seattle, London or Frankfurt all work well). Wait for confirmation.

- [ ] **Step 2: Run the full live suite**

Run: `uv run pytest -m live -v`
Expected: PASS. `test_list_lvars_enumerates_the_loaded_aircraft` skips if the MobiFlight WASM module is not installed.

- [ ] **Step 3: Smoke-test the server end to end**

Run:
```bash
uv run python -c "
import asyncio
from simconnect_mcp.server import mcp
async def m():
    r = await mcp.call_tool('msfs_connect', {})
    print('connect:', r[1])
    r = await mcp.call_tool('msfs_get_simvar', {'name': 'PLANE_ALTITUDE', 'unit': 'feet'})
    print('altitude:', r[1])
    r = await mcp.call_tool('msfs_get_aircraft_snapshot', {'sections': ['identity','position']})
    print('snapshot:', r[1])
    r = await mcp.call_tool('msfs_search_events', {'keyword': 'pushback', 'limit': 3})
    print('events:', r[1])
asyncio.run(m())"
```
Expected: real values; the altitude in feet; the snapshot showing `TITLE` as a `str`, not `bytes`.

- [ ] **Step 4: Confirm stdout stayed clean**

Run:
```bash
uv run python -c "
import asyncio, io, sys
buf = io.StringIO(); real = sys.stdout; sys.stdout = buf
from simconnect_mcp.server import mcp
async def m():
    await mcp.call_tool('msfs_connect', {})
    await mcp.call_tool('msfs_get_nearby_airports', {'radius_nm': 100})
    await mcp.call_tool('msfs_get_connection_status', {})
asyncio.run(m())
sys.stdout = real
print('stdout bytes during tool calls:', len(buf.getvalue()))
print(repr(buf.getvalue()[:400]))"
```
Expected: `0`. Anything else means a library print path is still reachable — the facilities call is the one that would trigger it.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: issues found during Phase 2 live verification"
```

---

## Phase 2 Exit Criteria

- [ ] `uv run pytest` passes; `uv run ruff check src/ tests/` is clean
- [ ] `uv run pytest -m live` passes against MSFS 2024
- [ ] 32 tools registered, all `msfs_`-prefixed, all with annotations and output schemas
- [ ] No tool returns `NOT_IMPLEMENTED`
- [ ] Zero bytes written to stdout across connect, facilities, and status calls
- [ ] README and CLAUDE.md match the shipped tool surface

## Whole-project verification

- [ ] Every failure in the spec's "Tools that cannot work" table is fixed, verified against the live sim
- [ ] `unit` is honoured and reported; `index=0` reaches the sim; failed writes raise
- [ ] Search results paginate with `total`/`has_more` instead of truncating at 50
- [ ] Configure the server in a real MCP client and confirm the tool list renders with titles and read-only badges
