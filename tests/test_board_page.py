"""The board view's API (app/tools/board/), on the board state sample's table and layout, live
(with TTS stubbed) and from a saved board.json."""

import json

import pytest
from fastapi.testclient import TestClient

import board
import data
import layouts
import tooltips
import tts_bridge
from app import server
from test_formats import figure, load, made_up_table
from test_tooltips import SHEET, squad


def table():
    """The sample table, and a Blue unit behind the central ruin (A1) from the Pathfinders."""
    return made_up_table() + [figure(4 + i * 1.5, -7, "Hellblasters", "army.py:Blue list", f"h{i}") for i in range(2)]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(board, "read_objects", table)
    monkeypatch.setattr(board, "BOARD_JSON", tmp_path / "board.json")
    monkeypatch.setattr(layouts, "load_all", lambda: [load("layout.json")])
    monkeypatch.setattr(data, "datasheets_by_id", lambda: {"c8b1-9d6c-4a53-b0e2": SHEET})
    unit = squad()
    trooper = tooltips.tooltip(unit, unit["models"][1], SHEET, lead=False)["text"]

    def lua(script, timeout=None):
        if "getGMNotes" in script:
            return "7:1:2:3"                  # the fingerprint
        return {f"a1b2c{i}": f"[Pathfinder Team]\n{trooper}" for i in range(3)}
    monkeypatch.setattr(tts_bridge, "run_lua", lua)
    return TestClient(server.create_app())


def index(st, name):
    return next(i for i, u in enumerate(st["units"]) if u["unit"] == name and u["on_table"])


def test_listed_on_the_homepage(client):
    assert any(t["path"] == "/tools/board/" for t in client.get("/api/tools").json())
    assert "Board view" in client.get("/tools/board/").text


def test_live_state(client):
    st = client.get("/api/board/state").json()
    assert (st["source"], st["fingerprint"], st["layout"]["id"]) == ("live", "7:1:2:3", "0c4960")
    assert {u["unit"] for u in st["units"]} == {"Pathfinder Team", "Stealth Battlesuits", "Intercessor Squad",
                                                 "Hellblasters"}
    assert st["layout"]["areas"] and st["units"][0]["positions"]
    assert client.get("/api/board/fingerprint").json() == {"fingerprint": "7:1:2:3"}


def test_sight_threat_and_pair(client):
    st = client.get("/api/board/state").json()
    pf, v = index(st, "Pathfinder Team"), st["v"]
    sight = client.get(f"/api/board/sight?i={pf}&v={v}").json()
    assert len(sight["polygons"]) == 3 and all(len(p) > 3 for p in sight["polygons"])
    sees = {s["unit"]: s["visible"] for s in sight["sees"]}
    assert sees == {"Intercessor Squad": 2, "Hellblasters": 0}          # the ruin is in the way
    got = client.get(f"/api/board/threat?i={pf}&v={v}").json()
    assert (got["move"], got["weapons_from"]) == (6, "models")
    assert {b["band"] for b in got["bands"]} >= {"move", "charge", "shoot: Bolt rifle"}
    pair = client.get(f"/api/board/pair?a={pf}&b={index(st, 'Intercessor Squad')}&v={v}").json()
    assert pair["distance"] == 31.63
    assert pair["a_sees"] == {"visible": 2, "fully_visible": 2, "of": 2}
    assert pair["b_sees"] == {"visible": 3, "fully_visible": 3, "of": 3}


def test_stale_version_and_bad_units(client):
    st = client.get("/api/board/state").json()
    pf = index(st, "Pathfinder Team")
    stale = client.get(f"/api/board/sight?i={pf}&v={st['v'] - 1}")
    assert stale.status_code == 400 and "changed" in stale.json()["error"]
    off = next(i for i, u in enumerate(st["units"]) if not u["on_table"])
    assert "off the table" in client.get(f"/api/board/sight?i={off}&v={st['v']}").json()["error"]
    untagged = index(st, "Intercessor Squad")
    assert "no cached datasheet" in client.get(f"/api/board/threat?i={untagged}&v={st['v']}").json()["error"]


def test_from_board_json(client, monkeypatch):
    assert "No board.json" in client.get("/api/board/state?source=file").json()["error"]
    board.BOARD_JSON.write_text(json.dumps(board.board_state(table(), [load("layout.json")])))

    def no_tts(*args, **kwargs):
        raise SystemExit("TTS isn't accepting commands on port 39999.")
    monkeypatch.setattr(tts_bridge, "run_lua", no_tts)   # a saved board needs no TTS
    st = client.get("/api/board/state?source=file").json()
    assert st["source"] == "file" and st["layout"]["id"] == "0c4960" and st["fingerprint"] is None
    pf = index(st, "Pathfinder Team")
    assert client.get(f"/api/board/sight?i={pf}&v={st['v']}").status_code == 200
    got = client.get(f"/api/board/threat?i={pf}&v={st['v']}").json()
    assert got["weapons_from"] == "datasheet defaults"
