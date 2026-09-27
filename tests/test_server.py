"""The hub starts and answers without TTS, and every tool is mounted."""

import pytest
from fastapi.testclient import TestClient

import tts_bridge
from app import server


@pytest.fixture
def client(monkeypatch):
    def no_tts(*args, **kwargs):
        raise SystemExit("TTS isn't accepting commands on port 39999.")
    monkeypatch.setattr(tts_bridge, "run_lua", no_tts)
    return TestClient(server.create_app())


def test_homepage(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "TTS Bridge" in r.text


def test_shared_assets(client):
    assert client.get("/shared.css").status_code == 200
    assert client.get("/shared.js").status_code == 200


def test_status_without_tts(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json() == {"connected": False, "lct": False}


def test_tts_errors_are_503(client):
    r = client.get("/api/lct/matchups")
    assert r.status_code == 503
    assert "39999" in r.json()["error"]


def test_every_tool_has_a_page(client):
    tools = client.get("/api/tools").json()
    assert tools
    for t in tools:
        assert t["name"] and t["description"]
        r = client.get(t["path"].rstrip("/") + "?path=//elsewhere.example", follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == t["path"]
        r = client.get(t["path"])
        assert r.status_code == 200, t["path"]
        assert "/shared.js" in r.text


def test_bad_request_is_400_with_message(client):
    r = client.post("/api/parse", json={})
    assert r.status_code == 400
    assert r.json()["error"]


def test_pages_are_rechecked(client):
    """So a browser never pairs a new page with an old shared.css."""
    for path in ("/", "/shared.css", "/shared.js", "/tools/data/"):
        assert client.get(path).headers["cache-control"] == "no-cache", path
