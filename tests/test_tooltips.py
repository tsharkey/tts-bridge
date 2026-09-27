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


def test_lead_model_carries_the_unit():
    unit = squad()
    assert tooltips.lead_model(unit, SHEET) == 0
    tip = tooltips.tooltip(unit, unit["models"][0], SHEET, lead=True)
    assert tip["name"] == "Squad Leader"
    text = tip["text"]
    assert text.startswith('[b]M[/b] 6"  [b]T[/b] 4  [b]Sv[/b] 3+  [b]W[/b] 2  [b]Ld[/b] 6+  [b]OC[/b] 2\n')
    assert "Base[-] 40mm" in text
    assert "[e8b53e]Power sword - strike[-]  Melee  A4  WS3+  S5  AP-2  D1" in text
    assert "[e8b53e]Twin carbine[-]" in text and "[b]Drone:[/b] It hovers." in text  # a drone's weapon and ability
    assert "Bolt rifle" not in text  # only what this model carries
    assert "[b]Hold Fast:[/b] MONSTER units can't Charge it." in text  # BSData's markup removed
    assert "Rules[-] Deep Strike" in text and "Keywords[-] Infantry, Battleline" in text
    assert "Enhancement[-] Iron Will" in text and "Warlord" in text


def test_other_models_stay_short():
    unit = squad()
    text = tooltips.tooltip(unit, unit["models"][1], SHEET, lead=False)["text"]
    assert "[b]InSv[/b] 5+" in text
    assert '2× [e8b53e]Bolt rifle[-]  24"  A2  BS3+  S4  AP-1  D1  [i]Assault, Heavy[/i]' in text
    assert "Hold Fast" not in text and "Keywords" not in text
    assert text.endswith("[i]Unit abilities: on the Squad Leader[/i]")


def test_describe_keeps_the_unit_line_first():
    unit = squad()
    unit["models"][0]["tooltip"] = tooltips.tooltip(unit, unit["models"][0], SHEET, lead=True)
    obj = tooltips.describe({"Name": "Custom_Model", "Nickname": "Old", "Description": "By someone"},
                            "Test Squad", unit["models"][0])
    assert obj["Name"] == "Custom_Model"  # the TTS object type, untouched
    assert obj["Nickname"] == "Squad Leader"
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
    first, counts = text.split("\n")[:2]
    assert "Leader of Bodyguard Squad" in first and "Warlord" in first and "Enhancement: Iron Will" in first
    assert counts == "1× Squad Leader · 4× Trooper"
    for section in ("[b]MODELS[/b]", "[b]RANGED WEAPONS[/b]", "[b]MELEE WEAPONS[/b]", "[b]ABILITIES[/b]",
                    "[b]WARGEAR ABILITIES[/b]", "[b]RULES[/b]", "[b]KEYWORDS[/b]", "[b]FACTION[/b]"):
        assert section in text, section
    assert "Bolt rifle" in text and "Power sword - strike" in text and "Twin carbine" in text
    assert "[b]Deep Strike:[/b] Arrives later." in text   # a unit rule, markup removed
    assert "[b]Assault:[/b]" in text                        # the Bolt rifle is "Assault, Heavy"
    assert "Lance" not in text                              # no weapon here has it
    card = tooltips.card_object(unit, text)
    assert (card["Name"], card["Nickname"], card["Tags"]) == ("Notecard", "Test Squad datasheet", ["tts-bridge:card"])
