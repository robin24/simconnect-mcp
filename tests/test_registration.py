import re
from pathlib import Path

import simconnect_mcp
from simconnect_mcp.server import mcp

# Anything shaped like a tool name in the embedded docs/prompts -- i.e.
# msfs_-prefixed -- is a claim that an agent can call that tool. This
# pattern is deliberately narrow: it must not match bare pre-rename names
# like "get_simvar" that may legitimately remain in prose describing
# internals (a Python function name, a docstring), since those aren't
# claims about the MCP surface.
_TOOL_NAME_PATTERN = re.compile(r"\bmsfs_[a-z][a-z0-9_]*\b")


def _doc_and_prompt_files() -> list[Path]:
    """The embedded docs served as MCP resources, plus the prompt templates
    module -- the two places prose can name a tool for an agent to call."""
    pkg_dir = Path(simconnect_mcp.__file__).parent
    files = sorted((pkg_dir / "docs").glob("*.md"))
    files.append(pkg_dir / "prompts" / "templates.py")
    return files


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


async def test_docs_and_prompts_only_reference_real_tool_names():
    """The embedded docs and prompt templates tell an agent which tools to
    call by name. A stale reference -- a rename that missed a file, or a
    tool consolidated away later -- would ship documentation instructing
    the agent to call something that no longer exists. Scanning every
    doc/prompt file for msfs_-shaped names and checking each is actually
    registered turns the one-time rename sweep into a standing guarantee.
    """
    names = await _tools()
    for path in _doc_and_prompt_files():
        text = path.read_text(encoding="utf-8")
        for match in _TOOL_NAME_PATTERN.findall(text):
            assert match in names, f"{path.name} references unknown tool {match!r}"
