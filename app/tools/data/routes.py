"""Data cache's API: what's cached, fetching a source, and rebuilding the
Force Org catalogue from TTS.

Fetches and rebuilds run in a thread so the page can show their progress: the
page starts one and polls GET /api/data/job for the log. Only one runs at a
time. They call the same code as `python3 data.py fetch` and `army.py index`.
"""

import threading
from datetime import datetime, timezone

import army
import data
import mods
import tts_bridge as tts
from app.core.api import router as api_router
from app.core.tts import lock as tts_lock

router = api_router()

SOURCES = {"bsdata": {"name": "Datasheets (BSData)", "refs": True,
                      "about": "Stats, weapons, abilities and keywords for every unit, from the community's "
                               "BattleScribe data. Downloaded once, then used offline."},
           "wahapedia": {"name": "Base sizes (Wahapedia)", "refs": False,
                         "about": "Every model's official base size, from Wahapedia's data export. Used to "
                                  "measure ranges from the edge of the base."}}

job_lock = threading.Lock()
job = {"source": None, "running": False, "log": [], "error": None, "started": None, "finished": None}


def log(line):
    job["log"].append(str(line))


def start(source, work):
    """Run work() in the background as the current job."""
    with job_lock:
        if job["running"]:
            raise ValueError(f"Already working on {job['source']}; wait for it to finish.")
        job.update(source=source, running=True, log=[], error=None, finished=None,
                   started=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def run():
        try:
            work()
        except (data.DataError, SystemExit) as e:  # SystemExit: TTS isn't reachable
            job["error"] = str(e) or "TTS isn't responding"
        except Exception as e:  # report anything else to the page too
            job["error"] = f"{type(e).__name__}: {e}"
        finally:
            job["running"] = False
            job["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    threading.Thread(target=run, daemon=True).start()
    return job


def locked_lua(code, **kw):
    # one call at a time, so the header's status check and other tools can run in between
    with tts_lock:
        return tts.run_lua(code, **kw)


def mod_file(key):
    """The mod's save file, or why it can't be read."""
    try:
        path, entry = mods.find_mod(key)
        return {"file": str(path), "workshop_id": entry["id"], "error": None}
    except data.DataError as e:
        return {"file": None, "workshop_id": None, "error": str(e)}


def force_org():
    tiles = sorted(army.CATALOG.glob("*.json")) if army.CATALOG.exists() else []
    newest = max((p.stat().st_mtime for p in tiles), default=None)
    return {"tiles": len(tiles),
            "built": datetime.fromtimestamp(newest, timezone.utc).isoformat(timespec="seconds") if newest else None,
            "source": data.read_json(mods.forceorg_source_file()), "mod": mod_file("forceorg")}


def lct():
    index = mods.lct_index()
    out = {"mod": mod_file("lct"), "source": None, "layouts": 0, "matchups": 0}
    if index:
        seen = {lo["guid"] for m in [*index["matchups"].values(), *index["other"].values()] for lo in m["layouts"]}
        out.update(source=index["source"], layouts=len(seen), matchups=len(index["matchups"]))
    return out


@router.get("/api/data/status")
def status():
    sources = []
    for key, info in data.status().items():
        row = {"key": key, **SOURCES[key], "default_url": data.SOURCES[key]["url"], "cached": info}
        if key == "bsdata" and info:
            index = data.datasheet_index() or {"catalogues": {}}
            row["factions"] = sorted(({"faction": c["faction"], "catalogue": n, "units": c["units"]}
                                      for n, c in index["catalogues"].items() if c["faction"]),
                                     key=lambda f: f["faction"])
            row["skipped"] = index.get("skipped", {})
        sources.append(row)
    return {"sources": sources, "force_org": force_org(), "lct": lct(), "job": job}


@router.post("/api/data/fetch")
def fetch(body: dict):
    source = body.get("source")
    if source not in data.SOURCES:
        raise ValueError(f"Unknown source {source!r}")
    url = (body.get("url") or "").strip() or None
    ref = (body.get("ref") or "").strip() or None

    def work():
        data.fetch(source, url, ref, log=log)
        data.IMPORTERS[source](log=log)
    return start(source, work)


@router.get("/api/data/force-org")
def force_org_loaded():
    """Is Force Org the game loaded in TTS? (503 when TTS isn't reachable.)"""
    return {"loaded": bool(army.force_org_tiles(locked_lua))}


@router.post("/api/data/force-org/refresh")
def force_org_refresh():
    return start("force_org", lambda: army.cmd_index(log=log, run_lua=locked_lua))


@router.post("/api/data/force-org/read-mods")
def force_org_from_mods():
    return start("force_org", lambda: mods.read_force_org(log=log))


@router.post("/api/data/lct/read-mods")
def lct_from_mods():
    return start("lct", lambda: mods.read_lct(log=log))


@router.get("/api/data/job")
def current_job():
    return job
