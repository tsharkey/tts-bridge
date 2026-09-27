"""
data.py — the local data cache: community data fetched once, kept in cache/
(git-ignored), and used offline after that.

    python3 data.py fetch bsdata                    # fetch BSData/wh40k-11e and import it
    python3 data.py fetch bsdata --from <link>      # a fork or other GitHub repo (downloaded as a zip)
    python3 data.py fetch bsdata --ref <commit>     # a branch, tag or commit
    python3 data.py fetch bsdata --from <folder>    # a local checkout, read in place
    python3 data.py fetch bsdata --from <git url>   # any other git remote (needs git)
    python3 data.py fetch wahapedia                 # Wahapedia's data export: base sizes (see bases.py)
    python3 data.py import bsdata                   # re-import what's cached, no network
    python3 data.py status                          # what's cached, from where, and when
    python3 data.py mods [forceorg|lct]             # read Force Org and LCT from TTS's mod files (see mods.py)

A source's link and ref are remembered, so a plain `fetch` refreshes the same
one. Imported datasheets are in cache/datasheets/, one file per catalogue, in
the format described in docs/formats/datasheet.md.

    import data
    units = data.datasheets("T'au Empire")          # every datasheet that army can take
"""

import io
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = ROOT / "cache"
SOURCES = {"bsdata": {"url": "https://github.com/BSData/wh40k-11e", "ref": None},
           "wahapedia": {"url": "https://wahapedia.ru/wh40k11ed", "ref": None}}
# army.FACTIONS names that BSData spells differently
FACTION_ALIASES = {"Imperial Agents": "Agents of the Imperium"}


class DataError(Exception):
    """A fetch or import that can't go ahead, with a message for the user."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]', "", s).strip()


def github_repo(url):
    """"https://github.com/BSData/wh40k-11e(.git)" -> ("BSData", "wh40k-11e"), else None."""
    m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([^/]+)/([^/#?]+?)(?:\.git)?/?$", url.strip())
    return (m.group(1), m.group(2)) if m else None


def get(url, timeout=60, **headers):
    req = urllib.request.Request(url, headers={"User-Agent": "tts-bridge", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise DataError(f"Not found: {url}. Check the link and the ref.") from e
        raise DataError(f"{url}: {e.code} {e.reason}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise DataError(f"Couldn't reach {url} ({getattr(e, 'reason', e)}). Check the link and your connection.") from e


# --------------------------------------------------------------------------
# Fetching: a source's files land in cache/<source>/raw, with source.json
# saying where they came from.

def source_dir(name, cache=None):
    cache = cache or CACHE
    return cache / name


def source_info(name, cache=None):
    cache = cache or CACHE
    return read_json(source_dir(name, cache) / "source.json")


def raw_dir(name, cache=None):
    """Where a source's files are: the local folder it was fetched from, or its copy in the cache."""
    cache = cache or CACHE
    info = source_info(name, cache) or {}
    return Path(info["path"]) if info.get("kind") == "folder" else source_dir(name, cache) / "raw"


def fetch(name, url=None, ref=None, cache=None, log=print):
    """Fetch a source into the cache. url/ref default to what was fetched last,
    then to the source's default. -> the new source.json content."""
    cache = cache or CACHE
    if name not in SOURCES:
        raise DataError(f"Unknown source {name!r}. Known: {', '.join(SOURCES)}")
    last = source_info(name, cache) or {}
    if url is None:
        url = last.get("url") or last.get("path") or SOURCES[name]["url"]
        ref = ref or last.get("ref") or SOURCES[name]["ref"]
    folder = Path(url).expanduser()
    if folder.is_dir():
        info = {"kind": "folder", "path": str(folder.resolve()), "ref": None, "commit": git_commit(folder)}
        log(f"{name}: reading {info['path']} in place")
    elif name == "wahapedia" and re.match(r"^https?://", url):
        info = fetch_export(name, url, last, cache, log)
    elif github_repo(url):
        info = fetch_github(name, url, ref, last, cache, log)
    elif re.match(r"^(https?|ssh|git|file)://|^[\w.-]+@[\w.-]+:", url):
        info = fetch_git(name, url, ref, cache, log)
    else:
        raise DataError(f"{url} isn't a folder, a GitHub link or a git remote.")
    info = {"source": name, **info, "fetched": now()}
    write_json(source_dir(name, cache) / "source.json", info)
    return info


def fetch_github(name, url, ref, last, cache, log):
    owner, repo = github_repo(url)
    url = f"https://github.com/{owner}/{repo}"
    raw = source_dir(name, cache) / "raw"
    try:
        commit = get(f"https://api.github.com/repos/{owner}/{repo}/commits/{ref or 'HEAD'}", timeout=20,
                     Accept="application/vnd.github.sha").decode().strip()
    except DataError:
        commit = None  # the API is rate-limited; the zip still says which commit it holds
    if commit and commit == last.get("commit") and last.get("url") == url and raw.is_dir():
        log(f"{name}: {owner}/{repo} is unchanged at {commit[:10]}")
        return {k: last[k] for k in ("kind", "url", "ref", "commit")}
    log(f"{name}: downloading {owner}/{repo} at {ref or 'its default branch'}...")
    body = get(f"https://codeload.github.com/{owner}/{repo}/zip/{ref or 'HEAD'}", timeout=300)
    try:
        z = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as e:
        raise DataError(f"{url} at {ref or 'HEAD'} didn't download as a zip.") from e
    commit = z.comment.decode(errors="ignore").strip() or commit
    new = raw.with_name("raw.new")
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir(parents=True)
    count = 0
    for item in z.infolist():
        parts = item.filename.split("/")
        if len(parts) == 2 and parts[1].endswith(".json"):  # top-level files only
            (new / parts[1]).write_bytes(z.read(item))
            count += 1
    if not count:
        shutil.rmtree(new)
        raise DataError(f"{url} has no .json catalogues at its top level. Is it a BSData repo?")
    shutil.rmtree(raw, ignore_errors=True)
    new.rename(raw)
    log(f"{name}: {count} files, commit {commit[:10] if commit else 'unknown'}")
    return {"kind": "github", "url": url, "ref": ref, "commit": commit}


def fetch_export(name, url, last, cache, log):
    """Wahapedia's CSV export: a few files at one address. Last_update.csv says
    when it last changed, so an unchanged export isn't downloaded again."""
    from bases import FILES

    url = url.rstrip("/")
    raw = source_dir(name, cache) / "raw"
    stamp = get(f"{url}/Last_update.csv", timeout=30).decode("utf-8-sig", "replace")
    updated = next((ln.strip(" |\r") for ln in stamp.splitlines()[1:] if ln.strip(" |\r")), None)
    if updated and updated == last.get("commit") and last.get("url") == url and raw.is_dir():
        log(f"{name}: unchanged since {updated}")
        return {k: last[k] for k in ("kind", "url", "ref", "commit")}
    new = raw.with_name("raw.new")
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir(parents=True)
    for f in FILES:
        log(f"{name}: downloading {f}...")
        body = get(f"{url}/{f}", timeout=120)
        if body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
            shutil.rmtree(new)
            raise DataError(f"{url}/{f} is a web page, not a CSV. Is {url} a Wahapedia export?")
        (new / f).write_bytes(body)
    shutil.rmtree(raw, ignore_errors=True)
    new.rename(raw)
    log(f"{name}: export of {updated or 'unknown date'}")
    return {"kind": "export", "url": url, "ref": None, "commit": updated}


def fetch_git(name, url, ref, cache, log):
    if not shutil.which("git"):
        raise DataError("Fetching from a git remote needs git installed. A GitHub link works without it.")
    raw = source_dir(name, cache) / "raw"

    def git(*args, cwd=None):
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        if r.returncode:
            raise DataError(f"git {args[0]} failed: {r.stderr.strip()}")
        return r.stdout.strip()

    if (raw / ".git").is_dir() and git("remote", "get-url", "origin", cwd=raw) == url:
        log(f"{name}: updating {url}...")
        git("fetch", "--depth", "1", "origin", ref or "HEAD", cwd=raw)
        git("checkout", "--quiet", "FETCH_HEAD", cwd=raw)
    else:
        log(f"{name}: cloning {url}...")
        shutil.rmtree(raw, ignore_errors=True)
        raw.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "--quiet", "--depth", "1", *(["--branch", ref] if ref else []), url, str(raw))
    return {"kind": "git", "url": url, "ref": ref, "commit": git("rev-parse", "HEAD", cwd=raw)}


def git_commit(folder):
    if not (folder / ".git").exists() or not shutil.which("git"):
        return None
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=folder, capture_output=True, text=True)
    return r.stdout.strip() or None


# --------------------------------------------------------------------------
# Importing into our own formats.

def datasheet_dir(cache=None):
    cache = cache or CACHE
    return cache / "datasheets"


def import_bsdata(cache=None, log=print):
    """Import the cached BSData files into cache/datasheets/. -> the index."""
    cache = cache or CACHE
    import bsdata

    info = source_info("bsdata", cache)
    folder = raw_dir("bsdata", cache)
    if not info or not folder.is_dir():
        raise DataError("BSData isn't fetched yet. Run `python3 data.py fetch bsdata`.")
    try:
        catalogues, skipped = bsdata.import_folder(folder)
    except ValueError as e:
        raise DataError(str(e)) from e
    out = datasheet_dir(cache)
    new = out.with_name("datasheets.new")
    shutil.rmtree(new, ignore_errors=True)
    index = {"source": info, "imported": now(), "skipped": dict(skipped.most_common()), "catalogues": {}}
    for name, cat in sorted(catalogues.items()):
        file = f"{safe_name(name)}.json"
        write_json(new / file, cat)
        index["catalogues"][name] = {"file": file, "faction": cat["faction"], "library": cat["library"],
                                     "imports": cat["imports"], "units": len(cat["units"])}
    write_json(new / "index.json", index)
    shutil.rmtree(out, ignore_errors=True)
    new.rename(out)
    units = sum(c["units"] for c in index["catalogues"].values())
    factions = sum(1 for c in index["catalogues"].values() if c["faction"])
    log(f"bsdata: {units} datasheets in {len(catalogues)} catalogues ({factions} factions)")
    return index


def import_wahapedia(cache=None, log=print):
    import bases
    return bases.import_wahapedia(cache, log)


IMPORTERS = {"bsdata": import_bsdata, "wahapedia": import_wahapedia}


# --------------------------------------------------------------------------
# Reading the cache.

def datasheet_index(cache=None):
    cache = cache or CACHE
    return read_json(datasheet_dir(cache) / "index.json")


def load_catalogue(name, cache=None):
    cache = cache or CACHE
    index = datasheet_index(cache) or {"catalogues": {}}
    entry = index["catalogues"].get(name)
    return read_json(datasheet_dir(cache) / entry["file"]) if entry else None


def faction_catalogue(faction, cache=None):
    """The catalogue name for an army.FACTIONS faction, or None."""
    cache = cache or CACHE
    faction = FACTION_ALIASES.get(faction, faction)
    index = datasheet_index(cache) or {"catalogues": {}}
    return next((n for n, c in index["catalogues"].items() if c["faction"] == faction), None)


def datasheets(faction, cache=None):
    """Every datasheet an army of this faction can take: its own catalogue's,
    then those of the catalogues it imports (a chapter imports Space Marines).
    Each unit's "catalogue" says where it came from. [] if nothing is cached."""
    cache = cache or CACHE
    name = faction_catalogue(faction, cache)
    out, seen, todo, done = [], set(), [name] if name else [], set()
    while todo:
        cat = load_catalogue(todo.pop(0), cache)
        if not cat or cat["catalogue"] in done:
            continue
        done.add(cat["catalogue"])
        for u in cat["units"]:
            if u["id"] not in seen:
                seen.add(u["id"])
                out.append(u)
        todo += cat["imports"]
    return out


def status(cache=None):
    """-> {source: {...source.json, "datasheets": n, "catalogues": n}} for what's cached."""
    cache = cache or CACHE
    out = {}
    for name in SOURCES:
        info = source_info(name, cache)
        if not info:
            out[name] = None
            continue
        out[name] = dict(info)
        if name == "bsdata" and datasheet_index(cache):
            index = datasheet_index(cache)
            out[name]["imported"] = index["imported"]
            out[name]["catalogues"] = len(index["catalogues"])
            out[name]["datasheets"] = sum(c["units"] for c in index["catalogues"].values())
        if name == "wahapedia":
            import bases
            index = bases.load(cache)
            if index:
                out[name]["imported"] = index["imported"]
                out[name]["datasheets"] = sum(len(f) for f in index["factions"].values())
                out[name]["models"] = sum(len(s["lines"]) for f in index["factions"].values() for s in f.values())
    return out


# --------------------------------------------------------------------------

def main(args):
    opts = {}
    rest = []
    it = iter(args)
    for a in it:
        if a in ("--from", "--ref"):
            opts[a[2:]] = next(it, None)
        else:
            rest.append(a)
    cmd, name = (rest + [None, None])[:2]
    try:
        if cmd == "fetch" and name:
            fetch(name, opts.get("from"), opts.get("ref"))
            IMPORTERS[name]()
        elif cmd == "import" and name in IMPORTERS:
            IMPORTERS[name]()
        elif cmd == "mods":
            import mods
            mods.main(rest[1:])
        elif cmd == "status":
            for src, info in status().items():
                if not info:
                    print(f"{src}: not fetched (python3 data.py fetch {src})")
                    continue
                where = info.get("url") or info.get("path")
                at = f"export of {info['commit']}" if info.get("kind") == "export" else info.get("ref") or "default branch"
                commit = "" if info.get("kind") == "export" else (info.get("commit") or "")[:10]
                print(f"{src}: {where} @ {' '.join(filter(None, [at, commit]))}, fetched {info['fetched']}")
                if "models" in info:
                    print(f"  base sizes for {info['models']} models in {info['datasheets']} datasheets, "
                          f"imported {info['imported']}")
                elif "datasheets" in info:
                    print(f"  {info['datasheets']} datasheets in {info['catalogues']} catalogues, imported {info['imported']}")
        else:
            sys.exit(__doc__)
    except DataError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main(sys.argv[1:])
