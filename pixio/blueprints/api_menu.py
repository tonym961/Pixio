"""API menu di boot."""
from flask import Blueprint, jsonify, request

from .. import settings as S
from ..services import ipxe_menu

bp = Blueprint("api_menu", __name__)


@bp.route("/api/menu")
def get_menu():
    cfg = S.load()
    return jsonify({"settings": cfg["menu"], "entries": ipxe_menu.entries(cfg), "preview": ipxe_menu.preview(cfg)})


@bp.route("/api/menu", methods=["PUT"])
def put_menu():
    data = request.get_json(silent=True) or {}
    s = data.get("settings") or data
    cfg = S.load()
    m = cfg["menu"]
    if "title" in s:
        m["title"] = str(s["title"]).strip()[:60] or "PIXIO"
    if "timeout" in s:
        try:
            t = int(s["timeout"])
        except (TypeError, ValueError):
            return jsonify({"error": "Timeout non valido"}), 400
        if not 0 <= t <= 600:
            return jsonify({"error": "Timeout tra 0 e 600 secondi"}), 400
        m["timeout"] = t
    if "default" in s:
        m["default"] = str(s["default"])[:64]
    for k in ("show_local", "show_shell", "show_reboot", "show_memtest"):
        if k in s:
            m[k] = bool(s[k])
    if "groups" in s and isinstance(s["groups"], list):
        m["groups"] = [str(g).strip()[:60] for g in s["groups"] if str(g).strip()][:20]
    S.save(cfg)
    return jsonify({"ok": True, "settings": m, "preview": ipxe_menu.preview(cfg)})
