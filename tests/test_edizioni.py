"""Test dell'edizione da installare: lettura delle immagini di install.wim, cache nel catalogo,
generazione dell'autounattend.xml e avvisi (docs/API.md, sezione 19).

Esecuzione: cd /opt/pixio && python3 -m unittest tests.test_edizioni
Non serve wimtools: wiminfo viene sostituito da un finto che stampa l'uscita vera di un install.wim.
I percorsi di config vengono reindirizzati in una cartella temporanea in setUpClass e ripristinati
alla fine, così il modulo convive con gli altri test nella stessa discovery.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.dom.minidom as minidom

os.environ["PIXIO_NO_BACKGROUND"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pixio import config as C  # noqa: E402
from pixio import privileged  # noqa: E402

OVERRIDE_KEYS = ("ETC_DIR", "CONFIG_FILE", "SECRET_FILE", "VAR_DIR", "JOBS_DIR", "UPLOAD_TMP_DIR",
                 "CLIENTS_FILE", "CATALOG_FILE", "ANSWERS_DIR", "ANSWERS_FILE", "WINPROFILES_FILE",
                 "LOG_DIR", "SRV_DIR", "LIBRARY_DIR", "CACHE_DIR", "TFTP_DIR", "HTTP_DIR",
                 "HTTP_ISO_DIR", "SOURCES_MOUNT_DIR")

# Uscita vera di `wiminfo` su una ISO Windows 11 Enterprise LTSC 2024: due immagini, ognuna con
# nome e nome visualizzato diversi. È il caso che ha fatto nascere la sezione 19: qui dentro
# "Windows 11 Pro" non c'è, e scriverlo nel profilo fermava l'installazione a metà.
WIMINFO_LTSC = """WIM Information:
----------------
Path:           /srv/pixio/http/iso/ltsc/sources/install.wim
GUID:           0x34f2cac6aafe95458dac6c052bc96b27
Version:        68864
Image Count:    2
Compression:    LZX

Available Images:
-----------------
Index:                  1
Name:                   Windows 11 Enterprise LTSC 2024
Description:            Windows 11 Enterprise LTSC 2024
Display Name:           Windows 11 Enterprise LTSC
Display Description:    Windows 11 Enterprise LTSC
Architecture:           x86_64
Major Version:          10
Minor Version:          0

Index:                  2
Name:                   Windows 11 Enterprise N LTSC 2024
Description:            Windows 11 Enterprise N LTSC 2024
Display Name:           Windows 11 Enterprise N LTSC
Display Description:    Windows 11 Enterprise N LTSC
Architecture:           x86_64
Major Version:          10
Minor Version:          0
"""

WIMINFO_UNA_SOLA = """WIM Information:
----------------
Version:        68864
Image Count:    1

Available Images:
-----------------
Index:                  1
Name:                   Windows 10 Pro
Description:            Windows 10 Pro
Display Name:           Windows 10 Pro
"""

EDIZIONI_LTSC = [
    {"index": 1, "name": "Windows 11 Enterprise LTSC 2024", "display_name": "Windows 11 Enterprise LTSC"},
    {"index": 2, "name": "Windows 11 Enterprise N LTSC 2024", "display_name": "Windows 11 Enterprise N LTSC"},
]


def _fake_call(*args, stdin_text=None, timeout=180):
    if args and args[0] == "list-mounts":
        return {"iso": {}, "sources": {}}
    return {"ok": True}


class FintoWiminfo:
    """Sostituisce subprocess.run dentro services/detect.py: niente wimtools, uscita decisa qui."""

    def __init__(self, stdout):
        self.stdout = stdout
        self.chiamate = []

    def __call__(self, cmd, **kw):
        self.chiamate.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=self.stdout, stderr="")


def percorsi(t):
    """Reindirizza i percorsi di pixio.config dentro la cartella temporanea `t`."""
    C.ETC_DIR = os.path.join(t, "etc")
    C.CONFIG_FILE = os.path.join(C.ETC_DIR, "config.json")
    C.SECRET_FILE = os.path.join(C.ETC_DIR, "secret")
    C.VAR_DIR = os.path.join(t, "var")
    C.JOBS_DIR = os.path.join(C.VAR_DIR, "jobs")
    C.UPLOAD_TMP_DIR = os.path.join(C.VAR_DIR, "uploads")
    C.CLIENTS_FILE = os.path.join(C.VAR_DIR, "clients.json")
    C.CATALOG_FILE = os.path.join(C.VAR_DIR, "catalog.json")
    C.ANSWERS_DIR = os.path.join(C.VAR_DIR, "answers")
    C.ANSWERS_FILE = os.path.join(C.VAR_DIR, "answers.json")
    C.WINPROFILES_FILE = os.path.join(C.VAR_DIR, "winprofiles.json")
    C.LOG_DIR = os.path.join(t, "log")
    C.SRV_DIR = os.path.join(t, "srv")
    C.LIBRARY_DIR = os.path.join(C.SRV_DIR, "library")
    C.CACHE_DIR = os.path.join(C.SRV_DIR, "cache")
    C.TFTP_DIR = os.path.join(C.SRV_DIR, "tftp")
    C.HTTP_DIR = os.path.join(C.SRV_DIR, "http")
    C.HTTP_ISO_DIR = os.path.join(C.HTTP_DIR, "iso")
    C.SOURCES_MOUNT_DIR = os.path.join(C.SRV_DIR, "sources")
    for d in (C.ETC_DIR, C.VAR_DIR, C.JOBS_DIR, C.UPLOAD_TMP_DIR, C.LOG_DIR, C.LIBRARY_DIR,
              C.CACHE_DIR, C.TFTP_DIR, C.HTTP_ISO_DIR, C.ANSWERS_DIR):
        os.makedirs(d, exist_ok=True)


def iso_finta(slug="ltsc", tipo="windows", install="sources/install.wim", **extra):
    """Voce di catalogo di una ISO Windows, con il file dell'immagine creato nel mount finto."""
    e = {"slug": slug, "source": "local", "rel_path": slug + ".iso", "file": slug + ".iso",
         "name": "Windows 11 Enterprise LTSC 2024", "enabled": True, "group": "Windows",
         "order": 10, "type": tipo, "size": 4238329309, "mtime": 1725600000,
         "path": os.path.join(C.LIBRARY_DIR, slug + ".iso"),
         "detect": {"type": tipo, "files": {"install": install} if install else {},
                    "editions": [], "images": []}}
    e.update(extra)
    if install:
        p = os.path.join(C.HTTP_ISO_DIR, slug, install)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"finto")
    return e


def scrivi_catalogo(*isos):
    from pixio.services import catalog
    catalog.save({"isos": {e["slug"]: e for e in isos}, "last_scan": None})


def base_settings(**over):
    """Impostazioni minime valide, con le sovrascritture richieste."""
    from pixio.services import winprofile as WP
    s = WP.defaults()
    s["admin_password"] = "PasswordDiProva1"
    s.update(over)
    return s


# ---------------------------------------------------------------- lettura di wiminfo

class WimImagesTest(unittest.TestCase):
    def setUp(self):
        from pixio.services import detect
        self.detect = detect
        self._run = detect.subprocess.run

    def tearDown(self):
        self.detect.subprocess.run = self._run

    def test_indici_e_nomi(self):
        """Ogni immagine con il suo indice 1-based, nell'ordine di wiminfo."""
        self.detect.subprocess.run = FintoWiminfo(WIMINFO_LTSC)
        images, version = self.detect.wim_images("/finto/install.wim")
        self.assertEqual(images, EDIZIONI_LTSC)
        self.assertEqual(version, "68864")       # la Version dell'intestazione, non "Major Version"

    def test_nomi_compatibili_con_prima(self):
        """wim_info() continua a dare nomi e versione come li dava prima della sezione 19."""
        self.detect.subprocess.run = FintoWiminfo(WIMINFO_LTSC)
        names, version = self.detect.wim_info("/finto/install.wim")
        self.assertEqual(names, ["Windows 11 Enterprise LTSC 2024", "Windows 11 Enterprise LTSC",
                                 "Windows 11 Enterprise N LTSC 2024", "Windows 11 Enterprise N LTSC"])
        self.assertEqual(version, "68864")

    def test_nomi_senza_doppioni(self):
        """Nome e nome visualizzato uguali contano una volta sola."""
        self.detect.subprocess.run = FintoWiminfo(WIMINFO_UNA_SOLA)
        self.assertEqual(self.detect.wim_info("/finto/install.wim")[0], ["Windows 10 Pro"])

    def test_wiminfo_assente(self):
        """Senza wimtools (o con un file illeggibile) l'elenco è vuoto e non salta niente."""
        def esplode(cmd, **kw):
            raise OSError("wiminfo non installato")
        self.detect.subprocess.run = esplode
        self.assertEqual(self.detect.wim_images("/finto/install.wim"), ([], ""))
        self.assertEqual(self.detect.wim_info("/finto/install.wim"), ([], ""))

    def test_uscita_vuota(self):
        self.detect.subprocess.run = FintoWiminfo("")
        self.assertEqual(self.detect.wim_images("/finto/install.wim"), ([], ""))


# ---------------------------------------------------------------- cache nel catalogo

class CacheCatalogoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pixio-ediz-test-")
        cls.saved = {k: getattr(C, k) for k in OVERRIDE_KEYS}
        cls._call = privileged.call
        privileged.call = _fake_call

    @classmethod
    def tearDownClass(cls):
        privileged.call = cls._call
        for k, v in cls.saved.items():
            setattr(C, k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        percorsi(self.tmp)
        from pixio.services import catalog, detect
        self.catalog = catalog
        self.detect = detect
        self._montate = catalog._mounted_slugs
        self._images = detect.wim_images
        self.letture = []

        def finte(path):
            self.letture.append(path)
            return list(EDIZIONI_LTSC), "68864"
        detect.wim_images = finte
        catalog._mounted_slugs = lambda: {"ltsc"}

    def tearDown(self):
        self.catalog._mounted_slugs = self._montate
        self.detect.wim_images = self._images
        shutil.rmtree(os.path.join(self.tmp, "srv"), ignore_errors=True)
        for f in (C.CATALOG_FILE, C.ANSWERS_FILE, C.WINPROFILES_FILE):
            if os.path.exists(f):
                os.unlink(f)

    def test_rilettura_scrive_la_cache(self):
        scrivi_catalogo(iso_finta())
        self.assertEqual(self.catalog.refresh_editions("ltsc"), EDIZIONI_LTSC)
        e = self.catalog.load()["isos"]["ltsc"]
        self.assertEqual(e["editions"]["images"], EDIZIONI_LTSC)
        self.assertEqual(e["editions"]["file"], "sources/install.wim")
        self.assertEqual(e["editions"]["error"], "")
        self.assertTrue(e["editions"]["updated"])
        # la voce pubblica porta l'elenco pronto per la GUI
        pub = self.catalog.get("ltsc")
        self.assertEqual(pub["editions"], EDIZIONI_LTSC)
        self.assertEqual(pub["editions_info"]["file"], "sources/install.wim")

    def test_non_rilegge_se_la_cache_vale_ancora(self):
        """Il file è grande e spesso sta su una share: si rilegge solo se è cambiato."""
        scrivi_catalogo(iso_finta())
        self.catalog.refresh_editions("ltsc")
        self.assertEqual(len(self.letture), 1)
        self.catalog.refresh_editions("ltsc")
        self.assertEqual(len(self.letture), 1, "seconda lettura inutile")
        self.catalog.refresh_editions("ltsc", force=True)
        self.assertEqual(len(self.letture), 2, "la richiesta esplicita deve rileggere")

    def test_rilegge_se_la_iso_e_cambiata(self):
        scrivi_catalogo(iso_finta())
        self.catalog.refresh_editions("ltsc")

        def upd(c):
            c["isos"]["ltsc"]["mtime"] = 1725600001
            return c
        from pixio.storage import update_json
        update_json(C.CATALOG_FILE, upd)
        self.assertTrue(self.catalog.editions_stale(self.catalog.load()["isos"]["ltsc"]))
        self.catalog.refresh_editions("ltsc")
        self.assertEqual(len(self.letture), 2)

    def test_install_esd(self):
        """Le ISO del Media Creation Tool hanno install.esd: si legge quello."""
        scrivi_catalogo(iso_finta(install="sources/install.esd"))
        self.assertEqual(self.catalog.refresh_editions("ltsc"), EDIZIONI_LTSC)
        self.assertTrue(self.letture[0].endswith("sources/install.esd"))

    def test_percorsi_soliti_senza_rilevamento(self):
        """Voce vecchia senza il percorso dell'immagine: si provano i nomi soliti."""
        e = iso_finta(install="sources/install.esd")
        e["detect"]["files"] = {}
        scrivi_catalogo(e)
        self.assertEqual(self.catalog.refresh_editions("ltsc"), EDIZIONI_LTSC)

    def test_immagine_assente(self):
        """Senza install.wim l'elenco resta vuoto, l'errore è scritto e la ISO resta usabile."""
        scrivi_catalogo(iso_finta(install=None))
        self.assertEqual(self.catalog.refresh_editions("ltsc"), [])
        pub = self.catalog.get("ltsc")
        self.assertEqual(pub["editions"], [])
        self.assertTrue(pub["editions_info"]["error"])
        self.assertTrue(any("Edizioni non leggibili" in w for w in pub["warnings"]))

    def test_solo_le_iso_windows(self):
        scrivi_catalogo(iso_finta(slug="ltsc", tipo="debian-installer"))
        self.assertEqual(self.catalog.refresh_editions("ltsc"), [])
        self.assertEqual(self.letture, [])

    def test_ripiego_sul_rilevamento(self):
        """Catalogo scritto prima della cache: le immagini registrate dal rilevamento bastano."""
        e = iso_finta()
        e["detect"]["images"] = list(EDIZIONI_LTSC)
        scrivi_catalogo(e)
        self.assertEqual(self.catalog.editions_of(self.catalog.load()["isos"]["ltsc"]), EDIZIONI_LTSC)

    def test_avviso_sul_profilo_collegato(self):
        """L'avviso deve vedersi nei dettagli della ISO, prima di avviare l'installazione."""
        from pixio.services import winprofile as WP
        e = iso_finta()
        e["editions"] = {"images": list(EDIZIONI_LTSC), "file": "sources/install.wim",
                         "error": "", "updated": "2026-01-01T00:00:00",
                         "size": e["size"], "mtime": e["mtime"]}
        e["answers"] = ["win11ltsc"]
        e["answer_id"] = "win11ltsc"
        scrivi_catalogo(e)
        with open(C.ANSWERS_FILE, "w", encoding="utf-8") as f:
            json.dump({"answers": {"win11ltsc": {"name": "Windows11LTSC", "kind": "windows",
                                                 "profile": "windows11ltsc",
                                                 "main_file": "autounattend.xml"}}}, f)
        WP.create({"name": "Windows11LTSC", "settings": base_settings(
            target="11-ltsc", edition_index="Windows 11 Pro")})
        avvisi = [w for w in self.catalog.get("ltsc")["warnings"] if "Windows 11 Pro" in w]
        self.assertEqual(len(avvisi), 1, self.catalog.get("ltsc")["warnings"])
        self.assertIn("Windows 11 Enterprise LTSC 2024", avvisi[0])
        # corretta l'edizione, l'avviso sparisce
        WP.update("windows11ltsc", {"settings": {"edition_index": "Windows 11 Enterprise LTSC 2024"}})
        self.assertEqual([w for w in self.catalog.get("ltsc")["warnings"] if "edizione" in w], [])

    def test_legami_profilo_iso(self):
        """iso_bindings() dice su quale immagine girerà ogni profilo (serve alla tendina)."""
        from pixio.services import winprofile as WP
        e = iso_finta()
        e["editions"] = {"images": list(EDIZIONI_LTSC), "file": "sources/install.wim", "error": "",
                         "updated": "2026-01-01T00:00:00", "size": e["size"], "mtime": e["mtime"]}
        e["answers"] = ["risposta-ltsc"]
        scrivi_catalogo(e)
        with open(C.ANSWERS_FILE, "w", encoding="utf-8") as f:
            json.dump({"answers": {"risposta-ltsc": {"name": "Altro nome", "kind": "windows",
                                                     "profile": "windows11ltsc"}}}, f)
        WP.create({"name": "Windows11LTSC", "settings": base_settings(target="11-ltsc")})
        legami = WP.iso_bindings()
        self.assertIn("windows11ltsc", legami)
        voce = legami["windows11ltsc"][0]
        self.assertEqual(voce["slug"], "ltsc")
        self.assertEqual(voce["editions"], EDIZIONI_LTSC)
        self.assertEqual(voce["answer_id"], "risposta-ltsc")

    def test_legami_risposte_vecchie(self):
        """Risposte create prima del campo `profile`: valgono id uguale e nome uguale."""
        from pixio.services import winprofile as WP
        e = iso_finta()
        e["answers"] = ["windows11ltsc", "per-nome"]
        scrivi_catalogo(e)
        with open(C.ANSWERS_FILE, "w", encoding="utf-8") as f:
            json.dump({"answers": {"windows11ltsc": {"name": "Un nome qualunque", "kind": "windows"},
                                   "per-nome": {"name": "Altro Profilo", "kind": "windows"}}}, f)
        WP.create({"name": "Windows11LTSC", "settings": base_settings()})
        WP.create({"name": "Altro Profilo", "settings": base_settings()})
        per_risposta = WP.profiles_by_answer()
        self.assertEqual(per_risposta["windows11ltsc"]["id"], "windows11ltsc")
        self.assertEqual(per_risposta["per-nome"]["id"], "altro-profilo")


# ---------------------------------------------------------------- confronto e avvisi

class ConfrontoTest(unittest.TestCase):
    def setUp(self):
        from pixio.services import winprofile as WP
        self.WP = WP

    def test_per_nome_indice_e_nome_visualizzato(self):
        m = self.WP.match_edition
        self.assertEqual(m(EDIZIONI_LTSC, "Windows 11 Enterprise LTSC 2024")["index"], 1)
        self.assertEqual(m(EDIZIONI_LTSC, "windows 11 enterprise n ltsc 2024")["index"], 2)
        self.assertEqual(m(EDIZIONI_LTSC, "Windows 11 Enterprise LTSC")["index"], 1)
        self.assertEqual(m(EDIZIONI_LTSC, "2")["index"], 2)
        self.assertIsNone(m(EDIZIONI_LTSC, "Windows 11 Pro"))
        self.assertIsNone(m(EDIZIONI_LTSC, "9"))
        self.assertIsNone(m(EDIZIONI_LTSC, ""))
        self.assertIsNone(m([], "Windows 11 Pro"))

    def test_avviso_con_elenco(self):
        a = self.WP.edition_warning(EDIZIONI_LTSC, "Windows 11 Pro")
        self.assertIn("Windows 11 Pro", a)
        self.assertIn("1 Windows 11 Enterprise LTSC 2024", a)
        self.assertIn("2 Windows 11 Enterprise N LTSC 2024", a)
        self.assertEqual(self.WP.edition_warning(EDIZIONI_LTSC, "1"), "")
        # senza elenco non si può dire che un valore sia sbagliato
        self.assertEqual(self.WP.edition_warning([], "Windows 11 Pro"), "")

    def test_avviso_sul_tipo_di_windows(self):
        w = self.WP.edition_target_warning
        self.assertIn("Windows 11 Pro", w("11-ltsc", "Windows 11 Pro"))
        self.assertIn("LTSC", w("10-ltsc", "Windows 10 Pro"))
        self.assertEqual(w("11-ltsc", "Windows 11 Enterprise LTSC 2024"), "")
        self.assertEqual(w("11-ltsc", "1"), "")
        self.assertEqual(w("client", "Windows 11 Pro"), "")
        self.assertIn("Windows Server", w("client", "Windows Server 2022 SERVERSTANDARD"))
        self.assertIn("Server", w("server", "Windows 11 Pro"))
        self.assertEqual(w("server", "Windows Server 2025 SERVERSTANDARD"), "")
        self.assertEqual(w("client", ""), "")

    def test_tipi_con_i_nomi_suggeriti(self):
        per_id = {t["id"]: t for t in self.WP.targets_list()}
        self.assertEqual(per_id["client"]["editions"], [])
        self.assertIn("Windows 11 Enterprise LTSC 2024", per_id["11-ltsc"]["editions"])
        for t, voci in per_id.items():
            for nome in voci["editions"]:
                self.assertEqual(self.WP.edition_target_warning(t, nome), "",
                                 f"{t}: suggerimento incoerente {nome}")


# ---------------------------------------------------------------- generazione dell'XML

class GenerazioneTest(unittest.TestCase):
    def setUp(self):
        from pixio.services import winprofile as WP
        self.WP = WP
        self.cfg = {"network": {"server_ip": "10.10.0.254"}, "windows": {}}

    def _xml(self, edizione, editions=None):
        prof = {"name": "Prova", "settings": base_settings(edition_index=edizione)}
        xml = self.WP.render_autounattend(prof, "10.10.0.254", cfg=self.cfg, editions=editions)
        return xml, minidom.parseString(xml)

    def _install_from(self, dom):
        """(chiave, valore) del MetaData di InstallFrom, oppure None se non c'è."""
        nodi = dom.getElementsByTagName("InstallFrom")
        if not nodi:
            return None
        md = nodi[0].getElementsByTagName("MetaData")[0]
        def testo(tag):
            el = md.getElementsByTagName(tag)[0]
            return "".join(n.data for n in el.childNodes if n.nodeType == n.TEXT_NODE).strip()
        return testo("Key"), testo("Value")

    def test_edizione_giusta_resta(self):
        _, dom = self._xml("Windows 11 Enterprise LTSC 2024", EDIZIONI_LTSC)
        self.assertEqual(self._install_from(dom), ("/IMAGE/NAME", "Windows 11 Enterprise LTSC 2024"))

    def test_indice_resta(self):
        _, dom = self._xml("2", EDIZIONI_LTSC)
        self.assertEqual(self._install_from(dom), ("/IMAGE/INDEX", "2"))

    def test_edizione_sbagliata_con_piu_immagini(self):
        """Più edizioni: InstallFrom non si genera e il setup chiede, invece di fallire."""
        xml, dom = self._xml("Windows 11 Pro", EDIZIONI_LTSC)
        self.assertIsNone(self._install_from(dom))
        self.assertNotIn("Windows 11 Pro", xml)
        # il resto del blocco resta: la partizione di destinazione va comunque scritta
        self.assertTrue(dom.getElementsByTagName("InstallTo"))

    def test_edizione_sbagliata_con_una_sola_immagine(self):
        una = [{"index": 1, "name": "Windows 10 Pro", "display_name": "Windows 10 Pro"}]
        _, dom = self._xml("Windows 11 Pro", una)
        self.assertEqual(self._install_from(dom), ("/IMAGE/NAME", "Windows 10 Pro"))

    def test_senza_edizioni_come_prima(self):
        """Senza sapere su quale immagine si va, il valore scritto dal tecnico non si tocca."""
        _, dom = self._xml("Windows 11 Pro")
        self.assertEqual(self._install_from(dom), ("/IMAGE/NAME", "Windows 11 Pro"))
        _, dom = self._xml("Windows 11 Pro", [])
        self.assertEqual(self._install_from(dom), ("/IMAGE/NAME", "Windows 11 Pro"))

    def test_edizione_vuota(self):
        _, dom = self._xml("", EDIZIONI_LTSC)
        self.assertIsNone(self._install_from(dom))

    def test_avviso_nei_log(self):
        with self.assertLogs("pixio.winprofile", level="WARNING") as reg:
            self._xml("Windows 11 Pro", EDIZIONI_LTSC)
        self.assertTrue(any("Windows 11 Pro" in r.getMessage() for r in reg.records))


# ---------------------------------------------------------------- API

class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pixio-ediz-api-")
        cls.saved = {k: getattr(C, k) for k in OVERRIDE_KEYS}
        percorsi(cls.tmp)
        cls._call = privileged.call
        privileged.call = _fake_call
        from pixio import create_app
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        r = cls.client.post("/api/auth/login", json={"password": "segreta1"})
        assert r.status_code == 200, r.get_json()
        cls.h = {"X-CSRF-Token": r.get_json()["csrf"]}

    @classmethod
    def tearDownClass(cls):
        privileged.call = cls._call
        for k, v in cls.saved.items():
            setattr(C, k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        percorsi(self.tmp)
        from pixio.services import catalog, detect
        self.catalog = catalog
        self.detect = detect
        self._montate = catalog._mounted_slugs
        self._images = detect.wim_images
        catalog._mounted_slugs = lambda: {"ltsc"}
        detect.wim_images = lambda path: (list(EDIZIONI_LTSC), "68864")
        for f in (C.CATALOG_FILE, C.ANSWERS_FILE, C.WINPROFILES_FILE):
            if os.path.exists(f):
                os.unlink(f)

    def tearDown(self):
        self.catalog._mounted_slugs = self._montate
        self.detect.wim_images = self._images
        shutil.rmtree(os.path.join(self.tmp, "srv", "http"), ignore_errors=True)

    def test_lettura_dalla_cache(self):
        e = iso_finta()
        e["editions"] = {"images": list(EDIZIONI_LTSC), "file": "sources/install.wim", "error": "",
                         "updated": "2026-01-01T00:00:00", "size": e["size"], "mtime": e["mtime"]}
        scrivi_catalogo(e)
        r = self.client.get("/api/catalog/ltsc/editions")
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d["slug"], "ltsc")
        self.assertEqual(d["editions"], EDIZIONI_LTSC)
        self.assertEqual(d["file"], "sources/install.wim")
        self.assertEqual(d["error"], "")

    def test_iso_inesistente(self):
        scrivi_catalogo(iso_finta())
        self.assertEqual(self.client.get("/api/catalog/non-esiste/editions").status_code, 404)

    def test_rilettura_esplicita(self):
        from pixio.services import jobs
        scrivi_catalogo(iso_finta())
        r = self.client.post("/api/catalog/ltsc/editions", headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        jobs.wait(r.get_json()["job_id"], 10)
        d = self.client.get("/api/catalog/ltsc/editions").get_json()
        self.assertEqual(d["editions"], EDIZIONI_LTSC)

    def test_rilettura_solo_su_windows(self):
        scrivi_catalogo(iso_finta(tipo="debian-installer"))
        r = self.client.post("/api/catalog/ltsc/editions", headers=self.h)
        self.assertEqual(r.status_code, 400)

    def test_profili_con_le_iso_abbinate(self):
        from pixio.services import winprofile as WP
        e = iso_finta()
        e["editions"] = {"images": list(EDIZIONI_LTSC), "file": "sources/install.wim", "error": "",
                         "updated": "2026-01-01T00:00:00", "size": e["size"], "mtime": e["mtime"]}
        e["answers"] = ["windows11ltsc"]
        scrivi_catalogo(e)
        with open(C.ANSWERS_FILE, "w", encoding="utf-8") as f:
            json.dump({"answers": {"windows11ltsc": {"name": "Windows11LTSC", "kind": "windows",
                                                     "profile": "windows11ltsc"}}}, f)
        WP.create({"name": "Windows11LTSC", "settings": base_settings(
            target="11-ltsc", edition_index="Windows 11 Pro")})
        d = self.client.get("/api/winprofiles").get_json()
        p = next(x for x in d["profiles"] if x["id"] == "windows11ltsc")
        self.assertEqual(len(p["isos"]), 1)
        self.assertEqual(p["isos"][0]["editions"], EDIZIONI_LTSC)
        self.assertTrue(all("editions" in t for t in d["targets"]))
        # anteprima per quella ISO: l'edizione sbagliata non finisce nel file
        r = self.client.post("/api/winprofiles/windows11ltsc/preview", headers=self.h,
                             json={"iso": "ltsc"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertNotIn("Windows 11 Pro", r.get_json()["xml"])
        # senza ISO il comportamento resta quello di prima
        r = self.client.post("/api/winprofiles/windows11ltsc/preview", headers=self.h, json={})
        self.assertIn("Windows 11 Pro", r.get_json()["xml"])


# ---------------------------------------------------------------- modelli

class PresetTest(unittest.TestCase):
    """I modelli non devono proporre un'edizione che su quel tipo di Windows non esiste."""

    def setUp(self):
        from pixio.services import winprofile as WP
        self.WP = WP
        percorso = getattr(C, "PRESETS_FILE",
                           os.path.join(getattr(C, "CODE_DIR", "/opt/pixio"), "data",
                                        "profile-presets.json"))
        try:
            with open(percorso, encoding="utf-8") as f:
                self.presets = [p for p in (json.load(f).get("presets") or [])
                                if p.get("kind") == "windows"]
        except (OSError, ValueError):
            self.presets = []
        if not self.presets:
            self.skipTest("modelli non installati")

    def test_edizione_coerente_col_tipo(self):
        for p in self.presets:
            s = p["settings"]
            avviso = self.WP.edition_target_warning(s.get("target"), s.get("edition_index"))
            self.assertEqual(avviso, "", f"{p['id']}: {avviso}")

    def test_ltsc_non_propone_windows_11_pro(self):
        per_id = {p["id"]: p["settings"] for p in self.presets}
        self.assertNotIn("Pro", per_id["win11-ltsc"]["edition_index"])
        self.assertIn("LTSC", per_id["win11-ltsc"]["edition_index"])
        self.assertIn("LTSC", per_id["win10-ltsc"]["edition_index"])

    def test_server_senza_edizione_fissa(self):
        """Il nome dell'immagine dei server cambia con l'anno: meglio vuoto che sbagliato."""
        per_id = {p["id"]: p["settings"] for p in self.presets}
        self.assertEqual(per_id["winserver"]["edition_index"], "")


if __name__ == "__main__":
    unittest.main()
