import re
from pathlib import Path

import simconnect_mcp
from simconnect_mcp.server import mcp

# Anything shaped like a tool name in agent-facing text -- i.e.
# msfs_-prefixed -- is a claim that an agent can call that tool. This
# pattern is deliberately narrow: it must not match bare pre-rename names
# like "get_simvar" that may legitimately remain in prose describing
# internals (a Python function name, a docstring), since those aren't
# claims about the MCP surface.
_TOOL_NAME_PATTERN = re.compile(r"\bmsfs_[a-z][a-z0-9_]*\b")

# Established by grepping the tree when this test was written: 58 msfs_
# references across the 8 docs .md files + templates.py, plus 4 more
# hardcoded into suggestion= strings under tools/ (20 files total). These
# are lower bounds, not exact counts, so legitimate future growth doesn't
# break the test -- but Path.glob() on a renamed/missing directory returns
# [] with no error, so without a floor a shrunk-to-nothing scan would pass
# just as silently as a healthy one. See test_docs_and_prompts_only_... below.
_MINIMUM_EXPECTED_FILES = 20
_MINIMUM_EXPECTED_MATCHES = 62


def _files_that_can_reference_a_tool() -> list[Path]:
    """Every place agent-facing text can name a tool: the embedded docs
    served as MCP resources, the prompt templates module, and the tool
    source itself -- a handful of hardcoded msfs_* names live in
    suggestion= strings there too (e.g. "reconnect with msfs_connect")."""
    pkg_dir = Path(simconnect_mcp.__file__).parent
    files = sorted((pkg_dir / "docs").glob("*.md"))
    files.append(pkg_dir / "prompts" / "templates.py")
    files.extend(sorted((pkg_dir / "tools").rglob("*.py")))
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
    """Docs, prompt templates, and hardcoded suggestion= strings in tool
    source all tell an agent (or a human reading an error message) which
    tool to call by name. A stale reference -- a rename that missed a
    file, a tool consolidated away later -- would point at something that
    no longer exists. Scanning every such file for msfs_-shaped names and
    checking each is actually registered turns the one-time rename sweep
    into a standing guarantee.

    The two floor assertions below are load-bearing, not decoration: an
    earlier version of this test asserted nothing about how many files or
    matches it found, so when the docs directory was renamed out from
    under it (reproducing that exact failure), `Path.glob()` silently
    returned `[]`, the loop below ran zero times, and the test passed at
    0-and-0 even with a fabricated unregistered tool name planted inside.
    """
    names = await _tools()
    files = _files_that_can_reference_a_tool()
    assert len(files) >= _MINIMUM_EXPECTED_FILES, (
        f"expected to scan at least {_MINIMUM_EXPECTED_FILES} files, found "
        f"only {len(files)} ({[str(f) for f in files]}) -- did a directory "
        f"get renamed or moved out from under this test?"
    )

    total_matches = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        matches = _TOOL_NAME_PATTERN.findall(text)
        total_matches += len(matches)
        for match in matches:
            assert match in names, f"{path} references unknown tool {match!r}"

    assert total_matches >= _MINIMUM_EXPECTED_MATCHES, (
        f"expected at least {_MINIMUM_EXPECTED_MATCHES} msfs_-shaped tool "
        f"references across docs/prompts/tool source, found only "
        f"{total_matches} -- the scan may be silently covering less than "
        f"it used to"
    )
