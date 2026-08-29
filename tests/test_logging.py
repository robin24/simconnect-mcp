import logging
import sys

import pytest


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """configure_logging() mutates the root logger's handlers/level process-wide.

    Snapshot and restore both around every test in this file so a test that
    calls configure_logging() (or clears handlers itself) can't leak state
    into unrelated tests later in the same pytest session.
    """
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    root.handlers[:] = original_handlers
    root.setLevel(original_level)


def test_logging_setup_uses_stderr_only(monkeypatch):
    """This is a stdio server: anything on stdout corrupts JSON-RPC."""
    from simconnect_mcp.server import configure_logging

    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    configure_logging()

    assert root.handlers, "expected a handler to be installed"
    for handler in root.handlers:
        assert getattr(handler, "stream", sys.stderr) is not sys.stdout


def test_default_log_level_is_warning(monkeypatch):
    from simconnect_mcp.server import configure_logging

    monkeypatch.delenv("SIMCONNECT_MCP_LOG_LEVEL", raising=False)
    configure_logging()
    assert logging.getLogger().level == logging.WARNING


def test_log_level_is_overridable_by_env(monkeypatch):
    from simconnect_mcp.server import configure_logging

    monkeypatch.setenv("SIMCONNECT_MCP_LOG_LEVEL", "DEBUG")
    configure_logging()
    assert logging.getLogger().level == logging.DEBUG


@pytest.mark.parametrize("value", ["BASIC_FORMAT", "basic_format", "bogus", "", "  ", "42"])
def test_invalid_log_level_never_crashes_startup(monkeypatch, value):
    """A bad env value must degrade to WARNING, not take the server down.
    getattr(logging, "BASIC_FORMAT") returns a format string, and setLevel
    raises ValueError on it."""
    from simconnect_mcp.server import configure_logging

    monkeypatch.setenv("SIMCONNECT_MCP_LOG_LEVEL", value)
    configure_logging()
    assert logging.getLogger().level == logging.WARNING


@pytest.mark.parametrize("name,expected", [
    ("DEBUG", logging.DEBUG), ("info", logging.INFO),
    ("WARNING", logging.WARNING), ("ERROR", logging.ERROR),
    ("critical", logging.CRITICAL), ("NOTSET", logging.NOTSET),
])
def test_every_valid_level_name_is_accepted(monkeypatch, name, expected):
    from simconnect_mcp.server import configure_logging

    monkeypatch.setenv("SIMCONNECT_MCP_LOG_LEVEL", name)
    configure_logging()
    assert logging.getLogger().level == expected


def test_run_sync_uses_get_running_loop():
    """get_event_loop() inside a coroutine is deprecated since 3.10.

    Covers both connection.py's run_sync and server.py's connect_to_sim/
    disconnect_from_sim, which had the same pattern inline. This is a
    source-text check, not a behavioral one: it would miss a differently
    formatted or aliased re-introduction of get_event_loop().
    """
    import inspect

    from simconnect_mcp import connection, server

    for module in (connection, server):
        source = inspect.getsource(module)
        assert "get_event_loop()" not in source, f"{module.__name__} still calls get_event_loop()"
        assert "get_running_loop()" in source, f"{module.__name__} never calls get_running_loop()"
