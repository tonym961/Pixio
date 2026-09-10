"""Log del programma di installazione depositati dai PC (docs/API.md, sezione 22).

I client scrivono via SMB nella share [pxelog] (in scrittura, l'unica: sta fuori da /srv/pixio/http, che
e' servito in sola lettura). Una cartella per tentativo, con il nome deciso dal server quando ha generato
install.cmd: <data>-<ora>-<ip>-<immagine>. Dentro: riepilogo.txt, rete.txt, disco.txt, autounattend.xml e
le cartelle Panther copiate dal PC.

Tutto quello che sta qui dentro l'ha scritto un client, quindi non ci si fida di niente: i nomi vengono
validati uno per uno, i percorsi risolti e confrontati con la radice, i file letti solo in coda e con un
tetto. Un contenuto strano deve al massimo essere illeggibile, mai far uscire dalla cartella.
"""
import logging
import os
import re
import shutil
import stat

from .. import config as C
from .. import settings as S

log = logging.getLogger("pixio.setuplogs")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")     # nome di cartella accettato
REL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,95}(/[A-Za-z0-9][A-Za-z0-9._ -]{0,95}){0,5}$")
SUMMARY_FILE = "riepilogo.txt"
SUMMARY_MAX = 16 * 1024          # riepilogo.txt e' una decina di righe: oltre, e' un file sbagliato
TAIL_BYTES = 256 * 1024          # quanto si legge della coda di un file di testo
MAX_FILES = 2000                 # tetto all'elenco dei file di una cartella
ERR_LINES = 6                    # righe di errore mostrate nell'elenco
TEXT_EXT = (".log", ".txt", ".xml", ".ini", ".cmd", ".etl")

# Le righe che raccontano il guasto. Il setup scrive in inglese qualunque sia la lingua dell'immagine.
_ERR_RE = re.compile(r",\s*(Error|Warning)\s", re.IGNORECASE)


def base_dir():
    return C.SETUPLOGS_DIR


def share_name(cfg=None):
    cfg = cfg or S.load()
    return (cfg.get("windows", {}) or {}).get("setup_logs_share_name") or "pxelog"


def enabled(cfg=None):
    """La raccolta e' attiva solo se lo e' anche la share [pxe]: l'helper le esporta insieme."""
    cfg = cfg or S.load()
    win = cfg.get("windows", {}) or {}
    return bool(win.get("smb_export_enabled")) and bool(win.get("setup_logs_enabled", True))


def share_unc(cfg=None):
    cfg = cfg or S.load()
    ip = (cfg.get("network", {}) or {}).get("server_ip") or ""
    return f"\\\\{ip}\\{share_name(cfg)}" if ip else ""


# ---------------------------------------------------------------- percorsi sicuri
def folder_path(name):
    """Percorso della cartella <name>, verificato. ValueError se il nome non va, FileNotFoundError se non c'e'."""
    if not NAME_RE.match(str(name or "")):
        raise ValueError("Nome della cartella non valido")
    root = os.path.realpath(base_dir())
    path = os.path.realpath(os.path.join(root, name))
    if path != root and not path.startswith(root + os.sep):
        raise ValueError("Nome della cartella non valido")
    if not os.path.isdir(path):
        raise FileNotFoundError("Log non trovato")
    return path


def file_path(name, rel):
    """Percorso di un file dentro la cartella, verificato (niente '..', niente collegamenti che escono)."""
    folder = folder_path(name)
    # niente strip dei "/" iniziali: un percorso assoluto deve essere rifiutato, non convertito
    rel = str(rel or "").replace("\\", "/").strip()
    if not REL_RE.match(rel):
        raise ValueError("Nome del file non valido")
    path = os.path.realpath(os.path.join(folder, *rel.split("/")))
    if not path.startswith(os.path.realpath(folder) + os.sep):
        raise ValueError("Nome del file non valido")
    if not os.path.isfile(path):
        raise FileNotFoundError("File non trovato")
    return path


# ---------------------------------------------------------------- lettura
def _walk(folder):
    """[(percorso relativo con /, percorso assoluto, dimensione, mtime)] dei soli file veri."""
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))][:200]
        for fn in files:
            full = os.path.join(root, fn)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):     # collegamenti e file speciali: fuori
                continue
            rel = os.path.relpath(full, folder).replace(os.sep, "/")
            out.append((rel, full, st.st_size, st.st_mtime))
            if len(out) >= MAX_FILES:
                return sorted(out)
    return sorted(out)


def tail_text(path, max_bytes=TAIL_BYTES):
    """Coda del file come testo. Ritorna (testo, troncato)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read(max_bytes)
    except OSError as e:
        raise FileNotFoundError(f"File non leggibile: {e}")
    truncated = size > max_bytes
    # i log del setup sono UTF-8 o UTF-16LE (Windows scrive entrambi): si riconosce dai byte nulli
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(data) > 40 and data[1::2].count(0) > len(data) // 4):
        text = data.decode("utf-16", "replace")
    else:
        text = data.decode("utf-8", "replace")
    if truncated:
        text = text.split("\n", 1)[-1]           # la prima riga e' tagliata a meta'
    return text.replace("\r\n", "\n").replace("\x00", ""), truncated


def parse_summary(path):
    """riepilogo.txt -> {chiave: valore}. Righe che non sono "chiave: valore" ignorate."""
    meta = {}
    try:
        if os.path.getsize(path) > SUMMARY_MAX:
            return meta
        with open(path, "rb") as f:
            raw = f.read(SUMMARY_MAX)
    except OSError:
        return meta
    for line in raw.decode("utf-8", "replace").replace("\r\n", "\n").split("\n"):
        k, sep, v = line.partition(":")
        k = k.strip().lower()
        if not sep or not k or len(k) > 40 or len(meta) >= 40:
            continue
        meta[k] = v.strip()[:200]
    return meta


def error_lines(path, limit=ERR_LINES):
    """Ultime righe di errore/avviso di un log del setup (o le ultime righe, se non ce ne sono)."""
    try:
        text, _ = tail_text(path)
    except (FileNotFoundError, OSError):
        return []
    righe = [l.strip() for l in text.split("\n") if l.strip()]
    err = [l for l in righe if _ERR_RE.search(l)]
    return [l[:300] for l in (err or righe)[-limit:]]


def _errori(folder, files):
    """Righe di errore da mostrare nell'elenco: prima setuperr.log (piccolo), poi setupact.log."""
    per_nome = {}
    for rel, full, size, _mt in files:
        per_nome.setdefault(os.path.basename(rel).lower(), []).append((size, full))
    for nome in ("setuperr.log", "setupact.log"):
        for size, full in sorted(per_nome.get(nome, []), reverse=True):
            if size <= 0:
                continue
            righe = error_lines(full)
            if righe:
                return righe, os.path.relpath(full, folder).replace(os.sep, "/")
    return [], ""


def _summary(folder):
    """Riga d'elenco di una cartella di log."""
    name = os.path.basename(folder)
    files = _walk(folder)
    meta = parse_summary(os.path.join(folder, SUMMARY_FILE))
    try:
        mtime = os.path.getmtime(folder)
    except OSError:
        mtime = 0
    if files:
        mtime = max(mtime, max(f[3] for f in files))
    errori, err_file = _errori(folder, files)
    esito = meta.get("esito setup.exe", "")
    return {
        "name": name,
        "received": mtime,
        "files": len(files),
        "size": sum(f[2] for f in files),
        "image": meta.get("immagine") or meta.get("slug") or "",
        "slug": meta.get("slug", ""),
        "client": meta.get("client", ""),
        "mac": meta.get("mac", ""),
        "pc": meta.get("pc", ""),
        "started": meta.get("avvio", ""),
        "esito": esito,
        "ok": esito == "0",
        "errors": errori,
        "error_file": err_file,
        "has_summary": os.path.isfile(os.path.join(folder, SUMMARY_FILE)),
    }


def list_folders():
    """Elenco dei log ricevuti, dal piu' recente. Una cartella illeggibile viene saltata, non fa fallire tutto."""
    root = base_dir()
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for n in names:
        path = os.path.join(root, n)
        if not NAME_RE.match(n) or os.path.islink(path) or not os.path.isdir(path):
            continue
        try:
            out.append(_summary(path))
        except OSError as e:
            log.warning("log %s non leggibile: %s", n, e)
    out.sort(key=lambda d: (d["received"], d["name"]), reverse=True)
    return out


def detail(name, rel=None):
    """Cartella + elenco dei file + contenuto del file chiesto (o del riepilogo)."""
    folder = folder_path(name)
    files = _walk(folder)
    d = _summary(folder)
    d["file_list"] = [{"name": r, "size": s, "mtime": m, "text": r.lower().endswith(TEXT_EXT)}
                      for r, _f, s, m in files]
    scelto = rel or (SUMMARY_FILE if any(r == SUMMARY_FILE for r, _f, _s, _m in files) else "")
    if not scelto and files:
        scelto = files[0][0]
    d["file"] = scelto
    d["content"], d["truncated"] = tail_text(file_path(name, scelto)) if scelto else ("", False)
    return d


def delete(name):
    shutil.rmtree(folder_path(name))
    log.info("log dell'installazione %s eliminato", name)


def delete_all():
    n = 0
    for d in list_folders():
        try:
            delete(d["name"])
            n += 1
        except (OSError, ValueError, FileNotFoundError) as e:
            log.warning("log %s non eliminato: %s", d["name"], e)
    return n


def prune(keep=None):
    """Tiene solo le ultime `keep` cartelle (impostazione windows.setup_logs_keep). Ritorna quante ne toglie."""
    if keep is None:
        try:
            keep = int((S.load().get("windows", {}) or {}).get("setup_logs_keep", 50))
        except (TypeError, ValueError):
            keep = 50
    keep = max(1, min(1000, int(keep)))
    tutte = list_folders()
    if len(tutte) <= keep:
        return 0
    n = 0
    for d in tutte[keep:]:
        try:
            delete(d["name"])
            n += 1
        except (OSError, ValueError, FileNotFoundError) as e:
            log.warning("log %s non eliminato: %s", d["name"], e)
    if n:
        log.info("log delle installazioni: eliminate %d cartelle oltre le %d da tenere", n, keep)
    return n
