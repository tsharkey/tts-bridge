"""
Parsing every saved list in lists/. Needs only mappings.json, not TTS or the
Force Org catalogue, so it runs anywhere.

If you change the parser on purpose and a count here changes, check the new
number against the list text and update the table in the same PR.
"""

from collections import Counter
from pathlib import Path

import pytest

import army

LISTS = Path(__file__).resolve().parent.parent / "lists"

# list file -> (faction, sub-faction, units, models)
EXPECTED = {
    "custodes.txt": ("Adeptus Custodes", None, 12, 29),
    "eldar_1.txt": ("Aeldari", None, 15, 41),
    "grey_knights.txt": ("Grey Knights", None, 11, 47),
    "necrons.txt": ("Necrons", None, 11, 38),
    "raven_guard_jumbo_maverick.txt": ("Space Marines", "Raven Guard", 9, 35),
    "ravenguard_lgt.txt": ("Space Marines", "Raven Guard", 14, 48),
    "tau_retaliation_cadre.txt": ("T'au Empire", None, 18, 57),
    "tyranids_1.txt": ("Tyranids", None, 18, 66),
    "tyranids_lgt.txt": ("Tyranids", None, 17, 42),
}


@pytest.fixture(scope="module")
def mappings():
    return army.load_mappings()


def parse(name, mappings):
    return army.parse_list((LISTS / name).read_text(), mappings)


def test_every_list_has_an_expectation():
    assert sorted(p.name for p in LISTS.glob("*.txt")) == sorted(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_list_parses(name, mappings):
    faction, sub, units, models = EXPECTED[name]
    parsed = parse(name, mappings)
    assert parsed["faction"] == faction
    assert parsed["sub"] == sub
    assert len(parsed["units"]) == units
    assert sum(len(u["models"]) for u in parsed["units"]) == models
    for u in parsed["units"]:
        assert u["models"], f"{u['name']} has no models"


def test_tau_compositions(mappings):
    """Look-alike units in issue #4 differ by composition; keep that intact."""
    units = {}
    for u in parse("tau_retaliation_cadre.txt", mappings)["units"]:
        units.setdefault(u["name"], Counter(m["name"] for m in u["models"]))
    assert units["Pathfinder Team"] == {"Pathfinder Shas'ui": 1, "Pathfinders": 9}
    assert units["Stealth Battlesuits"] == {"Stealth Shas'vre": 1, "Stealth Shas'ui": 4}
    # pinned in mappings.json "units": the flat export can't express it
    assert units["The Twin Lance"] == {"Ri'Lantar": 1, "Ri'Locai": 1}
    assert units["Crisis Fireknife Battlesuits"] == {"Crisis Fireknife Shas'vre": 1, "Crisis Fireknife Shas'ui": 2}


def test_drones_are_not_models(mappings):
    for u in parse("tau_retaliation_cadre.txt", mappings)["units"]:
        assert not any("drone" in m["name"].lower() for m in u["models"]), u["name"]
