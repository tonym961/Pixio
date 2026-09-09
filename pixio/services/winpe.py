"""File iniettati nel WinPE (via wimboot -> X:\\Windows\\System32): winpeshl.ini e install.cmd, generati al volo.

install.cmd: drvload dei driver iniettati (rete/storage) -> wpeinit -> [net use share pxe -> drvload cartelle "setup_load"]
             -> setup.exe dalla share (o X:\\setup.exe se la share non e' attiva).

Ogni voce del menu riceve solo i driver abbinati alla sua immagine: flags() e install_cmd() portano avanti la
voce di catalogo (docs/API.md, sezione 15). Senza ISO il comportamento resta quello di prima (tutte le cartelle).

Perche' la share SMB e non un download: il programma di installazione di Windows legge install.wim (4 GB) un pezzo
per volta mentre installa, e sa farlo solo da un supporto locale o da una cartella di rete. Con la share non si
scarica niente prima di iniziare; scaricare l'immagine intera vorrebbe dire aspettare a ogni installazione.
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
    # le virgolette proteggono le password con caratteri che cmd interpreterebbe (&, ^, |)
    cred = f'"{wpass}" /user:{wuser}'
    L = ["@echo off", "title Pixio - avvio Windows", "echo.", "echo Pixio: preparazione di Windows PE...", "echo."]
    infs = [name for _, name, _ in f["driver_files"] if name.lower().endswith(".inf")]
    if infs:
        L.append("echo Carico i driver forniti da Pixio:")
        for name in infs:
            L.append(f'drvload X:\\Windows\\System32\\{name} >nul 2>&1 && echo    ok {name} || echo    non caricato: {name}')
        L.append("echo.")
    L += [
        # wimboot mette i file iniettati in X:\Windows\System32, ma il programma di installazione
        # cerca autounattend.xml solo nella radice dei supporti e in \Windows\Panther: se non glielo
        # passiamo con /unattend il file c'e' ma non viene usato, e l'installazione parte senza
        # nessuna delle personalizzazioni scelte in Pixio.
        "set PIXIO_UA=",
        "if exist X:\\Windows\\System32\\autounattend.xml set PIXIO_UA=/unattend:X:\\Windows\\System32\\autounattend.xml",
        "if defined PIXIO_UA (echo Personalizzazioni di Pixio: attive.) else (echo Nessuna personalizzazione: l'installazione fara' le domande.)",
        "echo.",
        "echo Avvio la rete...",
        "wpeinit",
        # In Windows PE il firewall e' attivo e il client SMB non sempre e' avviato: senza queste due
        # righe "net use" fallisce con l'errore di sistema 53 (percorso di rete non trovato).
        "wpeutil disablefirewall >nul 2>&1",
        "net start LanmanWorkstation >nul 2>&1",
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
            f"net use S: \\\\{ip}\\pxe {cred} /persistent:no >nul 2>&1 && goto ok",
            "if %tries% GEQ 15 goto diagnosi",
            "echo    tentativo %tries% di 15...",
            # il ping va fatto al server, non al loopback: cosi' l'attesa serve davvero ad aspettare
            # che la rete sia pronta invece di scandire dieci secondi a vuoto
            f"ping -n 3 {ip} >nul",
            "goto retry",
            ":ok",
            "echo Cartella collegata.",
            "echo.",
        ]
        for folder in f["setup_folders"]:
            L.append(f'echo Carico i driver della cartella "{folder}"...')
            L.append(f'for /r "S:\\drivers\\{folder}" %%f in (*.inf) do drvload "%%f" >nul 2>&1')
        L += [
            f'if not exist "S:\\iso\\{slug}\\setup.exe" goto senzasetup',
            "echo.",
            f"echo Avvio il programma di installazione da \\\\{ip}\\pxe\\iso\\{slug}",
            f"S:\\iso\\{slug}\\setup.exe %PIXIO_UA%",
            "goto fine_setup",
            # ------------------------------------------------------------------
            # Diagnosi: un solo schermo con la causa vera, cosi' basta fotografarlo.
            # I comandi girano senza >nul apposta: il messaggio di errore di "net use"
            # e' l'unica cosa che distingue una porta chiusa da una password sbagliata.
            ":diagnosi",
            "echo.",
            f"echo La cartella \\\\{ip}\\pxe non risponde. Ecco cosa dice questo PC:",
            "echo.",
            "echo --- indirizzi di questo PC ---",
            'ipconfig | find "IPv4"',
            'ipconfig | find "Subnet"',
            "echo.",
            f"echo --- il server {ip} risponde? ---",
            f"ping -n 2 {ip}",
            "echo.",
            "echo --- servizio client di rete ---",
            "net start LanmanWorkstation",
            "echo.",
            "echo --- prova di collegamento (senza cartella) ---",
            f"net use \\\\{ip}\\IPC$ {cred}",
            "echo.",
            "echo --- prova di collegamento (con la cartella) ---",
            f"net use S: \\\\{ip}\\pxe {cred} /persistent:no",
            "echo.",
            "echo Come si legge il risultato:",
            "echo   errore 53   = la porta 445 non arriva al server (rete, VLAN o firewall in mezzo)",
            "echo   errore 1326 = utente o password non corrispondono",
            "echo   errore 67   = il nome della cartella e' sbagliato",
            "echo   errore 1219 = c'e' gia' un collegamento con altre credenziali",
            "echo.",
            "echo Fotografa questa schermata: contiene la causa.",
            "echo.",
            "cmd.exe",
            "goto prompt",
            ":senzasetup",
            "echo.",
            f"echo PROBLEMA: la cartella e' collegata ma manca S:\\iso\\{slug}\\setup.exe.",
            "echo Quasi sempre significa che l'immagine non e' piu' montata sul server.",
            "echo Apri Pixio, pagina Catalogo, e rimonta l'immagine.",
            "echo.",
            "echo Contenuto di S:\\iso :",
            "dir S:\\iso",
            "echo.",
            "cmd.exe",
            "goto prompt",
        ]
    else:
        L += [
            "echo Installazione via rete non attiva nelle impostazioni di Pixio:",
            "echo avvio il programma di installazione del solo WinPE (senza immagine di Windows).",
            "X:\\setup.exe %PIXIO_UA%",
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
