"""Board from image's API: vision analysis, scenes, and sending a scene to TTS."""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import Response

import config
import recreate
from app import ROOT
from app.core import lct
from app.core.api import router as api_router
from app.core.lists import parse, safe_name
from app.core.tts import lock as tts_lock
from app.tools.board_replay import vision

SCENES = ROOT / "scenes"
router = api_router()


@router.get("/api/config")
def app_config():
    return {"vod_import": bool(config.VOD_INGEST_URL)}


@router.get("/api/models")
def models():
    return vision.vision_models()


@router.get("/api/scenes")
def scenes():
    return sorted(p.stem for p in SCENES.glob("*.json"))


@router.get("/api/scene")
def scene(name: str):
    name = safe_name(name)
    scene = json.loads((SCENES / f"{name}.json").read_text())
    for a in scene["armies"]:
        if "list_text" not in a:
            a["list_text"] = (ROOT / a["list"]).read_text()
    scene["has_image"] = (SCENES / f"{name}.image").exists()
    return scene


@router.get("/api/scene_image")
def scene_image(name: str):
    f = SCENES / f"{safe_name(name)}.image"
    if not f.exists():
        return Response(status_code=404)
    raw = f.read_bytes()
    ctype = ("image/png" if raw[:4] == b"\x89PNG" else "image/webp" if raw[8:12] == b"WEBP"
             else "image/gif" if raw[:3] == b"GIF" else "image/jpeg")
    return Response(raw, media_type=ctype)


@router.post("/api/import_vod")
def import_vod(body: dict):
    # Personal integration: the maintainer's local 40K VOD Index ingest tool.
    # Off unless VOD_INGEST_URL is set (see .env.example).
    base = config.VOD_INGEST_URL
    if not base:
        raise ValueError("The VOD Index import isn't enabled. Set VOD_INGEST_URL in .env.")
    url = f"{base}/api/export/tts?" + urllib.parse.urlencode({"game": body["game"]})
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            raise ValueError(json.loads(e.read()).get("error") or f"Ingest tool answered {e.code}")
        except json.JSONDecodeError:
            raise ValueError(f"Ingest tool answered {e.code}")
    except urllib.error.URLError:
        raise ValueError(f"Couldn't reach the ingest tool at {base}. Is `pnpm ingest` running?")


@router.post("/api/board")
def board(body: dict):
    img = vision.decode_data_url(body["image"])
    return {"board": vision.to_data_url(vision.board_image(img, body["rect"]))}


@router.post("/api/analyze")
def analyze(body: dict):
    armies = [{"parsed": parse(a["text"]), "hint": a.get("hint", "")} for a in body["armies"]]
    img = vision.decode_data_url(body["image"])
    t0 = time.time()
    units, notes, board, usage = vision.analyze(body["api_key"], body["model"], img, body["rect"], armies,
                                                body.get("deployment"))
    placed, problems = vision.validate(units, armies)
    return {"units": placed, "problems": problems, "notes": notes, "usage": usage,
            "seconds": round(time.time() - t0, 1), "board": vision.to_data_url(board)}


@router.post("/api/place")
def place(body: dict):
    name = safe_name(body["name"])
    face = vision.facings(body.get("deployment"))
    scene = {
        "source": body.get("source", "web app"),
        "rect": body.get("rect"),
        "deployment": body.get("deployment"),
        "table": body.get("table"),   # {red, blue, layout} picked in the page
        "armies": [{"side": ("red", "blue")[i], "list_text": a["text"], "facing": face[i], "units": a["units"]}
                   for i, a in enumerate(body["armies"])],
    }
    SCENES.mkdir(exist_ok=True)
    (SCENES / f"{name}.json").write_text(json.dumps(scene, indent=1))
    if body.get("image"):
        raw = base64.b64decode(body["image"].split(",", 1)[1])
        (SCENES / f"{name}.image").write_bytes(raw)
    loaded = None
    if body.get("setup"):  # load the LCT layout first; it wipes the mat
        su = body["setup"]
        loaded = lct.lct_setup(int(su["red"]), int(su["blue"]), int(su["layout"]))["loaded"]
        scene["layout"] = loaded
        (SCENES / f"{name}.json").write_text(json.dumps(scene, indent=1))
    with tts_lock:
        summary = recreate.place_scene(scene, f"recreate:{name}", keep=bool(body.get("keep")),
                                       log=lambda s: None)
    return {"saved": name, "layout": loaded, **summary}


@router.post("/api/place_saved")
def place_saved(body: dict):
    name = safe_name(body["name"])
    scene = json.loads((SCENES / f"{name}.json").read_text())
    with tts_lock:
        summary = recreate.place_scene(scene, f"recreate:{name}", keep=bool(body.get("keep")),
                                       log=lambda s: None)
    return {"saved": name, **summary}
