"""API dei preset (modelli pronti per i profili Windows e Debian) — docs/API.md, sezione 9.

Il file `data/profile-presets.json` fa parte del codice ed è di sola lettura: qui viene solo letto,
tenuto in cache finché non cambia il mtime e ripulito dai preset malformati (che vengono scartati
con un messaggio nel log invece di far fallire la richiesta).
"""
import copy
import logging
import os
import threading

from flask import Blueprint, jsonify, request

from .. import config as C
from ..storage import read_json

bp = Blueprint("api_presets", __name__)
log = logging.getLogger("pixio.presets")

KINDS = ("windows", "debian")
# Campi obbligatori di ogni preset (docs/API.md: {id, kind, name, description, settings})
REQUIRED = ("id", "kind", "name", "description", "settings")

_cache = {"mtime": None, "size": None, "presets": [], "warnings": []}
_lock = threading.Lock()


def _err(msg, code=400):
    return jsonify({"error": msg}), code


def presets_file():
    return getattr(C, "PRESETS_FILE",
                   os.path.join(getattr(C, "CODE_DIR", "/opt/pixio"), "data", "profile-presets.json"))


def _valid(p, warnings):
    """True se il preset ha tutti i campi previsti e valori del tipo giusto."""
    if not isinstance(p, dict):
        warnings.append("Preset ignorato: non è un oggetto")
        return False
    mancanti = [k for k in REQUIRED if not p.get(k)]
    if mancanti:
        warnings.append(f"Preset {p.get('id') or '(senza id)'} ignorato: mancano i campi "
                        + ", ".join(mancanti))
        return False
    if p["kind"] not in KINDS:
        warnings.append(f"Preset {p['id']} ignorato: tipo sconosciuto '{p['kind']}' "
                        f"(ammessi: {', '.join(KINDS)})")
        return False
    if not isinstance(p["settings"], dict) or not p["settings"]:
        warnings.append(f"Preset {p['id']} ignorato: impostazioni assenti o non valide")
        return False
    for k in ("id", "name", "description"):
        if not isinstance(p[k], str):
            warnings.append(f"Preset {p.get('id')} ignorato: il campo {k} deve essere testo")
            return False
    return True


def load_presets():
    """Elenco dei preset validi. Rilegge il file solo se è cambiato (mtime + dimensione)."""
    path = presets_file()
    try:
        st = os.stat(path)
        chiave = (int(st.st_mtime_ns), st.st_size)
    except OSError:
        chiave = None
    with _lock:
        if chiave is not None and _cache["mtime"] == chiave[0] and _cache["size"] == chiave[1]:
            return copy.deepcopy(_cache["presets"]), list(_cache["warnings"])
        data = read_json(path, {})
        crudi = data.get("presets") if isinstance(data, dict) else data
        if not isinstance(crudi, list):
            crudi = []
        warnings, out, visti = [], [], set()
        for p in crudi:
            if not _valid(p, warnings):
                continue
            if p["id"] in visti:
                warnings.append(f"Preset {p['id']} ignorato: identificativo ripetuto")
                continue
            visti.add(p["id"])
            out.append({"id": p["id"], "kind": p["kind"], "name": p["name"],
                        "description": p["description"], "settings": p["settings"]})
        if not out and crudi:
            log.warning("nessun preset valido in %s", path)
        for w in warnings:
            log.warning("%s", w)
        if chiave is not None:
            _cache.update({"mtime": chiave[0], "size": chiave[1], "presets": out, "warnings": warnings})
        else:
            _cache.update({"mtime": None, "size": None, "presets": out, "warnings": warnings})
        return copy.deepcopy(out), list(warnings)


def get_preset(preset_id, kind=None):
    """Un singolo preset (o None). Usato dalle API dei profili per la creazione da modello."""
    for p in load_presets()[0]:
        if p["id"] == preset_id and (kind is None or p["kind"] == kind):
            return p
    return None


@bp.route("/api/presets")
def list_presets():
    kind = (request.args.get("kind") or "").strip().lower()
    if kind and kind not in KINDS:
        return _err("Tipo di modello non valido: " + " oppure ".join(KINDS))
    presets, warnings = load_presets()
    if kind:
        presets = [p for p in presets if p["kind"] == kind]
    return jsonify({"presets": presets, "kinds": list(KINDS), "warnings": warnings})
