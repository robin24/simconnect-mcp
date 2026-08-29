"""Test fixtures for SimConnect MCP tests."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.data.simvar_catalog import is_string_var, resolve_unit


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset SimConnectManager singleton between tests."""
    SimConnectManager.reset()
    yield
    SimConnectManager.reset()


@pytest.fixture
def mock_simconnect():
    """Mock the SimConnect library for testing without MSFS."""
    mock_sm = MagicMock()
    mock_sm.paused = False
    mock_sm.running = True

    mock_aq = MagicMock()
    mock_ae = MagicMock()

    # Default SimVar responses
    simvar_values = {
        "PLANE_LATITUDE": 47.6062,
        "PLANE_LONGITUDE": -122.3321,
        "PLANE_ALTITUDE": 35000.0,
        "AIRSPEED_INDICATED": 250.0,
        "GROUND_VELOCITY": 450.0,
        "VERTICAL_SPEED": 0.0,
        "PLANE_HEADING_DEGREES_TRUE": 180.0,
        "PLANE_HEADING_DEGREES_MAGNETIC": 175.0,
        "GROUND_ALTITUDE": 0.0,
        "SIM_ON_GROUND": 0.0,
        "TITLE": b"Boeing 747-8i",
        "ATC_TYPE": b"B748",
        "ATC_ID": b"N12345",
        "AUTOPILOT_MASTER": 1.0,
        "ELECTRICAL_MASTER_BATTERY": 1.0,
        "FUEL_TOTAL_QUANTITY_WEIGHT": 50000.0,
        "SIMULATION_RATE": 1.0,
    }
    mock_aq.get.side_effect = lambda key: simvar_values.get(key.split(":")[0])
    mock_aq.set.return_value = None

    # Mock event find
    mock_event = MagicMock()
    mock_ae.find.return_value = mock_event

    def _decode(name: str, raw):
        """Mirror the real SimVarAccessor: string variables are decoded to
        `str` before being returned (verified against a live sim, where
        `read("TITLE")` returns `'777F'`, not `b'777F'`). The raw `aq` mock
        deliberately keeps returning `bytes` for these -- that is what the
        underlying AircraftRequests table actually does -- so only the
        accessor mock's lambdas route through this.
        """
        if is_string_var(name) and isinstance(raw, bytes):
            return raw.decode("ascii", errors="replace").strip()
        return raw

    with patch.dict(sys.modules, {
        "SimConnect": MagicMock(
            SimConnect=MagicMock(return_value=mock_sm),
            AircraftRequests=MagicMock(return_value=mock_aq),
            AircraftEvents=MagicMock(return_value=mock_ae),
            FacilitiesRequests=MagicMock(),
        ),
        "SimConnect.Constants": MagicMock(),
        "SimConnect.Enum": MagicMock(),
        "SimConnect.RequestList": MagicMock(),
    }):
        manager = SimConnectManager()
        manager.sm = mock_sm
        manager.aq = mock_aq
        manager.ae = mock_ae
        manager._state = manager._state.__class__("connected")

        mock_accessor = MagicMock()
        mock_accessor.read.side_effect = lambda name, unit=None, index=None, timeout=2.0: (
            _decode(name, simvar_values.get(name.split(":")[0]))
        )
        mock_accessor.read_many.side_effect = lambda reqs, timeout=2.0: {
            (n if i is None else f"{n}:{i}"): {
                "value": _decode(n, simvar_values.get(n.split(":")[0])),
                "unit": resolve_unit(n, u)
            }
            for n, u, i in reqs
        }
        manager.accessor = mock_accessor

        yield {
            "manager": manager,
            "sm": mock_sm,
            "aq": mock_aq,
            "ae": mock_ae,
            "event": mock_event,
            "simvar_values": simvar_values,
            "accessor": mock_accessor,
        }
