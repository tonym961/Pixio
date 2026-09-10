"""Personalizzazione Windows: profili che generano un file autounattend.xml (docs/API.md, sezione 8).

Un profilo è `{id, name, note, updated, settings:{...}}`; stanno tutti in un solo file JSON
(WINPROFILES_FILE, di norma /var/lib/pixio/winprofiles.json) scritto in modo atomico da storage.update_json.

`render_autounattend(profile, server_ip)` costruisce l'XML con xml.etree (quindi con l'escaping corretto
di & < > " anche dentro password e nomi), lo rilegge con xml.dom.minidom per verificarlo e lo restituisce
indentato. I passaggi generati sono tre:

  windowsPE   lingua del setup, tastiera, partizionamento (GPT per UEFI, MBR per BIOS), immagine da
              installare, chiave di prodotto, EULA accettata, ed eventuali voci di registro LabConfig
              per aggirare i requisiti di Windows 11;
  specialize  nome computer, fuso orario, aggiunta al dominio, percorso dei driver di Pixio, tweak di
              sistema (criteri HKLM e avvio dei servizi) e preferenze HKCU scritte una volta sola sul
              profilo predefinito con un solo reg load/reg unload;
  oobeSystem  schermate OOBE saltate, utenti locali, accesso automatico, comandi al primo accesso
              (rimozione delle app preinstallate, comandi delle ottimizzazioni, dism per le
              funzionalità facoltative e comandi del tecnico).

Le ottimizzazioni in stile nLite stanno in data/windows-tweaks.json (docs/API.md, sezione 10) e si
scelgono per identificativo in settings["tweaks"]; il catalogo si carica una volta e si ricarica solo
se il file cambia (tweaks_catalog(), tweak(id)).

Il campo settings["target"] dice a che tipo di Windows è destinato il profilo: "client" (Windows 10
e 11 con Microsoft Store, predefinito), "10-ltsc" (Windows 10 Enterprise LTSC 2019/2021),
"11-ltsc" (Windows 11 Enterprise LTSC 2024) oppure "server" (Windows Server 2016-2025 con
interfaccia grafica). Ogni voce del catalogo dichiara in "editions" le piattaforme su cui ha
davvero effetto: la validazione rifiuta le voci non compatibili col target, la generazione le salta
senza errori e le app Appx da rimuovere vengono ignorate con i tipi senza Microsoft Store, cioè
"server" e le due LTSC (docs/API.md, sezioni 11 e 12).

Il campo settings["language_install"] installa una lingua dopo il setup (docs/API.md, sezione 13):
serve alle ISO che contengono una lingua sola, per esempio Windows Server 2022 English, dove
finora bisognava aggiungere a mano il pacchetto della lingua italiana. Con l'interruttore acceso
in FirstLogonCommands finiscono prima i comandi che installano la lingua (Install-Language da
Windows Update oppure curl.exe + dism sul pacchetto caricato in Pixio) e poi quelli che la
rendono lingua di sistema (Set-SystemPreferredUILanguage e compagni); con l'interruttore spento
non viene generato niente.

ATTENZIONE alle password: autounattend.xml le contiene in chiaro (PlainText true) e il file viene servito
ai client via HTTP senza autenticazione. È il funzionamento previsto dal contratto; la GUI lo dice a chiare
lettere. Anche il bypass dei requisiti di Windows 11 non è una configurazione supportata da Microsoft.
"""
import copy
import logging
import os
import re
import secrets
import threading
import time
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET

from .. import config as C
from ..storage import read_json, update_json, deep_merge

log = logging.getLogger("pixio.winprofile")

# ---------------------------------------------------------------- costanti e valori predefiniti

PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# lingua/locale in stile Windows: it-IT, en-US, sr-Latn-RS
LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$")
# tastiera: sigla di lingua oppure coppia esadecimale 0410:00000410 (anche più d'una separata da ;)
KBD_HEX_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{8}$")
# fuso orario in formato Windows: "W. Europe Standard Time"
TIMEZONE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 .()'+-]{2,63}$")
PRODUCT_KEY_RE = re.compile(r"^[A-Z0-9]{5}(-[A-Z0-9]{5}){4}$")
COMPUTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*\*?$")
DOMAIN_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                       r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
OU_RE = re.compile(r"^[A-Za-z0-9 ,=._'()-]{3,255}$")
# nome utente locale di Windows: niente " / \ [ ] : ; | = , + * ? < > @
USER_BAD_CHARS = set('"/\\[]:;|=,+*?<>@')
EDITION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,63}$")
APP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
# identificativo di un'ottimizzazione del catalogo (slug stabile, finisce nei profili)
TWEAK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# sigla di un servizio Windows (quella di sc.exe, non il nome visualizzato)
SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# nome di una funzionalità facoltativa per DISM (es. NetFx3, SMB1Protocol)
FEATURE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

MAX_NAME = 64
MAX_NOTE = 200
MAX_PROFILES = 200
MAX_APPS = 100
MAX_COMMANDS = 40
MAX_CMD_LEN = 500
MAX_TWEAKS = 200
MAX_SERVICES = 60
MAX_FEATURES = 30
MAX_PASSWORD = 127          # limite di Windows per le password locali
MAX_USER = 20               # limite di Windows per il nome utente locale
MAX_COMPUTER = 15           # limite NetBIOS

ARCHITECTURES = ("amd64", "x86")
DISK_MODES = ("auto-uefi", "auto-bios", "manuale")
DISK_MODE_LABELS = {
    "auto-uefi": "Automatico UEFI (GPT)",
    "auto-bios": "Automatico BIOS legacy (MBR)",
    "manuale": "Manuale (le schermate del disco restano)",
}
POWER_SCHEMES = ("bilanciato", "prestazioni")
# GUID degli schemi di risparmio energia di Windows
POWER_GUIDS = {
    "bilanciato": "381b4222-f694-41f0-9685-ff5bb260df2e",
    "prestazioni": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
}
LOCAL_GROUPS = ("Users", "Administrators", "Power Users", "Remote Desktop Users", "Guests")

# Tipo di Windows a cui è destinato il profilo (docs/API.md, sezioni 11 e 12). Serve a tenere fuori
# dai server e dalle edizioni Enterprise LTSC le ottimizzazioni che toccano componenti che lì non
# esistono: su Windows Server mancano Cortana, Copilot, widget, Xbox, Microsoft Store, esperienze
# consumer e la barra applicazioni di Windows 11; nelle LTSC (Windows 10 Enterprise LTSC 2019/2021
# e Windows 11 Enterprise LTSC 2024) mancano il Microsoft Store e le app che ne dipendono, Cortana,
# Copilot, widget e notizie, Teams e Chat, Xbox e Game Bar, le esperienze consumer, i contenuti
# consigliati e il client OneDrive preinstallato, mentre restano telemetria, Windows Search,
# Defender, SmartScreen, UAC, Windows Update, servizi, rete, effetti visivi, energia e componenti
# opzionali.
TARGETS = ("client", "10-ltsc", "11-ltsc", "server")
TARGET_LABELS = {
    "client": "Windows client (10 e 11)",
    "10-ltsc": "Windows 10 Enterprise LTSC (2019 e 2021)",
    "11-ltsc": "Windows 11 Enterprise LTSC (2024)",
    "server": "Windows Server (2016-2025, con interfaccia grafica)",
}
# Piattaforme ammesse nel campo "editions" del catalogo e piattaforme coperte da ogni target.
PLATFORMS = ("10", "10-ltsc", "11", "11-ltsc", "server")
PLATFORM_LABELS = {
    "10": "Windows 10",
    "10-ltsc": "Windows 10 Enterprise LTSC",
    "11": "Windows 11",
    "11-ltsc": "Windows 11 Enterprise LTSC",
    "server": "Windows Server",
}
TARGET_PLATFORMS = {
    "client": ("10", "11"),
    "10-ltsc": ("10-ltsc",),
    "11-ltsc": ("11-ltsc",),
    "server": ("server",),
}
# Tipi di Windows senza Microsoft Store: la rimozione delle app Appx viene ignorata in generazione
# (docs/API.md, sezione 12). L'elenco resta scritto nel profilo, così tornando a un tipo con lo
# Store si ritrova.
TARGETS_SENZA_STORE = ("10-ltsc", "11-ltsc", "server")
# Perché una voce non compatibile non ha effetto su quel tipo di Windows: entra nel messaggio di
# errore della validazione, che deve dire quale voce e perché.
TARGET_MOTIVI = {
    "server": "tocca componenti che su Windows Server non esistono",
    "10-ltsc": "tocca componenti che in Windows 10 Enterprise LTSC non sono presenti (Microsoft "
               "Store e le app che ne dipendono, Cortana, contenuti consigliati, esperienze "
               "consumer, Xbox, OneDrive preinstallato, aggiornamenti di funzionalità)",
    "11-ltsc": "tocca componenti che in Windows 11 Enterprise LTSC non sono presenti (Microsoft "
               "Store e le app che ne dipendono, Cortana, Copilot, widget e notizie, Teams e Chat, "
               "contenuti consigliati, esperienze consumer, Xbox, OneDrive preinstallato, "
               "aggiornamenti di funzionalità)",
}

# Nomi delle immagini più comuni per ogni tipo di Windows (docs/API.md, sezione 19). Non sono
# valori predefiniti ma suggerimenti: l'elenco vero lo dà install.wim della ISO abbinata al
# profilo, e questi servono solo quando la ISO non si conosce, per non far scrivere a memoria un
# nome che in quell'immagine non esiste. Le LTSC, per esempio, non contengono nessun "Windows 11
# Pro": chi lo scrive vede fallire l'installazione a metà.
TARGET_EDITIONS = {
    # sulle ISO client il nome cambia con la ISO (Home, Pro, Education...): meglio farlo scegliere
    "client": [],
    "10-ltsc": ["Windows 10 Enterprise LTSC 2021", "Windows 10 Enterprise N LTSC 2021",
                "Windows 10 Enterprise LTSC 2019", "Windows 10 Enterprise N LTSC 2019"],
    "11-ltsc": ["Windows 11 Enterprise LTSC 2024", "Windows 11 Enterprise N LTSC 2024"],
    # i nomi dei server contengono l'anno: quello giusto dipende dalla ISO che si sta usando
    "server": ["Windows Server 2025 SERVERSTANDARD", "Windows Server 2025 SERVERDATACENTER",
               "Windows Server 2022 SERVERSTANDARD", "Windows Server 2022 SERVERDATACENTER",
               "Windows Server 2019 SERVERSTANDARD", "Windows Server 2016 SERVERSTANDARD"],
}

# Avvio dei servizi come lo scrive il registro (chiave Start): 2 automatico, 3 manuale, 4 disabilitato.
# 0 e 1 (driver di avvio) non si toccano da un file di risposta: renderebbero il sistema non avviabile.
SERVICE_STARTS = (2, 3, 4)
SERVICE_START_LABELS = {2: "in avvio automatico", 3: "in avvio manuale", 4: "disabilitato"}
REG_TYPES = ("REG_DWORD", "REG_SZ", "REG_EXPAND_SZ", "REG_QWORD", "REG_MULTI_SZ", "REG_BINARY")
IMPACTS = ("sicuro", "attenzione", "rischioso")

# Profilo predefinito: si carica il suo NTUSER.DAT una volta sola, si scrivono tutte le preferenze
# HKCU dei tweak e lo si scarica. Vale per tutti gli utenti creati dopo l'installazione.
DEFAULT_HIVE = "HKU\\PixioDef"
DEFAULT_NTUSER = "C:\\Users\\Default\\NTUSER.DAT"
SERVICES_KEY = "SYSTEM\\CurrentControlSet\\Services"

# Partizione riservata di sistema in BIOS/MBR: dimensione fissa, come fa il setup di Windows
BIOS_SYSTEM_MB = 500
# TypeID GPT della partizione di ripristino (Windows RE)
RECOVERY_TYPE_ID = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"

NS = "urn:schemas-microsoft-com:unattend"
WCM = "http://schemas.microsoft.com/WMIConfig/2002/State"
PUBLIC_KEY_TOKEN = "31bf3856ad364e35"

# Fusi orari di Windows più usati (nome esatto richiesto dal setup). L'elenco serve alla tendina della GUI.
TIMEZONES = [
    "W. Europe Standard Time", "Central European Standard Time", "Romance Standard Time",
    "GMT Standard Time", "Greenwich Standard Time", "UTC",
    "E. Europe Standard Time", "FLE Standard Time", "GTB Standard Time",
    "Russian Standard Time", "Turkey Standard Time", "Israel Standard Time",
    "Arabian Standard Time", "India Standard Time", "China Standard Time", "Tokyo Standard Time",
    "AUS Eastern Standard Time", "New Zealand Standard Time",
    "Eastern Standard Time", "Central Standard Time", "Mountain Standard Time",
    "Pacific Standard Time", "SA Eastern Standard Time",
]

# Lingue dell'interfaccia proposte nella GUI
LANGUAGES = [
    {"id": "it-IT", "name": "Italiano (Italia)"},
    {"id": "en-US", "name": "Inglese (Stati Uniti)"},
    {"id": "en-GB", "name": "Inglese (Regno Unito)"},
    {"id": "de-DE", "name": "Tedesco (Germania)"},
    {"id": "de-AT", "name": "Tedesco (Austria)"},
    {"id": "de-CH", "name": "Tedesco (Svizzera)"},
    {"id": "fr-FR", "name": "Francese (Francia)"},
    {"id": "fr-CH", "name": "Francese (Svizzera)"},
    {"id": "es-ES", "name": "Spagnolo (Spagna)"},
    {"id": "pt-PT", "name": "Portoghese (Portogallo)"},
    {"id": "nl-NL", "name": "Olandese (Paesi Bassi)"},
    {"id": "sl-SI", "name": "Sloveno (Slovenia)"},
    {"id": "hr-HR", "name": "Croato (Croazia)"},
    {"id": "el-GR", "name": "Greco (Grecia)"},
    {"id": "pl-PL", "name": "Polacco (Polonia)"},
    {"id": "ro-RO", "name": "Rumeno (Romania)"},
]

# ---- Lingua installata dopo il setup (docs/API.md, sezione 13) --------------------------------
# Serve alle ISO che contengono una lingua sola (per esempio Windows Server 2022 English, il cui
# install.wim ha solo en-US): l'installazione parte in inglese e si ritrova in italiano da sola,
# senza che nessuno debba installare a mano il pacchetto lingua. I comandi finiscono in
# FirstLogonCommands e usano il modulo LanguagePackManagement (Install-Language), presente in
# Windows 10/11 e in Windows Server 2019, 2022 e 2025.

# Tag BCP-47 come lo vuole il contratto: due lettere minuscole più eventuali sottotag (it-IT,
# en-US, sr-Latn-RS). È più stretto di LANG_RE: qui i valori finiscono dentro comandi PowerShell.
LANG_TAG_RE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2,8})*$")
# Indirizzo del pacchetto lingua: lo scarica il PC in installazione, quindi solo http/https
# (niente percorsi UNC o file:// che il setup non saprebbe raggiungere) e niente spazi o virgolette
LANG_URL_RE = re.compile(r"^https?://[^\s\"'<>]+$", re.I)
MAX_LANG_INSTALL = 5        # lingue installabili in una volta sola (ognuna è un pacchetto da scaricare)
MAX_GEO_ID = 100000
MAX_URL = 500

# Da dove arriva il pacchetto lingua
LANG_SOURCES = ("windows-update", "file")
LANG_SOURCE_LABELS = {
    "windows-update": "Windows Update",
    "file": "Pacchetto caricato in Pixio",
}

# Lingue più comuni in Italia con il loro GeoId, quello che vuole Set-WinHomeLocation:
# Italia 118, Regno Unito 242, Stati Uniti 244, Germania 94, Francia 84, Spagna 217.
LANGUAGES_INSTALL = [
    {"tag": "it-IT", "name": "Italiano (Italia)", "geo_id": 118},
    {"tag": "en-GB", "name": "Inglese (Regno Unito)", "geo_id": 242},
    {"tag": "en-US", "name": "Inglese (Stati Uniti)", "geo_id": 244},
    {"tag": "de-DE", "name": "Tedesco (Germania)", "geo_id": 94},
    {"tag": "fr-FR", "name": "Francese (Francia)", "geo_id": 84},
    {"tag": "es-ES", "name": "Spagnolo (Spagna)", "geo_id": 217},
]
GEO_IDS = {x["tag"]: x["geo_id"] for x in LANGUAGES_INSTALL}
GEO_ID_ITALIA = 118
# File temporaneo dove finisce il pacchetto scaricato: curl.exe c'è in Windows 10 dalla 1803 e in
# Windows Server dal 2019, quindi anche nel primo accesso di un'installazione appena fatta.
LANG_CAB = "%TEMP%\\lang.cab"

# App preinstallate che il tecnico rimuove più spesso (Windows 10/11)
APPS = [
    {"id": "Microsoft.XboxApp", "name": "Xbox"},
    {"id": "Microsoft.GamingApp", "name": "Xbox (app giochi)"},
    {"id": "Microsoft.XboxGamingOverlay", "name": "Xbox Game Bar"},
    {"id": "Microsoft.XboxGameOverlay", "name": "Xbox (overlay di gioco)"},
    {"id": "Microsoft.XboxIdentityProvider", "name": "Xbox (accesso)"},
    {"id": "Microsoft.ZuneMusic", "name": "Groove Musica / Media Player"},
    {"id": "Microsoft.ZuneVideo", "name": "Film e TV"},
    {"id": "Microsoft.BingNews", "name": "Notizie"},
    {"id": "Microsoft.BingWeather", "name": "Meteo"},
    {"id": "Microsoft.SkypeApp", "name": "Skype"},
    {"id": "Microsoft.MicrosoftSolitaireCollection", "name": "Solitario"},
    {"id": "Microsoft.People", "name": "Contatti"},
    {"id": "Microsoft.GetHelp", "name": "Richiesta supporto"},
    {"id": "Microsoft.Getstarted", "name": "Suggerimenti"},
    {"id": "Microsoft.MicrosoftOfficeHub", "name": "Office (collegamento)"},
    {"id": "Microsoft.Todos", "name": "To Do"},
    {"id": "Microsoft.WindowsFeedbackHub", "name": "Hub di feedback"},
    {"id": "Microsoft.WindowsMaps", "name": "Mappe"},
    {"id": "Microsoft.YourPhone", "name": "Collegamento al telefono"},
    {"id": "Clipchamp.Clipchamp", "name": "Clipchamp"},
    {"id": "MicrosoftTeams", "name": "Teams (personale)"},
    {"id": "MSTeams", "name": "Teams (nuovo)"},
    {"id": "Microsoft.MicrosoftStickyNotes", "name": "Note adesive"},
    {"id": "Microsoft.549981C3F5F10", "name": "Cortana"},
]

# Valori predefiniti: PC italiano, disco UEFI, OOBE saltato, driver di Pixio attivi.
DEFAULTS = {
    # "client" (Windows 10/11), "10-ltsc", "11-ltsc" (Enterprise LTSC) oppure "server"
    "target": "client",
    "language": "it-IT",
    "input_locale": "it-IT",
    "timezone": "W. Europe Standard Time",
    # lingua installata dopo il setup, per le ISO che non contengono quella voluta (sezione 13):
    # con "enabled" falso non viene generato nessun comando
    "language_install": {
        "enabled": False,
        "languages": ["it-IT"],
        "source": "windows-update",     # oppure "file": pacchetto caricato in Pixio
        "file_url": "",
        "set_system": True,             # lingua del sistema, formati e area geografica
        "geo_id": 118,                  # Italia
        "keyboard": "it-IT",
    },
    "architecture": "amd64",
    "edition_index": "",
    "product_key": "",
    "computer_name": "*",
    "organization": "",
    "owner": "",
    "admin_user": "amministratore",
    "admin_password": "",
    "autologon": False,
    "autologon_count": 0,
    "extra_user": {"name": "", "password": "", "group": "Users"},
    "join_domain": {"enabled": False, "domain": "", "ou": "", "user": "", "password": ""},
    "disk": {"mode": "auto-uefi", "wipe": True, "efi_mb": 300, "msr_mb": 16, "recovery_mb": 750},
    "skip_oobe": True,
    "bypass_requirements": False,
    "disable_defender_prompt": False,
    "hide_files_ext": True,
    "disable_hibernate": True,
    "power_scheme": "bilanciato",
    "remove_apps": [],
    "run_commands": [],
    "tweaks": [],                # id delle ottimizzazioni scelte in data/windows-tweaks.json
    "services_extra": [],        # [{name, start}] servizi aggiunti a mano dal tecnico
    "features_enable": [],       # funzionalità Windows da attivare con dism
    "features_disable": [],      # funzionalità Windows da disattivare con dism
    "drivers_from_pixio": True,
}


def defaults():
    """Copia profonda dei valori predefiniti (i dizionari annidati non vanno condivisi)."""
    return copy.deepcopy(DEFAULTS)


def timezones_list():
    return list(TIMEZONES)


def languages_list():
    return [dict(x) for x in LANGUAGES]


def languages_install_list():
    """Lingue installabili dopo il setup, con il loro GeoId (docs/API.md, sezione 13).

    Ritorna `[{tag, name, geo_id}]` per la tendina "Lingua da installare" della GUI: le lingue più
    comuni in Italia (italiano, inglese del Regno Unito e degli Stati Uniti, tedesco, francese e
    spagnolo). Il GeoId è il numero che vuole Set-WinHomeLocation: Italia 118, Regno Unito 242,
    Stati Uniti 244, Germania 94, Francia 84, Spagna 217.
    """
    return [dict(x) for x in LANGUAGES_INSTALL]


def lang_sources_list():
    """Sorgenti del pacchetto lingua per la GUI: [{id, name}] (docs/API.md, sezione 13)."""
    return [{"id": s, "name": LANG_SOURCE_LABELS[s]} for s in LANG_SOURCES]


def geo_id_di(tag):
    """GeoId della lingua indicata; per una lingua fuori elenco quello dell'Italia."""
    return GEO_IDS.get(_txt(tag), GEO_ID_ITALIA)


def apps_list():
    return [dict(x) for x in APPS]


def disk_modes_list():
    return [{"id": m, "name": DISK_MODE_LABELS[m]} for m in DISK_MODES]


def groups_list():
    return list(LOCAL_GROUPS)


def targets_list():
    """Tipi di Windows per la tendina della GUI: [{id, name, editions}] (docs/API.md, sez. 11, 12 e 19).

    `editions` sono i nomi di immagine tipici di quel tipo di Windows: la GUI li propone quando il
    profilo non è abbinato a nessuna ISO da cui leggere quelli veri."""
    return [{"id": t, "name": TARGET_LABELS[t], "editions": list(TARGET_EDITIONS.get(t) or [])}
            for t in TARGETS]


def target_ha_store(target):
    """True se su quel tipo di Windows c'è il Microsoft Store (e quindi le app Appx da rimuovere)."""
    return _txt(target).lower() not in TARGETS_SENZA_STORE


def check_target(valore, default="client"):
    """Normalizza settings.target. Alza ValueError se non è uno dei tipi previsti."""
    t = _txt(valore).lower() or default
    if t not in TARGETS:
        raise ValueError("Tipo di Windows non valido: " + ", ".join(TARGETS) + ". "
                         + "; ".join("%s = %s" % (x, TARGET_LABELS[x]) for x in TARGETS))
    return t


# ---------------------------------------------------------------- edizione da installare
# `settings["edition_index"]` finisce in <InstallFrom> come nome dell'immagine (/IMAGE/NAME) o come
# indice (/IMAGE/INDEX). Se quel valore nell'install.wim non c'è, il setup si ferma con
# "impossibile trovare l'immagine" a metà installazione: quando l'immagine di destinazione è nota
# (docs/API.md, sezione 19) conviene accorgersene prima, e in generazione non scrivere niente
# invece di scrivere qualcosa che fallirà di sicuro.

def match_edition(editions, valore):
    """Immagine di `editions` che corrisponde a `valore`, oppure None.

    Il confronto è quello che fa il setup, più tollerante sulle maiuscole: un numero vale come
    indice, un testo come nome dell'immagine, e vale anche il nome visualizzato, che è quello che
    la gente legge nelle schermate di installazione."""
    v = _txt(valore)
    if not v or not editions:
        return None
    if v.isdigit():
        n = int(v)
        return next((im for im in editions if int(im.get("index") or 0) == n), None)
    basso = v.lower()
    for chiave in ("name", "display_name"):
        im = next((im for im in editions if _txt(im.get(chiave)).lower() == basso), None)
        if im is not None:
            return im
    return None


def editions_labels(editions):
    """Elenco leggibile delle edizioni: "1 Windows 11 Pro, 2 Windows 11 Home"."""
    return ", ".join(f"{im.get('index')} {_txt(im.get('name')) or _txt(im.get('display_name'))}".strip()
                     for im in (editions or []))


def edition_warning(editions, valore):
    """Avviso da mostrare quando l'edizione scelta non è dentro l'immagine, altrimenti "".

    Vuoto anche quando l'elenco delle edizioni non si conosce: senza elenco non si può dire che
    un valore sia sbagliato."""
    v = _txt(valore)
    if not v or not editions or match_edition(editions, v) is not None:
        return ""
    return (f"L'edizione \"{v}\" non è dentro questa immagine. "
            f"Edizioni disponibili: {editions_labels(editions)}.")


def edition_fallback(editions, target=""):
    """Edizione da installare quando quella chiesta dal profilo nell'immagine non c'è.

    Si sceglie la prima (indice più basso) coerente con il tipo di Windows del profilo — su una ISO
    Enterprise LTSC 2024 è l'indice 1, "Windows 11 Enterprise LTSC 2024", non la N — e se nessuna lo
    è si prende comunque la prima dell'immagine: meglio installare l'edizione sbagliata e dirlo che
    lasciare il PC fermo davanti alla pagina di scelta. Ritorna il nome dell'immagine (o il suo
    indice come testo se il nome manca), "" se l'elenco è vuoto."""
    voci = sorted([e for e in (editions or []) if isinstance(e, dict)],
                  key=lambda e: _int(e.get("index"), 99))

    def nome(e):
        return _txt(e.get("name")) or _txt(e.get("display_name")) or str(e.get("index") or 1)

    if not voci:
        return ""
    for e in voci:
        if not edition_target_warning(target, nome(e)):
            return nome(e)
    return nome(voci[0])


def edition_for_image(valore, editions, nome_profilo="", target=""):
    """Valore da scrivere in InstallFrom sapendo quali edizioni contiene l'immagine.

    Regola: se il valore corrisponde si usa così com'è; se non corrisponde si ripiega
    sull'edizione dell'immagine coerente con il tipo di Windows del profilo (edition_fallback).

    Prima Pixio, con più di un'edizione nell'immagine, tornava stringa vuota e ometteva
    InstallFrom, "perché una domanda in più è sempre meglio di un'installazione che si pianta". In
    un'installazione via PXE quella domanda È l'installazione che si pianta: per il setup un
    ImageInstall/OSImage senza InstallFrom non significa "scegli tu", significa "chiedi
    all'utente". Con la chiave di prodotto vuota abbina tutte le immagini del .wim, ne trova più di
    una, apre la pagina "Selezione immagine" e aspetta che qualcuno prema Avanti; davanti a un PC
    avviato dalla rete non c'è nessuno e l'installazione resta ferma lì per sempre (guasto del
    10 settembre 2026: 5 minuti e 47 secondi di attesa nel setupact.log di laboratorio, per sempre
    sul PC dell'utente).

    Il valore vuoto invece resta vuoto: "Chiedi durante l'installazione" è una scelta esplicita del
    tecnico, non un errore di battitura, e la GUI avvisa che su un'immagine con più edizioni ferma
    l'installazione automatica."""
    v = _txt(valore)
    if not v or not editions or match_edition(editions, v) is not None:
        return v
    chi = f"profilo \"{nome_profilo}\": " if nome_profilo else ""
    scelta = edition_fallback(editions, target)
    if len(editions) == 1:
        log.warning("%sl'edizione \"%s\" non è nell'immagine, si installa l'unica presente (\"%s\")",
                    chi, v, scelta)
        return scelta
    log.warning("%sl'edizione \"%s\" non è nell'immagine (disponibili: %s): si installa \"%s\", "
                "correggi l'edizione del profilo. Il ripiego serve solo a non lasciare il PC fermo "
                "sulla pagina \"Selezione immagine\" del programma di installazione",
                chi, v, editions_labels(editions), scelta)
    return scelta


def edition_target_warning(target, valore):
    """Avviso quando il nome dell'edizione non c'entra col tipo di Windows del profilo, altrimenti "".

    Non sostituisce il confronto con l'immagine (l'elenco vero lo dà install.wim), ma prende il
    caso più frequente anche quando la ISO non si conosce: "Windows 11 Pro" su un profilo LTSC,
    che nessuna ISO Enterprise LTSC contiene."""
    v = _txt(valore)
    t = _txt(target).lower() or "client"
    if not v or v.isdigit() or t not in TARGETS:
        return ""
    basso = v.lower()
    ltsc = "ltsc" in basso or "ltsb" in basso
    server = "server" in basso
    if t in ("10-ltsc", "11-ltsc") and not ltsc:
        return (f"Il profilo è per {TARGET_LABELS[t]}, ma \"{v}\" non è un'edizione LTSC: quelle "
                "immagini contengono solo edizioni Enterprise LTSC.")
    if t == "server" and not server:
        return f"Il profilo è per {TARGET_LABELS[t]}, ma \"{v}\" non è un'edizione di Windows Server."
    if t == "client" and (ltsc or server):
        etichetta = "LTSC" if ltsc else "di Windows Server"
        return f"Il profilo è per {TARGET_LABELS[t]}, ma \"{v}\" è un'edizione {etichetta}."
    return ""


def iso_entry(slug):
    """Voce di catalogo di una ISO ({slug, group, name, type}), oppure None se non c'è.

    Serve alla generazione del file di risposta per sapere quali cartelle driver sono abbinate
    all'immagine che sta partendo (apply_to). Senza catalogo si torna None e non si filtra nulla."""
    slug = _txt(slug)
    if not slug:
        return None
    try:
        from . import catalog
        e = (catalog.load() or {}).get("isos", {}).get(slug)
    except Exception:  # noqa: BLE001 - senza catalogo si genera come se la ISO non si conoscesse
        return None
    if not isinstance(e, dict):
        return None
    return {"slug": slug, "group": e.get("group") or "", "name": e.get("name") or slug,
            "type": e.get("type") or ""}


def editions_for_iso(slug):
    """Edizioni note di una ISO del catalogo, dalla cache. [] se la ISO non c'è o non si sa nulla."""
    slug = _txt(slug)
    if not slug:
        return []
    try:
        from . import catalog
        e = (catalog.load() or {}).get("isos", {}).get(slug)
        return catalog.editions_of(e) if isinstance(e, dict) else []
    except Exception:  # noqa: BLE001 - senza catalogo si genera come se la ISO non si conoscesse
        return []


def _profilo_della_risposta(a, profili, per_nome):
    """Profilo che ha generato la risposta `a`, oppure None, con gli indici dei profili già pronti.

    Il collegamento vero è il campo `profile` che save_as_answer() scrive nella risposta; per le
    risposte create prima si ripiega sull'identificativo (nasce dallo stesso nome del profilo) e
    sul nome uguale, che è come le genera Pixio."""
    if not isinstance(a, dict) or a.get("kind") != "windows":
        return None
    return (profili.get(_txt(a.get("profile"))) or profili.get(_txt(a.get("id")))
            or per_nome.get(_txt(a.get("name")).lower()))


def profile_for_answer(answer):
    """Profilo che ha generato una singola risposta, oppure None.

    None vuol dire "risposta che non nasce da un profilo": scritta o caricata a mano dal tecnico,
    oppure generata da un profilo cancellato nel frattempo. In tutti e due i casi l'unica cosa da
    servire al client è il file statico salvato nella risposta (docs/API.md, sezione 20)."""
    if not isinstance(answer, dict) or answer.get("kind") != "windows":
        return None
    profili = {p["id"]: p for p in list_profiles()}
    per_nome = {_txt(p["name"]).lower(): p for p in profili.values()}
    return _profilo_della_risposta(answer, profili, per_nome)


def profiles_by_answer():
    """{answer_id: {id, name, edition_index, target}} dei profili che hanno generato le risposte.

    Stesse regole di collegamento di profile_for_answer(), applicate a tutte le risposte in una
    volta sola (i profili si leggono una volta, non una per risposta)."""
    try:
        idx = _answers().index()
    except Exception:  # noqa: BLE001
        return {}
    profili = {p["id"]: p for p in list_profiles()}
    per_nome = {_txt(p["name"]).lower(): p for p in profili.values()}
    out = {}
    for aid, a in idx.items():
        p = _profilo_della_risposta(dict(a, id=a.get("id") or aid), profili, per_nome)
        if p is None:
            continue
        st = p.get("settings") or {}
        out[aid] = {"id": p["id"], "name": p["name"], "edition_index": _txt(st.get("edition_index")),
                    "target": _txt(st.get("target")) or "client"}
    return out


def iso_bindings():
    """{profile_id: [{slug, name, answer_id, editions}]}: le ISO su cui girerà ogni profilo.

    Un profilo è legato a una ISO tramite la risposta che ha generato: è quella l'immagine da cui
    leggere le edizioni per la tendina "Edizione da installare" della GUI."""
    per_risposta = profiles_by_answer()
    if not per_risposta:
        return {}
    try:
        from . import catalog
        isos = (catalog.load() or {}).get("isos") or {}
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for slug, e in isos.items():
        if not isinstance(e, dict) or (e.get("type") or "") != "windows":
            continue
        collegate = catalog.answers_of(e)
        if not collegate:
            continue
        edizioni = catalog.editions_of(e)
        for aid in collegate:
            p = per_risposta.get(aid)
            if not p:
                continue
            out.setdefault(p["id"], []).append({
                "slug": slug, "name": e.get("name") or e.get("file") or slug,
                "answer_id": aid, "editions": edizioni})
    for voci in out.values():
        voci.sort(key=lambda x: (x["name"] or "").lower())
    return out


def check_edition_for_isos(profile_id, settings):
    """Rifiuta di salvare un profilo che chiede un'edizione che le ISO abbinate non contengono.

    È il controllo che sarebbe servito il 10 settembre 2026: il profilo chiedeva "Windows 11 Pro" su
    una ISO Enterprise LTSC 2024, che quell'edizione non ha, e il difetto si è visto solo davanti al
    PC in installazione. Le ISO sono quelle a cui è collegata la risposta generata dal profilo
    (iso_bindings), quindi il controllo scatta solo quando si sa davvero cosa c'è dentro
    l'immagine; un profilo non ancora abbinato a nessuna ISO si salva come prima.

    Se il profilo gira su più ISO basta che l'edizione sia in almeno una: sulle altre l'XML la
    corregge da solo al momento dell'avvio (edition_for_image) e la GUI lo dice.
    """
    ed = _txt((settings or {}).get("edition_index"))
    if not ed or not profile_id:
        return
    try:
        legami = [i for i in (iso_bindings().get(profile_id) or []) if i.get("editions")]
    except Exception:  # noqa: BLE001 - senza catalogo il salvataggio non si blocca
        return
    if not legami or any(match_edition(i["editions"], ed) is not None for i in legami):
        return
    nomi = ", ".join(i["name"] for i in legami)
    disponibili = editions_labels(legami[0]["editions"])
    raise ValueError(
        f"L'edizione \"{ed}\" non è dentro {nomi}: il programma di installazione si fermerebbe "
        f"sulla pagina \"Selezione immagine\" ad aspettare che qualcuno scelga, e su un PC avviato "
        f"dalla rete non c'è nessuno davanti allo schermo. Scegli una delle edizioni presenti "
        f"({disponibili})")


# ---------------------------------------------------------------- catalogo delle ottimizzazioni

# Catalogo di data/windows-tweaks.json tenuto in memoria e ricaricato quando cambia il file
# (stesso schema di services/recipes.py: confronto sul mtime, un lock perché i thread di gunicorn
# possono chiedere il catalogo contemporaneamente).
_tweaks_cache = {"mtime": None, "path": None, "data": None}
_tweaks_lock = threading.Lock()


def tweaks_file():
    """Percorso del catalogo, letto da pixio.config a ogni chiamata (i test lo spostano).

    Se pixio.config non ha WINTWEAKS_FILE si sta accanto ai modelli (data/windows-tweaks.json):
    si parte da PRESETS_FILE, che è un percorso assoluto fisso, e non da CODE_DIR, che alcune
    prove spostano su una cartella temporanea.
    """
    percorso = getattr(C, "WINTWEAKS_FILE", "")
    if percorso:
        return percorso
    cartella = os.path.dirname(getattr(C, "PRESETS_FILE", "") or "")
    if not cartella:
        cartella = os.path.join(getattr(C, "CODE_DIR", "/opt/pixio"), "data")
    return os.path.join(cartella, "windows-tweaks.json")


def _load_tweaks():
    """Catalogo normalizzato: {"categories": [...], "items": [...], "index": {id: voce}}.

    Le voci malformate (senza id valido, con categoria inesistente o ripetute) vengono scartate:
    un errore di battitura nel file non deve impedire alla GUI di aprirsi.
    """
    percorso = tweaks_file()
    with _tweaks_lock:
        try:
            m = os.stat(percorso).st_mtime
        except OSError:
            m = None
        if (_tweaks_cache["data"] is None or _tweaks_cache["mtime"] != m
                or _tweaks_cache["path"] != percorso):
            d = read_json(percorso, {})
            if not isinstance(d, dict):
                d = {}
            categorie = [c for c in (d.get("categories") or [])
                         if isinstance(c, dict) and TWEAK_ID_RE.match(str(c.get("id") or ""))]
            note = {c["id"] for c in categorie}
            voci, indice = [], {}
            for t in (d.get("tweaks") or []):
                if not isinstance(t, dict):
                    continue
                tid = str(t.get("id") or "")
                if not TWEAK_ID_RE.match(tid) or tid in indice:
                    continue
                if t.get("category") not in note:
                    continue
                voci.append(t)
                indice[tid] = t
            _tweaks_cache.update({"mtime": m, "path": percorso,
                                  "data": {"categories": categorie, "items": voci, "index": indice}})
        return _tweaks_cache["data"]


def tweaks_catalog():
    """Catalogo per la GUI e per l'API: {categories: [...], items: [...]} (docs/API.md, sezione 10)."""
    d = _load_tweaks()
    return {"categories": copy.deepcopy(d["categories"]), "items": copy.deepcopy(d["items"])}


def tweak(tweak_id):
    """Una voce del catalogo (copia) oppure None se l'identificativo non esiste."""
    v = _load_tweaks()["index"].get(str(tweak_id or ""))
    return copy.deepcopy(v) if v else None


def tweak_ids():
    """Identificativi del catalogo nell'ordine in cui sono scritti nel file."""
    return [t["id"] for t in _load_tweaks()["items"]]


def tweak_platforms(voce):
    """Piattaforme dichiarate da una voce del catalogo, filtrate su PLATFORMS.

    Una voce senza "editions" (o con valori sconosciuti) resta compatibile con tutto: un errore di
    battitura nel catalogo non deve far sparire dalla GUI un'ottimizzazione già usata nei profili.
    """
    if not isinstance(voce, dict):
        return set(PLATFORMS)
    p = {str(x) for x in (voce.get("editions") or []) if str(x) in PLATFORMS}
    return p or set(PLATFORMS)


def tweak_compatibile(voce, target):
    """True se la voce ha effetto sul tipo di Windows scelto (docs/API.md, sezioni 11 e 12)."""
    return bool(tweak_platforms(voce) & set(TARGET_PLATFORMS.get(target, PLATFORMS)))


def tweak_piattaforme_testo(voce):
    """Piattaforme di una voce scritte per esteso, nell'ordine di PLATFORMS: "Windows 10, Windows 11"."""
    p = tweak_platforms(voce)
    return ", ".join(PLATFORM_LABELS[x] for x in PLATFORMS if x in p)


def tweaks_incompatibili(tweak_ids_scelti, target):
    """Identificativi (fra quelli passati) che non hanno effetto sul target indicato.

    Gli identificativi sconosciuti al catalogo vengono ignorati qui: se ne occupa validate().
    """
    indice = _load_tweaks()["index"]
    return [t for t in (tweak_ids_scelti or [])
            if t in indice and not tweak_compatibile(indice[t], target)]


def _tweaks_scelti(st):
    """Voci scelte nel profilo, sempre nell'ordine del catalogo (generazione stabile).

    Le voci non compatibili con settings.target vengono saltate senza sollevare eccezioni: la
    validazione le rifiuta al salvataggio, ma un profilo salvato prima della distinzione fra
    client, LTSC e server deve continuare a generare un XML valido (docs/API.md, sezioni 11 e 12).
    """
    indice = _load_tweaks()["index"]
    target = _txt(st.get("target")).lower() or DEFAULTS["target"]
    if target not in TARGETS:
        target = DEFAULTS["target"]
    return [indice[t] for t in (st.get("tweaks") or [])
            if t in indice and tweak_compatibile(indice[t], target)]


# ---------------------------------------------------------------- utilità

def _file():
    """Percorso del file dei profili: letto da pixio.config a ogni chiamata (i test lo spostano)."""
    return getattr(C, "WINPROFILES_FILE", os.path.join(C.VAR_DIR, "winprofiles.json"))


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _txt(v):
    """Testo su una riga sola, senza caratteri di controllo (finirebbero nell'XML come dati binari)."""
    s = "" if v is None else str(v)
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = "".join(ch for ch in s if ch >= " " and ch != "\x7f")
    return s.strip()


def _pw(v):
    """Password: si conservano gli spazi interni, si tolgono solo i caratteri di controllo."""
    s = "" if v is None else str(v)
    return "".join(ch for ch in s if ch >= " " and ch != "\x7f")


def _bool(v, default=False):
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "si", "sì", "yes", "on")
    return bool(v)


def _int(v, default=0):
    if v is None or v == "":
        return default
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        raise ValueError("Valore numerico non valido: " + _txt(v))


def _list(v):
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [x for x in re.split(r"[\r\n]+", v) if x.strip()]
    if isinstance(v, (list, tuple)):
        return [x for x in v if x is not None]
    raise ValueError("Atteso un elenco")


def slugify_id(name):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9_-]+", "-", s).strip("-_")[:64].strip("-_")
    if s and not re.match(r"^[a-z0-9]", s):
        s = "p" + s
    return s or "profilo"


def check_id(profile_id):
    if not PROFILE_ID_RE.match(str(profile_id or "")):
        raise ValueError("Identificativo del profilo non valido")
    return str(profile_id)


def _check_user_name(nome, campo):
    """Nome utente locale di Windows: max 20 caratteri, senza i simboli vietati da Windows."""
    if len(nome) > MAX_USER:
        raise ValueError(f"{campo}: massimo {MAX_USER} caratteri")
    if set(nome) & USER_BAD_CHARS:
        raise ValueError(f"{campo}: non sono ammessi i caratteri \" / \\ [ ] : ; | = , + * ? < > @")
    if nome.endswith(".") or nome != nome.strip():
        raise ValueError(f"{campo}: non può iniziare o finire con spazi né finire con un punto")
    if not nome.strip(". "):
        raise ValueError(f"{campo}: nome non valido")
    return nome


def _check_password(pw, campo):
    if len(pw) > MAX_PASSWORD:
        raise ValueError(f"{campo}: massimo {MAX_PASSWORD} caratteri (limite di Windows)")
    return pw


# ---------------------------------------------------------------- validazione

def check_language_install(valore):
    """Controlla e normalizza settings.language_install (docs/API.md, sezione 13).

    Forma attesa: `{enabled, languages, source, file_url, set_system, geo_id, keyboard}`.
    I controlli sulla forma (tag, numero di lingue, indirizzo, sorgente, GeoId) valgono anche con
    `enabled` falso: un valore sbagliato lasciato lì dentro non deve saltare fuori mesi dopo, la
    prima volta che qualcuno accende l'interruttore. Sono invece legati all'interruttore i due
    controlli di coerenza, cioè "almeno una lingua" e "con la sorgente file serve l'indirizzo".
    """
    d = valore if isinstance(valore, dict) else {}
    base = DEFAULTS["language_install"]
    attivo = _bool(d.get("enabled"), False)

    lingue = []
    for v in _list(d.get("languages")):
        tag = _txt(v)
        if not tag:
            continue
        if not LANG_TAG_RE.match(tag):
            raise ValueError(f"Lingua da installare non valida: {tag}. Serve una sigla come it-IT, "
                             "en-US o de-DE")
        if tag not in lingue:
            lingue.append(tag)
    if len(lingue) > MAX_LANG_INSTALL:
        raise ValueError(f"Troppe lingue da installare (max {MAX_LANG_INSTALL}): ogni lingua è un "
                         "pacchetto che il PC deve scaricare e installare durante il primo accesso")
    if attivo and not lingue:
        raise ValueError("Indica almeno una lingua da installare, oppure spegni l'installazione "
                         "automatica della lingua")

    sorgente = _txt(d.get("source")).lower() or base["source"]
    if sorgente not in LANG_SOURCES:
        raise ValueError("Sorgente della lingua non valida: " + ", ".join(LANG_SOURCES)
                         + ". windows-update = il PC scarica il pacchetto da Microsoft; "
                           "file = pacchetto già caricato in Pixio, indicato con un indirizzo "
                           "http/https")

    url = _txt(d.get("file_url"))
    if url:
        if len(url) > MAX_URL:
            raise ValueError(f"Indirizzo del pacchetto lingua troppo lungo (max {MAX_URL} caratteri)")
        if not LANG_URL_RE.match(url):
            raise ValueError("Indirizzo del pacchetto lingua non valido: deve cominciare con "
                             "http:// oppure https:// (è il PC in installazione a scaricarlo, non "
                             "il server), senza spazi né virgolette")
    if attivo and sorgente == "file" and not url:
        raise ValueError("Indica l'indirizzo http/https del pacchetto lingua da installare, oppure "
                         "scegli Windows Update come sorgente")

    geo = _int(d.get("geo_id"), base["geo_id"])
    if geo < 0 or geo > MAX_GEO_ID:
        raise ValueError(f"Area geografica (GeoId) non valida: da 0 a {MAX_GEO_ID}. "
                         "Italia = 118, Regno Unito = 242, Stati Uniti = 244, Germania = 94, "
                         "Francia = 84, Spagna = 217")

    kbd = _txt(d.get("keyboard"))
    if kbd and not (LANG_RE.match(kbd) or KBD_HEX_RE.match(kbd)):
        raise ValueError("Tastiera della lingua da installare non valida: attesa una sigla come "
                         "it-IT oppure un identificativo come 0410:00000410")

    return {"enabled": attivo, "languages": lingue, "source": sorgente, "file_url": url,
            "set_system": _bool(d.get("set_system"), True), "geo_id": geo, "keyboard": kbd}


def validate(settings, rifiuta_incompatibili=True):
    """Controlla e normalizza le impostazioni. Alza ValueError con un messaggio in italiano.

    I valori mancanti arrivano dai predefiniti, così un profilo parziale resta valido.

    Con `rifiuta_incompatibili` (predefinito) le ottimizzazioni che non hanno effetto sul tipo di
    Windows scelto in `target` fanno fallire la validazione, dicendo quale voce e perché. La
    generazione dell'XML chiama invece la validazione con False e si limita a saltarle
    (docs/API.md, sezioni 11 e 12): un profilo salvato prima della distinzione fra client, LTSC e
    server, o un modello cambiato sotto i piedi, deve continuare a produrre un autounattend.xml
    valido.
    """
    if settings is not None and not isinstance(settings, dict):
        raise ValueError("Impostazioni non valide: atteso un oggetto")
    s = deep_merge(copy.deepcopy(DEFAULTS), settings or {})
    out = {}

    # --- tipo di Windows (client, 10-ltsc, 11-ltsc, server): decide quali ottimizzazioni sono
    # ammesse e se le app del Microsoft Store hanno senso
    out["target"] = check_target(s.get("target"), DEFAULTS["target"])

    # --- lingua e area
    lang = _txt(s.get("language")) or DEFAULTS["language"]
    if not LANG_RE.match(lang):
        raise ValueError("Lingua non valida: attesa una sigla come it-IT o en-US")
    out["language"] = lang

    kbd = _txt(s.get("input_locale")) or lang
    parti = [p.strip() for p in kbd.split(";") if p.strip()]
    if not parti or len(parti) > 4:
        raise ValueError("Tastiera non valida: da 1 a 4 layout separati da punto e virgola")
    for p in parti:
        if not (LANG_RE.match(p) or KBD_HEX_RE.match(p)):
            raise ValueError("Tastiera non valida: attesa una sigla come it-IT "
                             "oppure un identificativo come 0410:00000410")
    out["input_locale"] = ";".join(parti)

    # lingua da installare dopo il setup: serve quando l'ISO non contiene la lingua voluta
    # (docs/API.md, sezione 13). Con l'interruttore spento resta scritta ma non genera comandi.
    out["language_install"] = check_language_install(s.get("language_install"))

    tz = _txt(s.get("timezone")) or DEFAULTS["timezone"]
    if not TIMEZONE_RE.match(tz):
        raise ValueError("Fuso orario non valido: Windows vuole un nome come "
                         "\"W. Europe Standard Time\", non la forma Europe/Rome")
    out["timezone"] = tz

    arch = _txt(s.get("architecture")).lower() or "amd64"
    if arch in ("x64", "x86_64"):
        arch = "amd64"
    if arch not in ARCHITECTURES:
        raise ValueError("Architettura non valida: " + " oppure ".join(ARCHITECTURES))
    out["architecture"] = arch

    # --- immagine e chiave
    ed = _txt(s.get("edition_index"))
    if ed:
        if ed.isdigit():
            n = int(ed)
            if n < 1 or n > 32:
                raise ValueError("Indice dell'edizione non valido: da 1 a 32")
            ed = str(n)
        elif not EDITION_NAME_RE.match(ed):
            raise ValueError("Edizione non valida: indica l'indice dell'immagine (es. 1) oppure il suo "
                             "nome esatto (es. Windows 11 Pro)")
    out["edition_index"] = ed

    key = _txt(s.get("product_key")).upper().replace(" ", "")
    if key and not PRODUCT_KEY_RE.match(key):
        raise ValueError("Chiave di prodotto non valida: formato XXXXX-XXXXX-XXXXX-XXXXX-XXXXX "
                         "oppure vuota")
    out["product_key"] = key

    # --- identità del computer
    cn = _txt(s.get("computer_name")) or "*"
    if cn != "*":
        if len(cn) > MAX_COMPUTER:
            raise ValueError(f"Nome computer troppo lungo: massimo {MAX_COMPUTER} caratteri")
        if not COMPUTER_RE.match(cn) or cn.endswith("-") or cn.rstrip("*").isdigit():
            raise ValueError("Nome computer non valido: lettere, cifre e trattino, senza trattino "
                             "iniziale o finale, non tutto numerico; usa * (anche come PC-*) "
                             "per farlo generare a caso")
    out["computer_name"] = cn

    org = _txt(s.get("organization"))
    owner = _txt(s.get("owner"))
    for v, campo in ((org, "Organizzazione"), (owner, "Intestatario")):
        if len(v) > 64:
            raise ValueError(f"{campo}: massimo 64 caratteri")
    out["organization"] = org
    out["owner"] = owner

    # --- amministratore locale
    admin = _txt(s.get("admin_user"))
    admin_pw = _pw(s.get("admin_password"))
    if admin:
        _check_user_name(admin, "Nome dell'amministratore locale")
        if not admin_pw and out.get("target") == "server":
            # Windows Server applica di serie il criterio di complessità: un account senza password
            # farebbe fallire l'installazione automatica.
            raise ValueError(f"Su Windows Server la password dell'utente {admin} è obbligatoria: "
                             "il criterio di complessità impedisce di creare account senza password")
        _check_password(admin_pw, "Password dell'amministratore")
    elif admin_pw:
        raise ValueError("C'è una password ma nessun nome utente amministratore")
    out["admin_user"] = admin
    out["admin_password"] = admin_pw if admin else ""

    autologon = _bool(s.get("autologon"), False)
    count = _int(s.get("autologon_count"), 0)
    if count < 0 or count > 99999:
        raise ValueError("Numero di accessi automatici non valido: da 0 a 99999")
    if autologon:
        if not admin:
            raise ValueError("L'accesso automatico richiede un utente amministratore locale")
        if count < 1:
            count = 9999
    else:
        count = 0
    out["autologon"] = autologon
    out["autologon_count"] = count

    # --- secondo utente
    eu = s.get("extra_user") if isinstance(s.get("extra_user"), dict) else {}
    eu_name = _txt(eu.get("name"))
    eu_pw = _pw(eu.get("password"))
    eu_group = _txt(eu.get("group")) or "Users"
    if eu_name:
        _check_user_name(eu_name, "Nome del secondo utente")
        if eu_name.lower() == admin.lower():
            raise ValueError("Il secondo utente ha lo stesso nome dell'amministratore")
        if not eu_pw and out.get("target") == "server":
            raise ValueError(f"Su Windows Server la password dell'utente {eu_name} è obbligatoria: "
                             "il criterio di complessità impedisce di creare account senza password")
        _check_password(eu_pw, "Password del secondo utente")
        if eu_group not in LOCAL_GROUPS:
            raise ValueError("Gruppo non valido: " + ", ".join(LOCAL_GROUPS))
    elif eu_pw:
        raise ValueError("C'è una password ma nessun nome per il secondo utente")
    out["extra_user"] = {"name": eu_name, "password": eu_pw if eu_name else "",
                         "group": eu_group if eu_name else "Users"}

    # --- dominio
    jd = s.get("join_domain") if isinstance(s.get("join_domain"), dict) else {}
    jd_on = _bool(jd.get("enabled"), False)
    dom = _txt(jd.get("domain")).lower()
    ou = _txt(jd.get("ou"))
    jd_user = _txt(jd.get("user"))
    jd_pw = _pw(jd.get("password"))
    if jd_on:
        if not dom:
            raise ValueError("Indica il dominio Active Directory a cui aggiungere il computer")
        if len(dom) > 253 or not DOMAIN_RE.match(dom):
            raise ValueError("Dominio non valido: es. azienda.local")
        if not jd_user:
            raise ValueError("Indica l'utente autorizzato ad aggiungere computer al dominio")
        if len(jd_user) > 104 or set(jd_user) & set('"/[]:;|=,+*?<>'):
            raise ValueError("Utente di dominio non valido: usa la forma utente oppure DOMINIO\\utente")
        if not jd_pw:
            raise ValueError("Indica la password dell'utente di dominio")
        _check_password(jd_pw, "Password dell'utente di dominio")
        if ou:
            if not OU_RE.match(ou) or "=" not in ou:
                raise ValueError("Unità organizzativa non valida: attesa una forma tipo "
                                 "OU=PC,DC=azienda,DC=local")
    else:
        dom, ou, jd_user, jd_pw = "", "", "", ""
    # Con il dominio attivo e il nome computer casuale (*) l'oggetto in AD sarà difficile da
    # riconoscere: è ammesso, la GUI suggerisce un prefisso tipo PC-*.
    out["join_domain"] = {"enabled": jd_on, "domain": dom, "ou": ou, "user": jd_user, "password": jd_pw}

    # --- disco
    dk = s.get("disk") if isinstance(s.get("disk"), dict) else {}
    mode = _txt(dk.get("mode")).lower() or "auto-uefi"
    if mode not in DISK_MODES:
        raise ValueError("Modalità del disco non valida: " + ", ".join(DISK_MODES))
    efi = _int(dk.get("efi_mb"), DEFAULTS["disk"]["efi_mb"])
    msr = _int(dk.get("msr_mb"), DEFAULTS["disk"]["msr_mb"])
    rec = _int(dk.get("recovery_mb"), DEFAULTS["disk"]["recovery_mb"])
    if mode == "auto-uefi":
        if efi < 100 or efi > 2048:
            raise ValueError("Partizione di sistema EFI non valida: da 100 a 2048 MB (consigliati 300)")
        if msr < 0 or msr > 128:
            raise ValueError("Partizione riservata Microsoft (MSR) non valida: da 0 a 128 MB "
                             "(consigliati 16)")
    if rec < 0 or rec > 8192:
        raise ValueError("Partizione di ripristino non valida: da 0 (nessuna) a 8192 MB")
    if 0 < rec < 300:
        raise ValueError("Partizione di ripristino troppo piccola: almeno 300 MB, oppure 0 per non crearla")
    # Partizionamento automatico e "non cancellare il disco" sono una combinazione impossibile: lo
    # schema che Pixio scrive (ripristino, EFI, MSR, Windows) è un disco costruito da zero, e senza
    # azzeramento il setup lo appende alla tabella che trova già lì. Sul primo disco già usato —
    # cioè il caso normale di Pixio, che reinstalla PC in servizio — la configurazione fallisce a
    # metà (0x80042565) col disco già modificato, oppure i PartitionID 1, 2 e 4 di ModifyPartition
    # e InstallTo finiscono sulle partizioni preesistenti e le formatta. Al salvataggio si rifiuta;
    # per i profili già salvati (validazione con rifiuta_incompatibili=False, che è quella della
    # generazione) si corregge, così l'autounattend.xml resta un file che può funzionare.
    wipe = _bool(dk.get("wipe"), True)
    if mode != "manuale" and not wipe:
        if rifiuta_incompatibili:
            raise ValueError(
                "Con il partizionamento automatico il disco 0 va per forza azzerato: Pixio ricrea "
                "le partizioni da zero e senza cancellazione il programma di installazione le "
                "aggiunge a quelle già presenti, fallendo a metà o formattando le partizioni "
                "sbagliate. Lascia la spunta \"Cancella il disco 0\", oppure scegli il "
                "partizionamento manuale, che lascia scegliere le partizioni dal setup")
        log.warning("profilo con partizionamento %s e cancellazione del disco disattivata: "
                    "combinazione impossibile, il disco 0 viene azzerato lo stesso", mode)
        wipe = True
    out["disk"] = {"mode": mode, "wipe": wipe,
                   "efi_mb": efi, "msr_mb": msr, "recovery_mb": rec}

    # --- opzioni di sistema
    out["skip_oobe"] = _bool(s.get("skip_oobe"), True)
    out["bypass_requirements"] = _bool(s.get("bypass_requirements"), False)
    out["disable_defender_prompt"] = _bool(s.get("disable_defender_prompt"), False)
    out["hide_files_ext"] = _bool(s.get("hide_files_ext"), True)
    out["disable_hibernate"] = _bool(s.get("disable_hibernate"), True)
    scheme = _txt(s.get("power_scheme")).lower() or "bilanciato"
    if scheme not in POWER_SCHEMES:
        raise ValueError("Schema di alimentazione non valido: " + " oppure ".join(POWER_SCHEMES))
    out["power_scheme"] = scheme

    # --- app da rimuovere (con i tipi senza Microsoft Store - server e LTSC - restano scritte ma
    # non generano comandi: vedi _pass_oobe. Non si scartano qui perché tornando a un tipo con lo
    # Store l'elenco deve riapparire)
    apps = []
    for a in _list(s.get("remove_apps")):
        a = _txt(a)
        if not a:
            continue
        if not APP_RE.match(a):
            raise ValueError(f"Nome del pacchetto non valido: {a}. Atteso il nome del pacchetto Appx "
                             "(es. Microsoft.BingNews)")
        if a not in apps:
            apps.append(a)
    if len(apps) > MAX_APPS:
        raise ValueError(f"Troppe app da rimuovere (max {MAX_APPS})")
    out["remove_apps"] = apps

    # --- comandi al primo accesso
    cmds = []
    for c in _list(s.get("run_commands")):
        c = _txt(c)
        if not c:
            continue
        if len(c) > MAX_CMD_LEN:
            raise ValueError(f"Comando troppo lungo (max {MAX_CMD_LEN} caratteri): {c[:60]}…")
        cmds.append(c)
    if len(cmds) > MAX_COMMANDS:
        raise ValueError(f"Troppi comandi al primo accesso (max {MAX_COMMANDS})")
    out["run_commands"] = cmds

    # --- ottimizzazioni scelte nel catalogo (data/windows-tweaks.json)
    posizione = {t["id"]: i for i, t in enumerate(_load_tweaks()["items"])}
    scelti = []
    for v in _list(s.get("tweaks")):
        tid = _txt(v)
        if not tid:
            continue
        if tid not in posizione:
            raise ValueError(f"Ottimizzazione sconosciuta: {tid}. Usa uno degli identificativi del "
                             "catalogo delle ottimizzazioni (data/windows-tweaks.json)")
        if tid not in scelti:
            scelti.append(tid)
    if len(scelti) > MAX_TWEAKS:
        raise ValueError(f"Troppe ottimizzazioni selezionate (max {MAX_TWEAKS})")
    # le voci che non hanno effetto sul tipo di Windows scelto vengono rifiutate, dicendo quale
    # voce e perché (docs/API.md, sezione 11): meglio un errore chiaro di un XML che non fa nulla
    indice = _load_tweaks()["index"]
    for tid in (tweaks_incompatibili(scelti, out["target"]) if rifiuta_incompatibili else []):
        voce = indice[tid]
        motivo = TARGET_MOTIVI.get(out["target"], "")
        motivo = (motivo + "; " if motivo else "") + "vale solo su " + tweak_piattaforme_testo(voce)
        raise ValueError("L'ottimizzazione \"%s\" (%s) non è compatibile con il tipo di Windows "
                         "scelto (%s): %s. Toglila dalla selezione oppure cambia il tipo di Windows."
                         % (voce.get("name", tid), tid, TARGET_LABELS[out["target"]], motivo))
    # ordine del catalogo, non quello di selezione: l'XML generato deve essere sempre lo stesso
    out["tweaks"] = sorted(scelti, key=lambda x: posizione[x])

    # --- servizi aggiunti a mano (oltre a quelli portati dalle ottimizzazioni)
    servizi, visti = [], set()
    for v in _list(s.get("services_extra")):
        if isinstance(v, dict):
            nome, avvio = _txt(v.get("name")), v.get("start", 4)
        else:
            nome, avvio = _txt(v), 4
            if ":" in nome:                     # forma comoda "DiagTrack:4" da una casella di testo
                nome, _, avvio = nome.partition(":")
                nome, avvio = nome.strip(), avvio.strip()
        if not nome:
            continue
        if not SERVICE_RE.match(nome):
            raise ValueError(f"Nome del servizio non valido: {nome}. Serve la sigla del servizio "
                             "(es. DiagTrack, WSearch), non il nome visualizzato")
        avvio = _int(avvio, 4)
        if avvio not in SERVICE_STARTS:
            raise ValueError(f"Avvio del servizio {nome} non valido: 2 = automatico, 3 = manuale, "
                             "4 = disabilitato")
        if nome.lower() in visti:
            continue
        visti.add(nome.lower())
        servizi.append({"name": nome, "start": avvio})
    if len(servizi) > MAX_SERVICES:
        raise ValueError(f"Troppi servizi (max {MAX_SERVICES})")
    out["services_extra"] = servizi

    # --- funzionalità facoltative di Windows (dism)
    def _funz(chiave, campo):
        nomi = []
        for v in _list(s.get(chiave)):
            f = _txt(v)
            if not f:
                continue
            if not FEATURE_RE.match(f):
                raise ValueError(f"{campo}: nome non valido: {f}. Serve la sigla usata da dism "
                                 "(es. NetFx3, SMB1Protocol, Microsoft-Hyper-V-All)")
            if f.lower() not in [x.lower() for x in nomi]:
                nomi.append(f)
        if len(nomi) > MAX_FEATURES:
            raise ValueError(f"{campo}: troppe funzionalità (max {MAX_FEATURES})")
        return nomi

    fe = _funz("features_enable", "Funzionalità da attivare")
    fd = _funz("features_disable", "Funzionalità da disattivare")
    doppie = {x.lower() for x in fe} & {x.lower() for x in fd}
    if doppie:
        raise ValueError("La stessa funzionalità è indicata sia da attivare sia da disattivare: "
                         + ", ".join(sorted(doppie)))
    out["features_enable"] = fe
    out["features_disable"] = fd

    out["drivers_from_pixio"] = _bool(s.get("drivers_from_pixio"), True)
    return out


# ---------------------------------------------------------------- costruzione dell'XML

def _q(tag):
    return "{%s}%s" % (NS, tag)


def _el(parent, tag, text=None, **attrs):
    """Sottoelemento nello spazio dei nomi unattend. Il testo non va mai messo a mano: ci pensa ElementTree
    a fare l'escaping di & < > (le password possono contenerli)."""
    e = ET.SubElement(parent, _q(tag))
    if attrs:
        for k, v in attrs.items():
            e.set(k, str(v))
    if text is not None:
        e.text = str(text)
    return e


def _add(parent, tag, text=None):
    """Elemento con wcm:action="add" (le liste di unattend lo vogliono su ogni voce)."""
    e = _el(parent, tag, text)
    e.set("{%s}action" % WCM, "add")
    return e


def _component(settings_el, name, arch):
    c = _el(settings_el, "component")
    c.set("name", name)
    c.set("processorArchitecture", arch)
    c.set("publicKeyToken", PUBLIC_KEY_TOKEN)
    c.set("language", "neutral")
    c.set("versionScope", "nonSxS")
    return c


def _pass(root, nome):
    s = _el(root, "settings")
    s.set("pass", nome)
    return s


def _password_el(parent, tag, valore):
    """Blocco password in chiaro (PlainText true): è l'unico modo per farlo funzionare senza
    l'offuscamento base64 di Windows, che comunque non è una protezione.
    Con password vuota l'elemento non viene scritto: l'account resta senza password."""
    if not valore:
        return None
    p = _el(parent, tag)
    _el(p, "Value", valore)
    _el(p, "PlainText", "true")
    return p


def _sync_command(parent, ordine, comando, descrizione):
    """RunSynchronousCommand del passaggio windowsPE/specialize."""
    c = _add(parent, "RunSynchronousCommand")
    _el(c, "Order", ordine)
    _el(c, "Path", comando)
    _el(c, "Description", descrizione)
    return c


def _first_logon(parent, ordine, comando, descrizione):
    c = _add(parent, "SynchronousCommand")
    _el(c, "Order", ordine)
    _el(c, "CommandLine", comando)
    _el(c, "Description", descrizione)
    _el(c, "RequiresUserInput", "false")
    return c



def _reg_type(v):
    t = _txt(v).upper() or "REG_DWORD"
    return t if t in REG_TYPES else "REG_SZ"


def _cmd_safe(v):
    """Testo da mettere dentro un comando cmd fra virgolette: le virgolette doppie chiuderebbero
    l'argomento a metà, quindi diventano apici. I valori arrivano dal catalogo, che non ne contiene."""
    return _txt(v).replace('"', "'")


def _ps(comando):
    """Comando PowerShell lanciato da cmd (come li scrive il setup in FirstLogonCommands).

    L'argomento di `-Command` sta fra virgolette doppie, che per cmd delimitano un unico argomento:
    dentro non ce ne possono essere altre, quindi le eventuali virgolette diventano apici come in
    tutti gli altri comandi generati. I valori che ci finiscono (tag di lingua e numeri) sono già
    passati dalla validazione, che non ammette né spazi né virgolette.
    """
    return 'powershell -NoProfile -ExecutionPolicy Bypass -Command "%s"' % _cmd_safe(comando)


def _reg_add(radice, percorso, nome, tipo, dato):
    """Comando `reg add` completo. Il percorso viene sempre virgolettato (contiene spazi, es.
    "Control Panel\\Desktop"), il dato pure (può essere vuoto o contenere spazi e &)."""
    chiave = _cmd_safe(radice).rstrip("\\") + "\\" + _cmd_safe(percorso).strip("\\")
    return ('cmd /c reg add "%s" /v "%s" /t %s /d "%s" /f'
            % (chiave, _cmd_safe(nome), _reg_type(tipo), _cmd_safe(dato)))


class _Emessi:
    """Memoria di ciò che è già stato generato, per non ripetere due volte lo stesso comando.

    Serve a tenere insieme i campi booleani storici del profilo (hide_files_ext, disable_hibernate,
    power_scheme, disable_defender_prompt) e le ottimizzazioni del catalogo che fanno la stessa cosa:
    vince chi arriva prima, gli altri vengono saltati.
    """

    def __init__(self):
        self.comandi = set()
        self.valori = set()

    def comando(self, cmd):
        """True se il comando non era ancora stato generato (e da questo momento lo è)."""
        k = re.sub(r"\s+", " ", str(cmd).strip().lower())
        if k.startswith("cmd /c "):
            k = k[7:]
        if not k or k in self.comandi:
            return False
        self.comandi.add(k)
        return True

    def valore(self, scope, percorso, nome):
        """True se quella voce di registro non era ancora stata scritta."""
        k = (str(scope).upper(), str(percorso).strip("\\").lower(), str(nome).lower())
        if k in self.valori:
            return False
        self.valori.add(k)
        return True


def _servizi_da_impostare(st):
    """(nome, avvio, descrizione) dei servizi: prima quelli delle ottimizzazioni (ordine del
    catalogo), poi quelli aggiunti a mano. Un servizio nominato più volte si imposta una volta sola."""
    fuori, visti = [], set()

    def agg(nome, avvio, descr):
        nome = _txt(nome)
        avvio = _int(avvio, 4)
        if not nome or not SERVICE_RE.match(nome) or avvio not in SERVICE_STARTS:
            return
        if nome.lower() in visti:
            return
        visti.add(nome.lower())
        fuori.append((nome, avvio, descr % SERVICE_START_LABELS[avvio]))

    for t in _tweaks_scelti(st):
        for sv in (t.get("services") or []):
            if isinstance(sv, dict):
                agg(sv.get("name"), sv.get("start", 4),
                    t.get("name", t["id"]) + ": servizio " + _txt(sv.get("name")) + " %s")
    for sv in (st.get("services_extra") or []):
        if isinstance(sv, dict):
            agg(sv.get("name"), sv.get("start", 4),
                "Servizio " + _txt(sv.get("name")) + " %s")
    return fuori


def _funzionalita_da_applicare(st):
    """(nome, True=attiva/False=disattiva) delle funzionalità Windows: prima le ottimizzazioni,
    poi le voci del profilo. Se un nome compare in entrambi i sensi vince la prima richiesta."""
    fuori, visti = [], set()

    def agg(nome, attiva):
        nome = _txt(nome)
        if not nome or not FEATURE_RE.match(nome) or nome.lower() in visti:
            return
        visti.add(nome.lower())
        fuori.append((nome, attiva))

    for t in _tweaks_scelti(st):
        for f in (t.get("features_enable") or []):
            agg(f, True)
        for f in (t.get("features_disable") or []):
            agg(f, False)
    for f in (st.get("features_enable") or []):
        agg(f, True)
    for f in (st.get("features_disable") or []):
        agg(f, False)
    return fuori


def _comandi_tweak_specialize(st, em):
    """(comando, descrizione) generati dalle ottimizzazioni nel passaggio specialize:
    criteri HKLM, avvio dei servizi e preferenze HKCU scritte sul profilo predefinito
    (un solo reg load / reg unload per tutte le scritture, come da contratto)."""
    righe = []

    def agg(cmd, descr):
        if em.comando(cmd):
            righe.append((cmd, descr))

    scelti = _tweaks_scelti(st)

    for t in scelti:
        for r in (t.get("reg") or []):
            if not isinstance(r, dict) or _txt(r.get("scope")).upper() != "HKLM":
                continue
            percorso, nome = _txt(r.get("path")), _txt(r.get("name"))
            if not percorso or not nome or not em.valore("HKLM", percorso, nome):
                continue
            agg(_reg_add("HKLM", percorso, nome, r.get("type"), r.get("data")),
                "%s: %s" % (t.get("name", t["id"]), nome))

    for nome, avvio, descr in _servizi_da_impostare(st):
        chiave = SERVICES_KEY + "\\" + nome
        if not em.valore("HKLM", chiave, "Start"):
            continue
        agg(_reg_add("HKLM", chiave, "Start", "REG_DWORD", avvio), descr)

    hkcu = []
    for t in scelti:
        for r in (t.get("reg") or []):
            if not isinstance(r, dict) or _txt(r.get("scope")).upper() != "HKCU":
                continue
            percorso, nome = _txt(r.get("path")), _txt(r.get("name"))
            if not percorso or not nome or not em.valore("HKCU", percorso, nome):
                continue
            hkcu.append((t, r, percorso, nome))
    if hkcu:
        agg('cmd /c reg load "%s" "%s"' % (DEFAULT_HIVE, DEFAULT_NTUSER),
            "Carica il profilo utente predefinito (vale per tutti gli utenti creati dopo)")
        for t, r, percorso, nome in hkcu:
            agg(_reg_add(DEFAULT_HIVE, percorso, nome, r.get("type"), r.get("data")),
                "%s: %s" % (t.get("name", t["id"]), nome))
        agg('cmd /c reg unload "%s"' % DEFAULT_HIVE, "Scarica il profilo utente predefinito")
    return righe


def _comandi_lingua(st):
    """(comando, descrizione) che installano la lingua di Windows al primo accesso.

    Nell'ordine giusto: prima l'installazione del pacchetto lingua (da Windows Update oppure dal
    pacchetto caricato in Pixio), poi le impostazioni di sistema, che hanno senso solo dopo. Con
    `enabled` falso, o senza nessuna lingua, non torna niente (docs/API.md, sezione 13).

    Le impostazioni di sistema si applicano alla prima lingua dell'elenco: è quella che si vuole
    davvero vedere: le altre restano installate e disponibili nella barra della lingua.
    Il cambio si vede al riavvio successivo, che il setup fa comunque.
    """
    li = st.get("language_install") if isinstance(st.get("language_install"), dict) else {}
    lingue = [x for x in (li.get("languages") or []) if x]
    if not li.get("enabled") or not lingue:
        return []
    prima = lingue[0]
    righe = []

    if li.get("source") == "file":
        # rete senza accesso a Windows Update: il pacchetto sta su Pixio e lo scarica il PC.
        # curl.exe fa parte di Windows dalla 1803 (e di Windows Server dal 2019).
        url = _cmd_safe(li.get("file_url"))
        righe.append(('cmd /c curl.exe -L -o "%s" "%s"' % (LANG_CAB, url),
                      "Scarica il pacchetto della lingua %s da Pixio" % prima))
        righe.append(('cmd /c dism /online /add-package /packagepath:"%s" /norestart' % LANG_CAB,
                      "Installa il pacchetto della lingua %s" % prima))
    else:
        # Install-Language (modulo LanguagePackManagement) si porta dietro anche i pacchetti di
        # funzionalità della lingua; -CopyToSettings la mette anche nelle schermate di accesso e
        # nei profili dei nuovi utenti
        for tag in lingue:
            righe.append((_ps("Install-Language -Language %s -CopyToSettings" % tag),
                          "Installa la lingua %s da Windows Update" % tag))

    if li.get("set_system"):
        geo = _int(li.get("geo_id"), geo_id_di(prima))
        righe.extend([
            (_ps("Set-SystemPreferredUILanguage %s" % prima),
             "Imposta %s come lingua del sistema (vale anche per i nuovi utenti)" % prima),
            (_ps("Set-WinUILanguageOverride -Language %s" % prima),
             "Imposta %s come lingua dell'interfaccia dell'utente" % prima),
            (_ps("Set-WinUserLanguageList %s -Force" % prima),
             "Mette %s in cima all'elenco delle lingue, con la sua tastiera" % prima),
            (_ps("Set-Culture %s" % prima),
             "Formati di data, ora, numeri e valuta di %s" % prima),
            (_ps("Set-WinHomeLocation -GeoId %d" % geo),
             "Area geografica del PC (GeoId %d)" % geo),
        ])
    return righe


def _partizioni_uefi(disco, st):
    """GPT: [ripristino] + EFI + [MSR] + Windows (che occupa il resto). Ritorna l'id della partizione Windows."""
    d = st["disk"]
    create = _el(disco, "CreatePartitions")
    modify = _el(disco, "ModifyPartitions")
    pid = 0
    ordine = 0
    mod_ordine = 0

    def crea(tipo, size=None, extend=False):
        nonlocal pid, ordine
        pid += 1
        ordine += 1
        p = _add(create, "CreatePartition")
        _el(p, "Order", ordine)
        _el(p, "Type", tipo)
        if extend:
            _el(p, "Extend", "true")
        else:
            _el(p, "Size", size)
        return pid

    def modifica(part_id, **campi):
        nonlocal mod_ordine
        mod_ordine += 1
        m = _add(modify, "ModifyPartition")
        _el(m, "Order", mod_ordine)
        _el(m, "PartitionID", part_id)
        for k, v in campi.items():
            _el(m, k, v)
        return m

    if d["recovery_mb"] > 0:
        # La partizione di ripristino va per prima, come nello schema consigliato da Microsoft
        rid = crea("Primary", d["recovery_mb"])
        modifica(rid, Label="Ripristino", Format="NTFS", TypeID=RECOVERY_TYPE_ID)
    eid = crea("EFI", d["efi_mb"])
    modifica(eid, Label="Sistema", Format="FAT32")
    if d["msr_mb"] > 0:
        crea("MSR", d["msr_mb"])          # la MSR non si formatta e non si modifica
    wid = crea("Primary", extend=True)
    modifica(wid, Label="Windows", Format="NTFS", Letter="C")
    return wid


def _partizioni_bios(disco):
    """MBR: partizione riservata di sistema (attiva) + Windows. Ritorna l'id della partizione Windows."""
    create = _el(disco, "CreatePartitions")
    modify = _el(disco, "ModifyPartitions")

    p1 = _add(create, "CreatePartition")
    _el(p1, "Order", 1)
    _el(p1, "Type", "Primary")
    _el(p1, "Size", BIOS_SYSTEM_MB)
    p2 = _add(create, "CreatePartition")
    _el(p2, "Order", 2)
    _el(p2, "Type", "Primary")
    _el(p2, "Extend", "true")

    m1 = _add(modify, "ModifyPartition")
    _el(m1, "Order", 1)
    _el(m1, "PartitionID", 1)
    _el(m1, "Label", "Riservato di sistema")
    _el(m1, "Format", "NTFS")
    _el(m1, "Active", "true")
    m2 = _add(modify, "ModifyPartition")
    _el(m2, "Order", 2)
    _el(m2, "PartitionID", 2)
    _el(m2, "Label", "Windows")
    _el(m2, "Format", "NTFS")
    _el(m2, "Letter", "C")
    return 2


def risolvi_nome_computer(nome):
    """Nome computer pronto per l'unattend.

    Windows accetta l'asterisco solo da solo ("*" = nome casuale di 15 caratteri): un prefisso
    come "PC-*" e' un nome illegale, e il setup si ferma con "installazione non riuscita" al primo
    avvio del sistema installato, cioe' dopo che l'immagine e' gia' stata copiata. Pixio ha sempre
    suggerito quella forma, quindi il prefisso lo risolviamo noi qui: l'asterisco diventa una parte
    casuale vera e il nome finale sta nei 15 caratteri di NetBIOS."""
    nome = (nome or "").strip()
    if "*" not in nome:
        return nome
    if nome == "*":
        return nome                      # nome interamente casuale: lo fa Windows
    prefisso = nome.replace("*", "")
    casuale = secrets.token_hex(3).upper()          # sei caratteri: collisioni improbabili
    prefisso = prefisso[:MAX_COMPUTER - len(casuale)]
    return (prefisso + casuale)[:MAX_COMPUTER]


def _pass_windows_pe(root, st, arch, server_ip="", cfg=None, iso=None):
    """windowsPE: lingua del setup, percorsi driver, LabConfig, disco, immagine, chiave di prodotto.

    `iso` è la voce di catalogo che si sta avviando: serve a scegliere i percorsi driver abbinati a
    quell'immagine (apply_to, docs/API.md, sezioni 15 e 24)."""
    sp = _pass(root, "windowsPE")

    intl = _component(sp, "Microsoft-Windows-International-Core-WinPE", arch)
    ui = _el(intl, "SetupUILanguage")
    _el(ui, "UILanguage", st["language"])
    _el(intl, "InputLocale", st["input_locale"])
    _el(intl, "SystemLocale", st["language"])
    _el(intl, "UILanguage", st["language"])
    _el(intl, "UserLocale", st["language"])

    # I driver vanno chiesti qui, non in specialize: PnpCustomizationsNonWinPE vale solo nei passaggi
    # auditSystem e offlineServicing, e soprattutto in specialize la rete del sistema appena installato
    # puo' non essere ancora pronta, mentre in Windows PE la condivisione l'abbiamo appena montata noi.
    if st["drivers_from_pixio"]:
        d = _driver_paths(server_ip, cfg, iso)
        if not d["voci"]:
            # Nessun componente e nessun DriverPaths vuoto: un percorso con dentro un pacchetto incompleto
            # fermerebbe l'installazione con 0x80070002 prima ancora di toccare il disco, e un componente
            # senza figli alcune versioni del setup lo segnalano come errore di schema. Senza il componente
            # l'installazione va avanti con i driver che Windows ha dentro.
            sp.append(_commento(
                "Nessun percorso driver scritto: delle %d cartelle esaminate nella libreria di Pixio "
                "nessuna e' percorribile senza incontrare un pacchetto incompleto. %s "
                "Il programma di installazione mette in staging ogni .inf che trova nel percorso che "
                "riceve e si ferma con 0x80070002 (file non trovato) al primo pacchetto incompleto, "
                "quindi Pixio preferisce non passargli niente: l'installazione prosegue con i driver "
                "che Windows ha dentro. Sistema le cartelle nella pagina Driver di Pixio."
                % (d["cartelle"], _elenco_scartate(d["scartate"]))))
            log.warning("driver: nessun percorso scritto nel file di risposta (%d cartelle esaminate)%s",
                        d["cartelle"], (": " + _elenco_scartate(d["scartate"], 12)) if d["scartate"] else "")
        else:
            pnp = _component(sp, "Microsoft-Windows-PnpCustomizationsWinPE", arch)
            if not d["attivo"]:
                pnp.append(ET.Comment(" Per usare queste cartelle serve l'opzione \"Installazione Windows "
                                      "via rete\" attiva nelle impostazioni di Pixio (share SMB di sola "
                                      "lettura), altrimenti il setup ignora i percorsi "))
            pnp.append(_commento(
                "Percorsi driver scelti da Pixio per questa immagine: %d %s da %d %s della libreria. "
                "Vengono elencate solo le cartelle in cui ogni .inf ha nel suo pacchetto i file che "
                "dichiara: il setup percorre ogni percorso con le sue sottocartelle e si ferma con "
                "0x80070002 (file non trovato) al primo pacchetto incompleto che incontra."
                % (len(d["voci"]), "percorso" if len(d["voci"]) == 1 else "percorsi",
                   d["offerte"], "cartella" if d["offerte"] == 1 else "cartelle")))
            if d["scartate"]:
                pnp.append(_commento("Non offerte al programma di installazione: "
                                     + _elenco_scartate(d["scartate"])))
            if d["fuori"]:
                pnp.append(_commento(
                    "Altri %d percorsi non sono stati scritti (tetto di %d voci o percorso piu' lungo di "
                    "%d caratteri): vedi la pagina Driver di Pixio"
                    % (len(d["fuori"]), d["tetto"], d["max_unc"])))
            dp = _el(pnp, "DriverPaths")
            # wcm:keyValue deve essere diverso per ogni voce: con piu' percorsi un valore fisso "1"
            # produrrebbe un XML che il setup rifiuta
            for i, percorso in enumerate(d["voci"], 1):
                pc = _add(dp, "PathAndCredentials")
                pc.set("{%s}keyValue" % WCM, str(i))
                _el(pc, "Path", percorso)
                cred = _el(pc, "Credentials")
                _el(cred, "Domain", d["domain"])
                _el(cred, "Username", d["user"])
                _el(cred, "Password", d["password"])

    setup = _component(sp, "Microsoft-Windows-Setup", arch)

    # Voci di registro lette dal setup: aggirano i requisiti di Windows 11 e la richiesta di rete/account
    # Microsoft. NON è una configurazione supportata da Microsoft: l'avviso è anche nella GUI.
    righe = []
    if st["bypass_requirements"]:
        setup.append(ET.Comment(" Requisiti di Windows 11 aggirati (LabConfig): installazione NON "
                                "supportata da Microsoft, niente garanzia sugli aggiornamenti "))
        for chiave in ("BypassTPMCheck", "BypassSecureBootCheck", "BypassRAMCheck", "BypassCPUCheck",
                       "BypassStorageCheck"):
            righe.append((f'cmd /c reg add "HKLM\\SYSTEM\\Setup\\LabConfig" /v {chiave} '
                          f'/t REG_DWORD /d 1 /f',
                          f"Aggira il controllo {chiave}"))
        righe.append(('cmd /c reg add "HKLM\\SYSTEM\\Setup\\MoSetup" /v '
                      'AllowUpgradesWithUnsupportedTPMOrCPU /t REG_DWORD /d 1 /f',
                      "Consente l'aggiornamento su hardware non supportato"))
    # BypassNRO non si scrive qui: questo passaggio gira dentro Windows PE, dove HKLM\SOFTWARE è
    # l'alveare del disco RAM e sparisce al riavvio. La chiave la legge l'OOBE del sistema
    # installato, quindi va scritta in specialize (_pass_specialize), come fa da sempre il modello
    # a mano di services/answers.py. Le LabConfig qui sopra invece restano: quelle le legge il
    # programma di installazione, che gira proprio dentro il WinPE.
    if righe:
        rs = _el(setup, "RunSynchronous")
        for i, (cmd, descr) in enumerate(righe, 1):
            _sync_command(rs, i, cmd, descr)

    part_windows = None
    if st["disk"]["mode"] != "manuale":
        dc = _el(setup, "DiskConfiguration")
        _el(dc, "WillShowUI", "OnError")
        dc.append(ET.Comment(" Il disco 0 viene azzerato prima di ricreare le partizioni: lo schema "
                             "qui sotto (numeri di partizione compresi) vale solo su un disco "
                             "vuoto. Per non toccare le partizioni esistenti serve il "
                             "partizionamento manuale, che non genera questa sezione "))
        disco = _add(dc, "Disk")
        _el(disco, "DiskID", 0)
        # Sempre true nelle modalità automatiche: la validazione rifiuta la combinazione al
        # salvataggio e la corregge per i profili già salvati, qui si scrive il valore giusto e
        # basta. Senza azzeramento il setup lavora sulla tabella che trova ("disk 0 already has 1
        # allocated partitions") e i PartitionID che seguono non sono più quelli creati qui.
        _el(disco, "WillWipeDisk", "true")
        if st["disk"]["mode"] == "auto-uefi":
            part_windows = _partizioni_uefi(disco, st)
        else:
            part_windows = _partizioni_bios(disco)

    if st["edition_index"] or part_windows is not None:
        ii = _el(setup, "ImageInstall")
        osi = _el(ii, "OSImage")
        if st["edition_index"]:
            inf = _el(osi, "InstallFrom")
            md = _add(inf, "MetaData")
            if st["edition_index"].isdigit():
                _el(md, "Key", "/IMAGE/INDEX")
            else:
                _el(md, "Key", "/IMAGE/NAME")
            _el(md, "Value", st["edition_index"])
        if part_windows is not None:
            it = _el(osi, "InstallTo")
            _el(it, "DiskID", 0)
            _el(it, "PartitionID", part_windows)
        _el(osi, "WillShowUI", "OnError")

    ud = _el(setup, "UserData")
    pk = _el(ud, "ProductKey")
    if st["product_key"]:
        _el(pk, "Key", st["product_key"])
    else:
        # Chiave vuota: il setup non chiede nulla e attiva più tardi (o usa la chiave del firmware)
        _el(pk, "Key", "")
    _el(pk, "WillShowUI", "OnError")
    _el(ud, "AcceptEula", "true")
    if st["owner"]:
        _el(ud, "FullName", st["owner"])
    if st["organization"]:
        _el(ud, "Organization", st["organization"])
    return sp


def _driver_paths(server_ip, cfg, iso=None):
    """Percorsi UNC da scrivere in DriverPaths e credenziali per raggiungerli (docs/API.md, sezione 24).

    La share di sola lettura \\\\<ip>\\pxe (cartella drivers) è quella che WinPE monta già per il setup:
    è protetta da utente e password, quindi servono le credenziali dentro l'XML. Se l'esportazione SMB
    per Windows non è attiva i percorsi vengono scritti lo stesso, con un commento che spiega cosa fare.

    Non si scrive più la radice della libreria: il setup percorre il percorso che riceve con tutte le sue
    sottocartelle e al primo pacchetto incompleto si ferma con 0x80070002, abortendo l'installazione
    intera. Si elencano quindi soltanto le cartelle percorribili per intero, filtrate per l'immagine che
    si sta avviando (`iso`: voce di catalogo con "slug" e "group"; None = nessun filtro).
    Ritorna {"voci": [percorsi UNC], "scartate": [...], "fuori": [...], "cartelle": n, "offerte": n,
    "user", "password", "domain", "attivo"}.
    """
    ip = _txt(server_ip) or "127.0.0.1"
    win = (cfg or {}).get("windows", {}) if isinstance(cfg, dict) else {}
    utente = _txt(win.get("smb_user")) or "pxe"
    password = _pw(win.get("smb_password"))
    attivo = bool(win.get("smb_export_enabled"))
    scelta = {"paths": [], "skipped": [], "dropped": [], "folders": 0, "offered": 0}
    tetto, max_unc = 64, 255
    try:
        from . import drivers
        tetto, max_unc = drivers.MAX_SETUP_PATHS, drivers.MAX_SETUP_UNC
        scelta = drivers.setup_paths(ip, iso)
    except Exception as e:  # noqa: BLE001 - una libreria illeggibile non deve lasciare il PC senza file
        log.error("percorsi driver non calcolabili (%s): nessun DriverPaths nel file di risposta", e)
    return {"voci": [v["unc"] for v in scelta["paths"]], "scartate": scelta["skipped"],
            "fuori": scelta["dropped"], "cartelle": scelta["folders"], "offerte": scelta["offered"],
            "tetto": tetto, "max_unc": max_unc,
            "user": utente, "password": password, "domain": ip, "attivo": attivo}


def _commento(testo):
    """Commento XML sicuro: "--" dentro un commento è vietato dallo standard e farebbe fallire la rilettura."""
    t = " ".join(str(testo or "").split()).replace("--", "-")
    return ET.Comment(" " + t.strip(" -") + " ")


def _elenco_scartate(scartate, quante=6):
    """Riga leggibile delle cartelle lasciate fuori, per il commento nell'XML e per i log."""
    voci = []
    for x in scartate:
        if x.get("reason") == "incompleto" and x.get("inf"):
            voci.append("%s\\%s (manca %s)" % (x.get("folder", ""), x["inf"].replace("/", "\\"),
                                                ", ".join(x.get("missing") or []) or "un file dichiarato"))
        elif x.get("reason") == "mai":
            voci.append("%s (esclusa a mano dal setup)" % x.get("folder", ""))
        elif x.get("reason") == "nome non valido":
            voci.append("%s (nome cartella non valido)" % x.get("folder", ""))
        elif x.get("reason") == "percorso troppo lungo":
            voci.append("%s\\%s (percorso troppo lungo)" % (x.get("folder", ""),
                                                             str(x.get("dir") or "").replace("/", "\\")))
        elif x.get("reason") == "troppo grande":
            voci.append("%s (cartella troppo grande da esaminare)" % x.get("folder", ""))
    resto = len(voci) - quante
    voci = voci[:quante]
    if resto == 1:
        voci.append("e un'altra")
    elif resto > 1:
        voci.append("e altre %d" % resto)
    return "; ".join(voci)


def _pass_specialize(root, st, arch, server_ip, cfg, em=None):
    """specialize: nome computer, fuso orario, dominio, driver, tweak di sistema."""
    em = em or _Emessi()
    sp = _pass(root, "specialize")

    shell = _component(sp, "Microsoft-Windows-Shell-Setup", arch)
    _el(shell, "ComputerName", risolvi_nome_computer(st["computer_name"]))
    _el(shell, "TimeZone", st["timezone"])
    if st["owner"]:
        _el(shell, "RegisteredOwner", st["owner"])
    if st["organization"]:
        _el(shell, "RegisteredOrganization", st["organization"])

    if st["join_domain"]["enabled"]:
        jd = st["join_domain"]
        uj = _component(sp, "Microsoft-Windows-UnattendedJoin", arch)
        ident = _el(uj, "Identification")
        cred = _el(ident, "Credentials")
        # L'utente può essere "utente" (con Domain a parte) oppure "DOMINIO\\utente"
        if "\\" in jd["user"]:
            dominio_cred, nome = jd["user"].split("\\", 1)
        else:
            dominio_cred, nome = jd["domain"], jd["user"]
        _el(cred, "Domain", dominio_cred)
        _el(cred, "Password", jd["password"])
        _el(cred, "Username", nome)
        _el(ident, "JoinDomain", jd["domain"])
        if jd["ou"]:
            _el(ident, "MachineObjectOU", jd["ou"])

    # Tweak di sistema: comandi eseguiti una volta sola, con i diritti di sistema.
    # Prima i campi storici del profilo, poi le ottimizzazioni del catalogo: se una ottimizzazione
    # rifà la stessa cosa di un campo booleano il comando viene generato una volta sola.
    tweaks = []

    def agg(cmd, descr):
        if em.comando(cmd):
            tweaks.append((cmd, descr))

    if st["skip_oobe"]:
        # Va scritto qui e non nel passaggio windowsPE: in Windows PE HKLM\SOFTWARE è l'alveare del
        # disco RAM, sparisce al riavvio e l'OOBE del sistema installato non vede niente. specialize
        # gira invece dentro il Windows appena applicato, prima dell'OOBE.
        oobe_key = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\OOBE"
        if em.valore("HKLM", oobe_key, "BypassNRO"):
            agg(_reg_add("HKLM", oobe_key, "BypassNRO", "REG_DWORD", 1),
                "Consente di completare l'OOBE senza rete e senza account Microsoft")
    if st["disable_hibernate"]:
        agg("cmd /c powercfg /hibernate off", "Disattiva l'ibernazione e libera hiberfil.sys")
    agg("cmd /c powercfg /setactive " + POWER_GUIDS[st["power_scheme"]],
        "Schema di alimentazione: " + st["power_scheme"])
    if st["disable_defender_prompt"]:
        spynet = "SOFTWARE\\Policies\\Microsoft\\Windows Defender\\Spynet"
        for nome, dato, descr in (
                ("SpynetReporting", 0, "Niente invio automatico di dati a Microsoft Defender"),
                ("SubmitSamplesConsent", 2,
                 "Non chiede l'invio di campioni sospetti (l'antivirus resta attivo)")):
            if em.valore("HKLM", spynet, nome):
                agg(_reg_add("HKLM", spynet, nome, "REG_DWORD", dato), descr)
    tweaks.extend(_comandi_tweak_specialize(st, em))
    if tweaks:
        dep = _component(sp, "Microsoft-Windows-Deployment", arch)
        rs = _el(dep, "RunSynchronous")
        for i, (cmd, descr) in enumerate(tweaks, 1):
            _sync_command(rs, i, cmd, descr[:250])
    return sp


def _pass_oobe(root, st, arch, em=None):
    """oobeSystem: schermate saltate, utenti locali, accesso automatico, comandi al primo accesso."""
    em = em or _Emessi()
    sp = _pass(root, "oobeSystem")

    intl = _component(sp, "Microsoft-Windows-International-Core", arch)
    _el(intl, "InputLocale", st["input_locale"])
    _el(intl, "SystemLocale", st["language"])
    _el(intl, "UILanguage", st["language"])
    _el(intl, "UserLocale", st["language"])

    shell = _component(sp, "Microsoft-Windows-Shell-Setup", arch)
    _el(shell, "TimeZone", st["timezone"])
    if st["owner"]:
        _el(shell, "RegisteredOwner", st["owner"])
    if st["organization"]:
        _el(shell, "RegisteredOrganization", st["organization"])

    if st["skip_oobe"]:
        oobe = _el(shell, "OOBE")
        _el(oobe, "HideEULAPage", "true")
        _el(oobe, "HideLocalAccountScreen", "true")
        _el(oobe, "HideOEMRegistrationScreen", "true")
        _el(oobe, "HideOnlineAccountScreens", "true")
        _el(oobe, "HideWirelessSetupInOOBE", "true")
        _el(oobe, "NetworkLocation", "Work")
        _el(oobe, "ProtectYourPC", "3")

    admin = st["admin_user"]
    eu = st["extra_user"]
    if admin or eu["name"]:
        ua = _el(shell, "UserAccounts")
        locali = None
        if admin:
            if admin.lower() == "administrator":
                # L'account predefinito non si può ricreare: si imposta solo la sua password
                ua.append(ET.Comment(" Account predefinito Administrator: viene impostata la password, "
                                     "l'utente non viene ricreato "))
                _password_el(ua, "AdministratorPassword", st["admin_password"])
            else:
                locali = _el(ua, "LocalAccounts")
                acc = _add(locali, "LocalAccount")
                _password_el(acc, "Password", st["admin_password"])
                _el(acc, "Name", admin)
                _el(acc, "DisplayName", admin)
                _el(acc, "Group", "Administrators")
                _el(acc, "Description", "Amministratore locale creato da Pixio")
        if eu["name"]:
            if locali is None:
                locali = _el(ua, "LocalAccounts")
            acc = _add(locali, "LocalAccount")
            _password_el(acc, "Password", eu["password"])
            _el(acc, "Name", eu["name"])
            _el(acc, "DisplayName", eu["name"])
            _el(acc, "Group", eu["group"])
            _el(acc, "Description", "Utente creato da Pixio")

    if st["autologon"] and admin:
        al = _el(shell, "AutoLogon")
        _password_el(al, "Password", st["admin_password"])
        _el(al, "Enabled", "true")
        _el(al, "LogonCount", st["autologon_count"])
        _el(al, "Username", admin)

    # Comandi al primo accesso: preferenze dell'utente, rimozione delle app, comandi delle
    # ottimizzazioni, funzionalità facoltative (dism) e infine i comandi scritti dal tecnico.
    comandi = []

    def agg(cmd, descr):
        if em.comando(cmd):
            comandi.append((cmd, descr))

    # Prima di tutto la lingua (docs/API.md, sezione 13): se l'ISO è in inglese e il PC deve
    # parlare italiano, il pacchetto va installato prima che comincino le ottimizzazioni e i
    # comandi del tecnico. Il cambio si vede al riavvio successivo.
    lingua = _comandi_lingua(st)
    if lingua:
        li = st["language_install"]
        shell.append(ET.Comment(
            " Lingua installata al primo accesso: %s (%s). Serve quando l'immagine di Windows non "
            "contiene la lingua voluta; il cambio si vede dopo il riavvio successivo. "
            % (", ".join(li["languages"]),
               "da Windows Update, il PC deve raggiungere internet"
               if li["source"] == "windows-update" else "dal pacchetto caricato in Pixio")))
        for cmd, descr in lingua:
            agg(cmd, descr)

    avanzate = "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced"
    # se un'ottimizzazione ha già scritto HideFileExt sul profilo predefinito non lo si rifà
    if em.valore("HKCU", avanzate, "HideFileExt"):
        agg(_reg_add("HKCU", avanzate, "HideFileExt", "REG_DWORD",
                     1 if st["hide_files_ext"] else 0),
            "Estensioni dei file " + ("nascoste" if st["hide_files_ext"] else "visibili"))
    # Le app Appx da rimuovere non hanno senso dove non c'è il Microsoft Store: Windows Server e le
    # edizioni Enterprise LTSC non hanno né lo Store né le app preinstallate dei client. Restano
    # scritte nel profilo (così tornando a un tipo con lo Store si ritrovano) ma non generano
    # nessun comando: il contratto (docs/API.md, sezioni 11 e 12) non prevede avvisi restituiti
    # dalla validazione, quindi la cosa si dice qui e nell'XML.
    con_store = target_ha_store(st["target"])
    apps_da_rimuovere = st["remove_apps"] if con_store else []
    if not con_store and st["remove_apps"]:
        shell.append(ET.Comment(" Le app da rimuovere sono state ignorate: il profilo è per %s, "
                                "dove il Microsoft Store e le sue app non sono installati "
                                % TARGET_LABELS.get(st["target"], st["target"])))
    for app in apps_da_rimuovere:
        agg("powershell -NoProfile -ExecutionPolicy Bypass -Command "
            f"\"Get-AppxPackage -AllUsers -Name '{app}' | Remove-AppxPackage -ErrorAction "
            "SilentlyContinue; Get-AppxProvisionedPackage -Online | "
            f"Where-Object DisplayName -eq '{app}' | Remove-AppxProvisionedPackage -Online "
            "-ErrorAction SilentlyContinue\"",
            "Rimuove l'app " + app)
    for t in _tweaks_scelti(st):
        for c in (t.get("commands") or []):
            c = _txt(c)
            if c:
                agg(c, t.get("name", t["id"]))
    for nome, attiva in _funzionalita_da_applicare(st):
        if attiva:
            agg("cmd /c dism /online /enable-feature /featurename:%s /all /norestart /quiet" % nome,
                "Attiva la funzionalità " + nome)
        else:
            agg("cmd /c dism /online /disable-feature /featurename:%s /norestart /quiet" % nome,
                "Disattiva la funzionalità " + nome)
    for c in st["run_commands"]:
        agg(c, "Comando del profilo")
    if comandi:
        flc = _el(shell, "FirstLogonCommands")
        for i, (cmd, descr) in enumerate(comandi, 1):
            _first_logon(flc, i, cmd, descr[:250])
    return sp


def render_autounattend(profile, server_ip="", cfg=None, editions=None, iso=None):
    """Genera l'autounattend.xml del profilo. Ritorna una stringa indentata e verificata.

    `profile` è il profilo completo ({name, settings}) oppure le sole impostazioni.
    `server_ip` serve per il percorso della share driver di Pixio.
    `editions` sono le edizioni dell'immagine per cui si sta generando ([{index, name}], di norma
    da catalog.editions_of): quando si sa su quale ISO andrà il file, un'edizione che lì non esiste
    non viene scritta (docs/API.md, sezione 19). Senza `editions` non cambia niente rispetto a prima.
    `iso` è la voce di catalogo che si sta avviando ({slug, group}, di norma da iso_entry(slug)): con
    quella si scrivono soltanto i percorsi driver abbinati a quell'immagine e percorribili dal
    programma di installazione (docs/API.md, sezione 24). Senza `iso` non si filtra per immagine.
    """
    # la generazione non fa fallire niente per colpa delle ottimizzazioni non compatibili con il
    # tipo di Windows del profilo: le salta e basta (docs/API.md, sezione 11)
    if isinstance(profile, dict) and "settings" in profile:
        nome = _txt(profile.get("name")) or "senza nome"
        st = validate(profile.get("settings") or {}, rifiuta_incompatibili=False)
    else:
        nome = "senza nome"
        st = validate(profile or {}, rifiuta_incompatibili=False)
    if cfg is None:
        try:
            from .. import settings as S
            cfg = S.load()
        except Exception:  # noqa: BLE001 - senza configurazione si usano i valori neutri
            cfg = {}
    if not server_ip:
        server_ip = ((cfg.get("network") or {}).get("server_ip") if isinstance(cfg, dict) else "") or ""
    if editions:
        st["edition_index"] = edition_for_image(st["edition_index"], editions, nome, st["target"])
    arch = st["architecture"]

    ET.register_namespace("", NS)
    ET.register_namespace("wcm", WCM)
    root = ET.Element(_q("unattend"))
    root.append(ET.Comment(f" autounattend.xml generato da Pixio dal profilo \"{nome}\" il {_now()}. "
                           "Le password sono in chiaro: chiunque legga questo file le vede. "))
    # una sola memoria dei comandi generati per tutti i passaggi: così i campi booleani storici
    # e le ottimizzazioni equivalenti non producono due volte la stessa riga
    em = _Emessi()
    _pass_windows_pe(root, st, arch, server_ip, cfg, iso)
    _pass_specialize(root, st, arch, server_ip, cfg, em)
    _pass_oobe(root, st, arch, em)

    grezzo = ET.tostring(root, encoding="utf-8")
    # minidom serve sia a verificare l'XML (parseString solleva se è malformato) sia a indentarlo
    dom = minidom.parseString(grezzo)
    testo = dom.toprettyxml(indent="    ", encoding="utf-8").decode("utf-8")
    # minidom lascia righe vuote quando un elemento ha solo figli: si tolgono per leggibilità
    righe = [r for r in testo.split("\n") if r.strip()]
    return "\n".join(righe) + "\n"


# ---------------------------------------------------------------- CRUD

def _all():
    d = read_json(_file(), {})
    if not isinstance(d, dict):
        d = {}
    p = d.get("profiles")
    return p if isinstance(p, dict) else {}


def _shape(profile_id, m):
    return {
        "id": profile_id,
        "name": m.get("name", profile_id),
        "note": m.get("note", ""),
        "updated": m.get("updated", ""),
        "created": m.get("created", ""),
        "settings": deep_merge(copy.deepcopy(DEFAULTS), m.get("settings") or {}),
    }


def list_profiles():
    d = _all()
    return [_shape(k, d[k]) for k in sorted(d, key=lambda x: str(d[x].get("name", x)).lower())]


def get(profile_id):
    d = _all()
    pid = str(profile_id or "")
    if pid not in d:
        return None
    return _shape(pid, d[pid])


def load_preset(preset_id):
    """Impostazioni di un modello windows da data/profile-presets.json. None se non esiste."""
    path = getattr(C, "PRESETS_FILE", os.path.join(getattr(C, "CODE_DIR", "/opt/pixio"),
                                                   "data", "profile-presets.json"))
    d = read_json(path, {})
    for p in (d.get("presets") or []):
        if isinstance(p, dict) and p.get("id") == preset_id and p.get("kind") == "windows":
            return p
    return None


def create(data):
    """Crea un profilo. data: {name, note?, preset?, settings?}."""
    data = data if isinstance(data, dict) else {}
    name = _txt(data.get("name"))
    if not name or len(name) > MAX_NAME:
        raise ValueError(f"Indica un nome per il profilo (max {MAX_NAME} caratteri)")
    note = _txt(data.get("note"))[:MAX_NOTE]
    base = {}
    preset_id = _txt(data.get("preset"))
    if preset_id:
        p = load_preset(preset_id)
        if not p:
            raise ValueError(f"Modello non trovato: {preset_id}")
        base = p.get("settings") or {}
    settings = validate(deep_merge(base, data.get("settings") or {}))

    esistenti = _all()
    if len(esistenti) >= MAX_PROFILES:
        raise ValueError(f"Troppi profili (max {MAX_PROFILES})")
    base_id = slugify_id(name)
    pid, n = base_id, 2
    while pid in esistenti:
        pid = f"{base_id}-{n}"[:64].strip("-_")
        n += 1
    check_id(pid)
    now = _now()

    def upd(d):
        d.setdefault("profiles", {})[pid] = {
            "name": name, "note": note, "settings": settings, "created": now, "updated": now,
        }
        return d
    update_json(_file(), upd, default={})
    return get(pid)


def update(profile_id, data):
    """Aggiorna nome, nota e/o impostazioni. Le impostazioni inviate sostituiscono campo per campo."""
    pid = check_id(profile_id)
    cur = get(pid)
    if not cur:
        raise FileNotFoundError("Profilo non trovato")
    data = data if isinstance(data, dict) else {}
    patch = {}
    if "name" in data:
        name = _txt(data.get("name"))
        if not name or len(name) > MAX_NAME:
            raise ValueError(f"Nome non valido (max {MAX_NAME} caratteri)")
        patch["name"] = name
    if "note" in data:
        patch["note"] = _txt(data.get("note"))[:MAX_NOTE]
    if "settings" in data:
        patch["settings"] = validate(deep_merge(cur["settings"], data.get("settings") or {}))
        check_edition_for_isos(pid, patch["settings"])
    if not patch:
        return cur
    patch["updated"] = _now()

    def upd(d):
        m = d.setdefault("profiles", {}).setdefault(pid, {})
        m.update(patch)
        return d
    update_json(_file(), upd, default={})
    return get(pid)


def duplicate(profile_id, name=None):
    """Copia un profilo con un nuovo nome."""
    cur = get(check_id(profile_id))
    if not cur:
        raise FileNotFoundError("Profilo non trovato")
    nuovo = _txt(name) or f"{cur['name']} (copia)"
    return create({"name": nuovo[:MAX_NAME], "note": cur["note"], "settings": cur["settings"]})


def delete(profile_id):
    pid = check_id(profile_id)
    if not get(pid):
        raise FileNotFoundError("Profilo non trovato")

    def upd(d):
        d.setdefault("profiles", {}).pop(pid, None)
        return d
    update_json(_file(), upd, default={})
    return True


# ---------------------------------------------------------------- salvataggio come risposta

def _answers():
    """Importa services/answers.py in modo pigro: potrebbe non esserci."""
    try:
        from . import answers
    except ImportError:
        raise RuntimeError("Il servizio delle risposte non è disponibile")
    return answers


def save_as_answer(profile_id, answer_id=None, server_ip="", iso_slug=""):
    """Genera l'autounattend.xml e lo salva come risposta di tipo windows.

    Con `answer_id` aggiorna una risposta esistente, altrimenti ne crea una nuova.
    Con `iso_slug` il file viene generato per quella ISO: l'edizione da installare viene
    confrontata con quelle che l'immagine contiene davvero (docs/API.md, sezione 19) e i percorsi
    driver sono quelli abbinati a quell'immagine (sezione 24). Il file salvato resta comunque una
    fotografia: quello che il PC riceve davvero lo rigenera /boot/answer/<slug>/<id>/autounattend.xml.
    Ritorna `{ok, answer_id, answer_name}`.
    """
    answers = _answers()
    prof = get(check_id(profile_id))
    if not prof:
        raise FileNotFoundError("Profilo non trovato")
    xml = render_autounattend(prof, server_ip, editions=editions_for_iso(iso_slug),
                              iso=iso_entry(iso_slug))
    # `profile` lega la risposta al profilo che l'ha generata: serve agli avvisi sull'edizione e
    # alla tendina della GUI, che così sa da quale ISO leggere le edizioni
    if answer_id:
        a = answers.update(answer_id, {"content": xml, "filename": "autounattend.xml",
                                       "profile": prof["id"]})
    else:
        a = answers.create({
            "name": prof["name"][:MAX_NAME],
            "kind": "windows",
            "note": ("Generata dal profilo Windows " + prof["name"])[:MAX_NOTE],
            "content": xml,
            "filename": "autounattend.xml",
            "profile": prof["id"],
        })
    return {"ok": True, "answer_id": a["id"], "answer_name": a.get("name", a["id"])}
