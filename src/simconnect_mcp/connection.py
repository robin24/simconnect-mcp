"""SimConnectManager — singleton, thread-safe, lazy-connect wrapper."""

from __future__ import annotations

import asyncio
import enum
import logging
import threading
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ConnectionState(enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class SimConnectManager:
    """Singleton manager for the SimConnect connection."""

    _instance: SimConnectManager | None = None
    _lock = threading.Lock()

    def __new__(cls) -> SimConnectManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._state = ConnectionState.DISCONNECTED
        self._sim_lock = threading.Lock()
        self.sm = None
        self.aq = None
        self.ae = None
        self.fr = None
        self.mobiflight = None
        self._mobiflight_available = False

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == ConnectionState.CONNECTED

    @property
    def mobiflight_available(self) -> bool:
        return self._mobiflight_available

    def connect(self) -> dict[str, Any]:
        """Establish SimConnect connection. Returns status dict.

        Attempts to use SimConnectMobiFlight (vendored) first, which is a
        drop-in subclass of SimConnect that adds client-data support needed
        by the MobiFlight WASM module. Falls back to plain SimConnect if
        the vendored extension isn't available.
        """
        if self._state == ConnectionState.CONNECTED:
            return {"status": "ok", "message": "Already connected"}

        self._state = ConnectionState.CONNECTING
        try:
            from SimConnect import AircraftRequests, AircraftEvents

            # Note: no _sim_lock here — connect/disconnect are only called
            # from a single thread, and locking during init can deadlock
            # because SimConnect's constructor starts a dispatch thread.

            # Prefer SimConnectMobiFlight — enables WASM client-data bridge
            try:
                from simconnect_mcp.vendor.simconnect_mobiflight import SimConnectMobiFlight
                self.sm = SimConnectMobiFlight()
                logger.info("Using SimConnectMobiFlight (WASM client-data enabled)")
            except Exception as e:
                logger.info("SimConnectMobiFlight not available (%s), falling back to SimConnect", e)
                from SimConnect import SimConnect
                self.sm = SimConnect()

            self.aq = AircraftRequests(self.sm, _time=2000)
            self.ae = AircraftEvents(self.sm)

            # Try to initialize FacilitiesRequests
            try:
                from SimConnect import FacilitiesRequests
                self.fr = FacilitiesRequests(self.sm)
            except Exception:
                self.fr = None
                logger.info("FacilitiesRequests not available")

            # Try MobiFlight variable requests (requires SimConnectMobiFlight + WASM module)
            try:
                from simconnect_mcp.vendor.mobiflight_variable_requests import MobiFlightVariableRequests
                self.mobiflight = MobiFlightVariableRequests(self.sm)
                # Clear stale variable registrations from prior sessions —
                # without this, the WASM module returns 0 for all reads.
                self.mobiflight.clear_sim_variables()
                self._mobiflight_available = True
                logger.info("MobiFlight WASM variable requests initialized")
            except Exception as e:
                self._mobiflight_available = False
                logger.info("MobiFlight variable requests not available: %s", e)

            self._state = ConnectionState.CONNECTED
            return {
                "status": "ok",
                "message": "Connected to MSFS",
                "mobiflight": self._mobiflight_available,
            }

        except ConnectionError as e:
            self._state = ConnectionState.ERROR
            return {
                "status": "error",
                "error": "NOT_CONNECTED",
                "message": f"Could not connect to MSFS: {e}",
                "suggestion": "Ensure MSFS is running and SimConnect is accessible.",
            }
        except Exception as e:
            self._state = ConnectionState.ERROR
            return {
                "status": "error",
                "error": "NOT_CONNECTED",
                "message": f"Connection failed: {e}",
                "suggestion": "Ensure MSFS is running and SimConnect is accessible.",
            }

    def disconnect(self) -> dict[str, Any]:
        """Close the SimConnect connection."""
        if self._state == ConnectionState.DISCONNECTED:
            return {"status": "ok", "message": "Already disconnected"}
        try:
            if self.sm is not None:
                self.sm.exit()
        except Exception as e:
            logger.warning("Error during disconnect: %s", e)
        finally:
            self.sm = None
            self.aq = None
            self.ae = None
            self.fr = None
            self.mobiflight = None
            self._mobiflight_available = False
            self._state = ConnectionState.DISCONNECTED
        return {"status": "ok", "message": "Disconnected"}

    def ensure_connected(self) -> dict[str, Any] | None:
        """Lazy-connect. Returns error dict if connection fails, None on success."""
        if self._state == ConnectionState.CONNECTED:
            return None
        result = self.connect()
        if result["status"] == "error":
            return result
        return None

    async def run_sync(self, fn: Callable[..., T], *args: Any) -> T:
        """Run a blocking SimConnect call in an executor with lock."""
        loop = asyncio.get_event_loop()

        def _locked_call() -> T:
            with self._sim_lock:
                return fn(*args)

        return await loop.run_in_executor(None, _locked_call)

    def get_status(self) -> dict[str, Any]:
        """Return current connection status."""
        result: dict[str, Any] = {
            "state": self._state.value,
            "connected": self.is_connected,
            "mobiflight_available": self._mobiflight_available,
        }

        if self.is_connected and self.sm is not None:
            try:
                with self._sim_lock:
                    result["sim_paused"] = bool(self.sm.paused)
                    result["sim_running"] = bool(self.sm.running)
            except Exception:
                pass

        return result

    def set_lvar(self, name: str, value: float) -> None:
        """Write an L-var using native SimConnect data definition.

        This uses AddToDataDefinition + SetDataOnSimObject, which is
        the method that works with proprietary aircraft like the Fenix.
        The MobiFlight RPN set() command does NOT work for these aircraft.
        """
        import ctypes
        from ctypes import c_float, c_void_p, cast, sizeof
        from SimConnect.Enum import SIMCONNECT_DATATYPE, SIMCONNECT_DATA_SET_FLAG
        from SimConnect.Constants import SIMCONNECT_OBJECT_ID_USER, SIMCONNECT_UNUSED

        # Ensure L: prefix
        if not name.startswith("L:"):
            name = f"L:{name}"

        # Create a new data definition
        def_id = self.sm.new_def_id()

        # Register the L-var in the data definition
        self.sm.dll.AddToDataDefinition(
            self.sm.hSimConnect,
            def_id.value,
            name.encode("ascii"),
            b"number",
            SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_FLOAT64,
            c_float(0.0),
            SIMCONNECT_UNUSED,
        )

        # Prepare and send the value
        data_array = (ctypes.c_double * 1)(value)
        p_data = cast(data_array, c_void_p)

        self.sm.dll.SetDataOnSimObject(
            self.sm.hSimConnect,
            def_id.value,
            SIMCONNECT_OBJECT_ID_USER,
            SIMCONNECT_DATA_SET_FLAG.SIMCONNECT_DATA_SET_FLAG_DEFAULT,
            0,
            sizeof(ctypes.c_double),
            p_data,
        )

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        if cls._instance is not None:
            cls._instance.disconnect()
            cls._instance = None
