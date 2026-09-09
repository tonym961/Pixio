"""Test della libreria driver: API cartelle (CRUD, flag, note), upload driver singolo, upload zip
con estrazione sicura (rifiuto di '../' e percorsi assoluti), eliminazione file.

Sezione 14 del contratto (docs/API.md): campo "path" per caricare cartelle intere mantenendo le
sottocartelle, contatori useful_files / ignored_files, PATCH su piu' cartelle insieme.

Sezione 15: campo apply_to (tutte le immagini / gruppi del menu / singole ISO) con la sua validazione,
filtro delle cartelle per la ISO che si sta avviando, esclusione dei singoli file dall'iniezione nel
WinPE e generazione dello script iPXE, che deve dare a ogni immagine solo i driver che le competono.

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


# Finti .inf per i test della sezione 21: dichiarano i propri file come fanno i pacchetti veri
# (SourceDisksFiles, CopyFiles con riga di continuazione e nome diretto "@", ServiceBinary con %12%\).
INF_PROVA = """; Finto pacchetto driver: dichiara prova.sys, aiuto.sys, prova.exe, prova.dll
; il file finto.sys e' solo nominato in un commento
[Version]
Signature="$Windows NT$"
Class=SCSIAdapter
CatalogFile=prova.cat        ; il .cat lo cerca Windows da solo, non e' un file dichiarato

[SourceDisksNames]
1 = %DiskId1%,,,""

[SourceDisksFiles.amd64]
prova.sys = 1,,,
aiuto.sys = 1,,,
prova.exe = 1,,,

[DestinationDirs]
Driver_files_copy = 12
Extra_files_copy = 11

[Driver_files_copy]
prova.sys

[Extra_files_copy]
prova.dll

[Prova_inst.NTamd64]
CopyFiles=Driver_files_copy, \\
          Extra_files_copy
CopyFiles=@aiuto.sys

[prova_service]
ServiceBinary  = %12%\\prova.sys
[aiuto_service]
ServiceBinary = %12%\\aiuto.sys

[Strings]
DiskId1 = "Disco driver; di prova"
"""

# .inf che non porta file propri (solo registro), come le estensioni dei pacchetti Intel
# Finto pacchetto in stile Intel RST (sezione 21): oltre al .sys dichiara una .dll di messaggi che sta
# nella cartella, un servizio .exe che non c'e' e una version.dll, che nel WinPE esiste gia' in System32.
INF_RST = """; Finto pacchetto RST: dichiara rst.sys, RstMsg.dll, RstServizio.exe, version.dll
[Version]
Signature="$Windows NT$"
Class=SCSIAdapter
CatalogFile=rst.cat

[SourceDisksNames]
1 = %DiskId1%,,,""

[SourceDisksFiles.amd64]
rst.sys = 1,,,
RstMsg.dll = 1,,,
RstServizio.exe = 1,,,
version.dll = 1,,,

[DestinationDirs]
DefaultDestDir = 13
Driver_files_copy = 12
Log_files_copy = 11

[Rst_inst.NTamd64]
CopyFiles=Driver_files_copy
CopyFiles=@RstServizio.exe
CopyFiles=Log_files_copy

[Driver_files_copy]
rst.sys

[Log_files_copy]
RstMsg.dll
version.dll

[rst_service]
ServiceBinary = %12%\\rst.sys

[Strings]
INTEL = "Finta Intel"
DiskId1 = "Disco driver"
"""


INF_SENZA_FILE = """[Version]
Signature="$Windows NT$"
Class=Extension
CatalogFile=est.cat

[Manufacturer]
%INTEL% = INTEL,NTamd64

[INTEL.NTamd64]
%Desc% = Est_inst, PCI\\VEN_8086&DEV_0001

[Est_inst.NTamd64]
AddReg = est_addreg

[est_addreg]
HKR,,"Prova",0x00000000,"1"

[Strings]
INTEL = "Finta Intel"
Desc = "Estensione di prova"
"""


def _leggi(path):
    """Contenuto di un file, senza lasciarlo aperto."""
    with open(path, "rb") as fh:
        return fh.read()


def _inf_bytes(testo, encoding="utf-16"):
    """Contenuto di un .inf: quelli veri sono UTF-16 con BOM oppure ANSI (cp1252), con fine riga CRLF."""
    return testo.replace("\n", "\r\n").encode(encoding)


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
        # 4: contano i file iniettabili (.inf/.sys/.cat, sottocartelle comprese, nomi appiattiti);
        # le .dll restano nella cartella ma non vengono iniettate nel WinPE
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

    # ---------------------------------------------------------------- sezione 15: driver abbinati alle ISO
    def _catalog(self):
        """Catalogo di prova: un server, un PC e uno strumento WinPE, piu' una ISO Linux."""
        import json
        isos = {
            "win-server-2022": {"slug": "win-server-2022", "name": "Windows Server 2022", "type": "windows",
                                "group": "Windows Server", "enabled": True, "order": 1, "source": "local",
                                "file": "ws2022.iso", "path": "/x/ws2022.iso", "missing": False,
                                "detect": {"type": "windows", "files": {"bootwim": "sources/boot.wim", "bcd": "boot/bcd",
                                                                        "bootsdi": "boot/boot.sdi", "install": "sources/install.wim"}}},
            "win11-pro": {"slug": "win11-pro", "name": "Windows 11 Pro", "type": "windows", "group": "Windows",
                          "enabled": True, "order": 2, "source": "local", "file": "w11.iso", "path": "/x/w11.iso",
                          "missing": False,
                          "detect": {"type": "windows", "files": {"bootwim": "sources/boot.wim", "bcd": "boot/bcd",
                                                                  "bootsdi": "boot/boot.sdi", "install": "sources/install.wim"}}},
            "hirens-pe": {"slug": "hirens-pe", "name": "Hiren's BootCD PE", "type": "winpe-tool", "group": "Strumenti",
                          "enabled": True, "order": 3, "source": "local", "file": "hbcd.iso", "path": "/x/hbcd.iso",
                          "missing": False,
                          "detect": {"type": "winpe-tool", "files": {"bootwim": "hbcd_pe_x64.wim", "bcd": "boot/bcd",
                                                                     "bootsdi": "boot/boot.sdi"}}},
            "debian-13": {"slug": "debian-13", "name": "Debian 13", "type": "debian-installer", "group": "Linux",
                          "enabled": True, "order": 4, "source": "local", "file": "deb.iso", "path": "/x/deb.iso",
                          "missing": False, "detect": {"type": "debian-installer", "files": {}}},
        }
        with open(C.CATALOG_FILE, "w", encoding="utf-8") as fh:
            json.dump({"isos": isos, "last_scan": None}, fh)
        return isos

    def _reset_drivers(self):
        """Spegne winpe_inject / setup_load su tutte le cartelle lasciate dai test precedenti."""
        names = list(self._folders())
        if names:
            r = self.client.patch("/api/drivers/folders",
                                  json={"names": names, "winpe_inject": False, "setup_load": False,
                                        "apply_to": {"mode": "all"}}, headers=self.h)
            self.assertEqual(r.status_code, 200, r.get_json())

    def test_12_apply_to_default_and_validation(self):
        from pixio.services import drivers
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": "Apply Val"}, headers=self.h).status_code, 201)
        f = self._folders()["Apply Val"]
        # valore predefinito: vale per tutte le immagini (retrocompatibilita')
        self.assertEqual(f["apply_to"], {"mode": "all", "groups": [], "isos": []})
        # le scelte possibili arrivano con l'elenco delle cartelle
        self._catalog()
        d = self.client.get("/api/drivers").get_json()
        self.assertEqual(sorted(d["apply_choices"]), ["groups", "isos"])
        self.assertEqual(d["groups"], d["apply_choices"]["groups"])
        self.assertEqual(d["isos"], d["apply_choices"]["isos"])
        self.assertIn("Windows Server", d["groups"])
        self.assertIn("Linux", d["groups"])
        slugs = {i["slug"]: i for i in d["isos"]}
        # solo le ISO che avviano un WinPE: la Debian non riceve driver
        self.assertEqual(sorted(slugs), ["hirens-pe", "win-server-2022", "win11-pro"])
        self.assertEqual(slugs["win-server-2022"]["name"], "Windows Server 2022")
        self.assertEqual(slugs["win-server-2022"]["group"], "Windows Server")
        # modifica valida
        r = self.client.patch("/api/drivers/folders/Apply%20Val",
                              json={"apply_to": {"mode": "groups", "groups": ["Windows Server", "Windows Server", " Windows "]}},
                              headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        # duplicati tolti e spazi ripuliti
        self.assertEqual(r.get_json()["folder"]["apply_to"], {"mode": "groups", "groups": ["Windows Server", "Windows"], "isos": []})
        r = self.client.patch("/api/drivers/folders/Apply%20Val",
                              json={"apply_to": {"mode": "isos", "isos": ["win-server-2022"]}}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        a = r.get_json()["folder"]["apply_to"]
        self.assertEqual(a["mode"], "isos")
        self.assertEqual(a["isos"], ["win-server-2022"])
        # apply_to si sostituisce tutto insieme: quello che non arriva viene azzerato
        # (la SPA rimanda sempre l'oggetto completo, così l'elenco già scelto non si perde cambiando modalità)
        self.assertEqual(a["groups"], [])
        r = self.client.patch("/api/drivers/folders/Apply%20Val",
                              json={"apply_to": {"mode": "groups", "groups": ["Windows"], "isos": ["win-server-2022"]}},
                              headers=self.h)
        self.assertEqual(r.get_json()["folder"]["apply_to"],
                         {"mode": "groups", "groups": ["Windows"], "isos": ["win-server-2022"]})
        # valori rifiutati
        for bad in ({"mode": "tutte"}, {"mode": 3}, {"mode": "all", "groups": "Windows"},
                    {"mode": "all", "groups": [3]}, {"mode": "all", "groups": ["x" * 61]},
                    {"mode": "all", "groups": ["g"] * 51}, {"mode": "all", "isos": "win11-pro"},
                    {"mode": "all", "isos": [7]}, {"mode": "all", "isos": ["Win11 Pro"]},
                    {"mode": "all", "isos": ["../fuori"]}, {"mode": "all", "isos": ["x"] * 501},
                    "all", ["all"], 5):
            r = self.client.patch("/api/drivers/folders/Apply%20Val", json={"apply_to": bad}, headers=self.h)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("error", r.get_json())
        # il valore valido precedente non e' stato toccato
        self.assertEqual(self._folders()["Apply Val"]["apply_to"],
                         {"mode": "groups", "groups": ["Windows"], "isos": ["win-server-2022"]})
        # stessa validazione a livello di servizio
        self.assertEqual(drivers.check_apply_to(None), {"mode": "all", "groups": [], "isos": []})
        with self.assertRaises(ValueError):
            drivers.check_apply_to({"mode": "gruppi"})
        self.client.delete("/api/drivers/folders/Apply%20Val", headers=self.h)

    def test_13_apply_to_filters_folders(self):
        from pixio.services import drivers
        self._reset_drivers()
        cat = self._catalog()
        server_iso, pc_iso = cat["win-server-2022"], cat["win11-pro"]
        tool_iso, linux_iso = cat["hirens-pe"], cat["debian-13"]
        for n, payload in (("RAID server", "raid"), ("Rete PC", "nic"), ("Comuni", "com"), ("Solo Hirens", "hir")):
            self.assertEqual(self.client.post("/api/drivers/folders", json={"name": n}, headers=self.h).status_code, 201)
            base = payload
            _put_file(self.client, self.h, n, f"{base}.inf", b"i" * 8)
            _put_file(self.client, self.h, n, f"{base}.sys", b"s" * 8)
            r = self.client.patch(f"/api/drivers/folders/{n.replace(' ', '%20')}",
                                  json={"winpe_inject": True, "setup_load": True}, headers=self.h)
            self.assertEqual(r.status_code, 200, r.get_json())
        self.client.patch("/api/drivers/folders/RAID%20server",
                          json={"apply_to": {"mode": "groups", "groups": ["Windows Server"]}}, headers=self.h)
        self.client.patch("/api/drivers/folders/Rete%20PC",
                          json={"apply_to": {"mode": "groups", "groups": ["Windows"]}}, headers=self.h)
        self.client.patch("/api/drivers/folders/Solo%20Hirens",
                          json={"apply_to": {"mode": "isos", "isos": ["hirens-pe"]}}, headers=self.h)
        # "Comuni" resta su "all"

        def cartelle(iso):
            return sorted({c for c, _n, _p in drivers.winpe_inject_files(iso)})
        # filtro per gruppo
        self.assertEqual(cartelle(server_iso), ["Comuni", "RAID server"])
        self.assertEqual(cartelle(pc_iso), ["Comuni", "Rete PC"])
        # filtro per singola ISO
        self.assertEqual(cartelle(tool_iso), ["Comuni", "Solo Hirens"])
        # una ISO di un gruppo che nessuno ha scelto riceve solo le cartelle "all"
        self.assertEqual(cartelle(linux_iso), ["Comuni"])
        # senza ISO: nessun filtro, comportamento di prima
        self.assertEqual(cartelle(None), ["Comuni", "RAID server", "Rete PC", "Solo Hirens"])
        # i file arrivano davvero, non solo le cartelle
        nomi = sorted(n for c, n, _p in drivers.winpe_inject_files(server_iso))
        self.assertEqual(nomi, ["com.inf", "com.sys", "raid.inf", "raid.sys"])
        # stesso filtro per le cartelle caricate prima del setup di Windows
        self.assertEqual(sorted(drivers.setup_load_folders(server_iso)), ["Comuni", "RAID server"])
        self.assertEqual(sorted(drivers.setup_load_folders(pc_iso)), ["Comuni", "Rete PC"])
        self.assertEqual(sorted(drivers.setup_load_folders(None)),
                         ["Comuni", "RAID server", "Rete PC", "Solo Hirens"])
        # il confronto sul gruppo non guarda maiuscole e spazi
        self.assertEqual(cartelle({"slug": "altro", "group": "  windows server  "}), ["Comuni", "RAID server"])
        # un elenco vuoto vuol dire "nessuna immagine"
        self.client.patch("/api/drivers/folders/RAID%20server",
                          json={"apply_to": {"mode": "groups", "groups": []}}, headers=self.h)
        self.assertEqual(cartelle(server_iso), ["Comuni"])
        self.client.patch("/api/drivers/folders/RAID%20server",
                          json={"apply_to": {"mode": "groups", "groups": ["Windows Server"]}}, headers=self.h)

    def test_14_excluded_files_not_injected(self):
        from pixio.services import drivers
        self._reset_drivers()
        n = "Esclusioni"
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": n}, headers=self.h).status_code, 201)
        for f in ("a.inf", "a.sys", "b.inf", "note.txt"):
            _put_file(self.client, self.h, n, f, b"x" * 12)
        _put_file(self.client, self.h, n, "c.inf", b"x" * 12, path="x64/c.inf")
        _put_file(self.client, self.h, n, "vecchio.inf", b"x" * 12, path="x86/vecchio.inf")
        self.client.patch(f"/api/drivers/folders/{n}", json={"winpe_inject": True}, headers=self.h)
        f = self._folders()[n]
        per_nome = {x["name"]: x for x in f["files"]}
        # candidati: i .inf/.sys/.cat/.dll fuori dalle cartelle a 32 bit
        self.assertTrue(per_nome["a.inf"]["winpe_cand"] and per_nome["x64/c.inf"]["winpe_cand"])
        self.assertFalse(per_nome["note.txt"]["winpe_cand"])
        self.assertFalse(per_nome["x86/vecchio.inf"]["winpe_cand"])
        self.assertEqual((f["winpe_files"], f["winpe_candidates"], f["excluded_files"]), (4, 4, 0))
        self.assertEqual(sorted(x[1] for x in drivers.winpe_inject_files()), ["a.inf", "a.sys", "b.inf", "c.inf"])
        # esclusione di un file
        r = self.client.patch(f"/api/drivers/folders/{n}/files/b.inf", json={"excluded": True}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        f = r.get_json()["folder"]
        self.assertEqual(f["excluded"], ["b.inf"])
        self.assertEqual((f["winpe_files"], f["winpe_candidates"], f["excluded_files"]), (3, 4, 1))
        per_nome = {x["name"]: x for x in f["files"]}
        self.assertTrue(per_nome["b.inf"]["excluded"])
        self.assertFalse(per_nome["b.inf"]["winpe"])
        self.assertTrue(per_nome["a.inf"]["winpe"])
        self.assertEqual(sorted(x[1] for x in drivers.winpe_inject_files()), ["a.inf", "a.sys", "c.inf"])
        self.assertNotIn("b.inf", [x[1] for x in drivers.winpe_inject_files(None)])
        # e il file resta sul disco
        self.assertTrue(os.path.isfile(os.path.join(C.DRIVERS_DIR, n, "b.inf")))
        # rimesso dentro
        r = self.client.patch(f"/api/drivers/folders/{n}/files/b.inf", json={"excluded": False}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["folder"]["excluded"], [])
        self.assertIn("b.inf", [x[1] for x in drivers.winpe_inject_files()])
        # corpo senza "excluded" -> 400; cartella inesistente -> 404
        self.assertEqual(self.client.patch(f"/api/drivers/folders/{n}/files/b.inf", json={}, headers=self.h).status_code, 400)
        self.assertEqual(self.client.patch("/api/drivers/folders/Fantasma/files/b.inf",
                                           json={"excluded": True}, headers=self.h).status_code, 404)
        self.client.patch(f"/api/drivers/folders/{n}", json={"winpe_inject": False}, headers=self.h)

    def test_15_ipxe_script_per_iso(self):
        from pixio.services import ipxe_menu
        self._reset_drivers()
        self._catalog()
        for n, base, apply_to in (("Driver server", "megaraid", {"mode": "groups", "groups": ["Windows Server"]}),
                                  ("Driver PC", "i225", {"mode": "groups", "groups": ["Windows"]}),
                                  ("Driver comuni", "usb3", {"mode": "all"})):
            self.assertEqual(self.client.post("/api/drivers/folders", json={"name": n}, headers=self.h).status_code, 201)
            _put_file(self.client, self.h, n, f"{base}.inf", b"i" * 8)
            self.client.patch("/api/drivers/folders/" + n.replace(" ", "%20"),
                              json={"winpe_inject": True, "apply_to": apply_to}, headers=self.h)
        srv, _w = ipxe_menu.entry_script("win-server-2022", "efi")
        pc, _w = ipxe_menu.entry_script("win11-pro", "efi")
        # la ISO server riceve i driver server e quelli comuni, non quelli abbinati al PC
        self.assertIn("megaraid.inf", srv)
        self.assertIn("usb3.inf", srv)
        self.assertNotIn("i225.inf", srv)
        # e viceversa
        self.assertIn("i225.inf", pc)
        self.assertIn("usb3.inf", pc)
        self.assertNotIn("megaraid.inf", pc)
        # le righe sono quelle di iniezione dei driver, con l'URL della cartella giusta
        self.assertIn("initrd http://10.10.0.254/pxe/drivers/Driver%20server/megaraid.inf megaraid.inf || goto failed", srv)
        # install.cmd: lo stesso filtro vale per i drvload dentro il WinPE
        from pixio.services import winpe
        self.assertIn("megaraid.inf", winpe.install_cmd("win-server-2022"))
        self.assertNotIn("megaraid.inf", winpe.install_cmd("win11-pro"))
        self.assertIn("i225.inf", winpe.install_cmd("win11-pro"))
        # una ISO che non c'e' non fa saltare nulla (nessun filtro applicabile)
        self.assertIsNone(winpe.iso_for("mai-vista"))
        self.assertTrue(winpe.install_cmd("mai-vista"))
        self._reset_drivers()

    # ------------------------------------- coerenza fra .inf e i file che dichiara (docs/API.md, sezione 21)
    def test_16_inf_declared_files(self):
        """Lettura dei file dichiarati da un .inf: UTF-16 con BOM, ANSI, continuazioni, commenti."""
        from pixio.services import drivers
        tmp = tempfile.mkdtemp(prefix="pixio-inf-")
        try:
            def scrivi(nome, testo, encoding="utf-16"):
                full = os.path.join(tmp, nome)
                with open(full, "wb") as fh:
                    fh.write(_inf_bytes(testo, encoding))
                return full

            # UTF-16 con BOM, come quasi tutti gli .inf dei pacchetti Intel
            u16 = scrivi("prova.inf", INF_PROVA)
            self.assertEqual(_leggi(u16)[:2], b"\xff\xfe")
            self.assertEqual(sorted(drivers.inf_declared_files(u16)),
                             ["aiuto.sys", "prova.dll", "prova.exe", "prova.sys"])
            # solo quelli che finirebbero anche loro nel WinPE (.inf/.sys/.cat)
            self.assertEqual(drivers.inf_needed_files(u16), ["aiuto.sys", "prova.sys"])
            # il nome citato in un commento non conta, e il .cat di CatalogFile nemmeno
            self.assertNotIn("finto.sys", drivers.inf_declared_files(u16))
            self.assertNotIn("prova.cat", drivers.inf_declared_files(u16))
            # ANSI (cp1252) con accenti: stessa lettura
            ansi = scrivi("ansi.inf", INF_PROVA.replace("Disco driver", "Disco però"), "cp1252")
            self.assertEqual(_leggi(ansi)[:2], b"; ")
            self.assertEqual(drivers.inf_needed_files(ansi), ["aiuto.sys", "prova.sys"])
            # UTF-8 con BOM: capita nei pacchetti riconfezionati
            u8 = scrivi("utf8.inf", INF_PROVA, "utf-8-sig")
            self.assertEqual(drivers.inf_needed_files(u8), ["aiuto.sys", "prova.sys"])
            # la riga di continuazione: senza unirla Extra_files_copy (prova.dll) si perderebbe
            self.assertIn("prova.dll", drivers.inf_declared_files(u16))
            # .inf che non porta file propri (solo registro): non dichiara niente
            senza = scrivi("senza.inf", INF_SENZA_FILE)
            self.assertEqual(drivers.inf_declared_files(senza), set())
            self.assertEqual(drivers.inf_needed_files(senza), [])
            # la grafia dell'.inf si conserva per gli avvisi, i confronti restano in minuscolo
            maiuscole = scrivi("maiuscole.inf", "[Prova_service]\nServiceBinary = %12%\\MioDriver.SYS\n")
            self.assertEqual(drivers.inf_declared_files(maiuscole), {"miodriver.sys"})
            self.assertEqual(drivers.inf_needed_files(maiuscole), ["MioDriver.SYS"])
            # file illeggibile o troppo grande: nessuna dipendenza, nessuna eccezione
            self.assertEqual(drivers.inf_declared_files(os.path.join(tmp, "mai-visto.inf")), set())
            limite, drivers.INF_MAX_BYTES = drivers.INF_MAX_BYTES, 32
            try:
                self.assertEqual(drivers.inf_declared_files(scrivi("grande.inf", INF_PROVA)), set())
            finally:
                drivers.INF_MAX_BYTES = limite
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_17_inf_complete_copy_wins(self):
        """Fra più copie dello stesso .inf vince quella che ha accanto i file che dichiara."""
        from pixio.services import drivers
        self._reset_drivers()
        n = "Copie INF"
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": n}, headers=self.h).status_code, 201)
        # copia al primo livello: sarebbe la preferita, ma le manca aiuto.sys
        _put_file(self.client, self.h, n, "prova.inf", _inf_bytes(INF_PROVA))
        _put_file(self.client, self.h, n, "prova.sys", b"radice-sys")
        _put_file(self.client, self.h, n, "prova.cat", b"radice-cat")
        # copia completa in una sottocartella, meno preferita dall'ordine di sempre
        for f, data in (("prova.inf", _inf_bytes(INF_PROVA)), ("prova.sys", b"pacchetto-sys"),
                        ("aiuto.sys", b"pacchetto-aiuto")):
            _put_file(self.client, self.h, n, f, data, path="pacchetto/x64/" + f)
        self.client.patch(f"/api/drivers/folders/{n.replace(' ', '%20')}",
                          json={"winpe_inject": True}, headers=self.h)

        f = self._folders()[n]
        per_nome = {x["name"]: x for x in f["files"]}
        # ogni copia dice cosa le manca
        self.assertEqual(per_nome["prova.inf"]["inf_missing"], ["aiuto.sys"])
        self.assertEqual(per_nome["pacchetto/x64/prova.inf"]["inf_missing"], [])
        # vince la copia completa, e si porta dietro i file che dichiara presi dalla sua cartella
        self.assertFalse(per_nome["prova.inf"]["winpe"])
        self.assertTrue(per_nome["pacchetto/x64/prova.inf"]["winpe"])
        self.assertFalse(per_nome["prova.sys"]["winpe"])
        self.assertTrue(per_nome["pacchetto/x64/prova.sys"]["winpe"])
        self.assertTrue(per_nome["pacchetto/x64/aiuto.sys"]["winpe"])
        # un file che l'.inf non dichiara resta scelto come prima: vince la radice
        self.assertTrue(per_nome["prova.cat"]["winpe"])
        # la copia scelta ha accanto tutti i .inf/.sys/.cat che dichiara; resta l'avviso per prova.dll e
        # prova.exe, dichiarati dall'.inf e assenti da ogni copia della cartella (sezione 21)
        self.assertEqual(f["winpe_missing"],
                         [{"inf": "pacchetto/x64/prova.inf", "missing": ["prova.dll", "prova.exe"]}])
        # e nell'iniezione vera i percorsi sono quelli della copia completa
        scelti = {nome: p for _c, nome, p in drivers.winpe_inject_files()}
        self.assertTrue(scelti["prova.inf"].endswith(os.path.join("pacchetto", "x64", "prova.inf")))
        self.assertEqual(_leggi(scelti["prova.sys"]), b"pacchetto-sys")
        self.assertEqual(_leggi(scelti["aiuto.sys"]), b"pacchetto-aiuto")
        self.assertEqual(_leggi(scelti["prova.cat"]), b"radice-cat")

        # un file dichiarato ma escluso a mano resta fuori: la scelta dell'utente vale
        r = self.client.patch(f"/api/drivers/folders/{n}/files/pacchetto/x64/aiuto.sys",
                              json={"excluded": True}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertNotIn("aiuto.sys", [x[1] for x in drivers.winpe_inject_files()])
        self.client.patch(f"/api/drivers/folders/{n}/files/pacchetto/x64/aiuto.sys",
                          json={"excluded": False}, headers=self.h)

        # cartella diversa con lo stesso nome di file: l'.inf tiene il suo, non quello dell'altra cartella
        a = "AAA doppioni"      # prima in ordine alfabetico: senza la regola vincerebbe il suo prova.sys
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": a}, headers=self.h).status_code, 201)
        _put_file(self.client, self.h, a, "prova.sys", b"altra-cartella")
        _put_file(self.client, self.h, a, "solo.inf", _inf_bytes(INF_SENZA_FILE))
        self.client.patch(f"/api/drivers/folders/{a.replace(' ', '%20')}",
                          json={"winpe_inject": True}, headers=self.h)
        scelti = {nome: p for _c, nome, p in drivers.winpe_inject_files()}
        self.assertEqual(_leggi(scelti["prova.sys"]), b"pacchetto-sys")
        self.assertEqual(_leggi(scelti["aiuto.sys"]), b"pacchetto-aiuto")
        self.assertIn("solo.inf", scelti)
        self._reset_drivers()

    def test_18_inf_missing_files_warning(self):
        """Se nessuna copia è completa il file mancante viene segnalato, invece di scoprirlo con drvload."""
        from pixio.services import drivers
        self._reset_drivers()
        n = "INF monco"
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": n}, headers=self.h).status_code, 201)
        _put_file(self.client, self.h, n, "prova.inf", _inf_bytes(INF_PROVA))
        _put_file(self.client, self.h, n, "prova.sys", b"sys")
        _put_file(self.client, self.h, n, "prova.inf", _inf_bytes(INF_PROVA), path="altra/prova.inf")
        self.client.patch(f"/api/drivers/folders/{n.replace(' ', '%20')}",
                          json={"winpe_inject": True}, headers=self.h)
        f = self._folders()[n]
        # nessuna copia ha aiuto.sys: si inietta comunque la migliore, ma con l'avviso (che elenca anche
        # prova.dll e prova.exe: l'.inf li dichiara e non ci sono in nessuna copia della cartella)
        self.assertEqual(f["winpe_missing"],
                         [{"inf": "prova.inf", "missing": ["aiuto.sys", "prova.dll", "prova.exe"]}])
        self.assertEqual({x["name"]: x["inf_missing"] for x in f["files"] if x["name"].endswith(".inf")},
                         {"prova.inf": ["aiuto.sys"], "altra/prova.inf": ["aiuto.sys", "prova.sys"]})
        self.assertIn("prova.inf", [x[1] for x in drivers.winpe_inject_files()])
        self.assertNotIn("aiuto.sys", [x[1] for x in drivers.winpe_inject_files()])
        # lo stesso avviso arriva alla GUI da GET /api/drivers
        d = self.client.get("/api/drivers", headers=self.h).get_json()
        cart = [x for x in d["folders"] if x["name"] == n][0]
        self.assertEqual(cart["winpe_missing"],
                         [{"inf": "prova.inf", "missing": ["aiuto.sys", "prova.dll", "prova.exe"]}])
        # messo il file accanto all'.inf l'avviso su aiuto.sys sparisce e il file viene iniettato
        _put_file(self.client, self.h, n, "aiuto.sys", b"aiuto")
        f = self._folders()[n]
        self.assertEqual(f["winpe_missing"],
                         [{"inf": "prova.inf", "missing": ["prova.dll", "prova.exe"]}])
        self.assertIn("aiuto.sys", [x[1] for x in drivers.winpe_inject_files()])
        # il limite di dimensione resta rispettato: nessun file oltre MAX_INJECT_BYTES
        vecchio, drivers.MAX_INJECT_BYTES = drivers.MAX_INJECT_BYTES, 1
        try:
            self.assertEqual(drivers.winpe_inject_files(), [])
        finally:
            drivers.MAX_INJECT_BYTES = vecchio
        self._reset_drivers()

    def test_19_declared_files_any_extension(self):
        """Un .inf iniettato porta con se\u2019 i file che dichiara, .dll comprese; gli altri restano fuori."""
        from pixio.services import drivers
        self._reset_drivers()
        n = "RST finto"
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": n}, headers=self.h).status_code, 201)
        for nome, data in (("rst.inf", _inf_bytes(INF_RST)), ("rst.sys", b"sys"), ("rst.cat", b"cat"),
                           ("RstMsg.dll", b"messaggi"), ("version.dll", b"finta version"),
                           ("libera.dll", b"non dichiarata"), ("lettimi.txt", b"documentazione")):
            _put_file(self.client, self.h, n, nome, data)
        self.client.patch(f"/api/drivers/folders/{n.replace(' ', '%20')}",
                          json={"winpe_inject": True}, headers=self.h)
        f = self._folders()[n]
        per_nome = {x["name"]: x for x in f["files"]}
        # la .dll dichiarata dall'.inf entra nel WinPE...
        self.assertTrue(per_nome["RstMsg.dll"]["winpe_cand"])
        self.assertTrue(per_nome["RstMsg.dll"]["winpe"])
        # ...quella che sta nella cartella ma nessun .inf dichiara resta fuori (e non e\u2019 nemmeno candidata)
        self.assertFalse(per_nome["libera.dll"]["winpe_cand"])
        self.assertFalse(per_nome["libera.dll"]["winpe"])
        self.assertFalse(per_nome["lettimi.txt"]["winpe_cand"])
        # version.dll e\u2019 un file che il WinPE ha gia\u2019 in System32: iniettarla lo sostituirebbe
        self.assertTrue(drivers.is_winpe_system_file("VERSION.DLL"))
        self.assertFalse(drivers.is_winpe_system_file("RstMsg.dll"))
        self.assertFalse(per_nome["version.dll"]["winpe_cand"])
        self.assertFalse(per_nome["version.dll"]["winpe"])
        self.assertEqual(f["winpe_shadowed"], [{"inf": "rst.inf", "files": ["version.dll"]}])
        # il file dichiarato che non c'e\u2019 in nessuna copia finisce fra gli avvisi
        self.assertEqual(f["winpe_missing"], [{"inf": "rst.inf", "missing": ["RstServizio.exe"]}])
        self.assertEqual(per_nome["rst.inf"]["inf_missing"], [])
        # nell'iniezione vera ci sono i tre file del driver piu\u2019 la .dll dichiarata, e nient'altro
        self.assertEqual(sorted(x[1] for x in drivers.winpe_inject_files()),
                         ["RstMsg.dll", "rst.cat", "rst.inf", "rst.sys"])
        # la .dll dichiarata si puo\u2019 comunque escludere a mano
        r = self.client.patch(f"/api/drivers/folders/{n}/files/RstMsg.dll",
                              json={"excluded": True}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertNotIn("RstMsg.dll", [x[1] for x in drivers.winpe_inject_files()])
        self.client.patch(f"/api/drivers/folders/{n}/files/RstMsg.dll",
                          json={"excluded": False}, headers=self.h)

        # ---- piu\u2019 copie: la .dll dichiarata conta nella scelta della copia, e la GUI dice qual e\u2019 quella buona
        m = "Copie con dll"
        self.assertEqual(self.client.post("/api/drivers/folders", json={"name": m}, headers=self.h).status_code, 201)
        for nome, data in (("prova.inf", _inf_bytes(INF_PROVA)), ("prova.sys", b"radice-sys"),
                           ("aiuto.sys", b"radice-aiuto")):
            _put_file(self.client, self.h, m, nome, data)            # radice: le manca prova.dll
        for nome, data in (("prova.inf", _inf_bytes(INF_PROVA)), ("prova.sys", b"completo-sys"),
                           ("aiuto.sys", b"completo-aiuto"), ("prova.dll", b"completo-dll")):
            _put_file(self.client, self.h, m, nome, data, path="completo/x64/" + nome)
        self.client.patch(f"/api/drivers/folders/{m.replace(' ', '%20')}",
                          json={"winpe_inject": True}, headers=self.h)
        f = self._folders()[m]
        per_nome = {x["name"]: x for x in f["files"]}
        # la copia in radice e\u2019 monca proprio per la .dll: vince quella completa, anche se meno preferita
        self.assertEqual(per_nome["prova.inf"]["inf_missing"], ["prova.dll"])
        self.assertEqual(per_nome["completo/x64/prova.inf"]["inf_missing"], [])
        self.assertTrue(per_nome["completo/x64/prova.inf"]["winpe"])
        self.assertTrue(per_nome["completo/x64/prova.dll"]["winpe"])
        self.assertEqual(f["winpe_better"], [])
        scelti = {nome: p for _c, nome, p in drivers.winpe_inject_files()}
        self.assertEqual(_leggi(scelti["prova.dll"]), b"completo-dll")
        self.assertEqual(_leggi(scelti["prova.sys"]), b"completo-sys")
        # escluso a mano l'.inf completo viene iniettata la copia monca: la GUI dice dov'e\u2019 quella coerente
        r = self.client.patch(f"/api/drivers/folders/{m}/files/completo/x64/prova.inf",
                              json={"excluded": True}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        f = r.get_json()["folder"]
        per_nome = {x["name"]: x for x in f["files"]}
        self.assertTrue(per_nome["prova.inf"]["winpe"])
        self.assertEqual(f["winpe_better"],
                         [{"inf": "prova.inf", "copy": "completo/x64/prova.inf", "folder": "completo/x64"}])
        self.assertEqual(f["winpe_missing"], [{"inf": "prova.inf", "missing": ["prova.dll", "prova.exe"]}])
        self.assertNotIn("prova.dll", [x[1] for x in drivers.winpe_inject_files()])
        self._reset_drivers()

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
