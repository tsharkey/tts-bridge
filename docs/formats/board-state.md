# Board state format

The table as it is right now, read from TTS: each army's units and where they stand, and the terrain.

- **Produced by** `board.board_state(objs)` from what `board.READ_LUA` reads; `python3 board.py summary`
  writes it to `board.json` (git-ignored).
- **Read by** the deployment skill (it reads `board.json`), the MCP `board_summary` tool
  (`app/mcp_server/table.py`, which returns it with the layout's areas, objectives and zones filled in), and
  (planned) the board view (#26).
- **Sample:** [`tests/fixtures/formats/board-state.json`](../../tests/fixtures/formats/board-state.json), made
  by `board_state` from a small made-up table; `tests/test_formats.py` checks it still matches.

```jsonc
{
  "surface_y": 1.0,                 // height of the playing surface in TTS (the mat's top); heights below are above it
  "layout": {                       // which of layouts/ is on the table, or null (board.find_layout: terrain positions,
                                    // and the table's meshes against LCT's cache to tell a map's terrain packs apart)
    "id": "0c4960",                 // layouts/<id>.json has its exact areas, objectives and zones
    "name": "TnH vs TnH 2 - Dawn of War - BTTF",
    "matched": 6, "pieces": 6       // how many of its areas and features have a piece on the table
  },
  "units": [
    {
      "army": "army.py:Ret Cadre",  // the army tag from GM Notes (army.py:<list title> or recreate:<scene>:Red|Blue),
                                    // or "untagged"
      "unit": "Pathfinder Team",    // from the "[<unit name>]" first line of each model's description
      "nth": 1,                     // the nth unit of this name in this army (list order when tagged)
      "models": 3,
      "on_table": true,             // false: off the table (reserves, not deployed); on and off are separate rows
      "unit_id": 2,                 // tts-bridge:unit:<n> tag (its place in the list, from 1), or null
      "datasheet": "c8b1-…",        // tts-bridge:sheet:<id> tag, or null
      "coherency": [],              // what breaks coherency, in words; [] when fine or off the table
      "x": -18.5, "z": 13.0,        // the models' mean position
      "box": [-20.0, -17.0, 13, 13],   // [xmin, xmax, zmin, zmax] of the model centres
      "facing": 180,                // the first model's facing, degrees (0 = +z, 90 = +x)
      "touching": ["Generic"],      // terrain whose box a model's base overlaps
      "zones": ["Red deployment zone"],   // scripting zones a model's centre is in
      "guids": ["a1b2c0", "a1b2c1", "a1b2c2"],
      "positions": [                // every model, in the order of guids
        {"guid": "a1b2c0", "x": -20.0, "z": 13, "height": 0.0,   // base centre; height of the base's bottom above
         "facing": 180, "base": [1.26, 1.26]}                    // the surface (> 0 on a floor); footprint [w, d]
      ],
      "areas": ["A2"],              // only with a layout: its terrain areas a model's base overlaps (none off the table)
      "objectives": ["expansion-red"]   // only with a layout: its objectives in those areas
    }
  ],
  "terrain": [
    {
      "name": "Ruin (large)",       // the object's name; unnamed flat pieces (LCT's area mats) are "Terrain area",
                                    // with the objective LCT tags them as ("Terrain area (red home objective)");
                                    // anything else unnamed is its TTS type
      "guid": "t-A1a",
      "kind": "terrain",            // terrain (locked objects that aren't models) | zone (scripting zones)
      "x": 0.5, "z": 0.5,           // centre of its bounds
      "w": 7.6, "d": 6.1,           // width along x, depth along z
      "height": 5.5,                // top above the playing surface
      "box": [-3.28, 4.28, -2.55, 3.55]   // [xmin, xmax, zmin, zmax]
    }
  ]
}
```

## Invariants

- Table inches, 0,0 at the centre, x along the 60" edge (−30…30), z along the 44" edge (−22…22).
- `nth` is counted the same way everywhere (vision, scenes, `board.py place --nth`): see AGENTS.md.
- Terrain boxes come from TTS's `getBounds()`, so they're axis-aligned: a ruin turned 45° comes out too big, and
  there's no category. Use [layout terrain](layout-terrain.md) where exact footprints matter.
- Objectives count by terrain area: a unit holds an objective whose area it's in. A marker on open ground
  (`area: null` in the layout) isn't matched to units here.
- Add fields; don't rename or remove them.
