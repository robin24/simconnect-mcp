import logging
import sys


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


def test_run_sync_uses_get_running_loop():
    """get_event_loop() inside a coroutine is deprecated since 3.10."""
    import inspect

    from simconnect_mcp import connection

    source = inspect.getsource(connection)
    assert "get_event_loop()" not in source
    assert "get_running_loop()" in source
