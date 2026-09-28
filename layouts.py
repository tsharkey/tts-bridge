"""
layouts.py — exact terrain for every LCT layout, in the layout terrain format
(docs/formats/layout-terrain.md): terrain areas and features as rotated
footprints, their categories and heights, objectives and deployment zones.

    python3 data.py mods lct       # first: LCT's layouts into cache/lct/
    python3 layouts.py build       # writes layouts/<id>.json for every matchup layout
    python3 layouts.py build --download   # also fetch meshes TTS hasn't downloaded, into cache/meshes/
    python3 layouts.py check       # compare the layout on the table in TTS with what build made
    python3 layouts.py list        # the layouts we have ("edited" when changed by hand)
    python3 layouts.py import      # build a new LCT version aside and show what would change
    python3 layouts.py import --apply [id ...]   # take the new and changed ones (or just these)
    python3 layouts.py delete <id> # remove one; import won't bring it back

The layouts tool in the web app (Layouts) does the same, and edits a layout's areas and
features over a 60" x 44" grid.

Everything comes from files TTS already has, so it runs offline with TTS
closed: each layout's objects from cache/lct/, and each object's mesh from
TTS's download cache (Mods/Models for OBJ files, Mods/Assetbundles for Unity
asset bundles, which need UnityPy: `pip install -r requirements-dev.txt`).
A mesh TTS hasn't downloaded yet is reported; build with --download (or load
that layout in TTS once) to fill the gap. The output is committed; LCT's own data isn't.

    import layouts
    layout = layouts.load("0c4960")   # one layout, by LCT's card GUID
"""

import functools
import json
import math
import re
import shutil
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


def edge_distance(point, poly):
    """How far a point is from a polygon's edge, inside or out."""
    x, z = point
    best = math.inf
    for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1]):
        dx, dz = x2 - x1, z2 - z1
        t = max(0.0, min(1.0, ((x - x1) * dx + (z - z1) * dz) / (dx * dx + dz * dz or 1)))
        best = min(best, math.hypot(x - x1 - t * dx, z - z1 - t * dz))
    return best


def distance(point, poly):
    """How far a point is from a polygon: 0 inside it."""
    return 0.0 if inside(point, poly) else edge_distance(point, poly)


def edges(poly):
    return zip(poly, poly[1:] + poly[:1])


def crossing(p1, p2, q1, q2):
    """Whether two segments cross (touching ends and overlapping collinear ones don't count)."""
    def side(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2, d3, d4 = side(q1, q2, p1), side(q1, q2, p2), side(p1, p2, q1), side(p1, p2, q2)
    return d1 * d2 < 0 and d3 * d4 < 0


def polygon_gap(a, b):
    """How far apart two polygons are: 0 when they overlap."""
    if any(inside(p, b) for p in a) or any(inside(p, a) for p in b) \
            or any(crossing(p1, p2, q1, q2) for p1, p2 in edges(a) for q1, q2 in edges(b)):
        return 0.0
    return min(min(edge_distance(p, b) for p in a), min(edge_distance(p, a) for p in b))


def polygon_within(a, b):
    """Whether polygon a is wholly inside polygon b (which needn't be convex)."""
    return all(inside(p, b) for p in a) and not any(
        crossing(p1, p2, q1, q2) for p1, p2 in edges(a) for q1, q2 in edges(b))


def bounds(poly):
    """(centre x, centre z, width, depth) of the box around a polygon."""
    xs, zs = [x for x, _ in poly], [z for _, z in poly]
    return (max(xs) + min(xs)) / 2, (max(zs) + min(zs)) / 2, max(xs) - min(xs), max(zs) - min(zs)


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


def place(vertices, *transforms):
    """Mesh vertices (as their files store them) -> table coordinates for an
    object with TTS Transform t. TTS mirrors x on import, like Unity. For a
    child object, pass its transform then its parents', innermost first."""
    out = [(-x, y, z) for x, y, z in vertices]
    for t in transforms:
        out = [rotate_euler((x * t["scaleX"], y * t["scaleY"], z * t["scaleZ"]), t["rotX"], t["rotY"], t["rotZ"])
               for x, y, z in out]
        out = [(x + t["posX"], y + t["posY"], z + t["posZ"]) for x, y, z in out]
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
        parts = [c.component if hasattr(c, "component") else c[-1] for c in go.m_Component]   # older bundles: (id, part)
        tr = next(c.read() for c in parts if c.type.name == "Transform")
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


def object_mesh(o, meshes, parents=(), missing=None):
    """An object's mesh and its child objects' (a piece made of several parts),
    on the table -> (vertices, triangles), or None if its own mesh is missing."""
    src = mesh_source(o)
    mesh = meshes.load(*src) if src else ([], [])
    if mesh is None:
        if missing is not None:
            missing.append(src[0])
        return None
    transforms = (o["Transform"], *parents)
    world, tris = place(mesh[0], *transforms), list(mesh[1])
    for child in o.get("ChildObjects") or []:
        part = object_mesh(child, meshes, transforms, missing)
        if part:
            tris += [(a + len(world), b + len(world), c + len(world)) for a, b, c in part[1]]
            world += part[0]
    return world, tris


def placed_objects(layout, meshes, problems=None):
    """[(LCT object, its mesh's vertices on the table, triangles, height span)]
    for every object with a mesh that isn't the table mat."""
    placed = []
    for o in layout["objects"]:
        src = mesh_source(o)
        if not src or "battlemaster_battlemat" in (o.get("Tags") or []):
            continue
        missing = []
        mesh = object_mesh(o, meshes, missing=missing)
        if missing and problems is not None:
            problems += [f"{o.get('Nickname') or o['Name']} {o['GUID']}: TTS hasn't downloaded {url}" for url in missing]
        if mesh is None:
            continue
        world, tris = mesh
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
    """Build every layout LCT offers into `out`, except ones saved by hand there ("edited",
    which are kept as they are) and ones retired there (which aren't brought back)."""
    index, wanted = matchup_layouts(cache)
    meshes = meshes or Meshes(fetch, log)
    out.mkdir(exist_ok=True)
    before = read_index(out)
    retired = {r["id"] for r in before.get("retired", [])}
    source = {"from": "lct", "lct_updated": index["source"].get("updated")}
    listing, all_problems, kept = [], {}, []
    for lo in wanted:
        if lo["guid"] in retired:
            continue
        path = out / f"{lo['guid']}.json"
        old = json.loads(path.read_text()) if path.exists() else None
        if old and old.get("source", {}).get("edited"):
            listing.append(entry(old))
            kept.append(lo["guid"])
            continue
        layout = data.read_json(mods.lct_dir(cache) / lo["file"])
        terrain, problems = build(layout, meshes)
        terrain = {**{k: terrain[k] for k in ("version", "id", "name", "map", "deployment", "pack")},
                   "source": source, **{k: terrain[k] for k in ("areas", "objectives", "zones")}}
        path.write_text(dumps(terrain) + "\n")
        listing.append({"id": lo["guid"], "name": lo["name"], "map": lo["map"], "deployment": lo["deployment"],
                        "pack": lo["pack"], "areas": len(terrain["areas"]), "problems": len(problems)})
        if problems:
            all_problems[lo["guid"]] = problems
    listing.sort(key=lambda lo: (lo["pack"] or "", lo["map"], lo["deployment"] or ""))
    written = {"source": source, "layouts": listing, "problems": all_problems}
    if before.get("retired"):
        written["retired"] = before["retired"]
    (out / "index.json").write_text(dumps(written) + "\n")
    load_all.cache_clear()
    log(f"Wrote {len(listing) - len(kept)} layouts to {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}/"
        + (f"; kept {len(kept)} edited by hand" if kept else ""))
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


def box_corners(o, meshes, parents=()):
    """TTS's bounds are the box around each part's own bounding box, turned:
    the corners of those boxes, on the table, to compare like with like."""
    transforms = (o["Transform"], *parents)
    src = mesh_source(o)
    verts = (meshes.load(*src) or ([], []))[0] if src else []
    out = []
    if verts:
        lo_hi = [(min(v[i] for v in verts), max(v[i] for v in verts)) for i in range(3)]
        out = place([(x, y, z) for x in lo_hi[0] for y in lo_hi[1] for z in lo_hi[2]], *transforms)
    for child in o.get("ChildObjects") or []:
        out += box_corners(child, meshes, transforms)
    return out


def compare_objects(layout, live, meshes):
    """Match an LCT layout's objects (cache/lct/) to the live table's (same mesh, nearest spot):
    one row per object, {"name", "x", "z", "found", "position", "rotation", "bounds"} (the gaps,
    in inches and degrees; None when it isn't on the table) and "ok" (within TOLERANCE)."""
    rows = []
    for o, _, _, _ in placed_objects(layout, meshes):
        t, url = o["Transform"], mesh_source(o)[0]
        row = {"name": o.get("Nickname") or " + ".join(o.get("Tags") or []) or "piece",
               "x": round(t["posX"], 2), "z": round(t["posZ"], 2), "found": False,
               "position": None, "rotation": None, "bounds": None, "ok": False}
        near = [lv for lv in live if lv["url"] == url and math.dist((lv["p"][0], lv["p"][2]), (t["posX"], t["posZ"])) < 1]
        if near:
            lv = min(near, key=lambda lv: math.dist((lv["p"][0], lv["p"][2]), (t["posX"], t["posZ"])))
            corners = box_corners(o, meshes)
            xs, zs = [x for x, _, _ in corners], [z for _, _, z in corners]
            box = ((max(xs) + min(xs)) / 2, (max(zs) + min(zs)) / 2, max(xs) - min(xs), max(zs) - min(zs))
            row.update(found=True, position=round(math.dist((lv["p"][0], lv["p"][2]), (t["posX"], t["posZ"])), 3),
                       rotation=round(angle_gap(lv["r"][1], t["rotY"]), 2),
                       bounds=round(max(abs(a - b) for a, b in zip(box, (*lv["c"], *lv["s"]))), 3))
            row["ok"] = (row["position"] <= TOLERANCE[0] and row["rotation"] <= TOLERANCE[1]
                         and row["bounds"] <= TOLERANCE[0])
        rows.append(row)
    return rows


TOLERANCE = (0.25, 2.0)   # inches, degrees: how far the table may be from a layout's geometry


def compare(layout, live, meshes):
    """-> (matched, missing, worst position gap, worst rotation gap, worst bounds gap)."""
    rows = compare_objects(layout, live, meshes)
    found = [r for r in rows if r["found"]]
    return (len(found), len(rows) - len(found), *(max((r[k] for r in found), default=0.0)
                                                  for k in ("position", "rotation", "bounds")))


def read_live():
    """What compare_objects needs of every custom object on the table (TTS must be running)."""
    import tts_bridge
    raw = tts_bridge.run_lua(READ_LUA, timeout=30)
    if raw is None:
        sys.exit("Couldn't read the table.")
    live = json.loads(raw) if isinstance(raw, str) else raw
    return list(live.values()) if isinstance(live, dict) else live


def check_layout(layout_id, cache=None, log=print):
    """compare_objects for one of layouts/ against the live table."""
    lct = data.read_json(mods.lct_dir(cache) / "layouts" / f"{layout_id}.json")
    if not lct:
        raise data.DataError(f"LCT's objects for {layout_id} aren't cached. Run `python3 data.py mods lct`.")
    return compare_objects(lct, read_live(), Meshes(log=log))


def check(log=print):
    """Which built layout is on the table, and how closely build's geometry matches it."""
    import tts_bridge
    tts_bridge.start_listener()
    live = read_live()
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
    ok = not missing and pos <= TOLERANCE[0] and rot <= TOLERANCE[1] and bounds <= TOLERANCE[0]
    log(f"{lo['name']} ({lo['id']}): {matched} objects matched, {missing} not on the table")
    log(f"  worst gaps: position {pos:.2f}\", rotation {rot:.1f}°, bounds {bounds:.2f}\"  ->  {'OK' if ok else 'CHECK'}")
    return ok


def load(layout_id, folder=None):
    folder = folder or LAYOUTS
    return json.loads((folder / f"{layout_id}.json").read_text())


@functools.cache
def load_all(folder=LAYOUTS):
    return [json.loads(f.read_text()) for f in sorted(folder.glob("*.json")) if f.name != "index.json"]


def identify(terrain, candidates=None, near=1.0, enough=0.7, meshes=None, lct=None):
    """Which layout is on the table, from board.py's terrain (TTS's bounds): the one with the
    most areas and features that have a piece of terrain centred within `near` inches of their
    own box's centre. A map comes in several terrain packs, with the same spots and different
    pieces; to tell them apart, `meshes` (the mesh and asset bundle URLs on the table,
    board.table_meshes) picks the pack whose objects in LCT's cache (`lct`, default
    cache/lct/) are there; without them, the pack closest in size. `candidates` defaults to
    every layout in layouts/.
    -> (layout, matched, total), or None when no layout has `enough` of its pieces there."""
    pieces = [(t["x"], t["z"], t["w"], t["d"]) for t in terrain if t["kind"] == "terrain"]
    scored = []
    for layout in load_all() if candidates is None else candidates:
        boxes = [bounds(p) for a in layout["areas"] for p in [a["polygon"], *(f["polygon"] for f in a["features"])]]
        matched, misfit = 0, 0.0
        for x, z, w, d in boxes:
            gaps = [(math.dist((x, z), (px, pz)), pw, pd) for px, pz, pw, pd in pieces]
            gap, pw, pd = min(gaps, default=(math.inf, 0, 0))
            if gap <= near:
                matched += 1
                misfit += gap + abs(w - pw) + abs(d - pd)
        if boxes and matched / len(boxes) >= enough:
            scored.append(((matched / len(boxes), -misfit / max(matched, 1)), layout, matched, len(boxes)))
    if not scored:
        return None
    key, best, matched, total = max(scored, key=lambda s: s[0])
    if meshes:
        packs = [s for s in scored if (s[1]["map"], s[1]["deployment"]) == (best["map"], best["deployment"])]
        if len(packs) > 1:
            on_table = {s[1]["id"]: pack_meshes_found(s[1]["id"], meshes, lct) for s in packs}
            if any(on_table.values()):
                key, best, matched, total = max(packs, key=lambda s: (on_table[s[1]["id"]], s[0]))
    return best, matched, total


def pack_meshes_found(layout_id, meshes, lct=None):
    """How much of a layout's own pieces (their mesh or asset bundle, from LCT's cache) is on the
    table, 0..1; 0 when the layout isn't cached."""
    cached = data.read_json((lct or mods.lct_dir()) / "layouts" / f"{layout_id}.json")
    urls = [mesh_source(o)[0] for o in (cached or {}).get("objects", []) if mesh_source(o)]
    return sum(u in meshes for u in urls) / len(urls) if urls else 0.0


# --------------------------------------------------------------------------
# Managing layouts/ (the layouts tool, app/tools/layouts/, and `layouts.py list|import|delete`).
# A layout saved by hand is marked "edited" in its source, and import leaves it alone unless
# asked; a deleted one is listed as retired in index.json, so import doesn't bring it back.

STAGING = data.CACHE / "layouts-import"


def read_index(folder=None):
    folder = folder or LAYOUTS
    return json.loads((folder / "index.json").read_text()) if (folder / "index.json").exists() else \
        {"source": None, "layouts": [], "problems": {}}


def write_index(index, folder=None):
    folder = folder or LAYOUTS
    index["layouts"].sort(key=lambda lo: (lo["pack"] or "", lo["map"], lo["deployment"] or ""))
    (folder / "index.json").write_text(dumps(index) + "\n")
    load_all.cache_clear()


def entry(layout):
    return {"id": layout["id"], "name": layout["name"], "map": layout["map"], "deployment": layout["deployment"],
            "pack": layout["pack"], "areas": len(layout["areas"]), "problems": 0,
            **({"edited": layout["source"]["edited"]} if layout.get("source", {}).get("edited") else {})}


def polygon_problems(poly, what):
    out = []
    if len(poly) < 3 or poly[0] == poly[-1]:
        out.append(f"{what}: needs 3 or more corners, without repeating the first")
    elif signed_area(poly) <= 0:
        out.append(f"{what}: corners should run counter-clockwise seen from above (or it crosses itself)")
    if any(abs(x) > HALF_X or abs(z) > HALF_Z for x, z in poly):
        out.append(f"{what}: goes off the table")
    edges = list(zip(poly, poly[1:] + poly[:1]))
    for i, (a, b) in enumerate(edges):
        for c, d in edges[i + 2:]:
            if c != b and d != a and _crosses(a, b, c, d):
                out.append(f"{what}: its edges cross")
                return out
    return out


def _crosses(p1, p2, q1, q2):
    def side(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return side(q1, q2, p1) * side(q1, q2, p2) < 0 and side(p1, p2, q1) * side(p1, p2, q2) < 0


def problems(layout):
    """What's wrong with a layout against the format (docs/formats/layout-terrain.md), in words."""
    out = []
    ids = [a["id"] for a in layout["areas"]]
    if not ids:
        out.append("no terrain areas")
    if len(ids) != len(set(ids)):
        out.append("two areas share an id")
    for a in layout["areas"]:
        out += polygon_problems(a["polygon"], f"area {a['id']}")
        for f in a["features"]:
            what = f"feature {f['id']} ({f.get('name')})"
            if f.get("category") not in ("dense", "light", "exposed"):
                out.append(f"{what}: category is dense, light or exposed")
            out += polygon_problems(f["polygon"], what)
            if not isinstance(f.get("height"), (int, float)) or f["height"] <= 0:
                out.append(f"{what}: height must be above 0")
            elif any(not 0 < h <= f["height"] for h in f.get("floors") or []):
                out.append(f"{what}: floors must be above 0 and no higher than the feature")
    kinds = [o["kind"] for o in layout["objectives"]]
    if kinds.count("home") != 2 or "central" not in kinds:
        out.append("objectives: needs a home for each side and a central one")
    for o in layout["objectives"]:
        if o["area"] is not None and o["area"] not in ids:
            out.append(f"objective {o['id']}: its area {o['area']} isn't in the layout")
    if sorted(z["side"] for z in layout["zones"]) != ["blue", "red"]:
        out.append("zones: needs a red and a blue deployment zone")
    for z in layout["zones"]:
        out += polygon_problems(z["polygon"], f"{z['side']} zone")
    return out


def save(layout, folder=None, edited=True):
    """Write one layout (checked first) and its index entry. edited: mark it as changed by
    hand, so import leaves it alone. -> the layout as written."""
    folder = folder or LAYOUTS
    found = problems(layout)
    if found:
        raise ValueError("Not saved: " + "; ".join(found))
    path = folder / f"{layout['id']}.json"
    layout = same_numbers({**layout, "version": VERSION}, json.loads(path.read_text()) if path.exists() else None)
    if edited:
        layout["source"] = {**(layout.get("source") or {}), "edited": data.now()}
    (folder / f"{layout['id']}.json").write_text(dumps(layout) + "\n")
    index = read_index(folder)
    index["layouts"] = [lo for lo in index["layouts"] if lo["id"] != layout["id"]] + [entry(layout)]
    index.get("problems", {}).pop(layout["id"], None)
    write_index(index, folder)
    return layout


def same_numbers(new, old):
    """A layout that came back through JSON from a browser, with its numbers as the file had
    them: 4 is 4.0 again, and a value that didn't change keeps its old spelling (-0.0), so a
    saved edit only shows what was edited in a diff."""
    if isinstance(new, bool) or new is None:
        return new
    if isinstance(new, (int, float)):
        if isinstance(old, (int, float)) and not isinstance(old, bool) and old == new:
            return old
        return new if isinstance(new, float) else float(new)
    if isinstance(new, dict):
        return {k: same_numbers(v, old.get(k) if isinstance(old, dict) else None) for k, v in new.items()}
    if isinstance(new, list):
        return [same_numbers(v, old[i] if isinstance(old, list) and i < len(old) else None) for i, v in enumerate(new)]
    return new


def delete(layout_id, folder=None):
    """Remove a layout, and list it as retired so import doesn't bring it back."""
    folder = folder or LAYOUTS
    path = folder / f"{layout_id}.json"
    if not path.exists():
        raise ValueError(f"No layout {layout_id}.")
    gone = json.loads(path.read_text())
    path.unlink()
    index = read_index(folder)
    index["layouts"] = [lo for lo in index["layouts"] if lo["id"] != layout_id]
    index.get("problems", {}).pop(layout_id, None)
    index["retired"] = [r for r in index.get("retired", []) if r["id"] != layout_id] + \
        [{"id": layout_id, "name": gone["name"], "retired": data.now()}]
    write_index(index, folder)


def differences(old, new, tolerance=TOLERANCE[0]):
    """How two versions of a layout differ, in words: areas, features, objectives and zones
    added, removed or changed (a corner moved more than `tolerance` inches)."""
    out = []

    def moved(a, b):
        return len(a) != len(b) or any(math.dist(p, q) > tolerance for p, q in zip(a, b))

    old_areas, new_areas = {a["id"]: a for a in old["areas"]}, {a["id"]: a for a in new["areas"]}
    out += [f"area {i} added" for i in new_areas.keys() - old_areas.keys()]
    out += [f"area {i} removed" for i in old_areas.keys() - new_areas.keys()]
    for i in sorted(old_areas.keys() & new_areas.keys()):
        a, b = old_areas[i], new_areas[i]
        if moved(a["polygon"], b["polygon"]):
            out.append(f"area {i} reshaped")
        fa, fb = {f["id"]: f for f in a["features"]}, {f["id"]: f for f in b["features"]}
        out += [f"feature {f} added ({fb[f]['name']})" for f in sorted(fb.keys() - fa.keys())]
        out += [f"feature {f} removed ({fa[f]['name']})" for f in sorted(fa.keys() - fb.keys())]
        for f in sorted(fa.keys() & fb.keys()):
            x, y = fa[f], fb[f]
            for k in ("name", "category", "height", "floors"):
                if x.get(k) != y.get(k):
                    out.append(f"feature {f} {k}: {x.get(k)} -> {y.get(k)}")
            if moved(x["polygon"], y["polygon"]):
                out.append(f"feature {f} reshaped")
    if [(o["id"], o["area"]) for o in old["objectives"]] != [(o["id"], o["area"]) for o in new["objectives"]]:
        out.append("objectives changed")
    if any(moved(a["polygon"], b["polygon"]) for a, b in zip(old["zones"], new["zones"])):
        out.append("deployment zones changed")
    for k in ("name", "map", "deployment", "pack"):
        if old.get(k) != new.get(k):
            out.append(f"{k}: {old.get(k)} -> {new.get(k)}")
    return out


def import_preview(cache=None, folder=None, staging=None, meshes=None, log=print):
    """Build every layout LCT offers into a staging folder (not layouts/), and say what would
    change: {"added", "changed" (with their differences; "edited" when changed by hand here),
    "removed" (ours, no longer in LCT), "unchanged" (a count), "retired" (skipped), "source"}."""
    folder = folder or LAYOUTS
    staging = staging or STAGING
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    build_all(cache, out=staging, meshes=meshes, log=log)
    ours, theirs = read_index(folder), read_index(staging)
    retired = {r["id"] for r in ours.get("retired", [])}
    have = {lo["id"] for lo in ours["layouts"]}
    out = {"added": [], "changed": [], "removed": [], "unchanged": 0, "retired": [], "source": theirs["source"]}
    for lo in theirs["layouts"]:
        if lo["id"] in retired:
            out["retired"].append({"id": lo["id"], "name": lo["name"]})
        elif lo["id"] not in have:
            out["added"].append({"id": lo["id"], "name": lo["name"]})
        else:
            old, new = load(lo["id"], folder), load(lo["id"], staging)
            found = differences(old, new)
            if found:
                out["changed"].append({"id": lo["id"], "name": lo["name"], "differences": found,
                                       "edited": bool(old.get("source", {}).get("edited"))})
            else:
                out["unchanged"] += 1
    offered = {lo["id"] for lo in theirs["layouts"]}
    out["removed"] = [{"id": lo["id"], "name": lo["name"]} for lo in ours["layouts"] if lo["id"] not in offered]
    return out


def import_apply(ids, folder=None, staging=None):
    """Take these layouts from the last import_preview's staging folder into layouts/ (added or
    changed ones), or remove them (ones LCT no longer has). -> {"written", "removed"}."""
    folder = folder or LAYOUTS
    staging = staging or STAGING
    theirs = read_index(staging)
    if not theirs["layouts"]:
        raise ValueError("Nothing staged. Preview the import first.")
    offered = {lo["id"]: lo for lo in theirs["layouts"]}
    index, written, removed = read_index(folder), [], []
    for i in ids:
        if i in offered:
            shutil.copyfile(staging / f"{i}.json", folder / f"{i}.json")
            index["layouts"] = [lo for lo in index["layouts"] if lo["id"] != i] + [offered[i]]
            if i in theirs.get("problems", {}):
                index.setdefault("problems", {})[i] = theirs["problems"][i]
            else:
                index.get("problems", {}).pop(i, None)
            written.append(i)
        elif (folder / f"{i}.json").exists():
            (folder / f"{i}.json").unlink()
            index["layouts"] = [lo for lo in index["layouts"] if lo["id"] != i]
            removed.append(i)
    if written:
        index["source"] = theirs["source"]
    write_index(index, folder)
    return {"written": written, "removed": removed}


def print_preview(found, log=print):
    log(f"From LCT updated {(found['source'] or {}).get('lct_updated')}: {len(found['added'])} new, "
        f"{len(found['changed'])} changed, {len(found['removed'])} gone from LCT, {found['unchanged']} unchanged"
        + (f", {len(found['retired'])} retired here (skipped)" if found["retired"] else ""))
    for lo in found["added"]:
        log(f"  + {lo['id']}  {lo['name']}")
    for lo in found["changed"]:
        log(f"  ~ {lo['id']}  {lo['name']}" + ("  (edited here: kept unless named)" if lo["edited"] else ""))
        for d in lo["differences"][:6]:
            log(f"      {d}")
    for lo in found["removed"]:
        log(f"  - {lo['id']}  {lo['name']}  (not in LCT any more: kept unless named)")


def main(args):
    try:
        if args[:1] == ["build"]:
            build_all(fetch="--download" in args)
        elif args[:1] == ["check"]:
            check()
        elif args[:1] == ["list"]:
            index = read_index()
            for lo in index["layouts"]:
                print(f"{lo['id']}  {lo['name']}" + ("  (edited)" if lo.get("edited") else ""))
            print(f"{len(index['layouts'])} layouts" + (f", {len(index.get('retired', []))} retired"
                                                         if index.get("retired") else ""))
        elif args[:1] == ["import"]:
            found = import_preview(log=lambda *a: None)
            print_preview(found)
            named = [a for a in args[1:] if not a.startswith("--")]
            if "--apply" in args:
                # new and changed ones LCT has, except those edited here; those gone from LCT only when named
                ids = named or [lo["id"] for lo in found["added"]] + \
                    [lo["id"] for lo in found["changed"] if not lo["edited"]]
                done = import_apply(ids)
                print(f"Wrote {len(done['written'])}, removed {len(done['removed'])}.")
            elif found["added"] or found["changed"] or found["removed"]:
                print("Nothing written. `python3 layouts.py import --apply` takes the new and changed ones "
                      "(or name ids after --apply).")
        elif args[:1] == ["delete"] and len(args) == 2:
            delete(args[1])
            print(f"Deleted {args[1]}; import won't bring it back.")
        else:
            print(__doc__)
    except (data.DataError, ValueError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main(sys.argv[1:])
