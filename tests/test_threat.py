"""Threat ranges (threat.py, issue #25): a unit's reach from its datasheet, measured from its
bases' edges, and clipped by line of sight. Datasheets come from the BSData fixture, imported
into our format, plus a hand-made one for the cases it doesn't have."""

import math
from pathlib import Path

import pytest

import bsdata
import layouts
import los
import threat

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bsdata"


@pytest.fixture(scope="module")
def squad():
    catalogues, _ = bsdata.import_folder(FIXTURE)
    return next(u for u in catalogues["Xenos - Test Empire"]["units"] if u["name"] == "Test Squad")


# Our datasheet format, cut down: a fast unit with an Assault gun and a pistol.
BIKES = {"name": "Test Bikers", "models": [
            {"name": "Biker", "stats": {"M": "12\""}, "equipped": [{"name": "Twin shotgun", "count": 1}]},
            {"name": "Sergeant", "stats": {"M": "14\""}, "equipped": [{"name": "Plasma pistol", "count": 1}]}],
         "wargear": {"Twin shotgun": {"weapons": [{"name": "Twin shotgun", "type": "ranged", "range": "12\"",
                                                   "keywords": ["Assault", "Twin-linked"]}]},
                     "Plasma pistol": {"weapons": [{"name": "Plasma pistol", "type": "ranged", "range": "12\"",
                                                    "keywords": ["Pistol"]}]}}}


def at(x, z, base=1.26):
    return los.model({"x": x, "z": z, "base": [base, base]})


def test_inches():
    assert [threat.inches(t) for t in ['10"', '20+"', "Melee", "-", None, "18\" "]] == [10, 20, None, None, None, 18]


def test_profile_from_the_datasheet(squad):
    assert threat.default_wargear(squad) == ["Pulse rifle", "Close combat weapon", "Twin blade"]
    p = threat.profile(squad)
    assert p["move"] == 6
    assert [(w["name"], w["range"]) for w in p["weapons"]] == [
        ("Pulse rifle", 30), ("Close combat weapon", None), ("Twin blade - strike", None), ("Twin blade - sweep", None)]
    melee_only = threat.profile(squad, wargear=["Close combat weapon"])
    assert [w["name"] for w in melee_only["weapons"]] == ["Close combat weapon"]


def test_bands(squad):
    got = {b["band"]: (b["avg"], b["max"]) for b in threat.bands(threat.profile(squad))}
    assert got == {"move": (6, 6), "advance": (9.5, 12), "charge": (13, 18), "shoot: Pulse rifle": (36, 36)}
    bikes = {b["band"]: (b["avg"], b["max"]) for b in threat.bands(threat.profile(BIKES))}
    assert bikes["move"] == (12, 12)                                      # the slowest model's M
    assert bikes["advance and shoot: Twin shotgun"] == (27.5, 30)        # Assault: advance, then shoot
    assert "advance and shoot: Plasma pistol" not in bikes
    assert threat.bands({"unit": "Wall", "move": None, "weapons": []}) == []


def test_charge_chance():
    assert threat.charge_chance(2) == 1
    assert threat.charge_chance(7) == 21 / 36
    assert threat.charge_chance(12) == 1 / 36
    assert threat.charge_chance(13) == 0


def test_threats_at_a_distance(squad):
    p = threat.profile(squad)
    got = threat.threats(p, 15.2)                  # 15.2 - 6 move, to base contact: needs a 10 (9.2 rounded up)
    assert got["charge"] == {"needed": 10, "chance": round(6 / 36, 3)} and not got["engaged"]
    assert got["weapons"] == [{"weapon": "Pulse rifle", "range": 30, "now": True, "after_move": True,
                               "after_advance": False}]
    assert threat.threats(p, 18)["charge"]["needed"] == 12
    assert threat.threats(p, 18.5)["charge"] is None                  # beyond 6 + 12
    assert threat.threats(p, 5)["charge"] == {"needed": 2, "chance": 1.0}   # the move stops 2" short
    assert threat.threats(p, 1.5)["engaged"] and threat.threats(p, 1.5)["charge"]["needed"] == 0
    far = threat.threats(p, 33)["weapons"][0]
    assert (far["now"], far["after_move"]) == (False, True)
    assert threat.threats(p, 10, visible=False)["weapons"][0]["now"] is False   # can't see it
    bikes = threat.threats(threat.profile(BIKES), 28)["weapons"]
    assert [(w["weapon"], w["after_move"], w["after_advance"]) for w in bikes] == [
        ("Twin shotgun", False, True), ("Plasma pistol", False, False)]


def test_ranges_are_from_base_edges_and_union_over_the_unit():
    unit = [at(0, 0), at(10, 0, base=2.0)]
    assert threat.within(unit, 6, 6.62, 0)          # 6" from the 32mm base's edge (r 0.63)
    assert not threat.within(unit, 6, 0, 6.7)
    assert threat.within(unit, 6, 17, 0)            # from the second, bigger base (r 1)
    assert not threat.within(unit, 6, 17.1, 0)
    assert threat.bubbles(unit, 6) == [{"x": 0, "z": 0, "r": 6.63}, {"x": 10, "z": 0, "r": 7.0}]


def test_weapon_range_clipped_by_line_of_sight():
    open_table = los.visibility_polygon(at(0, 0), [], max_range=12)
    assert math.isclose(abs(layouts.signed_area(open_table)), math.pi * 12.63 ** 2, rel_tol=0.001)
    wall = {"areas": [{"id": "A1", "polygon": [[5, -3], [8, -3], [8, 3], [5, 3]],
                       "features": [{"id": "A1a", "category": "dense", "polygon": [[6, -1], [7, -1], [7, 1], [6, 1]],
                                     "height": 5, "floors": []}]}]}
    clipped = los.visibility_polygon(at(0, 0), los.blockers(wall), max_range=12)
    assert layouts.inside((7.5, 2), clipped) and not layouts.inside((10, 0), clipped)   # behind the area
    assert layouts.inside((0, 12), clipped) and not layouts.inside((0, 12.8), clipped)  # in range, just out
