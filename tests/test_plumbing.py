"""Test dei servizi di base di Pixio (job, client, log, upload, impostazioni).

Esecuzione: cd /opt/pixio && python3 -m unittest tests.test_plumbing
Non serve l'helper privilegiato: pixio.privileged.call viene sostituito da un finto.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

os.environ["PIXIO_NO_BACKGROUND"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pixio import config as C  # noqa: E402
from pixio import privileged  # noqa: E402

TMP = tempfile.mkdtemp(prefix="pixio-test-")
# I moduli leggono C.<X> al momento della chiamata: basta ridefinire le costanti prima di usarli.
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
C.TFTP_DIR = os.path.join(C.SRV_DIR, "tftp")
C.SOURCES_MOUNT_DIR = os.path.join(C.SRV_DIR, "sources")
for d in (C.ETC_DIR, C.VAR_DIR, C.JOBS_DIR, C.UPLOAD_TMP_DIR, C.LOG_DIR, C.LIBRARY_DIR, C.CACHE_DIR, C.TFTP_DIR):
    os.makedirs(d, exist_ok=True)

from pixio.services import jobs, clients, logs, uploads  # noqa: E402

HELPER_CALLS = []


def fake_call(*args, stdin_text=None, timeout=180):
    HELPER_CALLS.append(args)
    if args[0] == "list-mounts":
        return {"iso": {}, "sources": {}}
    if args[0] == "apply":
        return {"ok": True, "results": {"dnsmasq": "ok", "nginx": "ok", "samba": "ok"}}
    return {"ok": True}


privileged.call = fake_call

DNSMASQ_LOG = """Sep  8 17:42:09 dnsmasq-dhcp[1234]: 1234567 available DHCP range: 10.10.0.1 -- 10.10.1.254
Sep  8 17:42:09 dnsmasq-dhcp[1234]: 1234567 vendor class: PXEClient:Arch:00007:UNDI:003016
Sep  8 17:42:09 dnsmasq-dhcp[1234]: 1234567 DHCPDISCOVER(ens18) d8:bb:c1:4a:20:7e
Sep  8 17:42:09 dnsmasq-dhcp[1234]: 1234567 PXE(ens18) d8:bb:c1:4a:20:7e proxy
Sep  8 17:42:10 dnsmasq-dhcp[1234]: 1234567 DHCPREQUEST(ens18) 10.10.0.57 d8:bb:c1:4a:20:7e
Sep  8 17:42:10 dnsmasq-dhcp[1234]: 1234567 PXE(ens18) 10.10.0.57 d8:bb:c1:4a:20:7e ipxe.efi
Sep  8 17:42:11 dnsmasq-tftp[1234]: sent /srv/pixio/tftp/ipxe.efi to 10.10.0.57
Sep  8 17:42:15 dnsmasq-dhcp[1234]: 1234568 vendor class: PXEClient:Arch:00007:UNDI:003016
Sep  8 17:42:15 dnsmasq-dhcp[1234]: 1234568 user class: iPXE
Sep  8 17:42:15 dnsmasq-dhcp[1234]: 1234568 PXE(ens18) 10.10.0.57 d8:bb:c1:4a:20:7e http://10.10.0.254/boot.ipxe
Sep  8 17:45:00 dnsmasq-dhcp[1234]: 2222222 vendor class: PXEClient:Arch:00000:UNDI:002001
Sep  8 17:45:00 dnsmasq-dhcp[1234]: 2222222 PXE(ens18) 52:54:00:12:34:56 proxy
"""


class JobsTest(unittest.TestCase):
    def test_start_progress_list_cancel(self):
        def work(job):
            job.set_progress(10, "inizio")
            while not job.cancelled:
                time.sleep(0.05)
            job.log("annullato dal test")
        j = jobs.start("copy", "slug-x", work, message="copia di prova")
        self.assertEqual(j.status, "running")
        self.assertTrue(os.path.isfile(os.path.join(C.JOBS_DIR, f"{j.id}.json")))
        time.sleep(0.2)
        self.assertEqual(jobs.get_job(j.id).progress, 10)
        lst = jobs.list_jobs()
        self.assertEqual(lst[0]["id"], j.id)
        jobs.cancel(j.id)
        jobs.wait(j.id, 5)
        self.assertEqual(jobs.get_job(j.id).status, "cancelled")
        with open(os.path.join(C.JOBS_DIR, f"{j.id}.json")) as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["status"], "cancelled")

    def test_done_error_and_scan_exclusive(self):
        j = jobs.start("detect", None, lambda job: job.set_progress(50))
        jobs.wait(j.id, 5)
        self.assertEqual(j.status, "done")
        self.assertEqual(j.progress, 100)
        e = jobs.start("detect", None, lambda job: (_ for _ in ()).throw(RuntimeError("boom")))
        jobs.wait(e.id, 5)
        self.assertEqual(e.status, "error")
        self.assertIn("boom", e.message)
        s1 = jobs.start("scan", None, lambda job: time.sleep(0.3))
        s2 = jobs.start("scan", None, lambda job: None)
        self.assertIs(s1, s2)
        jobs.wait(s1.id, 5)

    def test_interrupted_on_restart(self):
        stale = {"id": "deadbeef", "type": "scan", "target": None, "status": "running", "progress": 5,
                 "message": "", "started": "2026-01-01T00:00:00+01:00", "finished": None, "cancelled": False}
        with open(os.path.join(C.JOBS_DIR, "deadbeef.json"), "w") as f:
            json.dump(stale, f)
        jobs._loaded = False
        jobs.mark_interrupted()
        j = jobs.get_job("deadbeef")
        self.assertEqual(j.status, "error")
        self.assertIn("interrotto", j.message)


class ClientsTest(unittest.TestCase):
    def setUp(self):
        for p in (C.CLIENTS_FILE, os.path.join(C.VAR_DIR, "clients_state.json")):
            try:
                os.unlink(p)
            except OSError:
                pass
        self.log = os.path.join(C.LOG_DIR, "dnsmasq.log")
        with open(self.log, "w") as f:
            f.write(DNSMASQ_LOG)

    def test_poll_parses_log(self):
        clients.poll()
        cl = {c["mac"]: c for c in clients.list_clients()}
        self.assertIn("d8:bb:c1:4a:20:7e", cl)
        c = cl["d8:bb:c1:4a:20:7e"]
        self.assertEqual(c["ip"], "10.10.0.57")
        self.assertEqual(c["arch"], "efi64")
        self.assertEqual(c["vendor_class"], "PXEClient:Arch:00007:UNDI:003016")
        self.assertEqual(c["count"], 1)            # 3 righe PXE entro 60 s = una sessione
        self.assertTrue(c["last_seen"].endswith("17:42:15+02:00") or "17:42:15" in c["last_seen"])
        self.assertEqual(cl["52:54:00:12:34:56"]["arch"], "bios")
        # seconda poll: niente di nuovo
        self.assertEqual(clients.poll(), 0)
        # nuove righe in coda + rotazione
        with open(self.log, "a") as f:
            f.write("Sep  8 17:50:00 dnsmasq-dhcp[1234]: 3333333 PXE(ens18) 10.10.0.57 d8:bb:c1:4a:20:7e ipxe.efi\n")
        self.assertEqual(clients.poll(), 1)
        self.assertEqual(clients.get("D8-BB-C1-4A-20-7E")["count"], 2)
        os.unlink(self.log)
        with open(self.log, "w") as f:
            f.write("Sep  8 17:55:00 dnsmasq-dhcp[1234]: 4444444 PXE(ens18) 10.10.0.99 aa:bb:cc:dd:ee:ff proxy\n")
        clients.poll()
        self.assertIsNotNone(clients.get("aa:bb:cc:dd:ee:ff"))

    def test_update_delete_record_boot(self):
        clients.record_boot("AA:BB:CC:DD:EE:01", "ubuntu-24-04", ip="10.10.0.5")
        c = clients.get("aa:bb:cc:dd:ee:01")
        self.assertEqual(c["last_entry"], "ubuntu-24-04")
        c = clients.update("aa:bb:cc:dd:ee:01", {"name": "PC test", "auto_boot": "debian-live"})
        self.assertEqual(c["name"], "PC test")
        self.assertEqual(c["auto_boot"], "debian-live")
        with self.assertRaises(ValueError):
            clients.update("aa:bb:cc:dd:ee:01", {"auto_boot": "../x"})
        clients.record_seen("aa:bb:cc:dd:ee:01", ip="10.10.0.6", arch="x86_64", platform="efi", manuf="QEMU", product="Standard PC")
        c = clients.get("aa:bb:cc:dd:ee:01")
        self.assertEqual((c["ip"], c["arch"], c["hw"]), ("10.10.0.6", "efi64", "QEMU Standard PC"))
        clients.delete("aa:bb:cc:dd:ee:01")
        self.assertIsNone(clients.get("aa:bb:cc:dd:ee:01"))
        with self.assertRaises(KeyError):
            clients.delete("aa:bb:cc:dd:ee:01")


class LogsTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(C.LOG_DIR, "dnsmasq.log"), "w") as f:
            f.write(DNSMASQ_LOG)
        self.nginx = os.path.join(TMP, "nginx-access.log")
        with open(self.nginx, "w") as f:
            f.write('10.10.0.57 [2026-09-08T17:42:20+02:00] "GET /boot.ipxe HTTP/1.1" 200 812 "iPXE/1.21.1"\n')
            f.write('10.10.0.57 [2026-09-08T17:42:21+02:00] "GET /pxe/nope HTTP/1.1" 404 0 "-"\n')
        self._orig = logs.NGINX_ACCESS_LOG
        logs.NGINX_ACCESS_LOG = self.nginx

    def tearDown(self):
        logs.NGINX_ACCESS_LOG = self._orig

    def test_read_with_cursor(self):
        # il journal reale della macchina puo' contenere centinaia di righe: limite alto per non perdere quelle finte
        r = logs.read("all", None, 2000)
        msgs = [l["msg"] for l in r["lines"]]
        self.assertIn("PXE d8:bb:c1:4a:20:7e (10.10.0.57) → ipxe.efi", msgs)
        self.assertIn("TFTP: inviato ipxe.efi a 10.10.0.57", msgs)
        self.assertTrue(any(l["source"] == "nginx" and l["level"] == "warn" for l in r["lines"]))
        ts = [l["ts"] for l in r["lines"]]
        self.assertEqual(ts, sorted(ts))
        self.assertTrue(r["cursor"].startswith(ts[-1] + "|"), r["cursor"])   # cursore "ts|n"
        # una nuova lettura con il cursore non deve restituire righe gia' viste
        self.assertEqual(logs.read("all", r["cursor"], 2000)["lines"], [])
        r2 = logs.read("all", r["cursor"], 200)
        self.assertEqual(r2["lines"], [])
        self.assertEqual(r2["cursor"], r["cursor"])
        # cursore intermedio: solo righe successive
        mid = r["lines"][3]["ts"]
        r3 = logs.read("dnsmasq", mid, 200)
        self.assertTrue(all(l["ts"] > mid for l in r3["lines"]))
        self.assertTrue(all(l["source"] == "dnsmasq" for l in r3["lines"]))
        # limit rispettato (a meno di gruppi con lo stesso ts)
        r4 = logs.read("nginx", None, 1)
        self.assertEqual(len(r4["lines"]), 1)
        with self.assertRaises(ValueError):
            logs.read("boh")


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pixio import create_app
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        st = cls.client.get("/api/auth/status").get_json()
        assert not st["password_set"]
        r = cls.client.post("/api/auth/login", json={"password": "segreta1"})
        assert r.status_code == 200, r.get_json()
        cls.csrf = r.get_json()["csrf"]
        cls.h = {"X-CSRF-Token": cls.csrf}

    def test_auth_and_csrf(self):
        anon = self.app.test_client()
        self.assertEqual(anon.get("/api/settings").status_code, 401)
        r = self.client.post("/api/upload/init", json={"filename": "x.iso", "size": 10})
        self.assertEqual(r.status_code, 403)     # senza CSRF

    def test_settings_get(self):
        r = self.client.get("/api/settings")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        for k in ("network", "menu", "library", "windows", "scan", "interfaces"):
            self.assertIn(k, d)
        self.assertNotIn("auth", d)
        self.assertIsInstance(d["library"]["samba_password_set"], bool)
        self.assertIn("dhcp_mode", d["network"])
        self.assertIsInstance(d["interfaces"], list)

    def test_settings_put_validation(self):
        r = self.client.put("/api/settings", json={"network": {"server_ip": "999.1.1.1"}}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())
        r = self.client.put("/api/settings", json={"menu": {"timeout": 9999}}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.client.put("/api/settings", json={"scan": {"interval_min": 5}, "menu": {"timeout": 15}}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["applied"]["dnsmasq"], "ok")
        self.assertIn(("apply", "all"), HELPER_CALLS)
        self.assertEqual(self.client.get("/api/settings").get_json()["scan"]["interval_min"], 5)
        with open(C.CONFIG_FILE) as f:
            cfg = json.load(f)
        self.assertTrue(cfg["auth"]["password_hash"])      # non perso dal PUT

    def test_samba_password(self):
        r = self.client.post("/api/settings/samba-password", json={"password": "pw"}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/settings/samba-password", json={"password": "pixio123"}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.client.get("/api/settings").get_json()["library"]["samba_password_set"])

    def test_upload_end_to_end(self):
        uploads.CHUNK_SIZE = 1024      # chunk piccoli per il test
        payload = os.urandom(1024 * 2 + 300)
        r = self.client.post("/api/upload/init", json={"filename": "../evil.exe", "size": len(payload)}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/upload/init", json={"filename": "sub/Test ISO (1).iso", "size": len(payload)}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        uid, cs = d["upload_id"], d["chunk_size"]
        self.assertEqual(cs, 1024)
        self.assertEqual(d["received"], [])
        n = (len(payload) + cs - 1) // cs
        # chunk troppo grande -> 400
        r = self.client.put(f"/api/upload/{uid}/chunk/0", data=payload[:cs + 1], headers=self.h,
                            content_type="application/octet-stream")
        self.assertEqual(r.status_code, 400)
        for i in range(n):
            if i == 1:
                continue
            r = self.client.put(f"/api/upload/{uid}/chunk/{i}", data=payload[i * cs:(i + 1) * cs], headers=self.h,
                                content_type="application/octet-stream")
            self.assertEqual(r.status_code, 200, r.get_json())
        # finish incompleto
        r = self.client.post(f"/api/upload/{uid}/finish", headers=self.h)
        self.assertEqual(r.status_code, 400)
        # ripresa: init con stesso nome+size ritorna i chunk ricevuti
        r = self.client.post("/api/upload/init", json={"filename": "Test ISO (1).iso", "size": len(payload)}, headers=self.h)
        d = r.get_json()
        self.assertEqual(d["upload_id"], uid)
        self.assertEqual(d["received"], [0, 2])
        lst = self.client.get("/api/upload").get_json()
        self.assertEqual(lst[0]["upload_id"], uid)
        self.assertEqual(lst[0]["received_bytes"], cs + 300)
        r = self.client.put(f"/api/upload/{uid}/chunk/1", data=payload[cs:2 * cs], headers=self.h,
                            content_type="application/octet-stream")
        self.assertEqual(r.get_json()["received"], 3)
        r = self.client.post(f"/api/upload/{uid}/finish", headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["slug"], "test-iso-1")
        dest = os.path.join(C.LIBRARY_DIR, "Test ISO (1).iso")
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), payload)
        self.assertFalse(os.path.exists(os.path.join(C.UPLOAD_TMP_DIR, uid)))
        self.assertEqual(self.client.get("/api/upload").get_json(), [])
        # file già presente -> 409
        r = self.client.post("/api/upload/init", json={"filename": "Test ISO (1).iso", "size": 5}, headers=self.h)
        self.assertEqual(r.status_code, 409)

    def test_clients_and_logs_routes(self):
        clients.record_boot("00:11:22:33:44:55", "x-slug")
        r = self.client.get("/api/clients")
        self.assertTrue(any(c["mac"] == "00:11:22:33:44:55" for c in r.get_json()))
        r = self.client.patch("/api/clients/00-11-22-33-44-55", json={"name": "Aula 1"}, headers=self.h)
        self.assertEqual(r.get_json()["name"], "Aula 1")
        self.assertEqual(self.client.delete("/api/clients/00:11:22:33:44:55", headers=self.h).status_code, 200)
        self.assertEqual(self.client.delete("/api/clients/00:11:22:33:44:55", headers=self.h).status_code, 404)
        r = self.client.get("/api/logs?source=dnsmasq&limit=5")
        self.assertEqual(r.status_code, 200)
        self.assertIn("cursor", r.get_json())
        self.assertEqual(self.client.get("/api/logs?source=zzz").status_code, 400)

    def test_system_status_and_jobs(self):
        r = self.client.get("/api/system/status")
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        for k in ("hostname", "services", "sources", "library", "disk", "catalog", "ipxe", "clients", "jobs_running", "warnings"):
            self.assertIn(k, d)
        self.assertIn("iPXE non compilato: eseguire la ricompilazione", d["warnings"])
        r = self.client.get("/api/jobs")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/jobs/nope").status_code, 404)

    def test_sources_api(self):
        r = self.client.post("/api/sources", json={"name": "Server ISO", "unc": "\\\\nas\\iso\\linux",
                                                   "username": "u", "password": "p"}, headers=self.h)
        self.assertEqual(r.status_code, 201, r.get_json())
        s = r.get_json()["source"]
        self.assertEqual(s["id"], "server-iso")
        self.assertEqual(s["unc"], "//nas/iso/linux")
        self.assertIn(("write-source", "server-iso"), HELPER_CALLS)
        # cambio unc senza password -> 400
        r = self.client.put("/api/sources/server-iso", json={"unc": "//nas/iso/altro"}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        self.assertIn("password", r.get_json()["error"])
        r = self.client.put("/api/sources/server-iso", json={"name": "NAS"}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["source"]["name"], "NAS")
        self.assertEqual(self.client.post("/api/sources/server-iso/test", headers=self.h).status_code, 200)
        self.assertEqual(self.client.delete("/api/sources/server-iso", headers=self.h).status_code, 200)
        self.assertEqual(self.client.get("/api/sources").get_json(), [])
        self.assertEqual(self.client.delete("/api/sources/server-iso", headers=self.h).status_code, 404)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


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


def setUpModule():
    _iso_setup()


def tearDownModule():
    _iso_teardown()
