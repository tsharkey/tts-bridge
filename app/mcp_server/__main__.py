"""
The MCP server over stdio, for Claude Desktop (which starts it). Its Lua goes
through the hub, which must be running; it never takes TTS's reply port, so
starting Claude Desktop first doesn't keep the hub from starting.

    PYTHONPATH=<this repo> .venv/bin/python -m app.mcp_server
"""

import tts_bridge
from app.mcp_server import server

if __name__ == "__main__":
    tts_bridge.use_hub()
    server().run("stdio")
