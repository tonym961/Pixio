"""Test della risposta Windows generata all'avvio per la ISO che sta partendo (docs/API.md, sezione 20).

Esecuzione: cd /opt/pixio && python3 -m unittest tests.test_risposta_boot
Il file statico della risposta resta quello che è: qui si controlla che la ricetta punti
all'indirizzo dinamico, che l'XML servito da lì sia generato con le edizioni di quella immagine e
che il file salvato non venga toccato.
I percorsi di config vengono reindirizzati in una cartella temporanea in setUpClass e ripristinati
alla fine, così il modulo convive con gli altri test nella stessa discovery.
"""
import json
import os
import shutil
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

IP = "10.10.0.254"

# Le due edizioni di una ISO Windows 11 Enterprise LTSC 2024: "Windows 11 Pro" qui dentro non c'è.
EDIZIONI_LTSC = [
    {"index": 1, "name": "Windows 11 Enterprise LTSC 2024", "display_name": "Windows 11 Enterprise LTSC"},
    {"index": 2, "name": "Windows 11 Enterprise N LTSC 2024", "display_name": "Windows 11 Enterprise N LTSC"},
]
# Una ISO con una sola immagine: l'intenzione era installare quella, e si installa quella.
EDIZIONI_UNA = [{"index": 1, "name": "Windows 10 Enterprise LTSC 2019",
                 "display_name": "Windows 10 Enterprise LTSC 2019"}]


def _fake_call(*args, stdin_text=None, timeout=180):
    if args and args[0] == "list-mounts":
        return {"iso": {}, "sources": {}}
    return {"ok": True}


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


def iso_finta(slug="ltsc", nome="Windows 11 Enterprise LTSC 2024", edizioni=None, answers=None):
    """Voce di catalogo di una ISO Windows con la cache delle edizioni già scritta."""
    e = {"slug": slug, "source": "local", "rel_path": slug + ".iso", "file": slug + ".iso",
         "name": nome, "enabled": True, "group": "Windows", "order": 10, "type": "windows",
         "size": 4238329309, "mtime": 1725600000,
         "path": os.path.join(C.LIBRARY_DIR, slug + ".iso"),
         "detect": {"type": "windows", "files": {"bootwim": "sources/boot.wim", "bcd": "boot/bcd",
                                                 "bootsdi": "boot/boot.sdi",
                                                 "install": "sources/install.wim"},
                    "editions": [], "images": []},
         "editions": {"images": list(EDIZIONI_LTSC if edizioni is None else edizioni),
                      "file": "sources/install.wim", "error": "",
                      "updated": "2026-01-01T00:00:00", "size": 4238329309, "mtime": 1725600000}}
    if answers:
        e["answers"] = list(answers)
        e["answer_id"] = answers[0]
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


def leggi(percorso):
    with open(percorso, encoding="utf-8") as f:
        return f.read()


def install_from(xml):
    """(chiave, valore) del MetaData di InstallFrom, oppure None se non c'è."""
    dom = minidom.parseString(xml)
    nodi = dom.getElementsByTagName("InstallFrom")
    if not nodi:
        return None
    md = nodi[0].getElementsByTagName("MetaData")[0]

    def testo(tag):
        el = md.getElementsByTagName(tag)[0]
        return "".join(n.data for n in el.childNodes if n.nodeType == n.TEXT_NODE).strip()
    return testo("Key"), testo("Value")


class Base(unittest.TestCase):
    """Cartelle temporanee, un profilo LTSC che chiede "Windows 11 Pro" e la risposta che genera."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pixio-rispboot-")
        cls.saved = {k: getattr(C, k) for k in OVERRIDE_KEYS}
        percorsi(cls.tmp)
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
        for f in (C.CATALOG_FILE, C.ANSWERS_FILE, C.WINPROFILES_FILE):
            if os.path.exists(f):
                os.unlink(f)
        shutil.rmtree(C.ANSWERS_DIR, ignore_errors=True)
        os.makedirs(C.ANSWERS_DIR, exist_ok=True)
        from pixio.services import answers, winprofile
        self.answers = answers
        self.WP = winprofile

    def prepara(self, edizioni=None, slug="ltsc", nome="Windows 11 Enterprise LTSC 2024"):
        """Profilo + risposta generata + ISO che la usa. Ritorna (profilo, risposta, voce ISO)."""
        prof = self.WP.create({"name": "Windows11LTSC", "settings": base_settings(
            target="11-ltsc", edition_index="Windows 11 Pro")})
        # com'è nata la risposta dell'utente: generata senza sapere su quale ISO sarebbe finita
        res = self.WP.save_as_answer(prof["id"], server_ip=IP)
        risposta = self.answers.get(res["answer_id"])
        e = iso_finta(slug=slug, nome=nome, edizioni=edizioni, answers=[risposta["id"]])
        scrivi_catalogo(e)
        return prof, risposta, e


# ---------------------------------------------------------------- scelta dell'indirizzo

class WinpeFilesTest(Base):
    def test_con_slug_indirizzo_dinamico(self):
        """Risposta nata da un profilo: la ricetta punta all'XML generato per questa ISO."""
        _, risposta, _ = self.prepara()
        self.assertEqual(self.answers.winpe_files(risposta, IP, "ltsc"),
                         [("autounattend.xml",
                           f"http://{IP}/boot/answer/ltsc/windows11ltsc/autounattend.xml")])

    def test_senza_slug_file_statico(self):
        """Chiamata come prima (due argomenti): niente cambia."""
        _, risposta, _ = self.prepara()
        self.assertEqual(self.answers.winpe_files(risposta, IP),
                         [("autounattend.xml", f"http://{IP}/answers/windows11ltsc/autounattend.xml")])

    def test_risposta_scritta_a_mano_file_statico(self):
        """Nessun profilo dietro: non c'è niente da rigenerare, si serve quello che ha scritto il tecnico."""
        a = self.answers.create({"name": "Aula 3 a mano", "kind": "windows",
                                 "content": "<unattend/>\n"})
        self.assertEqual(self.answers.winpe_files(a, IP, "ltsc"),
                         [("autounattend.xml", f"http://{IP}/answers/aula-3-a-mano/autounattend.xml")])
        self.assertIsNone(self.WP.profile_for_answer(a))

    def test_profilo_cancellato_file_statico(self):
        prof, risposta, _ = self.prepara()
        self.WP.delete(prof["id"])
        risposta = self.answers.get(risposta["id"])
        self.assertEqual(self.answers.winpe_files(risposta, IP, "ltsc"),
                         [("autounattend.xml", f"http://{IP}/answers/windows11ltsc/autounattend.xml")])

    def test_risposta_generica_file_statico(self):
        """Una risposta 'generic' con dentro un autounattend.xml non nasce da un profilo."""
        a = self.answers.create({"name": "Extra", "kind": "generic", "filename": "autounattend.xml",
                                 "content": "<unattend/>\n"})
        self.assertEqual(self.answers.winpe_files(a, IP, "ltsc"),
                         [("autounattend.xml", f"http://{IP}/answers/extra/autounattend.xml")])

    def test_slug_non_valido_ripiega_sul_file(self):
        """Uno slug strano non deve lasciare il PC senza file di risposta."""
        _, risposta, _ = self.prepara()
        self.assertEqual(self.answers.winpe_files(risposta, IP, "../etc"),
                         [("autounattend.xml", f"http://{IP}/answers/windows11ltsc/autounattend.xml")])
        with self.assertRaises(ValueError):
            self.answers.boot_url(IP, "../etc", "windows11ltsc")

    def test_legame_con_id_uguale(self):
        """Risposte create prima del campo `profile`: vale l'id uguale a quello del profilo."""
        self.WP.create({"name": "Windows11LTSC", "settings": base_settings(target="11-ltsc")})
        with open(C.ANSWERS_FILE, "w", encoding="utf-8") as f:
            json.dump({"answers": {"windows11ltsc": {"name": "Un nome qualunque", "kind": "windows",
                                                     "main_file": "autounattend.xml"}}}, f)
        a = self.answers.get("windows11ltsc")
        self.assertEqual(self.WP.profile_for_answer(a)["id"], "windows11ltsc")
        self.assertEqual(self.answers.winpe_files(a, IP, "ltsc")[0][1],
                         f"http://{IP}/boot/answer/ltsc/windows11ltsc/autounattend.xml")


# ---------------------------------------------------------------- la ricetta di boot

class RicettaTest(Base):
    def test_riga_initrd_della_ricetta(self):
        from pixio.services import recipes
        _, risposta, e = self.prepara()
        args, files = recipes.answer_for(e, IP, risposta["id"])
        self.assertEqual(args, "")
        self.assertEqual(files, [("autounattend.xml",
                                  f"http://{IP}/boot/answer/ltsc/windows11ltsc/autounattend.xml")])
        lines, _ = recipes.render(e, IP, "bios", answer=risposta["id"])
        riga = f"initrd http://{IP}/boot/answer/ltsc/windows11ltsc/autounattend.xml autounattend.xml"
        self.assertIn(riga, lines)
        self.assertFalse([l for l in lines if "/answers/windows11ltsc/" in l])


# ---------------------------------------------------------------- salvataggio del profilo

class SalvataggioTest(Base):
    """Pixio non deve lasciar salvare un profilo che genera un file di risposta che non funziona.

    L'edizione si confronta con le ISO a cui è collegata la risposta generata dal profilo: è
    l'unico momento, prima dell'avvio del PC, in cui si sa davvero cosa c'è dentro l'immagine."""

    def test_edizione_inesistente_blocca_il_salvataggio(self):
        prof, _, _ = self.prepara()
        with self.assertRaises(ValueError) as ctx:
            self.WP.update(prof["id"], {"settings": {"edition_index": "Windows 11 Pro"}})
        msg = str(ctx.exception)
        self.assertIn("Windows 11 Pro", msg)
        self.assertIn("Windows 11 Enterprise LTSC 2024", msg)   # l'elenco di quelle buone
        # il profilo salvato non è stato toccato
        self.assertEqual(self.WP.get(prof["id"])["settings"]["edition_index"], "Windows 11 Pro")

    def test_edizione_presente_si_salva(self):
        prof, _, _ = self.prepara()
        agg = self.WP.update(prof["id"],
                             {"settings": {"edition_index": "Windows 11 Enterprise N LTSC 2024"}})
        self.assertEqual(agg["settings"]["edition_index"], "Windows 11 Enterprise N LTSC 2024")
        # anche l'indice va bene
        self.assertEqual(self.WP.update(prof["id"], {"settings": {"edition_index": "2"}})
                         ["settings"]["edition_index"], "2")

    def test_profilo_senza_iso_abbinata_si_salva_come_prima(self):
        """Senza sapere su quale immagine finirà non si può dire che un valore sia sbagliato."""
        p = self.WP.create({"name": "Nuovo", "settings": base_settings(
            target="11-ltsc", edition_index="Windows 11 Pro")})
        self.assertEqual(self.WP.update(p["id"], {"note": "x"})["note"], "x")
        self.assertEqual(self.WP.update(p["id"], {"settings": {"edition_index": "Windows 11 Pro"}})
                         ["settings"]["edition_index"], "Windows 11 Pro")

    def test_altre_modifiche_bloccate_finche_l_edizione_e_sbagliata(self):
        """Il profilo dell'utente resta com'è finché l'edizione non viene corretta: è il punto,
        non un effetto collaterale. Le modifiche che non toccano le impostazioni passano."""
        prof, _, _ = self.prepara()
        with self.assertRaises(ValueError):
            self.WP.update(prof["id"], {"settings": {"timezone": "W. Europe Standard Time"}})
        self.assertEqual(self.WP.update(prof["id"], {"note": "da correggere"})["note"],
                         "da correggere")


# ---------------------------------------------------------------- l'XML servito al boot

class EndpointTest(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from pixio import create_app
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    def _xml(self, slug, answer_id, atteso=200):
        r = self.client.get(f"/boot/answer/{slug}/{answer_id}/autounattend.xml")
        self.assertEqual(r.status_code, atteso, r.data[:200])
        return r.get_data(as_text=True)

    def test_edizione_inesistente_diventa_quella_giusta(self):
        """Due edizioni: si scrive quella coerente col profilo, non si omette InstallFrom.

        È il guasto del 10 settembre 2026: senza InstallFrom il setup apre "Selezione immagine" e
        aspetta che qualcuno prema Avanti, e su un PC avviato dalla rete non c'è nessuno."""
        _, risposta, _ = self.prepara()
        xml = self._xml("ltsc", risposta["id"])
        self.assertNotIn("Windows 11 Pro", xml)
        self.assertEqual(install_from(xml), ("/IMAGE/NAME", "Windows 11 Enterprise LTSC 2024"))
        self.assertIn("<unattend", xml)

    def test_una_sola_edizione_si_installa_quella(self):
        _, risposta, _ = self.prepara(edizioni=EDIZIONI_UNA, slug="ltsc2019",
                                      nome="Windows 10 Enterprise LTSC 2019")
        xml = self._xml("ltsc2019", risposta["id"])
        self.assertEqual(install_from(xml), ("/IMAGE/NAME", "Windows 10 Enterprise LTSC 2019"))
        self.assertNotIn("Windows 11 Pro", xml)

    def test_edizione_giusta_resta(self):
        _, risposta, _ = self.prepara()
        self.WP.update("windows11ltsc",
                       {"settings": {"edition_index": "Windows 11 Enterprise N LTSC 2024"}})
        xml = self._xml("ltsc", risposta["id"])
        self.assertEqual(install_from(xml), ("/IMAGE/NAME", "Windows 11 Enterprise N LTSC 2024"))

    def test_avviso_nei_log_con_profilo_e_iso(self):
        _, risposta, _ = self.prepara()
        with self.assertLogs("pixio.boot", level="WARNING") as reg:
            self._xml("ltsc", risposta["id"])
        msg = "\n".join(r.getMessage() for r in reg.records)
        self.assertIn("Windows11LTSC", msg)                       # il profilo
        self.assertIn("Windows 11 Enterprise LTSC 2024", msg)     # la ISO e le sue edizioni
        self.assertIn("Windows 11 Pro", msg)                      # l'edizione che non c'è

    def test_il_file_statico_non_si_tocca(self):
        """Resta scaricabile e modificabile dalla GUI: nessuno lo riscrive alle spalle del tecnico."""
        _, risposta, _ = self.prepara()
        p = self.answers.file_path(risposta["id"], "autounattend.xml")
        prima, mtime = leggi(p), os.stat(p).st_mtime_ns
        self._xml("ltsc", risposta["id"])
        self.assertEqual(leggi(p), prima)
        self.assertEqual(os.stat(p).st_mtime_ns, mtime)
        self.assertIn("Windows 11 Pro", prima)
        r = self.client.get(f"/answers/{risposta['id']}/autounattend.xml")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Windows 11 Pro", r.get_data(as_text=True))

    def test_profilo_cancellato_serve_il_file_statico(self):
        prof, risposta, _ = self.prepara()
        self.WP.delete(prof["id"])
        xml = self._xml("ltsc", risposta["id"])
        self.assertIn("Windows 11 Pro", xml)
        self.assertEqual(xml, leggi(self.answers.file_path(risposta["id"], "autounattend.xml")))

    def test_iso_sconosciuta_genera_come_prima(self):
        """Senza edizioni da confrontare (ISO non nel catalogo) si genera quello che dice il profilo."""
        _, risposta, _ = self.prepara()
        xml = self._xml("mai-vista", risposta["id"])
        self.assertEqual(install_from(xml), ("/IMAGE/NAME", "Windows 11 Pro"))

    def test_risposta_inesistente_e_slug_non_valido(self):
        self.prepara()
        self._xml("ltsc", "mai-esistita", atteso=404)
        r = self.client.get("/boot/answer/Slug Strano/windows11ltsc/autounattend.xml")
        self.assertEqual(r.status_code, 400)

    def test_senza_sessione(self):
        """È un indirizzo di boot: il client PXE non ha nessuna sessione."""
        _, risposta, _ = self.prepara()
        anon = self.app.test_client()
        self.assertEqual(anon.get(f"/boot/answer/ltsc/{risposta['id']}/autounattend.xml").status_code, 200)

    def test_ricetta_ipxe_punta_all_indirizzo_nuovo(self):
        _, risposta, _ = self.prepara()
        r = self.client.get(f"/boot/ltsc.ipxe?platform=bios&answer={risposta['id']}")
        self.assertEqual(r.status_code, 200)
        testo = r.get_data(as_text=True)
        self.assertIn("/boot/answer/ltsc/windows11ltsc/autounattend.xml autounattend.xml", testo)
        self.assertNotIn("/answers/windows11ltsc/autounattend.xml", testo)


if __name__ == "__main__":
    unittest.main()
