"""API della copia locale automatica: impostazioni, piano, esecuzione e pulizia della cache.

Il servizio è in pixio/services/autocache.py; le copie vere le fa catalog.set_cache
(job di tipo "copy" visibile in /api/jobs).
"""
from flask import Blueprint, jsonify, request

from .. import config as C
from ..privileged import HelperError
from ..services import autocache

bp = Blueprint("api_cache", __name__)


@bp.errorhandler(ValueError)
def _value_err(e):
    return jsonify({"error": str(e) or "Richiesta non valida"}), 400


@bp.errorhandler(FileExistsError)
def _exists_err(e):
    return jsonify({"error": str(e) or "Esiste già"}), 409


@bp.errorhandler(FileNotFoundError)
def _missing_err(e):
    return jsonify({"error": str(e) or "Non trovato"}), 404


@bp.errorhandler(HelperError)
def _helper_err(e):
    return jsonify({"error": f"Operazione fallita: {e}"}), 500


@bp.route("/api/cache")
def get_cache():
    return jsonify({"settings": autocache.current_settings(), "stats": autocache.stats(), "plan": autocache.plan()})


@bp.route("/api/cache", methods=["POST"])
def set_cache():
    """Salva le impostazioni della copia locale (auto, min_size_gb, only_enabled, keep_free_gb)."""
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        raise ValueError("Corpo JSON non valido")
    incoming = d.get("settings") if isinstance(d.get("settings"), dict) else d
    prima = autocache.current_settings().get("auto")
    settings = autocache.save_settings(incoming)
    # Chi attiva la copia dalla GUI si aspetta che parta adesso, non al giro successivo
    # del controllo periodico: senza questo sembra che l'opzione non funzioni.
    job_id = None
    if settings.get("auto") and not prima:
        job_id = autocache.start().id
    return jsonify({"ok": True, "settings": settings, "job_id": job_id})


@bp.route("/api/cache/run", methods=["POST"])
def run_cache():
    return jsonify({"ok": True, "job_id": autocache.start().id})


@bp.route("/api/cache/clear", methods=["POST"])
def clear_cache():
    """{slug} libera una copia, senza slug le libera tutte."""
    d = request.get_json(silent=True) or {}
    if not isinstance(d, dict):
        raise ValueError("Corpo JSON non valido")
    slug = d.get("slug")
    if slug is not None:
        slug = str(slug)
        if not C.SLUG_RE.match(slug):
            raise ValueError("Slug non valido")
    res = autocache.clear(slug)
    return jsonify({"ok": True, "freed": res["freed"], "bytes": res["bytes"]})
