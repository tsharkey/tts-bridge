# Parsed list format

What every tool starts from: an army list export, parsed.

- **Produced by** `datasheets.parse(text, mappings)`: `army.parse_list` (what the list says), then the
  datasheet cache (datasheets matched, compositions filled in) when it's there. `bases.attach(parsed, mappings)`
  then adds base sizes. `army.resolve` adds each model's Force Org pick.
- **Read by** `army.py plan/build`, `recreate.py`, the hub (`app/core/lists.py`), and the vision roster.
- **Samples:** the lists in [`tests/fixtures/`](../../tests/fixtures/); `tests/test_parse_list.py` and
  `tests/test_datasheet_matching.py` show what they parse to.

```jsonc
{
  "format": "gw",                  // gw | tournament ("+++") | nr | simple | short (New Recruit)
  "title": "2k Ret Cadre v4",      // Saved Objects and army tags are named after it
  "faction": "Space Marines",      // an army.FACTIONS name
  "sub": "Raven Guard",            // chapter, or null
  "detachment": "Retaliation Cadre", "disposition": "Purge the Foe", "battle_size": "Strike Force",
  "points": 2005,                  // null where the list doesn't say
  "units": [
    {
      "name": "Librarian in Terminator Armour",
      "points": 110,
      "allied": false,
      "label": "Char1",            // the tournament format's CharN, else null
      "role": "leader",            // leader | bodyguard | support (from "Attached as:"), else null
      "attached_to": 1,            // index in "units" of the bodyguard a leader or support is attached to
                                   // (army.attach_leaders applies a saved list's own choices over the list's)
      "can_lead": [1, 4],          // units it can be attached to, from its datasheet's Leader ability (datasheets)
      "enhancements": ["Temporal Corridor"],
      "warlord": false,
      "complete": true,            // false for simple / short exports, which leave models out
      "composition": "datasheet",  // only when the models were filled in from the datasheet
      "priced": [],                // New Recruit: costed options not yet told apart (datasheets.parse empties it)
      "datasheet": {"id": "…", "name": "…", "catalogue": "…", "how": "exact"},   // see datasheet.md
      "card": "…",                 // the whole datasheet as TTS BBCode, for the viewer on its models (tooltips.attach)
      "models": [
        {
          "name": "Terminator",
          "wargear": ["Power fist", "Storm bolter"],        // names, once each, no drones: pins are keyed on this
          "gear": [{"name": "Power fist", "count": 1},      // everything carried, with counts: drones, and lines
                                                            // that lost their bullet, which aren't in wargear
                   {"name": "Storm bolter", "count": 1}],
          "sheet_model": "Terminator w/ Power Fist",        // from datasheets (see datasheet.md)
          "sheet_wargear": ["Power fist", "Storm bolter"],
          "base": {"shape": "round", "mm": [40], "inches": [1.57], "…": "…"},   // from bases.attach
          "pick": "1e84c2:12",                              // from army.resolve
          "tooltip": {"name": "Terminator", "text": "[b]M[/b] 5\"  …"}   // from tooltips.attach (TTS BBCode)
        }
      ]
    }
  ]
}
```

## Invariants

- Units keep the list's order. `n`, the nth unit of the same name, is counted in this order everywhere.
- `wargear` is what `army.model_key` (and so every pin in `mappings.json`) is built from. New information goes in
  new fields; `wargear` doesn't change.
- Fields from the data cache (`datasheet`, `sheet_model`, `sheet_wargear`, `composition`, `can_lead`, `base`, `tooltip`) are only there
  when the cache is. Tools must work without them.
