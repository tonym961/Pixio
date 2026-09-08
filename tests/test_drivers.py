"""Test della libreria driver: API cartelle (CRUD, flag, note), upload driver singolo, upload zip
con estrazione sicura (rifiuto di '../' e percorsi assoluti), eliminazione file.

Esecuzione: cd /opt/pixio && python3 -m unittest tests.test_drivers
I percorsi di config vengono reindirizzati in una directory temporanea in setUpClass e ripristinati alla fine,
così il modulo convive con tests/test_plumbing.py nella stessa discovery.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

os.environ["PIXIO_NO_BACKGROUND"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pixio import config as C  # noqa: E402
from pixio import privileged  # noqa: E402

OVERRIDE_KEYS = ("ETC_DIR", "CONFIG_FILE", "SECRET_FILE", "SOURCES_DIR", "VAR_DIR", "JOBS_DIR", "UPLOAD_TMP_DIR",
                 "CLIENTS_FILE", "CATALOG_FILE", "DRIVERS_FILE", "LOG_DIR", "SRV_DIR", "LIBRARY_DIR", "CACHE_DIR",
                 "TFTP_DIR", "HTTP_DIR", "DRIVERS_DIR", "SOURCES_MOUNT_DIR")


def _fake_call(*args, stdin_text=None, timeout=180):
    if args and args[0] == "list-mounts":
        return {"iso": {}, "sources": {}}
    return {"ok": True}


def _put_file(client, h, folder, name, payload, kind="driver", chunk=None):
    """Upload completo via API (init + chunk + finish). Ritorna la risposta di finish."""
    r = client.post("/api/upload/init", json={"filename": name, "size": len(payload), "kind": kind, "folder": folder}, headers=h)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    uid, cs = d["upload_id"], d["chunk_size"]
    n = (len(payload) + cs - 1) // cs
    for i in range(n):
        r = client.put(f"/api/upload/{uid}/chunk/{i}", data=payload[i * cs:(i + 1) * cs], headers=h,
                       content_type="application/octet-stream")
        assert r.status_code == 200, r.get_json()
    return client.post(f"/api/upload/{uid}/finish", headers=h)


def _zip_bytes(entries):
    """entries: [(nome, bytes)] -> contenuto zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


class DriversApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pixio-drv-test-")
        cls.saved = {k: getattr(C, k) for k in OVERRIDE_KEYS}
        t = cls.tmp
        C.ETC_DIR = os.path.join(t, "etc")
        C.CONFIG_FILE = os.path.join(C.ETC_DIR, "config.json")
        C.SECRET_FILE = os.path.join(C.ETC_DIR, "secret")
        C.SOURCES_DIR = os.path.join(C.ETC_DIR, "sources")
        C.VAR_DIR = os.path.join(t, "var")
        C.JOBS_DIR = os.path.join(C.VAR_DIR, "jobs")
        C.UPLOAD_TMP_DIR = os.path.join(C.VAR_DIR, "uploads")
        C.CLIENTS_FILE = os.path.join(C.VAR_DIR, "clients.json")
        C.CATALOG_FILE = os.path.join(C.VAR_DIR, "catalog.json")
        C.DRIVERS_FILE = os.path.join(C.VAR_DIR, "drivers.json")
        C.LOG_DIR = os.path.join(t, "log")
        C.SRV_DIR = os.path.join(t, "srv")
        C.LIBRARY_DIR = os.path.join(C.SRV_DIR, "library")
        C.CACHE_DIR = os.path.join(C.SRV_DIR, "cache")
        C.TFTP_DIR = os.path.join(C.SRV_DIR, "tftp")
        C.HTTP_DIR = os.path.join(C.SRV_DIR, "http")
        C.DRIVERS_DIR = os.path.join(C.HTTP_DIR, "drivers")
        C.SOURCES_MOUNT_DIR = os.path.join(C.SRV_DIR, "sources")
        for d in (C.ETC_DIR, C.VAR_DIR, C.JOBS_DIR, C.UPLOAD_TMP_DIR, C.LOG_DIR, C.LIBRARY_DIR, C.CACHE_DIR,
                  C.TFTP_DIR, C.DRIVERS_DIR):
            os.makedirs(d, exist_ok=True)
        cls._orig_call = privileged.call
        privileged.call = _fake_call
        from pixio import create_app
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        st = cls.client.get("/api/auth/status").get_json()
        assert not st["password_set"], "config temporanea attesa senza password"
        r = cls.client.post("/api/auth/login", json={"password": "segreta1"})
        assert r.status_code == 200, r.get_json()
        cls.h = {"X-CSRF-Token": r.get_json()["csrf"]}

    @classmethod
    def tearDownClass(cls):
        privileged.call = cls._orig_call
        for k, v in cls.saved.items():
            setattr(C, k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _folders(self):
        r = self.client.get("/api/drivers")
        self.assertEqual(r.status_code, 200, r.get_json())
        return {f["name"]: f for f in r.get_json()["folders"]}

    # ---------------------------------------------------------------- cartelle
    def test_01_list_and_share_info(self):
        r = self.client.get("/api/drivers")
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d["root"], C.DRIVERS_DIR)
        self.assertTrue(d["samba_path"].startswith("\\\\"))
        self.assertTrue(d["samba_path"].endswith("\\drivers"))
        self.assertIsInstance(d["samba_enabled"], bool)
        self.assertIsInstance(d["folders"], list)

    def test_02_folder_crud(self):
        r = self.client.post("/api/drivers/folders", json={"name": "Intel I225 (2.1)"}, headers=self.h)
        self.assertEqual(r.status_code, 201, r.get_json())
        f = r.get_json()["folder"]
        self.assertEqual(f["name"], "Intel I225 (2.1)")
        self.assertEqual((f["count"], f["size"], f["inf_count"], f["winpe_files"]), (0, 0, 0, 0))
        self.assertFalse(f["winpe_inject"])
        self.assertTrue(f["valid_name"])
        self.assertTrue(os.path.isdir(os.path.join(C.DRIVERS_DIR, "Intel I225 (2.1)")))
        # duplicato -> 409, nome non valido -> 400
        r = self.client.post("/api/drivers/folders", json={"name": "Intel I225 (2.1)"}, headers=self.h)
        self.assertEqual(r.status_code, 409)
        self.assertIn("error", r.get_json())
        for bad in ("../x", "", "a/b", ".nascosta", "x" * 70):
            r = self.client.post("/api/drivers/folders", json={"name": bad}, headers=self.h)
            self.assertEqual(r.status_code, 400, bad)
        # flag e note
        r = self.client.patch("/api/drivers/folders/Intel%20I225%20(2.1)", json={"winpe_inject": True, "note": "NIC 2.5G"}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        f = r.get_json()["folder"]
        self.assertTrue(f["winpe_inject"])
        self.assertFalse(f["setup_load"])
        self.assertEqual(f["note"], "NIC 2.5G")
        r = self.client.patch("/api/drivers/folders/Intel%20I225%20(2.1)", json={"setup_load": "si"}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.client.patch("/api/drivers/folders/Intel%20I225%20(2.1)", json={}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.client.patch("/api/drivers/folders/Inesistente", json={"setup_load": True}, headers=self.h)
        self.assertEqual(r.status_code, 404)
        self.assertIn("Intel I225 (2.1)", self._folders())
        # eliminazione
        r = self.client.delete("/api/drivers/folders/Intel%20I225%20(2.1)", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("Intel I225 (2.1)", self._folders())
        self.assertEqual(self.client.delete("/api/drivers/folders/Intel%20I225%20(2.1)", headers=self.h).status_code, 404)
        self.assertEqual(self.client.delete("/api/drivers/folders/..", headers=self.h).status_code, 400)

    # ---------------------------------------------------------------- upload singolo
    def test_03_upload_single_driver(self):
        from pixio.services import uploads
        uploads.CHUNK_SIZE = 1024
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": "Realtek"}, headers=self.h).status_code, 201)
        payload = os.urandom(1024 * 2 + 100)
        # cartella inesistente -> 404, estensione non ammessa -> 400, kind sconosciuto -> 400
        r = self.client.post("/api/upload/init", json={"filename": "rt.inf", "size": 10, "kind": "driver", "folder": "Nope"}, headers=self.h)
        self.assertEqual(r.status_code, 404)
        r = self.client.post("/api/upload/init", json={"filename": "rt.iso", "size": 10, "kind": "driver", "folder": "Realtek"}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/upload/init", json={"filename": "rt.inf", "size": 10, "kind": "boh", "folder": "Realtek"}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/upload/init", json={"filename": "rt.inf", "size": 10, "kind": "driver", "folder": "../Realtek"}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        # upload completo con ripresa (init ripetuto ritorna lo stesso id e conserva kind/folder)
        r = self.client.post("/api/upload/init", json={"filename": "sub\\rt640x64.sys", "size": len(payload), "kind": "driver", "folder": "Realtek"}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        uid, cs = d["upload_id"], d["chunk_size"]
        self.assertEqual((d["kind"], d["folder"]), ("driver", "Realtek"))
        r = self.client.put(f"/api/upload/{uid}/chunk/0", data=payload[:cs], headers=self.h, content_type="application/octet-stream")
        self.assertEqual(r.status_code, 200)
        lst = self.client.get("/api/upload").get_json()
        self.assertEqual((lst[0]["kind"], lst[0]["folder"], lst[0]["filename"]), ("driver", "Realtek", "rt640x64.sys"))
        r = self.client.post("/api/upload/init", json={"filename": "rt640x64.sys", "size": len(payload), "kind": "driver", "folder": "Realtek"}, headers=self.h)
        self.assertEqual(r.get_json()["upload_id"], uid)
        self.assertEqual(r.get_json()["received"], [0])
        # stesso nome ma kind iso -> upload diverso (estensione non ammessa per le ISO)
        r = self.client.post("/api/upload/init", json={"filename": "rt640x64.sys", "size": len(payload)}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        n = (len(payload) + cs - 1) // cs
        for i in range(1, n):
            r = self.client.put(f"/api/upload/{uid}/chunk/{i}", data=payload[i * cs:(i + 1) * cs], headers=self.h, content_type="application/octet-stream")
            self.assertEqual(r.status_code, 200)
        r = self.client.post(f"/api/upload/{uid}/finish", headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual((d["ok"], d["kind"], d["folder"], d["extracted"], d["files"]), (True, "driver", "Realtek", 0, ["rt640x64.sys"]))
        dest = os.path.join(C.DRIVERS_DIR, "Realtek", "rt640x64.sys")
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), payload)
        self.assertFalse(os.path.exists(os.path.join(C.LIBRARY_DIR, "rt640x64.sys")))
        self.assertEqual(self.client.get("/api/upload").get_json(), [])
        # già presente -> 409
        r = self.client.post("/api/upload/init", json={"filename": "rt640x64.sys", "size": 5, "kind": "driver", "folder": "Realtek"}, headers=self.h)
        self.assertEqual(r.status_code, 409)
        # elenco e conteggi
        r = _put_file(self.client, self.h, "Realtek", "rt640x64.inf", b"[Version]\r\nSignature=\"$WINDOWS NT$\"\r\n")
        self.assertEqual(r.status_code, 200, r.get_json())
        f = self._folders()["Realtek"]
        self.assertEqual((f["count"], f["inf_count"], f["winpe_files"]), (2, 1, 2))
        self.assertEqual(f["size"], len(payload) + os.path.getsize(os.path.join(C.DRIVERS_DIR, "Realtek", "rt640x64.inf")))
        self.assertEqual(f["winpe_size"], f["size"])
        # eliminazione file
        r = self.client.delete("/api/drivers/folders/Realtek/files/rt640x64.sys", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(os.path.exists(dest))
        self.assertEqual(self.client.delete("/api/drivers/folders/Realtek/files/rt640x64.sys", headers=self.h).status_code, 404)
        self.assertEqual(self.client.delete("/api/drivers/folders/Realtek/files/../../x", headers=self.h).status_code, 404)
        self.assertEqual(self._folders()["Realtek"]["count"], 1)

    # ---------------------------------------------------------------- zip
    def test_04_upload_zip_extracted(self):
        from pixio.services import uploads
        uploads.CHUNK_SIZE = 1024
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": "Intel NVMe"}, headers=self.h).status_code, 201)
        data = _zip_bytes([
            ("iaStorVD.inf", b"[Version]\r\n" * 50),
            ("iaStorVD.sys", os.urandom(3000)),
            ("iaStorVD.cat", os.urandom(200)),
            ("x64/extra/readme.txt", b"ciao"),
            ("__MACOSX/._iaStorVD.inf", b"junk"),
            ("cartella/", b""),
        ])
        r = _put_file(self.client, self.h, "Intel NVMe", "driver-pack.zip", data)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["extracted"], 4)
        self.assertEqual(sorted(d["files"]), ["iaStorVD.cat", "iaStorVD.inf", "iaStorVD.sys", "x64/extra/readme.txt"])
        base = os.path.join(C.DRIVERS_DIR, "Intel NVMe")
        self.assertFalse(os.path.exists(os.path.join(base, "driver-pack.zip")))
        self.assertTrue(os.path.isfile(os.path.join(base, "x64", "extra", "readme.txt")))
        self.assertFalse(os.path.exists(os.path.join(base, "__MACOSX")))
        f = self._folders()["Intel NVMe"]
        self.assertEqual((f["count"], f["inf_count"], f["winpe_files"]), (4, 1, 3))
        self.assertIn("x64/extra/readme.txt", [x["name"] for x in f["files"]])
        # file in sottocartella eliminabile
        r = self.client.delete("/api/drivers/folders/Intel%20NVMe/files/x64/extra/readme.txt", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._folders()["Intel NVMe"]["count"], 3)

    def test_05_zip_traversal_rejected(self):
        from pixio.services import uploads
        uploads.CHUNK_SIZE = 1024
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": "Cattivo"}, headers=self.h).status_code, 201)
        base = os.path.join(C.DRIVERS_DIR, "Cattivo")
        for entries in (
            [("ok.inf", b"x"), ("../evil.inf", b"evil")],
            [("sub/../../evil2.sys", b"evil")],
            [("/etc/evil3.inf", b"evil")],
            [("C:\\Windows\\evil4.inf", b"evil")],
        ):
            r = _put_file(self.client, self.h, "Cattivo", "bad.zip", _zip_bytes(entries))
            self.assertEqual(r.status_code, 400, r.get_json())
            self.assertIn("non sicuro", r.get_json()["error"])
            # niente estratto, zip cancellato, nessun file fuori dalla cartella
            self.assertEqual(os.listdir(base), [])
            self.assertFalse(os.path.exists(os.path.join(C.DRIVERS_DIR, "evil.inf")))
            self.assertFalse(os.path.exists(os.path.join(C.HTTP_DIR, "evil2.sys")))
        # zip corrotto -> 400 e cancellato
        r = _put_file(self.client, self.h, "Cattivo", "corrotto.zip", b"non sono uno zip" * 100)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(os.listdir(base), [])
        # limite di file
        old = uploads.ZIP_MAX_FILES
        uploads.ZIP_MAX_FILES = 3
        try:
            r = _put_file(self.client, self.h, "Cattivo", "troppi.zip", _zip_bytes([(f"f{i}.inf", b"x") for i in range(5)]))
            self.assertEqual(r.status_code, 400)
            self.assertIn("troppi file", r.get_json()["error"])
        finally:
            uploads.ZIP_MAX_FILES = old
        self.assertEqual(os.listdir(base), [])

    # ---------------------------------------------------------------- servizio
    def test_06_winpe_inject_and_setup_load_lists(self):
        from pixio.services import drivers
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": "Inject"}, headers=self.h).status_code, 201)
        _put_file(self.client, self.h, "Inject", "e1d.inf", b"inf")
        _put_file(self.client, self.h, "Inject", "e1d.sys", b"sys")
        _put_file(self.client, self.h, "Inject", "note.txt", b"txt")
        self.client.patch("/api/drivers/folders/Inject", json={"winpe_inject": True, "setup_load": True}, headers=self.h)
        inj = drivers.winpe_inject_files()
        self.assertEqual(sorted(x[1] for x in inj if x[0] == "Inject"), ["e1d.inf", "e1d.sys"])
        self.assertIn("Inject", drivers.setup_load_folders())
        self.client.patch("/api/drivers/folders/Inject", json={"setup_load": False}, headers=self.h)
        self.assertNotIn("Inject", drivers.setup_load_folders())
        # la cancellazione della cartella rimuove anche i flag
        self.client.delete("/api/drivers/folders/Inject", headers=self.h)
        from pixio.storage import read_json
        self.assertNotIn("Inject", read_json(C.DRIVERS_FILE, {}).get("folders", {}))


if __name__ == "__main__":
    unittest.main()
