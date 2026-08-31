"""Test fixtures for SimConnect MCP tests."""

from __future__ import annotations

import sys
from unittest.mock import DEFAULT, MagicMock, patch

import pytest

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.data.simvar_catalog import is_string_var, resolve_unit
from simconnect_mcp.simvar_access import MAX_BATCH_BUDGET, SimVarTimeoutError, values_match


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

    # trigger_event's catalog/mapped branches, PmdgDataManager.send_control,
    # and create_ai_object all correlate a PendingRequest against
    # mock_sm.registry -- either by request id (resolved by resolve_data(),
    # as SIMOBJECT_DATA/ASSIGNED_OBJECT_ID would be) or by send id (resolved
    # by resolve_exception(), as a SimConnect exception would be). Nothing
    # else here simulates the dispatch thread that would normally deliver
    # either, so left unconfigured, every one of those calls would genuinely
    # block for its full real wait window on every single test that reaches
    # it -- up to 0.2s for the send-id pattern, or create_ai_object's
    # multi-second reply timeout for the request-id one. Auto-resolving
    # here, immediately and without an exception, matches the common/success
    # case for both patterns and keeps the default fixture fast. A test that
    # specifically wants rejection, a timeout, or "no registry available"
    # replaces mock_sm.registry (or deletes the attribute) itself, exactly
    # as the correlation-focused tests in test_events.py/test_pmdg.py/
    # test_pmdg_ng3.py/test_flight.py already do -- that replacement fully
    # shadows the side effects below for those tests.
    MOCK_ASSIGNED_OBJECT_ID = 4242

    def _auto_register(req):
        if req.request_id is not None:
            req.value = MOCK_ASSIGNED_OBJECT_ID
            req.resolved = True
            req.done.set()

    def _auto_bind_send_id(req, send_id, _locked=False):
        req.done.set()

    mock_sm.registry.register.side_effect = _auto_register
    mock_sm.registry.bind_send_id.side_effect = _auto_bind_send_id

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
        "ATC_MODEL": b"ATCCOM.AC_MODEL B747.0.text",
        "ATC_TYPE": b"B748",
        "ATC_ID": b"N12345",
        "AUTOPILOT_MASTER": 1.0,
        "ELECTRICAL_MASTER_BATTERY": 1.0,
        "FUEL_TOTAL_QUANTITY_WEIGHT": 50000.0,
        "SIMULATION_RATE": 1.0,
    }
    mock_aq.get.side_effect = lambda key: simvar_values.get(key.split(":")[0])
    mock_aq.set.return_value = None

    # Mock event find. Kept as `.return_value` (not `.side_effect`) so tests
    # can keep overriding it with `mock_simconnect["ae"].find.return_value =
    # None` to force the mapped fallback -- an established pattern across
    # several existing tests, which a side_effect would silently shadow.
    # `.deff` (events.py's trigger_event reads it off a found Event to get
    # the raw name for map_to_sim_event) is left as a plain, unconfigured
    # mock attribute: map_to_sim_event is itself fully mocked and does
    # nothing with its argument beyond returning a truthy stand-in, so no
    # test needs `.deff` to equal any particular bytes.
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

        # Notional cost of one read, in seconds. The previous mock ignored
        # its timeout argument entirely, so no test could produce a
        # timed-out entry and the whole batch-budget failure path was
        # unreachable -- which is why a budget bug reached a live sim before
        # it reached a test. Nothing here actually sleeps: the cost is
        # bookkeeping against the same budget arithmetic the real
        # SimVarAccessor.read_many performs. 0.0 leaves every read
        # succeeding, so existing tests are unaffected; a test that wants
        # the failure path raises it.
        mock_accessor.simulated_read_seconds = 0.0

        def _entry(name, unit):
            return {
                "value": _decode(name, simvar_values.get(name.split(":")[0])),
                "unit": resolve_unit(name, unit),
            }

        # L-vars written through the raw-datum path, so a write and its
        # verifying read-back see the same store. Without this the mock
        # could not distinguish a write that landed from one that did
        # nothing, which is the whole point of set_lvar's `verified` field.
        lvar_values: dict[str, float] = {}

        def _read(name, unit=None, index=None, timeout=2.0, raw_name=False):
            if mock_accessor.simulated_read_seconds > timeout:
                raise SimVarTimeoutError(
                    f"No response for SimVar '{name}' within {timeout}s."
                )
            if raw_name:
                # Verified against a live sim: a native read of an L-var
                # that was never set returns 0.0 rather than raising, so an
                # absent name is indistinguishable from one holding zero.
                return lvar_values.get(name.strip(), 0.0)
            return _decode(name, simvar_values.get(name.split(":")[0]))

        def _write(name, value, unit=None, index=None, grace=0.15, verify=False,
                   raw_name=False):
            if not raw_name:
                # Leave SimVar writes to whatever the test configured via
                # write.return_value / write.side_effect.
                return DEFAULT
            lvar_values[name.strip()] = float(value)
            return values_match(float(value), float(value)) if verify else None

        def _read_many(reqs, per_item_timeout=2.0):
            # Mirrors the real SimVarAccessor.read_many's cap (simvar_access.py)
            # so a test that drives per_item_timeout/len(reqs) high enough to
            # hit MAX_BATCH_BUDGET sees the same truncated budget here that it
            # would against the real accessor, rather than an uncapped one
            # this mock cannot express.
            budget = min(len(reqs) * per_item_timeout, MAX_BATCH_BUDGET)
            spent = 0.0
            out = {}
            for n, u, i in reqs:
                key = n if i is None else f"{n}:{i}"
                remaining = budget - spent
                cost = mock_accessor.simulated_read_seconds
                if remaining <= 0 or cost > remaining:
                    out[key] = {
                        "error": (
                            f"The batch read budget of {budget:.1f}s ran out before "
                            f"reaching '{n}'."
                        ),
                        "error_type": "SimVarBatchTimeoutError",
                        "unit": resolve_unit(n, u),
                    }
                    spent = budget
                    continue
                spent += cost
                out[key] = _entry(n, u)
            return out

        mock_accessor.read.side_effect = _read
        mock_accessor.read_many.side_effect = _read_many
        mock_accessor.write.side_effect = _write
        manager.accessor = mock_accessor

        yield {
            "manager": manager,
            "sm": mock_sm,
            "aq": mock_aq,
            "ae": mock_ae,
            "event": mock_event,
            "simvar_values": simvar_values,
            "accessor": mock_accessor,
            "lvar_values": lvar_values,
            "mock_assigned_object_id": MOCK_ASSIGNED_OBJECT_ID,
        }
