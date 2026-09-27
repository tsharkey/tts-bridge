"""Saved army lists, parsing and model picks, as the hub pages need them."""

import json
import re

import army
import datasheets
from app import ROOT

LISTS = ROOT / "lists"


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
    datasheets.attach(parsed, mappings)
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
                    "datasheet": (u.get("datasheet") or {}).get("name"),
                    "unmatched": sum(1 for m in u["models"] if not m["pick"])})
    return {"title": parsed["title"], "faction": parsed["faction"], "sub": parsed["sub"],
            "units": out, "models": sum(u["count"] for u in out)}


def pin(key, pick):
    mappings = army.load_mappings()
    mappings.setdefault("models", {})[key] = [pick]
    army.MAPPINGS.write_text(json.dumps(mappings, indent=1, ensure_ascii=False))
