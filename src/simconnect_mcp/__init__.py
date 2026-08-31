"""SimConnect MCP Server — MSFS add-on development companion."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("simconnect-mcp")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
