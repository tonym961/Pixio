"""API upload a chunk (riprendibile): ISO nella libreria locale (kind "iso", default), file driver
in una cartella della libreria driver (kind "driver" + folder, con "path" facoltativo per le sottocartelle)
o file aggiuntivo di una risposta automatica (kind "answer" + folder = id della risposta)."""
from flask import Blueprint, jsonify, request

from .. import settings as S
from ..services import uploads
from ..services.uploads import UploadError

bp = Blueprint("api_upload", __name__)


@bp.errorhandler(UploadError)
def _upload_err(e):
    return jsonify({"error": str(e)}), e.status


def _check_enabled():
    if not S.load().get("library", {}).get("web_upload_enabled", True):
        raise UploadError("Upload dalla web UI disattivato nelle impostazioni", 403)


@bp.route("/api/upload")
def list_uploads():
    return jsonify(uploads.list_uploads())


@bp.route("/api/upload/init", methods=["POST"])
def init():
    _check_enabled()
    d = request.get_json(silent=True) or {}
    path = d.get("path")
    if path is not None and not isinstance(path, str):
        raise UploadError("path: testo atteso (sottopercorso dentro la cartella, es. \"x64/rt.inf\")")
    res = uploads.init(d.get("filename", ""), d.get("size", 0), kind=d.get("kind") or "iso",
                       folder=d.get("folder"), path=path)
    return jsonify(res)


@bp.route("/api/upload/<upload_id>/chunk/<int:n>", methods=["PUT"])
def put_chunk(upload_id, n):
    _check_enabled()
    received = uploads.put_chunk(upload_id, n, request.stream)
    return jsonify({"ok": True, "received": received})


@bp.route("/api/upload/<upload_id>/finish", methods=["POST"])
def finish(upload_id):
    _check_enabled()
    res = uploads.finish(upload_id)
    if res.get("kind") == "driver":
        return jsonify({"ok": True, "kind": "driver", "folder": res.get("folder"), "path": res.get("path"),
                        "extracted": res.get("extracted", 0), "files": res.get("files", [])})
    if res.get("kind") == "answer":
        return jsonify({"ok": True, "kind": "answer", "answer_id": res.get("answer_id"),
                        "folder": res.get("folder"), "files": res.get("files", [])})
    return jsonify({"ok": True, "slug": res.get("slug"), "path": res.get("path"), "job_id": res.get("job_id")})


@bp.route("/api/upload/<upload_id>", methods=["DELETE"])
def discard(upload_id):
    uploads._load(upload_id)     # 404 se non esiste
    uploads.discard(upload_id)
    return jsonify({"ok": True})
