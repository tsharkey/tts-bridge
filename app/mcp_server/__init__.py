"""
The MCP server: tools Claude can call on the game, from Claude Code and Claude
Desktop's chat. The hub serves it at http://127.0.0.1:8765/mcp (Claude Code);
`python -m app.mcp_server` serves the same tools over stdio (Claude Desktop),
sending its Lua through the hub. See the README for adding it to either.

Adding a tool: a module here with its tool functions in TOOLS, and one line in
MODULES below. Each function's docstring is what Claude reads to decide when
to call it, and its type hints are its parameters. Give it a return type
(a TypedDict, or a list of them) so Claude gets structured data with a schema. Hold app.core.tts.lock around a run of TTS calls that must not
interleave with another request's. When TTS isn't reachable, the call comes
back to Claude as an error saying so.
"""

import functools

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.mcp_server import status, table

MODULES = [status, table]

INSTRUCTIONS = """Tools for a Warhammer 40,000 game in Tabletop Simulator (TTS), through the
tts-bridge hub running on this computer. Coordinates are table inches, 0,0 at the centre, x along
the 60" edge (-30..30), z along the 44" edge (-22..22); facing in degrees, 0 = +z, 90 = +x.
Call status first if a tool says TTS isn't reachable."""


def errors_to_claude(fn):
    """tts_bridge exits (SystemExit) when TTS or the hub isn't reachable: turn
    that into an ordinary error, so Claude reads it and the server keeps running."""
    @functools.wraps(fn)
    def call(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SystemExit as e:
            raise ToolError(str(e) or "TTS isn't responding") from None
    return call


def server():
    """A new MCP server with every tool registered (one per app, since its
    session manager runs once)."""
    mcp = MCPServer(name="tts-bridge", instructions=INSTRUCTIONS)
    for module in MODULES:
        for fn in module.TOOLS:
            mcp.add_tool(errors_to_claude(fn), name=fn.__name__)
    return mcp
