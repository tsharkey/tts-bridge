"""Layout terrain from LCT's data (layouts.py, issue #20): placing meshes the
way TTS does, deployment zones, building one layout from made-up objects and
meshes, and the committed layouts/ files against the format."""

import json
import math
from pathlib import Path

import pytest

import board
import layouts

ROOT = Path(__file__).resolve().parent.parent


def near(a, b, tol=0.01):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_place_mirrors_x_and_turns_clockwise():
    """TTS mirrors a mesh file's x, and rotY turns clockwise seen from above
    (90 takes +z to +x), like facing."""
    t = {"posX": 5, "posY": 1, "posZ": 5, "rotX": 0, "rotY": 90, "rotZ": 0, "scaleX": 1, "scaleY": 1, "scaleZ": 1}
    assert near(layouts.place([(1, 0, 0)], t)[0], (5, 1, 6))    # file +x -> table -x, turned 90° -> +z
    assert near(layouts.place([(0, 0, 1)], t)[0], (6, 1, 5))    # +z turned 90° -> +x
    flipped = {**t, "rotY": 0, "rotZ": 180, "scaleX": 2}
    assert near(layouts.place([(1, 1, 0)], flipped)[0], (7, 0, 5))   # upside down: x and y flip back


@pytest.mark.parametrize("deployment", sorted(layouts.DEPLOYMENTS))
def test_zones_are_mirror_images(deployment):
    zones = {z["side"]: z["polygon"] for z in layouts.deployment_zones(deployment)}
    for poly in zones.values():
        assert layouts.signed_area(poly) > 0
        assert all(abs(x) <= 30 and abs(z) <= 22 for x, z in poly)
    mirrored = sorted((-x + 0.0, -z + 0.0) for x, z in zones["red"])
    assert near([c for p in mirrored for c in p], [c for p in sorted(map(tuple, zones["blue"])) for c in p])
    assert all(layouts.inside((-x, -z), zones["red"]) is False for x, z in [(25, 20)])


def test_zone_shapes():
    area = {d: {z["side"]: layouts.signed_area(z["polygon"]) for z in layouts.deployment_zones(d)}
            for d in layouts.DEPLOYMENTS}
    assert area["Dawn of War"]["red"] == 60 * 12
    assert area["Hammer and Anvil"]["red"] == 44 * 18
    assert area["Tipping Point"]["red"] == 22 * 20 + 22 * 12
    assert area["Sweeping Engagement"]["red"] == 30 * 14 + 30 * 8
    assert area["Crucible of Battle"]["red"] == 30 * 44 / 2
    assert area["Search and Destroy"]["red"] == pytest.approx(30 * 22 - math.pi * 81 / 4, abs=1)
    red = {d: [z for z in layouts.deployment_zones(d) if z["side"] == "red"][0]["polygon"] for d in layouts.DEPLOYMENTS}
    assert layouts.inside((25, 0), red["Hammer and Anvil"]) and layouts.inside((0, 20), red["Dawn of War"])
    assert layouts.inside((15, 5), red["Tipping Point"]) and not layouts.inside((15, -5), red["Tipping Point"])
    assert layouts.inside((20, 20), red["Search and Destroy"]) and not layouts.inside((5, 5), red["Search and Destroy"])
    assert layouts.inside((25, 15), red["Crucible of Battle"]) and not layouts.inside((5, -15), red["Crucible of Battle"])


# --------------------------------------------------------------------------
# One made-up layout

def box(w, d, h, floor=None):
    """A w × d × h mesh sitting on y = 0: a base and two walls, open on top (a
    roof would be a floor too), optionally with a floor at `floor`."""
    xs, zs = (-w / 2, w / 2), (-d / 2, d / 2)
    verts = [(x, y, z) for y in (0, h) for x in xs for z in zs]
    tris = [(0, 1, 3), (0, 3, 2),                               # base
            (0, 1, 5), (0, 5, 4), (2, 3, 7), (2, 7, 6)]         # two walls
    if floor is not None:
        n = len(verts)
        verts += [(x, floor, z) for x in xs for z in zs]
        tris += [(n, n + 1, n + 3), (n, n + 3, n + 2)]
    return verts, tris


MESHES = {"mat": box(6, 4, 0.04), "big mat": box(60, 44, 0.5), "ruin": box(4, 2, 5, floor=3), "wall": box(3, 0.5, 2)}


class FakeMeshes:
    def load(self, url, kind):
        return MESHES.get(url)


def thing(guid, mesh, x, z, rot=0, tags=(), description="", nickname=""):
    return {"GUID": guid, "Name": "Custom_Model", "Nickname": nickname, "Description": description, "Tags": list(tags),
            "CustomMesh": {"MeshURL": mesh},
            "Transform": {"posX": x, "posY": 0.96, "posZ": z, "rotX": 0, "rotY": rot, "rotZ": 0,
                          "scaleX": 1, "scaleY": 1, "scaleZ": 1}}


LAYOUT = {"guid": "abc123", "name": "Test 1 - Dawn of War - Test", "map": "Test 1", "deployment": "Dawn of War",
          "pack": "Test", "objects": [
              thing("m00000", "big mat", 0, 0, tags=["battlemaster_battlemat"]),
              thing("a00001", "mat", 10, 15, tags=["obj_home_red"]),
              thing("a00002", "mat", -10, -15, tags=["obj_home_blue"]),
              thing("a00003", "mat", 12, 5, rot=90, tags=["obj_neutral"]),
              thing("a00004", "mat", 0, 0.5, tags=["obj_center1"]),
              thing("r00001", "ruin", 10, 15, description="Dense", nickname="Ruin"),
              thing("w00001", "wall", 20, -10, description="Light", nickname="Wall"),   # in no area
              thing("x00001", "not downloaded", 0, 10, description="Dense"),
          ]}


def test_build_one_layout():
    terrain, problems = layouts.build(LAYOUT, FakeMeshes())
    assert len(problems) == 2
    assert "hasn't downloaded" in problems[0] and "not in a terrain area" in problems[1]

    areas = {a["id"]: a for a in terrain["areas"]}
    assert len(areas) == 5   # four mats, and the wall's own
    home = areas["A1"]
    assert sorted(home["polygon"]) == sorted([[7, 13], [13, 13], [13, 17], [7, 17]])
    [ruin] = home["features"]
    assert ruin == {"id": "A1a", "name": "Ruin", "category": "dense", "polygon": ruin["polygon"],
                    "height": 5.0, "floors": [3.0]}
    assert sorted(ruin["polygon"]) == sorted([[8, 14], [12, 14], [12, 16], [8, 16]])
    turned = areas["A3"]["polygon"]   # 6 × 4 turned 90°: 4 wide, 6 deep
    assert max(x for x, _ in turned) - min(x for x, _ in turned) == pytest.approx(4)

    objectives = {o["id"]: o for o in terrain["objectives"]}
    assert set(objectives) == {"home-red", "home-blue", "expansion-red", "central"}
    assert objectives["expansion-red"]["area"] == "A3"
    assert (objectives["home-red"]["x"], objectives["home-red"]["z"]) == (10, 15)
    assert [z["side"] for z in terrain["zones"]] == ["red", "blue"]


def test_a_piece_in_parts_is_one_feature():
    """LCT builds some pieces from several meshes (TTS child objects); their
    transforms are relative to the parent's."""
    ruin = thing("r00002", "wall", 10, 15, rot=90, description="Dense", nickname="Ruin")
    part = thing("p00001", "ruin", 0, 3)          # 3" along the parent's z, before the parent turns
    part["Transform"]["posY"] = 0
    ruin["ChildObjects"] = [part]
    layout = {**LAYOUT, "objects": [LAYOUT["objects"][1], ruin]}
    terrain, problems = layouts.build(layout, FakeMeshes())
    assert not problems
    [feature] = terrain["areas"][0]["features"]
    xs = [x for x, _ in feature["polygon"]]
    assert max(xs) == pytest.approx(14)          # the part sits at x 12..14 once turned 90°
    assert feature["floors"] == [3.0] and feature["height"] == 5.0


def test_categories():
    assert layouts.category({"Description": "Tower = Dense\nWalls = Light"}) == "dense"
    assert layouts.category({"Nickname": "Light terrain"}) == "light"
    assert layouts.category({"Description": "This is Heavy terrain."}) == "dense"
    assert layouts.category({"Description": ""}) is None


# --------------------------------------------------------------------------
# The committed files

FILES = sorted(p for p in (ROOT / "layouts").glob("*.json") if p.name != "index.json")


def polygon_ok(poly):
    assert len(poly) >= 3 and poly[0] != poly[-1]
    assert all(abs(x) <= 30 and abs(z) <= 22 for x, z in poly)
    assert layouts.signed_area(poly) > 0, "corners should run counter-clockwise"


def test_every_layout_is_listed():
    index = json.loads((ROOT / "layouts" / "index.json").read_text())
    assert {lo["id"] for lo in index["layouts"]} == {p.stem for p in FILES}
    assert len(FILES) > 200
    assert not index["problems"]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_layout_file(path):
    layout = json.loads(path.read_text())
    assert layout["version"] == layouts.VERSION and layout["id"] == path.stem
    ids = [a["id"] for a in layout["areas"]]
    assert len(ids) == len(set(ids)) and ids
    for a in layout["areas"]:
        polygon_ok(a["polygon"])
        for f in a["features"]:
            assert f["category"] in ("dense", "light", "exposed")
            polygon_ok(f["polygon"])
            assert layouts.inside(layouts.centroid(f["polygon"]), a["polygon"]) or len(a["features"]) == 1
            assert f["height"] > 0 and all(0 < h <= f["height"] for h in f["floors"])
    kinds = [o["kind"] for o in layout["objectives"]]
    assert kinds.count("home") == 2 and "central" in kinds
    for o in layout["objectives"]:
        assert o["area"] in ids
        assert o["side"] in (("red", "blue") if o["kind"] != "central" else (None,))
    assert sorted(z["side"] for z in layout["zones"]) == ["blue", "red"]
    for z in layout["zones"]:
        polygon_ok(z["polygon"])


def test_distance_to_a_polygon():
    square = [[0, 0], [4, 0], [4, 4], [0, 4]]
    assert layouts.distance((2, 2), square) == 0
    assert layouts.distance((7, 2), square) == 3
    assert math.isclose(layouts.distance((7, 8), square), 5)
    assert layouts.bounds(square) == (2, 2, 4, 4)


def pieces(layout):
    """A layout's areas and features as board.collect_terrain reads them from TTS."""
    return [{"kind": "terrain", **dict(zip("xzwd", layouts.bounds(p)))}
            for a in layout["areas"] for p in [a["polygon"], *(f["polygon"] for f in a["features"])]]


@pytest.mark.parametrize("layout_id", ["33ce09", "fe8650", "3edf8d", "7e2c7f", "0c4960"])
def test_identify_the_layout_on_the_table(layout_id):
    """Terrain packs of one map share every spot (the first four are PtF vs Recon 3); size tells them apart."""
    terrain = pieces(layouts.load(layout_id))
    layout, matched, total = layouts.identify(terrain)
    assert layout["id"] == layout_id and matched == total


def test_identify_nothing_on_the_table():
    assert layouts.identify([]) is None
    few = pieces(layouts.load("33ce09"))[:10]   # a layout half cleared away isn't that layout
    assert layouts.identify(few) is None
    zones = [{**p, "kind": "zone"} for p in pieces(layouts.load("33ce09"))]
    assert layouts.identify(zones) is None


def test_edge_distance_inside_and_out():
    square = [[0, 0], [4, 0], [4, 4], [0, 4]]
    assert layouts.edge_distance((1, 2), square) == 1
    assert layouts.edge_distance((6, 2), square) == 2


def test_polygon_gap_and_within():
    square = [[0, 0], [4, 0], [4, 4], [0, 4]]
    assert layouts.polygon_gap([[5, 1], [6, 1], [6, 2], [5, 2]], square) == 1
    assert layouts.polygon_gap([[3, 3], [6, 3], [6, 6], [3, 6]], square) == 0     # overlapping corners
    assert layouts.polygon_gap([[-1, 1], [5, 1], [5, 2], [-1, 2]], square) == 0   # straight across, no corner inside
    assert layouts.polygon_within([[1, 1], [2, 1], [2, 2], [1, 2]], square)
    ell = [[0, 0], [6, 0], [6, 2], [2, 2], [2, 6], [0, 6]]     # an L: the notch is x > 2, z > 2
    across = [[1, 1.5], [5, 1.5], [5, 3], [1, 3]]              # every corner but one in the L, and it crosses the notch
    assert not layouts.polygon_within(across, ell)
    assert layouts.polygon_within([[0.5, 0.5], [5, 0.5], [5, 1.5], [0.5, 1.5]], ell)


def test_the_table_meshes_pick_the_terrain_pack(tmp_path):
    """Two terrain packs of one map, every piece in the same spot (#105): pack A's footprints are
    closer in size to what TTS reports, but pack B's meshes are the ones on the table."""
    base = layouts.load("33ce09")
    pack_a = {**base, "id": "aaaaaa", "pack": "A"}
    pack_b = {**base, "id": "bbbbbb", "pack": "B"}
    other_map = {**base, "id": "cccccc", "map": "Somewhere else"}
    for pack, url in (("aaaaaa", "http://example.test/a.obj"), ("bbbbbb", "http://example.test/b.obj")):
        (tmp_path / "layouts").mkdir(exist_ok=True)
        (tmp_path / "layouts" / f"{pack}.json").write_text(json.dumps(
            {"guid": pack, "objects": [{"CustomMesh": {"MeshURL": url}}, {"CustomMesh": {"MeshURL": url + "2"}}]}))
    terrain = pieces(pack_a)
    candidates = [pack_a, pack_b, other_map]
    assert layouts.identify(terrain, candidates)[0]["id"] == "aaaaaa"          # by size: a tie, so the first
    on_table = {"http://example.test/b.obj", "http://example.test/b.obj2", "http://example.test/mat.obj"}
    assert layouts.identify(terrain, candidates, meshes=on_table, lct=tmp_path)[0]["id"] == "bbbbbb"
    neither = {"http://example.test/other.obj"}
    assert layouts.identify(terrain, candidates, meshes=neither, lct=tmp_path)[0]["id"] == "aaaaaa"
    assert layouts.pack_meshes_found("bbbbbb", {"http://example.test/b.obj"}, tmp_path) == 0.5
    assert layouts.pack_meshes_found("zzzzzz", on_table, tmp_path) == 0.0     # not cached


def test_board_reads_meshes_for_identify():
    """Only terrain on the table counts: not a pack parked beside it, loose pieces or models."""
    def piece(guid, x, mesh, locked=True):
        return {"guid": guid, "tag": "Custom_Model", "name": "Ruin", "head": "", "notes": "", "tags": [],
                "locked": locked, "rot": 0, "p": [x, 1, 0], "c": [x, 2, 0], "s": [4, 2, 3], "mesh": mesh}
    objs = [piece("a1", 0, "http://example.test/a.obj"), piece("b1", 45, "http://example.test/b.obj"),
            piece("c1", 5, "http://example.test/c.obj", locked=False), piece("d1", 10, None)]
    assert board.table_meshes(objs) == {"http://example.test/a.obj"}
