"""
Matching parsed lists to datasheets (datasheets.py). Runs on a tiny cache in our
own format, tests/fixtures/datacache/datasheets/: names from the fixture lists, made-up
ids and no stats, so nothing from BSData is committed.
"""

import copy
from collections import Counter
from pathlib import Path

import pytest

import army
import datasheets

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CACHE = FIXTURES / "datacache"  # its datasheets/ folder, as data.py lays a cache out


def parse(name, mappings=None):
    return army.parse_list((FIXTURES / name).read_text(), mappings or {})


def unit(parsed, name, n=0):
    return [u for u in parsed["units"] if u["name"] == name][n]


def test_units_match_by_name():
    parsed = parse("tau_tournament.txt")
    mappings = {}
    datasheets.attach(parsed, mappings, CACHE)
    farsight = unit(parsed, "Commander Farsight")
    assert farsight["datasheet"] == {"id": "t-farsight", "name": "Commander Farsight",
                                     "catalogue": "Xenos - T'au Empire", "how": "exact"}
    # "[Legends]" and curly apostrophes don't get in the way
    assert unit(parsed, "Riptide Battlesuit")["datasheet"]["id"] == "t-riptide"
    # a second copy of a unit reports how it was matched, not "pinned"
    assert unit(parsed, "Riptide Battlesuit", 1)["datasheet"]["how"] == "exact"
    # units the sample doesn't have are flagged
    assert unit(parsed, "Breacher Team")["datasheet"] is None
    assert "Breacher Team" in datasheets.unmatched(parsed)["units"]


def test_models_and_wargear():
    parsed = parse("tau_tournament.txt")
    datasheets.attach(parsed, {}, CACHE)
    stealth = unit(parsed, "Stealth Battlesuits")
    # BSData splits models by loadout; the wargear picks the right one
    assert Counter((m["name"], m["sheet_model"]) for m in stealth["models"]) == {
        ("Stealth Shas'vre", "Stealth Shas’vre"): 1,
        ("Stealth Shas'ui", "Stealth Shas’ui w/ burst cannon"): 2,
        ("Stealth Shas'ui", "Stealth Shas’ui w/ fusion blaster"): 2,
    }
    # a model named for its unit is the unit's one required model
    [broadside] = unit(parsed, "Broadside Battlesuits")["models"]
    assert broadside["sheet_model"] == "Broadside Shas’vre"
    farsight = unit(parsed, "Commander Farsight")["models"][0]
    assert dict(zip(farsight["wargear"], farsight["sheet_wargear"])) == {
        "Dawn Blade": "Dawn Blade", "High-intensity plasma rifle": "High-intensity plasma rifle"}


def test_wargear_inside_other_wargear():
    sheets = datasheets.Datasheets("Space Marines", CACHE)
    terminators = sheets.get("sm-terminators")
    assert datasheets.match_wargear("Cyclone missile launcher", terminators) == "Cyclone Missile Launcher & Storm Bolter"
    assert datasheets.match_wargear("power FIST", terminators) == "Power fist"
    assert datasheets.match_wargear("Thunder hammer", terminators) is None
    broadside = datasheets.Datasheets("T'au Empire", CACHE).get("t-broadside")
    assert datasheets.match_wargear("Missile pod", broadside) == "Missile Drone"  # a drone's weapon


def test_chapter_scope():
    """A Raven Guard list searches Raven Guard, then Space Marines; a Black
    Templars list gets its own Terminator Squad."""
    parsed = parse("gw_app_raven_guard.txt")
    mappings = {}
    datasheets.attach(parsed, mappings, CACHE)
    squad = unit(parsed, "Terminator Squad")
    assert squad["datasheet"]["id"] == "sm-terminators"
    assert Counter(m["sheet_model"] for m in squad["models"]) == {
        "Terminator Sergeant": 1, "Terminator w/ Heavy Weapon": 2, "Terminator w/ Power Fist": 7}
    assert mappings["datasheets"]["Raven Guard|Terminator Squad"] == {
        "id": "sm-terminators", "name": "Terminator Squad", "catalogue": "Imperium - Adeptus Astartes - Space Marines"}

    templars = copy.deepcopy(parsed)
    templars["sub"] = "Black Templars"
    datasheets.attach(templars, {}, CACHE)
    assert unit(templars, "Terminator Squad")["datasheet"]["id"] == "bt-terminators"


def test_pins():
    parsed = parse("gw_app_raven_guard.txt")
    mappings = {"datasheets": {"Raven Guard|Terminator Squad": {"id": "bt-terminators", "name": "Terminator Squad"}}}
    datasheets.attach(parsed, mappings, CACHE)
    assert unit(parsed, "Terminator Squad")["datasheet"]["how"] == "pinned"
    assert unit(parsed, "Terminator Squad")["datasheet"]["id"] == "bt-terminators"

    # a pin whose id is gone upstream is matched again, and re-pinned
    mappings["datasheets"]["Raven Guard|Terminator Squad"]["id"] = "gone"
    datasheets.attach(parsed, mappings, CACHE)
    assert unit(parsed, "Terminator Squad")["datasheet"]["how"] == "exact, pin had gone"
    assert mappings["datasheets"]["Raven Guard|Terminator Squad"]["id"] == "sm-terminators"

    # repick ignores pins
    mappings["datasheets"]["Raven Guard|Terminator Squad"]["id"] = "bt-terminators"
    datasheets.attach(parsed, mappings, CACHE, repick=True)
    assert unit(parsed, "Terminator Squad")["datasheet"]["id"] == "sm-terminators"


def fake_army(faction, *units):
    return {"faction": faction, "sub": None,
            "units": [{"name": n, "allied": allied, "models": []} for n, allied in units]}


def test_fuzzy_and_allies():
    parsed = fake_army("T'au Empire", ("Stealth Battlesuit", False), ("Inquisitor", True))
    datasheets.attach(parsed, {}, CACHE)
    stealth, allied = parsed["units"]
    assert (stealth["datasheet"]["id"], stealth["datasheet"]["how"]) == ("t-stealth", "fuzzy 100%")
    # allies can come from any catalogue; the army's own units can't
    assert allied["datasheet"]["id"] == "ag-inquisitor"
    parsed = fake_army("T'au Empire", ("Inquisitor", False))
    datasheets.attach(parsed, {}, CACHE)
    assert parsed["units"][0]["datasheet"] is None


def test_imports_are_searched():
    """Space Marines import Agents of the Imperium, so an Inquisitor is theirs to take."""
    parsed = fake_army("Space Marines", ("Inquisitor", False))
    datasheets.attach(parsed, {}, CACHE)
    assert parsed["units"][0]["datasheet"]["catalogue"] == "Imperium - Agents of the Imperium"


def test_nothing_cached(tmp_path):
    parsed = parse("tau_tournament.txt")
    assert datasheets.attach(parsed, {}, tmp_path) is None
    assert all("datasheet" not in u for u in parsed["units"])


@pytest.mark.parametrize("name", ["tau_tournament.txt", "tau_gw.txt", "tau_nr.txt"])
def test_plan_prints_datasheets(name, capsys):
    parsed = parse(name)
    datasheets.attach(parsed, {}, CACHE)
    army.print_plan(parsed, None, None)
    out = capsys.readouterr().out
    assert "Commander Farsight" in out and "datasheet: Commander Farsight (exact)" in out
    assert "datasheet: NONE" in out and "no datasheet: " in out and "Breacher Team" in out


# Compositions and names from datasheets (issue #10).

def read(name, mappings=None):
    return datasheets.parse((FIXTURES / name).read_text(), mappings if mappings is not None else {}, CACHE)


def test_flat_list_models_from_datasheet():
    """The old "+++" export lists The Twin Lance's two models as if they were
    wargear; the datasheet's model names tell them apart, with no mappings.json."""
    lance = unit(read("plus_format_tau.txt"), "The Twin Lance")
    assert [(m["name"], m["sheet_model"]) for m in lance["models"]] == [
        ("Ri'Lantar", "Ri’Lantar"), ("Ri'Locai", "Ri’Locai")]
    assert "Fusion eliminator" in lance["models"][0]["wargear"]
    assert lance.get("composition") is None  # the list said it; nothing filled in


def test_short_export_compositions():
    parsed = read("tau_short.txt")
    crisis = unit(parsed, "Crisis Starscythe Battlesuits")
    assert crisis["composition"] == "datasheet" and not crisis["complete"]
    assert Counter(m["name"] for m in crisis["models"]) == {"Crisis Starscythe Shas'vre": 1, "Crisis Starscythe Shas'ui": 2}
    assert crisis["models"][0]["wargear"] == ["Battlesuit fists", "Burst cannon"]
    lance = unit(parsed, "The Twin Lance")  # "2x The Twin Lance"
    assert Counter(m["sheet_model"] for m in lance["models"]) == {"Ri’Lantar": 1, "Ri’Locai": 1}
    stealth = unit(parsed, "Stealth Battlesuits")  # 5x: the Shas'vre, then the rest where there's room
    assert Counter(m["sheet_model"] for m in stealth["models"]) == {
        "Stealth Shas’vre": 1, "Stealth Shas’ui w/ burst cannon": 4}
    # a unit the sample has no datasheet for keeps its placeholders
    assert Counter(m["name"] for m in unit(parsed, "Breacher Team")["models"]) == {"Breacher Team": 10}


def test_simple_export_keeps_what_it_names():
    stealth = unit(read("tau_simple.txt"), "Stealth Battlesuits")
    assert Counter(m["sheet_model"] for m in stealth["models"]) == {
        "Stealth Shas’vre": 1, "Stealth Shas’ui w/ burst cannon": 2, "Stealth Shas’ui w/ fusion blaster": 2}


def test_composition_counts():
    sheet = datasheets.Datasheets("T'au Empire", CACHE).get("t-starscythe")
    assert [(m["name"], n) for m, n in datasheets.composition(sheet, 6)] == [
        ("Crisis Starscythe Shas’vre", 1), ("Crisis Starscythe Shas’ui", 5)]
    assert [n for _, n in datasheets.composition(sheet, 1)] == [1, 2]  # never below the datasheet's minimum


def test_costed_wargear_or_enhancement():
    coldstar = unit(read("tau_nr.txt"), "Commander in Coldstar Battlesuit", 1)
    assert coldstar["enhancements"] == ["Starflare Ignition System"]
    assert "Starflare Ignition System" not in coldstar["models"][0]["wargear"]
    assert "priced" not in coldstar


SOB_LIST = """Test (500 Points)

Adepta Sororitas
Army of Faith (3 Detachment Points)
Purge the Foe
Incursion (1,000 Points)

Dominion Squad (115 Points)
  • 1x Dominion Superior
     ◦ 1x Bolt pistol
     ◦ 1x Boltgun
  • 1x Dominion w/ Special Weapon
     ◦ 1x Bolt pistol
     ◦ 1x Meltagun
  • 3x Dominion
     ◦ 3x Boltgun
"""


def test_stand_in_look_alike():
    """Force Org has no Dominion figures. The datasheet with the same body and
    the most wargear in common is Battle Sisters (Retributors have the same
    body but other guns; Seraphim the same guns but jump packs)."""
    sheets = datasheets.Datasheets("Adepta Sororitas", CACHE)
    dominion = sheets.get("s-dominion")
    assert datasheets.stand_in(sheets, dominion, "Dominion Superior") == ("Battle Sisters Squad", "Sister Superior")
    assert datasheets.stand_in(sheets, dominion, "Dominion") == ("Battle Sisters Squad", "Battle Sister")
    assert datasheets.stand_in(sheets, dominion, "Dominion w/ Special Weapon") == (
        "Battle Sisters Squad", "Battle Sister w/ Special Weapon")
    assert datasheets.stand_in(sheets, dominion, "Nobody") is None


def test_dominion_gets_battle_sister_figures():
    # figures named as Force Org names them
    catalog = {"430877": [{"Name": "Custom_Model", "Nickname": n} for n in (
        "Sister Superior", "Battle Sister", "Battle Sister - Meltagun + Bolt Pistol", "Retributor")]}
    mappings = {}
    parsed = datasheets.parse(SOB_LIST, mappings, CACHE)
    rows = army.resolve(parsed, catalog, mappings, sheet_cache=CACHE)
    got = {m["name"]: (catalog["430877"][int(m["pick"].split(":")[1])]["Nickname"], how)
           for _, m, how in rows if how != "pinned"}
    assert got == {
        "Dominion Superior": ("Sister Superior", "auto cover=100% via Sister Superior"),
        "Dominion w/ Special Weapon": ("Battle Sister - Meltagun + Bolt Pistol", "auto cover=100% via Battle Sister"),
        "Dominion": ("Battle Sister", "auto cover=100% via Battle Sister"),
    }
    assert "aliases" not in mappings and "units" not in mappings


def test_mapped_placeholder_keeps_its_gear():
    """A full export's Broadside has one model named for its unit, which is the
    datasheet's required Shas'vre: the list's gear stays (not the default loadout)."""
    [broadside] = unit(read("tau_tournament.txt"), "Broadside Battlesuits")["models"]
    assert broadside["sheet_model"] == "Broadside Shas’vre"
    assert "Seeker missile" in broadside["wargear"]  # from the list; not in the datasheet's defaults
    assert unit(read("tau_tournament.txt"), "Broadside Battlesuits").get("composition") is None


def test_leaders_from_the_leader_ability():
    def sheet(name, text=None):
        return {"id": name, "name": name, "abilities": [{"name": "Leader", "text": text}] if text else []}
    captain = sheet("Captain", "This model can be attached to the following units:\n- **Intercessor Squad**\n- Hellblaster Squad.")
    chaplain = sheet("Chaplain", "This model can be attached to the following units: INTERCESSOR SQUAD, TERMINATOR SQUAD")
    assert datasheets.leads(captain) == ["intercessor squad", "hellblaster squad"]
    assert datasheets.leads(chaplain) == ["intercessor squad", "terminator squad"]
    assert datasheets.leads(sheet("Intercessor Squad")) == []
    sheets = {s["id"]: s for s in (captain, chaplain, sheet("Intercessor Squad"))}

    class Sheets:
        get = staticmethod(sheets.get)
    units = [{"name": n, "datasheet": {"id": i, "name": i}} for n, i in (
        ("Captain", "Captain"), ("Intercessor Squad", "Intercessor Squad"), ("Chaplain", "Chaplain"),
        ("Intercessor Squad", "Intercessor Squad"))]
    datasheets.link_leaders(units, Sheets)
    assert units[0]["can_lead"] == [1, 3] and units[2]["can_lead"] == [1, 3] and "can_lead" not in units[1]


def test_attach_leaders_over_the_list():
    units = [{"name": "Captain", "role": "leader", "attached_to": 1}, {"name": "Squad", "role": "bodyguard"},
             {"name": "Squad", "role": None}, {"name": "Chaplain", "role": None, "attached_to": None}]
    assert army.unit_keys(units) == ["Captain#1", "Squad#1", "Squad#2", "Chaplain#1"]
    army.attach_leaders(units, {"Captain#1": "Squad#2", "Chaplain#1": "Squad#2", "Gone#1": "Squad#1"})
    assert [u["role"] for u in units] == ["leader", None, "bodyguard", "leader"]
    assert units[0]["attached_to"] == units[3]["attached_to"] == 2
    army.attach_leaders(units, {"Captain#1": None})
    assert (units[0]["role"], units[0]["attached_to"]) == (None, None) and units[2]["role"] == "bodyguard"


def test_model_variant_by_everything_it_carries():
    """Datasheet models that differ only by loadout ("Rider (Salvo Launcher)",
    "Rider (Hurricane Bolter)") are told apart by the model's gear as well as its
    wargear: a line that lost its bullet is gear only."""
    def variant(name, gear):
        return {"name": name, "min": 0, "max": 3, "stats": None, "options": [],
                "equipped": [{"name": g, "count": 1} for g in gear]}
    sheet = {"models": [variant("Rider (Salvo Launcher)", ["Lance", "Salvo launcher"]),
                        variant("Rider (Hurricane Bolter)", ["Lance", "Hurricane bolter"])],
             "wargear": {n: {"weapons": [], "abilities": []} for n in ("Lance", "Salvo launcher", "Hurricane bolter")}}
    rider = {"name": "Rider", "wargear": ["Lance"],
             "gear": [{"name": "Hurricane bolter", "count": 1}, {"name": "Lance", "count": 1}]}
    unit = {"name": "Riders", "models": [rider]}
    assert datasheets.match_model(rider, unit, sheet)["name"] == "Rider (Hurricane Bolter)"
