"""API libreria driver: cartelle in DRIVERS_DIR, flag winpe_inject / setup_load, note, eliminazione file.

L'upload dei file passa da /api/upload (kind:"driver", folder:"<nome>", "path" per le sottocartelle),
vedi api_upload.py. PATCH su /api/drivers/folders (senza nome) applica gli stessi flag a piu' cartelle.
"""
import os
import socket

from flask import Blueprint, jsonify, request

from .. import config as C
from .. import settings as S
from ..services import drivers

bp = Blueprint("api_drivers", __name__)


@bp.errorhandler(ValueError)
def _value_err(e):
    return jsonify({"error": str(e) or "Richiesta non valida"}), 400


@bp.errorhandler(FileExistsError)
def _exists_err(e):
    return jsonify({"error": str(e) or "Esiste già"}), 409


@bp.errorhandler(FileNotFoundError)
def _missing_err(e):
    return jsonify({"error": str(e) or "Non trovato"}), 404


def _share_info():
    cfg = S.load()
    lib = cfg.get("library", {}) or {}
    ip = (cfg.get("network", {}) or {}).get("server_ip", "") or socket.gethostname()
    share = lib.get("drivers_share_name") or "drivers"
    return {
        "root": C.DRIVERS_DIR,
        "samba_path": f"\\\\{ip}\\{share}",
        "samba_enabled": bool(lib.get("samba_share_enabled", True)),
    }


def _folder_or_404(name):
    """Valida il nome (400) e verifica che la cartella esista (404). Ritorna il percorso."""
    p = drivers.folder_path(name)
    if not os.path.isdir(p):
        raise FileNotFoundError("Cartella driver non trovata")
    return p


def _get_folder(name):
    for f in drivers.list_folders():
        if f["name"] == name:
            return f
    raise FileNotFoundError("Cartella driver non trovata")


@bp.route("/api/drivers")
def list_drivers():
    out = _share_info()
    out["folders"] = drivers.list_folders()
    return jsonify(out)


@bp.route("/api/drivers/folders", methods=["POST"])
def create_folder():
    d = request.get_json(silent=True) or {}
    name = str(d.get("name", "") or "").strip()
    drivers.create_folder(name)
    return jsonify({"ok": True, "folder": _get_folder(name)}), 201


def _flag_patch(d, fields=("winpe_inject", "setup_load")):
    """Estrae i flag booleani dal corpo JSON (solleva ValueError se il tipo e' sbagliato)."""
    patch = {}
    for k in fields:
        if k in d:
            if not isinstance(d[k], bool):
                raise ValueError(f"{k}: valore booleano atteso")
            patch[k] = d[k]
    return patch


@bp.route("/api/drivers/folders", methods=["PATCH"])
def patch_folders():
    """Stessa modifica su piu' cartelle: {names:[...], winpe_inject?, setup_load?}."""
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        raise ValueError("Corpo JSON non valido")
    names = d.get("names")
    if not isinstance(names, list) or not names:
        raise ValueError("names: elenco di nomi di cartelle atteso")
    if len(names) > 500:
        raise ValueError("Troppe cartelle in una sola richiesta (max 500)")
    if any(not isinstance(n, str) for n in names):
        raise ValueError("names: i nomi delle cartelle devono essere testo")
    patch = _flag_patch(d)
    if not patch:
        raise ValueError("Nessun campo da modificare (winpe_inject, setup_load)")
    res = drivers.set_flags_many(names, patch)
    return jsonify({"ok": not res["errors"], "updated": res["updated"], "errors": res["errors"]})


@bp.route("/api/drivers/folders/<name>", methods=["PATCH"])
def patch_folder(name):
    _folder_or_404(name)
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        raise ValueError("Corpo JSON non valido")
    patch = _flag_patch(d)
    if "note" in d:
        if d["note"] is not None and not isinstance(d["note"], str):
            raise ValueError("note: testo atteso")
        note = (d["note"] or "").replace("\r", " ").replace("\n", " ").strip()
        if len(note) > 200:
            raise ValueError("Nota troppo lunga (max 200 caratteri)")
        patch["note"] = note
    if not patch:
        raise ValueError("Nessun campo da modificare (winpe_inject, setup_load, note)")
    folder = drivers.set_flags(name, patch)
    if folder is None:
        raise FileNotFoundError("Cartella driver non trovata")
    return jsonify({"ok": True, "folder": folder})


@bp.route("/api/drivers/folders/<name>", methods=["DELETE"])
def delete_folder(name):
    _folder_or_404(name)
    drivers.delete_folder(name)
    return jsonify({"ok": True})


@bp.route("/api/drivers/folders/<name>/clean", methods=["POST"])
def clean_one(name):
    """Toglie dalla cartella i file che non servono al driver."""
    removed = drivers.clean_folder(name)
    return jsonify({"ok": True, "removed": removed, "count": len(removed), "folder": name})


@bp.route("/api/drivers/clean", methods=["POST"])
def clean_many():
    """Stessa pulizia su più cartelle: {names:[...]}"""
    data = request.get_json(silent=True) or {}
    names = data.get("names")
    if not isinstance(names, list) or not names:
        raise ValueError("Indica almeno una cartella da pulire")
    if len(names) > 200:
        raise ValueError("Troppe cartelle in una sola richiesta")
    res = drivers.clean_folders([str(n) for n in names])
    tot = sum(len(v) for v in res["cleaned"].values())
    return jsonify({"ok": not res["errors"], "cleaned": res["cleaned"], "errors": res["errors"], "count": tot})


@bp.route("/api/drivers/folders/<name>/files/<path:file>", methods=["DELETE"])
def delete_file(name, file):
    _folder_or_404(name)
    drivers.delete_file(name, file)
    return jsonify({"ok": True})
