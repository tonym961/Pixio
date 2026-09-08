"""Endpoint pubblici per i client PXE: /boot.ipxe e /boot/<slug>.ipxe."""
import logging
import re

from flask import Blueprint, Response, request

from .. import config as C
from .. import settings as S
from ..services import ipxe_menu

bp = Blueprint("boot", __name__)
log = logging.getLogger("pixio.boot")
MAC_RE = re.compile(r"^[0-9a-fA-F]{2}([-:][0-9a-fA-F]{2}){5}$")


def _ipv4(v):
    import ipaddress
    try:
        return str(ipaddress.IPv4Address(v))
    except Exception:
        return ""


def _mac():
    m = request.args.get("mac", "")
    return m.lower().replace("-", ":") if MAC_RE.match(m) else ""


def _text(body):
    return Response(body, mimetype="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})


@bp.route("/boot.ipxe")
def boot_menu():
    cfg = S.load()
    ip = cfg["network"]["server_ip"]
    platform = request.args.get("platform", "")
    if not platform and not request.args.get("preview"):
        return _text(ipxe_menu.bootstrap_script(ip))
    mac = _mac()
    auto = None
    if mac:
        try:
            from ..services import clients
            c = clients.get(mac)
            if c and c.get("auto_boot"):
                auto = c["auto_boot"]
            rs = getattr(clients, "record_seen", None)
            if rs:
                rs(mac, ip=_ipv4(request.args.get("ip", "")) or request.remote_addr, arch=request.args.get("arch", "")[:16],
                   platform=platform[:8], manuf=request.args.get("manuf", "")[:40], product=request.args.get("product", "")[:40])
        except Exception as e:  # noqa
            log.debug("clients: %s", e)
    return _text(ipxe_menu.menu_script(platform or "bios", mac=mac.replace(":", "-") if mac else None, auto_boot=auto, cfg=cfg,
                                       client_ip=_ipv4(request.args.get("ip", ""))))


@bp.route("/boot/<slug>.ipxe")
def boot_entry(slug):
    if not C.SLUG_RE.match(slug):
        return _text("#!ipxe\necho slug non valido\nexit 1\n"), 400
    platform = request.args.get("platform", "bios")
    mac = _mac()
    text, warnings = ipxe_menu.entry_script(slug, platform)
    if mac:
        try:
            from ..services import clients
            clients.record_boot(mac, slug)
        except Exception as e:  # noqa
            log.debug("record_boot: %s", e)
    log.info("boot %s da %s (%s) %s", slug, request.remote_addr, platform, "; ".join(warnings))
    return _text(text)


@bp.route("/boot/inject/<slug>/<name>")
def boot_inject(slug, name):
    """File iniettati nel WinPE via wimboot, generati al volo (sempre aggiornati a impostazioni e driver)."""
    if not C.SLUG_RE.match(slug):
        return _text("slug non valido"), 400
    from ..services import winpe
    if name == "winpeshl.ini":
        return _text(winpe.winpeshl_ini())
    if name == "install.cmd":
        return _text(winpe.install_cmd(slug))
    return _text("file non previsto"), 404
