"""Thread demone di manutenzione: parsing log client, controllo mount, scansione periodica.

Un solo thread per processo (gunicorn ha 1 worker). Ogni eccezione viene loggata: il thread non muore mai.
"""
import logging
import os
import threading
import time

from .. import settings as S
from . import jobs

log = logging.getLogger("pixio.background")

_thread = None
_stop = threading.Event()
_guard = threading.Lock()

TICK = 2.0
MOUNT_CHECK_EVERY = 60
CLEANUP_EVERY = 3600
# la copia locale ha un suo ritmo: chi la attiva dalla GUI non deve aspettare un'ora
# per capire se sta funzionando.
CACHE_CHECK_EVERY = 300


def _catalog():
    try:
        from . import catalog
        return catalog
    except ImportError:
        return None


def _safe(label, fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception:  # noqa: BLE001
        log.exception("%s fallito", label)
        return None


def start_scan():
    """Avvia una scansione del catalogo come job 'scan' (se il modulo catalogo esiste). Ritorna il job o None."""
    cat = _catalog()
    if cat is None:
        return None
    if jobs.running("scan"):
        return jobs.running("scan")[0]
    starter = getattr(cat, "start_scan", None)
    if callable(starter):
        return starter()
    scan = getattr(cat, "scan", None)
    if not callable(scan):
        return None

    def run(job):
        try:
            scan(job)
        except TypeError:
            scan()
    return jobs.start("scan", None, run, message="Scansione delle sorgenti")


def _startup():
    from . import sources
    _safe("marcatura job interrotti", jobs.mark_interrupted)
    # le copie locali vivono in un thread: un riavvio le interrompe e senza questo resterebbero
    # in stato "copying" per sempre, senza mai essere riprovate
    try:
        from . import autocache
        _safe("copie locali interrotte", autocache.reset_interrupted)
    except ImportError:
        pass
    res = _safe("mount sorgenti", sources.mount_all)
    if res:
        log.info("mount sorgenti: %s", res)
    cat = _catalog()
    if cat is not None and callable(getattr(cat, "remount_enabled", None)):
        _safe("remount ISO abilitate", cat.remount_enabled)


def _autocache_tick():
    """Copia locale automatica delle ISO grandi che stanno su share remote (se attiva nelle impostazioni)."""
    try:
        from . import autocache
    except ImportError:
        return
    if not S.load().get("cache", {}).get("auto"):
        return
    jobs.start("copy", None, autocache.run, message="Copia locale automatica")


def _loop():
    from . import sources, clients, uploads
    _startup()
    last_mount = time.monotonic()
    last_scan = time.monotonic()
    last_cleanup = time.monotonic()
    last_cache = 0.0
    while not _stop.is_set():
        _safe("poll client", clients.poll)
        now = time.monotonic()
        if now - last_mount >= MOUNT_CHECK_EVERY:
            last_mount = now
            res = _safe("controllo mount sorgenti", sources.remount_missing)
            if res:
                log.info("rimontaggio sorgenti: %s", res)
            # ISO abilitate ma non montate (share tornata raggiungibile, riavvio): rimontale
            cat_mod = _catalog()
            if cat_mod is not None and callable(getattr(cat_mod, "remount_enabled", None)):
                _safe("remount ISO abilitate", cat_mod.remount_enabled)
        if now - last_cache >= CACHE_CHECK_EVERY:
            last_cache = now
            _safe("copia locale automatica", _autocache_tick)
        if now - last_cleanup >= CLEANUP_EVERY:
            last_cleanup = now
            _safe("pulizia upload", uploads.cleanup)
        try:
            scan_cfg = S.load().get("scan", {})
        except Exception:  # noqa: BLE001
            scan_cfg = {}
        try:
            interval = max(1, int(scan_cfg.get("interval_min") or 10)) * 60
        except (TypeError, ValueError):
            interval = 600
        if scan_cfg.get("auto", True) and now - last_scan >= interval:
            last_scan = now
            if not jobs.running("scan"):
                _safe("scansione automatica", start_scan)
        _stop.wait(TICK)


def start(app=None):
    """Avvia il thread (idempotente). Disattivabile con PIXIO_NO_BACKGROUND=1 (test)."""
    global _thread
    if os.environ.get("PIXIO_NO_BACKGROUND"):
        return None
    with _guard:
        if _thread is not None and _thread.is_alive():
            return _thread
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="pixio-background", daemon=True)
        _thread.start()
        log.info("thread di background avviato")
        return _thread


def stop(timeout=5):
    """Ferma il thread (usato dai test)."""
    global _thread
    _stop.set()
    t = _thread
    if t is not None and t.is_alive():
        t.join(timeout)
    with _guard:
        _thread = None
    _stop.clear()


def is_running():
    return _thread is not None and _thread.is_alive()
