"""
deploy_demo.py — example deployment of the Tau list into Red's Search and
Destroy zone (+x/+z table quarter, more than 9" from centre).

Validates every base against the zone, other bases and terrain, then moves
everything in one Lua call.

    python deploy_demo.py --check   # validate only
    python deploy_demo.py           # validate and deploy
"""

import math
import sys

import tts_bridge as tts

TABLE_HALF_X, TABLE_HALF_Z = 30, 22
CENTRE_EXCLUSION = 9
FACING = 225  # toward Blue's corner (-x, -z)
DROP_Y = 1.6

# Base radius in inches, from getBoundsNormalized().
R = {"hh": 1.18, "broad": 1.18, "cmd": 1.28, "crisis": 0.985, "riptide": 2.35,
     "stealth": 0.63, "pf": 0.49, "dark": 0.63, "shadow": 0.985}

# World-axis boxes (xmin, xmax, zmin, zmax) for terrain in or near our quarter.
TERRAIN = {
    "CO (dense)":          (12.2, 15.0, 11.8, 18.0),
    "Generator (dense)":   (-3.1, 1.9, 10.2, 12.8),
    "Tower (dense)":       (17.8, 20.4, -2.6, 0.0),
    "Long Barrier":        (13.05, 13.55, 0.85, 5.15),
    "Small L":             (23.35, 25.65, 9.4, 10.8),
    "Small L flip (7.5)":  (6.35, 8.65, 13.4, 14.8),
    "Small L flip (10.5)": (9.35, 11.65, 17.1, 18.5),
}

# unit -> list of (guid, base, x, z)
PLAN = {
    "Pathfinders A + Darkstrider (on objective 0,15.5)": [
        ("c71792", "pf", 1.0, 16.5), ("291340", "pf", 2.2, 16.5), ("46d02d", "pf", 3.4, 16.5),
        ("a9b984", "pf", 4.6, 16.5), ("650cdb", "pf", 5.8, 16.5),
        ("52ea2b", "pf", 1.0, 17.7), ("6353aa", "pf", 2.2, 17.7), ("3d8e15", "pf", 3.4, 17.7),
        ("7cdd71", "pf", 4.6, 17.7), ("32bbc4", "pf", 5.8, 17.7),
        ("72ae1f", "dark", 3.4, 19.1),
    ],
    "Pathfinders B (on objective 19.4,0, behind Tower)": [
        ("65c470", "pf", 21.0, 1.0), ("1eddb1", "pf", 22.2, 1.0), ("0d7ae0", "pf", 23.4, 1.0),
        ("6ed6ed", "pf", 24.6, 1.0), ("db7604", "pf", 25.8, 1.0),
        ("29f522", "pf", 21.0, 2.2), ("0447bb", "pf", 22.2, 2.2), ("478384", "pf", 23.4, 2.2),
        ("f0b80d", "pf", 24.6, 2.2), ("6a5620", "pf", 25.8, 2.2),
    ],
    "Stealth A + Shadowsun (ruin behind the Small L)": [
        ("34762b", "stealth", 9.5, 12.7), ("bb2f70", "stealth", 10.9, 12.7),
        ("13ad02", "stealth", 9.5, 14.1), ("c84a7f", "stealth", 10.9, 14.1),
        ("d6dfab", "stealth", 9.5, 15.5),
        ("30d929", "shadow", 8.0, 16.3),
    ],
    "Stealth B (behind Long Barrier)": [
        ("597f4e", "stealth", 14.4, 1.5), ("20bfe0", "stealth", 15.8, 1.5),
        ("df2d0f", "stealth", 14.4, 2.9), ("860ac4", "stealth", 15.8, 2.9),
        ("167468", "stealth", 14.4, 4.3),
    ],
    "Fireknife + Farsight": [
        ("111d06", "crisis", 9.5, 8.5), ("be7504", "crisis", 11.7, 8.5), ("bf2c13", "crisis", 13.9, 8.5),
        ("b937bf", "cmd", 11.7, 6.2),
    ],
    "Starscythe 1 + Coldstar (back right)": [
        ("409541", "crisis", 24.0, 5.5), ("d0d595", "crisis", 26.2, 5.5), ("d4ed54", "crisis", 28.4, 5.5),
        ("1cedcb", "cmd", 26.2, 8.0),
    ],
    "Starscythe 2 + Enforcer": [
        ("69b9b6", "crisis", 17.5, 8.5), ("fd6303", "crisis", 19.7, 8.5), ("ec5793", "crisis", 21.9, 8.5),
        ("b73bc6", "cmd", 19.7, 11.0),
    ],
    "Ri'Lantar": [("a08e33", "cmd", 20.5, 5.0)],
    "Ri'Locai": [("df46c0", "cmd", 22.5, 13.2)],
    "Hammerheads (hidden behind CO)": [
        ("80fae9", "hh", 17.0, 14.8), ("f71cd2", "hh", 17.0, 20.0),
    ],
    "Broadsides (in back ruin)": [
        ("8c638b", "broad", 21.5, 16.0), ("38d194", "broad", 24.0, 16.0),
    ],
    "Riptide (back corner)": [("c47895", "riptide", 26.8, 19.5)],
}


def circle_hits_box(x, z, r, box):
    xmin, xmax, zmin, zmax = box
    dx = max(xmin - x, 0, x - xmax)
    dz = max(zmin - z, 0, z - zmax)
    return dx * dx + dz * dz < r * r


def validate():
    models = [(unit, g, R[b], x, z) for unit, ms in PLAN.items() for g, b, x, z in ms]
    problems = []
    for unit, g, r, x, z in models:
        if x - r < 0 or z - r < 0 or x + r > TABLE_HALF_X or z + r > TABLE_HALF_Z:
            problems.append(f"{g} ({unit}) base leaves the deployment quarter")
        if math.hypot(x, z) - r < CENTRE_EXCLUSION:
            problems.append(f"{g} ({unit}) is within 9\" of centre")
        for name, box in TERRAIN.items():
            if circle_hits_box(x, z, r, box):
                problems.append(f"{g} ({unit}) overlaps {name}")
    for i, (u1, g1, r1, x1, z1) in enumerate(models):
        for u2, g2, r2, x2, z2 in models[i + 1:]:
            if math.hypot(x1 - x2, z1 - z2) < r1 + r2:
                problems.append(f"{g1} ({u1}) overlaps {g2} ({u2})")
    return models, problems


def deploy(models):
    lines = []
    for _, g, _, x, z in models:
        lines.append(f'do local o = getObjectFromGUID("{g}") if o then '
                     f'o.setPositionSmooth({{{x}, {DROP_Y}, {z}}}, false, true) '
                     f'o.setRotationSmooth({{0, {FACING}, 0}}, false, true) end end')
    lines.append(f"return {len(models)}")
    return tts.run_lua("\n".join(lines))


def main():
    models, problems = validate()
    for p in problems:
        print("PROBLEM:", p)
    print(f"{len(models)} models in {len(PLAN)} units, {len(problems)} problems")
    if problems or "--check" in sys.argv:
        return
    tts.start_listener()
    print("Deployed", deploy(models), "models")


if __name__ == "__main__":
    main()
