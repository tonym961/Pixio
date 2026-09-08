"""API di backup e ripristino della configurazione (archivio .tar.gz).

Scaricamento: GET /api/backup. Ripristino: POST /api/backup/restore con il file in multipart
(campo 'file') oppure con il corpo binario della richiesta.
"""
from flask import Blueprint, Response, jsonify, request

from ..privileged import HelperError
from ..services import backup

bp = Blueprint("api_backup", __name__)


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


@bp.route("/api/backup")
def download():
    data = backup.export_archive()
    resp = Response(data, mimetype="application/gzip")
    resp.headers["Content-Disposition"] = f'attachment; filename="{backup.archive_name()}"'
    resp.headers["Content-Length"] = str(len(data))
    return resp


@bp.route("/api/backup/restore", methods=["POST"])
def restore():
    limit = backup.MAX_ARCHIVE
    f = request.files.get("file")
    if f is not None:
        data = f.read(limit + 1)
    else:
        if request.content_length and request.content_length > limit:
            raise ValueError(f"Archivio troppo grande (massimo {limit >> 20} MB)")
        data = request.get_data(cache=False)
    if not data:
        raise ValueError("Nessun file di backup ricevuto")
    res = backup.restore(data)
    return jsonify({"ok": True, "restored": res["restored"], "warnings": res["warnings"]})
