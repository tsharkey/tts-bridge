"""
layouts.py — exact terrain for every LCT layout, in the layout terrain format
(docs/formats/layout-terrain.md): terrain areas and features as rotated
footprints, their categories and heights, objectives and deployment zones.

    python3 data.py mods lct       # first: LCT's layouts into cache/lct/
    python3 layouts.py build       # writes layouts/<id>.json for every matchup layout
    python3 layouts.py build --download   # also fetch meshes TTS hasn't downloaded, into cache/meshes/
    python3 layouts.py check       # compare the layout on the table in TTS with what build made

Everything comes from files TTS already has, so it runs offline with TTS
closed: each layout's objects from cache/lct/, and each object's mesh from
TTS's download cache (Mods/Models for OBJ files, Mods/Assetbundles for Unity
asset bundles, which need UnityPy: `pip install -r requirements-dev.txt`).
A mesh TTS hasn't downloaded yet is reported; build with --download (or load
that layout in TTS once) to fill the gap. The output is committed; LCT's own data isn't.

    import layouts
    layout = layouts.load("0c4960")   # one layout, by LCT's card GUID
"""

import json
import math
import re
import sys
from pathlib import Path

import data
import mods

ROOT = Path(__file__).parent
LAYOUTS = ROOT / "layouts"
VERSION = 1
HALF_X, HALF_Z = 30, 22
FLAT = 0.3          # a mesh less tall than this is a terrain area's footprint, not a feature
MAT = 40            # a mesh wider than this is the table mat
FLOOR_AREA = 2.0    # square inches of upward-facing surface at one height that make a floor
FLOOR_MIN = 1.0     # lower than this is the ground floor (bases, rubble)
OBJECTIVE_TAGS = {"obj_home_red": ("home", "red"), "obj_home_blue": ("home", "blue"),
                  "obj_center": ("central", None), "obj_center1": ("central", None),
                  "obj_center2": ("central", None), "obj_triangle": ("central", None),   # Priority Assets' pair
                  "obj_neutral": ("expansion", None)}

# LCT's 11th edition deployment zones (DeployZonesEdition11 in its Start Menu
# script), in its own terms: "fromSide" is from the table edge the zone is on,
# steps run from +z to -z (or +x to -x for the z zones). Teal is Blue.
DEPLOYMENTS = {
    "Hammer and Anvil": {"red": ("line", "x", 18), "blue": ("line", "-x", 18)},
    "Dawn of War": {"red": ("line", "z", 12), "blue": ("line", "-z", 12)},
    "Sweeping Engagement": {"red": ("stepped", "z", [14, 8]), "blue": ("stepped", "-z", [8, 14])},
    "Search and Destroy": {"red": ("quarter", "xz", 9), "blue": ("quarter", "-x-z", 9)},
    "Crucible of Battle": {"red": ("triangle", "x", None), "blue": ("triangle", "-x", None)},
    "Tipping Point": {"red": ("stepped", "x", [20, 12]), "blue": ("stepped", "-x", [12, 20])},
}


# --------------------------------------------------------------------------
# Geometry. Points are (x, z) in table inches; polygons run counter-clockwise
# seen from above (+x right, +z up).

def signed_area(poly):
    return sum(x1 * z2 - x2 * z1 for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1])) / 2


def ccw(poly):
    return poly if signed_area(poly) > 0 else poly[::-1]


def hull(points):
    """Convex hull, counter-clockwise (Andrew's monotone chain)."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out[:-1]
    return half(pts) + half(reversed(pts))


def simplify(poly, tolerance=0.05):
    """Drop corners that change the outline by less than `tolerance` square
    inches (the rugged edges of LCT's area mats have hundreds)."""
    poly = list(poly)
    while len(poly) > 3:
        areas = [abs(signed_area([poly[i - 1], poly[i], poly[(i + 1) % len(poly)]])) for i in range(len(poly))]
        i = min(range(len(poly)), key=areas.__getitem__)
        if areas[i] >= tolerance:
            break
        poly.pop(i)
    return poly


def centroid(poly):
    a = signed_area(poly)
    cx = sum((x1 + x2) * (x1 * z2 - x2 * z1) for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1]))
    cz = sum((z1 + z2) * (x1 * z2 - x2 * z1) for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1]))
    return cx / (6 * a), cz / (6 * a)


def inside(point, poly):
    x, z = point
    hit = False
    for (x1, z1), (x2, z2) in zip(poly, poly[-1:] + poly[:-1]):
        if (z1 > z) != (z2 > z) and x < (x2 - x1) * (z - z1) / (z2 - z1) + x1:
            hit = not hit
    return hit


def rounded(poly):
    return [[round(x, 2), round(z, 2)] for x, z in poly]


# --------------------------------------------------------------------------
# Placing a mesh on the table the way TTS does

def rotate_euler(v, rx, ry, rz):
    """Unity's rotation order: z, then x, then y (degrees)."""
    x, y, z = v
    a = math.radians(rz)
    x, y = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
    a = math.radians(rx)
    y, z = y * math.cos(a) - z * math.sin(a), y * math.sin(a) + z * math.cos(a)
    a = math.radians(ry)
    x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)
    return x, y, z


def place(vertices, t):
    """Mesh vertices (as their files store them) -> table coordinates for an
    object with TTS Transform t. TTS mirrors x on import, like Unity."""
    out = []
    for x, y, z in vertices:
        v = rotate_euler((-x * t["scaleX"], y * t["scaleY"], z * t["scaleZ"]), t["rotX"], t["rotY"], t["rotZ"])
        out.append((v[0] + t["posX"], v[1] + t["posY"], v[2] + t["posZ"]))
    return out


# --------------------------------------------------------------------------
# Meshes from TTS's download cache

def tts_cache_dir():
    return mods.mods_dir().parent   # .../Mods, beside Workshop


MESH_CACHE = data.CACHE / "meshes"   # meshes we downloaded ourselves (--download)


def cached_path(url, kind):
    """Where TTS keeps a downloaded mesh (its URL with everything but letters
    and digits removed), or where --download put it."""
    folder, ext = {"obj": ("Models", ".obj"), "bundle": ("Assetbundles", ".unity3d")}[kind]
    name = re.sub(r"[^A-Za-z0-9]", "", url) + ext
    tts = tts_cache_dir() / folder / name
    return tts if tts.exists() else MESH_CACHE / name


def download(url, path):
    import urllib.request
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as r:
        path.write_bytes(r.read())


def read_obj(path):
    """-> (vertices, triangles as vertex index triples)."""
    verts, tris = [], []
    for line in Path(path).read_text(errors="replace").splitlines():
        if line.startswith("v "):
            verts.append(tuple(float(a) for a in line.split()[1:4]))
        elif line.startswith("f "):
            idx = [int(p.split("/")[0]) for p in line.split()[1:]]
            idx = [i - 1 if i > 0 else len(verts) + i for i in idx]
            tris += [(idx[0], idx[i], idx[i + 1]) for i in range(1, len(idx) - 1)]
    return verts, tris


def read_bundle(path):
    """Vertices of every mesh in a Unity asset bundle, in the bundle's root
    object's space (the root's own transform is where TTS puts the object)."""
    try:
        import UnityPy
    except ImportError:
        sys.exit("Reading LCT's asset bundles needs UnityPy: .venv/bin/pip install -r requirements-dev.txt")
    verts = []
    for obj in UnityPy.load(str(path)).objects:
        if obj.type.name != "MeshFilter":
            continue
        mf = obj.read()
        mesh = mf.m_Mesh.read()
        vs = [tuple(float(a) for a in line.split()[1:4]) for line in mesh.export().splitlines() if line.startswith("v ")]
        vs = [(-x, y, z) for x, y, z in vs]   # UnityPy's OBJ export mirrors x; undo it so bundles read like OBJ files
        go = mf.m_GameObject.read()
        tr = next(c.component.read() for c in go.m_Component if c.component.type.name == "Transform")
        while tr.m_Father and tr.m_Father.path_id:   # every transform but the root's
            p, q, s = tr.m_LocalPosition, tr.m_LocalRotation, tr.m_LocalScale
            vs = [quat_rotate((x * s.x, y * s.y, z * s.z), (q.x, q.y, q.z, q.w)) for x, y, z in vs]
            vs = [(x + p.x, y + p.y, z + p.z) for x, y, z in vs]
            tr = tr.m_Father.read()
        # back to OBJ-file handedness, which place() mirrors
        verts += [(-x, y, z) for x, y, z in vs]
    return verts, []


def quat_rotate(v, q):
    x, y, z = v
    qx, qy, qz, qw = q
    tx, ty, tz = 2 * (qy * z - qz * y), 2 * (qz * x - qx * z), 2 * (qx * y - qy * x)
    return (x + qw * tx + qy * tz - qz * ty, y + qw * ty + qz * tx - qx * tz, z + qw * tz + qx * ty - qy * tx)


def mesh_source(o):
    """(url, kind) of an object's mesh, or None."""
    if o.get("CustomMesh"):
        return o["CustomMesh"]["MeshURL"], "obj"
    if o.get("CustomAssetbundle"):
        return o["CustomAssetbundle"]["AssetbundleURL"], "bundle"
    return None


class Meshes:
    """Meshes read from TTS's cache, each once. load(url, kind) -> (vertices, triangles) or None.
    With fetch, a mesh TTS hasn't downloaded is downloaded into cache/meshes/."""
    def __init__(self, fetch=False, log=print):
        self.seen, self.fetch, self.log = {}, fetch, log

    def load(self, url, kind):
        if (url, kind) not in self.seen:
            path = cached_path(url, kind)
            if not path.exists() and self.fetch:
                self.log(f"  downloading {url}")
                try:
                    download(url, path)
                except OSError as e:
                    self.log(f"  couldn't download it: {e}")
            self.seen[url, kind] = (read_obj(path) if kind == "obj" else read_bundle(path)) if path.exists() else None
        return self.seen[url, kind]


# --------------------------------------------------------------------------
# One layout

def category(o):
    """dense | light | exposed from LCT's description or name, or None."""
    text = f"{o.get('Description') or ''} {o.get('Nickname') or ''}".lower()
    if "dense" in text or "heavy" in text:   # "Tower = Dense, Walls = Light" counts as dense
        return "dense"
    if "light" in text:
        return "light"
    if "exposed" in text:
        return "exposed"
    return None


def floors(world, tris, surface, top):
    """Heights above the table of the upward-facing surfaces big enough to stand on."""
    by_height = {}
    for a, b, c in tris:
        (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = world[a], world[b], world[c]
        ux, uy, uz, vx, vy, vz = x2 - x1, y2 - y1, z2 - z1, x3 - x1, y3 - y1, z3 - z1
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        size = math.sqrt(nx * nx + ny * ny + nz * nz)
        if not size or abs(ny) < 0.98 * size:
            continue   # not level (within about 10°, for pieces TTS has slightly tilted)
        area = abs(ny) / 2
        h = round((y1 + y2 + y3) / 3 - surface, 1)
        if h >= FLOOR_MIN:
            by_height[h] = by_height.get(h, 0) + area
    out = []
    for h in sorted(by_height):
        if by_height[h] < FLOOR_AREA or h > top + 0.05:
            continue
        if out and h - out[-1] < 0.5:   # the same floor's thickness or trim
            continue
        out.append(h)
    return out


def deployment_zones(deployment):
    zones = DEPLOYMENTS.get(deployment)
    return [{"side": side, "polygon": rounded(ccw(zone_polygon(*spec)))} for side, spec in zones.items()] if zones else []


def zone_polygon(kind, position, value):
    """A zone as LCT draws it (see DEPLOYMENTS), for position x or xz; the
    others are the same mirrored."""
    flip_x, flip_z = position.startswith("-"), position.endswith("-z") or position == "-z"
    along_z = position in ("z", "-z")
    deep, half = (HALF_Z, HALF_X) if along_z else (HALF_X, HALF_Z)   # from the zone's edge in, and along it
    if kind == "line":
        poly = [(deep - value, -half), (deep, -half), (deep, half), (deep - value, half)]
    elif kind == "stepped":
        first, second = (deep - value[0], deep - value[1])   # boundaries, first from +half to 0, then to -half
        poly = [(second, -half), (deep, -half), (deep, half), (first, half), (first, 0), (second, 0)]
    elif kind == "quarter":
        arc = [(value * math.cos(math.radians(a)), value * math.sin(math.radians(a))) for a in range(90, -1, -10)]
        poly = arc + [(HALF_X, 0), (HALF_X, HALF_Z), (0, HALF_Z)]
    elif kind == "triangle":
        poly = [(0, HALF_Z), (HALF_X, -HALF_Z), (HALF_X, HALF_Z)]
        flip_z = flip_x   # LCT turns the other player's triangle round rather than mirroring it
    else:
        raise ValueError(f"Unknown deployment zone type {kind}")
    if along_z:
        # LCT's z zones are its x zones with x and z swapped
        poly = [(z, x) for x, z in poly]
        flip_x, flip_z = False, position.startswith("-")
    return [(-x if flip_x else x, -z if flip_z else z) for x, z in poly]


def placed_objects(layout, meshes, problems=None):
    """[(LCT object, its mesh's vertices on the table, triangles, height span)]
    for every object with a mesh that isn't the table mat."""
    placed = []
    for o in layout["objects"]:
        src = mesh_source(o)
        if not src or "battlemaster_battlemat" in (o.get("Tags") or []):
            continue
        mesh = meshes.load(*src)
        if mesh is None:
            if problems is not None:
                problems.append(f"{o.get('Nickname') or o['Name']} {o['GUID']}: TTS hasn't downloaded {src[0]}")
            continue
        verts, tris = mesh
        world = place(verts, o["Transform"])
        xs, ys, zs = zip(*world)
        if max(max(xs) - min(xs), max(zs) - min(zs)) > MAT:
            continue
        placed.append((o, world, tris, max(ys) - min(ys)))
    return placed


def build(layout, meshes):
    """cache/lct/layouts/<id>.json -> (layout terrain dict, problems)."""
    problems = []
    placed = placed_objects(layout, meshes, problems)
    area_objs = [p for p in placed if p[3] < FLAT and not category(p[0])]
    surface = sorted(min(y for _, y, _ in p[1]) for p in area_objs)[len(area_objs) // 2] if area_objs else 0.96
    areas = []
    for n, (o, world, _, _) in enumerate(area_objs, 1):
        poly = simplify(hull([(x, z) for x, _, z in world]))
        areas.append({"id": f"A{n}", "polygon": rounded(poly), "features": [], "_obj": o})

    for o, world, tris, _ in placed:
        cat = category(o)
        if not cat:
            if not any(a["_obj"] is o for a in areas):
                problems.append(f"{o.get('Nickname') or o['Name']} {o['GUID']}: not flat and no category, left out")
            continue
        poly = simplify(hull([(x, z) for x, _, z in world]))
        top = round(max(y for _, y, _ in world) - surface, 1)
        c = centroid(poly)
        home = next((a for a in areas if inside(c, a["polygon"])), None)
        if home is None:   # a feature on its own is its own area
            home = {"id": f"A{len(areas) + 1}", "polygon": rounded(poly), "features": [], "_obj": None}
            areas.append(home)
            problems.append(f"{o.get('Nickname') or cat} {o['GUID']}: not in a terrain area, given its own")
        home["features"].append({"id": f"{home['id']}{chr(ord('a') + len(home['features']))}",
                                 "name": o.get("Nickname") or " + ".join(o.get("Tags") or []) or cat.title(),
                                 "category": cat, "polygon": rounded(poly), "height": top,
                                 "floors": floors(world, tris, surface, top)})

    objectives = []
    for a in areas:
        tags = [t for t in (a["_obj"] or {}).get("Tags") or [] if t in OBJECTIVE_TAGS]
        if tags:
            kind, side = OBJECTIVE_TAGS[tags[0]]
            x, z = centroid(a["polygon"])
            objectives.append({"id": None, "kind": kind, "side": side, "x": round(x, 2), "z": round(z, 2),
                               "area": a["id"]})
    homes = {o["side"]: o for o in objectives if o["kind"] == "home"}
    for o in objectives:
        if o["kind"] == "expansion" and math.hypot(o["x"], o["z"]) < 3:
            o["kind"] = "central"   # one T5S2 layout tags its centre obj_neutral
        if o["kind"] == "expansion" and len(homes) == 2:   # the player whose home it's nearer
            o["side"] = min(homes, key=lambda s: math.dist((o["x"], o["z"]), (homes[s]["x"], homes[s]["z"])))
    for kind in ("home", "expansion", "central"):
        same = sorted((o for o in objectives if o["kind"] == kind), key=lambda o: (o["side"] or "", -o["z"], o["x"]))
        for i, o in enumerate(same, 1):
            o["id"] = "-".join(filter(None, [kind, o["side"], str(i) if len(same) > len({x["side"] for x in same}) else None]))

    for a in areas:
        del a["_obj"]
    return {
        "version": VERSION, "id": layout["guid"], "name": layout["name"], "map": layout["map"],
        "deployment": layout["deployment"], "pack": layout["pack"],
        "areas": areas, "objectives": objectives, "zones": deployment_zones(layout["deployment"]),
    }, problems


# --------------------------------------------------------------------------
# Every layout

def matchup_layouts(cache=None):
    """Each layout LCT offers for a matchup, once (both colour orders share a bag)."""
    index = mods.lct_index(cache)
    if not index:
        raise data.DataError("LCT isn't cached. Run `python3 data.py mods lct` first.")
    seen = {}
    for m in index["matchups"].values():
        for lo in m["layouts"]:
            seen.setdefault(lo["guid"], lo)
    return index, list(seen.values())


def build_all(cache=None, out=LAYOUTS, meshes=None, fetch=False, log=print):
    index, wanted = matchup_layouts(cache)
    meshes = meshes or Meshes(fetch, log)
    out.mkdir(exist_ok=True)
    source = {"from": "lct", "lct_updated": index["source"].get("updated")}
    listing, all_problems = [], {}
    for lo in wanted:
        layout = data.read_json(mods.lct_dir(cache) / lo["file"])
        terrain, problems = build(layout, meshes)
        terrain = {**{k: terrain[k] for k in ("version", "id", "name", "map", "deployment", "pack")},
                   "source": source, **{k: terrain[k] for k in ("areas", "objectives", "zones")}}
        (out / f"{lo['guid']}.json").write_text(dumps(terrain) + "\n")
        listing.append({"id": lo["guid"], "name": lo["name"], "map": lo["map"], "deployment": lo["deployment"],
                        "pack": lo["pack"], "areas": len(terrain["areas"]), "problems": len(problems)})
        if problems:
            all_problems[lo["guid"]] = problems
    listing.sort(key=lambda lo: (lo["pack"] or "", lo["map"], lo["deployment"] or ""))
    (out / "index.json").write_text(dumps({"source": source, "layouts": listing, "problems": all_problems}) + "\n")
    log(f"Wrote {len(listing)} layouts to {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}/")
    if all_problems:
        log(f"{len(all_problems)} have problems (listed in index.json), e.g. {next(iter(all_problems.values()))[0]}")
    return listing


def dumps(obj):
    """JSON with each polygon on one line, so a layout reads (and diffs) by feature."""
    text = json.dumps(obj, indent=1, ensure_ascii=False)
    text = re.sub(r"\[\s+(-?[\d.]+),\s+(-?[\d.]+)\s+\]", r"[\1, \2]", text)
    return re.sub(r"\[\s+(\[-?[\d.]+, -?[\d.]+\](?:,\s+\[-?[\d.]+, -?[\d.]+\])*)\s+\]",
                  lambda m: "[" + re.sub(r",\s+\[", ", [", m[1]) + "]", text)


# --------------------------------------------------------------------------
# Checking against the live table

READ_LUA = """
local out = {}
for _, o in ipairs(getObjects()) do
    local c = o.getCustomObject() or {}
    local url = c.mesh or c.assetbundle
    if url and url ~= "" then
        local p, r, b = o.getPosition(), o.getRotation(), o.getBounds()
        table.insert(out, {url = url, p = {p.x, p.y, p.z}, r = {r.x, r.y, r.z},
                           c = {b.center.x, b.center.z}, s = {b.size.x, b.size.z}})
    end
end
return out
"""


def angle_gap(a, b):
    return abs((a - b + 180) % 360 - 180)


def compare(layout, live, meshes):
    """Match a layout's objects to the live table's (same mesh, nearest spot)
    -> (matched, missing, worst position gap, worst rotation gap, worst bounds gap)."""
    matched, missing, worst = 0, 0, [0.0, 0.0, 0.0]
    for o, _, _, _ in placed_objects(layout, meshes):
        t, url = o["Transform"], mesh_source(o)[0]
        near = [lv for lv in live if lv["url"] == url and math.dist((lv["p"][0], lv["p"][2]), (t["posX"], t["posZ"])) < 1]
        if not near:
            missing += 1
            continue
        lv = min(near, key=lambda lv: math.dist((lv["p"][0], lv["p"][2]), (t["posX"], t["posZ"])))
        # TTS's bounds are the box around the mesh's own bounding box, turned: compare like with like
        verts = meshes.load(url, mesh_source(o)[1])[0]
        lo_hi = [(min(v[i] for v in verts), max(v[i] for v in verts)) for i in range(3)]
        corners = place([(x, y, z) for x in lo_hi[0] for y in lo_hi[1] for z in lo_hi[2]], t)
        xs, zs = [x for x, _, _ in corners], [z for _, _, z in corners]
        box = ((max(xs) + min(xs)) / 2, (max(zs) + min(zs)) / 2, max(xs) - min(xs), max(zs) - min(zs))
        worst[0] = max(worst[0], math.dist((lv["p"][0], lv["p"][2]), (t["posX"], t["posZ"])))
        worst[1] = max(worst[1], angle_gap(lv["r"][1], t["rotY"]))
        worst[2] = max(worst[2], *(abs(a - b) for a, b in zip(box, (*lv["c"], *lv["s"]))))
        matched += 1
    return matched, missing, *worst


def check(log=print):
    """Which built layout is on the table, and how closely build's geometry matches it."""
    import tts_bridge
    tts_bridge.start_listener()
    raw = tts_bridge.run_lua(READ_LUA, timeout=30)
    if raw is None:
        sys.exit("Couldn't read the table.")
    live = json.loads(raw) if isinstance(raw, str) else raw
    live = list(live.values()) if isinstance(live, dict) else live
    urls = {lv["url"] for lv in live}
    meshes = Meshes()
    best = None
    for lo in json.loads((LAYOUTS / "index.json").read_text())["layouts"]:
        layout = data.read_json(mods.lct_dir() / "layouts" / f"{lo['id']}.json")
        if not any(mesh_source(o) and mesh_source(o)[0] in urls for o in layout["objects"]):
            continue
        result = compare(layout, live, meshes)
        if best is None or result[0] > best[1][0]:
            best = (lo, result)
    if not best or not best[1][0]:
        sys.exit("No LCT layout on the table.")
    lo, (matched, missing, pos, rot, bounds) = best
    ok = not missing and pos <= 0.25 and rot <= 2 and bounds <= 0.25
    log(f"{lo['name']} ({lo['id']}): {matched} objects matched, {missing} not on the table")
    log(f"  worst gaps: position {pos:.2f}\", rotation {rot:.1f}°, bounds {bounds:.2f}\"  ->  {'OK' if ok else 'CHECK'}")
    return ok


def load(layout_id, folder=LAYOUTS):
    return json.loads((folder / f"{layout_id}.json").read_text())


def main(args):
    try:
        if args[:1] == ["build"]:
            build_all(fetch="--download" in args)
        elif args[:1] == ["check"]:
            check()
        else:
            print(__doc__)
    except data.DataError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main(sys.argv[1:])
