"""Layouts' API: the layouts in layouts/, saving an edit or deleting one (layouts.save /
delete), comparing one with the live table, its diagram from LCT, and importing a new LCT
version: a background job builds it aside (layouts.import_preview, about a minute and a
half), the page shows what would change, and only what the user picks is written.

Saving writes layouts/<id>.json in this folder, to commit in a PR like any other change.
"""

import threading

import data
import layouts
from app.core import lct
from app.core.api import router as api_router

router = api_router()

job_lock = threading.Lock()
job = {"running": False, "log": [], "error": None, "preview": None}


@router.get("/api/layouts")
def listing():
    index = layouts.read_index()
    return {"source": index["source"], "layouts": index["layouts"], "retired": index.get("retired", []),
            "problems": index.get("problems", {}), "tolerance": layouts.TOLERANCE}


@router.get("/api/layouts/import")
def import_state():
    return job


@router.post("/api/layouts/import")
def import_start():
    with job_lock:
        if job["running"]:
            raise ValueError("Already importing; wait for it to finish.")
        job.update(running=True, log=[], error=None, preview=None)

    def run():
        try:
            job["preview"] = layouts.import_preview(log=lambda line: job["log"].append(str(line)))
        except (data.DataError, ValueError) as e:
            job["error"] = str(e)
        except Exception as e:   # the page shows anything else too
            job["error"] = f"{type(e).__name__}: {e}"
        finally:
            job["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return job


@router.post("/api/layouts/import/apply")
def import_apply(body: dict):
    if job["running"]:
        raise ValueError("Still importing; wait for it to finish.")
    done = layouts.import_apply(body.get("ids") or [])
    job["preview"] = None
    return done


@router.get("/api/layouts/{layout_id}")
def one(layout_id: str):
    if not (layouts.LAYOUTS / f"{layout_id}.json").exists():
        raise ValueError(f"No layout {layout_id}.")
    return layouts.load(layout_id)


@router.post("/api/layouts/{layout_id}")
def save(layout_id: str, body: dict):
    if body.get("id") != layout_id:
        raise ValueError("The layout's id doesn't match the address.")
    return layouts.save(body)


@router.delete("/api/layouts/{layout_id}")
def delete(layout_id: str):
    layouts.delete(layout_id)
    return {"deleted": layout_id}


@router.get("/api/layouts/{layout_id}/check")
def check(layout_id: str):
    """Each of the layout's pieces against the live table (TTS, and LCT's cached objects)."""
    rows = layouts.check_layout(layout_id, log=lambda *a: None)
    return {"pieces": rows, "found": sum(r["found"] for r in rows), "ok": sum(r["ok"] for r in rows)}


@router.get("/api/layouts/{layout_id}/art")
def art(layout_id: str):
    """LCT's diagram of the layout's map, when LCT is the game loaded in TTS; else null."""
    lo = layouts.load(layout_id)
    try:
        matchups = lct.lct_matchups()
    except (ValueError, SystemExit):
        return {"url": None}
    return {"url": next((m["art"] for mu in matchups.values() for m in mu["layouts"]
                         if m["card"] == lo["map"] and m.get("art")), None)}
