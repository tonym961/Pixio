"""Login con password, sessione firmata, CSRF e rate limit (tutto in memoria: 1 worker gunicorn)."""
import hmac
import os
import secrets
import threading
import time

from flask import Blueprint, jsonify, request, session, abort
from werkzeug.security import check_password_hash, generate_password_hash

from . import config as C
from . import settings as S
from .storage import update_json

bp = Blueprint("auth", __name__)

_attempts = {}          # ip -> [timestamps]
_lock = threading.Lock()
MAX_ATTEMPTS = 8
WINDOW = 300            # secondi


def secret_key():
    try:
        with open(C.SECRET_FILE) as f:
            k = f.read().strip()
            if len(k) >= 32:
                return k
    except FileNotFoundError:
        pass
    k = secrets.token_hex(32)
    os.makedirs(C.ETC_DIR, exist_ok=True)
    with open(os.open(C.SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(k)
    return k


def password_is_set():
    return bool(S.load()["auth"].get("password_hash"))


def set_password(pw):
    if len(pw) < 6:
        raise ValueError("La password deve avere almeno 6 caratteri")
    h = generate_password_hash(pw)

    def upd(cfg):
        cfg.setdefault("auth", {})["password_hash"] = h
        return cfg
    update_json(C.CONFIG_FILE, upd, default={})


def verify_password(pw):
    h = S.load()["auth"].get("password_hash", "")
    return bool(h) and check_password_hash(h, pw)


def _rate_limited(ip):
    now = time.time()
    with _lock:
        lst = [t for t in _attempts.get(ip, []) if now - t < WINDOW]
        _attempts[ip] = lst
        return len(lst) >= MAX_ATTEMPTS


def _record_attempt(ip):
    with _lock:
        _attempts.setdefault(ip, []).append(time.time())


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    tok = request.headers.get("X-CSRF-Token") or (request.form.get("csrf") if request.form else None)
    if not tok or not hmac.compare_digest(tok, session.get("csrf", "")):
        abort(403, description="Token CSRF mancante o non valido")


def logged_in():
    return bool(session.get("user"))


@bp.route("/api/auth/status")
def status():
    return jsonify({"logged_in": logged_in(), "password_set": password_is_set(), "csrf": csrf_token()})


@bp.route("/api/auth/login", methods=["POST"])
def login():
    ip = request.remote_addr or "?"
    if _rate_limited(ip):
        return jsonify({"error": "Troppi tentativi. Riprova tra qualche minuto."}), 429
    data = request.get_json(silent=True) or {}
    pw = str(data.get("password", ""))
    if not password_is_set():
        # primo avvio: la prima password impostata diventa quella dell'amministratore
        try:
            set_password(pw)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    elif not verify_password(pw):
        _record_attempt(ip)
        return jsonify({"error": "Password errata"}), 401
    session.clear()
    session["user"] = "admin"
    session.permanent = True
    return jsonify({"ok": True, "csrf": csrf_token()})


@bp.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.route("/api/auth/password", methods=["POST"])
def change_password():
    if not logged_in():
        abort(401)
    check_csrf()
    data = request.get_json(silent=True) or {}
    if not verify_password(str(data.get("current", ""))):
        return jsonify({"error": "Password attuale errata"}), 400
    try:
        set_password(str(data.get("new", "")))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})
