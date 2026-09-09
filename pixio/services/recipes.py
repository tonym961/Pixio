"""Ricette di boot: caricamento di data/recipes.json e rendering dello script iPXE per una ISO.

I driver iniettati nel WinPE (segnaposto {drivers}) dipendono dalla ISO: ogni voce riceve solo le cartelle
driver abbinate alla sua immagine (campo apply_to, docs/API.md sezione 15). render() riceve gia' la voce di
catalogo, quindi se chi chiama non passa i flag questi vengono calcolati per quella ISO, non per tutte."""
import json
import os
import re
import threading

from .. import config as C

_cache = {"mtime": None, "data": None}
_lock = threading.Lock()


def load():
    with _lock:
        try:
            m = os.stat(C.RECIPES_FILE).st_mtime
        except OSError:
            m = None
        if _cache["data"] is None or _cache["mtime"] != m:
            with open(C.RECIPES_FILE, encoding="utf-8") as f:
                _cache["data"] = json.load(f)
            _cache["mtime"] = m
        return _cache["data"]


def types():
    """Lista ordinata per priorità dei tipi (id, name, category, platforms, manual_only)."""
    return sorted(load()["types"], key=lambda t: t.get("priority", 500))


def get_type(type_id):
    for t in load()["types"]:
        if t["id"] == type_id:
            return t
    return None


def type_name(type_id):
    t = get_type(type_id)
    return t["name"] if t else type_id


def type_group(type_id):
    """Gruppo predefinito del menu per questo tipo di ISO."""
    t = get_type(type_id)
    return (t or {}).get("group", "")


def builtin(name):
    return load().get("builtin", {}).get(name)


def urls(server_ip, slug):
    base = f"http://{server_ip}/pxe"
    return {
        "http_iso": f"{base}/iso/{slug}",
        "http_isofile": f"{base}/isofile/{slug}.iso",
        "http_boot": f"{base}/boot",
        "http_inject": f"{base}/inject/{slug}",
        "server_ip": server_ip,
        "slug": slug,
    }


_PH = re.compile(r"\{(f|fn):([a-z_]+)\}|\{([a-z_]+)\}")


def render_lines(lines, ctx, files, flags):
    """Espande placeholder e condizionali. files: {chiave: percorso relativo}. flags: {nome: bool}."""
    out = []
    for raw in lines:
        line = raw
        if line.strip() == "{drivers}":
            for folder, name, _path in (flags.get("driver_files") or []):
                from . import drivers as _drv
                # l'URL deve puntare al file dov'è davvero (anche in una sottocartella); il secondo
                # argomento è il nome che avrà dentro il WinPE, dove wimboot mette tutto insieme
                rel = os.path.relpath(_path, os.path.join(C.DRIVERS_DIR, folder)).replace(os.sep, "/")
                out.append(f"initrd {_drv.http_url(ctx['server_ip'], folder, rel)} {name}")
            continue
        m = re.match(r"^\?(!?)(has:)?([a-z_]+)\s+(.*)$", line)
        if m:
            neg, has, name, rest = m.group(1) == "!", bool(m.group(2)), m.group(3), m.group(4)
            cond = bool(files.get(name)) if has else bool(flags.get(name))
            if cond == neg:
                continue
            line = rest

        def sub(mm):
            if mm.group(1) == "f":
                return files.get(mm.group(2), "")
            if mm.group(1) == "fn":
                return os.path.basename(files.get(mm.group(2), ""))
            return str(ctx.get(mm.group(3), mm.group(0)))
        out.append(_PH.sub(sub, line))
    return out


AUTO = object()      # "usa la risposta predefinita della ISO": diverso da None, che vuol dire "nessuna"


def answer_for(iso, server_ip, answer=AUTO):
    """Risposta automatica da agganciare: (argomenti kernel, file da iniettare nel WinPE).

    answer: AUTO = la predefinita della ISO (come prima della sezione 17); None = nessuna, cioe'
    installazione guidata a mano; altrimenti l'id scelto al boot, gia' risolto da chi chiama."""
    try:
        from . import answers
    except ImportError:
        return "", []
    try:
        if answer is AUTO:
            a = answers.get_for_slug(iso.get("slug"))
        else:
            a = answers.get(answer) if answer else None
        if not a:
            return "", []
        # lo slug serve alle risposte Windows generate da un profilo: l'autounattend.xml viene
        # rigenerato al boot per questa ISO, con le edizioni che contiene (docs/API.md, sezione 20)
        return (answers.kernel_args(a, iso.get("type"), server_ip) or "",
                answers.winpe_files(a, server_ip, iso.get("slug") or "") or [])
    except Exception:  # noqa: BLE001
        return "", []


def _apply_answer(lines, iso, server_ip, answer=AUTO):
    """Aggiunge gli argomenti della risposta alla riga kernel e i file iniettati (Windows)."""
    args, files = answer_for(iso, server_ip, answer)
    if not args and not files:
        return lines
    out = []
    for line in lines:
        if args and line.startswith("kernel ") and "wimboot" not in line:
            line = line.rstrip() + " " + args
        out.append(line)
        if files and line.startswith("kernel ") and "wimboot" in line:
            for name, url in files:
                out.append(f"initrd {url} {name}")
    return out


def flags_for(iso):
    """Flag di default per questa voce di catalogo: driver abbinati alla ISO, non tutti quelli presenti."""
    try:
        from . import winpe
        return winpe.flags(None, iso)
    except Exception:  # noqa: BLE001
        return {}


def render(iso, server_ip, platform, flags=None, answer=AUTO):
    """Script iPXE (senza shebang) per la voce `iso` (dict del catalogo) sulla piattaforma 'efi'|'bios'.
    Ritorna (lines, warnings). lines vuoto se la ricetta non supporta la piattaforma.

    flags: quelli di services/winpe.flags(cfg, iso). Se non passati vengono calcolati per questa ISO,
    così il segnaposto {drivers} elenca solo i driver abbinati a questa immagine.
    answer: risposta da usare (AUTO = la predefinita della ISO, None = nessuna, id = quella scelta)."""
    flags = flags_for(iso) if flags is None else (flags or {})
    ctx = urls(server_ip, iso["slug"])
    files = (iso.get("detect") or {}).get("files") or {}
    custom = iso.get("custom_recipe")
    warnings = []
    if custom:
        plats = custom.get("platforms") or ["bios", "efi"]
        if platform not in plats:
            return [], [f"La ricetta personalizzata non supporta {platform}"]
        lines = []
        kernel = (custom.get("kernel") or "").strip()
        initrds = [i.strip() for i in (custom.get("initrds") or []) if i.strip()]
        cmdline = (custom.get("cmdline") or "").strip()
        if not kernel:
            return [], ["Ricetta personalizzata senza kernel"]
        lines.append(f"kernel {kernel} {cmdline}".rstrip())
        for i in initrds:
            lines.append(f"initrd {i}")
        lines.append("boot")
        return _apply_answer(render_lines(lines, ctx, files, flags), iso, server_ip, answer), warnings
    t = get_type(iso.get("type") or "unknown") or get_type("unknown")
    if platform not in t.get("platforms", []):
        return [], [f"Il tipo '{t['name']}' non è avviabile in modalità {platform.upper()}"]
    key = f"script_{platform}"
    lines = t.get(key) or t.get("script") or []
    for k, msg in (t.get("warnings_if") or {}).items():
        neg = k.startswith("!")
        name = k.lstrip("!")
        if bool(flags.get(name)) == (not neg):
            warnings.append(msg)
    return _apply_answer(render_lines(lines, ctx, files, flags), iso, server_ip, answer), warnings + list(t.get("warnings") or [])


def render_builtin(name, server_ip, platform):
    b = builtin(name)
    if not b or platform not in b.get("platforms", []):
        return []
    lines = b.get(f"script_{platform}") or b.get("script") or []
    return render_lines(lines, urls(server_ip, name), {}, {})
