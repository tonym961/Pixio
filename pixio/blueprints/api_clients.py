"""API client PXE visti."""
from flask import Blueprint, jsonify, request

from ..services import clients

bp = Blueprint("api_clients", __name__)


@bp.errorhandler(ValueError)
def _value_err(e):
    return jsonify({"error": str(e)}), 400


@bp.errorhandler(KeyError)
def _key_err(e):
    return jsonify({"error": "Client non trovato"}), 404


@bp.route("/api/clients")
def list_clients():
    return jsonify(clients.list_clients())


@bp.route("/api/clients/<mac>")
def get_client(mac):
    c = clients.get(mac)
    if c is None:
        raise KeyError(mac)
    return jsonify(c)


@bp.route("/api/clients/<mac>", methods=["PATCH"])
def update_client(mac):
    data = request.get_json(silent=True) or {}
    return jsonify(clients.update(mac, data))


@bp.route("/api/clients/<mac>", methods=["DELETE"])
def delete_client(mac):
    clients.delete(mac)
    return jsonify({"ok": True})
