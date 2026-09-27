# Board state format

The table as it is right now, read from TTS: each army's units and where they stand, and the terrain.

- **Produced by** `board.board_state(objs)` from what `board.READ_LUA` reads; `python3 board.py summary`
  writes it to `board.json` (git-ignored).
- **Read by** the deployment skill (it reads `board.json`), and (planned) the board view (#26) and the MCP
  board summary (#39).
- **Sample:** [`tests/fixtures/formats/board-state.json`](../../tests/fixtures/formats/board-state.json), made
  by `board_state` from a small made-up table; `tests/test_formats.py` checks it still matches.

```jsonc
{
  "surface_y": 1.0,                 // height of the playing surface in TTS (the mat's top); heights below are above it
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
      "x": 5.5, "z": 17.0,          // the models' mean position
      "box": [4.0, 7.0, 17, 17],    // [xmin, xmax, zmin, zmax] of the model centres
      "facing": 180,                // the first model's facing, degrees (0 = +z, 90 = +x)
      "touching": ["Ruin (large)"], // terrain whose box a model's base overlaps
      "zones": ["Red deployment zone"],   // scripting zones a model's centre is in
      "guids": ["a1b2c0", "a1b2c1", "a1b2c2"]
    }
  ],
  "terrain": [
    {
      "name": "Ruin (large)",       // the object's name, or its TTS type when it has none
      "guid": "dc9993",
      "kind": "terrain",            // terrain (locked objects that aren't models) | zone (scripting zones)
      "x": 5, "z": 17,              // centre of its bounds
      "w": 9.2, "d": 8.1,           // width along x, depth along z
      "height": 5.0,                // top above the playing surface
      "box": [0.4, 9.6, 12.95, 21.05]   // [xmin, xmax, zmin, zmax]
    }
  ]
}
```

## Invariants

- Table inches, 0,0 at the centre, x along the 60" edge (−30…30), z along the 44" edge (−22…22).
- `nth` is counted the same way everywhere (vision, scenes, `board.py place --nth`): see AGENTS.md.
- Terrain boxes come from TTS's `getBounds()`, so they're axis-aligned: a ruin turned 45° comes out too big, and
  there's no category. Use [layout terrain](layout-terrain.md) where exact footprints matter.
- Per-model positions aren't in the file yet (only `guids`); the MCP board summary (#39) adds them as a new field.
  Add fields; don't rename or remove them.
