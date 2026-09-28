"""show_on_table and clear_table_overlays: draw a unit's line of sight and threat ranges on the
TTS table (overlays.py), and remove them. Also what a spawned model's right-click menu asks
the hub for (menu_request)."""

from mcp.server.mcpserver.exceptions import ToolError
from typing_extensions import TypedDict   # see table.py

import board
import overlays
import tts_bridge
from app.core import tts
from app.mcp_server import reach, table
from app.mcp_server.table import UnitRef

THREAT = ["move", "advance", "charge"]   # what the right-click menu's "Show threat range" draws


class Shown(TypedDict):
    unit: UnitRef
    shown: list[str]
    lines: int


class Cleared(TypedDict):
    removed: int


def showing(objs, show, unit=None, army=None, nth=None, guid=None, dice="max", candidates=None) -> Shown:
    """Draw `show` for a unit named (unit / army / nth) or holding the model `guid`."""
    st = table.summary(objs, candidates)
    units = board.collect_units(objs)
    if guid:
        u = next((u for u in units if any(o["guid"] == guid for o in u["models"])), None)
        if u is None:
            raise ValueError("That model isn't in a unit tts-bridge can read.")
    else:
        u = board.find_unit(units, unit, army, nth)
    i = next(i for i, r in enumerate(st["units"])
             if (r["army"], r["unit"], r["nth"], r["on_table"]) == (u["army"], u["name"], u["nth"], u["on_table"]))
    profile = reach.row_profile(st["units"][i])[0] if set(show) - {"los"} else None
    lines = overlays.show(st, i, show, profile, dice)
    return {"unit": board.unit_ref(u), "shown": list(show), "lines": lines}


def show_on_table(unit: str, show: list[str] | None = None, army: str | None = None, nth: int | None = None,
                  dice: str = "max") -> Shown:
    """Draw on the Tabletop Simulator table, for everyone playing to see, what a unit can see
    and how far it reaches, replacing whatever this tool drew before. Use it to show the user
    what you're talking about. unit / army / nth: as in measure.

    show: any of "los" (the outline of what the unit's middle model can see, and a line to each
    enemy model the unit sees: green fully, yellow partly), "move", "advance", "charge", and
    "shoot: <weapon>" for its ranged weapons (names as threat_ranges lists them); default
    ["los"]. Bands are rings round the whole unit at that reach from its bases' edges. dice:
    "max" or "avg" for advance and charge. Lines stay until clear_table_overlays or a save is
    loaded; only tts-bridge's own lines are touched."""
    if dice not in ("max", "avg"):
        raise ToolError('dice is "max" or "avg".')
    with tts.lock:
        try:
            return showing(board.read_objects(), show or ["los"], unit, army, nth, dice=dice)
        except (ValueError, board.UnitError) as e:
            raise ToolError(str(e)) from None


def clear_table_overlays() -> Cleared:
    """Remove the lines show_on_table (or a model's right-click menu) drew on the table."""
    with tts.lock:
        return {"removed": overlays.clear()}


def menu_request(message):
    """sendExternalMessage({ttsBridge = "overlay", guid, show = "threat" | "los" | "clear", color})
    from a spawned model's right-click menu (sheetviewer.py); run by the hub. Problems go to
    the player who asked, in TTS."""
    try:
        with tts.lock:
            if message.get("show") == "clear":
                overlays.clear()
                return
            show = THREAT if message.get("show") == "threat" else ["los"]
            showing(board.read_objects(), show, guid=message.get("guid"))
    except (ValueError, board.UnitError, SystemExit) as e:
        colour = message.get("color") or "White"
        try:
            tts_bridge.run_lua(f"broadcastToColor({tts_bridge.lua_str('tts-bridge: ' + str(e))}, "
                               f"{tts_bridge.lua_str(colour)}, {{1, 0.45, 0.45}}) return 1", timeout=5)
        except SystemExit:
            pass


TOOLS = [show_on_table, clear_table_overlays]
