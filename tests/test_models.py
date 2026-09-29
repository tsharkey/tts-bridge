"""Browsing the Force Org catalogue (/api/catalog...), on a made-up catalogue."""

import json

import pytest
from fastapi.testclient import TestClient

import army
from app import server


def figure(name, kind="Custom_Model"):
    return {"Name": kind, "Nickname": name, "Description": "By someone\nmore", "Transform": {}}


@pytest.fixture
def client(monkeypatch, tmp_path):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    tiles = {"e598e0": [figure("Commander Farsight"), figure("Stealth Shas'ui"),
                        figure("Farsight Breachers", "Custom_Assetbundle"), {"Name": "Card", "Nickname": "Not a model"},
                        figure("Commander Farsight")],   # another sculpt
             "1e84c2": [figure("Intercessor"), figure("Farsight (counts-as)")],
             "999999": [figure("Mystery")]}
    for g, objs in tiles.items():
        (catalog / f"{g}.json").write_text(json.dumps({"tile": g, "sha1": "x", "objects": objs}))
    monkeypatch.setattr(army, "CATALOG", catalog)
    return TestClient(server.create_app())


def test_tiles(client):
    tiles = client.get("/api/catalog/tiles", params={"faction": "T'au Empire"}).json()
    assert tiles[0] == {"tile": "e598e0", "label": "T'au Empire", "army": True, "models": 4}  # the army's own first
    labels = {t["tile"]: t["label"] for t in tiles}
    assert labels["999999"] == "Other models (999999)"
    assert all(not t["army"] for t in client.get("/api/catalog/tiles").json())


def test_search(client):
    r = client.get("/api/catalog", params={"q": "farsight"}).json()
    assert [(m["name"], m["tile"]) for m in r["models"]] == [
        ("Commander Farsight", "T'au Empire"), ("Commander Farsight", "T'au Empire"),
        ("Farsight (counts-as)", "Space Marines / Ultramarines"), ("Farsight Breachers", "T'au Empire")]
    assert r["models"][0]["credit"] == "By someone"
    r = client.get("/api/catalog", params={"q": "farsight", "static": True, "tiles": "e598e0"}).json()
    assert [m["name"] for m in r["models"]] == ["Commander Farsight", "Commander Farsight"]
    assert client.get("/api/catalog", params={"tiles": "e598e0"}).json()["total"] == 4  # the card isn't a model


def test_entry(client):
    info = client.get("/api/catalog/entry", params={"pick": "e598e0:0"}).json()
    assert (info["name"], info["static"], info["tile"]) == ("Commander Farsight", True, "T'au Empire")


def test_no_catalogue(client, monkeypatch, tmp_path):
    monkeypatch.setattr(army, "CATALOG", tmp_path / "nothing")
    r = client.get("/api/catalog", params={"q": "x"})
    assert r.status_code == 400 and "Data cache page" in r.json()["error"]


def test_models_page(client):
    r = client.get("/tools/models/")
    assert r.status_code == 200 and "/model-browser.js" in r.text
    assert client.get("/model-browser.js").status_code == 200 and client.get("/viewer3d.js").status_code == 200


# Favourites: models starred, picked first for their own army's units they match.

@pytest.fixture
def mappings(monkeypatch, tmp_path):
    path = tmp_path / "mappings.json"
    monkeypatch.setattr(army, "MAPPINGS", path)
    return path


def test_set_and_clear_favourites(client, mappings):
    assert client.get("/api/favorites").json()["models"] == []
    client.post("/api/favorites", json={"pick": "1e84c2:1"})
    client.post("/api/favorites", json={"pick": "e598e0:0"})
    got = client.get("/api/favorites").json()["models"]
    assert sorted(m["pick"] for m in got) == ["1e84c2:1", "e598e0:0"]
    client.post("/api/favorites", json={"pick": "1e84c2:1", "on": False})
    assert json.loads(mappings.read_text())["favorites"] == ["e598e0:0"]
    client.post("/api/favorites", json={"pick": "e598e0:0", "on": False})
    assert "favorites" not in json.loads(mappings.read_text())


def test_older_per_unit_favourites_still_count(mappings):
    old = {"favorites": {"T'au Empire|Commander Farsight": ["e598e0:4"], "Orks|Boyz": ["aaaaaa:1", "e598e0:4"]}}
    assert army.favourite_picks(old) == {"e598e0:4", "aaaaaa:1"}
    mappings.write_text(json.dumps(old))
    from app.core import lists
    assert lists.set_favourite("e598e0:0") == ["aaaaaa:1", "e598e0:0", "e598e0:4"]   # rewritten as one list


def test_resolve_prefers_a_favourite(client):
    catalog = army.load_catalog()
    parsed = {"faction": "T'au Empire", "sub": None, "units": [
        {"name": "Commander Farsight", "allied": False, "models": [{"name": "Commander Farsight", "wargear": []}]}]}
    # no favourite: the first of Force Org's two Farsights
    rows = army.resolve(parsed, catalog, {})
    assert rows[0][1]["pick"] == "e598e0:0"
    # a favourite wins among the models that match as well
    rows = army.resolve(parsed, catalog, {"favorites": ["e598e0:4"]})
    assert (rows[0][1]["pick"], rows[0][2]) == ("e598e0:4", "auto cover=100% favourite")
    # a favourite from another army, or one whose name matches less, never does
    for other in ("1e84c2:1", "e598e0:2"):
        assert army.resolve(parsed, catalog, {"favorites": [other]})[0][1]["pick"] == "e598e0:0"
    # a pinned model keeps its pin
    pinned = {"favorites": ["e598e0:4"],
              "models": {army.model_key("T'au Empire", parsed["units"][0], parsed["units"][0]["models"][0]): ["e598e0:0"]}}
    assert army.resolve(parsed, catalog, pinned)[0][1]["pick"] == "e598e0:0"


def test_scribe_shows_favourites(client, mappings, monkeypatch):
    import data
    from pathlib import Path
    monkeypatch.setattr(data, "CACHE", Path(__file__).resolve().parent / "fixtures" / "datacache")
    client.post("/api/favorites", json={"pick": "e598e0:4"})
    client.post("/api/favorites", json={"pick": "1e84c2:1"})   # another army's: not listed for Farsight
    text = (Path(__file__).resolve().parent / "fixtures" / "tau_tournament.txt").read_text()
    v = client.post("/api/scribe/read", json={"text": text}).json()
    farsight = next(u for u in v["units"] if u["name"] == "Commander Farsight")
    assert [f["pick"] for f in farsight["favorites"]] == ["e598e0:4"]
    assert farsight["groups"][0]["picks"][0]["pick"] == "e598e0:4"   # picked because it's a favourite


# Finding the models selected in TTS in the catalogue, by the meshes and bundles they use.

def test_find_selected_in_tts(monkeypatch, tmp_path):
    import tts_bridge
    from app.core import lists
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    base = {"Name": "Custom_Model", "Nickname": "Base", "CustomMesh": {"MeshURL": "base.obj"}}
    tiles = {"e598e0": [{**base, "Nickname": "Commander Farsight", "ChildObjects": [{**base, "CustomMesh": {"MeshURL": "farsight.obj"}}]},
                        {**base, "Nickname": "Crisis Battlesuit", "CustomMesh": {"MeshURL": "crisis.obj"},
                         "States": {"2": {**base, "CustomAssetbundle": {"AssetbundleURL": "crisis-alt.unity3d"}}}},
                        {**base, "Nickname": "Pathfinder"}]}
    (catalog / "e598e0.json").write_text(json.dumps({"tile": "e598e0", "sha1": "x", "objects": tiles["e598e0"]}))
    monkeypatch.setattr(army, "CATALOG", catalog)
    # the one sharing the most: the base alone is on Farsight and the Pathfinder, the figure only on Farsight
    assert [m["pick"] for m in lists.find_by_urls(["base.obj", "farsight.obj"])] == ["e598e0:0"]
    assert [m["pick"] for m in lists.find_by_urls(["base.obj"])] == ["e598e0:0", "e598e0:1", "e598e0:2"]   # all have it
    assert [m["pick"] for m in lists.find_by_urls(["crisis-alt.unity3d"])] == ["e598e0:1"]   # another state's bundle
    assert lists.find_by_urls(["nowhere.obj"]) == []
    selected = [{"guid": "a1", "name": "[4/4] Commander Farsight", "player": "Red", "urls": ["farsight.obj", "base.obj"]},
                {"guid": "b2", "name": "Dice", "player": "Red", "urls": {}}]
    monkeypatch.setattr(tts_bridge, "run_lua", lambda script, timeout=None: selected)
    client = TestClient(server.create_app())
    got = client.get("/api/catalog/selected").json()
    assert [(o["name"], [m["name"] for m in o["matches"]]) for o in got] == [
        ("[4/4] Commander Farsight", ["Commander Farsight"]), ("Dice", [])]
    monkeypatch.setattr(tts_bridge, "run_lua", lambda script, timeout=None: [])
    r = client.get("/api/catalog/selected")
    assert r.status_code == 400 and "Nothing is selected in TTS" in r.json()["error"]


# States: an entry's other loadouts, poses and colours, pickable as "<tile>:<index>:<state>".

def sternguard():
    def look(name, mesh, tint=0.5, transform=True):
        o = {"Name": "Custom_Model", "Nickname": name, "CustomMesh": {"MeshURL": mesh},
             "ColorDiffuse": {"r": tint, "g": tint, "b": tint}}
        return {**o, "Transform": {"posX": 1, "scaleX": 1}} if transform else o
    entry = look("Sternguard Veteran w/ Auto-Plasma", "plasma.obj")
    entry["States"] = {"2": look("Sternguard Veteran w/ Heavy Bolter", "heavy.obj", transform=False),
                       "3": look("Sternguard Veteran w/ Auto-Plasma", "plasma.obj"),          # the same again
                       "4": look("Sternguard Veteran w/ Auto-Plasma", "plasma.obj", 0.9)}     # a recolour
    return entry


def test_states_and_picks():
    assert army.split_pick("e598e0:12") == ("e598e0", 12, None)
    assert army.split_pick("e598e0:12:3") == ("e598e0", 12, 3)
    entry = sternguard()
    assert [n for n, _ in army.states_of(entry)] == [1, 2, 4]           # state 3 looks just like 1
    assert army.states_of({"Nickname": "Plain"}) == [(1, {"Nickname": "Plain"})]
    shown_third = {**entry, "States": {"1": entry["States"]["2"], "2": entry["States"]["4"]}}
    assert [n for n, _ in army.states_of(shown_third)] == [1, 2, 3]     # it shows the number States leaves out
    cat = {"e598e0": [entry]}
    assert army.pick_object(cat, "e598e0:0") is entry
    heavy = army.pick_object(cat, "e598e0:0:2")
    assert heavy["Nickname"].endswith("Heavy Bolter") and heavy["Transform"] == entry["Transform"]   # the entry's
    assert army.pick_object(cat, "e598e0:0:1") is entry                 # the state it shows: the entry
    assert army.nickname(cat, "e598e0:0:2") == "Sternguard Veteran w/ Heavy Bolter"


def test_a_state_is_matched_and_spawned(monkeypatch):
    cat = {"5e90c0": [sternguard()]}
    monkeypatch.setitem(army.FACTIONS, "Space Marines", ["5e90c0"])
    parsed = {"faction": "Space Marines", "sub": None, "title": "Test", "units": [
        {"name": "Sternguard Veteran Squad", "allied": False, "models": [
            {"name": "Sternguard Veteran", "wargear": ["Heavy bolter"]},
            {"name": "Sternguard Veteran", "wargear": ["Auto-plasma"]}]}]}
    rows = army.resolve(parsed, cat, {})
    assert [m["pick"] for m in parsed["units"][0]["models"]] == ["5e90c0:0:2", "5e90c0:0"]   # by loadout
    assert rows[0][2].startswith("auto")
    import tooltips
    monkeypatch.setattr(tooltips, "attach", lambda army_: 0)
    objs, _, _ = army.model_objects(parsed, cat)
    assert [o["CustomMesh"]["MeshURL"] for o in objs] == ["heavy.obj", "plasma.obj"]
    assert not any("States" in o for o in objs) and objs[0]["Transform"]["posX"] == 1   # one state, placed


def test_entry_info_lists_states(client):
    from app.core import lists
    cat = {"5e90c0": [sternguard()]}
    info = lists.entry_info(cat, "5e90c0:0")
    assert info["state"] == 1 and [(s["pick"], s["n"], s["name"]) for s in info["states"]] == [
        ("5e90c0:0", 1, "Sternguard Veteran w/ Auto-Plasma"), ("5e90c0:0:2", 2, "Sternguard Veteran w/ Heavy Bolter"),
        ("5e90c0:0:4", 4, "Sternguard Veteran w/ Auto-Plasma")]
    heavy = lists.entry_info(cat, "5e90c0:0:2")
    assert (heavy["name"], heavy["state"], heavy["preview"]["mesh"]) == ("Sternguard Veteran w/ Heavy Bolter", 2, "heavy.obj")
    assert "states" not in lists.entry_info({"5e90c0": [{"Name": "Custom_Model", "Nickname": "X"}]}, "5e90c0:0")
