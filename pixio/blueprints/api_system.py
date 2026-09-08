"""API di sistema: stato, interfacce, apply, servizi, ricompilazione iPXE, spegnimento, job."""
from flask import Blueprint, jsonify, request

from ..privileged import HelperError
from ..services import jobs, system

bp = Blueprint("api_system", __name__)


@bp.errorhandler(HelperError)
def _helper_err(e):
    return jsonify({"error": f"Operazione di sistema fallita: {e}"}), 500


@bp.errorhandler(ValueError)
def _value_err(e):
    return jsonify({"error": str(e)}), 400


def _body():
    return request.get_json(silent=True) or {}


@bp.route("/api/system/status")
def status():
    return jsonify(system.status())


@bp.route("/api/system/interfaces")
def interfaces():
    return jsonify(system.interfaces())


@bp.route("/api/system/apply", methods=["POST"])
def apply():
    what = str(_body().get("what", "all") or "all")
    res = system.apply(what)
    return jsonify({"ok": bool(res.get("ok", True)), "results": res.get("results", res)})


@bp.route("/api/system/service", methods=["POST"])
def service():
    d = _body()
    system.service_action(str(d.get("name", "")), str(d.get("action", "")))
    return jsonify({"ok": True})


@bp.route("/api/system/rebuild-ipxe", methods=["POST"])
def rebuild_ipxe():
    job = system.rebuild_ipxe()
    return jsonify({"ok": True, "job_id": job.id})


@bp.route("/api/system/power", methods=["POST"])
def power():
    system.power(str(_body().get("action", "")))
    return jsonify({"ok": True})


# ---------------------------------------------------------------- job in background
@bp.route("/api/jobs")
def jobs_list():
    try:
        limit = max(1, min(500, int(request.args.get("limit", 50))))
    except ValueError:
        limit = 50
    return jsonify(jobs.list_jobs(limit))


@bp.route("/api/jobs/<job_id>")
def job_get(job_id):
    j = jobs.get_job(job_id)
    if j is None:
        return jsonify({"error": "Job non trovato"}), 404
    return jsonify(j.to_full_dict())


@bp.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def job_cancel(job_id):
    j = jobs.cancel(job_id)
    if j is None:
        return jsonify({"error": "Job non trovato"}), 404
    return jsonify({"ok": True, "job": j.to_dict()})
