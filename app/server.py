"""
server.py — local web app for rebuilding a board state in TTS.

    .venv/bin/python app/server.py        # then open http://localhost:8765

Needs TTS running with a game loaded and the External Editor API on. Only
one process can hold the bridge's listener port, so stop any other
tts_bridge.py / army.py / recreate.py runs while this is up.
"""

import base64
import json
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import army          # noqa: E402
import recreate      # noqa: E402
import tts_bridge as tts  # noqa: E402
import vision        # noqa: E402

PORT = 8765
STATIC = ROOT / "app" / "static"
LISTS = ROOT / "lists"
SCENES = ROOT / "scenes"
tts_lock = threading.Lock()


def safe_name(name):
    name = re.sub(r"[^A-Za-z0-9 _.-]+", "", name or "").strip().replace(" ", "_")
    if not name:
        raise ValueError("Give it a name")
    return name


def parse(text):
    """Parse + match a list, reporting errors instead of exiting."""
    catalog = army.load_catalog()
    mappings = army.load_mappings()
    try:
        parsed = army.parse_list(text, mappings)
    except SystemExit as e:
        raise ValueError(str(e))
    army.resolve(parsed, catalog, mappings)
    return parsed


def mesh_parts(o):
    """An object's meshes as a tree (TTS models are often a base with the figure
    attached as a child). Returns (tree or None, contains an asset bundle)."""
    animated = o.get("Name") == "Custom_Assetbundle"
    kids, part = [], None
    for c in o.get("ChildObjects") or []:
        sub, anim = mesh_parts(c)
        animated = animated or anim
        if sub:
            kids.append(sub)
    mesh = o.get("CustomMesh") or {}
    t = o.get("Transform") or {}
    if mesh.get("MeshURL") or kids:
        c = o.get("ColorDiffuse") or {}
        part = {"mesh": mesh.get("MeshURL") or "", "diffuse": mesh.get("DiffuseURL") or "",
                "color": [c.get("r", 0.8), c.get("g", 0.8), c.get("b", 0.8)],
                "pos": [t.get("posX", 0), t.get("posY", 0), t.get("posZ", 0)],
                "rot": [t.get("rotX", 0), t.get("rotY", 0), t.get("rotZ", 0)],
                "scale": [t.get("scaleX", 1), t.get("scaleY", 1), t.get("scaleZ", 1)],
                "children": kids}
    return part, animated


def entry_info(catalog, pick):
    """What the page needs to show (and preview) one catalogue entry."""
    g, i = pick.split(":")
    o = catalog[g][int(i)]
    tree, animated = mesh_parts(o)
    info = {"pick": pick, "name": (o.get("Nickname") or "").strip(), "tile": army.tile_label(g),
            "static": o.get("Name") in army.STATIC and not animated,
            "credit": (o.get("Description") or "").strip().split("\n")[0][:80]}
    if info["static"] and tree:
        tree["pos"] = [0, 0, 0]   # the root's own world position doesn't matter for a preview
        tree["rot"] = [0, 0, 0]
        info["preview"] = tree
    return info


def army_models(text, prefer_static=False, repick=False):
    """Every model entry in a list, grouped per unit, with the current pick and
    the closest alternatives."""
    catalog = army.load_catalog()
    mappings = army.load_mappings()
    try:
        parsed = army.parse_list(text, mappings)
    except SystemExit as e:
        raise ValueError(str(e))
    army.resolve(parsed, catalog, mappings, prefer_static=prefer_static, repick=repick)
    if repick:
        army.MAPPINGS.write_text(json.dumps(mappings, indent=1, ensure_ascii=False))
    matcher = army.Matcher(catalog, parsed, mappings.get("aliases", {}).get(parsed["faction"]))
    seen, units = {}, []
    for u in parsed["units"]:
        seen[u["name"]] = seen.get(u["name"], 0) + 1
        groups = {}
        for m in u["models"]:
            key = army.model_key(parsed["faction"], u, m)
            if key not in groups:
                groups[key] = {"key": key, "model": m["name"], "wargear": m["wargear"], "count": 0,
                               "picks": mappings["models"].get(key, []), "options": None, "_m": m}
            groups[key]["count"] += 1
        for gr in groups.values():
            ranked = matcher.ranked(u, gr.pop("_m"), u["allied"], prefer_static)
            opts = [f"{g}:{i}" for _, g, i, _ in ranked[:15]]
            opts = [p for p in gr["picks"] if p not in opts] + opts
            gr["options"] = [entry_info(catalog, p) for p in opts]
            gr["picks"] = [entry_info(catalog, p) for p in gr["picks"]]
        units.append({"name": u["name"], "n": seen[u["name"]], "groups": list(groups.values())})
    return {"title": parsed["title"], "faction": parsed["faction"], "units": units}


def unit_summary(parsed):
    seen, out = {}, []
    for u in parsed["units"]:
        seen[u["name"]] = seen.get(u["name"], 0) + 1
        out.append({"name": u["name"], "n": seen[u["name"]], "count": len(u["models"]),
                    "unmatched": sum(1 for m in u["models"] if not m["pick"])})
    return {"title": parsed["title"], "faction": parsed["faction"], "sub": parsed["sub"],
            "units": out, "models": sum(u["count"] for u in out)}


def tts_status():
    with tts_lock:
        try:
            r = tts.run_lua('return getObjectFromGUID("738804") ~= nil and "lct" or "game"', timeout=3)
        except SystemExit:
            r = None
    return {"connected": r is not None, "lct": r == "lct"}


# --------------------------------------------------------------------------
# LCT table setup (dispositions, matchup, layout). Everything goes through
# the Start Menu object's own functions, exactly like clicking its buttons.

START_MENU = "738804"
DISPOSITIONS = ["Disruption", "Priority Assets", "Purge the Foe", "Reconnaissance", "Take and Hold"]
DEPLOYMENTS = {"SE": "Sweeping Engagement", "CoB": "Crucible of Battle", "TP": "Tipping Point",
               "SnD": "Search and Destroy", "DoW": "Dawn of War", "HnA": "Hammer and Anvil"}

MATCHUPS_LUA = """
local m = getObjectFromGUID("738804")
if not m then return "no-lct" end
local out = {}
local missions = Global.getTable("missionMatchups") or {}
for key, deck in pairs(m.getTable("deploymentMatrixDecks") or {}) do
  local label, layouts = nil, {}
  for _, v in pairs(deck) do
    if type(v) == "string" and v:find(" vs ") then label = v end
    if type(v) == "table" then
      for _, c in pairs(v) do if type(c) == "table" and c.name then table.insert(layouts, c.name) end end
    end
  end
  local mm = missions[key] or {}
  out[key] = {label = label, layouts = layouts, red = mm.red, blue = mm.blue}
end
-- layout art: one card per map in deck fb4b5d, each with its own image
local art = {}
local deck = getObjectFromGUID("fb4b5d")
if deck then
  for _, c in ipairs(deck.getData().ContainedObjects or {}) do
    for _, sheet in pairs(c.CustomDeck or {}) do
      art[#art + 1] = {name = c.Nickname, url = sheet.FaceURL}
      break
    end
  end
end
return {matchups = out, art = art}
"""
_matchups = None


def lct_matchups():
    global _matchups
    if _matchups is None:
        with tts_lock:
            r = tts.run_lua(MATCHUPS_LUA, timeout=10)
        if r in (None, "no-lct"):
            raise ValueError("Load the LCT table in TTS first")
        raw = json.loads(r)
        # art card "Rec vs Rec 1 - Sweeping Engagement" belongs to map "Rec vs Rec 1"
        art = {a["name"].split(" - ")[0].strip(): a["url"] for a in raw["art"] if a.get("name")}
        _matchups = {}
        for key, v in raw["matchups"].items():
            layouts = []
            for name in sorted(v["layouts"]):   # "Rec vs Rec 1 - SE"
                prefix, _, code = name.rpartition(" - ")
                layouts.append({"card": prefix, "deployment": DEPLOYMENTS.get(code, code),
                                "art": art.get(prefix)})
            _matchups[key] = {"label": v["label"], "red_mission": v["red"], "blue_mission": v["blue"],
                              "layouts": layouts}
    return _matchups


def lua_menu(expr):
    return tts.run_lua(f'local m = getObjectFromGUID("{START_MENU}") {expr}', timeout=10)


FIND_MAP_CARD_LUA = """
for _, o in ipairs(getObjects()) do
  if o.tag == "Card" and o.getName():sub(1, {n}) == {prefix} then
    for _, b in ipairs(o.getButtons() or {{}}) do
      if b.click_function == "loadMap" then return o.guid .. "|" .. o.getName() end
    end
  end
end
return "none"
"""


def menu_buttons():
    r = lua_menu('local out = {} for _, b in ipairs(m.getButtons() or {}) do table.insert(out, b.click_function) end '
                 'return out')
    return set(json.loads(r or "[]"))


def lct_setup(red, blue, layout):
    """Drive LCT's Start Menu: 1v1, dispositions, deal, load the chosen layout."""
    matchup = lct_matchups()[f"{red}_{blue}"]
    card_prefix = matchup["layouts"][layout]["card"] + " - "
    find_card = FIND_MAP_CARD_LUA.format(n=len(card_prefix), prefix=json.dumps(card_prefix))
    with tts_lock:
        if lua_menu('return m.getVar("gameMode")') != "game":
            lua_menu('m.call("confirmStandardGame") return 1')
            time.sleep(2)
        if "backToSelection" in menu_buttons():
            # a layout is already loaded; LCT only deals layout cards from the selection screen
            lua_menu('m.call("backToSelection") return 1')
            time.sleep(3)
        for color, target in (("red", red), ("blue", blue)):
            for _ in range(6):
                if str(lua_menu(f'return tostring(m.getVar("{color}DispositionSelected"))')) == str(target):
                    break
                lua_menu(f'm.call("{color}DispositionUp") return 1')
                time.sleep(0.4)
            else:
                raise RuntimeError(f"Couldn't set {color} disposition")
        card = tts.run_lua(find_card, timeout=10)
        if card in (None, "none"):
            lua_menu('m.call("generateMission") return 1')
            for _ in range(40):
                time.sleep(0.5)
                if lua_menu('return tostring(m.getVar("missionDealInProgress"))') == "true":
                    continue
                card = tts.run_lua(find_card, timeout=10)
                if card not in (None, "none"):
                    break
        if card in (None, "none"):
            raise RuntimeError(f"LCT didn't deal the {card_prefix[:-3]} card")
        guid, name = card.split("|", 1)
        tts.run_lua(f'local c = getObjectFromGUID("{guid}") c.call("loadMap", {{c, "Red", false}}) return 1')
        time.sleep(2.5)  # terrain spawns ~0.5s after the wipe; zones and objectives just after
    return {"loaded": name, "matchup": matchup["label"],
            "red_mission": matchup["red_mission"], "blue_mission": matchup["blue_mission"]}


# --------------------------------------------------------------------------

def api_get(path, q):
    if path == "/api/status":
        return tts_status()
    if path == "/api/models":
        return vision.vision_models()
    if path == "/api/lct/matchups":
        return {"dispositions": DISPOSITIONS, "matchups": lct_matchups()}
    if path == "/api/lists":
        return sorted(p.stem for p in LISTS.glob("*.txt"))
    if path == "/api/list":
        return {"text": (LISTS / f"{safe_name(q['name'][0])}.txt").read_text()}
    if path == "/api/scenes":
        return sorted(p.stem for p in SCENES.glob("*.json"))
    if path == "/api/scene":
        name = safe_name(q["name"][0])
        scene = json.loads((SCENES / f"{name}.json").read_text())
        for a in scene["armies"]:
            if "list_text" not in a:
                a["list_text"] = (ROOT / a["list"]).read_text()
        scene["has_image"] = (SCENES / f"{name}.image").exists()
        return scene
    raise KeyError(path)


def api_post(path, body):
    if path == "/api/lists":
        LISTS.mkdir(exist_ok=True)
        name = safe_name(body["name"])
        (LISTS / f"{name}.txt").write_text(body["text"])
        return {"saved": name}

    if path == "/api/lct/setup":
        return lct_setup(int(body["red"]), int(body["blue"]), int(body["layout"]))

    if path == "/api/import_vod":
        # The 40K VOD Index ingest tool is the only place list text lives.
        base = (body.get("ingest") or "http://localhost:3001").rstrip("/")
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

    if path == "/api/army_models":
        return army_models(body["text"], bool(body.get("prefer_static")), bool(body.get("repick")))

    if path == "/api/pin":
        mappings = army.load_mappings()
        mappings.setdefault("models", {})[body["key"]] = [body["pick"]]
        army.MAPPINGS.write_text(json.dumps(mappings, indent=1, ensure_ascii=False))
        return {"pinned": body["key"]}

    if path == "/api/parse":
        return unit_summary(parse(body["text"]))

    if path == "/api/board":
        img = vision.decode_data_url(body["image"])
        return {"board": vision.to_data_url(vision.board_image(img, body["rect"]))}

    if path == "/api/analyze":
        armies = [{"parsed": parse(a["text"]), "hint": a.get("hint", "")} for a in body["armies"]]
        img = vision.decode_data_url(body["image"])
        t0 = time.time()
        units, notes, board, usage = vision.analyze(body["api_key"], body["model"], img, body["rect"], armies,
                                                    body.get("deployment"))
        placed, problems = vision.validate(units, armies)
        return {"units": placed, "problems": problems, "notes": notes, "usage": usage,
                "seconds": round(time.time() - t0, 1), "board": vision.to_data_url(board)}

    if path == "/api/place":
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
            loaded = lct_setup(int(su["red"]), int(su["blue"]), int(su["layout"]))["loaded"]
            scene["layout"] = loaded
            (SCENES / f"{name}.json").write_text(json.dumps(scene, indent=1))
        with tts_lock:
            summary = recreate.place_scene(scene, f"recreate:{name}", keep=bool(body.get("keep")),
                                           log=lambda s: None)
        return {"saved": name, "layout": loaded, **summary}

    if path == "/api/place_saved":
        name = safe_name(body["name"])
        scene = json.loads((SCENES / f"{name}.json").read_text())
        with tts_lock:
            summary = recreate.place_scene(scene, f"recreate:{name}", keep=bool(body.get("keep")),
                                           log=lambda s: None)
        return {"saved": name, **summary}

    raise KeyError(path)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if "/api/" in self.path and "/api/status" not in self.path:
            sys.stderr.write("%s\n" % (fmt % args))

    def send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_api(self, fn, *args):
        try:
            self.send(200, fn(*args))
        except KeyError as e:
            self.send(404, {"error": f"Not found: {e}"})
        except SystemExit as e:  # tts_bridge exits when TTS isn't reachable
            self.send(503, {"error": str(e) or "TTS isn't responding"})
        except Exception as e:  # report every failure to the page
            traceback.print_exc()
            self.send(400, {"error": str(e)})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/scene_image":
            f = SCENES / f"{safe_name(parse_qs(u.query)['name'][0])}.image"
            if not f.exists():
                return self.send(404, b"", "text/plain")
            raw = f.read_bytes()
            ctype = ("image/png" if raw[:4] == b"\x89PNG" else "image/webp" if raw[8:12] == b"WEBP"
                     else "image/gif" if raw[:3] == b"GIF" else "image/jpeg")
            return self.send(200, raw, ctype)
        if u.path.startswith("/api/"):
            return self.handle_api(api_get, u.path, parse_qs(u.query))
        f = STATIC / ("index.html" if u.path == "/" else u.path.lstrip("/"))
        if not f.resolve().is_relative_to(STATIC) or not f.is_file():
            return self.send(404, b"not found", "text/plain")
        ctype = {"html": "text/html", "js": "text/javascript", "css": "text/css"}.get(f.suffix[1:], "application/octet-stream")
        self.send(200, f.read_bytes(), ctype + "; charset=utf-8")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        self.handle_api(api_post, urlparse(self.path).path, body)


def main():
    tts.start_listener()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"TTS Bridge app running at http://localhost:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
