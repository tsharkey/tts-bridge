# tts-bridge

Drive a running Tabletop Simulator (TTS) game of Warhammer 40,000 from outside TTS:

- **Spawn an army list** as real models, taken from the Force Org mod's model catalogue.
- **Rebuild a board state from a top-down image.** A vision model on OpenRouter reads where each unit is, the
  app loads the matching LCT layout, and both armies are placed. Anything not visible goes to the reserves boards.

Everything talks to TTS through its built-in External Editor API (localhost ports 39999/39998), so there is
nothing to install inside TTS.

## Requirements

- **Tabletop Simulator** with a game loaded and the **External Editor API** turned on
  (Options → General in TTS).
- **Python 3.10+.**
- **The Force Org mod** from the Steam Workshop, needed once, to build the model catalogue.
- **LCT** ([Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=3710681747)), for layouts,
  deployment zones and reserves boards. Without it, models are still placed, just without a layout.
- **An [OpenRouter](https://openrouter.ai) API key**, only for analysing images. You enter it in the web app;
  it is kept in your browser, never saved to disk.

## Setup

```bash
git clone git@github.com:tsharkey/tts-bridge.git
cd tts-bridge
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Then build the model catalogue and the LCT layouts. Subscribe to **Force Org** and **LCT** in the Steam
Workshop (and open each once in TTS so it downloads), then run:

```bash
python3 data.py mods
```

This reads both mods straight from TTS's Workshop folder, so TTS can be closed. It copies the model data out of
every Force Org army tile into `catalog/` (about 34 MB, 44 tiles) and every LCT layout into `cache/lct/`. Run it
again when a mod updates; unchanged tiles are skipped. If your Workshop folder isn't in the usual place, or
you're subscribed to more than one version of a mod, see `.env.example`.

(`python3 army.py index` still builds the catalogue the old way, through TTS with Force Org loaded.)

Datasheets (stats, weapons, abilities, keywords) come from the community's
[BSData](https://github.com/BSData/wh40k-11e) files. Fetch them once; after that they work offline:

```bash
python3 data.py fetch bsdata
```

This downloads BSData (about 5 MB, no git needed) into `cache/` and imports it. Run it again to update; it
skips the download when nothing has changed. `--from <link or folder>` uses a fork or a local checkout, and
`--ref <branch or commit>` pins a version; both are remembered for the next fetch. `python3 data.py status`
shows what's cached.

Official base sizes come from [Wahapedia](https://wahapedia.ru)'s data export. They're used to measure from
the edge of a model's base:

```bash
python3 data.py fetch wahapedia
```

## The web app

```bash
.venv/bin/python app/server.py
```

Open <http://localhost:8765>. The homepage lists the tools; every page's header shows whether TTS is
connected, and whether LCT is loaded.

### Scribe

Turns an army list into a TTS army. Paste a list from the GW app, New Recruit (full, simple or short export) or a
"+++" tournament list, or pick a saved one, and press **Read list**. It shows what the list became: each unit's
datasheet, leaders and what they're attached to, the warlord, enhancements, and every model with its gear, base
size and the TTS model it will use. Anything that didn't match is listed at the top; pick a datasheet or a TTS
model on the unit to fix it (**View** previews static models in 3D, **Tooltip** shows what hovering it in TTS
will show). A model's dropdown lists the unit's favourites and the suggested models; **Browse all models…** opens
every model of the army, and ☆ makes the current one a favourite for that unit.

With datasheets cached, every model it saves or spawns carries its datasheet: hover over it in TTS for its stats,
base and the weapons it carries. One model per unit (the leader or sergeant) also shows the unit's abilities,
rules, enhancements and keywords. Each unit also gets a **datasheet card** beside it (a plain TTS notecard):
its whole datasheet, with every rule and weapon keyword explained. **Datasheet** on a unit in Scribe shows the
same card. `army.py build` adds the same tooltips and cards; **Board from image** adds the tooltips.

- **Save as Saved Object** writes the army to TTS's Saved Objects folder, with TTS closed. In any game, load it
  from **Objects → Saved Objects**.
- **Spawn on the table** places it in the running game, top-left corner at x, z (table inches), rows up to the
  width given, facing the way given.

Both remember your choices in `mappings.json`, so the list comes out the same next time.

### Models

Every Force Org model, by army (or all of them), searchable by name, with a 3D view of static models. To set a
unit's **favourites**, pick an army and a unit under **Favorites for** and star the models you like for it. When a
list is read, a unit's favourites are used first (whatever they're called, and from any army), and they're listed
first when you choose a model in Scribe.

### Board from image

1. **Model:** paste your OpenRouter key and pick any vision model. Prices are shown per model.
2. **Armies:** paste the Red and Blue lists, or pick saved ones (kept in `lists/`, on your machine only), and press **Check**. Add a hint for spotting
   each army if you like ("black armour, purple trim").
3. **Table (LCT):** dispositions are filled in from the lists. Pick one of the matchup's three layouts; each
   shows LCT's layout diagram.
4. **1 · Image:** drop in a top-down image and fit the yellow 60"×44" frame to the table edges. Drag to move,
   scroll to resize, ⟳ to rotate. The frame can run off a cropped image. The layout overlay helps line up
   terrain and deployment zones. Then click **Use this board**.
5. **2 · Board:** **Analyse image** asks the model where each unit is. Units show as circles on the straightened
   board. Click one to change what it is, how many models it has, or remove it; drag to move it.
6. **3 · Units:** **Send to TTS** clears the table, loads the chosen LCT layout, then places both armies. Units
   that aren't on the board go to each army's reserves board. Untick "Load the chosen LCT layout first" to
   place models on whatever table is already there.

Each send is saved to `scenes/` (the lists, positions, frame and image) and can be reloaded from the
**Load a saved scene** menu.

### Data cache

Shows what's in the local data cache: where the datasheets came from, which version, when they were fetched, and
how many there are per faction, and the same for base sizes. **Fetch** / **Refresh** downloads each one and imports it, showing its progress
and any error. Change the link or the branch/commit first to use a fork or pin a version. It doesn't need TTS.
The Force Org models and LCT layouts are shown too. **Refresh from mod files** reads them from TTS's Workshop
folder, with TTS closed. Force Org can also be read from TTS: a light turns green when Force Org is the game
loaded, and **Refresh from TTS** rebuilds the catalogue the same way as `python3 army.py index`.

## Command-line tools

These use the same catalogue. Stop the web app first: only one process can hold the bridge's listener port.

**Spawn an army list** (GW app, `+++` tournament, and New Recruit full, simple and short exports all work; simple and short ones leave out models, so check what `plan` shows). **Scribe** in the web app does the same without a terminal:

```bash
python3 army.py plan my_list.txt                # show which model each list entry matched
python3 army.py build my_list.txt -30 21 11 90  # spawn it: top-left x z, row width, facing
```

`build` also writes the army to TTS's Saved Objects folder, so it can be loaded into any game from
Objects → Saved Objects.

**Recreate a saved scene:**

```bash
python3 recreate.py scenes/<name>.json
```

**Read the table as units and place one** (used by the deployment skill below):

```bash
python3 board.py summary                               # units, terrain and zones; writes board.json
python3 board.py dist "Pathfinder" "Intercessor"       # closest base-to-base distance
python3 board.py place "Pathfinder Team" 6 17 180 --cols 5 --check   # validate a spot
python3 board.py place "Pathfinder Team" 6 17 180 --cols 5           # move the unit there
python3 board.py undo                                  # put it back
```

Units are recognised by the `[<unit name>]` line `army.py` and `recreate.py` put in each model's description.
`place` won't move a unit off the table, onto other models, or within 2" of an enemy unless you add `--force`.

**Talk to TTS directly:**

```bash
python3 tts_bridge.py state            # dump every object on the table to tts_state.json
python3 tts_bridge.py run "<lua>"      # run Lua in the game and print the result
```

## Deployment planning with Claude Code

`.claude/skills/wh40k-deployment-planner/` is a Claude Code skill for the deployment phase. Open Claude Code in
this folder and ask it to plan your deployment, or to pick your next drop as units go down. It reads the table
with `board.py`, weighs cover and Hidden, objectives, shooting lanes, charge staging and screening against what
the opponent has placed, checks each position for both first-turn outcomes, and can place the unit for you.
Give it the mission (both Primary Missions and the deployment card or layout), both lists, and which units start
in reserves or transports. Set the table up with the web app first, then stop the web app before asking Claude
Code to read the board (they share the bridge's listener port).

## Optional settings

Settings are read from environment variables or from a `.env` file in the repo root (copy `.env.example`).
None are needed.

- **`VOD_INGEST_URL`**: a personal integration, not a normal way to load lists. If you run the
  [40K VOD Index](https://40kvodindex.com) ingest tool locally (`pnpm ingest`), set this to its address
  (e.g. `http://localhost:3001`) and **Armies** gets an "Import both lists from a 40K VOD Index game" option.
  Without it, the option is hidden.

## Fixing model matches

Every choice the matcher makes is saved in `mappings.json`, so a list always comes out the same way. The file is
yours: it's created on first use and isn't committed.

- **`models`** maps `"<faction>|<unit>|<model>|<wargear>"` to catalogue entries (`"<tile>:<index>"`). Edit an
  entry to pick a different model, or delete it to have it matched again.
- **`units`** gives the model composition for datasheets the parser can't work out from a list, for example
  `"T'au Empire|The Twin Lance": [["Ri'Lantar", 1], ["Ri'Locai", 1]]`. With datasheets cached this is rarely
  needed: model names and compositions come from the datasheet.
- **`datasheets`** maps `"<chapter or faction>|<unit>"` to the unit's datasheet (`{"id", "name", "catalogue"}`),
  once datasheets are cached (`python3 data.py fetch bsdata`). `plan` shows each unit's datasheet and flags
  anything that didn't match. Change the `id` to pick another datasheet, or delete the entry to match it again.
- **`bases`** fixes a model's base size when Wahapedia's is missing or wrong:
  `"<chapter or faction>|<unit>|<model>": "32mm"` (any size Wahapedia would write, like `"60 x 35mm"`).
- **`favorites`** lists the figures you like for a unit, `"<chapter or faction>|<unit>": ["<tile>:<index>", ...]`.
  They're tried first for that unit's models. Set them with the stars in **Models** or **Scribe**.
- **`aliases`** renames a model the catalogue calls something else, per faction, for example
  `"Adepta Sororitas": {"Dominion": "Battle Sister"}`. With datasheets cached, a model the catalogue has no
  figure for is matched to a look-alike's (same stats, most wargear in common) without one.

Models are only matched from the army's own tiles (a chapter can also use its parent's and sibling chapters'
tiles); only allied units search every army.

`python3 army.py plan <list>` shows each match and whether it was pinned or matched automatically.

## Notes

- Spawned models are tagged. Re-sending replaces only models this project spawned; terrain and everything else
  is left alone, except when a layout is loaded, which clears the mat the same way LCT's own buttons do.
- Drones are rules wargear in the current edition, so they are not spawned as models.
- The Saved Objects path in `army.py` is the macOS one (`~/Library/Tabletop Simulator/...`). On Windows, change
  `SAVED_OBJECTS` to `Documents/My Games/Tabletop Simulator/Saves/Saved Objects`.
- TTS's API can't load a mod or save, so load Force Org or LCT yourself; everything after that can be driven
  from here.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how work is organised (issues, the project board, branches and PRs),
and [AGENTS.md](AGENTS.md) for what the code assumes. Run the checks with:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
```
