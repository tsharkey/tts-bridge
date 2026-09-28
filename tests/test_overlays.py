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
    monkeypatch.setattr(tts_bridge, "execute", lambda script, timeout=None: sent.append(script) or {"ok": True, "result": 3})
    lines = [{"points": [[0, 1, 0], [1, 1, 0]], "color": [1, 0, 0], "thickness": 0.1, "loop": False}]
    assert overlays.draw(lines) == 3
    script = sent[-1]
    assert json.dumps({"fixed": lines, "watch": None, "replace": True}) in script
    assert overlays.HELPER_NOTES in script and "Global.setVectorLines" not in script   # only our helper's lines
    assert 'helper.call("ttsbSet"' in script and "LuaScriptState = data" in script     # a helper there, or a new one
    assert overlays.clear() == 3 and "destruct" in sent[-1] and overlays.HELPER_NOTES in sent[-1]
    assert 'o.call("ttsBridgeThreat", {on = false})' in sent[-1]     # models' own threat rings off too
    # TTS failing (it can stop spawning objects) is an error, not "0 lines drawn"
    monkeypatch.setattr(tts_bridge, "execute", lambda script, timeout=None: {"ok": False, "error": "No response from TTS."})
    with pytest.raises(ValueError, match="Couldn't draw on the table: No response"):
        overlays.draw(lines)
    with pytest.raises(ValueError, match="Couldn't clear the lines"):
        overlays.clear()


@pytest.mark.skipif(not shutil.which("luajit"), reason="LuaJIT isn't installed")
def test_helper_is_gone_after_a_save_is_loaded(tmp_path):
    lua = tmp_path / "helper.lua"
    lua.write_text("self = {destruct = function() print('destructed') end}\nWait = {time = function() end}\n"
                   + overlays.HELPER_SCRIPT + """
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
    monkeypatch.setattr(overlays, "draw", lambda lines, watch=None:
                        drawn.append({"lines": lines, "watch": watch}) or len(lines))
    monkeypatch.setattr(overlays, "clear", lambda: drawn.append("cleared") or 1)
    return drawn, lua


def test_menu_request(table_and_drawing):
    drawn, lua = table_and_drawing
    # models spawned before their rings were in their own script still ask the hub
    overlay.menu_request({"ttsBridge": "overlay", "guid": "a1b2c1", "show": "threat", "color": "Red"})
    colours = {tuple(ln["color"]) for ln in drawn[-1]["lines"]}
    assert colours == {tuple(overlays.COLOURS[b]) for b in ("move", "advance", "charge")}
    assert drawn[-1]["watch"] is None
    overlay.menu_request({"ttsBridge": "overlay", "guid": "a1b2c1", "show": "los", "color": "Red"})
    assert drawn[-1]["lines"][0]["color"] == overlays.COLOURS["los"] and "a1b2c1" in drawn[-1]["watch"]
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
    assert got["unit"]["unit"] == "Pathfinder Team" and got["shown"] == ["los", "move"]
    assert got["lines"] == len(drawn[0]["lines"]) and drawn[0]["watch"]
    assert overlays.COLOURS["move"] in [ln["color"] for ln in drawn[0]["lines"]]
    assert bad_dice.is_error and cleared.structured_content == {"removed": 1}


def test_what_is_watched():
    st = state()
    i = index(st, "Pathfinder Team")
    pf = st["units"][i]["positions"]
    watched = overlays.watch_for(st, i)
    mine = {p["guid"] for p in pf}
    enemy = {p["guid"] for r in st["units"] if r["on_table"] and r["army"] != st["units"][i]["army"] for p in r["positions"]}
    assert set(watched) == mine | enemy and enemy                    # its own models and the enemy's, nobody else's


def test_line_of_sight_redrawn_as_models_move(monkeypatch):
    sent = []
    monkeypatch.setattr(overlays, "send", lambda data, count: sent.append(data) or count)
    overlays.live.clear()
    st = state()
    i = index(st, "Pathfinder Team")
    assert overlays.moved([{"guid": "x"}]) == 0                       # nothing kept up to date yet
    overlays.show(st, i, ["los"])
    assert sent[-1]["replace"] and sent[-1]["watch"]["guids"]
    before = [ln for ln in sent[-1]["fixed"]]
    p = st["units"][i]["positions"][0]
    overlays.moved([{"guid": p["guid"], "x": p["x"] + 6, "z": p["z"], "bottom": st["surface_y"] + 50, "held": True}])
    assert "replace" not in sent[-1] and sent[-1]["fixed"] != before  # only the lines change: bands keep following
    kept = overlays.live["state"]["units"][i]["positions"][0]
    assert kept["x"] == round(p["x"] + 6, 2) and kept["height"] == p["height"]   # held: not seen from where it's carried
    assert st["units"][i]["positions"][0]["x"] == p["x"]              # the table it was asked with isn't changed
    overlays.moved([{"guid": p["guid"], "x": p["x"], "z": p["z"], "bottom": st["surface_y"] + 3, "held": False}])
    assert overlays.live["state"]["units"][i]["positions"][0]["height"] == 3.0   # put down on a floor
    overlays.draw([])                                                 # anything else drawn: no more updates
    assert overlays.moved([{"guid": p["guid"], "x": 0, "z": 0, "bottom": 0}]) == 0


def test_only_the_newest_move_is_drawn(monkeypatch):
    drawn, first, go = [], threading.Event(), threading.Event()

    def slow(models):
        drawn.append(models[0]["guid"])
        if len(drawn) == 1:
            first.set()
            go.wait(2)
        return 1
    monkeypatch.setattr(overlays, "moved", slow)
    worker = threading.Thread(target=overlay.menu_request, args=({"show": "moved", "models": [{"guid": "a"}]},))
    worker.start()
    assert first.wait(2)
    for g in "bcd":                                                   # three more while "a" is being drawn
        overlay.menu_request({"show": "moved", "models": [{"guid": g}]})
    go.set()
    worker.join(2)
    assert drawn == ["a", "d"] and not overlay.moves["busy"]


HELPER_HARNESS = r"""
local log = {}
local function note(s) table.insert(log, s) end
local objects = {}
local function model(guid, x, z)
  local o = {x = x, y = 1, z = z}
  o.getBounds = function() return {center = {x = o.x, y = o.y + 0.5, z = o.z}, size = {x = 1, y = 1, z = 1}} end
  objects[guid] = o
  return o
end
function getObjectFromGUID(g) return objects[g] end
self = {positionToLocal = function(p) return {p[1], p[2] + 10, p[3]} end,   -- the helper sits at y -10
        setVectorLines = function(lines) note("lines " .. #lines) last = lines end,
        destruct = function() note("destructed") end}
Wait = {time = function() end}
JSON = {decode = function(s) return s end}   -- the state below is already a table
function sendExternalMessage(t)
  local held = false
  for _, m in ipairs(t.models) do held = held or m.held end
  note("sent " .. t.show .. " " .. #t.models .. (held and " held" or ""))
end

%s

local a, e = model("a", 0, 0), model("e", 10, 0)
onLoad({replace = true, watch = {guids = {"a", "e"}},
        fixed = {{points = {{0, 1.4, 0}, {1, 1.4, 0}}, color = {1, 1, 1}, thickness = 0.1}}})
note("y " .. last[1].points[1][2])
ttsbTick()                                  -- first look: nothing has moved
a.x = 5 ttsbTick()                          -- moved: the hub told at once
a.x = 6 ttsbTick()                          -- still moving: not again until 3 ticks on
a.x = 7 a.held_by_color = "Red" ttsbTick()
a.x = 8 ttsbTick()                          -- 3 ticks: told (a is being held)
a.x = 9 ttsbTick()
a.held_by_color = nil ttsbTick()            -- let go: told where it stopped
ttsbTick() ttsbTick()                       -- still: nothing
ttsbSet({fixed = {}})                       -- the hub's redraw: lines only
e.x = 3 ttsbTick()                          -- an enemy moved: told
ttsbSet({fixed = {}, replace = true})       -- something else drawn: nothing watched
e.x = 4 ttsbTick()
print(table.concat(log, "\n"))
"""


@pytest.mark.skipif(not shutil.which("luajit"), reason="LuaJIT isn't installed")
def test_helper_reports_moves(tmp_path):
    lua = tmp_path / "helper.lua"
    lua.write_text(HELPER_HARNESS % overlays.HELPER_SCRIPT)
    run = subprocess.run(["luajit", str(lua)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "lines 1", "y 11.4",                     # drawn where the hub said (the helper is at -10)
        "sent moved 2",
        "sent moved 2 held",
        "sent moved 2",
        "lines 0",
        "sent moved 2",
        "lines 0",
    ]
