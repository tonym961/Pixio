"""Test del backup della configurazione (services/backup.py + blueprints/api_backup.py).

Esecuzione: cd /opt/pixio && python3 -m unittest tests.test_backup
I percorsi di config vengono reindirizzati in una directory temporanea in setUpClass e ripristinati
alla fine; pixio.privileged.call è sostituito da un finto.
"""
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest

os.environ["PIXIO_NO_BACKGROUND"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pixio import config as C  # noqa: E402
from pixio import privileged  # noqa: E402

OVERRIDE_KEYS = ("ETC_DIR", "CONFIG_FILE", "SECRET_FILE", "SOURCES_DIR", "VAR_DIR", "JOBS_DIR", "UPLOAD_TMP_DIR",
                 "CLIENTS_FILE", "CATALOG_FILE", "DRIVERS_FILE", "ANSWERS_DIR", "ANSWERS_FILE", "LOG_DIR",
                 "SRV_DIR", "LIBRARY_DIR", "CACHE_DIR", "TFTP_DIR", "HTTP_DIR", "SOURCES_MOUNT_DIR")


def _fake_call(*args, stdin_text=None, timeout=180):
    if args and args[0] == "list-mounts":
        return {"iso": {}, "sources": {}}
    return {"ok": True}


def _tar(entries, links=None):
    """Crea un tar.gz in memoria. entries: [(nome, bytes)]; links: [(nome, destinazione)]."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for name, target in (links or []):
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tf.addfile(info)
    return buf.getvalue()


def _members(data):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        return {m.name: (tf.extractfile(m).read() if m.isreg() else b"") for m in tf.getmembers()}


class BackupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pixio-backup-test-")
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
        C.ANSWERS_DIR = os.path.join(C.VAR_DIR, "answers")
        C.ANSWERS_FILE = os.path.join(C.VAR_DIR, "answers.json")
        C.LOG_DIR = os.path.join(t, "log")
        C.SRV_DIR = os.path.join(t, "srv")
        C.LIBRARY_DIR = os.path.join(C.SRV_DIR, "library")
        C.CACHE_DIR = os.path.join(C.SRV_DIR, "cache")
        C.TFTP_DIR = os.path.join(C.SRV_DIR, "tftp")
        C.HTTP_DIR = os.path.join(C.SRV_DIR, "http")
        C.SOURCES_MOUNT_DIR = os.path.join(C.SRV_DIR, "sources")
        for d in (C.ETC_DIR, C.VAR_DIR, C.JOBS_DIR, C.LOG_DIR, C.LIBRARY_DIR, C.CACHE_DIR, C.TFTP_DIR,
                  C.ANSWERS_DIR, C.SOURCES_MOUNT_DIR):
            os.makedirs(d, exist_ok=True)
        cls._orig_call = privileged.call
        privileged.call = _fake_call
        from pixio import create_app
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        from pixio.blueprints import api_backup
        if "api_backup" not in cls.app.blueprints:     # non è (ancora) in blueprints/MODULES
            cls.app.register_blueprint(api_backup.bp)
        cls.client = cls.app.test_client()
        r = cls.client.post("/api/auth/login", json={"password": "segreta1"})
        assert r.status_code == 200, r.get_json()
        cls.h = {"X-CSRF-Token": r.get_json()["csrf"]}
        from pixio.services import backup
        cls.backup = backup

    @classmethod
    def tearDownClass(cls):
        privileged.call = cls._orig_call
        for k, v in cls.saved.items():
            setattr(C, k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- fixture
    def setUp(self):
        self.hash = self._config().get("auth", {}).get("password_hash", "")
        self.assertTrue(self.hash, "la password di prova deve essere impostata")
        self._write(C.CONFIG_FILE, {"network": {"interface": "lo", "server_ip": "10.10.0.254"},
                                    "menu": {"title": "PIXIO - prova", "timeout": 15},
                                    "windows": {"smb_export_enabled": True, "smb_password": "SegretoSMB"},
                                    "cache": {"auto": True, "min_size_gb": 3},
                                    "auth": {"password_hash": self.hash, "session_hours": 12}})
        self._write(C.CATALOG_FILE, {"isos": {"alpine": {"slug": "alpine", "source": "local", "name": "Alpine",
                                                         "enabled": True,
                                                         "cache": {"status": "ready", "path": "/sparito.iso",
                                                                   "progress": 100}}},
                                     "last_scan": "2026-09-08T10:00:00"})
        self._write(C.CLIENTS_FILE, {"aa:bb:cc:dd:ee:ff": {"mac": "aa:bb:cc:dd:ee:ff", "name": "Portatile"}})
        self._write(C.DRIVERS_FILE, {"folders": {"Rete Realtek": {"winpe_inject": True, "note": "schede r8168"}}})
        self._write(C.ANSWERS_FILE, {"answers": {"win": {"id": "win", "name": "Windows base", "kind": "windows",
                                                         "main_file": "autounattend.xml"}}})
        shutil.rmtree(C.ANSWERS_DIR, ignore_errors=True)
        os.makedirs(os.path.join(C.ANSWERS_DIR, "win"), exist_ok=True)
        with open(os.path.join(C.ANSWERS_DIR, "win", "autounattend.xml"), "w") as f:
            f.write("<unattend/>")

    def _write(self, path, data):
        with open(path, "w") as f:
            json.dump(data, f)

    def _config(self):
        try:
            with open(C.CONFIG_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    # ---------------------------------------------------------------- esportazione
    def test_01_download_headers_and_content(self):
        r = self.client.get("/api/backup")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/gzip")
        cd = r.headers["Content-Disposition"]
        self.assertIn("attachment;", cd)
        self.assertIn("pixio-backup-", cd)
        self.assertTrue(cd.rstrip('"').endswith(".tar.gz"), cd)
        m = _members(r.data)
        for name in ("manifest.json", "config.json", "catalog.json", "clients.json", "drivers.json", "answers.json"):
            self.assertIn(name, m)
        self.assertIn("answers/win/autounattend.xml", m)
        self.assertEqual(m["answers/win/autounattend.xml"], b"<unattend/>")

    def test_02_export_hides_secrets(self):
        m = _members(self.backup.export_archive())
        cfg = json.loads(m["config.json"])
        self.assertNotIn("password_hash", cfg.get("auth", {}))
        self.assertNotIn("smb_password", cfg.get("windows", {}))
        self.assertEqual(cfg["menu"]["title"], "PIXIO - prova")
        self.assertTrue(cfg["windows"]["smb_export_enabled"])
        info = json.loads(m["manifest.json"])
        self.assertEqual(info["version"], C.VERSION)
        self.assertTrue(info["hostname"])
        self.assertIn("password", info["note"])
        self.assertIn("config.json", info["files"])

    def test_03_archive_name(self):
        name = self.backup.archive_name()
        self.assertTrue(name.startswith("pixio-backup-"))
        self.assertTrue(name.endswith(".tar.gz"))
        self.assertNotIn(" ", name)

    # ---------------------------------------------------------------- ripristino
    def test_04_roundtrip_keeps_credentials(self):
        data = self.backup.export_archive()
        self._write(C.CONFIG_FILE, {"menu": {"title": "cambiato", "timeout": 99},
                                    "windows": {"smb_password": "AltraSegreta"},
                                    "auth": {"password_hash": self.hash}})
        res = self.backup.restore(data)
        cfg = self._config()
        self.assertEqual(cfg["menu"]["title"], "PIXIO - prova")
        self.assertEqual(cfg["menu"]["timeout"], 15)
        self.assertEqual(cfg["auth"]["password_hash"], self.hash)      # non è nel backup: resta quella attuale
        self.assertEqual(cfg["windows"]["smb_password"], "AltraSegreta")
        self.assertTrue(any("Impostazioni" in x for x in res["restored"]))
        self.assertTrue(any("password" in w for w in res["warnings"]))

    def test_05_roundtrip_restores_all_sections(self):
        data = self.backup.export_archive()
        os.unlink(C.CLIENTS_FILE)
        self._write(C.DRIVERS_FILE, {"folders": {}})
        self._write(C.ANSWERS_FILE, {"answers": {}})
        shutil.rmtree(C.ANSWERS_DIR, ignore_errors=True)
        res = self.backup.restore(data)
        with open(C.CLIENTS_FILE) as f:
            self.assertIn("aa:bb:cc:dd:ee:ff", json.load(f))
        with open(C.DRIVERS_FILE) as f:
            self.assertIn("Rete Realtek", json.load(f)["folders"])
        with open(C.ANSWERS_FILE) as f:
            self.assertIn("win", json.load(f)["answers"])
        p = os.path.join(C.ANSWERS_DIR, "win", "autounattend.xml")
        self.assertTrue(os.path.isfile(p))
        with open(p) as f:
            self.assertEqual(f.read(), "<unattend/>")
        labels = " | ".join(res["restored"])
        for frag in ("Impostazioni", "Catalogo ISO", "Client PXE", "Cartelle driver", "Risposte automatiche",
                     "File delle risposte"):
            self.assertIn(frag, labels)

    def test_06_catalog_cache_state_is_reset(self):
        data = self.backup.export_archive()
        self._write(C.CATALOG_FILE, {"isos": {}})
        self.backup.restore(data)
        with open(C.CATALOG_FILE) as f:
            iso = json.load(f)["isos"]["alpine"]
        self.assertEqual(iso["cache"], {"status": "none", "path": "", "progress": 0})
        self.assertTrue(iso["enabled"])

    def test_07_partial_archive(self):
        """Un tar con solo clients.json non deve toccare il resto."""
        prima = self._config()
        data = _tar([("clients.json", json.dumps({"11:22:33:44:55:66": {"mac": "11:22:33:44:55:66"}}).encode())])
        res = self.backup.restore(data)
        self.assertEqual(len(res["restored"]), 1)
        self.assertIn("Client PXE", res["restored"][0])
        with open(C.CLIENTS_FILE) as f:
            self.assertIn("11:22:33:44:55:66", json.load(f))
        self.assertEqual(self._config(), prima)

    def test_08_invalid_json_member_is_warned(self):
        data = _tar([("clients.json", b"non json"),
                     ("drivers.json", json.dumps({"folders": {"X": {}}}).encode())])
        res = self.backup.restore(data)
        self.assertTrue(any("clients.json" in w for w in res["warnings"]))
        self.assertEqual(len(res["restored"]), 1)

    def test_09_unsafe_archives_are_refused(self):
        cases = {
            "assoluto": _tar([("/etc/passwd", b"x")]),
            "risalita": _tar([("../../etc/passwd", b"x")]),
            "risalita interna": _tar([("answers/../../x.json", b"x")]),
            "collegamento": _tar([("config.json", b"{}")], links=[("answers/win/link.xml", "/etc/shadow")]),
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(ValueError) as ctx:
                    self.backup.restore(data)
                self.assertIn("non sicuro", str(ctx.exception).lower())
        # nome non previsto: ignorato con avviso, non è un errore
        data = _tar([("config.json", json.dumps({"menu": {"timeout": 7}}).encode()), ("segreto.sh", b"rm -rf /")])
        res = self.backup.restore(data)
        self.assertTrue(any("segreto.sh" in w for w in res["warnings"]))
        self.assertFalse(os.path.exists(os.path.join(C.VAR_DIR, "segreto.sh")))

    def test_10_bad_archives(self):
        with self.assertRaises(ValueError):
            self.backup.restore(b"")
        with self.assertRaises(ValueError):
            self.backup.restore(b"questo non e' un tar.gz")
        with self.assertRaises(ValueError) as ctx:          # solo il manifest: niente da ripristinare
            self.backup.restore(_tar([("manifest.json", b'{"app": "Pixio"}')]))
        self.assertIn("ripristinare", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:          # nessun nome previsto
            self.backup.restore(_tar([("altro.txt", b"x")]))
        self.assertIn("nessun file", str(ctx.exception).lower())

    def test_11_oversized_content_is_refused(self):
        big = _tar([("catalog.json", b"\0" * (self.backup.MAX_ARCHIVE + 1024))])
        self.assertLess(len(big), self.backup.MAX_ARCHIVE, "gli zeri comprimono: il limite scatta sul contenuto")
        with self.assertRaises(ValueError) as ctx:
            self.backup.restore(big)
        self.assertIn("troppo grande", str(ctx.exception))

    # ---------------------------------------------------------------- API
    def test_12_restore_api_multipart_and_raw(self):
        data = self.backup.export_archive()
        self._write(C.CLIENTS_FILE, {})
        r = self.client.post("/api/backup/restore", headers=self.h,
                             data={"file": (io.BytesIO(data), "pixio-backup.tar.gz")},
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertTrue(d["restored"])
        self.assertIsInstance(d["warnings"], list)
        with open(C.CLIENTS_FILE) as f:
            self.assertIn("aa:bb:cc:dd:ee:ff", json.load(f))
        self._write(C.CLIENTS_FILE, {})
        r = self.client.post("/api/backup/restore", headers=self.h, data=data,
                             content_type="application/octet-stream")
        self.assertEqual(r.status_code, 200, r.get_json())
        with open(C.CLIENTS_FILE) as f:
            self.assertIn("aa:bb:cc:dd:ee:ff", json.load(f))

    def test_13_restore_api_errors(self):
        r = self.client.post("/api/backup/restore", headers=self.h, data=b"",
                             content_type="application/octet-stream")
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())
        r = self.client.post("/api/backup/restore", headers=self.h, data=b"spazzatura",
                             content_type="application/octet-stream")
        self.assertEqual(r.status_code, 400)
        self.assertIn("non valido", r.get_json()["error"])

    def test_14_requires_login_and_csrf(self):
        anon = self.app.test_client()
        self.assertEqual(anon.get("/api/backup").status_code, 401)
        r = self.client.post("/api/backup/restore", data=b"x", content_type="application/octet-stream")
        self.assertEqual(r.status_code, 403)


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
