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
e 11, predefinito) oppure "server" (Windows Server 2016-2025 con interfaccia grafica). Ogni voce del
catalogo dichiara in "editions" le piattaforme su cui ha davvero effetto: la validazione rifiuta le
voci non compatibili col target, la generazione le salta senza errori e le app Appx da rimuovere
vengono ignorate quando il target è "server" (docs/API.md, sezione 11).

ATTENZIONE alle password: autounattend.xml le contiene in chiaro (PlainText true) e il file viene servito
ai client via HTTP senza autenticazione. È il funzionamento previsto dal contratto; la GUI lo dice a chiare
lettere. Anche il bypass dei requisiti di Windows 11 non è una configurazione supportata da Microsoft.
"""
import copy
import os
import re
import threading
import time
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET

from .. import config as C
from ..storage import read_json, update_json, deep_merge

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

# Tipo di Windows a cui è destinato il profilo (docs/API.md, sezione 11). Serve a tenere fuori dai
# server le ottimizzazioni che toccano componenti che su Windows Server non esistono (Cortana,
# Copilot, widget, Xbox, Microsoft Store, esperienze consumer, barra applicazioni di Windows 11).
TARGETS = ("client", "server")
TARGET_LABELS = {
    "client": "Windows client (10 e 11)",
    "server": "Windows Server (2016-2025, con interfaccia grafica)",
}
# Piattaforme ammesse nel campo "editions" del catalogo e piattaforme coperte da ogni target.
PLATFORMS = ("10", "11", "server")
TARGET_PLATFORMS = {"client": ("10", "11"), "server": ("server",)}

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
    "target": "client",          # "client" (Windows 10/11) oppure "server" (Windows Server)
    "language": "it-IT",
    "input_locale": "it-IT",
    "timezone": "W. Europe Standard Time",
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


def apps_list():
    return [dict(x) for x in APPS]


def disk_modes_list():
    return [{"id": m, "name": DISK_MODE_LABELS[m]} for m in DISK_MODES]


def groups_list():
    return list(LOCAL_GROUPS)


def targets_list():
    """Tipi di Windows per la tendina della GUI: [{id, name}] (docs/API.md, sezione 11)."""
    return [{"id": t, "name": TARGET_LABELS[t]} for t in TARGETS]


def check_target(valore, default="client"):
    """Normalizza settings.target. Alza ValueError se non è né client né server."""
    t = _txt(valore).lower() or default
    if t not in TARGETS:
        raise ValueError("Tipo di Windows non valido: " + " oppure ".join(TARGETS)
                         + " (client = Windows 10/11, server = Windows Server)")
    return t


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
    """True se la voce ha effetto sul tipo di Windows scelto (docs/API.md, sezione 11)."""
    return bool(tweak_platforms(voce) & set(TARGET_PLATFORMS.get(target, PLATFORMS)))


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
    validazione le rifiuta al salvataggio, ma un profilo salvato prima della distinzione
    client/server deve continuare a generare un XML valido (docs/API.md, sezione 11).
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

def validate(settings, rifiuta_incompatibili=True):
    """Controlla e normalizza le impostazioni. Alza ValueError con un messaggio in italiano.

    I valori mancanti arrivano dai predefiniti, così un profilo parziale resta valido.

    Con `rifiuta_incompatibili` (predefinito) le ottimizzazioni che non hanno effetto sul tipo di
    Windows scelto in `target` fanno fallire la validazione, dicendo quale voce e perché. La
    generazione dell'XML chiama invece la validazione con False e si limita a saltarle
    (docs/API.md, sezione 11): un profilo salvato prima della distinzione client/server, o un
    modello cambiato sotto i piedi, deve continuare a produrre un autounattend.xml valido.
    """
    if settings is not None and not isinstance(settings, dict):
        raise ValueError("Impostazioni non valide: atteso un oggetto")
    s = deep_merge(copy.deepcopy(DEFAULTS), settings or {})
    out = {}

    # --- tipo di Windows (client oppure server): decide quali ottimizzazioni sono ammesse
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
        if not admin_pw:
            raise ValueError(f"Indica la password dell'utente {admin}: senza password Windows non "
                             "completa l'installazione automatica")
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
        if not eu_pw:
            raise ValueError(f"Indica la password dell'utente {eu_name}")
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
    out["disk"] = {"mode": mode, "wipe": _bool(dk.get("wipe"), True),
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

    # --- app da rimuovere (con target "server" restano scritte ma non generano comandi: vedi
    # _pass_oobe. Non si scartano qui perché tornando al tipo "client" l'elenco deve riapparire)
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
        piattaforme = ", ".join(p for p in PLATFORMS if p in tweak_platforms(voce))
        if out["target"] == "server":
            motivo = ("tocca componenti che su Windows Server non esistono; vale solo su "
                      "Windows " + piattaforme)
        else:
            motivo = "vale solo su Windows Server"
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
    l'offuscamento base64 di Windows, che comunque non è una protezione."""
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


def _pass_windows_pe(root, st, arch):
    """windowsPE: lingua del setup, LabConfig, disco, immagine, chiave di prodotto."""
    sp = _pass(root, "windowsPE")

    intl = _component(sp, "Microsoft-Windows-International-Core-WinPE", arch)
    ui = _el(intl, "SetupUILanguage")
    _el(ui, "UILanguage", st["language"])
    _el(intl, "InputLocale", st["input_locale"])
    _el(intl, "SystemLocale", st["language"])
    _el(intl, "UILanguage", st["language"])
    _el(intl, "UserLocale", st["language"])

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
    if st["skip_oobe"]:
        righe.append(('cmd /c reg add "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\OOBE" '
                      '/v BypassNRO /t REG_DWORD /d 1 /f',
                      "Consente di completare l'OOBE senza rete e senza account Microsoft"))
    if righe:
        rs = _el(setup, "RunSynchronous")
        for i, (cmd, descr) in enumerate(righe, 1):
            _sync_command(rs, i, cmd, descr)

    part_windows = None
    if st["disk"]["mode"] != "manuale":
        dc = _el(setup, "DiskConfiguration")
        _el(dc, "WillShowUI", "OnError")
        disco = _add(dc, "Disk")
        _el(disco, "DiskID", 0)
        _el(disco, "WillWipeDisk", "true" if st["disk"]["wipe"] else "false")
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


def _driver_path(server_ip, cfg):
    """Percorso UNC della libreria driver di Pixio e credenziali da usare per raggiungerla.

    La share di sola lettura \\\\<ip>\\pxe (cartella drivers) è quella che WinPE monta già per il setup:
    è protetta da utente e password, quindi servono le credenziali dentro l'XML. Se l'esportazione SMB
    per Windows non è attiva il percorso viene scritto lo stesso, con un commento che spiega cosa fare.
    """
    ip = _txt(server_ip) or "127.0.0.1"
    win = (cfg or {}).get("windows", {}) if isinstance(cfg, dict) else {}
    utente = _txt(win.get("smb_user")) or "pxe"
    password = _pw(win.get("smb_password"))
    attivo = bool(win.get("smb_export_enabled"))
    return {"path": f"\\\\{ip}\\pxe\\drivers", "user": utente, "password": password,
            "domain": ip, "attivo": attivo}


def _pass_specialize(root, st, arch, server_ip, cfg, em=None):
    """specialize: nome computer, fuso orario, dominio, driver, tweak di sistema."""
    em = em or _Emessi()
    sp = _pass(root, "specialize")

    shell = _component(sp, "Microsoft-Windows-Shell-Setup", arch)
    _el(shell, "ComputerName", st["computer_name"])
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

    if st["drivers_from_pixio"]:
        d = _driver_path(server_ip, cfg)
        pnp = _component(sp, "Microsoft-Windows-PnpCustomizationsNonWinPE", arch)
        if not d["attivo"]:
            pnp.append(ET.Comment(" Per usare questa cartella serve l'opzione \"Installazione Windows "
                                  "via rete\" attiva nelle impostazioni di Pixio (share SMB di sola "
                                  "lettura), altrimenti il setup ignora il percorso "))
        dp = _el(pnp, "DriverPaths")
        pc = _add(dp, "PathAndCredentials")
        pc.set("{%s}keyValue" % WCM, "1")
        _el(pc, "Path", d["path"])
        cred = _el(pc, "Credentials")
        _el(cred, "Domain", d["domain"])
        _el(cred, "Username", d["user"])
        _el(cred, "Password", d["password"])

    # Tweak di sistema: comandi eseguiti una volta sola, con i diritti di sistema.
    # Prima i campi storici del profilo, poi le ottimizzazioni del catalogo: se una ottimizzazione
    # rifà la stessa cosa di un campo booleano il comando viene generato una volta sola.
    tweaks = []

    def agg(cmd, descr):
        if em.comando(cmd):
            tweaks.append((cmd, descr))

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

    avanzate = "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced"
    # se un'ottimizzazione ha già scritto HideFileExt sul profilo predefinito non lo si rifà
    if em.valore("HKCU", avanzate, "HideFileExt"):
        agg(_reg_add("HKCU", avanzate, "HideFileExt", "REG_DWORD",
                     1 if st["hide_files_ext"] else 0),
            "Estensioni dei file " + ("nascoste" if st["hide_files_ext"] else "visibili"))
    # Le app Appx da rimuovere non hanno senso su Windows Server: non c'è il Microsoft Store e le
    # app preinstallate dei client non ci sono. Restano scritte nel profilo (così tornando al tipo
    # "client" si ritrovano) ma non generano nessun comando: il contratto (docs/API.md, sezione 11)
    # non prevede avvisi restituiti dalla validazione, quindi la cosa si dice qui e nell'XML.
    apps_da_rimuovere = [] if st["target"] == "server" else st["remove_apps"]
    if st["target"] == "server" and st["remove_apps"]:
        shell.append(ET.Comment(" Le app da rimuovere sono state ignorate: il profilo è per Windows "
                                "Server, dove le app del Microsoft Store non sono installate "))
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


def render_autounattend(profile, server_ip="", cfg=None):
    """Genera l'autounattend.xml del profilo. Ritorna una stringa indentata e verificata.

    `profile` è il profilo completo ({name, settings}) oppure le sole impostazioni.
    `server_ip` serve per il percorso della share driver di Pixio.
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
    arch = st["architecture"]

    ET.register_namespace("", NS)
    ET.register_namespace("wcm", WCM)
    root = ET.Element(_q("unattend"))
    root.append(ET.Comment(f" autounattend.xml generato da Pixio dal profilo \"{nome}\" il {_now()}. "
                           "Le password sono in chiaro: chiunque legga questo file le vede. "))
    # una sola memoria dei comandi generati per tutti i passaggi: così i campi booleani storici
    # e le ottimizzazioni equivalenti non producono due volte la stessa riga
    em = _Emessi()
    _pass_windows_pe(root, st, arch)
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


def save_as_answer(profile_id, answer_id=None, server_ip=""):
    """Genera l'autounattend.xml e lo salva come risposta di tipo windows.

    Con `answer_id` aggiorna una risposta esistente, altrimenti ne crea una nuova.
    Ritorna `{ok, answer_id, answer_name}`.
    """
    answers = _answers()
    prof = get(check_id(profile_id))
    if not prof:
        raise FileNotFoundError("Profilo non trovato")
    xml = render_autounattend(prof, server_ip)
    if answer_id:
        a = answers.update(answer_id, {"content": xml, "filename": "autounattend.xml"})
    else:
        a = answers.create({
            "name": prof["name"][:MAX_NAME],
            "kind": "windows",
            "note": ("Generata dal profilo Windows " + prof["name"])[:MAX_NOTE],
            "content": xml,
            "filename": "autounattend.xml",
        })
    return {"ok": True, "answer_id": a["id"], "answer_name": a.get("name", a["id"])}
