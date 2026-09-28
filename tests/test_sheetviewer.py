"""The datasheet viewer script (sheetviewer.py). Its Lua is run under LuaJIT, with
TTS's API faked, when LuaJIT is installed (skipped otherwise, e.g. in CI)."""

import math
import shutil
import subprocess

import pytest

import overlays
import sheetviewer

CARD = "[e8b53e]Leader of X[-]\n[9aa1ad][b]ABILITIES[/b][-]\n[b]Sunforge:[/b] re-roll if D6 < 3"

# Just enough of TTS for the script: a model (self), Global vars, the screen UI
# (which already has a mod's panel), Wait, hotkeys. Prints what happened.
HARNESS = r"""
local log = {}
local function note(k, v) table.insert(log, k .. "=" .. tostring(v)) end
self = {guid = "abc123"}
function self.getGUID() return "abc123" end
function self.getVar(k) return _G[k] end
local name = "3/3 Crisis Shas'vre"
function self.getName() return name end
function self.setName(n) name = n; note("name", n) end
-- a model 2" wide standing at (10, 1, 5); its local space is the table's, moved to it
function self.getBounds() return {center = {x = 10, y = 2, z = 5}, size = {x = 2, y = 2, z = 2}} end
function self.positionToLocal(p) return {p[1] - 10, p[2] - 1, p[3] - 5} end
function self.setVectorLines(lines)
  local rings, dashes, strokes, far, top = 0, 0, 0, 0, 0
  for _, l in ipairs(lines) do
    if l.thickness ~= 0.12 then strokes = strokes + 1
    elseif #l.points == 3 then dashes = dashes + 1
    else rings = rings + 1 end
    for _, p in ipairs(l.points) do far, top = math.max(far, p[1]), math.max(top, p[3]) end
  end
  note("rings", rings .. (rings > 0 and string.format(" dashes %%d %%.2f y%%.2f labels %%d to %%.2f", dashes, far,
                                                      lines[1].points[1][2], strokes, top) or ""))
end
function self.call(fn, params) _G[fn](params) end
function getObjects() return {self} end
self.menus = {}
function self.addContextMenuItem(label, fn) note("menu", label); self.menus[label] = fn end
function sendExternalMessage(t) note("external", t.ttsBridge .. ":" .. t.guid .. ":" .. t.show .. ":" .. t.color) end
local vars = {}
Global = {getVar = function(k) return vars[k] end, setVar = function(k, v) vars[k] = v end}
local xml = {{tag = "Panel", attributes = {id = "lctStartMenu"}}}
UI = {
  getXmlTable = function() return xml end,
  setXmlTable = function(t) xml = t; note("setXmlTable", #t) end,
  setValue = function(id, v) note("value:" .. id, #v > 40 and v:sub(1, 40) or v) end,
  setAttribute = function(id, k, v) note("attr:" .. id .. ":" .. k, v) end,
  show = function(id) note("show", id) end,
  hide = function(id) note("hide", id) end,
}
Wait = {frames = function(fn, n) fn() end}
function getObjectFromGUID(g) if g == "abc123" then return self end end
function addHotkey(label, fn) note("hotkey", label) end

%s

onLoad("saved state")
self.menus["Datasheet"]("Red")
self.menus["Threat range on/off"]()     -- on
self.menus["Threat range on/off"]()     -- off
self.menus["Threat range on/off"]()     -- on again: Clear overlays turns it off
self.menus["Show line of sight"]("Green")
self.menus["Clear overlays"]("Green")
ttsBridgeShow({color = "Blue"})
for _ = 1, 4 do self.menus["Take a wound"]() end   -- never below 0
self.menus["Heal a wound"]()
ttsBridgeClose(nil, nil, nil)
onLoad("again")  -- another model loading: no second hotkey
note("earlier", earlierOnLoad)
note("panels", #xml)
note("first panel", xml[1].attributes.id)
print(table.concat(log, "\n"))
"""


def test_underline_is_the_ability_colour():
    assert sheetviewer.rich_text("[b][u]Hold Fast[/u][/b]") == "<b><color=#c49bf2>Hold Fast</color></b>"


def test_rich_text():
    assert sheetviewer.rich_text(CARD) == (
        "<color=#e8b53e>Leader of X</color>\n<color=#9aa1ad><b>ABILITIES</b></color>\n"
        "<b>Sunforge:</b> re-roll if D6 ‹ 3")


def test_attach_keeps_and_replaces():
    obj = {"LuaScript": "function onLoad(s) earlierOnLoad = s end"}
    sheetviewer.attach(obj, "Crisis Sunforge Battlesuits", CARD)
    first = obj["LuaScript"]
    assert first.startswith("function onLoad(s) earlierOnLoad = s end\n\n" + sheetviewer.MARKER)
    sheetviewer.attach(obj, "Crisis Sunforge Battlesuits", CARD + " more")
    assert obj["LuaScript"].count(sheetviewer.MARKER) == 1 and "more" in obj["LuaScript"]  # replaced, not stacked
    assert sheetviewer.attach({}, "Unit", CARD)["LuaScript"].startswith(sheetviewer.MARKER)


@pytest.mark.skipif(not shutil.which("luajit"), reason="LuaJIT isn't installed")
def test_script_runs_in_lua(tmp_path):
    reach = {"base": 0.5, "bands": [{"band": "move", "reach": 6}, {"band": "advance", "reach": 12},
                                    {"band": "charge", "reach": 20}]}
    obj = sheetviewer.attach({"LuaScript": "function onLoad(s) earlierOnLoad = s end"},
                             "Crisis Sunforge Battlesuits", CARD, reach)
    lua = tmp_path / "viewer.lua"
    lua.write_text(HARNESS % obj["LuaScript"])
    run = subprocess.run(["luajit", str(lua)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    log = run.stdout.splitlines()
    assert log.count("menu=Datasheet") == 2 and log.count("hotkey=Show datasheet") == 1
    # threat rings: drawn on the model, no hub, at its base; move and advance solid, the charge
    # dashed (about one dash an inch round it: 0.5 + 20 from its centre), with the labels
    # ('6" MOVE' and the rest) just outside their rings (20.5 + 0.2, 0.5 tall)
    labels = sum(len(sheetviewer.label_strokes(f'{r}" {n}')) for r, n in ((6, "MOVE"), (12, "ADVANCE"), (20, "CHARGE")))
    drawn = f"rings=2 dashes {math.floor(2 * math.pi * 20.5)} 20.50 y0.05 labels {labels} to 21.20"
    assert [line for line in log if line.startswith("rings=")] == [drawn, "rings=0", drawn, "rings=0"]
    assert "hotkey=Threat range on/off" in log
    # line of sight and clearing ask the hub to draw (app/mcp_server/overlay.py menu_request)
    assert [line for line in log if line.startswith("external=")] == [
        "external=overlay:abc123:los:Green", "external=overlay:abc123:clear:Green"]
    # the first opening adds the window beside the mod's own UI; the second reuses it
    assert log.count("setXmlTable=2") == 1 and "panels=2" in log and "first panel=lctStartMenu" in log
    assert "value:ttsBridgeSheetTitle=Crisis Sunforge Battlesuits" in log
    assert any(line.startswith("value:ttsBridgeSheetText=<color=#e8b53e>Leader of X") for line in log)
    assert "attr:ttsBridgeSheetClose:onClick=abc123/ttsBridgeClose" in log
    assert ["attr:ttsBridgeSheet:visibility=Red", "attr:ttsBridgeSheet:visibility=Blue"] == \
        [line for line in log if line.startswith("attr:ttsBridgeSheet:visibility")]
    assert log.count("show=ttsBridgeSheet") == 2 and "hide=ttsBridgeSheet" in log
    assert "earlier=again" in log  # the model's own onLoad still runs
    # the wound tracker counts down to 0 and back up, in the model's name
    # (from a name without brackets, as models spawned before them were named)
    assert [line for line in log if line.startswith("name=")] == [
        "name=[2/3] Crisis Shas'vre", "name=[1/3] Crisis Shas'vre", "name=[0/3] Crisis Shas'vre",
        "name=[0/3] Crisis Shas'vre", "name=[1/3] Crisis Shas'vre"]
    assert "hotkey=Take a wound" in log and "hotkey=Heal a wound" in log


def test_rings():
    """Movement in greens, weapons in blues; solid from where it stands, dashed after moving."""
    bands = [{"band": "move", "reach": 6}, {"band": "advance", "reach": 12}, {"band": "charge", "reach": 18},
             {"band": "range: Bolt rifle", "reach": 24}, {"band": "shoot: Bolt rifle", "reach": 30},
             {"band": "range: Bolt pistol", "reach": 12}, {"band": "shoot: Bolt pistol", "reach": 18},
             {"band": "range: Stalker bolt rifle", "reach": 24}, {"band": "shoot: Stalker bolt rifle", "reach": 30}]
    c, s = overlays.COLOURS, overlays.SHOTS
    assert sheetviewer.rings(bands) == [
        (6, c["move"], "MOVE", False), (12, c["advance"], "ADVANCE", False), (18, c["charge"], "CHARGE", True),
        (12, s[0], "Bolt pistol", False), (18, s[0], "Bolt pistol +MOVE", True),
        (24, s[1], "Bolt rifle / Stalker bolt rifle", False),   # the same range: one pair of rings
        (30, s[1], "Bolt rifle / Stalker bolt rifle +MOVE", True)]


def test_reach_as_lua():
    reach = {"base": 0.63, "bands": [{"band": "move", "reach": 6}, {"band": "charge", "reach": 18}]}
    lua = sheetviewer.reach_lua(reach)
    assert lua.startswith("{base = 0.63, bands = {{reach = 6, color = {0.55, 0.93, 0.55}, dashed = false, row = 0, label = {{")
    assert "{reach = 18, color = {0.13, 0.55, 0.3}, dashed = true, row = 0, label = " in lua
    assert sheetviewer.reach_lua({"base": None, "bands": [{"band": "move", "reach": 5}]}).startswith("{base = nil,")
    assert sheetviewer.reach_lua({"base": 1, "bands": []}) == sheetviewer.reach_lua(None) == "nil"   # can't move
    both = sheetviewer.reach_lua({"base": 0.63, "bands": [{"band": "advance", "reach": 12},
                                                          {"band": "range: Bolt pistol", "reach": 12}]})
    assert "dashed = false, row = 0," in both and "dashed = false, row = 1," in both   # two 12" labels: stacked


def test_label_strokes():
    strokes = sheetviewer.label_strokes('6" MOVE')
    assert len(strokes) == sum(len(sheetviewer.FONT.get(c, [])) for c in '6" MOVE')   # the space is a gap
    xs = [x for s in strokes for x, _ in s]
    zs = [z for s in strokes for _, z in s]
    assert min(xs) == -max(xs) and (min(zs), max(zs)) == (0, sheetviewer.LABEL_HEIGHT)   # centred, on the baseline
    for band in ("move", "advance", "charge"):                     # every label can be written
        assert all(c in sheetviewer.FONT or c == " " for c in f'12.5" {sheetviewer.LABELS[band]}')
