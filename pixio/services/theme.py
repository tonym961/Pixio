"""Tema del menu di boot iPXE: stile della console, sfondo PNG generato dal server e colori del menu.

Perche' lo stile della console conta (misure in QEMU, BIOS e UEFI, 11 voci, 5 ripetizioni):
a ogni spostamento della selezione iPXE riscrive due righe intere, cioe' 168 operazioni di console
in modalita' testo e 214 in grafica. Dove finiscono quelle operazioni decide tutto.
  * console del firmware: ogni carattere e' una chiamata al firmware (ConOut->OutputString in UEFI,
    fino a 3 INT 10h in modo reale nel BIOS). Su un firmware lento - console grafica UEFI, oppure
    Console Redirection / Serial-over-LAN / BMC / AMT attivi - bastano 20-30 ms per chiamata per
    arrivare ai ~5 secondi per tasto segnalati su PC reali.
  * framebuffer: efifb (interface/efi/efi_fbcon.c) e vesafb (arch/x86/interface/pcbios/vesafb.c)
    spengono la console del firmware (CONSOLE_DISABLED_OUTPUT) e scrivono direttamente in memoria
    video. Nelle misure: 0 byte verso il firmware per tasto, 29-40 ms per tasto in tutte le prove.
Quindi lo sfondo non rallenta: al contrario, e' il modo grafico che elimina il percorso lento.
Gli stili "testo" e "grafico" usano entrambi il framebuffer; "compatibile" e' la vecchia console di
testo del firmware, da tenere solo se sul PC il framebuffer non parte.
"""
import glob
import logging
import os
import re
import threading

from .. import config as C

log = logging.getLogger("pixio.theme")
BG_DIR = "theme"                              # -> HTTP_INJECT_DIR/theme = /pxe/inject/theme
BG_FMT = BG_DIR + "/bg-%dx%d.png"             # la risoluzione sta nel nome: niente sfondo vecchio riusato
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
STYLES = ("testo", "grafico", "compatibile")
# 4:3 in tutti i casi. Tempi misurati (BIOS/UEFI): comparsa del menu con lo sfondo 0,46/0,32 s a
# 1024x768, 0,33/0,26 a 800x600, 0,23/0,20 a 640x480; per tasto 38/38, 33/33, 30/29 ms.
RESOLUTIONS = ("1024x768", "800x600", "640x480")
DEFAULT_RES = "1024x768"
DEFAULT_THEME = {"bg": "#0B1220", "accent": "#3FC1CF", "fg": "#E6ECF2", "muted": "#7C8A99",
                 "logo_text": "PIXIO", "subtitle": "Avvio da rete", "style": "testo",
                 "resolution": DEFAULT_RES}
# margini della cornice di testo (in pixel a 1024x768, riscalati alle altre risoluzioni)
MARGINS = {"grafico": {"left": 48, "right": 48, "top": 104, "bottom": 60},   # spazio per intestazione e piede disegnati
           "testo": {"left": 24, "right": 24, "top": 24, "bottom": 24}}
PALETTE_COLORS = 256      # PNG indicizzato: 1 byte per pixel invece di 3, stessa resa (errore max 7/255)
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
    t["style"] = t.get("style") if t.get("style") in STYLES else DEFAULT_THEME["style"]
    t["resolution"] = t.get("resolution") if t.get("resolution") in RESOLUTIONS else DEFAULT_RES
    t["logo_text"] = str(t["logo_text"])[:16] or "PIXIO"
    t["subtitle"] = str(t["subtitle"])[:40]
    return t


def size(t):
    """(larghezza, altezza) della risoluzione scelta."""
    w, h = str(t.get("resolution") or DEFAULT_RES).split("x")
    return int(w), int(h)


def margins(t):
    """Margini della cornice di testo, riscalati alla risoluzione scelta."""
    w, _ = size(t)
    base = MARGINS["grafico"] if t["style"] == "grafico" else MARGINS["testo"]
    return {k: int(round(v * w / 1024.0)) for k, v in base.items()}


def ipxe_rgb(h):
    return "0x" + h.lstrip("#").lower()


def bg_rel(t):
    """Percorso relativo dello sfondo per la risoluzione scelta."""
    return BG_FMT % size(t)


def bg_path(t):
    return os.path.join(C.HTTP_INJECT_DIR, bg_rel(t))


def bg_web(t):
    """URL relativo dello sfondo (per l'anteprima nella GUI)."""
    return "/pxe/inject/" + bg_rel(t)


def bg_url(t, server_ip):
    return f"http://{server_ip}/pxe/inject/{bg_rel(t)}"


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def render_background(cfg, server_ip=""):
    """Genera lo sfondo alla risoluzione del tema. Ritorna il percorso o None se PIL manca."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("python3-pil non installato: menu senza sfondo grafico")
        return None
    t = theme(cfg)
    W, H = size(t)
    s = W / 1024.0                                   # tutto il disegno e' pensato a 1024x768
    def px(v):
        return int(round(v * s))
    bg, acc, fg, muted = _rgb(t["bg"]), _rgb(t["accent"]), _rgb(t["fg"]), _rgb(t["muted"])
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    # leggera sfumatura verticale + bagliore ciano in alto a sinistra
    top = _mix(bg, (255, 255, 255), 0.05)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=_mix(top, bg, y / H))
    glow = Image.new("RGB", (W, H), bg)
    gd = ImageDraw.Draw(glow)
    r0 = px(420)
    for r in range(r0, 0, -max(1, px(6))):
        gd.ellipse([px(48) - r, px(40) - r, px(48) + r, px(40) + r], fill=_mix(bg, acc, 0.10 * (1 - r / r0)))
    img = Image.blend(img, glow, 0.6)
    d = ImageDraw.Draw(img)
    # griglia discreta
    grid = _mix(bg, fg, 0.05)
    for x in range(0, W, px(64)):
        d.line([(x, 0), (x, H)], fill=grid)
    for y in range(0, H, px(64)):
        d.line([(0, y), (W, y)], fill=grid)
    try:
        f_logo = ImageFont.truetype(FONT_BOLD, px(46))
        f_sub = ImageFont.truetype(FONT_REG, px(18))
        f_small = ImageFont.truetype(FONT_MONO, px(15))
    except OSError:
        f_logo = f_sub = f_small = ImageFont.load_default()
    # logo a sinistra, sottotitolo e IP a destra, riga di accento
    d.text((px(48), px(24)), t["logo_text"], font=f_logo, fill=fg)
    lw = d.textlength(t["logo_text"], font=f_logo)
    d.rectangle([px(48), px(78), px(48) + lw, px(81)], fill=acc)
    right = f"{t['subtitle']}   ·   http://{server_ip}/" if server_ip else t["subtitle"]
    rw = d.textlength(right, font=f_sub)
    d.text((W - px(48) - rw, px(44)), right, font=f_sub, fill=muted)
    d.line([(px(48), px(92)), (W - px(48), px(92))], fill=_mix(bg, acc, 0.35), width=1)
    # piede
    foot = "↑ ↓ scegli    Invio avvia    Esc esci"
    d.text((px(48), H - px(40)), foot, font=f_small, fill=muted)
    tail = "Pixio PXE"
    tw = d.textlength(tail, font=f_small)
    d.text((W - px(48) - tw, H - px(40)), tail, font=f_small, fill=_mix(muted, acc, 0.5))
    d.line([(px(48), H - px(52)), (W - px(48), H - px(52))], fill=_mix(bg, acc, 0.25), width=1)
    # PNG indicizzato: un terzo dei dati da scompattare in iPXE e ~27% di file in meno,
    # cosi' lo sfondo non pesa sulla comparsa del menu (errore massimo 7/255, invisibile).
    img = img.convert("P", palette=Image.Palette.ADAPTIVE, colors=PALETTE_COLORS)
    path = bg_path(t)
    with _lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        img.save(tmp, "PNG", optimize=True)
        os.replace(tmp, path)
        os.chmod(path, 0o644)
        for old in glob.glob(os.path.join(C.HTTP_INJECT_DIR, BG_DIR, "bg*.png")):
            if old != path:
                try:
                    os.remove(old)          # sfondi di risoluzioni non piu' usate
                except OSError:
                    pass
    return path


def ensure_background(cfg, server_ip):
    """Genera lo sfondo se manca (chiamata pigra dal menu)."""
    t = theme(cfg)
    p = bg_path(t)
    if not os.path.isfile(p):
        try:
            render_background(cfg, server_ip)
        except Exception as e:  # noqa
            log.warning("sfondo non generato: %s", e)
    return os.path.isfile(p)


def console_lines(cfg, server_ip):
    """Righe 'console' che scelgono il percorso di disegno (vedi la nota in cima al modulo)."""
    t = theme(cfg)
    if t["style"] == "compatibile":
        return []                                    # console di testo del firmware, come prima
    w, h = size(t)
    m = margins(t)
    geom = f"-x {w} -y {h} --left {m['left']} --right {m['right']} --top {m['top']} --bottom {m['bottom']}"
    if t["style"] != "grafico" or not ensure_background(cfg, server_ip):
        return [f"console {geom} ||"]
    # Rete di sicurezza sulla stessa riga: se lo sfondo non arriva o non si decodifica, iPXE non
    # riconfigura la console (console_cmd.c esce prima di console_configure) e con "||" esegue il
    # secondo comando, restando comunque sul framebuffer invece di ricadere sulla console lenta del
    # firmware. Sulla stessa riga non costa nulla quando l'immagine c'e' (una riga a parte pesava
    # 30-60 ms in piu' su ogni comparsa del menu, misurati in QEMU).
    plain = margins(dict(t, style="testo"))
    return [f"console {geom} --picture {bg_url(t, server_ip)} || "
            f"console -x {w} -y {h} --left {plain['left']} --right {plain['right']} --top {plain['top']} --bottom {plain['bottom']} ||"]


def ipxe_header(cfg, server_ip):
    """Righe iPXE che applicano il tema: console (framebuffer o firmware) e colori del menu."""
    lines = console_lines(cfg, server_ip)
    t = theme(cfg)
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
