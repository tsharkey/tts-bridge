"""
sheetviewer.py — the datasheet viewer script spawned models carry.

Right-click a model and choose **Datasheet** (or hover it and press the key
bound to "Show datasheet" in TTS's Options > Game Keys) to open a floating,
scrollable, draggable window with its unit's whole datasheet (tooltips.card_text),
shown only to the player who opened it. **Threat range on/off** (or the hotkey
of that name) rings the model with how far it moves, advances and charges, from
its base's edge: lines on the model itself, so they go where it goes, worked out
when it was spawned (tooltips.reach), with no hub needed. **Show line of sight**
asks the hub to draw on the table (overlays.py), through
sendExternalMessage({ttsBridge = "overlay", ...}); **Clear overlays** turns off
every model's rings and the hub's lines. A model with more
than one wound is named "[<left>/<max>] <name>" (tooltips.py): **Take a wound** and
**Heal a wound** in its menu, or the hotkeys of those names, count them.

    import sheetviewer
    sheetviewer.attach(obj, unit_name, card_text, reach)   # adds the script to a spawned object

The text lives in the model's own script. The window is one shared panel, added
to the game's screen UI beside whatever the table already has (not replacing
it) the first time any model opens it. A model that already has a script keeps
it: ours goes after it and calls its onLoad first.
"""

import re

import overlays
import tts_bridge as tts

PANEL_ID = "ttsBridgeSheet"
UNDERLINE = "c49bf2"   # tooltips.ABILITIES
MARKER = "-- tts-bridge datasheet viewer"


def rich_text(bbcode):
    """TTS tooltip BBCode ([b], [i], [u], [RRGGBB]…[-]) as the Unity rich text the
    screen UI understands (<b>, <i>, <color=#RRGGBB>…</color>). It can't underline,
    so underlined names (abilities) are shown in the tooltip's ability colour."""
    text = (bbcode or "").replace("<", "‹").replace(">", "›")  # a rule's own "<" isn't a tag
    text = text.replace("[u]", f"<color=#{UNDERLINE}>").replace("[/u]", "</color>")
    text = re.sub(r"\[(/?)(b|i)\]", r"<\1\2>", text)
    text = re.sub(r"\[([0-9a-fA-F]{6})\]", r"<color=#\1>", text)
    return text.replace("[-]", "</color>")


# The window, as a TTS UI element table (UI.setXmlTable). Unity rich text in the
# Text; the layout's content size fitter lets the scroll view scroll.
PANEL_LUA = """{
  tag = "Panel",
  attributes = {id = "%(id)s", active = "false", width = "560", height = "680",
                rectAlignment = "MiddleRight", offsetXY = "-60 0", color = "#15171bF2",
                outline = "#343944", outlineSize = "1 1", allowDragging = "true",
                returnToOriginalPositionWhenReleased = "false"},
  children = {
    {tag = "Text", attributes = {id = "%(id)sTitle", rectAlignment = "UpperLeft", offsetXY = "14 -10",
                                  width = "480", height = "30", fontSize = "18", fontStyle = "Bold",
                                  color = "#e8b53e", alignment = "MiddleLeft"}},
    {tag = "Button", attributes = {id = "%(id)sClose", rectAlignment = "UpperRight", offsetXY = "-8 -8",
                                    width = "30", height = "30", text = "X", fontSize = "16",
                                    color = "#262a31", textColor = "#e6e8ec"}},
    {tag = "VerticalScrollView", attributes = {rectAlignment = "LowerCenter", width = "544", height = "624",
                                                offsetXY = "0 8", scrollSensitivity = "40", color = "#00000000"},
     children = {
       {tag = "VerticalLayout", attributes = {childForceExpandHeight = "false", contentSizeFitter = "vertical",
                                              padding = "8 14 6 6"},
        children = {
          {tag = "Text", attributes = {id = "%(id)sText", fontSize = "14", color = "#e6e8ec",
                                        alignment = "UpperLeft", horizontalOverflow = "Wrap",
                                        verticalOverflow = "Overflow", contentSizeFitter = "vertical"}}}}}},
  },
}""" % {"id": PANEL_ID}

SCRIPT = """%(marker)s: right-click > Datasheet, or the "Show datasheet" hotkey
TTSB_TITLE = %(title)s
TTSB_SHEET = %(sheet)s
TTSB_HAS_SHEET = true
TTSB_REACH = %(reach)s   -- {base = radius, bands = {{reach, color}}}: the threat rings, or nil

local ttsbPanel = %(panel)s

local ttsbEarlierOnLoad = onLoad  -- the model's own script, if it had one
function onLoad(state)
  if ttsbEarlierOnLoad then ttsbEarlierOnLoad(state) end
  self.addContextMenuItem("Datasheet", function(color) ttsBridgeShow({color = color}) end)
  if TTSB_REACH then
    self.addContextMenuItem("Threat range on/off", function() ttsBridgeThreat({}) end)
  end
  -- drawn by the tts-bridge hub (overlays.py), when it's running
  self.addContextMenuItem("Show line of sight", function(color) ttsBridgeOverlay(color, "los") end)
  self.addContextMenuItem("Clear overlays", function(color)
    for _, o in ipairs(getObjects()) do
      if o.getVar("TTSB_REACH") then o.call("ttsBridgeThreat", {on = false}) end
    end
    ttsBridgeOverlay(color, "clear")
  end)
  -- the wound tracker, on models named "[<left>/<max>] <name>" (tooltips.py); the menu stays open
  if ttsbWounds() then
    self.addContextMenuItem("Take a wound", function() ttsBridgeWound({change = -1}) end, true)
    self.addContextMenuItem("Heal a wound", function() ttsBridgeWound({change = 1}) end, true)
  end
  -- one "Show datasheet" hotkey per game, from whichever model loads first
  local owner = Global.getVar("ttsBridgeHotkey")
  if owner == nil or getObjectFromGUID(owner) == nil then
    Global.setVar("ttsBridgeHotkey", self.guid)
    addHotkey("Show datasheet", function(color, hovered)
      if hovered ~= nil and hovered.getVar("TTSB_HAS_SHEET") then hovered.call("ttsBridgeShow", {color = color}) end
    end)
    addHotkey("Threat range on/off", function(color, hovered)
      if hovered ~= nil and hovered.getVar("TTSB_REACH") then hovered.call("ttsBridgeThreat", {}) end
    end)
    addHotkey("Take a wound", function(color, hovered)
      if hovered ~= nil and hovered.getVar("TTSB_HAS_SHEET") then hovered.call("ttsBridgeWound", {change = -1}) end
    end)
    addHotkey("Heal a wound", function(color, hovered)
      if hovered ~= nil and hovered.getVar("TTSB_HAS_SHEET") then hovered.call("ttsBridgeWound", {change = 1}) end
    end)
  end
end

local function ttsbHasPanel(xml)
  for _, element in ipairs(xml) do
    if element.attributes and element.attributes.id == "%(id)s" then return true end
  end
  return false
end

function ttsBridgeShow(params)
  local color = params and params.color
  local function fill()
    UI.setValue("%(id)sTitle", TTSB_TITLE)
    UI.setValue("%(id)sText", TTSB_SHEET)
    UI.setAttribute("%(id)sClose", "onClick", self.getGUID() .. "/ttsBridgeClose")
    if color then UI.setAttribute("%(id)s", "visibility", color) end
    UI.show("%(id)s")
  end
  local xml = UI.getXmlTable() or {}
  if ttsbHasPanel(xml) then fill() return end
  table.insert(xml, ttsbPanel)  -- beside the table's own UI, not instead of it
  UI.setXmlTable(xml)
  Wait.frames(fill, 3)          -- the new UI is built over the next frames
end

-- -> wounds left, most, and the rest of the name; also reads "<left>/<max> <name>",
-- how models spawned before the brackets were named
function ttsbWounds()
  local name = self.getName()
  local left, most, rest = string.match(name, "^%%[(%%d+)/(%%d+)%%] (.*)$")
  if left == nil then left, most, rest = string.match(name, "^(%%d+)/(%%d+) (.*)$") end
  return left, most, rest
end

function ttsBridgeWound(params)
  local left, most, rest = ttsbWounds()
  if left == nil then return end
  left = math.max(0, math.min(tonumber(most), tonumber(left) + params.change))
  self.setName("[" .. left .. "/" .. most .. "] " .. rest)
end

-- the threat rings: on, off, or (on = nil) the other way round. Lines on the model itself,
-- so they move with it; a ring is its reach out from the base's edge, at the base, with its
-- label ('6" MOVE') just outside it, reading from the -z side.
local ttsbRingsOn = false
function ttsBridgeThreat(params)
  local on = params and params.on
  if on == nil then on = not ttsbRingsOn end
  ttsbRingsOn = on
  if not on then
    self.setVectorLines({})
    return
  end
  local b = self.getBounds()
  local base = TTSB_REACH.base or (b.size.x + b.size.z) / 4
  local y = b.center.y - b.size.y / 2 + 0.05
  local lines = {}
  for _, band in ipairs(TTSB_REACH.bands) do
    local points, r = {}, base + band.reach
    for k = 0, 72 do
      local a = 2 * math.pi * k / 72
      table.insert(points, self.positionToLocal({b.center.x + r * math.cos(a), y, b.center.z + r * math.sin(a)}))
    end
    table.insert(lines, {points = points, color = band.color, thickness = 0.12})
    for _, stroke in ipairs(band.label or {}) do
      local letters = {}
      for _, p in ipairs(stroke) do
        table.insert(letters, self.positionToLocal({b.center.x + p[1], y, b.center.z + r + 0.2 + p[2]}))
      end
      table.insert(lines, {points = letters, color = band.color, thickness = 0.05})
    end
  end
  self.setVectorLines(lines)
end

function ttsBridgeOverlay(color, show)
  sendExternalMessage({ttsBridge = "overlay", guid = self.getGUID(), show = show, color = color})
end

function ttsBridgeClose(player, value, id)
  UI.hide("%(id)s")
end
"""


# A stroke font for the rings' labels ('6" MOVE'): each character as polylines on a grid 4
# wide and 6 tall, because vector lines are all a model can draw on itself.
FONT = {
    "0": [[(0, 0), (4, 0), (4, 6), (0, 6), (0, 0), (4, 6)]],
    "1": [[(1, 5), (2, 6), (2, 0)], [(1, 0), (3, 0)]],
    "2": [[(0, 6), (4, 6), (4, 3), (0, 3), (0, 0), (4, 0)]],
    "3": [[(0, 6), (4, 6), (4, 0), (0, 0)], [(1, 3), (4, 3)]],
    "4": [[(0, 6), (0, 3), (4, 3)], [(4, 6), (4, 0)]],
    "5": [[(4, 6), (0, 6), (0, 3), (4, 3), (4, 0), (0, 0)]],
    "6": [[(4, 6), (0, 6), (0, 0), (4, 0), (4, 3), (0, 3)]],
    "7": [[(0, 6), (4, 6), (4, 0)]],
    "8": [[(0, 0), (4, 0), (4, 6), (0, 6), (0, 0)], [(0, 3), (4, 3)]],
    "9": [[(4, 3), (0, 3), (0, 6), (4, 6), (4, 0), (0, 0)]],
    ".": [[(2, 0), (2, 0.6)]],
    '"': [[(1, 6), (1, 4.5)], [(3, 6), (3, 4.5)]],
    "A": [[(0, 0), (2, 6), (4, 0)], [(1, 3), (3, 3)]],
    "C": [[(4, 6), (0, 6), (0, 0), (4, 0)]],
    "D": [[(0, 0), (0, 6), (3, 6), (4, 5), (4, 1), (3, 0), (0, 0)]],
    "E": [[(4, 6), (0, 6), (0, 0), (4, 0)], [(0, 3), (3, 3)]],
    "G": [[(4, 6), (0, 6), (0, 0), (4, 0), (4, 3), (2, 3)]],
    "H": [[(0, 0), (0, 6)], [(4, 0), (4, 6)], [(0, 3), (4, 3)]],
    "M": [[(0, 0), (0, 6), (2, 3), (4, 6), (4, 0)]],
    "N": [[(0, 0), (0, 6), (4, 0), (4, 6)]],
    "O": [[(0, 0), (4, 0), (4, 6), (0, 6), (0, 0)]],
    "R": [[(0, 0), (0, 6), (4, 6), (4, 3), (0, 3), (4, 0)]],
    "V": [[(0, 6), (2, 0), (4, 6)]],
}
LABEL_HEIGHT = 0.5    # inches: how tall a ring's label is
LABELS = {"move": "MOVE", "advance": "ADVANCE", "charge": "CHARGE"}


def label_strokes(text, height=LABEL_HEIGHT):
    """`text` as polylines of [x, z] in inches, centred on x = 0 with its baseline on z = 0,
    reading along +x. Characters FONT hasn't got are left as gaps."""
    unit = height / 6
    width = (len(text) * 6 - 2) * unit
    out = []
    for n, ch in enumerate(text):
        x0 = n * 6 * unit - width / 2
        out += [[[round(x0 + x * unit, 3), round(z * unit, 3)] for x, z in stroke] for stroke in FONT.get(ch, [])]
    return out


def lua_points(strokes):
    return "{" + ", ".join("{" + ", ".join(f"{{{x:g}, {z:g}}}" for x, z in s) + "}" for s in strokes) + "}"


def band_lua(b):
    colour = ", ".join(f"{c:g}" for c in overlays.COLOURS[b["band"]])
    label = label_strokes(f'{b["reach"]:g}" {LABELS[b["band"]]}')
    return f"{{reach = {b['reach']:g}, color = {{{colour}}}, label = {lua_points(label)}}}"


def reach_lua(reach):
    """tooltips.reach as the Lua table TTSB_REACH: each band in its overlay colour, with its
    label ('6" MOVE') as strokes; nil for a model with nothing to ring."""
    if not reach or not reach.get("bands"):
        return "nil"
    bands = ", ".join(band_lua(b) for b in reach["bands"])
    return f"{{base = {'nil' if reach['base'] is None else format(reach['base'], 'g')}, bands = {{{bands}}}}}"


def script(title, card_text, reach=None):
    """The viewer script for one model, with its unit's datasheet in it, and its reach
    (tooltips.reach) for its threat rings."""
    return SCRIPT % {"marker": MARKER, "title": tts.lua_str(title), "sheet": tts.lua_str(rich_text(card_text)),
                     "panel": PANEL_LUA, "id": PANEL_ID, "reach": reach_lua(reach)}


def attach(obj, unit_name, card_text, reach=None):
    """Give a spawned object the viewer, after any script it already has
    (replacing an earlier viewer of ours)."""
    own = obj.get("LuaScript") or ""
    if MARKER in own:
        own = own[:own.index(MARKER)]
    obj["LuaScript"] = (own.rstrip() + "\n\n" if own.strip() else "") + script(unit_name, card_text, reach)
    return obj
