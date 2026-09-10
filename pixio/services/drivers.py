"""Libreria driver: cartelle in DRIVERS_DIR (share Samba in scrittura + upload web), flag per cartella in DRIVERS_FILE.

winpe_inject: i file .inf/.sys/.cat della cartella vengono iniettati nel WinPE via wimboot (finiscono in
              X:\\Windows\\System32) e caricati con drvload prima di wpeinit -> driver di rete/storage. Insieme a un
              .inf iniettato ci vanno anche i file che quell'.inf dichiara e che gli stanno accanto, qualunque
              sia l'estensione (.dll, .exe, .bin, .dat): drvload fallisce anche solo per un file di CopyFiles
              che non riesce a mettere a posto.
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
              La regola per le estensioni fuori da WINPE_EXT e' "lo inietto perche' quell'.inf lo chiede", non
              "lo inietto perche' e' li'": un file dichiarato che porta il nome di un file gia' presente in
              X:\\Windows\\System32 non si inietta (winpe_shadowed), e se un'altra copia dello stesso .inf e'
              coerente mentre quella scelta no, la GUI lo dice (winpe_better).
"""
import glob
import os
import re
import shutil
import subprocess

from .. import config as C
from ..storage import read_json, update_json

FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,63}$")
FILE_RE = re.compile(r"^[^/\\\x00]{1,200}$")
# Nel WinPE i file iniettati finiscono tutti in X:\Windows\System32: una .dll qualsiasi trovata in una
# cartella driver non serve a drvload e, con nomi comuni come "generic.dll", rischia di sovrascrivere un
# file di sistema e far riavviare il PC. Da sole entrano quindi solo queste tre estensioni. Le altre
# entrano soltanto se un .inf iniettato le dichiara come proprie e stanno nella sua stessa cartella:
# l'elenco lo scrive il produttore del driver, non e' "tutto quello che c'e' nella cartella"
# (inf_extra_files, _mark_inject, docs/API.md sezione 21).
WINPE_EXT = (".inf", ".sys", ".cat")
# estensioni che servono davvero a installare un driver (tutto il resto e' scarto: .exe, .txt, .ini, ...)
USEFUL_EXT = (".inf", ".sys", ".cat", ".dll", ".bin", ".dat", ".cab", ".sepolicy")
MAX_INJECT_BYTES = 256 * 1024 * 1024

# setup_offer: se la cartella puo' finire fra i percorsi driver del file di risposta (docs/API.md, sezione 24)
SETUP_OFFER_MODES = ("auto", "mai", "sempre")
MAX_SETUP_PATHS = 64          # quante voci PathAndCredentials si scrivono al massimo nell'autounattend
MAX_SETUP_DEPTH = 8           # livelli di sottocartelle esaminati in una cartella driver
MAX_SETUP_DIRS = 500          # directory esaminate in una cartella driver
MAX_SETUP_FILES = 5000        # file esaminati in una cartella driver
MAX_SETUP_UNC = 255           # <Path> e' un percorso UNC: oltre MAX_PATH il setup non lo apre

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
        # cosa di questa cartella puo' finire fra i percorsi driver del file di risposta (sezione 24)
        offerta = _read_setup_offer(f)
        albero = setup_tree_of(p, offerta)
        for x in files:
            if x["name"].lower().endswith(".inf"):
                x["setup_missing"] = albero["infs"].get(x["name"], [])
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
            # percorsi che il file di risposta offre al programma di installazione, e sottocartelle
            # lasciate fuori perche' contengono un .inf incompleto (docs/API.md, sezione 24)
            "setup_offer": offerta, "setup_paths": albero["paths"],
            "setup_skipped": albero["skipped"], "setup_truncated": albero["truncated"],
            # cosa l'.inf iniettato dichiara e non trovera' nel WinPE: quello che sta in un'altra copia
            # (inf_missing) e quello che non c'e' in nessuna copia della cartella (inf_absent, .exe compresi)
            "winpe_missing": [{"inf": x["name"], "missing": _nomi_uniti(x["inf_missing"], x["inf_absent"])}
                              for x in inject if x.get("inf_missing") or x.get("inf_absent")],
            # dichiarati e presenti, ma con il nome di un file che il WinPE ha gia' in \\Windows\\System32
            "winpe_shadowed": [{"inf": x["name"], "files": list(x["inf_shadowed"])}
                               for x in inject if x.get("inf_shadowed")],
            # la copia scelta non e' un set coerente, un'altra si': si dice in quale cartella sta
            "winpe_better": [{"inf": x["name"], "copy": x["inf_better"], "folder": _dir_of(x["inf_better"])}
                             for x in inject if x.get("inf_better")],
            "note": f.get("note", ""), "valid_name": bool(FOLDER_RE.match(name)),
        })
    return out


def _mark_inject(base, files, esclusi):
    """Segna ogni file della cartella e ritorna quelli che finiscono davvero nel WinPE.

    Stessa logica di winpe_inject_files(): sottocartelle di altre architetture saltate, un solo file per nome
    (wimboot appiattisce tutto in X:\\Windows\\System32), i file esclusi a mano lasciano il posto al gemello.
    Fra piu' copie dello stesso .inf vince quella completa, cioe' quella che ha accanto tutti i file che
    dichiara; e i file dichiarati da un .inf scelto si prendono dalla sua stessa cartella, non da un'altra
    copia. Da soli entrano solo i .inf/.sys/.cat, ma un .inf scelto si porta dietro anche i file dichiarati
    con altra estensione (.dll, .exe...) che gli stanno accanto (docs/API.md, sezione 21).
    Campi aggiunti a ogni file: winpe_cand (potrebbe essere iniettato), excluded (escluso a mano),
    winpe (finisce davvero nel WinPE); sui .inf anche inf_missing (file dichiarati che stanno in un'altra
    copia ma non accanto a questo .inf), inf_absent (dichiarati e assenti da tutta la cartella),
    inf_shadowed (dichiarati, presenti, ma con il nome di un file di sistema del WinPE: non si iniettano) e
    inf_better (percorso di un'altra copia dello stesso .inf che invece e' un set coerente)."""
    cand = []
    presenti = set(x["name"].lower() for x in files)                        # percorsi relativi
    nomi_cartella = set(x["name"].split("/")[-1].lower() for x in files)    # nomi, ovunque nella cartella
    per_rel = {x["name"].lower(): x for x in files}
    for x in files:
        x["excluded"] = x["name"] in esclusi
        x["winpe_cand"] = x["name"].lower().endswith(WINPE_EXT) and not _inject_skip(x["name"])
        x["winpe"] = False
        if x["name"].lower().endswith(".inf"):
            x["inf_missing"], x["inf_absent"], x["inf_shadowed"], x["inf_better"] = [], [], [], ""
        if x["winpe_cand"] and not x["excluded"]:
            cand.append(x)

    # ogni copia di un .inf dice di quali file ha bisogno: si guarda quali le stanno accanto e quali no
    infs = [x for x in files if x["name"].lower().endswith(".inf") and not _inject_skip(x["name"])]
    coerente = {}          # percorso dell'.inf -> True se tutti i file che dichiara sono davvero iniettabili
    for x in infs:
        dich = _inf_declared_cached(os.path.join(base, *x["name"].split("/")))
        manca, assenti, ombra, tolti = [], [], [], []
        for low, orig in sorted(dich.items()):
            accanto = _sibling(x["name"], low)
            if accanto in presenti:
                if not low.endswith(WINPE_EXT) and is_winpe_system_file(low):
                    ombra.append(orig)             # il WinPE ha gia' un file con questo nome: non si tocca
                elif per_rel[accanto]["excluded"]:
                    tolti.append(orig)             # c'e', ma il tecnico l'ha escluso a mano
                continue
            if low in nomi_cartella:
                manca.append(orig)                 # un'altra copia ce l'ha: questa copia e' monca
            else:
                assenti.append(orig)               # non c'e' in nessuna copia: il pacchetto e' incompleto
                if low.endswith(WINPE_EXT):
                    manca.append(orig)             # un .inf/.sys/.cat dichiarato e mai presente resta "manca"
        x["inf_missing"] = sorted(manca, key=str.lower)
        x["inf_absent"], x["inf_shadowed"] = assenti, ombra
        coerente[x["name"]] = not (manca or ombra or tolti)
        # i file dichiarati che stanno accanto all'.inf sono candidati anche se non sono .inf/.sys/.cat:
        # entrano pero' solo se questo .inf viene scelto, mai perche' si trovano nella cartella. Un .inf
        # escluso a mano non tira dentro niente, quindi i suoi file non sono nemmeno candidati.
        if x["excluded"]:
            continue
        for low in dich:
            if low.endswith(WINPE_EXT) or is_winpe_system_file(low):
                continue
            y = per_rel.get(_sibling(x["name"], low))
            if y is not None:
                y["winpe_cand"] = True

    # una copia completa batte una monca anche se sta in una cartella meno preferita
    cand.sort(key=lambda y: (1 if y.get("inf_missing") else 0, _inject_rank(y["name"]), y["name"].lower()))
    scelti = {}
    for x in cand:
        scelti.setdefault(x["name"].split("/")[-1].lower(), x)
    # coerenza: i file dichiarati da un .inf scelto arrivano dalla cartella dell'.inf, non da un'altra copia
    per_inj = {x["name"].lower(): x for x in files if x["winpe_cand"] and not x["excluded"]}
    bloccati, scelti_inf = set(), []
    for x in cand:
        nome = x["name"].split("/")[-1].lower()
        if not nome.endswith(".inf") or scelti.get(nome) is not x:
            continue
        scelti_inf.append(x)
        for n in inf_all_files(os.path.join(base, *x["name"].split("/"))):
            n = n.lower()
            fratello = per_inj.get(_sibling(x["name"], n))
            if n in bloccati or fratello is None:
                continue
            scelti[n] = fratello
            bloccati.add(n)
    # piu' copie dello stesso .inf: se quella scelta non e' un set coerente e un'altra lo e', si dice quale
    for x in scelti_inf:
        if coerente.get(x["name"]):
            continue
        nome = x["name"].split("/")[-1].lower()
        alt = sorted((y for y in infs if y is not x and coerente.get(y["name"])
                      and y["name"].split("/")[-1].lower() == nome),
                     key=lambda y: (_inject_rank(y["name"]), y["name"].lower()))
        if alt:
            x["inf_better"] = alt[0]["name"]
    inject = []
    for x in files:
        if scelti.get(x["name"].split("/")[-1].lower()) is x:
            x["winpe"] = True
            inject.append(x)
    return inject


def _nomi_uniti(*elenchi):
    """Unione di piu' elenchi di nomi file: senza doppioni (confronto in minuscolo) e in ordine alfabetico."""
    out = {}
    for e in elenchi:
        for n in e:
            out.setdefault(str(n).lower(), n)
    return [out[k] for k in sorted(out)]


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
    setup_offer = check_setup_offer(patch["setup_offer"]) if "setup_offer" in patch else None

    def upd(d):
        f = d.setdefault("folders", {}).setdefault(name, {})
        for k in ("winpe_inject", "setup_load"):
            if k in patch:
                f[k] = bool(patch[k])
        if "note" in patch:
            f["note"] = str(patch["note"])[:200]
        if apply_to is not None:
            f["apply_to"] = apply_to
        if setup_offer is not None:
            f["setup_offer"] = setup_offer
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
    setup_offer = check_setup_offer(patch["setup_offer"]) if "setup_offer" in patch else None
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
                if setup_offer is not None:
                    f["setup_offer"] = setup_offer
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


def _inf_parse(path):
    """Nomi dei file citati da un .inf, divisi per ruolo (docs/API.md, sezioni 21 e 24).

    {"copiati": {...}, "servizi": {...}, "catalogo": {...}, "dichiarati": {...}}, ogni voce
    {nome minuscolo: grafia usata nell'.inf}:
    - copiati: i file che il pacchetto promette di **copiare** — sezioni [SourceDisksFiles*], righe
      CopyFiles (nomi diretti con "@" e sezioni di copia referenziate). Sono quelli che il programma
      di installazione cerca davvero sul disco quando mette il driver in staging.
    - servizi: i nomi che compaiono **solo** come ServiceBinary. Spesso il file lo fornisce Windows
      ("ServiceBinary = %12%\\pci.sys" nel Matrox): pretenderlo accanto all'.inf sarebbe un falso allarme.
    - catalogo: CatalogFile= della sezione [Version], cioe' il .cat con la firma del pacchetto. Il setup,
      a differenza di drvload, pretende la firma: un .inf senza il suo .cat accanto viene rifiutato.
    - dichiarati: copiati + servizi, quello che serve all'iniezione nel WinPE (sezione 21).
    Un .inf illeggibile o troppo grande non dichiara nulla."""
    try:
        text = _inf_text(path)
    except OSError:
        return {"copiati": {}, "servizi": {}, "catalogo": {}, "dichiarati": {}}
    sezioni, cur = {}, ""
    for riga in _inf_lines(text):
        m = _INF_SECTION_RE.match(riga)
        if m:
            cur = m.group(1).strip().lower()
            sezioni.setdefault(cur, [])
            continue
        if cur:
            sezioni[cur].append(riga)

    nomi, servizi, catalogo, copia = {}, {}, {}, set()
    for sez, righe in sezioni.items():
        sorgenti = sez.split(".")[0] == "sourcedisksfiles"
        versione = sez.split(".")[0] == "version"
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
                    servizi.setdefault(n.lower(), n)
            elif versione and chiave.split(".")[0] == "catalogfile":
                n = _inf_file_name(v)         # "CatalogFile.NTamd64 = iaStorVD.cat"
                if n:
                    catalogo.setdefault(n.lower(), n)
    # sezioni di copia: "file-destinazione, file-sorgente, ...": conta il file sorgente, se c'e'
    for sez in copia:
        for riga in sezioni.get(sez, []):
            campi = [c.strip() for c in riga.split(",")]
            n = _inf_file_name(campi[1]) if len(campi) > 1 and campi[1] else _inf_file_name(campi[0])
            if n:
                nomi.setdefault(n.lower(), n)
    dichiarati = dict(nomi)
    for low, orig in servizi.items():
        dichiarati.setdefault(low, orig)
    return {"copiati": nomi, "servizi": servizi, "catalogo": catalogo, "dichiarati": dichiarati}


def _inf_declared_map(path):
    """{nome minuscolo: grafia usata nell'.inf} dei file che l'.inf dichiara come propri.

    Guarda le sezioni [SourceDisksFiles*], le righe CopyFiles (nomi diretti con "@" e sezioni di copia
    referenziate) e le righe ServiceBinary. Un .inf illeggibile o troppo grande non dichiara nulla."""
    return _inf_parse(path)["dichiarati"]


def inf_declared_files(path):
    """Nomi (minuscoli, senza percorso) dei file che l'.inf dichiara come propri."""
    return set(_inf_declared_cached(path))


def _inf_parse_cached(path):
    """_inf_parse con memoria per (percorso, mtime, dimensione): list_folders gira a ogni polling."""
    try:
        st = os.stat(path)
    except OSError:
        return {"copiati": {}, "servizi": {}, "catalogo": {}, "dichiarati": {}}
    key = (path, int(st.st_mtime), st.st_size)
    v = _INF_CACHE.get(key)
    if v is None:
        if len(_INF_CACHE) >= _INF_CACHE_MAX:
            _INF_CACHE.clear()
        v = _inf_parse(path)
        _INF_CACHE[key] = v
    return v


def _inf_declared_cached(path):
    """Mappa dei file dichiarati (copiati + ServiceBinary), dalla memoria di _inf_parse_cached."""
    return _inf_parse_cached(path)["dichiarati"]


def inf_needed_files(path):
    """File dichiarati dall'.inf che finirebbero anche loro nel WinPE (.inf/.sys/.cat), in ordine.

    I nomi tornano con la grafia usata nell'.inf: i confronti vanno fatti in minuscolo."""
    return sorted((orig for low, orig in _inf_declared_cached(path).items() if low.endswith(WINPE_EXT)),
                  key=str.lower)


def inf_extra_files(path):
    """File dichiarati dall'.inf con un'estensione fuori da WINPE_EXT (.dll, .exe, .bin, .dat...), in ordine.

    Sono i file che l'.inf si porta dietro con CopyFiles o SourceDisksFiles ma che da soli non entrerebbero
    mai nel WinPE. drvload fallisce anche quando non riesce a mettere a posto uno solo dei file di CopyFiles
    (successo davvero con iaStorVD.inf e RstMwEventLogMsg.dll), quindi vanno iniettati anche loro - ma solo
    quelli che l'.inf dichiara e che stanno nella sua stessa cartella."""
    return sorted((orig for low, orig in _inf_declared_cached(path).items() if not low.endswith(WINPE_EXT)),
                  key=str.lower)


def inf_all_files(path):
    """Tutti i file dichiarati dall'.inf che possono seguirlo nel WinPE: prima i .inf/.sys/.cat, poi gli altri."""
    return inf_needed_files(path) + inf_extra_files(path)


# ---------------------------------------------------------------------------
# Percorsi driver offerti al programma di installazione (docs/API.md, sezione 24)
#
# Il file di risposta scriveva un solo DriverPaths verso la radice della libreria: il setup percorre il
# percorso che riceve con tutte le sue sottocartelle, mette in staging OGNI .inf che trova e al primo file
# dichiarato che non trova si ferma con 0x80070002, abortendo l'installazione intera (0xC190011F dopo due
# minuti e mezzo, senza toccare il disco - log reali del 10 settembre 2026 con RAID_drivers\iaStorVD.inf).
# Un solo pacchetto incompleto in libreria bastava a non far installare piu' nessun PC.
# Da qui in avanti nell'XML si scrivono soltanto le cartelle che il setup puo' percorrere per intero.


def default_setup_offer():
    """Valore predefinito: Pixio decide da solo quali sottocartelle offrire (regola della sezione 24)."""
    return "auto"


def check_setup_offer(v):
    """Normalizza e valida setup_offer. Solleva ValueError con messaggio in italiano."""
    if v is None:
        return default_setup_offer()
    if not isinstance(v, str):
        raise ValueError('setup_offer: valori ammessi "auto", "mai", "sempre"')
    x = v.strip().lower()
    if x not in SETUP_OFFER_MODES:
        raise ValueError('setup_offer: valori ammessi "auto", "mai", "sempre"')
    return x


def _read_setup_offer(f):
    """setup_offer salvato nei flag della cartella, ripulito: i valori vecchi o rotti tornano "auto"."""
    try:
        return check_setup_offer((f or {}).get("setup_offer"))
    except ValueError:
        return default_setup_offer()


def setup_offer_of(name):
    """setup_offer della cartella (predefinito se non impostato)."""
    return _read_setup_offer(_flags()["folders"].get(name))


def inf_setup_files(path):
    """{nome minuscolo: grafia nell'.inf} dei file che il pacchetto promette di copiare, piu' il suo .cat.

    Differenza voluta rispetto a inf_all_files(): un nome che compare **solo** come ServiceBinary non
    conta, perche' di norma lo fornisce Windows ("ServiceBinary = %12%\\pci.sys" del Matrox G200eW:
    pretenderlo accanto all'.inf sarebbe un falso allarme). Il .cat invece si aggiunge, perche' il setup
    pretende la firma mentre drvload no."""
    m = _inf_parse_cached(path)
    out = dict(m["copiati"])
    for low, orig in m["catalogo"].items():
        out.setdefault(low, orig)
    return out


def inf_setup_missing(path, presenti=None):
    """Nomi (grafia dell'.inf) che il pacchetto promette di copiare e che non si trovano nel suo pacchetto.

    `presenti` e' l'insieme dei nomi minuscoli disponibili nel sottoalbero della cartella dell'.inf; se non
    viene passato si legge la cartella dell'.inf con le sue sottocartelle. Si guarda il sottoalbero e non
    solo i file accanto perche' un .inf puo' dichiarare i propri file in una sottocartella
    ([SourceDisksNames] con il campo percorso): la regola stretta lo boccerebbe a torto.
    Elenco vuoto = pacchetto completo, il setup lo puo' installare."""
    voluti = inf_setup_files(path)
    if not voluti:
        return []
    if presenti is None:
        presenti = set()
        base = os.path.dirname(path)
        for _rel in _setup_names(base)[0]:
            presenti.add(_rel.split("/")[-1].lower())
    return sorted((orig for low, orig in voluti.items() if low not in presenti), key=str.lower)


def _setup_names(base):
    """(percorsi relativi dei file della cartella, troncato) con i limiti della sezione 24.

    La generazione del file di risposta gira a ogni avvio di un PC: oltre i limiti la cartella viene
    trattata come non percorribile invece di far aspettare il PC (e la GUI lo dice)."""
    rels, dirs, troncato = [], 0, False
    for dirpath, dirnames, filenames in os.walk(base):
        rel = os.path.relpath(dirpath, base).replace(os.sep, "/")
        rel = "" if rel == "." else rel
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        profondita = (rel.count("/") + 1) if rel else 0
        if profondita >= MAX_SETUP_DEPTH and dirnames:
            troncato = True
            dirnames[:] = []
        dirs += 1
        if dirs > MAX_SETUP_DIRS:
            return rels, True
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            rels.append((rel + "/" + fn) if rel else fn)
            if len(rels) >= MAX_SETUP_FILES:
                return rels, True
    return rels, troncato


def _setup_tree(base, rels, troncato=False):
    """Cartelle di `base` che il setup puo' percorrere senza incontrare un pacchetto incompleto.

    Tre definizioni in cascata:
    1) un .inf e' completo se ogni file che promette di copiare (piu' il suo .cat) sta nel suo pacchetto;
    2) una directory e' offribile se **tutti** gli .inf del suo sottoalbero sono completi e ce n'e' almeno
       uno: il setup percorre ricorsivamente il percorso che riceve, quindi non basta guardare la directory;
    3) si scende in ampiezza dalla radice: la prima directory offribile si scrive e li' ci si ferma (dentro
       ci pensa il setup), una non offribile si scavalca e si esaminano le sue figlie. Ne esce un'anticatena
       di cartelle massimali: nessun percorso contiene un .inf incompleto e nessuno e' annidato in un altro.

    Ritorna {"paths": [percorso relativo, "" = radice della cartella], "skipped": [{dir, inf, missing,
    lost}], "infs": {percorso .inf: [nomi mancanti]}, "truncated": bool}."""
    files_dir, figli, dirs = {}, {}, {""}
    for rel in rels:
        d = _dir_of(rel)
        cur = ""
        for parte in (d.split("/") if d else []):
            giu = (cur + "/" + parte) if cur else parte
            figli.setdefault(cur, set()).add(giu)
            dirs.add(giu)
            cur = giu
        files_dir.setdefault(d, []).append(rel.split("/")[-1])

    # nomi disponibili nel sottoalbero di ogni directory: si parte dalle piu' profonde
    ordinate = sorted(dirs, key=lambda d: (-(d.count("/") + 1) if d else 0, d.lower()))
    sotto = {}
    for d in ordinate:
        n = set(x.lower() for x in files_dir.get(d, ()))
        for c in figli.get(d, ()):
            n |= sotto.get(c, set())
        sotto[d] = n

    # verdetto su ogni .inf della cartella
    manca, infs_dir = {}, {}
    for rel in rels:
        if not rel.lower().endswith(".inf"):
            continue
        d = _dir_of(rel)
        infs_dir.setdefault(d, []).append(rel)
        manca[rel] = inf_setup_missing(os.path.join(base, *rel.split("/")), sotto.get(d, set()))

    ok, ha_inf = {}, {}
    for d in ordinate:                      # profondita' decrescente: le figlie sono gia' decise
        ok[d] = (all(not manca[r] for r in infs_dir.get(d, ()))
                 and all(ok.get(c, True) for c in figli.get(d, ())))
        ha_inf[d] = bool(infs_dir.get(d)) or any(ha_inf.get(c) for c in figli.get(d, ()))

    paths, coda = [], [""]
    while coda and not troncato:
        d = coda.pop(0)
        if not ha_inf.get(d):
            continue                        # niente .inf qui sotto: sarebbe una scansione SMB a vuoto
        if ok.get(d):
            paths.append(d)
            continue
        coda.extend(sorted(figli.get(d, ()), key=str.lower))
    paths.sort(key=str.lower)

    scartate = []
    if troncato:
        scartate.append({"dir": "", "inf": "", "missing": [], "lost": [], "reason": "troppo grande"})
    for rel in sorted(manca, key=str.lower):
        if not manca[rel]:
            continue
        d = _dir_of(rel)
        # gli .inf sani che stanno nella stessa directory si perdono insieme a quello rotto: <Path> accetta
        # una cartella, non c'e' modo di dire al setup "in questa cartella salta quell'.inf"
        buoni = [r.split("/")[-1] for r in sorted(infs_dir.get(d, ()), key=str.lower) if not manca[r]]
        scartate.append({"dir": d, "inf": rel, "missing": list(manca[rel]), "lost": buoni,
                         "reason": "incompleto"})
    return {"paths": paths, "skipped": scartate, "infs": manca, "truncated": troncato}


def setup_tree_of(base, offer="auto"):
    """_setup_tree per una cartella driver, tenendo conto del flag setup_offer.

    "mai": nessun percorso (la cartella serve solo al WinPE via drvload). "sempre": si scrive la radice
    cosi' com'e', saltando il controllo — e' la scappatoia per un falso allarme del parser, e gli avvisi
    restano perche' la GUI li deve mostrare lo stesso."""
    rels, troncato = _setup_names(base)
    t = _setup_tree(base, rels, troncato)
    if offer == "mai":
        t["paths"] = []
    elif offer == "sempre":
        t["paths"] = [""] if rels else []
    return t


def setup_unc(server_ip, folder, rel=""):
    """Percorso UNC di una cartella della libreria driver, come lo scrive il file di risposta."""
    p = "\\\\%s\\pxe\\drivers\\%s" % (str(server_ip or "").strip() or "127.0.0.1", folder)
    if rel:
        p += "\\" + rel.replace("/", "\\")
    return p


def _setup_priority(f):
    """Chi entra per primo quando si supera MAX_SETUP_PATHS: prima le cartelle abbinate apposta a questa
    immagine (isos), poi i gruppi, poi quelle valide per tutte."""
    a = f.get("apply_to") if isinstance(f.get("apply_to"), dict) else {}
    return {"isos": 0, "groups": 1}.get(a.get("mode"), 2)


def setup_paths(server_ip="", iso=None):
    """Percorsi driver da scrivere nel file di risposta per la voce di catalogo che si sta avviando.

    iso: dict con "slug" e "group" (None = nessun filtro, anteprima generica). Entrano solo le cartelle
    abbinate all'immagine (apply_to, sezione 15), con nome valido, non messe su setup_offer="mai" e
    percorribili senza incontrare un pacchetto incompleto.
    Ritorna {"paths": [{folder, rel, unc}], "skipped": [{folder, dir, inf, missing, lost, reason}],
    "dropped": [{folder, rel, unc}] (oltre il tetto o percorso troppo lungo), "folders": quante cartelle
    sono state esaminate, "offered": quante ne hanno prodotto almeno un percorso}."""
    ip = str(server_ip or "").strip() or "127.0.0.1"
    voci, scartate, esaminate, offerte = [], [], 0, 0
    lunghi = []
    for f in list_folders():
        if not apply_matches(f.get("apply_to"), iso):
            continue
        esaminate += 1
        if not f["valid_name"]:
            scartate.append({"folder": f["name"], "dir": "", "inf": "", "missing": [], "lost": [],
                             "reason": "nome non valido"})
            continue
        if f.get("setup_offer") == "mai":
            scartate.append({"folder": f["name"], "dir": "", "inf": "", "missing": [], "lost": [],
                             "reason": "mai"})
            continue
        for s in (f.get("setup_skipped") or []):
            scartate.append(dict(s, folder=f["name"]))
        mie = []
        for rel in (f.get("setup_paths") or []):
            unc = setup_unc(ip, f["name"], rel)
            if len(unc) > MAX_SETUP_UNC:
                lunghi.append({"folder": f["name"], "rel": rel, "unc": unc})
                scartate.append({"folder": f["name"], "dir": rel, "inf": "", "missing": [], "lost": [],
                                 "reason": "percorso troppo lungo"})
                continue
            mie.append({"folder": f["name"], "rel": rel, "unc": unc, "prio": _setup_priority(f)})
        if mie:
            offerte += 1
        voci.extend(mie)
    # oltre il tetto entrano le cartelle che il tecnico ha abbinato apposta a questa immagine
    voci.sort(key=lambda v: (v["prio"], v["folder"].lower(), v["rel"].lower()))
    fuori = voci[MAX_SETUP_PATHS:]
    voci = voci[:MAX_SETUP_PATHS]
    # la priorita' decide chi entra, l'ordine alfabetico come si scrive l'elenco che entra
    voci.sort(key=lambda v: (v["folder"].lower(), v["rel"].lower()))
    for v in voci + fuori:
        v.pop("prio", None)
    return {"paths": voci, "skipped": scartate, "dropped": fuori + lunghi,
            "folders": esaminate, "offered": offerte}


# ---------------------------------------------------------------------------
# Protezione dei file di sistema del WinPE (docs/API.md, sezione 21)
#
# wimboot appiattisce tutto in X:\Windows\System32, dove il WinPE ha gia' i suoi file: iniettare un file
# dichiarato che si chiama come uno di quelli lo sostituirebbe, e il WinPE si riavvia. I nomi qui sotto sono
# quelli veri di \Windows\System32 del WinPE (ricavati da "wimdir <boot.wim> 2" su
# /srv/pixio/http/iso/ltsc2021-x64/sources/boot.wim), scelti fra quelli che un pacchetto driver puo'
# davvero portarsi dietro: runtime C/C++, librerie di installazione, nomi generici.
# L'elenco completo (1234 nomi) non sta nel codice: winpe_system32_names() lo legge una volta sola dal primo
# boot.wim che trova in HTTP_ISO_DIR e lo tiene in memoria per tutta la vita del processo. Se wimlib non c'e'
# o nessuna ISO e' montata resta valido l'elenco scritto qui.
WINPE_SYSTEM32 = frozenset((
    # runtime e librerie che gli installatori dei driver si portano dietro
    "msvcrt.dll", "msvcirt.dll", "msvcp60.dll", "msvcp_win.dll", "ucrtbase.dll", "mfc42.dll", "mfc42u.dll",
    "atl.dll", "atmlib.dll", "asycfilt.dll", "advpack.dll", "cabinet.dll", "imagehlp.dll", "dbghelp.dll",
    "dbgcore.dll", "propsys.dll", "xmllite.dll", "msxml3.dll", "msxml6.dll", "riched20.dll", "riched32.dll",
    # installazione dei driver: un pacchetto che le ricopia sostituirebbe quelle del WinPE
    "setupapi.dll", "difxapi.dll", "newdev.dll", "cfgmgr32.dll", "devobj.dll", "devrtl.dll", "drvstore.dll",
    "drvsetup.dll", "spinf.dll", "syssetup.dll", "sppnp.dll", "msports.dll", "storprop.dll", "hid.dll",
    "wimgapi.dll", "wdscore.dll", "sfc.dll", "sfc_os.dll", "streamci.dll", "sdhcinst.dll",
    # librerie di sistema con nomi che capita di trovare anche altrove
    "kernel32.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "shell32.dll", "shlwapi.dll", "ole32.dll",
    "oleaut32.dll", "oleacc.dll", "comdlg32.dll", "rpcrt4.dll", "ntdll.dll", "combase.dll", "sechost.dll",
    "ws2_32.dll", "wsock32.dll", "winmm.dll", "winhttp.dll", "wininet.dll", "urlmon.dll", "iertutil.dll",
    "crypt32.dll", "cryptsp.dll", "bcrypt.dll", "ncrypt.dll", "wintrust.dll", "netapi32.dll", "secur32.dll",
    "psapi.dll", "powrprof.dll", "uxtheme.dll", "dwmapi.dll", "d2d1.dll", "dwrite.dll", "gdiplus.dll",
    "imm32.dll", "msctf.dll", "usp10.dll", "version.dll", "profapi.dll", "dnsapi.dll", "mpr.dll", "wmi.dll",
    # nomi corti e generici: sono proprio quelli che fanno danno
    "input.dll", "console.dll", "security.dll", "authz.dll", "avrt.dll", "clb.dll", "dab.dll", "fms.dll",
    "kd.dll", "mi.dll", "nsi.dll", "wer.dll", "dpx.dll", "ci.dll", "cdd.dll", "lpk.dll", "lz32.dll",
    "slc.dll", "sxs.dll", "spp.dll", "tbs.dll", "tdh.dll", "ulib.dll", "esent.dll", "hal.dll",
    # eseguibili con nomi comuni
    "attrib.exe", "compact.exe", "convert.exe", "doskey.exe", "expand.exe", "find.exe", "net.exe",
    "notepad.exe", "ping.exe", "print.exe", "recover.exe", "replace.exe", "reg.exe", "sfc.exe",
    "subst.exe", "vds.exe", "verifier.exe", "xcopy.exe",
))
WIM_LIST_TIMEOUT = 20            # wimdir legge solo i metadati del .wim: ~0,1 s, ma non si resta appesi
WIM_LIST_MAX = 2                 # bastano uno o due WinPE: i nomi di System32 sono quasi gli stessi
_SYS32_CACHE = None              # calcolato una volta sola: list_folders() gira a ogni aggiornamento pagina


def _wim_system32_names(wim):
    """Nomi dei file in \\Windows\\System32 di un boot.wim (immagine 2, il WinPE del setup)."""
    try:
        p = subprocess.run(["wimdir", wim, "2"], capture_output=True, text=True, timeout=WIM_LIST_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return set()
    if p.returncode != 0:
        return set()
    nomi = set()
    for riga in (p.stdout or "").splitlines():
        parti = riga.strip().replace("\\", "/").lower().split("/")
        # solo i file al primo livello di /windows/system32: le sottocartelle (drivers, ...) non c'entrano
        if len(parti) == 4 and parti[:3] == ["", "windows", "system32"] and "." in parti[3]:
            nomi.add(parti[3])
    return nomi


def winpe_system32_names():
    """Nomi (minuscoli) che il WinPE ha gia' in \\Windows\\System32: elenco scritto nel codice piu' quelli
    letti una volta sola da un boot.wim del catalogo. Il risultato resta in memoria fino al riavvio."""
    global _SYS32_CACHE
    if _SYS32_CACHE is None:
        nomi = set(WINPE_SYSTEM32)
        try:
            # C.HTTP_DIR e non C.HTTP_ISO_DIR: cosi' segue la radice HTTP anche quando e' reindirizzata
            wims = sorted(glob.glob(os.path.join(C.HTTP_DIR, "iso", "*", "sources", "boot.wim")))
        except OSError:
            wims = []
        for wim in wims[:WIM_LIST_MAX]:
            nomi |= _wim_system32_names(wim)
        _SYS32_CACHE = frozenset(nomi)
    return _SYS32_CACHE


def is_winpe_system_file(nome):
    """True se il WinPE ha gia' un file con questo nome in \\Windows\\System32: iniettarlo lo sostituirebbe."""
    return str(nome or "").split("/")[-1].lower() in winpe_system32_names()


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
    Valgono anche i file dichiarati con altra estensione (.dll, .exe...): drvload fallisce pure quando non
    riesce a mettere a posto un file di CopyFiles. Un file dichiarato che porta il nome di un file di sistema
    del WinPE non viene iniettato (is_winpe_system_file): lo sostituirebbe.
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
            for n in inf_all_files(os.path.join(base, *x["name"].split("/"))):
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
