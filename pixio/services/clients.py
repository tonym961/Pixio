"""Client PXE visti: ricavati dal log di dnsmasq (log-dhcp) e dalle richieste a /boot/<slug>.ipxe.

Stato: config.CLIENTS_FILE ({mac: {...}}) e VAR_DIR/clients_state.json (offset/inode del log letto).
"""
import datetime
import os
import re
import threading
import time

from .. import config as C
from ..storage import read_json, write_json, update_json
from .logs import parse_syslog_line, _DNSMASQ_RE, _TXN_RE, _PXE_RE, _TFTP_SENT_RE

ARCHS = {"00000": "bios", "00006": "efi32", "00007": "efi64", "00009": "efi64", "0000b": "arm64", "0000a": "arm32"}
COUNT_MIN_INTERVAL = 60          # secondi: al massimo una "sessione PXE" al minuto per MAC
MAX_READ_PER_POLL = 4 * 1024 * 1024
FIRST_READ_TAIL = 1024 * 1024    # alla prima lettura si parte dagli ultimi 1 MiB

_lock = threading.RLock()
_txn = {}          # id transazione dnsmasq -> {vendor_class, user_class, ts}
_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
_ARCH_RE = re.compile(r"Arch:([0-9a-fA-F]{5})")
_DHCP_RE = re.compile(r"^(?P<kind>DHCP[A-Z]+)\((?P<iface>[^)]+)\) (?:(?P<ip>\d+\.\d+\.\d+\.\d+) )?(?P<mac>(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})(?: (?P<extra>.*))?$")


def _now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _state_file():
    return os.path.join(C.VAR_DIR, "clients_state.json")


def _log_file():
    return os.path.join(C.LOG_DIR, "dnsmasq.log")


def normalize_mac(mac):
    """'D8-BB-C1-4A-20-7E', 'd8bbc14a207e', 'd8:bb:...' -> 'd8:bb:c1:4a:20:7e'. ValueError se non valido."""
    s = re.sub(r"[^0-9a-fA-F]", "", str(mac or "")).lower()
    if len(s) != 12:
        raise ValueError("Indirizzo MAC non valido")
    m = ":".join(s[i:i + 2] for i in range(0, 12, 2))
    if not _MAC_RE.match(m):
        raise ValueError("Indirizzo MAC non valido")
    return m


def _load():
    d = read_json(C.CLIENTS_FILE, {})
    return d if isinstance(d, dict) else {}


def _new_client(mac, ts):
    return {"mac": mac, "ip": None, "arch": "?", "vendor_class": None, "name": "", "first_seen": ts,
            "last_seen": ts, "count": 0, "last_entry": None, "auto_boot": None, "hw": ""}


def _public(c):
    base = _new_client(c.get("mac", ""), c.get("first_seen"))
    base.update({k: v for k, v in c.items() if k in base})
    return base


# ---------------------------------------------------------------- API
def list_clients():
    d = _load()
    return sorted((_public(c) for c in d.values()), key=lambda c: c.get("last_seen") or "", reverse=True)


def get(mac):
    mac = normalize_mac(mac)
    c = _load().get(mac)
    return _public(c) if c else None


def update(mac, data):
    mac = normalize_mac(mac)
    data = data or {}
    changes = {}
    if "name" in data:
        name = str(data.get("name") or "").strip()
        if len(name) > 64:
            raise ValueError("Nome troppo lungo (max 64 caratteri)")
        changes["name"] = name
    if "auto_boot" in data:
        ab = data.get("auto_boot")
        if ab in (None, "", False):
            changes["auto_boot"] = None
        else:
            ab = str(ab).strip()
            if not C.SLUG_RE.match(ab):
                raise ValueError("Voce di avvio automatico non valida")
            changes["auto_boot"] = ab
    result = {}

    def upd(d):
        d = d if isinstance(d, dict) else {}
        c = d.get(mac)
        if c is None:
            raise KeyError(mac)
        c.update(changes)
        result.update(c)
        return d
    with _lock:
        update_json(C.CLIENTS_FILE, upd, default={})
    return _public(result)


def delete(mac):
    mac = normalize_mac(mac)
    found = []

    def upd(d):
        d = d if isinstance(d, dict) else {}
        if d.pop(mac, None) is not None:
            found.append(mac)
        return d
    with _lock:
        update_json(C.CLIENTS_FILE, upd, default={})
    if not found:
        raise KeyError(mac)


def record_boot(mac, slug, ip=None):
    """Il client ha richiesto /boot/<slug>.ipxe: aggiorna last_entry/last_seen (crea il client se serve)."""
    mac = normalize_mac(mac)
    ts = _now_iso()

    def upd(d):
        d = d if isinstance(d, dict) else {}
        c = d.setdefault(mac, _new_client(mac, ts))
        c["last_seen"] = ts
        c["last_entry"] = slug
        if ip:
            c["ip"] = ip
        return d
    with _lock:
        update_json(C.CLIENTS_FILE, upd, default={})


def _arch_from_ipxe(platform, buildarch):
    """Piattaforma/architettura riportate da iPXE (${platform}, ${buildarch}) -> bios|efi32|efi64|arm64."""
    p, b = (platform or "").lower(), (buildarch or "").lower()
    if p == "pcbios":
        return "bios"
    if p == "efi":
        if b in ("arm64", "aarch64"):
            return "arm64"
        if b in ("i386", "x86", "ia32"):
            return "efi32"
        return "efi64"
    return None


def record_seen(mac, ip=None, arch="", platform="", manuf="", product=""):
    """Il client (già in iPXE) ha chiesto il menu: aggiorna last_seen, ip, architettura e hardware."""
    mac = normalize_mac(mac)
    ts = _now_iso()
    a = _arch_from_ipxe(platform, arch)
    hw = " ".join(x.strip() for x in (manuf or "", product or "") if x and x.strip())[:80]

    def upd(d):
        d = d if isinstance(d, dict) else {}
        c = d.setdefault(mac, _new_client(mac, ts))
        c["last_seen"] = ts
        if ip:
            c["ip"] = ip
        if a:
            c["arch"] = a
        if hw:
            c["hw"] = hw
        return d
    with _lock:
        update_json(C.CLIENTS_FILE, upd, default={})


# ---------------------------------------------------------------- parsing del log dnsmasq
def _arch_from_vendor(vc):
    m = _ARCH_RE.search(vc or "")
    if not m:
        return None
    return ARCHS.get(m.group(1).lower(), "?")


def _txn_get(txn):
    t = _txn.get(txn)
    if t is None:
        if len(_txn) > 2000:
            # tiene solo le transazioni più recenti
            for k in sorted(_txn, key=lambda k: _txn[k]["ts"])[:1000]:
                _txn.pop(k, None)
        t = _txn[txn] = {"vendor_class": None, "user_class": None, "ts": time.monotonic()}
    return t


def _ts_epoch(ts_iso):
    try:
        return datetime.datetime.fromisoformat(ts_iso).timestamp()
    except (TypeError, ValueError):
        return time.time()


def _apply_line(line, clients, state, now):
    """Applica una riga di log a `clients` (dict) e `state`. Ritorna True se ha cambiato qualcosa."""
    ts, rest = parse_syslog_line(line, now)
    if ts is None:
        return False
    md = _DNSMASQ_RE.match(rest)
    if not md:
        return False
    sub, body = md.group("sub"), md.group("body")
    if sub == "tftp":
        ms = _TFTP_SENT_RE.match(body)
        if not ms:
            return False
        ip = ms.group("ip")
        changed = False
        for c in clients.values():
            if c.get("ip") == ip:
                c["last_seen"] = ts
                changed = True
        return changed
    if sub != "dhcp":
        return False
    mt = _TXN_RE.match(body)
    if not mt:
        return False
    txn, msg = mt.group("txn"), mt.group("msg")
    if msg.startswith("vendor class: "):
        _txn_get(txn)["vendor_class"] = msg[len("vendor class: "):].strip()
        return False
    if msg.startswith("user class: "):
        _txn_get(txn)["user_class"] = msg[len("user class: "):].strip()
        return False
    ctx = _txn.get(txn) or {}
    mp = _PXE_RE.match(msg)
    mac = ip = None
    pxe_event = False
    if mp:
        mac, ip, pxe_event = mp.group("mac").lower(), mp.group("ip"), True
    else:
        mdh = _DHCP_RE.match(msg)
        if not mdh:
            return False
        mac, ip = mdh.group("mac").lower(), mdh.group("ip")
    c = clients.get(mac)
    if c is None:
        c = clients[mac] = _new_client(mac, ts)
    c["last_seen"] = ts
    if ip:
        c["ip"] = ip
    vc = ctx.get("vendor_class")
    if vc:
        c["vendor_class"] = vc
        arch = _arch_from_vendor(vc)
        if arch:
            c["arch"] = arch
    if pxe_event:
        last = state.setdefault("count_ts", {}).get(mac, 0)
        ev = _ts_epoch(ts)
        if ev - last >= COUNT_MIN_INTERVAL or ev < last:
            c["count"] = int(c.get("count") or 0) + 1
            state["count_ts"][mac] = ev
    return True


def poll():
    """Legge le nuove righe del log dnsmasq (incrementale, con gestione della rotazione) e aggiorna i client."""
    path = _log_file()
    with _lock:
        state = read_json(_state_file(), {})
        if not isinstance(state, dict):
            state = {}
        try:
            st = os.stat(path)
        except OSError:
            return 0
        offset = int(state.get("offset") or 0)
        inode = state.get("inode")
        if inode is None:
            offset = max(0, st.st_size - FIRST_READ_TAIL)   # prima lettura: solo la coda
        elif inode != st.st_ino or st.st_size < offset:
            offset = 0                                       # file ruotato o troncato
        if offset >= st.st_size:
            if inode != st.st_ino or offset != int(state.get("offset") or 0):
                state.update(inode=st.st_ino, offset=offset)
                write_json(_state_file(), state)
            return 0
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read(MAX_READ_PER_POLL)
        except OSError:
            return 0
        if inode is None and offset > 0:
            nl = data.find(b"\n")
            data = data[nl + 1:] if nl >= 0 else b""
            offset += nl + 1 if nl >= 0 else len(data)
        end = data.rfind(b"\n")
        if end < 0:
            state.update(inode=st.st_ino, offset=offset)
            write_json(_state_file(), state)
            return 0
        chunk = data[:end + 1]
        new_offset = offset + len(chunk)
        lines = chunk.decode("utf-8", "replace").splitlines()
        now = datetime.datetime.now()
        n = 0

        def upd(clients):
            nonlocal n
            clients = clients if isinstance(clients, dict) else {}
            for line in lines:
                try:
                    if _apply_line(line, clients, state, now):
                        n += 1
                except Exception:  # noqa: BLE001 - una riga strana non deve bloccare il parsing
                    continue
            return clients
        update_json(C.CLIENTS_FILE, upd, default={})
        state.update(inode=st.st_ino, offset=new_offset)
        write_json(_state_file(), state)
        return n


def counts():
    """{today, total} per la dashboard."""
    today = datetime.date.today().isoformat()
    cl = _load()
    return {"today": sum(1 for c in cl.values() if str(c.get("last_seen") or "").startswith(today)), "total": len(cl)}
