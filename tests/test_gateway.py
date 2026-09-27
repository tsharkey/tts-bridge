"""The TTS gateway: the hub owns the reply port and forwards Lua for everyone
else. A fake TTS stands in for the game: it answers the bridge's wrapped
scripts the way TTS does (a new connection per message to the reply port)."""

import asyncio
import json
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

import tts_bridge
from app import server
from app.core import tts

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def send(port, msg):
    with socket.create_connection(("127.0.0.1", port)) as s:
        s.sendall(json.dumps(msg).encode())


class FakeTTS:
    """Replies to `return <word>` with the word. `-- slow` delays the reply,
    `-- silent` never replies, `error` fails, `print("x")` also sends a print."""

    def __init__(self, reply_port):
        self.reply_port = reply_port
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            conn, _ = self.srv.accept()
            with conn:
                data = b""
                while chunk := conn.recv(4096):
                    data += chunk
            threading.Thread(target=self.answer, args=(json.loads(data)["script"],), daemon=True).start()

    def answer(self, script):
        reply_id = re.search(r'reply = "(\w+)"', script)[1]
        if "-- silent" in script:
            return
        if "-- slow" in script:
            time.sleep(0.5)
        if printed := re.search(r'print\("(.*?)"\)', script):
            send(self.reply_port, {"messageID": 2, "message": printed[1]})
        ok = "error" not in script
        result = re.search(r"return (\w+)", script)[1] if ok else "boom"
        send(self.reply_port, {"messageID": 4, "customMessage": {"reply": reply_id, "ok": ok, "result": result}})


@pytest.fixture
def game(monkeypatch):
    """A fake TTS, and this process's bridge listening for it on a free port."""
    monkeypatch.setattr(tts_bridge, "LISTEN_PORT", free_port())
    monkeypatch.setattr(tts_bridge, "HUB_PORT", free_port())
    monkeypatch.setattr(tts_bridge, "listeners", [])
    fake = FakeTTS(tts_bridge.LISTEN_PORT)
    monkeypatch.setattr(tts_bridge, "TTS_SEND_PORT", fake.port)
    tts_bridge.start_listener()
    assert tts_bridge.hub is None
    return fake


def test_run_lua(game):
    assert tts_bridge.run_lua("return two") == "two"


def test_lua_error_and_timeout(game, capsys):
    assert tts_bridge.execute("error()") == {"ok": False, "error": "Lua error: boom"}
    assert tts_bridge.run_lua("return x -- silent", timeout=0.3) is None
    assert "No response from TTS" in capsys.readouterr().out
    assert not tts_bridge.waiting


def test_a_duplicate_reply_doesnt_block_the_listener(game):
    """The first reply is delivered; a second with the same id is dropped, not queued behind it (#85)."""
    import queue
    q = queue.Queue(1)
    with tts_bridge.waiting_lock:
        tts_bridge.waiting["dup"] = q
    for _ in range(2):
        send(tts_bridge.LISTEN_PORT, {"messageID": 4, "customMessage": {"reply": "dup", "ok": True, "result": 1}})
    assert q.get(timeout=2) == {"reply": "dup", "ok": True, "result": 1}
    assert tts_bridge.run_lua("return still") == "still"
    assert "dup" not in tts_bridge.waiting


def test_a_reply_that_lands_as_the_call_times_out_is_kept(game, monkeypatch):
    """If the reply arrives between the wait timing out and the waiter being removed, use it (#85)."""
    import queue
    real_get = queue.Queue.get

    def get(self, block=True, timeout=None):
        if block and timeout:   # the timed wait: time out, but the reply is already on its way
            self.put_nowait({"reply": "x", "ok": True, "result": "late"})
            raise queue.Empty
        return real_get(self, block, timeout)
    monkeypatch.setattr(queue.Queue, "get", get)
    assert tts_bridge.execute("return x -- silent", timeout=0.1) == {"ok": True, "result": "late"}
    assert not tts_bridge.waiting


def test_concurrent_calls_each_get_their_own_reply(game):
    client = TestClient(server.create_app())
    results = {}

    def call(word, extra=""):
        results[word] = client.post("/api/tts/lua", json={"script": f"return {word} {extra}"}).json()

    slow = threading.Thread(target=call, args=("first", "-- slow"))
    slow.start()
    time.sleep(0.1)
    call("second")   # answered while the first still waits
    assert results == {"second": {"ok": True, "result": "second"}}
    slow.join()
    assert results["first"] == {"ok": True, "result": "first"}


def test_bad_messages_dont_stop_the_listener(game):
    """Every call depends on the listener thread, so nothing one message holds may end it (#84)."""
    for junk in (b"\xff\xfe", b"[1, 2]", b"42", b"not json"):
        with socket.create_connection(("127.0.0.1", tts_bridge.LISTEN_PORT)) as s:
            s.sendall(junk)
    assert tts_bridge.run_lua("return still") == "still"


def test_a_failing_listener_doesnt_stop_the_others(game, monkeypatch):
    got = []

    def broken(msg):
        raise UnicodeEncodeError("charmap", "x", 0, 1, "can't encode")
    monkeypatch.setattr(tts_bridge, "listeners", [broken, got.append])
    send(tts_bridge.LISTEN_PORT, {"messageID": 2, "message": "one"})
    send(tts_bridge.LISTEN_PORT, {"messageID": 2, "message": "two"})
    assert tts_bridge.run_lua("return still") == "still"   # replies still get through, after both prints
    assert [m["message"] for m in got] == ["one", "two"]


def test_events_stream_what_tts_prints(game):
    async def read():
        stream = tts.events(disconnected=lambda: asyncio.sleep(0, False))
        assert (await anext(stream)).startswith(":")
        threading.Thread(target=tts_bridge.run_lua, args=('print("hello") return 1',), daemon=True).start()
        event = await asyncio.wait_for(anext(stream), 5)
        await stream.aclose()
        return event

    event = asyncio.run(read())
    assert json.loads(event.removeprefix("data: ")) == {"messageID": 2, "message": "hello"}
    assert not tts_bridge.listeners


def test_timeouts_that_would_hang_are_refused_or_clamped(game, monkeypatch):
    """NaN and inf would make a call wait for ever, holding a hub worker thread (#86)."""
    client = TestClient(server.create_app())
    r = client.post("/api/tts/lua", content='{"script": "return x -- silent", "timeout": NaN}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and "timeout" in r.json()["error"]
    assert not tts_bridge.waiting
    with pytest.raises(ValueError):
        tts_bridge.execute("return x", float("inf"))
    seen = []
    monkeypatch.setattr(tts_bridge, "execute", lambda script, timeout: seen.append(timeout) or {"ok": True, "result": 1})
    for asked in (1e9, 0, -5, 3):
        client.post("/api/tts/lua", json={"script": "return 1", "timeout": asked})
    assert seen == [600, 0.1, 0.1, 3]


def test_cli_forwards_through_the_hub(game):
    """With the hub holding the reply port, a CLI tool in its own process
    binds nothing and still gets its answer."""
    hub = uvicorn.Server(uvicorn.Config(server.create_app(server.HOSTS), host="127.0.0.1",
                                        port=tts_bridge.HUB_PORT, log_level="warning"))
    threading.Thread(target=hub.run, daemon=True).start()
    while not hub.started:
        time.sleep(0.05)
    try:
        cli = subprocess.run([sys.executable, "-c", f"""
import tts_bridge as t
t.LISTEN_PORT, t.HUB_PORT = {tts_bridge.LISTEN_PORT}, {tts_bridge.HUB_PORT}
t.start_listener()
print(t.hub)
print(t.run_lua("return forwarded"))
t.run_lua("error()")
"""], cwd=ROOT, capture_output=True, text=True, timeout=30)
    finally:
        hub.should_exit = True
    assert cli.returncode == 0, cli.stderr
    assert cli.stdout.split("\n")[:3] == [f"http://127.0.0.1:{tts_bridge.HUB_PORT}", "forwarded", "Lua error: boom"]


def test_busy_port_without_the_hub(monkeypatch):
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        monkeypatch.setattr(tts_bridge, "LISTEN_PORT", other.getsockname()[1])
        monkeypatch.setattr(tts_bridge, "HUB_PORT", free_port())
        with pytest.raises(SystemExit, match="busy"):
            tts_bridge.start_listener()


def test_only_local_host_names():
    """A page on another site can't reach the gateway by DNS rebinding."""
    app = server.create_app(server.HOSTS)
    assert TestClient(app).get("/api/tts").status_code == 400
    assert TestClient(app, base_url="http://127.0.0.1").get("/api/tts").json() == {"gateway": True}
