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
