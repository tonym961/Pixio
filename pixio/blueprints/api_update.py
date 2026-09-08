"""API di aggiornamento del software (git) e del certificato TLS della GUI.

Il servizio è in pixio/services/updater.py; l'aggiornamento vero lo esegue l'helper
(`pixio-helper update`) in una unit systemd separata, così il riavvio del servizio non lo
interrompe. Qui restano solo la lettura dello stato e l'avvio del job.
"""
from flask import Blueprint, jsonify, request

from ..privileged import HelperError
from ..services import updater

bp = Blueprint("api_update", __name__)


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


def _body():
    d = request.get_json(silent=True)
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise ValueError("Corpo JSON non valido")
    return d


@bp.route("/api/update/check")
def update_check():
    return jsonify(updater.check())


@bp.route("/api/update/apply", methods=["POST"])
def update_apply():
    """Avvia l'aggiornamento. Con {force:true} passa sopra a modifiche locali e a "già aggiornato"."""
    job = updater.apply(force=bool(_body().get("force")))
    return jsonify({"ok": True, "job_id": job.id})


@bp.route("/api/system/cert")
def cert():
    return jsonify(updater.cert_info())


@bp.route("/api/system/cert/regenerate", methods=["POST"])
def cert_regenerate():
    """Rigenera il certificato autofirmato; {hostname} opzionale per il nome nel certificato."""
    info = updater.regenerate(str(_body().get("hostname", "") or ""))
    return jsonify({"ok": True, **info})
