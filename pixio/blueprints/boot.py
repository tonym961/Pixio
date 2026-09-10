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


def _nome_iso(slug):
    """Nome della ISO nel catalogo (per i log): lo slug se il catalogo non dice niente."""
    try:
        from ..services import catalog
        e = (catalog.load() or {}).get("isos", {}).get(slug) or {}
        return e.get("name") or e.get("file") or slug
    except Exception:  # noqa: BLE001
        return slug


def _text(body):
    return Response(body, mimetype="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})


@bp.route("/autoexec.ipxe")
def autoexec():
    """iPXE cerca questo file accanto a se stesso appena parte: gli diamo lo stesso avvio dello
    script incorporato, così non compare l'errore 'autoexec.ipxe not found'."""
    cfg = S.load()
    return _text(ipxe_menu.bootstrap_script(cfg["network"]["server_ip"]))


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
            take_once = getattr(clients, "take_boot_once", None)
            once = take_once(mac) if take_once else None
            c = clients.get(mac)
            if once:
                auto = once                      # valido per questo solo avvio
            elif c and c.get("auto_boot"):
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
    # ?answer=<id> = installazione scelta nel menu, ?answer= (vuoto) = avvio a mano, parametro assente = da scegliere
    answer = request.args.get("answer")[:64] if "answer" in request.args else None
    text, warnings = ipxe_menu.entry_script(slug, platform, answer=answer, mac=mac.replace(":", "-") if mac else None)
    if mac:
        try:
            from ..services import clients
            clients.record_boot(mac, slug)
        except Exception as e:  # noqa
            log.debug("record_boot: %s", e)
    log.info("boot %s da %s (%s)%s %s", slug, request.remote_addr, platform,
             f" risposta={answer}" if answer is not None else "", "; ".join(warnings))
    return _text(text)


@bp.route("/boot/answer/<slug>/<answer_id>/autounattend.xml")
def boot_answer(slug, answer_id):
    """autounattend.xml generato adesso, per la coppia (ISO che si sta avviando, risposta).

    Il file salvato in /var/lib/pixio/answers/<id>/ è uno solo, ma la stessa risposta compare nel
    menu di ISO diverse: l'edizione scritta lì dentro può non esistere nell'immagine che parte, e
    da quando install.cmd passa il file al setup con /unattend: quel valore ferma l'installazione
    invece di essere ignorato. Qui l'XML si rigenera dal profilo che ha creato la risposta con le
    edizioni di questa ISO (docs/API.md, sezione 20). Il file statico non viene toccato: resta
    scaricabile e modificabile dalla GUI, ed è quello che si serve quando la risposta non nasce da
    un profilo o il profilo è stato cancellato."""
    if not C.SLUG_RE.match(slug):
        return _text("slug non valido"), 400
    from ..services import answers, winprofile
    a = answers.get(answer_id)
    if not a:
        return _text("risposta non trovata"), 404

    def statico(motivo):
        """Ripiego: il contenuto salvato della risposta, esattamente come lo serve /answers/."""
        log.info("risposta %s per %s servita dal file salvato (%s)", a["id"], slug, motivo)
        try:
            return _text(answers.read_content(a["id"]))
        except (FileNotFoundError, ValueError, OSError) as e:
            log.warning("risposta %s: file non leggibile (%s)", a["id"], e)
            return _text("file non leggibile"), 404

    prof = winprofile.profile_for_answer(a)
    if not prof:
        return statico("nessun profilo collegato")
    edizioni = winprofile.editions_for_iso(slug)
    voluta = str((prof.get("settings") or {}).get("edition_index") or "")
    if edizioni and voluta and winprofile.match_edition(edizioni, voluta) is None:
        log.warning("risposta %s su %s: il profilo \"%s\" chiede l'edizione \"%s\", che in questa "
                    "immagine non c'è (presenti: %s); nell'XML non viene scritta così com'è",
                    a["id"], _nome_iso(slug), prof["name"], voluta,
                    winprofile.editions_labels(edizioni))
    try:
        xml = winprofile.render_autounattend(prof, S.load()["network"]["server_ip"],
                                             editions=edizioni)
    except Exception as e:  # noqa: BLE001 - un profilo illeggibile non deve lasciare il PC senza file
        log.error("risposta %s su %s: generazione fallita (%s)", a["id"], slug, e)
        return statico("generazione fallita")
    log.info("risposta %s generata per %s dal profilo %s, servita a %s",
             a["id"], slug, prof["id"], request.remote_addr)
    return _text(xml)


@bp.route("/boot/inject/<slug>/<name>")
def boot_inject(slug, name):
    """File iniettati nel WinPE via wimboot, generati al volo (sempre aggiornati a impostazioni e driver)."""
    if not C.SLUG_RE.match(slug):
        return _text("slug non valido"), 400
    from ..services import winpe
    if name == "winpeshl.ini":
        return _text(winpe.winpeshl_ini())
    if name == "install.cmd":
        # chi lo sta scaricando e' il PC che sta per installare: con il suo indirizzo e l'ora di adesso
        # lo script sa gia' come chiamare la cartella in cui depositera' i log (docs/API.md, sezione 22)
        return _text(winpe.install_cmd(slug, client_ip=request.remote_addr or ""))
    return _text("file non previsto"), 404
