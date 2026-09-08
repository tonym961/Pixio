"""Test delle risposte automatiche: API CRUD, creazione da modello, upload di un file aggiuntivo,
kernel_args per i quattro tipi, servizio pubblico dei file (senza sessione) e rifiuto del path traversal.

Esecuzione: cd /opt/pixio && python3 -m unittest tests.test_answers
I percorsi di config vengono reindirizzati in una directory temporanea in setUpClass e ripristinati alla fine,
così il modulo convive con gli altri test nella stessa discovery.
"""
import os
import shutil
import sys
import tempfile
import unittest

os.environ["PIXIO_NO_BACKGROUND"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pixio import config as C  # noqa: E402
from pixio import privileged  # noqa: E402

OVERRIDE_KEYS = ("ETC_DIR", "CONFIG_FILE", "SECRET_FILE", "SOURCES_DIR", "VAR_DIR", "JOBS_DIR", "UPLOAD_TMP_DIR",
                 "CLIENTS_FILE", "CATALOG_FILE", "DRIVERS_FILE", "ANSWERS_DIR", "ANSWERS_FILE", "LOG_DIR",
                 "SRV_DIR", "LIBRARY_DIR", "CACHE_DIR", "TFTP_DIR", "HTTP_DIR", "DRIVERS_DIR", "SOURCES_MOUNT_DIR")


def _fake_call(*args, stdin_text=None, timeout=180):
    if args and args[0] == "list-mounts":
        return {"iso": {}, "sources": {}}
    return {"ok": True}


def _put_file(client, h, folder, name, payload, kind="answer"):
    """Upload completo via API (init + chunk + finish). Ritorna la risposta di finish."""
    r = client.post("/api/upload/init", json={"filename": name, "size": len(payload), "kind": kind, "folder": folder},
                    headers=h)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    uid, cs = d["upload_id"], d["chunk_size"]
    n = (len(payload) + cs - 1) // cs
    for i in range(n):
        r = client.put(f"/api/upload/{uid}/chunk/{i}", data=payload[i * cs:(i + 1) * cs], headers=h,
                       content_type="application/octet-stream")
        assert r.status_code == 200, r.get_json()
    return client.post(f"/api/upload/{uid}/finish", headers=h)


class AnswersApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pixio-ans-test-")
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
        C.DRIVERS_DIR = os.path.join(C.HTTP_DIR, "drivers")
        C.SOURCES_MOUNT_DIR = os.path.join(C.SRV_DIR, "sources")
        for d in (C.ETC_DIR, C.VAR_DIR, C.JOBS_DIR, C.UPLOAD_TMP_DIR, C.LOG_DIR, C.LIBRARY_DIR, C.CACHE_DIR,
                  C.TFTP_DIR, C.DRIVERS_DIR, C.ANSWERS_DIR):
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

    def _answers(self):
        r = self.client.get("/api/answers")
        self.assertEqual(r.status_code, 200, r.get_json())
        return {a["id"]: a for a in r.get_json()["answers"]}

    def _create(self, name, kind, **extra):
        body = {"name": name, "kind": kind}
        body.update(extra)
        r = self.client.post("/api/answers", json=body, headers=self.h)
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["answer"]

    # ---------------------------------------------------------------- elenco e tipi
    def test_01_list_and_kinds(self):
        r = self.client.get("/api/answers")
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertIsInstance(d["answers"], list)
        kinds = {k["id"]: k for k in d["kinds"]}
        self.assertEqual(set(kinds), {"windows", "debian", "ubuntu", "redhat", "generic"})
        self.assertEqual(kinds["windows"]["main_file"], "autounattend.xml")
        self.assertEqual(kinds["ubuntu"]["main_file"], "user-data")
        self.assertEqual(kinds["redhat"]["main_file"], "ks.cfg")
        for k in kinds.values():
            self.assertTrue(k["name"] and k["hint"], k)

    def test_02_templates(self):
        for kind, needle in (("windows", "<unattend"), ("debian", "d-i "), ("ubuntu", "autoinstall:"),
                             ("redhat", "%packages"), ("generic", "Pixio")):
            r = self.client.get(f"/api/answers/templates/{kind}")
            self.assertEqual(r.status_code, 200, r.get_json())
            d = r.get_json()
            self.assertEqual(d["kind"], kind)
            self.assertIn(needle, d["content"])
        self.assertEqual(self.client.get("/api/answers/templates/inesistente").status_code, 400)

    # ---------------------------------------------------------------- CRUD
    def test_03_create_from_template(self):
        tpl = self.client.get("/api/answers/templates/debian").get_json()
        a = self._create("Aula 1 Debian", "debian", note="Laboratorio", content=tpl["content"])
        self.assertEqual(a["id"], "aula-1-debian")
        self.assertEqual(a["kind"], "debian")
        self.assertEqual(a["main_file"], "preseed.cfg")
        self.assertEqual([f["name"] for f in a["files"]], ["preseed.cfg"])
        self.assertEqual(a["used_by"], [])
        r = self.client.get("/api/answers/aula-1-debian")
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertIn("d-i debian-installer/language string it", d["content"])
        self.assertEqual(d["url"], "http://10.10.0.254/answers/aula-1-debian/preseed.cfg")
        self.assertEqual(d["note"], "Laboratorio")

    def test_04_create_ubuntu_makes_meta_data(self):
        tpl = self.client.get("/api/answers/templates/ubuntu").get_json()
        a = self._create("Server Ubuntu", "ubuntu", content=tpl["content"])
        names = [f["name"] for f in a["files"]]
        self.assertIn("user-data", names)
        self.assertIn("meta-data", names)
        self.assertEqual(a["main_file"], "user-data")

    def test_05_create_duplicate_name_gets_new_id(self):
        a = self._create("Aula 1 Debian", "debian")
        self.assertEqual(a["id"], "aula-1-debian-2")
        self.assertEqual(a["files"], [])          # senza content non viene scritto nulla

    def test_06_create_errors(self):
        r = self.client.post("/api/answers", json={"name": "", "kind": "debian"}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        self.assertIn("nome", r.get_json()["error"].lower())
        r = self.client.post("/api/answers", json={"name": "X", "kind": "solaris"}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/answers", json={"name": "X", "kind": "debian", "filename": "boot.exe"},
                             headers=self.h)
        self.assertEqual(r.status_code, 400)

    def test_07_update_content_and_metadata(self):
        r = self.client.put("/api/answers/aula-1-debian",
                            json={"name": "Aula 1 (Debian 13)", "note": "aggiornata",
                                  "content": "d-i debian-installer/locale string it_IT.UTF-8\n"},
                            headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        a = r.get_json()["answer"]
        self.assertEqual(a["name"], "Aula 1 (Debian 13)")
        self.assertEqual(a["note"], "aggiornata")
        d = self.client.get("/api/answers/aula-1-debian").get_json()
        self.assertEqual(d["content"], "d-i debian-installer/locale string it_IT.UTF-8\n")
        r = self.client.put("/api/answers/aula-1-debian", json={}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.client.put("/api/answers/mai-esistita", json={"note": "x"}, headers=self.h)
        self.assertEqual(r.status_code, 404)

    def test_08_update_extra_file_and_delete(self):
        r = self.client.put("/api/answers/aula-1-debian",
                            json={"filename": "postinstall.sh", "content": "#!/bin/sh\necho ciao\n"}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        names = [f["name"] for f in r.get_json()["answer"]["files"]]
        self.assertIn("postinstall.sh", names)
        d = self.client.get("/api/answers/aula-1-debian?file=postinstall.sh").get_json()
        self.assertIn("echo ciao", d["content"])
        r = self.client.delete("/api/answers/aula-1-debian/files/postinstall.sh", headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertNotIn("postinstall.sh", [f["name"] for f in r.get_json()["answer"]["files"]])
        self.assertEqual(self.client.delete("/api/answers/aula-1-debian/files/postinstall.sh",
                                            headers=self.h).status_code, 404)

    def test_09_delete_answer(self):
        a = self._create("Da buttare", "generic")
        self.assertIn(a["id"], self._answers())
        r = self.client.delete(f"/api/answers/{a['id']}", headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertNotIn(a["id"], self._answers())
        self.assertFalse(os.path.exists(os.path.join(C.ANSWERS_DIR, a["id"])))
        self.assertEqual(self.client.delete(f"/api/answers/{a['id']}", headers=self.h).status_code, 404)

    # ---------------------------------------------------------------- upload di file aggiuntivi
    def test_10_upload_extra_file(self):
        a = self._create("Windows aula", "windows", content="<unattend/>\n")
        payload = b"@echo off\r\necho Pixio\r\n"
        r = _put_file(self.client, self.h, a["id"], "postsetup.cmd", payload)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d["kind"], "answer")
        self.assertEqual(d["answer_id"], a["id"])
        on_disk = os.path.join(C.ANSWERS_DIR, a["id"], "postsetup.cmd")
        self.assertTrue(os.path.isfile(on_disk))
        with open(on_disk, "rb") as f:
            self.assertEqual(f.read(), payload)
        names = [f["name"] for f in self.client.get(f"/api/answers/{a['id']}").get_json()["files"]]
        self.assertIn("postsetup.cmd", names)

    def test_11_upload_rejects_bad_extension_and_folder(self):
        r = self.client.post("/api/upload/init",
                             json={"filename": "virus.exe", "size": 10, "kind": "answer", "folder": "windows-aula"},
                             headers=self.h)
        self.assertEqual(r.status_code, 400, r.get_json())
        r = self.client.post("/api/upload/init",
                             json={"filename": "x.xml", "size": 10, "kind": "answer", "folder": "mai-esistita"},
                             headers=self.h)
        self.assertEqual(r.status_code, 404, r.get_json())
        r = self.client.post("/api/upload/init",
                             json={"filename": "x.xml", "size": 10, "kind": "answer", "folder": "../../etc"},
                             headers=self.h)
        self.assertEqual(r.status_code, 400, r.get_json())

    # ---------------------------------------------------------------- funzioni usate dal boot
    def test_12_kernel_args_and_winpe_files(self):
        from pixio.services import answers
        ip = "10.10.0.254"
        deb = answers.get("aula-1-debian")
        self.assertEqual(answers.kernel_args(deb, "debian-installer", ip),
                         "auto=true priority=critical url=http://10.10.0.254/answers/aula-1-debian/preseed.cfg")
        ubu = answers.get("server-ubuntu")
        self.assertEqual(answers.kernel_args(ubu, "ubuntu-casper", ip),
                         "autoinstall ds=nocloud-net;s=http://10.10.0.254/answers/server-ubuntu/")
        rh = answers.create({"name": "Rocky base", "kind": "redhat", "content": "text\n"})
        self.assertEqual(answers.kernel_args(rh, "redhat-installer", ip),
                         "inst.ks=http://10.10.0.254/answers/rocky-base/ks.cfg")
        win = answers.get("windows-aula")
        self.assertEqual(answers.kernel_args(win, "windows", ip), "")
        self.assertEqual(answers.winpe_files(win, ip),
                         [("autounattend.xml", "http://10.10.0.254/answers/windows-aula/autounattend.xml")])
        self.assertEqual(answers.winpe_files(deb, ip), [])
        self.assertEqual(answers.kernel_args(None, "debian-installer", ip), "")
        # risposta generica: nessun argomento se il tipo della ISO non dice nulla
        gen = answers.create({"name": "Script extra", "kind": "generic", "content": "ciao\n"})
        self.assertEqual(answers.kernel_args(gen, "unknown", ip), "")
        self.assertEqual(answers.kernel_args(gen, "debian-installer", ip),
                         "auto=true priority=critical url=http://10.10.0.254/answers/script-extra/risposta.txt")

    def test_13_get_for_slug(self):
        from pixio.services import answers, catalog
        cat = catalog.load()
        cat["isos"]["prova-iso"] = {"slug": "prova-iso", "source": "local", "rel_path": "p.iso", "file": "p.iso",
                                    "path": os.path.join(C.LIBRARY_DIR, "p.iso"), "name": "Prova", "enabled": False,
                                    "type": "debian-installer", "answer_id": "aula-1-debian", "detect": {}}
        catalog.save(cat)
        a = answers.get_for_slug("prova-iso")
        self.assertIsNotNone(a)
        self.assertEqual(a["id"], "aula-1-debian")
        self.assertIn("prova-iso", answers.get("aula-1-debian")["used_by"])
        self.assertIsNone(answers.get_for_slug("slug-inventato"))

    # ---------------------------------------------------------------- servizio pubblico
    def test_14_public_download_without_session(self):
        anon = self.app.test_client()          # nessun cookie di sessione: è il client PXE
        self.assertEqual(anon.get("/api/answers").status_code, 401)
        r = anon.get("/answers/aula-1-debian/preseed.cfg")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn(b"d-i debian-installer/locale", r.data)
        self.assertTrue(r.mimetype.startswith("text/plain"))
        self.assertEqual(anon.get("/answers/aula-1-debian/inesistente.cfg").status_code, 404)
        self.assertEqual(anon.get("/answers/mai-esistita/preseed.cfg").status_code, 404)
        self.assertEqual(anon.get("/answers/server-ubuntu/meta-data").status_code, 200)

    def test_15_path_traversal_refused(self):
        from pixio.services import answers
        anon = self.app.test_client()
        for bad in ("/answers/aula-1-debian/..%2f..%2f..%2fetc%2fpasswd",
                    "/answers/..%2f..%2fetc/passwd",
                    "/answers/aula-1-debian/.%2e/.%2e/etc/passwd"):
            r = anon.get(bad)
            self.assertIn(r.status_code, (400, 404), f"{bad} -> {r.status_code}")
            self.assertNotIn(b"root:", r.data)
        r = self.client.delete("/api/answers/aula-1-debian/files/..%2f..%2fpasswd", headers=self.h)
        self.assertIn(r.status_code, (400, 404), r.data)
        with self.assertRaises(ValueError):
            answers.file_path("aula-1-debian", "../preseed.cfg")
        with self.assertRaises(ValueError):
            answers.file_path("../etc", "preseed.cfg")
        with self.assertRaises(ValueError):
            answers.folder_path("../../etc")
        with self.assertRaises(ValueError):
            answers.check_filename("sotto/cartella.cfg")
        # un link simbolico fuori dalla cartella non viene servito
        link = os.path.join(C.ANSWERS_DIR, "aula-1-debian", "fuori.cfg")
        os.symlink("/etc/passwd", link)
        try:
            with self.assertRaises(ValueError):
                answers.file_path("aula-1-debian", "fuori.cfg")
            r = anon.get("/answers/aula-1-debian/fuori.cfg")
            self.assertIn(r.status_code, (400, 404))
            self.assertNotIn(b"root:", r.data)
        finally:
            os.unlink(link)


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
