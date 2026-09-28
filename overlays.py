"""
overlays.py — draw line of sight and threat ranges on the TTS table itself, as vector lines.

    import overlays
    overlays.show(state, unit_index, ["los", "charge"])   # state: board_summary's data (app.mcp_server.table)
    overlays.clear()
    overlays.draw(overlays.shape_lines(shapes, y))   # Claude's highlights (app/mcp_server/shared.py)

The lines hang off one helper object tts-bridge spawns under the table (locked, not
interactable), never off Global, so the table's own lines and other mods' are left alone,
and clearing just removes the helper. Its script removes it when a save is loaded, so
nothing drawn outlives the session it was drawn in.

What's drawn, just above the mats on the table (the board's and the terrain areas', which sit
a little above the table surface):
- A threat band (move, advance, charge, "shoot: <weapon>"; threat.bands) as the outline of
  everything within that reach of any of the unit's bases: the union of circles, one ring
  round the unit rather than one per model.
- Line of sight ("los") as the visibility outline of the model nearest the unit's middle
  (los.visibility_polygon), and a line to every enemy model the unit sees, from the nearest
  of its models that sees it: green when fully visible, yellow when partly.
"""

import json
import math

import los
import threat
import tts_bridge as tts

HELPER_NOTES = "tts-bridge:overlays"
LIFT = 0.5             # inches above the table surface: clear of the board and terrain-area mats on it
RING_STEPS = 120       # points round a full circle
COLOURS = {"move": [0.31, 0.77, 0.48], "advance": [0.91, 0.71, 0.24], "charge": [1.0, 0.55, 0.26],
           "los": [1.0, 0.96, 0.82], "full": [0.31, 0.9, 0.48], "partial": [0.95, 0.85, 0.25]}
SHOTS = [[0.7, 0.55, 1.0], [0.29, 0.84, 0.84], [1.0, 0.36, 0.62], [0.6, 0.82, 0.29], [1.0, 0.82, 0.29]]

HELPER_SCRIPT = """-- tts-bridge overlays: line of sight and threat ranges drawn by tts-bridge.
-- Removed when a save is loaded, so nothing it draws outlives the session.
function onSave() return "saved" end
function onLoad(state) if state == "saved" then self.destruct() end end
"""

DRAW_LUA = """
local lines = JSON.decode(%(lines)s)
local helper = nil
for _, o in ipairs(getObjects()) do
  if o.getGMNotes() == "%(notes)s" then helper = o end
end
if helper == nil then
  helper = spawnObjectData({data = {Name = "BlockSquare", Nickname = "tts-bridge overlays",
    GMNotes = "%(notes)s", Locked = true, Tooltip = false, LuaScript = %(script)s,
    Transform = {posX = 0, posY = %(below)s, posZ = 0, rotX = 0, rotY = 0, rotZ = 0,
                 scaleX = 1, scaleY = 1, scaleZ = 1}}})
  helper.interactable = false
end
local drawn = {}
for _, line in ipairs(lines) do
  local points = {}
  for _, p in ipairs(line.points) do table.insert(points, helper.positionToLocal(p)) end
  table.insert(drawn, {points = points, color = line.color, thickness = line.thickness, loop = line.loop})
end
helper.setVectorLines(drawn)
return #drawn
"""

CLEAR_LUA = """
local n = 0
for _, o in ipairs(getObjects()) do
  if o.getGMNotes() == "%s" then o.destruct(); n = n + 1 end
end
return n
""" % HELPER_NOTES


def ring_outline(circles, steps=RING_STEPS):
    """The outline of a union of circles [(x, z, r)], as polylines of [x, z]: each circle's
    arcs that aren't inside another circle."""
    out = []
    for i, (cx, cz, r) in enumerate(circles):
        pts = [(cx + r * math.cos(2 * math.pi * k / steps), cz + r * math.sin(2 * math.pi * k / steps))
               for k in range(steps)]
        keep = [not any(math.dist(p, (ox, oz)) < orr - 1e-6 for j, (ox, oz, orr) in enumerate(circles) if j != i)
                for p in pts]
        if all(keep):
            out.append([list(p) for p in pts] + [list(pts[0])])
            continue
        if not any(keep):
            continue
        start = keep.index(False)   # walk from a hidden point, so no arc is split across the wrap
        arc = []
        for k in range(1, steps + 1):
            idx = (start + k) % steps
            if keep[idx]:
                arc.append(list(pts[idx]))
            elif arc:
                out.append(arc)
                arc = []
        if arc:
            out.append(arc)
    return out


def lifted(points, y):
    return [[round(x, 3), round(y, 3), round(z, 3)] for x, z in points]


def band_lines(row, band, reach, colour, y):
    circles = [(p["x"], p["z"], (p["base"][0] + p["base"][1]) / 4 + reach) for p in row["positions"]]
    return [{"points": lifted(arc, y), "color": colour, "thickness": 0.12, "loop": False}
            for arc in ring_outline(circles) if len(arc) > 1]


def los_lines(row, state, y):
    """The visibility outline of the unit's middle model, and a line to each enemy model it sees."""
    terrain = los.blockers(state["layout"])
    mine = [los.model(p) for p in row["positions"]]
    middle = min(mine, key=lambda m: math.dist((m["x"], m["z"]), (row["x"], row["z"])))
    out = [{"points": lifted(los.visibility_polygon(middle, terrain), y), "color": COLOURS["los"],
            "thickness": 0.08, "loop": True}]
    at = {m["guid"]: m for m in mine}
    for other in state["units"]:
        if not other["on_table"] or other["army"] == row["army"]:
            continue
        targets = [los.model(p) for p in other["positions"]]
        seen = los.unit_visibility(mine, targets, terrain)
        for t, got in zip(targets, seen["models"]):
            if got["visible"] == "none":
                continue
            src = min((at[g] for g in got["seen_by"]), key=lambda m: math.dist((m["x"], m["z"]), (t["x"], t["z"])))
            out.append({"points": lifted([(src["x"], src["z"]), (t["x"], t["z"])], y + 0.05),
                        "color": COLOURS[got["visible"]], "thickness": 0.06, "loop": False})
    return out


def lines_for(state, i, show, bands=(), dice="max"):
    """The lines to draw for unit i of a board_summary state: `show` names "los" and bands
    from `bands` (threat.bands for the unit), measured at their "max" or "avg"."""
    row = state["units"][i]
    if not row["on_table"]:
        raise ValueError(f"{row['unit']} is off the table.")
    y = state["surface_y"] + LIFT
    out = []
    shots = [b["band"] for b in bands if b["band"] not in COLOURS]
    for b in bands:
        if b["band"] in show:
            colour = COLOURS.get(b["band"]) or SHOTS[shots.index(b["band"]) % len(SHOTS)]
            out += band_lines(row, b["band"], b[dice], colour, y)
    if "los" in show:
        if not state["layout"]:
            raise ValueError("Line of sight needs the terrain of the LCT layout on the table, and none of ours "
                             "matches it.")
        out += los_lines(row, state, y)
    unknown = set(show) - {"los"} - {b["band"] for b in bands}
    if unknown:
        raise ValueError(f"Nothing called {', '.join(sorted(unknown))} to show; it has: "
                         + ", ".join(["los"] + [b["band"] for b in bands]) + ".")
    return out


COLOUR_NAMES = {"red": [1.0, 0.36, 0.36], "blue": [0.29, 0.66, 1.0], "green": [0.31, 0.9, 0.48],
                "yellow": [1.0, 0.82, 0.29], "orange": [1.0, 0.55, 0.26], "purple": [0.76, 0.55, 1.0],
                "cyan": [0.29, 0.84, 0.84], "white": [1.0, 1.0, 1.0]}


def colour(name):
    """A colour name (COLOUR_NAMES) or "#rrggbb" as TTS's [r, g, b]; yellow when unknown."""
    if isinstance(name, str) and len(name) == 7 and name.startswith("#"):
        try:
            return [int(name[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        except ValueError:
            pass
    return COLOUR_NAMES.get(name or "", COLOUR_NAMES["yellow"])


def shape_lines(shapes, y):
    """Claude's highlights (app/mcp_server/shared.py resolves them: units to their outline)
    as vector lines at height y."""
    out = []
    for sh in shapes:
        c, lift = colour(sh.get("color")), y + 0.1
        if sh["kind"] == "unit":
            out += [{"points": lifted(arc, lift), "color": c, "thickness": 0.14, "loop": False}
                    for arc in sh["outline"] if len(arc) > 1]
        elif sh["kind"] in ("point", "circle"):
            r = sh.get("r") or 0.8
            [loop] = ring_outline([(sh["x"], sh["z"], r)], steps=48)
            out.append({"points": lifted(loop, lift), "color": c, "thickness": 0.12, "loop": False})
            if sh["kind"] == "point":
                out += [{"points": lifted([(sh["x"] - r, sh["z"]), (sh["x"] + r, sh["z"])], lift), "color": c,
                         "thickness": 0.1, "loop": False},
                        {"points": lifted([(sh["x"], sh["z"] - r), (sh["x"], sh["z"] + r)], lift), "color": c,
                         "thickness": 0.1, "loop": False}]
        else:   # line, area
            out.append({"points": lifted(sh["points"], lift), "color": c, "thickness": 0.12,
                        "loop": sh["kind"] == "area"})
    return out


def run(script, doing, timeout):
    """Run Lua in the game; a failure is a ValueError saying what couldn't be done and why."""
    answer = tts.execute(script, timeout=timeout)
    if not answer["ok"]:
        raise ValueError(f"Couldn't {doing} on the table: {answer['error']}")
    return int(answer["result"] or 0)


def draw(lines):
    """Replace what tts-bridge has drawn on the table with these lines. -> lines drawn."""
    script = DRAW_LUA % {"lines": json.dumps(json.dumps(lines)), "notes": HELPER_NOTES,
                         "script": tts.lua_str(HELPER_SCRIPT), "below": -10}
    return run(script, "draw", 30)


def clear():
    """Remove everything tts-bridge has drawn on the table. -> helpers removed."""
    return run(CLEAR_LUA, "clear the lines", 10)


def show(state, i, show, profile=None, dice="max"):
    """Draw `show` ("los", and band names) for unit i of a board_summary state, replacing what
    was drawn before. profile: its threat.profile, when bands are asked for. -> lines drawn."""
    bands = threat.bands(profile) if profile else []
    return draw(lines_for(state, i, show, bands, dice))
