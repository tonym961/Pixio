"""Stato del sistema per la dashboard e azioni di sistema (apply, servizi, ricompilazione iPXE)."""
import datetime
import logging
import os
import shutil
import socket
import subprocess
import time

from .. import config as C
from .. import privileged
from .. import settings as S
from ..privileged import HelperError
from . import jobs

log = logging.getLogger("pixio.system")

SERVICE_UNITS = {"dnsmasq": "dnsmasq.service", "nginx": "nginx.service", "smbd": "smbd.service"}
IPXE_UNIT = "pixio-ipxe-build.service"
IPXE_BUILD_TIMEOUT = 15 * 60
ISO_EXT = (".iso", ".img", ".wim")


def _catalog():
    try:
        from . import catalog
        return catalog
    except ImportError:
        return None


# ---------------------------------------------------------------- servizi systemd (lettura, senza sudo)
def _systemd_ts(s):
    """'Tue 2026-09-08 18:09:29 CEST' -> ISO locale; stringa vuota se non attivo."""
    parts = (s or "").split()
    if len(parts) < 3:
        return None
    try:
        dt = datetime.datetime.strptime(f"{parts[1]} {parts[2]}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return s
    return dt.astimezone().isoformat(timespec="seconds")


def services_status(names=None):
    names = names or list(SERVICE_UNITS)
    units = [SERVICE_UNITS.get(n, n) for n in names]
    res = {n: {"active": False, "state": "unknown", "sub": "", "since": None} for n in names}
    try:
        p = subprocess.run(["systemctl", "show", *units, "--property=ActiveState,SubState,ActiveEnterTimestamp"],
                           capture_output=True, text=True, timeout=10)
        blocks = [b for b in p.stdout.strip().split("\n\n") if b.strip()]
    except (OSError, subprocess.SubprocessError):
        return res
    for name, block in zip(names, blocks):
        props = dict(l.split("=", 1) for l in block.splitlines() if "=" in l)
        state = props.get("ActiveState", "unknown")
        res[name] = {"active": state == "active", "state": state, "sub": props.get("SubState", ""),
                     "since": _systemd_ts(props.get("ActiveEnterTimestamp"))}
    return res


def unit_is_active(unit):
    try:
        p = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10)
        return p.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# ---------------------------------------------------------------- misure varie
def dir_size(path):
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            for fn in files:
                try:
                    total += os.lstat(os.path.join(root, fn)).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def count_isos(path):
    try:
        return sum(1 for fn in os.listdir(path) if fn.lower().endswith(ISO_EXT) and not fn.startswith("."))
    except OSError:
        return 0


def disk_status():
    try:
        u = shutil.disk_usage(C.SRV_DIR if os.path.isdir(C.SRV_DIR) else "/")
        total, free, used = u.total, u.free, u.used
    except OSError:
        total = free = used = 0
    return {"total": total, "free": free, "used": used,
            "library_used": dir_size(C.LIBRARY_DIR), "cache_used": dir_size(C.CACHE_DIR)}


def ipxe_status():
    marker = os.path.join(C.TFTP_DIR, ".ipxe-build")
    built = os.path.isfile(marker)
    built_at = None
    if built:
        try:
            with open(marker) as f:
                txt = f.read().strip()
            parts = txt.split()
            built_at = parts[1] if len(parts) >= 2 else None
            if not built_at:
                built_at = datetime.datetime.fromtimestamp(os.path.getmtime(marker)).astimezone().isoformat(timespec="seconds")
        except OSError:
            pass
    building = unit_is_active(IPXE_UNIT) in ("active", "activating", "reloading") or bool(jobs.running("ipxe-build"))
    return {"built": built, "built_at": built_at, "building": building}


def catalog_summary():
    """{total, enabled, mounted, unknown, last_scan} dal modulo catalogo se c'è, altrimenti dal file."""
    empty = {"total": 0, "enabled": 0, "mounted": 0, "unknown": 0, "last_scan": None}
    cat = _catalog()
    fn = (getattr(cat, "catalog_summary", None) or getattr(cat, "summary", None)) if cat else None
    if callable(fn):
        try:
            d = dict(fn())
            return {**empty, **d}
        except Exception:  # noqa: BLE001
            log.exception("catalog_summary fallita")
    from ..storage import read_json
    data = read_json(C.CATALOG_FILE, {})
    isos = data.get("isos") if isinstance(data, dict) else data
    if isinstance(isos, dict):
        isos = list(isos.values())
    if not isinstance(isos, list):
        return empty
    return {
        "total": len(isos),
        "enabled": sum(1 for i in isos if isinstance(i, dict) and i.get("enabled")),
        "mounted": sum(1 for i in isos if isinstance(i, dict) and i.get("mounted")),
        "unknown": sum(1 for i in isos if isinstance(i, dict) and i.get("type") in (None, "", "unknown")),
        "last_scan": data.get("last_scan") if isinstance(data, dict) else None,
    }


# ---------------------------------------------------------------- stato completo
def status():
    from . import sources, clients
    cfg = S.load()
    net, lib = cfg["network"], cfg["library"]
    ip = net.get("server_ip", "")
    srv = services_status()
    try:
        src = sources.list_sources()
    except Exception:  # noqa: BLE001
        log.exception("lettura sorgenti fallita")
        src = []
    cat = catalog_summary()
    ipxe = ipxe_status()
    try:
        cl = clients.counts()
    except Exception:  # noqa: BLE001
        cl = {"today": 0, "total": 0}
    running = jobs.running()
    library_count = count_isos(C.LIBRARY_DIR)
    try:
        from . import catalog as _cat
        library_count = max(library_count, sum(1 for e in _cat.load()["isos"].values() if e.get("source") == "local" and not e.get("missing")))
    except Exception:  # noqa: BLE001
        pass
    warnings = []
    if not srv["dnsmasq"]["active"]:
        warnings.append("dnsmasq non attivo: i client PXE non riceveranno risposta")
    if not srv["nginx"]["active"]:
        warnings.append("nginx non attivo: menu e ISO non raggiungibili via HTTP")
    if lib.get("samba_share_enabled", True) and not srv["smbd"]["active"]:
        warnings.append("smbd non attivo: la share della libreria non è disponibile")
    if lib.get("samba_share_enabled", True) and not lib.get("samba_password_set"):
        warnings.append("Nessuna password Samba impostata per la libreria (Impostazioni → Libreria)")
    if not ipxe["built"]:
        warnings.append("iPXE in ricompilazione" if ipxe["building"] else "iPXE non compilato: eseguire la ricompilazione")
    if not src and library_count == 0:
        warnings.append("Nessuna sorgente ISO configurata e libreria locale vuota")
    for s in src:
        if s.get("error"):
            warnings.append(f"Sorgente '{s['name']}': {s['error']}")
        elif not s.get("mounted"):
            warnings.append(f"Sorgente '{s['name']}' non montata")
    disk = disk_status()
    if disk["total"] and disk["free"] < 2 * 1024 ** 3:
        warnings.append("Spazio su disco quasi esaurito")
    last_scan = cat.get("last_scan") or max((s.get("last_scan") or "" for s in src), default="") or None
    share = lib.get("samba_share_name", "iso") or "iso"
    return {
        "hostname": socket.gethostname(),
        "version": C.VERSION,
        "server_ip": ip,
        "interface": net.get("interface", ""),
        "dhcp_mode": net.get("dhcp_mode", "proxy"),
        "services": {n: {"active": v["active"], "state": v["state"], "since": v["since"]} for n, v in srv.items()},
        "sources": [{k: s.get(k) for k in ("id", "name", "unc", "mounted", "iso_count", "error")} for s in src],
        "library": {"path": C.LIBRARY_DIR, "iso_count": library_count,
                    "samba_enabled": bool(lib.get("samba_share_enabled", True)),
                    "samba_path": f"\\\\{ip}\\{share}" if ip else "",
                    "samba_password_set": bool(lib.get("samba_password_set")),
                    "web_upload_enabled": bool(lib.get("web_upload_enabled", True))},
        "disk": disk,
        "catalog": {k: cat.get(k, 0) for k in ("total", "enabled", "mounted", "unknown")},
        "ipxe": ipxe,
        "clients": cl,
        "jobs_running": len(running),
        "last_scan": last_scan,
        "warnings": warnings,
    }


# ---------------------------------------------------------------- azioni
def apply(what):
    if what not in ("dnsmasq", "nginx", "samba", "all"):
        raise ValueError("Cosa applicare? dnsmasq, nginx, samba o all")
    return privileged.call("apply", what, timeout=180)


def service_action(name, action):
    if name not in SERVICE_UNITS:
        raise ValueError("Servizio non gestito (dnsmasq, nginx, smbd)")
    if action not in ("start", "stop", "restart"):
        raise ValueError("Azione non valida (start, stop, restart)")
    return privileged.call("service", name, action, timeout=90)


def power(action):
    if action not in ("reboot", "poweroff"):
        raise ValueError("Azione non valida (reboot, poweroff)")
    return privileged.call("power", action, timeout=30)


def interfaces():
    return S.list_interfaces()


def rebuild_ipxe(ip=None):
    """Avvia la compilazione di iPXE (unit transiente via helper) e un job che ne segue l'esito."""
    ip = ip or S.load()["network"].get("server_ip", "")
    if not ip:
        raise ValueError("Indirizzo IP del server non impostato")
    cur = jobs.running("ipxe-build")
    if cur:
        return cur[0]
    marker = os.path.join(C.TFTP_DIR, ".ipxe-build")
    try:
        before = os.path.getmtime(marker)
    except OSError:
        before = 0
    privileged.call("rebuild-ipxe", ip, timeout=60)

    def run(job):
        job.set_progress(0, "compilazione in corso")
        t0 = time.monotonic()
        seen_active = False
        while time.monotonic() - t0 < IPXE_BUILD_TIMEOUT:
            if job.cancelled:
                job.message = "annullato (la compilazione prosegue in background)"
                return
            st = unit_is_active(IPXE_UNIT)
            if st in ("active", "activating"):
                seen_active = True
            elif seen_active or time.monotonic() - t0 > 10:
                break
            job.set_progress(min(95, int((time.monotonic() - t0) / IPXE_BUILD_TIMEOUT * 100)), "compilazione in corso")
            time.sleep(5)
        try:
            after = os.path.getmtime(marker)
        except OSError:
            after = 0
        if after > before:
            job.set_progress(100, f"iPXE compilato per {ip}")
        else:
            raise RuntimeError("compilazione iPXE non riuscita (controllare: journalctl -u pixio-ipxe-build)")
    return jobs.start("ipxe-build", None, run, message=f"Compilazione iPXE per {ip}")
