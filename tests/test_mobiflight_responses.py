import struct
from unittest.mock import MagicMock

from simconnect_mcp.vendor.mobiflight_variable_requests import MobiFlightVariableRequests


def _bridge():
    sm = MagicMock()
    bridge = MobiFlightVariableRequests(sm)
    return bridge


def _string_message(text: str):
    """A client-data message carrying an ASCII string in the 256-byte area."""
    payload = text.encode("ascii").ljust(256, b"\x00")
    words = struct.unpack(f"<{len(payload) // 4}I", payload)

    message = MagicMock()
    message.dwDefineID = 0  # DATA_STRING_DEFINITION_ID
    message.dwData = words
    return message


def test_response_strings_reach_a_registered_handler():
    """Regression: definition 0 is never in sim_vars, so every response
    string was logged as 'not found' and dropped."""
    bridge = _bridge()
    seen = []
    bridge.add_response_handler(seen.append)

    bridge.client_data_callback_handler(_string_message("A32NX_AUTOPILOT_1_ACTIVE"))

    assert seen == ["A32NX_AUTOPILOT_1_ACTIVE"]


def test_response_string_is_trimmed_of_padding():
    bridge = _bridge()
    seen = []
    bridge.add_response_handler(seen.append)
    bridge.client_data_callback_handler(_string_message("SHORT"))
    assert seen == ["SHORT"]


def test_removing_a_handler_stops_delivery():
    bridge = _bridge()
    seen = []
    bridge.add_response_handler(seen.append)
    bridge.remove_response_handler(seen.append)
    bridge.client_data_callback_handler(_string_message("IGNORED"))
    assert seen == []


def test_a_failing_handler_does_not_break_the_dispatch_thread():
    bridge = _bridge()
    good = []

    def boom(_):
        raise RuntimeError("handler exploded")

    bridge.add_response_handler(boom)
    bridge.add_response_handler(good.append)
    bridge.client_data_callback_handler(_string_message("STILL_DELIVERED"))

    assert good == ["STILL_DELIVERED"]


def test_variable_values_still_work():
    """Numeric variable updates must be unaffected."""
    bridge = _bridge()
    bridge.get("(L:TEST_VAR)")

    message = MagicMock()
    message.dwDefineID = 1
    message.dwData = struct.unpack("<I", struct.pack("<f", 42.0))
    bridge.client_data_callback_handler(message)
    bridge.client_data_callback_handler(message)  # first zero-check pass

    assert bridge.sim_vars[1].float_value == 42.0
