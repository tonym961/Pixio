"""Copia locale automatica delle ISO che stanno su share remote (cache in SRV/cache).

Leggere una ISO in loop da una share CIFS è lento e fragile: le ISO grandi conviene copiarle
sul disco locale. Questo servizio decide da solo cosa copiare e cosa buttare via:
- candidate: ISO di una sorgente remota, più grandi di `min_size_gb`, (se `only_enabled`) abilitate
  nel menu e non ancora in cache;
- da eliminare: le copie usate meno di recente (ultimo avvio registrato dai client PXE, altrimenti
  data del file in cache), quel tanto che basta a lasciare liberi `keep_free_gb`.

Impostazioni in config.json -> `cache` (auto, min_size_gb, only_enabled, keep_free_gb).
Il thread di background chiama run() quando `auto` è attivo; la GUI usa /api/cache.
"""
import datetime
import logging
import os
import shutil
import threading
import time

from .. import config as C
from .. import settings as S
from ..storage import read_json, update_json
from . import catalog, jobs

log = logging.getLogger("pixio.autocache")

GB = 1 << 30
DEFAULTS = {"auto": False, "min_size_gb": 2, "only_enabled": True, "keep_free_gb": 20}
MIN_SIZE_RANGE = (0.1, 100)       # GB
KEEP_FREE_RANGE = (1, 500)        # GB
MAX_COPY_WAIT = 24 * 3600         # attesa massima di una singola copia (s)
POLL = 0.5

_run_lock = threading.Lock()


# ---------------------------------------------------------------- impostazioni
def _bool(v, label):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.lower() in ("true", "false", "1", "0", "on", "off"):
        return v.lower() in ("true", "1", "on")
    raise ValueError(f"{label}: valore booleano atteso")


def _num(v, label, lo, hi):
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: numero atteso")
    if n != n or n < lo or n > hi:
        raise ValueError(f"{label}: valore fuori intervallo ({lo}-{hi})")
    return int(n) if n == int(n) else round(n, 2)


def current_settings():
    """Impostazioni `cache` con i valori predefiniti e i limiti applicati."""
    raw = S.load().get("cache") or {}
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in raw:
            out[k] = raw[k]
    try:
        out["auto"] = _bool(out["auto"], "auto")
    except ValueError:
        out["auto"] = DEFAULTS["auto"]
    try:
        out["only_enabled"] = _bool(out["only_enabled"], "only_enabled")
    except ValueError:
        out["only_enabled"] = DEFAULTS["only_enabled"]
    try:
        out["min_size_gb"] = _num(out["min_size_gb"], "min_size_gb", *MIN_SIZE_RANGE)
    except ValueError:
        out["min_size_gb"] = DEFAULTS["min_size_gb"]
    try:
        out["keep_free_gb"] = _num(out["keep_free_gb"], "keep_free_gb", *KEEP_FREE_RANGE)
    except ValueError:
        out["keep_free_gb"] = DEFAULTS["keep_free_gb"]
    return out


def validate(incoming):
    """Valida i campi presenti; ValueError con messaggio in italiano se qualcosa non va."""
    if not isinstance(incoming, dict):
        raise ValueError("Corpo della richiesta non valido")
    clean = {}
    if "auto" in incoming:
        clean["auto"] = _bool(incoming["auto"], "Copia automatica")
    if "only_enabled" in incoming:
        clean["only_enabled"] = _bool(incoming["only_enabled"], "Solo le ISO abilitate")
    if "min_size_gb" in incoming:
        clean["min_size_gb"] = _num(incoming["min_size_gb"], "Dimensione minima (GB)", *MIN_SIZE_RANGE)
    if "keep_free_gb" in incoming:
        clean["keep_free_gb"] = _num(incoming["keep_free_gb"], "Spazio da lasciare libero (GB)", *KEEP_FREE_RANGE)
    if not clean:
        raise ValueError("Nessuna impostazione da modificare (auto, min_size_gb, only_enabled, keep_free_gb)")
    return clean


def save_settings(incoming):
    clean = validate(incoming)

    def upd(cfg):
        cfg.setdefault("cache", {}).update(clean)
        return cfg
    update_json(C.CONFIG_FILE, upd, default={}, mode=0o640)
    return current_settings()


# ---------------------------------------------------------------- utilità
def free_bytes():
    """Spazio libero sul filesystem che ospita le copie locali."""
    for p in (C.CACHE_DIR, C.SRV_DIR, "/"):
        if p and os.path.isdir(p):
            try:
                return shutil.disk_usage(p).free
            except OSError:
                continue
    return 0


def _epoch(value):
    if not value:
        return 0.0
    try:
        return datetime.datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _boot_times():
    """slug -> ultimo avvio registrato dai client PXE (last_entry + last_seen)."""
    out = {}
    data = read_json(C.CLIENTS_FILE, {})
    if not isinstance(data, dict):
        return out
    for c in data.values():
        if not isinstance(c, dict):
            continue
        slug = c.get("last_entry")
        if not slug:
            continue
        t = _epoch(c.get("last_seen"))
        if t > out.get(slug, 0.0):
            out[slug] = t
    return out


def _cache_path(e):
    p = (e.get("cache") or {}).get("path") or ""
    return p if isinstance(p, str) else ""


def _copy_size(e):
    """Byte occupati dalla copia locale (dimensione reale se il file c'è)."""
    p = _cache_path(e)
    if p:
        try:
            return os.path.getsize(p)
        except OSError:
            pass
    try:
        return int(e.get("size") or 0)
    except (TypeError, ValueError):
        return 0


def _last_use(slug, e, boots):
    t = boots.get(slug)
    if t:
        return t
    p = _cache_path(e)
    if p:
        try:
            return os.path.getmtime(p)
        except OSError:
            pass
    return 0.0


def _size(e):
    try:
        return int(e.get("size") or 0)
    except (TypeError, ValueError):
        return 0


def is_candidate(e, cfg, min_bytes=None):
    """ISO da copiare in locale: sorgente remota, grande a sufficienza, non già in cache."""
    if not isinstance(e, dict):
        return False
    if min_bytes is None:
        min_bytes = int(float(cfg["min_size_gb"]) * GB)
    if not e.get("source") or e.get("source") == "local":
        return False
    if e.get("missing"):
        return False
    if _size(e) < min_bytes:
        return False
    if cfg.get("only_enabled") and not e.get("enabled"):
        return False
    if (e.get("cache") or {}).get("status") in ("ready", "copying"):
        return False
    return os.path.isfile(e.get("path") or "")


def is_cached(e):
    return isinstance(e, dict) and (e.get("cache") or {}).get("status") == "ready"


def _plural(n, uno, molti):
    return f"{n} {uno}" if n == 1 else f"{n} {molti}"


# ---------------------------------------------------------------- piano
def plan():
    """Cosa copiare e cosa eliminare, senza toccare niente.

    {to_copy:[slug], to_free:[slug], free_gb, reason, candidates, skipped}
    """
    cfg = current_settings()
    isos = catalog.load().get("isos", {})
    boots = _boot_times()
    keep = int(float(cfg["keep_free_gb"]) * GB)
    min_bytes = int(float(cfg["min_size_gb"]) * GB)
    free = free_bytes()

    cands = [(s, e) for s, e in isos.items() if is_candidate(e, cfg, min_bytes)]
    # prima le più usate di recente, poi le più piccole (ne entrano di più)
    cands.sort(key=lambda x: (-boots.get(x[0], 0.0), _size(x[1]), x[0]))
    # candidate all'eliminazione: copie con l'ultimo utilizzo più vecchio, a parità la più grande
    victims = [(s, e) for s, e in isos.items() if is_cached(e)]
    victims.sort(key=lambda x: (_last_use(x[0], x[1], boots), -_copy_size(x[1]), x[0]))

    to_free, to_copy = [], []
    avail = free - keep
    vi = 0

    def _evict():
        nonlocal avail, vi
        if vi >= len(victims):
            return False
        s, e = victims[vi]
        vi += 1
        to_free.append(s)
        avail += _copy_size(e)
        return True

    while avail < 0 and _evict():
        pass
    skipped = 0
    for slug, e in cands:
        need = _size(e)
        while avail < need and _evict():
            pass
        if avail >= need:
            to_copy.append(slug)
            avail -= need
        else:
            skipped += 1

    parts = []
    if to_copy:
        parts.append(f"{len(to_copy)} ISO da copiare in locale")
    if to_free:
        parts.append(_plural(len(to_free), "copia da eliminare", "copie da eliminare")
                     + f" per lasciare liberi {cfg['keep_free_gb']} GB")
    if skipped:
        parts.append(_plural(skipped, "ISO saltata: spazio insufficiente", "ISO saltate: spazio insufficiente"))
    if not parts:
        parts.append("Niente da fare: nessuna ISO remota da copiare" if not cands else "Niente da fare")
    return {"to_copy": to_copy, "to_free": to_free, "free_gb": round(free / GB, 1),
            "reason": "; ".join(parts), "candidates": len(cands), "skipped": skipped}


def stats():
    """{cached, cached_bytes, free_bytes, candidates} per la GUI."""
    cfg = current_settings()
    isos = catalog.load().get("isos", {})
    min_bytes = int(float(cfg["min_size_gb"]) * GB)
    cached = [e for e in isos.values() if is_cached(e)]
    return {"cached": len(cached),
            "cached_bytes": sum(_copy_size(e) for e in cached),
            "free_bytes": free_bytes(),
            "candidates": sum(1 for e in isos.values() if is_candidate(e, cfg, min_bytes))}


# ---------------------------------------------------------------- esecuzione
def _cancelled(job):
    return job is not None and bool(getattr(job, "cancelled", False))


def _progress(job, done, steps, message):
    if job is not None:
        job.set_progress(int(100 * done / max(1, steps)), message)


def _entry(slug):
    return catalog.load().get("isos", {}).get(slug)


def _wait_copy(job, sub, done, steps):
    """Attende il job di copia avviato da catalog.set_cache, propagando progresso e annullamento."""
    deadline = time.monotonic() + MAX_COPY_WAIT
    while sub.status == "running" and time.monotonic() < deadline:
        if _cancelled(job):
            jobs.cancel(sub.id)
        if job is not None:
            pct = int(100 * (done + (sub.progress or 0) / 100.0) / max(1, steps))
            job.set_progress(pct, sub.message or "Copia in corso")
        jobs.wait(sub.id, POLL)
        time.sleep(0.05)
    return sub.status


def run(job=None):
    """Esegue il piano: prima libera, poi copia. Rilegge il catalogo a ogni passo
    (durante l'esecuzione l'utente può abilitare, disabilitare o eliminare una ISO)."""
    result = {"copied": [], "freed": [], "skipped": [], "errors": {}, "busy": False}
    if not _run_lock.acquire(blocking=False):
        result["busy"] = True
        if job is not None:
            job.set_progress(100, "Copia locale automatica già in corso")
        return result
    try:
        cfg = current_settings()
        p = plan()
        steps = len(p["to_free"]) + len(p["to_copy"])
        if steps == 0:
            if job is not None:
                job.set_progress(100, p["reason"])
            return result
        done = 0
        for slug in p["to_free"]:
            if _cancelled(job):
                break
            e = _entry(slug)
            done += 1
            if not is_cached(e):
                result["skipped"].append(slug)
                continue
            try:
                catalog.set_cache(slug, False)
                result["freed"].append(slug)
                _progress(job, done, steps, f"Copia locale di {e.get('file') or slug} eliminata")
            except Exception as ex:  # noqa: BLE001 - un errore su una ISO non ferma le altre
                log.warning("eliminazione copia locale %s: %s", slug, ex)
                result["errors"][slug] = str(ex)

        min_bytes = int(float(cfg["min_size_gb"]) * GB)
        keep = int(float(cfg["keep_free_gb"]) * GB)
        for slug in p["to_copy"]:
            if _cancelled(job):
                break
            e = _entry(slug)
            if not is_candidate(e, cfg, min_bytes):
                done += 1
                result["skipped"].append(slug)          # catalogo cambiato: non è più da copiare
                continue
            if free_bytes() - _size(e) < keep:
                done += 1
                result["skipped"].append(slug)          # spazio finito nel frattempo
                continue
            _progress(job, done, steps, f"Copia locale di {e.get('file') or slug}")
            try:
                sub = catalog.set_cache(slug, True)
            except Exception as ex:  # noqa: BLE001
                log.warning("avvio copia locale %s: %s", slug, ex)
                result["errors"][slug] = str(ex)
                done += 1
                continue
            if sub is None:
                result["skipped"].append(slug)
            else:
                status = _wait_copy(job, sub, done, steps)
                if status == "done":
                    result["copied"].append(slug)
                elif status == "cancelled":
                    result["skipped"].append(slug)
                else:
                    result["errors"][slug] = sub.message or "copia non riuscita"
            done += 1
        msg = f"Copie fatte: {len(result['copied'])}; copie eliminate: {len(result['freed'])}"
        if result["errors"]:
            msg += f"; {_plural(len(result['errors']), 'errore', 'errori')}"
        if job is not None:
            job.set_progress(100, msg)
        return result
    finally:
        _run_lock.release()


def start(message="Copia locale automatica"):
    """Avvia run() come job 'copy' (uno solo alla volta)."""
    cur = jobs.running("copy", "autocache")
    if cur:
        return cur[0]
    return jobs.start("copy", "autocache", run, message=message)


def clear(slug=None):
    """Libera la copia locale di una ISO (slug) o di tutte. {freed:[slug], bytes:int}."""
    isos = catalog.load().get("isos", {})
    if slug is not None:
        if slug not in isos:
            raise FileNotFoundError("ISO non trovata")
        targets = [slug]
    else:
        targets = [s for s, e in isos.items()
                   if is_cached(e) or e.get("cache_wanted") or (e.get("cache") or {}).get("status") == "copying"]
    freed, size = [], 0
    for s in targets:
        e = isos.get(s) or {}
        n = _copy_size(e) if _cache_path(e) and os.path.isfile(_cache_path(e)) else 0
        for j in jobs.running("copy", s):      # copia in corso: prima si annulla il job
            jobs.cancel(j.id)
            jobs.wait(j.id, 10)
        try:
            catalog.set_cache(s, False)
        except Exception as ex:  # noqa: BLE001
            log.warning("liberazione copia locale %s: %s", s, ex)
            continue
        freed.append(s)
        size += n
    if slug is None:
        size += _remove_orphans()
    return {"freed": freed, "bytes": size}


def _remove_orphans():
    """Elimina i file rimasti in CACHE_DIR e non più riferiti dal catalogo (copie interrotte)."""
    keep = {_cache_path(e) for e in catalog.load().get("isos", {}).values() if _cache_path(e)}
    total = 0
    try:
        names = os.listdir(C.CACHE_DIR)
    except OSError:
        return 0
    for fn in names:
        if not fn.endswith((".iso", ".part")):
            continue
        full = os.path.join(C.CACHE_DIR, fn)
        if full in keep or not os.path.isfile(full):
            continue
        try:
            total += os.path.getsize(full)
            os.unlink(full)
        except OSError as ex:
            log.warning("rimozione file orfano %s: %s", full, ex)
    return total
