"""Pagination and response rendering shared by every search and browse tool.

Markdown is the default for catalog listings because a table of a few hundred
variables costs materially less context than the equivalent JSON.  Telemetry
reads stay structured -- markdown-formatting numbers an agent computes on
makes them harder to use, not easier.
"""
from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Any

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
