"""Percorsi e costanti di Pixio. Tutto il codice importa da qui: niente path hardcoded altrove."""
import os
import re

APP_NAME = "Pixio"
VERSION = "1.0.0"

ETC_DIR = "/etc/pixio"
CONFIG_FILE = os.path.join(ETC_DIR, "config.json")        # impostazioni (proprietario pixio, 0640)
SECRET_FILE = os.path.join(ETC_DIR, "secret")             # chiave sessione Flask (0600)
SOURCES_DIR = os.path.join(ETC_DIR, "sources")            # <id>.json + <id>.cred, scritti SOLO dall'helper (root, 0600)

VAR_DIR = "/var/lib/pixio"
CATALOG_FILE = os.path.join(VAR_DIR, "catalog.json")      # catalogo ISO (tutte le sorgenti)
CLIENTS_FILE = os.path.join(VAR_DIR, "clients.json")      # client PXE visti
DRIVERS_FILE = os.path.join(VAR_DIR, "drivers.json")      # flag per cartella driver (winpe_inject, setup_load, note)
ANSWERS_DIR = os.path.join(VAR_DIR, "answers")            # file di risposta (autounattend.xml, preseed.cfg, user-data...)
ANSWERS_FILE = os.path.join(VAR_DIR, "answers.json")      # metadati dei file di risposta
JOBS_DIR = os.path.join(VAR_DIR, "jobs")                  # stato job in background (copie, upload, scansioni)
UPLOAD_TMP_DIR = os.path.join(VAR_DIR, "uploads")         # chunk degli upload web in corso
LOG_DIR = "/var/log/pixio"

SRV_DIR = "/srv/pixio"
SOURCES_MOUNT_DIR = os.path.join(SRV_DIR, "sources")      # /srv/pixio/sources/<id>  (mount CIFS, ro)
LIBRARY_DIR = os.path.join(SRV_DIR, "library")            # libreria locale (share samba in scrittura + upload web)
CACHE_DIR = os.path.join(SRV_DIR, "cache")                # copie locali di ISO remote
TFTP_DIR = os.path.join(SRV_DIR, "tftp")                  # undionly.kpxe, ipxe.efi, ...
HTTP_DIR = os.path.join(SRV_DIR, "http")                  # radice servita da nginx come /pxe/
HTTP_ISO_DIR = os.path.join(HTTP_DIR, "iso")              # /pxe/iso/<slug>/  (loop mount ro)
HTTP_ISOFILE_DIR = os.path.join(HTTP_DIR, "isofile")      # /pxe/isofile/<slug>.iso (symlink al file)
HTTP_BOOT_DIR = os.path.join(HTTP_DIR, "boot")            # /pxe/boot/ wimboot, memdisk, memtest
HTTP_INJECT_DIR = os.path.join(HTTP_DIR, "inject")        # /pxe/inject/<slug>/ file iniettati (winpeshl.ini, ...)
DRIVERS_DIR = os.path.join(HTTP_DIR, "drivers")             # libreria driver (share \\ip\\drivers + upload web), servita come /pxe/drivers/
DETECT_DIR = os.path.join(SRV_DIR, "detect")                # mount temporanei per il rilevamento

CODE_DIR = "/opt/pixio"
RECIPES_FILE = os.path.join(CODE_DIR, "data", "recipes.json")
STATIC_DIR = os.path.join(CODE_DIR, "static")
HELPER = "/usr/local/sbin/pixio-helper"

# Utente di servizio della GUI e dei file
SERVICE_USER = "pixio"
SERVICE_GROUP = "pixio"

# Identificatori: slug ISO e id sorgente. Stesse regole nell'helper: tenerle allineate.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# Radici da cui è lecito montare in loop un file ISO
ISO_ROOTS = (SOURCES_MOUNT_DIR, LIBRARY_DIR, CACHE_DIR)

DEFAULT_CONFIG = {
    "network": {
        "interface": "",            # es. ens18 (vuoto = rilevata all'avvio)
        "server_ip": "",            # es. 10.10.0.254 (vuoto = rilevato)
        "dhcp_mode": "proxy",       # proxy | full
        "dhcp_range_start": "",     # solo per full
        "dhcp_range_end": "",
        "dhcp_netmask": "",
        "dhcp_router": "",
        "dhcp_dns": "",
        "dhcp_lease": "12h",
    },
    "menu": {
        "title": "PIXIO - Avvio da rete",
        "timeout": 30,              # secondi; 0 = nessun timeout
        "default": "local",         # slug voce predefinita o local/shell
        "show_local": True,
        "show_shell": True,
        "show_reboot": True,
        "show_memtest": True,
        "groups": ["Strumenti", "Windows", "Windows Server", "Linux", "Hypervisor"],
        "submenus": "auto",          # auto | always | never: sottomenu per gruppo nel menu di boot
        "submenu_threshold": 8,      # con "auto" i sottomenu compaiono oltre questo numero di voci
        "theme": {"bg": "#0B1220", "accent": "#3FC1CF", "fg": "#E6ECF2", "muted": "#7C8A99", "logo_text": "PIXIO", "subtitle": "Avvio da rete"},
    },
    "library": {
        "samba_share_enabled": True,   # share \\pixio\iso in scrittura
        "samba_share_name": "iso",
        "drivers_share_name": "drivers",
        "web_upload_enabled": True,
    },
    "windows": {
        "smb_export_enabled": False,   # ri-esporta le ISO Windows montate per il setup (OFF di default)
        "smb_user": "pxe",             # utente Samba dedicato (i guest SMB sono bloccati da WinPE)
        "smb_password": "",            # generata quando si attiva l'opzione
    },
    "scan": {"auto": True, "interval_min": 10},
    "cache": {                       # copia locale automatica delle ISO che stanno su share remote
        "auto": False,
        "min_size_gb": 2,            # copia solo le ISO piu' grandi di cosi'
        "only_enabled": True,        # solo quelle abilitate nel menu
        "keep_free_gb": 20,          # spazio da lasciare libero: sotto questa soglia elimina le copie meno usate
    },
    "web": {                         # accesso alla GUI
        "https_enabled": False,
        "redirect_http": True,
    },
    "auth": {"password_hash": "", "session_hours": 12},
}
