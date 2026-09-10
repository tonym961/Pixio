"""API impostazioni: lettura, validazione, salvataggio e applicazione della configurazione."""
import ipaddress
import logging
import os
import re

from flask import Blueprint, jsonify, request

from .. import config as C
from .. import privileged
from .. import settings as S
from ..privileged import HelperError
from ..storage import deep_merge, update_json
from ..services import system

bp = Blueprint("api_settings", __name__)
log = logging.getLogger("pixio.api_settings")

LEASE_RE = re.compile(r"^\d{1,4}[mhd]?$")
SMBNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
IFACE_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,15}$")
SECTIONS = ("network", "menu", "library", "windows", "scan")


@bp.errorhandler(HelperError)
def _helper_err(e):
    return jsonify({"error": f"Operazione fallita: {e}"}), 500


@bp.errorhandler(ValueError)
def _value_err(e):
    return jsonify({"error": str(e)}), 400


# ---------------------------------------------------------------- validazione
def _ipv4(v, label, required=False):
    v = str(v or "").strip()
    if not v:
        if required:
            raise ValueError(f"{label}: indirizzo IPv4 obbligatorio")
        return ""
    try:
        return str(ipaddress.IPv4Address(v))
    except ipaddress.AddressValueError:
        raise ValueError(f"{label}: indirizzo IPv4 non valido ({v})")


def _bool(v, label):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.lower() in ("true", "false", "1", "0", "on", "off"):
        return v.lower() in ("true", "1", "on")
    raise ValueError(f"{label}: valore booleano atteso")


def _int(v, label, lo, hi):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: numero intero atteso")
    if n < lo or n > hi:
        raise ValueError(f"{label}: valore fuori intervallo ({lo}-{hi})")
    return n


def _str(v, label, maxlen=80, allow_empty=True):
    s = str(v if v is not None else "").strip()
    if any(c in s for c in "\n\r\x00"):
        raise ValueError(f"{label}: caratteri non ammessi")
    if not s and not allow_empty:
        raise ValueError(f"{label}: valore obbligatorio")
    if len(s) > maxlen:
        raise ValueError(f"{label}: troppo lungo (max {maxlen} caratteri)")
    return s


def validate(incoming, current):
    """Valida i campi presenti in `incoming` e ritorna la config finale (deep_merge su `current`)."""
    if not isinstance(incoming, dict):
        raise ValueError("Corpo della richiesta non valido")
    clean = {}
    net_in = incoming.get("network")
    if isinstance(net_in, dict):
        n = {}
        merged = {**current.get("network", {}), **net_in}
        if "interface" in net_in:
            iface = _str(net_in["interface"], "Interfaccia", 15, allow_empty=False)
            if not IFACE_RE.match(iface) or not os.path.isdir(os.path.join("/sys/class/net", iface)):
                raise ValueError(f"Interfaccia di rete inesistente: {iface}")
            n["interface"] = iface
        if "server_ip" in net_in:
            n["server_ip"] = _ipv4(net_in["server_ip"], "IP del server", required=True)
        if "dhcp_mode" in net_in:
            if net_in["dhcp_mode"] not in ("proxy", "full"):
                raise ValueError("Modalità DHCP non valida (proxy o full)")
            n["dhcp_mode"] = net_in["dhcp_mode"]
        full_mode = merged.get("dhcp_mode") == "full"
        for k, label in (("dhcp_range_start", "Inizio intervallo DHCP"), ("dhcp_range_end", "Fine intervallo DHCP"),
                         ("dhcp_netmask", "Netmask"), ("dhcp_router", "Gateway"), ("dhcp_dns", "DNS")):
            if k in net_in and (full_mode or str(net_in[k] or "").strip()):
                n[k] = _ipv4(net_in[k], label)       # in modalita' proxy i campi vuoti vengono ignorati
        if "dhcp_lease" in net_in and (full_mode or str(net_in["dhcp_lease"] or "").strip()):
            lease = _str(net_in["dhcp_lease"], "Durata lease", 8) or "12h"
            if not LEASE_RE.match(lease):
                raise ValueError("Durata lease non valida (es. 12h, 30m, 1d)")
            n["dhcp_lease"] = lease
        if merged.get("dhcp_mode") == "full":
            start = _ipv4(n.get("dhcp_range_start", merged.get("dhcp_range_start")), "Inizio intervallo DHCP", required=True)
            end = _ipv4(n.get("dhcp_range_end", merged.get("dhcp_range_end")), "Fine intervallo DHCP", required=True)
            if ipaddress.IPv4Address(start) > ipaddress.IPv4Address(end):
                raise ValueError("Intervallo DHCP non valido: l'inizio è maggiore della fine")
        clean["network"] = n
    menu_in = incoming.get("menu")
    if isinstance(menu_in, dict):
        m = {}
        if "title" in menu_in:
            m["title"] = _str(menu_in["title"], "Titolo del menu", 80) or C.DEFAULT_CONFIG["menu"]["title"]
        if "timeout" in menu_in:
            m["timeout"] = _int(menu_in["timeout"], "Timeout del menu", 0, 600)
        if "default" in menu_in:
            d = _str(menu_in["default"], "Voce predefinita", 64) or "local"
            if d not in ("local", "shell", "memtest", "reboot") and not C.SLUG_RE.match(d):
                raise ValueError("Voce predefinita non valida")
            m["default"] = d
        for k in ("show_local", "show_shell", "show_reboot", "show_memtest"):
            if k in menu_in:
                m[k] = _bool(menu_in[k], k)
        if "groups" in menu_in:
            g = menu_in["groups"]
            if not isinstance(g, list):
                raise ValueError("Gruppi del menu: elenco atteso")
            groups = []
            for x in g:
                s = _str(x, "Nome gruppo", 40)
                if s and s not in groups:
                    groups.append(s)
            m["groups"] = groups
        clean["menu"] = m
    lib_in = incoming.get("library")
    if isinstance(lib_in, dict):
        lib = {}
        for k in ("samba_share_enabled", "web_upload_enabled"):
            if k in lib_in:
                lib[k] = _bool(lib_in[k], k)
        if "samba_share_name" in lib_in:
            name = _str(lib_in["samba_share_name"], "Nome share", 32) or "iso"
            if not SMBNAME_RE.match(name):
                raise ValueError("Nome della share non valido (lettere, numeri, . _ -)")
            lib["samba_share_name"] = name
        clean["library"] = lib          # samba_password_set non è modificabile da qui
    win_in = incoming.get("windows")
    if isinstance(win_in, dict):
        w = {}
        if "smb_export_enabled" in win_in:
            w["smb_export_enabled"] = _bool(win_in["smb_export_enabled"], "smb_export_enabled")
        if "setup_logs_enabled" in win_in:
            w["setup_logs_enabled"] = _bool(win_in["setup_logs_enabled"], "setup_logs_enabled")
        if "setup_logs_keep" in win_in:
            w["setup_logs_keep"] = _int(win_in["setup_logs_keep"], "Log delle installazioni da tenere", 1, 1000)
        clean["windows"] = w
    scan_in = incoming.get("scan")
    if isinstance(scan_in, dict):
        sc = {}
        if "auto" in scan_in:
            sc["auto"] = _bool(scan_in["auto"], "Scansione automatica")
        if "interval_min" in scan_in:
            sc["interval_min"] = _int(scan_in["interval_min"], "Intervallo di scansione", 1, 1440)
        clean["scan"] = sc
    return deep_merge(current, clean)


def public_config(cfg):
    out = {k: cfg[k] for k in SECTIONS if k in cfg}
    out["library"] = dict(out.get("library", {}))
    out["library"]["samba_password_set"] = bool(out["library"].get("samba_password_set"))
    out["interfaces"] = S.list_interfaces()
    return out


# ---------------------------------------------------------------- route
@bp.route("/api/settings")
def get_settings():
    return jsonify(public_config(S.load()))


@bp.route("/api/settings", methods=["PUT"])
def put_settings():
    incoming = request.get_json(silent=True)
    if not isinstance(incoming, dict):
        raise ValueError("Corpo JSON non valido")
    current = S.load()
    old_ip = current["network"].get("server_ip", "")
    new_cfg = validate(incoming, current)
    new_cfg["auth"] = current.get("auth", {})     # mai modificabile da questa API
    warnings = []
    # Installazione Windows via rete: la share [pxe] usa un utente Samba dedicato (i guest sono bloccati da WinPE).
    win = new_cfg.setdefault("windows", {})
    win.setdefault("smb_user", "pxe")
    if win.get("smb_export_enabled") and not win.get("smb_password"):
        import secrets
        win["smb_password"] = secrets.token_urlsafe(12)
    S.save(new_cfg)
    if win.get("smb_export_enabled"):
        try:
            privileged.call("windows-share-password", stdin_text=win["smb_password"] + "\n")
        except HelperError as e:
            warnings.append(f"Utente Samba per il setup di Windows non impostato: {e}")
        try:
            from ..services import catalog as _cat
            for _e in _cat.load()["isos"].values():
                if _e.get("enabled") and _e.get("type") == "windows":
                    _cat.write_inject_files(_e)
        except Exception as e:  # noqa
            warnings.append(f"File di avvio Windows non rigenerati: {e}")
    applied = {"dnsmasq": "non applicato", "nginx": "non applicato", "samba": "non applicato"}
    try:
        res = system.apply("all")
        applied.update(res.get("results") or {})
    except HelperError as e:
        return jsonify({"error": f"Impostazioni salvate ma non applicate: {e}", "applied": applied}), 500
    for k, v in applied.items():
        if v != "ok":
            warnings.append(f"{k}: {v}")
    new_ip = new_cfg["network"].get("server_ip", "")
    if new_ip and new_ip != old_ip:
        try:
            system.rebuild_ipxe(new_ip)
            warnings.append("iPXE in ricompilazione con il nuovo indirizzo: 2-3 minuti")
        except (HelperError, ValueError) as e:
            warnings.append(f"Ricompilazione iPXE non avviata: {e}")
    return jsonify({"ok": all(v == "ok" for v in applied.values()), "applied": applied, "warnings": warnings})


@bp.route("/api/settings/samba-password", methods=["POST"])
def samba_password():
    d = request.get_json(silent=True) or {}
    pw = str(d.get("password", ""))
    if len(pw) < 4:
        raise ValueError("La password Samba deve avere almeno 4 caratteri")
    if any(c in pw for c in "\n\r\x00"):
        raise ValueError("Caratteri non ammessi nella password")
    privileged.call("samba-password", stdin_text=pw + "\n", timeout=60)

    def upd(cfg):
        cfg = cfg if isinstance(cfg, dict) else {}
        cfg.setdefault("library", {})["samba_password_set"] = True
        return cfg
    update_json(C.CONFIG_FILE, upd, default={})
    return jsonify({"ok": True})
