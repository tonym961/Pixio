"""Client dell'helper privilegiato. L'app gira come utente 'pixio'; le operazioni root passano da sudo."""
import json
import os
import subprocess

from . import config as C


class HelperError(Exception):
    pass


def call(*args, stdin_text=None, timeout=180):
    """Esegue pixio-helper <args...>. Ritorna il dict JSON; solleva HelperError se error/exit!=0."""
    cmd = [C.HELPER, *[str(a) for a in args]]
    if os.geteuid() != 0:
        cmd = ["sudo", "-n", *cmd]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, input=stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HelperError(f"timeout eseguendo {' '.join(args[:2])}")
    except FileNotFoundError:
        raise HelperError("helper non installato (eseguire install.sh)")
    txt = (p.stdout or "").strip()
    try:
        data = json.loads(txt.splitlines()[-1]) if txt else {}
    except (json.JSONDecodeError, IndexError):
        data = {}
    if p.returncode != 0 or (isinstance(data, dict) and data.get("error")):
        msg = (data.get("error") if isinstance(data, dict) else None) or p.stderr.strip() or f"exit {p.returncode}"
        raise HelperError(msg)
    return data


def call_json(*args, payload=None, timeout=180):
    return call(*args, stdin_text=json.dumps(payload or {}), timeout=timeout)
