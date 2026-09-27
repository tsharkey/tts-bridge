# Notes for coding agents

Read this before changing anything. [CONTRIBUTING.md](CONTRIBUTING.md) has the workflow (issues, branches, PRs);
this file has what the code assumes. The [README](README.md) explains what each tool does for a user.

## What this is

Local tools for playing Warhammer 40,000 (11th edition) in Tabletop Simulator (TTS). Python, no framework:
the web app is `http.server` plus one static HTML page (`app/static/index.html`, plain JS, three.js from a CDN).
The command-line tools use only the standard library; `numpy` and `opencv` are for the web app.

| File | Does |
|---|---|
| `tts_bridge.py` | Talks to TTS: `run_lua()` sends Lua on port 39999 and waits for the reply on 39998. |
| `army.py` | Parses army lists (GW app export, `+++` format), matches them to Force Org models, spawns them, writes Saved Objects. |
| `board.py` | Reads the table as units and terrain, measures between units, places a unit in formation. |
| `recreate.py` | Rebuilds a board state from a scene (unit positions per army). |
| `app/server.py` | The web app's HTTP API. |
| `app/vision.py` | Straightens a board image and asks a vision model (via OpenRouter) where units are. |
| `config.py` | Optional settings from the environment or `.env` (see `.env.example`). |
| `mappings.json` | Pinned model matches. Changes here change which models spawn. |
| `lists/` | The user's saved army lists (git-ignored). Tests use the trimmed exports in `tests/fixtures/`. |
| `.claude/skills/wh40k-deployment-planner/` | A Claude skill for deployment, driving `board.py`. |

## Rules

- **Don't clear or rearrange the user's table** (loading a layout, destroying objects, Clear Table) unless the
  task is exactly that or the user said so. Only move or remove objects this project spawned.
- **One listener.** Only one process can bind port 39998, so the web app and the CLI tools can't run at the
  same time (until the TTS gateway lands). Don't "fix" a busy-port error by killing processes.
- **The first line of a spawned model's description is exactly `[<unit name>]`.** `board.py` groups models into
  units by it (`UNIT_RE`). Put anything else (datasheets, tooltips) after that line.
- **Army tags live in GM Notes:** `army.py:<list title>` or `recreate:<scene>:Red|Blue`. Keep them there.
- **Write tooltip names to `Nickname`, never `Name`**, which is the TTS object type.
- **Coordinates are table inches**, 0,0 at the centre, x along the 60" edge (−30…30), z along the 44" edge
  (−22…22), y up. Facing in degrees: 0 = +z, 90 = +x.
- **`n` is the nth same-named unit in `parse_list` order** everywhere (vision, scenes, placement). Don't reorder.
- **Never commit** army lists, `catalog/`, BSData, Wahapedia downloads, `.env`, `scenes/`, `debug/` or `usage.jsonl`.
  Test data goes in `tests/fixtures/`, trimmed to what the test needs and with no player names.
- **Tests run without TTS.** Put new logic where it can be tested offline, and add tests for it.
- Match the surrounding code: module docstrings with usage at the top, sparse comments that say why.
  User-facing text (README, UI) is plain and direct.

## Checks

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

If you change the list parser on purpose and a test's expected composition changes, check it against the
fixture's text and update the test in the same PR.
