"""Board view's API: the table as board_summary sees it, live from TTS or from board.json (for
working without TTS), and for the units the page selects: line of sight (los.py), threat bands
(threat.py), and the distance and sight between two units.

The page reads the state when it opens and on Refresh. With "Follow the table" ticked (off by
default: each read runs in TTS, and moving models while it reads makes them stutter) it also
polls GET /api/board/fingerprint (a cheap sum of where the armies' models stand) and reads the
state again when it changes: TTS sends no event when a model moves. Units are picked by their place in the last state read ("i"), with its version
("v"), so a query can't land on a different unit after a refresh.
"""

import json
import threading

import board
import los
import terrain
import threat
import tts_bridge
from app.core import view
from app.core.api import router as api_router
from app.core.tts import lock as tts_lock
from app.mcp_server import reach, table

router = api_router()

FINGERPRINT_LUA = """
local n, sx, sz, sr = 0, 0, 0, 0
for _, o in ipairs(getObjects()) do
  local g = o.getGMNotes()
  if g:find("army.py:", 1, true) == 1 or g:find("recreate:", 1, true) == 1 then
    local p = o.getPosition()
    n = n + 1
    sx = sx + math.floor(p.x * 20 + 0.5) * n
    sz = sz + math.floor(p.z * 20 + 0.5) * n
    sr = sr + math.floor(o.getRotation().y + 0.5) * n
  end
end
return n .. ":" .. sx .. ":" .. sz .. ":" .. sr
"""

last = {"state": None, "source": None, "v": 0}
last_lock = threading.Lock()


@router.get("/api/board/state")
def state(source: str = "live"):
    """The table: board_summary's data, plus "source", "v" (its version) and "fingerprint"."""
    if source == "file":
        if not board.BOARD_JSON.exists():
            raise ValueError("No board.json yet. Run `python3 board.py summary` with a game loaded, "
                             "or look at the live table.")
        st, mark = table.expand(json.loads(board.BOARD_JSON.read_text())), None
    else:
        mark = fingerprint()["fingerprint"]
        st = table.summary(board.read_objects())
    with last_lock:
        last.update(state=st, source=source, v=last["v"] + 1)
        return {**st, "source": source, "v": last["v"], "fingerprint": mark}


@router.post("/api/board/terrain")
def send_terrain():
    """Write the layout on the table onto tts-bridge's terrain object, for the models' line of
    sight in the game (terrain.py)."""
    with tts_lock:
        return terrain.send()


@router.get("/api/board/fingerprint")
def fingerprint():
    return {"fingerprint": tts_bridge.run_lua(FINGERPRINT_LUA, timeout=10)}


def picked(i, v):
    with last_lock:
        st = last["state"]
        if st is None or v != last["v"]:
            raise ValueError("The table has changed since this page read it; it's reading it again.")
        if not 0 <= i < len(st["units"]):
            raise ValueError(f"No unit {i} on the table.")
        return st, st["units"][i]


def models(row):
    return [los.model(p) for p in row["positions"]]


def terrain_of(st):
    if not st["layout"]:
        raise ValueError("Line of sight needs the terrain of the LCT layout on the table, and none of ours "
                         "matches it.")
    return los.blockers(st["layout"])


def ref(i, row):
    return {"i": i, "army": row["army"], "unit": row["unit"], "nth": row["nth"], "models": row["models"]}


@router.get("/api/board/sight")
def sight(i: int, v: int):
    """What unit i sees: each model's visibility polygon, and how much of each enemy unit."""
    st, row = picked(i, v)
    if not row["on_table"]:
        raise ValueError(f"{row['unit']} is off the table.")
    terrain, mine = terrain_of(st), models(row)
    sees = []
    for j, other in enumerate(st["units"]):
        if other["on_table"] and other["army"] != row["army"]:
            got = los.unit_visibility(mine, models(other), terrain)
            sees.append({**ref(j, other), "visible": got["visible"], "fully_visible": got["fully_visible"]})
    sees.sort(key=lambda s: (-s["visible"], s["unit"]))
    return {"polygons": [los.visibility_polygon(m, terrain) for m in mine], "sees": sees}


@router.get("/api/board/threat")
def threat_bands(i: int, v: int):
    """Unit i's threat bands, from its datasheet and the weapons its models carry (on the live
    table; board.json has no tooltips, so the datasheet's defaults)."""
    _, row = picked(i, v)
    p, source = reach.row_profile(row, live=last["source"] == "live")
    return {"move": p["move"], "weapons": p["weapons"], "weapons_from": source, "bands": threat.bands(p)}


@router.get("/api/board/pair")
def pair(a: int, b: int, v: int):
    """Units a and b: the closest distance between their bases, and how much each sees of the other."""
    st, ra = picked(a, v)
    _, rb = picked(b, v)
    gap = min(board.gap(p["x"], p["z"], p["base"], q["x"], q["z"], q["base"])
              for p in ra["positions"] for q in rb["positions"])
    out = {"a": ref(a, ra), "b": ref(b, rb), "distance": round(max(gap, 0.0), 2), "a_sees": None, "b_sees": None}
    if st["layout"] and ra["on_table"] and rb["on_table"]:
        terrain = los.blockers(st["layout"])
        for key, x, y in (("a_sees", ra, rb), ("b_sees", rb, ra)):
            got = los.unit_visibility(models(x), models(y), terrain)
            out[key] = {"visible": got["visible"], "fully_visible": got["fully_visible"], "of": y["models"]}
    return out


# --------------------------------------------------------------------------
# Shared with Claude (app/core/view.py, the MCP tools in app/mcp_server/shared.py)

@router.post("/api/board/selection")
def set_selection(body: dict):
    """The page's selected units: {"units": [{"army", "unit", "nth", "on_table"}]}."""
    view.select(body.get("units") or [])
    return view.local_selection()


@router.get("/api/board/selection")
def get_selection():
    return view.local_selection()


@router.get("/api/board/highlights")
def highlights():
    """What Claude has drawn, by label, and its version; the page polls it."""
    return view.page_polled()


@router.post("/api/board/highlights")
def set_highlights(body: dict):
    if not body.get("label"):
        raise ValueError("A highlight needs a label.")
    return {"v": view.local_set(body["label"], body.get("shapes") or [], body.get("note"))}


@router.delete("/api/board/highlights")
def clear_highlights(label: str | None = None):
    return {"cleared": view.local_clear(label)}
