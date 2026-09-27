"""Board from image on the shared list pipeline (issue #69): the Armies
summary, and Send to TTS spawning tagged, tooltipped models with datasheet
cards. Runs on the made-up caches and a fake TTS."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import army
import data
import recreate
import tts_bridge
from app import server

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TOURNAMENT = (FIXTURES / "tau_tournament.txt").read_text()


@pytest.fixture
def caches(monkeypatch, tmp_path):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    figures = [{"Name": "Custom_Model", "Nickname": n, "Transform": {}} for n in
               ("Commander Farsight", "Stealth Shas'vre", "Stealth Shas'ui", "Riptide Battlesuit")]
    (catalog / "e598e0.json").write_text(json.dumps({"tile": "e598e0", "sha1": "x", "objects": figures}))
    monkeypatch.setattr(army, "CATALOG", catalog)
    monkeypatch.setattr(army, "MAPPINGS", tmp_path / "mappings.json")
    monkeypatch.setattr(data, "CACHE", FIXTURES / "datacache")
    return tmp_path


def test_armies_summary(caches):
    s = TestClient(server.create_app()).post("/api/parse", json={"text": TOURNAMENT}).json()
    assert s["datasheets_cached"] is True
    assert "Breacher Team" in s["no_datasheet"]          # not in the made-up datasheets
    assert s["guessed"] == []                            # a full export says what's in each unit
    short = TestClient(server.create_app()).post(
        "/api/parse", json={"text": (FIXTURES / "tau_short.txt").read_text()}).json()
    assert "Crisis Starscythe Battlesuits" in short["guessed"]   # filled in from its datasheet


def test_send_to_tts_tags_models_and_brings_cards(caches, monkeypatch):
    sent = []

    def fake_lua(code, **kw):
        sent.append(code)
        if "spawnObjectJSON" in code:
            return json.dumps([f"g{i:05d}" for i in range(code.count("spawnObjectJSON"))])
        if "loading_custom" in code:
            return json.dumps({f"g{i:05d}": [1.3, 1.3] for i in range(200)})
        if "Reserve" in code or "reserve" in code:
            return "[]"
        return "1"
    monkeypatch.setattr(tts_bridge, "run_lua", fake_lua)
    monkeypatch.setattr(tts_bridge, "lua_str", json.dumps)  # so the test can read the spawned objects back
    scene = {"armies": [{"list_text": TOURNAMENT, "units": [{"unit": "Commander Farsight", "at": [0, 0]}]},
                        {"list_text": TOURNAMENT, "units": []}]}
    summary = recreate.place_scene(scene, "recreate:test", log=lambda *_: None)

    spawned = [json.loads(json.loads(line.split("json = ", 1)[1].rsplit("}).guid", 1)[0]))
               for code in sent if "spawnObjectJSON" in code for line in code.split("\n") if "spawnObjectJSON" in line]
    cards = [o for o in spawned if o["Name"] == "Notecard"]
    models = [o for o in spawned if o["Name"] != "Notecard"]
    assert summary["datasheet_cards"] == len(cards) > 0
    assert summary["spawned"] == len(models)
    farsight = next(o for o in models if o["Description"].startswith("[Commander Farsight]"))
    assert "tts-bridge:unit:1" in farsight["Tags"] and farsight["GMNotes"] == "recreate:test:Red"
    card = next(c for c in cards if c["Nickname"] == "Commander Farsight datasheet")
    assert card["Tags"][:2] == ["tts-bridge:card", "tts-bridge:unit:1"] and card["GMNotes"] == "recreate:test:Red"
    assert {c["GMNotes"] for c in cards} == {"recreate:test:Red", "recreate:test:Blue"}
