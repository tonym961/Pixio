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
    """Shell di Windows PE. Il percorso deve essere assoluto: se winpeshl non trova il programma esce
    subito e Windows PE riavvia il PC senza spiegazioni."""
    return "[LaunchApps]\r\n\"%SYSTEMDRIVE%\\Windows\\System32\\install.cmd\"\r\n"


def install_cmd(slug, cfg=None, iso=None):
    """Script eseguito dentro il WinPE: carica i driver, controlla la rete, mappa la share e lancia il setup.
    I messaggi devono dire cosa non va: senza, un guasto sembra solo un'attesa infinita."""
    cfg = cfg or S.load()
    f = flags(cfg, iso if iso is not None else iso_for(slug))
    ip = cfg["network"]["server_ip"]
    wuser = cfg["windows"].get("smb_user") or "pxe"
    wpass = cfg["windows"].get("smb_password") or ""
    L = ["@echo off", "title Pixio - avvio Windows", "echo.", "echo Pixio: preparazione di Windows PE...", "echo."]
    infs = [name for _, name, _ in f["driver_files"] if name.lower().endswith(".inf")]
    if infs:
        L.append("echo Carico i driver forniti da Pixio:")
        for name in infs:
            L.append(f'drvload X:\\Windows\\System32\\{name} >nul 2>&1 && echo    ok {name} || echo    non caricato: {name}')
        L.append("echo.")
    L += [
        "echo Avvio la rete...",
        "wpeinit",
        "echo.",
        # senza indirizzo IP e' inutile insistere: manca il driver della scheda di rete
        "set PIXIO_IP=",
        'for /f "tokens=2 delims=:" %%a in (\'ipconfig ^| find "IPv4"\') do if not defined PIXIO_IP set PIXIO_IP=%%a',
        "if not defined PIXIO_IP goto senzarete",
        "echo Indirizzo ottenuto:%PIXIO_IP%",
    ]
    if f["smb_export"]:
        L += [
            f"echo Collego la cartella di installazione \\\\{ip}\\pxe ...",
            "set /a tries=0",
            ":retry",
            "set /a tries+=1",
            f"net use S: \\\\{ip}\\pxe {wpass} /user:{wuser} /persistent:no >nul 2>&1 && goto ok",
            "if %tries% GEQ 10 goto nonraggiungibile",
            "echo    tentativo %tries% di 10...",
            "ping -n 3 127.0.0.1 >nul",
            "goto retry",
            ":ok",
        ]
        for folder in f["setup_folders"]:
            L.append(f'echo Carico i driver della cartella "{folder}"...')
            L.append(f'for /r "S:\\drivers\\{folder}" %%f in (*.inf) do drvload "%%f" >nul 2>&1')
        L += [
            "echo.",
            f"echo Avvio il programma di installazione da \\\\{ip}\\pxe\\iso\\{slug}",
            f"S:\\iso\\{slug}\\setup.exe",
            "goto fine_setup",
            ":nonraggiungibile",
            "echo.",
            f"echo PROBLEMA: la rete funziona ma la cartella \\\\{ip}\\pxe non risponde.",
            "echo Verifica l'ultimo messaggio qui sotto e poi usa il prompt.",
            f"net use S: \\\\{ip}\\pxe {wpass} /user:{wuser} /persistent:no",
            "echo.",
            "cmd.exe",
            "goto fine_setup",
        ]
    else:
        L += [
            "echo Installazione via rete non attiva nelle impostazioni di Pixio:",
            "echo avvio il programma di installazione del solo WinPE (senza immagine di Windows).",
            "X:\\setup.exe",
            "goto fine_setup",
        ]
    L += [
        ":fine_setup",
        "echo.",
        "echo Il programma di installazione e' terminato.",
        "echo Chiudendo questa finestra il PC si riavvia.",
        "cmd.exe",
        "goto prompt",
        ":senzarete",
        "echo.",
        "echo PROBLEMA: Windows PE non ha ottenuto un indirizzo di rete.",
        "echo Quasi sempre significa che manca il driver della scheda Ethernet di questo PC.",
        "echo Scaricalo dal sito del produttore, caricalo nella pagina Driver di Pixio",
        "echo e attiva \"Carica in WinPE all'avvio\" sulla sua cartella.",
        "echo.",
        "echo Schede rilevate:",
        "wpeutil listnetworkadapters 2>nul || ipconfig /all",
        "echo.",
        "echo Premi un tasto per aprire il prompt dei comandi.",
        "pause >nul",
        "cmd.exe",
        "goto prompt",
        ":prompt",
        # Windows PE riavvia il PC appena questo script finisce: restiamo sempre su un prompt aperto,
        # cosi' un errore resta leggibile invece di trasformarsi in un riavvio improvviso.
        "echo.",
        "echo Prompt di Pixio: scrivi exit per riavviare il PC.",
        "cmd.exe",
        "goto prompt",
        ":end",
        "goto prompt",
    ]
    return "\r\n".join(L) + "\r\n"
