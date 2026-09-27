"""
bases.py — official base sizes, from Wahapedia's data export
(`python3 data.py fetch wahapedia`), in our own format.

    import army, bases, datasheets
    parsed = army.parse_list(text, mappings)
    datasheets.attach(parsed, mappings)
    bases.attach(parsed, mappings)          # each model gets "base" (or None)
    parsed["units"][0]["models"][0]["base"]
    # {"shape": "round", "mm": [32], "inches": [1.26], "flying": False, "text": "32mm", "note": ""}

Shapes: "round" (mm: [diameter]), "oval" (mm: [length, width]), "model" (Wahapedia
says "Use model": measure the hull), "none" (no official size). Measurements
are from the edge of the base, so tools take the base from here rather than
from the model's bounding box.

A model is found by its unit's name (in its own army's Wahapedia faction first),
then its model line. Fixes go in mappings.json:

    "bases": "<sub-faction or faction>|<unit>|<model>" -> "32mm"   (any Wahapedia-style size)
"""

import csv
import re

import army
import data
from data import DataError, now, read_json, write_json

MM_PER_INCH = 25.4
FILES = ["Factions.csv", "Datasheets.csv", "Datasheets_models.csv", "Last_update.csv"]


def key(name):
    return re.sub(r"\s*\[legends\]", "", army.clean(name or "").casefold()).strip()


def loose(name):
    """Without a trailing bracketed tag: "Chaos Spawn (Flesh Change)" -> "chaos spawn"."""
    return re.sub(r"\s*[(\[][^)\]]*[)\]]\s*$", "", key(name)).strip()


def parse_base(text, note=""):
    """"32mm", "170 x 109mm", "60mm flying base", "Use model" -> our base dict."""
    t = (text or "").strip()
    out = {"shape": "none", "mm": [], "inches": [], "flying": "flying" in t.lower(), "text": t, "note": note or ""}
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", t)]
    if re.search(r"use model", t, re.I):
        out["shape"] = "model"
    elif nums and "mm" in t.lower():
        out["shape"] = "oval" if len(nums) >= 2 else "round"
        out["mm"] = [int(n) if n.is_integer() else n for n in nums[:2]]
        out["inches"] = [round(n / MM_PER_INCH, 2) for n in nums[:2]]
    return out


# --------------------------------------------------------------------------
# Import: the export's CSVs -> cache/bases/index.json, by faction and datasheet.

def bases_file(cache=None):
    return (cache or data.CACHE) / "bases" / "index.json"


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(f, delimiter="|")]


def import_wahapedia(cache=None, log=print):
    """Import the cached Wahapedia export into cache/bases/. -> the index."""
    info = data.source_info("wahapedia", cache)
    folder = data.raw_dir("wahapedia", cache)
    missing = [f for f in FILES[:3] if not (folder / f).exists()]
    if not info or missing:
        raise DataError("Wahapedia's export isn't fetched yet. Run `python3 data.py fetch wahapedia`."
                        if not info else f"{folder} is missing {', '.join(missing)}.")
    factions = {r["id"]: army.clean(r["name"]) for r in read_csv(folder / "Factions.csv")}
    sheets = {r["id"]: r for r in read_csv(folder / "Datasheets.csv")}
    out = {}
    for r in read_csv(folder / "Datasheets_models.csv"):
        s = sheets.get(r["datasheet_id"])
        if not s:
            continue
        faction = factions.get(s["faction_id"], s["faction_id"])
        sheet = out.setdefault(faction, {}).setdefault(key(s["name"]), {"id": s["id"], "name": s["name"], "lines": {}})
        sheet["lines"][key(r["name"])] = {"name": r["name"], **parse_base(r["base_size"], r.get("base_size_descr"))}
    lines = sum(len(s["lines"]) for f in out.values() for s in f.values())
    index = {"source": info, "imported": now(), "factions": out}
    write_json(bases_file(cache), index)
    log(f"wahapedia: base sizes for {lines} models in {sum(len(f) for f in out.values())} datasheets")
    return index


def load(cache=None):
    return read_json(bases_file(cache))


# --------------------------------------------------------------------------
# Lookup.

def find_sheet(index, unit_name, faction=None):
    """A unit's Wahapedia datasheet: its own faction's first, then any, by
    exact name, then without a bracketed tag."""
    factions = index["factions"]
    order = ([faction] if faction in factions else []) + [f for f in factions if f != faction]
    for match in (key, loose):
        want = match(unit_name)
        for f in order:
            for k, sheet in factions[f].items():
                if (k if match is key else loose(sheet["name"])) == want:
                    return sheet
    return None


def find_line(sheet, model_name, unit_name):
    """The Wahapedia model line for one model, or None when it can't be told."""
    lines = sheet["lines"]
    for name in (model_name, (model_name or "").split(" w/ ")[0]):
        if key(name) in lines:
            return lines[key(name)]
    if len(lines) == 1 or len({(b["text"], b["note"]) for b in lines.values()}) == 1:
        return next(iter(lines.values()))  # one line, or every line the same size
    best = None
    for b in lines.values():
        coverage, fit = army.score(army.tokens_of(b["name"]), {"name": model_name, "wargear": []},
                                   {"name": unit_name}, set(), lambda t: 1)
        if coverage >= 0.5 and (best is None or (coverage, fit) > best[0]):
            best = ((coverage, fit), b)
    return best[1] if best else None


def attach(parsed, mappings, cache=None):
    """Give every model its base ("base": dict, or None when it isn't known).
    -> None when no base sizes are cached (models are left without "base")."""
    index = load(cache)
    if not index:
        return None
    fixes = mappings.get("bases", {})
    scope = parsed["sub"] or parsed["faction"]
    for u in parsed["units"]:
        sheet_name = (u.get("datasheet") or {}).get("name") or u["name"]
        sheet = find_sheet(index, sheet_name, parsed["faction"])
        for m in u["models"]:
            fix = fixes.get(f"{scope}|{u['name']}|{m['name']}")
            if fix:
                m["base"] = {**parse_base(fix), "fixed": True}
                continue
            line = find_line(sheet, m.get("sheet_model") or m["name"], u["name"]) if sheet else None
            m["base"] = {k: v for k, v in line.items() if k != "name"} if line else None
    return index


def missing(parsed):
    """Models with no base size, or only "Use model" / no official size: [(unit, model, why)]."""
    out = []
    for u in parsed["units"]:
        for m in u["models"]:
            b = m.get("base")
            why = "not found" if b is None else {"model": "use the model", "none": "no official size"}.get(b["shape"])
            if why and (u["name"], m["name"], why) not in out:
                out.append((u["name"], m["name"], why))
    return out
