"""API client PXE visti: nome, avvio automatico, avvio una tantum e Wake-on-LAN."""
from flask import Blueprint, jsonify, request

from ..privileged import HelperError
from ..services import clients

bp = Blueprint("api_clients", __name__)


@bp.errorhandler(ValueError)
def _value_err(e):
    return jsonify({"error": str(e) or "Richiesta non valida"}), 400


@bp.errorhandler(KeyError)
def _key_err(e):
    return jsonify({"error": "Client non trovato"}), 404


@bp.errorhandler(HelperError)
def _helper_err(e):
    return jsonify({"error": str(e) or "Operazione non riuscita"}), 500


@bp.route("/api/clients")
def list_clients():
    return jsonify(clients.list_clients())


@bp.route("/api/clients/wake", methods=["POST"])
def wake_clients():
    """Risveglio multiplo: {macs:[...]} -> {ok, results:{mac: bool}}."""
    data = request.get_json(silent=True) or {}
    macs = data.get("macs")
    if not isinstance(macs, list) or not macs:
        raise ValueError("Nessun indirizzo MAC indicato")
    if len(macs) > 200:
        raise ValueError("Troppi client selezionati (max 200)")
    return jsonify({"ok": True, "results": clients.wake_many(macs)})


@bp.route("/api/clients/<mac>")
def get_client(mac):
    c = clients.get(mac)
    if c is None:
        raise KeyError(mac)
    return jsonify(c)


@bp.route("/api/clients/<mac>/wake", methods=["POST"])
def wake_client(mac):
    return jsonify({"ok": True, "sent": clients.wake(mac)})


@bp.route("/api/clients/<mac>", methods=["PATCH"])
def update_client(mac):
    data = request.get_json(silent=True) or {}
    return jsonify(clients.update(mac, data))


@bp.route("/api/clients/<mac>", methods=["DELETE"])
def delete_client(mac):
    clients.delete(mac)
    return jsonify({"ok": True})
