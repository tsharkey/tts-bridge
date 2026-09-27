"""
tooltips.py — datasheet tooltips for spawned models: what TTS shows when you
hover over one.

    import tooltips
    tooltips.attach(parsed)        # after datasheets.parse (and bases.attach, for base sizes)
    parsed["units"][0]["models"][0]["tooltip"]   # {"name": ..., "text": ...}

Every model gets its stat line, base and the weapons it carries. One model per
unit (its leader or sergeant, or its only model) also gets the unit's
abilities, rules, enhancements and keywords, so the others stay short. The
text is TTS BBCode ([b], [i], [RRGGBB]...[-]). army.model_objects writes it
after the "[<unit>]" line that board.py groups units by.
"""

import re

import datasheets

LABEL = "9aa1ad"   # labels, in the hub's muted grey
ACCENT = "e8b53e"  # names, in the hub's accent


def plain(text):
    """BSData's markup ("**MONSTER**", "^^Markerlight^^") as plain text."""
    return re.sub(r"\*\*|\^\^", "", text or "").strip()


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


def attach(parsed, cache=None):
    """Give every model of a datasheet-matched unit a "tooltip". -> how many."""
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
    return count


def describe(obj, unit_name, model):
    """Name a catalogue object for its model and put the tooltip in its
    description, after the "[<unit>]" line board.py groups by. Keeps the
    catalogue's own description (usually who made the model) at the end."""
    credit = (obj.get("Description") or "").strip()
    tip = model.get("tooltip")
    if tip:
        obj["Nickname"] = tip["name"]
        obj["Description"] = f"[{unit_name}]\n{tip['text']}" + (f"\n\n[i]{credit}[/i]" if credit else "")
    else:
        obj["Description"] = f"[{unit_name}]\n" + credit
    return obj
