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

Then build the model catalogue. Load the **Force Org** mod in TTS and run:

```bash
python3 army.py index
```

This copies the model data out of every Force Org army tile into `catalog/` (about 34 MB, 44 tiles, a couple
of seconds). You only need to do it again when Force Org updates; unchanged tiles are skipped. Once the
catalogue exists, Force Org doesn't need to be open: armies can be spawned into any game.

## The web app

```bash
.venv/bin/python app/server.py
```

Open <http://localhost:8765>. The header shows whether TTS is connected, and whether LCT is loaded.

1. **Model:** paste your OpenRouter key and pick any vision model. Prices are shown per model.
2. **Armies:** paste the Red and Blue lists, or pick saved ones, and press **Check**. Add a hint for spotting
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

## Command-line tools

These use the same catalogue. Stop the web app first: only one process can hold the bridge's listener port.

**Spawn an army list** (GW app exports and the `+++` format both work):

```bash
python3 army.py plan lists/necrons.txt                # show which model each list entry matched
python3 army.py build lists/necrons.txt -30 21 11 90  # spawn it: top-left x z, row width, facing
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
Code to read the board (they share the bridge's listener port). Scenes sent before `board.py` existed tag both
armies the same way; send them again so it can tell the armies apart.

## Fixing model matches

Every choice the matcher makes is saved in `mappings.json`, so a list always comes out the same way.

- **`models`** maps `"<faction>|<unit>|<model>|<wargear>"` to catalogue entries (`"<tile>:<index>"`). Edit an
  entry to pick a different model, or delete it to have it matched again.
- **`units`** gives the model composition for datasheets the parser can't work out from a list, for example
  `"T'au Empire|The Twin Lance": [["Ri'Lantar", 1], ["Ri'Locai", 1]]`.

`python3 army.py plan <list>` shows each match and whether it was pinned or matched automatically.

## Notes

- Spawned models are tagged. Re-sending replaces only models this project spawned; terrain and everything else
  is left alone, except when a layout is loaded, which clears the mat the same way LCT's own buttons do.
- Drones are rules wargear in the current edition, so they are not spawned as models.
- The Saved Objects path in `army.py` is the macOS one (`~/Library/Tabletop Simulator/...`). On Windows, change
  `SAVED_OBJECTS` to `Documents/My Games/Tabletop Simulator/Saves/Saved Objects`.
- TTS's API can't load a mod or save, so load Force Org or LCT yourself; everything after that can be driven
  from here.
