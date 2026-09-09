"""Pixio - app factory Flask. Tutte le route /api/* richiedono login tranne l'allowlist pubblica."""
import datetime
import logging
import os

from flask import Flask, jsonify, request, send_from_directory, session, abort
from werkzeug.exceptions import HTTPException

from . import config as C
from . import auth

# Percorsi raggiungibili senza login (i client PXE non hanno sessione)
PUBLIC_PREFIXES = ("/boot.ipxe", "/boot/", "/answers/", "/api/health", "/api/auth/status", "/api/auth/login", "/static/", "/favicon")


def create_app():
    app = Flask(__name__, static_folder=None)
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)   # nginx e' l'unico proxy davanti a gunicorn
    app.config.update(
        SECRET_KEY=auth.secret_key(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="pixio_session",
        MAX_CONTENT_LENGTH=None,
        JSON_SORT_KEYS=False,
        PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=12),
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @app.before_request
    def _guard():
        p = request.path
        if p == "/" or p == "/index.html" or any(p.startswith(x) for x in PUBLIC_PREFIXES):
            return None
        if not auth.logged_in():
            if p.startswith("/api/"):
                return jsonify({"error": "Accesso non autorizzato"}), 401
            abort(401)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            auth.check_csrf()
        return None

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(HTTPException)
    def _http_err(e):
        if request.path.startswith("/api/") or request.path.startswith("/boot"):
            return jsonify({"error": e.description or e.name}), e.code
        return e

    @app.errorhandler(Exception)
    def _err(e):
        app.logger.exception("errore non gestito")
        if request.path.startswith("/api/"):
            return jsonify({"error": f"Errore interno: {e}"}), 500
        raise e

    @app.route("/")
    @app.route("/index.html")
    def index():
        """La pagina viene servita con la versione dei file statici nell'indirizzo: così dopo un
        aggiornamento il browser non tiene in memoria i vecchi script."""
        import re as _re
        path = os.path.join(C.STATIC_DIR, "index.html")
        try:
            html = open(path, encoding="utf-8").read()
        except OSError:
            return send_from_directory(C.STATIC_DIR, "index.html")

        def stamp(m):
            rel = m.group(2)
            try:
                v = int(os.stat(os.path.join(C.STATIC_DIR, rel)).st_mtime)
            except OSError:
                return m.group(0)
            return f'{m.group(1)}="/static/{rel}?v={v}"'
        html = _re.sub(r'(src|href)="/static/([A-Za-z0-9_.-]+)"', stamp, html)
        return app.response_class(html, mimetype="text/html", headers={"Cache-Control": "no-cache"})

    @app.route("/static/<path:name>")
    def static_files(name):
        return send_from_directory(C.STATIC_DIR, name)

    @app.route("/api/health")
    def health():
        return jsonify({"ok": True, "app": C.APP_NAME, "version": C.VERSION})

    app.register_blueprint(auth.bp)
    from .blueprints import register_all
    register_all(app)
    try:
        from .services import background
        background.start(app)
    except Exception as e:  # noqa
        app.logger.error("scheduler in background non avviato: %s", e)
    return app
