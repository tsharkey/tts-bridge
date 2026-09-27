"""
TTS access for the hub, which owns the bridge's listener port and forwards
Lua for everyone else (the CLI tools, and later the MCP server) through
/api/tts/lua. Each call gets its own reply, so concurrent calls don't need a
lock to be correct. Hold `lock` only around a run of calls that TTS must see
in order and uninterrupted by the hub's other requests (spawning an army,
setting up LCT).
"""

import asyncio
import json
import threading

import tts_bridge

lock = threading.Lock()


def status():
    try:
        r = tts_bridge.run_lua('return getObjectFromGUID("738804") ~= nil and "lct" or "game"', timeout=3)
    except SystemExit:
        r = None
    return {"connected": r is not None, "lct": r == "lct"}


async def events(disconnected, keepalive=15):
    """Server-sent events: everything TTS sends that isn't a reply (print,
    error, sendExternalMessage, object created, game saved), until
    `await disconnected()` is true."""
    loop = asyncio.get_running_loop()
    q = asyncio.Queue()

    def listener(msg):
        loop.call_soon_threadsafe(q.put_nowait, msg)

    tts_bridge.listeners.append(listener)
    try:
        yield ": connected\n\n"
        while not await disconnected():
            try:
                msg = await asyncio.wait_for(q.get(), keepalive)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(msg)}\n\n"
    finally:
        tts_bridge.listeners.remove(listener)
