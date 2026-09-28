"""line_of_sight: what a unit can see of one enemy unit, or of every enemy unit on the table,
from the LCT layout's terrain (los.py)."""

from mcp.server.mcpserver.exceptions import ToolError
from typing_extensions import NotRequired, TypedDict   # see table.py

import board
import layouts
import los
from app.mcp_server.table import UnitRef


class ModelSight(TypedDict):
    guid: str
    visible: str                   # full | partial | none
    seen_by: list[str]             # guids of the unit's models that see it
    blocked_by: list[str]          # terrain area and feature ids that blocked lines to it
    plunging_fire: bool
    beyond_detection: NotRequired[list[str]]


class TargetSight(TypedDict):
    target: UnitRef
    distance: float                # closest base to base, inches
    visible: int                   # models seen at all
    fully_visible: int
    plunging_fire: bool
    hidden_areas: list[str]        # light / dense areas every model is in: Hidden if INFANTRY, BEASTS or SWARM that didn't shoot
    treated_as_hidden: bool
    models_seen: NotRequired[list[ModelSight]]


class Sight(TypedDict):
    unit: UnitRef
    layout: str
    targets: list[TargetSight]


def seeing(objs, unit, target=None, army=None, nth=None, target_army=None, target_nth=None,
           hidden_targets=False, all_obscuring=False, candidates=None) -> Sight:
    """line_of_sight on objects as board.READ_LUA returns them."""
    units = board.collect_units(objs)
    try:
        u = board.find_unit(units, unit, army, nth)
        chosen = [board.find_unit(units, target, target_army, target_nth)] if target else None
    except board.UnitError as e:
        raise ToolError(str(e)) from None
    found = layouts.identify(board.collect_terrain(objs), candidates)
    if not found:
        raise ToolError("Line of sight needs the terrain of the LCT layout on the table, and none of layouts/ "
                        "matches it (board_summary's layout is null).")
    layout = found[0]
    if chosen and chosen[0] is u:
        raise ToolError(f'"{unit}" and "{target}" are the same unit.')
    targets = chosen or [t for t in units if t["on_table"] and t["army"] != u["army"]]
    terrain = los.blockers(layout, all_obscuring)
    surface = board.table_surface(objs)
    observers = [los.model(p) for p in board.positions(u["models"], surface)]
    rows = []
    for t in targets:
        models = [los.model(p) for p in board.positions(t["models"], surface)]
        areas = los.hidden_areas(models, layout)
        hidden = hidden_targets and bool(areas)
        seen = los.unit_visibility(observers, models, terrain, target_hidden=hidden)
        row = {"target": board.unit_ref(t), "distance": round(board.closest(u["models"], t["models"]), 2),
               "visible": seen["visible"], "fully_visible": seen["fully_visible"],
               "plunging_fire": seen["plunging_fire"], "hidden_areas": areas, "treated_as_hidden": hidden}
        if chosen:
            row["models_seen"] = seen["models"]
        rows.append(row)
    rows.sort(key=lambda r: (-r["visible"], -r["fully_visible"], r["distance"]))
    return {"unit": board.unit_ref(u), "layout": layout["id"], "targets": rows}


def line_of_sight(unit: str, target: str | None = None, army: str | None = None, nth: int | None = None,
                  target_army: str | None = None, target_nth: int | None = None, hidden_targets: bool = False,
                  all_obscuring: bool = False) -> Sight:
    """What a unit on the table can see of an enemy unit ("target"), or with no target, of
    every enemy unit on the table (most visible first). Worked out from the LCT layout's
    terrain, so it needs one on the table (board_summary's "layout").

    unit / army / nth and target / target_army / target_nth: as in measure.

    Per target: "visible" and "fully_visible" (how many of its models the unit sees at all, and
    fully), "distance" (closest base to base), "plunging_fire" (a model seeing it is on a floor
    more than 3" up and it's at ground level), and "hidden_areas": the light or dense terrain
    areas every one of its models is in, so it's Hidden if it's INFANTRY, BEASTS or SWARM and
    didn't shoot this turn or last (every unit counts as not having shot on the first turn).
    With a named target, "models_seen" gives each of its models: "visible" (full, partial,
    none), "seen_by" (the unit's models that see it), "blocked_by" (terrain area / feature ids,
    as in board_summary's layout).

    hidden_targets: true treats targets with hidden_areas as Hidden, so only models within 15"
    see them (12" when Gone to Ground: not fully visible because of a dense feature);
    "beyond_detection" lists the models too far away. Use it when you know those targets'
    keywords and that they haven't shot. all_obscuring: every terrain area is Obscuring, not
    just those with a light or dense feature.

    Rules: an Obscuring area blocks lines through it unless either model is in it; a dense
    feature blocks lines at ground level (3" or lower). It works from footprints: features are
    their outlines from above (windows and doorways aren't modelled), bases are circles, and a
    line from or to a floor above 3" goes over dense features. Say so when a call is close."""
    return seeing(board.read_objects(), unit, target, army, nth, target_army, target_nth, hidden_targets,
                  all_obscuring)


TOOLS = [line_of_sight]
