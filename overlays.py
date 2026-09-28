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

Threat bands follow their unit: the helper's script redraws them round the models, several
times a second, as they're moved. Line of sight needs the layout's terrain, which only the
hub has, so the helper reports moves of the unit's models and the enemy's to the hub
(sendExternalMessage({ttsBridge = "overlay", show = "moved", ...}), app/mcp_server/overlay.py),
and the hub redraws it (moved) from the table it read when it was asked.

What's drawn, just above the mats on the table (the board's and the terrain areas', which sit
a little above the table surface):
- A threat band (move, advance, charge, "shoot: <weapon>"; threat.bands) as the outline of
  everything within that reach of any of the unit's bases: the union of circles, one ring
  round the unit rather than one per model.
- Line of sight ("los") as the visibility outline of the model nearest the unit's middle
  (los.visibility_polygon), and a line to every enemy model the unit sees, from the nearest
  of its models that sees it: green when fully visible, yellow when partly.
"""

import copy
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

HELPER_SCRIPT = r"""-- tts-bridge overlays: line of sight and threat ranges drawn by tts-bridge (overlays.py).
-- Threat bands follow their unit: this script redraws them as its models move. Line of
-- sight is the hub's to work out: this script tells it when the models it watches move.
-- Removed when a save is loaded, so nothing it draws outlives the session.
TTSB_OVERLAYS = 2      -- this script's version: the hub replaces a helper with an older one

local fixed = {}       -- lines as the hub drew them, in table coordinates
local follow = nil     -- threat bands: {y, models = {{guid, r}}, bands = {{reach, color, thickness}}}
local watch = nil      -- line of sight: {guids = {...}}, whose moves go to the hub
local seen = {}        -- guid -> where it was when last looked at
local moved = false    -- watched models moved since the hub was last told
local quiet = 0        -- ticks since the hub was last told
local STEPS = 72       -- points round a full circle
local EVERY = 3        -- ticks (0.1s) between line-of-sight updates while models move

function onSave() return "saved" end

function onLoad(state)
  if state == "saved" then self.destruct() return end
  if state ~= nil and state ~= "" then ttsbSet(JSON.decode(state)) end
  Wait.time(ttsbTick, 0.1, -1)
end

-- the outline of a union of circles {{x, z, r}}: each circle's arcs not inside another
-- (overlays.ring_outline)
function ttsbOutline(circles)
  local out = {}
  for i, c in ipairs(circles) do
    local pts, keep, some, every = {}, {}, false, true
    for k = 1, STEPS do
      local a = 2 * math.pi * (k - 1) / STEPS
      local x, z = c[1] + c[3] * math.cos(a), c[2] + c[3] * math.sin(a)
      local inside = false
      for j, o in ipairs(circles) do
        if j ~= i and math.sqrt((x - o[1]) ^ 2 + (z - o[2]) ^ 2) < o[3] - 1e-6 then inside = true break end
      end
      pts[k], keep[k] = {x, z}, not inside
      some, every = some or not inside, every and not inside
    end
    if every then
      table.insert(pts, pts[1])
      table.insert(out, pts)
    elseif some then
      local start = 1
      while keep[start] do start = start + 1 end   -- walk from a hidden point
      local arc = {}
      for k = 1, STEPS do
        local idx = (start - 1 + k) % STEPS + 1
        if keep[idx] then
          table.insert(arc, pts[idx])
        elseif #arc > 0 then
          table.insert(out, arc)
          arc = {}
        end
      end
      if #arc > 0 then table.insert(out, arc) end
    end
  end
  return out
end

local function here(guid)
  local o = getObjectFromGUID(guid)
  if o == nil then return nil end
  return o, o.getBounds()
end

function ttsbDraw()
  local function relative(x, y, z) return self.positionToLocal({x, y, z}) end
  local drawn = {}
  for _, line in ipairs(fixed) do
    local points = {}
    for _, p in ipairs(line.points) do table.insert(points, relative(p[1], p[2], p[3])) end
    table.insert(drawn, {points = points, color = line.color, thickness = line.thickness, loop = line.loop})
  end
  if follow then
    for _, band in ipairs(follow.bands) do
      local circles = {}
      for _, m in ipairs(follow.models) do
        local o, b = here(m.guid)
        if o then table.insert(circles, {b.center.x, b.center.z, m.r + band.reach}) end
      end
      for _, arc in ipairs(ttsbOutline(circles)) do
        if #arc > 1 then
          local points = {}
          for _, p in ipairs(arc) do table.insert(points, relative(p[1], follow.y, p[2])) end
          table.insert(drawn, {points = points, color = band.color, thickness = band.thickness})
        end
      end
    end
  end
  self.setVectorLines(drawn)
  return #drawn
end

-- data: {fixed = lines, follow, watch, replace}. replace: follow and watch are these (or
-- none); otherwise only the fixed lines change (the hub's line of sight, redrawn).
function ttsbSet(data)
  if data.fixed then fixed = data.fixed end
  if data.replace then
    follow, watch, seen, moved, quiet = data.follow, data.watch, {}, false, EVERY
  end
  return ttsbDraw()
end

function ttsbTick()
  if follow == nil and watch == nil then return end
  local followed, watched, guids = {}, {}, {}
  if follow then for _, m in ipairs(follow.models) do followed[m.guid], guids[m.guid] = true, true end end
  if watch then for _, g in ipairs(watch.guids) do watched[g], guids[g] = true, true end end
  local redraw, stirred = false, false
  for g in pairs(guids) do
    local o, b = here(g)
    local key = o and string.format("%.2f,%.2f,%.2f", b.center.x, b.center.y, b.center.z) or "gone"
    if seen[g] ~= key then
      if seen[g] ~= nil then
        redraw = redraw or followed[g] == true
        stirred = stirred or watched[g] == true
      end
      seen[g] = key
    end
  end
  if redraw then ttsbDraw() end
  -- line of sight: tell the hub at once, then every EVERY ticks while they move, and
  -- once more when they stop
  moved = moved or stirred
  quiet = quiet + 1
  if moved and (quiet >= EVERY or not stirred) then
    local models = {}
    for g in pairs(watched) do
      local o, b = here(g)
      if o then
        table.insert(models, {guid = g, x = b.center.x, z = b.center.z, bottom = b.center.y - b.size.y / 2,
                              held = o.held_by_color ~= nil})
      end
    end
    sendExternalMessage({ttsBridge = "overlay", show = "moved", models = models})
    moved, quiet = false, 0
  end
end
"""

HELPER_VERSION = 2   # TTSB_OVERLAYS in HELPER_SCRIPT

DRAW_LUA = """
local data = %(data)s
local helper = nil
for _, o in ipairs(getObjects()) do
  if o.getGMNotes() == "%(notes)s" then
    if o.getVar("TTSB_OVERLAYS") == %(version)d then helper = o else o.destruct() end   -- an older helper
  end
end
if helper == nil then
  if not %(replace)s then return 0 end   -- cleared since: nothing to update
  helper = spawnObjectData({data = {Name = "BlockSquare", Nickname = "tts-bridge overlays",
    GMNotes = "%(notes)s", Locked = true, Tooltip = false, LuaScript = %(script)s, LuaScriptState = data,
    Transform = {posX = 0, posY = %(below)s, posZ = 0, rotX = 0, rotY = 0, rotZ = 0,
                 scaleX = 1, scaleY = 1, scaleZ = 1}}})
  helper.interactable = false
  return %(count)d
end
helper.call("ttsbSet", JSON.decode(data))
return %(count)d
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


def lines_for(state, i, show, bands=(), dice="max", follow=False):
    """The lines to draw for unit i of a board_summary state: `show` names "los" and bands
    from `bands` (threat.bands for the unit), measured at their "max" or "avg". follow: leave
    the bands out, for the helper to draw round the models as they move (follow_for)."""
    row = state["units"][i]
    if not row["on_table"]:
        raise ValueError(f"{row['unit']} is off the table.")
    y = state["surface_y"] + LIFT
    out = []
    shots = [b["band"] for b in bands if b["band"] not in COLOURS]
    for b in bands:
        if b["band"] in show and not follow:
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


live = {}   # the line of sight kept up to date as models move (show, moved): {"state", "i"}


def follow_for(state, i, show, bands, dice="max"):
    """What the helper needs to draw unit i's threat bands in `show` round its models
    wherever they are: {y, models: [{guid, r}], bands: [{reach, color, thickness}]}, or
    None when no band is shown."""
    row = state["units"][i]
    shots = [b["band"] for b in bands if b["band"] not in COLOURS]
    shown = [{"reach": b[dice], "thickness": 0.12,
              "color": COLOURS.get(b["band"]) or SHOTS[shots.index(b["band"]) % len(SHOTS)]}
             for b in bands if b["band"] in show]
    if not shown:
        return None
    return {"y": round(state["surface_y"] + LIFT, 3), "bands": shown,
            "models": [{"guid": p["guid"], "r": (p["base"][0] + p["base"][1]) / 4} for p in row["positions"]]}


def watch_for(state, i):
    """The models whose moves change unit i's line of sight: its own, and every other
    army's on the table."""
    row = state["units"][i]
    return [p["guid"] for r in state["units"] if r["on_table"] and (r is row or r["army"] != row["army"])
            for p in r["positions"]]


def send(data, count):
    script = DRAW_LUA % {"data": tts.lua_str(json.dumps(data)), "notes": HELPER_NOTES, "version": HELPER_VERSION,
                         "replace": "true" if data.get("replace") else "false", "count": count,
                         "script": tts.lua_str(HELPER_SCRIPT), "below": -10}
    return run(script, "draw", 30)


def draw(lines, follow=None, watch=None):
    """Replace what tts-bridge has drawn on the table: `lines` where they are, `follow`
    threat bands the helper redraws round their models as they move (follow_for), and
    `watch`, models whose moves the helper reports to the hub (watch_for). -> lines drawn."""
    live.clear()
    return send({"fixed": lines, "follow": follow, "watch": {"guids": watch} if watch else None, "replace": True},
                len(lines) + (len(follow["bands"]) if follow else 0))


def moved(models):
    """Redraw the line of sight show() keeps up to date, with the models the helper reports
    where they are now: [{guid, x, z, bottom, held}]. A model being held keeps the height it
    had, not the height it's carried at. -> lines drawn; 0 when nothing is kept up to date."""
    st = live.get("state")
    if not st:
        return 0
    at = {p["guid"]: p for row in st["units"] for p in row["positions"]}
    for m in models:
        p = at.get(m.get("guid"))
        if p is None:
            continue
        p["x"], p["z"] = round(m["x"], 2), round(m["z"], 2)
        if not m.get("held"):
            p["height"] = round(m["bottom"] - st["surface_y"], 1)
    for row in st["units"]:
        if row["positions"]:
            row["x"] = round(sum(p["x"] for p in row["positions"]) / len(row["positions"]), 2)
            row["z"] = round(sum(p["z"] for p in row["positions"]) / len(row["positions"]), 2)
    lines = lines_for(st, live["i"], ["los"])
    return send({"fixed": lines}, len(lines))


def clear():
    """Remove everything tts-bridge has drawn on the table. -> helpers removed."""
    return run(CLEAR_LUA, "clear the lines", 10)


def show(state, i, show, profile=None, dice="max"):
    """Draw `show` ("los", and band names) for unit i of a board_summary state, replacing what
    was drawn before. The bands follow the unit's models as they move; the line of sight is
    redrawn when they or the enemy move (moved). profile: its threat.profile, when bands
    are asked for. -> lines drawn."""
    bands = threat.bands(profile) if profile else []
    lines = lines_for(state, i, show, bands, dice, follow=True)
    watch = watch_for(state, i) if "los" in show else None
    n = draw(lines, follow_for(state, i, show, bands, dice), watch)
    if watch:
        live.update(state=copy.deepcopy(state), i=i)
    return n
