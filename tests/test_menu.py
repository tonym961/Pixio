"""Test del menu di boot iPXE: sottomenu per gruppo (auto/always/never), sintassi dello script e API /api/menu.

Esecuzione: cd /opt/pixio && PIXIO_NO_BACKGROUND=1 python3 -m unittest tests.test_menu
Catalogo finto su file, mount simulati (catalog._mounted_slugs sostituito) e helper privilegiato finto:
i percorsi di config vengono reindirizzati in una directory temporanea e ripristinati alla fine.
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

os.environ["PIXIO_NO_BACKGROUND"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pixio import config as C  # noqa: E402
from pixio import privileged  # noqa: E402

OVERRIDE_KEYS = ("ETC_DIR", "CONFIG_FILE", "SECRET_FILE", "SOURCES_DIR", "VAR_DIR", "JOBS_DIR", "UPLOAD_TMP_DIR",
                 "CLIENTS_FILE", "CATALOG_FILE", "DRIVERS_FILE", "LOG_DIR", "SRV_DIR", "LIBRARY_DIR", "CACHE_DIR",
                 "TFTP_DIR", "HTTP_DIR", "HTTP_ISO_DIR", "HTTP_INJECT_DIR", "DRIVERS_DIR", "SOURCES_MOUNT_DIR")

# gruppo -> nomi delle ISO finte (tutte di tipo avviabile su BIOS e UEFI)
FAKE_ISOS = {
    "Strumenti": ["Clonezilla 3.1", "GParted 1.6"],
    "Windows": ["Windows 11 Pro 24H2", "Windows 10 22H2", "WinPE di servizio"],
    "Linux": ["Debian 13 netinst", "Ubuntu 24.04 LTS", "Fedora 41 Workstation", "Alpine 3.21", "Arch Linux 2026.01"],
    "": ["ISO senza gruppo", "Utility varie"],
}
LABEL_RE = re.compile(r"^:[a-z0-9][a-z0-9.-]*$")


def _fake_call(*args, stdin_text=None, timeout=180):
    if args and args[0] == "list-mounts":
        return {"iso": {}, "sources": {}}
    return {"ok": True}


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _catalogo():
    """Catalogo finto: ISO abilitate, con gruppo e ordine, tipo con ricetta valida su bios+efi."""
    isos, order = {}, 0
    for group, names in FAKE_ISOS.items():
        for n in names:
            order += 1
            s = _slug(n)
            isos[s] = {"slug": s, "name": n, "file": s + ".iso", "rel_path": s + ".iso", "path": f"/srv/pixio/library/{s}.iso",
                       "source": "local", "source_name": "Libreria locale", "size": 1 << 30, "mtime": 1,
                       "type": "debian-live", "enabled": True, "group": group, "order": order,
                       "custom_recipe": None, "cache_wanted": False, "cache": {"status": "none", "path": "", "progress": 0},
                       "detect": {"files": {}}, "first_seen": "2026-09-01T10:00:00", "last_seen": "2026-09-08T10:00:00",
                       "missing": False}
    return {"isos": isos, "last_scan": "2026-09-08T10:00:00"}


def _sezione(script, label):
    """Righe della sezione ':label' fino alla label successiva (esclusa)."""
    out, dentro = [], False
    for l in script.splitlines():
        if l.startswith(":"):
            if dentro:
                break
            dentro = l == ":" + label
            continue
        if dentro:
            out.append(l)
    return out


def _items(righe):
    """Chiavi degli item selezionabili (esclusi i separatori --gap)."""
    return [l.split()[1] for l in righe if l.startswith("item ") and not l.startswith("item --gap")]


class MenuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pixio-menu-test-")
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
        C.HTTP_ISO_DIR = os.path.join(C.HTTP_DIR, "iso")
        C.HTTP_INJECT_DIR = os.path.join(C.HTTP_DIR, "inject")
        C.DRIVERS_DIR = os.path.join(C.HTTP_DIR, "drivers")
        C.SOURCES_MOUNT_DIR = os.path.join(C.SRV_DIR, "sources")
        for d in (C.ETC_DIR, C.VAR_DIR, C.JOBS_DIR, C.UPLOAD_TMP_DIR, C.LOG_DIR, C.LIBRARY_DIR, C.CACHE_DIR,
                  C.TFTP_DIR, C.HTTP_ISO_DIR, C.HTTP_INJECT_DIR, C.DRIVERS_DIR):
            os.makedirs(d, exist_ok=True)
        cls._orig_call = privileged.call
        privileged.call = _fake_call

        from pixio.services import catalog
        catalog.save(_catalogo())
        cls._orig_mounted = catalog._mounted_slugs
        catalog._mounted_slugs = lambda: set(_catalogo()["isos"])     # tutte "montate": niente loop mount nei test
        cls.catalog = catalog

        from pixio import settings as S
        cfg = S.load()
        cfg["network"]["server_ip"] = "10.10.0.254"
        cfg["network"]["interface"] = "ens18"
        cfg["menu"]["groups"] = ["Strumenti", "Windows", "Linux"]
        S.save(cfg)
        cls.S = S

        from pixio import create_app
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        assert not cls.client.get("/api/auth/status").get_json()["password_set"], "config temporanea attesa senza password"
        r = cls.client.post("/api/auth/login", json={"password": "segreta1"})
        assert r.status_code == 200, r.get_json()
        cls.h = {"X-CSRF-Token": r.get_json()["csrf"]}

    @classmethod
    def tearDownClass(cls):
        privileged.call = cls._orig_call
        cls.catalog._mounted_slugs = cls._orig_mounted
        for k, v in cls.saved.items():
            setattr(C, k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- utilita'
    def menu(self, **kw):
        """Aggiorna le impostazioni del menu e ritorna lo script generato per UEFI."""
        from pixio.services import ipxe_menu
        cfg = self.S.load()
        cfg["menu"].update(kw)
        self.S.save(cfg)
        return ipxe_menu.menu_script("efi", cfg=cfg)

    def principale(self, script):
        return _sezione(script, "menu")

    # ---------------------------------------------------------------- sottomenu
    def test_01_sotto_soglia_nessun_sottomenu(self):
        """12 voci con soglia 20: menu piatto come prima, tutte le ISO nel menu principale."""
        script = self.menu(submenus="auto", submenu_threshold=20, default="local", timeout=30)
        principale = self.principale(script)
        chiavi = _items(principale)
        for s in _catalogo()["isos"]:
            self.assertIn(s, chiavi, s)
        self.assertNotIn("grp-1", chiavi)
        self.assertNotIn(":grp-1", script)
        self.assertNotIn("Torna al menu principale", script)
        self.assertIn("item --gap WINDOWS", script)

    def test_02_sopra_soglia_un_item_per_gruppo(self):
        """12 voci con soglia 5: nel principale un item per gruppo (con il conteggio) e nessuna ISO."""
        script = self.menu(submenus="auto", submenu_threshold=5, default="local", timeout=30)
        principale = self.principale(script)
        chiavi = _items(principale)
        for s in _catalogo()["isos"]:
            self.assertNotIn(s, chiavi, f"{s} non deve stare nel menu principale")
        gruppi = [c for c in chiavi if c.startswith("grp-")]
        self.assertEqual(len(gruppi), 4)                       # Strumenti, Windows, Linux, Altro
        self.assertEqual(gruppi, sorted(set(gruppi), key=gruppi.index))   # etichette univoche
        testo = "\n".join(principale)
        self.assertIn("Strumenti (2)", testo)
        self.assertIn("Windows (3)", testo)
        self.assertIn("Linux (5)", testo)
        self.assertIn("Altro (2)", testo)
        # ordine: quello di menu.groups, i gruppi extra e le voci senza gruppo in coda
        nomi = [l.split(None, 2)[2].strip() for l in principale if l.startswith("item grp-")]
        self.assertEqual(nomi, ["Strumenti (2)", "Windows (3)", "Linux (5)", "Altro (2)"])

    def test_03_ogni_gruppo_ha_la_sua_sezione(self):
        """Ogni gruppo ha una label ':grp-N' nello stesso script, con le sue voci e il ritorno al principale."""
        script = self.menu(submenus="always", submenu_threshold=8, default="local", timeout=30)
        principale = self.principale(script)
        atteso = {"Strumenti": 2, "Windows": 3, "Linux": 5, "Altro": 2}
        for riga in [l for l in principale if l.startswith("item grp-")]:
            label = riga.split()[1]
            nome = riga.split(None, 2)[2].strip().rsplit(" (", 1)[0]
            self.assertIn(f":{label}", script.splitlines())
            sez = _sezione(script, label)
            self.assertTrue(sez[0].startswith("menu "), sez[0])
            self.assertIn(nome, sez[0])                       # titolo del sottomenu
            voci = [c for c in _items(sez) if c != "menu"]
            self.assertEqual(len(voci), atteso[nome], nome)
            for s in voci:
                self.assertIn(f":{s}", script.splitlines())   # la voce ha la sua label di chain
            self.assertTrue(any(l.startswith("item menu ") and l.endswith("Torna al menu principale") for l in sez), sez)
            self.assertTrue(any(l.startswith("choose ") for l in sez))
            self.assertNotIn("--timeout", " ".join(l for l in sez if l.startswith("choose ")))
            self.assertIn("goto ${sel} || goto menu", sez)
        # niente chain HTTP verso un secondo menu: tutto in un unico script
        self.assertNotIn("/boot.ipxe", script.split(":menu", 1)[1])

    def test_04_voci_di_sistema_nel_principale(self):
        script = self.menu(submenus="always", default="local", timeout=30)
        chiavi = _items(self.principale(script))
        for k in ("memtest", "local", "shell", "reboot", "exit"):
            self.assertIn(k, chiavi, k)
        self.assertIn("choose --default local --timeout 30000 sel || goto local", script)

    def test_05_timeout_solo_nel_principale(self):
        script = self.menu(submenus="always", default="local", timeout=15)
        scelte = [l for l in script.splitlines() if l.startswith("choose ")]
        self.assertEqual(len([l for l in scelte if "--timeout" in l]), 1)
        self.assertIn("--timeout 15000", scelte[0])

    def test_06_predefinita_dentro_un_sottomenu(self):
        """La voce predefinita resta avviabile allo scadere del timeout anche se sta in un gruppo."""
        slug = _slug("Ubuntu 24.04 LTS")
        script = self.menu(submenus="always", default=slug, timeout=20)
        principale = self.principale(script)
        self.assertIn(slug, _items(principale))               # scorciatoia nel menu principale
        self.assertIn("(predefinita)", "\n".join(principale))
        self.assertIn(f"choose --default {slug} --timeout 20000 sel || goto {slug}", script)
        self.assertIn(f":{slug}", script.splitlines())
        # resta anche nel sottomenu del suo gruppo, gia' selezionata
        linux = [l.split()[1] for l in principale if l.startswith("item grp-") and "Linux" in l][0]
        sez = _sezione(script, linux)
        self.assertIn(slug, _items(sez))
        self.assertIn(f"choose --default {slug} sel || goto menu", sez)

    def test_07_never_come_prima(self):
        script = self.menu(submenus="never", submenu_threshold=1, default="local", timeout=30)
        chiavi = _items(self.principale(script))
        for s in _catalogo()["isos"]:
            self.assertIn(s, chiavi, s)
        self.assertNotIn("grp-", script)
        self.assertNotIn("Torna al menu principale", script)

    def test_08_sintassi_ipxe(self):
        """Un comando per riga, niente ';', label valide e univoche, ogni goto/item punta a una label esistente."""
        for modo in ("never", "always"):
            script = self.menu(submenus=modo, default="local", timeout=30)
            righe = script.splitlines()
            self.assertEqual(righe[0], "#!ipxe")
            label = [l[1:] for l in righe if l.startswith(":")]
            self.assertEqual(len(label), len(set(label)), f"label duplicate ({modo})")
            for l in righe:
                if l.startswith(":"):
                    self.assertRegex(l, LABEL_RE, f"label non valida: {l}")
                    continue
                self.assertNotIn(";", l, f"punto e virgola in: {l}")
            for l in righe:
                if l.startswith("goto ") and "${sel}" not in l:
                    self.assertIn(l.split()[1], label, l)
                if " || goto " in l:
                    self.assertIn(l.rsplit("goto ", 1)[1].strip(), label + ["${sel}"], l)
                if l.startswith("item ") and not l.startswith("item --gap"):
                    self.assertIn(l.split()[1], label, f"item senza label: {l}")

    # ---------------------------------------------------------------- API
    def test_09_api_get(self):
        self.menu(submenus="auto", submenu_threshold=8)
        d = self.client.get("/api/menu").get_json()
        self.assertEqual(d["settings"]["submenus"], "auto")
        self.assertEqual(d["settings"]["submenu_threshold"], 8)
        self.assertIn("efi", d["preview"])
        self.assertEqual(len(d["entries"]), 12)

    def test_10_api_put_validazione(self):
        for bad in ({"submenus": "forse"}, {"submenu_threshold": 0}, {"submenu_threshold": 101}, {"submenu_threshold": "molte"}):
            r = self.client.put("/api/menu", json={"settings": bad}, headers=self.h)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("error", r.get_json())
        r = self.client.put("/api/menu", json={"settings": {"submenus": "always", "submenu_threshold": 3}}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d["settings"]["submenus"], "always")
        self.assertEqual(d["settings"]["submenu_threshold"], 3)
        self.assertEqual(self.S.load()["menu"]["submenus"], "always")

    def test_11_api_anteprima_riflette_la_scelta(self):
        r = self.client.put("/api/menu", json={"settings": {"submenus": "never", "default": "local"}}, headers=self.h)
        self.assertNotIn(":grp-1", r.get_json()["preview"]["efi"])
        r = self.client.put("/api/menu", json={"settings": {"submenus": "always"}}, headers=self.h)
        for plat in ("efi", "bios"):
            self.assertIn(":grp-1", r.get_json()["preview"][plat])
        self.assertIn(":grp-1", self.client.get("/api/menu").get_json()["preview"]["efi"])


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
