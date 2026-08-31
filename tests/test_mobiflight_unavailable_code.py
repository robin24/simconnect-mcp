"""Every "the MobiFlight WASM bridge is not there" path must report the
same error code.

Before this fix, tools/pmdg.py's _ensure_pmdg_manager used a distinct
MOBIFLIGHT_REQUIRED code while every other such site --
tools/lvars.py's _require_mobiflight (get_lvar, list_lvars,
execute_calculator_code), tools/events.py's trigger_custom_event, and
tools/pmdg.py's own send_pmdg_event "rotor_brake" branch -- used
MOBIFLIGHT_NOT_AVAILABLE. An agent that learned to branch on the common
code would silently miss msfs_get_pmdg_var and msfs_get_pmdg_cdu, the two
tools routed exclusively through _ensure_pmdg_manager.

Parametrized over one representative tool call per distinct call site
(not just per underlying helper), so a fifth site added later has to be
added to SCENARIOS below or this test cannot see it drift.
"""
from __future__ import annotations

import pytest

from simconnect_mcp.tools.events import trigger_custom_event
from simconnect_mcp.tools.lvars import execute_calculator_code, get_lvar, list_lvars
from simconnect_mcp.tools.models import ToolError
from simconnect_mcp.tools.pmdg import get_pmdg_cdu, get_pmdg_var, send_pmdg_event


async def _lvars_get_lvar(mock_simconnect):
    # mock_simconnect leaves _mobiflight_available False by default.
    return await get_lvar("A32NX_TEST")


async def _lvars_list_lvars(mock_simconnect):
    return await list_lvars()


async def _lvars_execute_calculator_code(mock_simconnect):
    return await execute_calculator_code("(A:PLANE ALTITUDE,feet)")


async def _events_trigger_custom_event(mock_simconnect):
    return await trigger_custom_event("MobiFlight.Custom")


async def _pmdg_send_pmdg_event_rotor_brake(mock_simconnect):
    # EVT_OH_ELEC_BATTERY_SWITCH resolves to the "rotor_brake" dispatch
    # method (a standard cockpit toggle, not a direct-set MCP value) --
    # the one send_pmdg_event branch that checks mobiflight_available
    # directly instead of going through _ensure_pmdg_manager.
    return await send_pmdg_event("EVT_OH_ELEC_BATTERY_SWITCH", variant="pmdg_777")


def _remove_client_data_support(mock_simconnect):
    # MagicMock auto-creates any attribute accessed on it, so
    # register_client_data_handler must be explicitly deleted to simulate
    # the plain-SimConnect fallback (see connection.py: only a
    # SimConnectDispatcher/SimConnectMobiFlight sm object has this method).
    del mock_simconnect["sm"].register_client_data_handler


async def _pmdg_ensure_manager_via_get_pmdg_var(mock_simconnect):
    _remove_client_data_support(mock_simconnect)
    return await get_pmdg_var("ELEC_Battery_Sw_ON", variant="pmdg_777")


async def _pmdg_ensure_manager_via_get_pmdg_cdu(mock_simconnect):
    _remove_client_data_support(mock_simconnect)
    return await get_pmdg_cdu(cdu=0, variant="pmdg_777")


SCENARIOS = [
    ("lvars.get_lvar", _lvars_get_lvar),
    ("lvars.list_lvars", _lvars_list_lvars),
    ("lvars.execute_calculator_code", _lvars_execute_calculator_code),
    ("events.trigger_custom_event", _events_trigger_custom_event),
    ("pmdg.send_pmdg_event[rotor_brake]", _pmdg_send_pmdg_event_rotor_brake),
    ("pmdg._ensure_pmdg_manager via get_pmdg_var", _pmdg_ensure_manager_via_get_pmdg_var),
    ("pmdg._ensure_pmdg_manager via get_pmdg_cdu", _pmdg_ensure_manager_via_get_pmdg_cdu),
]


@pytest.mark.parametrize("name, scenario", SCENARIOS, ids=[s[0] for s in SCENARIOS])
async def test_every_mobiflight_unavailable_path_uses_the_same_code(
    mock_simconnect, name, scenario
):
    result = await scenario(mock_simconnect)

    assert isinstance(result, ToolError), f"{name} returned {result!r}, not a ToolError"
    assert result.error == "MOBIFLIGHT_NOT_AVAILABLE", (
        f"{name} returned error={result.error!r}, not the unified MOBIFLIGHT_NOT_AVAILABLE code"
    )
