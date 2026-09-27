"""
Scribe (app/tools/scribe): list in, TTS army out. Runs on the made-up datasheet
cache (tests/fixtures/datacache/), a two-tile made-up Force Org catalogue, and
temporary mappings.json and Saved Objects folders. TTS is faked where needed.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import army
import data
import tts_bridge
from app import server

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TOURNAMENT = (FIXTURES / "tau_tournament.txt").read_text()


def figure(name):
    return {"Name": "Custom_Model", "Nickname": name, "Transform": {"posX": 0, "posY": 0, "posZ": 0},
            "Description": "A test figure"}


@pytest.fixture
def paths(monkeypatch, tmp_path):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "e598e0.json").write_text(json.dumps({"tile": "e598e0", "sha1": "x", "objects": [
        figure("Commander Farsight"), figure("Stealth Shas'vre"), figure("Stealth Shas'ui"),
        figure("Riptide Battlesuit")]}))
    monkeypatch.setattr(army, "CATALOG", catalog)
    monkeypatch.setattr(army, "MAPPINGS", tmp_path / "mappings.json")
    monkeypatch.setattr(data, "CACHE", FIXTURES / "datacache")
    monkeypatch.setenv("TTS_SAVED_OBJECTS", str(tmp_path / "Saved Objects"))
    return tmp_path


@pytest.fixture
def client(paths):
    return TestClient(server.create_app())


def unit(view, name):
    return next(u for u in view["units"] if u["name"] == name)


def test_read(client, paths):
    r = client.post("/api/scribe/read", json={"text": TOURNAMENT})
    assert r.status_code == 200, r.text
    v = r.json()
    assert (v["army"]["faction"], v["army"]["format"], v["army"]["points"]) == ("T'au Empire", "tournament", 2005)
    assert v["cached"] == {"datasheets": True, "catalog": True, "bases": False}
    farsight = unit(v, "Commander Farsight")
    assert farsight["datasheet"]["how"] == "exact"
    [group] = farsight["groups"]
    assert (group["count"], group["picks"][0]["name"], group["options"][0]["name"]) == (1, "Commander Farsight",
                                                                                      "Commander Farsight")
    coldstar = unit(v, "Commander in Coldstar Battlesuit")
    assert coldstar["groups"][0]["gear"][-1] == "2x Missile pod"
    assert unit(v, "Commander in Coldstar Battlesuit")["n"] == 1
    assert "Breacher Team" in v["problems"]["datasheets"]            # not in the made-up cache
    assert "Breacher Team: Breacher Fire Warriors" in v["problems"]["figures"]  # not in the made-up catalogue
    assert v["totals"]["picked"] < v["totals"]["models"]
    assert v["saved_object"].endswith("Saved Objects/T'au Empire/T'au Empire Retaliation Cadre (Bonded Heroes) 2005.json")
    assert not army.MAPPINGS.exists()  # reading changes nothing


def test_read_errors(client):
    assert client.post("/api/scribe/read", json={"text": " "}).json()["error"] == "Paste a list first."
    r = client.post("/api/scribe/read", json={"text": "hello\nthere"})
    assert r.status_code == 400 and "faction" in r.json()["error"]


def test_roles_and_enhancements(client):
    v = client.post("/api/scribe/read", json={"text": (FIXTURES / "plus_format_tau.txt").read_text()}).json()
    farsight = unit(v, "Commander Farsight")
    assert (farsight["role"], farsight["attached_to"], farsight["warlord"]) == ("leader", 1, True)
    assert v["units"][1]["role"] == "bodyguard"


def test_save_without_tts(client, paths, monkeypatch):
    def no_tts(*a, **kw):
        raise AssertionError("saving must not need TTS")
    monkeypatch.setattr(tts_bridge, "run_lua", no_tts)
    r = client.post("/api/scribe/save", json={"text": TOURNAMENT, "facing": 90})
    assert r.status_code == 200, r.text
    path = Path(r.json()["path"])
    assert path.parent == paths / "Saved Objects" / "T'au Empire"
    states = json.loads(path.read_text())["ObjectStates"]
    assert len(states) == r.json()["models"] > 0
    first = states[0]
    assert first["Description"].split("\n")[0] == "[Commander Farsight]"  # board.py groups units by it
    assert first["GMNotes"] == "army.py:T'au Empire Retaliation Cadre (Bonded Heroes) 2005"
    assert "GUID" not in first and first["Transform"]["rotY"] == 90
    spots = {(s["Transform"]["posX"], s["Transform"]["posZ"]) for s in states}
    assert len(spots) == len(states)  # every model in its own place
    # its choices are pinned, as `army.py build` pins them
    mappings = json.loads(army.MAPPINGS.read_text())
    assert mappings["datasheets"]["T'au Empire|Commander Farsight"]["id"] == "t-farsight"
    assert any(k.startswith("T'au Empire|Commander Farsight|") for k in mappings["models"])


def test_save_needs_a_catalogue(client, paths, monkeypatch):
    monkeypatch.setattr(army, "CATALOG", paths / "empty")
    v = client.post("/api/scribe/read", json={"text": TOURNAMENT}).json()
    assert v["cached"]["catalog"] is False and v["units"][0]["groups"][0]["options"] == []
    r = client.post("/api/scribe/save", json={"text": TOURNAMENT})
    assert r.status_code == 400 and "Data cache page" in r.json()["error"]


def test_pick_another_datasheet(client):
    choices = client.get("/api/scribe/datasheets", params={"faction": "T'au Empire"}).json()
    own = [c["name"] for c in choices if c["own"]]
    assert "Stealth Battlesuits" in own and "Terminator Squad" not in own
    assert any(c["name"] == "Terminator Squad" for c in choices)  # everything else, after
    r = client.post("/api/scribe/datasheet", json={"key": "T'au Empire|Breacher Team", "id": "t-stealth"})
    assert r.json()["datasheet"] == "Stealth Battlesuits"
    v = client.post("/api/scribe/read", json={"text": TOURNAMENT}).json()
    assert unit(v, "Breacher Team")["datasheet"] == {"id": "t-stealth", "name": "Stealth Battlesuits",
                                                     "catalogue": "Xenos - T'au Empire", "how": "pinned"}
    assert client.post("/api/scribe/datasheet", json={"key": "x", "id": "nope"}).status_code == 400


def test_spawn(client, monkeypatch):
    calls = []

    def fake_lua(code, **kw):
        calls.append(code)
        if "spawnObjectJSON" in code:
            return json.dumps([f"g{i:05d}" for i in range(code.count("spawnObjectJSON"))])
        if "loading_custom" in code:
            return json.dumps({f"g{i:05d}": [1.5, 1.5] for i in range(100)})
        return "ok"
    monkeypatch.setattr(tts_bridge, "run_lua", fake_lua)
    r = client.post("/api/scribe/spawn", json={"text": TOURNAMENT, "x": -30, "z": 21, "width": 60, "facing": 180})
    assert r.status_code == 200, r.text
    assert r.json()["spawned"] == calls[0].count("spawnObjectJSON") > 0
    assert "setPosition" in calls[-1]


def test_spawn_without_tts(client, monkeypatch):
    def no_tts(*a, **kw):
        raise SystemExit("TTS isn't accepting commands on port 39999.")
    monkeypatch.setattr(tts_bridge, "run_lua", no_tts)
    r = client.post("/api/scribe/spawn", json={"text": TOURNAMENT})
    assert r.status_code == 503 and "39999" in r.json()["error"]


def test_saved_objects_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_SAVED_OBJECTS", str(tmp_path / "x"))
    assert army.saved_objects_dir() == tmp_path / "x"
    monkeypatch.delenv("TTS_SAVED_OBJECTS")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "Documents/My Games/Tabletop Simulator/Saves").mkdir(parents=True)  # Windows
    assert army.saved_objects_dir() == tmp_path / "Documents/My Games/Tabletop Simulator/Saves/Saved Objects"


def test_pack_wraps_rows():
    units = [("A", [0, 1, 2]), ("B", [3])]
    dims = [[1, 1]] * 4
    moves = army.pack(units, dims, 0, 0, width=3)  # two 1" models and their gaps per line
    assert [k for k, _, _ in moves] == [0, 1, 2, 3]
    xs = {k: x for k, x, _ in moves}
    zs = {k: z for k, _, z in moves}
    assert xs[0] < xs[1] and zs[2] < zs[0]  # a third model wraps to the next line
    assert zs[3] < zs[2]                   # the next unit wraps to the next row
