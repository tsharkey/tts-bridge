"""Datasheet tooltips on spawned models (tooltips.py), on a made-up datasheet."""

import board
import tooltips

SHEET = {
    "id": "u1", "name": "Test Squad", "catalogue": "Test", "rules": ["Deep Strike"],
    "keywords": ["Infantry", "Battleline"], "factions": ["Test Empire"],
    "abilities": [{"name": "Hold Fast", "type": "Abilities", "text": "**MONSTER** units can't ^^Charge^^ it."}],
    "models": [
        {"name": "Squad Leader", "min": 1, "max": 1, "equipped": [], "options": [],
         "stats": {"M": '6"', "T": "4", "Sv": "3+", "W": "2", "Ld": "6+", "OC": "2", "InSv": None}},
        {"name": "Trooper", "min": 4, "max": 9, "equipped": [], "options": [],
         "stats": {"M": '6"', "T": "4", "Sv": "3+", "W": "2", "Ld": "6+", "OC": "2", "InSv": "5+"}},
    ],
    "wargear": {
        "Bolt rifle": {"weapons": [{"name": "Bolt rifle", "type": "ranged", "range": '24"', "A": "2", "skill": "3+",
                                    "S": "4", "AP": "-1", "D": "1", "keywords": ["Assault", "Heavy"]}], "abilities": []},
        "Power sword": {"weapons": [{"name": "Power sword - strike", "type": "melee", "range": "Melee", "A": "4",
                                     "skill": "3+", "S": "5", "AP": "-2", "D": "1", "keywords": []}], "abilities": []},
        "Gun Drone": {"weapons": [{"name": "Twin carbine", "type": "ranged", "range": '20"', "A": "2", "skill": "5+",
                                   "S": "5", "AP": "0", "D": "1", "keywords": []}],
                      "abilities": [{"name": "Drone", "text": "It hovers."}]},
    },
}


def squad():
    leader = {"name": "Squad Leader", "wargear": ["Power sword"], "sheet_model": "Squad Leader",
              "gear": [{"name": "Power sword", "count": 1}, {"name": "Gun Drone", "count": 1}],
              "base": {"shape": "round", "text": "40mm", "inches": [1.57]}}
    troopers = [{"name": "Trooper", "wargear": ["Bolt rifle"], "sheet_model": "Trooper",
                 "gear": [{"name": "Bolt rifle", "count": 2}]} for _ in range(4)]
    return {"name": "Test Squad", "models": [leader] + troopers, "enhancements": ["Iron Will"], "warlord": True}


def test_tooltip_sections():
    unit = squad()
    assert tooltips.lead_model(unit, SHEET) == 0
    tip = tooltips.tooltip(unit, unit["models"][0], SHEET, lead=True)
    assert tip["name"] == "2/2 Squad Leader"                    # the wound tracker's count
    sections = tip["text"].split("\n\n")
    stats, weapons, abilities, footer = [s.split("\n") for s in sections]
    assert stats[0] == tooltips.RULE
    assert stats[1] == "[8fd694]M    T   Sv   W   Ld   OC[-]"   # names over values, padded to line up
    assert stats[2] == '[b]6"   4   3+   2   6+   2[/b]'
    assert weapons == ["[ef6f6c][b]Weapons[/b][-]",
                       "[e8b53e]Power sword - strike[-]", "Melee  A4  WS3+  S5  AP-2  D1",
                       "[e8b53e]Twin carbine[-]", '20"  A2  BS5+  S5  AP0  D1']   # a drone's weapon too
    assert abilities == ["[c49bf2][b]Abilities[/b][-]", "Hold Fast", "Drone", "Deep Strike"]   # names only
    assert footer == ["[e8b53e]Warlord · Enhancement: Iron Will[-]", "[9aa1ad]Keywords: Infantry, Battleline[-]",
                      "[9aa1ad]Base: 40mm[-]"]


def test_every_model_has_the_same_sections():
    unit = squad()
    tip = tooltips.tooltip(unit, unit["models"][1], SHEET, lead=False)
    text = tip["text"]
    assert tip["name"] == "2/2 Trooper"
    assert "InSv" in text.split("\n")[1] and "5+" in text.split("\n")[2]
    assert '2× [e8b53e]Bolt rifle[-]\n24"  A2  BS3+  S4  AP-1  D1  [i]Assault, Heavy[/i]' in text
    assert "Hold Fast" in text and "Keywords" in text
    assert "Warlord" not in text and "Iron Will" not in text   # what the list chose shows on the leader only


def test_one_wound_models_have_no_tracker():
    sheet = dict(SHEET, models=[dict(m, stats=dict(m["stats"], W="1")) for m in SHEET["models"]])
    unit = squad()
    assert tooltips.tooltip(unit, unit["models"][1], sheet, lead=False)["name"] == "Trooper"
    assert tooltips.wounds({"W": "D6"}) is None and tooltips.wounds({"W": "13"}) == 13


def test_describe_keeps_the_unit_line_first():
    unit = squad()
    unit["models"][0]["tooltip"] = tooltips.tooltip(unit, unit["models"][0], SHEET, lead=True)
    obj = tooltips.describe({"Name": "Custom_Model", "Nickname": "Old", "Description": "By someone"},
                            "Test Squad", unit["models"][0])
    assert obj["Name"] == "Custom_Model"  # the TTS object type, untouched
    assert obj["Nickname"] == "2/2 Squad Leader"
    head, rest = obj["Description"].split("\n", 1)
    assert board.UNIT_RE.match(head).group(1) == "Test Squad"  # board.py still finds the unit
    assert rest.endswith("[i]By someone[/i]") and "Hold Fast" in rest
    # without a datasheet: just the unit line and the catalogue's own description
    plain = tooltips.describe({"Description": "By someone"}, "Test Squad", {"name": "x", "wargear": []})
    assert plain["Description"] == "[Test Squad]\nBy someone" and "Nickname" not in plain


def test_card_has_the_whole_datasheet():
    sheet = dict(SHEET, glossary={"Deep Strike": "Arrives **later**.", "Assault": "Shoot after Advancing.",
                                  "Lance": "Not carried here."})
    unit = squad()
    unit["role"], unit["attached_to"] = "leader", 1
    others = [unit, {"name": "Bodyguard Squad"}]
    text = tooltips.card_text(unit, sheet, others)
    sections = text.split("\n\n")
    first, counts = sections[0].split("\n")
    assert "Leader of Bodyguard Squad" in first and "Warlord" in first and "Enhancement: Iron Will" in first
    assert counts == "1× Squad Leader · 4× Trooper"
    heads = [s.split("\n")[0] for s in sections[1:]]
    assert heads == ["[8fd694][b]MODELS[/b][-]", "[ef6f6c][b]RANGED WEAPONS[/b][-]", "[ef6f6c][b]MELEE WEAPONS[/b][-]",
                     "[c49bf2][b]ABILITIES[/b][-]", "[c49bf2][b]WARGEAR ABILITIES[/b][-]", "[c49bf2][b]RULES[/b][-]",
                     "[9aa1ad][b]KEYWORDS[/b][-] Infantry, Battleline"]
    assert "[b]FACTION[/b]" in sections[-1]
    assert "[e8b53e]Bolt rifle[-]\n24\"  A2" in text and "Power sword - strike" in text and "Twin carbine" in text
    assert "[b]Deep Strike[/b]\nArrives later." in text   # a unit rule, markup removed, text under its name
    assert "[b]Assault[/b]" in text                        # the Bolt rifle is "Assault, Heavy"
    assert "Lance" not in text                             # no weapon here has it


def test_weapon_names_read_back_from_a_tooltip():
    """What reach.py reads off spawned models: the weapon profiles in each one's tooltip."""
    unit = squad()
    lead = tooltips.tooltip(unit, unit["models"][0], SHEET, lead=True)["text"]
    trooper = tooltips.tooltip(unit, unit["models"][1], SHEET, lead=False)["text"]
    assert tooltips.weapon_names(f"[Test Squad]\n{lead}") == ["Power sword - strike", "Twin carbine"]
    assert tooltips.weapon_names(trooper) == ["Bolt rifle"]          # "2× " in front
    assert tooltips.weapon_names("[Test Squad]\nno tooltip") == [] and tooltips.weapon_names(None) == []
    # models spawned with the earlier one-line template still read
    old = ("[Test Squad]\n[b]M[/b] 6\"\n[9aa1ad]Weapons[-]\n2× [e8b53e]Bolt rifle[-]  24\"  A2\n"
           "[e8b53e]Power sword - strike[-]  Melee  A4\n[9aa1ad]Abilities[-]\n[b]Hold Fast:[/b] text")
    assert tooltips.weapon_names(old) == ["Bolt rifle", "Power sword - strike"]
