"""API dei profili di personalizzazione Windows (docs/API.md, sezione 8).

Rotte:
  GET    /api/winprofiles                      elenco + valori predefiniti + elenchi per la GUI
                                               (compreso il catalogo delle ottimizzazioni, sezione 10,
                                               i tipi di Windows client/LTSC/server, sezioni 11 e 12,
                                               e le lingue installabili dopo il setup, sezione 13)
  POST   /api/winprofiles                      crea (con "preset" come base, sovrascritto da "settings")
  GET    /api/winprofiles/<id>                 un profilo
  PUT    /api/winprofiles/<id>                 aggiorna nome/nota/impostazioni
  DELETE /api/winprofiles/<id>                 elimina
  POST   /api/winprofiles/<id>/duplicate       duplica (comodità per la GUI)
  POST   /api/winprofiles/preview              anteprima di impostazioni non ancora salvate
  POST   /api/winprofiles/<id>/preview         anteprima dell'autounattend.xml di un profilo salvato
  POST   /api/winprofiles/<id>/save-answer     genera l'XML e lo salva come risposta "windows"

Anteprima e salvataggio accettano "iso": lo slug della ISO per cui si genera il file. Con quello
l'edizione da installare viene confrontata con quelle che l'immagine contiene davvero, invece di
scrivere un valore che farà fallire il programma di installazione (docs/API.md, sezione 19).
"""
from flask import Blueprint, jsonify, request

from .. import config as C
from .. import settings as S
from ..services import winprofile

bp = Blueprint("api_winprofile", __name__)


def _err(msg, code=400):
    return jsonify({"error": msg}), code


def _body():
    return request.get_json(silent=True) or {}


def _server_ip():
    """IP del server per il percorso della share driver. Se le impostazioni non si leggono, stringa vuota."""
    try:
        return (S.load().get("network") or {}).get("server_ip") or ""
    except Exception:  # noqa: BLE001 - l'anteprima deve funzionare anche senza configurazione
        return ""


def _presets():
    """Modelli windows per la tendina "Parti da un modello". Se il file manca la pagina funziona lo stesso."""
    try:
        from .api_presets import load_presets
        return [p for p in load_presets()[0] if p["kind"] == "windows"]
    except Exception:  # noqa: BLE001 - i modelli sono un di più, non un requisito
        return []


def _tweaks():
    """Catalogo delle ottimizzazioni. Se il file manca la pagina deve aprirsi lo stesso, con
    l'elenco vuoto: i profili già salvati continuano a funzionare, semplicemente non si possono
    più scegliere nuove ottimizzazioni."""
    try:
        return winprofile.tweaks_catalog()
    except Exception:  # noqa: BLE001 - il catalogo è un di più, non un requisito
        return {"categories": [], "items": []}


def _con_isos(profiles):
    """Aggiunge a ogni profilo le ISO su cui girerà, con le loro edizioni (docs/API.md, sez. 19).

    È il collegamento che permette alla GUI di proporre una tendina invece di un campo libero: le
    edizioni arrivano dalla cache del catalogo, quindi non si legge nessun file qui dentro."""
    try:
        legami = winprofile.iso_bindings()
    except Exception:  # noqa: BLE001 - senza catalogo la pagina deve aprirsi lo stesso
        legami = {}
    for p in profiles:
        p["isos"] = legami.get(p["id"], [])
    return profiles


def _iso_slug(data):
    """Slug della ISO per cui generare l'XML, dal corpo della richiesta (vuoto = nessuna)."""
    slug = str(data.get("iso") or data.get("iso_slug") or "").strip()
    return slug if C.SLUG_RE.match(slug) else ""


@bp.route("/api/winprofiles")
def list_profiles():
    return jsonify({
        "profiles": _con_isos(winprofile.list_profiles()),
        "defaults": winprofile.defaults(),
        "timezones": winprofile.timezones_list(),
        "languages": winprofile.languages_list(),
        # lingue installabili dopo il setup con il loro GeoId, per il riquadro "Lingua da
        # installare" della GUI (docs/API.md, sezione 13)
        "languages_install": winprofile.languages_install_list(),
        "lang_sources": winprofile.lang_sources_list(),
        "apps": winprofile.apps_list(),
        "disk_modes": winprofile.disk_modes_list(),
        "groups": winprofile.groups_list(),
        # tipo di Windows del profilo: client, 10-ltsc, 11-ltsc oppure server (docs/API.md,
        # sezioni 11 e 12). Ogni voce del catalogo porta il suo "editions", così la GUI filtra da sola.
        "targets": winprofile.targets_list(),
        "architectures": list(winprofile.ARCHITECTURES),
        "power_schemes": list(winprofile.POWER_SCHEMES),
        # catalogo delle ottimizzazioni in stile nLite (docs/API.md, sezione 10)
        "tweaks": _tweaks(),
        "impacts": list(winprofile.IMPACTS),
        "service_starts": [{"id": v, "name": winprofile.SERVICE_START_LABELS[v]}
                           for v in winprofile.SERVICE_STARTS],
        "presets": _presets(),
        "server_ip": _server_ip(),
    })


@bp.route("/api/winprofiles", methods=["POST"])
def create_profile():
    try:
        p = winprofile.create(_body())
    except ValueError as ex:
        return _err(str(ex))
    return jsonify(p), 201


@bp.route("/api/winprofiles/<pid>")
def get_profile(pid):
    try:
        winprofile.check_id(pid)
    except ValueError as ex:
        return _err(str(ex))
    p = winprofile.get(pid)
    if not p:
        return _err("Profilo non trovato", 404)
    return jsonify(_con_isos([p])[0])


@bp.route("/api/winprofiles/<pid>", methods=["PUT"])
def update_profile(pid):
    try:
        p = winprofile.update(pid, _body())
    except FileNotFoundError as ex:
        return _err(str(ex), 404)
    except ValueError as ex:
        return _err(str(ex))
    return jsonify(p)


@bp.route("/api/winprofiles/<pid>", methods=["DELETE"])
def delete_profile(pid):
    try:
        winprofile.delete(pid)
    except FileNotFoundError as ex:
        return _err(str(ex), 404)
    except ValueError as ex:
        return _err(str(ex))
    return jsonify({"ok": True})


@bp.route("/api/winprofiles/<pid>/duplicate", methods=["POST"])
def duplicate_profile(pid):
    try:
        p = winprofile.duplicate(pid, _body().get("name"))
    except FileNotFoundError as ex:
        return _err(str(ex), 404)
    except ValueError as ex:
        return _err(str(ex))
    return jsonify(p), 201


@bp.route("/api/winprofiles/preview", methods=["POST"])
def preview_new():
    """Anteprima di un profilo non ancora salvato: {preset?, settings?} → {xml}."""
    data = _body()
    base = {}
    preset_id = str(data.get("preset") or "").strip()
    if preset_id:
        p = winprofile.load_preset(preset_id)
        if not p:
            return _err(f"Modello non trovato: {preset_id}", 404)
        base = p.get("settings") or {}
    from ..storage import deep_merge
    try:
        st = winprofile.validate(deep_merge(base, data.get("settings") or {}))
        xml = winprofile.render_autounattend(
            {"name": data.get("name") or "anteprima", "settings": st}, _server_ip(),
            editions=winprofile.editions_for_iso(_iso_slug(data)))
    except ValueError as ex:
        return _err(str(ex))
    return jsonify({"xml": xml})


@bp.route("/api/winprofiles/<pid>/preview", methods=["POST"])
def preview_profile(pid):
    """Anteprima dell'XML. Se il corpo contiene "settings" si usa quello (modifiche non salvate)."""
    try:
        winprofile.check_id(pid)
    except ValueError as ex:
        return _err(str(ex))
    p = winprofile.get(pid)
    if not p:
        return _err("Profilo non trovato", 404)
    data = _body()
    if data.get("settings"):
        from ..storage import deep_merge
        p = dict(p)
        p["settings"] = deep_merge(p["settings"], data["settings"])
    try:
        xml = winprofile.render_autounattend(p, _server_ip(),
                                             editions=winprofile.editions_for_iso(_iso_slug(data)))
    except ValueError as ex:
        return _err(str(ex))
    return jsonify({"xml": xml})


@bp.route("/api/winprofiles/<pid>/save-answer", methods=["POST"])
def save_answer(pid):
    """Genera l'autounattend.xml e lo salva come risposta di tipo windows (services/answers.py)."""
    data = _body()
    answer_id = data.get("answer_id") or None
    try:
        out = winprofile.save_as_answer(pid, answer_id, _server_ip(), _iso_slug(data))
    except FileNotFoundError as ex:
        return _err(str(ex), 404)
    except FileExistsError as ex:
        return _err(str(ex), 409)
    except RuntimeError as ex:
        # il servizio delle risposte non è disponibile (modulo assente)
        return _err(str(ex), 503)
    except ValueError as ex:
        return _err(str(ex))
    return jsonify(out)
