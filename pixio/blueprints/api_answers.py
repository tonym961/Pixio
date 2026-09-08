"""API delle risposte automatiche: elenco, creazione da modello, editor del contenuto, file aggiuntivi.

I file vengono serviti ai client PXE dal blueprint pubblico answers_public.py; l'upload di file
aggiuntivi passa da /api/upload (kind:"answer", folder:"<id risposta>"), vedi api_upload.py.
"""
import socket

from flask import Blueprint, jsonify, request

from .. import settings as S
from ..services import answers

bp = Blueprint("api_answers", __name__)


@bp.errorhandler(ValueError)
def _value_err(e):
    return jsonify({"error": str(e) or "Richiesta non valida"}), 400


@bp.errorhandler(FileExistsError)
def _exists_err(e):
    return jsonify({"error": str(e) or "Esiste già"}), 409


@bp.errorhandler(FileNotFoundError)
def _missing_err(e):
    return jsonify({"error": str(e) or "Non trovato"}), 404


def _server_ip():
    cfg = S.load()
    return (cfg.get("network", {}) or {}).get("server_ip", "") or socket.gethostname()


def _get_or_404(answer_id):
    a = answers.get(answer_id)
    if not a:
        raise FileNotFoundError("Risposta non trovata")
    return a


def _detail(a):
    """Risposta + contenuto del file richiesto (o del principale) + URL pubblici."""
    ip = _server_ip()
    name = request.args.get("file") or a["main_file"]
    out = dict(a)
    out["url"] = answers.public_url(ip, a["id"], name) if name else answers.folder_url(ip, a["id"])
    out["folder_url"] = answers.folder_url(ip, a["id"])
    out["file"] = name
    out["content"] = answers.read_content(a["id"], name) if name else ""
    out["kernel_args"] = answers.kernel_args(a, None, ip)
    return out


@bp.route("/api/answers")
def list_answers():
    return jsonify({"answers": answers.list_answers(), "kinds": answers.kinds_list()})


@bp.route("/api/answers/templates/<kind>")
def get_template(kind):
    return jsonify(answers.template(kind))


@bp.route("/api/answers", methods=["POST"])
def create_answer():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        raise ValueError("Corpo JSON non valido")
    a = answers.create(d)
    return jsonify({"ok": True, "answer": a}), 201


@bp.route("/api/answers/<answer_id>")
def get_answer(answer_id):
    return jsonify(_detail(_get_or_404(answer_id)))


@bp.route("/api/answers/<answer_id>", methods=["PUT"])
def update_answer(answer_id):
    _get_or_404(answer_id)
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        raise ValueError("Corpo JSON non valido")
    if not any(k in d for k in ("name", "note", "content", "filename")):
        raise ValueError("Nessun campo da modificare (name, note, content, filename)")
    a = answers.update(answer_id, d)
    return jsonify({"ok": True, "answer": a})


@bp.route("/api/answers/<answer_id>", methods=["DELETE"])
def delete_answer(answer_id):
    _get_or_404(answer_id)
    answers.delete(answer_id)
    return jsonify({"ok": True})


@bp.route("/api/answers/<answer_id>/files/<name>", methods=["DELETE"])
def delete_answer_file(answer_id, name):
    _get_or_404(answer_id)
    answers.delete_file(answer_id, name)
    return jsonify({"ok": True, "answer": answers.get(answer_id)})
