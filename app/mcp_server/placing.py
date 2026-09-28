"""place_unit and undo_place: set a unit up on the table where Claude recommends, with the
checks `board.py place` makes, and put it back. Only moves models tts-bridge spawned."""

from mcp.server.mcpserver.exceptions import ToolError
from typing_extensions import NotRequired, TypedDict   # see table.py

import board
import layouts
from app.core import tts
from app.mcp_server.table import Point, UnitRef


class Placement(TypedDict):
    unit: UnitRef
    x: float
    z: float
    facing: int
    moved: int                     # models moved; 0 when checking, or refused for problems
    problems: list[str]
    nearest_enemy: float | None
    touching: list[str]
    positions: list[Point]
    areas: NotRequired[list[str]]
    objectives: NotRequired[list[str]]
    zones: NotRequired[list[str]]


class Undone(TypedDict):
    moved: int
    left: int                      # earlier placements that can still be undone


def placing(objs, unit, x, z, facing=None, cols=None, army=None, nth=None, from_reserves=False,
            check_only=False, force=False, height=None, candidates=None, apply=board.apply_place) -> Placement:
    """place_unit on objects as board.READ_LUA returns them; `apply` moves the unit."""
    try:
        u = board.find_unit(board.collect_units(objs), unit, army, nth, on_table=not from_reserves)
        found = layouts.identify(board.collect_terrain(objs), candidates)
        y = board.DROP_Y if height is None else board.table_surface(objs) + height + 0.6
        plan = board.plan_place(objs, u, x, z, facing, cols, y, found[0] if found else None)
    except board.UnitError as e:
        raise ToolError(str(e)) from None
    moved = 0 if check_only or (plan["problems"] and not force) else int(apply(u, plan))
    return {**{k: v for k, v in plan.items() if k != "moves"}, "moved": moved}


def place_unit(unit: str, x: float, z: float, facing: float | None = None, cols: int | None = None,
               army: str | None = None, nth: int | None = None, from_reserves: bool = False,
               check_only: bool = False, force: bool = False, height: float | None = None) -> Placement:
    """Set up a unit on the Tabletop Simulator table: its models in rows of `cols` (default: a
    square-ish block), centred on x, z, the front row toward `facing` (degrees, 0 = +z, 90 = +x;
    default: as it faces now). Only moves models tts-bridge spawned.

    unit / army / nth: as in measure. from_reserves: take the unit from off the table (reserves)
    instead of moving one already on it. height: for an upper floor, its height above the table
    in inches (board_summary's feature "floors"); models are dropped onto it.

    Checks first: off the table, overlapping another model, within engagement range (2") of an
    enemy, and coherency. With any problem it doesn't move ("moved": 0) unless force is true;
    check_only never moves. Always call with check_only first when unsure, and show the user the
    problems.

    Returns the unit, "facing", "problems" (in words), "nearest_enemy" (inches, base to base),
    "touching" (terrain boxes a base would overlap), "positions" (where each model's base centre
    goes), "moved", and with an LCT layout on the table: the "areas" it would overlap, the
    "objectives" in them, and the deployment "zones" (red, blue) it would be wholly within.
    undo_place puts it back."""
    with tts.lock:
        return placing(board.read_objects(), unit, x, z, facing, cols, army, nth, from_reserves, check_only,
                       force, height)


def undo_place() -> Undone:
    """Put back the unit place_unit (or board.py place) last moved; call again for the one before.
    Returns how many models moved and how many earlier placements are left to undo."""
    with tts.lock:
        try:
            moved, left = board.undo()
        except board.UnitError as e:
            raise ToolError(str(e)) from None
    return {"moved": int(moved), "left": left}


TOOLS = [place_unit, undo_place]
