"""API sorgenti ISO remote (share SMB)."""
import logging

from flask import Blueprint, jsonify, request

from .. import config as C
from ..privileged import HelperError
from ..services import sources

bp = Blueprint("api_sources", __name__)
log = logging.getLogger("pixio.api_sources")


@bp.errorhandler(HelperError)
def _helper_err(e):
    return jsonify({"error": f"Operazione fallita: {e}"}), 500


@bp.errorhandler(ValueError)
def _value_err(e):
    return jsonify({"error": str(e)}), 400


@bp.errorhandler(KeyError)
def _key_err(e):
    return jsonify({"error": "Sorgente non trovata"}), 404


def _body():
    return request.get_json(silent=True) or {}


def _check_id(sid):
    if not C.SOURCE_ID_RE.match(sid or "") or not sources.exists(sid):
        raise KeyError(sid)
    return sid


def _start_scan():
    """Avvia una scansione del catalogo, se il modulo esiste. Ritorna job_id o None."""
    try:
        from ..services import background
        job = background.start_scan()
        return getattr(job, "id", None) if job is not None else None
    except Exception as e:  # noqa: BLE001
        log.warning("scansione non avviata: %s", e)
        return None


@bp.route("/api/sources")
def list_sources():
    return jsonify(sources.list_sources())


@bp.route("/api/sources", methods=["POST"])
def create():
    src = sources.create(_body())
    sid = src["id"]
    warnings = []
    # test della connessione, mount e scansione: gli errori non annullano la creazione
    try:
        t = sources.test(sid)
        if not t.get("ok"):
            warnings.append("Test di connessione fallito: " + " | ".join(t.get("output", [])[-3:]))
    except HelperError as e:
        warnings.append(f"Test di connessione non eseguito: {e}")
    try:
        sources.mount(sid)
    except HelperError as e:
        warnings.append(f"Mount fallito: {e}")
    job_id = _start_scan() if not warnings else None
    src = sources.get(sid) or src
    return jsonify({"ok": True, "source": src, "warnings": warnings, "job_id": job_id}), 201


@bp.route("/api/sources/<sid>", methods=["PUT"])
def update(sid):
    _check_id(sid)
    src = sources.update(sid, _body())
    warnings = []
    try:
        sources.umount(sid)
    except HelperError as e:
        warnings.append(f"Smontaggio fallito: {e}")
    try:
        sources.mount(sid)
    except HelperError as e:
        warnings.append(f"Mount fallito: {e}")
    job_id = _start_scan() if not warnings else None
    src = sources.get(sid) or src
    return jsonify({"ok": True, "source": src, "warnings": warnings, "job_id": job_id})


@bp.route("/api/sources/<sid>", methods=["DELETE"])
def delete(sid):
    _check_id(sid)
    sources.delete(sid)
    # le ISO di questa sorgente escono dal catalogo (se il modulo lo supporta)
    try:
        from ..services import catalog
        fn = getattr(catalog, "forget_source", None)
        if callable(fn):
            fn(sid)
    except Exception as e:  # noqa: BLE001
        log.warning("pulizia catalogo per %s non riuscita: %s", sid, e)
    return jsonify({"ok": True})


@bp.route("/api/sources/<sid>/test", methods=["POST"])
def test(sid):
    _check_id(sid)
    return jsonify(sources.test(sid))


@bp.route("/api/sources/<sid>/mount", methods=["POST"])
def mount(sid):
    _check_id(sid)
    res = sources.mount(sid)
    return jsonify({"ok": True, "mounted": bool(res.get("mounted", True)), "source": sources.get(sid)})


@bp.route("/api/sources/<sid>/umount", methods=["POST"])
def umount(sid):
    _check_id(sid)
    res = sources.umount(sid)
    return jsonify({"ok": True, "mounted": bool(res.get("mounted", False)), "source": sources.get(sid)})
