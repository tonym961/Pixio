"""Lettura unificata dei log: dnsmasq (file), nginx (access log di Pixio) e app (journal).

Ogni riga diventa {ts (ISO 8601), source, level, msg}. Il cursore è il timestamp dell'ultima
riga restituita: la chiamata successiva ritorna solo le righe con ts > cursor.
"""
import datetime
import json
import os
import re
import subprocess
import threading
import time

from .. import config as C

NGINX_ACCESS_LOG = "/var/log/nginx/pixio-access.log"
APP_UNIT = "pixio.service"
TAIL_BYTES = 512 * 1024
JOURNAL_LINES = 300
SOURCES = ("dnsmasq", "nginx", "pixio")

MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}

_SYSLOG_RE = re.compile(r"^(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}) (?P<h>\d\d):(?P<m>\d\d):(?P<s>\d\d) (?P<rest>.*)$")
_DNSMASQ_RE = re.compile(r"^(?:\S+ )?dnsmasq(?:-(?P<sub>dhcp|tftp|dns|script))?\[\d+\]: (?P<body>.*)$")
_TXN_RE = re.compile(r"^(?P<txn>\d+) (?P<msg>.*)$")
_PXE_RE = re.compile(r"^PXE\((?P<iface>[^)]+)\) (?:(?P<ip>\d+\.\d+\.\d+\.\d+) )?(?P<mac>(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}) (?P<what>.+)$")
_TFTP_SENT_RE = re.compile(r"^sent (?P<path>\S+) to (?P<ip>\S+)$")
_TFTP_FAIL_RE = re.compile(r"^(?:failed sending|error \d+ .* to|file .* not found for) ?(?P<rest>.*)$")
_NGINX_RE = re.compile(r'^(?P<ip>\S+) \[(?P<ts>[^\]]+)\] "(?P<req>[^"]*)" (?P<status>\d{3}) (?P<bytes>\d+|-) "(?P<ua>[^"]*)"')

_journal_cache = {"ts": 0.0, "lines": []}
_journal_lock = threading.Lock()
JOURNAL_TTL = 2.0


# ---------------------------------------------------------------- utilità timestamp
def _fmt(dt):
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat(timespec="seconds")


def syslog_ts_to_iso(mon, day, h, m, s, now=None):
    """Timestamp syslog (senza anno) -> ISO locale. Se cade nel futuro è dell'anno scorso."""
    now = now or datetime.datetime.now()
    month = MONTHS.get(mon)
    if not month:
        return None
    try:
        dt = datetime.datetime(now.year, month, int(day), int(h), int(m), int(s))
    except ValueError:
        return None
    if dt > now + datetime.timedelta(days=1):
        try:
            dt = dt.replace(year=now.year - 1)
        except ValueError:
            return None
    return _fmt(dt.astimezone())


def parse_syslog_line(line, now=None):
    """Ritorna (ts_iso, resto) oppure (None, line)."""
    m = _SYSLOG_RE.match(line)
    if not m:
        return None, line
    ts = syslog_ts_to_iso(m.group("mon"), m.group("day"), m.group("h"), m.group("m"), m.group("s"), now)
    return ts, m.group("rest")


def _iso_norm(s):
    """Normalizza un ISO 8601 qualsiasi nel formato usato qui (ordinabile come stringa)."""
    try:
        dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return _fmt(dt.astimezone())


# ---------------------------------------------------------------- lettura file
def tail_lines(path, max_bytes=TAIL_BYTES):
    """Ultime righe complete di un file (al massimo max_bytes letti)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - max_bytes)
            f.seek(start)
            data = f.read()
    except OSError:
        return []
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()
    if start > 0 and lines:
        lines = lines[1:]      # la prima riga è probabilmente tagliata
    return lines


# ---------------------------------------------------------------- dnsmasq
def _dnsmasq_level(msg):
    low = msg.lower()
    if any(k in low for k in ("failed", "error", "cannot", "unable", "refus")):
        return "error"
    if any(k in low for k in ("not found", "no address", "warning", "ignor", "no response")):
        return "warn"
    return "info"


def humanize_dnsmasq(body, sub=None):
    """Rende leggibile il corpo di una riga dnsmasq (senza prefisso 'dnsmasq-dhcp[pid]: ')."""
    m = _TXN_RE.match(body)
    msg = m.group("msg") if m else body
    if sub == "tftp":
        ms = _TFTP_SENT_RE.match(msg)
        if ms:
            return f"TFTP: inviato {os.path.basename(ms.group('path'))} a {ms.group('ip')}"
        return f"TFTP: {msg}"
    mp = _PXE_RE.match(msg)
    if mp:
        ip = f" ({mp.group('ip')})" if mp.group("ip") else ""
        return f"PXE {mp.group('mac').lower()}{ip} → {mp.group('what')}"
    return msg


def read_dnsmasq():
    path = os.path.join(C.LOG_DIR, "dnsmasq.log")
    out = []
    now = datetime.datetime.now()
    for line in tail_lines(path):
        ts, rest = parse_syslog_line(line, now)
        if ts is None:
            continue
        md = _DNSMASQ_RE.match(rest)
        if md:
            msg = humanize_dnsmasq(md.group("body"), md.group("sub"))
        else:
            msg = rest
        out.append({"ts": ts, "source": "dnsmasq", "level": _dnsmasq_level(msg), "msg": msg})
    return out


# ---------------------------------------------------------------- nginx
def read_nginx():
    out = []
    for line in tail_lines(NGINX_ACCESS_LOG):
        m = _NGINX_RE.match(line)
        if not m:
            continue
        ts = _iso_norm(m.group("ts"))
        if ts is None:
            continue
        status = int(m.group("status"))
        level = "error" if status >= 500 else ("warn" if status >= 400 else "info")
        req = m.group("req")
        parts = req.split(" ")
        req_short = " ".join(parts[:2]) if len(parts) >= 2 else req
        ua = m.group("ua")
        ua_s = f" [{ua[:60]}]" if ua and ua != "-" else ""
        out.append({"ts": ts, "source": "nginx", "level": level,
                    "msg": f"{m.group('ip')} {req_short} → {status}{ua_s}"})
    return out


# ---------------------------------------------------------------- journal dell'app
def _journal_msg(v):
    if isinstance(v, list):          # journalctl codifica i messaggi non-UTF8 come array di byte
        try:
            return bytes(int(b) for b in v).decode("utf-8", "replace")
        except (TypeError, ValueError):
            return str(v)
    return "" if v is None else str(v)


def read_journal():
    with _journal_lock:
        now = time.monotonic()
        if now - _journal_cache["ts"] < JOURNAL_TTL:
            return list(_journal_cache["lines"])
        out = []
        try:
            p = subprocess.run(["journalctl", "-u", APP_UNIT, "-o", "json", "--no-pager", "-n", str(JOURNAL_LINES)],
                               capture_output=True, text=True, timeout=10)
            raw = p.stdout if p.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            raw = ""
        for line in raw.splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                us = int(d.get("__REALTIME_TIMESTAMP", 0))
            except (TypeError, ValueError):
                continue
            if not us:
                continue
            ts = _fmt(datetime.datetime.fromtimestamp(us / 1e6).astimezone())
            try:
                prio = int(d.get("PRIORITY", 6))
            except (TypeError, ValueError):
                prio = 6
            msg = _journal_msg(d.get("MESSAGE"))
            level = "error" if prio <= 3 else ("warn" if prio == 4 else "info")
            low = msg.lower()
            if level == "info" and (" error " in low or "traceback" in low or low.startswith("error")):
                level = "error"
            elif level == "info" and ("warning" in low or " warn " in low):
                level = "warn"
            out.append({"ts": ts, "source": "pixio", "level": level, "msg": msg})
        _journal_cache.update(ts=now, lines=out)
        return list(out)


# ---------------------------------------------------------------- API
def read(source="all", cursor=None, limit=200):
    """Righe unificate ordinate per ts. Ritorna {lines, cursor}."""
    source = source or "all"
    if source not in SOURCES and source != "all":
        raise ValueError("Sorgente log non valida (all|dnsmasq|nginx|pixio)")
    try:
        limit = max(1, min(2000, int(limit)))
    except (TypeError, ValueError):
        limit = 200
    lines = []
    if source in ("all", "dnsmasq"):
        lines += read_dnsmasq()
    if source in ("all", "nginx"):
        lines += read_nginx()
    if source in ("all", "pixio"):
        lines += read_journal()
    lines.sort(key=lambda l: l["ts"])
    # cursore "ts|n": n = righe con quel timestamp gia' restituite (righe scritte nello stesso secondo dopo il poll)
    cur, cur_n = None, 0
    if cursor:
        ts_part, sep, n_part = str(cursor).rpartition("|")
        if not sep:
            ts_part, n_part = str(cursor), None       # cursore "nudo" (solo ts): righe strettamente successive
        cur = _iso_norm(ts_part)
        cur_n = int(n_part) if (n_part or "").isdigit() else None
    if cur:
        same = [l for l in lines if l["ts"] == cur]
        if cur_n is None:
            cur_n = len(same)
        lines = same[cur_n:] + [l for l in lines if l["ts"] > cur]
    all_lines = lines
    if len(lines) > limit:
        cut = len(lines) - limit
        # non spezzare un gruppo di righe con lo stesso timestamp
        first_ts = lines[cut]["ts"]
        while cut > 0 and lines[cut - 1]["ts"] == first_ts:
            cut -= 1
        lines = lines[cut:]
    if lines:
        last_ts = lines[-1]["ts"]
        n_same = sum(1 for l in all_lines if l["ts"] == last_ts) + (cur_n if cur == last_ts else 0)
        new_cursor = f"{last_ts}|{n_same}"
    else:
        new_cursor = f"{cur}|{cur_n}" if cur else cursor
    return {"lines": lines, "cursor": new_cursor}
