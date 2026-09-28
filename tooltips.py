"""
tooltips.py — datasheet tooltips for spawned models: what TTS shows when you
hover over one.

    import tooltips
    tooltips.attach(parsed)        # after datasheets.parse (and bases.attach, for base sizes)
    parsed["units"][0]["models"][0]["tooltip"]   # {"name": ..., "text": ...}

Every model gets its stat line, base and the weapons it carries. One model per
unit (its leader or sergeant, or its only model) also gets the unit's
abilities, rules, enhancements and keywords, so the others stay short. The
unit gets its whole datasheet as "card" text (card_text), with every rule
explained, for the datasheet viewer on each of its models (sheetviewer.py). The
text is TTS BBCode ([b], [i], [RRGGBB]...[-]). army.model_objects writes it
after the "[<unit>]" line that board.py groups units by.
"""

import re

import datasheets

LABEL = "9aa1ad"   # labels, in the hub's muted grey
ACCENT = "e8b53e"  # names, in the hub's accent


def plain(text):
    """BSData's markup ("**MONSTER**", "^^Markerlight^^", "*Example:*") as plain text."""
    return re.sub(r"\*+|\^\^", "", text or "").strip()


def colour(hexcode, text):
    return f"[{hexcode}]{text}[-]"


def stat_line(stats):
    if not stats:
        return None
    parts = [f"[b]{k}[/b] {stats[k]}" for k in ("M", "T", "Sv", "W", "Ld", "OC") if stats.get(k)]
    if stats.get("InSv"):
        parts.append(f"[b]InSv[/b] {stats['InSv']}")
    return "  ".join(parts)


def weapon_line(w, count=1):
    melee = w.get("type") == "melee"
    kind = "WS" if melee else "BS"
    stats = "  ".join(x for x in (
        w.get("range") if w.get("range") and w["range"] != "Melee" else "Melee" if melee else None,
        f"A{w['A']}" if w.get("A") else None, f"{kind}{w['skill']}" if w.get("skill") else None,
        f"S{w['S']}" if w.get("S") else None, f"AP{w['AP']}" if w.get("AP") else None,
        f"D{w['D']}" if w.get("D") else None) if x)
    keywords = f"  [i]{', '.join(w['keywords'])}[/i]" if w.get("keywords") else ""
    return f"{f'{count}× ' if count > 1 else ''}{colour(ACCENT, w['name'])}  {stats}{keywords}"


WEAPON_RE = re.compile(r"^(?:\d+× )?\[" + ACCENT + r"\](.+?)\[-\]  ")


def weapon_names(description):
    """The weapon profiles a spawned model's tooltip lists (weapon_line), by name."""
    lines = (description or "").splitlines()
    start = next((i for i, line in enumerate(lines) if line == colour(LABEL, "Weapons")), None)
    if start is None:
        return []
    out = []
    for line in lines[start + 1:]:
        m = WEAPON_RE.match(line)
        if not m:
            break
        out.append(m.group(1))
    return out


def base_line(model):
    b = model.get("base")
    if not b or b["shape"] == "none":
        return None
    return f"{colour(LABEL, 'Base')} {'use the model' if b['shape'] == 'model' else b['text']}"


def carried(model, sheet):
    """[(datasheet wargear name, count)] a model carries: its wargear and its
    gear (drones included), matched to the datasheet."""
    out = {}
    for g in model.get("gear") or [{"name": w, "count": 1} for w in model["wargear"]]:
        name = datasheets.match_wargear(g["name"], sheet)
        if name:
            out[name] = max(out.get(name, 0), g["count"])
    return list(out.items())


def lead_model(unit, sheet):
    """Index of the model that carries the unit's rules: its one required
    single model (the sergeant or leader), else the first."""
    singles = {m["name"] for m in sheet["models"] if m["min"] == m["max"] == 1}
    return next((i for i, m in enumerate(unit["models"]) if m.get("sheet_model") in singles), 0)


def unit_lines(unit, sheet):
    lines = [colour(LABEL, "Abilities")]
    for a in sheet["abilities"]:
        lines.append(f"[b]{a['name']}:[/b] {plain(a['text'])}" if a.get("text") else f"[b]{a['name']}[/b]")
    if sheet["rules"]:
        lines.append(f"{colour(LABEL, 'Rules')} {', '.join(sheet['rules'])}")
    extras = ([f"{colour(LABEL, 'Enhancement')} {e}" for e in unit.get("enhancements") or []]
              + ([colour(ACCENT, "Warlord")] if unit.get("warlord") else []))
    lines += extras
    keywords = ", ".join(sheet["keywords"])
    if keywords:
        lines.append(f"{colour(LABEL, 'Keywords')} {keywords}")
    if sheet["factions"]:
        lines.append(f"{colour(LABEL, 'Faction')} {', '.join(sheet['factions'])}")
    return lines


def tooltip(unit, model, sheet, lead):
    """{"name", "text"} for one model; lead: whether it carries the unit's rules."""
    sm = next((m for m in sheet["models"] if m["name"] == model.get("sheet_model")), None)
    lines = [x for x in (stat_line(sm and sm["stats"]), base_line(model)) if x]
    weapons, abilities = [], []
    for name, count in carried(model, sheet):
        w = sheet["wargear"][name]
        weapons += [weapon_line(p, count) for p in w["weapons"]]
        abilities += [f"[b]{a['name']}:[/b] {plain(a['text'])}" for a in w["abilities"]]
    if weapons:
        lines += [colour(LABEL, "Weapons")] + weapons
    if abilities:
        lines += [colour(LABEL, "Wargear")] + abilities
    if lead:
        lines += unit_lines(unit, sheet)
    else:
        lines.append(f"[i]Unit abilities: on the {unit['models'][lead_model(unit, sheet)]['name']}[/i]")
    return {"name": model["name"], "text": "\n".join(lines)}


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
    """The unit's whole datasheet as TTS BBCode: what the list chose, every
    model's stats, the weapons it carries, abilities, rules explained, keywords."""
    lines = []
    chosen = []
    if unit.get("role") in ("leader", "support") and unit.get("attached_to") is not None and units:
        chosen.append(f"{unit['role'].title()} of {units[unit['attached_to']]['name']}")
    elif unit.get("role") == "bodyguard":
        chosen.append("Bodyguard")
    if unit.get("warlord"):
        chosen.append("Warlord")
    chosen += [f"Enhancement: {e}" for e in unit.get("enhancements") or []]
    if chosen:
        lines.append(colour(ACCENT, " · ".join(chosen)))
    counts = {}
    for m in unit["models"]:
        counts[m.get("sheet_model") or m["name"]] = counts.get(m.get("sheet_model") or m["name"], 0) + 1
    lines.append(" · ".join(f"{n}× {plain(name)}" for name, n in counts.items()))

    lines.append(colour(LABEL, "[b]MODELS[/b]"))
    shown = [m for m in sheet["models"] if m["name"] in counts] or sheet["models"]
    for sm in shown:
        stats = stat_line(sm["stats"])
        lines.append(f"[b]{plain(sm['name'])}[/b]  {stats}" if stats else f"[b]{plain(sm['name'])}[/b]")

    carried_names = {}
    for m in unit["models"]:
        for name, count in carried(m, sheet):
            carried_names[name] = max(carried_names.get(name, 0), count)
    weapons = [(p, n) for name, n in carried_names.items() for p in sheet["wargear"][name]["weapons"]]
    for kind, title in (("ranged", "RANGED WEAPONS"), ("melee", "MELEE WEAPONS")):
        these = [weapon_line(p, 1) for p, _ in weapons if (p.get("type") == "melee") == (kind == "melee")]
        if these:
            lines += [colour(LABEL, f"[b]{title}[/b]")] + these

    lines.append(colour(LABEL, "[b]ABILITIES[/b]"))
    for a in sheet["abilities"]:
        lines.append(f"[b]{a['name']}:[/b] {plain(a['text'])}" if a.get("text") else f"[b]{a['name']}[/b]")
    gear = [a for name in carried_names for a in sheet["wargear"][name]["abilities"]]
    if gear:
        lines.append(colour(LABEL, "[b]WARGEAR ABILITIES[/b]"))
        lines += [f"[b]{a['name']}:[/b] {plain(a['text'])}" for a in gear]
    rules = rules_for(sheet, [k for p, _ in weapons for k in p.get("keywords") or []])
    if rules:
        lines.append(colour(LABEL, "[b]RULES[/b]"))
        lines += [f"[b]{name}:[/b] {plain(text)}" for name, text in rules.items()]
    if sheet["keywords"]:
        lines.append(f"{colour(LABEL, '[b]KEYWORDS[/b]')} {', '.join(sheet['keywords'])}")
    if sheet["factions"]:
        lines.append(f"{colour(LABEL, '[b]FACTION[/b]')} {', '.join(sheet['factions'])}")
    return "\n".join(lines)


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
