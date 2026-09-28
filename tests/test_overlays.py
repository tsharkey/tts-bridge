"""Drawing line of sight and threat ranges on the TTS table (overlays.py, issue #30): the
shapes, the Lua that draws and clears them, and the right-click menu's route through the hub,
on the board state sample's table with TTS stubbed."""

import asyncio
import json
import math
import shutil
import subprocess
import threading

import pytest
from mcp import Client

import board
import data
import layouts
import overlays
import threat
import tts_bridge
from app import mcp_server
from app.mcp_server import overlay, table
from test_formats import load, made_up_table
from test_tooltips import SHEET


def state():
    return table.summary(made_up_table(), [load("layout.json")])


def index(st, name):
    return next(i for i, u in enumerate(st["units"]) if u["unit"] == name and u["on_table"])


def test_one_circle_is_one_loop():
    [loop] = overlays.ring_outline([(0, 0, 2)], steps=8)
    assert len(loop) == 9 and loop[0] == loop[-1]
    assert all(math.isclose(math.hypot(x, z), 2) for x, z in loop)


def test_overlapping_circles_leave_one_outline():
    circles = [(0, 0, 2), (3, 0, 2), (10, 0, 1)]
    arcs = overlays.ring_outline(circles, steps=60)
    assert len(arcs) == 3                          # an arc from each of the two that overlap, and the loose one
    for arc in arcs:
        for p in arc:
            on = [c for c in circles if math.isclose(math.dist(p, c[:2]), c[2], abs_tol=1e-9)]
            assert on and not any(math.dist(p, c[:2]) < c[2] - 1e-6 for c in circles if c not in on)
    assert overlays.ring_outline([(0, 0, 5), (1, 0, 1)]) == overlays.ring_outline([(0, 0, 5)])   # one inside another


def test_lines_for_a_unit():
    st = state()
    i = index(st, "Pathfinder Team")
    bands = threat.bands(threat.profile(SHEET))
    lines = overlays.lines_for(st, i, ["los", "charge"], bands)
    y = st["surface_y"] + overlays.LIFT
    charge = [ln for ln in lines if ln["color"] == overlays.COLOURS["charge"]]
    assert charge and all(p[1] == round(y, 3) for ln in charge for p in ln["points"])
    reach = next(b["max"] for b in bands if b["band"] == "charge")
    pf = st["units"][i]["positions"]
    for ln in charge:                                  # on the outline: reach from the nearest base's edge
        for x, _, z in ln["points"]:
            edge = min(math.dist((x, z), (p["x"], p["z"])) - (p["base"][0] + p["base"][1]) / 4 for p in pf)
            assert math.isclose(edge, reach, abs_tol=0.01)
    outline = next(ln for ln in lines if ln["color"] == overlays.COLOURS["los"])
    assert outline["loop"] and len(outline["points"]) > 100
    fan = [ln for ln in lines if ln["color"] in (overlays.COLOURS["full"], overlays.COLOURS["partial"])]
    assert len(fan) == 2                               # one to each Intercessor
    assert {tuple(ln["points"][1][::2]) for ln in fan} == {(-6.0, -18.0), (-4.5, -18.0)}
    avg = overlays.lines_for(st, i, ["charge"], bands, dice="avg")
    assert avg != charge


def test_lines_for_problems():
    st = state()
    bands = threat.bands(threat.profile(SHEET))
    off = next(i for i, u in enumerate(st["units"]) if not u["on_table"])
    with pytest.raises(ValueError, match="off the table"):
        overlays.lines_for(st, off, ["los"])
    with pytest.raises(ValueError, match="Nothing called lasers.*charge"):
        overlays.lines_for(st, index(st, "Pathfinder Team"), ["lasers"], bands)
    with pytest.raises(ValueError, match="needs the terrain"):
        overlays.lines_for({**st, "layout": None}, index(st, "Pathfinder Team"), ["los"])


def test_draw_and_clear_send_lua(monkeypatch):
    sent = []
    monkeypatch.setattr(tts_bridge, "run_lua", lambda script, timeout=None: sent.append(script) or 3)
    lines = [{"points": [[0, 1, 0], [1, 1, 0]], "color": [1, 0, 0], "thickness": 0.1, "loop": False}]
    assert overlays.draw(lines) == 3
    script = sent[-1]
    assert json.dumps(json.dumps(lines)) in script and overlays.HELPER_NOTES in script
    assert "Global.setVectorLines" not in script and "positionToLocal" in script   # only our helper's lines
    assert overlays.clear() == 3 and "destruct" in sent[-1] and overlays.HELPER_NOTES in sent[-1]


@pytest.mark.skipif(not shutil.which("luajit"), reason="LuaJIT isn't installed")
def test_helper_is_gone_after_a_save_is_loaded(tmp_path):
    lua = tmp_path / "helper.lua"
    lua.write_text("self = {destruct = function() print('destructed') end}\n" + overlays.HELPER_SCRIPT + """
onLoad("")                  -- spawned: stays
print("saved as " .. onSave())
onLoad(onSave())            -- that save loaded: removes itself
""")
    run = subprocess.run(["luajit", str(lua)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["saved as saved", "destructed"]


def test_commands_go_to_their_handler_not_the_event_stream(monkeypatch):
    got, heard, done = [], [], threading.Event()
    monkeypatch.setitem(tts_bridge.commands, "overlay", lambda m: (got.append(m), done.set()))
    monkeypatch.setattr(tts_bridge, "listeners", [heard.append])
    tts_bridge.dispatch({"messageID": 4, "customMessage": {"ttsBridge": "overlay", "guid": "a1", "show": "los"}})
    assert done.wait(2) and got[0]["guid"] == "a1" and heard == []
    tts_bridge.dispatch({"messageID": 4, "customMessage": {"ttsBridge": "other"}})   # nobody handles it
    tts_bridge.dispatch({"messageID": 2, "message": "hello"})
    assert [m.get("messageID") for m in heard] == [4, 2]


@pytest.fixture
def table_and_drawing(monkeypatch):
    monkeypatch.setattr(board, "read_objects", made_up_table)
    monkeypatch.setattr(layouts, "load_all", lambda: [load("layout.json")])
    monkeypatch.setattr(data, "datasheets_by_id", lambda: {"c8b1-9d6c-4a53-b0e2": SHEET})
    lua, drawn = [], []
    monkeypatch.setattr(tts_bridge, "run_lua", lambda script, timeout=None: lua.append(script) or {})
    monkeypatch.setattr(overlays, "draw", lambda lines: drawn.append(lines) or len(lines))
    monkeypatch.setattr(overlays, "clear", lambda: drawn.append("cleared") or 1)
    return drawn, lua


def test_menu_request(table_and_drawing):
    drawn, lua = table_and_drawing
    overlay.menu_request({"ttsBridge": "overlay", "guid": "a1b2c1", "show": "threat", "color": "Red"})
    colours = {tuple(ln["color"]) for ln in drawn[-1]}
    assert colours == {tuple(overlays.COLOURS[b]) for b in ("move", "advance", "charge")}
    overlay.menu_request({"ttsBridge": "overlay", "guid": "a1b2c1", "show": "los", "color": "Red"})
    assert drawn[-1][0]["color"] == overlays.COLOURS["los"]
    overlay.menu_request({"ttsBridge": "overlay", "guid": "a1b2c1", "show": "clear", "color": "Red"})
    assert drawn[-1] == "cleared"
    # the Intercessors have no datasheet: the player who asked is told so, in TTS
    overlay.menu_request({"ttsBridge": "overlay", "guid": "0f0f00", "show": "threat", "color": "Blue"})
    assert "broadcastToColor" in lua[-1] and "no cached datasheet" in lua[-1] and "Blue" in lua[-1]


def test_mcp_tools(table_and_drawing):
    drawn, _ = table_and_drawing

    async def main():
        async with Client(mcp_server.server()) as client:
            return (await client.call_tool("show_on_table", {"unit": "Pathfinder", "show": ["los", "move"]}),
                    await client.call_tool("show_on_table", {"unit": "Pathfinder", "dice": "lots"}),
                    await client.call_tool("clear_table_overlays", {}))
    shown, bad_dice, cleared = asyncio.run(main())
    got = shown.structured_content
    assert got["unit"]["unit"] == "Pathfinder Team" and got["shown"] == ["los", "move"] and got["lines"] == len(drawn[0])
    assert bad_dice.is_error and cleared.structured_content == {"removed": 1}
