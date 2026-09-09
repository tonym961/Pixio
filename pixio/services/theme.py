"""Tema del menu di boot iPXE: sfondo PNG generato dal server (console framebuffer) e colori del menu."""
import logging
import os
import re
import threading

from .. import config as C

log = logging.getLogger("pixio.theme")
BG_REL = "theme/bg.png"                       # -> HTTP_INJECT_DIR/theme/bg.png = /pxe/inject/theme/bg.png
W, H = 1024, 768
MARGINS = {"left": 48, "right": 48, "top": 104, "bottom": 60}   # area di testo del menu dentro l'immagine
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
DEFAULT_THEME = {"bg": "#0B1220", "accent": "#3FC1CF", "fg": "#E6ECF2", "muted": "#7C8A99",
                 "logo_text": "PIXIO", "subtitle": "Avvio da rete", "style": "testo"}
STYLES = ("testo", "grafico")
_lock = threading.Lock()
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def hexcol(v, default):
    m = HEX_RE.match(str(v or ""))
    return ("#" + m.group(1)).upper() if m else default


def theme(cfg):
    t = dict(DEFAULT_THEME)
    t.update({k: v for k, v in (cfg.get("menu", {}).get("theme") or {}).items() if k in t})
    for k in ("bg", "accent", "fg", "muted"):
        t[k] = hexcol(t[k], DEFAULT_THEME[k])
    t["style"] = t.get("style") if t.get("style") in STYLES else "testo"
    t["logo_text"] = str(t["logo_text"])[:16] or "PIXIO"
    t["subtitle"] = str(t["subtitle"])[:40]
    return t


def ipxe_rgb(h):
    return "0x" + h.lstrip("#").lower()


def bg_path():
    return os.path.join(C.HTTP_INJECT_DIR, BG_REL)


def bg_url(server_ip):
    return f"http://{server_ip}/pxe/inject/{BG_REL}"


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def render_background(cfg, server_ip=""):
    """Genera lo sfondo (1024x768). Ritorna il percorso o None se PIL manca."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("python3-pil non installato: menu senza sfondo grafico")
        return None
    t = theme(cfg)
    bg, acc, fg, muted = _rgb(t["bg"]), _rgb(t["accent"]), _rgb(t["fg"]), _rgb(t["muted"])
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    # leggera sfumatura verticale + bagliore ciano in alto a sinistra
    top = _mix(bg, (255, 255, 255), 0.05)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=_mix(top, bg, y / H))
    glow = Image.new("RGB", (W, H), bg)
    gd = ImageDraw.Draw(glow)
    for r in range(420, 0, -6):
        gd.ellipse([48 - r, 40 - r, 48 + r, 40 + r], fill=_mix(bg, acc, 0.10 * (1 - r / 420)))
    img = Image.blend(img, glow, 0.6)
    d = ImageDraw.Draw(img)
    # griglia discreta
    grid = _mix(bg, fg, 0.05)
    for x in range(0, W, 64):
        d.line([(x, 0), (x, H)], fill=grid)
    for y in range(0, H, 64):
        d.line([(0, y), (W, y)], fill=grid)
    try:
        f_logo = ImageFont.truetype(FONT_BOLD, 46)
        f_sub = ImageFont.truetype(FONT_REG, 18)
        f_small = ImageFont.truetype(FONT_MONO, 15)
    except OSError:
        f_logo = f_sub = f_small = ImageFont.load_default()
    # logo a sinistra, sottotitolo e IP a destra, riga di accento
    d.text((48, 24), t["logo_text"], font=f_logo, fill=fg)
    lw = d.textlength(t["logo_text"], font=f_logo)
    d.rectangle([48, 78, 48 + lw, 81], fill=acc)
    right = f"{t['subtitle']}   ·   http://{server_ip}/" if server_ip else t["subtitle"]
    rw = d.textlength(right, font=f_sub)
    d.text((W - 48 - rw, 44), right, font=f_sub, fill=muted)
    d.line([(48, 92), (W - 48, 92)], fill=_mix(bg, acc, 0.35), width=1)
    # piede
    foot = "↑ ↓ scegli    Invio avvia    Esc esci"
    d.text((48, H - 40), foot, font=f_small, fill=muted)
    tail = "Pixio PXE"
    tw = d.textlength(tail, font=f_small)
    d.text((W - 48 - tw, H - 40), tail, font=f_small, fill=_mix(muted, acc, 0.5))
    d.line([(48, H - 52), (W - 48, H - 52)], fill=_mix(bg, acc, 0.25), width=1)
    path = bg_path()
    with _lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        img.save(tmp, "PNG", optimize=True)
        os.replace(tmp, path)
        os.chmod(path, 0o644)
    return path


def ensure_background(cfg, server_ip):
    """Genera lo sfondo se manca (chiamata pigra dal menu)."""
    p = bg_path()
    if not os.path.isfile(p):
        try:
            render_background(cfg, server_ip)
        except Exception as e:  # noqa
            log.warning("sfondo non generato: %s", e)
    return os.path.isfile(p)


def ipxe_header(cfg, server_ip):
    """Righe iPXE che applicano il tema (framebuffer con sfondo; in modalita' testo restano i colori base)."""
    t = theme(cfg)
    m = MARGINS
    lines = []
    # Con lo sfondo grafico iPXE ridisegna l'immagine a ogni spostamento della selezione: bello ma lento
    # sui PC reali, soprattutto in UEFI. Con lo stile "testo" restano solo i colori e il menu è immediato.
    if t["style"] == "grafico" and ensure_background(cfg, server_ip):
        lines.append(f"console --picture {bg_url(server_ip)} --left {m['left']} --right {m['right']} --top {m['top']} --bottom {m['bottom']} ||")
    lines += [
        f"colour --rgb {ipxe_rgb(t['bg'])} 0 ||",        # nero   -> sfondo
        f"colour --rgb {ipxe_rgb(t['muted'])} 4 ||",     # blu    -> testo attenuato
        f"colour --rgb {ipxe_rgb(t['accent'])} 6 ||",    # ciano  -> accento
        f"colour --rgb {ipxe_rgb(t['fg'])} 7 ||",        # bianco -> testo
        "cpair --foreground 7 --background 0 0 ||",      # default
        "cpair --foreground 7 --background 0 1 ||",      # normale
        "cpair --foreground 0 --background 6 2 ||",      # voce selezionata: scuro su ciano
        "cpair --foreground 6 --background 0 3 ||",      # separatori / gruppi
        "cpair --foreground 0 --background 7 4 ||",      # campo di modifica
        "cpair --foreground 7 --background 1 5 ||",      # avvisi
        "cpair --foreground 6 --background 0 6 ||",      # URL
        "cpair --foreground 7 --background 0 7 ||",      # PXE
    ]
    return lines
