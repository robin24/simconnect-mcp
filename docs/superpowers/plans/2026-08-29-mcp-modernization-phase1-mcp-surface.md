# SimConnect MCP Modernization — Phase 1: MCP Surface

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the tool surface up to modern MCP standards — behaviour annotations, described and constrained inputs, structured outputs, real pagination, and collision-safe names — without changing what any tool does.

**Architecture:** Two shared modules carry the new conventions. `tools/models.py` holds every Pydantic output model plus the `ToolError` envelope; `tools/formatting.py` holds pagination and the markdown renderers. Each domain module is then converted to flat `Annotated` parameters and a `Model | ToolError` return type. `server.py` stops registering tools through a loop — which cannot carry per-tool metadata — and registers each one explicitly with its annotations.

**Tech Stack:** Python 3.10+, `mcp[cli]>=1.26` (FastMCP), Pydantic v2, pytest, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-08-29-mcp-modernization-design.md`
**Depends on:** Phase 0 complete (`2026-08-29-mcp-modernization-phase0-correctness.md`)

## Global Constraints

- **Use flat `Annotated[T, Field(...)]` parameters, never a single `params: SomeModel` argument.** This was verified against the installed SDK: a single model parameter produces `inputSchema.properties == {"params": {"$ref": ...}}`, hiding every argument behind one opaque object, and a `-> dict` return produces **no** `outputSchema` at all. Flat parameters plus a Pydantic return type produce a fully described input schema *and* an output schema.
- **Every tool's return annotation must be a union `SomeModel | ToolError`.** `handle_simconnect_errors` returns the error envelope, so a bare `-> SomeModel` annotation would fail validation on the error path. Verified working: both paths produce valid `structuredContent`. Because the root type is a union, FastMCP nests the payload one level under `result` — that is expected and consistent across all tools.
- Behaviour must not change in this phase. Only names, schemas, annotations, pagination, and formatting.
- Tool renames happen in Task 8, all at once, so the tree is never half-renamed.
- Every SimConnect DLL call still runs inside `run_sync`.
- Nothing may be written to stdout.
- Run tests with `uv run pytest`.

---

### Task 1: Shared output models and the error envelope

Every tool currently returns a bare `dict`, which means no `outputSchema`. This task defines the models that the rest of the phase returns, and converts `handle_simconnect_errors` to return a `ToolError` instead of a dict — keeping the same field names so existing consumers keep parsing.

**Files:**
- Create: `src/simconnect_mcp/tools/models.py`
- Modify: `src/simconnect_mcp/tools/__init__.py` (decorator returns `ToolError`)
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: `simvar_access` error classes (Phase 0 Task 5)
- Produces:
  - `ToolError(status="error", error: str, message: str, suggestion: str | None, suggestions: list[str] | None)`
  - `Page` mixin fields: `total: int`, `count: int`, `offset: int`, `has_more: bool`, `next_offset: int | None`
  - `SimVarValue`, `SimVarBulkResult`, `SimVarWriteResult`, `WatchResult`, `SearchResult`, `CategoryList`, `EventResult`, `LVarValue`, `LVarWriteResult`, `LVarList`, `CalculatorResult`, `CatalogBrowse`, `AircraftSnapshot`, `TextResult`, `PositionResult`, `ConnectionStatus`
  - `error_from(exc) -> ToolError` mapping `simvar_access` exceptions to codes

- [ ] **Step 1: Write the failing tests**

Create `tests/test_models.py`:

```python
import pytest

from simconnect_mcp.simvar_access import (
    SimVarNotFoundError,
    SimVarNotSettableError,
    SimVarTimeoutError,
    UnitMismatchError,
)
from simconnect_mcp.tools.models import Page, SimVarValue, ToolError, error_from


def test_tool_error_keeps_the_legacy_field_names():
    err = ToolError(error="SIMVAR_NOT_FOUND", message="nope", suggestion="try search")
    dumped = err.model_dump()
    assert dumped["status"] == "error"
    assert set(dumped) >= {"status", "error", "message", "suggestion"}


@pytest.mark.parametrize(
    "exc,code",
    [
        (SimVarNotFoundError("x"), "SIMVAR_NOT_FOUND"),
        (SimVarNotSettableError("x"), "SIMVAR_NOT_SETTABLE"),
        (UnitMismatchError("x"), "UNIT_MISMATCH"),
        (SimVarTimeoutError("x"), "SIM_TIMEOUT"),
    ],
)
def test_error_from_maps_each_accessor_exception(exc, code):
    assert error_from(exc).error == code


def test_error_from_always_supplies_a_suggestion():
    assert error_from(SimVarNotFoundError("x")).suggestion


def test_page_reports_has_more_and_next_offset():
    page = Page.build(total=100, offset=0, count=25)
    assert page.has_more is True
    assert page.next_offset == 25


def test_page_on_the_last_slice_has_no_next_offset():
    page = Page.build(total=30, offset=25, count=5)
    assert page.has_more is False
    assert page.next_offset is None


def test_simvar_value_accepts_a_string_value():
    """TITLE and friends return str, not float."""
    value = SimVarValue(name="TITLE", value="Boeing 747-8i", unit="string")
    assert value.value == "Boeing 747-8i"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simconnect_mcp.tools.models'`

- [ ] **Step 3: Create the models module**

Create `src/simconnect_mcp/tools/models.py`:

```python
"""Pydantic models for tool inputs and outputs.

Tools return a `SomeModel | ToolError` union so FastMCP can emit an
outputSchema covering both paths.  A bare `-> SomeModel` annotation would
fail validation whenever handle_simconnect_errors returns an error.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from simconnect_mcp.simvar_access import (
    SimVarError,
    SimVarNotFoundError,
    SimVarNotSettableError,
    SimVarTimeoutError,
    UnitMismatchError,
)


class ToolError(BaseModel):
    """Failure envelope. Field names match the pre-Phase-1 error dicts."""

    status: Literal["error"] = "error"
    error: str = Field(..., description="Stable machine-readable error code")
    message: str = Field(..., description="What went wrong")
    suggestion: str | None = Field(None, description="What to try next")
    suggestions: list[str] | None = Field(
        None, description="Close name matches, when the failure was a bad name"
    )


_ERROR_CODES: dict[type[Exception], tuple[str, str]] = {
    SimVarNotFoundError: (
        "SIMVAR_NOT_FOUND",
        "Use search_simvars to find the correct variable name.",
    ),
    SimVarNotSettableError: (
        "SIMVAR_NOT_SETTABLE",
        "This variable is read-only. Check the 'settable' flag with "
        "search_simvars, or use trigger_event for an equivalent control.",
    ),
    UnitMismatchError: (
        "UNIT_MISMATCH",
        "Check the variable's units with search_simvars, or omit the unit "
        "argument to use the catalog default.",
    ),
    SimVarTimeoutError: (
        "SIM_TIMEOUT",
        "The sim may be paused or loading. Try again shortly.",
    ),
}


def error_from(exc: Exception, suggestions: list[str] | None = None) -> ToolError:
    """Map an accessor exception to a ToolError with an actionable suggestion."""
    code, suggestion = _ERROR_CODES.get(
        type(exc), ("SIMCONNECT_ERROR", "Check that MSFS is running and try again.")
    )
    return ToolError(
        error=code, message=str(exc), suggestion=suggestion, suggestions=suggestions
    )


class Page(BaseModel):
    """Pagination metadata returned alongside every list or search result."""

    total: int = Field(..., description="Total matches before pagination")
    count: int = Field(..., description="Number of results in this response")
    offset: int = Field(..., description="Offset this response starts at")
    has_more: bool = Field(..., description="Whether more results follow")
    next_offset: int | None = Field(None, description="Offset for the next page")

    @classmethod
    def build(cls, total: int, offset: int, count: int) -> Page:
        has_more = (offset + count) < total
        return cls(
            total=total,
            count=count,
            offset=offset,
            has_more=has_more,
            next_offset=offset + count if has_more else None,
        )


class OkModel(BaseModel):
    status: Literal["ok"] = "ok"


class SimVarValue(OkModel):
    name: str
    value: float | str | None = None
    unit: str = Field(..., description="Unit the value was actually read in")
    index: int | None = None


class SimVarWriteResult(OkModel):
    name: str
    value_set: float
    unit: str
    index: int | None = None


class SimVarBulkResult(OkModel):
    count: int
    variables: dict[str, dict[str, Any]] = Field(
        ..., description="Keyed by NAME or NAME:index; each holds a value or an error"
    )


class WatchSample(BaseModel):
    t: float = Field(..., description="Seconds since the watch started")
    value: float | str | None = None


class WatchResult(OkModel):
    name: str
    unit: str
    index: int | None = None
    samples: list[WatchSample]
    sample_count: int
    error_count: int
    duration_s: int
    interval_ms: int


class SearchResult(OkModel):
    """Search results, either rendered markdown or structured rows."""

    page: Page
    results: list[dict[str, Any]] | None = Field(
        None, description="Structured rows; null when response_format is markdown"
    )
    markdown: str | None = Field(
        None, description="Rendered table; null when response_format is json"
    )
    query: str | None = None
    filters: dict[str, Any] | None = None


class CategoryList(OkModel):
    categories: dict[str, int] = Field(..., description="Category name to variable count")
    total_variables: int


class EventResult(OkModel):
    event: str
    parameter: int | None = None
    resolved_via: str | None = Field(
        None, description="'catalog' or 'mapped' -- how the event name was resolved"
    )
    custom: bool = False
    message: str


class LVarValue(OkModel):
    name: str
    rpn: str
    value: float | None = None


class LVarWriteResult(OkModel):
    name: str
    value_set: float


class LVarList(OkModel):
    page: Page
    lvars: list[str]


class CalculatorResult(OkModel):
    code: str
    mode: Literal["read", "execute"]
    value: float | None = None
    message: str | None = None


class CatalogBrowse(OkModel):
    """Catalog listing: aircraft catalogs, panels, or one panel's variables."""

    catalog: str | None = None
    page: Page
    catalogs: list[dict[str, Any]] | None = None
    panels: list[dict[str, Any]] | None = None
    panel: str | None = None
    variables: list[dict[str, Any]] | None = None
    markdown: str | None = None


class AircraftSnapshot(OkModel):
    sections: list[str] = Field(..., description="Sections included in this snapshot")
    data: dict[str, Any]


class TextResult(OkModel):
    message: str
    duration_s: float
    color: str


class PositionResult(OkModel):
    message: str
    latitude: float
    longitude: float
    altitude: float | None = None
    heading: float | None = None
    on_ground: bool
    airspeed: int


class ConnectionStatus(BaseModel):
    state: str
    connected: bool
    mobiflight_available: bool
    sim_paused: bool | None = None
    sim_running: bool | None = None
```

- [ ] **Step 4: Make the decorator return ToolError**

In `src/simconnect_mcp/tools/__init__.py`, replace the three error dicts in `handle_simconnect_errors` and the one in `require_connection` with `ToolError` instances:

```python
import functools
import logging
from typing import Any, Callable

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.simvar_access import SimVarError
from simconnect_mcp.tools.models import ToolError, error_from

logger = logging.getLogger(__name__)


def handle_simconnect_errors(fn: Callable) -> Callable:
    """Wrap a tool so failures return a ToolError instead of raising."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except SimVarError as e:
            return error_from(e)
        except ConnectionError as e:
            return ToolError(
                error="CONNECTION_LOST",
                message=str(e),
                suggestion="Try reconnecting with msfs_connect.",
            )
        except OSError as e:
            return ToolError(
                error="CONNECTION_LOST",
                message=f"SimConnect communication error: {e}",
                suggestion="The connection may have dropped. Try reconnecting.",
            )
        except Exception as e:
            logger.exception("Unexpected error in %s", fn.__name__)
            return ToolError(
                error="UNEXPECTED",
                message=str(e),
                suggestion="Check that MSFS is running and try again.",
            )

    return wrapper


def require_connection(fn: Callable) -> Callable:
    """Ensure SimConnect is connected before calling the tool."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        manager = SimConnectManager()
        err = manager.ensure_connected()
        if err is not None:
            return ToolError(
                error=err.get("error", "NOT_CONNECTED"),
                message=err.get("message", "Not connected to MSFS"),
                suggestion=err.get("suggestion"),
            )
        return await fn(*args, **kwargs)

    return wrapper
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Run the full suite and fix dict-shaped assertions**

Run: `uv run pytest -q`
Expected: Some existing tests assert `result["error"] == ...` on error paths. Update those to `result.error == ...`, since the decorator now returns a model. Success paths still return dicts until each domain task converts them.

- [ ] **Step 7: Commit**

```bash
git add src/simconnect_mcp/tools/models.py src/simconnect_mcp/tools/__init__.py tests/test_models.py
git commit -m "feat: add Pydantic tool models and ToolError envelope

Tools returning bare dicts produce no outputSchema. Field names in
ToolError match the previous error dicts so consumers keep parsing."
```

---

### Task 2: Pagination and markdown formatting helpers

Three separate search functions hard-slice `[:50]` with no `total` and no signal that results were dropped — across 4,900+ catalog entries. This task centralises pagination and the markdown rendering so no domain module reimplements either.

**Files:**
- Create: `src/simconnect_mcp/tools/formatting.py`
- Create: `tests/test_formatting.py`

**Interfaces:**
- Consumes: `tools.models.Page`
- Produces:
  - `ResponseFormat` enum (`MARKDOWN = "markdown"`, `JSON = "json"`)
  - `paginate(rows, offset, limit) -> tuple[list, Page]`
  - `render_table(rows, columns, title=None) -> str`
  - `build_search_result(rows, offset, limit, response_format, columns, title, query=None, filters=None) -> SearchResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_formatting.py`:

```python
from simconnect_mcp.tools.formatting import (
    ResponseFormat,
    build_search_result,
    paginate,
    render_table,
)

ROWS = [{"name": f"VAR_{i}", "units": "number", "settable": i % 2 == 0} for i in range(120)]
COLUMNS = [("name", "Name"), ("units", "Units"), ("settable", "Settable")]


def test_paginate_returns_the_requested_slice():
    rows, page = paginate(ROWS, offset=0, limit=25)
    assert len(rows) == 25
    assert page.total == 120
    assert page.has_more is True
    assert page.next_offset == 25


def test_paginate_respects_offset():
    rows, page = paginate(ROWS, offset=100, limit=25)
    assert len(rows) == 20
    assert page.has_more is False
    assert page.next_offset is None
    assert rows[0]["name"] == "VAR_100"


def test_paginate_beyond_the_end_returns_nothing():
    rows, page = paginate(ROWS, offset=500, limit=25)
    assert rows == []
    assert page.total == 120
    assert page.has_more is False


def test_render_table_emits_a_markdown_table():
    md = render_table(ROWS[:2], COLUMNS, title="SimVars")
    assert "# SimVars" in md
    assert "| Name | Units | Settable |" in md
    assert "VAR_0" in md


def test_render_table_escapes_pipes_in_values():
    md = render_table([{"name": "A|B", "units": "", "settable": False}], COLUMNS)
    assert r"A\|B" in md


def test_render_table_handles_no_rows():
    assert "No matches" in render_table([], COLUMNS)


def test_markdown_format_populates_markdown_and_leaves_results_null():
    result = build_search_result(
        ROWS, offset=0, limit=10, response_format=ResponseFormat.MARKDOWN,
        columns=COLUMNS, title="SimVars", query="var",
    )
    assert result.markdown is not None
    assert result.results is None
    assert result.page.total == 120


def test_json_format_populates_results_and_leaves_markdown_null():
    result = build_search_result(
        ROWS, offset=0, limit=10, response_format=ResponseFormat.JSON,
        columns=COLUMNS, title="SimVars",
    )
    assert result.results is not None
    assert len(result.results) == 10
    assert result.markdown is None


def test_markdown_notes_that_more_results_exist():
    """Truncation must be visible; the old [:50] slice was silent."""
    result = build_search_result(
        ROWS, offset=0, limit=10, response_format=ResponseFormat.MARKDOWN,
        columns=COLUMNS, title="SimVars",
    )
    assert "110 more" in result.markdown
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_formatting.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create the module**

Create `src/simconnect_mcp/tools/formatting.py`:

```python
"""Pagination and response rendering shared by every search and browse tool.

Markdown is the default for catalog listings because a table of a few hundred
variables costs materially less context than the equivalent JSON.  Telemetry
reads stay structured -- markdown-formatting numbers an agent computes on
makes them harder to use, not easier.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Sequence

from simconnect_mcp.tools.models import Page, SearchResult

DEFAULT_LIMIT = 25
MAX_LIMIT = 200


class ResponseFormat(str, Enum):
    """Output shape for search and browse tools."""

    MARKDOWN = "markdown"
    JSON = "json"


def paginate(
    rows: Sequence[dict[str, Any]], offset: int = 0, limit: int = DEFAULT_LIMIT
) -> tuple[list[dict[str, Any]], Page]:
    """Slice rows and describe the slice. Never truncates silently."""
    offset = max(0, offset)
    limit = max(1, min(limit, MAX_LIMIT))
    window = list(rows[offset : offset + limit])
    return window, Page.build(total=len(rows), offset=offset, count=len(window))


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("|", r"\|").replace("\n", " ")


def render_table(
    rows: Sequence[dict[str, Any]],
    columns: Sequence[tuple[str, str]],
    title: str | None = None,
) -> str:
    """Render rows as a markdown table. `columns` is (key, header) pairs."""
    lines: list[str] = []
    if title:
        lines += [f"# {title}", ""]
    if not rows:
        lines.append("No matches.")
        return "\n".join(lines)

    headers = [header for _, header in columns]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(_cell(row.get(key)) for key, _ in columns) + " |")
    return "\n".join(lines)


def build_search_result(
    rows: Sequence[dict[str, Any]],
    offset: int,
    limit: int,
    response_format: ResponseFormat,
    columns: Sequence[tuple[str, str]],
    title: str | None = None,
    query: str | None = None,
    filters: dict[str, Any] | None = None,
) -> SearchResult:
    """Paginate and render in the requested format."""
    window, page = paginate(rows, offset, limit)

    if response_format is ResponseFormat.JSON:
        return SearchResult(
            page=page, results=window, markdown=None, query=query, filters=filters
        )

    markdown = render_table(window, columns, title=title)
    if page.has_more:
        remaining = page.total - (page.offset + page.count)
        markdown += (
            f"\n\n_{remaining} more result(s). "
            f"Call again with offset={page.next_offset} to continue._"
        )
    return SearchResult(
        page=page, results=None, markdown=markdown, query=query, filters=filters
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_formatting.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/tools/formatting.py tests/test_formatting.py
git commit -m "feat: shared pagination and markdown rendering

Replaces three separate hard [:50] slices that truncated silently."
```

---

### Task 3: Convert the SimVar tools

**Files:**
- Modify: `src/simconnect_mcp/tools/simvars.py` (all six tools)
- Modify: `tests/test_simvars.py`

**Interfaces:**
- Consumes: `tools.models`, `tools.formatting`, `manager.accessor`
- Produces: `get_simvar`, `set_simvar`, `get_simvar_bulk`, `search_simvars`, `list_simvar_categories`, `watch_simvar` — all with flat `Annotated` parameters and `Model | ToolError` returns. Function names are unchanged here; Task 8 renames them.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_simvars.py`:

```python
from simconnect_mcp.tools.formatting import ResponseFormat
from simconnect_mcp.tools.models import SearchResult, SimVarValue
from simconnect_mcp.tools.simvars import list_simvar_categories, search_simvars


async def test_get_simvar_returns_a_model(mock_simconnect):
    result = await get_simvar("PLANE_ALTITUDE", unit="feet")
    assert isinstance(result, SimVarValue)
    assert result.unit == "feet"


async def test_search_defaults_to_markdown(mock_simconnect):
    result = await search_simvars("altitude")
    assert isinstance(result, SearchResult)
    assert result.markdown is not None
    assert result.results is None


async def test_search_json_format_returns_rows(mock_simconnect):
    result = await search_simvars("altitude", response_format=ResponseFormat.JSON)
    assert result.results is not None


async def test_search_paginates_instead_of_truncating(mock_simconnect):
    """The old code sliced [:50] with no total and no signal."""
    first = await search_simvars("e", limit=10, offset=0, response_format=ResponseFormat.JSON)
    assert first.page.total > 50
    assert first.page.count == 10
    assert first.page.has_more is True

    second = await search_simvars("e", limit=10, offset=10, response_format=ResponseFormat.JSON)
    assert second.results[0] != first.results[0]


async def test_search_limit_is_clamped(mock_simconnect):
    result = await search_simvars("e", limit=5000, response_format=ResponseFormat.JSON)
    assert result.page.count <= 200


async def test_list_categories_returns_a_model(mock_simconnect):
    result = await list_simvar_categories()
    assert result.total_variables > 1000
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_simvars.py -v`
Expected: FAIL — tools still return dicts

- [ ] **Step 3: Convert the tools**

Rewrite the tool signatures and returns in `src/simconnect_mcp/tools/simvars.py`. Add these imports:

```python
from typing import Annotated, Any

from pydantic import Field

from simconnect_mcp.tools.formatting import (
    DEFAULT_LIMIT,
    ResponseFormat,
    build_search_result,
)
from simconnect_mcp.tools.models import (
    CategoryList,
    SearchResult,
    SimVarBulkResult,
    SimVarValue,
    SimVarWriteResult,
    ToolError,
    WatchResult,
    WatchSample,
    error_from,
)
```

`get_simvar` — keep the Phase 0 body, change the signature and the returns:

```python
@handle_simconnect_errors
@require_connection
async def get_simvar(
    name: Annotated[
        str,
        Field(description="SimVar name, e.g. 'PLANE_ALTITUDE' or 'AIRSPEED_INDICATED'",
              min_length=1, max_length=128),
    ],
    unit: Annotated[
        str | None,
        Field(description="Unit to read in, e.g. 'feet', 'meters', 'knots'. "
                          "Defaults to the catalog unit for this variable."),
    ] = None,
    index: Annotated[
        int | None,
        Field(description="Index for indexed SimVars such as engine number. "
                          "Index 0 is valid.", ge=0, le=64),
    ] = None,
) -> SimVarValue | ToolError:
    """Read a SimVar value by name, in the requested unit.

    Returns the value together with the unit it was actually read in.
    Use search_simvars first if you are unsure of the exact name or units.
    """
    manager = SimConnectManager()
    resolved_unit = resolve_unit(name, unit)
    try:
        value = await manager.run_sync(
            lambda: manager.accessor.read(name, unit=unit, index=index)
        )
    except SimVarNotFoundError as e:
        return error_from(e, suggestions=suggest_names(name))
    except SimVarError as e:
        return error_from(e)
    return SimVarValue(name=name, value=value, unit=resolved_unit, index=index)
```

`set_simvar`:

```python
@handle_simconnect_errors
@require_connection
async def set_simvar(
    name: Annotated[str, Field(description="SimVar name; must be settable",
                               min_length=1, max_length=128)],
    value: Annotated[float, Field(description="Value to write")],
    unit: Annotated[
        str | None,
        Field(description="Unit the value is expressed in. Defaults to the catalog unit."),
    ] = None,
    index: Annotated[
        int | None, Field(description="Index for indexed SimVars. Index 0 is valid.",
                          ge=0, le=64)
    ] = None,
) -> SimVarWriteResult | ToolError:
    """Write a value to a settable SimVar.

    Fails with a specific error if the sim rejects the write, rather than
    reporting success. Check the 'settable' flag with search_simvars first.
    """
    manager = SimConnectManager()
    resolved_unit = resolve_unit(name, unit)
    try:
        await manager.run_sync(
            lambda: manager.accessor.write(name, value, unit=unit, index=index)
        )
    except SimVarNotFoundError as e:
        return error_from(e, suggestions=suggest_names(name))
    except SimVarError as e:
        return error_from(e)
    return SimVarWriteResult(
        name=name, value_set=value, unit=resolved_unit, index=index
    )
```

`get_simvar_bulk`:

```python
@handle_simconnect_errors
@require_connection
async def get_simvar_bulk(
    variables: Annotated[
        list[dict],
        Field(description="Variables to read. Each dict takes 'name' and optional "
                          "'unit' and 'index'. Example: "
                          '[{"name": "PLANE_LATITUDE"}, '
                          '{"name": "ENG_N1_RPM", "index": 1, "unit": "percent"}]',
              min_length=1, max_length=100),
    ],
) -> SimVarBulkResult | ToolError:
    """Read several SimVars in one call.

    Results are keyed by 'NAME' or 'NAME:index'. A failure on one variable
    does not abort the others -- that entry carries an 'error' instead.
    """
    manager = SimConnectManager()
    requests = [(v["name"], v.get("unit"), v.get("index")) for v in variables]
    results = await manager.run_sync(lambda: manager.accessor.read_many(requests))
    return SimVarBulkResult(count=len(results), variables=results)
```

`search_simvars`:

```python
SIMVAR_COLUMNS = [
    ("name", "Name"),
    ("units", "Units"),
    ("settable", "Settable"),
    ("category", "Category"),
    ("description", "Description"),
]


@handle_simconnect_errors
async def search_simvars(
    keyword: Annotated[str, Field(description="Search term, e.g. 'altitude', 'engine'",
                                  min_length=1, max_length=100)],
    category: Annotated[
        str | None, Field(description="Restrict to one category, e.g. 'Aircraft Position'")
    ] = None,
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=200)] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description="Results to skip, for paging", ge=0)] = 0,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' for a compact table, 'json' for rows")
    ] = ResponseFormat.MARKDOWN,
) -> SearchResult | ToolError:
    """Search the SimVar catalog by keyword.

    Returns each variable's units and whether it is settable, so you can call
    get_simvar or set_simvar with the right arguments. Results are paginated.
    """
    rows = search_catalog(keyword, category)
    return build_search_result(
        rows, offset, limit, response_format, SIMVAR_COLUMNS,
        title=f"SimVars matching '{keyword}'",
        query=keyword, filters={"category": category},
    )
```

`list_simvar_categories`:

```python
@handle_simconnect_errors
async def list_simvar_categories() -> CategoryList | ToolError:
    """List every SimVar category with its variable count.

    Use this to discover category names for the 'category' filter on
    search_simvars.
    """
    catalog = load_catalog()
    categories = {name: len(entries) for name, entries in catalog.items()}
    return CategoryList(categories=categories, total_variables=sum(categories.values()))
```

`watch_simvar` — keep the Phase 0 body; change the signature to `Annotated` parameters with `interval_ms` `ge=50 le=10000` and `duration_s` `ge=1 le=30`, return `WatchResult(...)` with `samples=[WatchSample(**s) for s in samples]`, and return `error_from(e)` on the fail-fast path.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_simvars.py -v`
Expected: PASS

- [ ] **Step 5: Verify the generated schemas**

Run:
```bash
uv run python -c "
import asyncio, json
from simconnect_mcp.server import mcp
async def m():
    for t in await mcp.list_tools():
        if t.name in ('get_simvar','search_simvars'):
            print(t.name, '| output schema:', t.outputSchema is not None,
                  '| props:', sorted(t.inputSchema.get('properties',{})))
asyncio.run(m())"
```
Expected: both report `output schema: True` and flat property lists (`['index','name','unit']`, not `['params']`).

- [ ] **Step 6: Commit**

```bash
git add src/simconnect_mcp/tools/simvars.py tests/test_simvars.py
git commit -m "feat: SimVar tools get described inputs, output schemas, pagination"
```

---

### Task 4: Convert the event tools

**Files:**
- Modify: `src/simconnect_mcp/tools/events.py`
- Modify: `tests/test_events.py`

**Interfaces:**
- Consumes: `tools.models.EventResult`, `tools.formatting`
- Produces: `trigger_event`, `search_events`, `trigger_custom_event` with `Annotated` parameters and model returns.

- [ ] **Step 1: Write the failing tests**

```python
from simconnect_mcp.tools.formatting import ResponseFormat
from simconnect_mcp.tools.models import EventResult, SearchResult
from simconnect_mcp.tools.events import search_events, trigger_custom_event


async def test_trigger_event_returns_a_model(mock_simconnect):
    result = await trigger_event("PARKING_BRAKES")
    assert isinstance(result, EventResult)
    assert result.resolved_via == "catalog"


async def test_search_events_paginates_over_the_full_catalog(mock_simconnect):
    result = await search_events("set", limit=10, response_format=ResponseFormat.JSON)
    assert isinstance(result, SearchResult)
    assert result.page.total > 50, "should span the 994-event catalog, not 50 builtins"
    assert result.page.count == 10


async def test_search_events_defaults_to_markdown(mock_simconnect):
    result = await search_events("autopilot")
    assert result.markdown is not None


async def test_custom_event_without_mobiflight_returns_error(mock_simconnect):
    mock_simconnect["manager"]._mobiflight_available = False
    result = await trigger_custom_event("MobiFlight.TEST")
    assert result.error == "MOBIFLIGHT_NOT_AVAILABLE"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_events.py -v`
Expected: FAIL

- [ ] **Step 3: Convert the tools**

Add imports mirroring Task 3, define columns, and convert:

```python
EVENT_COLUMNS = [("name", "Event"), ("category", "Category"), ("description", "Description")]


@handle_simconnect_errors
@require_connection
async def trigger_event(
    name: Annotated[str, Field(description="Event name, e.g. 'PARKING_BRAKES', "
                                           "'AP_MASTER', 'THROTTLE_SET'",
                               min_length=1, max_length=128)],
    parameter: Annotated[
        int | None,
        Field(description="Integer parameter for events that take one. Negative "
                          "values are supported (e.g. AP_VS_VAR_SET_ENGLISH)."),
    ] = None,
) -> EventResult | ToolError:
    """Fire a SimConnect event.

    Resolves through the library's 994-event catalog first, then falls back to
    mapping the name directly, so third-party and newer MSFS events work too.
    """
    # ... Phase 0 body unchanged, returning:
    return EventResult(
        event=name, parameter=parameter, resolved_via=resolved_via,
        message=f"Event '{name}' triggered successfully",
    )
```

`search_events` follows the `search_simvars` shape exactly, with `EVENT_COLUMNS` and `title=f"Events matching '{keyword}'"`. `trigger_custom_event` returns `EventResult(..., custom=True)` and `ToolError(error="MOBIFLIGHT_NOT_AVAILABLE", ...)`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_events.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/tools/events.py tests/test_events.py
git commit -m "feat: event tools get described inputs, output schemas, pagination"
```

---

### Task 5: Convert the L-var tools and consolidate catalog browsing

`list_lvar_panels` and `list_lvar_catalogs` are two entry points into the same catalog data. They merge into `browse_lvar_catalog`.

**Files:**
- Modify: `src/simconnect_mcp/tools/lvars.py`
- Modify: `src/simconnect_mcp/data/catalog.py` (remove the `>= 50` cap in `search_catalog`)
- Modify: `tests/test_search.py`

**Interfaces:**
- Consumes: `data.catalog.search_catalog`, `list_panels`, `get_panel_variables`, `list_catalogs`
- Produces: `get_lvar`, `set_lvar`, `list_lvars`, `execute_calculator_code`, `search_lvars`, `browse_lvar_catalog(catalog=None, panel=None, limit, offset, response_format)`. `list_lvar_panels` and `list_lvar_catalogs` are deleted.

- [ ] **Step 1: Write the failing tests**

```python
from simconnect_mcp.tools.formatting import ResponseFormat
from simconnect_mcp.tools.lvars import browse_lvar_catalog, search_lvars


async def test_browse_with_no_arguments_lists_catalogs(mock_simconnect):
    result = await browse_lvar_catalog(response_format=ResponseFormat.JSON)
    assert result.catalogs is not None
    assert result.panels is None


async def test_browse_with_a_catalog_lists_its_panels(mock_simconnect):
    result = await browse_lvar_catalog(catalog="fenix_a320", response_format=ResponseFormat.JSON)
    assert result.panels is not None
    assert result.catalog == "fenix_a320"


async def test_browse_with_a_panel_lists_its_variables(mock_simconnect):
    result = await browse_lvar_catalog(
        catalog="fenix_a320", panel="Signs", response_format=ResponseFormat.JSON
    )
    assert result.variables is not None
    assert result.panel


async def test_browse_unknown_panel_returns_error(mock_simconnect):
    result = await browse_lvar_catalog(panel="NoSuchPanel")
    assert result.error == "PANEL_NOT_FOUND"


async def test_search_lvars_is_not_capped_at_fifty(mock_simconnect):
    """data.catalog.search_catalog used to return early at 50 matches."""
    result = await search_lvars("s", limit=10, response_format=ResponseFormat.JSON)
    assert result.page.total > 50
    assert result.page.count == 10
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_search.py -v`
Expected: FAIL — `browse_lvar_catalog` does not exist

- [ ] **Step 3: Remove the cap from the catalog search**

In `src/simconnect_mcp/data/catalog.py`, delete these two lines from `search_catalog`:

```python
            if len(results) >= 50:
                return results
```

The docstring gains: `Uncapped -- callers paginate.`

- [ ] **Step 4: Convert the L-var tools**

`search_lvars` keeps its Phase 0 body for catalog detection, then returns `build_search_result(...)` with:

```python
LVAR_COLUMNS = [
    ("name", "L-Var"),
    ("display_name", "Description"),
    ("category", "Category"),
    ("writable", "Writable"),
]
```

Replace `list_lvar_panels` and `list_lvar_catalogs` with:

```python
@handle_simconnect_errors
async def browse_lvar_catalog(
    catalog: Annotated[
        str | None,
        Field(description="Catalog key, e.g. 'fenix_a320', 'pmdg_737', 'pmdg_777'. "
                          "Omit to auto-detect from the loaded aircraft, or to "
                          "list all available catalogs."),
    ] = None,
    panel: Annotated[
        str | None,
        Field(description="Panel name to open, e.g. 'Signs', 'FCU', 'Electrical'. "
                          "Omit to list the panels in the catalog."),
    ] = None,
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=200)] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description="Results to skip", ge=0)] = 0,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' for a table, 'json' for rows")
    ] = ResponseFormat.MARKDOWN,
) -> CatalogBrowse | ToolError:
    """Browse the aircraft L-var catalogs.

    Three levels, narrowing as you supply arguments:
      * no arguments  -- every available aircraft catalog
      * catalog only  -- the panels in that catalog
      * catalog+panel -- the variables on that panel, with their valid values

    With no 'catalog', the loaded aircraft is auto-detected from its TITLE.
    """
    from simconnect_mcp.data.catalog import (
        detect_catalog, get_panel_variables, list_catalogs, list_panels,
    )

    catalog_key = catalog
    if catalog_key is None:
        title = await SimConnectManager().detect_aircraft_title()
        if title:
            catalog_key = detect_catalog(title)

    if panel:
        found = get_panel_variables(panel, catalog_key)
        if found is None:
            return ToolError(
                error="PANEL_NOT_FOUND",
                message=f"No panel matching '{panel}' found.",
                suggestion="Call browse_lvar_catalog without 'panel' to list available panels.",
            )
        rows, page = paginate(found["variables"], offset, limit)
        return CatalogBrowse(
            catalog=found["catalog"], panel=found["panel"], page=page,
            variables=None if response_format is ResponseFormat.MARKDOWN else rows,
            markdown=(render_table(rows, LVAR_COLUMNS, title=f"Panel: {found['panel']}")
                      if response_format is ResponseFormat.MARKDOWN else None),
        )

    if catalog_key:
        rows, page = paginate(list_panels(catalog_key), offset, limit)
        return CatalogBrowse(
            catalog=catalog_key, page=page,
            panels=None if response_format is ResponseFormat.MARKDOWN else rows,
            markdown=(render_table(
                rows, [("panel", "Panel"), ("variable_count", "Variables")],
                title=f"Panels in {catalog_key}")
                if response_format is ResponseFormat.MARKDOWN else None),
        )

    rows, page = paginate(list_catalogs(), offset, limit)
    return CatalogBrowse(
        catalog=None, page=page,
        catalogs=None if response_format is ResponseFormat.MARKDOWN else rows,
        markdown=(render_table(
            rows,
            [("key", "Key"), ("aircraft", "Aircraft"), ("variable_count", "Variables"),
             ("panel_count", "Panels")],
            title="Available aircraft L-var catalogs")
            if response_format is ResponseFormat.MARKDOWN else None),
    )
```

Change `_require_mobiflight()` to return `ToolError | None` rather than a dict, so callers can `return err` directly:

```python
def _require_mobiflight() -> ToolError | None:
    """Check MobiFlight availability. Returns a ToolError, or None if fine."""
    if SimConnectManager().mobiflight_available:
        return None
    return ToolError(
        error="MOBIFLIGHT_NOT_AVAILABLE",
        message="The MobiFlight WASM extension is not available; L-var "
                "operations require it.",
        suggestion="Install the MobiFlight WASM module in your MSFS Community "
                   "folder and reconnect with msfs_connect.",
    )
```

Convert `get_lvar` → `LVarValue`, `set_lvar` → `LVarWriteResult`, `execute_calculator_code` → `CalculatorResult`, each with `Annotated` parameters. `list_lvars` keeps its Phase 0 behaviour and returns `ToolError(error="NOT_IMPLEMENTED", ...)` until Phase 2 Task 4 implements it — do **not** leave it returning a fabricated success.

Also add an explicit `mode` parameter to `execute_calculator_code`, since the current read/execute heuristic misclassifies expressions like `(L:A) (L:B) max`:

```python
    mode: Annotated[
        Literal["auto", "read", "execute"],
        Field(description="'read' returns a value, 'execute' runs the code for effect. "
                          "'auto' guesses from the syntax, which is unreliable for "
                          "compound expressions."),
    ] = "auto",
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_search.py -v && uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/simconnect_mcp/tools/lvars.py src/simconnect_mcp/data/catalog.py tests/test_search.py
git commit -m "feat: consolidate L-var catalog browsing, remove the 50-result cap

list_lvar_panels and list_lvar_catalogs merge into browse_lvar_catalog.
execute_calculator_code gains an explicit read/execute mode."
```

---

### Task 6: Consolidate the aircraft snapshot tools

`get_aircraft_state` (~30 vars), `get_aircraft_position` (10) and `get_aircraft_systems` (17) differ only by variable list, and the first is close to a superset of the other two. Three tool entries for one capability.

**Files:**
- Modify: `src/simconnect_mcp/tools/aircraft.py` (full rewrite)
- Create: `tests/test_aircraft.py`

**Interfaces:**
- Consumes: `manager.accessor.read_many`
- Produces: `get_aircraft_snapshot(sections=None)` returning `AircraftSnapshot`. The three old tools are deleted.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_aircraft.py`:

```python
import pytest

from simconnect_mcp.tools.aircraft import SECTIONS, get_aircraft_snapshot
from simconnect_mcp.tools.models import AircraftSnapshot


async def test_snapshot_defaults_to_every_section(mock_simconnect):
    result = await get_aircraft_snapshot()
    assert isinstance(result, AircraftSnapshot)
    assert set(result.sections) == set(SECTIONS)


async def test_snapshot_can_be_narrowed_to_one_section(mock_simconnect):
    result = await get_aircraft_snapshot(sections=["position"])
    assert result.sections == ["position"]
    assert "PLANE_LATITUDE" in result.data
    assert "FUEL_TOTAL_QUANTITY" not in result.data


async def test_unknown_section_is_rejected_with_the_valid_list(mock_simconnect):
    result = await get_aircraft_snapshot(sections=["nonsense"])
    assert result.error == "INVALID_SECTION"
    assert "position" in result.suggestion


async def test_snapshot_reads_every_variable_in_one_batch(mock_simconnect):
    """One read_many call, not one read per variable."""
    await get_aircraft_snapshot(sections=["position", "engines"])
    assert mock_simconnect["accessor"].read_many.call_count == 1


async def test_sections_are_deduplicated(mock_simconnect):
    result = await get_aircraft_snapshot(sections=["position", "position"])
    assert result.sections == ["position"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_aircraft.py -v`
Expected: FAIL — `get_aircraft_snapshot` does not exist

- [ ] **Step 3: Rewrite the module**

Replace `src/simconnect_mcp/tools/aircraft.py` entirely:

```python
"""Aircraft state snapshots, grouped into selectable sections."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection
from simconnect_mcp.tools.models import AircraftSnapshot, ToolError

# (name, unit, index) per section. Units are explicit so the snapshot is
# reproducible regardless of catalog defaults.
SECTIONS: dict[str, list[tuple[str, str | None, int | None]]] = {
    "identity": [
        ("TITLE", None, None),
        ("ATC_TYPE", None, None),
        ("ATC_ID", None, None),
    ],
    "position": [
        ("PLANE_LATITUDE", "degrees", None),
        ("PLANE_LONGITUDE", "degrees", None),
        ("PLANE_ALTITUDE", "feet", None),
        ("PLANE_HEADING_DEGREES_TRUE", "degrees", None),
        ("PLANE_HEADING_DEGREES_MAGNETIC", "degrees", None),
        ("GROUND_ALTITUDE", "feet", None),
        ("SIM_ON_GROUND", "bool", None),
        ("AIRSPEED_INDICATED", "knots", None),
        ("AIRSPEED_TRUE", "knots", None),
        ("GROUND_VELOCITY", "knots", None),
        ("VERTICAL_SPEED", "feet per minute", None),
    ],
    "engines": [
        ("GENERAL_ENG_THROTTLE_LEVER_POSITION", "percent", 1),
        ("GENERAL_ENG_THROTTLE_LEVER_POSITION", "percent", 2),
        ("ENG_N1_RPM", "percent", 1),
        ("ENG_N1_RPM", "percent", 2),
        ("ENG_N2_RPM", "percent", 1),
        ("ENG_N2_RPM", "percent", 2),
        ("FUEL_TOTAL_QUANTITY", "gallons", None),
        ("FUEL_TOTAL_QUANTITY_WEIGHT", "pounds", None),
    ],
    "systems": [
        ("ELECTRICAL_MASTER_BATTERY", "bool", None),
        ("ELECTRICAL_AVIONICS_BUS_VOLTAGE", "volts", None),
        ("GENERAL_ENG_GENERATOR_ACTIVE", "bool", 1),
        ("GENERAL_ENG_GENERATOR_ACTIVE", "bool", 2),
        ("FLAPS_HANDLE_INDEX", "number", None),
        ("GEAR_HANDLE_POSITION", "bool", None),
        ("SPOILERS_HANDLE_POSITION", "percent", None),
        ("ELEVATOR_POSITION", "position", None),
        ("AILERON_POSITION", "position", None),
        ("RUDDER_POSITION", "position", None),
    ],
    "autopilot": [
        ("AUTOPILOT_MASTER", "bool", None),
        ("AUTOPILOT_HEADING_LOCK_DIR", "degrees", None),
        ("AUTOPILOT_ALTITUDE_LOCK_VAR", "feet", None),
        ("AUTOPILOT_VERTICAL_HOLD_VAR", "feet per minute", None),
        ("AUTOPILOT_AIRSPEED_HOLD_VAR", "knots", None),
    ],
    "environment": [
        ("AMBIENT_TEMPERATURE", "celsius", None),
        ("AMBIENT_WIND_VELOCITY", "knots", None),
        ("AMBIENT_WIND_DIRECTION", "degrees", None),
        ("BAROMETER_PRESSURE", "millibars", None),
        ("SIMULATION_RATE", "number", None),
        ("ZULU_TIME", "seconds", None),
        ("LOCAL_TIME", "seconds", None),
    ],
}


@handle_simconnect_errors
@require_connection
async def get_aircraft_snapshot(
    sections: Annotated[
        list[str] | None,
        Field(description="Sections to include: identity, position, engines, systems, "
                          "autopilot, environment. Omit for all of them."),
    ] = None,
) -> AircraftSnapshot | ToolError:
    """Read a snapshot of the current aircraft state.

    Narrow with 'sections' to keep the response small -- for example
    sections=['position'] for a position fix, or ['engines','systems'] when
    debugging a systems issue. All variables are read in one batched call.
    """
    if sections is None:
        chosen = list(SECTIONS)
    else:
        chosen = list(dict.fromkeys(sections))  # dedupe, preserve order
        unknown = [s for s in chosen if s not in SECTIONS]
        if unknown:
            return ToolError(
                error="INVALID_SECTION",
                message=f"Unknown section(s): {', '.join(unknown)}",
                suggestion=f"Valid sections are: {', '.join(SECTIONS)}.",
            )

    requests: list[tuple[str, str | None, int | None]] = []
    for section in chosen:
        requests.extend(SECTIONS[section])

    manager = SimConnectManager()
    data = await manager.run_sync(lambda: manager.accessor.read_many(requests))
    return AircraftSnapshot(sections=chosen, data=data)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_aircraft.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/tools/aircraft.py tests/test_aircraft.py
git commit -m "feat: merge three aircraft snapshot tools into one with sections

get_aircraft_state was close to a superset of position and systems;
three tool entries described one capability."
```

---

### Task 7: Convert the utility, facilities and PMDG tools

**Files:**
- Modify: `src/simconnect_mcp/tools/utilities.py`, `src/simconnect_mcp/tools/facilities.py`, `src/simconnect_mcp/tools/pmdg.py`
- Modify: `tests/test_utilities.py`, `tests/test_pmdg.py`, `tests/test_pmdg_ng3.py`

**Interfaces:**
- Consumes: `tools.models`
- Produces: `send_sim_text` → `TextResult`, `set_aircraft_position` → `PositionResult`, PMDG tools returning models. Facilities tools keep dict returns and gain a `NOT_IMPLEMENTED` `ToolError` until Phase 2 Task 2 — they must not report success while non-functional.

- [ ] **Step 1: Write the failing tests**

```python
from simconnect_mcp.tools.models import PositionResult, TextResult


async def test_send_sim_text_returns_a_model(mock_simconnect):
    result = await send_sim_text("hello")
    assert isinstance(result, TextResult)


async def test_set_position_returns_a_model(mock_simconnect):
    result = await set_aircraft_position(latitude=47.6, longitude=-122.3, on_ground=True)
    assert isinstance(result, PositionResult)
    assert result.on_ground is True


async def test_facilities_report_not_implemented_rather_than_faking_success(mock_simconnect):
    """These cannot work until Phase 2; they must not claim ok."""
    from simconnect_mcp.tools.facilities import get_nearby_airports

    result = await get_nearby_airports()
    assert result.error == "NOT_IMPLEMENTED"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_utilities.py -v`
Expected: FAIL

- [ ] **Step 3: Convert utilities**

`send_sim_text` gains `Annotated` parameters — `text` `min_length=1 max_length=200`, `duration_s` `ge=0.1 le=60`, `color` as a `Literal` over the eight colour names — and returns `TextResult(...)`. `set_aircraft_position` gains `latitude` `ge=-90 le=90`, `longitude` `ge=-180 le=180`, `altitude` `ge=-2000 le=275000`, `heading` `ge=0 lt=360`, `airspeed` `ge=0 le=2000`, `pitch`/`bank` `ge=-180 le=180`, and returns `PositionResult(...)`. With Pydantic validating latitude and longitude at the schema boundary, delete the manual range check added in Phase 0 Task 15 and its test.

- [ ] **Step 4: Make the facilities tools honest**

Replace both bodies in `src/simconnect_mcp/tools/facilities.py`:

```python
_NOT_IMPLEMENTED = ToolError(
    error="NOT_IMPLEMENTED",
    message=(
        "Facility lookup is not available yet. The SimConnect library's "
        "FacilitiesRequests only prints results to stdout and returns nothing, "
        "so this server implements its own facilities handler instead."
    ),
    suggestion=(
        "Use msfs_get_aircraft_snapshot(sections=['position']) for the current "
        "position, or an external navdata source for airport details."
    ),
)
```

and return it from both tools, keeping their signatures.

- [ ] **Step 5: Convert the PMDG tools**

Add `Annotated` parameters: `name` `min_length=1`, `variant` as `Literal["pmdg_777", "pmdg_737"] | None`, `cdu` `ge=0 le=2`, `parameter` unconstrained. Add PMDG result models to `tools/models.py`:

```python
class PmdgVarResult(OkModel):
    name: str
    value: float | int | str | None = None
    display_name: str
    category: str
    catalog: str
    variant_source: str | None = None
    value_description: str | None = None
    warning: str | None = None


class PmdgCduResult(OkModel):
    cdu: int
    cdu_name: str | None = None
    powered: bool
    rows: list[str] | None = None
    grid: list[list[dict]] | None = None
    catalog: str
    variant_source: str | None = None
    warning: str | None = None
```

Convert the returns; the existing bodies are unchanged. Also wrap the `resolve_pmdg_event` `ValueError` in `send_pmdg_event`:

```python
    try:
        dispatch = resolve_pmdg_event(event_name, parameter)
    except ValueError as e:
        return ToolError(
            error="PMDG_EVENT_NOT_FOUND",
            message=str(e),
            suggestion="Use msfs_search_lvars to find PMDG events for this aircraft.",
        )
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. The PMDG tests assert on dict keys; update them to attribute access.

- [ ] **Step 7: Commit**

```bash
git add src/simconnect_mcp/tools/utilities.py src/simconnect_mcp/tools/facilities.py src/simconnect_mcp/tools/pmdg.py src/simconnect_mcp/tools/models.py tests/
git commit -m "feat: convert utility, facilities and PMDG tools to models

Facilities now report NOT_IMPLEMENTED rather than an empty success."
```

---

### Task 8: Explicit registration, annotations, and the `msfs_` rename

`server.py` registers tools through `for tool_fn in [...]: mcp.tool()(tool_fn)`, which cannot carry per-tool names, titles, or annotations. Every tool is currently indistinguishable from a read-only one — including `execute_calculator_code`, which runs arbitrary RPN in the sim.

**Files:**
- Modify: `src/simconnect_mcp/server.py` (registration block, full rewrite)
- Create: `tests/test_registration.py`

**Interfaces:**
- Consumes: every tool function
- Produces: 26 tools registered as `msfs_*` with `ToolAnnotations`. Python function names stay unchanged; only the MCP-visible `name` changes.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_registration.py`:

```python
import pytest

from simconnect_mcp.server import mcp

WRITE_TOOLS = {
    "msfs_set_simvar",
    "msfs_set_lvar",
    "msfs_execute_calculator_code",
    "msfs_trigger_event",
    "msfs_trigger_custom_event",
    "msfs_send_pmdg_event",
    "msfs_set_aircraft_position",
}


async def _tools():
    return {t.name: t for t in await mcp.list_tools()}


async def test_every_tool_is_msfs_prefixed():
    for name in await _tools():
        assert name.startswith("msfs_"), f"{name} lacks the service prefix"


async def test_expected_tool_count():
    assert len(await _tools()) == 26


async def test_consolidated_tools_replaced_their_predecessors():
    names = await _tools()
    assert "msfs_get_aircraft_snapshot" in names
    assert "msfs_browse_lvar_catalog" in names
    for gone in ("msfs_get_aircraft_state", "msfs_get_aircraft_position",
                 "msfs_get_aircraft_systems", "msfs_list_lvar_panels",
                 "msfs_list_lvar_catalogs"):
        assert gone not in names


async def test_every_tool_has_annotations_and_a_title():
    for name, tool in (await _tools()).items():
        assert tool.annotations is not None, f"{name} has no annotations"
        assert tool.annotations.title, f"{name} has no title"


async def test_write_tools_are_marked_destructive():
    tools = await _tools()
    for name in WRITE_TOOLS:
        ann = tools[name].annotations
        assert ann.readOnlyHint is False, f"{name} claims to be read-only"
        assert ann.destructiveHint is True, f"{name} is not marked destructive"


async def test_read_tools_are_marked_read_only():
    tools = await _tools()
    for name in ("msfs_get_simvar", "msfs_search_simvars", "msfs_get_aircraft_snapshot",
                 "msfs_search_events", "msfs_get_pmdg_cdu"):
        assert tools[name].annotations.readOnlyHint is True


async def test_every_tool_declares_an_output_schema():
    for name, tool in (await _tools()).items():
        assert tool.outputSchema is not None, f"{name} has no outputSchema"


async def test_no_tool_hides_its_arguments_behind_a_params_object():
    """A single Pydantic model parameter collapses the schema to {'params': ...}."""
    for name, tool in (await _tools()).items():
        assert list(tool.inputSchema.get("properties", {})) != ["params"], name
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_registration.py -v`
Expected: FAIL — tools are unprefixed and unannotated

- [ ] **Step 3: Rewrite the registration block**

In `src/simconnect_mcp/server.py`, delete the `for tool_fn in [...]` loop and the three inline `@mcp.tool()` connection tools, and replace with an explicit table. Add `from mcp.types import ToolAnnotations`.

```python
def _register(fn, name: str, title: str, *, read_only: bool, idempotent: bool = False,
              destructive: bool | None = None) -> None:
    """Register one tool with explicit behaviour annotations.

    destructiveHint is only meaningful when readOnlyHint is false, so it
    defaults to the inverse of read_only.
    """
    mcp.tool(
        name=name,
        title=title,
        annotations=ToolAnnotations(
            title=title,
            readOnlyHint=read_only,
            destructiveHint=(not read_only) if destructive is None else destructive,
            idempotentHint=idempotent,
            openWorldHint=True,  # every tool talks to a live external simulator
        ),
    )(fn)


# --- Connection ---
_register(connect_to_sim, "msfs_connect", "Connect to MSFS",
          read_only=False, destructive=False, idempotent=True)
_register(disconnect_from_sim, "msfs_disconnect", "Disconnect from MSFS",
          read_only=False, destructive=False, idempotent=True)
_register(get_connection_status, "msfs_get_connection_status", "Get Connection Status",
          read_only=True, idempotent=True)

# --- SimVars ---
_register(get_simvar, "msfs_get_simvar", "Read SimVar", read_only=True, idempotent=True)
_register(set_simvar, "msfs_set_simvar", "Write SimVar", read_only=False, idempotent=True)
_register(get_simvar_bulk, "msfs_get_simvars_bulk", "Read Multiple SimVars",
          read_only=True, idempotent=True)
_register(search_simvars, "msfs_search_simvars", "Search SimVars",
          read_only=True, idempotent=True)
_register(list_simvar_categories, "msfs_list_simvar_categories", "List SimVar Categories",
          read_only=True, idempotent=True)
_register(watch_simvar, "msfs_watch_simvar", "Watch SimVar Over Time",
          read_only=True, idempotent=False)

# --- Events ---
_register(trigger_event, "msfs_trigger_event", "Trigger Event", read_only=False)
_register(search_events, "msfs_search_events", "Search Events",
          read_only=True, idempotent=True)
_register(trigger_custom_event, "msfs_trigger_custom_event", "Trigger Custom Event",
          read_only=False)

# --- L-vars ---
_register(get_lvar, "msfs_get_lvar", "Read L-Var", read_only=True, idempotent=True)
_register(set_lvar, "msfs_set_lvar", "Write L-Var", read_only=False, idempotent=True)
_register(list_lvars, "msfs_list_lvars", "List Aircraft L-Vars",
          read_only=True, idempotent=True)
_register(execute_calculator_code, "msfs_execute_calculator_code", "Execute RPN Code",
          read_only=False)
_register(search_lvars, "msfs_search_lvars", "Search L-Vars", read_only=True, idempotent=True)
_register(browse_lvar_catalog, "msfs_browse_lvar_catalog", "Browse L-Var Catalogs",
          read_only=True, idempotent=True)

# --- Aircraft ---
_register(get_aircraft_snapshot, "msfs_get_aircraft_snapshot", "Get Aircraft Snapshot",
          read_only=True, idempotent=True)

# --- Facilities ---
_register(get_nearby_airports, "msfs_get_nearby_airports", "Get Nearby Airports",
          read_only=True, idempotent=True)
_register(get_facility_info, "msfs_get_facility_info", "Get Facility Info",
          read_only=True, idempotent=True)

# --- Utilities ---
_register(send_sim_text, "msfs_send_sim_text", "Show Text In Sim",
          read_only=False, destructive=False)
_register(set_aircraft_position, "msfs_set_aircraft_position", "Reposition Aircraft",
          read_only=False, idempotent=True)

# --- PMDG ---
_register(get_pmdg_var, "msfs_get_pmdg_var", "Read PMDG Variable",
          read_only=True, idempotent=True)
_register(get_pmdg_cdu, "msfs_get_pmdg_cdu", "Read PMDG CDU Screen",
          read_only=True, idempotent=True)
_register(send_pmdg_event, "msfs_send_pmdg_event", "Send PMDG Event", read_only=False)
```

Move the three connection tools out of `server.py` into `src/simconnect_mcp/tools/connection_tools.py` as plain `async def` functions returning `ConnectionStatus | ToolError`, so `server.py` holds registration only. Import them alongside the rest.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_registration.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Inspect the registered surface by hand**

Run:
```bash
uv run python -c "
import asyncio
from simconnect_mcp.server import mcp
async def m():
    for t in sorted(await mcp.list_tools(), key=lambda x: x.name):
        a = t.annotations
        print(f'{t.name:34} ro={a.readOnlyHint!s:5} destructive={a.destructiveHint!s:5} {a.title}')
asyncio.run(m())"
```
Expected: 26 rows; every write tool shows `ro=False destructive=True`.

- [ ] **Step 6: Run the full suite and ruff**

Run: `uv run pytest -q && uv run ruff check src/ tests/`
Expected: PASS, clean

- [ ] **Step 7: Commit**

```bash
git add src/simconnect_mcp/server.py src/simconnect_mcp/tools/connection_tools.py tests/test_registration.py
git commit -m "feat: explicit tool registration with annotations and msfs_ prefix

The registration loop could not carry per-tool metadata, so every tool
looked read-only to clients -- including execute_calculator_code."
```

---

### Task 9: Register the orphaned PMDG docs and fix the section filter

`src/simconnect_mcp/docs/` contains `pmdg_777.md` (19 KB) and `pmdg_737.md`, neither of which is reachable — `_DOC_FILES` does not list them and no resource serves them. The section-filter logic is also duplicated between two resources and has a bug: a second `## ` heading that also matches the category re-enters the section instead of ending it.

**Files:**
- Modify: `src/simconnect_mcp/resources/documentation.py`
- Create: `tests/test_documentation.py`

**Interfaces:**
- Consumes: nothing
- Produces: `extract_section(content, category) -> str`; resources at `simconnect://docs/pmdg/{variant}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_documentation.py`:

```python
import pytest

from simconnect_mcp.resources.documentation import _read_doc, extract_section

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


@pytest.mark.parametrize("name", ["overview", "simvars", "events", "rpn", "lvars",
                                  "best-practices", "pmdg-777", "pmdg-737"])
def test_every_registered_doc_file_exists(name):
    content = _read_doc(name)
    assert "not yet available" not in content
    assert len(content) > 500


async def test_pmdg_docs_are_registered_as_resources():
    from simconnect_mcp.server import mcp

    uris = {str(t.uriTemplate) for t in await mcp.list_resource_templates()}
    uris |= {str(r.uri) for r in await mcp.list_resources()}
    assert any("pmdg" in u for u in uris)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_documentation.py -v`
Expected: FAIL — `extract_section` does not exist; PMDG docs are unregistered

- [ ] **Step 3: Rewrite the module**

Replace `src/simconnect_mcp/resources/documentation.py`:

```python
"""Serve the embedded SimConnect documentation as MCP resources."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

DOCS_DIR = Path(__file__).parent.parent / "docs"

_DOC_FILES = {
    "overview": "overview.md",
    "simvars": "simvars.md",
    "events": "events.md",
    "rpn": "rpn.md",
    "lvars": "lvars.md",
    "best-practices": "best_practices.md",
    "pmdg-777": "pmdg_777.md",
    "pmdg-737": "pmdg_737.md",
}


def extract_section(content: str, category: str) -> str:
    """Return the '## ' section whose heading contains `category`.

    Returns the whole document for 'all' or when nothing matches.  The section
    ends at the next '## ' heading of any kind -- the previous implementation
    re-entered the section whenever a later heading also matched the category
    (e.g. 'Engine Limits' for category 'engine').
    """
    if category == "all":
        return content

    needle = category.lower()
    lines = content.split("\n")
    collected: list[str] = []
    in_section = False

    for line in lines:
        if line.startswith("## "):
            if in_section:
                break  # any next heading ends the section
            if needle in line.lower():
                in_section = True
        if in_section:
            collected.append(line)

    return "\n".join(collected) if collected else content


def _read_doc(name: str) -> str:
    """Read a bundled documentation file."""
    path = DOCS_DIR / _DOC_FILES.get(name, f"{name}.md")
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"Documentation for '{name}' not yet available."


def register_doc_resources(mcp: FastMCP) -> None:
    """Register documentation resources on the MCP server."""

    @mcp.resource("simconnect://docs/overview", mime_type="text/markdown",
                  title="SimConnect Overview")
    def docs_overview() -> str:
        """SimConnect architecture and key concepts."""
        return _read_doc("overview")

    @mcp.resource("simconnect://docs/simvars/{category}", mime_type="text/markdown",
                  title="SimVar Documentation")
    def docs_simvars(category: str) -> str:
        """SimVar documentation. Use category='all' for the full listing."""
        return extract_section(_read_doc("simvars"), category)

    @mcp.resource("simconnect://docs/events/{category}", mime_type="text/markdown",
                  title="Event Documentation")
    def docs_events(category: str) -> str:
        """SimConnect event documentation. Use category='all' for everything."""
        return extract_section(_read_doc("events"), category)

    @mcp.resource("simconnect://docs/rpn", mime_type="text/markdown",
                  title="RPN Calculator Syntax")
    def docs_rpn() -> str:
        """RPN calculator syntax guide."""
        return _read_doc("rpn")

    @mcp.resource("simconnect://docs/lvars", mime_type="text/markdown",
                  title="L-Var Guide")
    def docs_lvars() -> str:
        """L-var usage for add-on development."""
        return _read_doc("lvars")

    @mcp.resource("simconnect://docs/best-practices", mime_type="text/markdown",
                  title="SimConnect Best Practices")
    def docs_best_practices() -> str:
        """Common pitfalls, performance tips, and testing guidance."""
        return _read_doc("best-practices")

    @mcp.resource("simconnect://docs/pmdg/{variant}", mime_type="text/markdown",
                  title="PMDG SDK Reference")
    def docs_pmdg(variant: str) -> str:
        """PMDG SDK reference. Use variant='777' or '737'."""
        key = f"pmdg-{variant.strip().lstrip('b').lower()}"
        if key not in _DOC_FILES:
            return (
                f"No PMDG documentation for '{variant}'. "
                "Available variants: 777, 737."
            )
        return _read_doc(key)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_documentation.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and ruff**

Run: `uv run pytest -q && uv run ruff check src/ tests/`
Expected: PASS, clean

- [ ] **Step 6: Commit**

```bash
git add src/simconnect_mcp/resources/documentation.py tests/test_documentation.py
git commit -m "feat: register PMDG docs as resources, fix section extraction

pmdg_777.md and pmdg_737.md shipped in the package but no resource
served them. The section filter re-entered on a second matching heading."
```

---

## Phase 1 Exit Criteria

- [ ] `uv run pytest` passes; `uv run ruff check src/ tests/` is clean
- [ ] `mcp.list_tools()` returns exactly 26 tools, every one `msfs_`-prefixed
- [ ] Every tool has `ToolAnnotations` with a title, and an `outputSchema`
- [ ] No tool's `inputSchema.properties` is just `{"params": ...}`
- [ ] Every write tool reports `readOnlyHint=False, destructiveHint=True`
- [ ] Every search and browse tool accepts `limit`/`offset` and returns `total`/`has_more`
- [ ] `msfs_list_lvars` and both facilities tools return `NOT_IMPLEMENTED`, not a fabricated success
- [ ] Behaviour is otherwise unchanged from Phase 0

Proceed to `2026-08-29-mcp-modernization-phase2-capability.md`.
