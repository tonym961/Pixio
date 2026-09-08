"""Backup e ripristino della configurazione di Pixio (archivio .tar.gz creato in memoria).

Contenuto dell'archivio:
  manifest.json   versione, data, hostname e nota sulle credenziali
  config.json     impostazioni SENZA auth.password_hash e SENZA windows.smb_password
  catalog.json    catalogo delle ISO (se esiste)
  clients.json    client PXE visti (se esiste)
  drivers.json    flag delle cartelle driver (se esiste)
  answers.json    metadati delle risposte automatiche (se esiste)
  answers/<id>/<file>   file delle risposte automatiche

Nel backup NON finisce nessuna password: quella dell'amministratore, quelle delle share SMB
delle sorgenti (/etc/pixio/sources/*.cred, scritte dall'helper) e quella Samba della libreria
restano quelle della macchina su cui si ripristina.

Il ripristino è difensivo: valida l'archivio (niente percorsi assoluti o '..', niente
collegamenti, dimensione massima, solo i nomi previsti) e applica solo le parti presenti,
unendo la configurazione con quella attuale.
"""
import datetime
import io
import json
import logging
import os
import re
import socket
import tarfile

from .. import config as C
from .. import settings as S
from ..storage import read_json, write_json, deep_merge

log = logging.getLogger("pixio.backup")

MAX_ARCHIVE = 64 * 1024 * 1024        # limite sul file ricevuto e sul contenuto estratto
MAX_ANSWER_FILES = 500
MAX_ANSWER_BYTES = 32 * 1024 * 1024
ANSWERS_PREFIX = "answers/"
MEMBERS = ("manifest.json", "config.json", "catalog.json", "clients.json", "drivers.json", "answers.json")
# stesse regole di services/answers.py: id della risposta e nome del file
ANSWER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ANSWER_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

CREDENTIALS_NOTE = ("Le password non sono incluse nel backup: quella dell'amministratore, quelle delle share SMB "
                    "delle sorgenti e quella Samba della libreria vanno reinserite dopo il ripristino.")


def _hostname():
    try:
        return socket.gethostname() or "pixio"
    except OSError:
        return "pixio"


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def archive_name(when=None):
    """pixio-backup-<host>-<data>.tar.gz"""
    host = re.sub(r"[^A-Za-z0-9._-]", "-", _hostname())[:40].strip("-") or "pixio"
    day = (when or datetime.datetime.now()).strftime("%Y%m%d")
    return f"pixio-backup-{host}-{day}.tar.gz"


# ---------------------------------------------------------------- esportazione
def export_config():
    """Configurazione effettiva ripulita dai segreti."""
    cfg = json.loads(json.dumps(S.load()))       # copia profonda
    if isinstance(cfg.get("auth"), dict):
        cfg["auth"].pop("password_hash", None)
    if isinstance(cfg.get("windows"), dict):
        cfg["windows"].pop("smb_password", None)
    return cfg


def _add_bytes(tf, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(datetime.datetime.now().timestamp())
    info.mode = 0o600
    info.uid = info.gid = 0
    info.uname = info.gname = "pixio"
    tf.addfile(info, io.BytesIO(data))


def _add_json(tf, name, obj):
    _add_bytes(tf, name, (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _read_file(path):
    try:
        with open(path, "rb") as f:
            return f.read(MAX_ARCHIVE + 1)
    except (OSError, ValueError):
        return None


def _add_answers(tf):
    """File delle risposte automatiche: answers/<id>/<file>. Ritorna i nomi aggiunti."""
    added, total = [], 0
    try:
        ids = sorted(os.listdir(C.ANSWERS_DIR))
    except OSError:
        return added
    for aid in ids:
        d = os.path.join(C.ANSWERS_DIR, aid)
        if not ANSWER_ID_RE.match(aid) or not os.path.isdir(d):
            continue
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in names:
            full = os.path.join(d, fn)
            if not ANSWER_FILE_RE.match(fn) or not os.path.isfile(full) or os.path.islink(full):
                continue
            data = _read_file(full)
            if data is None:
                continue
            total += len(data)
            if len(added) >= MAX_ANSWER_FILES or total > MAX_ANSWER_BYTES:
                log.warning("backup: file delle risposte troppo numerosi o troppo grandi, elenco troncato")
                return added
            name = f"{ANSWERS_PREFIX}{aid}/{fn}"
            _add_bytes(tf, name, data)
            added.append(name)
    return added


def manifest(files):
    return {"app": C.APP_NAME, "version": C.VERSION, "created": _now(), "hostname": _hostname(),
            "files": sorted(files), "note": CREDENTIALS_NOTE}


def export_archive():
    """Ritorna i byte dell'archivio .tar.gz."""
    buf = io.BytesIO()
    files = ["config.json"]
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        _add_json(tf, "config.json", export_config())
        for name, path in (("catalog.json", C.CATALOG_FILE), ("clients.json", C.CLIENTS_FILE),
                           ("drivers.json", C.DRIVERS_FILE), ("answers.json", C.ANSWERS_FILE)):
            data = _read_file(path)
            if data is not None:
                _add_bytes(tf, name, data)
                files.append(name)
        files.extend(_add_answers(tf))
        _add_json(tf, "manifest.json", manifest(files))
    return buf.getvalue()


# ---------------------------------------------------------------- ripristino
def _allowed(name):
    if name in MEMBERS:
        return True
    if name.startswith(ANSWERS_PREFIX):
        parts = name[len(ANSWERS_PREFIX):].split("/")
        return len(parts) == 2 and bool(ANSWER_ID_RE.match(parts[0])) and bool(ANSWER_FILE_RE.match(parts[1]))
    return False


def _clean_name(name):
    n = str(name or "").replace("\\", "/")
    while n.startswith("./"):
        n = n[2:]
    return n


def read_archive(data):
    """Apre e valida l'archivio. Ritorna ({nome: byte}, avvisi). ValueError se non è sicuro."""
    if not data:
        raise ValueError("Nessun file di backup ricevuto")
    if len(data) > MAX_ARCHIVE:
        raise ValueError(f"Archivio troppo grande (massimo {MAX_ARCHIVE >> 20} MB)")
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except (tarfile.TarError, EOFError, OSError):
        raise ValueError("Archivio non valido: atteso un file .tar.gz creato da Pixio")
    entries, warnings, total = {}, [], 0
    try:
        for m in tf.getmembers():
            name = _clean_name(m.name)
            if not name or name.startswith("/") or ".." in name.split("/"):
                raise ValueError(f"Archivio non sicuro: percorso non ammesso ({m.name})")
            if m.isdir():
                continue
            if not m.isreg():
                raise ValueError(f"Archivio non sicuro: {name} non è un file normale")
            if not _allowed(name):
                warnings.append(f"Voce ignorata (non prevista): {name}")
                continue
            total += max(0, int(m.size or 0))
            if total > MAX_ARCHIVE:
                raise ValueError(f"Contenuto dell'archivio troppo grande (massimo {MAX_ARCHIVE >> 20} MB)")
            f = tf.extractfile(m)
            entries[name] = f.read(MAX_ARCHIVE + 1) if f is not None else b""
    except tarfile.TarError:
        raise ValueError("Archivio danneggiato: impossibile leggerlo fino in fondo")
    finally:
        tf.close()
    if not entries:
        raise ValueError("L'archivio non contiene nessun file di configurazione di Pixio")
    return entries, warnings


def _member_json(entries, name, warnings):
    raw = entries.get(name)
    if raw is None:
        return None
    try:
        d = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        warnings.append(f"{name}: JSON non valido, ignorato")
        return None
    if not isinstance(d, dict):
        warnings.append(f"{name}: formato inatteso, ignorato")
        return None
    return d


def _restore_config(entries, restored, warnings):
    inc = _member_json(entries, "config.json", warnings)
    if inc is None:
        return
    cur = read_json(C.CONFIG_FILE, {})
    if not isinstance(cur, dict):
        cur = {}
    merged = deep_merge(cur, inc)
    # le credenziali restano quelle della macchina: il backup non le contiene
    merged.setdefault("auth", {})["password_hash"] = (cur.get("auth") or {}).get("password_hash", "")
    merged.setdefault("windows", {})["smb_password"] = (cur.get("windows") or {}).get("smb_password", "")
    write_json(C.CONFIG_FILE, merged, mode=0o640)
    restored.append("Impostazioni (rete, menu, libreria, cache, aggiornamenti)")
    warnings.append(CREDENTIALS_NOTE)
    warnings.append("Applica le impostazioni (o riavvia Pixio) per rigenerare dnsmasq, nginx e Samba.")


def _restore_catalog(entries, restored, warnings):
    cat = _member_json(entries, "catalog.json", warnings)
    if cat is None:
        return
    isos = cat.get("isos")
    if not isinstance(isos, dict):
        warnings.append("catalog.json: elenco delle ISO mancante, ignorato")
        return
    for e in isos.values():
        if not isinstance(e, dict):
            continue
        p = (e.get("cache") or {}).get("path") or ""
        if not (isinstance(p, str) and p and os.path.isfile(p)):
            e["cache"] = {"status": "none", "path": "", "progress": 0}
    write_json(C.CATALOG_FILE, cat)
    restored.append(f"Catalogo ISO ({len(isos)} voci)")
    warnings.append("Avvia una scansione: i percorsi delle ISO vengono riallineati alle sorgenti presenti.")


def _restore_simple(entries, name, path, label, restored, warnings, count_key=None):
    d = _member_json(entries, name, warnings)
    if d is None:
        return
    write_json(path, d)
    n = len(d.get(count_key) or {}) if count_key else len(d)
    restored.append(f"{label} ({n})")


def _restore_answer_files(entries, restored, warnings):
    files = sorted(n for n in entries if n.startswith(ANSWERS_PREFIX))
    written = 0
    for name in files:
        aid, fn = name[len(ANSWERS_PREFIX):].split("/", 1)
        d = os.path.join(C.ANSWERS_DIR, aid)
        try:
            os.makedirs(d, exist_ok=True)
            full = os.path.join(d, fn)
            with open(full, "wb") as f:
                f.write(entries[name])
            try:
                os.chmod(full, 0o664)
            except OSError:
                pass
            written += 1
        except OSError as ex:
            warnings.append(f"{name}: scrittura non riuscita ({ex})")
    if written:
        restored.append(f"File delle risposte automatiche ({written})")


def restore(data):
    """Ripristina le parti presenti nell'archivio. {restored:[str], warnings:[str]}."""
    entries, warnings = read_archive(data)
    restored = []
    info = _member_json(entries, "manifest.json", warnings)
    if info:
        ver = info.get("version")
        if ver and ver != C.VERSION:
            warnings.append(f"Backup creato con Pixio {ver} (versione attuale {C.VERSION}).")
    _restore_config(entries, restored, warnings)
    _restore_catalog(entries, restored, warnings)
    _restore_simple(entries, "clients.json", C.CLIENTS_FILE, "Client PXE", restored, warnings)
    _restore_simple(entries, "drivers.json", C.DRIVERS_FILE, "Cartelle driver", restored, warnings,
                    count_key="folders")
    _restore_simple(entries, "answers.json", C.ANSWERS_FILE, "Risposte automatiche", restored, warnings,
                    count_key="answers")
    _restore_answer_files(entries, restored, warnings)
    if not restored:
        raise ValueError("L'archivio non contiene niente da ripristinare")
    return {"restored": restored, "warnings": warnings}
