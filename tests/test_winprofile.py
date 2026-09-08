"""Test dei profili di personalizzazione Windows (docs/API.md, sezione 8).

Esecuzione: cd /opt/pixio && python3 -m unittest tests.test_winprofile
Non serve l'helper privilegiato né il servizio delle risposte reale: entrambi vengono sostituiti da finti.
"""
import os
import shutil
import sys
import tempfile
import types
import unittest
import xml.dom.minidom as minidom

os.environ["PIXIO_NO_BACKGROUND"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pixio import config as C  # noqa: E402
from pixio import privileged  # noqa: E402

TMP = tempfile.mkdtemp(prefix="pixio-win-test-")
# I moduli leggono C.<X> al momento della chiamata: basta ridefinire le costanti prima di usarli.
# Percorsi "nostri": vanno riapplicati in ApiTest, perché gli altri file di test ridefiniscono le
# stesse costanti quando vengono importati nello stesso processo (python3 -m unittest tests.*).
MIO_ETC = os.path.join(TMP, "etc")
MIO_VAR = os.path.join(TMP, "var")
# Percorsi assoluti nostri: gli altri file di test riscrivono C.VAR_DIR quando girano insieme,
# quindi non si può ricavare nulla da C.<X> al momento del test — si riparte sempre da qui.
MIO_WINPROFILES = os.path.join(MIO_VAR, "winprofiles.json")
MIO_ANSWERS_DIR = os.path.join(MIO_VAR, "answers-reali")
MIO_ANSWERS_FILE = os.path.join(MIO_VAR, "answers-reali.json")
C.ETC_DIR = MIO_ETC
C.CONFIG_FILE = os.path.join(MIO_ETC, "config.json")
C.SECRET_FILE = os.path.join(MIO_ETC, "secret")
C.VAR_DIR = MIO_VAR
C.JOBS_DIR = os.path.join(C.VAR_DIR, "jobs")
C.UPLOAD_TMP_DIR = os.path.join(C.VAR_DIR, "uploads")
C.CLIENTS_FILE = os.path.join(C.VAR_DIR, "clients.json")
C.CATALOG_FILE = os.path.join(C.VAR_DIR, "catalog.json")
C.WINPROFILES_FILE = MIO_WINPROFILES
C.ANSWERS_DIR = os.path.join(C.VAR_DIR, "answers")
C.ANSWERS_FILE = os.path.join(C.VAR_DIR, "answers.json")
C.LOG_DIR = os.path.join(TMP, "log")
C.SRV_DIR = os.path.join(TMP, "srv")
C.LIBRARY_DIR = os.path.join(C.SRV_DIR, "library")
C.CACHE_DIR = os.path.join(C.SRV_DIR, "cache")
C.TFTP_DIR = os.path.join(C.SRV_DIR, "tftp")
C.SOURCES_MOUNT_DIR = os.path.join(C.SRV_DIR, "sources")
for _d in (C.ETC_DIR, C.VAR_DIR, C.JOBS_DIR, C.UPLOAD_TMP_DIR, C.LOG_DIR, C.LIBRARY_DIR, C.CACHE_DIR, C.TFTP_DIR):
    os.makedirs(_d, exist_ok=True)


def fake_call(*args, stdin_text=None, timeout=180):
    """Finto helper privilegiato: nessuna rotta di questo file lo usa, ma se qualcosa lo chiamasse
    non deve provare a eseguire /usr/local/sbin/pixio-helper."""
    if args and args[0] == "list-mounts":
        return {"iso": {}, "sources": {}}
    return {"ok": True}


# Lo sostituiamo solo se nessun altro file di test lo ha già fatto: gli altri contano le chiamate
# ricevute e sovrascrivere il loro finto li farebbe fallire quando le suite girano insieme.
if getattr(privileged.call, "__module__", "") == "pixio.privileged":
    privileged.call = fake_call

from pixio.services import winprofile as WP  # noqa: E402

CFG_FINTA = {
    "network": {"server_ip": "10.10.0.254"},
    "windows": {"smb_export_enabled": True, "smb_user": "pxe", "smb_password": "segreta"},
}


def base_settings(**over):
    """Impostazioni minime valide, con le sovrascritture richieste (annidate con la notazione __)."""
    s = WP.defaults()
    s["admin_password"] = "PasswordDiProva1"
    for k, v in over.items():
        parti = k.split("__")
        d = s
        for p in parti[:-1]:
            d = d[p]
        d[parti[-1]] = v
    return s


def rendi(**over):
    """XML generato dalle impostazioni di prova, già letto con minidom."""
    xml = WP.render_autounattend({"name": "Prova", "settings": base_settings(**over)},
                                 "10.10.0.254", cfg=CFG_FINTA)
    return xml, minidom.parseString(xml)


def testo(nodo):
    return "".join(n.data for n in nodo.childNodes if n.nodeType == n.TEXT_NODE).strip()


def uno(dom, tag, dentro=None):
    """Primo elemento con quel nome (dentro un altro elemento, se indicato)."""
    base = dentro if dentro is not None else dom
    el = base.getElementsByTagName(tag)
    return el[0] if el else None


def passaggio(dom, nome):
    for s in dom.getElementsByTagName("settings"):
        if s.getAttribute("pass") == nome:
            return s
    return None


def componente(dom, passo, nome):
    sp = passaggio(dom, passo)
    if sp is None:
        return None
    for c in sp.getElementsByTagName("component"):
        if c.getAttribute("name") == nome:
            return c
    return None


# ---------------------------------------------------------------- validazione

class ValidazioneTest(unittest.TestCase):
    def test_predefiniti_italiani(self):
        d = WP.defaults()
        self.assertEqual(d["language"], "it-IT")
        self.assertEqual(d["input_locale"], "it-IT")
        self.assertEqual(d["timezone"], "W. Europe Standard Time")
        self.assertEqual(d["architecture"], "amd64")
        self.assertEqual(d["disk"]["mode"], "auto-uefi")
        # defaults() deve restituire una copia: modificarla non tocca WP.DEFAULTS
        d["disk"]["mode"] = "toccato"
        self.assertEqual(WP.DEFAULTS["disk"]["mode"], "auto-uefi")

    def test_normalizza(self):
        v = WP.validate(base_settings(product_key="vk7jg-nphtm-c97jm-9mpgt-3v66t",
                                      architecture="x64",
                                      join_domain__enabled=False,
                                      join_domain__domain="Azienda.Local"))
        self.assertEqual(v["product_key"], "VK7JG-NPHTM-C97JM-9MPGT-3V66T")
        self.assertEqual(v["architecture"], "amd64")
        # dominio disattivato: i campi vengono azzerati, non conservati
        self.assertEqual(v["join_domain"], {"enabled": False, "domain": "", "ou": "",
                                            "user": "", "password": ""})

    def test_autologon_conta(self):
        v = WP.validate(base_settings(autologon=True, autologon_count=0))
        self.assertEqual(v["autologon_count"], 9999)
        v = WP.validate(base_settings(autologon=False, autologon_count=5))
        self.assertEqual(v["autologon_count"], 0)

    def test_impostazioni_parziali(self):
        """Un profilo con due soli campi resta valido: il resto arriva dai predefiniti."""
        v = WP.validate({"computer_name": "PC01", "admin_user": "tec", "admin_password": "x"})
        self.assertEqual(v["timezone"], "W. Europe Standard Time")
        self.assertEqual(v["computer_name"], "PC01")

    def _ko(self, atteso, **over):
        with self.assertRaises(ValueError) as ctx:
            WP.validate(base_settings(**over))
        self.assertIn(atteso.lower(), str(ctx.exception).lower())

    def test_nome_computer(self):
        self._ko("nome computer troppo lungo", computer_name="PC-TROPPO-LUNGO-DAVVERO")
        self._ko("nome computer non valido", computer_name="PC 01")
        self._ko("nome computer non valido", computer_name="-pc01")
        self._ko("nome computer non valido", computer_name="pc01-")
        self._ko("nome computer non valido", computer_name="123456")
        self._ko("nome computer non valido", computer_name="pc<01>")
        # forme ammesse
        for buono in ("*", "PC-*", "PC01", "pc-01"):
            self.assertEqual(WP.validate(base_settings(computer_name=buono))["computer_name"], buono)

    def test_chiave_prodotto(self):
        self._ko("chiave di prodotto", product_key="1234-5678")
        self._ko("chiave di prodotto", product_key="VK7JG-NPHTM-C97JM-9MPGT")
        self._ko("chiave di prodotto", product_key="VK7JG NPHTM C97JM 9MPGT 3V66T!")
        self.assertEqual(WP.validate(base_settings(product_key=""))["product_key"], "")

    def test_password_obbligatoria(self):
        s = WP.defaults()          # admin_user impostato, password vuota
        with self.assertRaises(ValueError) as ctx:
            WP.validate(s)
        self.assertIn("password", str(ctx.exception).lower())
        # utente vuoto ma password presente: incoerente
        self._ko("nessun nome utente", admin_user="")
        # secondo utente senza password
        self._ko("indica la password", extra_user__name="ospite")

    def test_utente_non_valido(self):
        self._ko("non sono ammessi i caratteri", admin_user="dominio\\tecnico")
        self._ko("massimo 20 caratteri", admin_user="a" * 21)
        self._ko("stesso nome dell'amministratore", extra_user__name="AMMINISTRATORE",
                 extra_user__password="x")

    def test_dominio(self):
        self._ko("indica il dominio", join_domain__enabled=True)
        self._ko("indica l'utente", join_domain__enabled=True, join_domain__domain="azienda.local")
        self._ko("indica la password", join_domain__enabled=True,
                 join_domain__domain="azienda.local", join_domain__user="admjoin")
        self._ko("unità organizzativa", join_domain__enabled=True,
                 join_domain__domain="azienda.local", join_domain__user="admjoin",
                 join_domain__password="x", join_domain__ou="cartella qualsiasi")

    def test_disco(self):
        self._ko("modalità del disco", disk__mode="raid")
        self._ko("partizione di sistema efi", disk__efi_mb=10)
        self._ko("riservata microsoft", disk__msr_mb=999)
        self._ko("partizione di ripristino troppo piccola", disk__recovery_mb=100)
        # in modalità manuale le dimensioni EFI non vengono controllate
        v = WP.validate(base_settings(disk__mode="manuale", disk__efi_mb=1))
        self.assertEqual(v["disk"]["mode"], "manuale")

    def test_lingua_e_tastiera(self):
        self._ko("lingua non valida", language="italiano")
        self._ko("tastiera non valida", input_locale="tastiera italiana")
        self.assertEqual(WP.validate(base_settings(input_locale="0410:00000410"))["input_locale"],
                         "0410:00000410")
        self.assertEqual(WP.validate(base_settings(input_locale="it-IT;en-US"))["input_locale"],
                         "it-IT;en-US")

    def test_altri_campi(self):
        self._ko("fuso orario", timezone="Europe/Rome\nx")
        self._ko("architettura", architecture="arm64")
        self._ko("edizione", edition_index="Windows 11 <Pro>")
        self._ko("indice dell'edizione", edition_index="99")
        self._ko("schema di alimentazione", power_scheme="silenzioso")
        self._ko("nome del pacchetto", remove_apps=["app con spazi"])
        self._ko("comando troppo lungo", run_commands=["cmd /c echo " + "a" * 600])
        self._ko("troppi comandi", run_commands=["cmd /c echo %d" % i for i in range(41)])

    def test_apps_ripetute(self):
        v = WP.validate(base_settings(remove_apps=["Microsoft.BingNews", "Microsoft.BingNews"]))
        self.assertEqual(v["remove_apps"], ["Microsoft.BingNews"])


# ---------------------------------------------------------------- generatore XML

class XmlTest(unittest.TestCase):
    def test_uefi(self):
        xml, dom = rendi(edition_index="Windows 11 Pro", product_key="VK7JG-NPHTM-C97JM-9MPGT-3V66T",
                         computer_name="PC-*")
        self.assertTrue(xml.startswith('<?xml version="1.0" encoding="utf-8"?>'))
        self.assertEqual(dom.documentElement.tagName, "unattend")
        self.assertEqual(dom.documentElement.getAttribute("xmlns"),
                         "urn:schemas-microsoft-com:unattend")
        # i tre passaggi previsti dal contratto
        for p in ("windowsPE", "specialize", "oobeSystem"):
            self.assertIsNotNone(passaggio(dom, p), "manca il passaggio " + p)

        setup = componente(dom, "windowsPE", "Microsoft-Windows-Setup")
        self.assertIsNotNone(setup)
        # SetupUILanguage nel componente International-Core-WinPE
        intl = componente(dom, "windowsPE", "Microsoft-Windows-International-Core-WinPE")
        self.assertEqual(testo(uno(dom, "UILanguage", uno(dom, "SetupUILanguage", intl))), "it-IT")

        # disco GPT: ripristino, EFI, MSR, Windows che si estende
        tipi = [testo(uno(dom, "Type", p)) for p in setup.getElementsByTagName("CreatePartition")]
        self.assertEqual(tipi, ["Primary", "EFI", "MSR", "Primary"])
        ultima = setup.getElementsByTagName("CreatePartition")[-1]
        self.assertEqual(testo(uno(dom, "Extend", ultima)), "true")
        self.assertEqual(testo(uno(dom, "Format", setup.getElementsByTagName("ModifyPartition")[1])),
                         "FAT32")
        # l'immagine va sulla partizione Windows (la quarta)
        it = uno(dom, "InstallTo", setup)
        self.assertEqual(testo(uno(dom, "PartitionID", it)), "4")
        # InstallFrom con il nome dell'immagine
        md = uno(dom, "MetaData", setup)
        self.assertEqual(testo(uno(dom, "Key", md)), "/IMAGE/NAME")
        self.assertEqual(testo(uno(dom, "Value", md)), "Windows 11 Pro")
        # chiave di prodotto e EULA
        ud = uno(dom, "UserData", setup)
        self.assertEqual(testo(uno(dom, "Key", uno(dom, "ProductKey", ud))),
                         "VK7JG-NPHTM-C97JM-9MPGT-3V66T")
        self.assertEqual(testo(uno(dom, "AcceptEula", ud)), "true")

        # specialize: nome computer e fuso orario
        shell = componente(dom, "specialize", "Microsoft-Windows-Shell-Setup")
        self.assertEqual(testo(uno(dom, "ComputerName", shell)), "PC-*")
        self.assertEqual(testo(uno(dom, "TimeZone", shell)), "W. Europe Standard Time")

        # oobeSystem: OOBE saltato e utente locale amministratore
        oobe = componente(dom, "oobeSystem", "Microsoft-Windows-Shell-Setup")
        self.assertEqual(testo(uno(dom, "HideEULAPage", oobe)), "true")
        acc = uno(dom, "LocalAccount", oobe)
        self.assertEqual(testo(uno(dom, "Name", acc)), "amministratore")
        self.assertEqual(testo(uno(dom, "Group", acc)), "Administrators")
        self.assertEqual(testo(uno(dom, "Value", uno(dom, "Password", acc))), "PasswordDiProva1")

    def test_bios(self):
        xml, dom = rendi(disk__mode="auto-bios", edition_index="2")
        setup = componente(dom, "windowsPE", "Microsoft-Windows-Setup")
        parti = setup.getElementsByTagName("CreatePartition")
        self.assertEqual(len(parti), 2)
        self.assertEqual(testo(uno(dom, "Size", parti[0])), "500")
        self.assertEqual(testo(uno(dom, "Extend", parti[1])), "true")
        mod = setup.getElementsByTagName("ModifyPartition")
        self.assertEqual(testo(uno(dom, "Active", mod[0])), "true")
        self.assertEqual(testo(uno(dom, "Letter", mod[1])), "C")
        self.assertEqual(testo(uno(dom, "PartitionID", uno(dom, "InstallTo", setup))), "2")
        # edizione indicata come indice
        self.assertEqual(testo(uno(dom, "Key", uno(dom, "MetaData", setup))), "/IMAGE/INDEX")
        # nessuna partizione EFI in BIOS
        self.assertNotIn("<Type>EFI</Type>", xml)

    def test_disco_manuale(self):
        xml, dom = rendi(disk__mode="manuale")
        setup = componente(dom, "windowsPE", "Microsoft-Windows-Setup")
        self.assertIsNone(uno(dom, "DiskConfiguration", setup))
        self.assertIsNone(uno(dom, "InstallTo", setup))

    def test_bypass_requisiti(self):
        xml, dom = rendi(bypass_requirements=True)
        setup = componente(dom, "windowsPE", "Microsoft-Windows-Setup")
        comandi = [testo(uno(dom, "Path", c))
                   for c in setup.getElementsByTagName("RunSynchronousCommand")]
        for chiave in ("BypassTPMCheck", "BypassSecureBootCheck", "BypassRAMCheck", "BypassCPUCheck"):
            self.assertTrue(any(chiave in c and "LabConfig" in c for c in comandi),
                            "manca la voce LabConfig " + chiave)
        # senza il bypass le voci non ci sono
        xml2, dom2 = rendi(bypass_requirements=False)
        self.assertNotIn("LabConfig", xml2)

    def test_dominio_e_driver(self):
        xml, dom = rendi(join_domain__enabled=True, join_domain__domain="azienda.local",
                         join_domain__user="admjoin", join_domain__password="Dom1234!",
                         join_domain__ou="OU=PC,DC=azienda,DC=local")
        uj = componente(dom, "specialize", "Microsoft-Windows-UnattendedJoin")
        self.assertEqual(testo(uno(dom, "JoinDomain", uj)), "azienda.local")
        self.assertEqual(testo(uno(dom, "MachineObjectOU", uj)), "OU=PC,DC=azienda,DC=local")
        cred = uno(dom, "Credentials", uj)
        self.assertEqual(testo(uno(dom, "Username", cred)), "admjoin")
        self.assertEqual(testo(uno(dom, "Password", cred)), "Dom1234!")

        pnp = componente(dom, "specialize", "Microsoft-Windows-PnpCustomizationsNonWinPE")
        self.assertEqual(testo(uno(dom, "Path", pnp)), "\\\\10.10.0.254\\pxe\\drivers")
        self.assertEqual(testo(uno(dom, "Username", uno(dom, "Credentials", pnp))), "pxe")

        # driver disattivati: il componente sparisce
        xml2, dom2 = rendi(drivers_from_pixio=False)
        self.assertIsNone(componente(dom2, "specialize", "Microsoft-Windows-PnpCustomizationsNonWinPE"))

    def test_dominio_con_utente_qualificato(self):
        xml, dom = rendi(join_domain__enabled=True, join_domain__domain="azienda.local",
                         join_domain__user="AZIENDA\\admjoin", join_domain__password="x")
        cred = uno(dom, "Credentials", componente(dom, "specialize", "Microsoft-Windows-UnattendedJoin"))
        self.assertEqual(testo(uno(dom, "Domain", cred)), "AZIENDA")
        self.assertEqual(testo(uno(dom, "Username", cred)), "admjoin")

    def test_account_administrator_predefinito(self):
        xml, dom = rendi(admin_user="Administrator", autologon=True)
        oobe = componente(dom, "oobeSystem", "Microsoft-Windows-Shell-Setup")
        self.assertIsNone(uno(dom, "LocalAccount", oobe))
        ap = uno(dom, "AdministratorPassword", oobe)
        self.assertIsNotNone(ap)
        self.assertEqual(testo(uno(dom, "Value", ap)), "PasswordDiProva1")
        al = uno(dom, "AutoLogon", oobe)
        self.assertEqual(testo(uno(dom, "Username", al)), "Administrator")
        self.assertEqual(testo(uno(dom, "LogonCount", al)), "9999")

    def test_app_e_comandi(self):
        xml, dom = rendi(remove_apps=["Microsoft.BingNews"],
                         run_commands=["cmd /c echo ciao", "cmd /c shutdown /r /t 60"])
        oobe = componente(dom, "oobeSystem", "Microsoft-Windows-Shell-Setup")
        righe = [testo(uno(dom, "CommandLine", c))
                 for c in oobe.getElementsByTagName("SynchronousCommand")]
        ordini = [int(testo(uno(dom, "Order", c)))
                  for c in oobe.getElementsByTagName("SynchronousCommand")]
        self.assertEqual(ordini, list(range(1, len(righe) + 1)))
        self.assertTrue(any("Microsoft.BingNews" in r and "Remove-AppxPackage" in r for r in righe))
        self.assertIn("cmd /c echo ciao", righe)
        self.assertIn("cmd /c shutdown /r /t 60", righe)
        self.assertTrue(any("HideFileExt" in r for r in righe))

    def test_escaping_caratteri_speciali(self):
        """Password e nomi con & < > " ' devono restare leggibili una volta riletto l'XML."""
        pw = 'p&ss<w>o"rd\'x'
        xml, dom = rendi(admin_password=pw, autologon=True,
                         organization='Rossi & Figli <SpA>',
                         run_commands=['cmd /c echo "a & b" > C:\\log.txt'])
        # nel testo grezzo i caratteri sono codificati...
        self.assertNotIn('p&ss<w>o"rd', xml)
        self.assertIn("&amp;", xml)
        self.assertIn("&lt;w&gt;", xml)
        # ...ma il parser restituisce il valore originale
        oobe = componente(dom, "oobeSystem", "Microsoft-Windows-Shell-Setup")
        acc = uno(dom, "LocalAccount", oobe)
        self.assertEqual(testo(uno(dom, "Value", uno(dom, "Password", acc))), pw)
        self.assertEqual(testo(uno(dom, "Value", uno(dom, "Password", uno(dom, "AutoLogon", oobe)))), pw)
        self.assertEqual(testo(uno(dom, "RegisteredOrganization", oobe)), "Rossi & Figli <SpA>")
        righe = [testo(uno(dom, "CommandLine", c))
                 for c in oobe.getElementsByTagName("SynchronousCommand")]
        self.assertIn('cmd /c echo "a & b" > C:\\log.txt', righe)

    def test_xml_ben_formato_e_indentato(self):
        xml, dom = rendi()
        # minidom.parseString ha già validato la forma; qui si controlla l'indentazione
        self.assertIn('\n    <settings pass="windowsPE">', xml)
        self.assertNotIn("\n\n", xml)
        self.assertTrue(xml.endswith("</unattend>\n"))

    def test_render_da_sole_impostazioni(self):
        """render_autounattend accetta anche il solo dizionario delle impostazioni."""
        xml = WP.render_autounattend(base_settings(), "10.10.0.254", cfg=CFG_FINTA)
        minidom.parseString(xml)
        self.assertIn("<ComputerName>*</ComputerName>", xml)

    def test_render_valida(self):
        with self.assertRaises(ValueError):
            WP.render_autounattend({"name": "x", "settings": base_settings(computer_name="pc 01")},
                                   "10.10.0.254", cfg=CFG_FINTA)


# ---------------------------------------------------------------- CRUD

class CrudTest(unittest.TestCase):
    def setUp(self):
        C.WINPROFILES_FILE = MIO_WINPROFILES
        if os.path.exists(C.WINPROFILES_FILE):
            os.unlink(C.WINPROFILES_FILE)

    def test_crea_e_rileggi(self):
        p = WP.create({"name": "Ufficio Windows 11", "note": "prova",
                       "settings": {"admin_user": "tecnico", "admin_password": "Pw1234567",
                                    "computer_name": "PC01"}})
        self.assertEqual(p["id"], "ufficio-windows-11")
        self.assertEqual(p["settings"]["computer_name"], "PC01")
        self.assertEqual(p["settings"]["timezone"], "W. Europe Standard Time")
        self.assertTrue(p["created"] and p["updated"])
        self.assertEqual([x["id"] for x in WP.list_profiles()], ["ufficio-windows-11"])
        self.assertEqual(WP.get("ufficio-windows-11")["name"], "Ufficio Windows 11")
        self.assertIsNone(WP.get("non-esiste"))

    def test_id_ripetuto(self):
        a = WP.create({"name": "Uguale", "settings": {"admin_password": "x"}})
        b = WP.create({"name": "Uguale", "settings": {"admin_password": "x"}})
        self.assertEqual(a["id"], "uguale")
        self.assertEqual(b["id"], "uguale-2")

    def test_nome_obbligatorio(self):
        with self.assertRaises(ValueError):
            WP.create({"name": "", "settings": {"admin_password": "x"}})

    def test_aggiorna_duplica_elimina(self):
        p = WP.create({"name": "Base", "settings": {"admin_password": "x"}})
        q = WP.update(p["id"], {"note": "aggiornata", "settings": {"computer_name": "PC-*"}})
        self.assertEqual(q["note"], "aggiornata")
        self.assertEqual(q["settings"]["computer_name"], "PC-*")
        # le impostazioni non toccate restano
        self.assertEqual(q["settings"]["admin_password"], "x")
        with self.assertRaises(ValueError):
            WP.update(p["id"], {"settings": {"computer_name": "PC 01"}})
        with self.assertRaises(FileNotFoundError):
            WP.update("non-esiste", {"note": "x"})

        c = WP.duplicate(p["id"])
        self.assertEqual(c["name"], "Base (copia)")
        self.assertEqual(c["settings"]["computer_name"], "PC-*")
        WP.delete(c["id"])
        self.assertIsNone(WP.get(c["id"]))
        with self.assertRaises(FileNotFoundError):
            WP.delete(c["id"])

    def test_id_non_valido(self):
        for cattivo in ("../fuga", "MAIUSCOLO", "con spazio", ""):
            with self.assertRaises(ValueError):
                WP.check_id(cattivo)

    def test_preset(self):
        """I modelli di data/profile-presets.json devono essere accettati dalla validazione."""
        p = WP.load_preset("win-pc-singolo")
        if p is None:
            self.skipTest("modelli non installati")
        prof = WP.create({"name": "Da modello", "preset": "win-pc-singolo",
                          "settings": {"admin_password": "Pw1234567"}})
        self.assertTrue(prof["settings"]["autologon"])
        self.assertEqual(prof["settings"]["computer_name"], "PC-*")
        with self.assertRaises(ValueError):
            WP.create({"name": "Inesistente", "preset": "non-esiste"})


# ---------------------------------------------------------------- API e salvataggio come risposta

class AnswersFinto:
    """Finto services/answers.py: registra creazioni e aggiornamenti."""

    def __init__(self):
        self.creati = []
        self.aggiornati = []

    def create(self, data):
        self.creati.append(data)
        return {"id": "risposta-finta", "name": data.get("name", "")}

    def update(self, answer_id, data):
        self.aggiornati.append((answer_id, data))
        return {"id": answer_id, "name": "Risposta aggiornata"}


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pixio import create_app
        from pixio.blueprints import api_winprofile
        # la password amministratore va creata nella NOSTRA cartella etc: gli altri file di test
        # si aspettano di trovare la loro configurazione ancora vergine
        cls._etc = (C.ETC_DIR, C.CONFIG_FILE, C.SECRET_FILE)
        C.ETC_DIR = MIO_ETC
        C.CONFIG_FILE = os.path.join(MIO_ETC, "config.json")
        C.SECRET_FILE = os.path.join(MIO_ETC, "secret")
        cls.app = create_app()
        # il blueprint potrebbe non essere ancora in blueprints/__init__.py: lo registro qui
        if "api_winprofile" not in cls.app.blueprints:
            cls.app.register_blueprint(api_winprofile.bp)
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        r = cls.client.post("/api/auth/login", json={"password": "segreta1"})
        assert r.status_code == 200, r.get_json()
        cls.h = {"X-CSRF-Token": r.get_json()["csrf"]}

    @classmethod
    def tearDownClass(cls):
        C.ETC_DIR, C.CONFIG_FILE, C.SECRET_FILE = cls._etc

    def setUp(self):
        C.WINPROFILES_FILE = MIO_WINPROFILES
        if os.path.exists(C.WINPROFILES_FILE):
            os.unlink(C.WINPROFILES_FILE)

    def test_elenco_vuoto(self):
        r = self.client.get("/api/winprofiles")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["profiles"], [])
        self.assertEqual(d["defaults"]["timezone"], "W. Europe Standard Time")
        self.assertIn("W. Europe Standard Time", d["timezones"])
        self.assertTrue(any(a["id"] == "it-IT" for a in d["languages"]))
        self.assertTrue(all("id" in a and "name" in a for a in d["apps"]))

    def test_crud_via_api(self):
        r = self.client.post("/api/winprofiles", headers=self.h, json={
            "name": "Api", "settings": {"admin_user": "tec", "admin_password": "Pw1234567"}})
        self.assertEqual(r.status_code, 201, r.get_json())
        pid = r.get_json()["id"]

        self.assertEqual(self.client.get(f"/api/winprofiles/{pid}").status_code, 200)
        self.assertEqual(self.client.get("/api/winprofiles/non-esiste").status_code, 404)
        self.assertEqual(self.client.get("/api/winprofiles/NON VALIDO").status_code, 400)

        r = self.client.put(f"/api/winprofiles/{pid}", headers=self.h,
                            json={"settings": {"computer_name": "PC-UFF"}})
        self.assertEqual(r.get_json()["settings"]["computer_name"], "PC-UFF")

        r = self.client.put(f"/api/winprofiles/{pid}", headers=self.h,
                            json={"settings": {"product_key": "abc"}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("chiave di prodotto", r.get_json()["error"].lower())

        r = self.client.post(f"/api/winprofiles/{pid}/duplicate", headers=self.h, json={"name": "Copia api"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.client.delete(f"/api/winprofiles/{r.get_json()['id']}",
                                            headers=self.h).status_code, 200)
        self.assertEqual(self.client.delete("/api/winprofiles/non-esiste", headers=self.h).status_code, 404)

    def test_csrf_obbligatorio(self):
        r = self.client.post("/api/winprofiles", json={"name": "Senza token"})
        self.assertIn(r.status_code, (400, 403))

    def test_anteprima(self):
        r = self.client.post("/api/winprofiles", headers=self.h, json={
            "name": "Anteprima", "settings": {"admin_user": "tec", "admin_password": "Pw1234567",
                                              "computer_name": "PC-ANT"}})
        pid = r.get_json()["id"]
        r = self.client.post(f"/api/winprofiles/{pid}/preview", headers=self.h, json={})
        self.assertEqual(r.status_code, 200)
        xml = r.get_json()["xml"]
        minidom.parseString(xml)
        self.assertIn("<ComputerName>PC-ANT</ComputerName>", xml)

        # anteprima con modifiche non salvate
        r = self.client.post(f"/api/winprofiles/{pid}/preview", headers=self.h,
                             json={"settings": {"computer_name": "PC-MOD"}})
        self.assertIn("<ComputerName>PC-MOD</ComputerName>", r.get_json()["xml"])
        # il profilo salvato non è cambiato
        self.assertEqual(self.client.get(f"/api/winprofiles/{pid}").get_json()["settings"]["computer_name"],
                         "PC-ANT")

        # anteprima di un profilo mai salvato
        r = self.client.post("/api/winprofiles/preview", headers=self.h, json={
            "name": "Al volo", "settings": {"admin_user": "tec", "admin_password": "x",
                                            "disk": {"mode": "auto-bios"}}})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Riservato di sistema", r.get_json()["xml"])

        r = self.client.post("/api/winprofiles/preview", headers=self.h,
                             json={"settings": {"admin_user": "tec", "admin_password": ""}})
        self.assertEqual(r.status_code, 400)

    def test_salva_come_risposta(self):
        import pixio.services
        finto = AnswersFinto()
        mod = types.ModuleType("pixio.services.answers")
        mod.create = finto.create
        mod.update = finto.update
        originale = sys.modules.get("pixio.services.answers")
        attr = getattr(pixio.services, "answers", None)
        sys.modules["pixio.services.answers"] = mod
        pixio.services.answers = mod
        try:
            r = self.client.post("/api/winprofiles", headers=self.h, json={
                "name": "Con risposta", "settings": {"admin_user": "tec", "admin_password": "Pw&<1",
                                                     "computer_name": "PC-RIS"}})
            pid = r.get_json()["id"]
            r = self.client.post(f"/api/winprofiles/{pid}/save-answer", headers=self.h, json={})
            self.assertEqual(r.status_code, 200, r.get_json())
            d = r.get_json()
            self.assertTrue(d["ok"])
            self.assertEqual(d["answer_id"], "risposta-finta")
            self.assertEqual(d["answer_name"], "Con risposta")
            self.assertEqual(finto.creati[0]["kind"], "windows")
            self.assertEqual(finto.creati[0]["filename"], "autounattend.xml")
            xml = finto.creati[0]["content"]
            minidom.parseString(xml)
            self.assertIn("<ComputerName>PC-RIS</ComputerName>", xml)
            self.assertIn("Pw&amp;&lt;1", xml)

            # con answer_id aggiorna invece di creare
            r = self.client.post(f"/api/winprofiles/{pid}/save-answer", headers=self.h,
                                 json={"answer_id": "vecchia"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(finto.aggiornati[0][0], "vecchia")
            self.assertEqual(finto.aggiornati[0][1]["filename"], "autounattend.xml")

            self.assertEqual(
                self.client.post("/api/winprofiles/non-esiste/save-answer", headers=self.h,
                                 json={}).status_code, 404)
        finally:
            if originale is not None:
                sys.modules["pixio.services.answers"] = originale
            else:
                sys.modules.pop("pixio.services.answers", None)
            if attr is not None:
                pixio.services.answers = attr

    def test_salva_come_risposta_reale(self):
        """Stesso giro con il servizio delle risposte vero, se è presente in questa installazione."""
        try:
            from pixio.services import answers
        except ImportError:
            self.skipTest("services/answers.py non disponibile")
        if getattr(answers, "create", None) is None or isinstance(answers, types.ModuleType) is False:
            self.skipTest("servizio delle risposte sostituito da un finto")
        C.ANSWERS_DIR = MIO_ANSWERS_DIR
        C.ANSWERS_FILE = MIO_ANSWERS_FILE
        os.makedirs(C.ANSWERS_DIR, exist_ok=True)
        r = self.client.post("/api/winprofiles", headers=self.h, json={
            "name": "Risposta vera", "settings": {"admin_user": "tec", "admin_password": "Pw1234567"}})
        pid = r.get_json()["id"]
        r = self.client.post(f"/api/winprofiles/{pid}/save-answer", headers=self.h, json={})
        self.assertEqual(r.status_code, 200, r.get_json())
        aid = r.get_json()["answer_id"]
        percorso = os.path.join(C.ANSWERS_DIR, aid, "autounattend.xml")
        self.assertTrue(os.path.isfile(percorso), percorso)
        with open(percorso, encoding="utf-8") as f:
            minidom.parseString(f.read())


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
