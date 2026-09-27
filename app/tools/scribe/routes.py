"""Scribe's API: read a list into what TTS will get, fix datasheets and models,
then save it as a Saved Object (no TTS needed) or spawn it (TTS needed).

Reading doesn't write mappings.json; saving and spawning do, like
`army.py build`, so the list comes out the same next time.
"""

import json

import army
import bases
import data
import datasheets
import tooltips
import tts_bridge as tts
from app.core.api import router as api_router
from app.core.lists import entry_info
from app.core.tts import lock as tts_lock

router = api_router()


def parse(text, mappings, prefer_static=False, repick=False):
    """-> (parsed list, catalogue or None). The list's parse errors come back
    as ValueError, so the page shows them as the list's problem, not TTS's."""
    if not (text or "").strip():
        raise ValueError("Paste a list first.")
    try:
        parsed = datasheets.parse(text, mappings, repick=repick)
    except SystemExit as e:
        raise ValueError(str(e)) from e
    bases.attach(parsed, mappings)
    tooltips.attach(parsed)
    catalog = army.load_catalog() if army.CATALOG.exists() and any(army.CATALOG.glob("*.json")) else None
    if catalog:
        army.resolve(parsed, catalog, mappings, prefer_static=prefer_static, repick=repick)
    return parsed, catalog


def base_text(m):
    b = m.get("base")
    return None if b is None else (b["text"] or "none") + (f" ({b['note']})" if b.get("note") else "")


def view(parsed, catalog, mappings, prefer_static=False):
    """Everything the page shows about a parsed list."""
    scope = parsed["sub"] or parsed["faction"]
    matcher = army.Matcher(catalog, parsed, mappings.get("aliases", {}).get(parsed["faction"])) if catalog else None
    seen, units = {}, []
    for i, u in enumerate(parsed["units"]):
        seen[u["name"]] = seen.get(u["name"], 0) + 1
        groups = {}
        for m in u["models"]:
            key = army.model_key(parsed["faction"], u, m)
            g = groups.setdefault(key, {"key": key, "model": m["name"], "count": 0, "wargear": m["wargear"],
                                        "gear": [f"{x['count']}x {x['name']}" if x["count"] > 1 else x["name"]
                                                 for x in m.get("gear", [])],
                                        "sheet_model": m.get("sheet_model"), "base": base_text(m),
                                        "pick": m.get("pick"), "tooltip": m.get("tooltip"), "_m": m})
            g["count"] += 1
        for g in groups.values():
            m = g.pop("_m")
            g["picks"] = [entry_info(catalog, g["pick"])] if catalog and g["pick"] else []
            g["options"] = []
            if matcher:
                ranked = matcher.ranked(u, m, u["allied"], prefer_static)
                opts = [f"{t}:{k}" for _, t, k, _ in ranked[:15]]
                if g["pick"] and g["pick"] not in opts:
                    opts.insert(0, g["pick"])
                g["options"] = [entry_info(catalog, p) for p in opts]
        units.append({"i": i, "name": u["name"], "n": seen[u["name"]], "points": u.get("points"),
                      "role": u.get("role"), "attached_to": u.get("attached_to"),
                      "warlord": u.get("warlord", False), "enhancements": u.get("enhancements", []),
                      "complete": u.get("complete", True), "composition": u.get("composition"),
                      "datasheet": u.get("datasheet"), "pin_key": f"{scope}|{u['name']}",
                      "allied": u["allied"], "groups": list(groups.values())})
    models = [m for u in parsed["units"] for m in u["models"]]
    missing = datasheets.unmatched(parsed)
    return {
        "army": {k: parsed.get(k) for k in ("title", "faction", "sub", "format", "detachment", "disposition",
                                            "battle_size", "points")},
        "cached": {"datasheets": any("datasheet" in u for u in parsed["units"]), "catalog": catalog is not None,
                   "bases": any("base" in m for m in models)},
        "units": units,
        "problems": {
            "datasheets": missing["units"] if any("datasheet" in u for u in parsed["units"]) else [],
            "models": [f"{u}: {m}" for u, m in missing["models"]],
            "figures": sorted({f"{u['name']}: {m['name']}" for u in parsed["units"] for m in u["models"]
                               if catalog and not m.get("pick")}),
            "bases": [f"{u}: {m} ({why})" for u, m, why in bases.missing(parsed)],
            "guessed": [u["name"] for u in parsed["units"] if u.get("composition") == "datasheet"],
        },
        "totals": {"models": len(models), "picked": sum(1 for m in models if m.get("pick"))},
        "saved_object": str(army.saved_object_path(parsed)),
    }


def save_mappings(mappings):
    army.MAPPINGS.write_text(json.dumps(mappings, indent=1, ensure_ascii=False))


@router.post("/api/scribe/read")
def read(body: dict):
    mappings = army.load_mappings()
    prefer_static, repick = bool(body.get("prefer_static")), bool(body.get("repick"))
    parsed, catalog = parse(body.get("text"), mappings, prefer_static, repick)
    if repick:
        save_mappings(mappings)
    return view(parsed, catalog, mappings, prefer_static)


@router.get("/api/scribe/datasheets")
def sheet_choices(faction: str, sub: str = ""):
    """Datasheets for the picker: the army's own first, then everything else."""
    sheets = datasheets.Datasheets(sub or faction)
    own = {u["id"] for u in sheets.scope}
    return [{"id": u["id"], "name": u["name"], "catalogue": u["catalogue"], "own": u["id"] in own}
            for u in sheets.everything()]


@router.post("/api/scribe/datasheet")
def pin_datasheet(body: dict):
    mappings = army.load_mappings()
    index = data.datasheet_index() or {"catalogues": {}}
    sheet = None
    for name in index["catalogues"]:
        sheet = next((u for u in (data.load_catalogue(name) or {"units": []})["units"] if u["id"] == body["id"]), None)
        if sheet:
            break
    if not sheet:
        raise ValueError("That datasheet isn't in the cache.")
    mappings.setdefault("datasheets", {})[body["key"]] = {"id": sheet["id"], "name": sheet["name"],
                                                          "catalogue": sheet["catalogue"]}
    save_mappings(mappings)
    return {"pinned": body["key"], "datasheet": sheet["name"]}


def ready(body):
    """Parse for output, pinning every choice as `army.py build` does."""
    mappings = army.load_mappings()
    parsed, catalog = parse(body.get("text"), mappings)
    if not catalog:
        raise ValueError("The Force Org model catalogue isn't built yet. Build it on the Data cache page.")
    save_mappings(mappings)
    return parsed, catalog


@router.post("/api/scribe/save")
def save(body: dict):
    parsed, catalog = ready(body)
    path = army.build_saved_object(parsed, catalog, facing=float(body.get("facing", 180)))
    return {"path": str(path), "models": sum(1 for u in parsed["units"] for m in u["models"] if m.get("pick"))}


def locked_lua(code, **kw):
    # one call at a time, so the header's status check can run in between
    with tts_lock:
        return tts.run_lua(code, **kw)


@router.post("/api/scribe/spawn")
def spawn(body: dict):
    parsed, catalog = ready(body)
    notes = []
    guids = army.spawn(parsed, catalog, float(body.get("x", -55)), float(body.get("z", 55)),
                       float(body.get("width", 110)), float(body.get("facing", 180)),
                       run_lua=locked_lua, log=notes.append)
    return {"spawned": len(guids), "notes": notes}
