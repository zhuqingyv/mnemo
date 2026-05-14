"""FastAPI parent app — mounts MCP at /mcp (SSE) and /mcp/http (streamable-http).

Single long-running process: one KnowledgeService, one engine, one
connection pool, shared by every transport (MCP SSE + streamable-http today,
REST tomorrow). Both MCP transports are kept live during the migration from
SSE to streamable-http so existing agents keep working while new agents can
opt into the newer transport via config.
"""

from __future__ import annotations

import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mnemo import __version__
from mnemo.config import MnemoConfig
from mnemo.db import get_session_factory, init_db, reset_engine
from mnemo.mcp.server import mcp, set_service
from mnemo.monitor.collector import configure as configure_monitor
from mnemo.guide.router import router as guide_router
from mnemo.server.routes import router as api_router
from mnemo.services.knowledge_service import KnowledgeService


def _resolve_viz_dir() -> Path:
    """Locate the bundled viz directory across all run modes.

    Resolution order:
      1. PyInstaller onefile bundle: extracted under sys._MEIPASS/mnemo_viz/
      2. Source / editable install:   <repo>/viz/
      3. Env override (MNEMO_VIZ_DIR) for debug or custom front-ends.
    """
    override = os.environ.get("MNEMO_VIZ_DIR")
    if override:
        return Path(override).expanduser().resolve()

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "mnemo_viz"
        if bundled.is_dir():
            return bundled

    return Path(__file__).resolve().parent.parent.parent.parent / "viz"


VIZ_DIR = _resolve_viz_dir()


def create_app() -> FastAPI:
    """Build the FastAPI app. Used by uvicorn factory mode (`--factory`)."""

    mcp_sse_app = mcp.http_app(transport="sse", path="/sse")
    mcp_http_app = mcp.http_app(transport="streamable-http", path="/mcp")

    viz_enabled = os.environ.get("MNEMO_VIZ_ENABLED", "1") != "0"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = MnemoConfig()
        await init_db(config)
        factory = get_session_factory(config)
        from mnemo.services.embedding_service import EmbeddingService
        embedding = EmbeddingService(config)
        service = KnowledgeService(session_factory=factory, config=config, embedding_service=embedding)
        set_service(service)
        app.state.service = service
        app.state.config = config
        configure_monitor(session_factory=factory, enabled=True)

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(mcp_sse_app.router.lifespan_context(mcp_sse_app))
            await stack.enter_async_context(mcp_http_app.router.lifespan_context(mcp_http_app))
            try:
                yield
            finally:
                await reset_engine()

    app = FastAPI(title="mnemo", version=__version__, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api/v1")
    app.include_router(guide_router, prefix="/api/v1/guide")
    app.mount("/mcp/http", mcp_http_app)
    app.mount("/mcp", mcp_sse_app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    if viz_enabled and VIZ_DIR.is_dir():
        viz_v2_dir = VIZ_DIR / "viz_v2"
        viz_v1_file = VIZ_DIR / "viz_v1_live.html"

        if viz_v1_file.is_file():

            @app.get("/viz/v1")
            async def viz_v1():
                return FileResponse(viz_v1_file)

        if viz_v2_dir.is_dir():
            app.mount("/viz", StaticFiles(directory=str(viz_v2_dir), html=True), name="viz")

    return app


app = create_app()
