"""Client PXE visti: ricavati dal log di dnsmasq (log-dhcp) e dalle richieste a /boot/<slug>.ipxe.

Oltre all'anagrafica gestisce il Wake-on-LAN (magic packet in broadcast UDP, nessun privilegio)
e l'avvio una tantum (`boot_once`: vale per un solo avvio, ha precedenza su `auto_boot`).

Stato: config.CLIENTS_FILE ({mac: {...}}) e VAR_DIR/clients_state.json (offset/inode del log letto).
"""
import datetime
import fcntl
import logging
import os
import re
import socket
import struct
import threading
import time

from .. import config as C
from .. import settings as S
from ..privileged import HelperError
from ..storage import read_json, write_json, update_json
from .logs import parse_syslog_line, _DNSMASQ_RE, _TXN_RE, _PXE_RE, _TFTP_SENT_RE

log = logging.getLogger("pixio.clients")

ARCHS = {"00000": "bios", "00006": "efi32", "00007": "efi64", "00009": "efi64", "0000b": "arm64", "0000a": "arm32"}
COUNT_MIN_INTERVAL = 60          # secondi: al massimo una "sessione PXE" al minuto per MAC
MAX_READ_PER_POLL = 4 * 1024 * 1024
FIRST_READ_TAIL = 1024 * 1024    # alla prima lettura si parte dagli ultimi 1 MiB
WOL_PORTS = (9, 7)               # discard ed echo: le due porte usate dai firmware per il magic packet
BROADCAST_FALLBACK = "255.255.255.255"
SIOCGIFADDR = 0x8915             # stessi ioctl usati in pixio/settings.py
SIOCGIFNETMASK = 0x891b

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
            "last_seen": ts, "count": 0, "last_entry": None, "auto_boot": None, "boot_once": None, "hw": ""}


def _public(c):
    base = _new_client(c.get("mac", ""), c.get("first_seen"))
    base.update({k: v for k, v in c.items() if k in base})
    base["wol_supported"] = True     # il magic packet si invia comunque: non serve nulla lato server
    return base


# ---------------------------------------------------------------- API
def list_clients():
    d = _load()
    return sorted((_public(c) for c in d.values()), key=lambda c: c.get("last_seen") or "", reverse=True)


def get(mac):
    mac = normalize_mac(mac)
    c = _load().get(mac)
    return _public(c) if c else None


def _check_catalog_slug(slug):
    """La voce deve esistere nel catalogo (import ritardato: catalog importa a sua volta altri servizi)."""
    from . import catalog
    if catalog.get(slug) is None:
        raise ValueError(f"Voce di avvio '{slug}' inesistente nel catalogo")


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
    if "boot_once" in data:
        bo = data.get("boot_once")
        if bo in (None, "", False):
            changes["boot_once"] = None
        else:
            bo = str(bo).strip()
            if not C.SLUG_RE.match(bo):
                raise ValueError("Voce di avvio una tantum non valida")
            _check_catalog_slug(bo)
            changes["boot_once"] = bo
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


# ---------------------------------------------------------------- Wake-on-LAN
def _broadcast_for(iface):
    """Broadcast dell'interfaccia (IP | ~netmask via ioctl). BROADCAST_FALLBACK se non ricavabile."""
    if not iface:
        return BROADCAST_FALLBACK
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            req = struct.pack("256s", str(iface)[:15].encode())
            ip = struct.unpack("!I", fcntl.ioctl(s.fileno(), SIOCGIFADDR, req)[20:24])[0]
            mask = struct.unpack("!I", fcntl.ioctl(s.fileno(), SIOCGIFNETMASK, req)[20:24])[0]
        finally:
            s.close()
    except (OSError, ValueError, struct.error):
        return BROADCAST_FALLBACK
    return socket.inet_ntoa(struct.pack("!I", (ip | (~mask & 0xFFFFFFFF)) & 0xFFFFFFFF))


def broadcast_address():
    """Broadcast dell'interfaccia configurata in impostazioni (network.interface)."""
    try:
        iface = (S.load().get("network") or {}).get("interface") or ""
    except Exception:  # noqa: BLE001 - senza configurazione leggibile si usa il broadcast generico
        iface = ""
    return _broadcast_for(iface)


def magic_packet(mac):
    """6 byte 0xFF seguiti da 16 ripetizioni del MAC."""
    return b"\xff" * 6 + bytes.fromhex(normalize_mac(mac).replace(":", "")) * 16


def wake(mac, broadcast=None):
    """Invia il magic packet in broadcast UDP sulle porte 9 e 7. Ritorna i pacchetti spediti."""
    mac = normalize_mac(mac)
    packet = magic_packet(mac)
    dest = broadcast or broadcast_address()
    sent, errors = 0, []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in WOL_PORTS:
            try:
                s.sendto(packet, (dest, port))
                sent += 1
            except OSError as e:
                errors.append(str(e))
    finally:
        s.close()
    if not sent:
        raise HelperError("Invio del magic packet non riuscito: " + ("; ".join(errors) or "errore di rete"))
    log.info("Wake-on-LAN %s verso %s: %d pacchetti", mac, dest, sent)
    return sent


def wake_many(macs):
    """Risveglio multiplo. Ritorna {mac: bool}: un MAC non valido o un invio fallito valgono False."""
    dest = broadcast_address()
    out = {}
    for m in macs or []:
        try:
            out[normalize_mac(m)] = bool(wake(m, dest))
        except (ValueError, HelperError, OSError) as e:
            out[str(m)] = False
            log.warning("Wake-on-LAN %s non riuscito: %s", m, e)
    return out


# ---------------------------------------------------------------- avvio una tantum
def set_boot_once(mac, slug):
    """Voce valida per il solo avvio successivo (slug, oppure None per annullare)."""
    return update(mac, {"boot_once": slug})


def take_boot_once(mac):
    """Legge la voce una tantum e la azzera. Ritorna lo slug o None."""
    try:
        mac = normalize_mac(mac)
    except ValueError:
        return None
    with _lock:
        c = _load().get(mac)
        if not c or not c.get("boot_once"):
            return None                      # niente da consegnare: nessuna riscrittura del file
        slug = str(c["boot_once"])

        def upd(d):
            d = d if isinstance(d, dict) else {}
            cur = d.get(mac)
            if cur:
                cur["boot_once"] = None
            return d
        update_json(C.CLIENTS_FILE, upd, default={})
    log.info("Avvio una tantum consegnato a %s: %s", mac, slug)
    return slug


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
        # tetto: l'endpoint e' pubblico, un client ostile non deve far crescere il file all'infinito
        MAX_CLIENTS = 500
        if len(d) > MAX_CLIENTS:
            victims = sorted((k for k, v in d.items() if k != mac and not v.get("name") and not v.get("auto_boot")),
                             key=lambda k: d[k].get("last_seen") or "")
            for k in victims[: len(d) - MAX_CLIENTS]:
                d.pop(k, None)
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
