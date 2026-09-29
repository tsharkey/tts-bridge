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

The web app is also the gateway to TTS for everything else here: the command-line tools (and Claude) send
their Lua through it. `POST /api/tts/lua` with `{"script": "...", "timeout": 10}` **runs any Lua you send it in
your game**, and `GET /api/tts/events` streams what TTS prints and sends. It only listens on 127.0.0.1 and only
answers requests addressed to `localhost` or `127.0.0.1`, so only programs on your computer can use it.

### Board view

The table from above: the LCT layout's terrain, objectives and deployment zones, and every model's base. It
keeps up with the game, redrawing within a couple of seconds when something moves in TTS.

- **Click a unit** to see what it can see (its line of sight, from the layout's terrain) and which enemy units
  it can see, and to switch on its move, advance, charge and weapon ranges, measured from its bases' edges.
- **Shift-click a second unit** for the distance between the two and how much each can see of the other.
- **Saved board.json** shows the last `python3 board.py summary` instead, without TTS.
- **With Claude:** Claude can read what you've selected ("where should this unit go?"), and what it draws with
  `highlight` appears on the board, listed under **From Claude** with its note and a button to clear it.

Line of sight is worked out from the terrain's footprints, so windows and doorways aren't counted, and ranges
are straight lines that ignore terrain in the way.

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
rules, enhancements and keywords. For the whole datasheet, with every rule and weapon keyword explained,
right-click a model and choose **Datasheet**: a scrollable window opens (drag it by its edges; only you see
it). You can also bind a key to **Show datasheet** in TTS's Options → Game Keys and press it while hovering a
model. The datasheet travels with the model, in a small script on it. **Datasheet** on a unit in Scribe shows
the same text. `army.py build` and **Board from image** give models the same tooltips and datasheets.

The same right-click menu has **Threat range on/off**: labelled rings round that model, measured from its base's
edge. Green rings are movement: its move and advance, and its charge (move + 12"). Blue rings are its ranged
weapons: each weapon's range from where it stands, and its range after a move. Solid rings are what it does from
where it stands; dashed rings are what it reaches after moving. They're part of the model, so they go where it
goes, and they work without the web app. Bind a key to "Threat range on/off" in Options → Game Keys to toggle the
model under your cursor.

**Line of sight** shades the area the model's unit can see from where its models are now, outlines it, and draws a
line to each enemy model it sees: green when fully visible, yellow when partly. It's worked out in the game, from the
layout's terrain, which tts-bridge puts on the table once: when the hub sets up the LCT table, when you press **Send
terrain to TTS** on the Board page, or the first time you choose Line of sight (with the web app running). After
that it works without the web app, and it's saved with the game. Nothing redraws on its own: after moving models,
choose **Refresh line of sight**. **Clear overlays** turns off every model's threat rings and the line of sight.
Everyone at the table sees the lines.

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
   each army if you like ("black armour, purple trim"). Check says which units have no datasheet and which had
   their models filled in from one; **Open in Scribe** shows the whole list there. **Choose models** works like
   Scribe's: each unit's favourites and the suggested models, **Browse all models…**, and ☆ for favourites.
3. **Table (LCT):** dispositions are filled in from the lists. Pick one of the matchup's three layouts; each
   shows LCT's layout diagram.
4. **1 · Image:** drop in a top-down image and fit the yellow 60"×44" frame to the table edges. Drag to move,
   scroll to resize, ⟳ to rotate. The frame can run off a cropped image. The layout overlay helps line up
   terrain and deployment zones. Then click **Use this board**.
5. **2 · Board:** **Analyse image** asks the model where each unit is. Units show as circles on the straightened
   board. Click one to change what it is, how many models it has, or remove it; drag to move it.
6. **3 · Units:** **Send to TTS** clears the table, loads the chosen LCT layout, then places both armies. Units
   that aren't on the board go to each army's reserves board. Models carry the same tooltips, datasheets and
   unit tags as Scribe's. Untick "Load the chosen LCT layout first" to place models on whatever table is
   already there.

Each send is saved to `scenes/` (the lists, positions, frame and image) and can be reloaded from the
**Load a saved scene** menu.

### Layouts

The exact terrain of every LCT layout (`layouts/`), which line of sight, the board view and Claude's tools use.
Pick one to see its terrain areas, features and objectives on a 60" × 44" grid, over LCT's diagram of the map
when LCT is loaded in TTS.

- **Fix a piece:** click a feature to change its category (dense, light, exposed), height or floors, or drag
  the corners of a feature or area. **Save** checks it against the format and writes `layouts/<id>.json`, to
  commit in a PR. A layout saved here is marked as edited, and an import leaves it alone.
- **Compare with the table:** with that layout loaded in TTS, each piece is marked green when the table has it
  within 0.25" and 2°, red when it's further off, and grey when it isn't there.
- **Import a new LCT version:** after `python3 data.py mods lct`, builds every layout aside and lists what's new,
  changed (with what changed) and gone from LCT. Nothing is written until you tick what to take.
- **Delete** a layout LCT no longer uses; imports won't bring it back.

### Data cache

Shows what's in the local data cache: where the datasheets came from, which version, when they were fetched, and
how many there are per faction, and the same for base sizes. **Fetch** / **Refresh** downloads each one and imports it, showing its progress
and any error. Change the link or the branch/commit first to use a fork or pin a version. It doesn't need TTS.
The Force Org models and LCT layouts are shown too. **Refresh from mod files** reads them from TTS's Workshop
folder, with TTS closed. Force Org can also be read from TTS: a light turns green when Force Org is the game
loaded, and **Refresh from TTS** rebuilds the catalogue the same way as `python3 army.py index`.

## Command-line tools

These use the same catalogue. They work with the web app running too: it holds the bridge's listener port and
passes their Lua on to TTS.

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

**Layout terrain:** `layouts/` has the exact terrain of every LCT layout: each terrain area and feature as a
rotated footprint, whether it's dense or light, its height and floors, the objectives and the deployment zones
([format](docs/formats/layout-terrain.md)). It's committed, so you don't need to build it. To rebuild it after LCT
updates (with TTS closed; needs `requirements-dev.txt` for LCT's asset bundles):

```bash
python3 data.py mods lct                   # read LCT's layouts from its mod file
python3 layouts.py build --download        # rebuild layouts/, fetching any terrain mesh TTS hasn't downloaded
python3 layouts.py check                   # with a layout loaded in TTS: compare it with its file
python3 layouts.py import                  # after `data.py mods lct`: what a new LCT version would change
python3 layouts.py import --apply          # take the new and changed layouts (not ones edited here)
```

**Talk to TTS directly:**

```bash
python3 tts_bridge.py state            # dump every object on the table to tts_state.json
python3 tts_bridge.py run "<lua>"      # run Lua in the game and print the result
```

## Claude tools (MCP)

The web app is also an [MCP](https://modelcontextprotocol.io) server, so Claude can use the game directly from
Claude Code or Claude Desktop's chat. Its tools so far:

- `status`: is TTS reachable, is LCT loaded.
- `board_summary`: every unit on the table and in reserves, with each model's position; which LCT layout is loaded,
  with its exact terrain areas, objectives and deployment zones; and which areas and objectives each unit is in.
- `measure`: the closest base-to-base distance between two units, and optionally how far each is from every
  objective and deployment zone (and whether a base overlaps the objective's terrain area, or every base is wholly
  within the zone).
- `place_unit`: set a unit up in rows at a spot and facing, from the table or from reserves. It checks first (off
  the table, overlapping, engagement range, coherency) and only moves with no problems unless forced; it also says
  which objectives and deployment zone the spot is in. `undo_place` puts units back, the last one first.
- `line_of_sight`: what a unit can see of an enemy unit, model by model and what blocks it, or of every enemy
  unit on the table. It uses the layout's terrain (Obscuring areas, dense features at ground level, Plunging Fire,
  Hidden and Gone to Ground ranges), worked out from footprints, so windows and doorways aren't modelled.
- `threat_ranges`: how far a unit reaches this turn, from its datasheet and the weapons its models carry: move,
  advance, charge and each gun's range, measured from its bases' edges.
- `can_reach`: what one unit can do to another this turn (the charge roll it needs and its chance, which guns are
  in range now, after moving or after advancing, and whether it can see the target), or which enemy units a unit
  can reach, or which can reach it. Distances are straight lines: terrain in the way isn't counted.
- `show_on_table` / `clear_table_overlays`: draw a unit's line of sight and threat ranges on the TTS table for
  everyone to see, so Claude can show what it's talking about, and remove them.
- `get_selection`: the units you've clicked in the Board view, so you can ask about "this unit".
- `highlight` / `clear_highlights`: Claude draws on the Board view (units, spots, ranges, lanes, areas, with
  labels and a note), and on the TTS table too if asked.

Tools only run while the web app is up, and a tool that changes the game only moves objects this project spawned. Like `/api/tts/lua`, the server
only answers requests from this computer.

**Claude Code** (with the web app running):

```bash
claude mcp add --transport http tts-bridge http://127.0.0.1:8765/mcp
```

**Claude Desktop** starts its tools itself, over stdio. Add this to its config (Settings → Developer → Edit
Config), with this folder's path, and restart Claude Desktop:

```json
{
  "mcpServers": {
    "tts-bridge": {
      "command": "/path/to/tts-bridge/.venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "env": {"PYTHONPATH": "/path/to/tts-bridge"}
    }
  }
}
```

It sends everything through the web app, so start the web app too (in either order).

## Deployment planning with Claude Code

`.claude/skills/wh40k-deployment-planner/` is a Claude Code skill for the deployment phase. Open Claude Code in
this folder and ask it to plan your deployment, or to pick your next drop as units go down. It reads the table
with `board.py`, weighs cover and Hidden, objectives, shooting lanes, charge staging and screening against what
the opponent has placed, checks each position for both first-turn outcomes, and can place the unit for you.
Give it the mission (both Primary Missions and the deployment card or layout), both lists, and which units start
in reserves or transports. The web app can stay open while it works.

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
