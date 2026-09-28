"""What the board view and Claude share (issue #44): the page's selection and Claude's
highlights, through the hub's routes, the MCP tools in process, and over HTTP as the stdio
server Claude Desktop starts reaches them. The table is the board state sample's, stubbed."""

import asyncio
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient
from mcp import Client

import board
import overlays
import tts_bridge
from app import mcp_server, server
from app.core import view
from app.mcp_server import shared
from test_formats import made_up_table
from test_mcp import free_port


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(view, "state", {"selection": {"units": [], "at": None}, "highlights": {}, "v": 0,
                                        "page_seen": 0.0})
    monkeypatch.setattr(tts_bridge, "hub", None)
    monkeypatch.setattr(board, "read_objects", made_up_table)


@pytest.fixture
def client():
    return TestClient(server.create_app())


def call(name, args=None):
    async def main():
        async with Client(mcp_server.server()) as c:
            return await c.call_tool(name, args or {})
    return asyncio.run(main())


PF = {"army": "army.py:Ret Cadre", "unit": "Pathfinder Team", "nth": 1, "on_table": True}


def test_the_page_selection_reaches_claude(client):
    assert call("get_selection").structured_content == {"units": [], "at": None, "page_open": False}
    got = client.post("/api/board/selection", json={"units": [{**PF, "positions": ["ignored"]}]}).json()
    assert got["units"] == [PF] and got["at"]
    client.get("/api/board/highlights")                        # the page polling
    sel = call("get_selection").structured_content
    assert sel["units"] == [PF] and sel["page_open"] is True


def test_highlights_reach_the_page(client):
    shapes = [{"kind": "unit", "unit": "Pathfinder", "color": "green"},
              {"kind": "point", "x": 0, "z": 16, "text": "drop here"},
              {"kind": "circle", "x": 0, "z": 0, "r": 6, "color": "#ff00aa"},
              {"kind": "line", "points": [[-20, 13], [-5, -18]], "text": "lane"},
              {"kind": "area", "points": [[-30, 10], [30, 10], [30, 22]], "color": "red"}]
    got = call("highlight", {"shapes": shapes, "label": "plan", "note": "Drop 3"}).structured_content
    assert got == {"label": "plan", "shapes": 5, "on_table": None}
    page = client.get("/api/board/highlights").json()
    plan = page["highlights"]["plan"]
    assert page["v"] == 1 and plan["note"] == "Drop 3"
    unit = plan["shapes"][0]
    assert unit["text"] == "Pathfinder Team" and unit["color"] == "green" and len(unit["outline"]) >= 1
    for arc in unit["outline"]:                                # 0.5" round the 32mm bases
        for x, z in arc:
            edge = min(((x - bx) ** 2 + (z - 13) ** 2) ** 0.5 for bx in (-20, -18.5, -17)) - 0.63
            assert abs(edge - shared.UNIT_MARGIN) < 0.02
    assert [s["kind"] for s in plan["shapes"]] == ["unit", "point", "circle", "line", "area"]
    call("highlight", {"shapes": [{"kind": "point", "x": 1, "z": 1}], "label": "other"})
    assert call("clear_highlights", {"label": "plan"}).structured_content == {"cleared": ["plan"], "table": None}
    assert list(client.get("/api/board/highlights").json()["highlights"]) == ["other"]
    assert client.delete("/api/board/highlights").json() == {"cleared": ["other"]}


@pytest.mark.parametrize("shape, expected", [
    ({"kind": "blob"}, "kind is one of"),
    ({"kind": "unit"}, "needs the unit's name"),
    ({"kind": "unit", "unit": "Hive Tyrant"}, "No unit on the table"),
    ({"kind": "point", "x": 1}, "needs x and z"),
    ({"kind": "circle", "x": 1, "z": 1}, "radius"),
    ({"kind": "line", "points": [[0, 0]]}, "2 or more"),
    ({"kind": "area", "points": [[0, 0], [1, 1]]}, "3 or more"),
])
def test_bad_shapes(shape, expected):
    result = call("highlight", {"shapes": [shape]})
    assert result.is_error and expected in result.content[0].text
    assert view.state["highlights"] == {}                       # nothing half drawn


def test_on_the_table(monkeypatch):
    drawn, cleared = [], []
    monkeypatch.setattr(overlays, "draw", lambda lines: drawn.append(lines) or len(lines))
    monkeypatch.setattr(overlays, "clear", lambda: cleared.append(1) or 1)
    got = call("highlight", {"shapes": [{"kind": "unit", "unit": "Pathfinder", "color": "blue"},
                                        {"kind": "point", "x": 0, "z": 0}], "on_table": True}).structured_content
    lines = drawn[0]
    assert got["on_table"] == len(lines) and lines[0]["color"] == overlays.COLOUR_NAMES["blue"]
    assert len(lines) >= 4                                     # the unit's ring, and a point's ring and cross
    assert all(p[1] > 1.0 for ln in lines for p in ln["points"])   # above the sample table's surface (y 1.0)
    assert call("clear_highlights", {"on_table": True}).structured_content == {"cleared": ["claude"], "table": 1}


def test_the_table_failing_still_draws_on_the_page(monkeypatch):
    def fails(lines):
        raise ValueError("Couldn't draw on the table: No response from TTS.")
    monkeypatch.setattr(overlays, "draw", fails)
    result = call("highlight", {"shapes": [{"kind": "point", "x": 0, "z": 0}], "label": "plan", "on_table": True})
    assert result.is_error and "Drawn on the board view, but not on the table" in result.content[0].text
    assert list(view.state["highlights"]) == ["plan"]


def test_colours():
    assert overlays.colour("red") == overlays.COLOUR_NAMES["red"]
    assert overlays.colour("#ff0000") == [1.0, 0.0, 0.0]
    assert overlays.colour("mauve") == overlays.colour(None) == overlays.COLOUR_NAMES["yellow"]


def test_from_another_process_through_the_hub(monkeypatch):
    """The stdio server (Claude Desktop) has its own process: it reads and writes the hub's state over HTTP."""
    port = free_port()
    hub = uvicorn.Server(uvicorn.Config(server.create_app(server.HOSTS), host="127.0.0.1", port=port,
                                        log_level="warning"))
    threading.Thread(target=hub.run, daemon=True).start()
    while not hub.started:
        time.sleep(0.05)
    try:
        view.select([PF])                                        # as if the page had posted it to the hub
        monkeypatch.setattr(tts_bridge, "hub", f"http://127.0.0.1:{port}")
        assert view.selection()["units"] == [PF]
        assert view.set_highlight("plan", [{"kind": "point", "x": 0, "z": 0}], "note") == 1
        assert view.state["highlights"]["plan"]["note"] == "note"   # it landed in the hub's state
        assert view.clear_highlights("plan") == ["plan"]
        assert view.clear_highlights("nothing") == []
    finally:
        hub.should_exit = True
