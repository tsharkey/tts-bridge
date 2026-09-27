"""
Importing BSData into our datasheet format, and the local data cache. Runs
offline on tests/fixtures/bsdata/: a made-up army written in BSData's schema
(BSData itself has no licence, so none of it is committed). It has one of each
shape the importer handles: a single-model character, a squad with a sized
group, choices and borrowed stat lines, a choice between whole compositions,
and things the import leaves out (hidden entries, Crusade, Warlord).
"""

import json
from pathlib import Path

import pytest

import bsdata
import data

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bsdata"


@pytest.fixture(scope="module")
def imported():
    return bsdata.import_folder(FIXTURE)


def unit(imported, name):
    return next(u for u in imported[0]["Xenos - Test Empire"]["units"] if u["name"] == name)


def test_catalogues(imported):
    catalogues, skipped = imported
    assert set(catalogues) == {"Xenos - Test Empire", "Test - Armoury"}
    test = catalogues["Xenos - Test Empire"]
    assert (test["faction"], test["library"], test["imports"]) == ("Test Empire", False, ["Test - Armoury"])
    assert catalogues["Test - Armoury"]["faction"] is None
    # root entries that aren't units (a detachment) and hidden units are left out
    assert [u["name"] for u in test["units"]] == ["Test Commander", "Test Squad", "Composition Squad"]
    assert skipped == {"hidden unit": 1, "hidden option": 1, "crusade option": 2, "warlord option": 1}


def test_single_model_character(imported):
    u = unit(imported, "Test Commander")
    assert (u["points"], u["size"], u["keywords"], u["factions"]) == (80, [1, 1], ["Character", "Epic Hero"],
                                                                       ["Test Empire"])
    assert u["rules"] == ["Deep Strike"]
    assert [a["name"] for a in u["abilities"]] == ["Inspiring", "Shared Ability"]  # inline, then linked
    [m] = u["models"]
    assert m["stats"] == {"M": '10"', "T": "5", "Sv": "2+", "W": "8", "Ld": "6+", "OC": "2", "InSv": "4+"}
    assert m["equipped"] == [{"name": "Twin blade", "count": 1}]
    assert u["wargear"]["Twin blade"]["weapons"] == [
        {"name": "Twin blade - strike", "type": "melee", "range": "Melee", "A": "4", "skill": "2+",
         "S": "10", "AP": "-2", "D": "3", "keywords": []},
        {"name": "Twin blade - sweep", "type": "melee", "range": "Melee", "A": "8", "skill": "2+",
         "S": "6", "AP": "-1", "D": "1", "keywords": []},
    ]


def test_squad(imported):
    u = unit(imported, "Test Squad")
    assert u["size"] == [5, 10]
    assert [(m["name"], m["min"], m["max"]) for m in u["models"]] == [
        ("Squad Leader", 1, 1), ("Trooper", 4, 9), ("Trooper w/ twin blade", 0, 2)]
    leader, trooper, special = u["models"]
    assert leader["stats"]["W"] == "1" and leader["stats"]["InSv"] is None
    assert leader["equipped"] == []
    assert leader["options"] == [
        {"name": "Weapon", "min": 1, "max": 1, "choices": ["Pulse rifle", "Close combat weapon"], "default": "Pulse rifle"},
        {"name": "Drones (0-1)", "min": 0, "max": 1, "choices": ["Marker Drone"]},
    ]
    assert trooper["stats"]["OC"] == "2"  # from a linked shared profile
    assert special["stats"] == trooper["stats"]  # no line of its own: "Trooper w/ ..." borrows "Trooper"
    assert trooper["equipped"] == [{"name": "Pulse rifle", "count": 1}, {"name": "Close combat weapon", "count": 1}]
    rifle = u["wargear"]["Pulse rifle"]["weapons"][0]
    assert (rifle["type"], rifle["range"], rifle["skill"], rifle["keywords"]) == ("ranged", '30"', "4+",
                                                                                 ["Rapid Fire 1", "Heavy"])
    assert u["wargear"]["Marker Drone"] == {"id": "w-drone", "weapons": [], "abilities": [
        {"name": "Marker Drone", "text": "The bearer's unit can act as an Observer."}]}
    assert "Boarding Actions kit" not in u["wargear"]  # hidden


def test_compositions(imported):
    """A choice between 1 Boss + 4 Grunts and 2 Bosses + 8 Grunts."""
    u = unit(imported, "Composition Squad")
    assert u["size"] == [5, 10]
    assert [(m["name"], m["min"], m["max"]) for m in u["models"]] == [("Boss", 1, 2), ("Grunt", 4, 8)]


def test_no_catalogues(tmp_path):
    with pytest.raises(ValueError):
        bsdata.import_folder(tmp_path)


# --------------------------------------------------------------------------
# The cache (data.py), fetching from a local folder so nothing touches the network.

@pytest.fixture
def cache(tmp_path):
    info = data.fetch("bsdata", str(FIXTURE), cache=tmp_path, log=lambda *_: None)
    assert (info["kind"], info["path"]) == ("folder", str(FIXTURE))
    data.import_bsdata(cache=tmp_path, log=lambda *_: None)
    return tmp_path


def test_fetch_and_import(cache):
    info = data.source_info("bsdata", cache)
    assert info["source"] == "bsdata" and info["fetched"]
    index = data.datasheet_index(cache)
    assert index["source"] == info
    assert index["catalogues"]["Xenos - Test Empire"] == {
        "file": "Xenos - Test Empire.json", "faction": "Test Empire", "library": False,
        "imports": ["Test - Armoury"], "units": 3}
    assert index["skipped"]["hidden unit"] == 1
    saved = json.loads((cache / "datasheets" / "Xenos - Test Empire.json").read_text())
    assert saved["units"][0]["name"] == "Test Commander"


def test_datasheets_by_faction(cache):
    assert [u["name"] for u in data.datasheets("Test Empire", cache)] == [
        "Test Commander", "Test Squad", "Composition Squad"]
    assert data.datasheets("Raptors", cache) == []


def test_refetch_remembers_the_source(cache):
    info = data.fetch("bsdata", cache=cache, log=lambda *_: None)
    assert info["path"] == str(FIXTURE)


def test_status(cache, tmp_path_factory):
    s = data.status(cache)["bsdata"]
    assert (s["kind"], s["catalogues"], s["datasheets"]) == ("folder", 2, 3)
    assert data.status(tmp_path_factory.mktemp("empty")) == {"bsdata": None}


def test_errors(tmp_path):
    with pytest.raises(data.DataError, match="Unknown source"):
        data.fetch("nope", cache=tmp_path)
    with pytest.raises(data.DataError, match="isn't a folder"):
        data.fetch("bsdata", "not a link", cache=tmp_path)
    with pytest.raises(data.DataError, match="fetch bsdata"):
        data.import_bsdata(cache=tmp_path)
    assert data.datasheets("Test Empire", tmp_path) == []


@pytest.mark.parametrize("url,repo", [
    ("https://github.com/BSData/wh40k-11e", ("BSData", "wh40k-11e")),
    ("https://github.com/someone/wh40k-11e.git", ("someone", "wh40k-11e")),
    ("github.com/someone/fork/", ("someone", "fork")),
    ("https://gitlab.com/someone/fork", None),
    ("git@github.com:someone/fork.git", None),  # a git remote, fetched with git
])
def test_github_links(url, repo):
    assert data.github_repo(url) == repo
