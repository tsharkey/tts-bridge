# Datasheet format

What every tool reads for a unit's stats, weapons, abilities and keywords. Sources are imported into this
format; nothing downstream reads a source's own files.

- **Produced by** `data.py import bsdata` ([`bsdata.py`](../../bsdata.py)), from [BSData](https://github.com/BSData/wh40k-11e).
- **Stored in** `cache/datasheets/` (git-ignored): `index.json` plus one file per catalogue.
- **Read with** `data.datasheets(faction)`: every unit an army can take, its own catalogue's first, then those
  of the catalogues it imports. `faction` is an `army.FACTIONS` name.
- **Sample:** [`tests/fixtures/bsdata/`](../../tests/fixtures/bsdata/) is a made-up army in BSData's schema;
  `tests/test_datasheets.py` shows what it imports to.

## index.json

```jsonc
{
  "source": {                       // cache/bsdata/source.json: what was imported
    "source": "bsdata",
    "kind": "github",               // github (zip download) | git | folder (read in place)
    "url": "https://github.com/BSData/wh40k-11e",   // "path" instead, for a folder
    "ref": null,                    // branch, tag or commit asked for; null = default branch
    "commit": "951d5900d1b4…",      // what was actually fetched, when known
    "fetched": "2026-09-27T18:02:57+00:00"
  },
  "imported": "2026-09-27T18:02:57+00:00",
  "skipped": {"crusade option": 4993, "hidden unit": 12},   // what the import left out, and how often
  "catalogues": {
    "Xenos - T'au Empire": {
      "file": "Xenos - T'au Empire.json",
      "faction": "T'au Empire",     // null for libraries, which no army is built from
      "library": false,
      "imports": ["Unaligned Forces"],   // catalogues whose units this army can also take
      "units": 66
    }
  }
}
```

## A catalogue file

```jsonc
{
  "catalogue": "Xenos - T'au Empire", "id": "…", "revision": 3,
  "faction": "T'au Empire", "library": false, "imports": ["Unaligned Forces"],
  "units": [ /* units, below */ ]
}
```

## A unit

```jsonc
{
  "id": "e88f-bc7a-c3f5-8e47",     // the source's id: stable when the name changes
  "name": "Crisis Sunforge Battlesuits",
  "catalogue": "Xenos - T'au Empire",
  "points": 125,                   // the base cost; size-dependent costs aren't resolved
  "keywords": ["Vehicle", "Walker", "Fly", "Battlesuit", "Crisis", "Sunforge"],
  "factions": ["T'au Empire"],     // faction keywords
  "rules": ["Deep Strike", "For The Greater Good"],   // core and army rules, by name
  "abilities": [{"name": "Sunforge", "type": "Abilities", "text": "Each time a model…"}],
  "size": [3, 3],                  // [min, max] models; max null = no limit
  "models": [
    {
      "id": "1370-a13b-2ed7-f383",
      "name": "Crisis Sunforge Shas’ui",   // names keep the source's spelling (curly apostrophes)
      "min": 2, "max": 2,          // how many of this model the unit has
      "stats": {"M": "10\"", "T": "5", "Sv": "3+", "InSv": "4+", "W": "4", "Ld": "7+", "OC": "2"},
      "equipped": [{"name": "Fusion blaster", "count": 2}, {"name": "Battlesuit fists", "count": 1}],
      "options": [
        {"name": "Drones (0-2)", "min": 0, "max": 2,
         "choices": ["Gun Drone", "Marker Drone", "Shield Drone"],
         "default": "Gun Drone"}   // only when the source names one
      ]
    }
  ],
  "equipped": [], "options": [],   // unit-wide wargear; only present when there is some
  "wargear": {                     // everything named in equipped/choices, by name
    "Fusion blaster": {
      "id": "abfe-83b-188b-bc12",
      "weapons": [{"name": "Fusion blaster", "type": "ranged", "range": "12\"", "A": "1", "skill": "4+",
                   "S": "9", "AP": "-4", "D": "D6", "keywords": ["Melta 2"]}],
      "abilities": []
    },
    "Marker Drone": {"id": "…", "weapons": [], "abilities": [{"name": "Marker Drone", "text": "…"}]}
  }
}
```

## On a parsed list

`datasheets.attach(parsed, mappings)` adds to what `army.parse_list` returns (only when datasheets are cached):

```jsonc
// each unit
"datasheet": {"id": "e88f-…", "name": "Crisis Sunforge Battlesuits",
              "catalogue": "Xenos - T'au Empire", "how": "exact"},   // null when nothing matched
// each model
"sheet_model": "Crisis Sunforge Shas’ui",            // null when the list can't say which
"sheet_wargear": ["Battlesuit fists", "Fusion blaster"]   // one per `wargear` entry, null where unmatched
```

`how` is `pinned`, `exact`, `fuzzy NN%`, or `…, pin had gone` when a pinned id no longer exists. Pins live
in `mappings.json` under `datasheets`, keyed `"<sub-faction or faction>|<unit>"`.

## Invariants

- Stat and weapon values are the source's text (`"10\""`, `"D6"`, `"Melee"`), not numbers. A blank value is `null`.
- A weapon with several profiles (`Dawn Blade - strike` / `- sweep`) is one piece of wargear with several `weapons`.
- A model with no stat line of its own uses its unit's (the one whose name its own starts with, or the first).
- A choice between whole compositions (10 or 20 Shock Troopers) is flattened: each model once, `min`/`max`
  across the choices, and `size` from the smallest to the largest.
- Left out: hidden entries, Crusade, Weapon Modifications, Warlord and Enhancements options, and every
  `modifier` (detachment- or game-mode-conditional changes). `index.json` counts what was skipped.

## Known gaps (BSData commit 951d590)

- 6 models have no stat line anywhere in their unit: Wulfen with the auto-launcher or death totem, the three
  Victrix Honour Guard models, and the Searchlight. Their `stats` is `null`.
