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

Only one process can listen on 39998 at a time, so stop the web app
(app/server.py) before running these.
"""

import socket
import json
import threading
import queue
import sys
import time
import uuid

TTS_HOST = "127.0.0.1"
TTS_SEND_PORT = 39999   # TTS listens here
LISTEN_PORT = 39998     # we listen here

incoming = queue.Queue()


def start_listener():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((TTS_HOST, LISTEN_PORT))
    except OSError:
        sys.exit(f"Port {LISTEN_PORT} is busy. Is the web app (app/server.py) "
                 "already running? Stop it first.")
    srv.listen(5)
    threading.Thread(target=listener_thread, args=(srv,), daemon=True).start()


def listener_thread(srv):
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
        incoming.put(msg)


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


def wait_for(match, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        try:
            msg = incoming.get(timeout=max(0.1, end - time.time()))
        except queue.Empty:
            break
        if match(msg):
            return msg
        handle_passive(msg)
    return None


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


def run_lua(script: str, timeout=10):
    reply_id = uuid.uuid4().hex
    wrapped = LUA_WRAPPER.format(script=script, reply_id=reply_id)
    send_message({"messageID": 3, "guid": "-1", "script": wrapped})
    answer = wait_for(lambda m: m.get("messageID") == 4
                      and m.get("customMessage", {}).get("reply") == reply_id,
                      timeout=timeout)
    if not answer:
        print("No response from TTS. Is it running with a game loaded, "
              "and is the External Editor API enabled?")
        return None
    payload = answer["customMessage"]
    if not payload.get("ok"):
        print("[Lua error]", payload.get("result"))
        return None
    return payload.get("result")


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
