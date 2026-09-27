# Layout terrain format (draft)

**Draft:** nothing produces this yet. #20 will generate one file per LCT layout and may refine the shape; until
then, build against the sample.

Where terrain is and what kind it is, for one layout, exactly and offline: rotated footprints instead of the
axis-aligned boxes `board.py` gets from TTS's `getBounds()`.

- **Produced by** (planned, #20) a script that reads LCT's layouts from the data cache (`cache/lct/layouts/`,
  written by `python3 data.py mods lct`). The output is our own derived geometry, so it's committed as
  `layouts/<id>.json`; LCT's raw data isn't.
- **Read by** (planned) the LOS engine (#24), threat ranges (#25), the vision phantom checks (#5), the board
  view (#26) and the deployment skill.
- **Sample:** [`tests/fixtures/formats/layout.json`](../../tests/fixtures/formats/layout.json), checked by
  `tests/test_formats.py`.

```jsonc
{
  "version": 1,                     // bumped when the shape changes
  "id": "0c4960",                   // LCT's layout card GUID (the file is layouts/<id>.json)
  "name": "TnH vs TnH 2 - Dawn of War - BTTF",
  "map": "TnH vs TnH 2", "deployment": "Dawn of War", "pack": "BTTF",   // as in cache/lct/index.json
  "source": {"from": "lct", "lct_updated": "2026-09-17T16:29:16+00:00"},   // which LCT version it came from
  "areas": [
    {
      "id": "A1",                   // unique in the file; objectives refer to it
      "polygon": [[-5.83, -0.1], [2.83, -5.1], [5.83, 0.1], [-2.83, 5.1]],   // the terrain area's footprint
      "features": [
        {
          "id": "A1a",
          "name": "Ruin (large)",   // what the piece is, for people; not used by the rules
          "category": "dense",      // dense | light | exposed
          "polygon": [[-3.28, 0.95], [2.78, -2.55], [4.28, 0.05], [-1.78, 3.55]],
          "height": 5.5,            // top of the feature above the table
          "floors": [3.2]           // heights of floors models can stand on, above the ground floor; [] for none
        }
      ]
    }
  ],
  "objectives": [
    {"id": "central", "kind": "central", "side": null, "x": 0, "z": 0, "area": "A1"}
    // kind: home | expansion | central; side: red | blue (whose home or expansion), null for central
    // area: the terrain area the objective is, or null for a marker on open ground
  ],
  "zones": [
    {"side": "red", "polygon": [[-30, 10], [30, 10], [30, 22], [-30, 22]]}   // deployment zones
  ]
}
```

## Invariants

- Coordinates are table inches, 0,0 at the centre, x along the 60" edge (−30…30), z along the 44" edge
  (−22…22). Heights are inches above the table surface.
- A polygon is its corners in order, counter-clockwise seen from above (+x right, +z up), without repeating
  the first corner. Rectangles turned in TTS are four rotated corners, not a box and an angle, so any shape fits.
- Everything is inside the table. A feature's polygon is inside its area's.
- Categories drive the rules (see the terrain table in the deployment skill): an area with a light or dense
  feature is **Obscuring** and gives **Hidden**; a dense feature is **Solid**; a floor above 3" gives
  **Plunging Fire**. The maintainer's games treat every area as Obscuring, so the LOS engine should offer that
  as an option rather than the file saying so.
- Red is the player whose deployment zone is `side: "red"`; which edge that is comes from the deployment
  (`DEPLOYMENT_SIDES` in `app/tools/board_replay/vision.py`).
