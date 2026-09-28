"""The LOS engine (los.py, issue #24): each terrain rule on hand-built terrain, and the
engine on the sample layout and every committed layout."""

import json
import statistics
import time
from pathlib import Path

import pytest

import layouts
import los

FORMATS = Path(__file__).parent / "fixtures" / "formats"


def square(x, z, half):
    return [[x - half, z - half], [x + half, z - half], [x + half, z + half], [x - half, z + half]]


def area(id, poly, *features):
    return {"id": id, "polygon": poly, "features": list(features)}


def feature(id, category, poly, height=5.0, floors=()):
    return {"id": id, "name": id, "category": category, "polygon": poly, "height": height, "floors": list(floors)}


# A ruin in the middle: a 6" area with a 2" dense block at its centre. A crater area off to
# the side with only an exposed feature, so it isn't Obscuring.
LAYOUT = {"areas": [area("A1", square(0, 0, 3), feature("A1a", "dense", square(0, 0, 1))),
                    area("A2", square(15, 0, 3), feature("A2a", "exposed", square(15, 0, 1), height=0.2))],
          "objectives": [], "zones": []}
TERRAIN = los.blockers(LAYOUT)


def at(x, z, height=0.0, guid=None):
    return los.model({"guid": guid or f"{x},{z}", "x": x, "z": z, "height": height, "base": [1.26, 1.26]})


def test_blockers():
    assert [(t["id"], t["kind"]) for t in TERRAIN] == [("A1", "obscuring"), ("A1a", "solid")]
    assert [t["id"] for t in los.blockers(LAYOUT, all_obscuring=True)] == ["A1", "A1a", "A2"]


def test_open_ground_is_fully_visible():
    assert los.sight(at(-10, 10), at(10, 10), TERRAIN) == ("full", set())
    assert los.sight(at(15, -10), at(15, 10), TERRAIN)[0] == "full"          # across the crater: not Obscuring
    assert los.sight(at(15, -10), at(15, 10), los.blockers(LAYOUT, all_obscuring=True))[0] == "none"


def test_obscuring_area_blocks_lines_through_it():
    state, by = los.sight(at(-10, 0), at(10, 0), TERRAIN)
    assert state == "none" and "A1" in by


def test_partly_visible_round_an_area_edge():
    """The target's base sticks out past the area's edge (z = 3); the observer's is below it."""
    assert los.sight(at(-10, 2), at(10, 3.2), TERRAIN) == ("partial", {"A1"})


def test_within_the_area_it_doesnt_obscure():
    """Either model in the area (even partly) ignores it; the dense block in the middle still
    stands in the way at ground level (Solid)."""
    state, by = los.sight(at(-10, 0), at(2.5, 0), TERRAIN)        # target in the area, behind the block
    assert state == "none" and by == {"A1a"}
    assert los.sight(at(-10, 2), at(2.5, 2), TERRAIN)[0] == "full"   # past the block's side
    assert los.sight(at(-3.3, 0.5), at(-10, 0.5), TERRAIN)[0] == "full"   # base overlaps the area's edge


def test_solid_is_only_the_ground_floor():
    """From a floor above 3", a line goes over the dense block's gaps; Obscuring still counts."""
    up = at(2.5, 0, height=4.0)
    assert los.sight(at(-2.5, 0), up, TERRAIN)[0] == "full"
    assert los.sight(at(-10, 0), up, TERRAIN)[0] == "full"          # the target is within the area
    assert los.sight(at(-10, 10), at(10, -10, height=4.0), TERRAIN)[0] == "none"   # through the area


def test_a_model_inside_a_dense_feature_sees_out():
    assert los.sight(at(0.5, 0), at(-2.5, 0), TERRAIN)[0] == "full"


def test_plunging_fire():
    up = at(2.5, 2.5, height=4.0, guid="up")
    low = at(2.5, 2.5, height=2.0, guid="low")
    target = [at(-2.5, 2.5, guid="t")]
    assert los.unit_visibility([up], target, TERRAIN)["plunging_fire"] is True
    assert los.unit_visibility([low], target, TERRAIN)["plunging_fire"] is False
    assert los.unit_visibility([up], [at(-2.5, 2.5, height=4.0)], TERRAIN)["plunging_fire"] is False


def test_unit_visibility_per_model():
    shooters = [at(-10, 8, guid="s1"), at(-10, 6, guid="s2")]
    targets = [at(10, 8, guid="t1"), at(5, 0, guid="t2")]           # t2 is behind the ruin from both
    got = los.unit_visibility(shooters, targets, TERRAIN)
    assert (got["visible"], got["fully_visible"]) == (1, 1)
    t1, t2 = got["models"]
    assert t1["visible"] == "full" and t1["seen_by"] == ["s1", "s2"]
    assert t2["visible"] == "none" and t2["seen_by"] == [] and "A1" in t2["blocked_by"]


def test_hidden_targets_need_detection_range():
    target = [at(2.5, 0, guid="t")]                   # in the ruin, behind its dense block
    near, far = at(2.5, 12, guid="near"), at(2.5, 18.5, guid="far")    # straight up the open side
    seen = los.unit_visibility([near, far], target, TERRAIN, target_hidden=True)["models"][0]
    assert seen["seen_by"] == ["near"] and seen["beyond_detection"] == ["far"]   # far is 17" away
    assert los.unit_visibility([far], target, TERRAIN)["models"][0]["visible"] == "full"


def test_gone_to_ground_is_12_inches():
    """Hidden and not fully visible because of a dense feature: 12" instead of 15"."""
    target = [at(1.6, 1.6, guid="t")]                 # in the ruin, tucked by its block's corner
    observer = at(-13, -3, guid="o")                  # 14" away, the block hides part of the base
    state, by = los.sight(observer, target[0], TERRAIN)
    assert state == "partial" and "A1a" in by and 12 < los.gap(observer, target[0]) < 15
    got = los.unit_visibility([observer], target, TERRAIN, target_hidden=True)["models"][0]
    assert got["visible"] == "none" and got["beyond_detection"] == ["o"]


def test_hidden_areas():
    assert los.hidden_areas([at(0, 2.5), at(1.5, 2.5)], LAYOUT) == ["A1"]
    assert los.hidden_areas([at(0, 2.5), at(0, 10)], LAYOUT) == []   # one model out in the open
    assert los.hidden_areas([at(15, 2)], LAYOUT) == []                 # the crater has no light or dense feature


def test_visibility_polygon_on_hand_built_terrain():
    poly = los.visibility_polygon(at(-10, 0), TERRAIN)
    seen = lambda x, z: layouts.inside((x, z), poly)   # noqa: E731
    assert seen(-20, 15) and seen(20, 15)             # open table
    assert seen(-2.5, 0)                              # into the ruin, in front of its block
    assert not seen(2, 0) and not seen(10, 0)         # behind the block, and behind the ruin
    assert not seen(15, 0)                            # the crater, behind the ruin
    beside = los.visibility_polygon(at(15, -10), TERRAIN)
    assert layouts.inside((15, 0), beside) and layouts.inside((15, 10), beside)   # the crater isn't Obscuring
    assert all(abs(x) <= 30.001 and abs(z) <= 22.001 for x, z in poly)


def test_on_the_sample_layout():
    layout = json.loads((FORMATS / "layout.json").read_text())
    terrain = los.blockers(layout)
    assert {t["id"] for t in terrain} == {"A1", "A1a", "A2"}           # A3 only has an exposed crater
    assert los.sight(at(-10, 0), at(10, 0), terrain)[0] == "none"      # across the central ruin
    assert los.sight(at(-10, 18), at(10, 18), terrain)[0] == "full"


@pytest.mark.parametrize("path", sorted((Path(los.__file__).parent / "layouts").glob("*.json")),
                         ids=lambda p: p.stem)
def test_on_real_layouts(path):
    if path.name == "index.json":
        pytest.skip("the index")
    terrain = los.blockers(json.loads(path.read_text()))
    poly = los.visibility_polygon(at(0, -20), terrain)
    assert len(poly) > 3 and 0 < abs(layouts.signed_area(poly)) <= 60 * 44 + 1


def test_fast_enough_for_clicking_around():
    terrain = los.blockers(layouts.load("33ce09"))
    times = []
    for x, z in [(0, -18), (-20, -16), (10, 0), (25, 15), (-5, 5)]:
        start = time.perf_counter()
        los.visibility_polygon(at(x, z), terrain)
        times.append(time.perf_counter() - start)
    assert statistics.median(times) < 0.05
