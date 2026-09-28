"""The datasheet viewer script (sheetviewer.py). Its Lua is run under LuaJIT, with
TTS's API faked, when LuaJIT is installed (skipped otherwise, e.g. in CI)."""

import shutil
import subprocess

import pytest

import sheetviewer

CARD = "[e8b53e]Leader of X[-]\n[9aa1ad][b]ABILITIES[/b][-]\n[b]Sunforge:[/b] re-roll if D6 < 3"

# Just enough of TTS for the script: a model (self), Global vars, the screen UI
# (which already has a mod's panel), Wait, hotkeys. Prints what happened.
HARNESS = r"""
local log = {}
local function note(k, v) table.insert(log, k .. "=" .. tostring(v)) end
self = {guid = "abc123"}
function self.getGUID() return "abc123" end
local name = "3/3 Crisis Shas'vre"
function self.getName() return name end
function self.setName(n) name = n; note("name", n) end
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
self.menus["Show threat range"]("Green")
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
    obj = sheetviewer.attach({"LuaScript": "function onLoad(s) earlierOnLoad = s end"},
                             "Crisis Sunforge Battlesuits", CARD)
    lua = tmp_path / "viewer.lua"
    lua.write_text(HARNESS % obj["LuaScript"])
    run = subprocess.run(["luajit", str(lua)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    log = run.stdout.splitlines()
    assert log.count("menu=Datasheet") == 2 and log.count("hotkey=Show datasheet") == 1
    # the overlay items ask the hub to draw (app/mcp_server/overlay.py menu_request)
    assert [line for line in log if line.startswith("external=")] == [
        "external=overlay:abc123:threat:Green", "external=overlay:abc123:los:Green", "external=overlay:abc123:clear:Green"]
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
