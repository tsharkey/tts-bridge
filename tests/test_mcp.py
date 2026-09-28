"""The MCP server (app/mcp_server/): its tools list and answer without TTS, in
process, over HTTP from the hub, and over stdio as Claude Desktop starts it."""

import asyncio
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from mcp import Client, StdioServerParameters

import board
import layouts
import tts_bridge
from app import mcp_server, server
from test_formats import load, made_up_table

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def no_tts(monkeypatch):
    def unreachable(*args, **kwargs):
        raise SystemExit("TTS isn't accepting commands on port 39999.")
    monkeypatch.setattr(tts_bridge, "run_lua", unreachable)


def names(tools):
    return sorted(t.name for t in tools.tools)


def test_tools_listed_and_described():
    async def main():
        async with Client(mcp_server.server()) as client:
            return await client.list_tools()
    tools = asyncio.run(main())
    assert {"status", "board_summary"} <= set(names(tools))
    assert all(t.description for t in tools.tools)


def test_status_without_tts(no_tts):
    async def main():
        async with Client(mcp_server.server()) as client:
            return await client.call_tool("status", {})
    result = asyncio.run(main())
    assert not result.is_error
    assert result.structured_content == {"connected": False, "lct": False}


def call(name, args=None):
    async def main():
        async with Client(mcp_server.server()) as client:
            return await client.call_tool(name, args or {})
    return asyncio.run(main())


def test_board_summary(monkeypatch):
    """The board state sample's table, with the sample layout loaded."""
    monkeypatch.setattr(board, "read_objects", made_up_table)
    monkeypatch.setattr(layouts, "load_all", lambda: [load("layout.json")])
    result = call("board_summary")
    assert not result.is_error
    got = result.structured_content
    state = load("board-state.json")
    assert got["units"] == state["units"]
    pathfinders = got["units"][0]
    assert pathfinders["areas"] == ["A2"] and pathfinders["objectives"] == ["expansion-red"]
    assert [p["x"] for p in pathfinders["positions"]] == [-20, -18.5, -17]
    layout = got["layout"]
    assert (layout["id"], layout["deployment"], layout["matched"], layout["pieces"]) == ("0c4960", "Dawn of War", 6, 6)
    assert [o["id"] for o in layout["objectives"]][:2] == ["home-red", "home-blue"]
    assert layout["areas"][0]["features"][0]["category"] == "dense"
    assert [t["name"] for t in got["terrain"]] == ["Red deployment zone"]   # the layout has the rest exactly


def test_board_summary_without_a_layout(monkeypatch):
    monkeypatch.setattr(board, "read_objects", made_up_table)
    monkeypatch.setattr(layouts, "load_all", lambda: [])
    got = call("board_summary").structured_content
    assert got["layout"] is None
    assert "areas" not in got["units"][0]
    assert len(got["terrain"]) == 7   # every piece TTS reports, as boxes


def test_board_summary_without_tts(no_tts):
    result = call("board_summary")
    assert result.is_error and "39999" in result.content[0].text


def test_unreachable_tts_is_an_error_not_an_exit(monkeypatch, no_tts):
    """A tool that hits SystemExit (TTS or the hub down) reports it, and the server keeps going."""
    def lct_only():
        """Needs TTS."""
        return tts_bridge.run_lua("return 1")
    monkeypatch.setattr(mcp_server, "MODULES", [type("m", (), {"TOOLS": [lct_only]})])

    async def main():
        async with Client(mcp_server.server()) as client:
            return await client.call_tool("lct_only", {}), await client.list_tools()
    result, tools = asyncio.run(main())
    assert result.is_error and "39999" in result.content[0].text
    assert names(tools) == ["lct_only"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_hub_serves_mcp_over_http(no_tts):
    port = free_port()
    hub = uvicorn.Server(uvicorn.Config(server.create_app(server.HOSTS), host="127.0.0.1", port=port,
                                        log_level="warning"))
    threading.Thread(target=hub.run, daemon=True).start()
    while not hub.started:
        time.sleep(0.05)

    async def main():
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            return await client.list_tools(), await client.call_tool("status", {})
    try:
        tools, result = asyncio.run(main())
    finally:
        hub.should_exit = True
    assert "status" in names(tools)
    assert result.structured_content == {"connected": False, "lct": False}


def test_stdio_for_claude_desktop():
    """Started the way Claude Desktop's config does. Listing tools needs no TTS and no hub."""
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"],
                                   env={**os.environ, "PYTHONPATH": str(ROOT)})

    async def main():
        async with Client(params) as client:
            return await client.list_tools()
    assert "status" in names(asyncio.run(main()))


def test_stdio_never_takes_the_reply_port(monkeypatch):
    monkeypatch.setattr(tts_bridge, "hub", None)
    tts_bridge.use_hub()
    assert tts_bridge.hub == f"http://127.0.0.1:{tts_bridge.HUB_PORT}"
