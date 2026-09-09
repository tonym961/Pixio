"""Test della libreria driver: API cartelle (CRUD, flag, note), upload driver singolo, upload zip
con estrazione sicura (rifiuto di '../' e percorsi assoluti), eliminazione file.

Sezione 14 del contratto (docs/API.md): campo "path" per caricare cartelle intere mantenendo le
sottocartelle, contatori useful_files / ignored_files, PATCH su piu' cartelle insieme.

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


def _put_file(client, h, folder, name, payload, kind="driver", chunk=None, path=None):
    """Upload completo via API (init + chunk + finish). Ritorna la risposta di finish."""
    body = {"filename": name, "size": len(payload), "kind": kind, "folder": folder}
    if path is not None:
        body["path"] = path
    r = client.post("/api/upload/init", json=body, headers=h)
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


    # ---------------------------------------------------------------- sezione 14: sottopercorsi
    def test_07_subpath_validation(self):
        from pixio.services.uploads import UploadError, check_subpath, driver_subdir
        # validi
        self.assertEqual(check_subpath(""), [])
        self.assertEqual(check_subpath(None), [])
        self.assertEqual(check_subpath("x64/rt.inf"), ["x64", "rt.inf"])
        self.assertEqual(check_subpath("x64\\win11\\rt.inf"), ["x64", "win11", "rt.inf"])
        self.assertEqual(check_subpath("a/b/c/d/e/f"), ["a", "b", "c", "d", "e", "f"])
        self.assertEqual(check_subpath("Intel (2.5G)/rt 640 [x64].sys"), ["Intel (2.5G)", "rt 640 [x64].sys"])
        # la sottocartella si ricava dal percorso completo o dalla sola cartella
        self.assertEqual(driver_subdir("x64/rt.inf", "rt.inf"), "x64")
        self.assertEqual(driver_subdir("x64/RT.INF", "rt.inf"), "x64")
        self.assertEqual(driver_subdir("x64", "rt.inf"), "x64")
        self.assertEqual(driver_subdir("", "rt.inf"), "")
        self.assertEqual(driver_subdir("x64/win11/rt.inf", "rt.inf"), "x64/win11")
        # non validi
        for bad in ("/etc/rt.inf", "../rt.inf", "x64/../../rt.inf", "..", ".", "./rt.inf",
                    ".nascosta/rt.inf", "x64/.ssh/rt.inf", "C:/Windows/rt.inf", "c:rt.inf",
                    "a/b/c/d/e/f/g.inf", "x*64/rt.inf", "x64/a?b.inf", "x64 /rt.inf", "x64./rt.inf",
                    "rt.inf\x00", "a" * 201 + "/rt.inf"):
            with self.assertRaises(UploadError, msg=bad):
                check_subpath(bad)

    def test_08_upload_with_path_creates_subfolders(self):
        from pixio.services import uploads
        uploads.CHUNK_SIZE = 1024
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": "Pacchetto RAID"}, headers=self.h).status_code, 201)
        base = os.path.join(C.DRIVERS_DIR, "Pacchetto RAID")
        # sottopercorsi rifiutati dall'API (400) e niente cartelle create
        for bad in ("../fuori/rt.inf", "/etc/rt.inf", ".nascosta/rt.inf", "a/b/c/d/e/f/g.inf", "x*/rt.inf"):
            r = self.client.post("/api/upload/init", json={"filename": "rt.inf", "size": 10, "kind": "driver",
                                                          "folder": "Pacchetto RAID", "path": bad}, headers=self.h)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("error", r.get_json())
        r = self.client.post("/api/upload/init", json={"filename": "rt.inf", "size": 10, "kind": "driver",
                                                      "folder": "Pacchetto RAID", "path": 5}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(os.listdir(base), [])
        # upload in una sottocartella profonda: le cartelle vengono create da sole
        payload = os.urandom(1024 * 2 + 7)
        r = _put_file(self.client, self.h, "Pacchetto RAID", "iaStorVD.sys", payload, path="x64/Win11/iaStorVD.sys")
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual((d["ok"], d["kind"], d["folder"], d["files"]), (True, "driver", "Pacchetto RAID", ["x64/Win11/iaStorVD.sys"]))
        dest = os.path.join(base, "x64", "Win11", "iaStorVD.sys")
        self.assertTrue(os.path.isfile(dest))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), payload)
        # l'elenco mostra il percorso relativo
        f = self._folders()["Pacchetto RAID"]
        self.assertIn("x64/Win11/iaStorVD.sys", [x["name"] for x in f["files"]])
        # stesso nome in sottocartelle diverse: nessun conflitto
        r = _put_file(self.client, self.h, "Pacchetto RAID", "iaStorVD.sys", b"x86", path="x86/iaStorVD.sys")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(os.path.isfile(os.path.join(base, "x86", "iaStorVD.sys")))
        # stessa sottocartella e stesso nome -> 409
        r = self.client.post("/api/upload/init", json={"filename": "iaStorVD.sys", "size": 5, "kind": "driver",
                                                      "folder": "Pacchetto RAID", "path": "x86/iaStorVD.sys"}, headers=self.h)
        self.assertEqual(r.status_code, 409)
        self.assertIn("x86", r.get_json()["error"])
        # la ripresa tiene conto del path: stesso file, sottocartella diversa = upload diverso
        big = os.urandom(1024 * 3)
        r = self.client.post("/api/upload/init", json={"filename": "e1d.inf", "size": len(big), "kind": "driver",
                                                      "folder": "Pacchetto RAID", "path": "x64/e1d.inf"}, headers=self.h)
        uid = r.get_json()["upload_id"]
        self.assertEqual(r.get_json()["path"], "x64/e1d.inf")
        self.client.put(f"/api/upload/{uid}/chunk/0", data=big[:1024], headers=self.h, content_type="application/octet-stream")
        r = self.client.post("/api/upload/init", json={"filename": "e1d.inf", "size": len(big), "kind": "driver",
                                                      "folder": "Pacchetto RAID", "path": "x64/e1d.inf"}, headers=self.h)
        self.assertEqual(r.get_json()["upload_id"], uid)
        self.assertEqual(r.get_json()["received"], [0])
        for other in ("x86/e1d.inf", None):
            body = {"filename": "e1d.inf", "size": len(big), "kind": "driver", "folder": "Pacchetto RAID"}
            if other:
                body["path"] = other
            r = self.client.post("/api/upload/init", json=body, headers=self.h)
            self.assertEqual(r.status_code, 200, r.get_json())
            self.assertNotEqual(r.get_json()["upload_id"], uid)
            self.assertEqual(r.get_json()["received"], [])
            self.client.delete(f"/api/upload/{r.get_json()['upload_id']}", headers=self.h)
        self.client.delete(f"/api/upload/{uid}", headers=self.h)
        self.assertEqual(self.client.get("/api/upload").get_json(), [])
        # eliminazione di un file dentro la sottocartella
        r = self.client.delete("/api/drivers/folders/Pacchetto%20RAID/files/x86/iaStorVD.sys", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(os.path.exists(os.path.join(base, "x86", "iaStorVD.sys")))

    # ---------------------------------------------------------------- sezione 14: file utili e scartati
    def test_09_useful_and_ignored_counters(self):
        from pixio.services import drivers, uploads
        uploads.CHUNK_SIZE = 1024
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": "Pacchetto Asus"}, headers=self.h).status_code, 201)
        # com'e' fatto davvero un pacchetto driver: driver veri + installatore, lingue, documentazione
        utili = ["rt640x64.inf", "rt640x64.sys", "rt640x64.cat", "rtnicprop64.dll", "oem.cab", "cfg.bin", "tab.dat"]
        scarti = ["AsusSetup.exe", "lingua.ini", "leggimi.txt", "setup.msi", "info.xml", "dati.json", "cfg.cfg"]
        for n in utili + scarti:
            self.assertEqual(_put_file(self.client, self.h, "Pacchetto Asus", n, b"x" * 20).status_code, 200, n)
        # anche in sottocartella
        self.assertEqual(_put_file(self.client, self.h, "Pacchetto Asus", "rt2.inf", b"x" * 10, path="x64/rt2.inf").status_code, 200)
        self.assertEqual(_put_file(self.client, self.h, "Pacchetto Asus", "note.txt", b"x" * 10, path="x64/note.txt").status_code, 200)
        f = self._folders()["Pacchetto Asus"]
        self.assertEqual(f["count"], len(utili) + len(scarti) + 2)
        self.assertEqual(f["useful_files"], len(utili) + 1)
        self.assertEqual(f["ignored_files"], len(scarti) + 1)
        self.assertEqual(f["useful_files"] + f["ignored_files"], f["count"])
        per_nome = {x["name"]: x for x in f["files"]}
        for n in utili:
            self.assertTrue(per_nome[n]["useful"], n)
        for n in scarti:
            self.assertFalse(per_nome[n]["useful"], n)
        self.assertTrue(per_nome["x64/rt2.inf"]["useful"])
        self.assertFalse(per_nome["x64/note.txt"]["useful"])
        # solo i .inf/.sys/.cat/.dll al primo livello finiscono in WinPE (invariato)
        self.assertEqual(f["winpe_files"], 4)
        self.assertTrue(drivers.is_useful("A.INF"))
        self.assertFalse(drivers.is_useful("AsusSetup.exe"))
        # una cartella vuota non ha ne' utili ne' scarti
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": "Vuota"}, headers=self.h).status_code, 201)
        v = self._folders()["Vuota"]
        self.assertEqual((v["useful_files"], v["ignored_files"]), (0, 0))

    # ---------------------------------------------------------------- sezione 14: PATCH su piu' cartelle
    def test_10_patch_many_folders(self):
        for n in ("Multi A", "Multi B", "Multi C"):
            self.assertEqual(self.client.post("/api/drivers/folders", json={"name": n}, headers=self.h).status_code, 201)
        # successo su tutte
        r = self.client.patch("/api/drivers/folders", json={"names": ["Multi A", "Multi B"], "winpe_inject": True,
                                                            "setup_load": True}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["updated"], ["Multi A", "Multi B"])
        self.assertEqual(d["errors"], {})
        fs = self._folders()
        for n in ("Multi A", "Multi B"):
            self.assertTrue(fs[n]["winpe_inject"], n)
            self.assertTrue(fs[n]["setup_load"], n)
        self.assertFalse(fs["Multi C"]["winpe_inject"])
        # nomi inesistenti o non validi: errore per nome, le altre passano lo stesso
        r = self.client.patch("/api/drivers/folders", json={"names": ["Multi A", "Fantasma", "../fuori", "Multi C"],
                                                            "winpe_inject": False}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertFalse(d["ok"])
        self.assertEqual(d["updated"], ["Multi A", "Multi C"])
        self.assertEqual(sorted(d["errors"]), ["../fuori", "Fantasma"])
        self.assertIn("non trovata", d["errors"]["Fantasma"])
        self.assertIn("non valido", d["errors"]["../fuori"])
        fs = self._folders()
        self.assertFalse(fs["Multi A"]["winpe_inject"])
        self.assertTrue(fs["Multi A"]["setup_load"])      # gli altri flag restano come sono
        self.assertTrue(fs["Multi B"]["winpe_inject"])    # cartella non elencata: invariata
        # corpo non valido
        for bad in ({}, {"names": []}, {"names": "Multi A", "winpe_inject": True},
                    {"names": ["Multi A"]}, {"names": ["Multi A"], "winpe_inject": "si"},
                    {"names": [3], "winpe_inject": True}, {"names": ["x"] * 501, "winpe_inject": True},
                    {"names": ["Multi A"], "note": "x"}):
            r = self.client.patch("/api/drivers/folders", json=bad, headers=self.h)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("error", r.get_json())
        # spegnere tutto in blocco
        r = self.client.patch("/api/drivers/folders", json={"names": ["Multi A", "Multi B", "Multi C"],
                                                            "winpe_inject": False, "setup_load": False}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        fs = self._folders()
        for n in ("Multi A", "Multi B", "Multi C"):
            self.assertFalse(fs[n]["winpe_inject"] or fs[n]["setup_load"], n)

    # ---------------------------------------------------------------- nessuna regressione su ISO e risposte
    def test_11_iso_and_answers_unchanged(self):
        from pixio.services import uploads
        uploads.CHUNK_SIZE = 1024
        # "path" vale solo per i driver
        for kind in (None, "iso", "answer"):
            body = {"filename": "x.iso" if kind in (None, "iso") else "x.xml", "size": 10, "path": "sub/x"}
            if kind:
                body["kind"] = kind
            r = self.client.post("/api/upload/init", json=body, headers=self.h)
            self.assertEqual(r.status_code, 400, kind)
            self.assertIn("path", r.get_json()["error"])
        # upload ISO completo: invariato (nessuna sottocartella, file nella libreria)
        payload = os.urandom(1024 * 2 + 11)
        r = self.client.post("/api/upload/init", json={"filename": "Prova (1).iso", "size": len(payload)}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual((d["kind"], d["folder"], d["received"]), ("iso", None, []))
        uid, cs = d["upload_id"], d["chunk_size"]
        for i in range((len(payload) + cs - 1) // cs):
            self.assertEqual(self.client.put(f"/api/upload/{uid}/chunk/{i}", data=payload[i * cs:(i + 1) * cs],
                                             headers=self.h, content_type="application/octet-stream").status_code, 200)
        r = self.client.post(f"/api/upload/{uid}/finish", headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["slug"], "prova-1")
        dest = os.path.join(C.LIBRARY_DIR, "Prova (1).iso")
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), payload)
        self.assertEqual(self.client.get("/api/upload").get_json(), [])


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
