"""API log unificati (dnsmasq, nginx, app)."""
from flask import Blueprint, jsonify, request

from ..services import logs

bp = Blueprint("api_logs", __name__)


@bp.errorhandler(ValueError)
def _value_err(e):
    return jsonify({"error": str(e)}), 400


@bp.route("/api/logs")
def read_logs():
    source = request.args.get("source", "all") or "all"
    cursor = request.args.get("cursor") or None
    limit = request.args.get("limit", 200)
    return jsonify(logs.read(source=source, cursor=cursor, limit=limit))
