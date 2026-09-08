"""Risposte automatiche per installazioni non presidiate.

Una "risposta" è una cartella in ANSWERS_DIR/<id> con dentro il file principale del tipo scelto
(autounattend.xml, preseed.cfg, user-data, ks.cfg) più eventuali file aggiuntivi; i metadati
(nome, tipo, nota, file principale) stanno in ANSWERS_FILE.

I file vengono serviti ai client PXE senza autenticazione su http://<ip>/answers/<id>/<nome file>
(blueprint pixio/blueprints/answers_public.py) e agganciati al boot da recipes.py tramite
kernel_args() (Debian/Ubuntu/RHEL) e winpe_files() (Windows).
"""
import os
import re
import shutil
import time
import unicodedata
from urllib.parse import quote

from .. import config as C
from ..storage import read_json, update_json

ANSWER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
# Estensioni ammesse per i file di una risposta (stesse dell'upload, docs/API.md sezione 3)
FILE_EXT = (".xml", ".cfg", ".ks", ".yaml", ".yml", ".txt", ".cmd", ".bat", ".ps1", ".reg",
            ".sh", ".conf", ".seed", ".json", ".ini")
# File senza estensione ammessi (cloud-init)
FILE_NOEXT = ("user-data", "meta-data", "vendor-data", "network-config")
MAX_CONTENT = 512 * 1024          # contenuto restituito/salvato dall'editor della GUI
MAX_FILES = 200

KINDS = [
    {"id": "windows", "name": "Windows (autounattend.xml)", "main_file": "autounattend.xml",
     "hint": "Installazione automatica di Windows. Il file viene iniettato nel WinPE all'avvio e letto dal setup: "
             "disco, edizione, utente e lingua senza domande. Le password sono scritte in chiaro."},
    {"id": "debian", "name": "Debian (preseed.cfg)", "main_file": "preseed.cfg",
     "hint": "Preconfigurazione dell'installatore Debian. Pixio aggiunge alla riga di avvio "
             "auto=true priority=critical url=<indirizzo del preseed>."},
    {"id": "ubuntu", "name": "Ubuntu (autoinstall: user-data)", "main_file": "user-data",
     "hint": "Autoinstall di Ubuntu Server (cloud-init NoCloud). Pixio aggiunge alla riga di avvio "
             "autoinstall ds=nocloud-net;s=<indirizzo della cartella> e crea da solo il file meta-data vuoto."},
    {"id": "redhat", "name": "Red Hat / Fedora / Rocky / Alma (kickstart)", "main_file": "ks.cfg",
     "hint": "File kickstart per gli installatori della famiglia Red Hat. Pixio aggiunge alla riga di avvio "
             "inst.ks=<indirizzo del kickstart>."},
    {"id": "generic", "name": "Generica (file libero)", "main_file": "risposta.txt",
     "hint": "Cartella di file serviti via HTTP, senza argomenti aggiunti automaticamente: utile per script, "
             "file di post-installazione o distribuzioni non previste. Il percorso va indicato a mano nella ricetta."},
]
KIND_IDS = tuple(k["id"] for k in KINDS)

# Tipo di ISO (id ricetta) -> famiglia di risposta, per le risposte "generic"
_ISO_FAMILY = (
    ("ubuntu", "ubuntu"),
    ("debian", "debian"),
    ("redhat", "redhat"),
    ("fedora", "redhat"),
    ("centos", "redhat"),
    ("rocky", "redhat"),
    ("alma", "redhat"),
    ("windows", "windows"),
)


# ---------------------------------------------------------------- validazione
def kinds_list():
    return [{"id": k["id"], "name": k["name"], "hint": k["hint"], "main_file": k["main_file"]} for k in KINDS]


def kind_info(kind):
    for k in KINDS:
        if k["id"] == kind:
            return k
    raise ValueError("Tipo di risposta non valido: " + ", ".join(KIND_IDS))


def check_id(answer_id):
    if not ANSWER_ID_RE.match(str(answer_id or "")):
        raise ValueError("Identificativo della risposta non valido")
    return str(answer_id)


def check_filename(name):
    """Nome file semplice, senza percorsi, con estensione ammessa (o nome cloud-init noto)."""
    name = str(name or "").strip()
    if not FILE_RE.match(name) or name in (".", ".."):
        raise ValueError("Nome file non valido (lettere, numeri, punto, trattino e underscore, max 100)")
    ext = os.path.splitext(name)[1].lower()
    if not ext:
        if name.lower() not in FILE_NOEXT:
            raise ValueError("File senza estensione ammesso solo per cloud-init: " + ", ".join(FILE_NOEXT))
    elif ext not in FILE_EXT:
        raise ValueError("Estensione non ammessa: sono accettati " + ", ".join(e.lstrip(".") for e in FILE_EXT))
    return name


def slugify_id(name):
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9_-]+", "-", s).strip("-_")[:64].strip("-_")
    if s and not re.match(r"^[a-z0-9]", s):
        s = "r" + s
    return s or "risposta"


def folder_path(answer_id):
    """Percorso assoluto della cartella della risposta, verificato dentro ANSWERS_DIR."""
    root = os.path.realpath(C.ANSWERS_DIR)
    p = os.path.realpath(os.path.join(root, check_id(answer_id)))
    if p != root and not p.startswith(root + os.sep):
        raise ValueError("Percorso non consentito")
    return p


def file_path(answer_id, filename):
    """Percorso assoluto di un file della risposta, verificato dentro la sua cartella."""
    base = folder_path(answer_id)
    p = os.path.realpath(os.path.join(base, check_filename(filename)))
    if not p.startswith(base + os.sep):
        raise ValueError("Percorso non consentito")
    return p


# ---------------------------------------------------------------- metadati e lettura
def _meta():
    d = read_json(C.ANSWERS_FILE, {})
    if not isinstance(d, dict):
        d = {}
    d.setdefault("answers", {})
    return d


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _files(answer_id):
    try:
        base = folder_path(answer_id)
    except ValueError:
        return []
    out = []
    try:
        names = sorted(os.listdir(base), key=str.lower)
    except OSError:
        return out
    for fn in names:
        full = os.path.join(base, fn)
        if fn.startswith(".") or not os.path.isfile(full):
            continue
        try:
            st = os.stat(full)
        except OSError:
            continue
        out.append({"name": fn, "size": st.st_size, "mtime": int(st.st_mtime)})
        if len(out) >= MAX_FILES:
            break
    return out


def _used_by(answer_id):
    """Slug delle ISO associate a questa risposta (campo answer_id del catalogo)."""
    try:
        from . import catalog
        isos = (catalog.load() or {}).get("isos") or {}
    except Exception:  # noqa: BLE001
        return []
    return sorted(s for s, e in isos.items() if isinstance(e, dict) and e.get("answer_id") == answer_id)


def _public(answer_id, m):
    files = _files(answer_id)
    main = m.get("main_file") or ""
    if main and not any(f["name"] == main for f in files):
        main = files[0]["name"] if files else main
    k = m.get("kind") if m.get("kind") in KIND_IDS else "generic"
    return {
        "id": answer_id, "name": m.get("name") or answer_id, "kind": k,
        "kind_name": kind_info(k)["name"], "note": m.get("note", ""),
        "created": m.get("created"), "main_file": main, "files": files,
        "size": sum(f["size"] for f in files), "used_by": _used_by(answer_id),
    }


def list_answers():
    d = _meta()["answers"]
    out = [_public(i, m) for i, m in d.items() if isinstance(m, dict) and ANSWER_ID_RE.match(i)]
    out.sort(key=lambda a: (a["name"] or "").lower())
    return out


def get(answer_id):
    if not ANSWER_ID_RE.match(str(answer_id or "")):
        return None
    m = _meta()["answers"].get(str(answer_id))
    if not isinstance(m, dict):
        return None
    return _public(str(answer_id), m)


def read_content(answer_id, filename=None):
    """Contenuto testuale di un file della risposta (max MAX_CONTENT). Ritorna '' se il file non c'è."""
    a = get(answer_id)
    if not a:
        raise FileNotFoundError("Risposta non trovata")
    name = filename or a["main_file"]
    if not name:
        return ""
    p = file_path(answer_id, name)
    try:
        with open(p, "rb") as f:
            raw = f.read(MAX_CONTENT + 1)
    except FileNotFoundError:
        if filename:
            raise FileNotFoundError("File non trovato nella risposta")
        return ""
    except OSError as e:
        raise FileNotFoundError(f"File non leggibile: {e}")
    if len(raw) > MAX_CONTENT:
        raise ValueError("File troppo grande per l'editor (max 512 KB): modificalo da riga di comando")
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------- scrittura
def _write_file(answer_id, filename, content):
    """Scrive (sovrascrive) un file della risposta. Ritorna il nome del file."""
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise ValueError("Contenuto non valido: atteso testo")
    data = content.replace("\r\n", "\n").encode("utf-8")
    if len(data) > MAX_CONTENT:
        raise ValueError("Contenuto troppo grande (max 512 KB)")
    p = file_path(answer_id, filename)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o664)
    except OSError:
        pass
    return os.path.basename(p)


def create(data):
    """Crea una risposta. data: {name, kind, note?, content?, filename?}. Ritorna l'oggetto risposta."""
    data = data if isinstance(data, dict) else {}
    name = str(data.get("name", "") or "").strip()
    if not name or len(name) > 64:
        raise ValueError("Indica un nome per la risposta (max 64 caratteri)")
    k = kind_info(str(data.get("kind", "") or ""))
    note = str(data.get("note", "") or "").replace("\r", " ").replace("\n", " ").strip()[:200]
    main = check_filename(data.get("filename") or k["main_file"])
    base_id = slugify_id(name)
    existing = _meta()["answers"]
    answer_id, n = base_id, 2
    while answer_id in existing or os.path.exists(os.path.join(C.ANSWERS_DIR, answer_id)):
        answer_id = f"{base_id}-{n}"[:64].strip("-_")
        n += 1
    check_id(answer_id)
    p = folder_path(answer_id)
    try:
        os.makedirs(p, mode=0o2775)
    except FileExistsError:
        raise FileExistsError("Esiste già una risposta con questo nome")
    content = data.get("content")
    if content:
        _write_file(answer_id, main, content)
    if k["id"] == "ubuntu":
        # cloud-init NoCloud pretende anche meta-data: lo creiamo vuoto se manca
        mp = file_path(answer_id, "meta-data")
        if not os.path.exists(mp):
            _write_file(answer_id, "meta-data", "")

    def upd(d):
        d.setdefault("answers", {})[answer_id] = {
            "name": name, "kind": k["id"], "note": note, "main_file": main, "created": _now(),
        }
        return d
    update_json(C.ANSWERS_FILE, upd, default={})
    return get(answer_id)


def update(answer_id, data):
    """Aggiorna metadati e/o contenuto. data: {name?, note?, content?, filename?}."""
    a = get(answer_id)
    if not a:
        raise FileNotFoundError("Risposta non trovata")
    data = data if isinstance(data, dict) else {}
    patch = {}
    if "name" in data:
        name = str(data.get("name") or "").strip()
        if not name or len(name) > 64:
            raise ValueError("Nome non valido (max 64 caratteri)")
        patch["name"] = name
    if "note" in data:
        if data["note"] is not None and not isinstance(data["note"], str):
            raise ValueError("Nota non valida: atteso testo")
        patch["note"] = (data["note"] or "").replace("\r", " ").replace("\n", " ").strip()[:200]
    written = None
    if "content" in data:
        target = check_filename(data.get("filename") or a["main_file"] or kind_info(a["kind"])["main_file"])
        written = _write_file(answer_id, target, data.get("content"))
        if not a["main_file"]:
            patch["main_file"] = written
    elif data.get("filename"):
        # cambio del file principale senza toccarne il contenuto
        target = check_filename(data["filename"])
        if not os.path.isfile(file_path(answer_id, target)):
            raise FileNotFoundError("File non trovato nella risposta")
        patch["main_file"] = target
    if patch:
        def upd(d):
            m = d.setdefault("answers", {}).setdefault(answer_id, {})
            m.update(patch)
            return d
        update_json(C.ANSWERS_FILE, upd, default={})
    out = get(answer_id)
    if written:
        out["written"] = written
    return out


def delete(answer_id):
    a = get(answer_id)
    if not a:
        raise FileNotFoundError("Risposta non trovata")
    p = folder_path(answer_id)
    if os.path.isdir(p):
        shutil.rmtree(p)

    def upd(d):
        d.setdefault("answers", {}).pop(answer_id, None)
        return d
    update_json(C.ANSWERS_FILE, upd, default={})
    # le ISO che la usavano restano senza risposta
    try:
        from . import catalog
        for slug in a["used_by"]:
            catalog.update(slug, {"answer_id": None})
    except Exception:  # noqa: BLE001
        pass


def delete_file(answer_id, filename):
    a = get(answer_id)
    if not a:
        raise FileNotFoundError("Risposta non trovata")
    p = file_path(answer_id, filename)
    if not os.path.isfile(p):
        raise FileNotFoundError("File non trovato nella risposta")
    os.unlink(p)


# ---------------------------------------------------------------- uso nel boot
def public_url(server_ip, answer_id, filename):
    """URL pubblico di un file della risposta (nessuna autenticazione: lo scarica il client PXE)."""
    return f"http://{server_ip}/answers/{quote(check_id(answer_id))}/{quote(check_filename(filename))}"


def folder_url(server_ip, answer_id):
    """URL della cartella, con lo slash finale che cloud-init pretende."""
    return f"http://{server_ip}/answers/{quote(check_id(answer_id))}/"


def get_for_slug(slug):
    """Risposta associata a una ISO del catalogo, oppure None."""
    try:
        from . import catalog
        e = (catalog.load() or {}).get("isos", {}).get(slug)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(e, dict) or not e.get("answer_id"):
        return None
    return get(e["answer_id"])


def _family(answer, iso_type):
    """Famiglia da usare: il tipo della risposta; per 'generic' si prova a dedurla dal tipo della ISO."""
    kind = (answer or {}).get("kind")
    if kind in KIND_IDS and kind != "generic":
        return kind
    t = str(iso_type or "").lower()
    for prefix, fam in _ISO_FAMILY:
        if prefix in t:
            return fam
    return "generic"


def kernel_args(answer, iso_type=None, server_ip=""):
    """Argomenti da aggiungere alla riga kernel della ricetta iPXE. Stringa vuota se non previsti."""
    if not answer or not answer.get("id"):
        return ""
    main = answer.get("main_file") or ""
    fam = _family(answer, iso_type)
    try:
        if fam == "debian":
            if not main:
                return ""
            return f"auto=true priority=critical url={public_url(server_ip, answer['id'], main)}"
        if fam == "ubuntu":
            return f"autoinstall ds=nocloud-net;s={folder_url(server_ip, answer['id'])}"
        if fam == "redhat":
            if not main:
                return ""
            return f"inst.ks={public_url(server_ip, answer['id'], main)}"
    except ValueError:
        return ""
    return ""


def winpe_files(answer, server_ip=""):
    """[(nome_destinazione, url)] da iniettare nel WinPE via wimboot (solo risposte Windows).

    Per una risposta "generic" il file viene iniettato solo se si chiama davvero autounattend.xml.
    """
    if not answer or not answer.get("id"):
        return []
    kind = answer.get("kind")
    names = [f["name"] for f in answer.get("files") or []]
    if kind == "windows":
        main = answer.get("main_file") or "autounattend.xml"
    elif kind == "generic" and "autounattend.xml" in names:
        main = "autounattend.xml"
    else:
        return []
    try:
        return [("autounattend.xml", public_url(server_ip, answer["id"], main))]
    except ValueError:
        return []
