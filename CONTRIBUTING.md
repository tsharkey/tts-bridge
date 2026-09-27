# Contributing

Several people (and their coding agents) work on this at once. These conventions keep that from colliding.

## Finding work

- The [project board](https://github.com/users/tsharkey/projects) has every issue. The **Ready** column is work
  with nothing blocking it; start there.
- Issues are grouped into **tracks** (the `area:` labels and the board's Track field), and each track has a
  parent issue with its sub-issues. **Milestones** give the order: M0 Foundation lands before the rest,
  because it moves files everyone else touches.
- An issue that's **blocked by** another (see its sidebar) isn't ready, even if it looks small.
- `needs decision` means the issue has an open question for the maintainer. Ask in the issue or in
  [Discussions](https://github.com/tsharkey/tts-bridge/discussions) before building.

## Claiming an issue

1. Assign yourself (or comment "taking this" if you can't) and move it to **In progress**.
2. Open a **draft PR** early, within a day or two, so others can see the direction.
3. If you stop, unassign yourself and leave a comment saying where you got to.

## Branches and PRs

- Branch from `main`: `<issue number>-<short-name>`, e.g. `6-bsdata-cache`.
- **One issue per PR.** Put `Closes #<n>` in the description.
- Stay inside your track's files where you can. If you need to change a shared file (`army.py`,
  `tts_bridge.py`, `app/server.py`, the data formats), say so in the PR description.
- Changing a data format that other tracks use (datasheets, layout terrain, board state, the TTS gateway)
  needs its own small PR or a Discussion first, so the other tracks can adjust.
- CI runs `ruff check .` and `pytest` on every PR. Both must pass.
- CODEOWNERS requests the right reviewers automatically.

## Setting up

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

The README covers running the tools. Anything that talks to TTS needs Tabletop Simulator open with a game
loaded and the External Editor API on. Tests must not need it: keep TTS calls at the edges, and test the
logic (parsing, matching, geometry) on its own.

## Writing issues

Use the **Task** form. The bar is: someone who wasn't in the conversation can start work from the issue
alone. Link dependencies as "blocked by", add it to the board, and set Track, Size and milestone.

## Data you must not commit

- Army lists (`lists/`): they're players' own. Tests use small trimmed exports in `tests/fixtures/`, with no
  player names.
- `catalog/` (Force Org's model data), BSData, and anything downloaded from Wahapedia: they're rebuilt locally
  and aren't ours to redistribute.
- `.env`, `scenes/`, `debug/`, `usage.jsonl` and other local run output. `.gitignore` covers these.
