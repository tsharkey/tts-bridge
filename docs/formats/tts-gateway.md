# TTS gateway API

How anything on this computer talks to the running game while the hub holds TTS's reply port (#17).

- **Served by** the hub (`app/server.py`) on `http://127.0.0.1:8765`; the routes are in `app/core/api.py`.
- **Used by** `tts_bridge.run_lua` in the CLI tools (`board.py`, `army.py`, `recreate.py`, `tts_bridge.py`)
  when the hub is running, and (planned) the MCP server (#38).
- **Sample:** [`tests/fixtures/formats/tts-gateway.json`](../../tests/fixtures/formats/tts-gateway.json);
  `tests/test_gateway.py` runs the endpoints against a fake TTS.

`POST /api/tts/lua` **runs any Lua you send it in your game.** The hub only listens on 127.0.0.1 and only
answers requests addressed to `localhost` or `127.0.0.1`.

## `GET /api/tts`

`{"gateway": true}`. How `tts_bridge.start_listener()` tells the hub from something else holding the port.

## `POST /api/tts/lua`

```jsonc
// request
{"script": "return 1 + 1", "timeout": 10}   // timeout in seconds, default 10
// reply
{"ok": true, "result": 2}                   // what the script returned; a table comes back JSON-encoded
{"ok": false, "error": "Lua error: chunk_4:(3,4-9): attempt to index a nil value"}
{"ok": false, "error": "No response from TTS. …"}   // it didn't answer within the timeout
```

The script runs as a function body in Global, so `return` gives the result. Concurrent calls each get their own
reply. HTTP 503 `{"error": …}` means TTS isn't accepting commands at all.

## `GET /api/tts/events`

Server-sent events: everything TTS sends that isn't a reply to `/api/tts/lua`, as it arrives, one JSON message per
`data:` line. Lines starting with `:` are keepalives. The messages are TTS's External Editor API's own:

```jsonc
{"messageID": 2, "message": "Red scored 5 VP"}             // print()
{"messageID": 3, "error": "…", "guid": "-1", "errorMessagePrefix": "…"}   // a script error
{"messageID": 4, "customMessage": {…}}                     // sendExternalMessage() from a mod
{"messageID": 6}                                           // game saved
{"messageID": 7, "guid": "a1b2c3"}                         // object created
// 0 (pushing new objects) and 1 (loading a new game) pass through too
```

## Invariants

- `run_lua` wraps the script so its result comes back as `sendExternalMessage({reply = <id>, ok, result})`;
  messages with a `reply` field are replies and never appear on the event stream. Mods shouldn't use `reply`.
- Messages whose `customMessage` has a `ttsBridge` field are requests from tts-bridge's own scripts on spawned
  models, handled by the hub (`tts_bridge.commands`), and never appear on the event stream either. So far:
  `{"ttsBridge": "overlay", "guid": "a1b2c3", "show": "threat" | "los" | "clear", "color": "Red"}`, from the
  right-click menu (`sheetviewer.py`), drawn by `app/mcp_server/overlay.py`. Mods shouldn't use `ttsBridge`.
- The routes don't hold `app.core.tts.lock`; a hub tool holds it around a run of calls that mustn't interleave
  with another request's.
- Changing an endpoint or message shape needs its own PR or a Discussion (see CONTRIBUTING.md).
