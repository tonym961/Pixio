"""Generazione del menu iPXE e degli script per singola voce."""
import re

from .. import settings as S
from . import catalog, recipes

PLATFORMS = ("efi", "bios")
SUBMENU_MODES = ("auto", "always", "never")
DEFAULT_THRESHOLD = 8
# Etichette gia' usate dallo script: i sottomenu non possono chiamarsi cosi'
RESERVED_LABELS = ("menu", "memtest", "local", "shell", "reboot", "exit", "failed")


def _plat(p):
    p = (p or "").lower()
    if p in ("efi", "uefi"):
        return "efi"
    if p in ("pcbios", "bios"):
        return "bios"
    return ""


def _flags(cfg, iso=None):
    """Flag per il rendering della ricetta. iso: voce di catalogo, per dare a ogni immagine i suoi driver."""
    from . import winpe
    try:
        return winpe.flags(cfg, iso)
    except Exception:  # noqa
        return {"smb_export": bool(cfg["windows"].get("smb_export_enabled")), "inject": bool(cfg["windows"].get("smb_export_enabled")), "driver_files": []}


def _safe(s):
    """Testo per le voci di menu: solo ASCII stampabile, niente a capo, ${}, token iPXE (&& || ;) o opzioni iniziali."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"[\r\n\t]", " ", s).replace("${", "$ {")
    s = re.sub(r"(?<!\S)(&&|\|\||;|#)(?!\S)", "-", s)
    s = re.sub(r"\s+", " ", s).strip().lstrip("-").strip()
    return (s or "voce")[:70]


def group_entries(ents, m):
    """[(nome gruppo, [voci])] nell'ordine di menu.groups; gruppi extra in coda, voci senza gruppo in "Altro"."""
    groups, seen = [], set()
    for g in list(m.get("groups") or []) + [e["group"] for e in ents if e["group"]]:
        if g and g not in seen:
            seen.add(g)
            groups.append(g)
    out = [(g, [e for e in ents if e["group"] == g]) for g in groups]
    out = [(g, ge) for g, ge in out if ge]
    ungrouped = [e for e in ents if not e["group"]]
    if ungrouped:
        out.append(("Altro", ungrouped))
    return out


def submenu_threshold(m):
    """Soglia (1-100) oltre la quale i sottomenu compaiono in modalita' "auto"."""
    try:
        t = int(m.get("submenu_threshold") or DEFAULT_THRESHOLD)
    except (TypeError, ValueError):
        t = DEFAULT_THRESHOLD
    return max(1, min(100, t))


def submenus_on(m, count):
    """True se il menu principale deve elencare i gruppi invece delle singole voci."""
    mode = str(m.get("submenus") or "auto").strip().lower()
    if mode not in SUBMENU_MODES:
        mode = "auto"
    if mode == "always":
        return True
    if mode == "never":
        return False
    return count > submenu_threshold(m)


def _group_labels(grouped, taken):
    """Etichette dei sottomenu (grp-1, grp-2, ...): solo a-z0-9-, univoche e senza collisioni con slug e voci di sistema."""
    used = set(taken) | set(RESERVED_LABELS)
    labels, n = {}, 0
    for name, _ in grouped:
        n += 1
        while f"grp-{n}" in used:
            n += 1
        labels[name] = f"grp-{n}"
        used.add(labels[name])
    return labels


def _submenu(label, title, name, ge, default, ind):
    """Sezione di un gruppo: secondo comando "menu" con le sue voci e il ritorno al menu principale (niente timeout)."""
    out = [f":{label}", f"menu {title}   |   {_safe(name)}"]
    for e in ge:
        out.append(f"item {e['slug']} {ind}{_safe(e['name'])}")
    out.append("item --gap")
    out.append(f"item menu {ind}Torna al menu principale")
    sel = default if any(e["slug"] == default for e in ge) else ge[0]["slug"]
    out += [f"choose --default {sel} sel || goto menu", "goto ${sel} || goto menu", ""]
    return out


def entries(cfg=None):
    """Voci abilitate e avviabili, raggruppate, in ordine."""
    cfg = cfg or S.load()
    isos, _ = catalog.list_isos()
    out = []
    for e in isos:
        if not e.get("enabled") or e.get("missing") or not e.get("mounted"):
            continue     # una ISO abilitata ma non montata (share giu') fallirebbe: il thread di background la rimonta
        out.append({"slug": e["slug"], "name": e["name"], "group": e.get("group") or "", "order": e.get("order", 0),
                    "enabled": True, "platforms": e.get("platforms", []), "type_name": e.get("type_name"), "mounted": e.get("mounted")})
    return out


def bootstrap_script(server_ip):
    """Primo script: rimanda al menu con i parametri del client (piattaforma, mac, arch)."""
    return "\n".join([
        "#!ipxe",
        "# Pixio: raccolgo le informazioni del client e carico il menu adatto",
        f"chain --autofree http://{server_ip}/boot.ipxe?platform=${{platform}}&arch=${{buildarch}}&mac=${{mac:hexhyp}}&ip=${{ip}}&manuf=${{manufacturer:uristring}}&product=${{product:uristring}} ||",
        "echo Pixio: impossibile caricare il menu. Riprovo tra 5 secondi...",
        "sleep 5",
        f"chain --autofree http://{server_ip}/boot.ipxe?platform=${{platform}}&arch=${{buildarch}}&mac=${{mac:hexhyp}}&ip=${{ip}} ||",
        "shell",
        "",
    ])


def menu_script(platform, mac=None, auto_boot=None, cfg=None, client_ip=""):
    """Script del menu per la piattaforma data. auto_boot: slug da avviare subito (client con boot automatico)."""
    cfg = cfg or S.load()
    ip = cfg["network"]["server_ip"]
    m = cfg["menu"]
    platform = _plat(platform) or "bios"
    plat_label = "UEFI" if platform == "efi" else "BIOS"
    ents = [e for e in entries(cfg) if platform in e["platforms"]]
    lines = ["#!ipxe", f"# Pixio - menu generato per {platform} {mac or ''}".rstrip(), f"set pixio_url http://{ip}", ""]
    from . import theme
    lines += theme.ipxe_header(cfg, ip)
    lines.append("")
    if auto_boot:
        target = f"http://{ip}/boot/{auto_boot}.ipxe?platform={platform}&mac={mac or ''}"
        lines += [f"echo Pixio: boot automatico configurato per questo PC ({auto_boot}). Premi ESC entro 5 secondi per il menu.",
                  "prompt --key 0x1b --timeout 5000 Premi ESC per il menu... && goto menu ||",
                  f"chain --autofree {target} || echo Boot automatico fallito, apro il menu.", ""]
    lines.append(":menu")
    title = _safe(m.get("title") or "PIXIO")
    lines.append(f"menu {title}   |   {plat_label}   |   {client_ip or '${ip}'}")
    grouped = group_entries(ents, m)
    subs = bool(grouped) and submenus_on(m, len(ents))
    labels = _group_labels(grouped, {e["slug"] for e in ents}) if subs else {}
    default = m.get("default") or "local"
    valid = {e["slug"] for e in ents} | {"local", "shell", "reboot", "exit", "memtest"}
    if default not in valid:
        default = "local"
    IND = "   "                      # rientro delle voci sotto il titolo del gruppo

    def group(title):
        lines.append("item --gap")   # riga vuota fra i gruppi
        lines.append(f"item --gap {_safe(title).upper()}")
    if subs:
        # la voce predefinita sta dentro un sottomenu: scorciatoia in cima, cosi' il timeout la avvia comunque
        dflt = next((e for e in ents if e["slug"] == default), None)
        if dflt:
            lines.append(f"item {dflt['slug']} {IND}{_safe(dflt['name'])} (predefinita)")
            lines.append("item --gap")
        for name, ge in grouped:
            lines.append(f"item {labels[name]} {IND}{_safe(name)} ({len(ge)})")
    else:
        for name, ge in grouped:
            group(name)
            for e in ge:
                lines.append(f"item {e['slug']} {IND}{_safe(e['name'])}")
    if not ents:
        lines.append("item --gap")
        lines.append("item --gap Nessuna ISO abilitata per questa piattaforma: aprire la GUI di Pixio")
    group("Sistema")
    if m.get("show_memtest", True):
        lines.append(f"item memtest {IND}Memtest86+ (test memoria)")
    if m.get("show_local", True):
        lines.append(f"item local {IND}Avvia dal disco locale")
    if m.get("show_shell", True):
        lines.append(f"item shell {IND}Shell iPXE")
    if m.get("show_reboot", True):
        lines.append(f"item reboot {IND}Riavvia")
    lines.append(f"item exit {IND}Esci da iPXE (prossimo dispositivo di boot)")
    timeout = int(m.get("timeout") or 0)
    if timeout > 0:      # il timeout vale solo per il menu principale
        lines.append(f"choose --default {default} --timeout {timeout * 1000} sel || goto {default}")
    else:
        lines.append(f"choose --default {default} sel || goto local")
    lines.append("goto ${sel} || goto menu")
    lines.append("")
    for name, ge in (grouped if subs else []):
        lines += _submenu(labels[name], title, name, ge, default, IND)
    for e in ents:
        lines += [f":{e['slug']}", f"chain --autofree http://{ip}/boot/{e['slug']}.ipxe?platform={platform}&mac={mac or ''} || goto failed", ""]
    memtest = recipes.render_builtin("memtest", ip, platform)
    lines += [":memtest"] + (memtest if memtest else ["echo Memtest non disponibile per questa piattaforma", "sleep 2", "goto menu"]) + [""]
    lines += [":local", "echo Avvio dal disco locale...",
              "sanboot --no-describe --drive 0x80 || echo Nessun disco avviabile trovato", "sleep 3", "goto menu", ""]
    lines += [":shell", "echo Digita 'exit' per tornare al menu", "shell", "goto menu", ""]
    lines += [":reboot", "reboot", "", ":exit", "exit", ""]
    lines += [":failed", "echo Avvio fallito. Torno al menu tra 5 secondi.", "sleep 5", "goto menu", ""]
    return "\n".join(lines)


def entry_script(slug, platform, cfg=None):
    """Script della singola voce: (testo, warnings). Testo con shebang; se non avviabile, script che torna al menu."""
    cfg = cfg or S.load()
    ip = cfg["network"]["server_ip"]
    platform = _plat(platform) or "bios"
    e = catalog.get(slug)
    if not e:
        return "#!ipxe\necho Voce non trovata\nsleep 3\nexit 1\n", ["voce non trovata"]
    lines, warnings = recipes.render(e, ip, platform, _flags(cfg, e))
    if not lines:
        msg = "; ".join(warnings) or "non avviabile"
        return f"#!ipxe\necho Pixio: {_safe(e['name'])} - {_safe(msg)}\nsleep 5\nexit 1\n", warnings
    head = ["#!ipxe", f"# Pixio - {e['name']} ({e.get('type_name')}) - {platform}", "imgfree",
            f"echo Avvio di {_safe(e['name'])}..."]
    body = []
    for l in lines:
        # ogni download deve fallire in modo visibile e tornare al menu
        if l.split(" ")[0] in ("kernel", "initrd", "chain", "imgfetch", "module"):
            body.append(f"{l} || goto failed")
        else:
            body.append(l)
    tail = ["", ":failed", "echo Download o avvio fallito (file mancante o non raggiungibile).", "imgfree", "sleep 5", "exit 1", ""]
    return "\n".join(head + body + tail), warnings


def preview(cfg=None):
    cfg = cfg or S.load()
    return {p: menu_script(p, mac="00-11-22-33-44-55", cfg=cfg, client_ip="10.0.0.99") for p in PLATFORMS}
