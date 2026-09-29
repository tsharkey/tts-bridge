"""
API plumbing shared by every tool, and the routes every tool can use (TTS
status and the TTS gateway, LCT setup, saved lists, model picks, and browsing
the model catalogue).

/api/tts/lua runs any Lua you send it in your game. The hub binds 127.0.0.1
only and refuses other Host names, so only programs on this computer reach it.

Each tool's routes.py makes its own `router()` so it gets the same error
handling: TTS unreachable → 503, anything else → 400, both as {"error": ...}
so the page can show the message.
"""

import math
import traceback

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.routing import APIRoute

import previews
import tts_bridge
from app.core import lct, lists, tts


MIN_TIMEOUT, MAX_TIMEOUT = 0.1, 600   # seconds, for /api/tts/lua


class ApiRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except HTTPException:
                raise
            except SystemExit as e:  # tts_bridge exits when TTS isn't reachable
                return JSONResponse({"error": str(e) or "TTS isn't responding"}, 503)
            except Exception as e:  # report every failure to the page
                traceback.print_exc()
                return JSONResponse({"error": str(e)}, 400)
        return handle


def router():
    return APIRouter(route_class=ApiRoute)


shared = router()


@shared.get("/api/status")
def status():
    return tts.status()


@shared.get("/api/tts")
def gateway():
    """How the CLI tools tell the hub from something else on its port."""
    return {"gateway": True}


@shared.post("/api/tts/lua")
def tts_lua(body: dict):
    """Run Lua in the game: {script, timeout} -> {ok, result} or {ok: false, error}.
    The timeout is clamped to 0.1..600 s, so a caller can't hold a worker thread for good."""
    timeout = float(body.get("timeout", 10))
    if not math.isfinite(timeout):
        raise ValueError("timeout must be a number of seconds")
    return tts_bridge.execute(body["script"], min(max(timeout, MIN_TIMEOUT), MAX_TIMEOUT))


@shared.get("/api/tts/events")
def tts_events(request: Request):
    return StreamingResponse(tts.events(request.is_disconnected), media_type="text/event-stream")


@shared.get("/api/lct/matchups")
def lct_matchups():
    return {"dispositions": lct.DISPOSITIONS, "matchups": lct.lct_matchups()}


@shared.post("/api/lct/setup")
def lct_setup(body: dict):
    return lct.lct_setup(int(body["red"]), int(body["blue"]), int(body["layout"]))


@shared.get("/api/lists")
def saved_lists():
    return sorted(p.stem for p in lists.LISTS.glob("*.txt"))


@shared.get("/api/list")
def saved_list(name: str):
    return lists.load(name)


@shared.post("/api/lists")
def save_list(body: dict):
    return {"saved": lists.save(body["name"], body["text"], body.get("leaders"))}


@shared.post("/api/parse")
def parse_list(body: dict):
    return lists.unit_summary(lists.parse(body["text"]))


@shared.post("/api/pin")
def pin(body: dict):
    lists.pin(body["key"], body["pick"])
    return {"pinned": body["key"]}


@shared.get("/api/favorites")
def favourites():
    """The models starred as favourites."""
    return {"models": lists.favourites()}


@shared.post("/api/favorites")
def set_favourite(body: dict):
    """Star ({"pick", "on": true}) or unstar a model."""
    return {"picks": lists.set_favourite(body["pick"], bool(body.get("on", True)))}


@shared.get("/api/catalog/tiles")
def model_tiles(faction: str = "", sub: str = ""):
    return lists.model_tiles(faction or None, sub or None)


@shared.get("/api/catalog")
def models(tiles: str = "", q: str = "", static: bool = False):
    return lists.find_models([t for t in tiles.split(",") if t] or None, q, static)


@shared.get("/api/catalog/bundle")
def model_bundle(pick: str):
    """An asset bundle model as the 3D viewer's parts, converted the first time (previews.py:
    a few seconds for a big model, then it's kept)."""
    cat = lists.catalog()
    if cat is None:
        raise ValueError("The Force Org model catalogue isn't built yet.")
    return {"preview": lists.bundle_preview(cat, pick)}


@shared.get("/api/catalog/preview/{key}/{name}")
def model_preview_file(key: str, name: str):
    return FileResponse(previews.preview_file(key, name))


# What each player has selected in TTS, with the meshes and bundles each object uses (its own,
# its attached parts' and its other states', as lists.object_urls reads them), read-only.
SELECTED_LUA = """
local function urls(d, out)
  if d.CustomMesh and d.CustomMesh.MeshURL and d.CustomMesh.MeshURL ~= "" then table.insert(out, d.CustomMesh.MeshURL) end
  if d.CustomAssetbundle and d.CustomAssetbundle.AssetbundleURL and d.CustomAssetbundle.AssetbundleURL ~= "" then
    table.insert(out, d.CustomAssetbundle.AssetbundleURL)
  end
  for _, c in ipairs(d.ChildObjects or {}) do urls(c, out) end
  for _, s in pairs(d.States or {}) do urls(s, out) end
  return out
end
local out = {}
for _, p in ipairs(Player.getPlayers()) do
  for _, o in ipairs(p.getSelectedObjects() or {}) do
    table.insert(out, {guid = o.getGUID(), name = o.getName(), player = p.color, urls = urls(o.getData(), {})})
  end
end
return out
"""


@shared.get("/api/catalog/selected")
def selected_models():
    """The models players have selected in TTS, each with the catalogue entries it is (matched
    by the meshes and bundles it uses, so it needn't have been spawned by tts-bridge)."""
    with tts.lock:
        got = tts_bridge.run_lua(SELECTED_LUA, timeout=10)
    if got is None:
        raise ValueError("TTS didn't answer. Is a game loaded?")
    objects = list(got.values()) if isinstance(got, dict) else got or []
    if not objects:
        raise ValueError("Nothing is selected in TTS. Click a model there (or drag a box round some), then try again.")
    return [{"guid": o.get("guid"), "name": o.get("name") or "", "player": o.get("player"),
             "matches": lists.find_by_urls(list((o.get("urls") or {}).values()) if isinstance(o.get("urls"), dict)
                                           else o.get("urls") or [])} for o in objects]


@shared.get("/api/catalog/entry")
def model_entry(pick: str):
    cat = lists.catalog()
    if cat is None:
        raise ValueError("The Force Org model catalogue isn't built yet.")
    return lists.entry_info(cat, pick)
