"""
tts_bridge.py — talk to a running Tabletop Simulator game from the command line.

Uses TTS's built-in "External Editor API":
  - TTS listens on 127.0.0.1:39999 for commands
  - TTS sends replies to a listener on 127.0.0.1:39998 (that's us)

Requires: TTS running, a game loaded/hosted, and External Editor API
enabled (Options -> General in TTS).

Usage:
    python tts_bridge.py state                 # dump the whole board to tts_state.json
    python tts_bridge.py move <guid> x y z      # move one object
    python tts_bridge.py rotate <guid> x y z    # rotate one object
    python tts_bridge.py run "<lua code>"       # run arbitrary Lua, print result
    python tts_bridge.py panel                  # spawn/refresh the turn panel in TTS
    python tts_bridge.py listen                 # stay running and react to panel buttons

Only one bridge process can listen at a time, so stop `listen` before
running other commands.
"""

import socket
import json
import threading
import queue
import sys
import time
import uuid
from pathlib import Path

TTS_HOST = "127.0.0.1"
TTS_SEND_PORT = 39999   # TTS listens here
LISTEN_PORT = 39998     # we listen here

PANEL_NAME = "Turn Panel"
PANEL_LUA = Path(__file__).with_name("panel.lua")
EVENT_LOG = Path(__file__).with_name("events.jsonl")

incoming = queue.Queue()


def start_listener():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((TTS_HOST, LISTEN_PORT))
    except OSError:
        sys.exit(f"Port {LISTEN_PORT} is busy. Is the web app (app/server.py) or "
                 "`tts_bridge.py listen` already running? Stop it first.")
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
    elif mid == 4 and "event" in msg.get("customMessage", {}):
        print("[TTS event]", msg["customMessage"])
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
# which arrives as messageID 4. The reply id keeps it apart from panel events,
# which use the same channel.
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


def broadcast(text, color="White"):
    run_lua(f'broadcastToAll({lua_str(text)}, "{color}")')


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


def cmd_move(guid, x, y, z):
    lua = f'local o = getObjectFromGUID("{guid}") o.setPositionSmooth({{{x}, {y}, {z}}})'
    run_lua(lua)
    print(f"Moved {guid} to ({x}, {y}, {z})")


def cmd_rotate(guid, x, y, z):
    lua = f'local o = getObjectFromGUID("{guid}") o.setRotationSmooth({{{x}, {y}, {z}}})'
    run_lua(lua)
    print(f"Rotated {guid} to ({x}, {y}, {z})")


def cmd_run(lua_code):
    result = run_lua(lua_code)
    print("Result:", result)


def cmd_panel(x=-38, z=20):
    """Spawn the turn panel, or push the latest panel.lua to an existing one."""
    script = lua_str(PANEL_LUA.read_text())
    lua = f"""
local script = {script}
for _, o in ipairs(getObjects()) do
    if o.getName() == "{PANEL_NAME}" then
        o.setLuaScript(script)
        o.reload()
        return "updated " .. o.guid
    end
end
local o = spawnObjectData({{data = {{
    Name = "BlockSquare",
    Nickname = "{PANEL_NAME}",
    Transform = {{posX = {x}, posY = 1.2, posZ = {z}, rotX = 0, rotY = 0, rotZ = 0,
                  scaleX = 6, scaleY = 0.3, scaleZ = 4}},
    ColorDiffuse = {{r = 0.12, g = 0.12, b = 0.14}},
    Locked = true,
    LuaScript = script,
}}}})
return "spawned " .. o.guid
"""
    print("Panel", run_lua(lua))


def on_event(ev):
    """Called for every panel button press. This is where the AI will hook in."""
    with EVENT_LOG.open("a") as f:
        f.write(json.dumps({"t": time.time(), **ev}) + "\n")
    kind = ev.get("event")
    print(f"[event] {kind} by {ev.get('player')}: round {ev.get('round')}, "
          f"{ev.get('active')} {ev.get('phase')}")
    if kind == "ai_turn":
        broadcast(f"[Bridge] AI turn requested for round {ev.get('round')} "
                  "(no AI hooked up yet).", "Yellow")


def cmd_listen():
    print(f"Listening for TTS events on {LISTEN_PORT}. Ctrl+C to stop.")
    try:
        while True:
            msg = incoming.get()
            payload = msg.get("customMessage", {})
            if msg.get("messageID") == 4 and "event" in payload:
                on_event(payload)
            else:
                handle_passive(msg)
    except KeyboardInterrupt:
        print("\nStopped.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    start_listener()
    cmd = sys.argv[1]
    if cmd == "state":
        cmd_state()
    elif cmd == "move" and len(sys.argv) == 6:
        cmd_move(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif cmd == "rotate" and len(sys.argv) == 6:
        cmd_rotate(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif cmd == "run" and len(sys.argv) == 3:
        cmd_run(sys.argv[2])
    elif cmd == "panel":
        cmd_panel()
    elif cmd == "listen":
        cmd_listen()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
