"""
LCT table setup (dispositions, matchup, layout). Everything goes through the
Start Menu object's own functions, exactly like clicking its buttons.
"""

import json
import time

import terrain
import tts_bridge as tts
from app.core.tts import lock as tts_lock

START_MENU = "738804"
DISPOSITIONS = ["Disruption", "Priority Assets", "Purge the Foe", "Reconnaissance", "Take and Hold"]
DEPLOYMENTS = {"SE": "Sweeping Engagement", "CoB": "Crucible of Battle", "TP": "Tipping Point",
               "SnD": "Search and Destroy", "DoW": "Dawn of War", "HnA": "Hammer and Anvil"}

MATCHUPS_LUA = """
local m = getObjectFromGUID("738804")
if not m then return "no-lct" end
local out = {}
local missions = Global.getTable("missionMatchups") or {}
for key, deck in pairs(m.getTable("deploymentMatrixDecks") or {}) do
  local label, layouts = nil, {}
  for _, v in pairs(deck) do
    if type(v) == "string" and v:find(" vs ") then label = v end
    if type(v) == "table" then
      for _, c in pairs(v) do if type(c) == "table" and c.name then table.insert(layouts, c.name) end end
    end
  end
  local mm = missions[key] or {}
  out[key] = {label = label, layouts = layouts, red = mm.red, blue = mm.blue}
end
-- layout art: one card per map in deck fb4b5d, each with its own image
local art = {}
local deck = getObjectFromGUID("fb4b5d")
if deck then
  for _, c in ipairs(deck.getData().ContainedObjects or {}) do
    for _, sheet in pairs(c.CustomDeck or {}) do
      art[#art + 1] = {name = c.Nickname, url = sheet.FaceURL}
      break
    end
  end
end
return {matchups = out, art = art}
"""
_matchups = None


def lct_matchups():
    global _matchups
    if _matchups is None:
        with tts_lock:
            r = tts.run_lua(MATCHUPS_LUA, timeout=10)
        if r in (None, "no-lct"):
            raise ValueError("Load the LCT table in TTS first")
        raw = json.loads(r)
        # art card "Rec vs Rec 1 - Sweeping Engagement" belongs to map "Rec vs Rec 1"
        art = {a["name"].split(" - ")[0].strip(): a["url"] for a in raw["art"] if a.get("name")}
        _matchups = {}
        for key, v in raw["matchups"].items():
            layouts = []
            for name in sorted(v["layouts"]):   # "Rec vs Rec 1 - SE"
                prefix, _, code = name.rpartition(" - ")
                layouts.append({"card": prefix, "deployment": DEPLOYMENTS.get(code, code),
                                "art": art.get(prefix)})
            _matchups[key] = {"label": v["label"], "red_mission": v["red"], "blue_mission": v["blue"],
                              "layouts": layouts}
    return _matchups


def lua_menu(expr):
    return tts.run_lua(f'local m = getObjectFromGUID("{START_MENU}") {expr}', timeout=10)


FIND_MAP_CARD_LUA = """
for _, o in ipairs(getObjects()) do
  if o.tag == "Card" and o.getName():sub(1, {n}) == {prefix} then
    for _, b in ipairs(o.getButtons() or {{}}) do
      if b.click_function == "loadMap" then return o.guid .. "|" .. o.getName() end
    end
  end
end
return "none"
"""


def menu_buttons():
    r = lua_menu('local out = {} for _, b in ipairs(m.getButtons() or {}) do table.insert(out, b.click_function) end '
                 'return out')
    return set(json.loads(r or "[]"))


def lct_setup(red, blue, layout):
    """Drive LCT's Start Menu: 1v1, dispositions, deal, load the chosen layout."""
    matchup = lct_matchups()[f"{red}_{blue}"]
    card_prefix = matchup["layouts"][layout]["card"] + " - "
    find_card = FIND_MAP_CARD_LUA.format(n=len(card_prefix), prefix=json.dumps(card_prefix))
    with tts_lock:
        if lua_menu('return m.getVar("gameMode")') != "game":
            lua_menu('m.call("confirmStandardGame") return 1')
            time.sleep(2)
        if "backToSelection" in menu_buttons():
            # a layout is already loaded; LCT only deals layout cards from the selection screen
            lua_menu('m.call("backToSelection") return 1')
            time.sleep(3)
        for color, target in (("red", red), ("blue", blue)):
            for _ in range(6):
                if str(lua_menu(f'return tostring(m.getVar("{color}DispositionSelected"))')) == str(target):
                    break
                lua_menu(f'm.call("{color}DispositionUp") return 1')
                time.sleep(0.4)
            else:
                raise RuntimeError(f"Couldn't set {color} disposition")
        card = tts.run_lua(find_card, timeout=10)
        if card in (None, "none"):
            lua_menu('m.call("generateMission") return 1')
            for _ in range(40):
                time.sleep(0.5)
                if lua_menu('return tostring(m.getVar("missionDealInProgress"))') == "true":
                    continue
                card = tts.run_lua(find_card, timeout=10)
                if card not in (None, "none"):
                    break
        if card in (None, "none"):
            raise RuntimeError(f"LCT didn't deal the {card_prefix[:-3]} card")
        guid, name = card.split("|", 1)
        tts.run_lua(f'local c = getObjectFromGUID("{guid}") c.call("loadMap", {{c, "Red", false}}) return 1')
        time.sleep(2.5)  # terrain spawns ~0.5s after the wipe; zones and objectives just after
        try:   # the terrain the models' line of sight works from (terrain.py)
            written = terrain.send()["name"]
        except (ValueError, SystemExit) as e:
            written = f"not written: {e}"
    return {"loaded": name, "matchup": matchup["label"], "terrain": written,
            "red_mission": matchup["red_mission"], "blue_mission": matchup["blue_mission"]}
