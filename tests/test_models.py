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
