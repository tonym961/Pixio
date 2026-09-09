"""Test del menu di boot iPXE: sottomenu per gruppo (auto/always/never), stile della console
(testo/grafico/compatibile, cioe' framebuffer o console del firmware), sintassi dello script, API /api/menu
e scelta dell'installazione automatica dopo la ISO (docs/API.md sezione 17).

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
                 "CLIENTS_FILE", "CATALOG_FILE", "DRIVERS_FILE", "ANSWERS_DIR", "ANSWERS_FILE", "LOG_DIR",
                 "SRV_DIR", "LIBRARY_DIR", "CACHE_DIR",
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
        C.DRIVERS_DIR = os.path.join(C.HTTP_DIR, "drivers")
        C.SOURCES_MOUNT_DIR = os.path.join(C.SRV_DIR, "sources")
        for d in (C.ETC_DIR, C.VAR_DIR, C.JOBS_DIR, C.UPLOAD_TMP_DIR, C.LOG_DIR, C.LIBRARY_DIR, C.CACHE_DIR,
                  C.TFTP_DIR, C.HTTP_ISO_DIR, C.HTTP_INJECT_DIR, C.DRIVERS_DIR, C.ANSWERS_DIR):
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

        from pixio.services import answers
        cls.answers = answers
        for nome in ("Aula base", "Aula con Office", "Senza domande"):
            answers.create({"name": nome, "kind": "debian", "content": f"# preseed {nome}\n"})

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

    # ------------------------------------------------- stile del menu (dove iPXE disegna = reattivita')
    def tema(self, **th):
        """Salva il tema indicato e ritorna (script UEFI, righe 'console' generate)."""
        from pixio.services import ipxe_menu
        cfg = self.S.load()
        cfg["menu"]["theme"] = dict(cfg["menu"].get("theme") or {}, **th)
        self.S.save(cfg)
        script = ipxe_menu.menu_script("efi", cfg=cfg)
        return script, [l for l in script.splitlines() if l.startswith("console ")]

    def test_12_stile_testo_prende_il_framebuffer(self):
        """Stile testo: una riga 'console' con la risoluzione e senza immagine.

        Serve a spegnere la console del firmware, dove ogni carattere e' una chiamata al BIOS/UEFI:
        a ogni spostamento della selezione iPXE ne fa circa 170 ed e' la causa dei menu da 5 secondi.
        """
        script, console = self.tema(style="testo", resolution="1024x768")
        self.assertEqual(len(console), 1, console)
        self.assertIn("-x 1024 -y 768", console[0])
        self.assertNotIn("--picture", console[0])
        self.assertTrue(console[0].endswith("||"), console[0])
        self.assertLess(script.index("console "), script.index("colour --basic"))   # console, poi colori
        self.assertLess(script.index("console "), script.index("\n:menu"))        # entrambi prima del menu

    def test_13_stile_grafico_ripiega_sul_framebuffer(self):
        """Stile grafico: una riga sola, sfondo con il framebuffer nudo come ripiego dopo '||'.

        Se il PNG non arriva o non si decodifica, iPXE non riconfigura la console (console_cmd.c
        esce prima di console_configure) ed esegue il secondo comando: resta sul framebuffer invece
        di ricadere sulla console lenta del firmware. Sulla stessa riga il ripiego non costa nulla
        quando l'immagine c'e'.
        """
        script, console = self.tema(style="grafico", resolution="1024x768")
        self.assertEqual(len(console), 1, console)
        picture, fallback = console[0].split(" || ", 1)
        self.assertIn("--picture http://10.10.0.254/pxe/inject/theme/bg-1024x768.png", picture)
        self.assertIn("--top 104", picture)             # cornice piu' larga: intestazione e piede disegnati
        self.assertTrue(fallback.startswith("console -x 1024 -y 768"), fallback)
        self.assertNotIn("--picture", fallback)
        self.assertTrue(console[0].endswith("||"), console[0])
        from pixio.services import theme as T
        self.assertTrue(os.path.isfile(T.bg_path(T.theme(self.S.load()))), "sfondo non generato")

    def test_14_risoluzione_nel_comando_e_nel_nome_del_file(self):
        """La risoluzione scelta finisce sia in -x/-y sia nel nome dello sfondo (niente PNG stantii)."""
        for res, w, h in (("800x600", 800, 600), ("640x480", 640, 480)):
            script, console = self.tema(style="grafico", resolution=res)
            self.assertEqual(len(console), 1, console)
            for cmd in console[0].split(" || "):
                if cmd.strip():
                    self.assertIn(f"-x {w} -y {h}", cmd, cmd)
            self.assertIn(f"bg-{w}x{h}.png", console[0])
            self.assertNotIn("bg-1024x768.png", console[0])

    def test_15_stile_compatibile_lascia_la_console_del_firmware(self):
        script, console = self.tema(style="compatibile")
        self.assertEqual(console, [], "in compatibilita' non si tocca la console")
        self.assertIn("colour --basic", script)          # i colori si impostano comunque
        self.assertIn("cpair --foreground 0 --background 6 2 ||", script)

    def test_16_valori_non_validi_tornano_al_predefinito(self):
        script, console = self.tema(style="fantasia", resolution="1920x1080")
        self.assertEqual(len(console), 1, console)
        self.assertIn("-x 1024 -y 768", console[0])
        self.assertNotIn("--picture", console[0])

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

    def test_17_api_stile_e_risoluzione(self):
        for bad in ({"theme": {"style": "fantasia"}}, {"theme": {"resolution": "1920x1080"}}):
            r = self.client.put("/api/menu", json={"settings": bad}, headers=self.h)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("error", r.get_json())
        r = self.client.put("/api/menu", json={"settings": {"theme": {"style": "grafico", "resolution": "800x600"}}}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d["settings"]["theme"]["style"], "grafico")
        self.assertEqual(d["settings"]["theme"]["resolution"], "800x600")
        self.assertEqual(d["settings"]["theme_bg_url"], "/pxe/inject/theme/bg-800x600.png")
        for plat in ("efi", "bios"):
            self.assertIn("--picture http://10.10.0.254/pxe/inject/theme/bg-800x600.png", d["preview"][plat])
        self.assertEqual(self.S.load()["menu"]["theme"]["style"], "grafico")

    def test_18_api_tema_sempre_completo(self):
        """Anche da una config vecchia (senza style/resolution) la GUI riceve valori utilizzabili."""
        cfg = self.S.load()
        cfg["menu"]["theme"] = {"bg": "#101010"}       # come una config salvata prima di questa versione
        self.S.save(cfg)
        full = self.client.get("/api/menu").get_json()
        d = full["settings"]
        self.assertEqual(d["theme"]["style"], "testo")
        self.assertEqual(d["theme"]["resolution"], "1024x768")
        self.assertEqual(d["theme"]["bg"], "#101010")
        self.assertEqual(d["theme_styles"], ["testo", "grafico", "compatibile"])
        self.assertEqual(d["theme_resolutions"], ["1024x768", "800x600", "640x480"])
        self.assertNotIn("--picture", full["preview"]["efi"])   # stile testo: nessuno sfondo

    # ------------------------------------------------- piu' risposte per ISO, scelta al boot (sezione 17)
    SLUG_WIN = _slug("Windows 11 Pro 24H2")

    def collega(self, slug=None, answers=(), default=None, manual=True):
        """Collega le risposte alla ISO scrivendo nel catalogo e ritorna la voce come la legge il boot."""
        slug = slug or self.SLUG_WIN
        cat = self.catalog.load()
        e = cat["isos"][slug]
        e["answers"] = list(answers)
        e["answer_id"] = default if default is not None else (list(answers)[0] if answers else None)
        e["answer_manual"] = manual
        self.catalog.save(cat)
        return self.catalog.get(slug)

    def script(self, slug=None, platform="efi", **kw):
        from pixio.services import ipxe_menu
        testo, _w = ipxe_menu.entry_script(slug or self.SLUG_WIN, platform, **kw)
        return testo

    def url_preseed(self, aid):
        return f"http://10.10.0.254/answers/{aid}/preseed.cfg"

    def tearDown(self):
        if self.SLUG_WIN in (self.catalog.load()["isos"] or {}):
            self.collega(answers=[], default=None)      # ogni prova riparte da una ISO senza risposte

    def test_20_migrazione_implicita_da_answer_id(self):
        """Una ISO salvata prima della sezione 17 ha solo answer_id: vale come elenco di una sola risposta."""
        cat = self.catalog.load()
        e = cat["isos"][self.SLUG_WIN]
        e.pop("answers", None)
        e.pop("answer_manual", None)
        e["answer_id"] = "aula-base"
        self.catalog.save(cat)
        self.assertEqual(self.catalog.answers_of(cat["isos"][self.SLUG_WIN]), ["aula-base"])
        d = self.catalog.get(self.SLUG_WIN)
        self.assertEqual(d["answers"], ["aula-base"])
        self.assertEqual(d["answer_id"], "aula-base")
        self.assertEqual(d["answer_name"], "Aula base")
        self.assertEqual([i["id"] for i in d["answers_info"]], ["aula-base"])
        self.assertEqual(d["answers_info"][0]["kind"], "debian")
        self.assertTrue(d["answer_manual"])
        # e continua a partire con quella risposta, come prima
        self.assertIn(self.url_preseed("aula-base"), self.script(answer="aula-base"))
        # PATCH del solo answer_id (GUI vecchia o script): collega la risposta senza toccare altro
        r = self.client.patch(f"/api/catalog/{self.SLUG_WIN}", json={"answer_id": "senza-domande"}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["answers"], ["senza-domande", "aula-base"])
        self.assertEqual(r.get_json()["answer_id"], "senza-domande")
        # answer_id vuoto da solo = nessuna installazione automatica, come faceva la GUI di prima
        r = self.client.patch(f"/api/catalog/{self.SLUG_WIN}", json={"answer_id": None}, headers=self.h)
        self.assertEqual(r.get_json()["answers"], [])
        self.assertIsNone(r.get_json()["answer_id"])

    def test_21_api_patch_validazione(self):
        for bad in ({"answers": ["mai-esistita"]}, {"answers": "aula-base"}, {"answers": ["aula-base"] * 9},
                    {"answers": ["aula-base", "senza-domande"], "answer_id": "aula-con-office"},
                    {"answer_id": "mai-esistita"}):
            r = self.client.patch(f"/api/catalog/{self.SLUG_WIN}", json=bad, headers=self.h)
            self.assertEqual(r.status_code, 400, (bad, r.get_json()))
            self.assertIn("error", r.get_json())
        r = self.client.patch(f"/api/catalog/{self.SLUG_WIN}",
                              json={"answers": ["aula-base", "senza-domande"], "answer_id": "senza-domande",
                                    "answer_manual": False}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d["answers"], ["aula-base", "senza-domande"])
        self.assertEqual(d["answer_id"], "senza-domande")
        self.assertFalse(d["answer_manual"])
        self.assertEqual([i["name"] for i in d["answers_info"]], ["Aula base", "Senza domande"])
        # doppioni tolti, ordine mantenuto
        r = self.client.patch(f"/api/catalog/{self.SLUG_WIN}",
                              json={"answers": ["senza-domande", "aula-base", "senza-domande"]}, headers=self.h)
        self.assertEqual(r.get_json()["answers"], ["senza-domande", "aula-base"])
        self.assertEqual(r.get_json()["answer_id"], "senza-domande")     # la predefinita resta valida

    def test_22_menu_di_scelta_con_due_risposte(self):
        """Due risposte e avvio a mano: lo script servito al client e' il menu di scelta."""
        self.collega(answers=["aula-base", "aula-con-office"], default="aula-con-office")
        script = self.script()
        righe = script.splitlines()
        self.assertEqual(righe[0], "#!ipxe")
        chiavi = _items(righe)
        self.assertEqual(chiavi, ["ans-1", "ans-2", "manuale"])
        self.assertIn("item ans-1    Aula base", script)
        self.assertIn("item ans-2    Aula con Office (predefinita)", script)
        self.assertIn("item manuale    Installazione guidata a mano", script)
        self.assertIn("choose --default ans-2 --timeout 10000 sel || goto ans-2", righe)
        self.assertIn("goto ${sel} || goto failed", righe)
        base = f"chain --autofree http://10.10.0.254/boot/{self.SLUG_WIN}.ipxe?platform=efi"
        self.assertIn(f"{base}&answer=aula-base || goto failed", righe)
        self.assertIn(f"{base}&answer=aula-con-office || goto failed", righe)
        self.assertIn(f"{base}&answer= || goto failed", righe)
        self.assertIn(":failed", righe)
        self.assertIn("echo Avvio fallito. Torno al menu principale.", righe)
        # nessuna ricetta qui dentro: lo script vero arriva con la seconda richiesta
        self.assertNotIn("kernel ", script)
        # sintassi: un comando per riga, label valide e univoche, ogni item/goto punta a una label esistente
        label = [l[1:] for l in righe if l.startswith(":")]
        self.assertEqual(len(label), len(set(label)))
        for l in righe:
            if l.startswith(":"):
                self.assertRegex(l, LABEL_RE, l)
                continue
            self.assertNotIn(";", l, l)
        for l in righe:
            if l.startswith("item ") and not l.startswith("item --gap"):
                self.assertIn(l.split()[1], label, l)
            if " || goto " in l:
                self.assertIn(l.rsplit("goto ", 1)[1].strip(), label, l)
            if l.startswith("goto ") and "${sel}" not in l:
                self.assertIn(l.split()[1], label, l)

    def test_23_uefi_e_bios_uguali(self):
        """Il menu di scelta e' lo stesso in UEFI e in BIOS: cambia solo la piattaforma negli URL."""
        self.collega(answers=["aula-base", "aula-con-office"])
        efi = self.script(platform="efi")
        bios = self.script(platform="bios")
        self.assertNotEqual(efi, bios)
        self.assertEqual(efi.replace("platform=efi", "platform=X").replace("(efi)", "(X)"),
                         bios.replace("platform=bios", "platform=X").replace("(bios)", "(X)"))

    def test_24_meno_di_due_voci_nessun_menu(self):
        """Una sola installazione disponibile: si avvia subito, esattamente come prima della sezione 17."""
        self.collega(answers=["aula-base"], manual=False)
        script = self.script()
        self.assertNotIn("item ans-1", script)
        self.assertIn(self.url_preseed("aula-base"), script)
        self.assertIn("auto=true priority=critical", script)
        self.collega(answers=[])
        script = self.script()
        self.assertNotIn("item ans-1", script)
        self.assertNotIn("/answers/", script)
        self.assertIn("kernel ", script)

    def test_25_parametro_answer(self):
        """?answer= sceglie la risposta; vuoto = installazione a mano; non valido = si usa la predefinita."""
        self.collega(answers=["aula-base", "senza-domande"], default="aula-base")
        self.assertIn(self.url_preseed("senza-domande"), self.script(answer="senza-domande"))
        self.assertIn(self.url_preseed("aula-base"), self.script(answer="aula-base"))
        a_mano = self.script(answer="")
        self.assertNotIn("/answers/", a_mano)
        self.assertIn("kernel ", a_mano)
        for cattivo in ("mai-esistita", "aula-con-office", "../../etc/passwd", "a b"):
            testo = self.script(answer=cattivo)      # non collegata o inventata: parte la predefinita
            self.assertIn(self.url_preseed("aula-base"), testo, cattivo)
            self.assertNotIn(cattivo, testo, cattivo)

    def test_26_risposta_cancellata(self):
        """Cancellare una risposta non deve rompere la ISO: voce saltata, predefinita alla prima rimasta."""
        a = self.answers.create({"name": "Da buttare", "kind": "debian", "content": "# usa e getta\n"})
        self.collega(answers=["aula-base", a["id"]], default=a["id"], manual=False)
        self.assertEqual([s for s in self.answers.get(a["id"])["used_by"]], [self.SLUG_WIN])
        self.answers.delete(a["id"])
        d = self.catalog.get(self.SLUG_WIN)
        self.assertEqual(d["answers"], ["aula-base"])
        self.assertEqual(d["answer_id"], "aula-base")           # era la predefinita: si passa alla prima
        script = self.script()
        self.assertNotIn(a["id"], script)
        self.assertIn(self.url_preseed("aula-base"), script)    # una voce sola: niente menu, parte e basta
        # id rimasto appeso nel catalogo (risposta sparita da fuori): voce saltata, il resto funziona
        self.collega(answers=["sparita", "aula-base", "senza-domande"], default="sparita")
        d = self.catalog.get(self.SLUG_WIN)
        self.assertEqual(d["answers"], ["aula-base", "senza-domande"])
        self.assertEqual(d["answer_id"], "aula-base")
        script = self.script()
        self.assertNotIn("sparita", script)
        self.assertIn("choose --default ans-1", script)
        self.assertEqual(_items(script.splitlines()), ["ans-1", "ans-2", "manuale"])

    def test_27_timeout_della_scelta(self):
        from pixio.services import ipxe_menu
        self.assertEqual(ipxe_menu.answer_timeout({}), 10)
        self.assertEqual(ipxe_menu.answer_timeout({"answer_timeout": "boh"}), 10)
        self.assertEqual(ipxe_menu.answer_timeout({"answer_timeout": 999}), 120)
        self.assertEqual(ipxe_menu.answer_timeout({"answer_timeout": -3}), 0)
        for bad in ({"answer_timeout": 121}, {"answer_timeout": -1}, {"answer_timeout": "subito"}):
            r = self.client.put("/api/menu", json={"settings": bad}, headers=self.h)
            self.assertEqual(r.status_code, 400, bad)
        r = self.client.put("/api/menu", json={"settings": {"answer_timeout": 0}}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["settings"]["answer_timeout"], 0)
        self.collega(answers=["aula-base", "senza-domande"])
        script = self.script()
        self.assertIn("choose --default ans-1 sel || goto ans-1", script.splitlines())
        self.assertNotIn("--timeout", script)
        self.client.put("/api/menu", json={"settings": {"answer_timeout": 25}}, headers=self.h)
        self.assertIn("choose --default ans-1 --timeout 25000 sel || goto ans-1", self.script().splitlines())
        self.assertEqual(self.client.get("/api/menu").get_json()["settings"]["answer_timeout"], 25)
        self.client.put("/api/menu", json={"settings": {"answer_timeout": 10}}, headers=self.h)

    def test_28_boot_ipxe_come_un_client_pxe(self):
        """Prova end-to-end con il test client: e' quello che scarica davvero il PC in avvio da rete."""
        anon = self.app.test_client()          # nessuna sessione: il client PXE non fa login
        self.collega(answers=["aula-base", "senza-domande"], default="senza-domande")
        for plat in ("efi", "bios"):
            r = anon.get(f"/boot/{self.SLUG_WIN}.ipxe?platform={plat}&mac=00-11-22-33-44-55")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.mimetype.startswith("text/plain"), r.mimetype)
            script = r.get_data(as_text=True)
            self.assertEqual(_items(script.splitlines()), ["ans-1", "ans-2", "manuale"])
            self.assertIn("choose --default ans-2 --timeout 10000 sel || goto ans-2", script)
            for aid in ("aula-base", "senza-domande"):
                self.assertIn(f"/boot/{self.SLUG_WIN}.ipxe?platform={plat}&answer={aid}&mac=00-11-22-33-44-55",
                              script)
            self.assertIn(f"?platform={plat}&answer=&mac=00-11-22-33-44-55", script)
            # seconda richiesta: lo script vero con la risposta scelta
            r = anon.get(f"/boot/{self.SLUG_WIN}.ipxe?platform={plat}&answer=aula-base&mac=00-11-22-33-44-55")
            script = r.get_data(as_text=True)
            self.assertIn(self.url_preseed("aula-base"), script)
            self.assertNotIn("item ans-1", script)
            self.assertIn("kernel ", script)
            self.assertNotIn(self.url_preseed("senza-domande"), script)
            # installazione a mano: nessuna risposta sulla riga di comando
            script = anon.get(f"/boot/{self.SLUG_WIN}.ipxe?platform={plat}&answer=").get_data(as_text=True)
            self.assertNotIn("/answers/", script)
            self.assertIn("kernel ", script)
            # parametro inventato: parte la predefinita
            script = anon.get(f"/boot/{self.SLUG_WIN}.ipxe?platform={plat}&answer=zzz").get_data(as_text=True)
            self.assertIn(self.url_preseed("senza-domande"), script)

    def test_29_anteprima_e_menu_principale_invariati(self):
        """Il menu principale chiama sempre /boot/<slug>.ipxe senza answer: la scelta la fa lo script della voce."""
        self.collega(answers=["aula-base", "senza-domande"])
        script = self.menu(submenus="never", default="local", timeout=30)
        self.assertIn(f"chain --autofree http://10.10.0.254/boot/{self.SLUG_WIN}.ipxe?platform=efi&mac= || goto failed",
                      script.splitlines())
        self.assertNotIn("answer=", script)
        d = self.client.get(f"/api/catalog/{self.SLUG_WIN}").get_json()
        for plat in ("efi", "bios"):     # l'anteprima mostra quello che riceve il client: il menu di scelta
            self.assertIn("item ans-1", d["recipe_preview"][plat])


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
