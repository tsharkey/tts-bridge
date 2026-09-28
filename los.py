"""
los.py — what a model can see, from layout terrain (docs/formats/layout-terrain.md),
worked out in 2D from the footprints, offline: no TTS, no meshes.

    import los, layouts
    terrain = los.blockers(layouts.load("33ce09"))
    a, b = los.model(position_a), los.model(position_b)   # board_summary's "positions" rows
    los.sight(a, b, terrain)                   # ("full" | "partial" | "none", {"A3", "A3a"})
    los.unit_visibility(unit_a, unit_b, terrain, target_hidden=True)
    los.visibility_polygon(a, terrain)          # the table a model can see, for drawing
    los.visibility_polygon(a, terrain, max_range=24)   # ... that a 24" gun reaches

The rules (the terrain table in .claude/skills/wh40k-deployment-planner/SKILL.md):
- Obscuring: a terrain area with a light or dense feature blocks a line that crosses it,
  unless either model is within the area (its base overlaps it). `all_obscuring` makes
  every area Obscuring, as the maintainer's games play it.
- Solid: a dense feature blocks a line through it at ground level (gaps 3" or lower).
- Plunging Fire: a shooter on a floor more than 3" up, against a target at ground level.
- Hidden: a hidden target is only visible within 15" (base to base), or 12" when it's
  Gone to Ground (not fully visible to that observer because of a dense feature).

Simplifications, since only footprints and heights are known:
- Bases are circles (half the mean of width and depth, as board.radius); a model's line
  of sight runs from any of 9 points on its base (the centre and 8 round the edge) to any
  of 9 on the target's. A target model is fully visible when every one of its points is
  seen from some point of the observer, partly when some are.
- A feature's footprint is its convex outline, so an L-shaped ruin blocks like a triangle,
  and windows and doorways aren't modelled.
- Solid applies when both models are 3" up or lower: a line from or to an upper floor goes
  over the gaps. A model within a dense feature's footprint sees out of it (models stand at
  its gaps), and is seen out of it the same way.
- Heights only matter through floors: Obscuring areas block whatever height the models are
  at, and nothing is too tall to see over.
"""

import math

import layouts

HALF_X, HALF_Z = 30, 22
FLOOR = 3.0            # Solid and Plunging Fire: above 3" is off the ground floor
GROUND = 1.0           # a base lower than this is at ground level (layouts.FLOOR_MIN)
HIDDEN_RANGE = 15.0
GONE_TO_GROUND_RANGE = 12.0
EDGE_POINTS = 8        # points round each base's edge, besides its centre
RAYS = 360             # visibility_polygon: rays round the circle, besides those at terrain corners
ORDER = {"none": 0, "partial": 1, "full": 2}


def model(position):
    """A model for the functions here, from a board_summary / board.json "positions" row."""
    w, d = position.get("base") or (1.26, 1.26)
    return {"guid": position.get("guid"), "x": position["x"], "z": position["z"],
            "r": (w + d) / 4, "height": position.get("height") or 0.0}


def box(poly):
    xs, zs = [x for x, _ in poly], [z for _, z in poly]
    return min(xs), max(xs), min(zs), max(zs)


def blockers(layout, all_obscuring=False):
    """What can block a line: Obscuring areas (those with a light or dense feature, or all of
    them) and Solid features (dense)."""
    out = []
    for a in layout["areas"]:
        if all_obscuring or any(f["category"] in ("light", "dense") for f in a["features"]):
            out.append({"id": a["id"], "kind": "obscuring", "polygon": a["polygon"], "box": box(a["polygon"])})
        for f in a["features"]:
            if f["category"] == "dense":
                out.append({"id": f["id"], "kind": "solid", "polygon": f["polygon"], "box": box(f["polygon"])})
    return out


def within(m, b):
    """Whether a model's base overlaps a blocker."""
    return layouts.distance((m["x"], m["z"]), b["polygon"]) < m["r"]


def points(m):
    x, z, r = m["x"], m["z"], m["r"]
    return [(x, z)] + [(x + r * math.cos(2 * math.pi * i / EDGE_POINTS), z + r * math.sin(2 * math.pi * i / EDGE_POINTS))
                       for i in range(EDGE_POINTS)]


def hits(p, q, poly):
    """Whether the segment p-q passes through a polygon's inside (grazing an edge or corner doesn't)."""
    (px, pz), (qx, qz) = p, q
    dx, dz = qx - px, qz - pz
    ts = [0.0, 1.0]
    for (x1, z1), (x2, z2) in layouts.edges(poly):
        ex, ez = x2 - x1, z2 - z1
        den = dx * ez - dz * ex
        if den == 0:
            continue
        t = ((x1 - px) * ez - (z1 - pz) * ex) / den
        u = ((x1 - px) * dz - (z1 - pz) * dx) / den
        if 0 < t < 1 and -1e-9 <= u <= 1 + 1e-9:
            ts.append(t)
    ts.sort()
    return any(layouts.inside((px + dx * (t1 + t2) / 2, pz + dz * (t1 + t2) / 2), poly)
               for t1, t2 in zip(ts, ts[1:]) if t2 - t1 > 1e-9)


def applicable(a, b, terrain):
    """The blockers that count between two models, near the line between them."""
    xmin, xmax = min(a["x"] - a["r"], b["x"] - b["r"]), max(a["x"] + a["r"], b["x"] + b["r"])
    zmin, zmax = min(a["z"] - a["r"], b["z"] - b["r"]), max(a["z"] + a["r"], b["z"] + b["r"])
    low = a["height"] <= FLOOR and b["height"] <= FLOOR
    return [t for t in terrain
            if (t["kind"] == "obscuring" or low)
            and t["box"][0] < xmax and t["box"][1] > xmin and t["box"][2] < zmax and t["box"][3] > zmin
            and not within(a, t) and not within(b, t)]


def sight(a, b, terrain):
    """How much of model b model a can see: ("full" | "partial" | "none", ids of what blocked
    the lines to what it can't see)."""
    near = applicable(a, b, terrain)
    if not near:
        return "full", set()
    seen, blocked = 0, set()
    for q in points(b):
        tried = set()
        for p in points(a):
            stop = next((t["id"] for t in near if hits(p, q, t["polygon"])), None)
            if stop is None:
                seen += 1
                break
            tried.add(stop)
        else:
            blocked |= tried
    total = EDGE_POINTS + 1
    return ("full" if seen == total else "partial" if seen else "none"), blocked


def gap(a, b):
    return max(0.0, math.dist((a["x"], a["z"]), (b["x"], b["z"])) - a["r"] - b["r"])


def unit_visibility(observers, targets, terrain, target_hidden=False):
    """What a unit (observers) can see of another (targets), per target model: "visible"
    (full | partial | none, the best any observer gets), "seen_by" (observer guids that see
    it at all), "blocked_by" (areas and features blocking lines to it), and whether Plunging
    Fire applies. target_hidden: the targets are Hidden (the caller knows keywords and whether
    they shot), so they're only seen within detection range; "beyond_detection" lists the
    observers that could see them but are too far."""
    solid = {t["id"] for t in terrain if t["kind"] == "solid"}
    rows = []
    for tm in targets:
        best, seen_by, blocked, far, plunging = "none", [], set(), [], False
        for om in observers:
            state, by = sight(om, tm, terrain)
            if state != "none" and target_hidden:
                reach = GONE_TO_GROUND_RANGE if state != "full" and by & solid else HIDDEN_RANGE
                if gap(om, tm) > reach:
                    far.append(om["guid"])
                    state = "none"
            blocked |= by
            if state == "none":
                continue
            seen_by.append(om["guid"])
            plunging |= om["height"] > FLOOR and tm["height"] < GROUND
            if ORDER[state] > ORDER[best]:
                best = state
        row = {"guid": tm["guid"], "visible": best, "seen_by": seen_by, "blocked_by": sorted(blocked),
               "plunging_fire": plunging}
        if target_hidden:
            row["beyond_detection"] = far
        rows.append(row)
    return {"visible": sum(r["visible"] != "none" for r in rows),
            "fully_visible": sum(r["visible"] == "full" for r in rows),
            "plunging_fire": any(r["plunging_fire"] for r in rows), "models": rows}


def hidden_areas(models, layout):
    """The terrain areas with a light or dense feature a unit is within, if every model's base
    overlaps one of them (what Hidden needs besides keywords and not having shot); else []."""
    areas = [a for a in layout["areas"] if any(f["category"] in ("light", "dense") for f in a["features"])]
    found = set()
    for m in models:
        mine = [a["id"] for a in areas if layouts.distance((m["x"], m["z"]), a["polygon"]) < m["r"]]
        if not mine:
            return []
        found.update(mine)
    return sorted(found)


def exit_distance(ox, oz, dx, dz, poly):
    """How far along a ray from (ox, oz) it leaves a polygon for the last time, or None if it misses."""
    far = None
    for (x1, z1), (x2, z2) in layouts.edges(poly):
        ex, ez = x2 - x1, z2 - z1
        den = dx * ez - dz * ex
        if den == 0:
            continue
        t = ((x1 - ox) * ez - (z1 - oz) * ex) / den
        u = ((x1 - ox) * dz - (z1 - oz) * dx) / den
        if t > 0 and 0 <= u <= 1 and (far is None or t > far):
            far = t
    return far


def visibility_polygon(m, terrain, max_range=None):
    """The part of the table a model can see from its base's centre, as a polygon of [x, z]
    corners, for drawing: rays out to the table edge, each stopped where it leaves the first
    blocker it passes through (a target inside an Obscuring area is within it, so the whole
    area is seen). Targets are taken to be at ground level. max_range: no further than this
    from the base's edge (a weapon's range, or Night Fighting's 18")."""
    ox, oz = m["x"], m["z"]
    low = m["height"] <= FLOOR
    near = []
    for t in terrain:
        if (t["kind"] == "solid" and not low) or within(m, t):
            continue
        angles = [math.atan2(z - oz, x - ox) for x, z in t["polygon"]]
        base = angles[0]
        spread = [(a - base + math.pi) % (2 * math.pi) - math.pi for a in angles]   # the polygon spans < 180°
        near.append((base + min(spread), base + max(spread), t["polygon"]))
    rays = [2 * math.pi * i / RAYS - math.pi for i in range(RAYS)]
    corners = [math.atan2(z - oz, x - ox) for t in near for x, z in t[2]]
    corners += [math.atan2(z - oz, x - ox) for x, z in ((HALF_X, HALF_Z), (-HALF_X, HALF_Z), (-HALF_X, -HALF_Z), (HALF_X, -HALF_Z))]
    rays += [a + e for a in corners for e in (-1e-4, 0.0, 1e-4)]
    out = []
    for angle in sorted(rays):
        dx, dz = math.cos(angle), math.sin(angle)
        # to the table edge
        reach = min((HALF_X - ox) / dx if dx > 0 else (-HALF_X - ox) / dx if dx < 0 else math.inf,
                    (HALF_Z - oz) / dz if dz > 0 else (-HALF_Z - oz) / dz if dz < 0 else math.inf,
                    math.inf if max_range is None else m["r"] + max_range)
        for lo, hi, poly in near:
            if (angle - lo) % (2 * math.pi) <= (hi - lo) + 1e-9:
                t = exit_distance(ox, oz, dx, dz, poly)
                if t is not None and t < reach:
                    reach = t
        out.append([round(ox + dx * reach, 3), round(oz + dz * reach, 3)])
    return [p for i, p in enumerate(out) if p != out[i - 1]]
