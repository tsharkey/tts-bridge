"""
Reading Force Org and LCT from TTS's mod files (mods.py), on a made-up
Workshop folder in tests/fixtures/mods/: two Force Org versions (only the newer
one downloaded), and an LCT with one matchup bag and a Combat Patrol bag.
"""

import json
from pathlib import Path

import pytest

import army
import data
import mods

FOLDER = Path(__file__).resolve().parent / "fixtures" / "mods"


@pytest.fixture
def tmp_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(army, "CATALOG", tmp_path / "catalog")
    monkeypatch.setattr(data, "CACHE", tmp_path / "cache")
    for k in ("TTS_MODS_DIR", "FORCEORG_WORKSHOP_ID", "LCT_WORKSHOP_ID"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def quiet(*_):
    pass


def test_newest_mod_is_picked(tmp_cache):
    path, entry = mods.find_mod("forceorg", FOLDER)
    assert (path.name, entry["id"]) == ("3000.json", "3000")


def test_mod_choice_from_env(tmp_cache, monkeypatch):
    monkeypatch.setenv("FORCEORG_WORKSHOP_ID", "2000")  # subscribed, but not downloaded
    with pytest.raises(data.DataError, match="Subscribe to it.*FORCEORG_WORKSHOP_ID"):
        mods.find_mod("forceorg", FOLDER)
    monkeypatch.setenv("TTS_MODS_DIR", str(FOLDER))
    monkeypatch.delenv("FORCEORG_WORKSHOP_ID")
    assert mods.find_mod("forceorg")[1]["id"] == "3000"


def test_missing_mod_says_what_to_subscribe_to(tmp_path):
    (tmp_path / "WorkshopFileInfos.json").write_text("[]")
    with pytest.raises(data.DataError, match="LCT isn't downloaded.*steamcommunity.com.*3710681747"):
        mods.find_mod("lct", tmp_path)
    with pytest.raises(data.DataError, match="TTS_MODS_DIR"):
        mods.find_mod("lct", tmp_path / "nope")


def test_force_org_catalog(tmp_cache):
    """The same catalog/ files `army.py index` writes through TTS: army tiles
    only, not tools packed in bags or objects without model data."""
    assert mods.read_force_org(FOLDER, log=quiet) == (2, 2)
    files = sorted(p.name for p in army.CATALOG.glob("*.json"))
    assert files == ["1e84c2.json", "e598e0.json"]
    tau = json.loads((army.CATALOG / "e598e0.json").read_text())
    assert [o["Nickname"] for o in tau["objects"]] == ["Commander Farsight", "Crisis Shas'ui"]
    assert army.load_catalog()["1e84c2"] == [{"Name": "Custom_Model", "Nickname": "Intercessor"}]
    # unchanged tiles are skipped, and where it came from is recorded beside the catalogue
    assert mods.read_force_org(FOLDER, log=quiet) == (2, 0)
    src = json.loads((tmp_cache / "cache" / "forceorg" / "source.json").read_text())
    assert (src["workshop_id"], src["name"]) == ("3000", "ForceOrg")


def test_lct_layouts(tmp_cache):
    index = mods.read_lct(FOLDER, log=quiet)
    assert index["source"]["workshop_id"] == "4000"
    # one bag serves both colour orders; missions follow the colours
    assert set(index["matchups"]) == {"5_4", "4_5"}
    m = index["matchups"]["5_4"]
    assert (m["label"], m["red_mission"], m["blue_mission"]) == (
        "Take and Hold vs Reconnaissance", "Hold Fast", "Scout Ahead")
    assert index["matchups"]["4_5"]["red_mission"] == "Scout Ahead"
    assert [(lo["map"], lo["deployment"], lo["pack"], lo["objects"]) for lo in m["layouts"]] == [
        ("TnH vs Rec 1", "Tipping Point", "LCT - Pack 1", 2),
        ("TnH vs Rec 2", "Dawn of War", "LCT - Pack 1", 3)]
    assert list(index["other"]) == ["Combat Patrol Maps"]
    layout = json.loads((mods.lct_dir() / m["layouts"][1]["file"]).read_text())
    assert (layout["name"], len(layout["objects"])) == ("TnH vs Rec 2 - Dawn of War - LCT - Pack 1", 3)
    assert layout["objects"][2]["Transform"] == {"posX": 2, "posZ": -2}
    assert mods.lct_index() == index


def test_not_the_right_mod(tmp_cache, tmp_path):
    folder = tmp_path / "Workshop"
    folder.mkdir()
    (folder / "WorkshopFileInfos.json").write_text(json.dumps([{"Directory": "x/5.json", "Name": "LCT"}]))
    (folder / "5.json").write_text(json.dumps({"ObjectStates": []}))
    with pytest.raises(data.DataError, match="No layouts"):
        mods.read_lct(folder, log=quiet)


@pytest.mark.parametrize("bag,key", [("Take and Hold vs Reconnaissance", "5_4"), ("Disruption vs Disruption", "1_1"),
                                     ("Combat Patrol Maps", None)])
def test_matchup_keys(bag, key):
    assert mods.matchup_key(bag) == key
