"""
Parsing army lists. Needs no TTS, Force Org catalogue or mappings.json, so it
runs anywhere.

The lists in tests/fixtures/ are trimmed exports, one per format the parser
understands. Saved lists in lists/ are the user's own and aren't committed.
"""

from collections import Counter
from pathlib import Path

import pytest

import army

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def mappings():
    # Not the user's mappings.json (git-ignored, and it varies): only the
    # composition override these fixtures need.
    return {"units": {"T'au Empire|The Twin Lance": [["Ri'Lantar", 1], ["Ri'Locai", 1]]}, "models": {}}


def compositions(name, mappings):
    """{unit name: [Counter of model names, one per copy of the unit]}"""
    parsed = army.parse_list((FIXTURES / name).read_text(), mappings)
    out = {}
    for u in parsed["units"]:
        out.setdefault(u["name"], []).append(Counter(m["name"] for m in u["models"]))
    return parsed, out


def test_gw_app_export(mappings):
    """The GW app's export: • for models, ◦ for their wargear."""
    parsed, units = compositions("gw_app_raven_guard.txt", mappings)
    assert (parsed["faction"], parsed["sub"]) == ("Space Marines", "Raven Guard")
    assert parsed["title"] == "Test list"
    assert units == {
        "Librarian in Terminator Armour": [{"Librarian in Terminator Armour": 1}],
        "Terminator Squad": [{"Terminator Sergeant": 1, "Terminator": 9}],
        "Aethon Shaan": [{"Aethon Shaan": 1}],
        "Techmarine": [{"Techmarine": 1}],
        "Intercessor Squad": [{"Intercessor Sergeant": 1, "Intercessor": 4}],
    }


def test_plus_format(mappings):
    """The "+++" header format with flat • lines. Look-alike units in issue #4
    differ by composition, so keep these exact."""
    parsed, units = compositions("plus_format_tau.txt", mappings)
    assert (parsed["faction"], parsed["sub"]) == ("T'au Empire", None)
    assert parsed["title"] == "T'au Empire Retaliation Cadre (Bonded Heroes) 2000"
    assert units == {
        "Commander Farsight": [{"Commander Farsight": 1}],
        "Crisis Fireknife Battlesuits": [{"Crisis Fireknife Shas'vre": 1, "Crisis Fireknife Shas'ui": 2}],
        # from the "units" override above: the flat export can't express it
        "The Twin Lance": [{"Ri'Lantar": 1, "Ri'Locai": 1}],
        "Pathfinder Team": [{"Pathfinder Shas'ui": 1, "Pathfinders": 9}],
        "Stealth Battlesuits": [{"Stealth Shas'vre": 1, "Stealth Shas'ui": 4}],
    }


def test_drones_are_not_models(mappings):
    _, units = compositions("plus_format_tau.txt", mappings)
    for name, copies in units.items():
        for c in copies:
            assert not any("drone" in m.lower() for m in c), name


def test_wargear_per_model(mappings):
    parsed = army.parse_list((FIXTURES / "gw_app_raven_guard.txt").read_text(), mappings)
    squad = next(u for u in parsed["units"] if u["name"] == "Intercessor Squad")
    sergeant = squad["models"][0]
    assert sergeant["name"] == "Intercessor Sergeant"
    assert "Power fist" in sergeant["wargear"]
    assert all("Power fist" not in m["wargear"] for m in squad["models"][1:])


# The same T'au list exported five ways (issue #33).
FULL = ["tau_tournament.txt", "tau_gw.txt", "tau_nr.txt"]
PARTIAL = ["tau_simple.txt", "tau_short.txt"]


def by_unit(units):
    """Unit compositions regardless of unit order, with New Recruit's loadout
    names ("Stealth Shas'ui w/ burst cannon") folded into the model's name."""
    out = {}
    for name, copies in units.items():
        folded = []
        for c in copies:
            f = Counter()
            for model, n in c.items():
                f[model.split(" w/ ")[0]] += n
            folded.append(sorted(f.items()))
        out[name] = sorted(folded)
    return out


@pytest.mark.parametrize("name", FULL + PARTIAL)
def test_every_format_reads_the_army(name):
    parsed = army.parse_list((FIXTURES / name).read_text(), {})
    assert (parsed["faction"], parsed["sub"]) == ("T'au Empire", None)
    assert parsed["points"] == 2005
    assert len(parsed["units"]) == 17
    assert sum(u["points"] for u in parsed["units"]) == 2005
    if name != "tau_simple.txt":  # the simple export has no detachment
        assert parsed["detachment"].startswith("Retaliation Cadre")


def test_format_detection():
    formats = [army.parse_list((FIXTURES / n).read_text(), {})["format"] for n in FULL + PARTIAL]
    assert formats == ["tournament", "gw", "nr", "simple", "short"]
    assert army.parse_list((FIXTURES / "gw_app_raven_guard.txt").read_text(), {})["format"] == "gw"


def test_gw_title_with_a_faction_in_it():
    """A GW list titled "2k - T'au Empire - Ret Cadre" is still the GW format."""
    text = (FIXTURES / "tau_gw.txt").read_text().replace("2k Ret Cadre v4", "2k - T'au Empire - Ret Cadre", 1)
    parsed = army.parse_list(text, {})
    assert (parsed["format"], parsed["title"]) == ("gw", "2k - T'au Empire - Ret Cadre")
    assert (parsed["battle_size"], parsed["disposition"], parsed["points"]) == ("Strike Force", "Purge the Foe", 2005)
    assert all(u["complete"] for u in parsed["units"])


def test_army_fields():
    fields = ("title", "detachment", "disposition", "battle_size")
    got = {n: tuple(army.parse_list((FIXTURES / n).read_text(), {})[f] for f in fields) for n in FULL + PARTIAL}
    assert got == {
        "tau_tournament.txt": ("T'au Empire Retaliation Cadre (Bonded Heroes) 2005",
                               "Retaliation Cadre (Bonded Heroes)", "Purge the Foe", None),
        "tau_gw.txt": ("2k Ret Cadre v4", "Retaliation Cadre", "Purge the Foe", "Strike Force"),
        "tau_nr.txt": ("2k Ret Cadre v4", "Retaliation Cadre", "Purge the Foe", "Strike Force"),
        "tau_simple.txt": ("2k Ret Cadre v4", None, None, None),
        "tau_short.txt": ("T'au Empire Retaliation Cadre", "Retaliation Cadre", None, None),
    }
    raven = army.parse_list((FIXTURES / "gw_app_raven_guard.txt").read_text(), {})
    assert (raven["detachment"], raven["disposition"], raven["battle_size"], raven["points"]) == (
        "Librarius Conclave and Shadowmark Talon", "Reconnaissance", "Strike Force", 1000)


def test_full_formats_agree():
    """Tournament, GW and New Recruit's full export: the same units, model
    counts and model names, with no mappings.json."""
    tournament, gw, nr = (by_unit(compositions(n, {})[1]) for n in FULL)
    assert tournament == gw
    # New Recruit names the Broadside's one model; the others use the unit's name.
    assert nr.pop("Broadside Battlesuits") == [[("Broadside Shas'vre", 1)]] * 2
    tournament.pop("Broadside Battlesuits")
    assert nr == tournament
    assert tournament["The Twin Lance"] == [[("Ri'Lantar", 1), ("Ri'Locai", 1)]]
    assert sum(sum(n for _, n in c) for copies in tournament.values() for c in copies) == 48  # 50 less the Broadsides


def test_new_recruit_gear_is_per_model():
    parsed = army.parse_list((FIXTURES / "tau_nr.txt").read_text(), {})
    breachers = next(u for u in parsed["units"] if u["name"] == "Breacher Team")
    warriors = [m for m in breachers["models"] if m["name"] == "Breacher Fire Warriors"]
    assert len(warriors) == 9
    assert all(m["wargear"] == ["Close combat weapon", "Pulse blaster", "Pulse pistol"] for m in warriors)
    riptide = next(u for u in parsed["units"] if u["name"] == "Riptide Battlesuit")
    # "[25 pts]" is dropped from the name; drones aren't models or wargear, but are gear
    [model] = riptide["models"]
    assert (model["name"], model["wargear"]) == ("Riptide Battlesuit", ["Ion accelerator", "Riptide fists", "Twin plasma rifle"])
    assert {g["name"]: g["count"] for g in model["gear"]} == {
        "Ion accelerator": 1, "Missile Drone": 2, "Riptide fists": 1, "Twin plasma rifle": 1}


def test_full_formats_are_complete():
    for n in FULL + ["gw_app_raven_guard.txt", "plus_format_tau.txt"]:
        assert all(u["complete"] for u in army.parse_list((FIXTURES / n).read_text(), {})["units"]), n


def test_short_format():
    """One line per unit: the count is the model count, and the models are
    placeholders named after the unit until datasheets fill them in."""
    parsed, units = compositions("tau_short.txt", {})
    assert not any(u["complete"] for u in parsed["units"])
    assert units["Breacher Team"] == [{"Breacher Team": 10}] * 2
    assert units["The Twin Lance"] == [{"The Twin Lance": 2}]
    assert units["Commander Farsight"] == [{"Commander Farsight": 1}]
    assert sum(len(u["models"]) for u in parsed["units"]) == 50


def test_simple_format():
    """Units with only some of their models: parse what's there, flag the rest."""
    parsed, units = compositions("tau_simple.txt", {})
    assert not any(u["complete"] for u in parsed["units"])
    assert units["Breacher Team"] == [{"Breacher Fire Warrior Shas'ui": 1}] * 2
    assert units["Stealth Battlesuits"][0] == {"Stealth Shas'vre": 1, "Stealth Shas'ui w/ burst cannon": 2,
                                              "Stealth Shas'ui w/ fusion blaster": 2}
    assert units["Crisis Sunforge Battlesuits"] == [{"Crisis Sunforge Shas'vre": 1, "Crisis Sunforge Shas'ui": 2}]
    assert units["Ghostkeel Battlesuit"] == [{"Ghostkeel Battlesuit": 1}]


# What the list says beyond models (issue #10).

def parse_file(name):
    return army.parse_list((FIXTURES / name).read_text(), {})


def test_attached_units_and_roles():
    parsed = parse_file("gw_app_raven_guard.txt")
    librarian, terminators, shaan = parsed["units"][:3]
    assert (librarian["role"], librarian["attached_to"]) == ("leader", 1)
    assert (terminators["role"], terminators["attached_to"]) == ("bodyguard", None)
    assert (shaan["role"], shaan["attached_to"]) == (None, None)
    plus = parse_file("plus_format_tau.txt")
    farsight, fireknives, lance = plus["units"][:3]
    assert (farsight["role"], farsight["attached_to"], fireknives["role"]) == ("leader", 1, "bodyguard")
    # under the "Attached Unit 1" heading, but not attached: it says nothing about it
    assert (lance["name"], lance["role"], lance["attached_to"]) == ("The Twin Lance", None, None)


def test_enhancements_and_warlord():
    raven = parse_file("gw_app_raven_guard.txt")
    assert raven["units"][0]["enhancements"] == ["Temporal Corridor"]
    assert [u["name"] for u in raven["units"] if u["warlord"]] == ["Aethon Shaan"]
    assert [u["name"] for u in parse_file("plus_format_tau.txt")["units"] if u["warlord"]] == ["Commander Farsight"]
    for name in ("tau_tournament.txt", "tau_gw.txt"):
        # tournament: the header's "(on Char5: ...)" and the line under the unit, once
        with_enhancement = [(i, u["enhancements"]) for i, u in enumerate(parse_file(name)["units"]) if u["enhancements"]]
        assert with_enhancement == [(2, ["Starflare Ignition System"])], name
    # New Recruit's full export lists it with the wargear; datasheets tell them apart
    nr = parse_file("tau_nr.txt")
    assert nr["units"][4]["priced"] == ["Starflare Ignition System"]
    assert nr["units"][4]["enhancements"] == []


def test_weapon_counts():
    parsed = parse_file("tau_gw.txt")
    coldstar = parsed["units"][1]["models"][0]
    assert {g["name"]: g["count"] for g in coldstar["gear"]}["Missile pod"] == 2
    assert coldstar["wargear"].count("Missile pod") == 1  # wargear (pin keys) as before
    starscythe = next(u for u in parsed["units"] if u["name"] == "Crisis Starscythe Battlesuits")
    # "◦ 4x Burst cannon" across 2 Shas'ui is 2 each
    assert [{g["name"]: g["count"] for g in m["gear"]}["Burst cannon"] for m in starscythe["models"]] == [2, 2, 2]


def test_drones_and_uncounted_gear():
    parsed = parse_file("plus_format_tau.txt")
    fireknives = parsed["units"][1]["models"]
    counts = [{g["name"]: g["count"] for g in m["gear"]} for m in fireknives]
    assert (counts[0]["Marker Drone"], counts[0]["Shield Drone"]) == (1, 1)  # "• Marker Drone, Shield Drone"
    assert [c["Gun Drone"] for c in counts[1:]] == [1, 1]                    # "• 2x Gun Drone, 2x Shield Drone"
    assert not any("Drone" in w for m in fireknives for w in m["wargear"])
    stealth = next(u for u in parsed["units"] if u["name"] == "Stealth Battlesuits")
    assert sum(g["count"] for m in stealth["models"] for g in m["gear"] if g["name"] == "Homing beacon") == 1
