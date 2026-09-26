"""
recreate.py — rebuild a board state in TTS from a scene file.

    python3 recreate.py scenes/<scene>.json [--keep]

A scene lists, per army, where each unit stands (table inches, 0,0 = centre,
+z = top of the image) and how many of its models are on the table. Units
that share a spot (a leader and its bodyguard) are packed together. Every
model in the list that the scene doesn't place goes onto that army's
reserves board, since a mid-game image won't show dead or reserve units.
Anything a previous army.py/recreate.py run spawned is removed first unless
keep=True; nothing else on the table is touched.
"""

import json
import math
import sys
import time
from pathlib import Path

import army
import tts_bridge as tts

HALF_X, HALF_Z = 30, 22
GAP = 0.25

# LCT's two "Reinforcements and Reserves Board" objects sit off the long
# edges; without them, reserves go in a strip just past the short edge.
FIND_RESERVE_BOARDS_LUA = """
local out = {}
for _, o in ipairs(getObjects()) do
    if o.getName() == "Reinforcements and Reserves Board" then
        local b = o.getBounds()
        table.insert(out, {b.center.x, b.center.z, b.size.x, b.size.z})
    end
end
return out
"""


def pick_models(parsed, entry):
    """(placed, rest) for the n-th unit with this name in the list."""
    matches = [u for u in parsed["units"] if u["name"] == entry["unit"]]
    n = entry.get("n", 1)
    if len(matches) < n:
        raise ValueError(f"{entry['unit']} #{n} isn't in the list")
    unit = matches[n - 1]
    count = entry.get("count", len(unit["models"]))
    return unit, unit["models"][:count], unit["models"][count:]


def cluster(center, sizes):
    """Lay models out in a rough square around center; returns [(x, z)]."""
    n = len(sizes)
    cols = max(1, math.ceil(math.sqrt(n)))
    step = max(max(w, d) for w, d in sizes) + GAP
    rows = math.ceil(n / cols)
    cx, cz = center
    out = []
    for i in range(n):
        r, c = divmod(i, cols)
        x = cx + (c - (cols - 1) / 2) * step
        z = cz - (r - (rows - 1) / 2) * step
        w, d = sizes[i]
        out.append((min(max(x, -HALF_X + w / 2), HALF_X - w / 2),
                    min(max(z, -HALF_Z + d / 2), HALF_Z - d / 2)))
    return out


def shelf(rect, sizes, groups):
    """Pack models into rect (xmin, xmax, zmin, zmax) row by row, keeping each
    unit together. groups: list of index lists. Returns {index: (x, z)}."""
    xmin, xmax, zmin, zmax = rect
    x, z, row_d, out = xmin, zmax, 0.0, {}
    for idxs in groups:
        for i in idxs:
            w, d = sizes[i]
            if x + w > xmax and x > xmin:
                x, z, row_d = xmin, z - row_d - GAP, 0.0
            out[i] = (x + w / 2, max(z - d / 2, zmin + d / 2))
            x += w + GAP
            row_d = max(row_d, d)
        x += 1.0
    return out


def model_object(catalog, m, unit_name, tag, facing):
    g, i = m["pick"].split(":")
    o = json.loads(json.dumps(catalog[g][int(i)]))
    o.pop("GUID", None)
    o["GMNotes"] = tag
    o["Description"] = f"[{unit_name}]\n" + (o.get("Description") or "")
    o["Locked"] = True
    o["Transform"].update(rotX=0, rotY=facing, rotZ=0)
    return o


def place_scene(scene, tag="recreate:scene", keep=False, log=print):
    """Spawn a scene into the running TTS game. Returns a summary dict."""
    catalog = army.load_catalog()
    mappings = army.load_mappings()

    field, reserves = [], []  # field: (center, facing, objs); reserves: (army idx, [objs per unit])
    for ai, a in enumerate(scene["armies"]):
        text = a.get("list_text") or Path(a["list"]).read_text()
        parsed = army.parse_list(text, mappings)
        army.resolve(parsed, catalog, mappings)
        facing = a.get("facing", 180 if ai == 0 else 0)
        used = set()
        by_spot = {}
        for entry in a["units"]:
            unit, placed, rest = pick_models(parsed, entry)
            used.add(id(unit))
            key = tuple(entry["at"])
            for m in placed:
                if m["pick"]:
                    by_spot.setdefault(key, []).append(model_object(catalog, m, unit["name"], tag, facing))
            if rest:
                reserves.append((ai, [model_object(catalog, m, unit["name"], tag, facing)
                                      for m in rest if m["pick"]]))
        for u in parsed["units"]:
            if id(u) not in used:
                reserves.append((ai, [model_object(catalog, m, u["name"], tag, facing)
                                      for m in u["models"] if m["pick"]]))
        field += [(k, facing, objs) for k, objs in by_spot.items()]

    tts.run_lua('for _, o in ipairs(getObjects()) do local g = o.getGMNotes() or "" '
                'if g:find("army.py:", 1, true) or g:find("recreate:", 1, true) then '
                'o.destruct() end end return 1') if not keep else None

    # spawn locked high above the table (well clear of any mod's zones)
    everything = [o for _, _, objs in field for o in objs] + [o for _, objs in reserves for o in objs]
    for n, o in enumerate(everything):
        o["Transform"].update(posX=-28 + (n % 30) * 1.9, posY=25 + (n // 30) * 3, posZ=0)
    lines = ["local g = {}"]
    for o in everything:
        lines.append(f"table.insert(g, spawnObjectJSON({{json = {tts.lua_str(json.dumps(o))}}}).guid)")
    lines.append("return g")
    guids = json.loads(tts.run_lua("\n".join(lines), timeout=90))

    sizes = {}
    for _ in range(60):
        r = tts.run_lua(army.SPAWN_LUA_WAIT.format(ids=army.lua_list(guids)))
        if r and r != "wait":
            sizes = json.loads(r)
            break
        time.sleep(1)
    dims = [tuple(sizes.get(g, [1.3, 1.3])) for g in guids]

    moves = []  # (guid, x, y, z, facing)
    k = 0
    for center, facing, objs in field:
        ids = list(range(k, k + len(objs)))
        k += len(objs)
        for i, (x, z) in zip(ids, cluster(center, [dims[i] for i in ids])):
            moves.append((guids[i], x, 4, z, facing))

    boards = json.loads(tts.run_lua(FIND_RESERVE_BOARDS_LUA) or "[]")
    for ai in range(len(scene["armies"])):
        groups = []
        for a_idx, objs in reserves:
            if a_idx == ai:
                groups.append(list(range(k, k + len(objs))))
                k += len(objs)
        if not groups:
            continue
        top = ai == 0  # first army is Red, whose reserves board sits on the +z side
        if boards:
            cx, cz, w, d = max(boards, key=lambda b: b[1]) if top else min(boards, key=lambda b: b[1])
            rect = (cx - w / 2 + 1, cx + w / 2 - 1, cz - d / 2 + 1, cz + d / 2 - 1)
        else:
            rect = (HALF_X + 3, HALF_X + 23, 1, HALF_Z) if top else (HALF_X + 3, HALF_X + 23, -HALF_Z, -1)
        facing = scene["armies"][ai].get("facing", 180 if ai == 0 else 0)
        for i, (x, z) in shelf(rect, dims, groups).items():
            moves.append((guids[i], x, 4, z, facing))

    tts.run_lua("\n".join(
        f'do local o = getObjectFromGUID("{g}") if o then o.setPosition({{{x:.2f}, {y}, {z:.2f}}}) '
        f'o.setRotation({{0, {f}, 0}}) o.setLock(false) end end' for g, x, y, z, f in moves), timeout=30)

    placed = sum(len(objs) for _, _, objs in field)
    summary = {"spawned": len(guids), "on_table": placed, "in_reserves": len(guids) - placed,
               "reserve_boards": bool(boards)}
    log(f"Placed {placed} models on the table and {len(guids) - placed} in reserves"
        + ("" if boards else " (no reserves boards found, used a strip beside the table)"))
    return summary


def main():
    scene_path = Path(sys.argv[1])
    tts.start_listener()
    place_scene(json.loads(scene_path.read_text()), f"recreate:{scene_path.stem}",
                keep="--keep" in sys.argv)


if __name__ == "__main__":
    main()
