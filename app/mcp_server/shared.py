"""get_selection, highlight and clear_highlights: what the board view (tools/board/) and Claude
share. Claude reads the units the user clicked on the page, and draws on it (and, if asked, on
the TTS table) to show what it's talking about. The state lives in the hub (app/core/view.py)."""

import math

from mcp.server.mcpserver.exceptions import ToolError
from typing_extensions import NotRequired, TypedDict   # see table.py

import board
import overlays
from app.core import tts, view

KINDS = ("unit", "point", "circle", "line", "area")
UNIT_MARGIN = 0.5   # inches round a highlighted unit's bases


class Picked(TypedDict):
    army: str
    unit: str
    nth: int
    on_table: bool


class Selection(TypedDict):
    units: list[Picked]
    at: str | None            # when it was picked (UTC)
    page_open: bool           # the board view is open and polling now


class Shape(TypedDict):
    kind: str                           # unit | point | circle | line | area
    unit: NotRequired[str]              # unit: its name, as board_summary lists it
    army: NotRequired[str]
    nth: NotRequired[int]
    x: NotRequired[float]               # point, circle: the centre
    z: NotRequired[float]
    r: NotRequired[float]               # circle: its radius, inches
    points: NotRequired[list[list[float]]]   # line: 2 or more [x, z]; area: 3 or more
    color: NotRequired[str]             # red, blue, green, yellow, orange, purple, cyan, white, or #rrggbb
    text: NotRequired[str]              # a label drawn beside it


class Highlighted(TypedDict):
    label: str
    shapes: int
    on_table: int | None      # lines drawn on the TTS table, or null when not asked


class Cleared(TypedDict):
    cleared: list[str]
    table: int | None         # tts-bridge overlays removed from the table, or null when not asked


def resolve(shapes, objs=None):
    """Claude's shapes, checked, with units turned into the outline round their bases (what the
    page and the table draw). objs: the table, as board.READ_LUA reads it (for units)."""
    out, units = [], None
    for i, sh in enumerate(shapes):
        kind, what = sh.get("kind"), f"shape {i + 1}"
        base = {k: sh[k] for k in ("color", "text") if sh.get(k)}
        if kind == "unit":
            if not sh.get("unit"):
                raise ValueError(f"{what}: a unit shape needs the unit's name.")
            units = units if units is not None else board.collect_units(objs)
            u = board.find_unit(units, sh["unit"], sh.get("army"), sh.get("nth"))
            pos = board.positions(u["models"])
            circles = [(p["x"], p["z"], (p["base"][0] + p["base"][1]) / 4 + UNIT_MARGIN) for p in pos]
            top = max(p["z"] + r for (_, _, r), p in zip(circles, pos))
            out.append({"kind": "unit", **base, "text": sh.get("text") or u["name"] + (f" #{u['nth']}" if u["nth"] > 1 else ""),
                        "outline": [[[round(x, 2), round(z, 2)] for x, z in arc] for arc in overlays.ring_outline(circles)],
                        "x": round(sum(p["x"] for p in pos) / len(pos), 2), "z": round(top, 2)})
        elif kind in ("point", "circle"):
            if not all(isinstance(sh.get(k), (int, float)) and math.isfinite(sh[k]) for k in ("x", "z")):
                raise ValueError(f"{what}: a {kind} needs x and z.")
            if kind == "circle" and not (isinstance(sh.get("r"), (int, float)) and sh["r"] > 0):
                raise ValueError(f"{what}: a circle needs a radius r above 0.")
            out.append({"kind": kind, **base, "x": sh["x"], "z": sh["z"], **({"r": sh["r"]} if kind == "circle" else {})})
        elif kind in ("line", "area"):
            pts = sh.get("points") or []
            need = 2 if kind == "line" else 3
            if len(pts) < need or any(len(p) != 2 for p in pts):
                raise ValueError(f"{what}: a {kind} needs {need} or more [x, z] points.")
            out.append({"kind": kind, **base, "points": [[float(x), float(z)] for x, z in pts]})
        else:
            raise ValueError(f"{what}: kind is one of {', '.join(KINDS)}.")
    return out


def get_selection() -> Selection:
    """The units the user has selected in the board view (the hub's Board view page): the first
    is the one they clicked, a second one they shift-clicked. Use it when the user says "this
    unit" or "these two". "page_open" says whether the page is open now; an empty list means
    nothing is selected. Look the units up with board_summary for where they are."""
    return view.selection()


def highlight(shapes: list[Shape], label: str = "claude", note: str | None = None,
              on_table: bool = False) -> Highlighted:
    """Draw on the board view (the hub's Board view page) to show the user what you mean,
    replacing what you drew before under the same label: a unit ("unit", with "army" / "nth" as
    in measure), a spot ("point", x, z), a range ("circle", x, z, r), a path or lane ("line",
    points), or a region ("area", points). Coordinates are table inches. Give each a "color"
    and a short "text" when it helps; "note" is shown beside the drawing (e.g. "Drop 3:
    Pathfinders here, out of sight of the Riptide").

    on_table: also draw it on the TTS table for everyone playing, replacing whatever tts-bridge
    drew there before (show_on_table's lines too). Labels let you keep several drawings and
    clear them one at a time."""
    if not label:
        raise ToolError("A highlight needs a label.")
    try:
        with tts.lock:
            objs = board.read_objects() if on_table or any(s.get("kind") == "unit" for s in shapes) else None
            resolved = resolve(shapes, objs)
    except (ValueError, board.UnitError) as e:
        raise ToolError(str(e)) from None
    view.set_highlight(label, resolved, note)
    lines = None
    if on_table:
        try:
            with tts.lock:
                lines = overlays.draw(overlays.shape_lines(resolved, board.table_surface(objs) + overlays.LIFT))
        except ValueError as e:
            raise ToolError(f"Drawn on the board view, but not on the table. {e}") from None
    return {"label": label, "shapes": len(resolved), "on_table": lines}


def clear_highlights(label: str | None = None, on_table: bool = False) -> Cleared:
    """Remove what highlight drew on the board view: one label, or all of them. on_table: also
    remove tts-bridge's lines from the TTS table."""
    cleared = view.clear_highlights(label)
    table = None
    if on_table:
        try:
            with tts.lock:
                table = overlays.clear()
        except ValueError as e:
            raise ToolError(f"Cleared the board view ({', '.join(cleared) or 'nothing there'}), not the table. {e}") from None
    return {"cleared": cleared, "table": table}


TOOLS = [get_selection, highlight, clear_highlights]
