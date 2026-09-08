"""Libreria driver: cartelle in DRIVERS_DIR (share Samba in scrittura + upload web), flag per cartella in DRIVERS_FILE.

winpe_inject: i file .inf/.sys/.cat/.dll al primo livello della cartella vengono iniettati nel WinPE via wimboot
              (finiscono in X:\\Windows\\System32) e caricati con drvload prima di wpeinit -> driver di rete/storage.
setup_load:   dopo aver mappato la share, drvload ricorsivo di tutti i .inf della cartella prima di setup.exe.
"""
import os
import re
import shutil

from .. import config as C
from ..storage import read_json, update_json

FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,63}$")
FILE_RE = re.compile(r"^[^/\\\x00]{1,200}$")
WINPE_EXT = (".inf", ".sys", ".cat", ".dll")
MAX_INJECT_BYTES = 256 * 1024 * 1024


def _flags():
    d = read_json(C.DRIVERS_FILE, {})
    d.setdefault("folders", {})
    return d


def check_folder(name):
    if not FOLDER_RE.match(name or "") or name in (".", ".."):
        raise ValueError("Nome cartella non valido (lettere, numeri, spazi, . _ - ( ) +, max 64)")
    return name


def check_file(name):
    if not FILE_RE.match(name or "") or name in (".", "..") or name.startswith("."):
        raise ValueError("Nome file non valido")
    return name


def folder_path(name):
    p = os.path.realpath(os.path.join(C.DRIVERS_DIR, check_folder(name)))
    if not p.startswith(os.path.realpath(C.DRIVERS_DIR) + os.sep):
        raise ValueError("Percorso non consentito")
    return p


def _walk(folder):
    """Elenco file (ricorsivo, percorsi relativi alla cartella) con dimensione e mtime."""
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, folder)
            out.append({"name": rel, "size": st.st_size, "mtime": int(st.st_mtime)})
            if len(out) >= 2000:
                return out
    return out


def list_folders():
    os.makedirs(C.DRIVERS_DIR, exist_ok=True)
    flags = _flags()["folders"]
    out = []
    for name in sorted(os.listdir(C.DRIVERS_DIR), key=str.lower):
        p = os.path.join(C.DRIVERS_DIR, name)
        if not os.path.isdir(p) or name.startswith("."):
            continue
        files = _walk(p)
        f = flags.get(name, {})
        inject = [x for x in files if "/" not in x["name"] and x["name"].lower().endswith(WINPE_EXT)]
        out.append({
            "name": name, "files": files, "count": len(files), "size": sum(x["size"] for x in files),
            "inf_count": sum(1 for x in files if x["name"].lower().endswith(".inf")),
            "winpe_files": len(inject), "winpe_size": sum(x["size"] for x in inject),
            "winpe_inject": bool(f.get("winpe_inject")), "setup_load": bool(f.get("setup_load")),
            "note": f.get("note", ""), "valid_name": bool(FOLDER_RE.match(name)),
        })
    return out


def create_folder(name):
    p = folder_path(name)
    if os.path.exists(p):
        raise FileExistsError("Esiste già una cartella con questo nome")
    os.makedirs(p, mode=0o2775)
    return name


def delete_folder(name):
    p = folder_path(name)
    if os.path.isdir(p):
        shutil.rmtree(p)

    def upd(d):
        d.setdefault("folders", {}).pop(name, None)
        return d
    update_json(C.DRIVERS_FILE, upd, default={})


def set_flags(name, patch):
    folder_path(name)

    def upd(d):
        f = d.setdefault("folders", {}).setdefault(name, {})
        for k in ("winpe_inject", "setup_load"):
            if k in patch:
                f[k] = bool(patch[k])
        if "note" in patch:
            f["note"] = str(patch["note"])[:200]
        return d
    update_json(C.DRIVERS_FILE, upd, default={})
    for f in list_folders():
        if f["name"] == name:
            return f
    return None


def delete_file(name, rel):
    base = folder_path(name)
    full = os.path.realpath(os.path.join(base, rel))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        raise FileNotFoundError("File non trovato")
    os.unlink(full)


def winpe_inject_files():
    """[(folder, filename, abs_path)] da iniettare nel WinPE (flag winpe_inject). Nomi duplicati: vince la prima cartella."""
    out, seen, total = [], set(), 0
    for f in list_folders():
        if not f["winpe_inject"]:
            continue
        base = os.path.join(C.DRIVERS_DIR, f["name"])
        for x in f["files"]:
            if "/" in x["name"] or not x["name"].lower().endswith(WINPE_EXT):
                continue
            key = x["name"].lower()
            if key in seen:
                continue
            total += x["size"]
            if total > MAX_INJECT_BYTES:
                break
            seen.add(key)
            out.append((f["name"], x["name"], os.path.join(base, x["name"])))
    return out


def setup_load_folders():
    return [f["name"] for f in list_folders() if f["setup_load"] and f["valid_name"]]


def http_url(server_ip, folder, filename):
    from urllib.parse import quote
    return f"http://{server_ip}/pxe/drivers/{quote(folder)}/{quote(filename)}"
