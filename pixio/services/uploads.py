"""Upload ISO dalla web UI: a chunk, riprendibile.

Chunk in config.UPLOAD_TMP_DIR/<upload_id>/<n>, stato in UPLOAD_TMP_DIR/<upload_id>.json.
Alla fine i chunk vengono assemblati in config.LIBRARY_DIR/<filename>.
"""
import datetime
import logging
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata

from .. import config as C
from ..storage import read_json, write_json, update_json
from . import jobs

log = logging.getLogger("pixio.uploads")

CHUNK_SIZE = 8 * 1024 * 1024
ALLOWED_EXT = (".iso", ".img", ".wim")
DISK_MARGIN = 1024 ** 3          # 1 GiB di margine sul disco
MAX_SIZE = 64 * 1024 ** 3        # 64 GiB
MAX_AGE = 7 * 86400              # upload abbandonati eliminati dopo 7 giorni
READ_BLOCK = 1024 * 1024

_lock = threading.RLock()


class UploadError(Exception):
    """Errore con codice HTTP associato."""
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def _now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def sanitize_filename(filename):
    """Solo il basename, caratteri sicuri, estensione ammessa. Solleva UploadError(400)."""
    name = os.path.basename(str(filename or "").replace("\\", "/")).strip()
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[^\w.\- ()+\[\]]", "_", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name or name.startswith("."):
        raise UploadError("Nome file non valido")
    base, ext = os.path.splitext(name)
    if ext.lower() not in ALLOWED_EXT:
        raise UploadError("Tipo di file non ammesso: sono accettati solo .iso, .img e .wim")
    name = base[:200] + ext.lower()
    if name in (".", "..") or "/" in name:
        raise UploadError("Nome file non valido")
    return name


def _state_path(upload_id):
    return os.path.join(C.UPLOAD_TMP_DIR, f"{upload_id}.json")


def _chunk_dir(upload_id):
    return os.path.join(C.UPLOAD_TMP_DIR, upload_id)


def _check_id(upload_id):
    if not re.match(r"^[0-9a-f]{16}$", str(upload_id or "")):
        raise UploadError("Upload non trovato", 404)
    return upload_id


def _load(upload_id):
    st = read_json(_state_path(_check_id(upload_id)), None)
    if not st or not isinstance(st, dict) or st.get("upload_id") != upload_id:
        raise UploadError("Upload non trovato", 404)
    return st


def _nchunks(size, chunk_size=CHUNK_SIZE):
    return max(1, (size + chunk_size - 1) // chunk_size)


def _received(upload_id, size, chunk_size=None):
    """Indici dei chunk presenti (completi) su disco."""
    chunk_size = chunk_size or CHUNK_SIZE
    d = _chunk_dir(upload_id)
    got = []
    n = _nchunks(size, chunk_size)
    try:
        names = os.listdir(d)
    except OSError:
        return got
    for fn in names:
        if not fn.isdigit():
            continue
        i = int(fn)
        if i >= n:
            continue
        expected = _expected_len(size, i, chunk_size)
        try:
            if os.path.getsize(os.path.join(d, fn)) == expected:
                got.append(i)
        except OSError:
            pass
    return sorted(got)


def _expected_len(size, n, chunk_size=None):
    chunk_size = chunk_size or CHUNK_SIZE
    total = _nchunks(size, chunk_size)
    if n < total - 1:
        return chunk_size
    return size - n * chunk_size


def _cs(st):
    return int(st.get("chunk_size") or CHUNK_SIZE)


def _public(st):
    cs = _cs(st)
    rec = _received(st["upload_id"], st["size"], cs)
    return {"upload_id": st["upload_id"], "filename": st["filename"], "size": st["size"],
            "chunk_size": cs, "received": rec,
            "received_bytes": sum(_expected_len(st["size"], i, cs) for i in rec), "started": st.get("started")}


def list_uploads():
    out = []
    try:
        names = sorted(os.listdir(C.UPLOAD_TMP_DIR))
    except OSError:
        return out
    for fn in names:
        if fn.endswith(".json") and not fn.startswith("."):
            st = read_json(os.path.join(C.UPLOAD_TMP_DIR, fn), None)
            if isinstance(st, dict) and st.get("upload_id"):
                p = _public(st)
                p.pop("received", None)
                out.append(p)
    return out


def init(filename, size):
    """Crea (o riprende) un upload. Ritorna {upload_id, chunk_size, received:[int]}."""
    name = sanitize_filename(filename)
    try:
        size = int(size)
    except (TypeError, ValueError):
        raise UploadError("Dimensione non valida")
    if size <= 0 or size > MAX_SIZE:
        raise UploadError("Dimensione non valida (max 64 GiB)")
    with _lock:
        cleanup()
        dest = os.path.join(C.LIBRARY_DIR, name)
        if os.path.exists(dest):
            raise UploadError(f"Esiste già un file '{name}' nella libreria", 409)
        # ripresa di un upload con stesso nome e dimensione
        for fn in os.listdir(C.UPLOAD_TMP_DIR) if os.path.isdir(C.UPLOAD_TMP_DIR) else []:
            if fn.endswith(".json"):
                st = read_json(os.path.join(C.UPLOAD_TMP_DIR, fn), None)
                if isinstance(st, dict) and st.get("filename") == name and st.get("size") == size:
                    p = _public(st)
                    return {"upload_id": st["upload_id"], "chunk_size": st.get("chunk_size", CHUNK_SIZE),
                            "received": p["received"], "resumed": True}
        os.makedirs(C.LIBRARY_DIR, exist_ok=True)
        os.makedirs(C.UPLOAD_TMP_DIR, exist_ok=True)
        free = shutil.disk_usage(C.LIBRARY_DIR).free
        if free < size + DISK_MARGIN:
            raise UploadError("Spazio su disco insufficiente per questo file", 507)
        upload_id = secrets.token_hex(8)
        os.makedirs(_chunk_dir(upload_id), exist_ok=True)
        st = {"upload_id": upload_id, "filename": name, "size": size, "chunk_size": CHUNK_SIZE,
              "started": _now_iso(), "chunks": _nchunks(size, CHUNK_SIZE)}
        write_json(_state_path(upload_id), st)
        return {"upload_id": upload_id, "chunk_size": CHUNK_SIZE, "received": [], "resumed": False}


def put_chunk(upload_id, n, stream):
    """Salva il chunk n leggendo `stream` a blocchi. Ritorna il numero di chunk ricevuti."""
    st = _load(upload_id)
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise UploadError("Indice chunk non valido")
    cs = _cs(st)
    total = _nchunks(st["size"], cs)
    if n < 0 or n >= total:
        raise UploadError("Indice chunk fuori intervallo")
    expected = _expected_len(st["size"], n, cs)
    d = _chunk_dir(upload_id)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".{n}.part-{secrets.token_hex(3)}")
    got = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                block = stream.read(min(READ_BLOCK, expected - got + 1))
                if not block:
                    break
                got += len(block)
                if got > expected:
                    raise UploadError(f"Chunk {n} troppo grande (attesi {expected} byte)")
                f.write(block)
            f.flush()
            os.fsync(f.fileno())
        if got != expected:
            raise UploadError(f"Chunk {n} incompleto: ricevuti {got} byte su {expected}")
        os.replace(tmp, os.path.join(d, str(n)))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    try:
        update_json(_state_path(upload_id), lambda s: {**s, "last_chunk": _now_iso()})
    except OSError:
        pass
    return len(_received(upload_id, st["size"], cs))


def _slug_from_filename(name):
    base = os.path.splitext(name)[0]
    s = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", s).strip("-._")[:64].strip("-._")
    return s or "iso"


def finish(upload_id):
    """Assembla i chunk nella libreria e avvia il rilevamento del tipo. Ritorna {path, slug, job_id}."""
    with _lock:
        st = _load(upload_id)
        size = st["size"]
        cs = _cs(st)
        total = _nchunks(size, cs)
        rec = _received(upload_id, size, cs)
        if len(rec) != total:
            missing = sorted(set(range(total)) - set(rec))
            raise UploadError(f"Upload incompleto: mancano {len(missing)} chunk (primo: {missing[0]})", 400)
        os.makedirs(C.LIBRARY_DIR, exist_ok=True)
        dest = os.path.join(C.LIBRARY_DIR, st["filename"])
        tmp_dest = os.path.join(C.LIBRARY_DIR, f".{st['filename']}.part")
        try:
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            raise UploadError(f"Esiste già un file '{st['filename']}' nella libreria", 409)
        os.close(fd)
        d = _chunk_dir(upload_id)
        try:
            with open(tmp_dest, "wb") as out:
                for i in range(total):
                    with open(os.path.join(d, str(i)), "rb") as f:
                        shutil.copyfileobj(f, out, 4 * 1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
            if os.path.getsize(tmp_dest) != size:
                raise UploadError("Dimensione finale non corrispondente", 500)
            os.replace(tmp_dest, dest)
        except Exception:
            for p in (tmp_dest, dest):
                try:
                    os.unlink(p)
                except OSError:
                    pass
            raise
        discard(upload_id)
    slug = _slug_from_filename(st["filename"])
    job_id = None
    try:
        from . import catalog  # noqa: WPS433 - modulo scritto da un altro componente, opzionale
    except ImportError:
        catalog = None
    if catalog is not None:
        # slug provvisorio con le stesse regole del catalogo (il job di rilevamento lo conferma)
        try:
            fn_slug = getattr(catalog, "slug_for_path", None)
            if callable(fn_slug):
                slug = fn_slug(dest) or slug
            elif callable(getattr(catalog, "slugify", None)):
                rel = os.path.relpath(dest, C.LIBRARY_DIR)
                slug = catalog.slugify(rel) or slug
                if callable(getattr(catalog, "unique_slug", None)) and callable(getattr(catalog, "load", None)):
                    isos = (catalog.load() or {}).get("isos") or {}
                    if not any(v.get("source") == "local" and v.get("rel_path") == rel for v in isos.values()):
                        slug = catalog.unique_slug(slug, isos)
        except Exception:  # noqa: BLE001
            pass
        register = getattr(catalog, "register_local_file", None)
        if callable(register):
            def run(job):
                job.log(f"rilevamento tipo di {os.path.basename(dest)}")
                res = register(dest)
                s = res.get("slug") if isinstance(res, dict) else (res if isinstance(res, str) else None)
                job.set_progress(100, f"registrata {s or os.path.basename(dest)}")
            job_id = jobs.start("detect", slug, run, message=f"Rilevamento {st['filename']}").id
    return {"path": dest, "slug": slug, "job_id": job_id}


def discard(upload_id):
    """Elimina chunk e stato di un upload."""
    _check_id(upload_id)
    shutil.rmtree(_chunk_dir(upload_id), ignore_errors=True)
    try:
        os.unlink(_state_path(upload_id))
    except OSError:
        pass


def cleanup(max_age=MAX_AGE):
    """Rimuove gli upload più vecchi di max_age secondi (dall'ultimo chunk o dall'inizio)."""
    removed = []
    try:
        names = os.listdir(C.UPLOAD_TMP_DIR)
    except OSError:
        return removed
    now = time.time()
    for fn in names:
        if not fn.endswith(".json"):
            continue
        p = os.path.join(C.UPLOAD_TMP_DIR, fn)
        st = read_json(p, None)
        uid = st.get("upload_id") if isinstance(st, dict) else None
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        if now - mtime > max_age:
            if uid and re.match(r"^[0-9a-f]{16}$", uid):
                discard(uid)
            else:
                try:
                    os.unlink(p)
                except OSError:
                    pass
            removed.append(uid or fn)
    # directory di chunk orfane (senza stato)
    for fn in names:
        if re.match(r"^[0-9a-f]{16}$", fn) and not os.path.exists(_state_path(fn)):
            shutil.rmtree(os.path.join(C.UPLOAD_TMP_DIR, fn), ignore_errors=True)
    if removed:
        log.info("upload scaduti rimossi: %s", ", ".join(removed))
    return removed
