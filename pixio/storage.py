"""Storage JSON su file: scrittura atomica + lock fcntl. Un solo processo gunicorn, più thread."""
import fcntl
import json
import os
import tempfile
import threading

_locks = {}
_locks_guard = threading.Lock()


def _tlock(path):
    with _locks_guard:
        if path not in _locks:
            _locks[path] = threading.RLock()
        return _locks[path]


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except FileNotFoundError:
        return {} if default is None else default
    except json.JSONDecodeError:
        return {} if default is None else default


def write_json(path, data, mode=0o640):
    """Scrive in modo atomico (tmp + rename) mantenendo i permessi indicati."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    with _tlock(path):
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def update_json(path, fn, default=None, mode=0o640):
    """Legge, applica fn(data) -> data, riscrive. Serializzato per path."""
    with _tlock(path):
        data = read_json(path, default)
        data = fn(data)
        write_json(path, data, mode)
        return data


def deep_merge(base, override):
    """Merge ricorsivo di dict (override vince). Non modifica gli input."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out
