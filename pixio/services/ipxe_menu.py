"""Generazione del menu iPXE e degli script per singola voce."""
import re

from .. import settings as S
from . import catalog, recipes

PLATFORMS = ("efi", "bios")


def _plat(p):
    p = (p or "").lower()
    if p in ("efi", "uefi"):
        return "efi"
    if p in ("pcbios", "bios"):
        return "bios"
    return ""


def _flags(cfg):
    return {"smb_export": bool(cfg["windows"].get("smb_export_enabled"))}


def _safe(s):
    """Testo per le voci di menu: niente a capo/${} che romperebbero lo script."""
    return re.sub(r"[\r\n]", " ", str(s)).replace("${", "$ {")[:70]


def entries(cfg=None):
    """Voci abilitate e avviabili, raggruppate, in ordine."""
    cfg = cfg or S.load()
    isos, _ = catalog.list_isos()
    out = []
    for e in isos:
        if not e.get("enabled") or e.get("missing"):
            continue
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
    if auto_boot:
        target = f"http://{ip}/boot/{auto_boot}.ipxe?platform={platform}&mac={mac or ''}"
        lines += [f"echo Pixio: boot automatico configurato per questo PC ({auto_boot}). Premi ESC entro 5 secondi per il menu.",
                  "prompt --key 0x1b --timeout 5000 Premi ESC per il menu... && goto menu ||",
                  f"chain --autofree {target} || echo Boot automatico fallito, apro il menu.", ""]
    lines.append(":menu")
    title = _safe(m.get("title") or "PIXIO")
    lines.append(f"menu {title} - {plat_label} - {client_ip or '${ip}'}")
    groups = list(m.get("groups") or [])
    for e in ents:
        if e["group"] and e["group"] not in groups:
            groups.append(e["group"])
    ungrouped = [e for e in ents if not e["group"]]
    for g in groups:
        ge = [e for e in ents if e["group"] == g]
        if not ge:
            continue
        lines.append(f"item --gap {_safe(g)}")
        for e in ge:
            lines.append(f"item {e['slug']} {_safe(e['name'])}")
    if ungrouped:
        lines.append("item --gap Altro")
        for e in ungrouped:
            lines.append(f"item {e['slug']} {_safe(e['name'])}")
    if not ents:
        lines.append("item --gap Nessuna ISO abilitata per questa piattaforma: aprire la GUI di Pixio")
    lines.append("item --gap Sistema")
    if m.get("show_memtest", True):
        lines.append("item memtest Memtest86+ (test memoria)")
    if m.get("show_local", True):
        lines.append("item local Avvia dal disco locale")
    if m.get("show_shell", True):
        lines.append("item shell Shell iPXE")
    if m.get("show_reboot", True):
        lines.append("item reboot Riavvia")
    lines.append("item exit Esci da iPXE (prossimo dispositivo di boot)")
    default = m.get("default") or "local"
    valid = {e["slug"] for e in ents} | {"local", "shell", "reboot", "exit", "memtest"}
    if default not in valid:
        default = "local"
    timeout = int(m.get("timeout") or 0)
    if timeout > 0:
        lines.append(f"choose --default {default} --timeout {timeout * 1000} sel || goto {default}")
    else:
        lines.append(f"choose --default {default} sel || goto local")
    lines.append("goto ${sel}")
    lines.append("")
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
    lines, warnings = recipes.render(e, ip, platform, _flags(cfg))
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
