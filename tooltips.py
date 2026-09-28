"""
tooltips.py — datasheet tooltips for spawned models: what TTS shows when you
hover over one.

    import tooltips
    tooltips.attach(parsed)        # after datasheets.parse (and bases.attach, for base sizes)
    parsed["units"][0]["models"][0]["tooltip"]   # {"name": ..., "text": ...}

Every model's tooltip has the same sections, a blank line apart, as
Yellowscribe's do: its stats (names over values), the weapons it carries,
ranged then melee (each name on its own line, its profile under it), the unit's
abilities and rules by name, then keywords and base. One model per unit (its
leader or sergeant, or its only model) also shows the warlord and enhancements. A model with more than one
wound is named "[<left>/<max>] <name>" for the wound tracker. The unit gets its
whole datasheet as "card" text (card_text), with every rule explained, for the
datasheet viewer on each of its models (sheetviewer.py). The text is TTS BBCode
([b], [i], [RRGGBB]...[-]). army.model_objects writes it after the "[<unit>]"
line that board.py groups units by.
"""

import re

import datasheets

LABEL = "9aa1ad"      # labels and rules, in the hub's muted grey
ACCENT = "e8b53e"     # weapon names, warlord and enhancements, in the hub's accent
STATS = "8fd694"      # section headings: stats in green,
WEAPONS = "ef6f6c"    # weapons in red,
ABILITIES = "c49bf2"  # abilities in purple
RULE = "[9aa1ad]" + "-" * 28 + "[-]"   # under the name, as Yellowscribe does


def plain(text):
    """BSData's markup ("**MONSTER**", "^^Markerlight^^", "*Example:*") as plain text."""
    return re.sub(r"\*+|\^\^", "", text or "").strip()


def colour(hexcode, text):
    return f"[{hexcode}]{text}[-]"


STAT_KEYS = ("M", "T", "Sv", "W", "Ld", "OC", "InSv")


def stat_table(stats):
    """A model's characteristics as two lines, names over values, padded to
    line up roughly (TTS's tooltip font isn't monospaced). None without stats."""
    keys = [k for k in STAT_KEYS if (stats or {}).get(k)]
    if not keys:
        return None
    widths = [max(len(k), len(str(stats[k]))) + 3 for k in keys]
    head = "".join(k.ljust(w) for k, w in zip(keys, widths)).rstrip()
    values = "".join(str(stats[k]).ljust(w) for k, w in zip(keys, widths)).rstrip()
    return [colour(STATS, head), f"[b]{values}[/b]"]


def stat_line(stats):
    """A model's characteristics on one line, for the datasheet's model list."""
    if not stats:
        return None
    return "  ".join(f"[b]{k}[/b] {stats[k]}" for k in STAT_KEYS if stats.get(k))


def weapon_stats(w):
    melee = w.get("type") == "melee"
    kind = "WS" if melee else "BS"
    stats = "  ".join(x for x in (
        w.get("range") if w.get("range") and w["range"] != "Melee" else "Melee" if melee else None,
        f"A{w['A']}" if w.get("A") else None, f"{kind}{w['skill']}" if w.get("skill") else None,
        f"S{w['S']}" if w.get("S") else None, f"AP{w['AP']}" if w.get("AP") else None,
        f"D{w['D']}" if w.get("D") else None) if x)
    keywords = f"  [i]{', '.join(w['keywords'])}[/i]" if w.get("keywords") else ""
    return stats + keywords


def weapon_lines(w, count=1):
    """A weapon profile as two lines: its name (and how many), then its stats."""
    stats = weapon_stats(w)
    return [f"{f'{count}× ' if count > 1 else ''}{colour(ACCENT, w['name'])}"] + ([stats] if stats else [])


def heading(hexcode, text):
    return colour(hexcode, f"[b]{text}[/b]")


WEAPON_RE = re.compile(r"^(?:\d+× )?\[" + ACCENT + r"\](.+?)\[-\](?:  |$)")
# this template's, then the ones before: one "Weapons" section, and the one-line template's
WEAPON_HEADINGS = (heading(WEAPONS, "Ranged weapons"), heading(WEAPONS, "Melee weapons"),
                   heading(WEAPONS, "Weapons"), colour(LABEL, "Weapons"))


def weapon_names(description):
    """The weapon profiles a spawned model's tooltip lists (weapon_lines), by
    name. Reads the earlier one-line template too, for models already spawned."""
    out, inside = [], False
    for line in (description or "").splitlines():
        m = WEAPON_RE.match(line)
        if line in WEAPON_HEADINGS:
            inside = True
        elif inside and m:
            out.append(m.group(1))
        elif not line or line.startswith("[") and not line.startswith("[i]"):
            inside = False   # the end of a section: a blank line or the next heading
    return list(dict.fromkeys(out))


def base_line(model):
    b = model.get("base")
    if not b or b["shape"] == "none":
        return None
    return colour(LABEL, f"Base: {'use the model' if b['shape'] == 'model' else b['text']}")


def carried(model, sheet):
    """[(datasheet wargear name, count)] a model carries: its wargear and its
    gear (drones included), matched to the datasheet, and whatever its
    datasheet model is always equipped with that the list left out (an export
    can drop a line: "5x Guardian spear" without its bullet)."""
    out = {}
    for g in model.get("gear") or [{"name": w, "count": 1} for w in model["wargear"]]:
        name = datasheets.match_wargear(g["name"], sheet)
        if name:
            out[name] = max(out.get(name, 0), g["count"])
    sm = next((m for m in sheet["models"] if m["name"] == model.get("sheet_model")), None)
    for e in (sm or {}).get("equipped", []):
        if e["name"] in sheet["wargear"] and e["name"] not in out:
            out[e["name"]] = e["count"]
    return list(out.items())


def lead_model(unit, sheet):
    """Index of the model that carries what the list chose for the unit
    (warlord, enhancements): its one required single model (the sergeant or
    leader), else the first."""
    singles = {m["name"] for m in sheet["models"] if m["min"] == m["max"] == 1}
    return next((i for i, m in enumerate(unit["models"]) if m.get("sheet_model") in singles), 0)


def wounds(stats):
    """A model's Wounds as a number, when it has more than one (the wound tracker's)."""
    w = str((stats or {}).get("W") or "")
    return int(w) if w.isdigit() and int(w) > 1 else None


def tooltip(unit, model, sheet, lead):
    """{"name", "text"} for one model: its stats, the weapons it carries, and
    the unit's abilities by name (the datasheet viewer has them in full). A
    model with more than one wound is named "[<left>/<max>] <name>" for the
    wound tracker (sheetviewer.py). lead: whether it shows what the list
    chose for the unit (warlord, enhancements)."""
    sm = next((m for m in sheet["models"] if m["name"] == model.get("sheet_model")), None)
    stats = sm and sm["stats"]
    sections = [[RULE] + (stat_table(stats) or [])]
    ranged, melee, gear = [], [], []
    for name, count in carried(model, sheet):
        w = sheet["wargear"][name]
        for p in w["weapons"]:
            (melee if p.get("type") == "melee" else ranged).extend(weapon_lines(p, count))
        gear += [a["name"] for a in w["abilities"]]
    if ranged:
        sections.append([heading(WEAPONS, "Ranged weapons")] + ranged)
    if melee:
        sections.append([heading(WEAPONS, "Melee weapons")] + melee)
    abilities = [a["name"] for a in sheet["abilities"]] + gear + list(sheet["rules"])
    if abilities:
        sections.append([heading(ABILITIES, "Abilities")] + list(dict.fromkeys(abilities)))
    chosen = ((["Warlord"] if unit.get("warlord") else [])
              + [f"Enhancement: {e}" for e in unit.get("enhancements") or []]) if lead else []
    footer = ([colour(ACCENT, " · ".join(chosen))] if chosen else []) + [x for x in (
        colour(LABEL, f"Keywords: {', '.join(sheet['keywords'])}") if sheet["keywords"] else None,
        base_line(model)) if x]
    if footer:
        sections.append(footer)
    w = wounds(stats)
    name = f"[{w}/{w}] {model['name']}" if w else model["name"]
    return {"name": name, "text": "\n\n".join("\n".join(s) for s in sections)}


# --------------------------------------------------------------------------
# The whole datasheet, for the viewer on each model (sheetviewer.py) and Scribe.

def rules_for(sheet, keywords):
    """The glossary entries a unit needs: its own rules, and the rules behind
    the keywords of the weapons it carries ("Anti-Infantry 4+" -> Anti)."""
    def norm(name):
        return name.casefold().replace(" ", "-")
    wanted = {norm(r) for r in sheet["rules"]}
    keys = [norm(k) for k in keywords]
    out = {}
    for name, text in (sheet.get("glossary") or {}).items():
        n = norm(name)
        if n in wanted or any(k.startswith(n) for k in keys):
            out[name] = text
    return out


def card_text(unit, sheet, units=()):
    """The unit's whole datasheet as TTS BBCode, in the tooltip's sections
    with a blank line between them: what the list chose, every model's stats,
    the weapons it carries, abilities, rules explained, keywords."""
    sections = []
    chosen = []
    if unit.get("role") in ("leader", "support") and unit.get("attached_to") is not None and units:
        chosen.append(f"{unit['role'].title()} of {units[unit['attached_to']]['name']}")
    elif unit.get("role") == "bodyguard":
        chosen.append("Bodyguard")
    if unit.get("warlord"):
        chosen.append("Warlord")
    chosen += [f"Enhancement: {e}" for e in unit.get("enhancements") or []]
    counts = {}
    for m in unit["models"]:
        counts[m.get("sheet_model") or m["name"]] = counts.get(m.get("sheet_model") or m["name"], 0) + 1
    sections.append(([colour(ACCENT, " · ".join(chosen))] if chosen else [])
                    + [" · ".join(f"{n}× {plain(name)}" for name, n in counts.items())])

    models = [heading(STATS, "MODELS")]
    shown = [m for m in sheet["models"] if m["name"] in counts] or sheet["models"]
    for sm in shown:
        stats = stat_line(sm["stats"])
        models.append(f"[b]{plain(sm['name'])}[/b]  {stats}" if stats else f"[b]{plain(sm['name'])}[/b]")
    sections.append(models)

    carried_names = {}
    for m in unit["models"]:
        for name, count in carried(m, sheet):
            carried_names[name] = max(carried_names.get(name, 0), count)
    weapons = [(p, n) for name, n in carried_names.items() for p in sheet["wargear"][name]["weapons"]]
    for kind, title in (("ranged", "RANGED WEAPONS"), ("melee", "MELEE WEAPONS")):
        these = [line for p, _ in weapons if (p.get("type") == "melee") == (kind == "melee")
                 for line in weapon_lines(p)]
        if these:
            sections.append([heading(WEAPONS, title)] + these)

    def explained(a):
        name = f"[b][u]{a['name']}[/u][/b]"   # underlined, so each rule's text is easy to find
        return f"{name}\n{plain(a['text'])}" if a.get("text") else name

    if sheet["abilities"]:
        sections.append([heading(ABILITIES, "ABILITIES")] + [explained(a) for a in sheet["abilities"]])
    gear = [a for name in carried_names for a in sheet["wargear"][name]["abilities"]]
    if gear:
        sections.append([heading(ABILITIES, "WARGEAR ABILITIES")] + [explained(a) for a in gear])
    rules = rules_for(sheet, [k for p, _ in weapons for k in p.get("keywords") or []])
    if rules:
        sections.append([heading(ABILITIES, "RULES")]
                        + [explained({"name": n, "text": t}) for n, t in rules.items()])
    footer = []
    if sheet["keywords"]:
        footer.append(f"{colour(LABEL, '[b]KEYWORDS[/b]')} {', '.join(sheet['keywords'])}")
    if sheet["factions"]:
        footer.append(f"{colour(LABEL, '[b]FACTION[/b]')} {', '.join(sheet['factions'])}")
    if footer:
        sections.append(footer)
    return "\n\n".join("\n".join(s) for s in sections)


def attach(parsed, cache=None):
    """Give every model of a datasheet-matched unit a "tooltip", and the unit
    its whole datasheet as "card" text. -> how many models."""
    sheets = None
    count = 0
    for u in parsed["units"]:
        if not u.get("datasheet"):
            continue
        if sheets is None:
            sheets = datasheets.Datasheets(parsed["sub"] or parsed["faction"], cache)
        sheet = sheets.get(u["datasheet"]["id"])
        if not sheet:
            continue
        lead = lead_model(u, sheet)
        for i, m in enumerate(u["models"]):
            m["tooltip"] = tooltip(u, m, sheet, i == lead)
            count += 1
        u["card"] = card_text(u, sheet, parsed["units"])
    return count


def describe(obj, unit_name, model):
    """Name a catalogue object for its model and put the tooltip in its
    description, after the "[<unit>]" line board.py groups by. The catalogue's
    own description (usually who made the model) is dropped: the tooltip is ours."""
    tip = model.get("tooltip")
    if tip:
        obj["Nickname"] = tip["name"]
    obj["Description"] = f"[{unit_name}]" + (f"\n{tip['text']}" if tip else "")
    return obj
