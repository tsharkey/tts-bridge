"""
threat.py — how far a unit reaches: Move, Advance, Charge and weapon ranges from its
datasheet (docs/formats/datasheet.md), measured from the edges of its models' bases.

    import threat, los
    p = threat.profile(datasheet_unit)            # or profile(unit, wargear=[...names...])
    threat.bands(p)                               # move, advance, charge and weapon reach, in inches
    threat.threats(p, gap=9.5)                    # what it can do to a unit 9.5" away, base to base
    threat.bubbles(models, 12)                    # circles whose union is everything within 12" of the unit

Distances are base edge to base edge: a unit threatens everything within its reach of any
of its models' bases, so the area is the union of each base grown by the reach (bubbles).
Dice use their average and maximum: Advance is M + D6 (3.5, 6), Charge is 2D6 (7, 12) after
a normal move, ending within engagement range (board.ENGAGEMENT) of the target.

Simplifications: movement goes straight, ignoring terrain and other models (no pathfinding);
the unit moves at its slowest model's M; a unit's weapons are all its models' weapons, fired
from the model nearest the target; rules that change these numbers (Fly, Deep Strike,
re-rolls, +1 to Advance) aren't applied. Clip what it can shoot now with the LOS engine:
los.unit_visibility, or los.visibility_polygon(model, terrain, max_range=...) to draw it.
"""

import math
import re

ENGAGEMENT = 2.0      # board.ENGAGEMENT
D6 = (3.5, 6)
TWO_D6 = (7, 12)


def inches(text):
    """'10"' -> 10.0, '20+"' -> 20.0; None for "Melee", "-" and blanks."""
    m = re.match(r"\s*(\d+(?:\.\d+)?)", text or "")
    return float(m.group(1)) if m else None


def default_wargear(unit):
    """What a unit's models carry when the list doesn't say: their equipped wargear and each
    option's default."""
    names = []
    for holder in [unit, *unit["models"]]:
        names += [e["name"] for e in holder.get("equipped") or []]
        names += [o["default"] for o in holder.get("options") or [] if o.get("default") and o.get("min", 0) >= 1]
    return list(dict.fromkeys(names))


def profile(unit, wargear=None):
    """A datasheet unit's reach: its Move (the slowest model's), and the weapons of `wargear`
    (names from the datasheet's wargear; default: default_wargear)."""
    moves = [inches((m.get("stats") or {}).get("M")) for m in unit["models"]]
    moves = [m for m in moves if m is not None]
    weapons = []
    for name in wargear if wargear is not None else default_wargear(unit):
        for w in (unit.get("wargear", {}).get(name) or {}).get("weapons", []):
            if not any(o["name"] == w["name"] for o in weapons):
                weapons.append({"name": w["name"], "type": w["type"], "range": inches(w["range"]),
                                "assault": any(k.lower() == "assault" for k in w.get("keywords") or [])})
    return {"unit": unit["name"], "move": min(moves) if moves else None, "weapons": weapons}


def bands(p):
    """How far the unit reaches, from its bases' edges: [{"band", "avg", "max"}], for move,
    advance, charge (move then 2D6, into engagement range), each ranged weapon after a move,
    and after an advance for Assault weapons. Empty for a unit that can't move."""
    m = p["move"]
    if m is None:
        return []
    out = [{"band": "move", "avg": m, "max": m},
           {"band": "advance", "avg": m + D6[0], "max": m + D6[1]},
           {"band": "charge", "avg": m + TWO_D6[0] + ENGAGEMENT, "max": m + TWO_D6[1] + ENGAGEMENT}]
    for w in p["weapons"]:
        if w["range"] is None:
            continue
        out.append({"band": f"shoot: {w['name']}", "avg": m + w["range"], "max": m + w["range"]})
        if w["assault"]:
            out.append({"band": f"advance and shoot: {w['name']}", "avg": m + D6[0] + w["range"],
                        "max": m + D6[1] + w["range"]})
    return out


def charge_chance(needed):
    """The chance a 2D6 charge roll is at least `needed`."""
    if needed <= 2:
        return 1.0
    return sum(1 for a in range(1, 7) for b in range(1, 7) if a + b >= needed) / 36


def threats(p, gap, visible=None):
    """What the unit can do to a target `gap` inches away, base edge to base edge, this turn.
    charge: the 2D6 roll it needs after moving its full M (0 when it's already within
    engagement range) and the chance of making it, or None when it's beyond 12". weapons: for
    each ranged weapon, whether the target is in range now, after moving, and after advancing
    (Assault weapons). visible: from the LOS engine, whether the unit can see the target now
    (None: not checked); a target it can't see isn't in range "now"."""
    m = p["move"] or 0
    needed = max(0, math.ceil(gap - m - ENGAGEMENT - 1e-9)) if p["move"] is not None else None
    charge = None
    if needed is not None and needed <= TWO_D6[1]:
        charge = {"needed": needed, "chance": round(charge_chance(needed), 3)}
    weapons = []
    for w in p["weapons"]:
        if w["range"] is None:
            continue
        now = gap <= w["range"]
        weapons.append({"weapon": w["name"], "range": w["range"], "now": now and visible is not False,
                        "after_move": p["move"] is not None and gap <= m + w["range"],
                        "after_advance": w["assault"] and p["move"] is not None and gap <= m + D6[1] + w["range"]})
    return {"gap": round(gap, 2), "engaged": gap <= ENGAGEMENT, "charge": charge, "weapons": weapons,
            "visible": visible}


def bubbles(models, reach):
    """Circles {"x", "z", "r"} whose union is everything within `reach` of the unit's bases
    (models as los.model makes them), for drawing."""
    return [{"x": m["x"], "z": m["z"], "r": round(m["r"] + reach, 3)} for m in models]


def within(models, reach, x, z):
    """Whether a point is within `reach` of any of the unit's bases."""
    return any(math.dist((m["x"], m["z"]), (x, z)) <= m["r"] + reach for m in models)
