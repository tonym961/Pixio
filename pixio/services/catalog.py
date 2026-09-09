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


def _new_entry(slug, sid, rel, n):
    return {"slug": slug, "source": sid, "rel_path": rel, "file": os.path.basename(rel),
            "name": os.path.splitext(os.path.basename(rel))[0], "enabled": False, "group": "",
            "order": 1000 + n, "custom_recipe": None, "cache_wanted": False,
            "cache": {"status": "none", "path": "", "progress": 0}, "first_seen": now(), "type": "unknown", "detect": {}}


def scan(job=None):
    """Scansiona tutte le sorgenti montate. Il catalogo viene aggiornato con UNA update_json (merge per sorgente+percorso):
    le modifiche fatte dall'utente durante la scansione non vengono perse. Poi rileva il tipo delle ISO nuove o cambiate."""
    if not _scan_lock.acquire(blocking=False):
        raise RuntimeError("scansione già in corso")
    try:
        scanned = {}                      # sorgenti effettivamente lette: solo le loro ISO possono diventare 'missing'
        found = []
        for sid, sname, root in _sources():
            if not os.path.isdir(root):
                continue
            scanned[sid] = sname
            for rel, full, st in _walk_isos(root):
                found.append((sid, sname, rel, full, st))
        todo = []

        def merge(cat):
            cat.setdefault("isos", {})
            isos = cat["isos"]
            by_key = {(v.get("source"), v.get("rel_path")): k for k, v in isos.items()}
            by_sig = {(v.get("source"), v.get("size"), int(v.get("mtime") or 0), v.get("file")): k for k, v in isos.items()}
            seen = set()
            for sid, sname, rel, full, st in found:
                slug = by_key.get((sid, rel))
                if slug is None:
                    # file rinominato/spostato dentro la stessa sorgente: stessa dimensione, mtime e nome
                    cand = by_sig.get((sid, st.st_size, int(st.st_mtime), os.path.basename(rel)))
                    if cand is not None and cand not in seen and isos[cand].get("missing", False) is not None:
                        slug = cand
                if slug is None:
                    slug = unique_slug(slugify(rel), isos)
                    isos[slug] = _new_entry(slug, sid, rel, len(isos))
                e = isos[slug]
                seen.add(slug)
                changed = (e.get("size") != st.st_size) or (int(e.get("mtime") or 0) != int(st.st_mtime)) or (e.get("rel_path") != rel)
                e.update({"source_name": sname, "rel_path": rel, "file": os.path.basename(rel), "path": full,
                          "size": st.st_size, "mtime": int(st.st_mtime), "last_seen": now(), "missing": False})
                d = e.get("detect") or {}
                if changed or not d or (d.get("error") and int(e.get("detect_attempts") or 0) < 3):
                    todo.append(slug)
            for slug, e in isos.items():
                if slug not in seen and e.get("source") in scanned:
                    e["missing"] = True
            cat["last_scan"] = now()
            return cat
        update_json(C.CATALOG_FILE, merge, default={})
        total = len(found)
        for i, slug in enumerate(todo):
            if job is not None and getattr(job, "cancelled", False):
                break
            e = load()["isos"].get(slug)
            if not e:
                continue
            if job is not None:
                job.set_progress(int(100 * i / max(1, len(todo))), f"Rilevamento {e['file']} ({i + 1}/{len(todo)})")
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
        x["detect_attempts"] = (int(x.get("detect_attempts") or 0) + 1) if d.get("error") else 0
        if not x.get("type_override"):
            x["type"] = d.get("type", "unknown")
        if d.get("name") and (x.get("name_auto", True)):
            x["name"] = d["name"]
            x["name_auto"] = True
        if not x.get("group") or x.get("group_auto", True):
            x["group"] = auto_group(x)
            x["group_auto"] = True
        return c
    update_json(C.CATALOG_FILE, upd)


SERVER_RE = re.compile(r"\bserver\b|\bsbs\b|small business|\bhyper-v\b", re.I)


def auto_group(e):
    """Gruppo predefinito per una ISO: dal tipo di ricetta, separando Windows client e Windows Server."""
    t = e.get("type") or "unknown"
    g = recipes.type_group(t)
    if t in ("windows", "windows-legacy"):
        d = e.get("detect") or {}
        text = " ".join([str(d.get("name") or ""), " ".join(d.get("editions") or []), str(e.get("file") or "")])
        return "Windows Server" if SERVER_RE.search(text) else "Windows"
    return g


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
        cat["isos"][slug] = _new_entry(slug, "local", rel, len(cat["isos"]))
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


def _decorate(e, mounted, idx=None):
    t = recipes.get_type(e.get("type") or "unknown") or {}
    e = dict(e)
    e["type_name"] = t.get("name", e.get("type"))
    e["category"] = t.get("category", "unknown")
    custom = e.get("custom_recipe")
    e["platforms"] = (custom.get("platforms") if custom and custom.get("platforms") else t.get("platforms", []))
    e["mounted"] = e["slug"] in mounted
    info = answers_info(e, answers_index() if idx is None else idx)
    e["answers"] = [i["id"] for i in info]        # solo quelle ancora esistenti
    e["answers_info"] = info
    e["answer_manual"] = answer_manual(e)
    e["answer_id"] = default_answer(e, info)
    if e["answer_id"]:
        e["answer_name"] = next(i["name"] for i in info if i["id"] == e["answer_id"])
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
    idx = answers_index()                          # una lettura sola per tutto il catalogo
    out = [_decorate(e, mounted, idx) for e in cat["isos"].values()]
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


# ---------------------------------------------------------------- risposte collegate (docs/API.md sezione 17)
MAX_ANSWERS = 8          # quante risposte si possono collegare alla stessa ISO


def answers_of(e):
    """Id delle risposte collegate alla ISO, in ordine e senza doppioni.

    Migrazione implicita: un catalogo scritto prima della sezione 17 ha solo `answer_id`, che vale
    come elenco di una sola risposta. Nessuna riscrittura del file: la conversione avviene in lettura."""
    ids = []
    for a in (e.get("answers") or []):
        a = str(a or "").strip()
        if a and a not in ids:
            ids.append(a)
    if not ids and e.get("answer_id"):
        ids = [str(e["answer_id"]).strip()]
    return ids[:MAX_ANSWERS]


def answers_index():
    """{id: {id, name, kind}} di tutte le risposte esistenti. Letto una volta sola per tutto il catalogo."""
    try:
        from . import answers as A
        return A.index()
    except Exception:  # noqa: BLE001
        return {}


def answers_info(e, idx=None):
    """Risposte collegate che esistono ancora: [{id, name, kind}] nell'ordine scelto.

    Se una risposta viene cancellata dalla pagina Risposte la voce sparisce e basta: la ISO resta avviabile."""
    idx = answers_index() if idx is None else idx
    return [idx[a] for a in answers_of(e) if a in idx]


def default_answer(e, info=None, idx=None):
    """Id della predefinita: `answer_id` se ancora collegato ed esistente, altrimenti la prima disponibile."""
    info = answers_info(e, idx) if info is None else info
    ids = [i["id"] for i in info]
    aid = str(e.get("answer_id") or "").strip()
    if aid and aid in ids:
        return aid
    return ids[0] if ids else None


def answer_manual(e):
    """Vero se al boot va offerta anche l'installazione guidata a mano (avvio senza risposta)."""
    return bool(e.get("answer_manual", True))


def resolve_answer(e, requested=None, idx=None):
    """Risposta da usare per questo avvio.

    requested None = la predefinita; "" = nessuna (installazione a mano);
    id non collegato alla ISO o cancellato = si ripiega sulla predefinita."""
    info = answers_info(e, idx)
    if requested is None:
        return default_answer(e, info)
    requested = str(requested).strip()
    if not requested:
        return None
    if any(i["id"] == requested for i in info):
        return requested
    return default_answer(e, info)


def detach_answer(answer_id):
    """Toglie una risposta cancellata da tutte le ISO che la usavano, sistemando la predefinita."""
    aid = str(answer_id or "").strip()
    if not aid:
        return
    cat = load()
    changed = False
    for e in cat["isos"].values():
        if not isinstance(e, dict):
            continue
        prima = answers_of(e)
        dopo = [a for a in prima if a != aid]
        if dopo == prima and e.get("answer_id") != aid:
            continue
        e["answers"] = dopo
        if e.get("answer_id") == aid or (e.get("answer_id") and e["answer_id"] not in dopo):
            e["answer_id"] = dopo[0] if dopo else None
        changed = True
    if changed:
        save(cat)


def _check_answer_ids(ids):
    """Valida un elenco di id di risposta: esistenti, senza doppioni, entro il massimo."""
    if len(ids) > MAX_ANSWERS:
        raise ValueError(f"Al massimo {MAX_ANSWERS} risposte per la stessa ISO")
    idx = answers_index()
    out = []
    for a in ids:
        aid = str(a or "").strip()[:64]
        if not aid or aid in out:
            continue
        if aid not in idx:
            raise ValueError(f"Risposta non trovata: {aid}")
        out.append(aid)
    return out


def _apply_answers_patch(e, patch):
    """Applica answers / answer_id / answer_manual all'entry, con la predefinita sempre coerente."""
    if "answer_manual" in patch:
        e["answer_manual"] = bool(patch["answer_manual"])
    if "answers" not in patch and "answer_id" not in patch:
        return
    if "answers" in patch:
        if not isinstance(patch["answers"], list):
            raise ValueError("Le risposte collegate devono essere un elenco di identificativi")
        e["answers"] = _check_answer_ids(patch["answers"])
    else:
        e["answers"] = answers_of(e)          # migrazione implicita prima di toccare la predefinita
    if "answer_id" in patch:
        aid = str(patch["answer_id"] or "").strip()[:64] or None
        if not aid and "answers" not in patch:
            e["answers"] = []          # come prima della sezione 17: answer_id vuoto = nessuna automatica
        if aid:
            _check_answer_ids([aid])
        if aid and "answers" in patch and aid not in e["answers"]:
            raise ValueError("La risposta predefinita deve essere fra quelle collegate")
        if aid and aid not in e["answers"]:
            e["answers"] = ([aid] + e["answers"])[:MAX_ANSWERS]     # PATCH del solo answer_id: la collega
        e["answer_id"] = aid
    if e.get("answer_id") not in e["answers"]:
        e["answer_id"] = e["answers"][0] if e["answers"] else None


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
        e["group_auto"] = False        # scelta dell'utente: le scansioni successive non la toccano
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
    _apply_answers_patch(e, patch)
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
    if e.get("type") == "esxi":
        from . import esxi
        try:
            ok, msg = esxi.prepare(slug, os.path.join(C.HTTP_ISO_DIR, slug), S.load()["network"]["server_ip"],
                                   (e.get("detect") or {}).get("files"))
            if not ok:
                log.warning("ESXi %s: %s", slug, msg)
        except Exception as ex:  # noqa
            log.warning("preparazione ESXi %s: %s", slug, ex)


def umount(slug):
    privileged.call("umount-iso", slug, timeout=60)
    d = os.path.join(C.HTTP_INJECT_DIR, slug)
    shutil.rmtree(d, ignore_errors=True)


def set_enabled(slug, enabled):
    if enabled:
        mount(slug)                 # se fallisce l'eccezione arriva alla API e la voce resta disabilitata

    def upd(c):
        if slug in c["isos"]:
            c["isos"][slug]["enabled"] = enabled
        return c
    update_json(C.CATALOG_FILE, upd)
    if not enabled:
        try:
            umount(slug)
        except privileged.HelperError as ex:
            log.warning("umount %s: %s", slug, ex)


def forget_source(sid):
    """Rimuove dal catalogo tutte le ISO di una sorgente eliminata (smontando quelle montate)."""
    cat = load()
    victims = [k for k, v in cat["isos"].items() if v.get("source") == sid]
    for slug in victims:
        try:
            umount(slug)
        except privileged.HelperError as ex:
            log.warning("umount %s: %s", slug, ex)

    def upd(c):
        for slug in victims:
            c["isos"].pop(slug, None)
        return c
    update_json(C.CATALOG_FILE, upd)
    return len(victims)


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
    """Compatibilita': i file WinPE (winpeshl.ini, install.cmd) sono ora generati al volo da /boot/inject/<slug>/."""
    shutil.rmtree(os.path.join(C.HTTP_INJECT_DIR, e["slug"]), ignore_errors=True)


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
