"""API catalogo ISO."""
from flask import Blueprint, jsonify, request

from .. import config as C
from ..privileged import HelperError
from ..services import catalog, ipxe_menu, jobs

bp = Blueprint("api_catalog", __name__)


def _err(msg, code=400):
    return jsonify({"error": msg}), code


@bp.route("/api/catalog")
def list_catalog():
    isos, last_scan = catalog.list_isos()
    running = any(j["type"] == "scan" and j["status"] == "running" for j in jobs.list_jobs(20))
    return jsonify({"isos": isos, "last_scan": last_scan, "scanning": running, "types": catalog.types_list()})


@bp.route("/api/catalog/scan", methods=["POST"])
def scan():
    j = jobs.start("scan", None, lambda job: catalog.scan(job), message="Scansione delle sorgenti")
    return jsonify({"ok": True, "job_id": j.id})


@bp.route("/api/catalog/<slug>")
def get_iso(slug):
    if not C.SLUG_RE.match(slug):
        return _err("slug non valido")
    e = catalog.get(slug)
    if not e:
        return _err("ISO non trovata", 404)
    prev = {}
    for p in ("efi", "bios"):
        text, _ = ipxe_menu.entry_script(slug, p)
        prev[p] = text
    e["recipe_preview"] = prev
    return jsonify(e)


@bp.route("/api/catalog/<slug>", methods=["PATCH"])
def patch_iso(slug):
    if not C.SLUG_RE.match(slug):
        return _err("slug non valido")
    data = request.get_json(silent=True) or {}
    allowed = {"name", "enabled", "group", "order", "type", "custom_recipe", "cache_wanted",
               "answer_id", "answers", "answer_manual"}
    patch = {k: v for k, v in data.items() if k in allowed}
    try:
        e = catalog.update(slug, patch)
    except KeyError:
        return _err("ISO non trovata", 404)
    except ValueError as ex:
        return _err(str(ex))
    except FileNotFoundError as ex:
        return _err(f"{ex}", 409)
    except HelperError as ex:
        return _err(f"Operazione fallita: {ex}", 500)
    return jsonify(e)


@bp.route("/api/catalog/<slug>/redetect", methods=["POST"])
def redetect(slug):
    if not C.SLUG_RE.match(slug):
        return _err("slug non valido")
    j = jobs.start("detect", slug, lambda job: catalog.redetect(slug), message="Nuovo rilevamento")
    return jsonify({"ok": True, "job_id": j.id})


@bp.route("/api/catalog/<slug>/mount", methods=["POST"])
def mount(slug):
    try:
        catalog.mount(slug)
    except (KeyError, FileNotFoundError, HelperError) as ex:
        return _err(str(ex), 500)
    return jsonify(catalog.get(slug))


@bp.route("/api/catalog/<slug>/umount", methods=["POST"])
def umount(slug):
    try:
        catalog.umount(slug)
    except HelperError as ex:
        return _err(str(ex), 500)
    return jsonify(catalog.get(slug))


@bp.route("/api/catalog/reorder", methods=["POST"])
def reorder():
    data = request.get_json(silent=True) or {}
    order = [s for s in (data.get("order") or []) if isinstance(s, str) and C.SLUG_RE.match(s)]
    catalog.reorder(order)
    return jsonify({"ok": True})


@bp.route("/api/catalog/<slug>", methods=["DELETE"])
def delete(slug):
    if not C.SLUG_RE.match(slug):
        return _err("slug non valido")
    try:
        catalog.delete_local(slug)
    except KeyError:
        return _err("ISO non trovata", 404)
    except ValueError as ex:
        return _err(str(ex))
    except HelperError as ex:
        return _err(str(ex), 500)
    return jsonify({"ok": True})
