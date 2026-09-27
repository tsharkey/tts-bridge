"""
tts_bridge.py — talk to a running Tabletop Simulator game from the command line.

Uses TTS's built-in "External Editor API":
  - TTS listens on 127.0.0.1:39999 for commands
  - TTS sends replies to a listener on 127.0.0.1:39998 (that's us)

Requires: TTS running, a game loaded/hosted, and External Editor API
enabled (Options -> General in TTS).

Usage:
    python tts_bridge.py state                 # dump the whole board to tts_state.json
    python tts_bridge.py run "<lua code>"       # run arbitrary Lua, print result

Only one process can listen on 39998. When the web app (app/server.py) has
it, the tools here send their Lua through the app instead (its
/api/tts/lua), so both can run at once.
"""

import json
import queue
import socket
import sys
import threading
import urllib.error
import urllib.request
import uuid

TTS_HOST = "127.0.0.1"
TTS_SEND_PORT = 39999   # TTS listens here
LISTEN_PORT = 39998     # we listen here
HUB_PORT = 8765         # the web app, which forwards Lua for us when it holds LISTEN_PORT

hub = None              # the web app's URL while we forward through it, else None
waiting = {}            # reply id -> queue for the run_lua call waiting on it
waiting_lock = threading.Lock()
listeners = []          # callables given everything TTS sends that isn't a reply


def start_listener():
    """Listen for TTS's replies, or, if the web app already does, forward through it."""
    global hub
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Forwarding relies on this bind failing while the hub holds the port. On Windows,
    # SO_REUSEADDR would let a second socket share it, so ask for the port exclusively there.
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((TTS_HOST, LISTEN_PORT))
    except OSError:
        srv.close()
        url = f"http://{TTS_HOST}:{HUB_PORT}"
        if not hub_answers(url):
            sys.exit(f"Port {LISTEN_PORT} is busy, and the web app isn't answering at {url}. "
                     "Is another tts_bridge.py, army.py, board.py or recreate.py still running?")
        hub = url
        return
    hub = None
    srv.listen(5)
    threading.Thread(target=listener_thread, args=(srv,), daemon=True).start()


def hub_answers(url):
    try:
        with urllib.request.urlopen(f"{url}/api/tts", timeout=2) as r:
            return json.load(r).get("gateway") is True
    except (OSError, ValueError):
        return False


def listener_thread(srv):
    # TTS connects once per message, sends JSON and closes
    while True:
        conn, _ = srv.accept()
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        conn.close()
        if not data:
            continue
        try:
            msg = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            msg = {"raw": data.decode("utf-8", errors="replace")}
        dispatch(msg)


def dispatch(msg):
    """A reply goes to the call waiting on it; anything else to the listeners
    (or is printed, when nothing is listening)."""
    custom = msg.get("customMessage")
    reply_id = custom.get("reply") if msg.get("messageID") == 4 and isinstance(custom, dict) else None
    if reply_id is not None:
        with waiting_lock:
            q = waiting.get(reply_id)
        if q:
            q.put(custom)
        return  # a reply nobody waits for any more (it timed out)
    for listener in list(listeners) or [handle_passive]:
        listener(msg)


def send_message(msg: dict):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect((TTS_HOST, TTS_SEND_PORT))
    except ConnectionRefusedError:
        sys.exit("TTS isn't accepting commands on port 39999. Is it running "
                 "with a game loaded?")
    s.sendall(json.dumps(msg).encode("utf-8"))
    s.close()


def handle_passive(msg):
    mid = msg.get("messageID")
    if mid == 2:
        print("[TTS print]", msg.get("message"))
    elif mid == 3:
        print("[TTS error]", msg.get("error"))
    elif mid == 6:
        print("[TTS] game saved")
    elif mid == 7:
        print("[TTS] object created:", msg.get("guid"))


GET_STATE_LUA = """
local objs = {}
for _, obj in ipairs(getObjects()) do
    local p = obj.getPosition()
    local r = obj.getRotation()
    table.insert(objs, {
        guid = obj.guid,
        name = obj.getName(),
        description = obj.getDescription(),
        tag = obj.tag,
        locked = obj.getLock(),
        pos = {p.x, p.y, p.z},
        rot = {r.x, r.y, r.z},
    })
end
return JSON.encode(objs)
"""

# TTS doesn't send back a script's return value (messageID 5) for global
# execution, so wrap the script and ship the result via sendExternalMessage,
# which arrives as messageID 4. The reply id keeps it apart from anything else
# a mod sends on the same channel.
LUA_WRAPPER = """
local __ok, __r = pcall(function()
{script}
end)
if type(__r) == "table" then __r = JSON.encode(__r) end
if not __ok then __r = tostring(__r) end
sendExternalMessage({{reply = "{reply_id}", ok = __ok, result = __r}})
"""


NO_RESPONSE = ("No response from TTS. Is it running with a game loaded, "
               "and is the External Editor API enabled?")


def execute(script: str, timeout=10):
    """Run Lua in the game -> {"ok": True, "result": ...} or {"ok": False, "error": ...}.
    Exits (SystemExit) if TTS isn't reachable. Safe to call from several threads at once."""
    if hub:
        return forward(script, timeout)
    reply_id = uuid.uuid4().hex
    q = queue.Queue(1)
    with waiting_lock:
        waiting[reply_id] = q
    try:
        send_message({"messageID": 3, "guid": "-1",
                      "script": LUA_WRAPPER.format(script=script, reply_id=reply_id)})
        payload = q.get(timeout=timeout)
    except queue.Empty:
        return {"ok": False, "error": NO_RESPONSE}
    finally:
        with waiting_lock:
            waiting.pop(reply_id, None)
    if not payload.get("ok"):
        return {"ok": False, "error": f"Lua error: {payload.get('result')}"}
    return {"ok": True, "result": payload.get("result")}


def forward(script, timeout):
    """execute() through the web app, which holds the listener port."""
    req = urllib.request.Request(f"{hub}/api/tts/lua", method="POST",
                                 data=json.dumps({"script": script, "timeout": timeout}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            error = json.load(e).get("error")
        except ValueError:
            error = None
        if e.code == 503:
            sys.exit(error or "TTS isn't responding")
        return {"ok": False, "error": error or f"The web app answered {e.code}"}
    except OSError as e:
        sys.exit(f"Lost the web app at {hub} ({e}). Is it still running?")


def run_lua(script: str, timeout=10):
    """Run Lua in the game and return its result, or print why not and return None."""
    answer = execute(script, timeout)
    if not answer["ok"]:
        print(answer["error"])
        return None
    return answer["result"]


def lua_str(s: str) -> str:
    """Quote a Python string as a Lua long-bracket literal."""
    level = 0
    while f"]{'=' * level}]" in s:
        level += 1
    eq = "=" * level
    return f"[{eq}[\n{s}]{eq}]"


def cmd_state(out_path="tts_state.json"):
    raw = run_lua(GET_STATE_LUA)
    if raw is None:
        return
    try:
        objs = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        objs = raw
    with open(out_path, "w") as f:
        json.dump(objs, f, indent=2)
    n = len(objs) if isinstance(objs, list) else "?"
    print(f"Saved {n} objects to {out_path}")


def cmd_run(lua_code):
    result = run_lua(lua_code)
    print("Result:", result)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    start_listener()
    cmd = sys.argv[1]
    if cmd == "state":
        cmd_state()
    elif cmd == "run" and len(sys.argv) == 3:
        cmd_run(sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
