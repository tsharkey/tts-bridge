"""board_summary: what's on the table, as data: board.py's board state, with the
layout's exact terrain areas, objectives and deployment zones when it's one of layouts/."""

import math
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


TOOLS = [board_summary]
