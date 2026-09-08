"""API dei profili di personalizzazione Debian (docs/API.md, sezione 9).

Rotte:
  GET    /api/debprofiles                      elenco + valori predefiniti + elenchi per la GUI
  POST   /api/debprofiles                      crea (con "preset" come base, sovrascritto da "settings")
  GET    /api/debprofiles/<id>                 un profilo
  PUT    /api/debprofiles/<id>                 aggiorna nome/nota/impostazioni
  DELETE /api/debprofiles/<id>                 elimina
  POST   /api/debprofiles/<id>/duplicate       duplica (comodità per la GUI)
  POST   /api/debprofiles/preview              anteprima di impostazioni non ancora salvate
  POST   /api/debprofiles/<id>/preview         anteprima del preseed di un profilo salvato
  POST   /api/debprofiles/<id>/save-answer     genera il preseed e lo salva come risposta "debian"
"""
from flask import Blueprint, jsonify, request

from ..services import debprofile

bp = Blueprint("api_debprofile", __name__)


def _err(msg, code=400):
    return jsonify({"error": msg}), code


def _body():
    return request.get_json(silent=True) or {}


def _presets():
    """Preset debian per la tendina "Parti da un modello". L'elenco non è indispensabile:
    se il file dei modelli manca la pagina deve funzionare lo stesso."""
    try:
        from .api_presets import load_presets
        return [p for p in load_presets()[0] if p["kind"] == "debian"]
    except Exception:  # noqa: BLE001 - i modelli sono un di più, non un requisito
        return []


@bp.route("/api/debprofiles")
def list_profiles():
    return jsonify({
        "profiles": debprofile.list_profiles(),
        "defaults": debprofile.defaults(),
        "tasks": debprofile.tasks_list(),
        "mirrors": debprofile.mirrors_list(),
        "timezones": list(debprofile.TIMEZONES),
        "locales": list(debprofile.LOCALES),
        "keyboards": list(debprofile.KEYBOARDS),
        "recipes": [{"id": r, "name": debprofile.RECIPE_LABELS[r]} for r in debprofile.RECIPES],
        "filesystems": list(debprofile.FILESYSTEMS),
        "presets": _presets(),
    })


@bp.route("/api/debprofiles", methods=["POST"])
def create_profile():
    try:
        p = debprofile.create(_body())
    except ValueError as ex:
        return _err(str(ex))
    return jsonify(p), 201


@bp.route("/api/debprofiles/<pid>")
def get_profile(pid):
    try:
        debprofile.check_id(pid)
    except ValueError as ex:
        return _err(str(ex))
    p = debprofile.get(pid)
    if not p:
        return _err("Profilo non trovato", 404)
    return jsonify(p)


@bp.route("/api/debprofiles/<pid>", methods=["PUT"])
def update_profile(pid):
    try:
        p = debprofile.update(pid, _body())
    except FileNotFoundError as ex:
        return _err(str(ex), 404)
    except ValueError as ex:
        return _err(str(ex))
    return jsonify(p)


@bp.route("/api/debprofiles/<pid>", methods=["DELETE"])
def delete_profile(pid):
    try:
        debprofile.delete(pid)
    except FileNotFoundError as ex:
        return _err(str(ex), 404)
    except ValueError as ex:
        return _err(str(ex))
    return jsonify({"ok": True})


@bp.route("/api/debprofiles/<pid>/duplicate", methods=["POST"])
def duplicate_profile(pid):
    try:
        p = debprofile.duplicate(pid, _body().get("name"))
    except FileNotFoundError as ex:
        return _err(str(ex), 404)
    except ValueError as ex:
        return _err(str(ex))
    return jsonify(p), 201


@bp.route("/api/debprofiles/preview", methods=["POST"])
def preview_new():
    """Anteprima di un profilo non ancora salvato: {preset?, settings?} → {preseed}."""
    data = _body()
    base = {}
    preset_id = str(data.get("preset") or "").strip()
    if preset_id:
        p = debprofile.load_preset(preset_id)
        if not p:
            return _err(f"Modello non trovato: {preset_id}", 404)
        base = p.get("settings") or {}
    from ..storage import deep_merge
    try:
        st = debprofile.validate(deep_merge(base, data.get("settings") or {}))
        testo = debprofile.render_preseed({"name": data.get("name") or "anteprima", "settings": st})
    except ValueError as ex:
        return _err(str(ex))
    return jsonify({"preseed": testo})


@bp.route("/api/debprofiles/<pid>/preview", methods=["POST"])
def preview_profile(pid):
    """Anteprima del preseed. Se il corpo contiene "settings" si usa quello (modifiche non salvate)."""
    try:
        debprofile.check_id(pid)
    except ValueError as ex:
        return _err(str(ex))
    p = debprofile.get(pid)
    if not p:
        return _err("Profilo non trovato", 404)
    data = _body()
    if data.get("settings"):
        from ..storage import deep_merge
        p = dict(p)
        p["settings"] = deep_merge(p["settings"], data["settings"])
    try:
        testo = debprofile.render_preseed(p)
    except ValueError as ex:
        return _err(str(ex))
    return jsonify({"preseed": testo})


@bp.route("/api/debprofiles/<pid>/save-answer", methods=["POST"])
def save_answer(pid):
    """Genera il preseed.cfg e lo salva come risposta di tipo debian (services/answers.py)."""
    answer_id = _body().get("answer_id") or None
    try:
        out = debprofile.save_as_answer(pid, answer_id)
    except FileNotFoundError as ex:
        return _err(str(ex), 404)
    except RuntimeError as ex:
        # il servizio delle risposte non è disponibile (modulo assente)
        return _err(str(ex), 503)
    except ValueError as ex:
        return _err(str(ex))
    return jsonify(out)
