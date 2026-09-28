"""
What the board view (tools/board/) and Claude share, held by the hub: the units selected on
the page, and the highlights Claude draws there.

    from app.core import view
    view.selection()                       # {"units": [...], "at": ..., "page_seen": ...}
    view.set_highlight("plan", shapes, note="Drop 3")   # shapes already on the table's coordinates
    view.clear_highlights("plan")          # or every label with None

Selection and highlights live in the hub process. The MCP server Claude Desktop starts runs
in its own process, so there (tts_bridge.hub is set) these functions call the hub's
/api/board/selection and /api/board/highlights instead, the way run_lua forwards Lua.
"""

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import tts_bridge

PAGE_FRESH = 10.0   # seconds: the board view polls every 1.5 s, so older than this means it's closed

lock = threading.Lock()
state = {"selection": {"units": [], "at": None}, "highlights": {}, "v": 0, "page_seen": 0.0}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hub(method, path, body=None):
    """Call the hub from another process (the stdio MCP server)."""
    req = urllib.request.Request(f"{tts_bridge.hub}{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            raise ValueError(json.load(e).get("error") or f"The web app answered {e.code}") from None
        except json.JSONDecodeError:
            raise ValueError(f"The web app answered {e.code}") from None
    except OSError as e:
        raise SystemExit(f"Can't reach the web app at {tts_bridge.hub} ({e}). Is it running?") from None


# --------------------------------------------------------------------------
# In the hub

def select(units):
    """The page's selection: [{"army", "unit", "nth", "on_table"}], first the unit it picked."""
    with lock:
        state["selection"] = {"units": [{k: u.get(k) for k in ("army", "unit", "nth", "on_table")} for u in units],
                              "at": now()}


def page_polled():
    with lock:
        state["page_seen"] = time.time()
        return {"v": state["v"], "highlights": state["highlights"]}


def local_selection():
    with lock:
        return {**state["selection"], "page_open": time.time() - state["page_seen"] < PAGE_FRESH}


def local_set(label, shapes, note=None):
    with lock:
        state["highlights"][label] = {"label": label, "shapes": shapes, "note": note, "at": now()}
        state["v"] += 1
        return state["v"]


def local_clear(label=None):
    with lock:
        gone = [label] if label in state["highlights"] else [] if label else list(state["highlights"])
        for k in gone:
            del state["highlights"][k]
        state["v"] += 1
        return gone


# --------------------------------------------------------------------------
# From anywhere

def selection():
    return hub("GET", "/api/board/selection") if tts_bridge.hub else local_selection()


def set_highlight(label, shapes, note=None):
    if tts_bridge.hub:
        return hub("POST", "/api/board/highlights", {"label": label, "shapes": shapes, "note": note})["v"]
    return local_set(label, shapes, note)


def clear_highlights(label=None):
    if tts_bridge.hub:
        return hub("DELETE", "/api/board/highlights" + (f"?label={urllib.request.quote(label)}" if label else ""))["cleared"]
    return local_clear(label)
