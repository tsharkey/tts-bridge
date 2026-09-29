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


NO_EDITS = {"cards": {}, "tooltips": {}}


def save(name, text, leaders=None, edits=None):
    """Save a list as lists/<name>.txt, with the choices made for it beside it in
    <name>.json: which unit each leader leads (army.attach_leaders), and its edits to
    datasheet and tooltip text (apply_edits). None keeps what's already saved (a page that
    doesn't make those choices). -> its name."""
    LISTS.mkdir(exist_ok=True)
    name = list_name(name)
    (LISTS / f"{name}.txt").write_text(text)
    path = LISTS / f"{name}.json"
    choices = json.loads(path.read_text()) if path.exists() else {}
    if leaders is not None:
        choices["leaders"] = leaders
    if edits is not None:
        choices["edits"] = {k: dict(edits.get(k) or {}) for k in NO_EDITS}
    choices = {k: v for k, v in choices.items() if v and v != NO_EDITS}
    if choices:
        path.write_text(json.dumps(choices, indent=1, ensure_ascii=False))
    else:
        path.unlink(missing_ok=True)
    return name


def load(name):
    """{"text", "leaders", "edits"} for a saved list."""
    name = list_name(name)
    path = LISTS / f"{name}.json"
    choices = json.loads(path.read_text()) if path.exists() else {}
    return {"text": (LISTS / f"{name}.txt").read_text(), "leaders": choices.get("leaders", {}),
            "edits": {**NO_EDITS, **choices.get("edits", {})}}


def tooltip_key(unit_key, model):
    """What a model's tooltip edit is saved under: its unit (army.unit_keys), its name and its
    wargear, so the models of a unit with the same loadout share it."""
    return f"{unit_key}|{model['name']}|{', '.join(model['wargear'])}"


def apply_edits(parsed, edits):
    """A list's edits to its datasheet text ("cards": unit key -> text) and tooltips
    ("tooltips": tooltip_key -> text), over what tooltips.attach made. Marks what it changed
    ("card_edited", "tooltip_edited"). Keys that no longer match a unit are ignored."""
    edits = edits or {}
    for key, u in zip(army.unit_keys(parsed["units"]), parsed["units"]):
        card = (edits.get("cards") or {}).get(key)
        if card is not None and u.get("card") is not None:
            u["card"], u["card_edited"] = card, True
        for m in u["models"]:
            text = (edits.get("tooltips") or {}).get(tooltip_key(key, m))
            if text is not None and m.get("tooltip"):
                m["tooltip"], m["tooltip_edited"] = {**m["tooltip"], "text": text}, True


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


def bundle_urls(o):
    """The asset bundles an object (and its attached children) is made of."""
    url = (o.get("CustomAssetbundle") or {}).get("AssetbundleURL")
    return ([url] if url else []) + [u for c in o.get("ChildObjects") or [] for u in bundle_urls(c)]


def bundle_preview(catalog, pick):
    """A catalogue entry made of asset bundles as the viewer's tree (like mesh_parts'), its
    bundles converted by previews.py the first time; any static meshes it has too."""
    import previews
    o = army.pick_object(catalog, pick)
    kids = []
    for url in bundle_urls(o):
        got = previews.bundle_preview(url)
        base = f"/api/catalog/preview/{got['id']}/"
        kids += [{"mesh": base + p["mesh"], "diffuse": base + p["diffuse"] if p["diffuse"] else "",
                  "color": p["color"], "pos": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1], "children": []}
                 for p in got["parts"]]
    if not kids:
        raise ValueError("That model has no asset bundle to preview.")
    tree, _ = mesh_parts(o)
    return {"mesh": "", "diffuse": "", "color": [0.8, 0.8, 0.8], "pos": [0, 0, 0], "rot": [0, 0, 0],
            "scale": [1, 1, 1], "children": kids + ([{**tree, "pos": [0, 0, 0], "rot": [0, 0, 0]}] if tree else [])}


def entry_info(catalog, pick):
    """What the page needs to show (and preview) one catalogue entry, or one of its states
    (army.split_pick). "states": the entry's states to choose between ({"pick", "n", "name"}),
    when it has more than one that look different; "state": which this is."""
    g, i, state = army.split_pick(pick)
    entry = catalog[g][i]
    o = army.pick_object(catalog, pick)
    states = army.states_of(entry)
    shown = next(n for n, s in states if s is entry)
    tree, animated = mesh_parts(o)
    name = (o.get("Nickname") or "").strip() or (entry.get("Nickname") or "").strip()
    info = {"pick": pick, "name": name, "tile": army.tile_label(g),
            "static": o.get("Name") in army.STATIC and not animated, "bundle": bool(bundle_urls(o)),
            "credit": (o.get("Description") or "").strip().split("\n")[0][:80]}
    if info["static"] and tree:
        tree["pos"] = [0, 0, 0]   # the root's own world position doesn't matter for a preview
        tree["rot"] = [0, 0, 0]
        info["preview"] = tree
    if len(states) > 1:
        info["state"] = state or shown
        info["states"] = [{"pick": f"{g}:{i}" if n == shown else f"{g}:{i}:{n}", "n": n,
                           "name": (s.get("Nickname") or "").strip() or name} for n, s in states]
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


def object_urls(o):
    """Every mesh and asset bundle an object's data uses: its own, its attached parts' and its
    other states'. What a model on the table can be matched to the catalogue by."""
    out = []
    for url in ((o.get("CustomMesh") or {}).get("MeshURL"), (o.get("CustomAssetbundle") or {}).get("AssetbundleURL")):
        if url:
            out.append(url)
    for c in (o.get("ChildObjects") or []) + list((o.get("States") or {}).values()):
        out += object_urls(c)
    return list(dict.fromkeys(out))


_urls = {"stamp": None, "index": None}


def url_index(cat):
    """{url: [picks]} over the catalogue: which entries use each mesh or bundle."""
    if _urls["stamp"] is not cat:
        index = {}
        for g, objs in cat.items():
            for i, o in enumerate(objs):
                if o.get("Name") in army.SPAWNABLE:
                    for url in object_urls(o):
                        index.setdefault(url, []).append(f"{g}:{i}")
        _urls.update(stamp=cat, index=index)
    return _urls["index"]


def find_by_urls(urls):
    """The catalogue entries a model on the table is, from the meshes and bundles it uses: those
    sharing the most of them, best first. -> [entry_info]."""
    cat = catalog()
    if cat is None:
        raise ValueError("The Force Org model catalogue isn't built yet. Build it on the Data cache page.")
    index, shared = url_index(cat), {}
    for url in dict.fromkeys(urls):
        for pick in index.get(url, []):
            shared[pick] = shared.get(pick, 0) + 1
    best = max(shared.values(), default=0)
    return [entry_info(cat, p) for p, n in sorted(shared.items()) if n == best]


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


def favourites():
    """The models starred as favourites (army.favourite_picks), as entries."""
    cat = catalog()
    picks = sorted(army.favourite_picks(army.load_mappings()))
    return [entry_info(cat, p) for p in picks if cat and army.split_pick(p)[0] in cat
            and army.split_pick(p)[1] < len(cat[army.split_pick(p)[0]])]


def set_favourite(pick, on=True):
    """Star (or unstar) a model. A favourite is the model's, in its own army: Scribe prefers it
    for any of that army's units it matches (army.Matcher.candidates). -> every favourite."""
    mappings = army.load_mappings()
    picks = sorted((army.favourite_picks(mappings) - {pick}) | ({pick} if on else set()))
    if picks:
        mappings["favorites"] = picks
    else:
        mappings.pop("favorites", None)
    army.MAPPINGS.write_text(json.dumps(mappings, indent=1, ensure_ascii=False))
    return picks


def pin(key, pick):
    mappings = army.load_mappings()
    mappings.setdefault("models", {})[key] = [pick]
    army.MAPPINGS.write_text(json.dumps(mappings, indent=1, ensure_ascii=False))
