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
