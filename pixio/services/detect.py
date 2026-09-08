"""Rilevamento del tipo di ISO: mount temporaneo (helper), indice dei file (case-insensitive), match con le ricette."""
import fnmatch
import logging
import os
import re
import subprocess

from .. import config as C
from .. import privileged
from . import recipes

log = logging.getLogger("pixio.detect")
MAX_DEPTH = 4
MAX_ENTRIES = 20000


def iso_label(path):
    """Volume id ISO9660 dal Primary Volume Descriptor (offset 32768+40, 32 byte)."""
    try:
        with open(path, "rb") as f:
            f.seek(32768)
            pvd = f.read(2048)
        if pvd[1:6] == b"CD001":
            return pvd[40:72].decode("ascii", "ignore").strip()
    except OSError:
        pass
    return ""


def build_index(root):
    """{percorso_relativo_minuscolo: percorso_relativo_reale} fino a MAX_DEPTH livelli."""
    index = {}
    root = root.rstrip("/")
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count("/") + 1
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        for name in list(dirnames) + filenames:
            r = name if rel == "." else f"{rel}/{name}"
            index[r.lower()] = r
            n += 1
            if n > MAX_ENTRIES:
                return index
    return index


def resolve(index, candidates):
    """Primo candidato esistente (supporta glob '*' nel nome file)."""
    for cand in candidates:
        c = cand.lower()
        if "*" in c:
            for k in sorted(index):
                if fnmatch.fnmatchcase(k, c):
                    return index[k]
        elif c in index:
            return index[c]
    return None


def read_small(root, rel, limit=4096):
    if not rel:
        return ""
    try:
        with open(os.path.join(root, rel), "r", encoding="utf-8", errors="ignore") as f:
            return f.read(limit)
    except OSError:
        return ""


def wim_info(path):
    """Nomi/versione delle immagini in un .wim tramite wiminfo (wimtools). Ritorna (names, version)."""
    try:
        p = subprocess.run(["wiminfo", path], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return [], ""
    names, version = [], ""
    for line in p.stdout.splitlines():
        s = line.strip()
        if s.startswith("Display Name:") or s.startswith("Name:"):
            v = s.split(":", 1)[1].strip()
            if v and v not in names:
                names.append(v)
        elif s.startswith("Version:") and not version:
            version = s.split(":", 1)[1].strip()
    return names, version


def match_type(index, root):
    """Ritorna (tipo, files_risolti) per la prima ricetta compatibile, in ordine di priorità."""
    for t in recipes.types():
        if t.get("manual_only"):
            continue
        files = {}
        ok = True
        for key, cands in (t.get("files") or {}).items():
            r = resolve(index, cands)
            if r:
                files[key] = r
        for key in t.get("require", []):
            if key not in files:
                ok = False
                break
        if not ok:
            continue
        if t.get("markers_any") and not any(resolve(index, [m]) for m in t["markers_any"]):
            continue
        if t.get("markers_all") and not all(resolve(index, [m]) for m in t["markers_all"]):
            continue
        if t.get("markers_none") and any(resolve(index, [m]) for m in t["markers_none"]):
            continue
        return t, files
    return recipes.get_type("unknown"), {}


def describe(index, root, t, files, label):
    """Versione/etichetta leggibile dal contenuto (.disk/info, .treeinfo, wiminfo, volume id)."""
    version, name = "", ""
    info = read_small(root, resolve(index, [".disk/info"]))
    if info:
        name = info.strip().splitlines()[0][:120]
    ti = read_small(root, resolve(index, [".treeinfo"]), 8192)
    if ti:
        m1 = re.search(r"^name\s*=\s*(.+)$", ti, re.M)
        m2 = re.search(r"^version\s*=\s*(.+)$", ti, re.M)
        if m1:
            name = m1.group(1).strip() + (f" {m2.group(1).strip()}" if m2 else "")
    editions = []
    if t["id"] in ("windows", "winpe-tool"):
        wim = files.get("install") or files.get("bootwim")
        if wim:
            editions, version = wim_info(os.path.join(root, wim))
            if t["id"] == "windows" and editions:
                base = re.sub(r"\s+(Home|Pro|Education|Enterprise|Core|N|Single Language|for Workstations|Standard|Datacenter|Essentials|Evaluation|\(.*\))+$", "", editions[0]).strip()
                name = base or editions[0]
                # lingua/arch dal volume id: es. CCCOMA_X64FRE_IT-IT_DV9
                m = re.search(r"_(X64|X86|ARM64)FRE_([A-Z]{2}-[A-Z]{2})", label or "")
                if m:
                    name += f" {m.group(2).lower()} {m.group(1).lower()}"
            elif editions:
                name = editions[0]
    if t["id"] == "archiso" and label:
        m = re.match(r"ARCH_(\d{4})(\d{2})", label)
        name = f"Arch Linux {m.group(1)}.{m.group(2)}" if m else label.replace("_", " ")
    if t["id"] == "manjaro" and label:
        name = label.replace("_", " ")
    if t["id"] == "sysresccd":
        m = re.match(r"RESCUE(\d{2})(\d{2})", label or "")
        ver = read_small(root, resolve(index, ["sysresccd/pkglist.x86_64.txt", "sysrescue.d/version"]))
        name = f"SystemRescue {m.group(1)}.{m.group(2)}" if m else "SystemRescue"
    if t["id"] == "alpine":
        rel = read_small(root, resolve(index, [".alpine-release"]))
        m = re.search(r"(\d+\.\d+(\.\d+)?)", rel or "")
        name = f"Alpine Linux {m.group(1)}" if m else "Alpine Linux"
    if t["id"] == "clonezilla":
        v = read_small(root, resolve(index, ["Clonezilla-Live-Version"]))
        name = "Clonezilla live " + (v.strip().splitlines()[0].split()[0] if v.strip() else "")
    if t["id"] == "gparted":
        v = read_small(root, resolve(index, ["GParted-Live-Version"]))
        name = "GParted live " + (v.strip().splitlines()[0].split()[0] if v.strip() else "")
    if t["id"] == "opensuse" and label:
        name = label.replace("-", " ")
    return {"label": label, "name": name.strip(), "version": version, "editions": editions[:12]}


def detect_file(slug, path):
    """Monta la ISO in DETECT_DIR/<slug>, la ispeziona e smonta. Ritorna dict 'detect' per il catalogo."""
    label = iso_label(path)
    res = {"type": "unknown", "files": {}, "label": label, "name": "", "version": "", "editions": [], "error": ""}
    try:
        r = privileged.call("mount-detect", slug, path, timeout=120)
    except privileged.HelperError as e:
        res["error"] = f"mount fallito: {e}"
        return res
    root = r.get("mountpoint") or os.path.join(C.DETECT_DIR, slug)
    try:
        index = build_index(root)
        t, files = match_type(index, root)
        res["type"] = t["id"]
        res["files"] = files
        res.update(describe(index, root, t, files, label))
        if t["id"] == "unknown":
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            if 0 < size <= 100 * 1024 * 1024:
                # mini-ISO (DOS, diagnostica, firmware): memdisk in BIOS e' quasi sempre la scelta giusta
                res["type"] = "memdisk"
                res["name"] = res.get("name") or label
        res["top"] = sorted({k.split("/")[0] for k in index})[:40]
    except Exception as e:  # noqa
        log.exception("rilevamento %s", path)
        res["error"] = str(e)
    finally:
        try:
            privileged.call("umount-detect", slug, timeout=60)
        except privileged.HelperError as e:
            log.warning("umount-detect %s: %s", slug, e)
    return res
