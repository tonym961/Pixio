"""Test dell'aggiornamento da GUI e del certificato TLS (contratto sezioni 6 e 7).

Esecuzione: cd /opt/pixio && python3 -m unittest tests.test_updater
Non serve l'helper installato: pixio.privileged.call viene sostituito da un finto e l'helper
viene importato come modulo (SourceFileLoader) e provato su cartelle temporanee.
Nessun test tocca l'istanza reale: niente "apply nginx", niente "update", niente systemd.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

os.environ["PIXIO_NO_BACKGROUND"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pixio import config as C  # noqa: E402
from pixio import privileged  # noqa: E402
from pixio.privileged import HelperError  # noqa: E402

TMP = tempfile.mkdtemp(prefix="pixio-test-upd-")
C.ETC_DIR = os.path.join(TMP, "etc")
C.CONFIG_FILE = os.path.join(C.ETC_DIR, "config.json")
C.SECRET_FILE = os.path.join(C.ETC_DIR, "secret")
C.VAR_DIR = os.path.join(TMP, "var")
C.JOBS_DIR = os.path.join(C.VAR_DIR, "jobs")
C.LOG_DIR = os.path.join(TMP, "log")
C.SRV_DIR = os.path.join(TMP, "srv")
C.CODE_DIR = os.path.join(TMP, "code")
for d in (C.ETC_DIR, C.VAR_DIR, C.JOBS_DIR, C.LOG_DIR, C.SRV_DIR, C.CODE_DIR):
    os.makedirs(d, exist_ok=True)

from pixio.services import jobs, updater  # noqa: E402

HELPER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "helper", "pixio-helper")
HAVE_GIT = shutil.which("git") is not None
HAVE_OPENSSL = shutil.which("openssl") is not None

CALLS = []                 # argomenti passati al finto helper
RESPONSES = {}             # comando -> risposta (o lista di risposte consumate in ordine)


def fake_call(*args, stdin_text=None, timeout=180):
    CALLS.append(tuple(str(a) for a in args))
    cmd = str(args[0]) if args else ""
    r = RESPONSES.get(cmd, {"ok": True})
    if isinstance(r, list):
        return r.pop(0) if len(r) > 1 else r[0]
    if isinstance(r, Exception):
        raise r
    return r


privileged.call = fake_call

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="Pixio", GIT_AUTHOR_EMAIL="pixio@example.invalid",
               GIT_COMMITTER_NAME="Pixio", GIT_COMMITTER_EMAIL="pixio@example.invalid",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", LC_ALL="C")


def git(cwd, *args):
    p = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, env=GIT_ENV, timeout=120)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr.strip()}"
    return p.stdout.strip()


def make_repo(path, filename="install.sh", content="#!/bin/sh\n"):
    os.makedirs(path, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    with open(os.path.join(path, filename), "w") as f:
        f.write(content)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "primo commit")
    return path


def capture(fn, *args):
    """Esegue un comando dell'helper catturando il JSON che stampa su stdout."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return json.loads(buf.getvalue().strip().splitlines()[-1])


def load_helper():
    return SourceFileLoader("pixio_helper_test", HELPER_PATH).load_module()


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


# ---------------------------------------------------------------- updater.check()
@unittest.skipUnless(HAVE_GIT, "git non installato")
class CheckTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="pixio-git-", dir=TMP)
        self.origin = make_repo(os.path.join(self.base, "origin"))
        p = subprocess.run(["git", "clone", "-q", self.origin, os.path.join(self.base, "work")],
                           capture_output=True, text=True, env=GIT_ENV, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.work = os.path.join(self.base, "work")
        self._old_code = C.CODE_DIR
        C.CODE_DIR = self.work

    def tearDown(self):
        C.CODE_DIR = self._old_code
        shutil.rmtree(self.base, ignore_errors=True)

    def test_01_aggiornato(self):
        st = updater.check()
        self.assertTrue(st["repo"])
        self.assertEqual(st["error"], "")
        self.assertEqual(st["branch"], "main")
        self.assertEqual(st["behind"], 0)
        self.assertFalse(st["dirty"])
        self.assertFalse(st["can_update"])
        self.assertEqual(st["current"], st["remote"])
        self.assertEqual(len(st["current"]), 7)

    def test_02_indietro_di_un_commit(self):
        with open(os.path.join(self.origin, "nuovo.txt"), "w") as f:
            f.write("x\n")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-q", "-m", "secondo commit")
        st = updater.check()
        self.assertGreaterEqual(st["behind"], 1)
        self.assertTrue(st["can_update"])
        self.assertNotEqual(st["current"], st["remote"])

    def test_03_modifiche_locali(self):
        with open(os.path.join(self.work, "install.sh"), "a") as f:
            f.write("# modificato a mano\n")
        st = updater.check()
        self.assertTrue(st["dirty"])
        self.assertFalse(st["can_update"])

    def test_04_non_e_un_repository(self):
        C.CODE_DIR = os.path.join(self.base, "vuota")
        os.makedirs(C.CODE_DIR, exist_ok=True)
        st = updater.check()
        self.assertFalse(st["repo"])
        self.assertFalse(st["can_update"])
        self.assertIn("repository git", st["error"])

    def test_05_server_non_raggiungibile(self):
        shutil.rmtree(self.origin)
        st = updater.check()
        self.assertNotEqual(st["error"], "")
        self.assertFalse(st["can_update"])
        self.assertEqual(st["current"], st["current_full"][:7])


# ---------------------------------------------------------------- updater.apply()
class ApplyTest(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        RESPONSES.clear()
        self._check = updater.check
        self._poll = updater.POLL
        self._grace = updater.START_GRACE
        updater.POLL = 0.01
        updater.START_GRACE = 0.05
        updater.check = lambda: {"current": "aaaaaaa", "current_full": "a" * 40, "remote": "bbbbbbb",
                                 "remote_full": "b" * 40, "branch": "main", "behind": 2, "dirty": False,
                                 "can_update": True, "repo": True, "checked": "", "error": ""}

    def tearDown(self):
        updater.check = self._check
        updater.POLL = self._poll
        updater.START_GRACE = self._grace

    def test_01_avvia_il_job_e_segue_la_unit(self):
        RESPONSES["update-status"] = [
            {"running": True, "active": "activating", "result": "", "exit_status": "0", "log": []},
            {"running": False, "active": "inactive", "result": "success", "exit_status": "0",
             "log": ["[pixio] Pacchetti", "[pixio] Fatto."]},
        ]
        j = updater.apply()
        jobs.wait(j.id, 30)
        self.assertEqual(j.status, "done", j.message)
        self.assertEqual(j.type, "update")
        self.assertIn(("update",), CALLS)
        self.assertIn(("update-status",), CALLS)
        self.assertTrue(any("Fatto" in l for l in j.logs))
        self.assertEqual(j.progress, 100)

    def test_02_unit_fallita(self):
        RESPONSES["update-status"] = [{"running": False, "active": "failed", "result": "exit-code",
                                       "exit_status": "1", "log": ["errore"]}]
        j = updater.apply()
        jobs.wait(j.id, 30)
        self.assertEqual(j.status, "error")
        self.assertIn("journalctl", j.message)

    def test_03_rifiuta_modifiche_locali_e_nessun_aggiornamento(self):
        updater.check = lambda: {"current": "a", "current_full": "a", "remote": "b", "remote_full": "b",
                                 "branch": "main", "behind": 1, "dirty": True, "can_update": False,
                                 "repo": True, "checked": "", "error": ""}
        with self.assertRaises(ValueError) as e:
            updater.apply()
        self.assertIn("modifiche locali", str(e.exception))
        updater.check = lambda: {"current": "a", "current_full": "a", "remote": "a", "remote_full": "a",
                                 "branch": "main", "behind": 0, "dirty": False, "can_update": False,
                                 "repo": True, "checked": "", "error": ""}
        with self.assertRaises(ValueError) as e:
            updater.apply()
        self.assertIn("Nessun aggiornamento", str(e.exception))
        self.assertNotIn(("update",), CALLS)

    def test_04_force_passa_sopra_alle_modifiche_locali(self):
        RESPONSES["update-status"] = [{"running": False, "active": "inactive", "result": "success",
                                       "exit_status": "0", "log": []}]
        updater.check = lambda: {"current": "a", "current_full": "a", "remote": "a", "remote_full": "a",
                                 "branch": "main", "behind": 0, "dirty": True, "can_update": False,
                                 "repo": True, "checked": "", "error": ""}
        j = updater.apply(force=True)
        jobs.wait(j.id, 30)
        self.assertEqual(j.status, "done", j.message)
        self.assertIn(("update", "--force"), CALLS)


# ---------------------------------------------------------------- certificato (servizio)
class CertServiceTest(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        RESPONSES.clear()
        with open(C.CONFIG_FILE, "w") as f:
            json.dump({"network": {"interface": "lo", "server_ip": "10.10.0.254"},
                       "web": {"https_enabled": True, "redirect_http": True},
                       "auth": {"password_hash": "x"}}, f)

    def test_01_cert_info(self):
        RESPONSES["cert-info"] = {"ok": True, "exists": True, "subject": "CN=pixio",
                                  "not_after": "2036-09-05T21:25:38+00:00", "days_left": 3649,
                                  "fingerprint": "AA:BB", "cert": "/etc/pixio/tls/cert.pem"}
        info = updater.cert_info()
        self.assertEqual(CALLS, [("cert-info",)])
        self.assertTrue(info["enabled"])
        self.assertTrue(info["exists"])
        self.assertEqual(info["subject"], "CN=pixio")
        self.assertEqual(info["fingerprint"], "AA:BB")
        self.assertEqual(info["not_after"], "2036-09-05T21:25:38+00:00")

    def test_02_helper_non_disponibile(self):
        RESPONSES["cert-info"] = HelperError("helper non installato (eseguire install.sh)")
        info = updater.cert_info()
        self.assertFalse(info["exists"])
        self.assertIn("helper", info["error"])

    def test_03_regenerate(self):
        RESPONSES["cert-info"] = {"ok": True, "exists": True, "subject": "CN=pixio.lan",
                                  "not_after": None, "fingerprint": "CC", "cert": "/etc/pixio/tls/cert.pem"}
        updater.regenerate()
        self.assertIn(("cert", "--force"), CALLS)
        updater.regenerate("pixio.lan")
        self.assertIn(("cert", "pixio.lan", "--force"), CALLS)
        with self.assertRaises(ValueError):
            updater.regenerate("host non valido!")


# ---------------------------------------------------------------- API
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            os.unlink(C.CONFIG_FILE)
        except FileNotFoundError:
            pass
        from pixio import create_app
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        from pixio.blueprints import api_update
        if "api_update" not in cls.app.blueprints:      # non è (ancora) in blueprints/MODULES
            cls.app.register_blueprint(api_update.bp)
        cls.client = cls.app.test_client()
        r = cls.client.post("/api/auth/login", json={"password": "segreta1"})
        assert r.status_code == 200, r.get_json()
        cls.h = {"X-CSRF-Token": r.get_json()["csrf"]}

    def setUp(self):
        CALLS.clear()
        RESPONSES.clear()
        with open(C.CONFIG_FILE, "w") as f:
            json.dump({"network": {"interface": "lo", "server_ip": "10.10.0.254"},
                       "web": {"https_enabled": True, "redirect_http": False},
                       "auth": {"password_hash": "x"}}, f)
        self._check = updater.check
        self._poll = updater.POLL
        self._grace = updater.START_GRACE
        updater.POLL = 0.01
        updater.START_GRACE = 0.05

    def tearDown(self):
        updater.check = self._check
        updater.POLL = self._poll
        updater.START_GRACE = self._grace

    def test_01_check(self):
        updater.check = lambda: {"current": "aaaaaaa", "remote": "bbbbbbb", "behind": 3, "dirty": False,
                                 "can_update": True, "branch": "main", "repo": True, "error": ""}
        r = self.client.get("/api/update/check")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        for k in ("current", "remote", "behind", "dirty", "can_update"):
            self.assertIn(k, d)
        self.assertEqual(d["behind"], 3)
        self.assertTrue(d["can_update"])

    def test_02_apply(self):
        RESPONSES["update-status"] = [{"running": False, "active": "inactive", "result": "success",
                                       "exit_status": "0", "log": []}]
        updater.check = lambda: {"current": "aaaaaaa", "current_full": "a" * 40, "remote": "bbbbbbb",
                                 "remote_full": "b" * 40, "branch": "main", "behind": 1, "dirty": False,
                                 "can_update": True, "repo": True, "checked": "", "error": ""}
        r = self.client.post("/api/update/apply", json={}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        jid = r.get_json()["job_id"]
        self.assertTrue(r.get_json()["ok"])
        jobs.wait(jid, 30)
        self.assertEqual(jobs.get_job(jid).status, "done")

    def test_03_apply_rifiutata(self):
        updater.check = lambda: {"current": "a", "current_full": "a", "remote": "a", "remote_full": "a",
                                 "branch": "main", "behind": 0, "dirty": False, "can_update": False,
                                 "repo": True, "checked": "", "error": ""}
        r = self.client.post("/api/update/apply", json={}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Nessun aggiornamento", r.get_json()["error"])

    def test_04_cert(self):
        RESPONSES["cert-info"] = {"ok": True, "exists": True, "subject": "CN=pixio", "not_after": "2036-01-01T00:00:00+00:00",
                                  "days_left": 3400, "fingerprint": "AA:BB", "cert": "/etc/pixio/tls/cert.pem"}
        d = self.client.get("/api/system/cert").get_json()
        self.assertTrue(d["enabled"])
        self.assertTrue(d["exists"])
        self.assertEqual(d["subject"], "CN=pixio")
        self.assertIn("not_after", d)
        self.assertIn("fingerprint", d)

    def test_05_cert_regenerate(self):
        RESPONSES["cert-info"] = {"ok": True, "exists": True, "subject": "CN=pixio.lan", "not_after": None,
                                  "fingerprint": "CC", "cert": "/etc/pixio/tls/cert.pem"}
        r = self.client.post("/api/system/cert/regenerate", json={"hostname": "pixio.lan"}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()["ok"])
        self.assertIn(("cert", "pixio.lan", "--force"), CALLS)
        r = self.client.post("/api/system/cert/regenerate", json={"hostname": "no buono!"}, headers=self.h)
        self.assertEqual(r.status_code, 400)

    def test_06_login_e_csrf(self):
        anon = self.app.test_client()
        self.assertEqual(anon.get("/api/update/check").status_code, 401)
        self.assertEqual(anon.get("/api/system/cert").status_code, 401)
        self.assertEqual(self.client.post("/api/update/apply", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/system/cert/regenerate", json={}).status_code, 403)


# ---------------------------------------------------------------- helper: configurazione nginx
class HelperNginxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = load_helper()
        cls.tmp = tempfile.mkdtemp(prefix="pixio-nginx-", dir=TMP)
        cls.h.TLS_DIR = os.path.join(cls.tmp, "tls")
        cls.h.TLS_KEY = os.path.join(cls.h.TLS_DIR, "key.pem")
        cls.h.TLS_CERT = os.path.join(cls.h.TLS_DIR, "cert.pem")

    def _render(self, https, redirect):
        cfg = {"network": {"interface": "lo", "server_ip": "10.10.0.254"},
               "web": {"https_enabled": https, "redirect_http": redirect}}
        return self.h.render_nginx(cfg)

    def test_01_senza_https(self):
        c = self._render(False, True)
        self.assertIn("listen 80 default_server;", c)
        self.assertNotIn("listen 443", c)
        self.assertNotIn("return 308", c)
        self.assertIn("location /pxe/", c)
        self.assertIn("proxy_pass http://127.0.0.1:8080;", c)

    def test_02_con_https_e_redirect(self):
        c = self._render(True, True)
        self.assertIn("listen 443 ssl default_server;", c)
        self.assertIn(f"ssl_certificate {self.h.TLS_CERT};", c)
        self.assertIn(f"ssl_certificate_key {self.h.TLS_KEY};", c)
        self.assertEqual(c.count("listen 80 default_server;"), 1)
        self.assertIn("return 308 https://$host$request_uri;", c)
        # i percorsi dei client PXE restano in HTTP anche con il reindirizzamento
        http80 = c.split("listen 443")[0]
        for path in ("location = /boot.ipxe", "location /boot/", "location /answers/", "location /pxe/"):
            self.assertIn(path, http80)
        self.assertEqual(c.count("location /pxe/"), 2)          # una per server

    def test_03_con_https_senza_redirect(self):
        c = self._render(True, False)
        self.assertIn("listen 443 ssl default_server;", c)
        self.assertNotIn("return 308", c)
        self.assertEqual(c.count("proxy_pass http://127.0.0.1:8080;"), 2)

    def test_04_modello_incompleto(self):
        bad = os.path.join(self.tmp, "modello.tpl")
        with open(bad, "w") as f:
            f.write("#@ base\n@HTTP@\n@HTTPS@\n")
        old, self.h.NGINX_TPL = self.h.NGINX_TPL, bad
        try:
            with self.assertRaises(self.h.HelperError):
                self._render(False, True)
            self.h.NGINX_TPL = os.path.join(self.tmp, "manca.tpl")
            with self.assertRaises(self.h.HelperError):
                self._render(False, True)
        finally:
            self.h.NGINX_TPL = old

    @unittest.skipUnless(shutil.which("nginx"), "nginx non installato")
    def test_05_nginx_t_accetta_la_configurazione(self):
        if not HAVE_OPENSSL:
            self.skipTest("openssl non installato")
        d = tempfile.mkdtemp(prefix="nginx-t-", dir=self.tmp)
        cfgfile = os.path.join(d, "config.json")
        with open(cfgfile, "w") as f:
            json.dump({"network": {"interface": "lo", "server_ip": "10.10.0.254"}}, f)
        old_cfg, self.h.CONFIG_FILE = self.h.CONFIG_FILE, cfgfile
        try:
            self.h.generate_cert("pixio.test", force=True)
        finally:
            self.h.CONFIG_FILE = old_cfg
        for https, redirect in ((False, True), (True, True), (True, False)):
            # i log finiscono nella cartella temporanea: "nginx -t" non deve toccare quelli veri
            conf = self._render(https, redirect).replace("/var/log/nginx/", d + "/")
            with open(os.path.join(d, "pixio.conf"), "w") as f:
                f.write(conf)
            with open(os.path.join(d, "nginx.conf"), "w") as f:
                f.write(f"pid {d}/nginx.pid;\nerror_log {d}/main-error.log;\nevents {{}}\n"
                        f"http {{\n  include {d}/pixio.conf;\n}}\n")
            p = subprocess.run(["nginx", "-t", "-p", d, "-c", os.path.join(d, "nginx.conf")],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0, f"https={https} redirect={redirect}: {p.stderr}")


# ---------------------------------------------------------------- helper: certificato
@unittest.skipUnless(HAVE_OPENSSL, "openssl non installato")
class HelperCertTest(unittest.TestCase):
    def setUp(self):
        self.h = load_helper()
        self.tmp = tempfile.mkdtemp(prefix="pixio-cert-", dir=TMP)
        self.h.TLS_DIR = os.path.join(self.tmp, "tls")
        self.h.TLS_KEY = os.path.join(self.h.TLS_DIR, "key.pem")
        self.h.TLS_CERT = os.path.join(self.h.TLS_DIR, "cert.pem")
        self.h.CONFIG_FILE = os.path.join(self.tmp, "config.json")
        with open(self.h.CONFIG_FILE, "w") as f:
            json.dump({"network": {"interface": "lo", "server_ip": "10.10.0.254"},
                       "web": {"https_enabled": True, "redirect_http": True}}, f)

    def _args(self, hostname="", force=False):
        class A:
            pass
        A.hostname, A.force = hostname, force
        return A

    def test_01_genera_certificato_e_permessi(self):
        if os.geteuid() != 0:
            self.skipTest("servono i privilegi di root per chown")
        d = capture(self.h.cmd_cert, self._args("pixio.test"))
        self.assertTrue(d["ok"])
        self.assertTrue(d["created"])
        self.assertTrue(d["exists"])
        self.assertEqual(d["cert"], self.h.TLS_CERT)
        self.assertIn("pixio.test", d["subject"])
        self.assertTrue(d["not_after"].startswith("20"))
        self.assertGreater(d["days_left"], 3000)
        self.assertRegex(d["fingerprint"], r"^[0-9A-F]{2}(:[0-9A-F]{2})+$")
        self.assertEqual(os.stat(self.h.TLS_KEY).st_mode & 0o777, 0o640)
        self.assertEqual(os.stat(self.h.TLS_CERT).st_mode & 0o777, 0o644)
        self.assertEqual(os.stat(self.h.TLS_KEY).st_uid, 0)
        txt = subprocess.run(["openssl", "x509", "-noout", "-text", "-in", self.h.TLS_CERT],
                             capture_output=True, text=True, timeout=60).stdout
        self.assertIn("DNS:pixio.test", txt)
        self.assertIn("IP Address:10.10.0.254", txt)
        # seconda chiamata: non rigenera nulla
        again = capture(self.h.cmd_cert, self._args("pixio.test"))
        self.assertFalse(again["created"])
        self.assertEqual(again["fingerprint"], d["fingerprint"])
        # con force cambia il certificato
        forced = capture(self.h.cmd_cert, self._args("pixio.test", force=True))
        self.assertTrue(forced["created"])
        self.assertNotEqual(forced["fingerprint"], d["fingerprint"])

    def test_02_cert_info_senza_certificato(self):
        d = capture(self.h.cmd_cert_info, self._args())
        self.assertFalse(d["exists"])
        self.assertEqual(d["subject"], "")
        self.assertIsNone(d["not_after"])

    def test_03_argomenti_non_validi(self):
        for bad in ("cattivo;host", "-flag", "host con spazi", "a" * 300, "http://pixio", "pi/xio"):
            with self.assertRaises(self.h.HelperError, msg=bad):
                self.h.cmd_cert(self._args(bad))
        self.assertFalse(os.path.exists(self.h.TLS_CERT))

    def test_04_server_ip_non_valido(self):
        with open(self.h.CONFIG_FILE, "w") as f:
            json.dump({"network": {"interface": "lo", "server_ip": "10.10.0.999"}}, f)
        with self.assertRaises(self.h.HelperError):
            self.h.cmd_cert(self._args("pixio.test"))


# ---------------------------------------------------------------- helper: update
@unittest.skipUnless(HAVE_GIT, "git non installato")
class HelperUpdateTest(unittest.TestCase):
    def setUp(self):
        self.h = load_helper()
        self.tmp = tempfile.mkdtemp(prefix="pixio-upd-", dir=TMP)
        self.h.CODE = make_repo(os.path.join(self.tmp, "code"))
        self.h.UPDATE_UNIT = "pixio-update-test-inesistente"

    def _args(self, force=False, lines="40"):
        class A:
            pass
        A.force, A.lines = force, lines
        return A

    def test_01_rifiuta_modifiche_locali(self):
        with open(os.path.join(self.h.CODE, "install.sh"), "a") as f:
            f.write("# modificato\n")
        with self.assertRaises(self.h.HelperError) as e:
            self.h.cmd_update(self._args())
        self.assertIn("modifiche locali", str(e.exception))

    def test_02_rifiuta_fuori_da_un_repository(self):
        self.h.CODE = os.path.join(self.tmp, "senza-git")
        os.makedirs(self.h.CODE, exist_ok=True)
        with self.assertRaises(self.h.HelperError) as e:
            self.h.cmd_update(self._args())
        self.assertIn("repository git", str(e.exception))

    def test_03_rifiuta_senza_install_sh(self):
        os.unlink(os.path.join(self.h.CODE, "install.sh"))
        git(self.h.CODE, "add", "-A")
        git(self.h.CODE, "commit", "-q", "-m", "senza install.sh")
        with self.assertRaises(self.h.HelperError) as e:
            self.h.cmd_update(self._args())
        self.assertIn("install.sh", str(e.exception))

    def test_04_update_status_righe_non_valide(self):
        for bad in ("zero", "0", "-5", "5000", None):
            with self.assertRaises(self.h.HelperError, msg=str(bad)):
                self.h.cmd_update_status(self._args(lines=bad))


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
