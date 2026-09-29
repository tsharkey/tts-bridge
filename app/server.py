"""
server.py — the hub: a local web app with a page per tool.

    .venv/bin/python app/server.py        # then open http://localhost:8765

Needs TTS running with a game loaded and the External Editor API on. The hub
holds the bridge's listener port and forwards Lua for tts_bridge.py, army.py,
board.py and recreate.py, so they can run while it's up (start it first).

Adding a tool: make app/tools/<name>/ (see app/tools/__init__.py) and add it
to TOOLS below. The MCP server for Claude is at /mcp (see app/mcp_server/).
"""

import contextlib
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):   # run as a script: make `app` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import terrain  # noqa: E402
import tts_bridge as tts  # noqa: E402
from app import mcp_server  # noqa: E402
from app.mcp_server import overlay  # noqa: E402
from app.core.api import shared  # noqa: E402
from app.tools import board, board_replay, data, layouts, models, scribe  # noqa: E402

PORT = tts.HUB_PORT
HOSTS = ["127.0.0.1", "localhost"]
STATIC = Path(__file__).resolve().parent / "static"
TOOLS = [board, scribe, models, board_replay, layouts, data]


class Static(StaticFiles):
    """Pages and scripts, re-checked on every load ("no-cache" still lets the
    browser reuse an unchanged file): without it, a browser can pair a new
    page with an old shared.css or shared.js after an update."""
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def slug(tool):
    return tool.__name__.rsplit(".", 1)[-1].replace("_", "-")


def redirect_to(target):
    # no parameters: FastAPI would read any as query parameters, letting ?x= pick the target
    def redirect():
        return RedirectResponse(target)
    return redirect


def create_app(hosts=None):
    """hosts: the Host names to answer (all if None). The server passes
    HOSTS, so a web page can't reach /api/tts/lua by DNS rebinding."""
    mcp = mcp_server.server()
    mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", host="127.0.0.1")

    @contextlib.asynccontextmanager
    async def lifespan(app):
        # spawned models' right-click "Clear overlays" (and older models' overlay items), and a
        # model asking for the terrain its line of sight needs (sheetviewer.py, terrain.py)
        tts.commands["overlay"] = overlay.menu_request
        tts.commands["terrain"] = terrain.request
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            tts.commands.pop("overlay", None)
            tts.commands.pop("terrain", None)

    app = FastAPI(title="TTS Bridge", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    if hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.include_router(shared)
    app.router.routes.extend(mcp_app.routes)   # /mcp, before the pages' catch-all mount

    @app.get("/api/tools")
    def tools():
        return [{**t.TOOL, "path": f"/tools/{slug(t)}/"} for t in TOOLS]

    for t in TOOLS:
        path = f"/tools/{slug(t)}"
        app.include_router(t.router)
        app.add_api_route(path, redirect_to(path + "/"), include_in_schema=False)
        app.mount(path, Static(directory=Path(t.__file__).parent / "static", html=True))
    app.mount("/", Static(directory=STATIC, html=True))   # homepage and shared.css / shared.js
    return app


class QuietAccessLog(logging.Filter):
    """Log API calls, but not the status poll or static files."""
    def filter(self, record):
        path = str(record.args[2]) if isinstance(record.args, tuple) and len(record.args) > 2 else ""
        return path.startswith("/api/") and not path.startswith("/api/status")


def main():
    import uvicorn
    tts.start_listener()
    logging.getLogger("uvicorn.access").addFilter(QuietAccessLog())
    print(f"TTS Bridge app running at http://localhost:{PORT}  (Ctrl+C to stop)")
    uvicorn.run(create_app(HOSTS), host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    main()
