"""
army.py — turn an army list into Force Org models in Tabletop Simulator.

    python3 army.py index                       # cache every Force Org army tile to catalog/
    python3 army.py plan  <list.txt>            # parse + match, print what would spawn
    python3 army.py build <list.txt> [x z [w [facing]]]
                                               # spawn on the table (top-left corner at x, z,
                                               # rows up to w inches wide, models turned to
                                               # facing degrees: 180 = -z, 270 = -x, 90 = +x)
                                               # and write a Saved Object

Understands the GW app export (• / ◦ bullets), the "+++" tournament format, and
New Recruit's full, simple and short exports. Simple and short lists don't say
every model, so their units are marked "complete": false.

Matching decisions live in mappings.json. `plan` and `build` pin every choice
they make there, so a list always comes out the same way; edit an entry to change it.
    "models":  "<faction>|<unit>|<model>|<wargear>" -> ["<tile>:<index>", ...]
    "units":   "<faction>|<unit>" -> [["<model name>", count], ...]
               (model composition for datasheets the parser can't work out)
"""

import copy
import hashlib
import json
import math
import re
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import tts_bridge as tts

ROOT = Path(__file__).parent
CATALOG = ROOT / "catalog"
MAPPINGS = ROOT / "mappings.json"
SAVED_OBJECTS = Path.home() / "Library/Tabletop Simulator/Saves/Saved Objects"

# Force Org tile GUIDs per faction. A sub-faction's tile is searched first,
# then its parent faction's, then every tile.
FACTIONS = {
    "Space Marines": ["1e84c2"],
    "Raven Guard": ["a4ac42"],
    "Ultramarines": ["1e84c2"],
    "Imperial Fists": ["5e90c0"],
    "Iron Hands": ["651a7a"],
    "Salamanders": ["f84cf1"],
    "White Scars": ["5619b8"],
    "Raptors": ["57f25b"],
    "Black Templars": ["aeca24"],
    "Blood Angels": ["ab6dae"],
    "Dark Angels": ["cf983b"],
    "Space Wolves": ["6cf725"],
    "Deathwatch": ["512f59"],
    "Grey Knights": ["95589f"],
    "Adeptus Custodes": ["eb80fa"],
    "Agents of the Imperium": ["dfaf90"],
    "Imperial Agents": ["dfaf90"],
    "Adeptus Mechanicus": ["36a47f"],
    "Astra Militarum": ["f31629"],
    "Imperial Knights": ["9df16b"],
    "Adepta Sororitas": ["430877"],
    "Chaos Space Marines": ["d39dd1", "44bb75", "eb04f6", "4558cc", "af1588"],
    "World Eaters": ["c960d1"],
    "Thousand Sons": ["81778f"],
    "Death Guard": ["a1864c"],
    "Emperor's Children": ["60399b"],
    "Chaos Knights": ["6301b3"],
    "Chaos Daemons": ["03a162"],
    "Tyranids": ["5bf5d6"],
    "T'au Empire": ["e598e0"],
    "Aeldari": ["e5cef8"],
    "Drukhari": ["6678d4"],
    "Necrons": ["25e7b7"],
    "Orks": ["4dbe42"],
    "Genestealer Cults": ["6a135b"],
    "Leagues of Votann": ["daff30"],
}
SUBFACTION_OF = {c: "Space Marines" for c in [
    "Raven Guard", "Ultramarines", "Imperial Fists", "Iron Hands", "Salamanders",
    "White Scars", "Raptors", "Black Templars", "Blood Angels", "Dark Angels",
    "Space Wolves", "Deathwatch"]}

SPAWNABLE = {"Custom_Model", "Custom_Assetbundle", "Figurine_Custom"}
# Custom_Model is a plain OBJ mesh (always static); asset bundles are how TTS
# models get animations and effects.
STATIC = {"Custom_Model", "Figurine_Custom"}


def is_static(o):
    """A plain mesh with no asset-bundle parts anywhere in it."""
    return o.get("Name") in STATIC and all(is_static(c) or c.get("Name") not in SPAWNABLE
                                          for c in o.get("ChildObjects") or [])


def tile_label(g):
    """Human name for a catalogue tile, e.g. "Raven Guard"."""
    names = [f for f, tiles in FACTIONS.items() if g in tiles]
    return " / ".join(names[:2]) if names else g


# --------------------------------------------------------------------------
# Catalogue: every Force Org "Load Models" tile, parsed into spawnable JSON.

LIST_TILES_LUA = """
local out = {}
for _, o in ipairs(getObjects()) do
    if o.tag == "Tile" then
        for _, b in ipairs(o.getButtons() or {}) do
            if b.label == "Load Models" then table.insert(out, o.guid) break end
        end
    end
end
return out
"""


def parse_tile_script(script):
    body = script[script.index("objectJSONs = {"):]
    objs = []
    for _, raw in re.findall(r"\[(=*)\[(.*?)\]\1\]", body, re.S):
        try:
            objs.append(json.loads(raw))
        except json.JSONDecodeError:
            pass
    return objs


def cmd_index():
    CATALOG.mkdir(exist_ok=True)
    guids = json.loads(tts.run_lua(LIST_TILES_LUA))
    for g in guids:
        script = tts.run_lua(f'return getObjectFromGUID("{g}").getLuaScript()', timeout=60)
        if not script or "objectJSONs" not in script:
            print(f"{g}: no model data, skipped")
            continue
        digest = hashlib.sha1(script.encode()).hexdigest()
        path = CATALOG / f"{g}.json"
        if path.exists() and json.loads(path.read_text()).get("sha1") == digest:
            print(f"{g}: unchanged")
            continue
        objs = parse_tile_script(script)
        path.write_text(json.dumps({"tile": g, "sha1": digest, "objects": objs}))
        print(f"{g}: {len(objs)} objects")


def load_catalog():
    if not CATALOG.exists():
        sys.exit("No catalog yet. Run `python3 army.py index` with Force Org open.")
    return {p.stem: json.loads(p.read_text())["objects"] for p in CATALOG.glob("*.json")}


# --------------------------------------------------------------------------
# List parsing.

# "Unit (80 Points)", NewRecruit's compact "Char1: 10x Unit (50 pts): wargear",
# and its full export's "Unit [50 pts]: wargear" (or "Unit [50 pts]:" over • lines).
UNIT_RE = re.compile(r"^(?:Char\d+:\s*)?(?:(?P<count>\d+)x\s+)?(?P<name>.+?)\s*"
                     r"[(\[](?P<pts>[\d,\s]+?)\s*(?:pts|points)[)\]]\s*(?::\s*(?P<gear>.*))?$", re.I)
BATTLE_SIZE_RE = re.compile(r"^(?:Combat Patrol|Incursion|Strike Force|Onslaught)\b", re.I)
# "[20 pts]" after a paid option in New Recruit's full export
COST_RE = re.compile(r"\s*\[[\d,\s]+pts\]", re.I)
LOADOUT_RE = re.compile(r"^(\d+) with (.+)$")
COUNT_RE = re.compile(r"^(\d+)x\s+(.+)$")
# Titles that mark a line as a model rather than wargear in the flat "+++" format.
RANKS = {"sergeant", "sgt", "superior", "leader", "exarch", "shasui", "shasvre",
         "shasla", "shasel", "nob", "boss", "champion", "alpha", "prime"}


def clean(s):
    s = unicodedata.normalize("NFKC", s).replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", s).strip()


def detect_faction(lines):
    for ln in lines[:15]:
        m = re.match(r"\+\s*FACTION KEYWORD:\s*(.+)", ln)
        if m:
            name = m.group(1).split(" - ")[-1].strip()
            return name, None
    known = [ln for ln in lines[:10] if ln in FACTIONS]
    if not known:
        sys.exit("Couldn't find the faction in the list header.")
    faction = next((k for k in known if k not in SUBFACTION_OF), known[0])
    sub = next((k for k in known if k in SUBFACTION_OF), None)
    return faction, sub


def list_title(lines, faction):
    first = lines[0]
    if first.startswith("+"):
        pts = next((re.sub(r"\D", "", ln) for ln in lines if "TOTAL ARMY POINTS" in ln), "")
        det = next((ln.split(":", 1)[1].strip() for ln in lines if "DETACHMENT:" in ln), "")
        return f"{faction} {det} {pts}".strip()
    return re.sub(r"\s*\([^)]*points\)\s*$", "", first, flags=re.I).strip()


def list_format(lines):
    """tournament ("+++" header), nr / simple / short (New Recruit, titled
    "Faction - Name - [pts]" or "Faction - Detachment"), or gw (the GW app)."""
    first = lines[0]
    if first.startswith("+"):
        return "tournament"
    if " - " in first and any(p.strip() in FACTIONS for p in first.split(" - ")):
        if not re.search(r"\[[\d,\s]+pts\]$", first, re.I):
            return "short"
        return "nr" if any(ln.startswith("##") for ln in lines) else "simple"
    return "gw"


def number(s):
    digits = re.sub(r"\D", "", s or "")
    return int(digits) if digits else None


def bare(s):
    """"Retaliation Cadre (3 Detachment Points)" -> "Retaliation Cadre"."""
    return re.sub(r"\s*[(\[][^)\]]*[)\]]", "", s).strip()


def read_header(lines, fmt):
    """The army's own fields: title, faction, sub, detachment, disposition,
    battle_size, points (None where the list doesn't say)."""
    h = dict.fromkeys(["detachment", "disposition", "battle_size", "points"])
    if fmt in ("nr", "simple", "short"):
        parts = [p.strip() for p in lines[0].split(" - ")]
        if fmt != "short":
            h["points"] = number(parts.pop())
        known = [i for i, p in enumerate(parts) if p in FACTIONS]
        names = [parts[i] for i in known]
        h["sub"] = next((n for n in names if n in SUBFACTION_OF), None)
        h["faction"] = next((n for n in names if n not in SUBFACTION_OF), SUBFACTION_OF.get(h["sub"]))
        rest = " - ".join(parts[known[-1] + 1:])
        if fmt == "short":
            h["detachment"] = rest or None
            h["title"] = f"{h['sub'] or h['faction']} {rest}".strip()
        else:
            h["title"] = rest or h["sub"] or h["faction"]
        for ln in lines:
            m = re.match(r"(Battle Size|Detachment|Force Disposition)\b[^:]*:\s*(.+)", ln)
            if m:
                key = {"Battle Size": "battle_size", "Detachment": "detachment"}.get(m.group(1), "disposition")
                h[key] = h[key] or bare(m.group(2))
        return h

    h["faction"], h["sub"] = detect_faction(lines)
    h["title"] = list_title(lines, h["faction"])
    if fmt == "tournament":
        for ln in lines:
            m = re.match(r"\+\s*(DETACHMENT|FORCE DISPOSITION|TOTAL ARMY POINTS):\s*(.+)", ln)
            if m:
                key = {"DETACHMENT": "detachment", "FORCE DISPOSITION": "disposition"}.get(m.group(1))
                if key:
                    h[key] = m.group(2).strip()
                else:
                    h["points"] = number(m.group(2))
        return h

    # GW app: title, faction (and chapter), detachment, disposition and
    # battle size, one per line, before the first heading or unit.
    pts = re.search(r"\(([\d,\s]+)points\)\s*$", lines[0], re.I)
    h["points"] = number(pts and pts.group(1))
    rest = []
    for ln in lines[1:]:
        if ln.isupper() or (UNIT_RE.match(ln) and not BATTLE_SIZE_RE.match(ln)):
            break
        if ln in FACTIONS:
            continue
        if BATTLE_SIZE_RE.match(ln):
            h["battle_size"] = bare(ln)
        elif "detachment point" in ln.lower():
            h["detachment"] = bare(ln)
        else:
            rest.append(ln)
    if h["detachment"] is None and rest:
        h["detachment"] = rest.pop(0)
    h["disposition"] = rest[0] if rest else None
    return h


def tokens_of(s):
    # apostrophes split words, so "Ri'Lantar" and "Shas'ri Lantar" share "lantar"
    s = re.sub(r"\[[0-9a-f]{6}\]|\[-\]", " ", clean(s).lower().replace("w/", " with "))
    s = re.sub(r"shas'(\w+)", r"shas\1 \1", s)  # T'au ranks stay whole: shasui, shasvre
    return {t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t
            for t in re.findall(r"[a-z0-9]+", s) if len(t) > 1} - STOP


STOP = {"with", "and", "the", "of", "in", "a", "on", "x", "w", "or"}


def split_wargear(n, gear):
    """Hand each of n models its own wargear set: gear everyone has goes to
    everyone, rarer gear to the least-equipped models first."""
    loadouts = [[] for _ in range(n)]
    for name, count in sorted(gear, key=lambda g: -g[1]):
        if count >= n:
            for lo in loadouts:
                lo.append(name)
            continue
        order = sorted(range(n), key=lambda i: len(loadouts[i]))
        for i in order[:count]:
            loadouts[i].append(name)
    return loadouts


def parse_list(text, mappings):
    raw = [(len(ln) - len(ln.lstrip()), clean(ln)) for ln in text.splitlines() if clean(ln)]
    lines = [ln for _, ln in raw]
    fmt = list_format(lines)
    army = {"format": fmt, **read_header(lines, fmt), "units": []}
    faction = army["faction"]
    # New Recruit's full export gives each model's own gear ("2x Burst cannon"
    # on each of 2 Shas'ui); the tournament format gives the group's ("2 with ...").
    per_model = fmt == "nr"
    allied = False
    unit = None
    for indent, ln in raw[1:]:  # the first line is always the title
        if ln.startswith(("+", "#")):
            continue
        if ln.upper() == "ALLIED UNITS":
            allied = True
            continue
        m = UNIT_RE.match(ln)
        if m and not ln.startswith(("•", "◦")):
            unit = {"name": m.group("name").strip(), "allied": allied, "lines": [],
                    "count": int(m.group("count") or 1), "points": number(m.group("pts"))}
            army["units"].append(unit)
            if m.group("gear"):  # compact single-line unit: every model carries this
                unit["lines"].append((0, MODEL, unit["count"], unit["name"]))
                unit["lines"] += compact_gear(m.group("gear"), unit["count"] if per_model else 1)
            continue
        if unit is None:
            continue
        if LOADOUT_RE.match(ln):  # compact "1 with Chaos icon, Meltagun" under a model line
            unit["lines"] += compact_gear(ln)
            continue
        bullet = ln[0] if ln.startswith(("•", "◦")) else None
        body = ln[1:].strip() if bullet else ln
        cm = COUNT_RE.match(body)
        if cm and bullet == "•" and ": " in cm.group(2):  # compact "• 9x Cultist: 9 with Autopistol, ..."
            name, gear = cm.group(2).split(": ", 1)
            unit["lines"].append((indent, MODEL, int(cm.group(1)), name.strip()))
            unit["lines"] += compact_gear(gear, int(cm.group(1)) if per_model else 1)
            continue
        if cm and bullet == "•" and fmt in ("nr", "simple"):  # a model with no gear listed
            unit["lines"].append((indent, MODEL, int(cm.group(1)), cm.group(2).strip()))
            continue
        # drones are wargear with no model of their own in 10th edition
        if cm and not re.search(r"\bdrone\b|,", cm.group(2), re.I):
            unit["lines"].append((indent, bullet, int(cm.group(1)), cm.group(2).strip()))

    # Short and simple exports leave models out; stand the unit's own name in
    # for them until datasheets can say what the unit holds.
    complete = fmt not in ("short", "simple")
    for u in army["units"]:
        if not u["lines"] and not complete:
            u["lines"].append((0, MODEL, u["count"], u["name"]))
        u["items"] = nest_lines(u.pop("lines"))
    army["units"] = [u for u in army["units"] if u["items"]]
    for u in army["units"]:
        u["models"] = unit_models(u, faction, mappings)
        u["complete"] = complete
        del u["items"], u["count"]
    army["points"] = army["points"] or sum(u["points"] or 0 for u in army["units"]) or None
    return army


MODEL = "model"   # a line already known to be a model (NewRecruit compact)


def nest_lines(lines):
    """(level, count, name, known model) for a unit's "Nx ..." lines. "◦" is always wargear.
    Newer GW app exports nest everything under "•" and show it by indentation
    (model at one depth, its wargear deeper); the shallowest bulleted depth is
    the model level. An indented unbulleted line continues the bullet above it,
    so it shares that bullet's level. A list pasted without its indentation has
    no depth to read: its unbulleted lines are wargear and are left out, as the
    flat format always was."""
    bulleted = [ind for ind, b, _, _ in lines if b == "•"]
    base = min(bulleted) if bulleted else 0
    out, level = [], 1
    for indent, bullet, n, name in lines:
        if bullet == MODEL:
            level = 1
        elif bullet == "◦":
            level = 2
        elif bullet == "•":
            level = 1 if indent <= base else 2
        elif indent <= base:
            continue
        out.append((level, n, name, bullet == MODEL))
    return out


def compact_gear(text, n=1):
    """NewRecruit compact wargear ("3 with Blastmaster, 2x Heavy bolter") as
    ◦ lines, so it groups under its model like the GW app's wargear does.
    n is how many models carry it when the text doesn't say ("3 with")."""
    rest = text
    lm = LOADOUT_RE.match(text.strip())
    if lm:
        n, rest = int(lm.group(1)), lm.group(2)
    out = []
    # commas inside brackets belong to one piece: "Gun Drone (Twin pulse carbine)"
    for piece in re.split(r",(?![^()]*\))", rest):
        piece = COST_RE.sub("", piece).strip()
        if not piece or re.search(r"\bdrone\b", piece, re.I):
            continue
        cm = COUNT_RE.match(piece)
        out.append((0, "◦", n * int(cm.group(1)), cm.group(2).strip()) if cm else (0, "◦", n, piece))
    return out


def unit_models(unit, faction, mappings):
    """-> [{"name", "wargear"}] with one entry per physical model."""
    items = unit["items"]
    override = mappings.get("units", {}).get(f"{faction}|{unit['name']}")
    unit_tokens = tokens_of(unit["name"])

    if any(lvl == 2 or known for lvl, _, _, known in items):
        # GW app export: model lines are the • lines that own ◦ wargear lines,
        # plus lines already known to be models.
        groups = []
        for lvl, n, name, known in items:
            if lvl == 1:
                groups.append([n, name, [], known])
            elif groups:
                groups[-1][2].append((name, n))
        groups = [g[:3] for g in groups if g[2] or g[3]]
    else:
        # Flat format: model lines are recognised by rank titles or by
        # ending in a word from the unit's name ("9x Pathfinders").
        groups = []
        known = {m for m, _ in override} if override else set()
        for _, n, name, _ in items:
            toks = [t for t in re.findall(r"[a-z0-9]+", clean(name).lower().replace("'", ""))]
            last = tokens_of(toks[-1]) if toks else set()
            # a rank titles a model when it ends the line ("Ravener Prime",
            # "Stealth Shas'vre"), not when it starts a weapon ("Prime claws")
            joined = re.findall(r"[a-z]+", clean(name).lower().replace("'", ""))
            is_model = (name in known or bool(joined and joined[-1] in RANKS)
                        or bool(last & unit_tokens))
            if is_model:
                groups.append([n, name, []])
            elif groups:
                groups[-1][2].append((name, n))
            else:
                groups.append([1, unit["name"], [(name, n)], "single"])  # single-model unit
        if groups and all(len(g) == 4 for g in groups):
            groups = [[1, unit["name"], [w for g in groups for w in g[2]]]]
        groups = [g[:3] for g in groups]

    if not groups:
        groups = [[1, unit["name"], [(name, n) for _, n, name, _ in items]]]

    models = []
    for n, name, gear in groups:
        for lo in split_wargear(n, gear):
            models.append({"name": name, "wargear": sorted(lo)})
    return models


# --------------------------------------------------------------------------
# Matching list models to catalogue entries.

def tok_hit(t, pool):
    # fuzzy only for typos ("Shaan"/"Shann", "Ulthran"/"Uthran"), not
    # different words that happen to share letters ("Terminator"/"Eliminator")
    return t in pool or any(len(t) > 4 and len(p) > 4 and t[0] == p[0] and
                            SequenceMatcher(None, t, p).ratio() >= 0.8 for p in pool)


def score(entry_tokens, model, unit, free, weight):
    """Sort key, best first: how much of the model's name the entry covers,
    then how well the rest of the entry fits (wargear up, stray words down)."""
    name_t = tokens_of(model["name"]) or tokens_of(unit["name"])
    unit_t = tokens_of(unit["name"])
    gear_t = set().union(*(tokens_of(w) for w in model["wargear"])) if model["wargear"] else set()
    # rare words ("Fireknife") count for more than common ones ("Crisis")
    # ...and rank words matter less than what the model is
    weight = (lambda w: lambda t: w(t) * (0.3 if t in RANKS or t in ("ui", "vre") else 1))(weight)
    total = sum(weight(t) for t in name_t) or 1
    coverage = sum(weight(t) for t in name_t if tok_hit(t, entry_tokens)) / total
    fit = 0.0
    for t in entry_tokens:
        if tok_hit(t, name_t):
            continue
        if tok_hit(t, unit_t):
            fit += 0.3
        elif tok_hit(t, gear_t):
            fit += 0.5
        elif t in free:
            fit += 0.25  # "Raven Guard Intercessor" over a generic "Intercessor"
        else:
            fit -= 1.5
    return coverage, fit


class Matcher:
    def __init__(self, catalog, army, aliases=None):
        self.catalog = catalog
        pref = []
        for f in (army["sub"], SUBFACTION_OF.get(army["sub"]), army["faction"]):
            for g in FACTIONS.get(f, []) if f else []:
                if g not in pref:
                    pref.append(g)
        self.pref = pref
        # Tiles a unit of this army may take a model from: its own, its parent
        # faction's and its sibling chapters' (a Raven Guard list can use the
        # Raptors' Thunderhawk). Nothing from another army: a name that only
        # matches elsewhere is a false friend ("Dominion" is also a Necron
        # monolith), so it is left unmatched rather than spawned.
        parent = SUBFACTION_OF.get(army["sub"]) or SUBFACTION_OF.get(army["faction"]) or army["faction"]
        family = [f for f, p in SUBFACTION_OF.items() if p == parent] + [parent]
        self.scope = set(pref) | {g for f in family for g in FACTIONS.get(f, [])}
        # names the catalogue doesn't use, from mappings.json ("Dominion" -> "Battle Sister")
        self.aliases = aliases or {}
        self.free = tokens_of(" ".join(filter(None, [army["sub"], army["faction"]])))
        self.entries = []
        for g, objs in catalog.items():
            for i, o in enumerate(objs):
                nick = (o.get("Nickname") or "").strip()
                if o.get("Name") in SPAWNABLE and nick:
                    self.entries.append((g, i, nick, tokens_of(nick)))
        df = {}
        for *_, toks in self.entries:
            for t in toks:
                df[t] = df.get(t, 0) + 1
        n = len(self.entries)
        self.weight = lambda t: math.log((n + 1) / (df.get(t, 0) + 1)) + 1

    def ranked(self, unit, model, allied, prefer_static=False):
        """Every plausible catalogue entry, best first: (key, tile, index, nickname).
        Key: name coverage, then army tile, then fit; with prefer_static, a
        static mesh beats an asset bundle before tile and fit are considered."""
        scope = [] if allied else self.pref
        if model["name"] in self.aliases:
            model = {**model, "name": self.aliases[model["name"]]}
        scored = []
        for g, i, nick, toks in self.entries:
            if not allied and self.scope and g not in self.scope:
                continue
            coverage, fit = score(toks, model, unit, self.free, self.weight)
            if coverage < 0.5:
                continue
            rank = scope.index(g) if g in scope else len(scope)
            static = int(is_static(self.catalog[g][i]))
            key = ((round(coverage, 2), static, -rank, round(fit, 2)) if prefer_static
                   else (round(coverage, 2), -rank, round(fit, 2)))
            scored.append((key, g, i, nick))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def candidates(self, unit, model, allied, prefer_static=False):
        scored = self.ranked(unit, model, allied, prefer_static)
        if not scored and model["name"] != unit["name"]:
            # a champion the catalogue doesn't name ("Disharmonist") still
            # belongs to its unit: use one of the unit's models
            scored = self.ranked(unit, {**model, "name": unit["name"]}, allied, prefer_static)
        if not scored:
            return [], None
        best = scored[0][0]
        # equally good variants (e.g. two Intercessor sculpts) are all kept
        return [x for x in scored if x[0] == best][:4], best


def model_key(faction, unit, model):
    return f"{faction}|{unit['name']}|{model['name']}|{', '.join(model['wargear'])}"


def resolve(army, catalog, mappings, prefer_static=False, repick=False):
    """Attach catalogue picks to every model. Returns rows for reporting.
    repick ignores (and overwrites) this list's existing pins."""
    matcher = Matcher(catalog, army, mappings.get("aliases", {}).get(army["faction"]))
    pinned = mappings.setdefault("models", {})
    rows = []
    redone = set()
    for u in army["units"]:
        variant = {}
        for m in u["models"]:
            key = model_key(army["faction"], u, m)
            if key in pinned and not (repick and key not in redone):
                picks, how = pinned[key], "pinned"
            else:
                redone.add(key)
                cands, best = matcher.candidates(u, m, u["allied"], prefer_static)
                picks = [f"{g}:{i}" for _, g, i, _ in cands]
                how = (f"auto cover={best[0]:.0%}" + (" static" if prefer_static else "")) if picks else "NO MATCH"
                if picks:
                    pinned[key] = picks
            n = variant.get(key, 0)
            variant[key] = n + 1
            m["pick"] = picks[n % len(picks)] if picks else None
            rows.append((u["name"], m, how))
    return rows


def nickname(catalog, pick):
    g, i = pick.split(":")
    return catalog[g][int(i)].get("Nickname", "").strip()


def print_plan(army, catalog, rows):
    print(f"{army['title']}  [{army['faction']}{' / ' + army['sub'] if army['sub'] else ''}]")
    last = None
    for unit, m, how in rows:
        if unit != last:
            print(f"\n  {unit}")
            last = unit
        got = nickname(catalog, m["pick"]) if m["pick"] else "-"
        gear = f"  ({', '.join(m['wargear'])})" if m["wargear"] else ""
        print(f"    {m['name'][:34]:34} -> {got[:48]:48} {how}{gear[:60]}")
    total = len(rows)
    missing = sum(1 for _, m, _ in rows if not m["pick"])
    print(f"\n{total} models, {missing} unmatched")


# --------------------------------------------------------------------------
# Spawning and layout.

SPAWN_LUA_WAIT = """
local ids = {ids}
for _, id in ipairs(ids) do
    local o = getObjectFromGUID(id)
    if o ~= nil and o.loading_custom then return "wait" end
end
local out = {{}}
for _, id in ipairs(ids) do
    local o = getObjectFromGUID(id)
    if o then local b = o.getBounds() out[id] = {{b.size.x, b.size.z}} end
end
return out
"""


def lua_list(xs):
    return "{" + ",".join(f'"{x}"' for x in xs) + "}"


def spawn(army, catalog, x0, z0, width=110, facing=180):
    objs, units = [], []
    for u in army["units"]:
        members = []
        for m in u["models"]:
            if not m["pick"]:
                continue
            g, i = m["pick"].split(":")
            o = copy.deepcopy(catalog[g][int(i)])
            o.pop("GUID", None)
            o["Description"] = f"[{u['name']}]\n" + (o.get("Description") or "")
            o["GMNotes"] = f"army.py:{army['title']}"
            members.append(len(objs))
            objs.append(o)
        units.append((u["name"], members))

    # stage everything locked and floating over its own target area (mods put
    # scripted zones around the table that eat stray objects), then pack once
    # sizes are known
    for n, o in enumerate(objs):
        o["Transform"].update(posX=x0 + (n % 20) * 3, posY=8, posZ=z0 - (n // 20) * 3,
                              rotX=0, rotY=180, rotZ=0)
        o["Locked"] = True
    lines = ["local g = {}"]
    for o in objs:
        lines.append(f"table.insert(g, spawnObjectJSON({{json = {tts.lua_str(json.dumps(o))}}}).guid)")
    lines.append("return g")
    guids = json.loads(tts.run_lua("\n".join(lines), timeout=60))

    sizes = None
    for _ in range(60):
        r = tts.run_lua(SPAWN_LUA_WAIT.format(ids=lua_list(guids)))
        if r and r != "wait":
            sizes = json.loads(r)
            break
        time.sleep(1)
    if sizes is None:
        print("Models still loading after 60s; laying out with guessed sizes.")
        sizes = {}

    gap, pad = 0.4, 3.0
    moves = []
    cx, cz, row_h = x0, z0, 0
    for _, members in units:
        if not members:
            continue
        dims = [sizes.get(guids[k], [2, 2]) for k in members]
        # as many models per line as fit the area (max 10), so narrow
        # deployment zones wrap a unit instead of spilling off the table
        per_row, run = 0, 0.0
        for d in dims[:10]:
            if per_row and run + d[0] + gap > width:
                break
            run += d[0] + gap
            per_row += 1
        uw = sum(d[0] + gap for d in dims[:per_row])
        if cx + uw > x0 + width and cx > x0:
            cx, cz, row_h = x0, cz - row_h - pad, 0
        ux, uz, line_h = cx, cz, 0
        for n, (k, d) in enumerate(zip(members, dims)):
            if n and n % per_row == 0:
                ux, uz, line_h = cx, uz - line_h - gap, 0
            moves.append((guids[k], ux + d[0] / 2, uz - d[1] / 2))
            ux += d[0] + gap
            line_h = max(line_h, d[1])
        row_h = max(row_h, cz - uz + line_h)
        cx += uw + pad

    lua = [f'do local o = getObjectFromGUID("{g}") if o then '
           f'o.setPosition({{{x:.2f}, 3, {z:.2f}}}) o.setRotation({{0, {facing}, 0}}) '
           f'o.setLock(false) end end'
           for g, x, z in moves]
    tts.run_lua("\n".join(lua), timeout=30)
    return guids


def save_object(army, guids):
    time.sleep(2)
    raw = tts.run_lua(f"local out = {{}} for _, id in ipairs({lua_list(guids)}) do "
                      f"local o = getObjectFromGUID(id) if o then table.insert(out, o.getJSON(false)) end "
                      f"end return out",
                      timeout=30)
    states = [json.loads(s) for s in json.loads(raw)]
    folder = SAVED_OBJECTS / (army["sub"] or army["faction"])
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[\\/:*?"<>|]', "", army["title"]).strip()
    path = folder / f"{name}.json"
    path.write_text(json.dumps({
        "SaveName": "", "GameMode": "", "Gravity": 0.5, "PlayArea": 0.5, "Date": "",
        "Table": "", "Sky": "", "Note": "", "Rules": "", "XmlUI": "", "LuaScript": "",
        "LuaScriptState": "", "ObjectStates": states, "TabStates": {}, "VersionNumber": "",
    }, indent=2))
    return path


# --------------------------------------------------------------------------

def load_mappings():
    return json.loads(MAPPINGS.read_text()) if MAPPINGS.exists() else {"units": {}, "models": {}}


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("index", "plan", "build"):
        print(__doc__)
        return
    if args[0] == "index":
        tts.start_listener()
        cmd_index()
        return

    catalog = load_catalog()
    mappings = load_mappings()
    army = parse_list(Path(args[1]).read_text(), mappings)
    rows = resolve(army, catalog, mappings)
    print_plan(army, catalog, rows)
    MAPPINGS.write_text(json.dumps(mappings, indent=1, ensure_ascii=False))
    if args[0] == "build":
        x0, z0 = (float(args[2]), float(args[3])) if len(args) >= 4 else (-55.0, 55.0)
        width = float(args[4]) if len(args) >= 5 else 110.0
        facing = float(args[5]) if len(args) >= 6 else 180.0
        tts.start_listener()
        guids = spawn(army, catalog, x0, z0, width, facing)
        print(f"\nSpawned {len(guids)} models. Saved Object: {save_object(army, guids)}")


if __name__ == "__main__":
    main()
