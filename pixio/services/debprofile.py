"""Personalizzazione Debian: profili che generano un file preseed.cfg (docs/API.md, sezione 9).

Un profilo è `{id, name, note, updated, settings:{...}}`; i profili stanno tutti in un solo file JSON
(DEBPROFILES_FILE, di norma /var/lib/pixio/debprofiles.json) scritto in modo atomico da storage.update_json.

Il generatore `render_preseed(profile)` produce un preseed.cfg completo, ordinato e commentato in italiano.
Il file viene poi salvato come "risposta" di tipo debian (services/answers.py) e agganciato al boot da
`answers.kernel_args()`, che aggiunge alla riga di comando del kernel:
    auto=true priority=critical url=http://<ip>/answers/<id>/preseed.cfg

ATTENZIONE alle password: il preseed le contiene in chiaro (d-i passwd/root-password, passwd/user-password).
È il comportamento voluto dal contratto; la GUI deve avvisare l'utente. Se però il valore inserito è già
un hash crypt (inizia con $1$/$5$/$6$/$y$/$2b$...) viene usato il campo *-crypted, che è preferibile.
"""
import copy
import os
import re
import time

from .. import config as C
from ..storage import read_json, update_json, deep_merge

# ---------------------------------------------------------------- costanti e valori predefiniti

PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# hostname RFC 1123: etichetta di 1-63 caratteri, lettere/cifre/trattino, non inizia né finisce con trattino
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
DOMAIN_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                       r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
LOCALE_RE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?(\.[A-Za-z0-9-]{2,20})?$")
KEYBOARD_RE = re.compile(r"^[a-z]{2,6}(-[a-z0-9]{1,12})?$")
TIMEZONE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+_-]*(/[A-Za-z0-9+._-]+){0,2}$")
SUITE_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,31}$")
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+._-]{0,63}$")
IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
# disco: "auto" oppure un device a blocchi plausibile (non una partizione)
DISK_RE = re.compile(
    r"^/dev/("
    r"sd[a-z]{1,2}|hd[a-z]|vd[a-z]{1,2}|xvd[a-z]{1,2}|"
    r"nvme\d{1,3}n\d{1,3}|mmcblk\d{1,3}|"
    r"disk/by-(id|path|uuid|label)/[A-Za-z0-9._:@+-]{1,160}"
    r")$")
PARTITION_RE = re.compile(r"^/dev/(sd[a-z]{1,2}\d+|hd[a-z]\d+|vd[a-z]{1,2}\d+|xvd[a-z]{1,2}\d+|"
                          r"nvme\d{1,3}n\d{1,3}p\d+|mmcblk\d{1,3}p\d+)$")
# hash crypt già pronto (yescrypt, sha512, bcrypt, md5...): si usa il campo *-crypted
CRYPT_RE = re.compile(r"^\$(1|2[abxy]?|5|6|7|y|gy)\$[^\s:]{5,}$")
# chiave pubblica SSH: tipo + base64 + commento facoltativo (niente apici: finisce dentro late_command)
SSH_KEY_RE = re.compile(
    r"^(ssh-rsa|ssh-ed25519|ssh-dss|ecdsa-sha2-nistp(256|384|521)|"
    r"sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)"
    r"\s+[A-Za-z0-9+/]{32,}={0,3}"
    r"(\s+[A-Za-z0-9._@/+ -]{1,120})?$")

MAX_NAME = 64
MAX_NOTE = 200
MAX_PROFILES = 200
MAX_PACKAGES = 100
MAX_SSH_KEYS = 20
MAX_LATE = 4000
MAX_SWAP_MB = 131072

RECIPES = ("atomic", "home", "multi", "lvm", "crypto")
RECIPE_LABELS = {
    "atomic": "Tutto in una partizione (consigliato)",
    "home": "Separa /home",
    "multi": "Separa /home, /var e /tmp",
    "lvm": "LVM (volumi ridimensionabili)",
    "crypto": "LVM cifrato (LUKS)",
}
FILESYSTEMS = ("ext4", "xfs", "btrfs")
NETWORK_MODES = ("dhcp", "static")

# Task di tasksel noti (Debian 12/13). Elenco chiuso: un task inventato blocca l'installazione.
TASKS = [
    {"id": "standard", "name": "Utilità di sistema standard"},
    {"id": "ssh-server", "name": "Server SSH"},
    {"id": "web-server", "name": "Server web (Apache)"},
    {"id": "print-server", "name": "Server di stampa (CUPS)"},
    {"id": "laptop", "name": "Portatile (gestione energia, wireless)"},
    {"id": "desktop", "name": "Ambiente desktop (predefinito)"},
    {"id": "gnome-desktop", "name": "Desktop GNOME"},
    {"id": "xfce-desktop", "name": "Desktop Xfce (leggero)"},
    {"id": "kde-desktop", "name": "Desktop KDE Plasma"},
    {"id": "cinnamon-desktop", "name": "Desktop Cinnamon"},
    {"id": "mate-desktop", "name": "Desktop MATE"},
    {"id": "lxde-desktop", "name": "Desktop LXDE"},
    {"id": "lxqt-desktop", "name": "Desktop LXQt"},
    {"id": "italian", "name": "Supporto per la lingua italiana"},
    {"id": "italian-desktop", "name": "Desktop in italiano"},
    {"id": "ssh-server-minimal", "name": "Server SSH (solo openssh-server)"},
]
TASK_IDS = tuple(t["id"] for t in TASKS)

# Mirror suggeriti nella GUI (il campo resta libero)
MIRRORS = [
    {"host": "deb.debian.org", "directory": "/debian", "name": "deb.debian.org (rete mondiale, consigliato)"},
    {"host": "ftp.it.debian.org", "directory": "/debian", "name": "ftp.it.debian.org (Italia)"},
    {"host": "debian.mirror.garr.it", "directory": "/debian", "name": "debian.mirror.garr.it (GARR, Italia)"},
    {"host": "ftp.eu.debian.org", "directory": "/debian", "name": "ftp.eu.debian.org (Europa)"},
    {"host": "ftp.de.debian.org", "directory": "/debian", "name": "ftp.de.debian.org (Germania)"},
]

TIMEZONES = [
    "Europe/Rome", "Europe/Vatican", "Europe/San_Marino", "Europe/Malta", "Europe/Zurich",
    "Europe/Paris", "Europe/Berlin", "Europe/Madrid", "Europe/Lisbon", "Europe/London",
    "Europe/Amsterdam", "Europe/Brussels", "Europe/Vienna", "Europe/Prague", "Europe/Warsaw",
    "Europe/Athens", "Europe/Bucharest", "Europe/Kyiv", "Europe/Moscow", "UTC",
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Sao_Paulo",
    "Asia/Dubai", "Asia/Kolkata", "Asia/Shanghai", "Asia/Tokyo", "Australia/Sydney",
]

LOCALES = [
    "it_IT.UTF-8", "en_US.UTF-8", "en_GB.UTF-8", "de_DE.UTF-8", "fr_FR.UTF-8",
    "es_ES.UTF-8", "pt_PT.UTF-8", "sl_SI.UTF-8", "de_AT.UTF-8", "fr_CH.UTF-8",
]

KEYBOARDS = ["it", "us", "gb", "de", "fr", "es", "ch", "pt", "si", "hr"]

# Valori predefiniti: PMI italiana, installazione non presidiata su un solo disco.
DEFAULTS = {
    "hostname": "debian",
    "domain": "",
    "locale": "it_IT.UTF-8",
    "keyboard": "it",
    "timezone": "Europe/Rome",
    "suite": "stable",
    "mirror": {"host": "deb.debian.org", "directory": "/debian", "proxy": ""},
    "network": {"mode": "dhcp", "ip": "", "netmask": "", "gateway": "", "dns": ""},
    "root": {"enabled": False, "password": ""},
    "user": {"fullname": "Amministratore", "username": "admin", "password": "", "sudo": True},
    "disk": {
        "device": "auto",
        "recipe": "atomic",
        "swap_mb": 0,
        "filesystem": "ext4",
        "wipe": True,
        "crypto_password": "",     # passphrase LUKS, obbligatoria con la ricetta "crypto"
    },
    "tasks": ["standard", "ssh-server"],
    "packages": [],
    "popcon": False,
    "grub_device": "default",
    "late_command": "",
    "reboot_after": True,
    "ssh_keys": [],
}


def defaults():
    """Copia profonda dei valori predefiniti.

    Serve copy.deepcopy e non deep_merge: quest'ultimo condivide i dizionari annidati non
    sovrascritti, e chi modificasse `settings["disk"]` cambierebbe DEFAULTS per tutti.
    """
    return copy.deepcopy(DEFAULTS)


def tasks_list():
    return [dict(t) for t in TASKS]


def mirrors_list():
    return [dict(m) for m in MIRRORS]


# ---------------------------------------------------------------- utilità

def _file():
    """Percorso del file dei profili: da pixio.config, letto a ogni chiamata (i test lo spostano)."""
    return getattr(C, "DEBPROFILES_FILE", os.path.join(C.VAR_DIR, "debprofiles.json"))


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _txt(v):
    """Valore testuale su una riga sola: il preseed non ammette a capo dentro un valore."""
    s = "" if v is None else str(v)
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = "".join(ch for ch in s if ch >= " " or ch == " ")
    return re.sub(r"\s+", " ", s).strip()


def _bool(v, default=False):
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "si", "sì", "yes", "on")
    return bool(v)


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        raise ValueError("Valore numerico non valido: " + _txt(v))


def _list(v):
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [x for x in re.split(r"[\s,;]+", v.strip()) if x]
    if isinstance(v, (list, tuple)):
        return [x for x in v if x is not None]
    raise ValueError("Atteso un elenco")


def _ipv4(v, campo):
    s = _txt(v)
    m = IPV4_RE.match(s)
    if not m or any(int(g) > 255 for g in m.groups()):
        raise ValueError(f"{campo}: indirizzo IPv4 non valido ({s or 'vuoto'})")
    return s


def _sh_quote(s):
    """Racchiude fra apici singoli per late_command (che gira sotto /bin/sh)."""
    return "'" + str(s).replace("'", "'\\''") + "'"


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


# ---------------------------------------------------------------- validazione

def validate(settings):
    """Controlla e normalizza le impostazioni. Alza ValueError con un messaggio in italiano.

    I valori mancanti vengono presi dai predefiniti, così un profilo parziale resta valido.
    """
    if settings is not None and not isinstance(settings, dict):
        raise ValueError("Impostazioni non valide: atteso un oggetto")
    s = deep_merge(copy.deepcopy(DEFAULTS), settings or {})
    out = {}

    # --- sistema e lingua
    host = _txt(s.get("hostname"))
    if not host:
        raise ValueError("Indica il nome host del computer")
    if len(host) > 63 or not HOSTNAME_RE.match(host):
        raise ValueError("Nome host non valido: da 1 a 63 caratteri fra lettere, cifre e trattino, "
                         "senza trattino iniziale o finale (RFC 1123)")
    out["hostname"] = host.lower()

    dom = _txt(s.get("domain"))
    if dom:
        if len(dom) > 253 or not DOMAIN_RE.match(dom):
            raise ValueError("Dominio non valido: es. azienda.local")
        dom = dom.lower()
    out["domain"] = dom

    loc = _txt(s.get("locale")) or DEFAULTS["locale"]
    if not LOCALE_RE.match(loc):
        raise ValueError("Lingua/locale non valida: es. it_IT.UTF-8")
    out["locale"] = loc

    kbd = _txt(s.get("keyboard")) or DEFAULTS["keyboard"]
    if not KEYBOARD_RE.match(kbd):
        raise ValueError("Disposizione della tastiera non valida: es. it")
    out["keyboard"] = kbd

    tz = _txt(s.get("timezone")) or DEFAULTS["timezone"]
    if not TIMEZONE_RE.match(tz) or ".." in tz:
        raise ValueError("Fuso orario non valido: es. Europe/Rome")
    out["timezone"] = tz

    suite = _txt(s.get("suite")) or DEFAULTS["suite"]
    if not SUITE_RE.match(suite):
        raise ValueError("Versione (suite) non valida: es. stable, trixie, bookworm")
    out["suite"] = suite

    # --- mirror
    mir = s.get("mirror") if isinstance(s.get("mirror"), dict) else {}
    mhost = _txt(mir.get("host")) or DEFAULTS["mirror"]["host"]
    if len(mhost) > 253 or not DOMAIN_RE.match(mhost):
        raise ValueError("Host del mirror non valido: es. deb.debian.org")
    mdir = _txt(mir.get("directory")) or DEFAULTS["mirror"]["directory"]
    if not mdir.startswith("/") or not re.match(r"^/[A-Za-z0-9._/-]{0,120}$", mdir):
        raise ValueError("Cartella del mirror non valida: deve iniziare con / (es. /debian)")
    proxy = _txt(mir.get("proxy"))
    if proxy and not re.match(r"^https?://[A-Za-z0-9._-]{1,253}(:\d{1,5})?/?$", proxy):
        raise ValueError("Proxy non valido: es. http://proxy.azienda.local:3128")
    out["mirror"] = {"host": mhost.lower(), "directory": mdir.rstrip("/") or "/debian", "proxy": proxy}

    # --- rete
    net = s.get("network") if isinstance(s.get("network"), dict) else {}
    mode = _txt(net.get("mode")).lower() or "dhcp"
    if mode not in NETWORK_MODES:
        raise ValueError("Modalità di rete non valida: dhcp oppure static")
    n = {"mode": mode, "ip": "", "netmask": "", "gateway": "", "dns": ""}
    if mode == "static":
        n["ip"] = _ipv4(net.get("ip"), "Indirizzo IP")
        n["netmask"] = _ipv4(net.get("netmask"), "Maschera di rete")
        n["gateway"] = _ipv4(net.get("gateway"), "Gateway")
        dns = [_ipv4(x, "DNS") for x in _list(net.get("dns"))]
        if not dns:
            raise ValueError("Con l'indirizzo statico serve almeno un server DNS")
        if len(dns) > 3:
            raise ValueError("Massimo 3 server DNS")
        n["dns"] = " ".join(dns)
    out["network"] = n

    # --- account
    root = s.get("root") if isinstance(s.get("root"), dict) else {}
    root_on = _bool(root.get("enabled"), False)
    root_pw = _txt(root.get("password"))
    if root_on and not root_pw:
        raise ValueError("L'accesso root è attivo: indica la password di root")
    if root_pw and len(root_pw) > 256:
        raise ValueError("Password di root troppo lunga (max 256 caratteri)")
    out["root"] = {"enabled": root_on, "password": root_pw if root_on else ""}

    usr = s.get("user") if isinstance(s.get("user"), dict) else {}
    username = _txt(usr.get("username")).lower()
    fullname = _txt(usr.get("fullname"))
    user_pw = _txt(usr.get("password"))
    sudo = _bool(usr.get("sudo"), True)
    # senza nome utente non si crea nessun utente: gli altri campi devono essere vuoti
    make_user = bool(user_pw or fullname)
    if not username and not root_on:
        raise ValueError("Serve almeno un accesso: attiva root oppure indica un utente")
    if username:
        if not USERNAME_RE.match(username):
            raise ValueError("Nome utente non valido: minuscole, cifre, trattino e underscore, "
                             "iniziale non numerica, max 32 caratteri")
        if username in ("root", "daemon", "bin", "sys", "sync", "nobody", "debian"):
            raise ValueError(f"Nome utente riservato dal sistema: {username}")
        if not user_pw:
            raise ValueError("Indica la password dell'utente " + username)
        if len(user_pw) > 256:
            raise ValueError("Password dell'utente troppo lunga (max 256 caratteri)")
        if len(fullname) > 100:
            raise ValueError("Nome completo troppo lungo (max 100 caratteri)")
        if not sudo and not root_on:
            raise ValueError("Senza root e senza sudo il sistema resterebbe inamministrabile: "
                             "attiva sudo per l'utente oppure abilita root")
    elif make_user:
        raise ValueError("Indica il nome utente oppure svuota tutti i campi dell'utente")
    out["user"] = {"fullname": fullname if username else "", "username": username,
                   "password": user_pw if username else "", "sudo": sudo}

    # --- disco
    dk = s.get("disk") if isinstance(s.get("disk"), dict) else {}
    dev = _txt(dk.get("device")) or "auto"
    if dev != "auto":
        if PARTITION_RE.match(dev):
            raise ValueError(f"{dev} è una partizione: indica il disco intero (es. /dev/sda)")
        if not DISK_RE.match(dev):
            raise ValueError("Disco non valido: usa auto oppure un device come /dev/sda, /dev/nvme0n1, "
                             "/dev/vda o /dev/disk/by-id/...")
    recipe = _txt(dk.get("recipe")).lower() or "atomic"
    if recipe not in RECIPES:
        raise ValueError("Schema di partizionamento non valido: " + ", ".join(RECIPES))
    fs = _txt(dk.get("filesystem")).lower() or "ext4"
    if fs not in FILESYSTEMS:
        raise ValueError("File system non valido: " + ", ".join(FILESYSTEMS))
    swap = _int(dk.get("swap_mb"), 0)
    if swap < 0 or swap > MAX_SWAP_MB:
        raise ValueError(f"Swap non valida: da 0 (automatica) a {MAX_SWAP_MB} MB")
    if 0 < swap < 128:
        raise ValueError("Swap troppo piccola: almeno 128 MB, oppure 0 per lasciar decidere l'installatore")
    crypto_pw = _txt(dk.get("crypto_password"))
    if recipe == "crypto":
        if not crypto_pw:
            raise ValueError("Con il disco cifrato serve una passphrase LUKS")
        if len(crypto_pw) < 8:
            raise ValueError("Passphrase LUKS troppo corta: almeno 8 caratteri")
    else:
        crypto_pw = ""
    out["disk"] = {"device": dev, "recipe": recipe, "swap_mb": swap, "filesystem": fs,
                   "wipe": _bool(dk.get("wipe"), True), "crypto_password": crypto_pw}

    # --- pacchetti
    tasks = []
    for t in _list(s.get("tasks")):
        t = _txt(t)
        if t not in TASK_IDS:
            raise ValueError(f"Task di tasksel sconosciuto: {t}. Ammessi: " + ", ".join(TASK_IDS))
        if t not in tasks:
            tasks.append(t)
    out["tasks"] = tasks

    pkgs = []
    for p in _list(s.get("packages")):
        p = _txt(p).lower()
        if not PACKAGE_RE.match(p):
            raise ValueError(f"Nome pacchetto non valido: {p}")
        if p not in pkgs:
            pkgs.append(p)
    if len(pkgs) > MAX_PACKAGES:
        raise ValueError(f"Troppi pacchetti aggiuntivi (max {MAX_PACKAGES})")
    out["packages"] = pkgs
    out["popcon"] = _bool(s.get("popcon"), False)

    # --- grub
    grub = _txt(s.get("grub_device")) or "default"
    if grub not in ("default", "auto"):
        if PARTITION_RE.match(grub):
            raise ValueError(f"{grub} è una partizione: per GRUB indica il disco intero o 'default'")
        if not DISK_RE.match(grub):
            raise ValueError("Disco di GRUB non valido: usa default oppure un device come /dev/sda")
    out["grub_device"] = grub

    # --- chiavi SSH
    keys = []
    for k in (s.get("ssh_keys") if isinstance(s.get("ssh_keys"), (list, tuple))
              else [x for x in str(s.get("ssh_keys") or "").splitlines()]):
        k = _txt(k)
        if not k:
            continue
        if not SSH_KEY_RE.match(k):
            raise ValueError("Chiave SSH non valida: attesa una riga tipo "
                             "'ssh-ed25519 AAAAC3Nz... utente@pc' (nessun apice nel commento)")
        if k not in keys:
            keys.append(k)
    if len(keys) > MAX_SSH_KEYS:
        raise ValueError(f"Troppe chiavi SSH (max {MAX_SSH_KEYS})")
    if keys and not out["user"]["username"] and not out["root"]["enabled"]:
        raise ValueError("Le chiavi SSH vanno assegnate a un utente o a root: nessuno dei due è attivo")
    out["ssh_keys"] = keys

    # --- comandi finali
    late = s.get("late_command")
    if isinstance(late, (list, tuple)):
        late_lines = [_txt(x) for x in late if _txt(x)]
    else:
        late_lines = [_txt(x) for x in str(late or "").splitlines() if _txt(x)]
    late_text = "\n".join(late_lines)
    if len(late_text) > MAX_LATE:
        raise ValueError(f"Comandi finali troppo lunghi (max {MAX_LATE} caratteri)")
    for line in late_lines:
        if "\\" in line:
            raise ValueError("Nei comandi finali non usare la barra rovesciata: "
                             "romperebbe la riga del preseed")
    out["late_command"] = late_text
    out["reboot_after"] = _bool(s.get("reboot_after"), True)
    return out


# ---------------------------------------------------------------- generatore del preseed.cfg

def _password_lines(prefix, password, commento):
    """Righe d-i per una password: in chiaro (passwd/<x>-password) oppure hash (passwd/<x>-password-crypted).

    Il contratto chiede la password in chiaro: il preseed viene servito via HTTP senza autenticazione,
    quindi chiunque sia sulla rete può leggerla. Se il valore è già un hash crypt si usa la forma -crypted.
    """
    pw = _txt(password)
    if CRYPT_RE.match(pw):
        return [f"# {commento} (hash crypt già pronto: non viene mai scritta in chiaro)",
                f"d-i passwd/{prefix}-password-crypted password {pw}"]
    return [f"# {commento} — ATTENZIONE: in chiaro dentro questo file, come previsto da d-i",
            f"d-i passwd/{prefix}-password password {pw}",
            f"d-i passwd/{prefix}-password-again password {pw}"]


def _recipe_entry(righe):
    """Una voce di partman-auto/expert_recipe: riga delle dimensioni, direttive rientrate, punto finale."""
    out = [" " * 14 + righe[0]]
    for r in righe[1:]:
        out.append(" " * 22 + r)
    out.append(" " * 14 + ".")
    return out


def _expert_recipe(recipe, fs, swap_mb, lvm):
    """Ricetta esplicita per partman: serve quando l'utente ha fissato la dimensione della swap
    (le ricette predefinite atomic/home/multi la calcolano da sole).

    Le voci sono `min priorita massimo tipo` seguite dalle direttive method{}/mountpoint{}.
    -1 = "tutto lo spazio restante". La voce EFI e quella biosgrub sono condizionate a $iflabel{ gpt },
    così la stessa ricetta funziona sia in UEFI sia in BIOS legacy.
    """
    vg = "vg0"
    voci = []
    # partizione di sistema EFI + area per GRUB su GPT in BIOS legacy
    voci += _recipe_entry([
        "538 538 1075 free",
        "$iflabel{ gpt } $reusemethod{ }",
        "method{ efi } format{ }",
    ])
    voci += _recipe_entry([
        "1 1 1 free",
        "$iflabel{ gpt } $reusemethod{ }",
        "method{ biosgrub }",
    ])
    if lvm:
        # /boot fuori dal volume group (obbligatorio con LUKS, comodo con LVM)
        voci += _recipe_entry([
            "512 1024 1024 ext4",
            "$primary{ } $bootable{ }",
            "method{ format } format{ }",
            "use_filesystem{ } filesystem{ ext4 }",
            "mountpoint{ /boot }",
        ])
        voci += _recipe_entry([
            "1000 5000 -1 ext4",
            "$defaultignore{ } $primary{ }",
            f"method{{ lvm }} vg_name{{ {vg} }}",
        ])

    def dati(minimo, prio, massimo, punto, nome_lv):
        r = [f"{minimo} {prio} {massimo} {fs}"]
        if lvm:
            r.append(f"$lvmok{{ }} in_vg{{ {vg} }} lv_name{{ {nome_lv} }}")
        r += ["method{ format } format{ }",
              f"use_filesystem{{ }} filesystem{{ {fs} }}",
              f"mountpoint{{ {punto} }}"]
        return r

    if swap_mb > 0:
        r = [f"{swap_mb} {swap_mb} {swap_mb} linux-swap"]
        if lvm:
            r.append(f"$lvmok{{ }} in_vg{{ {vg} }} lv_name{{ swap }}")
        r += ["method{ swap } format{ }"]
        voci += _recipe_entry(r)

    if recipe in ("atomic", "lvm", "crypto"):
        voci += _recipe_entry(dati(4000, 10000, -1, "/", "root"))
    elif recipe == "home":
        voci += _recipe_entry(dati(8000, 20000, 30000, "/", "root"))
        voci += _recipe_entry(dati(4000, 30000, -1, "/home", "home"))
    else:  # multi
        voci += _recipe_entry(dati(4000, 10000, 20000, "/", "root"))
        voci += _recipe_entry(dati(2000, 5000, 10000, "/var", "var"))
        voci += _recipe_entry(dati(500, 1000, 2000, "/tmp", "tmp"))
        voci += _recipe_entry(dati(4000, 20000, -1, "/home", "home"))

    corpo = ["      pixio ::"] + voci
    larghezza = max(len(x) for x in corpo) + 2
    out = ["d-i partman-auto/expert_recipe string".ljust(larghezza) + "\\"]
    for i, riga in enumerate(corpo):
        out.append(riga.ljust(larghezza) + ("\\" if i < len(corpo) - 1 else ""))
    return [x.rstrip() if not x.endswith("\\") else x for x in out]


def _late_command(st):
    """Costruisce l'elenco dei comandi di fine installazione (chiavi SSH + comandi personalizzati)."""
    cmds = []
    keys = st["ssh_keys"]
    if keys:
        if st["user"]["username"]:
            utente = st["user"]["username"]
            home = "/home/" + utente
        else:
            utente = "root"
            home = "/root"
        elenco = " ".join(_sh_quote(k) for k in keys)
        cmds.append(f"in-target sh -c \"install -d -m 0700 -o {utente} -g {utente} {home}/.ssh\"")
        cmds.append(f"in-target sh -c \"printf '%s\\n' {elenco} > {home}/.ssh/authorized_keys\"")
        cmds.append(f"in-target sh -c \"chown {utente}:{utente} {home}/.ssh/authorized_keys; "
                    f"chmod 0600 {home}/.ssh/authorized_keys\"")
    for riga in st["late_command"].splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#"):
            continue
        # una riga che comincia già con in-target/chroot viene lasciata com'è
        if riga.startswith("in-target ") or riga.startswith("chroot "):
            cmds.append(riga)
        else:
            cmds.append("in-target sh -c " + _sh_quote(riga))
    return cmds


def render_preseed(profile):
    """Genera il testo completo del preseed.cfg per un profilo (o per un dizionario di impostazioni)."""
    if isinstance(profile, dict) and "settings" in profile:
        nome = _txt(profile.get("name")) or "senza nome"
        st = validate(profile.get("settings"))
    else:
        nome = "anteprima"
        st = validate(profile)

    L = []
    a = L.append
    a("#" + "-" * 78)
    a(f"# preseed.cfg generato da {C.APP_NAME} — profilo: {nome}")
    a(f"# Creato il {time.strftime('%d/%m/%Y %H:%M')}. Installazione Debian non presidiata.")
    a("# Il file viene servito via HTTP e agganciato con: auto=true priority=critical url=<indirizzo>")
    a("# ATTENZIONE: le password sono in chiaro qui sotto. Chi legge questo file può usarle.")
    a("#" + "-" * 78)
    a("")

    # 1. localizzazione ------------------------------------------------------
    a("### 1. Lingua, paese e locale")
    a(f"d-i debian-installer/locale string {st['locale']}")
    a(f"d-i localechooser/supported-locales multiselect {st['locale']}")
    a("d-i debian-installer/language string " + st["locale"].split(".")[0])
    a("")

    # 2. tastiera ------------------------------------------------------------
    a("### 2. Tastiera")
    a("d-i keyboard-configuration/xkb-keymap select " + st["keyboard"])
    a("d-i console-setup/ask_detect boolean false")
    a("")

    # 3. rete ----------------------------------------------------------------
    a("### 3. Rete")
    a("# Sceglie da sola l'interfaccia collegata (utile sui server con più schede)")
    a("d-i netcfg/choose_interface select auto")
    a("d-i netcfg/link_wait_timeout string 30")
    net = st["network"]
    if net["mode"] == "static":
        a("# Indirizzo fisso: niente DHCP")
        a("d-i netcfg/disable_dhcp boolean true")
        a("d-i netcfg/disable_autoconfig boolean true")
        a(f"d-i netcfg/get_ipaddress string {net['ip']}")
        a(f"d-i netcfg/get_netmask string {net['netmask']}")
        a(f"d-i netcfg/get_gateway string {net['gateway']}")
        a(f"d-i netcfg/get_nameservers string {net['dns']}")
        a("d-i netcfg/confirm_static boolean true")
    else:
        a("# Indirizzo assegnato dal DHCP della rete")
        a("d-i netcfg/disable_autoconfig boolean false")
        a("d-i netcfg/dhcp_timeout string 60")
        a("d-i netcfg/dhcpv6_timeout string 15")
    a(f"d-i netcfg/get_hostname string {st['hostname']}")
    a(f"d-i netcfg/hostname string {st['hostname']}")
    a(f"d-i netcfg/get_domain string {st['domain']}")
    a("# Non chiedere conferma se il DHCP non fornisce un nome host")
    a("d-i netcfg/hostname_error note")
    a("d-i hw-detect/load_firmware boolean true")
    a("")

    # 4. mirror --------------------------------------------------------------
    a("### 4. Mirror dei pacchetti")
    a("d-i mirror/country string manual")
    a("d-i mirror/protocol string http")
    a(f"d-i mirror/http/hostname string {st['mirror']['host']}")
    a(f"d-i mirror/http/directory string {st['mirror']['directory']}")
    if st["mirror"]["proxy"]:
        a("# Proxy HTTP aziendale per apt")
        a(f"d-i mirror/http/proxy string {st['mirror']['proxy']}")
    else:
        a("# Nessun proxy (lasciare vuoto è voluto)")
        a("d-i mirror/http/proxy string")
    a(f"d-i mirror/suite string {st['suite']}")
    a("# Aggiornamenti di sicurezza e firmware non liberi attivi")
    a("d-i apt-setup/services-select multiselect security, updates")
    a("d-i apt-setup/security_host string security.debian.org")
    a("d-i apt-setup/non-free-firmware boolean true")
    a("d-i apt-setup/contrib boolean false")
    a("d-i apt-setup/non-free boolean false")
    a("d-i apt-setup/cdrom/set-first boolean false")
    a("")

    # 5. orologio ------------------------------------------------------------
    a("### 5. Orologio e fuso orario")
    a("# Orologio hardware in UTC: la scelta giusta se sul PC c'è solo Debian")
    a("d-i clock-setup/utc boolean true")
    a(f"d-i time/zone string {st['timezone']}")
    a("d-i clock-setup/ntp boolean true")
    a("")

    # 6. account -------------------------------------------------------------
    a("### 6. Account")
    if st["root"]["enabled"]:
        a("d-i passwd/root-login boolean true")
        for r in _password_lines("root", st["root"]["password"], "Password di root"):
            a(r)
    else:
        a("# Accesso root disattivato: si amministra con sudo dall'utente qui sotto")
        a("d-i passwd/root-login boolean false")
    if st["user"]["username"]:
        u = st["user"]
        a("d-i passwd/make-user boolean true")
        a(f"d-i passwd/user-fullname string {u['fullname'] or u['username']}")
        a(f"d-i passwd/username string {u['username']}")
        for r in _password_lines("user", u["password"], f"Password dell'utente {u['username']}"):
            a(r)
        gruppi = "audio cdrom dip floppy video plugdev netdev"
        if u["sudo"]:
            gruppi += " sudo"
            a("# L'utente è nel gruppo sudo: può amministrare il sistema")
        a(f"d-i passwd/user-default-groups string {gruppi}")
        a("d-i passwd/user-uid string 1000")
    else:
        a("# Nessun utente non privilegiato: si entra solo come root")
        a("d-i passwd/make-user boolean false")
    a("")

    # 7. partizionamento -----------------------------------------------------
    dk = st["disk"]
    metodo = {"atomic": "regular", "home": "regular", "multi": "regular",
              "lvm": "lvm", "crypto": "crypto"}[dk["recipe"]]
    lvm = metodo in ("lvm", "crypto")
    a("### 7. Dischi e partizionamento")
    a(f"# Schema scelto: {dk['recipe']} — {RECIPE_LABELS[dk['recipe']]}")
    if dk["device"] == "auto":
        a("# Disco: automatico (il primo disco trovato). Con più dischi conviene indicarlo a mano.")
        a("d-i partman/early_command string \\")
        a(" debconf-set partman-auto/disk \"$(list-devices disk | head -n1)\"")
    else:
        a(f"d-i partman-auto/disk string {dk['device']}")
    a(f"d-i partman-auto/method string {metodo}")
    if dk["wipe"]:
        a("# Cancellazione del disco: via LVM, RAID e tabella delle partizioni preesistenti,")
        a("# senza chiedere conferma. Tutti i dati presenti vengono persi.")
        a("d-i partman-lvm/device_remove_lvm boolean true")
        a("d-i partman-md/device_remove_md boolean true")
        a("d-i partman-partitioning/confirm_write_new_label boolean true")
        a("d-i partman-lvm/confirm boolean true")
        a("d-i partman-lvm/confirm_nooverwrite boolean true")
        a("d-i partman-basicfilesystems/no_swap boolean false")
    else:
        a("# Cancellazione del disco disattivata: se trova LVM o RAID l'installatore si ferma e chiede.")
        a("d-i partman-lvm/device_remove_lvm boolean false")
        a("d-i partman-md/device_remove_md boolean false")
    if lvm:
        a("# Usa tutto lo spazio disponibile per il gruppo di volumi")
        a("d-i partman-auto-lvm/guided_size string max")
        a("d-i partman-auto-lvm/new_vg_name string vg0")
        a("d-i partman-auto-lvm/no_boot boolean true")
    if metodo == "crypto":
        a("# Passphrase del volume cifrato (LUKS): in chiaro, come le altre password")
        a(f"d-i partman-crypto/passphrase password {dk['crypto_password']}")
        a(f"d-i partman-crypto/passphrase-again password {dk['crypto_password']}")
        a("d-i partman-crypto/weak_passphrase boolean true")
        a("# Non azzerare il disco prima di cifrarlo: su dischi grandi ci vorrebbero ore")
        a("d-i partman-auto-crypto/erase_disks boolean false")
    a(f"d-i partman/default_filesystem string {dk['filesystem']}")
    if dk["swap_mb"] > 0:
        a(f"# Ricetta esplicita: swap fissa a {dk['swap_mb']} MB e file system {dk['filesystem']}")
        a("d-i partman-auto/choose_recipe select pixio")
        for r in _expert_recipe(dk["recipe"], dk["filesystem"], dk["swap_mb"], lvm):
            a(r)
    else:
        base = "atomic" if dk["recipe"] in ("lvm", "crypto") else dk["recipe"]
        a("# Ricetta predefinita dell'installatore (swap calcolata in automatico)")
        a(f"d-i partman-auto/choose_recipe select {base}")
    a("# Conferme finali: nessuna domanda a video")
    a("d-i partman-partitioning/choose_label string gpt")
    a("d-i partman-partitioning/default_label string gpt")
    a("d-i partman/choose_partition select finish")
    a("d-i partman/confirm boolean true")
    a("d-i partman/confirm_nooverwrite boolean true")
    a("d-i partman-efi/non_efi_system boolean true")
    a("d-i partman-basicfilesystems/choose_label string gpt")
    a("")

    # 8. pacchetti -----------------------------------------------------------
    a("### 8. Pacchetti")
    if st["tasks"]:
        etichette = ", ".join(f"{t}" for t in st["tasks"])
        a(f"# Gruppi di pacchetti (tasksel): {etichette}")
        a("tasksel tasksel/first multiselect " + ", ".join(st["tasks"]))
    else:
        a("# Nessun gruppo tasksel: sistema minimo")
        a("tasksel tasksel/first multiselect")
    if st["packages"]:
        a("# Pacchetti aggiuntivi richiesti dal profilo")
        a("d-i pkgsel/include string " + " ".join(st["packages"]))
    a("d-i pkgsel/upgrade select safe-upgrade")
    a("d-i pkgsel/update-policy select unattended-upgrades")
    a("")
    a("### 8b. Statistiche d'uso (popularity-contest)")
    if st["popcon"]:
        a("# Invia a Debian l'elenco anonimo dei pacchetti usati")
        a("popularity-contest popularity-contest/participate boolean true")
    else:
        a("# Nessun invio di statistiche")
        a("popularity-contest popularity-contest/participate boolean false")
    a("")

    # 9. bootloader ----------------------------------------------------------
    a("### 9. Bootloader (GRUB)")
    a("d-i grub-installer/only_debian boolean true")
    a("d-i grub-installer/with_other_os boolean true")
    a("d-i grub-installer/skip boolean false")
    if st["grub_device"] in ("default", "auto"):
        a("# Installa GRUB dove serve (ESP in UEFI, MBR del primo disco in BIOS)")
        a("d-i grub-installer/bootdev string default")
    else:
        a(f"d-i grub-installer/bootdev string {st['grub_device']}")
    a("d-i grub-installer/force-efi-extra-removable boolean true")
    a("")

    # 10. comandi finali -----------------------------------------------------
    a("### 10. Comandi eseguiti a fine installazione (late_command)")
    cmds = _late_command(st)
    if cmds:
        if st["ssh_keys"]:
            dest = st["user"]["username"] or "root"
            a(f"# Chiavi SSH ({len(st['ssh_keys'])}) installate in authorized_keys dell'utente {dest}")
        if st["late_command"].strip():
            a("# Seguono i comandi personalizzati del profilo, eseguiti dentro il sistema installato")
        a("d-i preseed/late_command string " + " ; ".join(cmds))
    else:
        a("# Nessun comando personalizzato")
    a("")

    # 11. fine ---------------------------------------------------------------
    a("### 11. Fine installazione")
    a("d-i finish-install/keep-consoles boolean false")
    a("d-i finish-install/reboot_in_progress note")
    a("d-i cdrom-detect/eject boolean true")
    if st["reboot_after"]:
        a("# Riavvio automatico senza chiedere conferma")
        a("d-i debian-installer/exit/poweroff boolean false")
        a("d-i debian-installer/exit/halt boolean false")
    else:
        a("# Riavvio disattivato: l'installatore si ferma e aspetta")
        a("d-i debian-installer/exit/halt boolean true")
    a("")
    # nessuno spazio in coda alle righe (tranne le continuazioni, che finiscono con la barra rovesciata)
    testo = "\n".join(x if x.endswith("\\") else x.rstrip() for x in L)
    # rete di sicurezza: nessun carattere di controllo può essere finito dentro un valore
    if any(ch in testo for ch in ("\x00", "\r", "\x1b")):
        raise ValueError("Il preseed generato contiene caratteri non validi")
    return testo


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
    """Impostazioni di un preset debian da data/profile-presets.json. None se non esiste."""
    path = getattr(C, "PRESETS_FILE", os.path.join(getattr(C, "CODE_DIR", "/opt/pixio"),
                                                   "data", "profile-presets.json"))
    d = read_json(path, {})
    for p in (d.get("presets") or []):
        if isinstance(p, dict) and p.get("id") == preset_id and p.get("kind") == "debian":
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
    """Importa services/answers.py in modo pigro: potrebbe non esserci ancora."""
    try:
        from . import answers
    except ImportError:
        raise RuntimeError("Il servizio delle risposte non è disponibile")
    return answers


def save_as_answer(profile_id, answer_id=None):
    """Genera il preseed e lo salva come risposta di tipo debian (file principale preseed.cfg).

    Con `answer_id` aggiorna una risposta esistente, altrimenti ne crea una nuova.
    Ritorna `{ok, answer_id, answer_name}`.
    """
    answers = _answers()
    prof = get(check_id(profile_id))
    if not prof:
        raise FileNotFoundError("Profilo non trovato")
    testo = render_preseed(prof)
    if answer_id:
        a = answers.update(answer_id, {"content": testo, "filename": "preseed.cfg"})
    else:
        a = answers.create({
            "name": prof["name"][:MAX_NAME],
            "kind": "debian",
            "note": ("Generata dal profilo Debian " + prof["name"])[:MAX_NOTE],
            "content": testo,
            "filename": "preseed.cfg",
        })
    return {"ok": True, "answer_id": a["id"], "answer_name": a.get("name", a["id"])}
