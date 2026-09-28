"""The layouts tool's API (app/tools/layouts/), on copies of the sample layout in a temporary
layouts folder. The import build, the table check and LCT's art are stubbed."""

import time

import pytest
from fastapi.testclient import TestClient

import layouts
from app import server
from app.core import lct
from test_layouts_manage import variant


@pytest.fixture
def client(monkeypatch, tmp_path):
    folder = tmp_path / "layouts"
    folder.mkdir()
    for i in ("aaa111", "bbb222"):
        (folder / f"{i}.json").write_text(layouts.dumps(variant(i)) + "\n")
    layouts.write_index({"source": {"from": "lct", "lct_updated": "old"},
                         "layouts": [layouts.entry(variant(i)) for i in ("aaa111", "bbb222")], "problems": {}}, folder)
    monkeypatch.setattr(layouts, "LAYOUTS", folder)
    monkeypatch.setattr(layouts, "STAGING", tmp_path / "staging")
    return TestClient(server.create_app())


def test_listed_on_the_homepage(client):
    assert any(t["path"] == "/tools/layouts/" for t in client.get("/api/tools").json())
    assert "Layouts" in client.get("/tools/layouts/").text


def test_list_open_save_delete(client):
    got = client.get("/api/layouts").json()
    assert [lo["id"] for lo in got["layouts"]] == ["aaa111", "bbb222"] and got["tolerance"] == [0.25, 2.0]
    lo = client.get("/api/layouts/aaa111").json()
    lo["areas"][0]["features"][0]["height"] = 6          # a browser sends 6, not 6.0
    saved = client.post("/api/layouts/aaa111", json=lo).json()
    assert saved["source"]["edited"] and saved["areas"][0]["features"][0]["height"] == 6.0
    text = (layouts.LAYOUTS / "aaa111.json").read_text()
    assert '"height": 6.0' in text and '"edited"' in text
    assert client.get("/api/layouts").json()["layouts"][0]["edited"]
    lo["areas"][0]["polygon"].reverse()
    bad = client.post("/api/layouts/aaa111", json=lo)
    assert bad.status_code == 400 and "counter-clockwise" in bad.json()["error"]
    assert "doesn't match" in client.post("/api/layouts/bbb222", json=lo).json()["error"]
    assert client.delete("/api/layouts/bbb222").json() == {"deleted": "bbb222"}
    assert [r["id"] for r in client.get("/api/layouts").json()["retired"]] == ["bbb222"]
    assert "No layout" in client.get("/api/layouts/bbb222").json()["error"]


def test_compare_and_art(client, monkeypatch):
    rows = [{"name": "Ruin", "x": 1, "z": 2, "found": True, "position": 0.5, "rotation": 0, "bounds": 0, "ok": False},
            {"name": "Wall", "x": 3, "z": 4, "found": False, "position": None, "rotation": None, "bounds": None, "ok": False}]
    monkeypatch.setattr(layouts, "check_layout", lambda layout_id, log=None: rows)
    assert client.get("/api/layouts/aaa111/check").json() == {"pieces": rows, "found": 1, "ok": 0}
    monkeypatch.setattr(lct, "lct_matchups", lambda: {"1_1": {"layouts": [
        {"card": "Map aaa111", "deployment": "Dawn of War", "art": "https://example.test/a.png"}]}})
    assert client.get("/api/layouts/aaa111/art").json() == {"url": "https://example.test/a.png"}
    assert client.get("/api/layouts/bbb222/art").json() == {"url": None}

    def no_lct():
        raise ValueError("Load the LCT table in TTS first")
    monkeypatch.setattr(lct, "lct_matchups", no_lct)
    assert client.get("/api/layouts/aaa111/art").json() == {"url": None}


def test_import_job(client, monkeypatch):
    preview = {"added": [{"id": "ccc333", "name": "new"}], "changed": [], "removed": [], "unchanged": 2,
               "retired": [], "source": {"from": "lct", "lct_updated": "new"}}
    staged = variant("ccc333")

    def fake_preview(log=print):
        log("building")
        layouts.STAGING.mkdir(parents=True, exist_ok=True)
        (layouts.STAGING / "ccc333.json").write_text(layouts.dumps(staged) + "\n")
        layouts.write_index({"source": preview["source"], "layouts": [layouts.entry(staged)], "problems": {}},
                            layouts.STAGING)
        return preview
    monkeypatch.setattr(layouts, "import_preview", fake_preview)
    assert client.post("/api/layouts/import", json={}).json()["running"] in (True, False)
    for _ in range(100):
        job = client.get("/api/layouts/import").json()
        if not job["running"]:
            break
        time.sleep(0.02)
    assert job["preview"] == preview and job["log"] == ["building"] and job["error"] is None
    assert client.post("/api/layouts/import/apply", json={"ids": ["ccc333"]}).json() == {"written": ["ccc333"],
                                                                                         "removed": []}
    assert "ccc333" in [lo["id"] for lo in client.get("/api/layouts").json()["layouts"]]
    assert client.get("/api/layouts/import").json()["preview"] is None


def test_numbers_keep_their_spelling():
    old = {"polygon": [[-0.0, 4.0], [1.5, 2.0]], "height": 5.0, "floors": [], "name": "x"}
    back = {"polygon": [[0, 4], [1.6, 2]], "height": 5, "floors": [3], "name": "x"}
    got = layouts.same_numbers(back, old)
    assert got == {"polygon": [[-0.0, 4.0], [1.6, 2.0]], "height": 5.0, "floors": [3.0], "name": "x"}
    assert str(got["polygon"][0][0]) == "-0.0" and isinstance(got["floors"][0], float)
