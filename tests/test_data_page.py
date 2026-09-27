"""The data cache page's API, on an empty cache in a temporary folder."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import army
import data
from app import server

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bsdata"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(data, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(army, "CATALOG", tmp_path / "catalog")
    return TestClient(server.create_app())


def wait(client):
    for _ in range(100):
        job = client.get("/api/data/job").json()
        if not job["running"]:
            return job
        time.sleep(0.05)
    raise AssertionError("fetch didn't finish")


def test_empty_cache_shows_missing(client):
    s = client.get("/api/data/status").json()
    [bsdata] = s["sources"]
    assert (bsdata["key"], bsdata["cached"]) == ("bsdata", None)
    assert bsdata["default_url"] == "https://github.com/BSData/wh40k-11e"
    assert s["force_org"] == {"tiles": 0, "built": None}


def test_fetch_fills_the_cache(client):
    r = client.post("/api/data/fetch", json={"source": "bsdata", "url": str(FIXTURE), "ref": ""})
    assert r.status_code == 200
    job = wait(client)
    assert job["error"] is None, job
    assert any("3 datasheets" in line for line in job["log"])
    [bsdata] = client.get("/api/data/status").json()["sources"]
    assert (bsdata["cached"]["kind"], bsdata["cached"]["datasheets"]) == ("folder", 3)
    assert bsdata["factions"] == [{"faction": "Test Empire", "catalogue": "Xenos - Test Empire", "units": 3}]
    assert bsdata["skipped"]["hidden unit"] == 1


def test_fetch_errors_reach_the_page(client):
    client.post("/api/data/fetch", json={"source": "bsdata", "url": "not a link"})
    job = wait(client)
    assert "isn't a folder" in job["error"]
    assert client.post("/api/data/fetch", json={"source": "nope"}).status_code == 400


def test_force_org_check_without_tts(client, monkeypatch):
    import tts_bridge

    def no_tts(*args, **kwargs):
        raise SystemExit("TTS isn't accepting commands on port 39999.")
    monkeypatch.setattr(tts_bridge, "run_lua", no_tts)
    r = client.get("/api/data/force-org")
    assert r.status_code == 503 and "39999" in r.json()["error"]
    client.post("/api/data/force-org/refresh")
    assert "39999" in wait(client)["error"]


def test_force_org_refresh(client, monkeypatch, tmp_path):
    """Rebuilding catalog/ from TTS, with a fake Force Org of two tiles."""
    import json

    import tts_bridge

    tile = 'objectJSONs = { [[{"Name": "Custom_Model", "Nickname": "Trooper"}]] }'

    def fake_lua(code, **kwargs):
        if "Load Models" in code:
            return json.dumps(["aaaaaa", "bbbbbb"])
        return tile if "aaaaaa" in code else ""
    monkeypatch.setattr(tts_bridge, "run_lua", fake_lua)
    assert client.get("/api/data/force-org").json() == {"loaded": True}
    client.post("/api/data/force-org/refresh")
    job = wait(client)
    assert job["error"] is None, job
    assert job["log"][-1] == "2 tiles read, 1 updated"
    assert json.loads((tmp_path / "catalog" / "aaaaaa.json").read_text())["objects"] == [
        {"Name": "Custom_Model", "Nickname": "Trooper"}]
    assert client.get("/api/data/status").json()["force_org"]["tiles"] == 1


def test_force_org_not_loaded(client, monkeypatch):
    import tts_bridge
    monkeypatch.setattr(tts_bridge, "run_lua", lambda code, **kw: "{}")  # Lua's empty table
    assert client.get("/api/data/force-org").json() == {"loaded": False}
    client.post("/api/data/force-org/refresh")
    assert "Load the Force Org mod" in wait(client)["error"]


def test_force_org_check_when_tts_is_silent(client, monkeypatch):
    import tts_bridge
    monkeypatch.setattr(tts_bridge, "run_lua", lambda code, **kw: None)  # run_lua's timeout
    r = client.get("/api/data/force-org")
    assert r.status_code == 503 and "didn't answer" in r.json()["error"]
