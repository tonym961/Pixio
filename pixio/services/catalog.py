"""Catalogo ISO: scansione delle sorgenti (share remote + libreria locale), rilevamento tipo, mount/umount, cache locale."""
import logging
import os
import re
import shutil
import threading
import time
import unicodedata

from .. import config as C
from .. import privileged
from .. import settings as S
from ..storage import read_json, write_json, update_json
from . import detect, recipes

log = logging.getLogger("pixio.catalog")
ISO_EXT = (".iso",)
MAX_DEPTH = 4
_scan_lock = threading.Lock()
USER_FIELDS = ("name", "enabled", "group", "order", "custom_recipe", "cache_wanted", "type_override")


def load():
    d = read_json(C.CATALOG_FILE, {})
    d.setdefault("isos", {})
    d.setdefault("last_scan", None)
    return d


def save(d):
    write_json(C.CATALOG_FILE, d)


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def slugify(name):
    base = os.path.splitext(os.path.basename(name))[0]
    s = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    s = s[:48].strip("-") or "iso"
    if not re.match(r"^[a-z0-9]", s):
        s = "i" + s
    return s


def unique_slug(base, existing):
    s, n = base, 2
    while s in existing:
        s = f"{base}-{n}"
        n += 1
    return s


def _sources():
    """[(source_id, source_name, root_dir)] per tutte le sorgenti montate + libreria locale."""
    out = [("local", "Locale", C.LIBRARY_DIR)]
    try:
        from . import sources as SRC
        for s in SRC.list_sources():
            if s.get("mounted"):
                out.append((s["id"], s.get("name") or s["id"], SRC.mountpoint(s["id"])))
    except Exception as e:  # noqa
        log.warning("sorgenti non disponibili: %s", e)
    return out


def _walk_isos(root):
    root = root.rstrip("/")
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count("/") + 1
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in ("$recycle.bin", "system volume information")]
        for fn in filenames:
            if fn.lower().endswith(ISO_EXT) and not fn.startswith("."):
                full = os.path.join(dirpath, fn)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if st.st_size < 1024 * 1024:
                    continue
                yield (fn if rel == "." else f"{rel}/{fn}"), full, st


def scan(job=None):
    """Scansiona tutte le sorgenti. Aggiorna il catalogo; rileva il tipo delle ISO nuove o cambiate."""
    if not _scan_lock.acquire(blocking=False):
        raise RuntimeError("scansione già in corso")
    try:
        cat = load()
        isos = cat["isos"]
        seen = set()
        found = []
        for sid, sname, root in _sources():
            if not os.path.isdir(root):
                continue
            for rel, full, st in _walk_isos(root):
                found.append((sid, sname, rel, full, st))
        total = len(found)
        by_key = {(v.get("source"), v.get("rel_path")): k for k, v in isos.items()}
        todo = []
        for i, (sid, sname, rel, full, st) in enumerate(found):
            key = (sid, rel)
            slug = by_key.get(key)
            if slug is None:
                slug = unique_slug(slugify(rel), isos)
                isos[slug] = {"slug": slug, "source": sid, "rel_path": rel, "file": os.path.basename(rel),
                              "name": os.path.splitext(os.path.basename(rel))[0], "enabled": False, "group": "",
                              "order": 1000 + len(isos), "custom_recipe": None, "cache_wanted": False,
                              "cache": {"status": "none", "path": "", "progress": 0}, "first_seen": now(), "type": "unknown", "detect": {}}
            e = isos[slug]
            seen.add(slug)
            e["source_name"] = sname
            e["path"] = full
            e["last_seen"] = now()
            e["missing"] = False
            changed = (e.get("size") != st.st_size) or (int(e.get("mtime") or 0) != int(st.st_mtime))
            e["size"] = st.st_size
            e["mtime"] = int(st.st_mtime)
            if changed or not e.get("detect") or e.get("detect", {}).get("error"):
                todo.append(slug)
        for slug, e in isos.items():
            if slug not in seen:
                e["missing"] = True
        cat["last_scan"] = now()
        save(cat)
        for i, slug in enumerate(todo):
            if job is not None and getattr(job, "cancelled", False):
                break
            if job is not None:
                job.set_progress(int(100 * i / max(1, len(todo))), f"Rilevamento {isos[slug]['file']} ({i + 1}/{len(todo)})")
            _detect_one(slug)
        if job is not None:
            job.set_progress(100, f"{total} ISO trovate, {len(todo)} analizzate")
        return {"found": total, "detected": len(todo)}
    finally:
        _scan_lock.release()


def _detect_one(slug):
    cat = load()
    e = cat["isos"].get(slug)
    if not e:
        return
    d = detect.detect_file(slug, e["path"])

    def upd(c):
        x = c["isos"].get(slug)
        if not x:
            return c
        x["detect"] = d
        if not x.get("type_override"):
            x["type"] = d.get("type", "unknown")
        if d.get("name") and (x.get("name_auto", True)):
            x["name"] = d["name"]
            x["name_auto"] = True
        if not x.get("group"):
            t = recipes.get_type(x["type"]) or {}
            x["group"] = "Strumenti" if t.get("category") == "tool" else "Sistemi operativi"
        return c
    update_json(C.CATALOG_FILE, upd)


def redetect(slug):
    _detect_one(slug)
    return get(slug)


def register_local_file(path):
    """Dopo un upload: aggiunge/aggiorna la ISO nel catalogo e la rileva. Ritorna slug."""
    rel = os.path.relpath(path, C.LIBRARY_DIR)
    cat = load()
    for k, v in cat["isos"].items():
        if v.get("source") == "local" and v.get("rel_path") == rel:
            slug = k
            break
    else:
        slug = unique_slug(slugify(rel), cat["isos"])
        cat["isos"][slug] = {"slug": slug, "source": "local", "rel_path": rel, "file": os.path.basename(rel),
                             "name": os.path.splitext(os.path.basename(rel))[0], "enabled": False, "group": "",
                             "order": 1000 + len(cat["isos"]), "custom_recipe": None, "cache_wanted": False,
                             "cache": {"status": "none", "path": "", "progress": 0}, "first_seen": now(), "type": "unknown", "detect": {}}
    st = os.stat(path)
    e = cat["isos"][slug]
    e.update({"source_name": "Locale", "path": path, "size": st.st_size, "mtime": int(st.st_mtime), "last_seen": now(), "missing": False})
    save(cat)
    _detect_one(slug)
    return slug


# ---------------------------------------------------------------- lettura
def _mounted_slugs():
    """Slug montati in loop sotto HTTP_ISO_DIR, letti da /proc/self/mounts (nessun privilegio necessario)."""
    prefix = C.HTTP_ISO_DIR.rstrip("/") + "/"
    out = set()
    try:
        with open("/proc/self/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 1 and parts[1].startswith(prefix):
                    slug = parts[1][len(prefix):].split("/")[0]
                    if C.SLUG_RE.match(slug):
                        out.add(slug)
    except OSError:
        pass
    return out


def _decorate(e, mounted):
    t = recipes.get_type(e.get("type") or "unknown") or {}
    e = dict(e)
    e["type_name"] = t.get("name", e.get("type"))
    e["category"] = t.get("category", "unknown")
    custom = e.get("custom_recipe")
    e["platforms"] = (custom.get("platforms") if custom and custom.get("platforms") else t.get("platforms", []))
    e["mounted"] = e["slug"] in mounted
    e.setdefault("cache", {"status": "none", "path": "", "progress": 0})
    w = list(t.get("warnings") or [])
    if e.get("missing"):
        w.append("File non più presente nella sorgente")
    if e.get("detect", {}).get("error"):
        w.append(f"Rilevamento fallito: {e['detect']['error']}")
    if e.get("type") == "unknown" and not custom:
        w.append("Nessuna ricetta di boot: scegli un tipo o una ricetta personalizzata")
    e["warnings"] = w
    return e


def list_isos():
    cat = load()
    mounted = _mounted_slugs()
    out = [_decorate(e, mounted) for e in cat["isos"].values()]
    out.sort(key=lambda x: (x.get("group") or "", x.get("order", 0), (x.get("name") or "").lower()))
    return out, cat.get("last_scan")


def get(slug):
    cat = load()
    e = cat["isos"].get(slug)
    if not e:
        return None
    return _decorate(e, _mounted_slugs())


def summary():
    cat = load()
    isos = cat["isos"]
    mounted = _mounted_slugs()
    return {"total": len(isos), "enabled": sum(1 for e in isos.values() if e.get("enabled")),
            "mounted": len(mounted), "unknown": sum(1 for e in isos.values() if e.get("type") == "unknown" and not e.get("custom_recipe")),
            "last_scan": cat.get("last_scan")}


def types_list():
    return [{"id": t["id"], "name": t["name"], "category": t.get("category"), "platforms": t.get("platforms", [])} for t in recipes.types()]


# ---------------------------------------------------------------- modifiche
def update(slug, patch):
    """Applica le modifiche utente. Gestisce enabled (mount/umount), cache_wanted (job copia), type, nome."""
    cat = load()
    e = cat["isos"].get(slug)
    if not e:
        raise KeyError(slug)
    if "name" in patch:
        e["name"] = str(patch["name"]).strip()[:120] or e["name"]
        e["name_auto"] = False
    if "group" in patch:
        e["group"] = str(patch["group"]).strip()[:60]
    if "order" in patch:
        e["order"] = int(patch["order"])
    if "type" in patch:
        t = patch["type"]
        if t and not recipes.get_type(t):
            raise ValueError("Tipo sconosciuto")
        if t and t != e.get("detect", {}).get("type"):
            e["type_override"] = t
            e["type"] = t
        else:
            e["type_override"] = ""
            e["type"] = e.get("detect", {}).get("type", "unknown")
    if "custom_recipe" in patch:
        cr = patch["custom_recipe"]
        if cr:
            cr = {"kernel": str(cr.get("kernel", ""))[:1000], "initrds": [str(x)[:1000] for x in (cr.get("initrds") or [])][:20],
                  "cmdline": str(cr.get("cmdline", ""))[:4000], "platforms": [p for p in (cr.get("platforms") or ["bios", "efi"]) if p in ("bios", "efi")]}
            for v in [cr["kernel"], cr["cmdline"], *cr["initrds"]]:
                if "\n" in v or "\r" in v:
                    raise ValueError("Le righe della ricetta non possono contenere a capo")
        e["custom_recipe"] = cr or None
    save(cat)
    if "cache_wanted" in patch:
        set_cache(slug, bool(patch["cache_wanted"]))
    if "enabled" in patch:
        set_enabled(slug, bool(patch["enabled"]))
    return get(slug)


def reorder(order):
    cat = load()
    for i, slug in enumerate(order):
        if slug in cat["isos"]:
            cat["isos"][slug]["order"] = i
    save(cat)


def delete_local(slug):
    cat = load()
    e = cat["isos"].get(slug)
    if not e:
        raise KeyError(slug)
    if e.get("source") != "local":
        raise ValueError("Si possono eliminare solo le ISO della libreria locale")
    set_enabled(slug, False)
    set_cache(slug, False)
    try:
        os.unlink(e["path"])
    except FileNotFoundError:
        pass
    cat = load()
    cat["isos"].pop(slug, None)
    save(cat)


def _boot_path(e):
    c = e.get("cache") or {}
    if c.get("status") == "ready" and c.get("path") and os.path.isfile(c["path"]):
        return c["path"]
    return e["path"]


def mount(slug):
    e = load()["isos"].get(slug)
    if not e:
        raise KeyError(slug)
    path = _boot_path(e)
    if not os.path.isfile(path):
        raise FileNotFoundError("File ISO non raggiungibile")
    privileged.call("mount-iso", slug, path, timeout=120)
    write_inject_files(e)


def umount(slug):
    privileged.call("umount-iso", slug, timeout=60)
    d = os.path.join(C.HTTP_INJECT_DIR, slug)
    shutil.rmtree(d, ignore_errors=True)


def set_enabled(slug, enabled):
    def upd(c):
        if slug in c["isos"]:
            c["isos"][slug]["enabled"] = enabled
        return c
    update_json(C.CATALOG_FILE, upd)
    if enabled:
        mount(slug)
    else:
        umount(slug)


def remount_enabled():
    """All'avvio: rimonta le ISO abilitate che non risultano montate."""
    cat = load()
    mounted = _mounted_slugs()
    errors = {}
    for slug, e in cat["isos"].items():
        if e.get("enabled") and slug not in mounted:
            try:
                mount(slug)
            except Exception as ex:  # noqa
                errors[slug] = str(ex)
                log.warning("rimontaggio %s: %s", slug, ex)
    return errors


def write_inject_files(e):
    """File iniettati nel WinPE (wimboot): winpeshl.ini + install.cmd (solo se export SMB attivo)."""
    slug = e["slug"]
    d = os.path.join(C.HTTP_INJECT_DIR, slug)
    cfg = S.load()
    if e.get("type") != "windows" or not cfg["windows"].get("smb_export_enabled"):
        shutil.rmtree(d, ignore_errors=True)
        return
    ip = cfg["network"]["server_ip"]
    wuser = cfg["windows"].get("smb_user") or "pxe"
    wpass = cfg["windows"].get("smb_password") or ""
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "winpeshl.ini"), "w", newline="\r\n") as f:
        f.write("[LaunchApps]\n\"install.cmd\"\n")
    cmd = f"""@echo off
title Pixio - installazione Windows
wpeinit
set /a tries=0
:retry
set /a tries+=1
net use S: \\\\{ip}\\pxe\\{slug} {wpass} /user:{wuser} /persistent:no >nul 2>&1 && goto ok
if %tries% GEQ 30 goto fail
echo In attesa della rete (%tries%/30)...
ping -n 3 127.0.0.1 >nul
goto retry
:ok
echo Avvio setup.exe da \\\\{ip}\\pxe\\{slug}
S:\\setup.exe
goto end
:fail
echo Impossibile raggiungere \\\\{ip}\\pxe\\{slug}. Apro il prompt.
cmd.exe
:end
"""
    with open(os.path.join(d, "install.cmd"), "w", newline="\r\n") as f:
        f.write(cmd)


# ---------------------------------------------------------------- cache locale
def set_cache(slug, wanted):
    cat = load()
    e = cat["isos"].get(slug)
    if not e:
        raise KeyError(slug)
    e["cache_wanted"] = wanted
    save(cat)
    if wanted:
        if e.get("source") == "local":
            return None
        if (e.get("cache") or {}).get("status") in ("ready", "copying"):
            return None
        from . import jobs
        return jobs.start("copy", slug, lambda job: _copy_job(job, slug), message=f"Copia locale di {e['file']}")
    else:
        c = e.get("cache") or {}
        if c.get("path") and os.path.exists(c["path"]):
            try:
                os.unlink(c["path"])
            except OSError:
                pass
        _set_cache_state(slug, "none", "", 0)
        if e.get("enabled"):
            try:
                mount(slug)
            except Exception as ex:  # noqa
                log.warning("remount dopo uncache %s: %s", slug, ex)
        return None


def _set_cache_state(slug, status, path, progress, error=""):
    def upd(c):
        if slug in c["isos"]:
            c["isos"][slug]["cache"] = {"status": status, "path": path, "progress": progress, "error": error}
        return c
    update_json(C.CATALOG_FILE, upd)


def _copy_job(job, slug):
    e = load()["isos"].get(slug)
    if not e:
        return
    os.makedirs(C.CACHE_DIR, exist_ok=True)
    dst = os.path.join(C.CACHE_DIR, f"{slug}.iso")
    tmp = dst + ".part"
    total = e.get("size") or 1
    free = shutil.disk_usage(C.CACHE_DIR).free
    if free < total + (1 << 30):
        _set_cache_state(slug, "error", "", 0, "Spazio su disco insufficiente")
        raise RuntimeError("Spazio su disco insufficiente per la copia locale")
    _set_cache_state(slug, "copying", dst, 0)
    done = 0
    last = 0
    try:
        with open(e["path"], "rb") as src, open(tmp, "wb") as out:
            while True:
                if job.cancelled:
                    raise RuntimeError("annullato")
                buf = src.read(8 << 20)
                if not buf:
                    break
                out.write(buf)
                done += len(buf)
                pct = int(100 * done / total)
                if pct != last:
                    last = pct
                    job.set_progress(pct, f"{done >> 20} / {total >> 20} MB")
                    _set_cache_state(slug, "copying", dst, pct)
        os.replace(tmp, dst)
        _set_cache_state(slug, "ready", dst, 100)
        if load()["isos"].get(slug, {}).get("enabled"):
            mount(slug)
    except Exception as ex:  # noqa
        try:
            os.unlink(tmp)
        except OSError:
            pass
        _set_cache_state(slug, "error", "", 0, str(ex))
        raise
