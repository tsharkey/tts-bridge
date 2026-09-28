"""Grouping models into units on the table (board.py), with and without the
unit tags army.py and recreate.py put on spawned models (issue #29)."""

import math

import pytest

import army
import board

BASE = [1.26, 1.0, 1.26]   # a 32mm base, as TTS bounds report it


def model(x, z, unit="Intercessor Squad", army_tag="army.py:Test", tags=None, guid=None):
    return {"guid": guid or f"{x}:{z}", "tag": "Figurine", "name": "", "head": f"[{unit}]", "notes": army_tag,
            "locked": False, "rot": 0, "p": [x, 1, z], "c": [x, 1, z], "s": list(BASE), "tags": tags or []}


def squad(index, x, z, n=5, step=1.5, **kw):
    """n models in a line from (x, z), a little apart (in coherency)."""
    tags = army.unit_tags(index, {"datasheet": {"id": "sheet-1"}})
    return [model(x + i * step, z, tags=tags, guid=f"u{index}m{i}", **kw) for i in range(n)]


def units_named(units, name="Intercessor Squad"):
    return [u for u in units if u["name"] == name]


def test_two_units_in_contact_stay_two():
    """Distance grouping would chain these into one unit of 10."""
    objs = squad(3, 0, 0) + squad(5, 0, 1.3)   # two rows, bases touching
    units = units_named(board.collect_units(objs))
    assert [(u["nth"], u["unit_id"], len(u["models"])) for u in units] == [(1, 3, 5), (2, 5, 5)]
    assert units[0]["datasheet"] == "sheet-1"
    assert all(board.coherency(u["models"]) == [] for u in units)


def test_model_out_of_coherency_stays_in_its_unit():
    objs = squad(1, 0, 0)
    objs[-1]["c"] = objs[-1]["p"] = [12, 1, 0]   # one model wanders off
    [unit] = units_named(board.collect_units(objs))
    assert len(unit["models"]) == 5
    problems = board.coherency(unit["models"])
    assert any("from the rest of its unit" in p for p in problems)
    assert any("apart" in p for p in problems)   # and more than 9" from the far end
    row = board.unit_row(unit, [])
    assert row["coherency"] == problems and row["unit_id"] == 1


def test_untagged_models_group_by_distance():
    objs = [model(0, 0), model(1.5, 0), model(10, 0), model(11.5, 0)]
    units = units_named(board.collect_units(objs))
    assert [len(u["models"]) for u in units] == [2, 2]
    assert all(u["unit_id"] is None for u in units)


def test_tagged_and_untagged_together():
    objs = squad(2, 0, 0, n=3) + [model(15, 0), model(16.5, 0)]
    units = units_named(board.collect_units(objs))
    assert [(u["nth"], u["unit_id"], len(u["models"])) for u in units] == [(1, 2, 3), (2, None, 2)]


def test_armies_and_reserves_apart():
    red = squad(1, 0, 0, n=2, army_tag="recreate:s:Red")
    blue = squad(1, 0, 5, n=2, army_tag="recreate:s:Blue")
    reserve = squad(1, 40, 0, n=1, army_tag="recreate:s:Red")   # off the table
    units = board.collect_units(red + blue + reserve)
    got = sorted((u["army"], u["on_table"], len(u["models"]), u["nth"]) for u in units)
    assert got == [("recreate:s:Blue", True, 2, 1), ("recreate:s:Red", False, 1, 1), ("recreate:s:Red", True, 2, 1)]


def test_lua_empty_tags_and_other_mods_tags():
    o = model(0, 0)
    o["tags"] = {}   # how TTS's JSON.encode sends an empty table
    assert board.unit_index(o) is None
    o["tags"] = ["Infantry", "tts-bridge:unit:7"]
    assert board.unit_index(o) == 7


def test_spawned_objects_carry_the_tags():
    parsed = {"title": "T", "sub": None, "faction": "T'au Empire", "units": [
        {"name": "A", "datasheet": {"id": "s-a"}, "models": [{"name": "A", "wargear": [], "pick": "t:0"}]},
        {"name": "B", "datasheet": None, "models": [{"name": "B", "wargear": [], "pick": "t:0"}]}]}
    catalog = {"t": [{"Name": "Custom_Model", "Nickname": "Fig", "Tags": ["Old", "tts-bridge:unit:9"],
                      "Transform": {}}]}
    parsed["units"][0]["card"] = "the datasheet"   # tooltips.attach sets it when the datasheet is cached
    objs, models, units = army.model_objects(parsed, catalog)
    a, b = objs
    assert units == [("A", [0]), ("B", [1])] and not any(o["Name"] == "Notecard" for o in objs)
    assert a["Tags"] == ["Old", "tts-bridge:unit:1", "tts-bridge:sheet:s-a"]   # ours replaced, theirs kept
    assert b["Tags"] == ["Old", "tts-bridge:unit:2"]
    assert board.unit_index({"tags": b["Tags"]}) == 2
    # the datasheet rides on the model, in its viewer script; B has no datasheet, so no script
    assert "the datasheet" in a["LuaScript"] and "addContextMenuItem" in a["LuaScript"]
    assert not b.get("LuaScript")


def test_closest_pair_between_units():
    a, b = squad(1, 0, 0, n=3), squad(2, 10, 0, n=2)
    d, p, q = board.closest_pair(a, b)
    assert (p["guid"], q["guid"]) == ("u1m2", "u2m0")
    assert abs(d - (10 - 3 - 1.26)) < 1e-9   # centres 7" apart, less two 32mm bases' radii


def test_find_unit_says_what_went_wrong():
    red = squad(1, 0, 0, n=2, army_tag="recreate:s:Red")
    blue = squad(1, 0, 10, n=2, army_tag="recreate:s:Blue")
    reserve = [model(40, 0, unit="Pathfinder Team")]
    units = board.collect_units(red + blue + reserve)
    with pytest.raises(board.UnitError, match="matches 2 units on the table.*Narrow it"):
        board.find_unit(units, "Intercessor")
    assert board.find_unit(units, "intercessor", army="blue")["army"] == "recreate:s:Blue"
    with pytest.raises(board.UnitError, match=r'No unit on the table matches "Pathfinder" \(one off the table does\)'):
        board.find_unit(units, "Pathfinder")
    with pytest.raises(board.UnitError, match=r'matches "Hive Tyrant"\.$'):
        board.find_unit(units, "Hive Tyrant")


ZONE = [[-30, 10], [30, 10], [30, 22], [-30, 22]]
LAYOUT = {"id": "test", "areas": [{"id": "A1", "polygon": [[-2, -2], [2, -2], [2, 2], [-2, 2]], "features": []}],
          "objectives": [{"id": "central", "kind": "central", "side": None, "x": 0, "z": 0, "area": "A1"},
                         {"id": "home-red", "kind": "home", "side": "red", "x": 0, "z": 18, "area": None}],
          "zones": [{"side": "red", "polygon": ZONE}]}


def test_landmarks():
    rows = {r["id"]: r for r in board.landmarks(squad(1, 2.5, 16, n=2), LAYOUT)}
    assert rows["central"]["within"] is False   # nearest the area's corner (2, 2)
    assert rows["central"]["distance"] == round(math.hypot(0.5, 14) - 0.63, 2)
    assert rows["home-red"]["within"] is None                  # a marker: range is the mission's
    assert rows["home-red"]["distance"] == round(((2.5 ** 2 + 2 ** 2) ** 0.5) - 0.63, 2)
    assert rows["red"] == {"kind": "zone", "id": "red", "distance": 0, "within": True}
    on_the_line = board.landmarks(squad(1, 0, 10.3, n=1), LAYOUT)[-1]   # base straddles the zone edge
    assert on_the_line["distance"] == 0 and on_the_line["within"] is False
    in_area = board.landmarks(squad(1, 2.4, 0, n=1), LAYOUT)[0]        # centre outside, base overlaps
    assert in_area["distance"] == 0 and in_area["within"] is True


def test_measure_units():
    a, b = board.collect_units(squad(1, 0, 0, n=2) + squad(2, 3.5, 0, n=1, army_tag="army.py:Enemy"))
    got = board.measure(a, b)
    assert got["a"]["unit"] == "Intercessor Squad" and got["b"]["army"] == "army.py:Enemy"
    assert got["distance"] == round(2 - 1.26, 2) and got["engagement_range"] is True
    assert [p["guid"] for p in got["closest"]] == ["u1m1", "u2m0"]
    assert got["landmarks"] == [] and got["layout"] is None
    alone = board.measure(a, layout=LAYOUT)
    assert alone["b"] is None and alone["distance"] is None
    assert {r["unit"] for r in alone["landmarks"]} == {"a"} and len(alone["landmarks"]) == 3


def test_overlapping_units_measure_zero():
    a, b = board.collect_units(squad(1, 0, 0, n=1) + squad(2, 1, 0, n=1, army_tag="army.py:Enemy"))
    d, p, q = board.closest_pair(a["models"], b["models"])
    assert d == 0 and (p["guid"], q["guid"]) == ("u1m0", "u2m0")
    assert board.model_gap(p, q) < 0   # place still sees the overlap


def long_base(x, z, w=4.0, d=1.0):
    return {**model(x, z), "s": [w, 1.0, d]}


def test_long_bases_measure_as_boxes():
    """A 4" x 1" base 1.5" inside the zone edge reaches 0.5" past it; as a circle (radius 1.25") it wouldn't."""
    hull = long_base(28.5, 16)                 # zone's +x edge is x = 30
    assert board.base_within(hull, ZONE) is False
    assert board.reach([hull], ZONE) == (0, False)
    turned = long_base(28.5, 16, w=1.0, d=4.0)   # same spot, the long side along the edge: inside
    assert board.base_within(turned, ZONE) is True
    outside = long_base(0, 5)                  # 5" below the zone, the base reaches 0.5" of that
    assert board.base_gap(outside, ZONE) == 4.5
    assert board.point_gap(long_base(0, 0), (3, 0)) == 1       # a marker 1" past the base's end
    assert board.point_gap(long_base(0, 0), (1, 0.2)) == 0     # under the base


def test_unnamed_terrain_areas_are_named():
    """LCT's area mats have no name and are "Board"s to TTS; they read as terrain areas, not "Board"."""
    def piece(guid, height, tags=(), name=""):
        return {"guid": guid, "tag": "Board", "name": name, "head": "", "notes": "", "locked": True, "rot": 0,
                "p": [5, 1, 5], "c": [5, 1, 5], "s": [6, height, 4], "tags": list(tags)}
    terrain = board.collect_terrain([piece("a", 0.0), piece("b", 0.0, ["obj_home_red"]),
                                     piece("c", 0.0, ["obj_neutral"]), piece("d", 3.0), piece("e", 0.0, name="Crater")])
    assert [t["name"] for t in terrain] == ["Terrain area", "Terrain area (red home objective)",
                                            "Terrain area (expansion objective)", "Board", "Crater"]


def test_plan_place_checks():
    red = squad(1, 0, 0, n=4)
    blue = [model(x, 10, army_tag="army.py:Enemy", guid=f"b{x}") for x in (0, 1.5)]
    objs = red + blue
    unit = board.find_unit(board.collect_units(objs), "Intercessor", army="Test")
    ok = board.plan_place(objs, unit, -10, -10, 0, cols=2)
    assert ok["problems"] == [] and ok["facing"] == 0 and len(ok["positions"]) == 4
    assert ok["nearest_enemy"] > 15 and "zones" not in ok
    assert [m[0] for m in ok["moves"]] == ["u1m0", "u1m1", "u1m2", "u1m3"]
    bad = board.plan_place(objs, unit, 0, 8, 0, cols=4)            # right up to the enemy
    assert any("engagement range of enemy Intercessor Squad" in p for p in bad["problems"])
    off = board.plan_place(objs, unit, 29.5, 0, 0, cols=4)
    assert any("off the table" in p for p in off["problems"])
    onto = board.plan_place(objs, unit, 0.75, 10, 0, cols=2)
    assert any("overlaps a model of Intercessor Squad" in p for p in onto["problems"])
    with pytest.raises(board.UnitError, match="only moves models it spawned"):
        board.plan_place(objs, {**unit, "army": "untagged"}, 0, 0)


def test_plan_place_with_a_layout():
    objs = squad(1, 0, 0, n=2)
    unit = board.collect_units(objs)[0]
    got = board.plan_place(objs, unit, 0.5, 16, 0, layout=LAYOUT)
    assert got["zones"] == ["red"] and got["areas"] == [] and got["objectives"] == []
    got = board.plan_place(objs, unit, 0.5, 0, 0, layout=LAYOUT)
    assert got["zones"] == [] and got["areas"] == ["A1"] and got["objectives"] == ["central"]


def test_undo_is_a_stack(tmp_path, monkeypatch):
    monkeypatch.setattr(board, "UNDO_JSON", tmp_path / "undo.json")
    sent = []
    monkeypatch.setattr(board, "move", lambda moves: sent.append(list(moves)) or len(moves))
    objs = squad(1, 0, 0, n=2) + squad(2, 5, 5, n=1)
    first, second = board.collect_units(objs)
    for u in (first, second):
        assert board.apply_place(u, board.plan_place(objs, u, -10, -10, 0)) == len(u["models"])
    assert board.undo() == (1, 1)
    assert sent[-1] == [["u2m0", 5, 1, 5, 0]]          # back where it was
    assert board.undo() == (2, 0)
    with pytest.raises(board.UnitError, match="Nothing to undo"):
        board.undo()
    board.UNDO_JSON.write_text('[["old", 1, 2, 90]]')   # a file from before heights were kept
    assert board.undo() == (1, 0) and sent[-1] == [["old", 1, board.DROP_Y, 2, 90]]
