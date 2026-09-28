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
    # everything it carries: wargear, and gear the list names without it being
    # wargear (a line that lost its bullet), so the loadout picks the variant
    gear = {key(w) for w in model["wargear"]} | {key(g["name"]) for g in model.get("gear") or []}
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


def parse(text, mappings, cache=None, repick=False):
    """Parse a list with everything the data cache knows: model lines told from
    wargear by the datasheet's model names, datasheets matched (attach), costed
    options that aren't wargear moved to enhancements, and compositions the list
    can't give filled in from the datasheet (compose). Without a cache, it's
    army.parse_list alone."""
    found = {}

    def model_names(army_, unit):
        if "sheets" not in found:
            found["sheets"] = Datasheets(army_["sub"] or army_["faction"], cache)
        sheet, _ = found["sheets"].find(unit["name"], unit.get("allied"))
        return [m["name"] for m in sheet["models"]] if sheet else []

    parsed = army.parse_list(text, mappings, model_names)
    sheets = attach(parsed, mappings, cache, repick)
    if sheets:
        for u in parsed["units"]:
            sheet = sheets.get(u["datasheet"]["id"]) if u.get("datasheet") else None
            if sheet:
                move_enhancements(u, sheet)
                compose(u, sheet)
        link_leaders(parsed["units"], sheets)
    return parsed


LEADER_RE = re.compile(r"attached to the following units?\s*:(.*)", re.I | re.S)


def leads(sheet):
    """The units a character's Leader ability says it can be attached to, by
    key() name. [] for a unit without one."""
    for a in sheet["abilities"]:
        m = a["name"].casefold() == "leader" and LEADER_RE.search(a.get("text") or "")
        if m:
            names = re.split(r"[,\n■•]", re.sub(r"\*+|\^\^", "", m.group(1)))
            return [key(n.strip().lstrip("-").strip().rstrip(".")) for n in names if n.strip(" -.")]
    return []


def link_leaders(units, sheets):
    """Each unit with a Leader ability gets `can_lead`: the other units of the
    list it can be attached to, by index."""
    for i, u in enumerate(units):
        sheet = sheets.get(u["datasheet"]["id"]) if u.get("datasheet") else None
        names = set(leads(sheet)) if sheet else set()
        if names:
            u["can_lead"] = [j for j, o in enumerate(units) if j != i and (
                key(o["name"]) in names or (o.get("datasheet") and key(o["datasheet"]["name"]) in names))]


def move_enhancements(unit, sheet):
    """New Recruit lists an enhancement among the wargear with its cost
    ("Starflare Ignition System [20 pts]"); anything costed that isn't the
    datasheet's wargear is an enhancement."""
    for name in unit.pop("priced", []):
        if match_wargear(name, sheet) is not None:
            continue
        if name not in unit["enhancements"]:
            unit["enhancements"].append(name)
        for m in unit["models"]:
            if name in m["wargear"]:
                i = m["wargear"].index(name)
                del m["wargear"][i]
                if m.get("sheet_wargear"):
                    del m["sheet_wargear"][i]
            m["gear"] = [g for g in m.get("gear", []) if g["name"] != name]


def default_loadout(sheet_model):
    """[(wargear, count)] a datasheet model has before any choices: what it's
    equipped with, plus each required option's default."""
    gear = [(e["name"], e["count"]) for e in sheet_model["equipped"]]
    for opt in sheet_model["options"]:
        if opt["min"] >= 1 and opt["choices"]:
            gear.append((opt.get("default") or opt["choices"][0], opt["min"]))
    return gear


def composition(sheet, n, floors=None):
    """[(datasheet model, count)] for a unit of n models: each at its minimum
    (or the list's own count), the rest to the models with the most room."""
    counts = {m["name"]: m["min"] for m in sheet["models"]}
    for name, c in (floors or {}).items():
        counts[name] = max(counts.get(name, 0), c)

    def room(m):
        return (m["max"] if m["max"] is not None else 10 ** 6) - counts[m["name"]]
    left = n - sum(counts.values())
    for m in sorted(sheet["models"], key=lambda m: -room(m)):
        if left <= 0:
            break
        add = min(left, room(m))
        counts[m["name"]] += add
        left -= add
    return [(m, counts[m["name"]]) for m in sheet["models"] if counts[m["name"]]]


def compose(unit, sheet):
    """Fill in a unit whose list can't say which models it has: a short or
    simple export, or a flat list's unit named only by itself. Its models are
    rebuilt from the datasheet ("composition": "datasheet")."""
    models = unit["models"]
    placeholders = all(key(m["name"]) == key(unit["name"]) for m in models)
    # a complete list's placeholder that matching already mapped (a Broadside's one
    # model is its Shas'vre) keeps the list's own gear
    unmapped = placeholders and not all(m.get("sheet_model") for m in models)
    if unit.get("complete", True) and not (unmapped and len(sheet["models"]) > 1):
        return
    if placeholders:
        n, floors = len(models), {}
    else:  # a simple export names some models: keep them, fill up to the datasheet's size
        n = max(len(models), (sheet.get("size") or [0])[0] or 0)
        floors = {}
        for m in models:
            if m.get("sheet_model"):
                floors[m["sheet_model"]] = floors.get(m["sheet_model"], 0) + 1
    rebuilt = []
    for sm, count in composition(sheet, n, floors):
        gear = default_loadout(sm)
        wargear = sorted(w for w, _ in gear)
        for _ in range(count):
            rebuilt.append({"name": army.clean(sm["name"]), "wargear": list(wargear),
                            "gear": [{"name": w, "count": c} for w, c in sorted(gear)],
                            "sheet_model": sm["name"], "sheet_wargear": list(wargear)})
    if rebuilt:
        unit["models"] = rebuilt
        unit["composition"] = "datasheet"


# --------------------------------------------------------------------------
# Stand-ins: a model Force Org has no figure for ("Dominion") is shown with a
# look-alike's figure: the model in another datasheet of the same army with the
# same body (M, T, Sv, W, Ld) and the most wargear in common (Battle Sister).

BODY = ("M", "T", "Sv", "W", "Ld")


def unit_gear(sheet):
    names = set()
    for m in sheet["models"]:
        names |= {e["name"] for e in m["equipped"]}
        for opt in m["options"]:
            names |= set(opt["choices"])
    return names


def counterpart(mine, sheet, other):
    """The model in `other` that plays `mine`'s part in `sheet`: the same
    loadout ("w/ Simulacrum Imperialis"), the leader, or the rank and file."""
    def suffix(name):
        return name.split(" w/ ", 1)[1].casefold() if " w/ " in name else None

    def leader(s):
        return next((m for m in s["models"] if m["min"] == m["max"] == 1), None)
    if suffix(mine["name"]):
        same = next((m for m in other["models"] if suffix(m["name"]) == suffix(mine["name"])), None)
        if same:
            return same["name"]
    if leader(sheet) is mine and leader(other):
        return leader(other)["name"]
    plain = [m for m in other["models"] if not suffix(m["name"]) and m is not leader(other)]
    return max(plain or other["models"], key=lambda m: m["max"] or 10 ** 6)["name"]


def stand_in(sheets, sheet, model_name, threshold=0.5):
    """(look-alike unit name, model name) for one of `sheet`'s models, or None."""
    mine = next((m for m in sheet["models"] if m["name"] == model_name), None)
    if not mine or not mine["stats"]:
        return None
    body = tuple(mine["stats"].get(k) for k in BODY)
    gear = unit_gear(sheet)
    best = None
    for other in sheets.scope:
        if other["id"] == sheet["id"]:
            continue
        if not any(m["stats"] and tuple(m["stats"].get(k) for k in BODY) == body for m in other["models"]):
            continue
        theirs = unit_gear(other)
        overlap = len(gear & theirs) / len(gear | theirs) if gear | theirs else 0
        if overlap >= threshold and (best is None or overlap > best[0]):
            best = (overlap, other)
    return (best[1]["name"], counterpart(mine, sheet, best[1])) if best else None


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
