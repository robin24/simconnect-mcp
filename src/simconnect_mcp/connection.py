"""SimConnectManager — singleton, thread-safe, lazy-connect wrapper."""

from __future__ import annotations

import asyncio
import enum
import logging
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _decode_identity_value(raw: Any) -> str | None:
    """Normalize a raw TITLE/ATC_MODEL SimVar read to a stripped str.

    The sim can hand back bytes, a plain str, or None depending on the code
    path (native accessor vs. legacy AircraftRequests), so this is
    defensive about all three.
    """
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("ascii", errors="replace").strip()
    return str(raw).strip()


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
        self.accessor = None  # SimVarAccessor, created on connect
        self._mobiflight_available = False
        self.pmdg = None  # PmdgDataManager (777), lazy-initialized
        self.pmdg_ng3 = None  # PmdgNG3DataManager (737), lazy-initialized
        # (timestamp, title, model); see detect_aircraft_identity().
        self._title_cache: tuple[float, str | None, str | None] | None = None
        # (title, model, variant) from tools.pmdg's client-data-area probe.
        # That probe is a real SimConnect round trip against two data areas
        # (expensive relative to a SimVar read), so the result is cached --
        # but the loaded aircraft CAN change mid-connection with no
        # reconnect (confirmed against real usage: a user swapped aircraft
        # mid-session), so this is keyed on the aircraft identity it was
        # found under, not cached unconditionally for the connection's
        # whole lifetime. See get_cached_pmdg_variant/set_cached_pmdg_variant
        # below -- a cache hit requires the *current* identity to match.
        self._pmdg_variant_cache: tuple[str | None, str | None, str] | None = None

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

        Attempts to use SimConnectDispatcher first, which owns the dispatch
        loop (enabling SimVar exception correlation via `self.accessor`) and
        is itself a drop-in subclass of the vendored SimConnectMobiFlight, so
        client-data support for the MobiFlight WASM module is preserved.
        Falls back to plain SimConnect if the dispatcher isn't available; in
        that case `self.accessor` stays None.
        """
        if self._state == ConnectionState.CONNECTED:
            return {"status": "ok", "message": "Already connected"}

        self._state = ConnectionState.CONNECTING
        try:
            from SimConnect import AircraftEvents, AircraftRequests

            # Note: no _sim_lock here — connect/disconnect are only called
            # from a single thread, and locking during init can deadlock
            # because SimConnect's constructor starts a dispatch thread.

            # Prefer SimConnectDispatcher -- owns the dispatch loop, which
            # both enables SimVar exception correlation and keeps the
            # library's print()ing branches out of the stdio stream.
            try:
                from simconnect_mcp.dispatch import SimConnectDispatcher
                self.sm = SimConnectDispatcher()
                logger.info("Using SimConnectDispatcher (WASM client-data enabled)")
            except Exception as e:
                logger.info("SimConnectDispatcher unavailable (%s), falling back", e)
                from SimConnect import SimConnect
                self.sm = SimConnect()

            self.aq = AircraftRequests(self.sm, _time=2000)
            self.ae = AircraftEvents(self.sm)

            # Generic SimVar access. Requires the dispatcher's request
            # registry, so it is only available on the dispatcher path.
            if hasattr(self.sm, "registry"):
                from simconnect_mcp.simvar_access import SimVarAccessor
                self.accessor = SimVarAccessor(self.sm)
            else:
                self.accessor = None
                logger.warning(
                    "Plain SimConnect fallback: unit-aware SimVar access unavailable"
                )

            # Try to initialize FacilitiesRequests
            try:
                from SimConnect import FacilitiesRequests
                self.fr = FacilitiesRequests(self.sm)
            except Exception:
                self.fr = None
                logger.info("FacilitiesRequests not available")

            # Try MobiFlight variable requests (requires SimConnectMobiFlight + WASM module)
            try:
                from simconnect_mcp.vendor.mobiflight_variable_requests import (
                    MobiFlightVariableRequests,
                )
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
            # Cleanup unregisters handlers on self.sm, so it must run first.
            if self.pmdg is not None:
                self.pmdg.cleanup()
                self.pmdg = None
            if self.pmdg_ng3 is not None:
                self.pmdg_ng3.cleanup()
                self.pmdg_ng3 = None
            self.sm = None
            self.aq = None
            self.ae = None
            self.fr = None
            self.mobiflight = None
            self.accessor = None
            self._mobiflight_available = False
            self._title_cache = None
            self._pmdg_variant_cache = None
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
        loop = asyncio.get_running_loop()

        def _locked_call() -> T:
            with self._sim_lock:
                return fn(*args)

        return await loop.run_in_executor(None, _locked_call)

    TITLE_CACHE_TTL = 5.0

    async def detect_aircraft_identity(self) -> tuple[str | None, str | None]:
        """Read TITLE and ATC_MODEL for aircraft detection.

        Four call sites used to read TITLE directly on the event loop with no
        lock. This routes through run_sync and caches briefly, since it is
        consulted on most catalog operations.

        ATC_MODEL is read alongside TITLE because some add-ons carry their
        vendor branding there instead: a PMDG 777F's TITLE is the terse
        "777F", which matches no catalog's title_pattern on its own. The two
        reads are independent -- one failing (e.g. an aircraft that doesn't
        expose ATC_MODEL) must not blank out a TITLE that succeeded.
        """
        if not self.is_connected or self.accessor is None:
            return None, None

        now = time.monotonic()
        if self._title_cache is not None and (now - self._title_cache[0]) < self.TITLE_CACHE_TTL:
            return self._title_cache[1], self._title_cache[2]

        def _read() -> tuple[Any, Any]:
            try:
                raw_title = self.accessor.read("TITLE")
            except Exception:
                logger.debug("Could not read TITLE", exc_info=True)
                raw_title = None
            try:
                raw_model = self.accessor.read("ATC_MODEL")
            except Exception:
                logger.debug("Could not read ATC_MODEL", exc_info=True)
                raw_model = None
            return raw_title, raw_model

        try:
            raw_title, raw_model = await self.run_sync(_read)
        except Exception:
            logger.debug("Could not read aircraft identity", exc_info=True)
            return None, None

        title = _decode_identity_value(raw_title)
        model = _decode_identity_value(raw_model)

        self._title_cache = (now, title, model)
        return title, model

    async def detect_aircraft_title(self) -> str | None:
        """Read the TITLE SimVar for aircraft detection.

        Thin wrapper over detect_aircraft_identity() for the common case
        where only the title is needed.
        """
        title, _ = await self.detect_aircraft_identity()
        return title

    def get_cached_pmdg_variant(self, title: str | None, model: str | None) -> str | None:
        """Return the PMDG variant probed for this exact aircraft identity.

        None if no probe has run yet, none responded, or -- critically --
        the cached result was found under a *different* (title, model): the
        loaded aircraft can change mid-connection with no reconnect, so a
        cache that ignored identity would keep answering with a previous
        aircraft's variant, mislabelled "probed" for the current one. This
        is exact rather than time-based: `title`/`model` is the same signal
        detect_aircraft_identity() uses to notice a swap at all, so a
        mismatch here means the aircraft has definitely changed, not just
        that some arbitrary TTL elapsed. Cleared on disconnect() regardless.
        """
        if self._pmdg_variant_cache is None:
            return None
        cached_title, cached_model, variant = self._pmdg_variant_cache
        if (cached_title, cached_model) != (title, model):
            return None
        return variant

    def set_cached_pmdg_variant(self, title: str | None, model: str | None, variant: str) -> None:
        """Record a successful probe result, keyed to the aircraft identity
        it was found under."""
        self._pmdg_variant_cache = (title, model, variant)

    async def get_status(self) -> dict[str, Any]:
        """Return current connection status.

        The sim_paused/sim_running lookup goes through run_sync rather than
        acquiring `_sim_lock` directly: this method used to take that lock
        synchronously on the event loop thread. If some other call was
        already holding it for a while (e.g. a large get_simvar_bulk against
        a hung sim), that direct acquisition blocked the event loop itself
        until the lock freed -- freezing every other tool call on the
        server, not just this one. Routing the wait through an executor
        keeps the event loop free regardless of how long the lock is held.
        """
        result: dict[str, Any] = {
            "state": self._state.value,
            "connected": self.is_connected,
            "mobiflight_available": self._mobiflight_available,
        }

        if self.is_connected and self.sm is not None:
            try:
                extra = await self.run_sync(self._read_sim_state)
                result.update(extra)
            except Exception:
                pass

        return result

    def _read_sim_state(self) -> dict[str, Any]:
        """Blocking read of sim_paused/sim_running. Call only via run_sync."""
        return {
            "sim_paused": bool(self.sm.paused),
            "sim_running": bool(self.sm.running),
        }

    LVAR_UNIT = "number"

    def set_lvar(self, name: str, value: float, verify: bool = False) -> bool | None:
        """Write an L-var through the SimVar accessor's definition layer.

        Still AddToDataDefinition + SetDataOnSimObject -- the native path
        that works with proprietary aircraft like the Fenix, where the
        MobiFlight RPN set() command does not. What changed is that it now
        goes through SimVarAccessor instead of a hand-rolled copy of that
        pattern, which fixes three things at once:

        * **The definition cache.** This used to call new_def_id() on every
          write. That function rebuilds an Enum from every prior member on
          each call, and the IDs it hands out were never reclaimed -- so a
          long session leaked one definition per write, unbounded.
          CLAUDE.md's documented Fenix FCU procedure issues one write per
          knob click at 15 ms intervals, making the documented usage
          pattern the one that leaked fastest.
        * **A typed error for a bad name.** `name.encode("ascii")` here
          raised a bare UnicodeEncodeError, which
          handle_simconnect_errors' catch-all turned into an UNEXPECTED
          envelope leaking Python exception text. The accessor converts
          that same failure to SimVarNotFoundError.
        * **Send-ID correlation**, so a write SimConnect actually rejects
          surfaces as SimVarNotSettableError instead of being invisible.

        Returns tri-state: True if a read-back confirmed the value landed,
        False if it confirmed it did not, None if verification was not
        attempted (`verify=False`) or could not be completed. Never
        reports False as True, and never reports None as either.

        Verification reads back natively rather than through MobiFlight.
        Measured live against MSFS 2024 with the WASM module loaded: after
        writing 0.0 over a previous 7.0, the native read returned 0.0 while
        MobiFlight still returned 7.0. MobiFlight's value is cached, so it
        would have reported a landed write as failed.
        """
        from simconnect_mcp.simvar_access import SimVarError, values_match

        if self.accessor is None:
            raise RuntimeError(
                "L-var writes require the SimConnect dispatcher; this connection "
                "fell back to plain SimConnect."
            )

        datum = name if name.startswith("L:") else f"L:{name}"
        self.accessor.write(datum, value, unit=self.LVAR_UNIT, raw_name=True)

        if not verify:
            return None

        # A read-back that fails is "could not verify", not "did not land":
        # reporting False here would assert something this call has no
        # evidence for. The write itself already raised for anything
        # SimConnect rejected outright.
        try:
            readback = self.accessor.read(datum, unit=self.LVAR_UNIT, raw_name=True)
        except SimVarError:
            logger.debug("L-var read-back failed for %s", datum, exc_info=True)
            return None
        if readback is None:
            return None
        return values_match(readback, value)

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        if cls._instance is not None:
            cls._instance.disconnect()
            cls._instance = None
