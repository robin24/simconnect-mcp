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
