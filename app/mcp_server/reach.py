"""threat_ranges and can_reach: how far units reach (threat.py), from their datasheets and
the weapons their models carry, and what they can do to each other this turn."""

import json

from mcp.server.mcpserver.exceptions import ToolError
from typing_extensions import TypedDict   # see table.py

import board
import data
import los
import threat
import tooltips
import tts_bridge
from app.mcp_server.table import UnitRef

DESCRIPTIONS_LUA = """
local out = {}
for _, g in ipairs(JSON.decode(%s)) do
    local o = getObjectFromGUID(g)
    if o then out[g] = o.getDescription() or "" end
end
return out
"""


class Weapon(TypedDict):
    name: str
    type: str                 # ranged | melee
    range: float | None       # inches; null for melee
    assault: bool


class Band(TypedDict):
    band: str
    avg: float
    max: float


class Ranges(TypedDict):
    unit: UnitRef
    datasheet: str
    move: float | None
    weapons: list[Weapon]
    weapons_from: str         # models (what the spawned models list) | datasheet defaults
    bands: list[Band]


class Charge(TypedDict):
    needed: int
    chance: float


class InRange(TypedDict):
    weapon: str
    range: float
    now: bool
    after_move: bool
    after_advance: bool


class Reach(TypedDict):
    attacker: UnitRef
    target: UnitRef
    gap: float
    engaged: bool
    visible: bool | None
    charge: Charge | None
    weapons: list[InRange]


class Reaches(TypedDict):
    reach: list[Reach]
    out_of_reach: list[UnitRef]    # pairs where nothing reaches this turn: the other unit
    unknown: list[UnitRef]         # units with no datasheet to work from (not spawned by tts-bridge, or not cached)


def descriptions(guids):
    """{guid: description} for objects on the table, in one Lua call."""
    raw = tts_bridge.run_lua(DESCRIPTIONS_LUA % json.dumps(json.dumps(guids)), timeout=20) if guids else {}
    return json.loads(raw) if isinstance(raw, str) else raw or {}


def weapon_names(guids, found):
    """The weapon profiles a unit's models carry, from their tooltips (found: descriptions())."""
    return list(dict.fromkeys(w for g in guids for w in tooltips.weapon_names(found.get(g))))


def weapons_carried(units):
    """{id(unit): [weapon profile names]} from the models' tooltips, one Lua call."""
    found = descriptions([o["guid"] for u in units for o in u["models"]])
    return {id(u): weapon_names([o["guid"] for o in u["models"]], found) for u in units}


def row_profile(row, sheets=None, live=True):
    """(threat profile, where its weapons came from) for a board_summary unit row: its datasheet
    from its tts-bridge:sheet: tag, and on the live table the weapons its models' tooltips list."""
    sheets = data.datasheets_by_id() if sheets is None else sheets
    sheet = sheets.get(row["datasheet"]) if row["datasheet"] else None
    if not sheet:
        raise ValueError(f"{row['unit']} has no cached datasheet"
                         + ("" if row["datasheet"] else " (it wasn't spawned by tts-bridge)") + ".")
    names = weapon_names(row["guids"], descriptions(row["guids"])) if live else []
    return (threat.profile(sheet, weapons=names), "models") if names else (threat.profile(sheet), "datasheet defaults")


def profiles(units, sheets, carried):
    """{id(unit): (threat profile, where its weapons came from)} for the units with a datasheet."""
    out = {}
    for u in units:
        sheet = sheets.get(u.get("datasheet"))
        if sheet:
            names = carried.get(id(u))
            out[id(u)] = (threat.profile(sheet, weapons=names) if names else threat.profile(sheet),
                          "models" if names else "datasheet defaults")
    return out


def find(units, name, army, nth):
    try:
        return board.find_unit(units, name, army, nth)
    except board.UnitError as e:
        raise ToolError(str(e)) from None


def ranging(objs, unit, army=None, nth=None, sheets=None, carried=None) -> Ranges:
    """threat_ranges on objects as board.READ_LUA returns them."""
    u = find(board.collect_units(objs), unit, army, nth)
    sheets = data.datasheets_by_id() if sheets is None else sheets
    if u.get("datasheet") not in sheets:
        raise ToolError(f"{u['name']} has no cached datasheet to work from"
                        + ("" if u.get("datasheet") else " (it wasn't spawned by tts-bridge)") + ".")
    p, source = profiles([u], sheets, weapons_carried([u]) if carried is None else carried)[id(u)]
    return {"unit": board.unit_ref(u), "datasheet": u["datasheet"], "move": p["move"], "weapons": p["weapons"],
            "weapons_from": source, "bands": threat.bands(p)}


def reaching(objs, unit=None, target=None, army=None, nth=None, target_army=None, target_nth=None,
             sheets=None, carried=None, candidates=None) -> Reaches:
    """can_reach on objects as board.READ_LUA returns them."""
    if not unit and not target:
        raise ToolError("Name a unit, a target, or both.")
    units = board.collect_units(objs)
    a = find(units, unit, army, nth) if unit else None
    b = find(units, target, target_army, target_nth) if target else None
    if a is not None and a is b:
        raise ToolError(f'"{unit}" and "{target}" are the same unit.')
    enemies = [u for u in units if u["on_table"] and u["army"] != (a or b)["army"]]
    pairs = [(a, b)] if a and b else [(a, e) for e in enemies] if a else [(e, b) for e in enemies]
    attackers = list({id(x): x for x, _ in pairs}.values())
    sheets = data.datasheets_by_id() if sheets is None else sheets
    known = profiles(attackers, sheets, weapons_carried(attackers) if carried is None else carried)
    if a is not None and id(a) not in known:
        raise ToolError(f"{a['name']} has no cached datasheet to work from"
                        + ("" if a.get("datasheet") else " (it wasn't spawned by tts-bridge)") + ".")
    found = board.find_layout(objs, candidates)
    terrain = los.blockers(found[0]) if found else None
    surface = board.table_surface(objs)

    def models(u):
        return [los.model(p) for p in board.positions(u["models"], surface)]
    out = {"reach": [], "out_of_reach": [], "unknown": []}
    for x, y in pairs:
        if id(x) not in known:
            out["unknown"].append(board.unit_ref(x))
            continue
        visible = los.unit_visibility(models(x), models(y), terrain)["visible"] > 0 if terrain else None
        got = threat.threats(known[id(x)][0], board.closest(x["models"], y["models"]), visible)
        if not (got["engaged"] or got["charge"] or any(w["after_move"] or w["after_advance"] for w in got["weapons"])):
            out["out_of_reach"].append(board.unit_ref(y if a else x))
            continue
        out["reach"].append({"attacker": board.unit_ref(x), "target": board.unit_ref(y), **got})
    out["reach"].sort(key=lambda r: (-sum(w["now"] for w in r["weapons"]),
                                     -(r["charge"]["chance"] if r["charge"] else 0), r["gap"]))
    return out


def threat_ranges(unit: str, army: str | None = None, nth: int | None = None) -> Ranges:
    """How far a unit on the table reaches this turn, from its datasheet: "move" (its slowest
    model's M), its "weapons" (what its models carry, from their tooltips, else the datasheet's
    default wargear: "weapons_from" says which), and "bands": each reach in inches from the
    edges of its bases, as average and max: move; advance (M + D6); charge (M + 2D6, to base
    contact); "shoot: <weapon>" (M + range); "advance and shoot: <weapon>"
    for Assault weapons (M + D6 + range). unit / army / nth: as in measure. Straight-line
    distances: terrain and other models in the way, Fly, Deep Strike and re-rolls aren't counted."""
    return ranging(board.read_objects(), unit, army, nth)


def can_reach(unit: str | None = None, target: str | None = None, army: str | None = None, nth: int | None = None,
              target_army: str | None = None, target_nth: int | None = None) -> Reaches:
    """What units on the table can do to each other this turn. Name both for one attacker and
    one target; only `unit` for what it can reach among the enemy units; only `target` for
    the enemy units that can reach it. Names as in measure.

    Each entry in "reach": "gap" (closest base to base), "engaged" (within 2"), "visible"
    (whether the attacker can see the target now, from the layout's terrain as line_of_sight
    works it out; null with no layout), "charge" (the 2D6 roll needed to reach base contact
    after a full move, and the chance of making it; null when that's more than 12), and each ranged weapon: in range "now" (and
    seen), "after_move", "after_advance" (Assault weapons). Most dangerous first.
    "out_of_reach": the other units nothing reaches this turn. "unknown": units with no
    datasheet to work from. Weapons are what the attacker's models carry. Straight-line
    distances, as in threat_ranges."""
    return reaching(board.read_objects(), unit, target, army, nth, target_army, target_nth)


TOOLS = [threat_ranges, can_reach]
