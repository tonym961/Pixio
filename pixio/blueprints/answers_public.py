"""File delle risposte automatiche serviti ai client PXE: /answers/<id>/<nome file>.

Nessuna autenticazione (il client in installazione non ha sessione), sola lettura, nomi validati:
l'id e il nome file passano dalle stesse regole del servizio e il percorso finale deve restare
dentro ANSWERS_DIR. Il percorso "/answers/" è nell'allowlist pubblica di pixio/__init__.py.
"""
import logging
import os

from flask import Blueprint, Response, request, send_file

from ..services import answers

bp = Blueprint("answers_public", __name__)
log = logging.getLogger("pixio.answers")


def _err(msg, code):
    return Response(msg + "\n", status=code, mimetype="text/plain; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


@bp.route("/answers/<answer_id>/<name>")
def serve_answer_file(answer_id, name):
    try:
        path = answers.file_path(answer_id, name)
    except ValueError as e:
        log.info("risposta: richiesta non valida da %s (%s)", request.remote_addr, e)
        return _err("Nome non valido", 400)
    if not answers.get(answer_id):
        return _err("Risposta non trovata", 404)
    if not os.path.isfile(path):
        return _err("File non trovato", 404)
    log.info("risposta %s/%s servita a %s", answer_id, os.path.basename(path), request.remote_addr)
    # Tutti i file di risposta sono testo: i client (d-i, cloud-init, anaconda) non guardano il tipo MIME.
    resp = send_file(path, mimetype="text/plain", conditional=True)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store"
    return resp
