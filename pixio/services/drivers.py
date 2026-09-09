"""Libreria driver: cartelle in DRIVERS_DIR (share Samba in scrittura + upload web), flag per cartella in DRIVERS_FILE.

winpe_inject: i file .inf/.sys/.cat/.dll al primo livello della cartella vengono iniettati nel WinPE via wimboot
              (finiscono in X:\\Windows\\System32) e caricati con drvload prima di wpeinit -> driver di rete/storage.
setup_load:   dopo aver mappato la share, drvload ricorsivo di tutti i .inf della cartella prima di setup.exe.

I pacchetti driver contengono spesso anche l'installatore .exe, file di lingua .ini, documentazione: file che
non servono all'installazione automatica. USEFUL_EXT elenca le estensioni che servono davvero; ogni file ha il
campo "useful" e ogni cartella i contatori useful_files / ignored_files (docs/API.md, sezione 14).

apply_to:     i driver RAID di un server non servono su un PC da ufficio. Ogni cartella dice a quali immagini si
              applica ({"mode": "all"|"groups"|"isos", "groups": [...], "isos": [slug]}), e winpe_inject_files(iso)
              / setup_load_folders(iso) filtrano in base alla voce di catalogo che si sta avviando
              (docs/API.md, sezione 15). Senza argomento si comportano come prima: nessun filtro.
excluded:     elenco dei percorsi relativi esclusi a mano dall'iniezione nel WinPE.
coerenza .inf: un pacchetto driver contiene spesso piu' copie dello stesso .inf e wimboot appiattisce tutto in
              X:\\Windows\\System32, quindi drvload cerca i file per nome. Ogni .inf dichiara i propri file
              (SourceDisksFiles, CopyFiles, ServiceBinary): fra piu' copie vince quella che li ha davvero
              accanto, e i file dichiarati si prendono dalla cartella dell'.inf scelto. Un .inf che dichiara
              file che non ci sono viene segnalato alla GUI in winpe_missing (docs/API.md, sezione 21).
"""
import os
import re
import shutil

from .. import config as C
from ..storage import read_json, update_json

FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,63}$")
FILE_RE = re.compile(r"^[^/\\\x00]{1,200}$")
# Nel WinPE i file iniettati finiscono tutti in X:\Windows\System32: le .dll dei pacchetti driver
# (co-installer e componenti in modalità utente) non servono a drvload e, con nomi comuni come
# "generic.dll", rischiano di sovrascrivere file di sistema e far riavviare il PC. Restano comunque
# nella cartella e raggiungibili dalla share con "Carica prima del setup".
WINPE_EXT = (".inf", ".sys", ".cat")
# estensioni che servono davvero a installare un driver (tutto il resto e' scarto: .exe, .txt, .ini, ...)
USEFUL_EXT = (".inf", ".sys", ".cat", ".dll", ".bin", ".dat", ".cab", ".sepolicy")
MAX_INJECT_BYTES = 256 * 1024 * 1024

# apply_to: a quali immagini si applica la cartella (docs/API.md, sezione 15)
APPLY_MODES = ("all", "groups", "isos")
MAX_APPLY_GROUPS = 50        # i gruppi sono quelli del menu di boot: pochi e con nomi brevi
MAX_APPLY_ISOS = 500         # una per voce di catalogo, con ampio margine
MAX_GROUP_LEN = 60           # stessa lunghezza del campo "group" del catalogo


def default_apply_to():
    """Valore predefinito: la cartella vale per tutte le immagini (comportamento storico)."""
    return {"mode": "all", "groups": [], "isos": []}


def check_apply_to(v):
    """Normalizza e valida apply_to. Solleva ValueError con messaggio in italiano."""
    if v is None:
        return default_apply_to()
    if not isinstance(v, dict):
        raise ValueError("apply_to: oggetto atteso ({mode, groups, isos})")
    mode = v.get("mode", "all")
    if not isinstance(mode, str) or mode.strip().lower() not in APPLY_MODES:
        raise ValueError('apply_to.mode: valori ammessi "all", "groups", "isos"')
    mode = mode.strip().lower()

    groups = v.get("groups") or []
    if not isinstance(groups, list):
        raise ValueError("apply_to.groups: elenco di nomi di gruppo atteso")
    if len(groups) > MAX_APPLY_GROUPS:
        raise ValueError(f"apply_to.groups: troppi gruppi (max {MAX_APPLY_GROUPS})")
    gs = []
    for g in groups:
        if not isinstance(g, str):
            raise ValueError("apply_to.groups: i nomi dei gruppi devono essere testo")
        g = g.replace("\r", " ").replace("\n", " ").strip()
        if not g:
            continue
        if len(g) > MAX_GROUP_LEN:
            raise ValueError(f"apply_to.groups: nome di gruppo troppo lungo (max {MAX_GROUP_LEN} caratteri)")
        if g not in gs:
            gs.append(g)

    isos = v.get("isos") or []
    if not isinstance(isos, list):
        raise ValueError("apply_to.isos: elenco di slug atteso")
    if len(isos) > MAX_APPLY_ISOS:
        raise ValueError(f"apply_to.isos: troppe immagini (max {MAX_APPLY_ISOS})")
    ss = []
    for x in isos:
        if not isinstance(x, str):
            raise ValueError("apply_to.isos: gli slug devono essere testo")
        x = x.strip()
        if not x:
            continue
        if not C.SLUG_RE.match(x):
            raise ValueError(f"apply_to.isos: slug non valido ({x[:40]})")
        if x not in ss:
            ss.append(x)

    # Un elenco vuoto e' ammesso e vuol dire "nessuna immagine": stato scomodo ma coerente, che la GUI
    # segnala con un avviso invece di rifiutare la modifica mentre l'utente sta ancora scegliendo.
    return {"mode": mode, "groups": gs, "isos": ss}


def _read_apply_to(f):
    """apply_to salvato nei flag della cartella, ripulito: i valori vecchi o rotti tornano "all"."""
    try:
        return check_apply_to((f or {}).get("apply_to"))
    except ValueError:
        return default_apply_to()


def apply_to_of(name):
    """apply_to della cartella (predefinito se non impostato)."""
    return _read_apply_to(_flags()["folders"].get(name))


def apply_matches(apply_to, iso):
    """True se la cartella vale per questa voce di catalogo. iso None = nessun filtro (anteprima generica)."""
    if iso is None:
        return True
    a = apply_to if isinstance(apply_to, dict) and apply_to.get("mode") in APPLY_MODES else default_apply_to()
    if a["mode"] == "all":
        return True
    if a["mode"] == "groups":
        g = str((iso or {}).get("group") or "").strip().lower()
        return bool(g) and g in [str(x).strip().lower() for x in (a.get("groups") or [])]
    return str((iso or {}).get("slug") or "") in [str(x) for x in (a.get("isos") or [])]


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
        inject = _mark_inject(p, files, esclusi)
        useful = sum(1 for x in files if x["useful"])
        cand = sum(1 for x in files if x["winpe_cand"])
        out.append({
            "name": name, "files": files, "count": len(files), "size": sum(x["size"] for x in files),
            "useful_files": useful, "ignored_files": len(files) - useful,
            "inf_count": sum(1 for x in files if x["name"].lower().endswith(".inf")),
            "winpe_files": len(inject), "winpe_size": sum(x["size"] for x in inject),
            "winpe_candidates": cand, "excluded_files": sum(1 for x in files if x["excluded"]),
            "winpe_inject": bool(f.get("winpe_inject")), "setup_load": bool(f.get("setup_load")),
            "excluded": sorted(esclusi), "apply_to": _read_apply_to(f),
            "winpe_missing": [{"inf": x["name"], "missing": list(x["inf_missing"])}
                              for x in inject if x.get("inf_missing")],
            "note": f.get("note", ""), "valid_name": bool(FOLDER_RE.match(name)),
        })
    return out


def _mark_inject(base, files, esclusi):
    """Segna ogni file della cartella e ritorna quelli che finiscono davvero nel WinPE.

    Stessa logica di winpe_inject_files(): sottocartelle di altre architetture saltate, un solo file per nome
    (wimboot appiattisce tutto in X:\\Windows\\System32), i file esclusi a mano lasciano il posto al gemello.
    Fra piu' copie dello stesso .inf vince quella completa, cioe' quella che ha accanto tutti i file che
    dichiara; e i file dichiarati da un .inf scelto si prendono dalla sua stessa cartella, non da un'altra
    copia (docs/API.md, sezione 21).
    Campi aggiunti a ogni file: winpe_cand (potrebbe essere iniettato), excluded (escluso a mano),
    winpe (finisce davvero nel WinPE); sui .inf candidati anche inf_missing (file dichiarati che non stanno
    accanto all'.inf)."""
    cand = []
    presenti = set(x["name"].lower() for x in files)
    for x in files:
        x["excluded"] = x["name"] in esclusi
        x["winpe_cand"] = x["name"].lower().endswith(WINPE_EXT) and not _inject_skip(x["name"])
        x["winpe"] = False
        if x["name"].lower().endswith(".inf"):
            x["inf_missing"] = []
        if x["winpe_cand"] and not x["excluded"]:
            cand.append(x)
    # ogni copia di un .inf dice di quali file ha bisogno: quelli che non le stanno accanto la rendono monca
    for x in cand:
        if not x["name"].lower().endswith(".inf"):
            continue
        x["inf_missing"] = [n for n in inf_needed_files(os.path.join(base, *x["name"].split("/")))
                            if _sibling(x["name"], n.lower()) not in presenti]
    # una copia completa batte una monca anche se sta in una cartella meno preferita
    cand.sort(key=lambda y: (1 if y.get("inf_missing") else 0, _inject_rank(y["name"]), y["name"].lower()))
    scelti = {}
    for x in cand:
        scelti.setdefault(x["name"].split("/")[-1].lower(), x)
    # coerenza: i file dichiarati da un .inf scelto arrivano dalla cartella dell'.inf, non da un'altra copia
    per_rel = {x["name"].lower(): x for x in cand}
    bloccati = set()
    for x in cand:
        nome = x["name"].split("/")[-1].lower()
        if not nome.endswith(".inf") or scelti.get(nome) is not x:
            continue
        for n in inf_needed_files(os.path.join(base, *x["name"].split("/"))):
            n = n.lower()
            fratello = per_rel.get(_sibling(x["name"], n))
            if n in bloccati or fratello is None:
                continue
            scelti[n] = fratello
            bloccati.add(n)
    inject = []
    for x in cand:
        if scelti.get(x["name"].split("/")[-1].lower()) is x:
            x["winpe"] = True
            inject.append(x)
    return inject


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
    apply_to = check_apply_to(patch["apply_to"]) if "apply_to" in patch else None

    def upd(d):
        f = d.setdefault("folders", {}).setdefault(name, {})
        for k in ("winpe_inject", "setup_load"):
            if k in patch:
                f[k] = bool(patch[k])
        if "note" in patch:
            f["note"] = str(patch["note"])[:200]
        if apply_to is not None:
            f["apply_to"] = apply_to
        return d
    update_json(C.DRIVERS_FILE, upd, default={})
    for f in list_folders():
        if f["name"] == name:
            return f
    return None


def set_flags_many(names, patch):
    """Applica lo stesso patch a piu' cartelle. Ritorna {"updated": [nomi], "errors": {nome: messaggio}}."""
    updated, errors, ok_names = [], {}, []
    apply_to = check_apply_to(patch["apply_to"]) if "apply_to" in patch else None
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
                if apply_to is not None:
                    f["apply_to"] = apply_to
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


# ---------------------------------------------------------------------------
# Lettura dei .inf: quali file dichiara come propri (docs/API.md, sezione 21)
#
# Un pacchetto driver contiene spesso piu' copie dello stesso .inf, di versioni diverse, e ogni copia ha
# accanto i file che le servono. wimboot appiattisce tutto in X:\Windows\System32, quindi drvload cerca i
# file per nome: se si inietta l'.inf di una copia e il .sys di un'altra (o non lo si inietta affatto) il
# driver non si carica. Per sceglierne una che sta in piedi bisogna sapere quali file l'.inf dichiara.
# Non serve un parser completo dell'INF: bastano i nomi citati.
INF_MAX_BYTES = 4 * 1024 * 1024      # oltre questa dimensione non e' un .inf: non si legge
_INF_NAME_RE = re.compile(r"^[A-Za-z0-9_.~()+#&\- ]{1,120}\.[A-Za-z0-9_]{1,12}$")
_INF_SECTION_RE = re.compile(r"^\[([^\]]*)\]")
_INF_CACHE = {}                      # (percorso, mtime, dimensione) -> {nome minuscolo: grafia originale}
_INF_CACHE_MAX = 500


def _inf_text(path):
    """Testo di un .inf. I file INF di Windows sono UTF-16 con BOM oppure ANSI (cp1252)."""
    with open(path, "rb") as fh:
        raw = fh.read(INF_MAX_BYTES + 1)
    if len(raw) > INF_MAX_BYTES:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def _inf_strip_comment(riga):
    """Toglie il commento (";" fino a fine riga), rispettando le virgolette."""
    virgolette = False
    for i, c in enumerate(riga):
        if c == '"':
            virgolette = not virgolette
        elif c == ";" and not virgolette:
            return riga[:i]
    return riga


def _inf_lines(text):
    """Righe logiche dell'.inf: commenti tolti e righe di continuazione ("\\" a fine riga) unite."""
    out, acc = [], ""
    for riga in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        riga = _inf_strip_comment(riga).rstrip()
        if riga.endswith("\\"):
            if len(acc) < 8000:
                acc += riga[:-1]
            continue
        riga = (acc + riga).strip()
        acc = ""
        if riga:
            out.append(riga)
    if acc.strip():
        out.append(acc.strip())
    return out


def _inf_file_name(tok):
    """Nome file da un token dell'.inf, come scritto nell'.inf: via "%12%\\", "@", virgolette e percorso.

    Ritorna "" se non e' un nome di file. La grafia originale serve per gli avvisi della GUI
    ("manca iaStorAfs.sys" si legge meglio di "manca iastorafs.sys"); i confronti si fanno in minuscolo."""
    t = str(tok or "").strip().strip('"').strip()
    if t.startswith("@"):
        t = t[1:].strip()
    t = re.sub(r"^%[^%]*%", "", t).strip()
    t = t.replace("\\", "/").split("/")[-1].strip().strip('"').strip()
    return t if _INF_NAME_RE.match(t) else ""


def _inf_declared_map(path):
    """{nome minuscolo: grafia usata nell'.inf} dei file che l'.inf dichiara come propri.

    Guarda le sezioni [SourceDisksFiles*], le righe CopyFiles (nomi diretti con "@" e sezioni di copia
    referenziate) e le righe ServiceBinary. Un .inf illeggibile o troppo grande non dichiara nulla."""
    try:
        text = _inf_text(path)
    except OSError:
        return {}
    sezioni, cur = {}, ""
    for riga in _inf_lines(text):
        m = _INF_SECTION_RE.match(riga)
        if m:
            cur = m.group(1).strip().lower()
            sezioni.setdefault(cur, [])
            continue
        if cur:
            sezioni[cur].append(riga)

    nomi, copia = {}, set()
    for sez, righe in sezioni.items():
        sorgenti = sez.split(".")[0] == "sourcedisksfiles"
        for riga in righe:
            k, _sep, v = riga.partition("=")
            if sorgenti:                      # "iaStorVD.sys = 1,,," -> il nome sta nella chiave
                n = _inf_file_name(k)
                if n:
                    nomi.setdefault(n.lower(), n)
                continue
            chiave = k.strip().lower()
            if chiave == "copyfiles":
                for tok in v.split(","):
                    tok = tok.strip()
                    if not tok:
                        continue
                    if tok.startswith("@"):   # "CopyFiles=@file.sys": nome diretto
                        n = _inf_file_name(tok)
                        if n:
                            nomi.setdefault(n.lower(), n)
                    else:                     # altrimenti e' il nome di una sezione di copia
                        copia.add(tok.strip('"').strip().lower())
            elif chiave == "servicebinary":   # "ServiceBinary = %12%\iaStorAfs.sys"
                n = _inf_file_name(v)
                if n:
                    nomi.setdefault(n.lower(), n)
    # sezioni di copia: "file-destinazione, file-sorgente, ...": conta il file sorgente, se c'e'
    for sez in copia:
        for riga in sezioni.get(sez, []):
            campi = [c.strip() for c in riga.split(",")]
            n = _inf_file_name(campi[1]) if len(campi) > 1 and campi[1] else _inf_file_name(campi[0])
            if n:
                nomi.setdefault(n.lower(), n)
    return nomi


def inf_declared_files(path):
    """Nomi (minuscoli, senza percorso) dei file che l'.inf dichiara come propri."""
    return set(_inf_declared_cached(path))


def _inf_declared_cached(path):
    """_inf_declared_map con memoria per (percorso, mtime, dimensione): list_folders gira a ogni polling."""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (path, int(st.st_mtime), st.st_size)
    v = _INF_CACHE.get(key)
    if v is None:
        if len(_INF_CACHE) >= _INF_CACHE_MAX:
            _INF_CACHE.clear()
        v = _inf_declared_map(path)
        _INF_CACHE[key] = v
    return v


def inf_needed_files(path):
    """File dichiarati dall'.inf che finirebbero anche loro nel WinPE (.inf/.sys/.cat), in ordine.

    I nomi tornano con la grafia usata nell'.inf: i confronti vanno fatti in minuscolo."""
    return sorted((orig for low, orig in _inf_declared_cached(path).items() if low.endswith(WINPE_EXT)),
                  key=str.lower)


def _dir_of(rel):
    """Cartella (percorso relativo) che contiene il file; "" se sta al primo livello."""
    return "/".join(rel.split("/")[:-1])


def _sibling(rel, nome):
    """Percorso relativo di "nome" preso accanto a "rel" (stessa cartella), in minuscolo."""
    d = _dir_of(rel).lower()
    return (d + "/" + nome) if d else nome


def winpe_inject_files(iso=None):
    """[(cartella, nome_destinazione, percorso)] da iniettare nel WinPE (flag winpe_inject).

    iso: voce di catalogo che si sta avviando (dict con "slug" e "group"); vengono usate solo le cartelle
    abbinate a quella immagine (campo apply_to). Con iso None nessun filtro: anteprima generica, come prima.

    I file vengono presi anche dalle sottocartelle, perché i pacchetti driver sono spesso divisi per
    architettura o versione di Windows. wimboot li mette tutti nella stessa cartella del WinPE, quindi il
    nome viene appiattito: le sottocartelle a 32 bit si saltano e, a parità di nome, vince la radice o la
    cartella a 64 bit (un .inf cerca i propri file per nome, senza percorso). I file esclusi a mano
    (set_excluded) restano fuori.

    Un .inf iniettato si porta dietro i file che dichiara presi dalla sua stessa cartella, anche quando un
    file con quello stesso nome era già stato scelto da un'altra cartella: se un .inf ha bisogno del suo
    iaStorAfs.sys deve avere quello che gli sta accanto, altrimenti drvload non lo carica (sezione 21).
    La sostituzione rispetta comunque MAX_INJECT_BYTES: se il file di ricambio non ci sta, resta il primo."""
    out, pos, dim, total = [], {}, [], 0     # pos: nome minuscolo -> indice in out; dim: dimensioni parallele

    def metti(cartella, base, x, forza=False):
        """Aggiunge il file all'elenco; con forza=True sostituisce il gemello già scelto altrove."""
        nonlocal total
        nome = x["name"].split("/")[-1]
        key = nome.lower()
        full = os.path.join(base, *x["name"].split("/"))
        i = pos.get(key)
        if i is not None:
            if not forza or out[i][2] == full:
                return
            delta = x["size"] - dim[i]
            if total + delta > MAX_INJECT_BYTES:
                return
            total += delta
            dim[i] = x["size"]
            out[i] = (cartella, nome, full)
            return
        if total + x["size"] > MAX_INJECT_BYTES:
            return
        total += x["size"]
        pos[key] = len(out)
        dim.append(x["size"])
        out.append((cartella, nome, full))

    cartelle = []
    for f in list_folders():
        if not f["winpe_inject"] or not apply_matches(f.get("apply_to"), iso):
            continue
        base = os.path.join(C.DRIVERS_DIR, f["name"])
        scelti = sorted([x for x in f["files"] if x.get("winpe")],
                        key=lambda x: (_inject_rank(x["name"]), x["name"].lower()))
        cartelle.append((f, base, scelti))
        for x in scelti:
            metti(f["name"], base, x)
    # secondo giro: ogni .inf iniettato tira dentro i file che dichiara, presi dalla sua cartella
    bloccati = set()
    for f, base, scelti in cartelle:
        per_rel = {x["name"].lower(): x for x in f["files"]
                   if x.get("winpe_cand") and not x.get("excluded")}
        for x in scelti:
            if not x["name"].lower().endswith(".inf"):
                continue
            for n in inf_needed_files(os.path.join(base, *x["name"].split("/"))):
                n = n.lower()
                fratello = per_rel.get(_sibling(x["name"], n))
                if n in bloccati or fratello is None:
                    continue
                bloccati.add(n)      # il primo .inf che lo chiede se lo tiene: scelta stabile
                metti(f["name"], base, fratello, forza=True)
    return out


def setup_load_folders(iso=None):
    """Cartelle da caricare con drvload prima del setup, per la voce di catalogo indicata (None = tutte)."""
    return [f["name"] for f in list_folders()
            if f["setup_load"] and f["valid_name"] and apply_matches(f.get("apply_to"), iso)]


# Tipi di ISO che avviano un WinPE e quindi possono ricevere driver iniettati o caricati prima del setup.
WINDOWS_TYPES = ("windows", "windows-legacy", "winpe-tool")


def apply_choices():
    """Scelte possibili per apply_to: {"groups": [nomi], "isos": [{slug, name, group, type}]}.

    I gruppi sono quelli del menu di boot (Impostazioni) piu' quelli gia' usati dalle voci di catalogo;
    le immagini sono quelle Windows / WinPE del catalogo, le uniche che ricevono driver."""
    groups, isos = [], []
    try:
        from .. import settings as S
        cfg = S.load()
        for g in (cfg.get("menu", {}) or {}).get("groups") or []:
            g = str(g).strip()
            if g and g not in groups:
                groups.append(g)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import catalog
        ents, _ = catalog.list_isos()
    except Exception:  # noqa: BLE001
        ents = []
    for e in ents:
        g = str(e.get("group") or "").strip()
        if g and g not in groups:
            groups.append(g)
        if (e.get("type") or "") in WINDOWS_TYPES:
            isos.append({"slug": e.get("slug"), "name": e.get("name") or e.get("slug"),
                         "group": g, "type": e.get("type"),
                         "enabled": bool(e.get("enabled"))})
    isos.sort(key=lambda x: ((x["group"] or "").lower(), (x["name"] or "").lower()))
    return {"groups": groups, "isos": isos}


def http_url(server_ip, folder, filename):
    from urllib.parse import quote
    return f"http://{server_ip}/pxe/drivers/{quote(folder)}/{quote(filename)}"
