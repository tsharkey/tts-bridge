"""
mods.py — read the Force Org and LCT mods straight from TTS's Workshop folder,
so their data can be cached with TTS closed. TTS keeps every subscribed mod as
a save file there; WorkshopFileInfos.json says which file is which.

    python3 data.py mods              # both
    python3 data.py mods forceorg     # catalog/, as `army.py index` builds it through TTS
    python3 data.py mods lct          # cache/lct/: each layout's objects, matchups and missions

Settings (.env or the environment, all optional):
    TTS_MODS_DIR            the Workshop folder, if it isn't in the usual place
    FORCEORG_WORKSHOP_ID    which Force Org to read when several are subscribed (default: the newest)
    LCT_WORKSHOP_ID         the same for LCT
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import army
import config  # noqa: F401  (loads .env)
import data
from data import DataError, now, read_json, write_json

MODS = {
    "forceorg": {"name": "ForceOrg", "setting": "FORCEORG_WORKSHOP_ID",
                 "workshop": "https://steamcommunity.com/sharedfiles/filedetails/?id=3753014527"},
    "lct": {"name": "LCT", "setting": "LCT_WORKSHOP_ID",
            "workshop": "https://steamcommunity.com/sharedfiles/filedetails/?id=3710681747"},
}
# LCT numbers dispositions in this order ("5_4" = Take and Hold red, Reconnaissance blue)
DISPOSITIONS = ["Disruption", "Priority Assets", "Purge the Foe", "Reconnaissance", "Take and Hold"]


def mods_dir():
    """TTS's Workshop folder: TTS_MODS_DIR, else the macOS or Windows default."""
    if os.environ.get("TTS_MODS_DIR"):
        return Path(os.environ["TTS_MODS_DIR"]).expanduser()
    home = Path.home()
    for p in (home / "Library/Tabletop Simulator/Mods/Workshop",                  # macOS
              home / "Documents/My Games/Tabletop Simulator/Mods/Workshop",       # Windows
              home / ".local/share/Tabletop Simulator/Mods/Workshop"):            # Linux
        if p.is_dir():
            return p
    return home / "Library/Tabletop Simulator/Mods/Workshop"


def find_mod(key, folder=None):
    """-> (path, {"id", "name", "updated"}) for a mod's save file, or DataError
    saying which Workshop item to subscribe to."""
    mod = MODS[key]
    folder = Path(folder) if folder else mods_dir()
    if not folder.is_dir():
        raise DataError(f"Can't find TTS's Workshop folder at {folder}. Set TTS_MODS_DIR in .env.")
    infos = read_json(folder / "WorkshopFileInfos.json", [])
    entries = [{"id": Path(e["Directory"]).stem, "name": e.get("Name"), "updated": e.get("UpdateTime") or 0}
               for e in infos if e.get("Directory")]
    wanted = os.environ.get(mod["setting"], "").strip()
    if wanted:
        entry = next((e for e in entries if e["id"] == wanted), {"id": wanted, "name": mod["name"], "updated": 0})
    else:
        named = [e for e in entries if (e["name"] or "").strip().lower() == mod["name"].lower()]
        entry = max(named, key=lambda e: e["updated"], default=None)
    path = folder / f"{entry['id']}.json" if entry else None
    if not path or not path.exists():
        raise DataError(f"{mod['name']} isn't downloaded. Subscribe to it in the Steam Workshop "
                        f"({mod['workshop']}) and open it once in TTS"
                        + (f", or check {mod['setting']}." if wanted else "."))
    return path, entry


def load_save(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise DataError(f"Couldn't read {path}: {e}") from e


def source(path, entry):
    stat = Path(path).stat()
    return {"file": str(path), "workshop_id": entry["id"], "name": entry["name"],
            "updated": datetime.fromtimestamp(entry["updated"] or stat.st_mtime, timezone.utc).isoformat(
                timespec="seconds"), "read": now()}


# --------------------------------------------------------------------------
# Force Org: the army tiles are the table's top-level objects whose script
# holds model data and makes a "Load Models" button (what `army.py index`
# looks for in TTS). Tools packed in bags have model data too, but aren't tiles.

def force_org_tiles(save):
    return [o for o in save.get("ObjectStates") or []
            if "objectJSONs" in (o.get("LuaScript") or "") and "Load Models" in o["LuaScript"]]


def forceorg_source_file(cache=None):
    # beside catalog/, not in it: load_catalog reads every .json there
    return (cache or data.CACHE) / "forceorg" / "source.json"


def read_force_org(folder=None, cache=None, log=print):
    """Build catalog/ from Force Org's save file. -> (tiles read, tiles updated)."""
    path, entry = find_mod("forceorg", folder)
    log(f"Force Org: reading {path.name} ({entry['name']})")
    tiles = force_org_tiles(load_save(path))
    if not tiles:
        raise DataError(f"No army tiles in {path}. Is it Force Org?")
    updated = 0
    for i, o in enumerate(tiles, 1):
        g = o["GUID"]
        updated += army.write_tile(g, o["LuaScript"], f"[{i}/{len(tiles)}] {army.tile_label(g)} ({g})", log)
    write_json(forceorg_source_file(cache), source(path, entry))
    log(f"{len(tiles)} tiles read, {updated} updated")
    return len(tiles), updated


# --------------------------------------------------------------------------
# LCT: one bag per matchup ("Take and Hold vs Reconnaissance"), holding a card
# per layout ("TnH vs Rec 1 - Tipping Point - LCT - Pack 1": map, deployment,
# terrain pack) whose script spawns the layout's objects. Missions come from
# the Global script's missionMatchups table.

def lct_dir(cache=None):
    return (cache or data.CACHE) / "lct"


def mission_matchups(global_script):
    """{"5_4": {"red": ..., "blue": ...}} from LCT's Global script."""
    return {k: {"red": r, "blue": b} for k, r, b in re.findall(
        r'\["(\d_\d)"\]\s*=\s*\{\s*red\s*=\s*"([^"]*)"\s*,\s*blue\s*=\s*"([^"]*)"', global_script or "")}


def layout_info(card_name):
    """"TnH vs Rec 1 - Tipping Point - LCT - Pack 1" -> map, deployment, pack."""
    parts = card_name.split(" - ", 2)
    return {"map": parts[0].strip(), "deployment": parts[1].strip() if len(parts) > 1 else None,
            "pack": parts[2].strip() if len(parts) > 2 else None}


def matchup_key(bag_name):
    """"Take and Hold vs Reconnaissance" -> "5_4", or None for other bags."""
    red, _, blue = bag_name.partition(" vs ")
    if red in DISPOSITIONS and blue in DISPOSITIONS:
        return f"{DISPOSITIONS.index(red) + 1}_{DISPOSITIONS.index(blue) + 1}"
    return None


def read_lct(folder=None, cache=None, log=print):
    """Write cache/lct/: index.json and layouts/<card guid>.json. -> the index."""
    path, entry = find_mod("lct", folder)
    log(f"LCT: reading {path.name} ({entry['name']})")
    save = load_save(path)
    missions = mission_matchups(save.get("LuaScript"))
    out = lct_dir(cache)
    layouts_dir = out / "layouts.new"
    shutil.rmtree(layouts_dir, ignore_errors=True)
    layouts_dir.mkdir(parents=True)
    index = {"source": source(path, entry), "matchups": {}, "other": {}}
    count = 0
    for bag in save.get("ObjectStates") or []:
        cards = [c for c in bag.get("ContainedObjects") or [] if "objectJSONs" in (c.get("LuaScript") or "")]
        if not cards:
            continue
        name = (bag.get("Nickname") or "").strip()
        layouts = []
        for c in cards:
            objs = army.parse_tile_script(c["LuaScript"])
            file = f"layouts/{c['GUID']}.json"
            write_json(layouts_dir / f"{c['GUID']}.json", {"name": c["Nickname"], "guid": c["GUID"],
                                                           **layout_info(c["Nickname"]), "objects": objs})
            layouts.append({"name": c["Nickname"], "guid": c["GUID"], **layout_info(c["Nickname"]),
                            "objects": len(objs), "file": file})
            count += 1
        layouts.sort(key=lambda lo: (lo["pack"] or "", lo["map"]))
        key = matchup_key(name)
        if key:
            reverse = "_".join(reversed(key.split("_")))
            for k in {key, reverse}:  # one bag serves both colour orders
                index["matchups"][k] = {"label": name, "red_mission": missions.get(k, {}).get("red"),
                                        "blue_mission": missions.get(k, {}).get("blue"), "layouts": layouts}
        else:
            index["other"][name] = {"layouts": layouts}
        log(f"  {name}: {len(layouts)} layouts")
    if not count:
        shutil.rmtree(layouts_dir)
        raise DataError(f"No layouts in {path}. Is it LCT?")
    shutil.rmtree(out / "layouts", ignore_errors=True)
    layouts_dir.rename(out / "layouts")
    write_json(out / "index.json", index)
    log(f"LCT: {count} layouts in {len(index['matchups'])} matchups")
    return index


def lct_index(cache=None):
    return read_json(lct_dir(cache) / "index.json")


def main(args):
    which = args or ["forceorg", "lct"]
    try:
        for w in which:
            if w == "forceorg":
                read_force_org()
            elif w == "lct":
                read_lct()
            else:
                sys.exit(__doc__)
    except DataError as e:
        sys.exit(str(e))
