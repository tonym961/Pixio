"""Aggiornamento di Pixio dal repository git e certificato TLS della GUI (sezioni 6 e 7 del contratto).

check() guarda solo: il commit locale, quello sul server (git ls-remote) e se ci sono modifiche
non salvate. Non tocca il repository (niente fetch, niente scritture) e usa timeout brevi con
GIT_TERMINAL_PROMPT=0 / BatchMode=yes, così una credenziale mancante fa fallire subito il comando
invece di bloccare la GUI.

apply() avvia un job che chiede all'helper `pixio-helper update`: l'helper lancia una unit systemd
transiente ("pixio-update") che esegue `git pull --ff-only` e poi install.sh. Il job segue lo stato
con `update-status`. install.sh riavvia pixio.service: il processo che ospita il job muore insieme
alla GUI e al riavvio quel job risulta "interrotto" — è normale, l'esito vero si legge con
`journalctl -u pixio-update` oppure ricontrollando /api/update/check.
"""
import datetime
import logging
import os
import re
import subprocess
import time

from .. import config as C
from .. import privileged
from .. import settings as S
from ..privileged import HelperError
from . import jobs

log = logging.getLogger("pixio.updater")

GIT_TIMEOUT = 15                  # comandi git locali
REMOTE_TIMEOUT = 20               # ls-remote verso il server
UPDATE_TIMEOUT = 45 * 60          # attesa massima della unit di aggiornamento
POLL = 5                          # intervallo di polling di update-status
START_GRACE = 20                  # attesa prima di dare per non partita la unit
HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                         r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _env():
    """Ambiente per git: mai una richiesta interattiva di credenziali."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    env["SSH_ASKPASS"] = ""
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=10"
    env["LC_ALL"] = "C"
    return env


def _git(*args, timeout=GIT_TIMEOUT):
    """Esegue git nella cartella del codice. Ritorna (returncode, stdout, stderr) senza sollevare."""
    cmd = ["git", "-C", C.CODE_DIR, *[str(a) for a in args]]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_env())
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout ({timeout}s) eseguendo git {args[0]}"
    except OSError as e:
        return 127, "", str(e)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def is_repo():
    return os.path.isdir(os.path.join(C.CODE_DIR, ".git"))


# ---------------------------------------------------------------- controllo aggiornamenti
def check():
    """Stato dell'aggiornamento: commit locale, commit remoto, quanti commit indietro, modifiche locali."""
    info = {"current": "", "current_full": "", "remote": "", "remote_full": "", "branch": "",
            "behind": 0, "dirty": False, "can_update": False, "repo": is_repo(),
            "checked": _now(), "error": ""}
    if not info["repo"]:
        info["error"] = f"{C.CODE_DIR} non è un repository git: aggiornamento dalla GUI non disponibile"
        return info

    rc, head, err = _git("rev-parse", "HEAD")
    if rc != 0:
        info["error"] = f"git non utilizzabile: {err or 'errore sconosciuto'}"
        return info
    info["current_full"] = head
    info["current"] = head[:7]

    rc, branch, _ = _git("rev-parse", "--abbrev-ref", "HEAD")
    info["branch"] = branch if rc == 0 else ""

    rc, status, err = _git("status", "--porcelain")
    if rc == 0:
        info["dirty"] = any(l.strip() for l in status.splitlines())
    else:
        info["error"] = f"git status fallito: {err}"
        return info

    if not info["branch"] or info["branch"] == "HEAD":
        info["error"] = "il repository non è su un ramo (HEAD staccato): aggiornamento non automatico"
        return info

    rc, upstream, _ = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if rc == 0 and "/" in upstream:
        remote_name, _, remote_branch = upstream.partition("/")
    else:
        remote_name, remote_branch = "origin", info["branch"]

    rc, ls, err = _git("ls-remote", "--heads", remote_name, remote_branch, timeout=REMOTE_TIMEOUT)
    if rc != 0:
        info["error"] = f"impossibile contattare il server git ({remote_name}): {err or 'errore sconosciuto'}"
        return info
    line = next((l for l in ls.splitlines() if l.strip()), "")
    remote_full = line.split("\t")[0].strip() if line else ""
    if not re.match(r"^[0-9a-f]{7,64}$", remote_full):
        info["error"] = f"ramo {remote_branch} non trovato su {remote_name}"
        return info
    info["remote_full"] = remote_full
    info["remote"] = remote_full[:7]

    if remote_full == info["current_full"]:
        info["behind"] = 0
    else:
        rc, cnt, _ = _git("rev-list", "--count", f"HEAD..{remote_full}")
        if rc == 0 and cnt.isdigit():
            info["behind"] = int(cnt)
        else:
            # il commit remoto non è ancora nel repository locale: c'è comunque un aggiornamento
            info["behind"] = 1
    info["can_update"] = info["behind"] > 0 and not info["dirty"]
    return info


# ---------------------------------------------------------------- esecuzione dell'aggiornamento
def status():
    """Stato della unit di aggiornamento (helper `update-status`)."""
    return privileged.call("update-status", timeout=90)


def apply(force=False):
    """Avvia l'aggiornamento in un job. Ritorna il job (quello già in corso, se c'è)."""
    cur = jobs.running("update")
    if cur:
        return cur[0]
    st = check()
    if st["error"] and not force:
        raise ValueError(st["error"])
    if st["dirty"] and not force:
        raise ValueError(f"Ci sono modifiche locali in {C.CODE_DIR}: aggiornamento non possibile")
    if not st["behind"] and not force:
        raise ValueError("Nessun aggiornamento disponibile")

    def run(job):
        job.set_progress(0, "avvio dell'aggiornamento")
        privileged.call(*(["update", "--force"] if force else ["update"]), timeout=120)
        job.log("unit pixio-update avviata (git pull + install.sh)")
        t0 = time.monotonic()
        seen_active = False
        last = {}
        while time.monotonic() - t0 < UPDATE_TIMEOUT:
            if job.cancelled:
                job.message = "annullato (l'aggiornamento prosegue in background)"
                return
            time.sleep(POLL)
            try:
                last = status()
            except HelperError as e:
                job.log(f"stato non disponibile: {e}")
                continue
            if last.get("running"):
                seen_active = True
                job.set_progress(min(95, int((time.monotonic() - t0) / UPDATE_TIMEOUT * 100)),
                                 "aggiornamento in corso")
                continue
            if seen_active or time.monotonic() - t0 > START_GRACE:
                break
        for line in (last.get("log") or [])[-20:]:
            job.log(line)
        result = str(last.get("result") or "")
        exit_status = str(last.get("exit_status") or "0")
        if (result and result != "success") or exit_status not in ("", "0"):
            raise RuntimeError(f"aggiornamento non riuscito ({result or 'exit ' + exit_status}): "
                               "controllare il registro con journalctl -u pixio-update")
        job.set_progress(100, "aggiornamento completato")

    msg = f"Aggiornamento da {st['current'] or '?'} a {st['remote'] or '?'}"
    return jobs.start("update", None, run, message=msg)


# ---------------------------------------------------------------- certificato TLS
def cert_info():
    """{enabled, exists, subject, not_after, fingerprint} del certificato della GUI."""
    web = S.load().get("web") or {}
    info = {"enabled": bool(web.get("https_enabled", False)),
            "redirect_http": bool(web.get("redirect_http", True)),
            "exists": False, "subject": "", "not_after": None, "days_left": None,
            "fingerprint": "", "path": "", "error": ""}
    try:
        d = privileged.call("cert-info", timeout=60)
    except HelperError as e:
        info["error"] = str(e)
        return info
    info.update(exists=bool(d.get("exists")), subject=str(d.get("subject") or ""),
                not_after=d.get("not_after"), days_left=d.get("days_left"),
                fingerprint=str(d.get("fingerprint") or ""), path=str(d.get("cert") or ""))
    return info


def regenerate(hostname=""):
    """Rigenera il certificato autofirmato (helper `cert <host> --force`) e ne ritorna i dati."""
    host = str(hostname or "").strip().rstrip(".")
    if host and not HOSTNAME_RE.match(host):
        raise ValueError("Nome host non valido")
    args = ["cert", host, "--force"] if host else ["cert", "--force"]
    privileged.call(*args, timeout=180)
    return cert_info()
