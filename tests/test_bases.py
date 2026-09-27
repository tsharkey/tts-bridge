"""
Base sizes (bases.py) from a made-up Wahapedia export in tests/fixtures/wahapedia/
(their CSV layout, invented sizes), joined to parsed lists through the made-up
datasheet cache in tests/fixtures/datacache/.
"""

from pathlib import Path

import pytest

import army
import bases
import data
import datasheets

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXPORT = FIXTURES / "wahapedia"
SHEETS = FIXTURES / "datacache"


def quiet(*_):
    pass


@pytest.fixture
def cache(tmp_path):
    data.fetch("wahapedia", str(EXPORT), cache=tmp_path, log=quiet)
    bases.import_wahapedia(tmp_path, log=quiet)
    return tmp_path


def parsed_with_bases(name, cache, mappings=None):
    parsed = army.parse_list((FIXTURES / name).read_text(), {})
    datasheets.attach(parsed, {}, SHEETS)
    bases.attach(parsed, mappings or {}, cache)
    return parsed


def model_bases(parsed, unit):
    return {(m["name"], m["base"]["text"] if m["base"] else None)
            for u in parsed["units"] if u["name"] == unit for m in u["models"]}


@pytest.mark.parametrize("text,shape,mm,inches,flying", [
    ("32mm", "round", [32], [1.26], False),
    ("28.5mm", "round", [28.5], [1.12], False),
    ("170 x 109mm", "oval", [170, 109], [6.69, 4.29], False),
    ("120 x 92mm flying base", "oval", [120, 92], [4.72, 3.62], True),
    ("60mm flying base", "round", [60], [2.36], True),
    ("Use model", "model", [], [], False),
    ("No official base size", "none", [], [], False),
    ("", "none", [], [], False),
])
def test_parse_base(text, shape, mm, inches, flying):
    b = bases.parse_base(text, "a note")
    assert (b["shape"], b["mm"], b["inches"], b["flying"], b["text"], b["note"]) == (shape, mm, inches, flying, text, "a note")


def test_import(cache):
    index = bases.load(cache)
    assert index["source"]["kind"] == "folder"
    assert set(index["factions"]) == {"T'au Empire", "Space Marines", "Imperial Agents"}  # curly apostrophe cleaned
    stealth = index["factions"]["T'au Empire"]["stealth battlesuits"]
    assert {k: v["text"] for k, v in stealth["lines"].items()} == {"stealth shas'vre": "40mm", "stealth shas'ui": "32mm"}
    assert data.status(cache)["wahapedia"]["models"] == 12


def test_list_models_get_bases(cache):
    parsed = parsed_with_bases("tau_tournament.txt", cache)
    assert model_bases(parsed, "Commander Farsight") == {("Commander Farsight", "60mm")}
    # BSData's "Stealth Shas'ui w/ burst cannon" is Wahapedia's "Stealth Shas'ui"
    assert model_bases(parsed, "Stealth Battlesuits") == {("Stealth Shas'vre", "40mm"), ("Stealth Shas'ui", "32mm")}
    assert model_bases(parsed, "Broadside Battlesuits") == {("Broadside Battlesuits", "60mm")}
    # the datasheet is "Riptide Battlesuit [Legends]"
    [riptide] = [m for u in parsed["units"] if u["name"] == "Riptide Battlesuit" for m in u["models"]][:1]
    assert (riptide["base"]["shape"], riptide["base"]["inches"], riptide["base"]["flying"]) == ("oval", [4.72, 3.62], True)
    # no datasheet in the sample, so it's found by the list's own name; the note comes along
    breachers = [u for u in parsed["units"] if u["name"] == "Breacher Team"][0]
    sergeant = breachers["models"][0]
    assert (sergeant["base"]["text"], sergeant["base"]["note"]) == ("28.5mm", "32mm if equipped with a support turret")
    assert {m["base"]["text"] for m in breachers["models"]} == {"28.5mm"}
    # units the export doesn't have are flagged
    assert ("Darkstrider", "Darkstrider", "not found") in bases.missing(parsed)


def test_own_faction_first(cache):
    """Space Marines and Imperial Agents both have a "Terminator Squad"."""
    parsed = parsed_with_bases("gw_app_raven_guard.txt", cache)
    assert model_bases(parsed, "Terminator Squad") == {("Terminator Sergeant", "40mm"), ("Terminator", "40mm")}


def test_no_official_size_and_use_model(cache):
    parsed = {"faction": "Space Marines", "sub": None, "units": [
        {"name": "Chaos Spawn (Flesh Change)", "models": [{"name": "Chaos Spawn", "wargear": []}]},
        {"name": "Hammerhead Gunship", "models": [{"name": "Hammerhead Gunship", "wargear": []}]}]}
    bases.attach(parsed, {}, cache)
    spawn, hammerhead = (u["models"][0]["base"] for u in parsed["units"])
    assert (spawn["shape"], spawn["note"]) == ("none", "Use a 60mm base?")   # found without "(Flesh Change)"
    assert hammerhead["shape"] == "model"
    assert bases.missing(parsed) == [("Chaos Spawn (Flesh Change)", "Chaos Spawn", "no official size"),
                                     ("Hammerhead Gunship", "Hammerhead Gunship", "use the model")]


def test_fixes_in_mappings(cache):
    fixes = {"bases": {"T'au Empire|Darkstrider|Darkstrider": "32mm"}}
    parsed = parsed_with_bases("tau_tournament.txt", cache, fixes)
    darkstrider = [u for u in parsed["units"] if u["name"] == "Darkstrider"][0]["models"][0]
    assert (darkstrider["base"]["mm"], darkstrider["base"]["fixed"]) == ([32], True)


def test_nothing_cached(tmp_path):
    parsed = army.parse_list((FIXTURES / "tau_tournament.txt").read_text(), {})
    assert bases.attach(parsed, {}, tmp_path) is None
    assert all("base" not in m for u in parsed["units"] for m in u["models"])
    with pytest.raises(data.DataError, match="fetch wahapedia"):
        bases.import_wahapedia(tmp_path, log=quiet)


def test_fetch_export(tmp_path, monkeypatch):
    """Downloading the export, skipping it when Last_update hasn't changed, and
    refusing a web page where a CSV should be."""
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return (EXPORT / url.rsplit("/", 1)[1]).read_bytes()
    monkeypatch.setattr(data, "get", fake_get)
    info = data.fetch("wahapedia", "https://example.test/wh40k11ed/", cache=tmp_path, log=quiet)
    assert (info["kind"], info["url"], info["commit"]) == ("export", "https://example.test/wh40k11ed", "2026-01-01 00:00:00")
    assert sorted(p.name for p in data.raw_dir("wahapedia", tmp_path).iterdir()) == sorted(bases.FILES)
    calls.clear()
    data.fetch("wahapedia", cache=tmp_path, log=quiet)
    assert calls == ["https://example.test/wh40k11ed/Last_update.csv"]  # unchanged: nothing else downloaded

    monkeypatch.setattr(data, "get", lambda url, **kw: b"<!DOCTYPE html><p>not here</p>"
                        if not url.endswith("Last_update.csv") else b"last_update|\r\n2026-02-02|\r\n")
    with pytest.raises(data.DataError, match="web page"):
        data.fetch("wahapedia", cache=tmp_path, log=quiet)
    assert data.source_info("wahapedia", tmp_path)["commit"] == "2026-01-01 00:00:00"  # old cache kept
