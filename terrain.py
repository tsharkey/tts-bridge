"""
terrain.py — the layout's terrain on the TTS table, and line of sight worked out in the game.

    import terrain
    terrain.send()                          # hub: write the layout on the table onto our terrain object
    board.written_layout(objs)              # which layout that object says is on the table
    terrain.script(layout, surface_y)       # the object's Lua: the terrain, and line of sight from it

The game is the source of truth: the hub only writes the terrain into it when asked (the
Board page, LCT setup, or a model asking because there's none), since only the hub can tell
which layout is on the table (layouts.identify needs every layout and TTS's meshes). The
terrain goes onto one object of ours (under the table, locked, not interactable), saved with
the game, and that object's script does the rest in the game: a model's right-click "Line of
sight" asks it (sheetviewer.py) to draw what the model's unit sees from where its models are
now, a see-through fill over the area seen from its middle model, the outline round it, and a
line to each enemy model it sees (green fully, yellow partly), as overlays.los_lines does.
"Refresh line of sight" draws it again; nothing redraws on its own.

The line of sight here is a Lua copy of los.py (the rules and simplifications are its); the
Python stays for Claude and offline use, and tests/test_terrain.py checks the two agree.
"""

import board
import layouts
import los
import overlays
import tts_bridge as tts

NOTES = board.TERRAIN_NOTES    # + the layout's id: the terrain object's GM Notes
VERSION = 1
FILL = 0.18                     # how opaque the fill over the area seen is
FILL_STEP = 0.5                 # inches between the fill's lines (each as thick)


def lua_value(v):
    """A JSON-like Python value as a Lua table literal."""
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {lua_value(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(lua_value(x) for x in v) + "}"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return f"{v:g}"
    if v is None:
        return "nil"
    return tts.lua_str(str(v))


def data(layout, surface_y):
    """What the terrain object holds: the layout's blockers (los.blockers) and where its pieces
    are, to check it's still the terrain on the table."""
    pieces = [layouts.bounds(p)[:2] for a in layout["areas"] for p in [a["polygon"], *(f["polygon"] for f in a["features"])]]
    return {"layout": layout["id"], "name": layout["name"], "surface": round(surface_y, 3),
            "blockers": [{"id": b["id"], "kind": b["kind"], "polygon": b["polygon"], "box": list(b["box"])}
                         for b in los.blockers(layout)],
            "pieces": [[round(x, 2), round(z, 2)] for x, z in pieces]}


def colour(rgb, alpha=None):
    return list(rgb) + ([alpha] if alpha is not None else [])


SCRIPT = r"""-- tts-bridge terrain: the layout's terrain (what blocks line of sight), written onto this
-- object by the tts-bridge hub, and line of sight worked out from it here, in the game.
-- A Lua copy of tts-bridge's los.py (terrain.py). Models ask it from their right-click menu.
TTSB_TERRAIN_VERSION = %(version)d
TTSB_TERRAIN = %(data)s

local FLOOR, EDGE_POINTS, RAYS = %(floor)s, %(edge_points)d, %(rays)d
local HALF_X, HALF_Z = %(half_x)s, %(half_z)s
local LIFT, FILL_STEP = %(lift)s, %(fill_step)s
local COLOURS = %(colours)s
local ORDER = {none = 0, partial = 1, full = 2}
local atan2 = math.atan2 or math.atan
local last = nil   -- the model line of sight was last drawn for, for Refresh

-- geometry (layouts.py) ------------------------------------------------------------------

function ttsbInside(x, z, poly)
  local hit, n = false, #poly
  for i = 1, n do
    local a, b = poly[i], poly[(i - 2) %% n + 1]
    if (a[2] > z) ~= (b[2] > z) and x < (b[1] - a[1]) * (z - a[2]) / (b[2] - a[2]) + a[1] then hit = not hit end
  end
  return hit
end

local function edgeDistance(x, z, poly)
  local best, n = math.huge, #poly
  for i = 1, n do
    local a, b = poly[i], poly[i %% n + 1]
    local dx, dz = b[1] - a[1], b[2] - a[2]
    local len = dx * dx + dz * dz
    if len == 0 then len = 1 end
    local t = math.max(0, math.min(1, ((x - a[1]) * dx + (z - a[2]) * dz) / len))
    best = math.min(best, math.sqrt((x - a[1] - t * dx) ^ 2 + (z - a[2] - t * dz) ^ 2))
  end
  return best
end

local function distance(x, z, poly)
  if ttsbInside(x, z, poly) then return 0 end
  return edgeDistance(x, z, poly)
end

-- line of sight (los.py) -------------------------------------------------------------------

local function within(m, b) return distance(m.x, m.z, b.polygon) < m.r end

local function points(m)
  local out = {{m.x, m.z}}
  for i = 0, EDGE_POINTS - 1 do
    local a = 2 * math.pi * i / EDGE_POINTS
    table.insert(out, {m.x + m.r * math.cos(a), m.z + m.r * math.sin(a)})
  end
  return out
end

-- whether the segment p-q passes through a polygon's inside (grazing an edge or corner doesn't)
function ttsbHits(p, q, poly)
  local dx, dz = q[1] - p[1], q[2] - p[2]
  local ts, n = {0, 1}, #poly
  for i = 1, n do
    local a, b = poly[i], poly[i %% n + 1]
    local ex, ez = b[1] - a[1], b[2] - a[2]
    local den = dx * ez - dz * ex
    if den ~= 0 then
      local t = ((a[1] - p[1]) * ez - (a[2] - p[2]) * ex) / den
      local u = ((a[1] - p[1]) * dz - (a[2] - p[2]) * dx) / den
      if t > 0 and t < 1 and u >= -1e-9 and u <= 1 + 1e-9 then table.insert(ts, t) end
    end
  end
  table.sort(ts)
  for k = 1, #ts - 1 do
    local t1, t2 = ts[k], ts[k + 1]
    if t2 - t1 > 1e-9 and ttsbInside(p[1] + dx * (t1 + t2) / 2, p[2] + dz * (t1 + t2) / 2, poly) then return true end
  end
  return false
end

local function applicable(a, b)
  local xmin, xmax = math.min(a.x - a.r, b.x - b.r), math.max(a.x + a.r, b.x + b.r)
  local zmin, zmax = math.min(a.z - a.r, b.z - b.r), math.max(a.z + a.r, b.z + b.r)
  local low = a.height <= FLOOR and b.height <= FLOOR
  local out = {}
  for _, t in ipairs(TTSB_TERRAIN.blockers) do
    if (t.kind == "obscuring" or low) and t.box[1] < xmax and t.box[2] > xmin and t.box[3] < zmax
        and t.box[4] > zmin and not within(a, t) and not within(b, t) then
      table.insert(out, t)
    end
  end
  return out
end

-- how much of model b model a can see: "full", "partial" or "none"
function ttsbSight(a, b)
  local near = applicable(a, b)
  if #near == 0 then return "full" end
  local seen = 0
  for _, q in ipairs(points(b)) do
    for _, p in ipairs(points(a)) do
      local blocked = false
      for _, t in ipairs(near) do
        if ttsbHits(p, q, t.polygon) then blocked = true break end
      end
      if not blocked then seen = seen + 1 break end
    end
  end
  if seen == EDGE_POINTS + 1 then return "full" elseif seen > 0 then return "partial" end
  return "none"
end

local function exitDistance(ox, oz, dx, dz, poly)
  local far, n = nil, #poly
  for i = 1, n do
    local a, b = poly[i], poly[i %% n + 1]
    local ex, ez = b[1] - a[1], b[2] - a[2]
    local den = dx * ez - dz * ex
    if den ~= 0 then
      local t = ((a[1] - ox) * ez - (a[2] - oz) * ex) / den
      local u = ((a[1] - ox) * dz - (a[2] - oz) * dx) / den
      if t > 0 and u >= 0 and u <= 1 and (far == nil or t > far) then far = t end
    end
  end
  return far
end

local function round3(v) return math.floor(v * 1000 + 0.5) / 1000 end

-- the part of the table a model can see from its base's centre, as a polygon of {x, z}
function ttsbVisibility(m)
  local ox, oz = m.x, m.z
  local low = m.height <= FLOOR
  local near, corners = {}, {}
  for _, t in ipairs(TTSB_TERRAIN.blockers) do
    if not ((t.kind == "solid" and not low) or within(m, t)) then
      local base, lo, hi = nil, math.huge, -math.huge
      for _, p in ipairs(t.polygon) do
        local a = atan2(p[2] - oz, p[1] - ox)
        table.insert(corners, a)
        base = base or a
        local s = (a - base + math.pi) %% (2 * math.pi) - math.pi   -- the polygon spans < 180 degrees
        lo, hi = math.min(lo, s), math.max(hi, s)
      end
      table.insert(near, {lo = base + lo, hi = base + hi, polygon = t.polygon})
    end
  end
  for _, c in ipairs({{HALF_X, HALF_Z}, {-HALF_X, HALF_Z}, {-HALF_X, -HALF_Z}, {HALF_X, -HALF_Z}}) do
    table.insert(corners, atan2(c[2] - oz, c[1] - ox))
  end
  local rays = {}
  for i = 0, RAYS - 1 do table.insert(rays, 2 * math.pi * i / RAYS - math.pi) end
  for _, a in ipairs(corners) do
    table.insert(rays, a - 1e-4) table.insert(rays, a) table.insert(rays, a + 1e-4)
  end
  table.sort(rays)
  local out = {}
  for _, angle in ipairs(rays) do
    local dx, dz = math.cos(angle), math.sin(angle)
    local rx = dx > 0 and (HALF_X - ox) / dx or dx < 0 and (-HALF_X - ox) / dx or math.huge
    local rz = dz > 0 and (HALF_Z - oz) / dz or dz < 0 and (-HALF_Z - oz) / dz or math.huge
    local reach = math.min(rx, rz)
    for _, b in ipairs(near) do
      if (angle - b.lo) %% (2 * math.pi) <= (b.hi - b.lo) + 1e-9 then
        local t = exitDistance(ox, oz, dx, dz, b.polygon)
        if t ~= nil and t < reach then reach = t end
      end
    end
    table.insert(out, {round3(ox + dx * reach), round3(oz + dz * reach)})
  end
  local kept = {}
  for i, p in ipairs(out) do
    local prev = out[(i - 2) %% #out + 1]
    if p[1] ~= prev[1] or p[2] ~= prev[2] then table.insert(kept, p) end
  end
  return kept
end

-- the table ---------------------------------------------------------------------------------

local function armyOf(o)
  local notes = o.getGMNotes() or ""
  if notes:sub(1, 8) == "army.py:" or notes:sub(1, 9) == "recreate:" then return notes end
  return nil
end

local function unitOf(o)
  for _, t in ipairs(o.getTags() or {}) do
    if t:sub(1, 16) == "tts-bridge:unit:" then return t end
  end
  -- the "[<unit>]" line, for models without our tags: a plain find for the line's end, since
  -- a pattern over a long description can be too much for TTS's Lua (board.READ_LUA)
  local d = o.getDescription() or ""
  local nl = d:find("\n", 1, true)
  return (nl and d:sub(1, nl - 1) or d):match("^%%s*(%%[.-%%])%%s*$")
end

-- a model for the maths, from its object: its base as a circle (board.radius), and its
-- height above the table surface
function ttsbModel(o)
  local b = o.getBounds()
  return {guid = o.getGUID(), x = b.center.x, z = b.center.z, r = (b.size.x + b.size.z) / 4,
          height = b.center.y - b.size.y / 2 - TTSB_TERRAIN.surface}
end

local function onTable(m) return math.abs(m.x) <= HALF_X and math.abs(m.z) <= HALF_Z end

-- whether the terrain on the table is still this layout's: most of its pieces have something
-- centred within 1" of theirs (layouts.identify)
local function stillHere()
  local centres = {}
  for _, o in ipairs(getObjects()) do
    if o ~= self and armyOf(o) == nil then
      local c = o.getBounds().center
      table.insert(centres, {c.x, c.z})
    end
  end
  local found = 0
  for _, p in ipairs(TTSB_TERRAIN.pieces) do
    for _, c in ipairs(centres) do
      if (c[1] - p[1]) ^ 2 + (c[2] - p[2]) ^ 2 <= 1 then found = found + 1 break end
    end
  end
  return #TTSB_TERRAIN.pieces == 0 or found / #TTSB_TERRAIN.pieces >= 0.7
end

local function tell(color, text)
  if color and Player[color] and Player[color].seated then
    broadcastToColor("tts-bridge: " .. text, color, {0.8, 0.85, 0.95})
  else
    broadcastToAll("tts-bridge: " .. text, {0.8, 0.85, 0.95})
  end
end

-- the lines: the fill (see-through, lines across the area), its outline, and a line to each
-- enemy model seen, from the nearest of the unit's models that sees it
function ttsbLines(mine, enemies)
  local mx, mz = 0, 0
  for _, m in ipairs(mine) do mx, mz = mx + m.x / #mine, mz + m.z / #mine end
  local middle, best = nil, math.huge
  for _, m in ipairs(mine) do
    local d = (m.x - mx) ^ 2 + (m.z - mz) ^ 2
    if d < best then middle, best = m, d end
  end
  local area = ttsbVisibility(middle)
  local y = TTSB_TERRAIN.surface + LIFT
  local lines = {}
  local function at(x, z, lift) return self.positionToLocal({x, y + (lift or 0), z}) end
  -- the fill: a line across the area every FILL_STEP inches, as thick, so they tile
  local zmin, zmax = math.huge, -math.huge
  for _, p in ipairs(area) do zmin, zmax = math.min(zmin, p[2]), math.max(zmax, p[2]) end
  local z = zmin + FILL_STEP / 2
  while z < zmax do
    local xs, n = {}, #area
    for i = 1, n do
      local a, b = area[i], area[i %% n + 1]
      if (a[2] > z) ~= (b[2] > z) then table.insert(xs, a[1] + (z - a[2]) * (b[1] - a[1]) / (b[2] - a[2])) end
    end
    table.sort(xs)
    for k = 1, #xs - 1, 2 do
      table.insert(lines, {points = {at(xs[k], z), at(xs[k + 1], z)}, color = COLOURS.fill, thickness = FILL_STEP})
    end
    z = z + FILL_STEP
  end
  local outline = {}
  for _, p in ipairs(area) do table.insert(outline, at(p[1], p[2], 0.02)) end
  table.insert(lines, {points = outline, color = COLOURS.los, thickness = 0.08, loop = true})
  local seen, full = 0, 0
  for _, t in ipairs(enemies) do
    local state, from, near = "none", nil, math.huge
    for _, o in ipairs(mine) do
      local s = ttsbSight(o, t)
      if s ~= "none" then
        if ORDER[s] > ORDER[state] then state = s end
        local d = (o.x - t.x) ^ 2 + (o.z - t.z) ^ 2
        if d < near then from, near = o, d end
      end
    end
    if state ~= "none" then
      seen = seen + 1
      if state == "full" then full = full + 1 end
      table.insert(lines, {points = {at(from.x, from.z, 0.05), at(t.x, t.z, 0.05)}, color = COLOURS[state],
                           thickness = 0.06})
    end
  end
  return lines, seen, full
end

-- line of sight for the unit of the model `params.guid`, from where its models are now
function ttsbLineOfSight(params)
  local color = params and params.color
  local o = params and params.guid and getObjectFromGUID(params.guid)
  if o == nil then return 0 end
  last = params.guid
  if not stillHere() then
    self.setVectorLines({})
    tell(color, "the terrain on the table isn't " .. TTSB_TERRAIN.name .. " any more. Send the terrain again from the "
                .. "hub's Board page.")
    return 0
  end
  local army, unit = armyOf(o), unitOf(o)
  local mine, enemies = {}, {}
  for _, other in ipairs(getObjects()) do
    local theirs = armyOf(other)
    if theirs ~= nil then
      local m = ttsbModel(other)
      if onTable(m) then
        if other == o or (theirs == army and unit ~= nil and unitOf(other) == unit) then
          table.insert(mine, m)
        elseif army ~= nil and theirs ~= army then
          table.insert(enemies, m)
        end
      end
    end
  end
  if #mine == 0 then
    tell(color, "that model is off the table.")
    return 0
  end
  local lines, seen, full = ttsbLines(mine, enemies)
  self.setVectorLines(lines)
  tell(color, string.format("line of sight: it sees %%d enemy models (%%d fully).", seen, full))
  return #lines
end

function ttsbRefresh(params)
  if last == nil then return 0 end
  return ttsbLineOfSight({guid = last, color = params and params.color})
end

function ttsbClear()
  last = nil
  self.setVectorLines({})
  return 0
end
"""


def script(layout, surface_y):
    """The terrain object's Lua: the layout's terrain, and line of sight worked out from it."""
    colours = {"los": overlays.COLOURS["los"], "full": overlays.COLOURS["full"],
               "partial": overlays.COLOURS["partial"], "fill": colour(overlays.COLOURS["los"], FILL)}
    return SCRIPT % {"version": VERSION, "data": lua_value(data(layout, surface_y)), "floor": los.FLOOR,
                     "edge_points": los.EDGE_POINTS, "rays": los.RAYS, "half_x": los.HALF_X, "half_z": los.HALF_Z,
                     "lift": overlays.LIFT, "fill_step": FILL_STEP, "colours": lua_value(colours)}


WRITE_LUA = """
for _, o in ipairs(getObjects()) do
  if (o.getGMNotes() or ""):sub(1, %(n)d) == "%(notes)s" then o.destruct() end   -- the terrain written before
end
local t = spawnObjectData({data = {Name = "BlockSquare", Nickname = "tts-bridge terrain", GMNotes = %(gm)s,
  Locked = true, Tooltip = false, LuaScript = %(script)s,
  Transform = {posX = 0, posY = -12, posZ = 0, rotX = 0, rotY = 0, rotZ = 0, scaleX = 1, scaleY = 1, scaleZ = 1}}})
t.interactable = false
return 1
"""


def send(objs=None):
    """Write the layout on the table onto our terrain object, replacing one written before.
    -> {"layout", "name"}. ValueError when no layout of ours matches the table."""
    objs = board.read_objects() if objs is None else objs
    found = board.find_layout([o for o in objs if not (o.get("notes") or "").startswith(NOTES)])
    if not found:
        raise ValueError("None of the LCT layouts tts-bridge knows matches the terrain on the table.")
    layout = found[0]
    answer = tts.execute(WRITE_LUA % {"n": len(NOTES), "notes": NOTES, "gm": tts.lua_str(NOTES + layout["id"]),
                                      "script": tts.lua_str(script(layout, board.table_surface(objs)))}, timeout=30)
    if not answer["ok"]:
        raise ValueError(f"Couldn't put the terrain on the table: {answer['error']}")
    return {"layout": layout["id"], "name": layout["name"]}


def request(message):
    """sendExternalMessage({ttsBridge = "terrain", color}) from a model whose line of sight found
    no terrain on the table (sheetviewer.py); run by the hub. Tells the player how it went."""
    colour_name = message.get("color") or "White"
    try:
        got = send()
        text = f"tts-bridge: {got['name']} is on the table now. Choose Line of sight again."
    except (ValueError, SystemExit) as e:
        text = f"tts-bridge: {e}"
    try:
        tts.run_lua(f"broadcastToColor({tts.lua_str(text)}, {tts.lua_str(colour_name)}, {{0.8, 0.85, 0.95}}) return 1",
                    timeout=5)
    except SystemExit:
        pass

