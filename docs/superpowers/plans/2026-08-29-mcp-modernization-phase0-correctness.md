# SimConnect MCP Modernization — Phase 0: Correctness

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every tool that fails, lies about success, or can corrupt the stdio transport, by replacing the `AircraftRequests` SimVar path with a generic SimConnect data-definition layer and taking ownership of the dispatch loop.

**Architecture:** Two new modules sit between `SimConnectManager` and the `SimConnect` package. `dispatch.py` subclasses the vendored `SimConnectMobiFlight` and owns `my_dispatch_proc`, routing `SIMOBJECT_DATA` and `EXCEPTION` messages to in-flight requests and swallowing the two library branches that print to stdout. `simvar_access.py` builds SimConnect data definitions directly (`AddToDataDefinition` + `RequestDataOnSimObject` / `SetDataOnSimObject`), giving real unit support, access to variables outside the library's hardcoded table, string variables, and typed failures. Existing tools are then rewired onto it.

**Tech Stack:** Python 3.10+, `mcp[cli]` (FastMCP), `SimConnect` 0.4.26, ctypes, pytest, pytest-asyncio, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-08-29-mcp-modernization-design.md`

## Global Constraints

- Every SimConnect DLL call must run inside `SimConnectManager.run_sync()`, which holds `_sim_lock` and runs in an executor. Never call the DLL from the event loop.
- This is a **stdio** server. Nothing may ever be written to stdout. Logging goes to stderr only.
- `vendor/` stays byte-faithful to upstream except where a task explicitly says otherwise (only Task 17 touches it, for log levels).
- Existing 159 tests must stay green after every task.
- Tool error envelopes keep the field names `status`, `error`, `message`, `suggestion`.
- Tool names do **not** change in Phase 0. The `msfs_` prefix lands in Phase 1.
- Run tests with `uv run pytest` (Task 1 makes this work).

---

### Task 1: Project hygiene and test infrastructure

`uv run pytest` — the command documented in CLAUDE.md — currently fails with "program not found" because dev dependencies live in `[project.optional-dependencies]`, which uv does not install by default. Every later task needs this working. This task also corrects the `TITLE` mock to return `bytes` like the real sim; the current `str` mock is why bytes-handling bugs never surfaced in tests.

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/conftest.py:43` (the `TITLE` entry) and `tests/conftest.py:51` (the `get` side effect)
- Create: `tests/live/__init__.py`, `tests/live/conftest.py`

**Interfaces:**
- Consumes: nothing
- Produces: working `uv run pytest`; `pytest.mark.live` marker deselected by default; `tests/live/conftest.py` exposing a `live_manager` fixture that later tasks add live tests against.

- [ ] **Step 1: Confirm the documented command is broken**

Run: `uv run pytest -q`
Expected: FAIL with `error: Failed to spawn: 'pytest'  Caused by: program not found`

- [ ] **Step 2: Move dev dependencies to a dependency group and pin mcp**

In `pyproject.toml`, replace the `[project.optional-dependencies]` block with a `[dependency-groups]` block, and tighten the `mcp` pin. `>=1.0.0` spans a large API evolution; the output-schema and annotation work in Phase 1 needs 1.26.

```toml
dependencies = [
    "mcp[cli]>=1.26,<2",
    "SimConnect>=0.4.26",
]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.6"]
```

Then extend the pytest section and add ruff config:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "live: requires a running MSFS instance with an aircraft loaded (deselected by default)",
]
addopts = "-m 'not live'"

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

- [ ] **Step 3: Verify the documented command now works**

Run: `uv run pytest -q`
Expected: PASS, `159 passed`

- [ ] **Step 4: Write a failing test that TITLE is bytes**

The real sim returns `bytes` for string SimVars. Add to `tests/test_simvars.py`:

```python
def test_title_mock_returns_bytes_like_the_real_sim(mock_simconnect):
    """The sim returns bytes for string SimVars; the mock must match or
    bytes-handling bugs stay invisible to the suite."""
    assert isinstance(mock_simconnect["aq"].get("TITLE"), bytes)
```

- [ ] **Step 5: Run it to confirm it fails**

Run: `uv run pytest tests/test_simvars.py::test_title_mock_returns_bytes_like_the_real_sim -v`
Expected: FAIL — the mock currently returns `str`

- [ ] **Step 6: Make string SimVars in the mock return bytes**

In `tests/conftest.py`, change the three string entries in `simvar_values`:

```python
        "TITLE": b"Boeing 747-8i",
        "ATC_TYPE": b"B748",
        "ATC_ID": b"N12345",
```

- [ ] **Step 7: Run the full suite and fix any assertion that assumed str**

Run: `uv run pytest -q`
Expected: PASS. If a test compares against `"Boeing 747-8i"`, update it to `b"Boeing 747-8i"`. Do not add `.decode()` calls to production code in this task — Task 7 handles string decoding properly.

- [ ] **Step 8: Create the live test scaffolding**

Create `tests/live/__init__.py` as an empty file. Create `tests/live/conftest.py`:

```python
"""Fixtures for tests that need a running MSFS.

Deselected by default via `addopts = "-m 'not live'"` in pyproject.toml.
Run them with:  uv run pytest -m live
"""
from __future__ import annotations

import pytest

from simconnect_mcp.connection import SimConnectManager


@pytest.fixture(scope="session")
def live_manager():
    """A connected SimConnectManager, or skip if MSFS is not running."""
    manager = SimConnectManager()
    result = manager.connect()
    if result["status"] != "ok":
        pytest.skip(f"MSFS not available: {result.get('message')}")
    yield manager
    manager.disconnect()
```

- [ ] **Step 9: Verify live tests are deselected by default**

Run: `uv run pytest -q`
Expected: PASS, no live tests collected.
Run: `uv run pytest -m live --collect-only -q`
Expected: collects 0 items (no live tests exist yet) without error.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml tests/conftest.py tests/test_simvars.py tests/live/
git commit -m "chore: fix uv run pytest, pin mcp, add live test scaffolding

Dev deps were in [project.optional-dependencies], which uv does not
install by default, so the command documented in CLAUDE.md failed.
Also corrects the TITLE mock to return bytes like the real sim."
```

---

### Task 2: Extract the SimVar catalog into its own module

`tools/simvars.py` currently owns catalog loading, which means the new `simvar_access` layer cannot use it without importing from the tools package (a circular import, since tools import connection which will import simvar_access). Moving it to `data/` also makes the catalog load-bearing: SimConnect requires a unit string for every data definition, and the catalog's `unit` field becomes the default source.

**Files:**
- Create: `src/simconnect_mcp/data/simvar_catalog.py`
- Create: `tests/test_simvar_catalog.py`
- Modify: `src/simconnect_mcp/tools/simvars.py:15-178` (delete the moved code, import instead)

**Interfaces:**
- Consumes: nothing
- Produces:
  - `load_catalog() -> dict[str, list[dict]]` — category name → list of entries with keys `name`, `category`, `description`, `units`, `settable`
  - `flat_simvars() -> list[dict]`
  - `resolve_unit(name: str, explicit: str | None) -> str` — explicit → catalog `units` → `"number"`
  - `is_string_var(name: str) -> bool` — True when the catalog unit is `string`
  - `search_catalog(keyword: str, category: str | None) -> list[dict]` — no result cap; callers paginate
  - `suggest_names(name: str, limit: int = 5) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_simvar_catalog.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_simvar_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simconnect_mcp.data.simvar_catalog'`

- [ ] **Step 3: Create the module**

Create `src/simconnect_mcp/data/simvar_catalog.py`:

```python
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
    """Catalog entry for a SimVar name, ignoring any `:index` suffix."""
    load_catalog()
    assert _by_name is not None
    return _by_name.get(name.split(":", 1)[0].strip().upper())


def resolve_unit(name: str, explicit: str | None) -> str:
    """Resolve the unit for a data definition: explicit, then catalog, then number."""
    if explicit:
        return explicit.strip()
    entry = lookup(name)
    if entry and entry.get("units"):
        return str(entry["units"]).strip()
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_simvar_catalog.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Point tools/simvars.py at the new module**

In `src/simconnect_mcp/tools/simvars.py`, delete `_SIMVAR_CATALOG`, `_FLAT_SIMVARS`, `_load_catalog`, `_builtin_catalog`, `_search_catalog` and `_fuzzy_suggest` (lines 15–178), and the now-unused `json`, `re` and `Path` imports. Add:

```python
from simconnect_mcp.data.simvar_catalog import (
    load_catalog,
    search_catalog,
    suggest_names,
)
```

Update the three call sites: `_search_catalog(keyword, category)` → `search_catalog(keyword, category)[:50]` (the cap moves out in Phase 1), `_load_catalog()` → `load_catalog()`, `_fuzzy_suggest(name)` → `suggest_names(name)`.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/simconnect_mcp/data/simvar_catalog.py src/simconnect_mcp/tools/simvars.py tests/test_simvar_catalog.py
git commit -m "refactor: extract SimVar catalog into data/simvar_catalog.py

Gives simvar_access a dependency-free source for unit resolution and
replaces the four-character-prefix suggestion heuristic with difflib."
```

---

### Task 3: Fix L-var catalog discovery

`data/catalog.py` globs `data/*.json`, which picks up `simvars_catalog.json` — a completely different schema. It is loaded as an aircraft catalog with zero variables and an empty `title_pattern`, so `list_lvar_catalogs` reports a phantom aircraft.

**Files:**
- Modify: `src/simconnect_mcp/data/catalog.py:21-41` (`_load_all_catalogs`)
- Modify: `tests/test_search.py` (add regression test)

**Interfaces:**
- Consumes: nothing
- Produces: `data.catalog.list_catalogs()` returning only real aircraft catalogs.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_search.py`:

```python
def test_simvar_catalog_is_not_loaded_as_an_aircraft_catalog():
    """data/*.json also matches simvars_catalog.json, which has a different
    schema and would appear as a phantom aircraft with zero variables."""
    from simconnect_mcp.data.catalog import list_catalogs

    keys = {c["key"] for c in list_catalogs()}
    assert "simvars_catalog" not in keys
    assert all(c["variable_count"] > 0 for c in list_catalogs())
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_search.py::test_simvar_catalog_is_not_loaded_as_an_aircraft_catalog -v`
Expected: FAIL — `simvars_catalog` is present

- [ ] **Step 3: Require the aircraft-catalog shape**

In `src/simconnect_mcp/data/catalog.py`, replace the body of `_load_all_catalogs` with:

```python
def _load_all_catalogs() -> None:
    """Load aircraft L-var catalogs from the data directory.

    `data/*.json` also matches simvars_catalog.json, which uses an unrelated
    schema.  An aircraft catalog is identified by having both a non-empty
    `title_pattern` and a non-empty `variables` list.
    """
    if _catalogs:
        return

    for path in sorted(DATA_DIR.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("Failed to load catalog %s: %s", path, e)
            continue

        if not isinstance(data, dict):
            continue
        pattern = data.get("title_pattern", "")
        variables = data.get("variables", [])
        if not pattern or not variables:
            logger.debug("Skipping %s: not an aircraft L-var catalog", path.name)
            continue

        key = path.stem
        _catalogs[key] = data
        _TITLE_PATTERNS[pattern.lower()] = key
        logger.info("Loaded L-var catalog: %s (%d variables)", key, len(variables))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_search.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/simconnect_mcp/data/catalog.py tests/test_search.py
git commit -m "fix: stop loading simvars_catalog.json as an aircraft L-var catalog

The data/*.json glob matched it, so list_lvar_catalogs reported a
phantom aircraft with zero variables."
```

---

### Task 4: Dispatch layer — request registry and message routing

The vendored `SimConnectMobiFlight` intercepts only `CLIENT_DATA` and delegates the rest to `SimConnect.my_dispatch_proc`, two branches of which call `print()`: `handle_state_event` on every `SYSTEM_STATE` message, and the facilities branch via `dump()`. On a stdio server those writes land inside the JSON-RPC stream. This task takes ownership of the loop and adds the request registry that Tasks 5–7 resolve against.

The routing logic is deliberately split from the ctypes casting so it can be unit-tested without a live sim.

**Files:**
- Create: `src/simconnect_mcp/dispatch.py`
- Create: `tests/test_dispatch.py`

**Interfaces:**
- Consumes: `simconnect_mcp.vendor.simconnect_mobiflight.SimConnectMobiFlight`
- Produces:
  - `PendingRequest` dataclass with fields `request_id: int | None`, `is_string: bool`, `send_id: int | None`, `value`, `exception: str | None`, `done: threading.Event`
  - `RequestRegistry` with `pending_lock`, `register(req)`, `bind_send_id(req, send_id)`, `resolve_data(request_id, value) -> bool`, `resolve_exception(send_id, name) -> bool`, `discard(req)`
  - `SimConnectDispatcher(SimConnectMobiFlight)` exposing the registry as `self.registry` and a `facility_handlers: list` for Phase 2

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dispatch.py`:

```python
import threading

from simconnect_mcp.dispatch import PendingRequest, RequestRegistry


def test_resolve_data_sets_value_and_signals_waiter():
    registry = RequestRegistry()
    req = PendingRequest(request_id=7)
    registry.register(req)

    assert registry.resolve_data(7, 1234.5) is True
    assert req.done.wait(0.1)
    assert req.value == 1234.5
    assert req.exception is None


def test_resolve_data_for_unknown_request_is_ignored():
    registry = RequestRegistry()
    assert registry.resolve_data(999, 1.0) is False


def test_resolve_exception_matches_on_send_id():
    registry = RequestRegistry()
    req = PendingRequest(request_id=3)
    registry.register(req)
    registry.bind_send_id(req, send_id=42)

    assert registry.resolve_exception(42, "SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED") is True
    assert req.done.wait(0.1)
    assert req.exception == "SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED"
    assert req.value is None


def test_resolve_exception_for_unrelated_send_id_is_ignored():
    registry = RequestRegistry()
    req = PendingRequest(request_id=3)
    registry.register(req)
    registry.bind_send_id(req, send_id=42)

    assert registry.resolve_exception(99, "SIMCONNECT_EXCEPTION_UNRECOGNIZED_ID") is False
    assert not req.done.is_set()


def test_discard_removes_both_index_entries():
    registry = RequestRegistry()
    req = PendingRequest(request_id=5)
    registry.register(req)
    registry.bind_send_id(req, send_id=11)
    registry.discard(req)

    assert registry.resolve_data(5, 1.0) is False
    assert registry.resolve_exception(11, "X") is False


def test_write_request_has_no_request_id_but_still_matches_exceptions():
    """Writes get no SIMOBJECT_DATA reply, so they are matched by send_id only."""
    registry = RequestRegistry()
    req = PendingRequest(request_id=None)
    registry.register(req)
    registry.bind_send_id(req, send_id=77)

    assert registry.resolve_exception(77, "SIMCONNECT_EXCEPTION_UNRECOGNIZED_ID") is True
    assert req.exception == "SIMCONNECT_EXCEPTION_UNRECOGNIZED_ID"


def test_registry_lock_is_reentrant_safe_across_threads():
    """The accessor holds pending_lock across send + bind_send_id so the
    dispatch thread cannot deliver an exception before the send_id is known."""
    registry = RequestRegistry()
    req = PendingRequest(request_id=1)
    registry.register(req)
    seen = []

    with registry.pending_lock:
        t = threading.Thread(
            target=lambda: seen.append(registry.resolve_exception(50, "BOOM"))
        )
        t.start()
        t.join(0.2)
        assert seen == [], "dispatch thread must block until send_id is bound"
        registry.bind_send_id(req, send_id=50, _locked=True)

    t.join(1.0)
    assert seen == [True]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simconnect_mcp.dispatch'`

- [ ] **Step 3: Create the module**

Create `src/simconnect_mcp/dispatch.py`:

```python
"""Ownership of the SimConnect dispatch loop.

The vendored SimConnectMobiFlight intercepts only CLIENT_DATA messages and
delegates everything else to SimConnect.my_dispatch_proc.  Two of those
delegated branches write to stdout, which on a stdio MCP server corrupts the
JSON-RPC stream:

  * handle_state_event  -- bare print() on every SYSTEM_STATE message
  * the *_LIST branch   -- facilitie.dump() and parent.dump(), both print()

SimConnectDispatcher takes over the loop so those branches are unreachable,
and so SimVar reads and SimConnect exceptions can be correlated back to the
call that caused them.

Exception correlation uses SIMCONNECT_RECV_EXCEPTION.dwSendID against the
value returned by GetLastSentPacketID at send time.  (SimConnect.py's own
handle_exception_event compares against the constant field UNKNOWN_SENDID
instead of dwSendID, which is why its correlation never matches.)
"""
from __future__ import annotations

import ctypes
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from SimConnect.Enum import (
    SIMCONNECT_EXCEPTION,
    SIMCONNECT_RECV_EXCEPTION,
    SIMCONNECT_RECV_ID,
    SIMCONNECT_RECV_SIMOBJECT_DATA,
)

from simconnect_mcp.vendor.simconnect_mobiflight import SimConnectMobiFlight

log = logging.getLogger(__name__)

FACILITY_RECV_IDS = frozenset({
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_AIRPORT_LIST,
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_WAYPOINT_LIST,
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_NDB_LIST,
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_VOR_LIST,
})


@dataclass
class PendingRequest:
    """One in-flight SimVar read or write.

    Reads carry a `request_id` and are resolved by SIMOBJECT_DATA.  Writes
    produce no reply, so `request_id` is None and they are only ever resolved
    by an exception (or by timing out successfully).
    """

    request_id: int | None
    is_string: bool = False
    send_id: int | None = None
    value: Any = None
    exception: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


class RequestRegistry:
    """Thread-safe index of in-flight requests, by request ID and send ID.

    `pending_lock` is public on purpose: the accessor holds it across the DLL
    send and the subsequent bind_send_id() so the dispatch thread cannot
    deliver an exception for a request whose send ID is not yet recorded.
    """

    def __init__(self) -> None:
        self.pending_lock = threading.Lock()
        self._by_request: dict[int, PendingRequest] = {}
        self._by_send: dict[int, PendingRequest] = {}

    def register(self, req: PendingRequest) -> None:
        with self.pending_lock:
            if req.request_id is not None:
                self._by_request[req.request_id] = req

    def bind_send_id(self, req: PendingRequest, send_id: int, _locked: bool = False) -> None:
        """Record the packet ID returned by GetLastSentPacketID.

        Pass _locked=True when pending_lock is already held by this thread.
        """
        if _locked:
            req.send_id = send_id
            self._by_send[send_id] = req
            return
        with self.pending_lock:
            req.send_id = send_id
            self._by_send[send_id] = req

    def resolve_data(self, request_id: int, value: Any) -> bool:
        with self.pending_lock:
            req = self._by_request.get(request_id)
        if req is None:
            return False
        req.value = value
        req.done.set()
        return True

    def resolve_exception(self, send_id: int, name: str) -> bool:
        with self.pending_lock:
            req = self._by_send.get(send_id)
        if req is None:
            return False
        req.exception = name
        req.done.set()
        return True

    def discard(self, req: PendingRequest) -> None:
        with self.pending_lock:
            if req.request_id is not None:
                self._by_request.pop(req.request_id, None)
            if req.send_id is not None:
                self._by_send.pop(req.send_id, None)


class SimConnectDispatcher(SimConnectMobiFlight):
    """SimConnectMobiFlight that owns the whole dispatch loop."""

    def __init__(self, auto_connect: bool = True, library_path: str | None = None) -> None:
        # Must exist before super().__init__ starts the dispatch thread.
        self.registry = RequestRegistry()
        self.facility_handlers: list = []
        if library_path:
            super().__init__(auto_connect, library_path)
        else:
            super().__init__(auto_connect)

    def my_dispatch_proc(self, pData, cbData, pContext):  # noqa: N802 (library name)
        dwID = pData.contents.dwID

        if dwID == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_SIMOBJECT_DATA:
            data = ctypes.cast(
                pData, ctypes.POINTER(SIMCONNECT_RECV_SIMOBJECT_DATA)
            ).contents
            self._on_simobject_data(data)
            return

        if dwID == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_EXCEPTION:
            exc = ctypes.cast(pData, ctypes.POINTER(SIMCONNECT_RECV_EXCEPTION)).contents
            try:
                name = SIMCONNECT_EXCEPTION(exc.dwException).name
            except ValueError:
                name = f"SIMCONNECT_EXCEPTION_{exc.dwException}"
            if not self.registry.resolve_exception(exc.dwSendID, name):
                log.debug("Unmatched SimConnect exception: %s (sendID=%s)", name, exc.dwSendID)
            return

        if dwID == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_SYSTEM_STATE:
            # SimConnect.handle_state_event print()s this straight to stdout.
            log.debug("SYSTEM_STATE message swallowed to protect the stdio stream")
            return

        if dwID in FACILITY_RECV_IDS:
            # The library's branch calls dump(), which print()s.  Phase 2
            # installs real handlers here.
            for handler in self.facility_handlers:
                handler(pData)
            return

        super().my_dispatch_proc(pData, cbData, pContext)

    def _on_simobject_data(self, data) -> None:
        """Decode one SIMOBJECT_DATA payload and resolve its request."""
        request_id = data.dwRequestID
        with self.registry.pending_lock:
            req = self.registry._by_request.get(request_id)
        if req is None:
            log.debug("SIMOBJECT_DATA for unknown request %s", request_id)
            return

        address = ctypes.addressof(data.dwData)
        if req.is_string:
            raw = ctypes.cast(address, ctypes.c_char_p).value or b""
            value: Any = raw.decode("ascii", errors="replace").rstrip("\x00").strip()
        else:
            value = ctypes.cast(address, ctypes.POINTER(ctypes.c_double)).contents.value
        self.registry.resolve_data(request_id, value)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/simconnect_mcp/dispatch.py tests/test_dispatch.py
git commit -m "feat: add SimConnectDispatcher owning the dispatch loop

Prevents the library's SYSTEM_STATE and facilities branches from
print()ing into the stdio JSON-RPC stream, and adds the request
registry that correlates SIMOBJECT_DATA and EXCEPTION messages by
dwSendID."
```

---

### Task 5: SimVarAccessor — numeric reads

The generic data-definition read path. This is what makes `unit` real and makes variables outside the library's 828-entry table readable.

**Files:**
- Create: `src/simconnect_mcp/simvar_access.py`
- Create: `tests/test_simvar_access.py`

**Interfaces:**
- Consumes: `dispatch.PendingRequest`, `dispatch.RequestRegistry`; `data.simvar_catalog.resolve_unit`, `is_string_var`
- Produces:
  - Exceptions `SimVarError`, `SimVarNotFoundError`, `SimVarNotSettableError`, `UnitMismatchError`, `SimVarTimeoutError`
  - `SimVarAccessor(sm)` with `read(name, unit=None, index=None, timeout=2.0)`
  - `SimVarAccessor.definition_id(name, unit, index, is_string) -> int` (cached)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_simvar_access.py`:

```python
import threading
from unittest.mock import MagicMock

import pytest

from simconnect_mcp.dispatch import RequestRegistry
from simconnect_mcp.simvar_access import (
    SimVarAccessor,
    SimVarNotFoundError,
    SimVarTimeoutError,
)


class FakeSM:
    """Minimal stand-in for SimConnectDispatcher.

    Resolves each read on a background thread, the way the real dispatch
    thread would, so the accessor's blocking wait is exercised.
    """

    def __init__(self, value=None, exception=None, respond=True):
        self.registry = RequestRegistry()
        self.hSimConnect = 1
        self.dll = MagicMock()
        self._next_id = 100
        self._value = value
        self._exception = exception
        self._respond = respond
        self.definitions = []
        self.dll.AddToDataDefinition.side_effect = self._record_definition
        self.dll.RequestDataOnSimObject.side_effect = self._respond_async
        self.dll.GetLastSentPacketID.side_effect = self._set_packet_id

    def _record_definition(self, handle, def_id, name, unit, datatype, epsilon, datum):
        self.definitions.append((def_id, name, unit, datatype))

    def _set_packet_id(self, handle, out):
        out.value = 555

    def _respond_async(self, handle, req_id, def_id, obj, period, flags, origin, interval, limit):
        if not self._respond:
            return
        def deliver():
            if self._exception is not None:
                self.registry.resolve_exception(555, self._exception)
            else:
                self.registry.resolve_data(req_id, self._value)
        threading.Timer(0.01, deliver).start()

    def new_def_id(self):
        self._next_id += 1
        return MagicMock(value=self._next_id)

    def new_request_id(self):
        self._next_id += 1
        return MagicMock(value=self._next_id)


def test_read_returns_the_dispatched_value():
    sm = FakeSM(value=35000.0)
    accessor = SimVarAccessor(sm)
    assert accessor.read("PLANE_ALTITUDE") == 35000.0


def test_read_passes_the_explicit_unit_to_the_data_definition():
    """The whole point of the new layer: the requested unit reaches SimConnect."""
    sm = FakeSM(value=10668.0)
    accessor = SimVarAccessor(sm)
    accessor.read("PLANE_ALTITUDE", unit="meters")

    _, name, unit, _ = sm.definitions[0]
    assert name == b"PLANE ALTITUDE"
    assert unit == b"meters"


def test_read_converts_underscores_to_spaces_for_simconnect():
    sm = FakeSM(value=1.0)
    SimVarAccessor(sm).read("AIRSPEED_INDICATED")
    assert sm.definitions[0][1] == b"AIRSPEED INDICATED"


def test_read_appends_index_to_the_variable_name():
    sm = FakeSM(value=1.0)
    SimVarAccessor(sm).read("ENG_N1_RPM", index=2)
    assert sm.definitions[0][1] == b"ENG N1 RPM:2"


def test_index_zero_is_honoured_not_dropped():
    sm = FakeSM(value=1.0)
    SimVarAccessor(sm).read("GENERAL_ENG_THROTTLE_LEVER_POSITION", index=0)
    assert sm.definitions[0][1].endswith(b":0")


def test_definitions_are_cached_per_name_unit_index():
    sm = FakeSM(value=1.0)
    accessor = SimVarAccessor(sm)
    accessor.read("PLANE_ALTITUDE", unit="feet")
    accessor.read("PLANE_ALTITUDE", unit="feet")
    assert len(sm.definitions) == 1, "definition IDs are a finite resource"


def test_different_units_get_different_definitions():
    sm = FakeSM(value=1.0)
    accessor = SimVarAccessor(sm)
    accessor.read("PLANE_ALTITUDE", unit="feet")
    accessor.read("PLANE_ALTITUDE", unit="meters")
    assert len(sm.definitions) == 2


def test_unrecognised_name_exception_raises_not_found():
    sm = FakeSM(exception="SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED")
    with pytest.raises(SimVarNotFoundError) as excinfo:
        SimVarAccessor(sm).read("NOT_A_REAL_VAR")
    assert "NOT_A_REAL_VAR" in str(excinfo.value)


def test_silent_sim_raises_timeout():
    sm = FakeSM(respond=False)
    with pytest.raises(SimVarTimeoutError):
        SimVarAccessor(sm).read("PLANE_ALTITUDE", timeout=0.05)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_simvar_access.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simconnect_mcp.simvar_access'`

- [ ] **Step 3: Create the module**

Create `src/simconnect_mcp/simvar_access.py`:

```python
"""Generic SimVar access via SimConnect data definitions.

Replaces SimConnect.AircraftRequests, which holds a hardcoded table of 828
variables each bound to one fixed unit, and signals failure by returning
None/False.  That design makes four things impossible: honouring a requested
unit, reading variables outside the table, reading string variables, and
telling a failed write from a successful one.

This layer builds definitions directly -- the same native pattern already
used by SimConnectManager.set_lvar -- so all four work.
"""
from __future__ import annotations

import ctypes
import logging
from collections import OrderedDict
from ctypes.wintypes import DWORD

from SimConnect.Constants import SIMCONNECT_OBJECT_ID_USER, SIMCONNECT_UNUSED
from SimConnect.Enum import (
    SIMCONNECT_DATA_SET_FLAG,
    SIMCONNECT_DATATYPE,
    SIMCONNECT_PERIOD,
)

from simconnect_mcp.data.simvar_catalog import is_string_var, resolve_unit
from simconnect_mcp.dispatch import PendingRequest

log = logging.getLogger(__name__)

STRING_SIZE = 256
DEFINITION_CACHE_SIZE = 256
DEFAULT_TIMEOUT = 2.0


class SimVarError(Exception):
    """Base class for SimVar access failures."""


class SimVarNotFoundError(SimVarError):
    """SimConnect did not recognise the variable name."""


class SimVarNotSettableError(SimVarError):
    """The variable exists but cannot be written."""


class UnitMismatchError(SimVarError):
    """The requested unit is not valid for this variable."""


class SimVarTimeoutError(SimVarError):
    """No data and no exception arrived before the timeout."""


# SimConnect reports the same exception codes for different underlying
# causes depending on the operation.  A bad unit on a read and a write to a
# read-only variable both surface as DATA_ERROR, so the mapping is chosen by
# operation rather than from one global table.  (There is no
# SIMCONNECT_EXCEPTION_ILLEGAL_OPERATION -- verify any code you add against
# SimConnect.Enum.SIMCONNECT_EXCEPTION before using it.)
_NOT_FOUND = frozenset({"SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED"})

# Ambiguous codes: on a read these mean the unit or datatype was wrong; on a
# write they usually mean the variable is not settable.
_DATA_CODES = frozenset({
    "SIMCONNECT_EXCEPTION_DATA_ERROR",
    "SIMCONNECT_EXCEPTION_INVALID_DATA_TYPE",
    "SIMCONNECT_EXCEPTION_INVALID_DATA_SIZE",
    "SIMCONNECT_EXCEPTION_DEFINITION_ERROR",
    "SIMCONNECT_EXCEPTION_OUT_OF_BOUNDS",
})


def simconnect_name(name: str, index: int | None = None) -> bytes:
    """Convert an MCP-style SimVar name to the form SimConnect expects.

    'PLANE_ALTITUDE'      -> b'PLANE ALTITUDE'
    'ENG_N1_RPM', index=2 -> b'ENG N1 RPM:2'

    `index` is compared against None, not truthiness: index 0 is valid.
    """
    base = name.strip().upper().split(":", 1)[0].replace("_", " ")
    if index is not None:
        base = f"{base}:{index}"
    return base.encode("ascii")


class SimVarAccessor:
    """Reads and writes SimVars through SimConnect data definitions."""

    def __init__(self, sm) -> None:
        self._sm = sm
        self._definitions: OrderedDict[tuple[str, str, int | None, bool], int] = OrderedDict()

    def definition_id(self, name: str, unit: str, index: int | None, is_string: bool) -> int:
        """Definition ID for this (name, unit, index), creating it once.

        Definition IDs are a finite SimConnect resource and new_def_id()
        rebuilds an Enum on every call, so they must not be created per read.
        Eviction drops only our mapping; SimConnect definitions are not
        reclaimable, so the bound caps growth rather than recycling IDs.
        """
        key = (name.strip().upper(), unit, index, is_string)
        cached = self._definitions.get(key)
        if cached is not None:
            self._definitions.move_to_end(key)
            return cached

        def_id = self._sm.new_def_id().value
        datatype = (
            SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_STRING256
            if is_string
            else SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_FLOAT64
        )
        self._sm.dll.AddToDataDefinition(
            self._sm.hSimConnect,
            def_id,
            simconnect_name(name, index),
            unit.encode("ascii"),
            datatype,
            ctypes.c_float(0.0),
            SIMCONNECT_UNUSED,
        )
        self._definitions[key] = def_id
        if len(self._definitions) > DEFINITION_CACHE_SIZE:
            self._definitions.popitem(last=False)
        return def_id

    def _last_packet_id(self) -> int:
        """Packet ID of the call just sent, for exception correlation."""
        out = DWORD(0)
        self._sm.dll.GetLastSentPacketID(self._sm.hSimConnect, out)
        return out.value

    def _raise_for(
        self, exception_name: str, name: str, unit: str, writing: bool = False
    ) -> None:
        """Translate a SimConnect exception into a typed error."""
        if exception_name in _NOT_FOUND:
            raise SimVarNotFoundError(f"SimConnect does not recognise SimVar '{name}'")
        if exception_name in _DATA_CODES:
            if writing:
                raise SimVarNotSettableError(
                    f"SimConnect rejected the write to '{name}'. It is most likely "
                    f"read-only; the unit '{unit}' or the value may also be invalid "
                    f"({exception_name})."
                )
            raise UnitMismatchError(
                f"Unit '{unit}' is not valid for SimVar '{name}' ({exception_name})"
            )
        raise SimVarError(f"SimConnect rejected '{name}': {exception_name}")

    def read(
        self,
        name: str,
        unit: str | None = None,
        index: int | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """Read one SimVar. Returns float, or str for string variables.

        Raises SimVarNotFoundError, UnitMismatchError, SimVarTimeoutError.
        """
        as_string = is_string_var(name)
        resolved_unit = "string" if as_string else resolve_unit(name, unit)
        def_id = self.definition_id(name, resolved_unit, index, as_string)

        req_id = self._sm.new_request_id().value
        pending = PendingRequest(request_id=req_id, is_string=as_string)
        self._sm.registry.register(pending)

        # Hold the lock across send + bind so the dispatch thread cannot
        # deliver an exception before we know which packet it belongs to.
        with self._sm.registry.pending_lock:
            self._sm.dll.RequestDataOnSimObject(
                self._sm.hSimConnect,
                req_id,
                def_id,
                SIMCONNECT_OBJECT_ID_USER,
                SIMCONNECT_PERIOD.SIMCONNECT_PERIOD_ONCE,
                0,
                0,
                0,
                0,
            )
            self._sm.registry.bind_send_id(pending, self._last_packet_id(), _locked=True)

        try:
            if not pending.done.wait(timeout):
                raise SimVarTimeoutError(
                    f"No response for SimVar '{name}' within {timeout}s. "
                    "The sim may be paused, loading, or not running."
                )
            if pending.exception is not None:
                self._raise_for(pending.exception, name, resolved_unit)
            return pending.value
        finally:
            self._sm.registry.discard(pending)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_simvar_access.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Run ruff**

Run: `uv run ruff check src/simconnect_mcp/simvar_access.py`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add src/simconnect_mcp/simvar_access.py tests/test_simvar_access.py
git commit -m "feat: add SimVarAccessor with generic data-definition reads

Honours the requested unit, reaches variables outside the library's
828-entry table, and raises typed errors instead of returning None."
```

---

### Task 6: SimVarAccessor — writes with typed failures

`AircraftRequests.set()` returns `False` for unknown or non-settable variables; `set_simvar` discards that and reports `status: ok`. A write must either succeed or raise.

Writes get no `SIMOBJECT_DATA` reply, so success is "no exception arrived within the grace window". The window is short (150 ms) because it only needs to outlast the sim's own round trip.

**Files:**
- Modify: `src/simconnect_mcp/simvar_access.py` (add `write`)
- Modify: `tests/test_simvar_access.py` (add write tests)

**Interfaces:**
- Consumes: everything from Task 5
- Produces: `SimVarAccessor.write(name, value, unit=None, index=None, grace=0.15) -> None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_simvar_access.py`:

```python
from simconnect_mcp.simvar_access import SimVarNotSettableError


class FakeWriteSM(FakeSM):
    """FakeSM whose SetDataOnSimObject optionally raises a SimConnect exception."""

    def __init__(self, exception=None):
        super().__init__(respond=False)
        self._write_exception = exception
        self.writes = []
        self.dll.SetDataOnSimObject.side_effect = self._on_write

    def _on_write(self, handle, def_id, obj, flags, count, size, data):
        self.writes.append((def_id, size))
        if self._write_exception is not None:
            threading.Timer(
                0.01, lambda: self.registry.resolve_exception(555, self._write_exception)
            ).start()


def test_write_sends_the_value_and_returns_none():
    sm = FakeWriteSM()
    assert SimVarAccessor(sm).write("AUTOPILOT_ALTITUDE_LOCK_VAR", 12000.0, grace=0.05) is None
    assert len(sm.writes) == 1


def test_write_to_non_settable_var_raises():
    """The bug this replaces: aq.set() returned False and the tool said ok."""
    sm = FakeWriteSM(exception="SIMCONNECT_EXCEPTION_DATA_ERROR")
    with pytest.raises(SimVarNotSettableError):
        SimVarAccessor(sm).write("AIRSPEED_INDICATED", 250.0, grace=0.2)


def test_write_to_unknown_var_raises_not_found():
    sm = FakeWriteSM(exception="SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED")
    with pytest.raises(SimVarNotFoundError):
        SimVarAccessor(sm).write("NOT_A_REAL_VAR", 1.0, grace=0.2)


def test_write_honours_index_zero():
    sm = FakeWriteSM()
    SimVarAccessor(sm).write("GENERAL_ENG_THROTTLE_LEVER_POSITION", 50.0, index=0, grace=0.05)
    assert sm.definitions[0][1].endswith(b":0")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_simvar_access.py -k write -v`
Expected: FAIL with `AttributeError: 'SimVarAccessor' object has no attribute 'write'`

- [ ] **Step 3: Implement write**

Add to `SimVarAccessor` in `src/simconnect_mcp/simvar_access.py`:

```python
    def write(
        self,
        name: str,
        value: float,
        unit: str | None = None,
        index: int | None = None,
        grace: float = 0.15,
    ) -> None:
        """Write one numeric SimVar.

        SimConnect sends no acknowledgement for a successful write, so success
        is defined as "no exception arrived within `grace`".  This is the only
        way to distinguish a real write from a rejected one -- AircraftRequests
        simply returned False and callers ignored it.
        """
        resolved_unit = resolve_unit(name, unit)
        def_id = self.definition_id(name, resolved_unit, index, False)

        pending = PendingRequest(request_id=None)
        self._sm.registry.register(pending)

        payload = (ctypes.c_double * 1)(float(value))
        with self._sm.registry.pending_lock:
            self._sm.dll.SetDataOnSimObject(
                self._sm.hSimConnect,
                def_id,
                SIMCONNECT_OBJECT_ID_USER,
                SIMCONNECT_DATA_SET_FLAG.SIMCONNECT_DATA_SET_FLAG_DEFAULT,
                0,
                ctypes.sizeof(ctypes.c_double),
                ctypes.cast(payload, ctypes.c_void_p),
            )
            self._sm.registry.bind_send_id(pending, self._last_packet_id(), _locked=True)

        try:
            # An exception, if any, arrives within one dispatch round trip.
            if pending.done.wait(grace) and pending.exception is not None:
                self._raise_for(pending.exception, name, resolved_unit, writing=True)
        finally:
            self._sm.registry.discard(pending)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_simvar_access.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/simvar_access.py tests/test_simvar_access.py
git commit -m "feat: add SimVarAccessor.write with typed failure detection

Correlates SimConnect exceptions to the write that caused them, so a
rejected write raises instead of being reported as success."
```

---

### Task 7: SimVarAccessor — string variables

`TITLE`, `ATC_ID` and `ATC_TYPE` are string SimVars. Reading them through the old path yielded `bytes`, which is not JSON-serialisable and leaked into tool responses. The accessor already routes them to `STRING256` (Task 5); this task adds the tests that pin the behaviour and a `read_many` helper that bulk callers use.

**Files:**
- Modify: `src/simconnect_mcp/simvar_access.py` (add `read_many`)
- Modify: `tests/test_simvar_access.py`

**Interfaces:**
- Consumes: everything from Tasks 5–6
- Produces: `SimVarAccessor.read_many(requests: list[tuple[str, str | None, int | None]]) -> dict[str, dict]` where each value is `{"value": ...}` or `{"error": "...", "error_type": "..."}`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_simvar_access.py`:

```python
from simconnect_mcp.simvar_access import STRING_SIZE, simconnect_name


def test_string_var_uses_string256_datatype():
    from SimConnect.Enum import SIMCONNECT_DATATYPE

    sm = FakeSM(value="Boeing 747-8i")
    SimVarAccessor(sm).read("TITLE")
    _, _, _, datatype = sm.definitions[0]
    assert datatype == SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_STRING256


def test_string_var_returns_str_not_bytes():
    sm = FakeSM(value="Boeing 747-8i")
    result = SimVarAccessor(sm).read("TITLE")
    assert result == "Boeing 747-8i"
    assert isinstance(result, str)


def test_read_many_returns_a_result_per_variable():
    sm = FakeSM(value=42.0)
    results = SimVarAccessor(sm).read_many(
        [("PLANE_ALTITUDE", None, None), ("AIRSPEED_INDICATED", "knots", None)]
    )
    assert results["PLANE_ALTITUDE"]["value"] == 42.0
    assert results["AIRSPEED_INDICATED"]["value"] == 42.0


def test_read_many_isolates_a_failing_variable():
    """One bad name must not abort the whole batch."""
    sm = FakeSM(respond=False)
    results = SimVarAccessor(sm).read_many([("NOT_A_REAL_VAR", None, None)], timeout=0.05)
    assert "error" in results["NOT_A_REAL_VAR"]
    assert results["NOT_A_REAL_VAR"]["error_type"] == "SimVarTimeoutError"


def test_read_many_keys_indexed_vars_distinctly():
    sm = FakeSM(value=1.0)
    results = SimVarAccessor(sm).read_many(
        [("ENG_N1_RPM", None, 1), ("ENG_N1_RPM", None, 2)]
    )
    assert set(results) == {"ENG_N1_RPM:1", "ENG_N1_RPM:2"}


def test_simconnect_name_helper_is_index_zero_safe():
    assert simconnect_name("ENG_N1_RPM", 0) == b"ENG N1 RPM:0"
    assert simconnect_name("ENG_N1_RPM", None) == b"ENG N1 RPM"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_simvar_access.py -k "string or read_many or helper" -v`
Expected: FAIL — `read_many` does not exist

- [ ] **Step 3: Implement read_many**

Add to `SimVarAccessor`:

```python
    def read_many(
        self,
        requests: list[tuple[str, str | None, int | None]],
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict[str, dict]:
        """Read several SimVars, isolating failures.

        Keys are `NAME` or `NAME:index`, so indexed variables stay distinct.
        A failure on one variable never aborts the batch.
        """
        results: dict[str, dict] = {}
        for name, unit, index in requests:
            key = name if index is None else f"{name}:{index}"
            try:
                results[key] = {"value": self.read(name, unit, index, timeout)}
            except SimVarError as e:
                results[key] = {"error": str(e), "error_type": type(e).__name__}
        return results
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_simvar_access.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/simvar_access.py tests/test_simvar_access.py
git commit -m "feat: string SimVar support and batched reads

TITLE/ATC_ID/ATC_TYPE read via STRING256 and decode to str, so bytes
no longer reach the JSON layer. read_many isolates per-variable failures."
```

---

### Task 8: Wire the dispatcher and accessor into SimConnectManager

**Files:**
- Modify: `src/simconnect_mcp/connection.py:64-139` (`connect`), `:141-164` (`disconnect`), `:37-50` (`__init__`)
- Modify: `tests/conftest.py` (expose an accessor on the mock manager)
- Modify: `tests/test_connection.py`

**Interfaces:**
- Consumes: `dispatch.SimConnectDispatcher`, `simvar_access.SimVarAccessor`
- Produces: `SimConnectManager.accessor: SimVarAccessor | None`, set on connect and cleared on disconnect. `manager.aq` stays available for `AircraftEvents`-adjacent code until Phase 1 finishes migrating callers.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_connection.py`:

```python
def test_manager_exposes_an_accessor_when_connected(mock_simconnect):
    manager = mock_simconnect["manager"]
    assert manager.accessor is not None


def test_disconnect_clears_the_accessor(mock_simconnect):
    manager = mock_simconnect["manager"]
    manager.disconnect()
    assert manager.accessor is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_connection.py -k accessor -v`
Expected: FAIL with `AttributeError: 'SimConnectManager' object has no attribute 'accessor'`

- [ ] **Step 3: Add the attribute and construct it on connect**

In `src/simconnect_mcp/connection.py` `__init__`, after `self.mobiflight = None`:

```python
        self.accessor = None  # SimVarAccessor, created on connect
```

In `connect()`, replace the `SimConnectMobiFlight` block with the dispatcher:

```python
            # Prefer SimConnectDispatcher -- owns the dispatch loop, which
            # both enables SimVar exception correlation and keeps the
            # library's print()ing branches out of the stdio stream.
            try:
                from simconnect_mcp.dispatch import SimConnectDispatcher
                self.sm = SimConnectDispatcher()
                logger.info("Using SimConnectDispatcher (WASM client-data enabled)")
            except Exception as e:
                logger.info("SimConnectDispatcher unavailable (%s), falling back", e)
                from SimConnect import SimConnect
                self.sm = SimConnect()
```

Then after `self.ae = AircraftEvents(self.sm)`:

```python
            # Generic SimVar access. Requires the dispatcher's request
            # registry, so it is only available on the dispatcher path.
            if hasattr(self.sm, "registry"):
                from simconnect_mcp.simvar_access import SimVarAccessor
                self.accessor = SimVarAccessor(self.sm)
            else:
                self.accessor = None
                logger.warning(
                    "Plain SimConnect fallback: unit-aware SimVar access unavailable"
                )
```

- [ ] **Step 4: Fix disconnect ordering and clear the accessor**

The current `disconnect()` sets `self.sm = None` before calling `pmdg.cleanup()`, which unregisters handlers on `sm`. Reorder so cleanup runs first. Replace the `finally` block:

```python
        finally:
            # Cleanup unregisters handlers on self.sm, so it must run first.
            if self.pmdg is not None:
                self.pmdg.cleanup()
                self.pmdg = None
            if self.pmdg_ng3 is not None:
                self.pmdg_ng3.cleanup()
                self.pmdg_ng3 = None
            self.sm = None
            self.aq = None
            self.ae = None
            self.fr = None
            self.mobiflight = None
            self.accessor = None
            self._mobiflight_available = False
            self._state = ConnectionState.DISCONNECTED
```

- [ ] **Step 5: Give the test mock an accessor**

In `tests/conftest.py`, inside the `with patch.dict(...)` block, after `manager.ae = mock_ae`:

```python
        mock_accessor = MagicMock()
        mock_accessor.read.side_effect = lambda name, unit=None, index=None, timeout=2.0: (
            simvar_values.get(name.split(":")[0])
        )
        mock_accessor.read_many.side_effect = lambda reqs, timeout=2.0: {
            (n if i is None else f"{n}:{i}"): {"value": simvar_values.get(n.split(":")[0])}
            for n, u, i in reqs
        }
        manager.accessor = mock_accessor
```

and add `"accessor": mock_accessor,` to the yielded dict.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/simconnect_mcp/connection.py tests/conftest.py tests/test_connection.py
git commit -m "feat: wire SimConnectDispatcher and SimVarAccessor into the manager

Also fixes disconnect ordering -- PMDG cleanup unregisters handlers on
sm, so it must run before sm is cleared."
```

---

### Task 9: Fix `get_simvar`

Today the unreadable-variable branch does `from SimConnect.Constants import DATATYPE_FLOAT64`, a constant that does not exist. It raises `ImportError`, so callers receive `{"status": "error", "error": "UNEXPECTED", "message": "cannot import name 'DATATYPE_FLOAT64'..."}` and the `SIMVAR_NOT_FOUND` branch with its suggestions below it is unreachable.

**Files:**
- Modify: `src/simconnect_mcp/tools/simvars.py` (`get_simvar`)
- Modify: `tests/test_simvars.py`

**Interfaces:**
- Consumes: `manager.accessor`, `data.simvar_catalog.suggest_names`
- Produces: `get_simvar` returning `{"status": "ok", "name", "value", "unit", "index"}` where `unit` is the unit **actually used**, or `SIMVAR_NOT_FOUND` / `UNIT_MISMATCH` / `SIM_TIMEOUT` errors.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_simvars.py`:

```python
import pytest

from simconnect_mcp.simvar_access import SimVarNotFoundError, SimVarTimeoutError
from simconnect_mcp.tools.simvars import get_simvar


async def test_get_simvar_reports_the_unit_actually_used(mock_simconnect):
    result = await get_simvar("PLANE_ALTITUDE", unit="meters")
    assert result["status"] == "ok"
    assert result["unit"] == "meters", "the old code echoed 'default' and ignored the unit"


async def test_get_simvar_passes_unit_through_to_the_accessor(mock_simconnect):
    await get_simvar("PLANE_ALTITUDE", unit="meters")
    mock_simconnect["accessor"].read.assert_called_once()
    assert mock_simconnect["accessor"].read.call_args.kwargs["unit"] == "meters"


async def test_unknown_simvar_returns_not_found_with_suggestions(mock_simconnect):
    """Previously unreachable: the branch above it raised ImportError."""
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    result = await get_simvar("PLANE_ALTITUD")
    assert result["error"] == "SIMVAR_NOT_FOUND"
    assert "PLANE_ALTITUDE" in result["suggestions"]


async def test_timeout_is_reported_as_its_own_error(mock_simconnect):
    mock_simconnect["accessor"].read.side_effect = SimVarTimeoutError("no response")
    result = await get_simvar("PLANE_ALTITUDE")
    assert result["error"] == "SIM_TIMEOUT"
    assert "suggestion" in result


async def test_index_zero_is_passed_through(mock_simconnect):
    await get_simvar("GENERAL_ENG_THROTTLE_LEVER_POSITION", index=0)
    assert mock_simconnect["accessor"].read.call_args.kwargs["index"] == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_simvars.py -v`
Expected: FAIL — unit is `"default"`, and the not-found test gets `UNEXPECTED`

- [ ] **Step 3: Rewrite get_simvar**

Replace `get_simvar` in `src/simconnect_mcp/tools/simvars.py`:

```python
@handle_simconnect_errors
@require_connection
async def get_simvar(name: str, unit: str | None = None, index: int | None = None) -> dict:
    """Read a SimVar value by name.

    Args:
        name: SimVar name (e.g., 'PLANE_LATITUDE', 'AIRSPEED_INDICATED')
        unit: Unit to read in (e.g., 'feet', 'meters', 'knots'). Defaults to
              the catalog unit for this variable, then to 'number'.
        index: Index for indexed SimVars (e.g., engine number). Index 0 is valid.

    Returns:
        Dict with the variable name, value, and the unit actually used.
    """
    manager = SimConnectManager()
    resolved_unit = resolve_unit(name, unit)

    try:
        value = await manager.run_sync(
            lambda: manager.accessor.read(name, unit=unit, index=index)
        )
    except SimVarNotFoundError:
        result: dict[str, Any] = {
            "status": "error",
            "error": "SIMVAR_NOT_FOUND",
            "message": f"SimConnect does not recognise SimVar '{name}'",
            "suggestion": "Use search_simvars to find the correct name.",
        }
        suggestions = suggest_names(name)
        if suggestions:
            result["suggestions"] = suggestions
        return result
    except UnitMismatchError as e:
        return {
            "status": "error",
            "error": "UNIT_MISMATCH",
            "message": str(e),
            "suggestion": (
                f"Check the units for '{name}' with search_simvars, "
                "or omit the unit argument to use the catalog default."
            ),
        }
    except SimVarTimeoutError as e:
        return {
            "status": "error",
            "error": "SIM_TIMEOUT",
            "message": str(e),
            "suggestion": "The sim may be paused or loading. Try again shortly.",
        }

    return {
        "status": "ok",
        "name": name,
        "value": value,
        "unit": resolved_unit,
        "index": index,
    }
```

Add these imports at the top of the file:

```python
from simconnect_mcp.data.simvar_catalog import resolve_unit, suggest_names
from simconnect_mcp.simvar_access import (
    SimVarNotFoundError,
    SimVarTimeoutError,
    UnitMismatchError,
)
```

Note: `run_sync` takes `(fn, *args)`. A zero-argument lambda works because the manager passes no extra args.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_simvars.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/tools/simvars.py tests/test_simvars.py
git commit -m "fix: get_simvar honours unit and reports real errors

The old fallback imported a nonexistent constant, so every unreadable
variable surfaced as an ImportError and the SIMVAR_NOT_FOUND branch
below it was dead code."
```

---

### Task 10: Fix `set_simvar`

`AircraftRequests.set()` returns `False` for unknown or non-settable variables. The return value is discarded and the tool reports `status: ok` — the most damaging bug in the server, because an agent believes a write landed when it did not.

**Files:**
- Modify: `src/simconnect_mcp/tools/simvars.py` (`set_simvar`)
- Modify: `tests/test_simvars.py`

**Interfaces:**
- Consumes: `manager.accessor.write`
- Produces: `set_simvar` returning `{"status": "ok", "name", "value_set", "unit", "index"}` or a typed error.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_simvars.py`:

```python
from simconnect_mcp.simvar_access import SimVarNotSettableError
from simconnect_mcp.tools.simvars import set_simvar


async def test_set_simvar_reports_failure_instead_of_faking_success(mock_simconnect):
    """Regression: the old code discarded aq.set()'s False and returned ok."""
    mock_simconnect["accessor"].write.side_effect = SimVarNotSettableError(
        "SimVar 'AIRSPEED_INDICATED' cannot be written"
    )
    result = await set_simvar("AIRSPEED_INDICATED", 250.0)
    assert result["status"] == "error"
    assert result["error"] == "SIMVAR_NOT_SETTABLE"


async def test_set_simvar_success_reports_the_unit_used(mock_simconnect):
    result = await set_simvar("AUTOPILOT_ALTITUDE_LOCK_VAR", 12000.0, unit="feet")
    assert result["status"] == "ok"
    assert result["unit"] == "feet"
    assert result["value_set"] == 12000.0


async def test_set_simvar_unknown_name_returns_not_found(mock_simconnect):
    mock_simconnect["accessor"].write.side_effect = SimVarNotFoundError("nope")
    result = await set_simvar("NOT_A_REAL_VAR", 1.0)
    assert result["error"] == "SIMVAR_NOT_FOUND"


async def test_set_simvar_passes_index_zero(mock_simconnect):
    await set_simvar("GENERAL_ENG_THROTTLE_LEVER_POSITION", 50.0, index=0)
    assert mock_simconnect["accessor"].write.call_args.kwargs["index"] == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_simvars.py -k set_simvar -v`
Expected: FAIL — the first test gets `status: ok`

- [ ] **Step 3: Rewrite set_simvar**

```python
@handle_simconnect_errors
@require_connection
async def set_simvar(
    name: str, value: float, unit: str | None = None, index: int | None = None
) -> dict:
    """Write a value to a settable SimVar.

    Args:
        name: SimVar name (must be settable)
        value: Value to write
        unit: Unit the value is expressed in. Defaults to the catalog unit.
        index: Index for indexed SimVars. Index 0 is valid.

    Returns:
        Confirmation dict, or an error if the sim rejected the write.
    """
    manager = SimConnectManager()
    resolved_unit = resolve_unit(name, unit)

    try:
        await manager.run_sync(
            lambda: manager.accessor.write(name, value, unit=unit, index=index)
        )
    except SimVarNotSettableError as e:
        return {
            "status": "error",
            "error": "SIMVAR_NOT_SETTABLE",
            "message": str(e),
            "suggestion": (
                f"'{name}' is read-only. Use search_simvars to check the "
                "'settable' flag, or trigger_event for an equivalent control."
            ),
        }
    except SimVarNotFoundError:
        result: dict[str, Any] = {
            "status": "error",
            "error": "SIMVAR_NOT_FOUND",
            "message": f"SimConnect does not recognise SimVar '{name}'",
            "suggestion": "Use search_simvars to find the correct name.",
        }
        suggestions = suggest_names(name)
        if suggestions:
            result["suggestions"] = suggestions
        return result
    except UnitMismatchError as e:
        return {
            "status": "error",
            "error": "UNIT_MISMATCH",
            "message": str(e),
            "suggestion": f"Check the valid units for '{name}' with search_simvars.",
        }

    return {
        "status": "ok",
        "name": name,
        "value_set": value,
        "unit": resolved_unit,
        "index": index,
    }
```

Add `SimVarNotSettableError` to the `simvar_access` import list.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_simvars.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/tools/simvars.py tests/test_simvars.py
git commit -m "fix: set_simvar reports rejected writes instead of faking success

aq.set() returns False for unknown and non-settable vars; that return
value was discarded and the tool always reported status: ok."
```

---

### Task 11: Fix `get_simvar_bulk` and `watch_simvar`

Both use `f"{name}:{idx}" if idx else name`, so `index=0` is silently dropped — engine 0 and throttle 0 read the un-indexed variable instead. Both also ignore `unit`.

**Files:**
- Modify: `src/simconnect_mcp/tools/simvars.py` (`get_simvar_bulk`, `watch_simvar`)
- Modify: `tests/test_simvars.py`

**Interfaces:**
- Consumes: `manager.accessor.read_many`, `manager.accessor.read`
- Produces: `get_simvar_bulk` returning `{"status": "ok", "variables": {key: {"value"|"error", ...}}}`; `watch_simvar` returning samples with `unit` set to the resolved unit.

- [ ] **Step 1: Write the failing tests**

```python
from simconnect_mcp.tools.simvars import get_simvar_bulk, watch_simvar


async def test_bulk_read_honours_index_zero(mock_simconnect):
    """Regression: `if idx` dropped index 0, silently reading the wrong var."""
    await get_simvar_bulk([{"name": "GENERAL_ENG_THROTTLE_LEVER_POSITION", "index": 0}])
    requests = mock_simconnect["accessor"].read_many.call_args.args[0]
    assert requests == [("GENERAL_ENG_THROTTLE_LEVER_POSITION", None, 0)]


async def test_bulk_read_passes_units(mock_simconnect):
    await get_simvar_bulk([{"name": "PLANE_ALTITUDE", "unit": "meters"}])
    requests = mock_simconnect["accessor"].read_many.call_args.args[0]
    assert requests == [("PLANE_ALTITUDE", "meters", None)]


async def test_bulk_read_returns_a_value_per_variable(mock_simconnect):
    result = await get_simvar_bulk([{"name": "PLANE_ALTITUDE"}, {"name": "AIRSPEED_INDICATED"}])
    assert result["status"] == "ok"
    assert result["variables"]["PLANE_ALTITUDE"]["value"] == 35000.0


async def test_watch_simvar_honours_index_zero(mock_simconnect):
    await watch_simvar("ENG_N1_RPM", index=0, interval_ms=50, duration_s=1)
    assert mock_simconnect["accessor"].read.call_args.kwargs["index"] == 0


async def test_watch_simvar_reports_resolved_unit(mock_simconnect):
    result = await watch_simvar("PLANE_ALTITUDE", unit="meters", interval_ms=50, duration_s=1)
    assert result["unit"] == "meters"
    assert len(result["samples"]) >= 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_simvars.py -k "bulk or watch" -v`
Expected: FAIL

- [ ] **Step 3: Rewrite both tools**

```python
@handle_simconnect_errors
@require_connection
async def get_simvar_bulk(variables: list[dict]) -> dict:
    """Read multiple SimVars in one call.

    Args:
        variables: List of dicts with 'name' and optional 'unit' and 'index'.
                   Example: [{"name": "PLANE_LATITUDE"},
                             {"name": "ENG_N1_RPM", "index": 1, "unit": "percent"}]

    Returns:
        Dict keyed by 'NAME' or 'NAME:index', each holding a value or an error.
        A failure on one variable does not abort the others.
    """
    manager = SimConnectManager()
    requests = [
        (var["name"], var.get("unit"), var.get("index"))  # index 0 must survive
        for var in variables
    ]
    results = await manager.run_sync(lambda: manager.accessor.read_many(requests))
    return {"status": "ok", "count": len(results), "variables": results}


@handle_simconnect_errors
@require_connection
async def watch_simvar(
    name: str,
    unit: str | None = None,
    index: int | None = None,
    interval_ms: int = 500,
    duration_s: int = 5,
) -> dict:
    """Sample a SimVar over time, returning a time series for debugging.

    Args:
        name: SimVar name to watch
        unit: Unit to read in. Defaults to the catalog unit.
        index: Index for indexed SimVars. Index 0 is valid.
        interval_ms: Polling interval in milliseconds (minimum 50)
        duration_s: Total duration in seconds (maximum 30)

    Returns:
        Time series of values with elapsed timestamps.
    """
    duration_s = min(duration_s, 30)
    interval_s = max(interval_ms / 1000.0, 0.05)
    manager = SimConnectManager()
    resolved_unit = resolve_unit(name, unit)

    samples: list[dict] = []
    errors = 0
    start = time.monotonic()

    while (time.monotonic() - start) < duration_s:
        try:
            value = await manager.run_sync(
                lambda: manager.accessor.read(name, unit=unit, index=index)
            )
            samples.append({"t": round(time.monotonic() - start, 3), "value": value})
        except SimVarError as e:
            errors += 1
            if not samples and errors == 1:
                # Fail fast on a name that will never work.
                return {
                    "status": "error",
                    "error": "SIMVAR_NOT_READABLE",
                    "message": str(e),
                    "suggestion": "Check the name with search_simvars.",
                }
        await asyncio.sleep(interval_s)

    return {
        "status": "ok",
        "name": name,
        "unit": resolved_unit,
        "index": index,
        "samples": samples,
        "sample_count": len(samples),
        "error_count": errors,
        "duration_s": duration_s,
        "interval_ms": interval_ms,
    }
```

Add `SimVarError` to the `simvar_access` import list.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_simvars.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/tools/simvars.py tests/test_simvars.py
git commit -m "fix: index 0 and units in get_simvar_bulk and watch_simvar

Both used `if idx`, so index 0 was dropped and the un-indexed variable
was read instead."
```

---

### Task 12: Fix `search_events`

`_load_event_catalog` does `from SimConnect.RequestList import EventList`. `EventList` is a module (`SimConnect/EventList.py`), not a name in `RequestList`, so the import raises and the bare `except` silently falls back to 50 hardcoded events. The installed library exposes **994 events across 24 categories** through `AircraftEvents`, and `trigger_event` can already fire all of them — so agents can trigger events they cannot discover.

The same function has a second latent bug: it falls back only when the import *raises*, not when parsing yields an empty catalog.

**Files:**
- Modify: `src/simconnect_mcp/tools/events.py:16-45` (`_load_event_catalog`)
- Modify: `tests/test_events.py`

**Interfaces:**
- Consumes: `SimConnect.EventList.AircraftEvents`
- Produces: `_load_event_catalog()` returning ≥ 900 events across ≥ 20 categories.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_events.py`:

```python
def test_event_catalog_loads_the_real_library_catalog():
    """The import was from the wrong module, silently degrading to 50 events."""
    from simconnect_mcp.tools import events

    events._EVENT_CATALOG = None
    events._FLAT_EVENTS = None
    catalog = events._load_event_catalog()

    total = sum(len(v) for v in catalog.values())
    assert total > 900, f"expected the full library catalog, got {total} events"
    assert len(catalog) >= 20


def test_search_events_finds_an_event_absent_from_the_builtin_list():
    """TOGGLE_PUSHBACK is in the library catalog but not the 50 builtins."""
    from simconnect_mcp.tools import events

    events._EVENT_CATALOG = None
    events._FLAT_EVENTS = None
    found = events._search_events("pushback")
    assert any("PUSHBACK" in e["name"].upper() for e in found)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_events.py -v`
Expected: FAIL — only ~50 events load

- [ ] **Step 3: Load from the correct module**

Replace `_load_event_catalog` in `src/simconnect_mcp/tools/events.py`:

```python
def _load_event_catalog() -> dict[str, list[dict]]:
    """Load the SimConnect event catalog from the installed library.

    Events live in SimConnect.EventList (not SimConnect.RequestList, which is
    what this used to import -- the ImportError was swallowed and the catalog
    silently degraded to the 50-entry builtin list while trigger_event could
    still fire all 994).

    AircraftEvents holds inner classes, each with a `list` of
    (b"EVENT_NAME", "description") tuples.
    """
    global _EVENT_CATALOG, _FLAT_EVENTS
    if _EVENT_CATALOG is not None:
        return _EVENT_CATALOG

    catalog: dict[str, list[dict]] = {}
    try:
        from SimConnect.EventList import AircraftEvents

        for attr_name in dir(AircraftEvents):
            inner = getattr(AircraftEvents, attr_name, None)
            if not isinstance(inner, type) or not hasattr(inner, "list"):
                continue
            # Inner classes are name-mangled: _AircraftEvents__Flight_Controls
            category = attr_name.split("__", 1)[-1].replace("_", " ").strip()
            entries = []
            for item in inner.list:
                raw_name, description = item[0], item[1] if len(item) > 1 else ""
                name = raw_name.decode() if isinstance(raw_name, bytes) else str(raw_name)
                entries.append(
                    {"name": name, "category": category, "description": description}
                )
            if entries:
                catalog[category] = entries
    except Exception:
        logger.warning("Could not load SimConnect event catalog", exc_info=True)
        catalog = {}

    # Fall back when parsing yields nothing, not only when the import raises.
    if not catalog:
        catalog = _builtin_event_catalog()

    _EVENT_CATALOG = catalog
    _FLAT_EVENTS = [e for entries in catalog.values() for e in entries]
    return catalog
```

Add at the top of the file:

```python
import logging

logger = logging.getLogger(__name__)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_events.py -v`
Expected: PASS

- [ ] **Step 5: Confirm the catalog size directly**

Run: `uv run python -c "from simconnect_mcp.tools.events import _load_event_catalog as l; c=l(); print(len(c), 'categories,', sum(len(v) for v in c.values()), 'events')"`
Expected: roughly `24 categories, 994 events`

- [ ] **Step 6: Commit**

```bash
git add src/simconnect_mcp/tools/events.py tests/test_events.py
git commit -m "fix: load the real event catalog from SimConnect.EventList

The import targeted SimConnect.RequestList, where EventList does not
exist; the swallowed ImportError degraded search_events from 994
events to 50 while trigger_event could still fire all of them."
```

---

### Task 13: Fix `trigger_event` — arbitrary events and negative parameters

Two gaps. `ae.find()` only covers the library's static list, so third-party and newer MSFS events cannot be triggered at all, even though `sm.map_to_sim_event()` accepts any name. And `send_event` passes the parameter as an unsigned `DWORD`, so negative values — which events like `AP_VS_VAR_SET_ENGLISH` require for descent — are rejected or wrap incorrectly.

**Files:**
- Modify: `src/simconnect_mcp/tools/events.py` (`trigger_event`)
- Modify: `tests/test_events.py`

**Interfaces:**
- Consumes: `manager.ae.find`, `manager.sm.map_to_sim_event`, `manager.sm.send_event`
- Produces: `trigger_event` returning `{"status": "ok", "event", "parameter", "resolved_via": "catalog"|"mapped"}`, or `EVENT_NOT_FOUND`.

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import MagicMock

from simconnect_mcp.tools.events import trigger_event


async def test_trigger_falls_back_to_map_to_sim_event(mock_simconnect):
    """Third-party and newer MSFS events are not in the library's static list."""
    mock_simconnect["ae"].find.return_value = None
    mock_simconnect["sm"].map_to_sim_event.return_value = MagicMock(value=99)

    result = await trigger_event("SOME_THIRD_PARTY_EVENT")

    assert result["status"] == "ok"
    assert result["resolved_via"] == "mapped"
    mock_simconnect["sm"].map_to_sim_event.assert_called_once_with(b"SOME_THIRD_PARTY_EVENT")


async def test_negative_parameter_is_sent_as_twos_complement(mock_simconnect):
    """AP_VS_VAR_SET_ENGLISH needs negative values; send_event takes a DWORD."""
    await trigger_event("AP_VS_VAR_SET_ENGLISH", parameter=-1800)
    sent = mock_simconnect["event"].call_args.args[0]
    assert sent == (-1800) & 0xFFFFFFFF


async def test_positive_parameter_is_unchanged(mock_simconnect):
    await trigger_event("HEADING_BUG_SET", parameter=270)
    assert mock_simconnect["event"].call_args.args[0] == 270


async def test_unmappable_event_returns_event_not_found(mock_simconnect):
    mock_simconnect["ae"].find.return_value = None
    mock_simconnect["sm"].map_to_sim_event.return_value = None

    result = await trigger_event("DEFINITELY_NOT_AN_EVENT")

    assert result["error"] == "EVENT_NOT_FOUND"
    assert "search_events" in result["suggestion"]


async def test_known_event_reports_catalog_resolution(mock_simconnect):
    result = await trigger_event("PARKING_BRAKES")
    assert result["resolved_via"] == "catalog"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_events.py -k trigger -v`
Expected: FAIL — no `resolved_via` key, no mapping fallback

- [ ] **Step 3: Rewrite trigger_event**

```python
def _to_dword(parameter: int) -> int:
    """SimConnect event parameters are unsigned DWORDs.

    Events such as AP_VS_VAR_SET_ENGLISH take negative values (descent rates),
    which must be sent as two's complement.
    """
    return parameter & 0xFFFFFFFF


@handle_simconnect_errors
@require_connection
async def trigger_event(name: str, parameter: int | None = None) -> dict:
    """Fire a SimConnect event.

    Resolves through the library's event catalog first, then falls back to
    mapping the name directly, so third-party and newer MSFS events work too.

    Args:
        name: Event name (e.g., 'PARKING_BRAKES', 'AP_MASTER', 'THROTTLE_SET')
        parameter: Optional integer parameter. Negative values are supported.

    Returns:
        Confirmation dict including how the event name was resolved.
    """
    manager = SimConnectManager()
    name = name.strip().upper()
    payload = _to_dword(parameter) if parameter is not None else None

    def _fire() -> str:
        event = manager.ae.find(name)
        if event is not None:
            event(payload) if payload is not None else event()
            return "catalog"

        # Not in the static catalog -- map it directly.
        mapped = manager.sm.map_to_sim_event(name.encode("ascii"))
        if mapped is None:
            raise LookupError(name)
        manager.sm.send_event(mapped, DWORD(payload if payload is not None else 0))
        return "mapped"

    try:
        resolved_via = await manager.run_sync(_fire)
    except LookupError:
        return {
            "status": "error",
            "error": "EVENT_NOT_FOUND",
            "message": f"SimConnect could not map event '{name}'.",
            "suggestion": (
                "Check the name with search_events. For aircraft-specific "
                "controls use trigger_custom_event or execute_calculator_code."
            ),
        }

    return {
        "status": "ok",
        "event": name,
        "parameter": parameter,
        "resolved_via": resolved_via,
        "message": f"Event '{name}' triggered successfully",
    }
```

Add at the top of `events.py`:

```python
from ctypes.wintypes import DWORD
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_events.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/tools/events.py tests/test_events.py
git commit -m "fix: trigger arbitrary events and support negative parameters

ae.find() only covers the library's static list, so third-party events
could not be fired. Negative parameters now go out as two's complement."
```

---

### Task 14: Fix `send_sim_text`

The tool calls `manager.sm.send_text(...)`. The library method is `sendText(text, timeSeconds, TEXT_TYPE)`. Every call raises `AttributeError` and returns `{"status": "error", "error": "UNEXPECTED"}`.

**Files:**
- Modify: `src/simconnect_mcp/tools/utilities.py:9-31`
- Modify: `tests/test_simvars.py` or create `tests/test_utilities.py`

**Interfaces:**
- Consumes: `manager.sm.sendText`
- Produces: `send_sim_text(text, duration_s=5.0, color="white")` returning `{"status": "ok", "message", "duration_s", "color"}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_utilities.py`:

```python
import pytest

from simconnect_mcp.tools.utilities import send_sim_text


async def test_send_sim_text_calls_the_real_library_method(mock_simconnect):
    """Regression: the tool called send_text; the method is sendText."""
    result = await send_sim_text("hello", duration_s=3.0)

    assert result["status"] == "ok"
    mock_simconnect["sm"].sendText.assert_called_once()
    args = mock_simconnect["sm"].sendText.call_args.args
    assert args[0] == "hello"
    assert args[1] == 3.0


async def test_send_sim_text_accepts_a_colour(mock_simconnect):
    result = await send_sim_text("caution", color="yellow")
    assert result["status"] == "ok"
    assert result["color"] == "yellow"


async def test_send_sim_text_rejects_an_unknown_colour(mock_simconnect):
    result = await send_sim_text("hi", color="chartreuse")
    assert result["error"] == "INVALID_COLOR"
    assert "white" in result["suggestion"]
```

The mock is a `MagicMock`, so `sendText` exists on it automatically. To make the regression real, assert the *old* name is never used:

```python
async def test_send_sim_text_does_not_call_the_nonexistent_send_text(mock_simconnect):
    await send_sim_text("hello")
    assert not mock_simconnect["sm"].send_text.called
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_utilities.py -v`
Expected: FAIL — `sendText` was never called; `send_text` was

- [ ] **Step 3: Rewrite send_sim_text**

```python
"""Utility tools — text overlay, position teleport."""

from __future__ import annotations

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection

# SIMCONNECT_TEXT_TYPE members for the PRINT_* colour variants.
_TEXT_COLORS = {
    "white": "SIMCONNECT_TEXT_TYPE_PRINT_WHITE",
    "red": "SIMCONNECT_TEXT_TYPE_PRINT_RED",
    "green": "SIMCONNECT_TEXT_TYPE_PRINT_GREEN",
    "blue": "SIMCONNECT_TEXT_TYPE_PRINT_BLUE",
    "yellow": "SIMCONNECT_TEXT_TYPE_PRINT_YELLOW",
    "magenta": "SIMCONNECT_TEXT_TYPE_PRINT_MAGENTA",
    "cyan": "SIMCONNECT_TEXT_TYPE_PRINT_CYAN",
    "black": "SIMCONNECT_TEXT_TYPE_PRINT_BLACK",
}


@handle_simconnect_errors
@require_connection
async def send_sim_text(text: str, duration_s: float = 5.0, color: str = "white") -> dict:
    """Display a text overlay message in the simulator (debug feedback).

    Args:
        text: Text message to display in the sim
        duration_s: How long to display it, in seconds
        color: One of white, red, green, blue, yellow, magenta, cyan, black

    Returns:
        Confirmation dict.
    """
    color_key = color.strip().lower()
    if color_key not in _TEXT_COLORS:
        return {
            "status": "error",
            "error": "INVALID_COLOR",
            "message": f"Unknown text colour '{color}'.",
            "suggestion": f"Use one of: {', '.join(sorted(_TEXT_COLORS))}.",
        }

    manager = SimConnectManager()

    def _send() -> None:
        from SimConnect.Enum import SIMCONNECT_TEXT_TYPE

        text_type = getattr(SIMCONNECT_TEXT_TYPE, _TEXT_COLORS[color_key])
        # The library method is sendText, not send_text.
        manager.sm.sendText(text, duration_s, text_type)

    await manager.run_sync(_send)
    return {
        "status": "ok",
        "message": f"Text displayed in sim: '{text}'",
        "duration_s": duration_s,
        "color": color_key,
    }
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_utilities.py -v`
Expected: PASS

- [ ] **Step 5: Add a live test**

Create `tests/live/test_live_utilities.py`:

```python
import pytest

pytestmark = pytest.mark.live


async def test_send_text_reaches_the_sim(live_manager):
    """Watch the sim window: a white message should appear for 3 seconds."""
    from simconnect_mcp.tools.utilities import send_sim_text

    result = await send_sim_text("simconnect-mcp live test", duration_s=3.0)
    assert result["status"] == "ok"
```

- [ ] **Step 6: Commit**

```bash
git add src/simconnect_mcp/tools/utilities.py tests/test_utilities.py tests/live/test_live_utilities.py
git commit -m "fix: send_sim_text calls sendText, not the nonexistent send_text

Every call raised AttributeError. Also adds colour selection."
```

---

### Task 15: Fix `set_aircraft_position`

The tool accepts `on_ground` and silently ignores it, and writes lat/lon/alt as individual SimVar sets rather than using `sm.set_pos()`, which sends a proper `SIMCONNECT_DATA_INITPOSITION` — the reliable way to reposition an aircraft.

`set_pos` signature: `set_pos(_Altitude, _Latitude, _Longitude, _Airspeed, _Pitch=0.0, _Bank=0.0, _Heading=0, _OnGround=0)`.

**Files:**
- Modify: `src/simconnect_mcp/tools/utilities.py` (`set_aircraft_position`)
- Modify: `tests/test_utilities.py`

**Interfaces:**
- Consumes: `manager.sm.set_pos`, `manager.accessor.read`
- Produces: `set_aircraft_position(latitude, longitude, altitude=None, heading=None, on_ground=False, airspeed=0, pitch=0.0, bank=0.0)`

- [ ] **Step 1: Write the failing tests**

```python
from simconnect_mcp.tools.utilities import set_aircraft_position


async def test_position_uses_set_pos_not_individual_writes(mock_simconnect):
    await set_aircraft_position(latitude=47.6, longitude=-122.3, altitude=5000)
    mock_simconnect["sm"].set_pos.assert_called_once()


async def test_on_ground_is_actually_honoured(mock_simconnect):
    """Regression: on_ground was accepted and silently ignored."""
    await set_aircraft_position(latitude=47.6, longitude=-122.3, on_ground=True)
    kwargs = mock_simconnect["sm"].set_pos.call_args.kwargs
    assert kwargs["_OnGround"] == 1


async def test_arguments_map_to_the_right_set_pos_parameters(mock_simconnect):
    await set_aircraft_position(
        latitude=47.6, longitude=-122.3, altitude=5000, heading=270, airspeed=250
    )
    kwargs = mock_simconnect["sm"].set_pos.call_args.kwargs
    assert kwargs["_Latitude"] == 47.6
    assert kwargs["_Longitude"] == -122.3
    assert kwargs["_Altitude"] == 5000
    assert kwargs["_Heading"] == 270
    assert kwargs["_Airspeed"] == 250


async def test_latitude_out_of_range_is_rejected(mock_simconnect):
    result = await set_aircraft_position(latitude=91.0, longitude=0.0)
    assert result["error"] == "INVALID_POSITION"
    assert not mock_simconnect["sm"].set_pos.called
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_utilities.py -k position -v`
Expected: FAIL — `set_pos` is never called

- [ ] **Step 3: Rewrite set_aircraft_position**

```python
@handle_simconnect_errors
@require_connection
async def set_aircraft_position(
    latitude: float,
    longitude: float,
    altitude: float | None = None,
    heading: float | None = None,
    on_ground: bool = False,
    airspeed: int = 0,
    pitch: float = 0.0,
    bank: float = 0.0,
) -> dict:
    """Reposition the aircraft (test scenario setup).

    Uses SimConnect's SIMCONNECT_DATA_INITPOSITION, which repositions the
    aircraft atomically. Writing PLANE_LATITUDE/LONGITUDE individually, as
    this used to, is unreliable and cannot set the on-ground state.

    Args:
        latitude: Target latitude, -90 to 90 degrees
        longitude: Target longitude, -180 to 180 degrees
        altitude: Target altitude in feet. Omit to keep the current altitude.
        heading: Target heading in degrees true. Omit to keep the current heading.
        on_ground: Place the aircraft on the ground at the position
        airspeed: Target airspeed in knots (0 for a stationary placement)
        pitch: Pitch in degrees
        bank: Bank in degrees

    Returns:
        Confirmation dict with the position applied.
    """
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        return {
            "status": "error",
            "error": "INVALID_POSITION",
            "message": f"Latitude {latitude} / longitude {longitude} is out of range.",
            "suggestion": "Latitude must be -90..90 and longitude -180..180.",
        }

    manager = SimConnectManager()

    def _current(name: str, fallback: float) -> float:
        try:
            value = manager.accessor.read(name)
            return float(value) if value is not None else fallback
        except Exception:
            return fallback

    def _set_pos() -> None:
        target_alt = altitude if altitude is not None else _current("PLANE_ALTITUDE", 0.0)
        target_hdg = (
            heading if heading is not None
            else _current("PLANE_HEADING_DEGREES_TRUE", 0.0)
        )
        manager.sm.set_pos(
            _Altitude=target_alt,
            _Latitude=latitude,
            _Longitude=longitude,
            _Airspeed=airspeed,
            _Pitch=pitch,
            _Bank=bank,
            _Heading=target_hdg,
            _OnGround=1 if on_ground else 0,
        )

    await manager.run_sync(_set_pos)

    result = {
        "status": "ok",
        "message": "Aircraft repositioned",
        "latitude": latitude,
        "longitude": longitude,
        "on_ground": on_ground,
        "airspeed": airspeed,
    }
    if altitude is not None:
        result["altitude"] = altitude
    if heading is not None:
        result["heading"] = heading
    return result
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_utilities.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/tools/utilities.py tests/test_utilities.py
git commit -m "fix: set_aircraft_position uses set_pos and honours on_ground

on_ground was accepted and silently ignored; repositioning now goes
through SIMCONNECT_DATA_INITPOSITION."
```

---

### Task 16: Thread-safety — shared aircraft-title detection

`search_lvars`, `list_lvar_panels`, `_detect_pmdg_variant` and the `simconnect://state/aircraft` resource all call `manager.aq.get("TITLE")` **directly on the event loop with no lock**, violating the invariant CLAUDE.md states for this project. Each can block the loop for the length of a sim round trip. The same four-line try/except is duplicated in all four places.

**Files:**
- Modify: `src/simconnect_mcp/connection.py` (add `detect_aircraft_title`)
- Modify: `src/simconnect_mcp/tools/lvars.py` (`search_lvars`, `list_lvar_panels`)
- Modify: `src/simconnect_mcp/tools/pmdg.py` (`_detect_pmdg_variant`, `_resolve_pmdg_catalog`)
- Modify: `src/simconnect_mcp/resources/state.py`
- Create: `tests/test_title_detection.py`

**Interfaces:**
- Consumes: `manager.accessor.read`, `manager.run_sync`
- Produces: `async SimConnectManager.detect_aircraft_title() -> str | None` — locked, executor-run, 5-second TTL cache. `tools/pmdg.py` gains `async _detect_pmdg_variant()` and `async _resolve_pmdg_catalog(...)`; every caller must now `await` them.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_title_detection.py`:

```python
import pytest

from simconnect_mcp.connection import SimConnectManager


async def test_detect_title_returns_a_string(mock_simconnect):
    title = await SimConnectManager().detect_aircraft_title()
    assert title == "Boeing 747-8i"


async def test_detect_title_decodes_bytes_from_the_sim(mock_simconnect):
    """The sim returns bytes for string SimVars."""
    mock_simconnect["accessor"].read.side_effect = lambda *a, **k: b"Fenix A320"
    manager = SimConnectManager()
    manager._title_cache = None
    assert await manager.detect_aircraft_title() == "Fenix A320"


async def test_detect_title_is_cached_within_the_ttl(mock_simconnect):
    manager = SimConnectManager()
    manager._title_cache = None
    await manager.detect_aircraft_title()
    calls = mock_simconnect["accessor"].read.call_count
    await manager.detect_aircraft_title()
    assert mock_simconnect["accessor"].read.call_count == calls


async def test_detect_title_returns_none_when_disconnected():
    manager = SimConnectManager()
    assert await manager.detect_aircraft_title() is None


async def test_search_lvars_does_not_touch_aq_directly(mock_simconnect):
    """search_lvars used to call aq.get() on the event loop with no lock."""
    from simconnect_mcp.tools.lvars import search_lvars

    await search_lvars("autopilot")
    assert not mock_simconnect["aq"].get.called
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_title_detection.py -v`
Expected: FAIL — `detect_aircraft_title` does not exist

- [ ] **Step 3: Add the shared helper to SimConnectManager**

In `src/simconnect_mcp/connection.py` `__init__`, add:

```python
        self._title_cache: tuple[float, str | None] | None = None
```

and the method (needs `import time` at module scope):

```python
    TITLE_CACHE_TTL = 5.0

    async def detect_aircraft_title(self) -> str | None:
        """Read the TITLE SimVar for aircraft detection.

        Four call sites used to read TITLE directly on the event loop with no
        lock. This routes through run_sync and caches briefly, since it is
        consulted on most catalog operations.
        """
        if not self.is_connected or self.accessor is None:
            return None

        now = time.monotonic()
        if self._title_cache is not None and (now - self._title_cache[0]) < self.TITLE_CACHE_TTL:
            return self._title_cache[1]

        try:
            raw = await self.run_sync(lambda: self.accessor.read("TITLE"))
        except Exception:
            logger.debug("Could not read TITLE", exc_info=True)
            return None

        if raw is None:
            title = None
        elif isinstance(raw, bytes):
            title = raw.decode("ascii", errors="replace").strip()
        else:
            title = str(raw).strip()

        self._title_cache = (now, title)
        return title
```

Clear the cache in `disconnect()`'s `finally` block: `self._title_cache = None`.

- [ ] **Step 4: Replace the four duplicated call sites**

In `src/simconnect_mcp/tools/lvars.py`, in both `search_lvars` and `list_lvar_panels`, replace the try/except block that reads TITLE with:

```python
    catalog_key = None
    manager = SimConnectManager()
    title = await manager.detect_aircraft_title()
    if title:
        catalog_key = detect_catalog(title)
```

In `src/simconnect_mcp/tools/pmdg.py`, make both helpers async:

```python
async def _detect_pmdg_variant() -> str | None:
    """Return "pmdg_777" or "pmdg_737" based on the loaded aircraft."""
    title = await SimConnectManager().detect_aircraft_title()
    if not title:
        return None
    title_str = title.lower()
    if "pmdg 737" in title_str or "pmdg b737" in title_str:
        return "pmdg_737"
    if "pmdg 777" in title_str or "pmdg b777" in title_str:
        return "pmdg_777"
    return None


async def _resolve_pmdg_catalog(
    name: str | None, explicit_variant: str | None
) -> tuple[str | None, str | None]:
```

changing `detected = _detect_pmdg_variant()` to `detected = await _detect_pmdg_variant()`, and leaving the rest of the body unchanged. Then update the three call sites in `get_pmdg_var`, `get_pmdg_cdu` and `send_pmdg_event` to `await _resolve_pmdg_catalog(...)`.

In `src/simconnect_mcp/resources/state.py`, make the resource async and use the accessor:

```python
    @mcp.resource("simconnect://state/aircraft")
    async def state_aircraft() -> dict:
        """Current aircraft title, type, and position."""
        manager = SimConnectManager()
        if not manager.is_connected or manager.accessor is None:
            return {"status": "not_connected"}

        names = [
            "TITLE", "ATC_TYPE", "ATC_ID",
            "PLANE_LATITUDE", "PLANE_LONGITUDE", "PLANE_ALTITUDE",
        ]
        try:
            data = await manager.run_sync(
                lambda: manager.accessor.read_many([(n, None, None) for n in names])
            )
        except Exception as e:
            return {"status": "error", "message": str(e)}
        return {"status": "ok", "aircraft": data}
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. The PMDG tests exercise `_resolve_pmdg_catalog`; if any call it synchronously, add `await`.

- [ ] **Step 6: Confirm no unlocked sim reads remain**

Run: `uv run grep -rn "aq\.get\|accessor\.read" src/simconnect_mcp/ --include=*.py | grep -v "run_sync\|def \|#"`
Expected: every remaining hit is inside a function passed to `run_sync`. Inspect any that are not.

- [ ] **Step 7: Commit**

```bash
git add src/simconnect_mcp/connection.py src/simconnect_mcp/tools/lvars.py src/simconnect_mcp/tools/pmdg.py src/simconnect_mcp/resources/state.py tests/test_title_detection.py
git commit -m "fix: route aircraft-title detection through run_sync

Four call sites read TITLE directly on the event loop without the sim
lock. Consolidated into one cached, locked helper."
```

---

### Task 17: Connection lifecycle — event loop, logging, and vendored log levels

Three remaining correctness items. `asyncio.get_event_loop()` inside a coroutine is deprecated from 3.10 and errors in newer versions. `main()` calls `logging.basicConfig(level=logging.INFO)`, and the vendored MobiFlight bridge logs at INFO on every client-data call, so a single L-var read emits several lines of noise. Logging must also be pinned to stderr — this is a stdio server.

**Files:**
- Modify: `src/simconnect_mcp/connection.py:175-183` (`run_sync`)
- Modify: `src/simconnect_mcp/server.py:81-100` (inline connection tools), `:130-133` (`main`)
- Modify: `src/simconnect_mcp/vendor/mobiflight_variable_requests.py` (log levels only)
- Create: `tests/test_logging.py`

**Interfaces:**
- Consumes: nothing
- Produces: `main()` configuring a stderr-only handler at WARNING, overridable via the `SIMCONNECT_MCP_LOG_LEVEL` environment variable.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_logging.py`:

```python
import logging
import sys


def test_logging_setup_uses_stderr_only(monkeypatch):
    """This is a stdio server: anything on stdout corrupts JSON-RPC."""
    from simconnect_mcp.server import configure_logging

    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    configure_logging()

    assert root.handlers, "expected a handler to be installed"
    for handler in root.handlers:
        assert getattr(handler, "stream", sys.stderr) is not sys.stdout


def test_default_log_level_is_warning(monkeypatch):
    from simconnect_mcp.server import configure_logging

    monkeypatch.delenv("SIMCONNECT_MCP_LOG_LEVEL", raising=False)
    configure_logging()
    assert logging.getLogger().level == logging.WARNING


def test_log_level_is_overridable_by_env(monkeypatch):
    from simconnect_mcp.server import configure_logging

    monkeypatch.setenv("SIMCONNECT_MCP_LOG_LEVEL", "DEBUG")
    configure_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_run_sync_uses_get_running_loop():
    """get_event_loop() inside a coroutine is deprecated since 3.10."""
    import inspect

    from simconnect_mcp import connection

    source = inspect.getsource(connection)
    assert "get_event_loop()" not in source
    assert "get_running_loop()" in source
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_logging.py -v`
Expected: FAIL — `configure_logging` does not exist

- [ ] **Step 3: Switch to get_running_loop**

In `src/simconnect_mcp/connection.py`, in `run_sync`:

```python
        loop = asyncio.get_running_loop()
```

In `src/simconnect_mcp/server.py`, in `connect_to_sim` and `disconnect_from_sim`, replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()`, and move `import asyncio` to module scope rather than inside each function.

- [ ] **Step 4: Add configure_logging**

In `src/simconnect_mcp/server.py`, replace `main()` and add above it:

```python
def configure_logging() -> None:
    """Send logs to stderr only.

    This server speaks JSON-RPC over stdio; anything written to stdout
    corrupts the protocol stream. Defaults to WARNING because the vendored
    MobiFlight bridge is chatty at INFO. Override with SIMCONNECT_MCP_LOG_LEVEL.
    """
    level_name = os.environ.get("SIMCONNECT_MCP_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)

    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)


def main() -> None:
    """Run the MCP server with stdio transport."""
    configure_logging()
    mcp.run(transport="stdio")
```

Add `import os` and `import sys` to the imports at the top of `server.py`.

- [ ] **Step 5: Quiet the vendored bridge**

In `src/simconnect_mcp/vendor/mobiflight_variable_requests.py`, change `logging.info` to `logging.debug` in exactly three methods — `add_to_client_data_definition`, `subscribe_to_data_change` and `send_data` — plus the one in `send_command`. These fire on every L-var read. Leave `__init__`, `initialize_client_data_areas` and `clear_sim_variables` at INFO; they run once per connection.

Add a note at the top of the file so the divergence from upstream is not lost:

```python
# Vendored from Koseng/MSFSPythonSimConnectMobiFlightExtension.
# Local change: per-call logging demoted from INFO to DEBUG (a single L-var
# read otherwise emits four INFO lines).
```

- [ ] **Step 6: Run to verify they pass**

Run: `uv run pytest tests/test_logging.py -v`
Expected: PASS

- [ ] **Step 7: Verify the server still starts and writes nothing to stdout**

Run: `uv run python -c "import sys, io; buf = io.StringIO(); sys.stdout = buf; import simconnect_mcp.server as s; s.configure_logging(); sys.stdout = sys.__stdout__; print('stdout bytes during import+setup:', len(buf.getvalue()))"`
Expected: `stdout bytes during import+setup: 0`

- [ ] **Step 8: Run the full suite and ruff**

Run: `uv run pytest -q && uv run ruff check src/ tests/`
Expected: PASS, no ruff findings

- [ ] **Step 9: Commit**

```bash
git add src/simconnect_mcp/connection.py src/simconnect_mcp/server.py src/simconnect_mcp/vendor/mobiflight_variable_requests.py tests/test_logging.py
git commit -m "fix: stderr-only logging, get_running_loop, quieter vendored bridge

Logging to stdout would corrupt the JSON-RPC stream. Default level
drops to WARNING and per-call bridge logging drops to DEBUG."
```

---

### Task 18: Phase 0 live verification

Everything the mocks cannot prove. Run with MSFS 2024 open and an aircraft loaded on a runway or parking spot.

**Files:**
- Create: `tests/live/test_live_simvars.py`
- Create: `tests/live/test_live_events.py`

**Interfaces:**
- Consumes: `live_manager` fixture from Task 1
- Produces: nothing consumed by later tasks; this is the Phase 0 acceptance gate.

- [ ] **Step 1: Write the live SimVar tests**

Create `tests/live/test_live_simvars.py`:

```python
"""Live verification of the data-definition layer.

Run with MSFS running and an aircraft loaded:  uv run pytest -m live
"""
import pytest

from simconnect_mcp.simvar_access import SimVarNotFoundError, SimVarNotSettableError

pytestmark = pytest.mark.live


def test_unit_conversion_is_real(live_manager):
    """The core claim of the new layer: the requested unit is honoured.

    1 foot = 0.3048 metres, so the two readings must differ by that ratio.
    """
    feet = live_manager.accessor.read("PLANE_ALTITUDE", unit="feet")
    metres = live_manager.accessor.read("PLANE_ALTITUDE", unit="meters")

    assert feet is not None and metres is not None
    assert metres == pytest.approx(feet * 0.3048, rel=0.01)


def test_string_simvar_decodes_to_str(live_manager):
    title = live_manager.accessor.read("TITLE")
    assert isinstance(title, str)
    assert title.strip(), "TITLE should not be empty with an aircraft loaded"


def test_unknown_simvar_raises_not_found(live_manager):
    with pytest.raises(SimVarNotFoundError):
        live_manager.accessor.read("DEFINITELY_NOT_A_REAL_SIMVAR")


def test_write_to_read_only_var_is_rejected(live_manager):
    """The bug this layer replaces reported success here."""
    with pytest.raises((SimVarNotSettableError, SimVarNotFoundError)):
        live_manager.accessor.write("AIRSPEED_TRUE", 250.0, grace=0.5)


def test_write_then_read_round_trips(live_manager):
    """Autopilot altitude is settable and immediately readable."""
    live_manager.accessor.write("AUTOPILOT_ALTITUDE_LOCK_VAR", 12000.0, unit="feet")
    value = live_manager.accessor.read("AUTOPILOT_ALTITUDE_LOCK_VAR", unit="feet")
    assert value == pytest.approx(12000.0, abs=50.0)


def test_indexed_read_with_index_zero_and_one(live_manager):
    """Index 0 must reach the sim rather than being dropped."""
    for index in (0, 1):
        value = live_manager.accessor.read(
            "GENERAL_ENG_THROTTLE_LEVER_POSITION", unit="percent", index=index
        )
        assert value is not None


def test_variable_outside_the_library_table_is_readable(live_manager):
    """AircraftRequests' hardcoded table does not cover everything.

    No explicit unit: this also proves the catalog default ('Slugs per cubic
    feet') is what reaches AddToDataDefinition.
    """
    value = live_manager.accessor.read("AMBIENT_DENSITY")
    assert value is not None
```

- [ ] **Step 2: Write the live event test**

Create `tests/live/test_live_events.py`:

```python
import pytest

pytestmark = pytest.mark.live


async def test_catalog_event_fires(live_manager):
    from simconnect_mcp.tools.events import trigger_event

    result = await trigger_event("PARKING_BRAKES")
    assert result["status"] == "ok"
    assert result["resolved_via"] == "catalog"


async def test_negative_parameter_reaches_the_sim(live_manager):
    """AP_VS_VAR_SET_ENGLISH with a descent rate; verified by reading it back."""
    from simconnect_mcp.tools.events import trigger_event

    result = await trigger_event("AP_VS_VAR_SET_ENGLISH", parameter=-1800)
    assert result["status"] == "ok"

    value = live_manager.accessor.read("AUTOPILOT_VERTICAL_HOLD_VAR", unit="feet per minute")
    assert value == pytest.approx(-1800, abs=50)
```

- [ ] **Step 3: Ask the user to launch MSFS 2024**

Stop and ask the user to start MSFS 2024 and load an aircraft (any aircraft, on the ground). Wait for confirmation before running the suite.

- [ ] **Step 4: Run the live suite**

Run: `uv run pytest -m live -v`
Expected: PASS. If `test_unit_conversion_is_real` fails, the unit is not reaching `AddToDataDefinition` — check `simconnect_name()` and the unit encoding in `definition_id`. If everything times out, the dispatcher is not resolving `SIMOBJECT_DATA`; check that `SimConnectDispatcher` was constructed rather than the plain `SimConnect` fallback by inspecting `manager.accessor is not None`.

- [ ] **Step 5: Confirm the unit tests still pass without the sim**

Run: `uv run pytest -q`
Expected: PASS, live tests deselected

- [ ] **Step 6: Commit**

```bash
git add tests/live/
git commit -m "test: live verification for the SimVar data-definition layer

Covers unit conversion, string decoding, exception correlation,
index 0, and negative event parameters against a running sim."
```

---

## Phase 0 Exit Criteria

- [ ] `uv run pytest` passes (the command documented in CLAUDE.md)
- [ ] `uv run pytest -m live` passes against MSFS 2024
- [ ] `uv run ruff check src/ tests/` is clean
- [ ] Every tool listed in the spec's "Tools that cannot work" table now works, or fails with a specific, actionable error
- [ ] No `aq.get` / `accessor.read` call outside a `run_sync` callable
- [ ] Nothing is written to stdout at import, connect, or during any tool call

Proceed to `2026-08-29-mcp-modernization-phase1-mcp-surface.md`.
