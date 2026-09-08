"""Accesso alle impostazioni (/etc/pixio/config.json) con default e rilevamento rete."""
import copy
import os
import socket
import struct
import fcntl

from . import config as C
from .storage import read_json, write_json, deep_merge


def load():
    cfg = deep_merge(C.DEFAULT_CONFIG, read_json(C.CONFIG_FILE, {}))
    if not cfg["network"]["interface"] or not cfg["network"]["server_ip"]:
        iface, ip = detect_primary()
        cfg["network"]["interface"] = cfg["network"]["interface"] or iface
        cfg["network"]["server_ip"] = cfg["network"]["server_ip"] or ip
    return cfg


def save(cfg):
    write_json(C.CONFIG_FILE, cfg, mode=0o640)


def detect_primary():
    """Interfaccia e IP della default route (senza dipendenze esterne)."""
    iface, ip = "", ""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if parts[1] == "00000000":
                    iface = parts[0]
                    break
    except OSError:
        pass
    if iface:
        ip = iface_ip(iface)
    return iface, ip


def iface_ip(iface):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", iface[:15].encode()))[20:24])
    except OSError:
        return ""


def list_interfaces():
    out = []
    for name in sorted(os.listdir("/sys/class/net")):
        if name == "lo":
            continue
        out.append({"name": name, "ip": iface_ip(name)})
    return out
