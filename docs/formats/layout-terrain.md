# Layout terrain format

Where terrain is and what kind it is, for one layout, exactly and offline: rotated footprints instead of the
axis-aligned boxes `board.py` gets from TTS's `getBounds()`.

- **Produced by** `python3 layouts.py build` ([`layouts.py`](../../layouts.py)), from LCT's layouts in the data
  cache (`cache/lct/layouts/`, written by `python3 data.py mods lct`) and the terrain meshes in TTS's download
  cache. The output is our own derived geometry, so it's committed as `layouts/<id>.json`, one per layout LCT
  offers for a matchup, with `layouts/index.json` listing them; LCT's raw data isn't. Read one with
  `layouts.load(id)`. `python3 layouts.py check` compares the layout loaded in TTS with its file.
- **Read by** `board.py` (`layouts.identify` names the layout on the table, and units get its areas and
  objectives), the MCP `board_summary` tool and the LOS engine (`los.py`); (planned) threat ranges (#25),
  the vision phantom checks (#5), the board view (#26) and the deployment skill.
- **Sample:** [`tests/fixtures/formats/layout.json`](../../tests/fixtures/formats/layout.json), checked by
  `tests/test_formats.py`; `tests/test_layouts.py` checks every file in `layouts/` the same way.

```jsonc
{
  "version": 1,                     // bumped when the shape changes
  "id": "0c4960",                   // LCT's layout card GUID (the file is layouts/<id>.json)
  "name": "TnH vs TnH 2 - Dawn of War - BTTF",
  "map": "TnH vs TnH 2", "deployment": "Dawn of War", "pack": "BTTF",   // as in cache/lct/index.json
  "source": {"from": "lct", "lct_updated": "2026-09-17T16:29:16+00:00",   // which LCT version it came from
             "edited": "2026-09-28T01:49:46+00:00"},   // only when saved by hand (the layouts tool): imports leave it alone
  "areas": [
    {
      "id": "A1",                   // unique in the file; objectives refer to it
      "polygon": [[-5.83, -0.1], [2.83, -5.1], [5.83, 0.1], [-2.83, 5.1]],   // the terrain area's footprint
      "features": [
        {
          "id": "A1a",
          "name": "Ruin (large)",   // LCT's name for the piece (its nickname or tags), for people; not used by the rules
          "category": "dense",      // dense | light | exposed
          "polygon": [[-3.28, 0.95], [2.78, -2.55], [4.28, 0.05], [-1.78, 3.55]],
          "height": 5.5,            // top of the feature above the table
          "floors": [3.2]           // heights of level surfaces models can stand on, 1" or more up; [] for none
        }
      ]
    }
  ],
  "objectives": [
    {"id": "central", "kind": "central", "side": null, "x": 0, "z": 0, "area": "A1"}
    // id: kind and side, numbered when there are several (central-1, central-2)
    // kind: home | expansion | central; side: red | blue (whose home, or the home an expansion is nearer), null for central
    // x, z: the middle of the objective's area; area: the terrain area the objective is (null for a marker on open ground)
  ],
  "zones": [
    {"side": "red", "polygon": [[-30, 10], [30, 10], [30, 22], [-30, 22]]}   // deployment zones
  ]
}
```

`layouts/index.json` lists every file (`id`, `name`, `map`, `deployment`, `pack`, `areas`, `problems`, and `edited`
when saved by hand), the LCT version they came from (`source`), what the build noticed per layout (`problems`), and
`retired`: layouts deleted with the layouts tool or `layouts.py delete` (`[{"id", "name", "retired"}]`), which
imports skip. `layouts.problems(layout)` checks a layout against the rules below.

## Invariants

- Coordinates are table inches, 0,0 at the centre, x along the 60" edge (−30…30), z along the 44" edge
  (−22…22). Heights are inches above the table surface.
- A polygon is its corners in order, counter-clockwise seen from above (+x right, +z up), without repeating
  the first corner. Rectangles turned in TTS are four rotated corners, not a box and an angle, so any shape fits.
- Everything is inside the table. A feature belongs to the area its middle is in, but can overhang it (LCT's
  barriers and pipes often do); the rules use the area. A feature in no area gets an area of its own.
- A feature's polygon is its **convex outline** seen from above, so an L-shaped ruin comes out as a triangle.
  Areas are LCT's mats, whose rugged edges are smoothed to within about 0.05 square inches.
- A feature is the whole piece, including the parts TTS holds as its child objects. `floors` are read from its
  meshes: level surfaces of 2 square inches or more, 1" or more up.
- Categories are LCT's: each feature's description says Dense or Light (a piece that is both, like T5S2's tower
  with walls, counts as dense). Categories drive the rules (see the terrain table in the deployment skill): an area with a light or dense
  feature is **Obscuring** and gives **Hidden**; a dense feature is **Solid**; a floor above 3" gives
  **Plunging Fire**. The maintainer's games treat every area as Obscuring, so the LOS engine should offer that
  as an option rather than the file saying so.
- Red is the player whose deployment zone is `side: "red"`; which edge that is comes from the deployment
  (`DEPLOYMENT_SIDES` in `app/tools/board_replay/vision.py`). The zones are LCT's 11th edition ones
  (`layouts.DEPLOYMENTS`).
