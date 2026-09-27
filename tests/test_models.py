"""Browsing the Force Org catalogue (/api/models...), on a made-up catalogue."""

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
                        figure("Farsight Breachers", "Custom_Assetbundle"), {"Name": "Card", "Nickname": "Not a model"}],
             "1e84c2": [figure("Intercessor"), figure("Farsight (counts-as)")],
             "999999": [figure("Mystery")]}
    for g, objs in tiles.items():
        (catalog / f"{g}.json").write_text(json.dumps({"tile": g, "sha1": "x", "objects": objs}))
    monkeypatch.setattr(army, "CATALOG", catalog)
    return TestClient(server.create_app())


def test_tiles(client):
    tiles = client.get("/api/models/tiles", params={"faction": "T'au Empire"}).json()
    assert tiles[0] == {"tile": "e598e0", "label": "T'au Empire", "army": True, "models": 3}  # the army's own first
    labels = {t["tile"]: t["label"] for t in tiles}
    assert labels["999999"] == "Other models (999999)"
    assert all(not t["army"] for t in client.get("/api/models/tiles").json())


def test_search(client):
    r = client.get("/api/models", params={"q": "farsight"}).json()
    assert [(m["name"], m["tile"]) for m in r["models"]] == [
        ("Commander Farsight", "T'au Empire"), ("Farsight (counts-as)", "Space Marines / Ultramarines"),
        ("Farsight Breachers", "T'au Empire")]
    assert r["models"][0]["credit"] == "By someone"
    r = client.get("/api/models", params={"q": "farsight", "static": True, "tiles": "e598e0"}).json()
    assert [m["name"] for m in r["models"]] == ["Commander Farsight"]
    assert client.get("/api/models", params={"tiles": "e598e0"}).json()["total"] == 3  # the card isn't a model


def test_entry(client):
    info = client.get("/api/models/entry", params={"pick": "e598e0:0"}).json()
    assert (info["name"], info["static"], info["tile"]) == ("Commander Farsight", True, "T'au Empire")


def test_no_catalogue(client, monkeypatch, tmp_path):
    monkeypatch.setattr(army, "CATALOG", tmp_path / "nothing")
    r = client.get("/api/models", params={"q": "x"})
    assert r.status_code == 400 and "Data cache page" in r.json()["error"]


def test_models_page(client):
    r = client.get("/tools/models/")
    assert r.status_code == 200 and "/model-browser.js" in r.text
    assert client.get("/model-browser.js").status_code == 200 and client.get("/viewer3d.js").status_code == 200


# Favourites: figures liked for a unit, tried first when picking models.

@pytest.fixture
def mappings(monkeypatch, tmp_path):
    path = tmp_path / "mappings.json"
    monkeypatch.setattr(army, "MAPPINGS", path)
    return path


def test_set_and_clear_favourites(client, mappings):
    key = "T'au Empire|Commander Farsight"
    assert client.get("/api/favorites", params={"key": key}).json()["models"] == []
    client.post("/api/favorites", json={"key": key, "pick": "1e84c2:1"})
    client.post("/api/favorites", json={"key": key, "pick": "e598e0:0"})
    got = client.get("/api/favorites", params={"key": key}).json()["models"]
    assert [m["name"] for m in got] == ["Farsight (counts-as)", "Commander Farsight"]
    client.post("/api/favorites", json={"key": key, "pick": "1e84c2:1", "on": False})
    assert json.loads(mappings.read_text())["favorites"] == {key: ["e598e0:0"]}
    client.post("/api/favorites", json={"key": key, "pick": "e598e0:0", "on": False})
    assert "favorites" not in json.loads(mappings.read_text()) or key not in json.loads(mappings.read_text())["favorites"]
    assert "T'au Empire" in client.get("/api/models/armies").json()


def test_resolve_prefers_a_favourite(client):
    catalog = army.load_catalog()
    parsed = {"faction": "T'au Empire", "sub": None, "units": [
        {"name": "Commander Farsight", "allied": False, "models": [{"name": "Commander Farsight", "wargear": []}]}]}
    # no favourite: Force Org's own Farsight, from the army's tile
    rows = army.resolve(parsed, catalog, {})
    assert rows[0][1]["pick"] == "e598e0:0"
    # a favourite wins, even from another army's tile and with a name that only half matches
    fav = {"favorites": {"T'au Empire|Commander Farsight": ["1e84c2:1"]}}
    rows = army.resolve(parsed, catalog, fav)
    assert (rows[0][1]["pick"], rows[0][2]) == ("1e84c2:1", "auto cover=41% favourite")
    # of several favourites, the one that fits the model best
    both = {"favorites": {"T'au Empire|Commander Farsight": ["1e84c2:0", "1e84c2:1"]}}
    assert army.resolve(parsed, catalog, both)[0][1]["pick"] == "1e84c2:1"
    # a pinned model keeps its pin
    pinned = {**both, "models": {army.model_key("T'au Empire", parsed["units"][0], parsed["units"][0]["models"][0]): ["e598e0:0"]}}
    assert army.resolve(parsed, catalog, pinned)[0][1]["pick"] == "e598e0:0"


def test_scribe_shows_favourites(client, mappings, monkeypatch):
    import data
    from pathlib import Path
    monkeypatch.setattr(data, "CACHE", Path(__file__).resolve().parent / "fixtures" / "datacache")
    client.post("/api/favorites", json={"key": "T'au Empire|Commander Farsight", "pick": "1e84c2:1"})
    text = (Path(__file__).resolve().parent / "fixtures" / "tau_tournament.txt").read_text()
    v = client.post("/api/scribe/read", json={"text": text}).json()
    farsight = next(u for u in v["units"] if u["name"] == "Commander Farsight")
    assert [f["name"] for f in farsight["favorites"]] == ["Farsight (counts-as)"]
    assert farsight["groups"][0]["picks"][0]["name"] == "Farsight (counts-as)"  # picked because it's a favourite
