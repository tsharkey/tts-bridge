"""The terrain object on the TTS table and the line of sight it works out in the game
(terrain.py, issue #122): its Lua agrees with los.py, draws from where models are, and is
written onto the table by the hub when asked. Runs the Lua under LuaJIT, with TTS faked,
when it's installed (skipped otherwise, e.g. in CI)."""

import json
import math
import shutil
import subprocess

import pytest

import board
import los
import overlays
import terrain
import tts_bridge
from app.mcp_server import table
from test_formats import load, made_up_table

LAYOUT = load("layout.json")
luajit = pytest.mark.skipif(not shutil.which("luajit"), reason="LuaJIT isn't installed")


def run_lua(tmp_path, body, surface=1.0):
    """Run the terrain object's script, then `body`, under LuaJIT; -> what it printed."""
    lua = tmp_path / "terrain.lua"
    lua.write_text("self = {positionToLocal = function(p) return p end}\n"
                   + terrain.script(LAYOUT, surface) + "\n" + body)
    run = subprocess.run(["luajit", str(lua)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return run.stdout


# models to look from: in the open, up against terrain, inside an area, and up on a floor
SPOTS = [(-20, 13, 0.63, 0.0), (0, 0, 0.63, 0.0), (5, -5, 0.8, 0.0), (-24, 0, 0.63, 0.0), (-24, 0, 0.63, 3.5),
         (12, 10, 1.3, 0.0)]


@luajit
def test_visibility_matches_los(tmp_path):
    body = "\n".join(
        f'local p = ttsbVisibility({{x = {x}, z = {z}, r = {r}, height = {h}}}) '
        f'local s = {{}} for _, q in ipairs(p) do table.insert(s, string.format("[%.3f,%.3f]", q[1], q[2])) end '
        f'print("[" .. table.concat(s, ",") .. "]")' for x, z, r, h in SPOTS)
    got = [json.loads(line) for line in run_lua(tmp_path, body).splitlines()]
    blockers = los.blockers(LAYOUT)
    for (x, z, r, h), poly in zip(SPOTS, got):
        want = los.visibility_polygon({"x": x, "z": z, "r": r, "height": h}, blockers)
        assert len(poly) == len(want), (x, z, h)
        assert all(math.dist(p, q) < 0.01 for p, q in zip(poly, want)), (x, z, h)


@luajit
def test_sight_matches_los(tmp_path):
    pairs = [(a, b) for a in SPOTS for b in SPOTS if a != b]
    body = "\n".join(f"print(ttsbSight({{x = {a[0]}, z = {a[1]}, r = {a[2]}, height = {a[3]}}}, "
                     f"{{x = {b[0]}, z = {b[1]}, r = {b[2]}, height = {b[3]}}}))" for a, b in pairs)
    got = run_lua(tmp_path, body).split()
    blockers = los.blockers(LAYOUT)
    want = [los.sight(*({"x": m[0], "z": m[1], "r": m[2], "height": m[3]} for m in pair), blockers)[0]
            for pair in pairs]
    assert got == want and {"full", "none"} <= set(got)   # some seen, some blocked


# the sample table as TTS objects: bounds, GM Notes, tags and descriptions
TABLE = r"""
local objects = {}
for _, t in ipairs(%(objs)s) do
  local o = {}
  o.getGUID = function() return t.guid end
  o.getGMNotes = function() return t.notes end
  o.getTags = function() return t.tags end
  o.getDescription = function() return t.head end
  o.getBounds = function() return {center = {x = t.c[1], y = t.c[2], z = t.c[3]}, size = {x = t.s[1], y = t.s[2], z = t.s[3]}} end
  objects[t.guid] = o
  table.insert(objects, o)
end
function getObjects() local out = {} for _, o in ipairs(objects) do table.insert(out, o) end return out end
function getObjectFromGUID(g) return objects[g] end
Player = {Green = {seated = true}}
function broadcastToColor(text, color) print("told " .. color .. ": " .. text) end
function self.setVectorLines(lines)
  local fill, outline, seen = 0, 0, {}
  for _, l in ipairs(lines) do
    if l.loop then outline = outline + 1
    elseif #l.color == 4 then fill = fill + 1
    else table.insert(seen, string.format("%%.1f,%%.1f:%%.2f", l.points[2][1], l.points[2][3], l.color[2])) end
  end
  table.sort(seen)
  print("fill " .. fill .. " outline " .. outline .. " seen " .. table.concat(seen, " "))
end
"""


def objects_lua(objs):
    return terrain.lua_value([{"guid": o["guid"], "notes": o["notes"], "tags": o.get("tags") or [],
                               "head": o["head"], "c": o["c"], "s": o["s"]} for o in objs])


@luajit
def test_line_of_sight_on_the_table(tmp_path):
    objs = made_up_table()
    body = TABLE % {"objs": objects_lua(objs)} + """
ttsbLineOfSight({guid = "a1b2c1", color = "Green"})
ttsbRefresh({color = "Green"})
ttsbClear()
print(ttsbRefresh({color = "Green"}))   -- cleared: nothing to refresh
"""
    out = run_lua(tmp_path, body).splitlines()
    # what the hub would draw for the same unit (overlays.los_lines), to compare
    st = table.summary(objs, [LAYOUT])
    i = next(i for i, u in enumerate(st["units"]) if u["unit"] == "Pathfinder Team" and u["on_table"])
    lines = overlays.los_lines(st["units"][i], st, 0)
    seen = sorted(f"{ln['points'][1][0]:.1f},{ln['points'][1][2]:.1f}:{ln['color'][1]:.2f}" for ln in lines[1:])
    assert seen   # the Pathfinders see some of the Intercessors
    drawn = [line for line in out if line.startswith("fill")]
    assert len(drawn) == 3 and drawn[0] == drawn[1] and drawn[2].startswith("fill 0 outline 0")   # cleared
    fill = int(drawn[0].split()[1])
    assert drawn[0] == f"fill {fill} outline 1 seen {' '.join(seen)}" and fill > 20   # a see-through fill too
    told = [line for line in out if line.startswith("told")]
    full = ":%.2f" % overlays.COLOURS["full"][1]
    fully = sum(s.endswith(full) for s in seen)
    assert told == [f"told Green: tts-bridge: line of sight: it sees {len(seen)} enemy models ({fully} fully)."] * 2
    assert out[-1] == "0"


@luajit
def test_terrain_that_isnt_on_the_table_any_more(tmp_path):
    objs = [o for o in made_up_table() if o["notes"]]   # the models only: the terrain's gone
    out = run_lua(tmp_path, TABLE % {"objs": objects_lua(objs)} + 'ttsbLineOfSight({guid = "a1b2c1", color = "Green"})')
    assert out.splitlines() == ["fill 0 outline 0 seen ",
                                f"told Green: tts-bridge: the terrain on the table isn't {LAYOUT['name']} any more. "
                                "Send the terrain again from the hub's Board page."]


def test_send_writes_the_layout_on_the_table(monkeypatch):
    sent = []
    monkeypatch.setattr(tts_bridge, "execute", lambda script, timeout=None: sent.append(script) or {"ok": True, "result": 1})
    monkeypatch.setattr(board.layouts, "load_all", lambda: [LAYOUT])
    objs = made_up_table()
    assert terrain.send(objs) == {"layout": LAYOUT["id"], "name": LAYOUT["name"]}
    script = sent[-1]
    assert tts_bridge.lua_str(terrain.NOTES + LAYOUT["id"]) in script and "o.destruct()" in script   # replaces the last
    assert "TTSB_TERRAIN_VERSION" in script and "ttsbLineOfSight" in script
    with pytest.raises(ValueError, match="None of the LCT layouts"):
        terrain.send([o for o in objs if o["notes"]])                 # no terrain on the table


def test_the_game_says_which_layout(monkeypatch):
    other = {**LAYOUT, "id": "other1", "name": "Another"}
    monkeypatch.setattr(board.layouts, "load_all", lambda: [LAYOUT, other])
    objs = made_up_table()
    ours = {"guid": "t3rra1", "tag": "BlockSquare", "name": "tts-bridge terrain", "head": "", "locked": True,
            "notes": terrain.NOTES + "other1", "rot": 0, "p": [0, -12, 0], "c": [0, -12, 0], "s": [1, 1, 1]}
    assert board.written_layout(objs + [ours]) == [other]            # the terrain object's layout
    assert board.written_layout(objs) is None                        # none there: every layout
    assert not any(t["guid"] == "t3rra1" for t in board.collect_terrain(objs + [ours]))   # not a terrain piece


def test_a_model_asking_for_the_terrain(monkeypatch):
    told = []
    monkeypatch.setattr(tts_bridge, "run_lua", lambda script, timeout=None: told.append(script) or 1)
    monkeypatch.setattr(terrain, "send", lambda: {"layout": "x", "name": "Crucible"})
    terrain.request({"ttsBridge": "terrain", "color": "Blue"})
    assert "Crucible is on the table now" in told[-1] and tts_bridge.lua_str("Blue") in told[-1]

    def fails():
        raise ValueError("None of the LCT layouts tts-bridge knows matches the terrain on the table.")
    monkeypatch.setattr(terrain, "send", fails)
    terrain.request({"ttsBridge": "terrain", "color": "Blue"})
    assert "None of the LCT layouts" in told[-1]


def test_lua_value():
    assert terrain.lua_value({"a": [1, 2.5, True, None], "b": 'say "hi"'}) == \
        '{a = {1, 2.5, true, nil}, b = ' + tts_bridge.lua_str('say "hi"') + '}'
