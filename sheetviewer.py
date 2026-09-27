"""
sheetviewer.py — the datasheet viewer script spawned models carry.

Right-click a model and choose **Datasheet** (or hover it and press the key
bound to "Show datasheet" in TTS's Options > Game Keys) to open a floating,
scrollable, draggable window with its unit's whole datasheet (tooltips.card_text),
shown only to the player who opened it.

    import sheetviewer
    sheetviewer.attach(obj, unit_name, card_text)   # adds the script to a spawned object

The text lives in the model's own script. The window is one shared panel, added
to the game's screen UI beside whatever the table already has (not replacing
it) the first time any model opens it. A model that already has a script keeps
it: ours goes after it and calls its onLoad first.
"""

import re

import tts_bridge as tts

PANEL_ID = "ttsBridgeSheet"
MARKER = "-- tts-bridge datasheet viewer"


def rich_text(bbcode):
    """TTS tooltip BBCode ([b], [i], [RRGGBB]…[-]) as the Unity rich text the
    screen UI understands (<b>, <i>, <color=#RRGGBB>…</color>)."""
    text = (bbcode or "").replace("<", "‹").replace(">", "›")  # a rule's own "<" isn't a tag
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

local ttsbPanel = %(panel)s

local ttsbEarlierOnLoad = onLoad  -- the model's own script, if it had one
function onLoad(state)
  if ttsbEarlierOnLoad then ttsbEarlierOnLoad(state) end
  self.addContextMenuItem("Datasheet", function(color) ttsBridgeShow({color = color}) end)
  -- one "Show datasheet" hotkey per game, from whichever model loads first
  local owner = Global.getVar("ttsBridgeHotkey")
  if owner == nil or getObjectFromGUID(owner) == nil then
    Global.setVar("ttsBridgeHotkey", self.guid)
    addHotkey("Show datasheet", function(color, hovered)
      if hovered ~= nil and hovered.getVar("TTSB_HAS_SHEET") then hovered.call("ttsBridgeShow", {color = color}) end
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

function ttsBridgeClose(player, value, id)
  UI.hide("%(id)s")
end
"""


def script(title, card_text):
    """The viewer script for one model, with its unit's datasheet in it."""
    return SCRIPT % {"marker": MARKER, "title": tts.lua_str(title), "sheet": tts.lua_str(rich_text(card_text)),
                     "panel": PANEL_LUA, "id": PANEL_ID}


def attach(obj, unit_name, card_text):
    """Give a spawned object the viewer, after any script it already has
    (replacing an earlier viewer of ours)."""
    own = obj.get("LuaScript") or ""
    if MARKER in own:
        own = own[:own.index(MARKER)]
    obj["LuaScript"] = (own.rstrip() + "\n\n" if own.strip() else "") + script(unit_name, card_text)
    return obj
