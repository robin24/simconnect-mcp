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
    needle = category.lower()
    if needle == "all":
        return content

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

    @mcp.resource(
        "simconnect://docs/overview", mime_type="text/markdown", title="SimConnect Overview"
    )
    def docs_overview() -> str:
        """SimConnect architecture and key concepts."""
        return _read_doc("overview")

    @mcp.resource(
        "simconnect://docs/simvars/{category}",
        mime_type="text/markdown",
        title="SimVar Documentation",
    )
    def docs_simvars(category: str) -> str:
        """SimVar documentation. Use category='all' for the full listing."""
        return extract_section(_read_doc("simvars"), category)

    @mcp.resource(
        "simconnect://docs/events/{category}",
        mime_type="text/markdown",
        title="Event Documentation",
    )
    def docs_events(category: str) -> str:
        """SimConnect event documentation. Use category='all' for everything."""
        return extract_section(_read_doc("events"), category)

    @mcp.resource("simconnect://docs/rpn", mime_type="text/markdown", title="RPN Calculator Syntax")
    def docs_rpn() -> str:
        """RPN calculator syntax guide."""
        return _read_doc("rpn")

    @mcp.resource("simconnect://docs/lvars", mime_type="text/markdown", title="L-Var Guide")
    def docs_lvars() -> str:
        """L-var usage for add-on development."""
        return _read_doc("lvars")

    @mcp.resource(
        "simconnect://docs/best-practices",
        mime_type="text/markdown",
        title="SimConnect Best Practices",
    )
    def docs_best_practices() -> str:
        """Common pitfalls, performance tips, and testing guidance."""
        return _read_doc("best-practices")

    @mcp.resource(
        "simconnect://docs/pmdg/{variant}", mime_type="text/markdown", title="PMDG SDK Reference"
    )
    def docs_pmdg(variant: str) -> str:
        """PMDG SDK reference. Use variant='777' or '737' (either bare or
        with a leading 'B', case-insensitive -- PMDG's own product naming is
        'B737'/'B777')."""
        key = f"pmdg-{variant.strip().lower().lstrip('b')}"
        if key not in _DOC_FILES:
            return (
                f"No PMDG documentation for '{variant}'. "
                "Available variants: 777, 737."
            )
        return _read_doc(key)
