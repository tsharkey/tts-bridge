"""
bsdata.py — import BSData's 40K catalogues (the BattleScribe schema, as JSON)
into our own datasheet format. `data.py fetch bsdata` calls this; everything
downstream reads the format (docs/formats/datasheet.md), never BSData itself.

    from bsdata import import_folder
    catalogues, skipped = import_folder(Path("cache/bsdata/raw"))

A catalogue's units are its root entries. Links resolve the way BattleScribe
does: in the catalogue itself, then the catalogues it links to, then the game
system. A flat import ignores `modifiers` (Crusade, Boarding Actions,
detachment-conditional changes) and leaves out hidden entries; `skipped`
counts what was left out and why.
"""

import json
import re
from collections import Counter
from pathlib import Path

CHILDREN = ("selectionEntries", "selectionEntryGroups", "entryLinks")
# Options that only exist in Crusade or the army builder, not on the datasheet.
NOT_DATASHEET = re.compile(r"\b(crusade|weapon modifications|battle honours|enhancements?)\b|^warlord$", re.I)
STATS = {"M": "M", "T": "T", "Sv": "Sv", "InSv": "InSv", "W": "W", "LD": "Ld", "OC": "OC"}
WEAPONS = {"Ranged Weapons": "ranged", "Melee Weapons": "melee"}


def text_of(ch):
    return (ch.get("$text") or "").strip()


def minmax(node):
    """(min, max) selections of a node within its parent; max None = no limit."""
    lo, hi = 0, None
    for c in node.get("constraints") or []:
        if c.get("field") == "selections" and c.get("scope") == "parent":
            if c.get("type") == "min":
                lo = int(c["value"])
            elif c.get("type") == "max":
                hi = int(c["value"])
    return lo, hi


def stats_of(profile):
    return {STATS[c["name"]]: text_of(c) or None for c in profile.get("characteristics") or [] if c["name"] in STATS}


def faction_of(cat):
    """The army a catalogue is for, named as army.FACTIONS names it."""
    if cat.get("library") or cat.get("type") == "gameSystem":
        return None
    name = cat["name"].split(" - ")[-1]
    return {"Craftworlds": "Aeldari"}.get(name, name)


class Catalogues:
    def __init__(self, files):
        """files: {file name: parsed JSON} for one BSData checkout."""
        self.cats = {}
        for data in files.values():
            cat = data.get("catalogue") or data.get("gameSystem")
            cat.setdefault("type", "catalogue" if "catalogue" in data else "gameSystem")
            self.cats[cat["id"]] = cat
        self.game = next((c for c in self.cats.values() if c["type"] == "gameSystem"), None)
        self.ids = {cid: self.index(c) for cid, c in self.cats.items()}
        self.skipped = Counter()
        # the game's own rules (weapon keywords, core abilities), by name, to explain keywords
        self.core_rules = {r["name"].casefold(): r for r in (self.game or {}).get("sharedRules") or []
                           if r.get("description")}
        self.glossary = {}

    @staticmethod
    def index(cat):
        out = {}

        def walk(x):
            if isinstance(x, dict):
                if isinstance(x.get("id"), str):
                    out.setdefault(x["id"], x)
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(cat)
        return out

    def scope(self, cat):
        """Catalogue ids to resolve links in, nearest first."""
        order, todo = [], [cat["id"]]
        while todo:
            cid = todo.pop(0)
            if cid in order or cid not in self.cats:
                continue
            order.append(cid)
            todo += [ln["targetId"] for ln in self.cats[cid].get("catalogueLinks") or []]
        if self.game and self.game["id"] not in order:
            order.append(self.game["id"])
        return order

    def imports(self, cat):
        """Catalogues whose root entries this one's army can take."""
        return [self.cats[ln["targetId"]]["name"] for ln in cat.get("catalogueLinks") or []
                if ln.get("importRootEntries") and ln["targetId"] in self.cats]

    # ------------------------------------------------------------------

    def import_catalogue(self, cat):
        self.chain = [self.ids[c] for c in self.scope(cat)]
        units = []
        roots = [dict(ln, _link=True) for ln in cat.get("entryLinks") or []] + list(cat.get("selectionEntries") or [])
        for r in roots:
            e = self.follow(r)
            if e is None:
                continue
            if e.get("type") not in ("unit", "model"):
                continue
            if e.get("hidden"):
                self.skipped["hidden unit"] += 1
                continue
            units.append(self.unit(e, cat["name"]))
        return {"catalogue": cat["name"], "id": cat["id"], "revision": cat.get("revision"),
                "faction": faction_of(cat), "library": bool(cat.get("library")),
                "imports": self.imports(cat), "units": units}

    def lookup(self, target):
        for ids in self.chain:
            if target in ids:
                return ids[target]
        return None

    def follow(self, node):
        """A child node with any link resolved: the target's content under the
        link's own constraints, hidden flag and extra children."""
        if not (node.get("_link") or node.get("targetId") and node.get("type") in
                ("selectionEntry", "selectionEntryGroup")):
            return node
        target = self.lookup(node["targetId"])
        if target is None:
            self.skipped["unresolved link"] += 1
            return None
        merged = dict(target)
        merged["constraints"] = node.get("constraints") or target.get("constraints") or []
        merged["hidden"] = bool(node.get("hidden") or target.get("hidden"))
        merged["_group"] = node.get("type") == "selectionEntryGroup"
        for k in CHILDREN + ("profiles", "infoLinks", "categoryLinks", "costs"):
            if node.get(k):
                merged[k] = (target.get(k) or []) + node[k]
        return merged

    def children(self, node, tally=True):
        """A node's options and models, links resolved, minus what the import leaves out."""
        for key in CHILDREN:
            for ch in node.get(key) or []:
                if key == "entryLinks":
                    ch = self.follow(dict(ch, _link=True))
                    if ch is None:
                        continue
                elif key == "selectionEntryGroups":
                    ch = dict(ch, _group=True)
                if ch.get("hidden"):
                    self.skipped["hidden option"] += tally
                    continue
                m = NOT_DATASHEET.search(ch.get("name", ""))
                if m:
                    self.skipped[f"{m.group(0).lower()} option"] += tally
                    continue
                yield ch

    def profiles(self, node):
        out = list(node.get("profiles") or [])
        for ln in node.get("infoLinks") or []:
            if ln.get("type") == "profile" and not ln.get("hidden"):
                p = self.lookup(ln["targetId"])
                if p:
                    out.append(p)
        return out

    def rules(self, node):
        """Names of the rules a node has, noting each one's text in the glossary."""
        names = []
        for r in node.get("rules") or []:
            names.append(r["name"])
            self.explain(r["name"], r.get("description"))
        for ln in node.get("infoLinks") or []:
            if ln.get("type") == "rule" and not ln.get("hidden"):
                names.append(ln["name"])
                self.explain(ln["name"], (self.lookup(ln["targetId"]) or {}).get("description"))
        return names

    def explain(self, name, text):
        if text and text.strip() and name not in self.glossary:
            self.glossary[name] = text.strip()

    def keyword_rule(self, keyword):
        """The game rule a weapon keyword is an instance of: "Anti-Infantry 4+" -> Anti,
        "Sustained Hits 2" -> Sustained Hits, "LETHAL HITS: non-MONSTER" -> Lethal Hits."""
        k = keyword.strip("[] ").casefold()
        base = re.split(r"\s+(?=[\dd+\-\"])", k)[0].strip()
        for guess in (k, k.split(":")[0].strip(), base, base.replace(" ", "-"),  # "Twin Linked"
                      "anti" if k.startswith(("anti-", "anti ")) else ""):
            if guess and guess in self.core_rules:
                return self.core_rules[guess]
        return None

    def has_models(self, node, depth=0):
        if depth > 6:
            return False
        return any(ch.get("type") == "model" or self.has_models(ch, depth + 1)
                   for ch in self.children(node, tally=False))

    # ------------------------------------------------------------------

    def unit(self, e, catalogue):
        self.glossary = {}
        cats = [c["name"] for c in e.get("categoryLinks") or [] if not c.get("hidden")]
        u = {"id": e["id"], "name": e["name"], "catalogue": catalogue,
             "points": next((int(c["value"]) for c in e.get("costs") or [] if c.get("name") == "pts"), None),
             "keywords": [c for c in cats if not c.startswith("Faction: ")],
             "factions": [c.removeprefix("Faction: ") for c in cats if c.startswith("Faction: ")],
             "rules": self.rules(e), "abilities": [], "size": None,
             "models": [], "equipped": [], "options": [], "wargear": {}}
        profiles = self.profiles(e)
        self.add_abilities(u, profiles)
        self.stat_lines = [p for p in profiles if p.get("typeName") == "Unit"]
        if e.get("type") == "model":
            u["models"].append(self.model(e, u, (1, 1)))
            u["size"] = [1, 1]
        else:
            u["size"] = list(self.unit_children(e, u))
        # A model with no stat line of its own shares one from its unit, often
        # the one on its sergeant ("Khorne Berzerker" on the Champion).
        for m in u["models"]:
            if m["stats"] is None:
                line = self.stat_line(m["name"], self.stat_lines)
                m["stats"] = stats_of(line) if line else None
        if not u["equipped"] and not u["options"]:
            del u["equipped"], u["options"]
        for w in u["wargear"].values():  # weapon keywords, explained by the game's rules
            for p in w["weapons"]:
                for k in p["keywords"]:
                    rule = self.keyword_rule(k)
                    if rule:
                        self.explain(rule["name"], rule["description"])
        u["glossary"] = dict(sorted(self.glossary.items()))
        return u

    def unit_children(self, node, u):
        """File a unit's children as models or unit-wide gear. -> (min, max) models."""
        lo, hi = 0, 0
        for ch in self.children(node):
            if ch.get("type") == "model":
                m = self.model(ch, u, minmax(ch))
                u["models"].append(m)
                clo, chi = m["min"], m["max"]
            elif self.has_models(ch):
                options = [g for g in self.children(ch, tally=False) if self.has_models(g)]
                if ch.get("_group") and minmax(ch)[1] == 1 and len(options) > 1:
                    clo, chi = self.compositions(options, u)
                else:
                    clo, chi = self.unit_children(ch, u)
                    glo, ghi = minmax(ch) if ch.get("_group") else (0, None)
                    clo, chi = glo or clo, ghi if ghi is not None else chi
            else:
                self.gear(ch, u, u)
                continue
            lo += clo
            hi = None if hi is None or chi is None else hi + chi
        return lo, hi

    def compositions(self, options, u):
        """A choice between whole compositions ("1 Sergeant and 9 Troopers" or
        "2 Sergeants and 18 Troopers"): each model once, with its range across
        them (from 0 if some composition leaves it out)."""
        sizes, merged, seen_in = [], {}, Counter()
        for opt in options:
            part = dict(u, models=[])
            sizes.append(self.unit_children(opt, part))
            for m in part["models"]:
                seen_in[m["id"]] += 1
                old = merged.setdefault(m["id"], m)
                if old is not m:
                    old["min"] = min(old["min"], m["min"])
                    old["max"] = None if None in (old["max"], m["max"]) else max(old["max"], m["max"])
        for mid, m in merged.items():
            if seen_in[mid] < len(options):
                m["min"] = 0
            u["models"].append(m)
        his = [hi for _, hi in sizes]
        return min(lo for lo, _ in sizes), None if None in his else max(his)

    @staticmethod
    def stat_line(name, lines):
        """The stat line for a model: its own name, else one its name starts
        with ("Khorne Berzerker w/ eviscerator"), else the first."""
        key = name.replace("’", "'").lower()
        named = sorted((p for p in lines if key.startswith(p["name"].replace("’", "'").lower())),
                       key=lambda p: -len(p["name"]))
        return named[0] if named else (lines[0] if lines else None)

    def model(self, e, u, counts):
        profiles = self.profiles(e)
        own = [p for p in profiles if p.get("typeName") == "Unit"]
        self.stat_lines += [p for p in own if p not in self.stat_lines]
        line = self.stat_line(e["name"], own) if own else None
        self.add_abilities(u, profiles)
        m = {"id": e["id"], "name": e["name"], "min": counts[0], "max": counts[1],
             "stats": stats_of(line) if line else None,
             "equipped": [], "options": []}
        for ch in self.children(e):
            if ch.get("type") != "model":
                self.gear(ch, m, u)
        return m

    def gear(self, ch, holder, u):
        """File one child of a model (or unit) as equipped wargear or an option."""
        lo, hi = minmax(ch)
        if ch.get("_group"):
            if not ch.get("constraints"):  # a plain folder ("Wargear"): look inside
                for g in self.children(ch):
                    self.gear(g, holder, u)
                return
            choices = [self.register(g, u) for g in self.children(ch) if g.get("type") != "model"]
            if not choices:
                return
            default = self.lookup(ch["defaultSelectionEntryId"]) if ch.get("defaultSelectionEntryId") else None
            opt = {"name": ch["name"], "min": lo, "max": hi, "choices": choices}
            if default:
                opt["default"] = default["name"]
            holder["options"].append(opt)
            return
        name = self.register(ch, u)
        if lo >= 1:
            holder["equipped"].append({"name": name, "count": lo})
        else:
            holder["options"].append({"name": name, "min": 0, "max": hi, "choices": [name]})

    def register(self, e, u):
        """Add a piece of wargear (with every weapon profile in it) to the unit."""
        name = e["name"]
        if name in u["wargear"]:
            return name
        w = {"id": e["id"], "weapons": [], "abilities": []}
        u["wargear"][name] = w
        todo = [e]
        while todo:
            node = todo.pop(0)
            for p in self.profiles(node):
                kind = WEAPONS.get(p.get("typeName"))
                ch = {c["name"]: text_of(c) for c in p.get("characteristics") or []}
                if kind:
                    kw = ch.get("Keywords", "")
                    w["weapons"].append({
                        "name": p["name"].lstrip("➤ ").strip(), "type": kind, "range": ch.get("Range"),
                        "A": ch.get("A"), "skill": ch.get("BS") or ch.get("WS"), "S": ch.get("S"),
                        "AP": ch.get("AP"), "D": ch.get("D"),
                        "keywords": [k.strip() for k in kw.split(",") if k.strip() and k.strip() != "-"]})
                elif p.get("typeName") == "Abilities":
                    w["abilities"].append({"name": p["name"], "text": ch.get("Description", "")})
            self.rules(node)  # a weapon's own rule links ("Melta"), for the glossary
            todo += [c for c in self.children(node) if c.get("type") != "model"]
        return name

    @staticmethod
    def add_abilities(u, profiles):
        seen = {a["name"] for a in u["abilities"]}
        for p in profiles:
            if p.get("typeName") in ("Unit", *WEAPONS) or p["name"] in seen:
                continue
            text = " ".join(text_of(c) for c in p.get("characteristics") or [] if text_of(c))
            u["abilities"].append({"name": p["name"], "type": p.get("typeName"), "text": text})
            seen.add(p["name"])


def import_folder(folder):
    """-> ({catalogue name: datasheet file content}, Counter of skipped things)"""
    files = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(folder).glob("*.json"))}
    if not files:
        raise ValueError(f"No BSData catalogues (*.json) in {folder}")
    cats = Catalogues(files)
    out = {}
    for cat in cats.cats.values():
        if cat["type"] == "catalogue":
            out[cat["name"]] = cats.import_catalogue(cat)
    return out, cats.skipped
