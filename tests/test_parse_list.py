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
