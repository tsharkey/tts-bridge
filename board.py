"""
board.py — read the table as units and terrain, measure between units, and
place a unit in formation. Built for planning deployment (see
.claude/skills/wh40k-deployment-planner), but works at any point in a game.

    python3 board.py summary                     # units, terrain and zones; writes board.json
    python3 board.py dist "<unit A>" "<unit B>"  # closest base-to-base distance, in inches
    python3 board.py place "<unit>" x z [facing] [--cols N] [--y H] [--army TEXT] [--nth N] [--check]
                                                 # move a unit into a block centred on x, z
    python3 board.py undo                        # put the last placed unit back

Coordinates are table inches with 0,0 at the centre: x runs along the 60"
edge (-30..30), z along the 44" edge (-22..22), y is height. Which side each
army deploys on depends on the deployment card (see DEPLOYMENT_SIDES in
app/vision.py). Facing is degrees: 0 = +z, 90 = +x, 180 = -z, 270 = -x.

A unit is every model whose description starts with a "[<unit name>]" line
(which is how army.py and recreate.py spawn them) and that shares an army tag
in GM Notes. Models they spawn also carry a "tts-bridge:unit:<n>" tag saying
which unit of the list they belong to, so a unit stays one unit wherever its
models stand, and --nth counts same-named units in list order. Models without
that tag (other people's armies, older saves) are grouped by where they stand:
models more than 2" from each other are separate units, and "1" is the one
nearest the +z edge. A leader and its bodyguard are separate datasheets, so
they are separate units here. Units out of coherency are flagged.

Like the other command-line tools, this runs with or without the web app: when
the app is up it sends its Lua through the app.
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

import layouts
import tts_bridge as tts

ROOT = Path(__file__).parent
BOARD_JSON = ROOT / "board.json"
UNDO_JSON = ROOT / ".board_undo.json"

HALF_X, HALF_Z = 30, 22
ENGAGEMENT = 2.0
COHERENCY_MAX = 9.0
GAP = 0.4          # space between bases in a placed block
DROP_Y = 1.6       # drop height onto the table; raise it with --y for upper floors
LINK = 2.0         # same-named models within this (edge to edge) are one unit: coherency distance
MAX_TERRAIN = 40   # anything wider than this is the table or mat, not terrain
IGNORE_TAGS = {"Card", "Deck", "Tile", "Hand", "Dice", "Chip", "Bag", "Infinite", "Calculator",
               "Notecard", "Tablet", "Counter", "Fog", "FogOfWar", "Surface", "Clock"}

# One object per line of output: what it is, where it is, and how big.
READ_LUA = """
local out = {}
for _, o in ipairs(getObjects()) do
    local b = o.getBounds()
    local p = o.getPosition()
    local r = o.getRotation()
    local d = o.getDescription() or ""
    -- a plain find, not a pattern: TTS's Lua gives up on "^([^\\n]*)\\n" ("pattern
    -- too complex") for a long description with no line break, like LCT's mission cards
    local nl = d:find("\\n", 1, true)
    table.insert(out, {
        guid = o.guid, tag = o.tag, name = o.getName() or "",
        head = nl and d:sub(1, nl - 1) or "", notes = o.getGMNotes() or "", tags = o.getTags(),
        locked = o.getLock(), rot = r.y, p = {p.x, p.y, p.z},
        c = {b.center.x, b.center.y, b.center.z}, s = {b.size.x, b.size.y, b.size.z},
    })
end
return out
"""

# The "[<unit name>]" line army.py and recreate.py put first in each model's
# description (a whole line, so BBCode like "[b]Objective[/b]" doesn't count).
UNIT_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def unit_name(o):
    if o["tag"] in IGNORE_TAGS:
        return None
    m = UNIT_RE.match(o["head"])
    return m.group(1).strip() if m else None


# --------------------------------------------------------------------------
# Reading the table

def read_objects():
    raw = tts.run_lua(READ_LUA, timeout=20)
    if raw is None:
        sys.exit("Couldn't read the table.")
    return json.loads(raw) if isinstance(raw, str) else raw


def is_round(s):
    """A footprint (w, d) about as wide as it is deep: a round base. Others are long bases and hulls."""
    return abs(s[0] - s[1]) <= 0.15 * max(s)


def gap(x1, z1, s1, x2, z2, s2):
    """Edge-to-edge distance between two bases centred at (x, z) with footprints s = (w, d).
    Round bases are measured as circles; long bases and hulls as boxes."""
    if is_round(s1) and is_round(s2):
        return math.dist((x1, z1), (x2, z2)) - (s1[0] + s1[1]) / 4 - (s2[0] + s2[1]) / 4
    dx = max(abs(x1 - x2) - (s1[0] + s2[0]) / 2, 0)
    dz = max(abs(z1 - z2) - (s1[1] + s2[1]) / 2, 0)
    if dx == 0 and dz == 0:  # overlapping boxes: report how far they overlap, as a negative gap
        return -min((s1[0] + s2[0]) / 2 - abs(x1 - x2), (s1[1] + s2[1]) / 2 - abs(z1 - z2))
    return math.hypot(dx, dz)


def foot(o):
    return (o["s"][0], o["s"][2])


def model_gap(a, b):
    return gap(a["c"][0], a["c"][2], foot(a), b["c"][0], b["c"][2], foot(b))


def radius(o):
    """Approximate a base as a circle: half the mean of its width and depth."""
    return (o["s"][0] + o["s"][2]) / 4


def army_of(o):
    notes = o["notes"]
    if notes.startswith(("army.py:", "recreate:")):
        return notes
    return "untagged"


def on_table(o):
    x, _, z = o["c"]
    return abs(x) <= HALF_X and abs(z) <= HALF_Z


UNIT_TAG = "tts-bridge:unit:"     # army.UNIT_TAG: which unit of its list a model is in
SHEET_TAG = "tts-bridge:sheet:"   # army.SHEET_TAG: that unit's datasheet id


def tags_of(o):
    tags = o.get("tags") or []
    return list(tags.values()) if isinstance(tags, dict) else list(tags)  # Lua's empty table is {}


def tagged(o, prefix):
    """The value after `prefix` in one of the object's tags, or None."""
    return next((t[len(prefix):] for t in tags_of(o) if t.startswith(prefix)), None)


def unit_index(o):
    value = tagged(o, UNIT_TAG)
    return int(value) if value and value.isdigit() else None


def coherency(models):
    """What breaks 11th edition coherency, edge to edge: each model within 2"
    of another model of its unit, and all of them within 9" of each other."""
    if len(models) < 2:
        return []
    out = []
    for m in models:
        nearest = min(model_gap(m, o) for o in models if o is not m)
        if nearest > LINK + 0.05:
            out.append(f"a model is {nearest:.1f}\" from the rest of its unit (coherency is 2\")")
    span = max(model_gap(a, b) for a in models for b in models if a is not b)
    if span > COHERENCY_MAX + 0.05:
        out.append(f"models are {span:.1f}\" apart (coherency is within 9\" of each other)")
    return out


def split_by_distance(models):
    """Group same-named models into units by chaining models within LINK inches."""
    groups, left = [], list(models)
    while left:
        group = [left.pop()]
        frontier = [group[0]]
        while frontier:
            m = frontier.pop()
            near = [o for o in left
                    if model_gap(m, o) <= LINK]
            for o in near:
                left.remove(o)
            group += near
            frontier += near
        groups.append(group)
    # nearest the +z edge first (then the -x edge), so --nth is stable
    groups.sort(key=lambda g: (-max(o["c"][2] for o in g), min(o["c"][0] for o in g)))
    return groups


def collect_units(objs):
    """Units per army and name: by unit tag (in list order) when the models
    have one, else by where they stand. A unit's models on and off the table
    are listed apart, with the same nth."""
    by_key = {}
    for o in objs:
        name = unit_name(o)
        if name:
            by_key.setdefault((army_of(o), name), []).append(o)
    units = []
    for (army, name), models in by_key.items():
        tagged_units, loose = {}, []
        for o in models:
            i = unit_index(o)
            (tagged_units.setdefault(i, []) if i is not None else loose).append(o)
        count = 0
        for count, i in enumerate(sorted(tagged_units), 1):
            sheet = next((tagged(o, SHEET_TAG) for o in tagged_units[i] if tagged(o, SHEET_TAG)), None)
            for placed in (True, False):
                group = [o for o in tagged_units[i] if on_table(o) == placed]
                if group:
                    units.append({"army": army, "name": name, "nth": count, "models": group, "on_table": placed,
                                  "unit_id": i, "datasheet": sheet})
        for placed in (True, False):
            group = [o for o in loose if on_table(o) == placed]
            for i, g in enumerate(split_by_distance(group), count + 1):
                units.append({"army": army, "name": name, "nth": i, "models": g, "on_table": placed,
                              "unit_id": None, "datasheet": None})
    return units


def table_surface(objs):
    """Height of the playing surface: the top of the mat under the table centre, or failing
    that the median base height of models on the table."""
    mats = [o["c"][1] + o["s"][1] / 2 for o in objs
            if o["tag"] != "Scripting" and o["s"][0] >= 40 and o["s"][2] >= 30 and o["s"][1] < 2
            and abs(o["c"][0]) < o["s"][0] / 2 and abs(o["c"][2]) < o["s"][2] / 2]
    if mats:
        return max(mats)
    ys = sorted(o["c"][1] - o["s"][1] / 2 for o in objs if unit_name(o) and on_table(o))
    return ys[len(ys) // 2] if ys else 0.0


def area_name(o):
    """What to call an unnamed flat piece: a terrain area's mat (LCT's have no name, and are
    "Board"s to TTS), with the objective LCT tags it as, if any. None for anything else."""
    if o["tag"] == "Scripting" or o["s"][1] >= layouts.FLAT:
        return None
    objective = next((layouts.OBJECTIVE_TAGS[t] for t in tags_of(o) if t in layouts.OBJECTIVE_TAGS), None)
    if not objective:
        return "Terrain area"
    kind, side = objective
    return f"Terrain area ({side + ' ' if side else ''}{kind} objective)"


def collect_terrain(objs):
    """Terrain features, terrain-area mats and scripting zones on the table.
    `height` is the top of the object above the playing surface."""
    surface = table_surface(objs)
    out = []
    for o in objs:
        if unit_name(o) or o["tag"] in IGNORE_TAGS or not on_table(o):
            continue
        sx, sy, sz = o["s"]
        if max(sx, sz) < 0.5 or (o["tag"] != "Scripting" and max(sx, sz) > MAX_TERRAIN):
            continue
        if o["tag"] != "Scripting" and not o["locked"]:
            continue  # loose tokens and markers; terrain in LCT and most tables is locked
        cx, cy, cz = o["c"]
        out.append({"name": o["name"] or area_name(o) or o["tag"], "guid": o["guid"], "kind": "zone" if o["tag"] == "Scripting" else "terrain",
                    "x": round(cx, 1), "z": round(cz, 1), "w": round(sx, 1), "d": round(sz, 1),
                    "height": round(cy + sy / 2 - surface, 1),
                    "box": [round(cx - sx / 2, 2), round(cx + sx / 2, 2), round(cz - sz / 2, 2), round(cz + sz / 2, 2)]})
    return out


def box_hit(x, z, r, box):
    xmin, xmax, zmin, zmax = box
    dx = max(xmin - x, 0, x - xmax)
    dz = max(zmin - z, 0, z - zmax)
    return dx * dx + dz * dz < r * r


def unit_row(u, terrain, surface=0.0, layout=None):
    """A unit as board.json lists it. With the layout on the table, also the terrain areas its
    models' bases overlap, and the objectives in those areas."""
    ms = u["models"]
    xs = [o["c"][0] for o in ms]
    zs = [o["c"][2] for o in ms]
    inside = sorted({t["name"] for t in terrain if t["kind"] == "terrain"
                     for o in ms if box_hit(o["c"][0], o["c"][2], radius(o), t["box"])})
    zones = sorted({t["name"] for t in terrain if t["kind"] == "zone"
                    for o in ms if box_hit(o["c"][0], o["c"][2], 0.01, t["box"])})
    areas = [a["id"] for a in (layout or {}).get("areas", [])
             if u["on_table"] and any(base_gap(o, a["polygon"]) == 0 for o in ms)]
    return {"army": u["army"], "unit": u["name"], "nth": u["nth"], "models": len(ms),
            "on_table": u["on_table"], "unit_id": u.get("unit_id"), "datasheet": u.get("datasheet"),
            "coherency": coherency(ms) if u["on_table"] else [],
            "x": round(sum(xs) / len(xs), 1), "z": round(sum(zs) / len(zs), 1),
            "box": [round(min(xs), 1), round(max(xs), 1), round(min(zs), 1), round(max(zs), 1)],
            "facing": round(ms[0]["rot"]) % 360, "touching": inside, "zones": zones,
            "guids": [o["guid"] for o in ms],
            "positions": [{"guid": o["guid"], "x": round(o["c"][0], 2), "z": round(o["c"][2], 2),
                           "height": round(o["c"][1] - o["s"][1] / 2 - surface, 1), "facing": round(o["rot"]) % 360,
                           "base": [round(o["s"][0], 2), round(o["s"][2], 2)]} for o in ms],
            **({"areas": areas, "objectives": [ob["id"] for ob in layout["objectives"] if ob["area"] in areas]}
               if layout else {})}


def closest_pair(a, b):
    """(distance, model of a, model of b): the closest two bases between two lists of models,
    0 when bases overlap (gap and model_gap keep the overlap, negative, for place's checks)."""
    d, p, q = min(((model_gap(p, q), p, q) for p in a for q in b), key=lambda t: t[0])
    return max(d, 0.0), p, q


def closest(a, b):
    """Closest base-to-base distance between two lists of models."""
    return closest_pair(a, b)[0]


def base_box(o):
    """A long base or hull's footprint as a polygon (axis-aligned, from its bounds)."""
    x, z = o["c"][0], o["c"][2]
    w, d = foot(o)
    return [[x - w / 2, z - d / 2], [x + w / 2, z - d / 2], [x + w / 2, z + d / 2], [x - w / 2, z + d / 2]]


def base_gap(o, poly):
    """How far a model's base is from a polygon, 0 when it overlaps; round bases as circles,
    long bases and hulls as boxes, like gap."""
    if is_round(foot(o)):
        return max(0.0, layouts.distance((o["c"][0], o["c"][2]), poly) - radius(o))
    return layouts.polygon_gap(base_box(o), poly)


def point_gap(o, point):
    """How far a model's base is from a point, 0 when the point is under it."""
    if is_round(foot(o)):
        return max(0.0, math.dist((o["c"][0], o["c"][2]), point) - radius(o))
    return layouts.distance(point, base_box(o))


def base_within(o, poly):
    """Whether a model's base is wholly inside a polygon."""
    if is_round(foot(o)):
        centre = (o["c"][0], o["c"][2])
        return layouts.inside(centre, poly) and layouts.edge_distance(centre, poly) >= radius(o)
    return layouts.polygon_within(base_box(o), poly)


def reach(models, poly):
    """(how far the nearest base is from a polygon, 0 when one overlaps it; whether every base is
    wholly inside it)."""
    return min(base_gap(o, poly) for o in models), all(base_within(o, poly) for o in models)


def landmarks(models, layout):
    """How far a unit is from each of the layout's objectives and deployment zones. `within`:
    a base overlaps the objective's terrain area (None for a marker on open ground, whose range
    the mission sets), or every base is wholly within the zone."""
    areas = {a["id"]: a["polygon"] for a in layout["areas"]}
    out = []
    for ob in layout["objectives"]:
        if ob["area"]:
            gap, _ = reach(models, areas[ob["area"]])
            within = gap == 0
        else:
            gap = min(point_gap(o, (ob["x"], ob["z"])) for o in models)
            within = None
        out.append({"kind": "objective", "id": ob["id"], "distance": round(gap, 2), "within": within})
    for zone in layout["zones"]:
        gap, wholly = reach(models, zone["polygon"])
        out.append({"kind": "zone", "id": zone["side"], "distance": round(gap, 2), "within": wholly})
    return out


def unit_ref(u):
    return {"army": u["army"], "unit": u["name"], "nth": u["nth"], "models": len(u["models"])}


def measure(a, b=None, layout=None):
    """Unit a to unit b (units as collect_units makes them): the closest base-to-base distance,
    horizontally, and the two models it's between; with a layout, each unit's distance to its
    objectives and zones."""
    out = {"a": unit_ref(a), "b": unit_ref(b) if b else None, "distance": None, "closest": None,
           "engagement_range": None, "layout": layout and layout["id"], "landmarks": []}
    if b:
        d, p, q = closest_pair(a["models"], b["models"])
        out.update(distance=round(d, 2), engagement_range=d <= ENGAGEMENT,
                   closest=[{"guid": o["guid"], "x": round(o["c"][0], 2), "z": round(o["c"][2], 2)} for o in (p, q)])
    if layout:
        out["landmarks"] = [{"unit": side, **row} for side, u in (("a", a), ("b", b)) if u
                            for row in landmarks(u["models"], layout)]
    return out


# --------------------------------------------------------------------------
# Commands

def board_state(objs, candidates=None):
    """The table as units and terrain: what board.json holds (docs/formats/board-state.md).
    `layout` is which of layouts/ (or `candidates`) is on the table, or None: its areas,
    objectives and zones are in layouts/<id>.json."""
    terrain, surface = collect_terrain(objs), table_surface(objs)
    found = layouts.identify(terrain, candidates)
    layout = found and found[0]
    return {"surface_y": round(surface, 2),
            "layout": found and {"id": layout["id"], "name": layout["name"], "matched": found[1], "pieces": found[2]},
            "units": [unit_row(u, terrain, surface, layout) for u in collect_units(objs)], "terrain": terrain}


def cmd_summary(_args):
    state = board_state(read_objects())
    rows, terrain, surface = state["units"], state["terrain"], state["surface_y"]
    BOARD_JSON.write_text(json.dumps(state, indent=2))
    if state["layout"]:
        print(f"Layout: {state['layout']['name']} (layouts/{state['layout']['id']}.json)")

    for army in sorted({r["army"] for r in rows}):
        print(f"\n== {army}")
        for r in sorted((r for r in rows if r["army"] == army), key=lambda r: (not r["on_table"], r["unit"])):
            label = r["unit"] + (f" #{r['nth']}" if r["nth"] > 1 else "")
            if not r["on_table"]:
                print(f"  {label:38} {r['models']:>2} models  off the table (reserves / not deployed)")
                continue
            where = f"({r['x']:6.1f}, {r['z']:6.1f})  facing {r['facing']:>3}"
            extra = ", ".join(r["touching"] + r["zones"] + [f"objective {o}" for o in r.get("objectives", [])])
            print(f"  {label:38} {r['models']:>2} models  {where}" + (f"  in: {extra}" if extra else ""))
            for problem in r["coherency"]:
                print(f"  {'':38} out of coherency: {problem}")
    print(f"\n== terrain and zones ({len(terrain)}); heights are above the table surface (y={surface:.1f})")
    for t in sorted(terrain, key=lambda t: (t["kind"], -t["z"], t["x"])):
        print(f"  {t['kind']:7} {t['name'][:36]:36} centre ({t['x']:6.1f}, {t['z']:6.1f})  "
              f"{t['w']:4.1f} x {t['d']:4.1f}  height {t['height']:4.1f}")
    print(f"\nWrote {BOARD_JSON.name}")


def army_matches(tag, army):
    """--army picks by the last part of the tag (Red / Blue / the list title)
    before falling back to any part of it."""
    return army is None or army.lower() == tag.split(":")[-1].lower()


class UnitError(ValueError):
    """A unit name that matches no unit, or several; the message says which."""


def find_unit(units, name, army=None, nth=None, on_table=True):
    want = name.lower()
    pool = [u for u in units if u["on_table"] == on_table]
    if army is not None and not any(army_matches(u["army"], army) for u in pool):
        pool = [u for u in pool if army.lower() in u["army"].lower()]
    elif army is not None:
        pool = [u for u in pool if army_matches(u["army"], army)]
    hits = [u for u in pool if want in u["name"].lower()]
    exact = [u for u in hits if u["name"].lower() == want]
    hits = exact or hits
    if nth is not None:
        hits = [u for u in hits if u["nth"] == nth]
    where = "on the table" if on_table else "off the table"
    if not hits:
        elsewhere = any(want in u["name"].lower() for u in units if u["on_table"] != on_table)
        raise UnitError(f'No unit {where} matches "{name}"'
                        + (f" (one {'off' if on_table else 'on'} the table does)." if elsewhere else "."))
    if len(hits) > 1:
        choices = "; ".join(f"{u['name']} #{u['nth']} ({u['army']})" for u in hits)
        raise UnitError(f'"{name}" matches {len(hits)} units {where}: {choices}. Narrow it by army or nth.')
    return hits[0]


def cmd_dist(args):
    units = collect_units(read_objects())
    a = find_unit(units, args.a, args.army_a, args.nth_a)
    b = find_unit(units, args.b, args.army_b, args.nth_b)
    d = closest(a["models"], b["models"])
    print(f'{a["name"]} -> {b["name"]}: {d:.1f}" base to base (round bases as circles, long bases as boxes)')


def formation(cx, cz, sizes, facing, cols):
    """Rows of `cols` models, the front row toward `facing`, centred on cx, cz."""
    n = len(sizes)
    cols = max(1, min(cols or math.ceil(math.sqrt(n)), n))
    rows = math.ceil(n / cols)
    t = math.radians(facing)
    fwd = (math.sin(t), math.cos(t))       # 0 -> +z, 90 -> +x
    right = (math.cos(t), -math.sin(t))
    # how far each footprint (w along x, d along z) reaches along the row and toward the front
    across = max(w * abs(right[0]) + d * abs(right[1]) for w, d in sizes) + GAP
    deep = max(w * abs(fwd[0]) + d * abs(fwd[1]) for w, d in sizes) + GAP
    out = []
    for i in range(n):
        r, c = divmod(i, cols)
        in_row = min(cols, n - r * cols)
        a = (c - (in_row - 1) / 2) * across
        b = -(r - (rows - 1) / 2) * deep
        out.append((cx + a * right[0] + b * fwd[0], cz + a * right[1] + b * fwd[1]))
    return out


def cmd_place(args):
    objs = read_objects()
    units = collect_units(objs)
    unit = find_unit(units, args.unit, args.army, args.nth, on_table=not args.from_reserves)
    ms = unit["models"]
    facing = round(args.facing if args.facing is not None else ms[0]["rot"]) % 360

    def new_foot(o):
        turn = (facing - o["rot"]) % 180
        return foot(o)[::-1] if 45 < turn < 135 else foot(o)
    feet = [new_foot(o) for o in ms]
    spots = formation(args.x, args.z, feet, facing, args.cols)
    terrain = collect_terrain(objs)

    problems, notes = [], []
    mine = {o["guid"] for o in ms}
    others = [o for u in units if u["on_table"] for o in u["models"] if o["guid"] not in mine]
    overlaps, engaged = set(), {}
    for f, (x, z) in zip(feet, spots):
        if abs(x) + f[0] / 2 > HALF_X or abs(z) + f[1] / 2 > HALF_Z:
            problems.append(f"a model at ({x:.1f}, {z:.1f}) would be off the table")
        for q in others:
            other = unit_name(q)
            g = gap(x, z, f, q["c"][0], q["c"][2], foot(q))
            if g < 0:
                overlaps.add(other)
            elif g < ENGAGEMENT and army_of(q) != unit["army"]:
                engaged[other] = min(g, engaged.get(other, g))
    problems += [f"overlaps a model of {u}" for u in sorted(overlaps)]
    problems += [f"within engagement range of enemy {u} ({g:.1f}\")" for u, g in sorted(engaged.items())]
    # coherency, edge to edge: every model within 2" of another and within 9" of all of them
    gaps = [[gap(*spots[i], feet[i], *spots[j], feet[j]) for j in range(len(ms))]
            for i in range(len(ms))]
    if len(ms) > 1:
        loose = max(min(g for j, g in enumerate(row) if j != i) for i, row in enumerate(gaps))
        if loose > 2.0:
            problems.append(f"a model would be {loose:.1f}\" from the rest of its unit (coherency is 2\"); "
                            "place it separately or change --cols")
        span = max(max(row) for row in gaps)
        if span > COHERENCY_MAX:
            problems.append(f"models would be {span:.1f}\" apart, more than 9\" coherency; use more --cols or rows")
    enemies = [o for o in others if army_of(o) != unit["army"]]
    if enemies:
        fake = [{"c": [x, 0, z], "s": [f[0], 0, f[1]]} for f, (x, z) in zip(feet, spots)]
        notes.append(f"nearest enemy model {closest(fake, enemies):.1f}\" away")
    inside = sorted({t["name"] for t in terrain for f, (x, z) in zip(feet, spots)
                     if box_hit(x, z, (f[0] + f[1]) / 4 if t["kind"] == "terrain" else 0.01, t["box"])})
    if inside:
        notes.append("touching: " + ", ".join(inside))

    for p in problems:
        print("PROBLEM:", p)
    for n in notes:
        print("note:", n)
    print(f'{unit["name"]}: {len(ms)} models to ({args.x}, {args.z}) facing {facing}, '
          f'{len(problems)} problems')
    if args.check or (problems and not args.force):
        if problems and not args.check:
            print("Not moved. Fix the problems, or pass --force to move anyway.")
        return

    # spots are where each base's centre goes; TTS positions the object's pivot, which
    # can sit off the base centre, so shift by the same offset
    UNDO_JSON.write_text(json.dumps([[o["guid"], *o["p"], o["rot"]] for o in ms]))
    moves = []
    for o, (x, z) in zip(ms, spots):
        # offset from pivot to base centre, turned by the change in facing
        t = math.radians(facing - o["rot"])
        ox, oz = o["c"][0] - o["p"][0], o["c"][2] - o["p"][2]
        ox, oz = ox * math.cos(t) + oz * math.sin(t), -ox * math.sin(t) + oz * math.cos(t)
        moves.append((o["guid"], x - ox, args.y, z - oz, facing))
    moved = move(moves)
    print(f"Moved {moved} models. `python3 board.py undo` puts them back.")


def move(moves):
    """moves: (guid, x, y, z, facing) with x, y, z the object's position."""
    lines = [f'do local o = getObjectFromGUID("{g}") if o then '
             f'o.setPositionSmooth({{{x:.2f}, {y:.2f}, {z:.2f}}}, false, true) '
             f'o.setRotationSmooth({{0, {f:.0f}, 0}}, false, true) end end'
             for g, x, y, z, f in moves]
    lines.append(f"return {len(moves)}")
    return tts.run_lua("\n".join(lines), timeout=20)


def cmd_undo(_args):
    if not UNDO_JSON.exists():
        sys.exit("Nothing to undo.")
    moves = json.loads(UNDO_JSON.read_text())
    # files from before undo kept heights hold [guid, x, z, facing]
    moves = [m if len(m) == 5 else [m[0], m[1], DROP_Y, m[2], m[3]] for m in moves]
    print("Moved back", move(moves), "models")
    UNDO_JSON.unlink()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary").set_defaults(fn=cmd_summary)
    d = sub.add_parser("dist")
    d.add_argument("a")
    d.add_argument("b")
    for side in ("a", "b"):
        d.add_argument(f"--army-{side}")
        d.add_argument(f"--nth-{side}", type=int)
    d.set_defaults(fn=cmd_dist)
    pl = sub.add_parser("place")
    pl.add_argument("unit")
    pl.add_argument("x", type=float)
    pl.add_argument("z", type=float)
    pl.add_argument("facing", type=float, nargs="?")
    pl.add_argument("--cols", type=int, help="models per row (default: a square-ish block)")
    pl.add_argument("--y", type=float, default=DROP_Y, help="drop height, for upper floors")
    pl.add_argument("--army", help="part of the army tag, when both armies have the unit")
    pl.add_argument("--nth", type=int, help="which unit, when there are several with this name")
    pl.add_argument("--from-reserves", action="store_true", help="take the unit from off the table")
    pl.add_argument("--check", action="store_true", help="validate only, don't move")
    pl.add_argument("--force", action="store_true", help="move even if there are problems")
    pl.set_defaults(fn=cmd_place)
    sub.add_parser("undo").set_defaults(fn=cmd_undo)
    args = p.parse_args()
    tts.start_listener()
    try:
        args.fn(args)
    except UnitError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
