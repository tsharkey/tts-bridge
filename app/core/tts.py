"""
TTS access for the hub. Every tool shares one bridge listener, so TTS calls
are serialised with `lock`: hold it around any run of calls that must not
interleave with another request's (the TTS gateway, #17, replaces this).
"""

import threading

import tts_bridge

lock = threading.Lock()


def status():
    with lock:
        try:
            r = tts_bridge.run_lua('return getObjectFromGUID("738804") ~= nil and "lct" or "game"', timeout=3)
        except SystemExit:
            r = None
    return {"connected": r is not None, "lct": r == "lct"}
