"""Grouping models into units on the table (board.py), with and without the
unit tags army.py and recreate.py put on spawned models (issue #29)."""

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
    objs, _, _ = army.model_objects(parsed, catalog)
    assert objs[0]["Tags"] == ["Old", "tts-bridge:unit:1", "tts-bridge:sheet:s-a"]   # ours replaced, theirs kept
    assert objs[1]["Tags"] == ["Old", "tts-bridge:unit:2"]
    assert board.unit_index({"tags": objs[1]["Tags"]}) == 2
