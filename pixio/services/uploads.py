"""Upload dalla web UI: a chunk, riprendibile.

Chunk in config.UPLOAD_TMP_DIR/<upload_id>/<n>, stato in UPLOAD_TMP_DIR/<upload_id>.json.
kind "iso" (default): alla fine i chunk vengono assemblati in config.LIBRARY_DIR/<filename> e parte il rilevamento.
kind "driver": destinazione config.DRIVERS_DIR/<folder>/<filename>; se il file è uno .zip viene estratto
(in modo sicuro) nella cartella e poi cancellato. Con il campo facoltativo "path" (sottopercorso relativo,
es. "x64/rt.inf") il file finisce in config.DRIVERS_DIR/<folder>/<path>: serve per caricare una cartella
intera mantenendo le sue sottocartelle.
kind "answer": destinazione config.ANSWERS_DIR/<id risposta>/<filename>, file di testo di pochi KB.
"""
import datetime
import logging
import os
import re
import secrets
import shutil
import stat as statmod
import threading
import time
import unicodedata
import zipfile

from .. import config as C
from ..storage import read_json, write_json, update_json
from . import jobs

log = logging.getLogger("pixio.uploads")

CHUNK_SIZE = 8 * 1024 * 1024
ALLOWED_EXT = (".iso", ".img", ".wim")
DRIVER_EXT = (".inf", ".sys", ".cat", ".dll", ".exe", ".cab", ".zip", ".msi", ".txt", ".bin", ".dat",
              ".ini", ".cfg", ".xml", ".json", ".7z", ".sepolicy")
ANSWER_EXT = (".xml", ".cfg", ".ks", ".yaml", ".yml", ".txt", ".cmd", ".bat", ".ps1", ".reg",
              ".sh", ".conf", ".seed", ".json", ".ini")
ANSWER_NOEXT = ("user-data", "meta-data", "vendor-data", "network-config")   # nomi cloud-init senza estensione
ANSWER_MAX_SIZE = 8 * 1024 * 1024   # i file di risposta sono testo: 8 MiB sono già abbondanti
KINDS = ("iso", "driver", "answer")
DISK_MARGIN = 1024 ** 3          # 1 GiB di margine sul disco
MAX_SIZE = 64 * 1024 ** 3        # 64 GiB
MAX_AGE = 7 * 86400              # upload abbandonati eliminati dopo 7 giorni
READ_BLOCK = 1024 * 1024
ZIP_MAX_BYTES = 2 * 1024 ** 3    # 2 GiB estratti al massimo
ZIP_MAX_FILES = 20000
MAX_SUBPATH_DEPTH = 6            # livelli massimi del campo "path" (sottocartelle + nome file)
# stessi caratteri ammessi dai nomi file (vedi sanitize_filename)
SUBPATH_SEG_RE = re.compile(r"^[\w.\-()+\[\] ]{1,200}$", re.UNICODE)

_lock = threading.RLock()


class UploadError(Exception):
    """Errore con codice HTTP associato."""
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def _now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def sanitize_filename(filename, kind="iso"):
    """Solo il basename, caratteri sicuri, estensione ammessa per il tipo di upload. Solleva UploadError(400)."""
    name = os.path.basename(str(filename or "").replace("\\", "/")).strip()
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[^\w.\- ()+\[\]]", "_", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name or name.startswith("."):
        raise UploadError("Nome file non valido")
    base, ext = os.path.splitext(name)
    if kind == "driver":
        if ext.lower() not in DRIVER_EXT:
            raise UploadError("Tipo di file non ammesso per i driver: sono accettati "
                              + ", ".join(e.lstrip(".") for e in DRIVER_EXT))
    elif kind == "answer":
        if ext and ext.lower() not in ANSWER_EXT:
            raise UploadError("Tipo di file non ammesso per le risposte: sono accettati "
                              + ", ".join(e.lstrip(".") for e in ANSWER_EXT)
                              + " (oppure " + ", ".join(ANSWER_NOEXT) + ")")
        # nomi semplici: le stesse regole con cui il servizio risposte valida e serve i file
        from . import answers as _answers
        simple = re.sub(r"[^A-Za-z0-9._-]+", "-", base[:100]).strip("-") + ext.lower()
        try:
            return _answers.check_filename(simple)
        except ValueError as e:
            raise UploadError(str(e))
    elif ext.lower() not in ALLOWED_EXT:
        raise UploadError("Tipo di file non ammesso: sono accettati solo .iso, .img e .wim")
    name = base[:200] + ext.lower()
    if name in (".", "..") or "/" in name:
        raise UploadError("Nome file non valido")
    return name


def check_subpath(path):
    """Valida il sottopercorso relativo di un file dentro una cartella driver.

    Ritorna la lista dei segmenti ([] se `path` è vuoto). Rifiuta percorsi assoluti (anche "C:\\..."),
    "." e "..", nomi nascosti, più di MAX_SUBPATH_DEPTH livelli e caratteri non ammessi nei nomi file.
    """
    raw = str(path or "").replace("\\", "/").strip()
    if not raw:
        return []
    if "\x00" in raw:
        raise UploadError("Percorso non valido")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise UploadError("Percorso non valido: non sono ammessi percorsi assoluti")
    parts = [seg for seg in raw.split("/") if seg != ""]
    if not parts:
        raise UploadError("Percorso non valido")
    if len(parts) > MAX_SUBPATH_DEPTH:
        raise UploadError(f"Percorso troppo profondo: al massimo {MAX_SUBPATH_DEPTH} livelli di sottocartelle")
    for seg in parts:
        if seg in (".", ".."):
            raise UploadError("Percorso non valido: '.' e '..' non sono ammessi")
        if seg.startswith("."):
            raise UploadError(f"Percorso non valido: '{seg}' è un nome nascosto")
        if not SUBPATH_SEG_RE.match(seg):
            raise UploadError(f"Percorso non valido: '{seg}' contiene caratteri non ammessi")
        if seg != seg.rstrip(" ."):
            raise UploadError(f"Percorso non valido: '{seg}' finisce con uno spazio o un punto")
    return parts


def driver_subdir(path, filename):
    """Sottocartella (relativa, con '/') in cui mettere `filename` secondo il campo "path".

    Accetta sia il percorso completo del file ("x64/rt.inf") sia la sola sottocartella ("x64").
    """
    parts = check_subpath(path)
    if parts and parts[-1].lower() == str(filename or "").lower():
        parts = parts[:-1]
    return "/".join(parts)


def _rel_name(subdir, filename):
    return f"{subdir}/{filename}" if subdir else filename


def _check_kind(kind):
    kind = str(kind or "iso").lower()
    if kind not in KINDS:
        raise UploadError("Tipo di upload non valido (iso, driver o answer)")
    return kind


def _driver_dir(folder, subdir="", create=False):
    """Cartella driver di destinazione: 400 se il nome non è valido, 404 se non esiste.

    Con `subdir` ritorna la sottocartella indicata dal campo "path" (creandola se `create`).
    """
    from . import drivers
    try:
        p = drivers.folder_path(str(folder or "").strip())
    except ValueError as e:
        raise UploadError(str(e), 400)
    if not os.path.isdir(p):
        raise UploadError("Cartella driver non trovata: creala prima dalla pagina Driver", 404)
    parts = check_subpath(subdir)
    if not parts:
        return p
    target = os.path.join(p, *parts)
    if not os.path.realpath(target).startswith(os.path.realpath(p) + os.sep):
        raise UploadError("Percorso non consentito", 400)
    if create:
        cur = p
        for seg in parts:
            cur = os.path.join(cur, seg)
            if not os.path.isdir(cur):
                try:
                    os.mkdir(cur)
                except FileExistsError:
                    raise UploadError(f"'{seg}' esiste già ed è un file, non una cartella", 409)
                except OSError as e:
                    raise UploadError(f"Impossibile creare la sottocartella '{seg}': {e}", 500)
                try:
                    os.chmod(cur, 0o2775)
                except OSError:
                    pass
    return target


def _answer_dir(folder):
    """Cartella della risposta di destinazione: 400 se l'id non è valido, 404 se non esiste."""
    from . import answers
    try:
        p = answers.folder_path(str(folder or "").strip())
    except ValueError as e:
        raise UploadError(str(e), 400)
    if not os.path.isdir(p):
        raise UploadError("Risposta non trovata: creala prima dalla pagina Risposte", 404)
    return p


def _dest_dir(st):
    """Cartella di destinazione per lo stato `st` (crea LIBRARY_DIR se serve)."""
    if st.get("kind") == "driver":
        return _driver_dir(st.get("folder"), st.get("subdir", ""), create=True)
    if st.get("kind") == "answer":
        return _answer_dir(st.get("folder"))
    os.makedirs(C.LIBRARY_DIR, exist_ok=True)
    return C.LIBRARY_DIR


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
            "kind": st.get("kind", "iso"), "folder": st.get("folder"),
            "path": _rel_name(st.get("subdir", ""), st["filename"]),
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


def init(filename, size, kind="iso", folder=None, path=None):
    """Crea (o riprende) un upload.

    Ritorna {upload_id, chunk_size, received:[int], resumed, kind, folder, path}.
    Per kind "driver" il campo facoltativo `path` è il sottopercorso dentro la cartella (es. "x64/rt.inf").
    """
    kind = _check_kind(kind)
    name = sanitize_filename(filename, kind)
    if kind == "driver":
        subdir = driver_subdir(path, name)
    else:
        if str(path or "").strip():
            raise UploadError("Il campo 'path' vale solo per i file driver")
        subdir = ""
    try:
        size = int(size)
    except (TypeError, ValueError):
        raise UploadError("Dimensione non valida")
    if size <= 0 or size > MAX_SIZE:
        raise UploadError("Dimensione non valida (max 64 GiB)")
    if kind == "answer" and size > ANSWER_MAX_SIZE:
        raise UploadError("File di risposta troppo grande (max 8 MiB)")
    if kind == "driver":
        folder = str(folder or "").strip()
        free_dir = _driver_dir(folder)
        dest_dir = _driver_dir(folder, subdir)      # può non esistere ancora: creata da finish
    elif kind == "answer":
        folder = str(folder or "").strip()
        dest_dir = free_dir = _answer_dir(folder)
    else:
        folder = None
        os.makedirs(C.LIBRARY_DIR, exist_ok=True)
        dest_dir = free_dir = C.LIBRARY_DIR
    with _lock:
        cleanup()
        dest = os.path.join(dest_dir, name)
        if os.path.exists(dest):
            if kind == "driver":
                where = f"nella cartella '{folder}/{subdir}'" if subdir else f"nella cartella '{folder}'"
            elif kind == "answer":
                where = f"nella risposta '{folder}'"
            else:
                where = "nella libreria"
            raise UploadError(f"Esiste già un file '{name}' {where}", 409)
        # ripresa di un upload con stesso nome, dimensione, tipo e cartella
        for fn in os.listdir(C.UPLOAD_TMP_DIR) if os.path.isdir(C.UPLOAD_TMP_DIR) else []:
            if fn.endswith(".json"):
                st = read_json(os.path.join(C.UPLOAD_TMP_DIR, fn), None)
                if (isinstance(st, dict) and st.get("filename") == name and st.get("size") == size
                        and st.get("kind", "iso") == kind and st.get("folder") == folder
                        and st.get("subdir", "") == subdir):
                    p = _public(st)
                    return {"upload_id": st["upload_id"], "chunk_size": st.get("chunk_size", CHUNK_SIZE),
                            "received": p["received"], "resumed": True, "kind": kind, "folder": folder,
                            "path": _rel_name(subdir, name)}
        os.makedirs(C.UPLOAD_TMP_DIR, exist_ok=True)
        free = shutil.disk_usage(free_dir).free
        if free < size + DISK_MARGIN:
            raise UploadError("Spazio su disco insufficiente per questo file", 507)
        upload_id = secrets.token_hex(8)
        os.makedirs(_chunk_dir(upload_id), exist_ok=True)
        st = {"upload_id": upload_id, "filename": name, "size": size, "chunk_size": CHUNK_SIZE,
              "kind": kind, "folder": folder, "subdir": subdir,
              "started": _now_iso(), "chunks": _nchunks(size, CHUNK_SIZE)}
        write_json(_state_path(upload_id), st)
        return {"upload_id": upload_id, "chunk_size": CHUNK_SIZE, "received": [], "resumed": False,
                "kind": kind, "folder": folder, "path": _rel_name(subdir, name)}


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


def _assemble(upload_id, st, dest_dir):
    """Concatena i chunk in dest_dir/<filename> (atomico: file .part + rename). Ritorna il percorso."""
    size = st["size"]
    cs = _cs(st)
    total = _nchunks(size, cs)
    rec = _received(upload_id, size, cs)
    if len(rec) != total:
        missing = sorted(set(range(total)) - set(rec))
        raise UploadError(f"Upload incompleto: mancano {len(missing)} chunk (primo: {missing[0]})", 400)
    dest = os.path.join(dest_dir, st["filename"])
    tmp_dest = os.path.join(dest_dir, f".{st['filename']}.part")
    where = {"driver": "nella cartella", "answer": "nella risposta"}.get(st.get("kind"), "nella libreria")
    try:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise UploadError(f"Esiste già un file '{st['filename']}' {where}", 409)
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
    return dest


# ---------------------------------------------------------------- estrazione zip (driver)
def _zip_member_parts(info):
    """Parti del percorso di un membro zip, oppure None se va saltato (cartella, symlink, file nascosti/junk).
    Solleva UploadError se il percorso è pericoloso (assoluto, '..', lettera di unità)."""
    name = info.filename.replace("\\", "/")
    if not name or name.endswith("/"):
        return None                                    # directory: creata al bisogno
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or "\x00" in name:
        raise UploadError(f"Archivio zip non sicuro: percorso non consentito ({info.filename})", 400)
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise UploadError(f"Archivio zip non sicuro: percorso non consentito ({info.filename})", 400)
    if any(p.startswith(".") or p == "__MACOSX" for p in parts):
        return None                                    # file nascosti / junk macOS
    if any(len(p) > 200 for p in parts):
        raise UploadError(f"Archivio zip: nome troppo lungo ({info.filename})", 400)
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode and statmod.S_ISLNK(mode):
        return None                                    # symlink: saltato
    if info.flag_bits & 0x1:
        raise UploadError(f"Archivio zip cifrato non supportato ({info.filename})", 400)
    return parts


def extract_zip(zip_path, dest_dir):
    """Estrae zip_path dentro dest_dir in modo sicuro. Ritorna (n_estratti, [percorsi relativi]).
    Rifiuta percorsi assoluti o con '..', salta symlink e file nascosti, limita a ZIP_MAX_FILES e ZIP_MAX_BYTES.
    La validazione avviene prima di scrivere qualsiasi file; in caso di errore i file già estratti vengono rimossi."""
    base = os.path.realpath(dest_dir)
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise UploadError(f"File zip non valido: {e}", 400)
    created = []
    with zf:
        members = []
        total_declared = 0
        for info in zf.infolist():
            parts = _zip_member_parts(info)
            if parts is None:
                continue
            members.append((info, parts))
            total_declared += max(0, info.file_size)
            if len(members) > ZIP_MAX_FILES:
                raise UploadError(f"Archivio zip con troppi file (max {ZIP_MAX_FILES})", 400)
            if total_declared > ZIP_MAX_BYTES:
                raise UploadError("Archivio zip troppo grande: max 2 GB estratti", 400)
        if not members:
            raise UploadError("L'archivio zip non contiene file utilizzabili", 400)
        written = 0
        try:
            for info, parts in members:
                target = os.path.join(base, *parts)
                if not os.path.realpath(target).startswith(base + os.sep):
                    raise UploadError(f"Archivio zip non sicuro: percorso non consentito ({info.filename})", 400)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    created.append(target)
                    while True:
                        block = src.read(READ_BLOCK)
                        if not block:
                            break
                        written += len(block)
                        if written > ZIP_MAX_BYTES:
                            raise UploadError("Archivio zip troppo grande: max 2 GB estratti", 400)
                        dst.write(block)
                try:
                    os.chmod(target, 0o664)
                except OSError:
                    pass
        except Exception:
            for p in created:
                try:
                    os.unlink(p)
                except OSError:
                    pass
            raise
    return len(created), ["/".join(parts) for _, parts in members]


def finish(upload_id):
    """Assembla i chunk nella destinazione.
    kind iso: avvia il rilevamento del tipo; ritorna {kind, path, slug, job_id}.
    kind driver: ritorna {kind, folder, path, extracted, files} (zip estratto e cancellato).
    kind answer: ritorna {kind, answer_id, folder, path, files}."""
    with _lock:
        st = _load(upload_id)
        dest_dir = _dest_dir(st)
        dest = _assemble(upload_id, st, dest_dir)
        discard(upload_id)
        if st.get("kind") == "driver":
            folder = st.get("folder")
            subdir = st.get("subdir", "")
            if st["filename"].lower().endswith(".zip"):
                try:
                    n, files = extract_zip(dest, dest_dir)
                finally:
                    try:
                        os.unlink(dest)
                    except OSError:
                        pass
                log.info("driver: zip %s estratto in '%s' (%d file)", st["filename"],
                         f"{folder}/{subdir}" if subdir else folder, n)
                return {"kind": "driver", "folder": folder, "path": dest_dir, "extracted": n,
                        "files": [_rel_name(subdir, f) for f in files]}
            try:
                os.chmod(dest, 0o664)
            except OSError:
                pass
            log.info("driver: caricato %s in '%s'", _rel_name(subdir, st["filename"]), folder)
            return {"kind": "driver", "folder": folder, "path": dest, "extracted": 0,
                    "files": [_rel_name(subdir, st["filename"])]}
        if st.get("kind") == "answer":
            folder = st.get("folder")
            try:
                os.chmod(dest, 0o664)
            except OSError:
                pass
            log.info("risposta: caricato %s in '%s'", st["filename"], folder)
            return {"kind": "answer", "answer_id": folder, "folder": folder, "path": dest,
                    "files": [st["filename"]]}
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
    return {"kind": "iso", "path": dest, "slug": slug, "job_id": job_id}


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
