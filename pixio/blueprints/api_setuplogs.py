"""API dei log del programma di installazione depositati dai PC (docs/API.md, sezione 22).

I file li scrivono i client nella share [pxelog]; qui si leggono e si eliminano soltanto.
Nessuna route pubblica: sono dati di diagnosi, si vedono dalla GUI dopo il login.
"""
from flask import Blueprint, jsonify, request

from .. import settings as S
from ..services import setuplogs

bp = Blueprint("api_setuplogs", __name__)


@bp.errorhandler(ValueError)
def _value_err(e):
    return jsonify({"error": str(e) or "Richiesta non valida"}), 400


@bp.errorhandler(FileNotFoundError)
def _missing_err(e):
    return jsonify({"error": str(e) or "Non trovato"}), 404


@bp.route("/api/setuplogs")
def list_setuplogs():
    cfg = S.load()
    return jsonify({"logs": setuplogs.list_folders(), "enabled": setuplogs.enabled(cfg),
                    "share": setuplogs.share_unc(cfg), "dir": setuplogs.base_dir()})


@bp.route("/api/setuplogs/<name>")
def get_setuplog(name):
    return jsonify(setuplogs.detail(name, request.args.get("file") or None))


@bp.route("/api/setuplogs/<name>", methods=["DELETE"])
def delete_setuplog(name):
    setuplogs.delete(name)
    return jsonify({"ok": True})


@bp.route("/api/setuplogs", methods=["DELETE"])
def delete_all_setuplogs():
    return jsonify({"ok": True, "deleted": setuplogs.delete_all()})
