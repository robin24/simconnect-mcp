"""Serve embedded SimConnect documentation as MCP resources."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

DOCS_DIR = Path(__file__).parent.parent / "docs"

# Map resource URIs to doc files
_DOC_FILES = {
    "overview": "overview.md",
    "simvars": "simvars.md",
    "events": "events.md",
    "rpn": "rpn.md",
    "lvars": "lvars.md",
    "best-practices": "best_practices.md",
}


def register_doc_resources(mcp: FastMCP) -> None:
    """Register documentation resources on the MCP server."""

    @mcp.resource("simconnect://docs/overview")
    def docs_overview() -> str:
        """SimConnect architecture and key concepts."""
        return _read_doc("overview")

    @mcp.resource("simconnect://docs/simvars/{category}")
    def docs_simvars(category: str) -> str:
        """SimVar documentation. Use category='all' for the full listing."""
        content = _read_doc("simvars")
        if category != "all":
            # Filter to section matching category if possible
            lines = content.split("\n")
            section_lines = []
            in_section = False
            for line in lines:
                if line.startswith("## ") and category.lower() in line.lower():
                    in_section = True
                elif line.startswith("## ") and in_section:
                    break
                if in_section:
                    section_lines.append(line)
            if section_lines:
                return "\n".join(section_lines)
        return content

    @mcp.resource("simconnect://docs/events/{category}")
    def docs_events(category: str) -> str:
        """SimConnect event documentation by category."""
        content = _read_doc("events")
        if category != "all":
            lines = content.split("\n")
            section_lines = []
            in_section = False
            for line in lines:
                if line.startswith("## ") and category.lower() in line.lower():
                    in_section = True
                elif line.startswith("## ") and in_section:
                    break
                if in_section:
                    section_lines.append(line)
            if section_lines:
                return "\n".join(section_lines)
        return content

    @mcp.resource("simconnect://docs/rpn")
    def docs_rpn() -> str:
        """RPN calculator syntax guide."""
        return _read_doc("rpn")

    @mcp.resource("simconnect://docs/lvars")
    def docs_lvars() -> str:
        """L-var usage for add-on development."""
        return _read_doc("lvars")

    @mcp.resource("simconnect://docs/best-practices")
    def docs_best_practices() -> str:
        """Common pitfalls, performance tips, and testing guidance."""
        return _read_doc("best-practices")


def _read_doc(name: str) -> str:
    """Read a documentation file, returning its content or a fallback."""
    filename = _DOC_FILES.get(name, f"{name}.md")
    path = DOCS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"Documentation for '{name}' not yet available."
