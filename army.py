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
    "datasheets": "<sub-faction or faction>|<unit>" -> {"id", "name", "catalogue"}
               (the unit's datasheet, when data.py has cached them; see datasheets.py)

`plan` shows each unit's datasheet and flags anything that didn't match. It
works without the Force Org catalogue, showing only the datasheets.
"""

import copy
import hashlib
import json
import math
import os
import re
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import config  # noqa: F401  (loads .env)
import tts_bridge as tts

ROOT = Path(__file__).parent
CATALOG = ROOT / "catalog"
MAPPINGS = ROOT / "mappings.json"


def saved_objects_dir():
    """TTS's Saved Objects folder: TTS_SAVED_OBJECTS, else the macOS, Windows
    or Linux default (the first that exists, else the macOS one)."""
    if os.environ.get("TTS_SAVED_OBJECTS"):
        return Path(os.environ["TTS_SAVED_OBJECTS"]).expanduser()
    home = Path.home()
    options = [home / "Library/Tabletop Simulator/Saves/Saved Objects",
               home / "Documents/My Games/Tabletop Simulator/Saves/Saved Objects",
               home / ".local/share/Tabletop Simulator/Saves/Saved Objects"]
    return next((p for p in options if p.parent.is_dir()), options[0])

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


def force_org_tiles(run_lua=None):
    """GUIDs of the Force Org army tiles on the table; [] when Force Org isn't loaded."""
    reply = (run_lua or tts.run_lua)(LIST_TILES_LUA)
    if reply is None:
        raise SystemExit("TTS didn't answer. Is a game loaded, and is the External Editor API on?")
    return json.loads(reply) or []


def cmd_index(log=print, run_lua=None):
    """Copy every Force Org army tile's models into catalog/, skipping
    unchanged tiles. run_lua defaults to tts.run_lua (the web app passes one
    that holds its TTS lock per call). -> (tiles read, tiles updated)."""
    run_lua = run_lua or tts.run_lua
    guids = force_org_tiles(run_lua)
    if not guids:
        raise SystemExit("No Force Org army tiles on the table. Load the Force Org mod in TTS first.")
    updated = 0
    for i, g in enumerate(guids, 1):
        script = run_lua(f'return getObjectFromGUID("{g}").getLuaScript()', timeout=60)
        updated += write_tile(g, script, f"[{i}/{len(guids)}] {tile_label(g)} ({g})", log)
    log(f"{len(guids)} tiles read, {updated} updated")
    return len(guids), updated


def write_tile(g, script, label, log=print):
    """Save one army tile's models to catalog/<guid>.json unless its script is
    unchanged. -> 1 if it was written. Shared by the TTS and mod-file indexers."""
    if not script or "objectJSONs" not in script:
        log(f"{label}: no model data, skipped")
        return 0
    digest = hashlib.sha1(script.encode()).hexdigest()
    path = CATALOG / f"{g}.json"
    if path.exists() and json.loads(path.read_text()).get("sha1") == digest:
        log(f"{label}: unchanged")
        return 0
    CATALOG.mkdir(exist_ok=True)
    objs = parse_tile_script(script)
    path.write_text(json.dumps({"tile": g, "sha1": digest, "objects": objs}))
    log(f"{label}: {len(objs)} objects")
    return 1


def load_catalog():
    if not CATALOG.exists():
        sys.exit("No catalog yet. Run `python3 army.py index` with Force Org open.")
    return {p.stem: json.loads(p.read_text())["objects"] for p in CATALOG.glob("*.json")}


# --------------------------------------------------------------------------
# List parsing.

# "Unit (80 Points)", NewRecruit's compact "Char1: 10x Unit (50 pts): wargear",
# and its full export's "Unit [50 pts]: wargear" (or "Unit [50 pts]:" over • lines).
UNIT_RE = re.compile(r"^(?:(?P<label>Char\d+):\s*)?(?:(?P<count>\d+)x\s+)?(?P<name>.+?)\s*"
                     r"[(\[](?P<pts>[\d,\s]+?)\s*(?:pts|points)[)\]]\s*(?::\s*(?P<gear>.*))?$", re.I)
BATTLE_SIZE_RE = re.compile(r"^(?:Combat Patrol|Incursion|Strike Force|Onslaught)\b", re.I)
# "[20 pts]" after a paid option in New Recruit's full export
COST_RE = re.compile(r"\s*\[[\d,\s]+pts\]", re.I)
# Drones are wargear with no model of their own. They go in a model's "gear"
# (with counts) but not its "wargear", which pins are keyed on.
DRONE_RE = re.compile(r"\bdrones?\b", re.I)
ENHANCEMENT_RE = re.compile(r"^enhancements?:\s*(.+)$", re.I)
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
    if re.search(r"\([\d,\s]+points\)$", first, re.I):  # a GW title can have " - " in it too
        return "gw"
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
    everyone, rarer gear to the least-equipped models first.
    gear: [(name, count)] or [(name, count, gear_only)]. -> [(wargear names,
    {name: count})] per model; gear_only items (drones) are only counted."""
    loadouts = [[] for _ in range(n)]
    counts = [{} for _ in range(n)]
    for name, count, *only in sorted(gear, key=lambda g: -g[1]):
        gear_only = bool(only and only[0])
        if count >= n:
            each, extra = divmod(count, n)
            for i in range(n):
                if not gear_only:
                    loadouts[i].append(name)
                counts[i][name] = counts[i].get(name, 0) + each + (i < extra)
            continue
        order = sorted(range(n), key=lambda i: len(loadouts[i]))
        for i in order[:count]:
            if not gear_only:
                loadouts[i].append(name)
            counts[i][name] = counts[i].get(name, 0) + 1
    return list(zip(loadouts, counts))


def parse_list(text, mappings, model_names=None):
    """Parse an army list export. model_names(army, unit) -> names the unit's
    datasheet gives its models, so a flat list's model lines can be told from
    its wargear (datasheets.parse passes it; without it, rank titles and the
    unit's name decide)."""
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
    group = None  # the "Attached unit N" heading we're under
    for indent, ln in raw[1:]:  # the first line is always the title
        if ln.startswith(("+", "#")):
            continue
        if ln.upper() == "ALLIED UNITS":
            allied = True
            continue
        gm = re.match(r"^attached unit (\d+)$", ln, re.I)
        if gm:
            group = int(gm.group(1))
            continue
        if ln.isupper() and ln.upper() != "ATTACHED UNITS":  # another heading: CHARACTERS, OTHER DATASHEETS...
            group = None
        m = UNIT_RE.match(ln)
        if m and not ln.startswith(("•", "◦")):
            unit = {"name": m.group("name").strip(), "allied": allied, "lines": [],
                    "count": int(m.group("count") or 1), "points": number(m.group("pts")),
                    "label": m.group("label"), "group": group, "role": None,
                    "enhancements": [], "warlord": False, "priced": []}
            army["units"].append(unit)
            if m.group("gear"):  # compact single-line unit: every model carries this
                unit["lines"].append((0, MODEL, unit["count"], unit["name"]))
                unit["lines"] += compact_gear(m.group("gear"), unit["count"] if per_model else 1, unit)
            continue
        if unit is None:
            continue
        if LOADOUT_RE.match(ln):  # compact "1 with Chaos icon, Meltagun" under a model line
            unit["lines"] += compact_gear(ln, unit=unit)
            continue
        bullet = ln[0] if ln.startswith(("•", "◦")) else None
        body = ln[1:].strip() if bullet else ln
        if body.lower().startswith("attached as:"):  # "Leader (Character)", "Bodyguard"
            unit["role"] = body.split(":", 1)[1].split("(")[0].strip().lower() or None
            continue
        if body.lower() == "warlord":
            unit["warlord"] = True
            continue
        em = ENHANCEMENT_RE.match(body)
        if em:  # "Enhancements: Temporal Corridor", "Enhancement: Starflare Ignition System (+20 pts)"
            for name in em.group(1).split(", "):
                add_enhancement(unit, name)
            continue
        cm = COUNT_RE.match(body)
        if cm and bullet == "•" and ": " in cm.group(2):  # compact "• 9x Cultist: 9 with Autopistol, ..."
            name, gear = cm.group(2).split(": ", 1)
            unit["lines"].append((indent, MODEL, int(cm.group(1)), name.strip()))
            unit["lines"] += compact_gear(gear, int(cm.group(1)) if per_model else 1, unit)
            continue
        if cm and bullet == "•" and fmt in ("nr", "simple"):  # a model with no gear listed
            unit["lines"].append((indent, MODEL, int(cm.group(1)), cm.group(2).strip()))
            continue
        if bullet and (DRONE_RE.search(body) or "," in body or not cm):
            # drones ("• 2x Gun Drone, 2x Shield Drone") and gear without a count
            # ("• Homing beacon"): counted in the model's gear, not its wargear
            for piece in re.split(r",(?![^()]*\))", body):
                pm = COUNT_RE.match(piece.strip())
                n, name = (int(pm.group(1)), pm.group(2)) if pm else (1, piece.strip())
                if name:
                    unit["lines"].append((indent, GEAR, n, re.sub(r"\s*\([^)]*\)$", "", name.strip())))
            continue
        if cm:
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
        u["models"] = unit_models(u, faction, mappings, model_names(army, u) if model_names else ())
        u["complete"] = complete
        del u["items"], u["count"]
    army["points"] = army["points"] or sum(u["points"] or 0 for u in army["units"]) or None
    header_extras(army, lines)
    link_attachments(army["units"])
    return army


def add_enhancement(unit, name):
    name = re.sub(r"\s*\([+\d\s]*pts\)\s*$", "", name, flags=re.I).strip()
    if name and name not in unit["enhancements"]:
        unit["enhancements"].append(name)


def header_extras(army, lines):
    """The "+++" header's WARLORD and ENHANCEMENT lines, onto their units: by
    "CharN" label, else by name."""
    def find(label, name):
        units = army["units"]
        return (next((u for u in units if label and u["label"] == label), None)
                or next((u for u in units if u["name"] == (name or "").strip()), None))
    for ln in lines:
        wm = re.match(r"\+\s*WARLORD:\s*(?:(Char\d+):\s*)?(.+)$", ln)
        if wm and find(wm.group(1), wm.group(2)):
            find(wm.group(1), wm.group(2))["warlord"] = True
        em = re.match(r"\+\s*ENHANCEMENTS?:\s*(.+?)\s*\(on\s+(?:(Char\d+):\s*)?(.+)\)\s*$", ln)
        if em and find(em.group(2), em.group(3)):
            add_enhancement(find(em.group(2), em.group(3)), em.group(1))


def link_attachments(units):
    """Each unit under an "Attached unit N" heading that says how it's
    attached gets its role; leaders (and supports) get the index of the
    bodyguard they're attached to."""
    for u in units:
        if u["role"] is None:
            u["group"] = None
    for u in units:
        if u["role"] and u["role"] != "bodyguard" and u["group"] is not None:
            u["attached_to"] = next((i for i, b in enumerate(units)
                                     if b["group"] == u["group"] and b["role"] == "bodyguard"), None)
        else:
            u["attached_to"] = None
    for u in units:
        del u["group"]


MODEL = "model"   # a line already known to be a model (NewRecruit compact)
GEAR = "gear"     # gear that isn't wargear: drones, and lines without a count


def nest_lines(lines):
    """(level, count, name, kind) for a unit's "Nx ..." lines: kind True for a
    line known to be a model, GEAR for gear-only lines, else False. "◦" is always wargear.
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
        if bullet == GEAR:
            out.append((2, n, name, GEAR))
            continue
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


def compact_gear(text, n=1, unit=None):
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
        priced = bool(COST_RE.search(piece))
        piece = COST_RE.sub("", piece).strip()
        if not piece:
            continue
        cm = COUNT_RE.match(piece)
        count, name = (n * int(cm.group(1)), cm.group(2).strip()) if cm else (n, piece)
        if DRONE_RE.search(name):  # "Gun Drone (Twin pulse carbine)": the drone, not its weapon
            out.append((0, GEAR, count, re.sub(r"\s*\([^)]*\)$", "", name)))
            continue
        if priced and unit is not None:  # maybe an enhancement; datasheets can tell (datasheets.py)
            unit["priced"].append(name)
        out.append((0, "◦", count, name))
    return out


def unit_models(unit, faction, mappings, model_names=()):
    """-> [{"name", "wargear", "gear"}] with one entry per physical model.
    "gear" counts everything the model carries, drones included; "wargear" is
    the names alone, without drones (pins are keyed on it). model_names are
    names known to be models (from the unit's datasheet, when it's cached)."""
    items = unit["items"]
    override = mappings.get("units", {}).get(f"{faction}|{unit['name']}")
    unit_tokens = tokens_of(unit["name"])

    if any((lvl == 2 and kind is not GEAR) or kind is True for lvl, _, _, kind in items):
        # GW app export: model lines are the • lines that own ◦ wargear lines,
        # plus lines already known to be models.
        groups = []
        for lvl, n, name, kind in items:
            if lvl == 1:
                groups.append([n, name, [], kind is True])
            elif groups:
                groups[-1][2].append((name, n, kind is GEAR))
        groups = [g[:3] for g in groups if g[2] or g[3]]
    else:
        # Flat format: model lines are recognised by rank titles or by
        # ending in a word from the unit's name ("9x Pathfinders").
        groups = []
        known = ({m for m, _ in override} if override else set()) | {clean(m).casefold() for m in model_names}
        for _, n, name, kind in items:
            if kind is GEAR:
                if groups:
                    groups[-1][2].append((name, n, True))
                else:
                    groups.append([1, unit["name"], [(name, n, True)], "single"])
                continue
            toks = [t for t in re.findall(r"[a-z0-9]+", clean(name).lower().replace("'", ""))]
            last = tokens_of(toks[-1]) if toks else set()
            # a rank titles a model when it ends the line ("Ravener Prime",
            # "Stealth Shas'vre"), not when it starts a weapon ("Prime claws")
            joined = re.findall(r"[a-z]+", clean(name).lower().replace("'", ""))
            is_model = (name in known or clean(name).casefold() in known
                        or bool(joined and joined[-1] in RANKS) or bool(last & unit_tokens))
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
        groups = [[1, unit["name"], [(name, n, kind is GEAR) for _, n, name, kind in items]]]

    models = []
    for n, name, gear in groups:
        for wargear, counts in split_wargear(n, gear):
            models.append({"name": name, "wargear": sorted(wargear),
                           "gear": [{"name": k, "count": v} for k, v in sorted(counts.items())]})
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

    def favourites(self, unit, model, picks):
        """The favourites among `picks`, best fit for this model first, like
        ranked() but from any army and however little the names agree."""
        if model["name"] in self.aliases:
            model = {**model, "name": self.aliases[model["name"]]}
        scored = []
        for g, i, nick, toks in self.entries:
            if f"{g}:{i}" in picks:
                coverage, fit = score(toks, model, unit, self.free, self.weight)
                scored.append(((round(coverage, 2), 0, round(fit, 2)), g, i, nick))
        return sorted(scored, key=lambda x: x[0], reverse=True)

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


def resolve(army, catalog, mappings, prefer_static=False, repick=False, sheet_cache=None):
    """Attach catalogue picks to every model. Returns rows for reporting.
    repick ignores (and overwrites) this list's existing pins. A unit's
    favourite figures (mappings.json "favorites") come first, from any army,
    whatever they're called: of several, the one that fits the model best
    (name, then wargear) is used. A model the catalogue has no figure
    for, on a matched datasheet, tries a look-alike's (datasheets.stand_in:
    "Dominion" -> "Battle Sister")."""
    matcher = Matcher(catalog, army, mappings.get("aliases", {}).get(army["faction"]))
    looks = {}

    def look_alike(u, m):
        import datasheets
        if "sheets" not in looks:
            looks["sheets"] = datasheets.Datasheets(army["sub"] or army["faction"], sheet_cache)
        sheet = looks["sheets"].get(u["datasheet"]["id"])
        return datasheets.stand_in(looks["sheets"], sheet, m["sheet_model"]) if sheet else None
    pinned = mappings.setdefault("models", {})
    favourites = mappings.get("favorites", {})
    scope = army["sub"] or army["faction"]
    rows = []
    redone = set()
    for u in army["units"]:
        variant = {}
        liked = set(favourites.get(f"{scope}|{u['name']}", []))
        for m in u["models"]:
            key = model_key(army["faction"], u, m)
            if key in pinned and not (repick and key not in redone):
                picks, how = pinned[key], "pinned"
            else:
                redone.add(key)
                cands, best, via = [], None, ""
                if liked:
                    fits = matcher.favourites(u, m, liked)
                    if fits:
                        best = fits[0][0]
                        cands, via = [x for x in fits if x[0] == best][:4], " favourite"
                if not cands:
                    cands, best = matcher.candidates(u, m, u["allied"], prefer_static)
                if not cands and u.get("datasheet") and m.get("sheet_model"):
                    alt = look_alike(u, m)
                    # the look-alike's own name, and without its loadout ("Battle Sister w/ Special
                    # Weapon" -> "Battle Sister", leaving the model's wargear to pick the figure):
                    # whichever matches better
                    for name in dict.fromkeys([alt[1], alt[1].split(" w/ ")[0]]) if alt else []:
                        c, b = matcher.candidates({**u, "name": alt[0]}, {**m, "name": name}, u["allied"], prefer_static)
                        if c and (not cands or b > best):
                            cands, best, via = c, b, f" via {name}"
                picks = [f"{g}:{i}" for _, g, i, _ in cands]
                how = (f"auto cover={best[0]:.0%}" + (" static" if prefer_static else "") + via) if picks else "NO MATCH"
                if picks:
                    pinned[key] = picks
            n = variant.get(key, 0)
            variant[key] = n + 1
            m["pick"] = picks[n % len(picks)] if picks else None
            rows.append((u, m, how))
    return rows


def nickname(catalog, pick):
    g, i = pick.split(":")
    return catalog[g][int(i)].get("Nickname", "").strip()


def sheet_label(unit):
    """"datasheet: <name> (<how>)", for plan output."""
    if "datasheet" not in unit:
        return ""
    d = unit["datasheet"]
    return f"datasheet: {d['name']} ({d['how']})" if d else "datasheet: NONE"


def print_bases(army):
    import bases

    models = [m for u in army["units"] for m in u["models"]]
    if not any("base" in m for m in models):
        return
    known = sum(1 for m in models if m.get("base") and m["base"]["shape"] in ("round", "oval"))
    print(f"{known} of {len(models)} models have a base size")
    gaps = bases.missing(army)
    if gaps:
        print("  base size: " + "; ".join(f"{u}: {m} ({why})" for u, m, why in gaps))


def print_plan(army, catalog, rows):
    """Each unit's datasheet and each model's pick. With no catalogue (rows
    None), just the datasheets."""
    import datasheets

    print(f"{army['title']}  [{army['faction']}{' / ' + army['sub'] if army['sub'] else ''}]")
    if rows is None:
        for u in army["units"]:
            print(f"  {u['name'][:44]:44} {sheet_label(u)}")
    else:
        last = None
        for unit, m, how in rows:
            if unit is not last:
                print(f"\n  {unit['name']}  {sheet_label(unit)}")
                last = unit
            got = nickname(catalog, m["pick"]) if m["pick"] else "-"
            gear = f"  ({', '.join(m['wargear'])})" if m["wargear"] else ""
            print(f"    {m['name'][:34]:34} -> {got[:48]:48} {how}{gear[:60]}")
        missing = sum(1 for _, m, _ in rows if not m["pick"])
        print(f"\n{len(rows)} models, {missing} unmatched")
    print_bases(army)
    if not any(u.get("datasheet") for u in army["units"]):
        return  # nothing cached, or nothing matched: sheet_label already says so
    miss = datasheets.unmatched(army)
    matched = sum(1 for u in army["units"] if u.get("datasheet"))
    print(f"{len(army['units'])} units, {matched} matched to datasheets")
    for label, items in (("no datasheet", miss["units"]),
                         ("model not on its datasheet", [f"{u}: {m}" for u, m in miss["models"]]),
                         ("wargear not on its datasheet", [f"{u}: {w}" for u, w in miss["wargear"]])):
        if items:
            print(f"  {label}: " + "; ".join(items))


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


# Tags every spawned model carries (TTS object tags, beside the army tag in GM Notes):
# which unit of its list it's in, and that unit's datasheet. board.py groups models
# into units by the first; the second finds the unit's datasheet in the data cache.
UNIT_TAG = "tts-bridge:unit:"     # + the unit's place in the list, from 1
SHEET_TAG = "tts-bridge:sheet:"   # + the datasheet's id


def unit_tags(index, unit):
    """The tags for a model of the unit at `index` (from 1) in its list."""
    tags = [f"{UNIT_TAG}{index}"]
    if unit.get("datasheet"):
        tags.append(f"{SHEET_TAG}{unit['datasheet']['id']}")
    return tags


def tag(obj, tags):
    """Put our tags on a catalogue object, replacing any of ours it already has."""
    obj["Tags"] = [t for t in obj.get("Tags") or [] if not t.startswith("tts-bridge:")] + tags
    return obj


def model_objects(army, catalog):
    """Each picked model's catalogue object, ready to spawn: its unit's name on
    the first description line (board.py groups by it), then its datasheet
    tooltip when datasheets are cached (tooltips.py), the army tag in GM
    Notes, its unit tags, and the datasheet viewer script (sheetviewer.py).
    -> (objects, models, units) with units as [(name, [object index])]."""
    import sheetviewer
    import tooltips

    if not any("tooltip" in m for u in army["units"] for m in u["models"]):
        tooltips.attach(army)
    objs, models, units = [], [], []
    for index, u in enumerate(army["units"], 1):
        members = []
        for m in u["models"]:
            if not m["pick"]:
                continue
            g, i = m["pick"].split(":")
            o = copy.deepcopy(catalog[g][int(i)])
            o.pop("GUID", None)
            tooltips.describe(o, u["name"], m)
            tag(o, unit_tags(index, u))
            if u.get("card"):
                sheetviewer.attach(o, u["name"], u["card"])
            o["GMNotes"] = f"army.py:{army['title']}"
            members.append(len(objs))
            objs.append(o)
            models.append(m)
        units.append((u["name"], members))
    return objs, models, units


def groups(army, units):
    """model_objects' units as the blocks to lay out: each unit with the leaders
    attached to it (bodyguard first), then everything else. -> [[unit index]]."""
    led = {}
    for i, u in enumerate(army["units"]):
        if u.get("attached_to") is not None and u["attached_to"] < len(army["units"]):
            led.setdefault(u["attached_to"], []).append(i)
    placed = {i for leaders in led.values() for i in leaders}
    return [[i] + led.get(i, []) for i in range(len(units)) if i not in placed]


def block(sizes, gap):
    """Models in a rough square, lines of at most 5. -> ([(dx, dz)] centres from
    the block's top-left, width, depth)."""
    per_row = min(5, math.ceil(math.sqrt(len(sizes))))
    out, width, z = [], 0.0, 0.0
    for r in range(0, len(sizes), per_row):
        line, x = sizes[r:r + per_row], 0.0
        depth = max(d[1] for d in line)
        for d in line:
            out.append((x + d[0] / 2, z - depth / 2))
            x += d[0] + gap
        width, z = max(width, x - gap), z - depth - gap
    return out, width, -z - gap


def pack(units, dims, x0, z0, width, gap=0.4, pad=2.0, blocks=None):
    """Lay units out as a compact block from (x0, z0), its top-left: each unit a
    rough square, leaders beside the unit they lead (`blocks`, from groups()),
    and the blocks packed in rows, deepest first, with `pad` between them. Rows
    are as wide as makes the army roughly square, and never wider than `width`.
    dims[k] is object k's [width, depth]. -> [(k, x, z)] centres."""
    laid = []
    for members in blocks or [[i] for i in range(len(units))]:
        parts, x, depth = [], 0.0, 0.0
        for i in members:
            ks = units[i][1]
            if not ks:
                continue
            spots, w, d = block([dims[k] for k in ks], gap)
            parts += [(k, x + dx, dz) for k, (dx, dz) in zip(ks, spots)]
            x, depth = x + w + gap * 2, max(depth, d)   # a leader stands close by, not a unit apart
        if parts:
            laid.append((parts, x - gap * 2, depth))
    if not laid:
        return []
    area = sum((w + pad) * (d + pad) for _, w, d in laid)
    row_w = min(width, max(max(w for _, w, _ in laid), math.sqrt(area)))
    moves, cx, cz, row_d = [], x0, z0, 0.0
    for parts, w, d in sorted(laid, key=lambda b: -b[2]):
        if cx > x0 and cx + w > x0 + row_w:
            cx, cz, row_d = x0, cz - row_d - pad, 0.0
        moves += [(k, cx + dx, cz + dz) for k, dx, dz in parts]
        cx, row_d = cx + w + pad, max(row_d, d)
    return sorted(moves)


def spawn(army, catalog, x0, z0, width=110, facing=180, run_lua=None, log=print):
    """Spawn the army's picked models in TTS and lay them out. -> their GUIDs."""
    run_lua = run_lua or tts.run_lua
    objs, _, units = model_objects(army, catalog)
    if not objs:
        raise SystemExit("No models to spawn: nothing in this list matched a Force Org model.")

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
    reply = run_lua("\n".join(lines), timeout=60)
    if reply is None:
        raise SystemExit("TTS didn't answer. Is a game loaded, and is the External Editor API on?")
    guids = json.loads(reply)

    sizes = None
    for _ in range(60):
        r = run_lua(SPAWN_LUA_WAIT.format(ids=lua_list(guids)))
        if r and r != "wait":
            sizes = json.loads(r)
            break
        time.sleep(1)
    if sizes is None:
        log("Models still loading after 60s; laying out with guessed sizes.")
        sizes = {}

    dims = [sizes.get(g, [2, 2]) for g in guids]
    lua = [f'do local o = getObjectFromGUID("{guids[k]}") if o then '
           f'o.setPosition({{{x:.2f}, 3, {z:.2f}}}) o.setRotation({{0, {facing}, 0}}) '
           f'o.setLock(false) end end'
           for k, x, z in pack(units, dims, x0, z0, width, blocks=groups(army, units))]
    run_lua("\n".join(lua), timeout=30)
    return guids


def file_name(text, default):
    """A list's own words as one file or folder name: no path separators or
    characters Windows forbids, and never "." or ".." (a list can say anything)."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", text or "").strip().strip(".").strip()
    return name or default


def saved_object_path(army):
    """<Saved Objects>/<chapter or faction>/<title>.json, inside the Saved Objects folder."""
    folder = saved_objects_dir() / file_name(army["sub"] or army["faction"], "Armies")
    return folder / f"{file_name(army['title'], 'Army')}.json"


def write_saved_object(army, states):
    """Write objects as a TTS Saved Object (Objects -> Saved Objects in TTS). -> its path."""
    path = saved_object_path(army)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "SaveName": "", "GameMode": "", "Gravity": 0.5, "PlayArea": 0.5, "Date": "",
        "Table": "", "Sky": "", "Note": "", "Rules": "", "XmlUI": "", "LuaScript": "",
        "LuaScriptState": "", "ObjectStates": states, "TabStates": {}, "VersionNumber": "",
    }, indent=2))
    return path


def save_object(army, guids):
    """The spawned models, read back from TTS, as a Saved Object."""
    time.sleep(2)
    raw = tts.run_lua(f"local out = {{}} for _, id in ipairs({lua_list(guids)}) do "
                      f"local o = getObjectFromGUID(id) if o then table.insert(out, o.getJSON(false)) end "
                      f"end return out",
                      timeout=30)
    return write_saved_object(army, [json.loads(s) for s in json.loads(raw)])


def footprint(model):
    """A model's [width, depth] in inches from its base, for laying out
    without TTS: a 32mm round base when the base isn't known."""
    b = model.get("base")
    if b and b.get("inches"):
        return [b["inches"][0], b["inches"][-1]]
    return [1.26, 1.26]


def build_saved_object(army, catalog, width=40, facing=180):
    """A Saved Object built from the catalogue alone, so TTS needn't be
    running: models laid out by their base sizes, facing `facing`. -> its path."""
    objs, models, units = model_objects(army, catalog)
    if not objs:
        raise SystemExit("No models to save: nothing in this list matched a Force Org model.")
    dims = [footprint(m) for m in models]
    for k, x, z in pack(units, dims, 0, 0, width, blocks=groups(army, units)):
        objs[k]["Transform"].update(posX=round(x, 2), posY=1.5, posZ=round(z, 2), rotX=0, rotY=facing, rotZ=0)
        objs[k]["Locked"] = False
    return write_saved_object(army, objs)


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

    import bases
    import datasheets

    mappings = load_mappings()
    army = datasheets.parse(Path(args[1]).read_text(), mappings)
    if not any("datasheet" in u for u in army["units"]):
        print("(No datasheets cached. Run `python3 data.py fetch bsdata` to match units to them.)\n")
    bases.attach(army, mappings)
    if args[0] == "plan" and not CATALOG.exists():  # datasheets only
        print_plan(army, None, None)
        MAPPINGS.write_text(json.dumps(mappings, indent=1, ensure_ascii=False))
        return
    catalog = load_catalog()
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
