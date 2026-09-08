"""Preparazione del boot ESXi: mboot.efi e boot.cfg adattati all'avvio via HTTP.

La ISO ESXi si avvia con mboot.efi (copia di efi/boot/bootx64.efi) che legge un boot.cfg.
Per l'avvio da rete il boot.cfg va riscritto: prefix= con l'URL della ISO montata, percorsi
senza slash iniziale e senza l'opzione cdromBoot (che farebbe cercare il CD).
"""
import logging
import os
import re
import shutil

from .. import config as C

log = logging.getLogger("pixio.esxi")


def prepare(slug, mountpoint, server_ip, files=None):
    """Scrive HTTP_INJECT_DIR/<slug>/{mboot.efi,boot.cfg}. Ritorna (ok, messaggio)."""
    files = files or {}
    src_efi = files.get("mboot") or "efi/boot/bootx64.efi"
    src_cfg = files.get("bootcfg") or "boot.cfg"
    efi_path = os.path.join(mountpoint, src_efi)
    cfg_path = os.path.join(mountpoint, src_cfg)
    if not os.path.isfile(efi_path) or not os.path.isfile(cfg_path):
        return False, "mboot.efi o boot.cfg non trovati nella ISO"
    dst = os.path.join(C.HTTP_INJECT_DIR, slug)
    os.makedirs(dst, exist_ok=True)
    shutil.copyfile(efi_path, os.path.join(dst, "mboot.efi"))
    os.chmod(os.path.join(dst, "mboot.efi"), 0o644)
    prefix = f"http://{server_ip}/pxe/iso/{slug}"
    out = []
    with open(cfg_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\r\n")
            key = line.split("=", 1)[0].strip().lower()
            if key == "prefix":
                continue                              # lo riscriviamo noi
            if key in ("kernel", "modules"):
                k, _, v = line.partition("=")
                v = re.sub(r"(^|\s|---\s*)/", lambda m: m.group(1), v)   # percorsi relativi al prefix
                line = f"{k}={v}"
            elif key == "kernelopt":
                k, _, v = line.partition("=")
                v = re.sub(r"\bcdromBoot\b", "", v).strip()
                line = f"{k}={v}"
            out.append(line)
    out.insert(0, f"prefix={prefix}")
    with open(os.path.join(dst, "boot.cfg"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(os.path.join(dst, "boot.cfg"), 0o644)
    log.info("ESXi %s: mboot.efi e boot.cfg preparati (prefix %s)", slug, prefix)
    return True, "ok"
