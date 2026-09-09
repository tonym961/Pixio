"""Libreria driver: cartelle in DRIVERS_DIR (share Samba in scrittura + upload web), flag per cartella in DRIVERS_FILE.

winpe_inject: i file .inf/.sys/.cat/.dll al primo livello della cartella vengono iniettati nel WinPE via wimboot
              (finiscono in X:\\Windows\\System32) e caricati con drvload prima di wpeinit -> driver di rete/storage.
setup_load:   dopo aver mappato la share, drvload ricorsivo di tutti i .inf della cartella prima di setup.exe.

I pacchetti driver contengono spesso anche l'installatore .exe, file di lingua .ini, documentazione: file che
non servono all'installazione automatica. USEFUL_EXT elenca le estensioni che servono davvero; ogni file ha il
campo "useful" e ogni cartella i contatori useful_files / ignored_files (docs/API.md, sezione 14).
"""
import os
import re
import shutil

from .. import config as C
from ..storage import read_json, update_json

FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,63}$")
FILE_RE = re.compile(r"^[^/\\\x00]{1,200}$")
WINPE_EXT = (".inf", ".sys", ".cat", ".dll")
# estensioni che servono davvero a installare un driver (tutto il resto e' scarto: .exe, .txt, .ini, ...)
USEFUL_EXT = (".inf", ".sys", ".cat", ".dll", ".bin", ".dat", ".cab", ".sepolicy")
MAX_INJECT_BYTES = 256 * 1024 * 1024


def is_useful(name):
    """True se il file serve all'installazione del driver (estensione in USEFUL_EXT)."""
    return str(name or "").lower().endswith(USEFUL_EXT)


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
            rel = os.path.relpath(full, folder).replace(os.sep, "/")
            out.append({"name": rel, "size": st.st_size, "mtime": int(st.st_mtime),
                        "useful": is_useful(fn)})
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
        esclusi = set(str(x) for x in (f.get("excluded") or []))
        # conteggio coerente con winpe_inject_files(): sottocartelle comprese, 32 bit escluse, nomi appiattiti
        inject, visti = [], set()
        for x in sorted([y for y in files if y["name"].lower().endswith(WINPE_EXT) and not _inject_skip(y["name"])],
                        key=lambda y: (_inject_rank(y["name"]), y["name"].lower())):
            nome = x["name"].split("/")[-1].lower()
            if nome in visti:
                continue
            visti.add(nome)
            if x["name"] in esclusi:
                x["excluded"] = True
                continue
            inject.append(x)
        useful = sum(1 for x in files if x["useful"])
        out.append({
            "name": name, "files": files, "count": len(files), "size": sum(x["size"] for x in files),
            "useful_files": useful, "ignored_files": len(files) - useful,
            "inf_count": sum(1 for x in files if x["name"].lower().endswith(".inf")),
            "winpe_files": len(inject), "winpe_size": sum(x["size"] for x in inject),
            "winpe_inject": bool(f.get("winpe_inject")), "setup_load": bool(f.get("setup_load")),
            "excluded": sorted(esclusi),
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


def excluded_of(name):
    """File esclusi a mano dall'iniezione nel WinPE, per questa cartella."""
    f = _flags()["folders"].get(name) or {}
    return [str(x) for x in (f.get("excluded") or [])]


def set_excluded(name, rel, escluso):
    """Include o esclude un singolo file dall'iniezione nel WinPE."""
    folder_path(name)
    rel = str(rel).strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("Nome file non valido")

    def upd(d):
        f = d.setdefault("folders", {}).setdefault(name, {})
        ex = [x for x in (f.get("excluded") or []) if x != rel]
        if escluso:
            ex.append(rel)
        f["excluded"] = ex
        return d
    update_json(C.DRIVERS_FILE, upd, default={})
    for f in list_folders():
        if f["name"] == name:
            return f
    return None


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


def set_flags_many(names, patch):
    """Applica lo stesso patch a piu' cartelle. Ritorna {"updated": [nomi], "errors": {nome: messaggio}}."""
    updated, errors, ok_names = [], {}, []
    for raw in names:
        name = str(raw or "").strip()
        try:
            p = folder_path(name)
        except ValueError as e:
            errors[str(raw)] = str(e)
            continue
        if not os.path.isdir(p):
            errors[name] = "Cartella driver non trovata"
            continue
        ok_names.append(name)

    if ok_names:
        def upd(d):
            folders = d.setdefault("folders", {})
            for name in ok_names:
                f = folders.setdefault(name, {})
                for k in ("winpe_inject", "setup_load"):
                    if k in patch:
                        f[k] = bool(patch[k])
                if "note" in patch:
                    f["note"] = str(patch["note"])[:200]
            return d
        update_json(C.DRIVERS_FILE, upd, default={})
        updated = ok_names
    return {"updated": updated, "errors": errors}


def delete_file(name, rel):
    base = folder_path(name)
    full = os.path.realpath(os.path.join(base, rel))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        raise FileNotFoundError("File non trovato")
    os.unlink(full)


def clean_folder(name):
    """Elimina dalla cartella i file che non servono al driver (.exe, documentazione, file di lingua...).
    Ritorna l'elenco dei percorsi relativi rimossi."""
    base = folder_path(name)
    if not os.path.isdir(base):
        raise FileNotFoundError("Cartella non trovata")
    removed = []
    for x in _walk(base):
        if is_useful(x["name"]):
            continue
        full = os.path.realpath(os.path.join(base, x["name"]))
        if not full.startswith(base + os.sep) or not os.path.isfile(full):
            continue
        try:
            os.unlink(full)
            removed.append(x["name"])
        except OSError:
            pass
    # sottocartelle rimaste vuote: si tolgono, partendo dalle più profonde
    for dirpath, dirnames, filenames in os.walk(base, topdown=False):
        if dirpath == base or dirnames or filenames:
            continue
        try:
            os.rmdir(dirpath)
        except OSError:
            pass
    return removed


def clean_folders(names):
    """Pulizia su più cartelle: {'cleaned': {nome: [file]}, 'errors': {nome: messaggio}}."""
    cleaned, errors = {}, {}
    for n in names:
        try:
            cleaned[n] = clean_folder(n)
        except (ValueError, FileNotFoundError) as e:
            errors[n] = str(e)
    return {"cleaned": cleaned, "errors": errors}


# Sottocartelle da saltare (architetture diverse da x64) e da preferire, nell'ordine.
SKIP_DIRS = ("x86", "i386", "ia64", "arm", "arm64", "win32", "32bit", "wow64")
PREFER_DIRS = ("x64", "amd64", "win11", "win10", "w11", "w10", "winx64", "64bit")


def _inject_rank(rel):
    """Ordine di preferenza fra file con lo stesso nome: prima la radice, poi le cartelle a 64 bit."""
    parts = rel.lower().split("/")[:-1]
    if not parts:
        return 0
    if any(p in PREFER_DIRS for p in parts):
        return 1
    return 2 + len(parts)


def _inject_skip(rel):
    """Salta le sottocartelle di altre architetture: iniettarle creerebbe conflitti di nome."""
    parts = rel.lower().split("/")[:-1]
    return any(p in SKIP_DIRS for p in parts)


def winpe_inject_files():
    """[(cartella, nome_destinazione, percorso)] da iniettare nel WinPE (flag winpe_inject).

    I file vengono presi anche dalle sottocartelle, perché i pacchetti driver sono spesso divisi per
    architettura o versione di Windows. wimboot li mette tutti nella stessa cartella del WinPE, quindi il
    nome viene appiattito: le sottocartelle a 32 bit si saltano e, a parità di nome, vince la radice o la
    cartella a 64 bit (un .inf cerca i propri file per nome, senza percorso)."""
    out, seen, total = [], {}, 0
    for f in list_folders():
        if not f["winpe_inject"]:
            continue
        base = os.path.join(C.DRIVERS_DIR, f["name"])
        esclusi = set(f.get("excluded") or [])
        candidati = [x for x in f["files"]
                     if x["name"].lower().endswith(WINPE_EXT) and not _inject_skip(x["name"])
                     and x["name"] not in esclusi]
        candidati.sort(key=lambda x: (_inject_rank(x["name"]), x["name"].lower()))
        for x in candidati:
            nome = x["name"].split("/")[-1]
            key = nome.lower()
            if key in seen:
                continue
            if total + x["size"] > MAX_INJECT_BYTES:
                continue
            total += x["size"]
            seen[key] = True
            out.append((f["name"], nome, os.path.join(base, *x["name"].split("/"))))
    return out


def setup_load_folders():
    return [f["name"] for f in list_folders() if f["setup_load"] and f["valid_name"]]


def http_url(server_ip, folder, filename):
    from urllib.parse import quote
    return f"http://{server_ip}/pxe/drivers/{quote(folder)}/{quote(filename)}"
