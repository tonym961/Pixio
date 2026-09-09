"""Test della copia locale automatica (services/autocache.py + blueprints/api_cache.py).

Esecuzione: cd /opt/pixio && python3 -m unittest tests.test_autocache
I percorsi di config vengono reindirizzati in una directory temporanea in setUpClass e ripristinati
alla fine; pixio.privileged.call è sostituito da un finto (nessun mount reale).
Per lavorare con file piccoli la costante autocache.GB vale 1 MiB durante i test: le soglie
"GB" delle impostazioni diventano quindi megabyte.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ["PIXIO_NO_BACKGROUND"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pixio import config as C  # noqa: E402
from pixio import privileged  # noqa: E402

OVERRIDE_KEYS = ("ETC_DIR", "CONFIG_FILE", "SECRET_FILE", "SOURCES_DIR", "VAR_DIR", "JOBS_DIR", "UPLOAD_TMP_DIR",
                 "CLIENTS_FILE", "CATALOG_FILE", "DRIVERS_FILE", "ANSWERS_DIR", "ANSWERS_FILE", "LOG_DIR",
                 "SRV_DIR", "LIBRARY_DIR", "CACHE_DIR", "TFTP_DIR", "HTTP_DIR", "HTTP_ISO_DIR", "HTTP_INJECT_DIR",
                 "SOURCES_MOUNT_DIR")
MB = 1 << 20
FREE = [500 * MB]          # capacità simulata: lo spazio libero è questo meno i file in CACHE_DIR


def _fake_free():
    """Spazio libero finto ma coerente: liberare una copia fa davvero salire lo spazio."""
    used = 0
    for fn in os.listdir(C.CACHE_DIR):
        try:
            used += os.path.getsize(os.path.join(C.CACHE_DIR, fn))
        except OSError:
            pass
    return max(0, FREE[0] - used)


def _fake_call(*args, stdin_text=None, timeout=180):
    if args and args[0] == "list-mounts":
        return {"iso": {}, "sources": {}}
    return {"ok": True}


def _iso(slug, source="srv", size=8 * MB, enabled=True, cache=None, path=None, missing=False, name=None):
    return {"slug": slug, "source": source, "source_name": "Sorgente" if source != "local" else "Locale",
            "rel_path": f"{slug}.iso", "file": f"{slug}.iso", "path": path or "", "name": name or slug,
            "enabled": enabled, "group": "Linux", "order": 1, "custom_recipe": None,
            "cache_wanted": bool(cache), "cache": cache or {"status": "none", "path": "", "progress": 0},
            "first_seen": "2026-09-01T10:00:00", "last_seen": "2026-09-08T10:00:00", "size": size,
            "mtime": 1, "type": "alpine", "detect": {}, "missing": missing}


class AutocacheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # /var/tmp e non /tmp: catalog._copy_job pretende 1 GB libero oltre alla dimensione della ISO
        root = "/var/tmp" if os.path.isdir("/var/tmp") else None
        cls.tmp = tempfile.mkdtemp(prefix="pixio-cache-test-", dir=root)
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
        C.HTTP_ISO_DIR = os.path.join(C.HTTP_DIR, "iso")
        C.HTTP_INJECT_DIR = os.path.join(C.HTTP_DIR, "inject")
        C.SOURCES_MOUNT_DIR = os.path.join(C.SRV_DIR, "sources")
        for d in (C.ETC_DIR, C.VAR_DIR, C.JOBS_DIR, C.LOG_DIR, C.LIBRARY_DIR, C.CACHE_DIR, C.TFTP_DIR,
                  C.HTTP_ISO_DIR, C.SOURCES_MOUNT_DIR, os.path.join(C.SOURCES_MOUNT_DIR, "srv")):
            os.makedirs(d, exist_ok=True)
        cls._orig_call = privileged.call
        privileged.call = _fake_call
        from pixio.services import autocache
        cls.autocache = autocache
        cls._orig_gb = autocache.GB
        autocache.GB = MB                      # nei test "GB" = MiB: file piccoli, stessa aritmetica
        cls._orig_free = autocache.free_bytes
        autocache.free_bytes = _fake_free
        from pixio import create_app
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        from pixio.blueprints import api_cache
        if "api_cache" not in cls.app.blueprints:      # non è (ancora) in blueprints/MODULES
            cls.app.register_blueprint(api_cache.bp)
        cls.client = cls.app.test_client()
        r = cls.client.post("/api/auth/login", json={"password": "segreta1"})
        assert r.status_code == 200, r.get_json()
        cls.h = {"X-CSRF-Token": r.get_json()["csrf"]}

    @classmethod
    def tearDownClass(cls):
        cls.autocache.GB = cls._orig_gb
        cls.autocache.free_bytes = cls._orig_free
        privileged.call = cls._orig_call
        for k, v in cls.saved.items():
            setattr(C, k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- helper
    def setUp(self):
        FREE[0] = 500 * MB
        shutil.rmtree(C.CACHE_DIR, ignore_errors=True)
        os.makedirs(C.CACHE_DIR, exist_ok=True)
        self._write_config({"auto": False, "min_size_gb": 2, "only_enabled": True, "keep_free_gb": 20})

    def _write_config(self, cache):
        cfg = {"network": {"interface": "lo", "server_ip": "10.10.0.254"}, "cache": cache,
               "auth": {"password_hash": "x"}}
        with open(C.CONFIG_FILE, "w") as f:
            json.dump(cfg, f)

    def _source_file(self, slug, size):
        p = os.path.join(C.SOURCES_MOUNT_DIR, "srv", f"{slug}.iso")
        with open(p, "wb") as f:
            f.write(b"\0" * size)
        return p

    def _cache_file(self, slug, size, age_days=0):
        os.makedirs(C.CACHE_DIR, exist_ok=True)
        p = os.path.join(C.CACHE_DIR, f"{slug}.iso")
        with open(p, "wb") as f:
            f.write(b"\0" * size)
        if age_days:
            old = time.time() - age_days * 86400
            os.utime(p, (old, old))
        return p

    def _write_catalog(self, isos):
        with open(C.CATALOG_FILE, "w") as f:
            json.dump({"isos": {e["slug"]: e for e in isos}, "last_scan": "2026-09-08T10:00:00"}, f)

    def _write_clients(self, clients):
        with open(C.CLIENTS_FILE, "w") as f:
            json.dump(clients, f)

    def _standard_catalog(self):
        """Un catalogo con tutti i casi: locale, piccola, spenta, persa, già copiata, da copiare."""
        self._write_clients({})
        big = self._source_file("remota-grande", 8 * MB)
        small = self._source_file("remota-piccola", 1 * MB)
        off = self._source_file("remota-spenta", 9 * MB)
        cached = self._cache_file("gia-copiata", 5 * MB)
        lib = os.path.join(C.LIBRARY_DIR, "locale-grande.iso")
        with open(lib, "wb") as f:
            f.write(b"\0" * (10 * MB))
        self._write_catalog([
            _iso("locale-grande", source="local", size=10 * MB, path=lib),
            _iso("remota-grande", size=8 * MB, path=big),
            _iso("remota-piccola", size=1 * MB, path=small),
            _iso("remota-spenta", size=9 * MB, enabled=False, path=off),
            _iso("remota-persa", size=9 * MB, path="/non/esiste.iso", missing=True),
            _iso("gia-copiata", size=5 * MB, path=self._source_file("gia-copiata", 5 * MB),
                 cache={"status": "ready", "path": cached, "progress": 100}),
        ])

    # ---------------------------------------------------------------- impostazioni
    def test_01_settings_api(self):
        r = self.client.get("/api/cache")
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(set(d), {"settings", "stats", "plan"})
        self.assertEqual(d["settings"], {"auto": False, "min_size_gb": 2, "only_enabled": True, "keep_free_gb": 20})
        self.assertEqual(set(d["stats"]), {"cached", "cached_bytes", "free_bytes", "candidates"})
        for k in ("to_copy", "to_free", "free_gb", "reason"):
            self.assertIn(k, d["plan"])
        r = self.client.post("/api/cache", json={"auto": True, "min_size_gb": 4.5, "keep_free_gb": 30},
                             headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        s = r.get_json()["settings"]
        self.assertEqual((s["auto"], s["min_size_gb"], s["keep_free_gb"], s["only_enabled"]), (True, 4.5, 30, True))
        with open(C.CONFIG_FILE) as f:
            self.assertEqual(json.load(f)["cache"]["min_size_gb"], 4.5)

    def test_02_settings_errors(self):
        for payload, frag in (({"min_size_gb": 500}, "intervallo"), ({"keep_free_gb": 0}, "intervallo"),
                              ({"auto": "forse"}, "booleano"), ({}, "Nessuna impostazione")):
            r = self.client.post("/api/cache", json=payload, headers=self.h)
            self.assertEqual(r.status_code, 400, (payload, r.get_json()))
            self.assertIn(frag, r.get_json()["error"])

    # ---------------------------------------------------------------- piano
    def test_03_plan_candidates(self):
        self._standard_catalog()
        p = self.autocache.plan()
        self.assertEqual(p["to_copy"], ["remota-grande"])
        self.assertEqual(p["to_free"], [])
        self.assertEqual(p["candidates"], 1)
        self.assertEqual(p["free_gb"], 495.0)      # 500 meno i 5 MB già in cache
        self.assertIn("1 ISO da copiare", p["reason"])
        st = self.autocache.stats()
        self.assertEqual((st["cached"], st["candidates"]), (1, 1))
        self.assertEqual(st["cached_bytes"], 5 * MB)

    def test_04_plan_only_enabled_off(self):
        self._standard_catalog()
        self._write_config({"auto": True, "min_size_gb": 2, "only_enabled": False, "keep_free_gb": 20})
        p = self.autocache.plan()
        self.assertEqual(sorted(p["to_copy"]), ["remota-grande", "remota-spenta"])

    def test_05_plan_min_size(self):
        self._standard_catalog()
        self._write_config({"auto": True, "min_size_gb": 0.5, "only_enabled": True, "keep_free_gb": 20})
        p = self.autocache.plan()
        self.assertIn("remota-piccola", p["to_copy"])

    def test_06_plan_frees_least_recently_used(self):
        """Spazio sotto soglia: si eliminano prima le copie con l'ultimo utilizzo più vecchio."""
        recente = self._cache_file("recente", 5 * MB, age_days=90)
        vecchia = self._cache_file("vecchia", 6 * MB, age_days=30)
        antica = self._cache_file("antica", 7 * MB, age_days=200)
        self._write_catalog([
            _iso("recente", size=5 * MB, cache={"status": "ready", "path": recente, "progress": 100}),
            _iso("vecchia", size=6 * MB, cache={"status": "ready", "path": vecchia, "progress": 100}),
            _iso("antica", size=7 * MB, cache={"status": "ready", "path": antica, "progress": 100}),
        ])
        # "recente" è stata avviata da un client poco fa: l'ultimo utilizzo batte la data del file
        self._write_clients({"aa:bb:cc:dd:ee:ff": {"mac": "aa:bb:cc:dd:ee:ff", "last_entry": "recente",
                                                   "last_seen": "2099-01-01T10:00:00", "count": 3}})
        FREE[0] = 28 * MB                     # 18 MB in cache -> 10 liberi: ne servono 20
        p = self.autocache.plan()
        self.assertEqual(p["to_free"], ["antica", "vecchia"])
        self.assertEqual(p["to_copy"], [])
        self.assertIn("copie da eliminare", p["reason"])

    def test_07_plan_frees_to_make_room(self):
        vecchia = self._cache_file("vecchia2", 10 * MB, age_days=200)
        nuova = self._source_file("nuova", 8 * MB)
        self._write_clients({})
        self._write_catalog([
            _iso("vecchia2", size=10 * MB, cache={"status": "ready", "path": vecchia, "progress": 100}),
            _iso("nuova", size=8 * MB, path=nuova),
        ])
        FREE[0] = 35 * MB                     # 10 MB in cache -> 25 liberi: 5 utili, non bastano per 8 MB
        p = self.autocache.plan()
        self.assertEqual(p["to_free"], ["vecchia2"])
        self.assertEqual(p["to_copy"], ["nuova"])

    def test_08_plan_skips_when_hopeless(self):
        nuova = self._source_file("enorme", 8 * MB)
        self._write_clients({})
        self._write_catalog([_iso("enorme", size=8 * MB, path=nuova)])
        FREE[0] = 21 * MB
        p = self.autocache.plan()
        self.assertEqual((p["to_copy"], p["to_free"], p["skipped"]), ([], [], 1))
        self.assertIn("spazio insufficiente", p["reason"])

    # ---------------------------------------------------------------- esecuzione
    def test_09_run_copies_and_frees(self):
        vecchia = self._cache_file("vecchia3", 6 * MB, age_days=200)
        nuova = self._source_file("da-copiare", 4 * MB)
        self._write_clients({})
        self._write_catalog([
            _iso("vecchia3", size=6 * MB, path=self._source_file("vecchia3", 6 * MB),
                 cache={"status": "ready", "path": vecchia, "progress": 100}),
            _iso("da-copiare", size=4 * MB, path=nuova),
        ])
        FREE[0] = 28 * MB                     # 6 MB in cache -> 22 liberi: prima si libera la copia vecchia
        res = self.autocache.run()
        self.assertEqual(res["copied"], ["da-copiare"])
        self.assertEqual(res["freed"], ["vecchia3"])
        self.assertEqual(res["errors"], {})
        self.assertFalse(os.path.exists(vecchia))
        from pixio.services import catalog
        e = catalog.get("da-copiare")
        self.assertEqual(e["cache"]["status"], "ready")
        self.assertTrue(os.path.isfile(e["cache"]["path"]))
        self.assertEqual(os.path.getsize(e["cache"]["path"]), 4 * MB)
        self.assertEqual(catalog.get("vecchia3")["cache"]["status"], "none")

    def test_10_run_survives_catalog_changes(self):
        """Il catalogo cambia mentre il job gira: le voci sparite o già a posto vengono saltate."""
        from pixio.services import catalog
        nuova = self._source_file("presente", 4 * MB)
        self._write_clients({})
        self._write_catalog([_iso("presente", size=4 * MB, path=nuova)])
        piano = {"to_copy": ["sparita", "presente"], "to_free": ["mai-esistita"], "free_gb": 100.0,
                 "reason": "prova", "candidates": 2, "skipped": 0}
        orig_plan, orig_set = self.autocache.plan, catalog.set_cache
        calls = []

        def fake_set_cache(slug, wanted):
            calls.append((slug, wanted))
            if slug == "presente":            # l'utente elimina la ISO proprio adesso
                cat = catalog.load()
                cat["isos"].pop("presente", None)
                catalog.save(cat)
                return None
            return orig_set(slug, wanted)
        self.autocache.plan = lambda: dict(piano)
        catalog.set_cache = fake_set_cache
        try:
            res = self.autocache.run()
        finally:
            self.autocache.plan = orig_plan
            catalog.set_cache = orig_set
        self.assertEqual(res["errors"], {})
        self.assertEqual(res["copied"], [])
        self.assertIn("sparita", res["skipped"])
        self.assertIn("mai-esistita", res["skipped"])
        self.assertEqual(calls, [("presente", True)])

    def test_11_run_via_api_job(self):
        from pixio.services import jobs
        nuova = self._source_file("via-api", 3 * MB)
        self._write_clients({})
        self._write_catalog([_iso("via-api", size=3 * MB, path=nuova)])
        r = self.client.post("/api/cache/run", headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        jid = r.get_json()["job_id"]
        j = jobs.wait(jid, 60)
        self.assertEqual(j.status, "done", j.message)
        self.assertEqual(j.progress, 100)
        from pixio.services import catalog
        self.assertEqual(catalog.get("via-api")["cache"]["status"], "ready")

    # ---------------------------------------------------------------- pulizia
    def test_12_clear_one_and_all(self):
        a = self._cache_file("copia-a", 2 * MB)
        b = self._cache_file("copia-b", 3 * MB)
        orfano = os.path.join(C.CACHE_DIR, "orfano.iso")
        with open(orfano, "wb") as f:
            f.write(b"\0" * MB)
        self._write_clients({})
        self._write_catalog([
            _iso("copia-a", size=2 * MB, path=self._source_file("copia-a", 2 * MB),
                 cache={"status": "ready", "path": a, "progress": 100}),
            _iso("copia-b", size=3 * MB, path=self._source_file("copia-b", 3 * MB),
                 cache={"status": "ready", "path": b, "progress": 100}),
        ])
        r = self.client.post("/api/cache/clear", json={"slug": "copia-a"}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["freed"], ["copia-a"])
        self.assertEqual(r.get_json()["bytes"], 2 * MB)
        self.assertFalse(os.path.exists(a))
        self.assertTrue(os.path.exists(b))
        r = self.client.post("/api/cache/clear", json={}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["freed"], ["copia-b"])
        self.assertFalse(os.path.exists(b))
        self.assertFalse(os.path.exists(orfano))       # anche i file rimasti orfani in cache
        r = self.client.post("/api/cache/clear", json={"slug": "non-esiste"}, headers=self.h)
        self.assertEqual(r.status_code, 404, r.get_json())
        r = self.client.post("/api/cache/clear", json={"slug": "NON valido!"}, headers=self.h)
        self.assertEqual(r.status_code, 400, r.get_json())

    def test_13_clear_cancels_running_copy(self):
        """Se c'è una copia in corso su quella ISO, la pulizia annulla prima il job."""
        from pixio.services import jobs
        a = self._cache_file("in-corso", MB)
        self._write_clients({})
        self._write_catalog([_iso("in-corso", size=4 * MB, enabled=False,
                                  path=self._source_file("in-corso", 4 * MB),
                                  cache={"status": "copying", "path": a, "progress": 40})])

        def lenta(job):
            for _ in range(200):
                if job.cancelled:
                    return
                time.sleep(0.02)
        j = jobs.start("copy", "in-corso", lenta, message="copia finta")
        res = self.autocache.clear("in-corso")
        self.assertEqual(res["freed"], ["in-corso"])
        self.assertEqual(jobs.get_job(j.id).status, "cancelled")
        self.assertFalse(os.path.exists(a))

    def test_14_requires_login_and_csrf(self):
        anon = self.app.test_client()
        self.assertEqual(anon.get("/api/cache").status_code, 401)
        r = self.client.post("/api/cache/run")          # senza header CSRF
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


class CopieInterrotte(unittest.TestCase):
    """Un riavvio del servizio uccide il thread della copia: senza recupero quella ISO
    resterebbe "copying" per sempre e non verrebbe mai piu' riprovata."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_riporta_a_zero_e_butta_il_parziale(self):
        from pixio.services import autocache
        cache_dir = os.path.join(self.tmp, "cache")
        os.makedirs(cache_dir)
        parziale = os.path.join(cache_dir, "iso-a-meta.iso.part")
        open(parziale, "wb").write(b"x" * 10)
        cat = {"isos": {
            "iso-a-meta": {"slug": "iso-a-meta", "cache": {"status": "copying",
                                                           "path": os.path.join(cache_dir, "iso-a-meta.iso"),
                                                           "progress": 42}},
            "iso-finita": {"slug": "iso-finita", "cache": {"status": "ready", "path": "/x.iso", "progress": 100}},
        }}
        stati = {}
        with mock.patch.object(autocache.catalog, "load", return_value=cat), \
             mock.patch.object(autocache.catalog, "_set_cache_state",
                               side_effect=lambda s, st, p, pr, error="": stati.setdefault(s, st)), \
             mock.patch.object(autocache.C, "CACHE_DIR", cache_dir):
            rimessi = autocache.reset_interrupted()
        self.assertEqual(rimessi, ["iso-a-meta"])
        self.assertEqual(stati, {"iso-a-meta": "none"})
        self.assertFalse(os.path.exists(parziale), "il file parziale va buttato: non e' riutilizzabile")

    def test_senza_copie_interrotte_non_tocca_niente(self):
        from pixio.services import autocache
        cat = {"isos": {"a": {"slug": "a", "cache": {"status": "ready", "path": "/x.iso", "progress": 100}}}}
        with mock.patch.object(autocache.catalog, "load", return_value=cat), \
             mock.patch.object(autocache.catalog, "_set_cache_state") as sets:
            self.assertEqual(autocache.reset_interrupted(), [])
        sets.assert_not_called()
