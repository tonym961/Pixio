"""Sorgenti ISO remote (share Windows/SMB montate in CIFS, sola lettura).

I metadati NON sensibili stanno in config.VAR_DIR/sources.json (leggibile dall'app).
Password e definizione usata per il mount le scrive l'helper in /etc/pixio/sources/<id>.{json,cred}
(root, 0600): l'app non può rileggerle.
"""
import os
import re
import threading
import time
import unicodedata

from .. import config as C
from .. import privileged
from ..privileged import HelperError
from ..storage import read_json, update_json

UNC_RE = re.compile(r"^//[A-Za-z0-9._-]+(/[^/\\\x00-\x1f\"';`*?<>|:]+)+$")     # stessa regola dell'helper
VERS_RE = re.compile(r"^(1\.0|2\.0|2\.1|3\.0|3\.02|3\.1\.1|3)$")
META_FIELDS = ("name", "unc", "domain", "username", "vers", "last_scan", "iso_count", "error")

_mounts_cache = {"ts": 0.0, "data": None}
_mounts_lock = threading.Lock()
MOUNTS_TTL = 3.0


def sources_file():
    return os.path.join(C.VAR_DIR, "sources.json")


def _load():
    d = read_json(sources_file(), {})
    return d if isinstance(d, dict) else {}


def normalize_unc(unc):
    """Accetta \\\\server\\share\\dir o //server/share/dir e ritorna la forma //server/share/dir."""
    u = str(unc or "").strip().replace("\\", "/")
    u = re.sub(r"/{3,}", "//", u).rstrip("/")
    if u and not u.startswith("//"):
        u = "//" + u.lstrip("/")
    return u


def slugify(name):
    """Id sorgente dal nome: minuscole, [a-z0-9_-], max 32."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9_-]+", "-", s.lower()).strip("-_")
    s = s[:32].strip("-_")
    if not s:
        s = "sorgente"
    return s


def _unique_id(base, existing):
    sid = base
    n = 2
    while sid in existing:
        suffix = f"-{n}"
        sid = base[:32 - len(suffix)] + suffix
        n += 1
    return sid


def _validate(data, current=None):
    """Valida/normalizza i campi ricevuti dalla GUI. Ritorna solo i campi presenti in `data`."""
    out = {}
    if "name" in data or current is None:
        name = str(data.get("name", "") or "").strip()
        if not name:
            raise ValueError("Nome della sorgente mancante")
        if len(name) > 64:
            raise ValueError("Nome troppo lungo (max 64 caratteri)")
        out["name"] = name
    if "unc" in data or current is None:
        unc = normalize_unc(data.get("unc", ""))
        if not UNC_RE.match(unc):
            raise ValueError("Percorso UNC non valido (atteso \\\\server\\condivisione[\\cartella])")
        out["unc"] = unc
    for k in ("domain", "username"):
        if k in data:
            v = str(data.get(k) or "").strip()
            if any(c in v for c in "\n\r\x00"):
                raise ValueError("Caratteri non ammessi nelle credenziali")
            if len(v) > 128:
                raise ValueError(f"Campo {k} troppo lungo")
            out[k] = v
    if "vers" in data:
        v = str(data.get("vers") or "").strip()
        if v and not VERS_RE.match(v):
            raise ValueError("Versione SMB non valida (es. 3.0, 2.1, 1.0 o vuota = automatica)")
        out["vers"] = v
    if "password" in data:
        pw = str(data.get("password") or "")
        if any(c in pw for c in "\n\r\x00"):
            raise ValueError("Caratteri non ammessi nella password")
        out["password"] = pw
    return out


def mounted_map(force=False):
    """{sid: bool} dai mount reali (via helper). In caso di errore dell'helper: nessuna sorgente montata."""
    with _mounts_lock:
        now = time.monotonic()
        if not force and _mounts_cache["data"] is not None and now - _mounts_cache["ts"] < MOUNTS_TTL:
            return dict(_mounts_cache["data"])
        # /proc/self/mounts non richiede privilegi: niente sudo a ogni richiesta di stato
        data = {}
        prefix = C.SOURCES_MOUNT_DIR.rstrip("/") + "/"
        try:
            with open("/proc/self/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) > 2 and parts[1].startswith(prefix) and parts[2] in ("cifs", "smb3"):
                        sid = parts[1][len(prefix):].split("/")[0]
                        data[sid] = True
        except OSError:
            data = {}
        _mounts_cache.update(ts=now, data=data)
        return dict(data)


def invalidate_mounts():
    with _mounts_lock:
        _mounts_cache["data"] = None


def _public(sid, meta, mounted):
    return {
        "id": sid,
        "name": meta.get("name", sid),
        "unc": meta.get("unc", ""),
        "domain": meta.get("domain", ""),
        "username": meta.get("username", ""),
        "vers": meta.get("vers", ""),
        "mounted": bool(mounted.get(sid, False)),
        "iso_count": int(meta.get("iso_count") or 0),
        "last_scan": meta.get("last_scan"),
        "error": meta.get("error") or None,
    }


def list_sources():
    d = _load()
    mounted = mounted_map() if d else {}
    return [_public(sid, meta, mounted) for sid, meta in sorted(d.items(), key=lambda kv: kv[1].get("name", kv[0]).lower())]


def get(sid):
    meta = _load().get(sid)
    if meta is None:
        return None
    return _public(sid, meta, mounted_map())


def exists(sid):
    return sid in _load()


def mountpoint(sid):
    return os.path.join(C.SOURCES_MOUNT_DIR, sid)


def is_mounted(sid):
    return bool(mounted_map().get(sid, False))


def _write_helper(sid, meta, password):
    privileged.call_json("write-source", sid, payload={
        "unc": meta.get("unc", ""), "domain": meta.get("domain", ""),
        "username": meta.get("username", ""), "password": password or "", "vers": meta.get("vers", ""),
    })


def create(data):
    """Crea la sorgente: id dal nome, definizione+password via helper, metadati in sources.json."""
    fields = _validate(data or {})
    d = _load()
    sid = _unique_id(slugify(fields["name"]), d)
    if not C.SOURCE_ID_RE.match(sid):
        raise ValueError("Impossibile derivare un identificativo valido dal nome")
    meta = {
        "name": fields["name"], "unc": fields["unc"], "domain": fields.get("domain", ""),
        "username": fields.get("username", ""), "vers": fields.get("vers", ""),
        "last_scan": None, "iso_count": 0, "error": None,
    }
    # senza username (e password) la sorgente è ad accesso guest
    _write_helper(sid, meta, fields.get("password", ""))

    def upd(cur):
        cur = cur if isinstance(cur, dict) else {}
        cur[sid] = meta
        return cur
    update_json(sources_file(), upd, default={})
    return _public(sid, meta, mounted_map(force=True))


def update(sid, data):
    """Aggiorna i metadati; se cambiano i dati di connessione riscrive la definizione tramite helper.

    Password vuota/assente = invariata. L'app non può rileggere la password salvata: quindi, se
    cambiano unc/username/dominio/versione e non viene fornita una password (e la sorgente non è
    guest), si chiede all'utente di reinserirla.
    """
    d = _load()
    cur = d.get(sid)
    if cur is None:
        raise KeyError(sid)
    fields = _validate(data or {}, current=cur)
    password = fields.pop("password", "")
    new = dict(cur)
    new.update({k: v for k, v in fields.items() if k in ("name", "unc", "domain", "username", "vers")})
    conn_changed = any(new.get(k, "") != cur.get(k, "") for k in ("unc", "domain", "username", "vers"))
    if password or conn_changed:
        guest = not new.get("username")
        if not password and not guest:
            raise ValueError("Per modificare server, utente, dominio o versione SMB reinserisci la password")
        _write_helper(sid, new, password)
        new["error"] = None

    def upd(cur_all):
        cur_all = cur_all if isinstance(cur_all, dict) else {}
        cur_all[sid] = new
        return cur_all
    update_json(sources_file(), upd, default={})
    return _public(sid, new, mounted_map(force=True))


def set_meta(sid, **fields):
    """Aggiorna campi di stato (last_scan, iso_count, error) — usato da catalogo e background."""
    allowed = {k: v for k, v in fields.items() if k in META_FIELDS}

    def upd(cur_all):
        cur_all = cur_all if isinstance(cur_all, dict) else {}
        if sid in cur_all:
            cur_all[sid].update(allowed)
        return cur_all
    update_json(sources_file(), upd, default={})


def delete(sid):
    if sid not in _load():
        raise KeyError(sid)
    privileged.call("remove-source", sid, timeout=120)

    def upd(cur_all):
        cur_all = cur_all if isinstance(cur_all, dict) else {}
        cur_all.pop(sid, None)
        return cur_all
    update_json(sources_file(), upd, default={})
    invalidate_mounts()


def test(sid):
    """Prova la connessione con smbclient (senza montare). Ritorna {ok, output:[str]}."""
    if sid not in _load():
        raise KeyError(sid)
    res = privileged.call("test-cifs", sid, timeout=60)
    return {"ok": bool(res.get("ok")), "output": list(res.get("output") or [])}


def mount(sid):
    if sid not in _load():
        raise KeyError(sid)
    try:
        res = privileged.call("mount-cifs", sid, timeout=90)
        set_meta(sid, error=None)
    except HelperError as e:
        set_meta(sid, error=str(e))
        raise
    finally:
        invalidate_mounts()
    return res


def umount(sid):
    if sid not in _load():
        raise KeyError(sid)
    try:
        return privileged.call("umount-cifs", sid, timeout=90)
    finally:
        invalidate_mounts()


def mount_all():
    """Monta tutte le sorgenti note. Ritorna {sid: 'ok' | messaggio di errore}."""
    res = {}
    for sid in list(_load().keys()):
        try:
            mount(sid)
            res[sid] = "ok"
        except HelperError as e:
            res[sid] = str(e)
    return res


def remount_missing():
    """Rimonta le sorgenti che risultano smontate (chiamata periodica dal background)."""
    d = _load()
    if not d:
        return {}
    mounted = mounted_map(force=True)
    res = {}
    for sid in d:
        if not mounted.get(sid):
            try:
                mount(sid)
                res[sid] = "ok"
            except HelperError as e:
                res[sid] = str(e)
    return res
