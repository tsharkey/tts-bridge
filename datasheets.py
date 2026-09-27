"""
datasheets.py — match a parsed army list to datasheets in the local data cache
(`python3 data.py fetch bsdata`).

    import army, datasheets
    parsed = army.parse_list(text, mappings)
    sheets = datasheets.attach(parsed, mappings)    # None when nothing is cached
    parsed["units"][0]["datasheet"]                  # {"id", "name", "catalogue", "how"}, None if unmatched
    sheets.get(parsed["units"][0]["datasheet"]["id"])   # the whole datasheet

Each unit gets `datasheet`, and each model `sheet_model` (the datasheet's model
it is) and `sheet_wargear` (the datasheet's name for each of its `wargear`, None
where there's no match). A unit is matched by its pin, then by exact name, then
by fuzzy score, searching the army's own catalogue first (a chapter, then the
catalogues it imports, like Space Marines); allied units may also come from any
other catalogue. Every match is pinned in mappings.json:

    "datasheets": "<sub-faction or faction>|<unit>" -> {"id", "name", "catalogue"}

Pins are kept by the source's id, which survives renames upstream; the name is
there to read. A pin whose id has gone is matched again.
"""

import re

import army
import data


def key(name):
    """Names compare without case, curly apostrophes or a "[Legends]" tag."""
    return re.sub(r"\s*\[legends\]", "", army.clean(name).casefold()).strip()


def base_weapon(name):
    """"Ion accelerator - overcharge" -> "Ion accelerator"."""
    return name.split(" - ")[0].strip()


def flat(weight=None):
    return weight or (lambda t: 1)


class Datasheets:
    """The datasheets an army can take, in the order they're searched."""

    def __init__(self, faction, cache=None):
        self.cache = cache
        self.scope = data.datasheets(faction, cache)
        self._everything = None

    def __bool__(self):
        return bool(self.scope)

    def everything(self):
        """Every datasheet in the cache, the army's own first."""
        if self._everything is None:
            seen = {u["id"] for u in self.scope}
            out = list(self.scope)
            index = data.datasheet_index(self.cache) or {"catalogues": {}}
            for name in index["catalogues"]:
                for u in (data.load_catalogue(name, self.cache) or {"units": []})["units"]:
                    if u["id"] not in seen:
                        seen.add(u["id"])
                        out.append(u)
            self._everything = out
        return self._everything

    def get(self, sheet_id):
        return next((u for u in self.scope if u["id"] == sheet_id), None) or \
            next((u for u in self.everything() if u["id"] == sheet_id), None)

    def find(self, unit_name, allied=False):
        """-> (datasheet, how) or (None, None), by exact name then fuzzy score."""
        pools = [self.scope] + ([self.everything()] if allied else [])
        for pool in pools:
            exact = next((u for u in pool if key(u["name"]) == key(unit_name)), None)
            if exact:
                return exact, "exact"
        best = None
        for pool in pools:
            for u in pool:
                coverage, fit = army.score(army.tokens_of(u["name"]), {"name": unit_name, "wargear": []},
                                           {"name": unit_name}, set(), flat())
                if coverage >= 0.75 and (best is None or (coverage, fit) > best[0]):
                    best = ((coverage, fit), u)
            if best:
                return best[1], f"fuzzy {best[0][0]:.0%}"
        return None, None


def match_unit(sheets, unit, pin_key, pins, repick=False, made=None):
    """-> (datasheet or None, how), pinning what it finds. `made` remembers
    how this run matched each pin, so a second copy of a unit says so too."""
    made = {} if made is None else made
    if pin_key in made:
        return sheets.get(pins[pin_key]["id"]), made[pin_key]
    pin = pins.get(pin_key)
    if pin and not repick:
        sheet = sheets.get(pin.get("id"))
        if sheet:
            return sheet, "pinned"
    sheet, how = sheets.find(unit["name"], unit.get("allied"))
    if sheet:
        if pin and not repick:
            how += ", pin had gone"
        pins[pin_key] = {"id": sheet["id"], "name": sheet["name"], "catalogue": sheet["catalogue"]}
        made[pin_key] = how
    return sheet, how


def carries(sheet_model, sheet):
    """Names of everything a datasheet model can have: its wargear, and the
    weapons in that wargear (a Missile Drone carries a "Missile pod")."""
    names = {e["name"] for e in sheet_model["equipped"]}
    for opt in sheet_model["options"]:
        names.update(opt["choices"])
    out = {key(n) for n in names}
    for n in names:
        for w in (sheet["wargear"].get(n) or {}).get("weapons", []):
            out.add(key(base_weapon(w["name"])))
    return out


def match_wargear(name, sheet):
    """The datasheet's wargear name for a list's wargear line, or None."""
    k = key(name)
    for n in sheet["wargear"]:
        if key(n) == k:
            return n
    for n, w in sheet["wargear"].items():  # a weapon inside a piece of wargear
        if any(key(base_weapon(p["name"])) == k for p in w["weapons"]):
            return n
    toks = army.tokens_of(name)
    for n in sheet["wargear"]:  # a bundle: "Cyclone Missile Launcher & Storm Bolter"
        if toks and toks <= army.tokens_of(n):
            return n
    return None


def match_model(model, unit, sheet):
    """The datasheet model a list model is, or None when the list can't say.

    A model named for its unit ("Broadside Battlesuits", or a short export's
    placeholder) is the datasheet's only model, or its one required model when
    the unit has a single model; anything else is left for the datasheet's
    composition to decide."""
    models = sheet["models"]
    for m in models:
        if key(m["name"]) == key(model["name"]):
            return m
    if key(model["name"]) == key(unit["name"]):
        if len(models) == 1:
            return models[0]
        required = [m for m in models if m["min"] >= 1]
        if len(unit["models"]) == 1 and len(required) == 1:
            return required[0]
        return None
    gear = {key(w) for w in model["wargear"]}
    best = None
    for m in models:
        coverage, fit = army.score(army.tokens_of(m["name"]), model, unit, set(), flat())
        if coverage < 0.5:
            continue
        can = carries(m, sheet)
        gear_fit = len(gear & can) / len(gear) if gear else 0
        rank = (round(coverage, 2), round(gear_fit, 2), round(fit, 2))
        if best is None or rank > best[0]:
            best = (rank, m)
    return best[1] if best else None


def attach(parsed, mappings, cache=None, repick=False):
    """Match every unit, model and wargear line of a parsed list to the cache.
    -> the Datasheets searched, or None when no datasheets are cached."""
    sheets = Datasheets(parsed["sub"] or parsed["faction"], cache)
    if not sheets and not (data.datasheet_index(cache) or {}).get("catalogues"):
        return None  # nothing cached: units get no "datasheet" at all
    pins = mappings.setdefault("datasheets", {})
    scope = parsed["sub"] or parsed["faction"]
    made = {}
    for u in parsed["units"]:
        sheet, how = match_unit(sheets, u, f"{scope}|{u['name']}", pins, repick, made)
        u["datasheet"] = {"id": sheet["id"], "name": sheet["name"], "catalogue": sheet["catalogue"],
                          "how": how} if sheet else None
        for m in u["models"]:
            sm = match_model(m, u, sheet) if sheet else None
            m["sheet_model"] = sm["name"] if sm else None
            m["sheet_wargear"] = [match_wargear(w, sheet) if sheet else None for w in m["wargear"]]
    return sheets


def unmatched(parsed):
    """What didn't match, for reporting: {"units": [...], "models": [...], "wargear": [...]}."""
    out = {"units": [], "models": [], "wargear": []}
    for u in parsed["units"]:
        if not u.get("datasheet"):
            out["units"].append(u["name"])
            continue
        for m in u["models"]:
            if not m.get("sheet_model") and (u["name"], m["name"]) not in out["models"]:
                out["models"].append((u["name"], m["name"]))
            for w, s in zip(m["wargear"], m.get("sheet_wargear") or []):
                if s is None and (u["name"], w) not in out["wargear"]:
                    out["wargear"].append((u["name"], w))
    return out
