"""File iniettati nel WinPE (via wimboot -> X:\\Windows\\System32): winpeshl.ini e install.cmd, generati al volo.

install.cmd: drvload dei driver iniettati (rete/storage) -> wpeinit -> [net use share pxe -> drvload cartelle "setup_load"]
             -> setup.exe dalla share (o X:\\setup.exe se la share non e' attiva).

Ogni voce del menu riceve solo i driver abbinati alla sua immagine: flags() e install_cmd() portano avanti la
voce di catalogo (docs/API.md, sezione 15). Senza ISO il comportamento resta quello di prima (tutte le cartelle).
"""
from .. import settings as S
from . import drivers


def flags(cfg=None, iso=None):
    """Flag per la generazione dello script iPXE / di install.cmd.

    iso: voce di catalogo che si sta avviando (dict con "slug" e "group"), per filtrare le cartelle driver
    in base ad apply_to. None = nessun filtro (anteprima generica)."""
    cfg = cfg or S.load()
    smb = bool(cfg["windows"].get("smb_export_enabled"))
    inj = drivers.winpe_inject_files(iso)
    return {"smb_export": smb, "inject": smb or bool(inj), "driver_files": inj,
            "setup_folders": drivers.setup_load_folders(iso) if smb else []}


def iso_for(slug):
    """Voce di catalogo per lo slug, o None se non c'e' (catalogo assente, slug sconosciuto)."""
    try:
        from . import catalog
        return catalog.get(slug)
    except Exception:  # noqa: BLE001
        return None


def winpeshl_ini():
    return "[LaunchApps]\r\n\"install.cmd\"\r\n"


def install_cmd(slug, cfg=None, iso=None):
    cfg = cfg or S.load()
    f = flags(cfg, iso if iso is not None else iso_for(slug))
    ip = cfg["network"]["server_ip"]
    wuser = cfg["windows"].get("smb_user") or "pxe"
    wpass = cfg["windows"].get("smb_password") or ""
    L = ["@echo off", "title Pixio - avvio Windows", "echo Pixio: preparazione WinPE..."]
    infs = [name for _, name, _ in f["driver_files"] if name.lower().endswith(".inf")]
    if infs:
        L.append("echo Carico i driver iniettati (rete/storage)...")
        for name in infs:
            L.append(f"drvload X:\\Windows\\System32\\{name} >nul 2>&1 && echo   ok {name} || echo   ERRORE {name}")
    L.append("wpeinit")
    if f["smb_export"]:
        L += [
            "set /a tries=0",
            ":retry",
            "set /a tries+=1",
            f"net use S: \\\\{ip}\\pxe {wpass} /user:{wuser} /persistent:no >nul 2>&1 && goto ok",
            "if %tries% GEQ 30 goto fail",
            "echo In attesa della rete (%tries%/30)...",
            "ping -n 3 127.0.0.1 >nul",
            "goto retry",
            ":ok",
        ]
        for folder in f["setup_folders"]:
            L.append(f"echo Carico i driver da \"{folder}\"...")
            L.append(f"for /r \"S:\\drivers\\{folder}\" %%f in (*.inf) do drvload \"%%f\" >nul 2>&1")
        L += [
            f"echo Avvio setup.exe da \\\\{ip}\\pxe\\iso\\{slug}",
            f"S:\\iso\\{slug}\\setup.exe",
            "goto end",
            ":fail",
            f"echo Impossibile raggiungere \\\\{ip}\\pxe (utente {wuser}). Controlla rete e driver. Apro il prompt.",
            "cmd.exe",
        ]
    else:
        L += [
            "echo Installazione Windows via rete non attiva: avvio il setup di WinPE (senza install.wim).",
            "echo Per usare install.wim attiva l'opzione nelle Impostazioni di Pixio, oppure mappa una share (Shift+F10, net use).",
            "X:\\setup.exe",
        ]
    L.append(":end")
    return "\r\n".join(L) + "\r\n"
