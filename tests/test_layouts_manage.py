"""Managing layouts/ (layouts.py, issue #21): checking a layout against the format, saving an
edit, deleting, what changed between versions, importing a new LCT version through a staging
folder, and comparing pieces with the live table. On copies of the sample layout in a
temporary folder; the build is faked."""

import copy
import json
from pathlib import Path

import pytest

import layouts
from test_layouts import LAYOUT, FakeMeshes

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "formats" / "layout.json").read_text())


def variant(layout_id, name=None, **changes):
    lo = copy.deepcopy(SAMPLE)
    lo.update(id=layout_id, name=name or f"Map {layout_id} - Dawn of War - Test", map=f"Map {layout_id}", **changes)
    return lo


@pytest.fixture
def folder(tmp_path):
    out = tmp_path / "layouts"
    out.mkdir()
    for i in ("aaa111", "bbb222", "ccc333"):
        (out / f"{i}.json").write_text(layouts.dumps(variant(i)) + "\n")
    layouts.write_index({"source": {"from": "lct", "lct_updated": "old"},
                         "layouts": [layouts.entry(variant(i)) for i in ("aaa111", "bbb222", "ccc333")],
                         "problems": {}}, out)
    return out


def test_the_sample_has_no_problems():
    assert layouts.problems(SAMPLE) == []


@pytest.mark.parametrize("change, expected", [
    (lambda lo: lo["areas"][0]["polygon"].reverse(), "counter-clockwise"),
    (lambda lo: lo["areas"][1].update(polygon=[[-24, 9], [-12, 15], [-12, 9], [-24, 15]]), "cross"),
    (lambda lo: lo["areas"][1]["polygon"][1].__setitem__(0, 31), "off the table"),
    (lambda lo: lo["areas"][0]["features"][0].update(category="heavy"), "category"),
    (lambda lo: lo["areas"][0]["features"][0].update(floors=[9]), "floors"),
    (lambda lo: lo["areas"][0]["features"][0].update(height=0), "height"),
    (lambda lo: lo["objectives"].pop(0), "home"),
    (lambda lo: lo["objectives"][2].update(area="A9"), "A9"),
    (lambda lo: lo["zones"].pop(), "red and a blue"),
])
def test_problems(change, expected):
    lo = copy.deepcopy(SAMPLE)
    change(lo)
    assert any(expected in p for p in layouts.problems(lo))


def test_save_marks_it_edited(folder):
    lo = layouts.load("aaa111", folder)
    lo["areas"][0]["features"][0]["category"] = "light"
    saved = layouts.save(lo, folder)
    assert saved["source"]["edited"] and saved["source"]["from"] == "lct"
    assert layouts.load("aaa111", folder)["areas"][0]["features"][0]["category"] == "light"
    [entry] = [e for e in layouts.read_index(folder)["layouts"] if e["id"] == "aaa111"]
    assert entry["edited"] == saved["source"]["edited"]
    lo["areas"][0]["polygon"].reverse()
    with pytest.raises(ValueError, match="Not saved: .*counter-clockwise"):
        layouts.save(lo, folder)
    assert layouts.load("aaa111", folder)["areas"][0]["polygon"] == SAMPLE["areas"][0]["polygon"]


def test_delete_retires_it(folder):
    layouts.delete("bbb222", folder)
    index = layouts.read_index(folder)
    assert not (folder / "bbb222.json").exists() and "bbb222" not in {lo["id"] for lo in index["layouts"]}
    assert [r["id"] for r in index["retired"]] == ["bbb222"]
    with pytest.raises(ValueError, match="No layout"):
        layouts.delete("bbb222", folder)


def test_differences():
    new = copy.deepcopy(SAMPLE)
    new["areas"][0]["features"][0].update(category="light", height=6.0)
    new["areas"][1]["polygon"][0] = [-24.5, 9.0]
    new["areas"].append({"id": "A4", "polygon": [[20, 0], [24, 0], [24, 4], [20, 4]], "features": []})
    got = layouts.differences(SAMPLE, new)
    assert "feature A1a category: dense -> light" in got and "feature A1a height: 5.5 -> 6.0" in got
    assert "area A2 reshaped" in got and "area A4 added" in got
    nudged = copy.deepcopy(SAMPLE)
    nudged["areas"][1]["polygon"][0] = [-24.1, 9.0]      # within 0.25"
    assert layouts.differences(SAMPLE, nudged) == []


def test_import_preview_and_apply(folder, tmp_path, monkeypatch):
    """LCT now has: aaa111 changed, bbb222 as it was, ccc333 gone, ddd444 new, eee555 (retired here)."""
    edited = layouts.load("bbb222", folder)
    layouts.save(edited, folder)                        # bbb222 edited by hand, unchanged in LCT
    changed = variant("aaa111")
    changed["areas"][0]["features"][0]["height"] = 7.0
    staged = [changed, variant("bbb222"), variant("ddd444"), variant("eee555")]
    index = layouts.read_index(folder)
    index["retired"] = [{"id": "eee555", "name": "old", "retired": "then"}]
    layouts.write_index(index, folder)

    def fake_build(cache=None, out=None, meshes=None, fetch=False, log=print):
        for lo in staged:
            (out / f"{lo['id']}.json").write_text(layouts.dumps(lo) + "\n")
        layouts.write_index({"source": {"from": "lct", "lct_updated": "new"},
                             "layouts": [layouts.entry(lo) for lo in staged], "problems": {}}, out)
    monkeypatch.setattr(layouts, "build_all", fake_build)
    staging = tmp_path / "staging"
    found = layouts.import_preview(folder=folder, staging=staging)
    assert [lo["id"] for lo in found["added"]] == ["ddd444"]
    assert [(lo["id"], lo["edited"]) for lo in found["changed"]] == [("aaa111", False)]
    assert found["changed"][0]["differences"] == ["feature A1a height: 5.5 -> 7.0"]
    assert [lo["id"] for lo in found["removed"]] == ["ccc333"] and found["retired"][0]["id"] == "eee555"
    assert found["unchanged"] == 1                      # bbb222: differences ignore the "edited" mark
    assert layouts.load("aaa111", folder)["areas"][0]["features"][0]["height"] == 5.5   # nothing written yet

    done = layouts.import_apply(["aaa111", "ddd444", "ccc333"], folder=folder, staging=staging)
    assert done == {"written": ["aaa111", "ddd444"], "removed": ["ccc333"]}
    assert layouts.load("aaa111", folder)["areas"][0]["features"][0]["height"] == 7.0
    index = layouts.read_index(folder)
    assert {lo["id"] for lo in index["layouts"]} == {"aaa111", "bbb222", "ddd444"}
    assert index["source"]["lct_updated"] == "new"
    with pytest.raises(ValueError, match="Nothing staged"):
        layouts.import_apply(["aaa111"], folder=folder, staging=tmp_path / "empty")


def live_from(layout, meshes, nudge=None):
    """The live table as layouts.READ_LUA reads it, from an LCT layout's objects: exact, or
    with one object (by GUID) moved nudge = (dx, dz) inches."""
    out = []
    for o, _, _, _ in layouts.placed_objects(layout, meshes):
        corners = layouts.box_corners(o, meshes)
        xs, zs = [x for x, _, _ in corners], [z for _, _, z in corners]
        t = o["Transform"]
        dx, dz = nudge[1] if nudge and nudge[0] == o["GUID"] else (0, 0)
        out.append({"url": layouts.mesh_source(o)[0], "p": [t["posX"] + dx, t["posY"], t["posZ"] + dz],
                    "r": [0, t["rotY"], 0], "c": [(max(xs) + min(xs)) / 2 + dx, (max(zs) + min(zs)) / 2 + dz],
                    "s": [max(xs) - min(xs), max(zs) - min(zs)]})
    return out


def test_compare_pieces_with_the_table():
    meshes = FakeMeshes()
    rows = layouts.compare_objects(LAYOUT, live_from(LAYOUT, meshes), meshes)
    assert rows and all(r["ok"] for r in rows)
    moved = layouts.compare_objects(LAYOUT, live_from(LAYOUT, meshes, ("r00001", (0.5, 0))), meshes)
    bad = [r for r in moved if not r["ok"]]
    assert [(r["name"], r["position"]) for r in bad] == [("Ruin", 0.5)]
    gone = layouts.compare_objects(LAYOUT, [], meshes)
    assert not any(r["found"] or r["ok"] for r in gone)
    assert layouts.compare(LAYOUT, live_from(LAYOUT, meshes), meshes)[:2] == (len(rows), 0)


def test_build_keeps_edited_and_retired(tmp_path, monkeypatch):
    """layouts.py build (and an import's staging) never overwrites a layout saved by hand, or
    brings back a retired one."""
    lct = tmp_path / "lct"
    (lct / "layouts").mkdir(parents=True)
    wanted = []
    for guid in ("abc123", "def456", "ghi789"):
        (lct / "layouts" / f"{guid}.json").write_text(json.dumps({**LAYOUT, "guid": guid}))
        wanted.append({"guid": guid, "file": f"layouts/{guid}.json", "name": LAYOUT["name"], "map": LAYOUT["map"],
                       "deployment": LAYOUT["deployment"], "pack": LAYOUT["pack"]})
    monkeypatch.setattr(layouts, "matchup_layouts", lambda cache=None: ({"source": {"updated": "new"}}, wanted))
    monkeypatch.setattr(layouts.mods, "lct_dir", lambda cache=None: lct)
    out = tmp_path / "layouts"
    out.mkdir()
    mine = variant("abc123", source={"from": "lct", "lct_updated": "old", "edited": "today"})
    (out / "abc123.json").write_text(layouts.dumps(mine) + "\n")
    layouts.write_index({"source": None, "layouts": [layouts.entry(mine)], "problems": {},
                         "retired": [{"id": "def456", "name": "gone", "retired": "then"}]}, out)
    built = layouts.build_all(out=out, meshes=FakeMeshes(), log=lambda *a: None)
    assert sorted(lo["id"] for lo in built) == ["abc123", "ghi789"]
    assert layouts.load("abc123", out) == mine                        # untouched
    assert not (out / "def456.json").exists()
    assert layouts.load("ghi789", out)["source"] == {"from": "lct", "lct_updated": "new"}
    assert layouts.read_index(out)["retired"][0]["id"] == "def456"
