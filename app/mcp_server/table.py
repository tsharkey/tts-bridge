"""Tools on what's on the table. board_summary: board.py's board state, with the
layout's exact terrain areas, objectives and deployment zones when it's one of
layouts/. measure: how far one unit is from another, and from the objectives and zones."""

import math

from mcp.server.mcpserver.exceptions import ToolError
from typing_extensions import NotRequired, TypedDict   # typing has NotRequired from 3.11; pydantic wants this TypedDict before 3.12

import board
import layouts


class Position(TypedDict):
    guid: str
    x: float
    z: float
    height: float          # bottom of the base above the table surface (upper floors are > 0)
    facing: int
    base: list[float]      # [width along x, depth along z] of the model's footprint


class Unit(TypedDict):
    army: str
    unit: str
    nth: int
    models: int
    on_table: bool
    unit_id: int | None
    datasheet: str | None
    coherency: list[str]
    x: float
    z: float
    box: list[float]
    facing: int
    touching: list[str]
    zones: list[str]
    guids: list[str]
    positions: list[Position]
    areas: NotRequired[list[str]]
    objectives: NotRequired[list[str]]


class Feature(TypedDict):
    id: str
    name: str
    category: str          # dense | light | exposed
    polygon: list[list[float]]
    height: float
    floors: list[float]


class Area(TypedDict):
    id: str
    polygon: list[list[float]]
    features: list[Feature]


class Objective(TypedDict):
    id: str
    kind: str              # home | expansion | central
    side: str | None       # red | blue, null for central
    x: float
    z: float
    area: str | None


class Zone(TypedDict):
    side: str
    polygon: list[list[float]]


class Layout(TypedDict):
    id: str
    name: str
    map: str
    deployment: str
    pack: str | None
    matched: int
    pieces: int
    areas: list[Area]
    objectives: list[Objective]
    zones: list[Zone]


class Terrain(TypedDict):
    name: str
    guid: str
    kind: str              # terrain | zone
    x: float
    z: float
    w: float
    d: float
    height: float
    box: list[float]


class BoardSummary(TypedDict):
    surface_y: float
    layout: Layout | None
    units: list[Unit]
    terrain: list[Terrain]


def summary(objs, candidates=None) -> BoardSummary:
    """board_summary on objects as board.READ_LUA returns them."""
    state = board.board_state(objs, candidates)
    layout, terrain = None, state["terrain"]
    if state["layout"]:
        full = next(lo for lo in (layouts.load_all() if candidates is None else candidates)
                    if lo["id"] == state["layout"]["id"])
        layout = {**state["layout"], **{k: full[k] for k in ("map", "deployment", "pack", "areas", "objectives",
                                                             "zones")}}
        # the layout has these exactly; keep what it doesn't (scripting zones, anything added since)
        boxes = [layouts.bounds(p) for a in full["areas"] for p in [a["polygon"], *(f["polygon"] for f in a["features"])]]
        terrain = [t for t in terrain if t["kind"] == "zone"
                   or not (any(math.dist((t["x"], t["z"]), (x, z)) <= 1.0 for x, z, _, _ in boxes)
                           or any(layouts.inside((t["x"], t["z"]), a["polygon"]) for a in full["areas"]))]
    return {"surface_y": state["surface_y"], "layout": layout, "units": state["units"], "terrain": terrain}


def board_summary() -> BoardSummary:
    """Everything on the Tabletop Simulator table right now: each army's units, and the terrain.
    Call it first for any question about the game, and again after anything moves.

    units: one row per unit, per army ("army" is its tag: "army.py:<list title>" or
    "recreate:<scene>:Red|Blue"). A unit's models on and off the table (reserves) are separate
    rows with the same "nth" (the nth unit of that name in its army). "x"/"z" are the models'
    mean position, "box" [xmin, xmax, zmin, zmax] their centres' extent, "facing" the first
    model's. "positions" gives every model: centre, height of its base above the table (above
    0 on an upper floor), facing and base footprint [w, d]. "coherency" lists what breaks it, in
    words. "datasheet" is the datasheet id when the army was spawned by tts-bridge.

    layout: which LCT layout is on the table (null if none of ours matches), with its exact
    terrain: "areas" (terrain area footprints, each with its "features": category dense, light or
    exposed, height, and "floors", the heights models can stand on), "objectives" (home,
    expansion, central; "area" is the terrain area the objective is, null for a marker on open
    ground) and deployment "zones" (red and blue). With a layout, each on-table unit also has
    "areas" its bases overlap and the "objectives" in those areas.

    terrain: what TTS reports beyond that, as axis-aligned boxes: scripting zones, and terrain
    pieces the layout doesn't have (every piece when there's no layout). "height" is the top
    above the table surface.

    Polygons are [x, z] corners, counter-clockwise from above. Distances in inches."""
    return summary(board.read_objects())


class UnitRef(TypedDict):
    army: str
    unit: str
    nth: int
    models: int


class Point(TypedDict):
    guid: str
    x: float
    z: float


class Landmark(TypedDict):
    unit: str              # a | b: whose distance this is
    kind: str              # objective | zone
    id: str                # the objective's id, or the zone's side (red | blue)
    distance: float
    within: bool | None


class Measurement(TypedDict):
    a: UnitRef
    b: UnitRef | None
    distance: float | None
    closest: list[Point] | None
    engagement_range: bool | None
    layout: str | None
    landmarks: list[Landmark]


def measuring(objs, unit, to=None, army=None, nth=None, to_army=None, to_nth=None, landmarks=False,
              candidates=None) -> Measurement:
    """measure on objects as board.READ_LUA returns them."""
    units = board.collect_units(objs)
    try:
        a = board.find_unit(units, unit, army, nth)
        b = board.find_unit(units, to, to_army, to_nth) if to else None
    except board.UnitError as e:
        raise ToolError(str(e)) from None
    found = layouts.identify(board.collect_terrain(objs), candidates) if landmarks else None
    return board.measure(a, b, found[0] if found else None)


def measure(unit: str, to: str | None = None, army: str | None = None, nth: int | None = None,
            to_army: str | None = None, to_nth: int | None = None, landmarks: bool = False) -> Measurement:
    """How far a unit on the table is from another: the closest base-to-base distance in inches,
    measured horizontally (round bases as circles, long bases and hulls as boxes), as the rules
    measure between units.

    unit / to: unit names as board_summary lists them (part of a name works when it's unique).
    army / to_army: which army, when both have one: the last part of its tag ("Red", "Blue", or
    the list title), or any part of it. nth / to_nth: which of several same-named units in that
    army (board_summary's "nth"). A name that matches no unit, or several, comes back as an
    error listing the matches. Leave out `to` to measure only landmarks.

    Returns "a" and "b" (the units measured), "distance", "closest" (the two models it's
    between: a's, then b's), and "engagement_range" (distance 2" or less; engagement also depends
    on height and which army, so check those).

    landmarks: true adds, for each unit, its distance to every objective and deployment zone of
    the LCT layout on the table ("layout" is its id, null if none matched, and then there are no
    landmarks). An objective's "within" is true when a base overlaps its terrain area; null for a
    marker on open ground, whose range the mission sets (distance is then to the marker's centre).
    A zone's "within" is true when every base is wholly within it; distance 0 means a base is in it."""
    return measuring(board.read_objects(), unit, to, army, nth, to_army, to_nth, landmarks)


TOOLS = [board_summary, measure]
