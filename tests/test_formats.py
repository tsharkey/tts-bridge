"""The format samples in tests/fixtures/formats/ load and have the shape their
docs in docs/formats/ describe, so tracks can build against them."""

import json
from pathlib import Path

import pytest

import army
import board
import layouts

FORMATS = Path(__file__).parent / "fixtures" / "formats"
DOCS = Path(__file__).resolve().parent.parent / "docs" / "formats"
HALF_X, HALF_Z = 30, 22


def load(name):
    return json.loads((FORMATS / name).read_text())


@pytest.mark.parametrize("doc, sample", [("layout-terrain.md", "layout.json"),
                                         ("board-state.md", "board-state.json"),
                                         ("tts-gateway.md", "tts-gateway.json")])
def test_each_doc_links_its_sample(doc, sample):
    assert f"tests/fixtures/formats/{sample}" in (DOCS / doc).read_text()


def polygon_ok(poly):
    """At least 3 corners, all on the table, counter-clockwise seen from above."""
    assert len(poly) >= 3 and poly[0] != poly[-1]
    assert all(abs(x) <= HALF_X and abs(z) <= HALF_Z for x, z in poly)
    area2 = sum(x1 * z2 - x2 * z1 for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1]))
    assert area2 > 0, "corners should run counter-clockwise"


def inside(point, poly):
    x, z = point
    return all((x2 - x1) * (z - z1) - (z2 - z1) * (x - x1) >= -1e-6
               for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1]))   # convex polygons only


def centroid(poly):
    return sum(x for x, _ in poly) / len(poly), sum(z for _, z in poly) / len(poly)


def test_layout_terrain_sample():
    layout = load("layout.json")
    assert {"version", "id", "name", "map", "deployment", "pack", "source", "areas", "objectives", "zones"} <= set(layout)
    ids = [a["id"] for a in layout["areas"]]
    assert len(ids) == len(set(ids))
    for a in layout["areas"]:
        polygon_ok(a["polygon"])
        for f in a["features"]:
            assert {"id", "name", "category", "polygon", "height", "floors"} <= set(f)
            assert f["category"] in ("dense", "light", "exposed")
            polygon_ok(f["polygon"])
            assert inside(centroid(f["polygon"]), a["polygon"]), f["id"]
            assert all(0 < h < f["height"] for h in f["floors"])
    for o in layout["objectives"]:
        assert o["kind"] in ("home", "expansion", "central")
        assert o["side"] in (("red", "blue") if o["kind"] != "central" else (None,))
        assert o["area"] is None or o["area"] in ids
        assert abs(o["x"]) <= HALF_X and abs(o["z"]) <= HALF_Z
    assert sorted(z["side"] for z in layout["zones"]) == ["blue", "red"]
    for z in layout["zones"]:
        polygon_ok(z["polygon"])


def figure(x, z, unit, army_tag, guid, tags=()):
    return {"guid": guid, "tag": "Figurine", "name": unit, "head": f"[{unit}]", "notes": army_tag, "locked": False,
            "rot": 180 if "Ret" in army_tag else 0, "p": [x, 1.5, z], "c": [x, 1.5, z], "s": [1.26, 1.0, 1.26],
            "tags": list(tags)}


def terrain_of(layout):
    """The layout's areas (flat mats) and features as TTS would read them: boxes around each polygon."""
    out = []
    for a in layout["areas"]:
        for piece, height in [(a, 0.1), *((f, f["height"]) for f in a["features"])]:
            x, z, w, d = layouts.bounds(piece["polygon"])
            out.append({"head": "", "notes": "", "locked": True, "guid": f"t-{piece['id']}", "tag": "Custom_Model",
                        "name": piece.get("name", "Generic"), "tags": [], "rot": 0, "p": [x, 1, z],
                        "c": [round(x, 2), 1 + height / 2, round(z, 2)], "s": [round(w, 2), height, round(d, 2)]})
    return out


def made_up_table():
    """The table the board state sample was made from, as board.READ_LUA returns it."""
    red = "army.py:Ret Cadre"
    objs = [figure(-20 + i * 1.5, 13, "Pathfinder Team", red, f"a1b2c{i}",
                   army.unit_tags(2, {"datasheet": {"id": "c8b1-9d6c-4a53-b0e2"}})) for i in range(3)]
    objs += [figure(-40 + i * 1.5, 30, "Stealth Battlesuits", red, f"d4e5f{i}",   # off the table
                    army.unit_tags(5, {"datasheet": {"id": "f00d-1234-5678-9abc"}})) for i in range(3)]
    objs += [figure(-6 + i * 1.5, -18, "Intercessor Squad", "army.py:Blue list", f"0f0f0{i}") for i in range(2)]
    fixed = {"head": "", "notes": "", "locked": True}
    return objs + [
        {**fixed, "guid": "a064d7", "tag": "Custom_Model", "name": "", "tags": ["battlemaster_battlemat"], "rot": 0,
         "p": [0, 0.5, 0], "c": [0, 0.5, 0], "s": [60, 1.0, 44]},
        *terrain_of(load("layout.json")),
        {**fixed, "guid": "5e5e5e", "tag": "Scripting", "name": "Red deployment zone", "tags": [], "rot": 0,
         "p": [0, 1, 16], "c": [0, 1, 16], "s": [60, 2, 12]},
    ]


def test_board_state_sample_is_what_board_py_writes():
    """If this fails, board.py's output changed: update the sample and board-state.md together."""
    assert board.board_state(made_up_table(), [load("layout.json")]) == load("board-state.json")


def test_tts_gateway_sample():
    gw = load("tts-gateway.json")
    assert gw["gateway"] == {"gateway": True}
    assert set(gw["lua_request"]) == {"script", "timeout"}
    for reply in gw["lua_replies"]:
        assert set(reply) == ({"ok", "result"} if reply["ok"] else {"ok", "error"})
    for event in gw["events"]:
        assert event["messageID"] in range(8) and event["messageID"] != 5
        assert "reply" not in (event.get("customMessage") or {})   # replies never reach the event stream
