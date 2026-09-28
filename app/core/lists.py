"""Saved army lists, parsing and model picks, as the hub pages need them."""

import json
import re

import army
import bases
import datasheets
from app import ROOT

LISTS = ROOT / "lists"


def safe_name(name):
    name = re.sub(r"[^A-Za-z0-9 _.-]+", "", name or "").strip().replace(" ", "_")
    if not name:
        raise ValueError("Give it a name")
    return name


def list_name(name):
    """A saved list's name as its file name. It's also the army's name in TTS
    (Scribe), so it keeps spaces, apostrophes and brackets; no path characters."""
    name = re.sub(r"[^\w '()&+,!-]+", "", name or "").strip()
    if not name:
        raise ValueError("Give it a name")
    return name


def save(name, text, leaders=None):
    """Save a list as lists/<name>.txt, with the choices made for it (which unit
    each leader leads; army.attach_leaders) beside it in <name>.json. Leaders
    None keeps the choices already saved (a page that doesn't make them). -> its name."""
    LISTS.mkdir(exist_ok=True)
    name = list_name(name)
    (LISTS / f"{name}.txt").write_text(text)
    choices = LISTS / f"{name}.json"
    if leaders:
        choices.write_text(json.dumps({"leaders": leaders}, indent=1, ensure_ascii=False))
    elif leaders is not None:
        choices.unlink(missing_ok=True)
    return name


def load(name):
    """{"text", "leaders"} for a saved list."""
    name = list_name(name)
    choices = LISTS / f"{name}.json"
    return {"text": (LISTS / f"{name}.txt").read_text(),
            "leaders": json.loads(choices.read_text()).get("leaders", {}) if choices.exists() else {}}


def parse(text):
    """Parse + match a list, reporting errors instead of exiting."""
    catalog = army.load_catalog()
    mappings = army.load_mappings()
    try:
        parsed = datasheets.parse(text, mappings)
    except SystemExit as e:
        raise ValueError(str(e))
    bases.attach(parsed, mappings)
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


_catalog = {"mtime": None, "data": None}


def catalog():
    """The Force Org catalogue, reloaded when catalog/ changes; None when it isn't built."""
    files = list(army.CATALOG.glob("*.json")) if army.CATALOG.exists() else []
    if not files:
        return None
    stamp = (str(army.CATALOG), len(files), max(p.stat().st_mtime for p in files))
    if _catalog["mtime"] != stamp:
        _catalog.update(mtime=stamp, data=army.load_catalog())
    return _catalog["data"]


def model_tiles(faction=None, sub=None):
    """Every army tile with how many models it has; with a faction, the tiles
    that army may take models from are marked "army" (as the matcher scopes them)."""
    cat = catalog()
    if cat is None:
        raise ValueError("The Force Org model catalogue isn't built yet. Build it on the Data cache page.")
    scope = army.Matcher(cat, {"faction": faction, "sub": sub or None}).scope if faction else set()
    def label(g):
        name = army.tile_label(g)
        return f"Other models ({g})" if name == g else name
    out = [{"tile": g, "label": label(g), "army": g in scope,
            "models": sum(1 for o in objs if o.get("Name") in army.SPAWNABLE and (o.get("Nickname") or "").strip())}
           for g, objs in cat.items()]
    return sorted(out, key=lambda t: (not t["army"], t["label"]))


def find_models(tiles=None, query="", static_only=False, limit=2000):
    """Catalogue models, by name: in the given tiles (all when None), whose
    name has every word of `query`, static meshes only if asked."""
    cat = catalog()
    if cat is None:
        raise ValueError("The Force Org model catalogue isn't built yet. Build it on the Data cache page.")
    words = [w for w in army.clean(query or "").casefold().split() if w]
    out = []
    for g, objs in cat.items():
        if tiles and g not in tiles:
            continue
        for i, o in enumerate(objs):
            name = (o.get("Nickname") or "").strip()
            if o.get("Name") not in army.SPAWNABLE or not name:
                continue
            if words and not all(w in army.clean(name).casefold() for w in words):
                continue
            static = army.is_static(o)
            if static_only and not static:
                continue
            out.append({"pick": f"{g}:{i}", "name": name, "tile": army.tile_label(g), "static": static,
                        "credit": (o.get("Description") or "").strip().split("\n")[0][:80]})
    out.sort(key=lambda m: (army.clean(m["name"]).casefold(), m["tile"]))
    return {"models": out[:limit], "total": len(out)}


def unit_summary(parsed):
    seen, out = {}, []
    for u in parsed["units"]:
        seen[u["name"]] = seen.get(u["name"], 0) + 1
        out.append({"name": u["name"], "n": seen[u["name"]], "count": len(u["models"]),
                    "datasheet": (u.get("datasheet") or {}).get("name"),
                    "guessed": u.get("composition") == "datasheet",
                    "unmatched": sum(1 for m in u["models"] if not m["pick"])})
    has_sheets = any("datasheet" in u for u in parsed["units"])
    return {"title": parsed["title"], "faction": parsed["faction"], "sub": parsed["sub"],
            "units": out, "models": sum(u["count"] for u in out), "datasheets_cached": has_sheets,
            "no_datasheet": [u["name"] for u, p in zip(out, parsed["units"]) if has_sheets and not p.get("datasheet")],
            "guessed": [u["name"] for u in out if u["guessed"]]}


def favourites(key):
    """A unit's favourite figures ("<chapter or faction>|<unit>" -> picks), as entries."""
    cat = catalog()
    picks = army.load_mappings().get("favorites", {}).get(key, [])
    return [entry_info(cat, p) for p in picks if cat and p.split(":")[0] in cat
            and int(p.split(":")[1]) < len(cat[p.split(":")[0]])]


def set_favourite(key, pick, on=True):
    """Add (or remove) a figure from a unit's favourites. -> the unit's favourites."""
    mappings = army.load_mappings()
    favs = mappings.setdefault("favorites", {})
    picks = [p for p in favs.get(key, []) if p != pick] + ([pick] if on else [])
    if picks:
        favs[key] = picks
    else:
        favs.pop(key, None)
    army.MAPPINGS.write_text(json.dumps(mappings, indent=1, ensure_ascii=False))
    return picks


def pin(key, pick):
    mappings = army.load_mappings()
    mappings.setdefault("models", {})[key] = [pick]
    army.MAPPINGS.write_text(json.dumps(mappings, indent=1, ensure_ascii=False))
