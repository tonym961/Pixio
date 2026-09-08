"""Job in background eseguiti in thread, con stato persistito in JSON (config.JOBS_DIR/<id>.json).

Un solo processo gunicorn: lo stato "vivo" sta in memoria, i file servono alla GUI per vedere
i job anche dopo un riavvio dell'app (quelli rimasti 'running' vengono marcati 'error' "interrotto").
"""
import datetime
import logging
import os
import secrets
import threading

from .. import config as C
from ..storage import read_json, write_json

log = logging.getLogger("pixio.jobs")

_lock = threading.RLock()
_jobs = {}            # id -> Job (tutti quelli noti: caricati da disco + avviati in questo processo)
_loaded = False
_seq = 0              # contatore progressivo: ordina i job avviati nello stesso secondo
KEEP = 200            # job conservati su disco
EXCLUSIVE_TYPES = ("scan",)   # un solo job di questo tipo alla volta


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


class Job:
    def __init__(self, job_type, target, message=""):
        global _seq
        _seq += 1
        self.seq = _seq
        self.id = secrets.token_hex(4)
        self.type = job_type
        self.target = target
        self.status = "running"
        self.progress = None
        self.message = message or ""
        self.started = _now()
        self.finished = None
        self.cancelled = False
        self.logs = []
        self._thread = None

    # --- API usata dalla funzione eseguita nel thread
    def set_progress(self, p, message=None):
        if p is not None:
            try:
                p = max(0, min(100, int(p)))
            except (TypeError, ValueError):
                p = None
        self.progress = p
        if message is not None:
            self.message = str(message)
        self.save()

    def log(self, msg):
        line = f"{_now()} {msg}"
        self.logs.append(line)
        if len(self.logs) > 200:
            self.logs = self.logs[-200:]
        log.info("job %s (%s): %s", self.id, self.type, msg)
        self.save()

    # --- serializzazione
    def to_dict(self):
        return {
            "id": self.id, "type": self.type, "target": self.target, "status": self.status,
            "progress": self.progress, "message": self.message, "started": self.started,
            "finished": self.finished, "cancelled": self.cancelled,
        }

    def to_full_dict(self):
        d = self.to_dict()
        d["logs"] = list(self.logs)
        return d

    @classmethod
    def from_dict(cls, d):
        j = cls(d.get("type", "?"), d.get("target"), d.get("message", ""))
        j.id = str(d.get("id") or j.id)
        j.seq = 0
        j.status = d.get("status", "error")
        j.progress = d.get("progress")
        j.started = d.get("started") or j.started
        j.finished = d.get("finished")
        j.cancelled = bool(d.get("cancelled"))
        j.logs = list(d.get("logs") or [])
        return j

    def _path(self):
        return os.path.join(C.JOBS_DIR, f"{self.id}.json")

    def save(self):
        try:
            write_json(self._path(), self.to_full_dict())
        except OSError as e:
            log.warning("impossibile salvare lo stato del job %s: %s", self.id, e)


def _ensure_loaded():
    """Carica i job da disco (una volta). Quelli 'running' non nostri sono resti di un processo precedente."""
    global _loaded
    with _lock:
        if _loaded:
            return
        _loaded = True
        try:
            names = sorted(os.listdir(C.JOBS_DIR))
        except OSError:
            names = []
        for fn in names:
            if not fn.endswith(".json") or fn.startswith("."):
                continue
            d = read_json(os.path.join(C.JOBS_DIR, fn), {})
            if not isinstance(d, dict) or not d.get("id"):
                continue
            j = Job.from_dict(d)
            if j.id in _jobs:
                continue
            if j.status == "running":
                j.status = "error"
                j.message = "interrotto (riavvio dell'applicazione)"
                j.finished = j.finished or _now()
                j.save()
            _jobs[j.id] = j


def mark_interrupted():
    """Da chiamare all'avvio: marca come 'error' i job rimasti 'running' da un'esecuzione precedente."""
    _ensure_loaded()


def _run(job, fn):
    try:
        fn(job)
        if job.cancelled:
            job.status = "cancelled"
            if not job.message:
                job.message = "annullato"
        else:
            job.status = "done"
            if job.progress is not None:
                job.progress = 100
    except Exception as e:  # noqa: BLE001 - il thread non deve mai morire senza stato
        log.exception("job %s (%s) fallito", job.id, job.type)
        job.status = "cancelled" if job.cancelled else "error"
        job.message = str(e) or e.__class__.__name__
    finally:
        job.finished = _now()
        job.save()
        try:
            purge()
        except Exception:  # noqa: BLE001
            log.exception("purge dei job fallito")


def running(job_type=None, target=None):
    """Job in stato 'running' (eventualmente filtrati per tipo e/o target)."""
    _ensure_loaded()
    with _lock:
        return [j for j in _jobs.values() if j.status == "running"
                and (job_type is None or j.type == job_type)
                and (target is None or j.target == target)]


def start(job_type, target, fn, message=""):
    """Avvia fn(job) in un thread demone. Per i tipi esclusivi ritorna il job già in corso, se c'è."""
    _ensure_loaded()
    with _lock:
        if job_type in EXCLUSIVE_TYPES:
            cur = running(job_type)
            if cur:
                return cur[0]
        job = Job(job_type, target, message)
        _jobs[job.id] = job
        job.save()
        t = threading.Thread(target=_run, args=(job, fn), name=f"job-{job_type}-{job.id}", daemon=True)
        job._thread = t
        t.start()
        return job


def get_job(job_id):
    _ensure_loaded()
    with _lock:
        return _jobs.get(str(job_id))


def list_jobs(limit=50):
    _ensure_loaded()
    with _lock:
        js = sorted(_jobs.values(), key=lambda j: (j.started or "", j.seq), reverse=True)
    return [j.to_dict() for j in js[:limit]]


def cancel(job_id):
    """Chiede l'annullamento: la funzione del job deve controllare job.cancelled."""
    job = get_job(job_id)
    if job is None:
        return None
    if job.status == "running":
        job.cancelled = True
        job.message = job.message or "annullamento in corso"
        job.save()
    return job


def wait(job_id, timeout=None):
    """Attende la fine del job (usato dai test)."""
    job = get_job(job_id)
    if job is not None and job._thread is not None:
        job._thread.join(timeout)
    return job


def purge(keep=KEEP):
    """Tiene solo gli ultimi `keep` job terminati (quelli in corso non si toccano)."""
    _ensure_loaded()
    with _lock:
        finished = sorted((j for j in _jobs.values() if j.status != "running"),
                          key=lambda j: (j.started or "", j.seq), reverse=True)
        for j in finished[keep:]:
            _jobs.pop(j.id, None)
            try:
                os.unlink(j._path())
            except OSError:
                pass
