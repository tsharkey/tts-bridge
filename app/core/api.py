"""
API plumbing shared by every tool, and the routes every tool can use (TTS
status, LCT setup, saved lists, model picks, and browsing the model catalogue).

Each tool's routes.py makes its own `router()` so it gets the same error
handling: TTS unreachable → 503, anything else → 400, both as {"error": ...}
so the page can show the message.
"""

import traceback

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

import army
from app.core import lct, lists, tts


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
    return {"text": (lists.LISTS / f"{lists.safe_name(name)}.txt").read_text()}


@shared.post("/api/lists")
def save_list(body: dict):
    lists.LISTS.mkdir(exist_ok=True)
    name = lists.safe_name(body["name"])
    (lists.LISTS / f"{name}.txt").write_text(body["text"])
    return {"saved": name}


@shared.post("/api/parse")
def parse_list(body: dict):
    return lists.unit_summary(lists.parse(body["text"]))


@shared.post("/api/pin")
def pin(body: dict):
    lists.pin(body["key"], body["pick"])
    return {"pinned": body["key"]}


@shared.get("/api/favorites")
def favourites(key: str):
    """A unit's favourite figures; key is "<chapter or faction>|<unit>"."""
    return {"key": key, "models": lists.favourites(key)}


@shared.post("/api/favorites")
def set_favourite(body: dict):
    picks = lists.set_favourite(body["key"], body["pick"], bool(body.get("on", True)))
    return {"key": body["key"], "picks": picks}


@shared.get("/api/catalog/armies")
def model_armies():
    """Army names for choosing whose favourites to set: every faction and chapter."""
    return sorted(army.FACTIONS)


@shared.get("/api/catalog/tiles")
def model_tiles(faction: str = "", sub: str = ""):
    return lists.model_tiles(faction or None, sub or None)


@shared.get("/api/catalog")
def models(tiles: str = "", q: str = "", static: bool = False):
    return lists.find_models([t for t in tiles.split(",") if t] or None, q, static)


@shared.get("/api/catalog/entry")
def model_entry(pick: str):
    cat = lists.catalog()
    if cat is None:
        raise ValueError("The Force Org model catalogue isn't built yet.")
    return lists.entry_info(cat, pick)
