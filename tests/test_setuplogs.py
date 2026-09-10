"""Test della raccolta automatica dei log del programma di installazione (docs/API.md, sezione 22).

Coprono le tre parti:
- lo script del WinPE (pixio/services/winpe.py): il sottoprogramma di copia c'è, non fa mai uscire lo
  script, non parte prima di setup.exe, sparisce quando la raccolta è spenta e i nomi arrivano dal server;
- il servizio (pixio/services/setuplogs.py): elenco, riepilogo, righe di errore, lettura in coda,
  eliminazione, sfoltimento e il rifiuto dei nomi e dei percorsi che tentano di uscire dalla cartella;
- l'API (/api/setuplogs) e la share [pxelog] resa dall'helper, che deve stare fuori da /srv/pixio/http.

Esecuzione: cd /opt/pixio && python3 -m unittest tests.test_setuplogs
I percorsi di config vengono reindirizzati in una directory temporanea in setUpClass e ripristinati alla
fine, così il modulo convive con gli altri test nella stessa discovery.
"""
import datetime
import importlib.machinery
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
                 "CLIENTS_FILE", "CATALOG_FILE", "DRIVERS_FILE", "LOG_DIR", "SRV_DIR", "LIBRARY_DIR", "CACHE_DIR",
                 "TFTP_DIR", "HTTP_DIR", "DRIVERS_DIR", "SOURCES_MOUNT_DIR", "SETUPLOGS_DIR")

CFG = {"network": {"server_ip": "10.10.0.254"},
       "windows": {"smb_export_enabled": True, "smb_user": "pxe", "smb_password": "segreta",
                   "setup_logs_enabled": True, "setup_logs_share_name": "pxelog"}}

RIEPILOGO = """Pixio: log del programma di installazione di Windows
immagine: Windows 11 Enterprise LTSC 2024
slug: it-it-windows-11-enterprise-ltsc-2024-x64-dvd-1e
avvio: 10/09/2026 09:14:32 (ora del server Pixio)
server: 10.10.0.254
client: 10.10.0.144
mac: 6c:2b:59:e8:75:09
pc: OptiPlex 7060
esito setup.exe: 1
"""

# Estratto vero del log del setup: la riga che spiega il guasto è quella con "Error".
SETUPACT = """2026-09-10 08:05:58, Info  SP  CSetupPlatform::PerformDiskProvisioning: Perform disk provisioning
2026-09-10 08:06:00, Info      CreatePartition: Successfully created partition on disk 0
2026-09-10 08:06:00, Error     CreatePartition: Disk 0 doesn't support creation of partitions of the specified type
2026-09-10 08:06:00, Error  SP  SPPerformDiskProvisioning: Failed to apply disk/storage configuration settings (0x80042565)
"""


def _fake_call(*args, stdin_text=None, timeout=180):
    if args and args[0] == "list-mounts":
        return {"iso": {}, "sources": {}}
    return {"ok": True}


def _load_helper():
    """L'helper non è un modulo importabile (non finisce in .py): lo si carica dal percorso."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "helper", "pixio-helper")
    return importlib.machinery.SourceFileLoader("pixio_helper_setuplogs", path).load_module()


class ScriptWinPeTest(unittest.TestCase):
    """Lo script che gira dentro il WinPE. Un errore qui si vede solo davanti a un PC in installazione."""

    def _script(self, cfg=None, **kw):
        from pixio.services import winpe
        return winpe.install_cmd("win11", cfg=cfg or CFG, iso={"slug": "win11", "name": "Windows 11 LTSC"}, **kw)

    def test_01_nome_cartella_dal_server(self):
        from pixio.services import winpe
        n = winpe.log_folder("win11", "10.10.0.144", datetime.datetime(2026, 9, 10, 9, 14, 32))
        self.assertEqual(n, "20260910-091432-10.10.0.144-win11")
        # senza IP il nome resta valido (e ordinabile): non deve mai venire fuori una cartella senza nome
        self.assertEqual(winpe.log_folder("win11", "", datetime.datetime(2026, 9, 10, 9, 14, 32)),
                         "20260910-091432-win11")
        # quello che arriva da fuori non entra nel nome così com'è
        self.assertEqual(winpe.log_folder("win11", "../../etc", datetime.datetime(2026, 1, 2, 3, 4, 5)),
                         "20260102-030405-....-win11")
        self.assertRegex(winpe.log_folder("win11", "10.10.0.144"), r"^\d{8}-\d{6}-")

    def test_02_testo_dentro_gli_echo_ripulito(self):
        from pixio.services import winpe
        # & % | ^ > < in un nome di ISO troncherebbero la riga o eseguirebbero altro
        self.assertEqual(winpe.cmd_safe("Windows & co. 100% <x>"), "Windows   co. 100   x")
        self.assertEqual(winpe.cmd_safe(""), "")

    def test_03_sottoprogramma_presente_e_completo(self):
        s = self._script(client_ip="10.10.0.144", when=datetime.datetime(2026, 9, 10, 9, 14, 32))
        self.assertIn(":pixio_log", s)
        self.assertIn("call :pixio_log", s)
        self.assertIn("net use P: \\\\10.10.0.254\\pxelog \"segreta\" /user:pxe", s)
        self.assertIn("set PIXIO_DEST=P:\\20260910-091432-10.10.0.144-win11", s)
        # i tre file che raccontano il guasto da soli
        self.assertIn("ipconfig /all > \"%PIXIO_DEST%\\rete.txt\"", s)
        self.assertIn("diskpart /s", s)
        self.assertIn("autounattend.xml \"%PIXIO_DEST%\\autounattend.xml\"", s)
        # le cartelle Panther, sul WinPE e sui dischi
        self.assertIn('call :pixio_copia "X:\\$WINDOWS.~BT\\Sources\\Panther" panther-winpe', s)
        self.assertIn("for %%u in (C D E) do call :pixio_copia", s)
        # il riepilogo scritto dal server
        self.assertIn(">\"%PIXIO_DEST%\\riepilogo.txt\" echo Pixio: log del programma", s)
        self.assertIn("echo client: 10.10.0.144", s)
        self.assertIn("echo esito setup.exe: %PIXIO_ESITO%", s)

    def test_03b_nome_giusto_anche_con_i_driver_della_share(self):
        """Regressione: il nome della cartella dei log veniva sovrascritto dall'ultima cartella driver.

        Trovato su Pixio vero: install.cmd diceva "set PIXIO_DEST=P:\\RAID_drivers". La copia sarebbe
        finita tutta nella stessa cartella, senza data, senza PC e sopra quella del tentativo prima."""
        from pixio.services import winpe, drivers
        orig = drivers.setup_load_folders
        drivers.setup_load_folders = lambda iso=None: ["RAID_drivers", "Chipset"]
        try:
            s = self._script(client_ip="10.10.0.144", when=datetime.datetime(2026, 9, 10, 9, 14, 32))
        finally:
            drivers.setup_load_folders = orig
        self.assertIn('for /r "S:\\drivers\\RAID_drivers"', s)          # i driver ci sono ancora
        self.assertIn("set PIXIO_DEST=P:\\20260910-091432-10.10.0.144-win11", s)
        dest = [l for l in s.split("\r\n") if l.startswith("set PIXIO_DEST=")]
        self.assertEqual(len(dest), 1, dest)
        self.assertRegex(dest[0], r"^set PIXIO_DEST=P:\\\d{8}-\d{6}-")

    def test_04_la_copia_viene_dopo_il_setup(self):
        s = self._script().replace("\r\n", "\n").split("\n")
        setup = next(i for i, l in enumerate(s) if l.startswith("S:\\iso\\win11\\setup.exe"))
        # l'esito va preso subito dopo, altrimenti %ERRORLEVEL% è quello di un altro comando
        self.assertEqual(s[setup + 1], "set PIXIO_ESITO=%ERRORLEVEL%")
        # nessuna chiamata alla raccolta prima che il setup sia partito
        self.assertNotIn("call :pixio_log", s[:setup])
        # e il sottoprogramma sta in coda, dove non ci si arriva per caduta
        self.assertGreater(s.index(":pixio_log"), s.index(":prompt"))

    def test_05_ogni_ramo_torna_al_chiamante(self):
        """Se lo script esce, Windows PE riavvia il PC: da :pixio_log si deve sempre tornare indietro."""
        righe = self._script().replace("\r\n", "\n").split("\n")
        coda = righe[righe.index(":pixio_log"):]
        for etichetta in (":pixio_log_ok", ":pixio_log_ko", ":pixio_copia"):
            self.assertIn(etichetta, coda)
        self.assertEqual(coda[-1], "")
        self.assertEqual(coda[-2], "goto :eof")
        # nessun "exit": chiuderebbe il prompt e farebbe riavviare il PC
        self.assertNotIn("exit", "\n".join(coda))

    def test_06_niente_attese_infinite(self):
        s = self._script()
        self.assertIn("if %PIXIO_TRY% GEQ 3 goto pixio_log_ko", s)      # tre tentativi e basta
        self.assertIn("/s /c /i /y /q", s)                              # xcopy va avanti sui file in uso
        self.assertIn("i log restano solo su questo PC", s)             # e lo dice a schermo

    def test_07_spenta_significa_assente(self):
        cfg = {"network": CFG["network"],
               "windows": dict(CFG["windows"], setup_logs_enabled=False)}
        s = self._script(cfg=cfg)
        self.assertNotIn("pixio_log", s)
        self.assertNotIn("pxelog", s)
        self.assertIn("setup.exe", s)          # il resto dello script non cambia
        # senza share [pxe] non c'è nemmeno la [pxelog]: niente raccolta
        cfg2 = {"network": CFG["network"], "windows": dict(CFG["windows"], smb_export_enabled=False)}
        self.assertNotIn("pixio_log", self._script(cfg=cfg2))

    def test_08_share_col_nome_scelto(self):
        cfg = {"network": CFG["network"], "windows": dict(CFG["windows"], setup_logs_share_name="diagnosi")}
        self.assertIn("\\\\10.10.0.254\\diagnosi", self._script(cfg=cfg))


class SetupLogsServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pixio-slog-test-")
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
        C.SETUPLOGS_DIR = os.path.join(C.SRV_DIR, "setuplogs")
        for d in (C.ETC_DIR, C.VAR_DIR, C.JOBS_DIR, C.UPLOAD_TMP_DIR, C.LOG_DIR, C.LIBRARY_DIR, C.CACHE_DIR,
                  C.TFTP_DIR, C.DRIVERS_DIR, C.SETUPLOGS_DIR):
            os.makedirs(d, exist_ok=True)
        cls._orig_call = privileged.call
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
        privileged.call = cls._orig_call
        for k, v in cls.saved.items():
            setattr(C, k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        for n in os.listdir(C.SETUPLOGS_DIR):
            p = os.path.join(C.SETUPLOGS_DIR, n)
            shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.unlink(p)

    # ---------------------------------------------------------------- finti depositi
    def _deposita(self, name="20260910-091432-10.10.0.144-win11", riepilogo=RIEPILOGO,
                  setupact=SETUPACT, setuperr=None, extra=None):
        folder = os.path.join(C.SETUPLOGS_DIR, name)
        os.makedirs(os.path.join(folder, "panther-winpe"), exist_ok=True)
        if riepilogo is not None:
            with open(os.path.join(folder, "riepilogo.txt"), "w", encoding="utf-8") as f:
                f.write(riepilogo)
        if setupact is not None:
            with open(os.path.join(folder, "panther-winpe", "setupact.log"), "w", encoding="utf-8") as f:
                f.write(setupact)
        if setuperr is not None:
            with open(os.path.join(folder, "panther-winpe", "setuperr.log"), "w", encoding="utf-8") as f:
                f.write(setuperr)
        for rel, body in (extra or {}).items():
            full = os.path.join(folder, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(body)
        return folder

    # ---------------------------------------------------------------- elenco
    def test_01_elenco_vuoto(self):
        from pixio.services import setuplogs
        self.assertEqual(setuplogs.list_folders(), [])

    def test_02_riepilogo_letto_e_riga_di_errore_trovata(self):
        from pixio.services import setuplogs
        self._deposita()
        d = setuplogs.list_folders()[0]
        self.assertEqual(d["name"], "20260910-091432-10.10.0.144-win11")
        self.assertEqual(d["image"], "Windows 11 Enterprise LTSC 2024")
        self.assertEqual(d["client"], "10.10.0.144")
        self.assertEqual(d["mac"], "6c:2b:59:e8:75:09")
        self.assertEqual(d["pc"], "OptiPlex 7060")
        self.assertEqual(d["esito"], "1")
        self.assertFalse(d["ok"])
        self.assertEqual(d["files"], 2)
        self.assertGreater(d["size"], 0)
        # è la riga con il codice del guasto quella che deve arrivare in GUI
        self.assertIn("0x80042565", d["errors"][-1])
        self.assertEqual(d["error_file"], "panther-winpe/setupact.log")

    def test_03_setuperr_ha_la_precedenza(self):
        from pixio.services import setuplogs
        self._deposita(setuperr="2026-09-10 08:06:00, Error  MOUPG  CDlpActionDiskManagement: Result = 0x80042565\n")
        d = setuplogs.list_folders()[0]
        self.assertEqual(d["error_file"], "panther-winpe/setuperr.log")
        self.assertIn("CDlpActionDiskManagement", d["errors"][-1])

    def test_04_installazione_riuscita_niente_errori(self):
        from pixio.services import setuplogs
        self._deposita(riepilogo=RIEPILOGO.replace("esito setup.exe: 1", "esito setup.exe: 0"),
                       setupact="2026-09-10 09:00:00, Info  SP  Finished disk provisioning\n")
        d = setuplogs.list_folders()[0]
        self.assertTrue(d["ok"])
        self.assertEqual(d["errors"], ["2026-09-10 09:00:00, Info  SP  Finished disk provisioning"])

    def test_05_cartella_senza_riepilogo(self):
        """Un deposito monco (share caduta a metà) non deve far sparire l'elenco."""
        from pixio.services import setuplogs
        self._deposita(riepilogo=None)
        d = setuplogs.list_folders()[0]
        self.assertFalse(d["has_summary"])
        self.assertEqual(d["image"], "")
        self.assertEqual(d["files"], 1)

    def test_06_ordine_dal_piu_recente(self):
        from pixio.services import setuplogs
        vecchio = self._deposita(name="20260901-070000-10.10.0.9-win10")
        os.utime(os.path.join(vecchio, "riepilogo.txt"), (1_600_000_000, 1_600_000_000))
        os.utime(os.path.join(vecchio, "panther-winpe", "setupact.log"), (1_600_000_000, 1_600_000_000))
        os.utime(vecchio, (1_600_000_000, 1_600_000_000))
        self._deposita()
        nomi = [d["name"] for d in setuplogs.list_folders()]
        self.assertEqual(nomi, ["20260910-091432-10.10.0.144-win11", "20260901-070000-10.10.0.9-win10"])

    # ---------------------------------------------------------------- lettura di un file
    def test_07_dettaglio_e_contenuto(self):
        from pixio.services import setuplogs
        self._deposita()
        d = setuplogs.detail("20260910-091432-10.10.0.144-win11")
        self.assertEqual(d["file"], "riepilogo.txt")            # senza indicazioni si apre il riepilogo
        self.assertIn("OptiPlex 7060", d["content"])
        self.assertEqual([f["name"] for f in d["file_list"]], ["panther-winpe/setupact.log", "riepilogo.txt"])
        self.assertTrue(all(f["text"] for f in d["file_list"]))
        d2 = setuplogs.detail("20260910-091432-10.10.0.144-win11", "panther-winpe/setupact.log")
        self.assertIn("0x80042565", d2["content"])
        self.assertFalse(d2["truncated"])

    def test_08_file_lungo_letto_in_coda(self):
        from pixio.services import setuplogs
        grande = ("x" * 199 + "\n") * 3000 + "ULTIMA RIGA\n"      # ~600 KB
        self._deposita(setupact=grande)
        d = setuplogs.detail("20260910-091432-10.10.0.144-win11", "panther-winpe/setupact.log")
        self.assertTrue(d["truncated"])
        self.assertLessEqual(len(d["content"]), setuplogs.TAIL_BYTES)
        self.assertTrue(d["content"].endswith("ULTIMA RIGA\n"))

    def test_09_utf16_come_lo_scrive_windows(self):
        from pixio.services import setuplogs
        folder = self._deposita()
        with open(os.path.join(folder, "panther-winpe", "setuperr.log"), "wb") as f:
            f.write("2026-09-10, Error  SP  disco non valido\n".encode("utf-16"))
        d = setuplogs.detail("20260910-091432-10.10.0.144-win11", "panther-winpe/setuperr.log")
        self.assertIn("disco non valido", d["content"])
        self.assertNotIn("\x00", d["content"])

    # ---------------------------------------------------------------- niente fiducia nei nomi
    def test_10_nomi_e_percorsi_che_tentano_di_uscire(self):
        from pixio.services import setuplogs
        self._deposita()
        buono = "20260910-091432-10.10.0.144-win11"
        for cattivo in ("..", "../etc", "/etc", "a/b", ".nascosta", "", "x" * 200):
            with self.assertRaises(ValueError, msg=cattivo):
                setuplogs.folder_path(cattivo)
        for rel in ("../riepilogo.txt", "/etc/passwd", "panther-winpe/../../x", "a/b/c/d/e/f/g/h"):
            with self.assertRaises(ValueError, msg=rel):
                setuplogs.file_path(buono, rel)
        with self.assertRaises(FileNotFoundError):
            setuplogs.folder_path("20260101-000000-mai-vista")
        with self.assertRaises(FileNotFoundError):
            setuplogs.file_path(buono, "non-c-e.log")

    def test_11_collegamenti_simbolici_ignorati(self):
        from pixio.services import setuplogs
        folder = self._deposita()
        os.symlink("/etc/passwd", os.path.join(folder, "passwd.txt"))
        os.symlink("/etc", os.path.join(C.SETUPLOGS_DIR, "20260101-000000-scorciatoia"))
        nomi = [d["name"] for d in setuplogs.list_folders()]
        self.assertEqual(nomi, ["20260910-091432-10.10.0.144-win11"])       # il link a /etc non è una cartella di log
        d = setuplogs.detail("20260910-091432-10.10.0.144-win11")
        self.assertNotIn("passwd.txt", [f["name"] for f in d["file_list"]])
        with self.assertRaises(ValueError):
            setuplogs.file_path("20260910-091432-10.10.0.144-win11", "passwd.txt")

    # ---------------------------------------------------------------- eliminazione e sfoltimento
    def test_12_eliminazione(self):
        from pixio.services import setuplogs
        self._deposita()
        self._deposita(name="20260910-100000-10.10.0.145-win11")
        setuplogs.delete("20260910-100000-10.10.0.145-win11")
        self.assertEqual([d["name"] for d in setuplogs.list_folders()], ["20260910-091432-10.10.0.144-win11"])
        self.assertEqual(setuplogs.delete_all(), 1)
        self.assertEqual(setuplogs.list_folders(), [])
        self.assertTrue(os.path.isdir(C.SETUPLOGS_DIR))       # la cartella della share resta

    def test_13_sfoltimento(self):
        from pixio.services import setuplogs
        for i in range(5):
            f = self._deposita(name=f"2026091{i}-090000-10.10.0.144-win11")
            t = 1_700_000_000 + i * 3600
            for root, _d, files in os.walk(f):
                for fn in files:
                    os.utime(os.path.join(root, fn), (t, t))
            os.utime(f, (t, t))
        self.assertEqual(setuplogs.prune(keep=2), 3)
        self.assertEqual([d["name"] for d in setuplogs.list_folders()],
                         ["20260914-090000-10.10.0.144-win11", "20260913-090000-10.10.0.144-win11"])
        self.assertEqual(setuplogs.prune(keep=2), 0)          # niente da togliere: non tocca più niente

    # ---------------------------------------------------------------- API
    def test_14_api_elenco_e_dettaglio(self):
        self._deposita()
        r = self.client.get("/api/setuplogs")
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(len(d["logs"]), 1)
        self.assertEqual(d["dir"], C.SETUPLOGS_DIR)
        self.assertIn("0x80042565", d["logs"][0]["errors"][-1])
        r = self.client.get("/api/setuplogs/20260910-091432-10.10.0.144-win11")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn("OptiPlex 7060", r.get_json()["content"])
        r = self.client.get("/api/setuplogs/20260910-091432-10.10.0.144-win11?file=panther-winpe/setupact.log")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn("0x80042565", r.get_json()["content"])

    def test_15_api_errori(self):
        r = self.client.get("/api/setuplogs/..%2f..%2fetc")
        self.assertIn(r.status_code, (400, 404), r.get_json())
        r = self.client.get("/api/setuplogs/20260101-000000-mai-vista")
        self.assertEqual(r.status_code, 404, r.get_json())
        self._deposita()
        r = self.client.get("/api/setuplogs/20260910-091432-10.10.0.144-win11?file=../../etc/passwd")
        self.assertEqual(r.status_code, 400, r.get_json())

    def test_16_api_eliminazione(self):
        self._deposita()
        self._deposita(name="20260910-100000-10.10.0.145-win11")
        r = self.client.delete("/api/setuplogs/20260910-100000-10.10.0.145-win11", headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        r = self.client.delete("/api/setuplogs", headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["deleted"], 1)
        self.assertEqual(self.client.get("/api/setuplogs").get_json()["logs"], [])

    def test_17_api_senza_sessione(self):
        c = self.app.test_client()          # client nuovo: nessun cookie di sessione
        self.assertEqual(c.get("/api/setuplogs").status_code, 401)
        self.assertEqual(c.delete("/api/setuplogs").status_code, 401)

    def test_18_stato_della_raccolta(self):
        from pixio import settings as S
        from pixio.services import setuplogs
        cfg = S.load()
        cfg["windows"]["smb_export_enabled"] = True
        S.save(cfg)
        self.assertTrue(setuplogs.enabled())
        self.assertEqual(setuplogs.share_unc(), f"\\\\{cfg['network']['server_ip']}\\pxelog")
        r = self.client.get("/api/setuplogs").get_json()
        self.assertTrue(r["enabled"])
        cfg["windows"]["setup_logs_enabled"] = False
        S.save(cfg)
        self.assertFalse(setuplogs.enabled())
        cfg["windows"]["setup_logs_enabled"] = True
        cfg["windows"]["smb_export_enabled"] = False
        S.save(cfg)
        self.assertFalse(setuplogs.enabled())      # senza la share [pxe] non c'è nemmeno la [pxelog]

    def test_19_impostazioni_salvabili_dalla_gui(self):
        r = self.client.put("/api/settings", json={"windows": {"smb_export_enabled": True, "setup_logs_enabled": False}},
                            headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        from pixio import settings as S
        self.assertFalse(S.load()["windows"]["setup_logs_enabled"])
        r = self.client.put("/api/settings", json={"windows": {"setup_logs_enabled": "forse"}}, headers=self.h)
        self.assertEqual(r.status_code, 400, r.get_json())
        r = self.client.put("/api/settings", json={"windows": {"setup_logs_keep": 0}}, headers=self.h)
        self.assertEqual(r.status_code, 400, r.get_json())


class ShareTest(unittest.TestCase):
    """La share dei log resa dall'helper: in scrittura, ma fuori dall'albero servito in sola lettura."""

    def test_01_share_dei_log(self):
        h = _load_helper()
        cfg = {"network": {"interface": "lo", "server_ip": "127.0.0.1", "dhcp_mode": "proxy"},
               "library": {"samba_share_enabled": False},
               "windows": {"smb_export_enabled": True, "smb_user": "pxe", "setup_logs_enabled": True}}
        smb = h.render_smb(cfg)
        self.assertIn("[pxelog]", smb)
        self.assertIn(f"path = {h.SETUPLOGS}", smb)
        self.assertIn("read only = no", smb)
        self.assertIn("valid users = pxe", smb)
        self.assertIn(f"force user = {h.SERVICE_USER}", smb)
        self.assertIn("guest ok = no", smb)
        # la cartella scrivibile NON deve stare dentro l'albero servito in sola lettura
        self.assertFalse(h.SETUPLOGS.startswith(h.HTTP_ROOT + "/"))
        # la share [pxe] resta in sola lettura
        pxe = smb.split("[pxe]", 1)[1].split("[pxelog]", 1)[0]
        self.assertIn("read only = yes", pxe)

    def test_02_share_assente_quando_spenta(self):
        h = _load_helper()
        base = {"network": {"interface": "lo", "server_ip": "127.0.0.1", "dhcp_mode": "proxy"},
                "library": {"samba_share_enabled": False}}
        smb = h.render_smb(dict(base, windows={"smb_export_enabled": True, "smb_user": "pxe",
                                               "setup_logs_enabled": False}))
        self.assertNotIn("[pxelog]", smb)
        smb = h.render_smb(dict(base, windows={"smb_export_enabled": False}))
        self.assertNotIn("[pxelog]", smb)
        self.assertNotIn("[pxe]", smb)

    def test_03_nome_della_share_validato(self):
        h = _load_helper()
        cfg = {"network": {"interface": "lo", "server_ip": "127.0.0.1", "dhcp_mode": "proxy"},
               "library": {"samba_share_enabled": False},
               "windows": {"smb_export_enabled": True, "smb_user": "pxe", "setup_logs_enabled": True,
                           "setup_logs_share_name": "log/../etc"}}
        with self.assertRaises(h.HelperError):
            h.render_smb(cfg)


if __name__ == "__main__":
    unittest.main()
