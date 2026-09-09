"""API menu di boot."""
from flask import Blueprint, jsonify, request

from .. import settings as S
from ..services import ipxe_menu

bp = Blueprint("api_menu", __name__)


@bp.route("/api/menu")
def get_menu():
    cfg = S.load()
    return jsonify({"settings": _settings(cfg), "entries": ipxe_menu.entries(cfg), "preview": ipxe_menu.preview(cfg)})


def _settings(cfg):
    """Impostazioni del menu normalizzate (sottomenu e tema sempre completi, anche da config vecchie)."""
    from ..services import theme as T
    m = dict(cfg["menu"])
    if str(m.get("submenus") or "").lower() not in ipxe_menu.SUBMENU_MODES:
        m["submenus"] = "auto"
    m["submenu_threshold"] = ipxe_menu.submenu_threshold(m)
    m["answer_timeout"] = ipxe_menu.answer_timeout(m)
    t = T.theme(cfg)
    m["theme"] = t
    m["theme_bg_url"] = T.bg_web(t)       # l'anteprima della GUI carica lo sfondo della risoluzione scelta
    m["theme_styles"] = list(T.STYLES)
    m["theme_resolutions"] = list(T.RESOLUTIONS)
    return m


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
    if "submenus" in s:
        v = str(s["submenus"]).strip().lower()
        if v not in ipxe_menu.SUBMENU_MODES:
            return jsonify({"error": "Sottomenu: valori ammessi auto, always o never"}), 400
        m["submenus"] = v
    if "submenu_threshold" in s:
        try:
            t = int(s["submenu_threshold"])
        except (TypeError, ValueError):
            return jsonify({"error": "Soglia dei sottomenu non valida"}), 400
        if not 1 <= t <= 100:
            return jsonify({"error": "Soglia dei sottomenu tra 1 e 100 voci"}), 400
        m["submenu_threshold"] = t
    if "answer_timeout" in s:
        try:
            t = int(s["answer_timeout"])
        except (TypeError, ValueError):
            return jsonify({"error": "Timeout della scelta dell'installazione non valido"}), 400
        if not 0 <= t <= ipxe_menu.MAX_ANSWER_TIMEOUT:
            return jsonify({"error": f"Timeout della scelta tra 0 e {ipxe_menu.MAX_ANSWER_TIMEOUT} secondi"}), 400
        m["answer_timeout"] = t
    if "theme" in s and isinstance(s["theme"], dict):
        from ..services import theme as T
        th = dict(m.get("theme") or {})
        for k in ("bg", "accent", "fg", "muted"):
            if k in s["theme"]:
                if not T.HEX_RE.match(str(s["theme"][k] or "")):
                    return jsonify({"error": f"Colore non valido per {k} (formato #RRGGBB)"}), 400
                th[k] = T.hexcol(s["theme"][k], T.DEFAULT_THEME[k])
        for k in ("logo_text", "subtitle"):
            if k in s["theme"]:
                th[k] = str(s["theme"][k]).strip()[:40]
        if "style" in s["theme"]:
            v = str(s["theme"]["style"]).strip().lower()
            if v not in T.STYLES:
                return jsonify({"error": "Stile del menu: valori ammessi " + ", ".join(T.STYLES)}), 400
            th["style"] = v
        if "resolution" in s["theme"]:
            v = str(s["theme"]["resolution"]).strip().lower()
            if v not in T.RESOLUTIONS:
                return jsonify({"error": "Risoluzione del menu: valori ammessi " + ", ".join(T.RESOLUTIONS)}), 400
            th["resolution"] = v
        m["theme"] = th
    S.save(cfg)
    try:
        from ..services import theme as T
        if T.theme(cfg)["style"] == "grafico":     # solo lo stile grafico usa lo sfondo PNG
            T.render_background(cfg, cfg["network"]["server_ip"])
    except Exception as e:  # noqa
        return jsonify({"ok": True, "settings": _settings(cfg), "preview": ipxe_menu.preview(cfg), "warnings": [f"Sfondo non rigenerato: {e}"]})
    return jsonify({"ok": True, "settings": _settings(cfg), "preview": ipxe_menu.preview(cfg)})
