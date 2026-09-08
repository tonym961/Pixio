"""Test di Wake-on-LAN e avvio una tantum (sezione 4 del contratto API).

Esecuzione: cd /opt/pixio && python3 -m unittest tests.test_clients_wol
Nessun pacchetto viene spedito davvero: socket.socket e fcntl.ioctl sono sostituiti da finti,
come l'helper privilegiato. Configurazione e stato stanno in una cartella temporanea.
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from unittest import mock

os.environ["PIXIO_NO_BACKGROUND"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pixio import config as C  # noqa: E402
from pixio import privileged  # noqa: E402

MAC = "d8:bb:c1:4a:20:7e"
RAW = bytes.fromhex("d8bbc14a207e")


def fake_call(*args, stdin_text=None, timeout=180):
    if args and args[0] == "list-mounts":
        return {"iso": {}, "sources": {}}
    return {"ok": True}


OVERRIDE_KEYS = ("ETC_DIR", "CONFIG_FILE", "SECRET_FILE", "VAR_DIR", "JOBS_DIR", "UPLOAD_TMP_DIR",
                 "CLIENTS_FILE", "CATALOG_FILE", "LOG_DIR", "SRV_DIR", "LIBRARY_DIR", "CACHE_DIR",
                 "SOURCES_MOUNT_DIR", "HTTP_ISO_DIR")

TMP = None
SAVED = {}


def setUpModule():
    _iso_setup()
    """Reindirizza i percorsi di config in una cartella temporanea (ripristinati in tearDownModule),
    così il modulo convive con gli altri test nella stessa discovery."""
    global TMP
    TMP = tempfile.mkdtemp(prefix="pixio-wol-")
    SAVED.update({k: getattr(C, k) for k in OVERRIDE_KEYS})
    C.ETC_DIR = os.path.join(TMP, "etc")
    C.CONFIG_FILE = os.path.join(C.ETC_DIR, "config.json")
    C.SECRET_FILE = os.path.join(C.ETC_DIR, "secret")
    C.VAR_DIR = os.path.join(TMP, "var")
    C.JOBS_DIR = os.path.join(C.VAR_DIR, "jobs")
    C.UPLOAD_TMP_DIR = os.path.join(C.VAR_DIR, "uploads")
    C.CLIENTS_FILE = os.path.join(C.VAR_DIR, "clients.json")
    C.CATALOG_FILE = os.path.join(C.VAR_DIR, "catalog.json")
    C.LOG_DIR = os.path.join(TMP, "log")
    C.SRV_DIR = os.path.join(TMP, "srv")
    C.LIBRARY_DIR = os.path.join(C.SRV_DIR, "library")
    C.CACHE_DIR = os.path.join(C.SRV_DIR, "cache")
    C.SOURCES_MOUNT_DIR = os.path.join(C.SRV_DIR, "sources")
    C.HTTP_ISO_DIR = os.path.join(C.SRV_DIR, "http", "iso")
    for d in (C.ETC_DIR, C.VAR_DIR, C.JOBS_DIR, C.UPLOAD_TMP_DIR, C.LOG_DIR, C.LIBRARY_DIR, C.CACHE_DIR, C.HTTP_ISO_DIR):
        os.makedirs(d, exist_ok=True)
    with open(C.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"network": {"interface": "ens18", "server_ip": "10.10.0.254", "dhcp_mode": "proxy"}}, f)
    with open(C.CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump({"isos": {"alpine": {"slug": "alpine", "name": "Alpine Linux", "source": "local", "file": "alpine.iso",
                                       "rel_path": "alpine.iso", "path": os.path.join(C.LIBRARY_DIR, "alpine.iso"),
                                       "enabled": True, "group": "Linux", "order": 1, "type": "alpine",
                                       "custom_recipe": None, "detect": {}}},
                   "last_scan": "2026-09-08T19:00:00"}, f)
    globals()["_orig_call"] = privileged.call
    privileged.call = fake_call


def tearDownModule():
    _iso_teardown()
    privileged.call = globals().get("_orig_call", privileged.call)
    for k, v in SAVED.items():
        setattr(C, k, v)
    shutil.rmtree(TMP, ignore_errors=True)


from pixio.services import clients  # noqa: E402

class FakeSocket:
    """Sostituto di socket.socket: registra i sendto senza toccare la rete."""
    sent = []          # [(dati, (indirizzo, porta))]
    opts = []          # [(livello, opzione, valore)]

    def __init__(self, family=None, type=None, proto=0):
        self.closed = False

    @classmethod
    def reset(cls):
        cls.sent, cls.opts = [], []

    def setsockopt(self, level, opt, value):
        FakeSocket.opts.append((level, opt, value))

    def sendto(self, data, addr):
        FakeSocket.sent.append((data, addr))
        return len(data)

    def fileno(self):
        return -1      # ogni ioctl fallisce: si prende il broadcast di ripiego

    def close(self):
        self.closed = True


def fake_ioctl_for(ip, netmask):
    def _ioctl(fd, op, arg):
        if op == clients.SIOCGIFADDR:
            return b"\x00" * 20 + socket.inet_aton(ip)
        if op == clients.SIOCGIFNETMASK:
            return b"\x00" * 20 + socket.inet_aton(netmask)
        raise OSError("ioctl non previsto")
    return _ioctl


class MagicPacketTest(unittest.TestCase):
    def setUp(self):
        FakeSocket.reset()

    def test_packet_content_and_ports(self):
        with mock.patch.object(clients.socket, "socket", FakeSocket):
            sent = clients.wake("D8-BB-C1-4A-20-7E")       # MAC in un formato qualsiasi
        self.assertEqual(sent, 2)
        self.assertEqual(len(FakeSocket.sent), 2)
        pkt, addr = FakeSocket.sent[0]
        self.assertEqual(len(pkt), 6 + 16 * 6)
        self.assertEqual(pkt[:6], b"\xff" * 6)
        self.assertEqual(pkt[6:], RAW * 16)
        self.assertEqual(pkt, clients.magic_packet(MAC))
        self.assertEqual([a[1] for _, a in FakeSocket.sent], [9, 7])
        self.assertEqual(addr[0], clients.BROADCAST_FALLBACK)   # senza ioctl si usa il ripiego
        self.assertTrue(all(p == pkt for p, _ in FakeSocket.sent))
        self.assertIn((socket.SOL_SOCKET, socket.SO_BROADCAST, 1), FakeSocket.opts)

    def test_broadcast_esplicito_e_mac_non_valido(self):
        with mock.patch.object(clients.socket, "socket", FakeSocket):
            clients.wake(MAC, "10.10.0.255")
            self.assertRaises(ValueError, clients.wake, "non-un-mac")
        self.assertEqual({a[0] for _, a in FakeSocket.sent}, {"10.10.0.255"})

    def test_invio_fallito(self):
        class Broken(FakeSocket):
            def sendto(self, data, addr):
                raise OSError("rete non raggiungibile")
        with mock.patch.object(clients.socket, "socket", Broken):
            with self.assertRaises(privileged.HelperError) as cm:
                clients.wake(MAC, "10.10.0.255")
        self.assertIn("magic packet", str(cm.exception))

    def test_wake_many(self):
        with mock.patch.object(clients.socket, "socket", FakeSocket):
            res = clients.wake_many(["D8-BB-C1-4A-20-7E", "52:54:00:12:34:56", "zzz"])
        self.assertEqual(res[MAC], True)
        self.assertEqual(res["52:54:00:12:34:56"], True)
        self.assertEqual(res["zzz"], False)
        self.assertEqual(len(FakeSocket.sent), 4)      # due MAC validi x due porte


class BroadcastTest(unittest.TestCase):
    def test_calcolo_dal_ioctl(self):
        with mock.patch.object(clients.socket, "socket", FakeSocket), \
             mock.patch.object(clients.fcntl, "ioctl", fake_ioctl_for("10.10.0.254", "255.255.255.0")):
            self.assertEqual(clients._broadcast_for("ens18"), "10.10.0.255")
            self.assertEqual(clients.broadcast_address(), "10.10.0.255")   # interfaccia da impostazioni
        with mock.patch.object(clients.socket, "socket", FakeSocket), \
             mock.patch.object(clients.fcntl, "ioctl", fake_ioctl_for("172.16.5.9", "255.255.0.0")):
            self.assertEqual(clients._broadcast_for("ens18"), "172.16.255.255")

    def test_ripiego(self):
        self.assertEqual(clients._broadcast_for(""), clients.BROADCAST_FALLBACK)
        with mock.patch.object(clients.socket, "socket", FakeSocket):     # fileno() = -1: ioctl fallisce
            self.assertEqual(clients._broadcast_for("ens18"), clients.BROADCAST_FALLBACK)

    def test_wake_usa_il_broadcast_dell_interfaccia(self):
        FakeSocket.reset()
        with mock.patch.object(clients.socket, "socket", FakeSocket), \
             mock.patch.object(clients.fcntl, "ioctl", fake_ioctl_for("10.10.0.254", "255.255.255.0")):
            clients.wake(MAC)
        self.assertEqual({a[0] for _, a in FakeSocket.sent}, {"10.10.0.255"})


class BootOnceTest(unittest.TestCase):
    def setUp(self):
        with open(C.CLIENTS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        clients.record_seen(MAC, ip="10.10.0.57", platform="efi", arch="x86_64")

    def test_imposta_consegna_azzera(self):
        c = clients.set_boot_once(MAC, "alpine")
        self.assertEqual(c["boot_once"], "alpine")
        self.assertEqual(clients.get(MAC)["boot_once"], "alpine")
        self.assertEqual(clients.list_clients()[0]["boot_once"], "alpine")
        self.assertIs(clients.list_clients()[0]["wol_supported"], True)
        self.assertEqual(clients.take_boot_once(MAC), "alpine")     # consegnato...
        self.assertIsNone(clients.get(MAC)["boot_once"])            # ...e azzerato
        self.assertIsNone(clients.take_boot_once(MAC))              # vale per un solo avvio

    def test_precedenza_su_auto_boot(self):
        clients.update(MAC, {"auto_boot": "alpine"})
        clients.set_boot_once(MAC, "alpine")
        once = clients.take_boot_once(MAC)
        self.assertEqual(once, "alpine")                            # boot.py preferisce questo...
        self.assertEqual(clients.get(MAC)["auto_boot"], "alpine")   # ...e l'avvio automatico resta

    def test_annullamento_e_validazione(self):
        clients.set_boot_once(MAC, "alpine")
        self.assertIsNone(clients.set_boot_once(MAC, None)["boot_once"])
        with self.assertRaises(ValueError):
            clients.set_boot_once(MAC, "non-esiste")
        with self.assertRaises(ValueError):
            clients.set_boot_once(MAC, "SLUG NON VALIDO")
        with self.assertRaises(KeyError):
            clients.set_boot_once("52:54:00:99:99:99", "alpine")
        self.assertIsNone(clients.take_boot_once("non-un-mac"))
        self.assertIsNone(clients.take_boot_once("52:54:00:99:99:99"))


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pixio import create_app
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        r = cls.client.post("/api/auth/login", json={"password": "segreta1"})
        assert r.status_code == 200, r.get_json()
        cls.h = {"X-CSRF-Token": r.get_json()["csrf"]}

    def setUp(self):
        FakeSocket.reset()
        with open(C.CLIENTS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        clients.record_seen(MAC, ip="10.10.0.57", platform="efi", arch="x86_64")

    def test_lista_contiene_boot_once_e_wol(self):
        d = self.client.get("/api/clients").get_json()
        self.assertEqual(len(d), 1)
        self.assertIn("boot_once", d[0])
        self.assertIs(d[0]["wol_supported"], True)

    def test_patch_boot_once(self):
        r = self.client.patch(f"/api/clients/{MAC}", json={"boot_once": "non-esiste"}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        self.assertIn("inesistente", r.get_json()["error"])
        r = self.client.patch(f"/api/clients/{MAC}", json={"boot_once": "alpine"}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["boot_once"], "alpine")
        r = self.client.patch(f"/api/clients/{MAC}", json={"boot_once": None}, headers=self.h)
        self.assertIsNone(r.get_json()["boot_once"])
        r = self.client.patch("/api/clients/52:54:00:99:99:99", json={"boot_once": "alpine"}, headers=self.h)
        self.assertEqual(r.status_code, 404)

    def test_wake_singolo(self):
        with mock.patch.object(clients.socket, "socket", FakeSocket):
            r = self.client.post(f"/api/clients/{MAC}/wake", headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json(), {"ok": True, "sent": 2})
        self.assertEqual(len(FakeSocket.sent), 2)
        r = self.client.post("/api/clients/non-un-mac/wake", headers=self.h)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.post(f"/api/clients/{MAC}/wake").status_code, 403)   # senza CSRF

    def test_wake_multiplo(self):
        with mock.patch.object(clients.socket, "socket", FakeSocket):
            r = self.client.post("/api/clients/wake", json={"macs": [MAC, "52:54:00:12:34:56", "zzz"]}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["results"], {MAC: True, "52:54:00:12:34:56": True, "zzz": False})
        self.assertEqual(self.client.post("/api/clients/wake", json={}, headers=self.h).status_code, 400)
        self.assertEqual(self.client.post("/api/clients/wake", json={"macs": []}, headers=self.h).status_code, 400)

    def test_boot_ipxe_usa_boot_once(self):
        """Il menu pubblico consuma la voce una tantum e la preferisce all'avvio automatico."""
        self.client.patch(f"/api/clients/{MAC}", json={"auto_boot": "alpine"}, headers=self.h)
        self.client.patch(f"/api/clients/{MAC}", json={"boot_once": "alpine"}, headers=self.h)
        r = self.app.test_client().get(f"/boot.ipxe?platform=efi&mac={MAC.replace(':', '-')}")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(clients.get(MAC)["boot_once"])      # consegnata e azzerata
        self.assertEqual(clients.get(MAC)["auto_boot"], "alpine")


if __name__ == "__main__":
    unittest.main()

# --------------------------------------------------------------------------
# Isolamento fra moduli di test: ogni modulo imposta pixio.config e il finto di
# pixio.privileged al proprio import. Eseguendo piu' moduli nello stesso processo
# l'ultimo import vincerebbe su tutti: qui i valori di QUESTO modulo vengono
# riapplicati prima dei suoi test e ripristinati alla fine.
_ISO_CONF = {k: v for k, v in vars(C).items() if k.isupper()}
_ISO_CALL = privileged.call
_ISO_PREV = {}


def _iso_setup():
    _ISO_PREV.clear()
    _ISO_PREV.update({k: getattr(C, k, None) for k in _ISO_CONF})
    _ISO_PREV["__call__"] = privileged.call
    for k, v in _ISO_CONF.items():
        setattr(C, k, v)
    privileged.call = _ISO_CALL


def _iso_teardown():
    for k, v in _ISO_PREV.items():
        if k == "__call__":
            privileged.call = v
        else:
            setattr(C, k, v)
