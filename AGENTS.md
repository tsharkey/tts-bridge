# Notes for coding agents

Read this before changing anything. [CONTRIBUTING.md](CONTRIBUTING.md) has the workflow (issues, branches, PRs);
this file has what the code assumes. The [README](README.md) explains what each tool does for a user.

## What this is

Local tools for playing Warhammer 40,000 (11th edition) in Tabletop Simulator (TTS). Python. The web app is a
small FastAPI hub (`app/`): a homepage plus one page per tool, plain HTML/JS with no build step (three.js from a CDN).
The command-line tools use only the standard library; FastAPI, `numpy` and `opencv` are for the web app.

| File | Does |
|---|---|
| `tts_bridge.py` | Talks to TTS: `run_lua()` sends Lua on port 39999 and waits for its reply on 39998, or forwards it to the hub when the hub holds 39998. |
| `army.py` | Parses army lists (GW app export, `+++` format), matches them to Force Org models, spawns them, writes Saved Objects. |
| `board.py` | Reads the table as units and terrain, measures between units, places a unit in formation. |
| `recreate.py` | Rebuilds a board state from a scene (unit positions per army). |
| `app/server.py` | The hub: mounts the shared routes and each tool (`TOOLS`), serves the homepage. |
| `app/core/` | Shared by every tool: TTS access, its lock and the event stream (`tts.py`), LCT setup, lists and model picks, the API error handling (`api.py`). |
| `app/mcp_server/` | The MCP server for Claude: one module per tool, listed in `MODULES`; served at `/mcp` and over stdio (`python -m app.mcp_server`). |
| `app/static/` | The homepage, and `shared.css` / `shared.js` (header, `api()`, `store`, theme) that every page loads. |
| `app/tools/<tool>/` | One tool: `TOOL` (its homepage card), `routes.py`, and `static/index.html`, served at `/tools/<tool-with-dashes>/`. |
| `app/tools/board_replay/vision.py` | Straightens a board image and asks a vision model (via OpenRouter) where units are. |
| `data.py` | The local data cache in `cache/`: fetches sources (BSData), imports them, and reads datasheets back (`datasheets(faction)`). |
| `datasheets.py` | `parse()`: a list parsed with the data cache (datasheets matched and pinned, compositions filled in); look-alike figures for `army.resolve`. |
| `mods.py` | Reads Force Org (into `catalog/`) and LCT's layouts (into `cache/lct/`) from TTS's Workshop folder, with TTS closed. |
| `tooltips.py` | Datasheet tooltips (TTS BBCode) for spawned models, written after the `[<unit>]` line. |
| `sheetviewer.py` | The datasheet viewer script on spawned models: right-click → Datasheet opens a scrollable window. |
| `bases.py` | Official base sizes from Wahapedia's export (`data.py fetch wahapedia`), attached to a parsed list's models. |
| `bsdata.py` | Imports BSData's catalogues into our datasheet format ([docs/formats/datasheet.md](docs/formats/datasheet.md)). |
| `config.py` | Optional settings from the environment or `.env` (see `.env.example`). |
| `mappings.json` | The user's pinned model matches (git-ignored; created on first use). |
| `lists/` | The user's saved army lists (git-ignored). Tests use the trimmed exports in `tests/fixtures/`. |
| `.claude/skills/wh40k-deployment-planner/` | A Claude skill for deployment, driving `board.py`. |

## Rules

- **The shapes tools pass each other are written down in [docs/formats/](docs/formats/)**: parsed list,
  datasheet, layout terrain (draft), board state and the TTS gateway, each with a sample in
  `tests/fixtures/formats/` or `tests/fixtures/` that a test loads. Build against those. Changing a format other
  tracks use needs its own small PR or a Discussion first (CONTRIBUTING.md); add fields rather than rename them.
- **Don't clear or rearrange the user's table** (loading a layout, destroying objects, Clear Table) unless the
  task is exactly that or the user said so. Only move or remove objects this project spawned.
- **One listener, and the hub forwards.** Only one process can bind port 39998. The hub holds it and serves
  `POST /api/tts/lua` (run Lua in the game) and `GET /api/tts/events` (everything else TTS sends, as SSE);
  `tts_bridge.start_listener()` forwards to the hub when the port is busy, so CLI tools run alongside it. Each
  call gets its own reply. Don't "fix" a busy-port error by killing processes.
- **The first line of a spawned model's description is exactly `[<unit name>]`.** `board.py` groups models into
  units by it (`UNIT_RE`). Put anything else (datasheets, tooltips) after that line.
- **Army tags live in GM Notes:** `army.py:<list title>` or `recreate:<scene>:Red|Blue`. Keep them there.
- **Unit membership lives in TTS tags:** `tts-bridge:unit:<n>` (the unit's place in its list, from 1) and
  `tts-bridge:sheet:<datasheet id>`, set by `army.unit_tags` / `army.tag`. `board.py` groups by them and falls back
  to distance for untagged models. Don't put other data in `tts-bridge:` tags without adding it here.
- **Write tooltip names to `Nickname`, never `Name`**, which is the TTS object type. Build spawned objects with
  `army.model_objects` (or `tooltips.describe`) so the `[<unit>]` line and tooltip stay consistent.
- **Our script on a model is appended, never replacing its own** (`sheetviewer.attach`), and our screen UI is added
  beside the table's (`UI.getXmlTable` + insert), never replacing it: LCT and other mods have their own.
- **Coordinates are table inches**, 0,0 at the centre, x along the 60" edge (−30…30), z along the 44" edge
  (−22…22), y up. Facing in degrees: 0 = +z, 90 = +x.
- **`n` is the nth same-named unit in `parse_list` order** everywhere (vision, scenes, placement). Don't reorder.
- **Parse lists with `datasheets.parse`**, not `army.parse_list`, so datasheets and compositions are used when
  cached. A model's `wargear` is what pins are keyed on: add new fields, don't change it
  ([docs/formats/parsed-list.md](docs/formats/parsed-list.md)).
- **Never commit** army lists, `mappings.json`, `catalog/`, `cache/`, BSData, Wahapedia downloads, `.env`, `scenes/`, `debug/` or `usage.jsonl`.
  Test data goes in `tests/fixtures/`, trimmed to what the test needs and with no player names.
- **Datasheets are read in our format only** (`data.datasheets()`), never from BSData's files, so another source
  is just another importer. BSData test data is made up in its schema (`tests/fixtures/bsdata/`), not copied.
- **Tests run without TTS.** Put new logic where it can be tested offline, and add tests for it.
- **New MCP tools go in `app/mcp_server/`**: a module with its functions in `TOOLS`, added to `MODULES`. The
  docstring is what Claude reads; give it typed parameters and a TypedDict return. Keep the logic in a module
  that tests can call without TTS (e.g. `board.py`), and the tool a thin wrapper over it. A test lists the tools.
- **New tools go in `app/tools/<name>/`**, added to `TOOLS` in `app/server.py`. Make routers with
  `app.core.api.router()` so errors reach the page the same way, and hold `app.core.tts.lock` around a run of TTS calls that must not interleave with another request's (spawning, LCT setup).
- Match the surrounding code: module docstrings with usage at the top, sparse comments that say why.
  User-facing text (README, UI) is plain and direct.

## Checks

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

If you change the list parser on purpose and a test's expected composition changes, check it against the
fixture's text and update the test in the same PR.
