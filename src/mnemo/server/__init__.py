"""HTTP server package — FastAPI parent app hosting MCP-over-SSE + future REST."""

from mnemo.server.app import create_app

__all__ = ["create_app"]
