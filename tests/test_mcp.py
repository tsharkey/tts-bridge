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
import data
import layouts
import tooltips
import tts_bridge
from app import mcp_server, server
from test_formats import figure, load, made_up_table
from test_tooltips import SHEET, squad

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
    assert {"status", "board_summary", "measure", "place_unit", "undo_place", "line_of_sight", "threat_ranges",
            "can_reach"} <= set(names(tools))
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


def test_measure(monkeypatch):
    monkeypatch.setattr(board, "read_objects", made_up_table)
    monkeypatch.setattr(layouts, "load_all", lambda: [load("layout.json")])
    got = call("measure", {"unit": "Pathfinder", "to": "Intercessor Squad", "landmarks": True}).structured_content
    assert (got["a"]["unit"], got["b"]["unit"]) == ("Pathfinder Team", "Intercessor Squad")
    assert got["distance"] == 31.63 and got["engagement_range"] is False   # (-17, 13) to (-6, -18), less the bases
    assert [p["guid"] for p in got["closest"]] == ["a1b2c2", "0f0f00"]
    assert got["layout"] == "0c4960"
    held = [(r["unit"], r["id"]) for r in got["landmarks"] if r["within"]]
    assert held == [("a", "expansion-red"), ("a", "red"), ("b", "blue")]


def test_measure_says_which_unit_it_cant_find(monkeypatch):
    monkeypatch.setattr(board, "read_objects", made_up_table)
    result = call("measure", {"unit": "Stealth Battlesuits", "to": "Pathfinder"})
    assert result.is_error and "one off the table does" in result.content[0].text
    same = call("measure", {"unit": "Pathfinder", "to": "Pathfinder Team"})
    assert same.is_error and "same unit" in same.content[0].text
    got = call("measure", {"unit": "Pathfinder"}).structured_content   # no layout asked for: nothing to measure to
    assert got["b"] is None and got["landmarks"] == [] and got["layout"] is None


def test_place_unit(monkeypatch, tmp_path):
    monkeypatch.setattr(board, "read_objects", made_up_table)
    monkeypatch.setattr(layouts, "load_all", lambda: [load("layout.json")])
    monkeypatch.setattr(board, "UNDO_JSON", tmp_path / "undo.json")
    sent = []
    monkeypatch.setattr(board, "move", lambda moves: sent.append(moves) or float(len(moves)))   # TTS returns 3.0
    args = {"unit": "Pathfinder", "x": 0, "z": 16, "facing": 180, "cols": 3}
    checked = call("place_unit", {**args, "check_only": True}).structured_content
    assert checked["moved"] == 0 and checked["problems"] == [] and not sent
    assert checked["zones"] == ["red"] and checked["touching"] == ["Red deployment zone"]   # z 10..22
    assert (checked["facing"], [p["x"] for p in checked["positions"]]) == (180, [1.66, 0.0, -1.66])
    placed = call("place_unit", args).structured_content
    assert placed["moved"] == 3 and len(sent) == 1
    assert call("undo_place").structured_content == {"moved": 3, "left": 0}
    assert call("undo_place").is_error


def test_place_unit_refuses_problems_unless_forced(monkeypatch, tmp_path):
    monkeypatch.setattr(board, "read_objects", made_up_table)
    monkeypatch.setattr(board, "UNDO_JSON", tmp_path / "undo.json")
    monkeypatch.setattr(board, "move", lambda moves: len(moves))
    args = {"unit": "Pathfinder", "x": -5.25, "z": -16.5, "cols": 3}   # onto the Intercessors
    refused = call("place_unit", args).structured_content
    assert refused["moved"] == 0 and any("engagement range" in p or "overlaps" in p for p in refused["problems"])
    assert call("place_unit", {**args, "force": True}).structured_content["moved"] == 3
    reserves = call("place_unit", {"unit": "Stealth", "x": 0, "z": 16, "from_reserves": True}).structured_content
    assert reserves["moved"] == 3
    assert call("place_unit", {"unit": "Stealth", "x": 0, "z": 16}).is_error   # not on the table


def table_with_a_unit_behind_the_ruin():
    """The sample table, and a Blue unit south-east of the central ruin (A1), which stands
    between it and the Pathfinders."""
    return made_up_table() + [figure(4 + i * 1.5, -7, "Hellblasters", "army.py:Blue list", f"h{i}") for i in range(2)]


def test_line_of_sight_to_a_target(monkeypatch):
    monkeypatch.setattr(board, "read_objects", made_up_table)
    monkeypatch.setattr(layouts, "load_all", lambda: [load("layout.json")])
    got = call("line_of_sight", {"unit": "Intercessor", "target": "Pathfinder"}).structured_content
    assert got["layout"] == "0c4960" and got["unit"]["unit"] == "Intercessor Squad"
    [t] = got["targets"]
    assert (t["target"]["unit"], t["visible"], t["fully_visible"], t["distance"]) == ("Pathfinder Team", 3, 3, 31.63)
    assert t["hidden_areas"] == ["A2"] and t["treated_as_hidden"] is False and t["plunging_fire"] is False
    assert [m["visible"] for m in t["models_seen"]] == ["full"] * 3
    assert t["models_seen"][0]["seen_by"] == ["0f0f01", "0f0f00"]
    # Hidden in their barricade area: 31" is past detection range
    hidden = call("line_of_sight", {"unit": "Intercessor", "target": "Pathfinder", "hidden_targets": True})
    [t] = hidden.structured_content["targets"]
    assert t["treated_as_hidden"] and t["visible"] == 0
    assert t["models_seen"][0]["beyond_detection"] == ["0f0f01", "0f0f00"]


def test_line_of_sight_blocked_and_every_enemy(monkeypatch):
    monkeypatch.setattr(board, "read_objects", table_with_a_unit_behind_the_ruin)
    monkeypatch.setattr(layouts, "load_all", lambda: [load("layout.json")])
    got = call("line_of_sight", {"unit": "Hellblasters", "target": "Pathfinder"}).structured_content
    [t] = got["targets"]
    assert t["visible"] == 0 and all("A1" in m["blocked_by"] for m in t["models_seen"])
    every = call("line_of_sight", {"unit": "Pathfinder"}).structured_content["targets"]
    assert [(t["target"]["unit"], t["visible"]) for t in every] == [("Intercessor Squad", 2), ("Hellblasters", 0)]
    assert all("models_seen" not in t for t in every)   # only for a named target


def test_line_of_sight_errors(monkeypatch):
    monkeypatch.setattr(board, "read_objects", made_up_table)
    monkeypatch.setattr(layouts, "load_all", lambda: [])
    no_layout = call("line_of_sight", {"unit": "Pathfinder"})
    assert no_layout.is_error and "layout" in no_layout.content[0].text
    monkeypatch.setattr(layouts, "load_all", lambda: [load("layout.json")])
    same = call("line_of_sight", {"unit": "Pathfinder", "target": "Pathfinder Team"})
    assert same.is_error and "same unit" in same.content[0].text


@pytest.fixture
def armed_table(monkeypatch):
    """The sample table; the Pathfinders' datasheet is tooltips' test squad (M 6", bolt rifles:
    24" Assault), and their models' tooltips list bolt rifles. The Intercessors have no datasheet."""
    monkeypatch.setattr(board, "read_objects", made_up_table)
    monkeypatch.setattr(layouts, "load_all", lambda: [load("layout.json")])
    monkeypatch.setattr(data, "datasheets_by_id", lambda: {"c8b1-9d6c-4a53-b0e2": SHEET})
    unit = squad()
    trooper = tooltips.tooltip(unit, unit["models"][1], SHEET, lead=False)["text"]
    descriptions = {f"a1b2c{i}": f"[Pathfinder Team]\n{trooper}" for i in range(3)}
    monkeypatch.setattr(tts_bridge, "run_lua", lambda script, timeout=None: descriptions)
    return descriptions


def test_threat_ranges(armed_table):
    got = call("threat_ranges", {"unit": "Pathfinder"}).structured_content
    assert (got["move"], got["weapons_from"], got["datasheet"]) == (6, "models", "c8b1-9d6c-4a53-b0e2")
    assert [w["name"] for w in got["weapons"]] == ["Bolt rifle"]
    bands = {b["band"]: (b["avg"], b["max"]) for b in got["bands"]}
    assert bands["charge"] == (13, 18) and bands["advance and shoot: Bolt rifle"] == (33.5, 36)
    armed_table.clear()                               # models without tooltips: the datasheet's defaults
    assert call("threat_ranges", {"unit": "Pathfinder"}).structured_content["weapons_from"] == "datasheet defaults"
    untagged = call("threat_ranges", {"unit": "Intercessor"})
    assert untagged.is_error and "wasn't spawned by tts-bridge" in untagged.content[0].text


def test_can_reach(armed_table):
    got = call("can_reach", {"unit": "Pathfinder"}).structured_content
    [r] = got["reach"]                                # 31.63" away: only an advance and a bolt rifle get there
    assert (r["target"]["unit"], r["gap"], r["visible"], r["charge"]) == ("Intercessor Squad", 31.63, True, None)
    assert r["weapons"] == [{"weapon": "Bolt rifle", "range": 24, "now": False, "after_move": False,
                             "after_advance": True}]
    assert got["out_of_reach"] == [] and got["unknown"] == []
    both = call("can_reach", {"unit": "Pathfinder", "target": "Intercessor"}).structured_content
    assert both["reach"] == got["reach"]
    against = call("can_reach", {"target": "Pathfinder"}).structured_content   # who reaches them: no datasheet
    assert against["reach"] == [] and [u["unit"] for u in against["unknown"]] == ["Intercessor Squad"]


def test_can_reach_errors(armed_table):
    assert "Name a unit" in call("can_reach", {}).content[0].text
    assert "same unit" in call("can_reach", {"unit": "Pathfinder", "target": "Pathfinder Team"}).content[0].text
    assert "no cached datasheet" in call("can_reach", {"unit": "Intercessor"}).content[0].text


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
