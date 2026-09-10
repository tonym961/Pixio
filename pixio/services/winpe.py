"""File iniettati nel WinPE (via wimboot -> X:\\Windows\\System32): winpeshl.ini e install.cmd, generati al volo.

install.cmd: drvload dei driver iniettati (rete/storage) -> wpeinit -> [net use share pxe -> drvload cartelle "setup_load"]
             -> setup.exe dalla share (o X:\\setup.exe se la share non e' attiva).

Ogni voce del menu riceve solo i driver abbinati alla sua immagine: flags() e install_cmd() portano avanti la
voce di catalogo (docs/API.md, sezione 15). Senza ISO il comportamento resta quello di prima (tutte le cartelle).

Perche' la share SMB e non un download: il programma di installazione di Windows legge install.wim (4 GB) un pezzo
per volta mentre installa, e sa farlo solo da un supporto locale o da una cartella di rete. Con la share non si
scarica niente prima di iniziare; scaricare l'immagine intera vorrebbe dire aspettare a ogni installazione.

Raccolta dei log (docs/API.md, sezione 22): quando setup.exe finisce, install.cmd copia da solo i log del
programma di installazione nella share [pxelog], l'unica in cui i client possono scrivere. Cosi' un guasto si
legge dalla pagina Log della GUI invece che da una fotografia dello schermo. Il nome della cartella lo decide
il server quando genera lo script (data, ora, IP del client, immagine): il WinPE non ha un orologio affidabile
e comporre nomi in cmd e' fragile. La copia non deve mai fermare l'installazione: se la share non risponde lo
script lo scrive a schermo e va avanti.
"""
import datetime
import re

from .. import settings as S
from . import drivers

# Cartelle da cui si prendono i log. La prima e' quella vera del setup avviato da WinPE
# (%SYSTEMDRIVE% e' X:), le altre servono quando il setup ha gia' copiato i file sul disco.
LOG_SOURCES = [("X:\\$WINDOWS.~BT\\Sources\\Panther", "panther-winpe"),
               ("X:\\Windows\\Panther", "winpe-panther")]
LOG_DISKS = ("C", "D", "E")
LOG_DRIVE = "P:"          # S: e' gia' la share di installazione
LOG_PATTERNS = ("*.log", "*.xml", "*.txt")


def flags(cfg=None, iso=None):
    """Flag per la generazione dello script iPXE / di install.cmd.

    iso: voce di catalogo che si sta avviando (dict con "slug" e "group"), per filtrare le cartelle driver
    in base ad apply_to. None = nessun filtro (anteprima generica)."""
    cfg = cfg or S.load()
    smb = bool(cfg["windows"].get("smb_export_enabled"))
    inj = drivers.winpe_inject_files(iso)
    return {"smb_export": smb, "inject": smb or bool(inj), "driver_files": inj,
            "setup_folders": drivers.setup_load_folders(iso) if smb else [],
            # la raccolta dei log passa dalla share [pxelog], che l'helper esporta solo insieme alla [pxe]
            "setup_logs": smb and bool(cfg["windows"].get("setup_logs_enabled", True))}


# ---------------------------------------------------------------- raccolta dei log del setup
_CMD_UNSAFE = re.compile(r"[%&<>|^\r\n\x00\"]")


def cmd_safe(text, maxlen=120):
    """Testo utilizzabile dentro un "echo" di cmd: via i caratteri che cmd interpreterebbe.

    Un nome di ISO arriva da chi ha creato il file e puo' contenere & o %: finirebbero per
    troncare la riga o eseguire altro. Qui non si perde niente di importante, sono nomi."""
    return _CMD_UNSAFE.sub(" ", str(text or "")).strip()[:maxlen]


def log_folder(slug, client_ip="", when=None):
    """Nome della cartella in cui il PC deposita i log: <data>-<ora>-<ip>-<immagine>.

    Lo decide il server perche' il WinPE non ha un orologio attendibile (e %DATE% cambia formato
    con la lingua). Ordinabile per nome = ordinabile per momento dell'avvio."""
    when = when or datetime.datetime.now()
    ip = re.sub(r"[^0-9.]", "", str(client_ip or ""))[:15]
    coda = f"{ip}-{slug}" if ip else str(slug)
    coda = re.sub(r"[^A-Za-z0-9._-]", "-", coda)[:64]
    return f"{when:%Y%m%d-%H%M%S}-{coda}"


def _mac_for_ip(client_ip):
    """MAC del client visto con quell'IP (dai client PXE gia' noti), o "" se non lo sappiamo."""
    if not client_ip:
        return "", ""
    try:
        from . import clients
        for c in clients.list_clients():
            if c.get("ip") == client_ip:
                return c.get("mac") or "", c.get("name") or ""
    except Exception:  # noqa: BLE001 - il nome del PC e' un di piu': non deve far fallire il boot
        pass
    return "", ""


def _riepilogo(dest_var, coppie):
    """Righe cmd che scrivono riepilogo.txt (una "chiave: valore" per riga). La prima tronca il file."""
    out = []
    for i, (k, v) in enumerate(coppie):
        red = ">" if i == 0 else ">>"
        out.append(f'{red}"{dest_var}\\riepilogo.txt" echo {cmd_safe(k, 40)}: {cmd_safe(v) or "-"}')
    return out


def raccolta_log(slug, ip, cred, cartella_log, meta):
    """Sottoprogramma cmd che copia i log del setup nella share [pxelog].

    Regole: non deve mai bloccare (tre tentativi e basta), non deve mai far uscire lo script
    (ogni ramo torna al chiamante) e non deve nascondere l'esito, perche' chi guarda lo schermo
    deve sapere se i log sono arrivati o no."""
    share = f"\\\\{ip}\\{meta['share']}"
    dest = f"{LOG_DRIVE}\\{cartella_log}"
    L = [
        ":pixio_log",
        "rem --- Log del programma di installazione verso Pixio (docs/API.md, sezione 22).",
        "echo.",
        "echo Copio i log dell'installazione su Pixio...",
        "set PIXIO_TRY=0",
        ":pixio_log_retry",
        "set /a PIXIO_TRY+=1",
        f"net use {LOG_DRIVE} {share} {cred} /persistent:no >nul 2>&1",
        "if not errorlevel 1 goto pixio_log_ok",
        "if %PIXIO_TRY% GEQ 3 goto pixio_log_ko",
        f"ping -n 2 {ip} >nul",
        "goto pixio_log_retry",
        ":pixio_log_ok",
        f'set PIXIO_DEST={dest}',
        'md "%PIXIO_DEST%" >nul 2>&1',
    ]
    L += _riepilogo("%PIXIO_DEST%", meta["righe"])
    L += [
        # l'esito di setup.exe e' il primo dato che serve: 0 = ha fatto il suo lavoro
        '>>"%PIXIO_DEST%\\riepilogo.txt" echo esito setup.exe: %PIXIO_ESITO%',
        '>>"%PIXIO_DEST%\\riepilogo.txt" echo personalizzazioni: %PIXIO_UA%',
        '>>"%PIXIO_DEST%\\riepilogo.txt" echo indirizzo del PC:%PIXIO_IP%',
        # lo stato della rete e quello dei dischi spiegano da soli meta' dei guasti
        'ipconfig /all > "%PIXIO_DEST%\\rete.txt" 2>&1',
        ">X:\\Windows\\Temp\\pixio-dp.txt echo list disk",
        ">>X:\\Windows\\Temp\\pixio-dp.txt echo list volume",
        'diskpart /s X:\\Windows\\Temp\\pixio-dp.txt > "%PIXIO_DEST%\\disco.txt" 2>&1',
        # il file di risposta davvero usato e lo script davvero eseguito: senza, l'analisi si fa a memoria
        'if exist X:\\Windows\\System32\\autounattend.xml copy /y X:\\Windows\\System32\\autounattend.xml "%PIXIO_DEST%\\autounattend.xml" >nul 2>&1',
        'if exist X:\\Windows\\System32\\install.cmd copy /y X:\\Windows\\System32\\install.cmd "%PIXIO_DEST%\\install.cmd" >nul 2>&1',
    ]
    for path, name in LOG_SOURCES:
        L.append(f'call :pixio_copia "{path}" {name}')
    # quando il setup ha gia' copiato i file sul disco i log proseguono li': le lettere possibili sono poche
    L.append(f'for %%u in ({" ".join(LOG_DISKS)}) do call :pixio_copia "%%u:\\$WINDOWS.~BT\\Sources\\Panther" panther-%%u')
    L.append(f'for %%u in ({" ".join(LOG_DISKS)}) do call :pixio_copia "%%u:\\Windows\\Panther" windows-%%u')
    L += [
        "echo    fatto: i log sono su Pixio.",
        f"echo    Pixio, pagina Log, scheda Installazioni: {cartella_log}",
        f"net use {LOG_DRIVE} /delete /y >nul 2>&1",
        "goto :eof",
        ":pixio_log_ko",
        f"echo    non riesco a collegare {share}: i log restano solo su questo PC.",
        "echo    (X:\\$WINDOWS.~BT\\Sources\\Panther\\setupact.log)",
        "goto :eof",
        # copia di una cartella di log: %1 = cartella di origine, %2 = nome della sottocartella di destinazione
        ":pixio_copia",
        'if not exist "%~1" goto :eof',
    ]
    for pat in LOG_PATTERNS:
        # /c: va avanti anche se un file e' in uso dal setup; /q e >nul: niente elenchi a schermo
        L.append(f'xcopy "%~1\\{pat}" "%PIXIO_DEST%\\%~2\\" /s /c /i /y /q >nul 2>&1')
    L.append("goto :eof")
    return L


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


def install_cmd(slug, cfg=None, iso=None, client_ip="", when=None):
    """Script eseguito dentro il WinPE: carica i driver, controlla la rete, mappa la share e lancia il setup.
    I messaggi devono dire cosa non va: senza, un guasto sembra solo un'attesa infinita.

    client_ip / when: chi sta chiedendo lo script e quando (li passa il blueprint boot.py). Servono solo a
    dare un nome alla cartella dei log e a scriverci dentro chi era il PC: senza, lo script funziona uguale."""
    cfg = cfg or S.load()
    voce = iso if iso is not None else iso_for(slug)
    f = flags(cfg, voce)
    ip = cfg["network"]["server_ip"]
    wuser = cfg["windows"].get("smb_user") or "pxe"
    wpass = cfg["windows"].get("smb_password") or ""
    # le virgolette proteggono le password con caratteri che cmd interpreterebbe (&, ^, |)
    cred = f'"{wpass}" /user:{wuser}'
    when = when or datetime.datetime.now()
    cartella_log = log_folder(slug, client_ip, when)
    mac, nome_pc = _mac_for_ip(client_ip)
    meta = {"share": cfg["windows"].get("setup_logs_share_name") or "pxelog",
            "righe": [("Pixio", "log del programma di installazione di Windows"),
                      ("immagine", (voce or {}).get("name") or slug),
                      ("slug", slug),
                      ("avvio", f"{when:%d/%m/%Y %H:%M:%S} (ora del server Pixio)"),
                      ("server", ip),
                      ("client", client_ip or "sconosciuto"),
                      ("mac", mac or "sconosciuto"),
                      ("pc", nome_pc or "")]}
    L = ["@echo off", "title Pixio - avvio Windows", "echo.", "echo Pixio: preparazione di Windows PE...", "echo."]
    if f["setup_logs"]:
        # l'esito serve nel riepilogo anche se il setup non parte proprio: senza valore la riga direbbe "%PIXIO_ESITO%"
        L.append("set PIXIO_ESITO=non avviato")
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
            "set PIXIO_ESITO=%ERRORLEVEL%",
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
        ]
        # la share risponde: il riepilogo (con l'elenco dei dischi) arriva su Pixio anche in questo caso
        if f["setup_logs"]:
            L += ["set PIXIO_ESITO=setup.exe non trovato sulla share", "call :pixio_log"]
        L += [
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
        "echo Il programma di installazione e' terminato (codice %PIXIO_ESITO%).",
    ]
    if f["setup_logs"]:
        L.append("call :pixio_log")
    L += [
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
    # sottoprogrammi in coda: ci si arriva solo con "call", mai per caduta (sopra si finisce sempre in :prompt)
    if f["setup_logs"]:
        L += raccolta_log(slug, ip, cred, cartella_log, meta)
    return "\r\n".join(L) + "\r\n"
