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
