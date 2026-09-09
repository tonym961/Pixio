"""Test dei profili di personalizzazione Windows (docs/API.md, sezione 8).

Esecuzione: cd /opt/pixio && python3 -m unittest tests.test_winprofile
Non serve l'helper privilegiato né il servizio delle risposte reale: entrambi vengono sostituiti da finti.
"""
import json
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


def preset_windows_ids():
    """Identificativi dei modelli windows di data/profile-presets.json (vuoto se il file manca)."""
    percorso = getattr(C, "PRESETS_FILE",
                       os.path.join(getattr(C, "CODE_DIR", "/opt/pixio"), "data",
                                    "profile-presets.json"))
    try:
        with open(percorso, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    return [p["id"] for p in (d.get("presets") or []) if p.get("kind") == "windows"]


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

    def test_catalogo_nell_elenco(self):
        """GET /api/winprofiles espone il catalogo delle ottimizzazioni (docs/API.md, sezione 10)."""
        d = self.client.get("/api/winprofiles").get_json()
        self.assertIn("tweaks", d)
        cat = d["tweaks"]
        self.assertIn("categories", cat)
        self.assertIn("items", cat)
        if not cat["items"]:
            self.skipTest("catalogo delle ottimizzazioni non installato")
        note = {c["id"] for c in cat["categories"]}
        for t in cat["items"]:
            self.assertIn(t["category"], note)
            self.assertIn(t["impact"], d["impacts"])
        self.assertEqual([s["id"] for s in d["service_starts"]], [2, 3, 4])
        # i modelli arrivano con le ottimizzazioni già scelte
        modelli = [p for p in d["presets"] if p["kind"] == "windows"]
        self.assertTrue(modelli)
        self.assertTrue(any(p["settings"].get("tweaks") for p in modelli))

    def test_profilo_con_tweak_via_api(self):
        ids = [t["id"] for t in WP.tweaks_catalog()["items"]][:5]
        if not ids:
            self.skipTest("catalogo delle ottimizzazioni non installato")
        r = self.client.post("/api/winprofiles", headers=self.h, json={
            "name": "Con tweak", "settings": {"admin_user": "tec", "admin_password": "Pw1234567",
                                              "tweaks": ids,
                                              "services_extra": [{"name": "Fax", "start": 4}],
                                              "features_disable": ["SMB1Protocol"]}})
        self.assertEqual(r.status_code, 201, r.get_json())
        pid = r.get_json()["id"]
        self.assertEqual(r.get_json()["settings"]["tweaks"], sorted(
            ids, key=lambda x: [t["id"] for t in WP.tweaks_catalog()["items"]].index(x)))
        xml = self.client.post(f"/api/winprofiles/{pid}/preview", headers=self.h,
                               json={}).get_json()["xml"]
        minidom.parseString(xml)
        self.assertIn("Services\\Fax", xml)
        self.assertIn("/featurename:SMB1Protocol", xml)
        # id inesistente: 400 con messaggio in italiano
        r = self.client.put(f"/api/winprofiles/{pid}", headers=self.h,
                            json={"settings": {"tweaks": ["non-esiste-affatto"]}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("ottimizzazione sconosciuta", r.get_json()["error"].lower())

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


# ---------------------------------------------------------------- catalogo delle ottimizzazioni

CAMPI_TWEAK = ("id", "category", "name", "description", "impact", "editions")


def catalogo():
    return WP.tweaks_catalog()


def voci_con(chiave):
    """Voci del catalogo che contengono quella chiave (reg, services, commands, features_*)."""
    return [t for t in catalogo()["items"] if t.get(chiave)]


def voci_reg(scope):
    return [t for t in catalogo()["items"]
            if any(r.get("scope") == scope for r in (t.get("reg") or []))]


class CatalogoTest(unittest.TestCase):
    def setUp(self):
        self.cat = catalogo()
        if not self.cat["items"]:
            self.skipTest("catalogo delle ottimizzazioni non installato")

    def test_dimensione_minima(self):
        """Il contratto chiede un catalogo vero, non tre voci di esempio."""
        self.assertGreaterEqual(len(self.cat["items"]), 55)
        self.assertGreaterEqual(len(self.cat["categories"]), 8)

    def test_id_univoci(self):
        ids = [t["id"] for t in self.cat["items"]]
        ripetuti = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(ripetuti, [], "identificativi ripetuti: %s" % ripetuti)
        for i in ids:
            self.assertRegex(i, r"^[a-z0-9][a-z0-9_-]{0,63}$", "id non valido: " + i)

    def test_categorie_esistenti(self):
        note = {c["id"] for c in self.cat["categories"]}
        for c in self.cat["categories"]:
            self.assertTrue(c.get("name"), "categoria senza nome: " + c["id"])
        for t in self.cat["items"]:
            self.assertIn(t["category"], note, "categoria sconosciuta in " + t["id"])

    def test_campi_obbligatori(self):
        for t in self.cat["items"]:
            for campo in CAMPI_TWEAK:
                self.assertTrue(t.get(campo), f"{t['id']}: manca il campo {campo}")
            self.assertIn(t["impact"], WP.IMPACTS, t["id"])
            self.assertIsInstance(t["editions"], list)
            self.assertTrue(set(t["editions"]) <= {"10", "11"}, t["id"])
            # una voce deve fare qualcosa
            self.assertTrue(any(t.get(k) for k in ("reg", "services", "commands",
                                                   "features_enable", "features_disable")),
                            t["id"] + ": non applica niente")
            # descrizione da tecnico: una frase vera, non due parole
            self.assertGreater(len(t["description"]), 40, t["id"] + ": descrizione troppo corta")

    def test_percorsi_di_registro(self):
        for t in self.cat["items"]:
            for r in (t.get("reg") or []):
                self.assertIn(r.get("scope"), ("HKLM", "HKCU"), t["id"])
                p = r.get("path") or ""
                self.assertTrue(p, t["id"] + ": percorso vuoto")
                self.assertFalse(p.upper().startswith("HKEY_"),
                                 f"{t['id']}: il percorso non deve contenere la radice: {p}")
                self.assertFalse(p.startswith("\\"),
                                 f"{t['id']}: percorso con barra iniziale: {p}")
                self.assertNotIn("/", p, t["id"] + ": separatore sbagliato in " + p)
                self.assertTrue(r.get("name"), t["id"] + ": valore senza nome")
                self.assertIn(r.get("type"), WP.REG_TYPES, t["id"])
                self.assertIsInstance(r.get("data"), str, t["id"] + ": dato non testuale")
                self.assertNotIn('"', p + str(r.get("name")) + str(r.get("data")),
                                 t["id"] + ": virgolette dentro una voce di registro")

    def test_servizi_e_funzionalita(self):
        for t in self.cat["items"]:
            for sv in (t.get("services") or []):
                self.assertRegex(sv.get("name", ""), r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", t["id"])
                self.assertIn(sv.get("start"), WP.SERVICE_STARTS,
                              f"{t['id']}: avvio non valido per {sv.get('name')}")
            for chiave in ("features_enable", "features_disable"):
                for f in (t.get(chiave) or []):
                    self.assertRegex(f, r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$", t["id"])
            for c in (t.get("commands") or []):
                self.assertTrue(c.strip(), t["id"] + ": comando vuoto")
                self.assertLessEqual(len(c), WP.MAX_CMD_LEN, t["id"] + ": comando troppo lungo")

    def test_accesso_singolo(self):
        primo = self.cat["items"][0]["id"]
        self.assertEqual(WP.tweak(primo)["id"], primo)
        self.assertIsNone(WP.tweak("questo-non-esiste"))
        self.assertIsNone(WP.tweak(""))
        # tweak() restituisce una copia: modificarla non sporca la cache
        v = WP.tweak(primo)
        v["name"] = "toccato"
        self.assertNotEqual(WP.tweak(primo)["name"], "toccato")

    def test_cache_sul_mtime(self):
        """Il catalogo si ricarica quando il file cambia (come services/recipes.py)."""
        finto = os.path.join(TMP, "tweaks-finti.json")
        with open(finto, "w", encoding="utf-8") as f:
            json.dump({"categories": [{"id": "prova", "name": "Prova"}],
                       "tweaks": [{"id": "uno", "category": "prova", "name": "Uno",
                                   "description": "x", "impact": "sicuro", "editions": ["11"],
                                   "reg": [{"scope": "HKLM", "path": "SOFTWARE\\X",
                                            "name": "A", "type": "REG_DWORD", "data": "1"}]}]}, f)
        vecchio = getattr(C, "WINTWEAKS_FILE", None)
        C.WINTWEAKS_FILE = finto
        try:
            self.assertEqual(WP.tweak_ids(), ["uno"])
            with open(finto, "w", encoding="utf-8") as f:
                json.dump({"categories": [{"id": "prova", "name": "Prova"}],
                           "tweaks": [{"id": "uno", "category": "prova", "name": "Uno",
                                       "description": "x", "impact": "sicuro", "editions": ["11"]},
                                      {"id": "due", "category": "prova", "name": "Due",
                                       "description": "x", "impact": "sicuro", "editions": ["11"]},
                                      {"id": "tre", "category": "sconosciuta", "name": "Tre",
                                       "description": "x", "impact": "sicuro", "editions": ["11"]}]}, f)
            os.utime(finto, (0, 0))          # mtime diverso: deve rileggere
            # la voce con categoria inesistente viene scartata, non fa saltare tutto il file
            self.assertEqual(WP.tweak_ids(), ["uno", "due"])
        finally:
            if vecchio is None:
                del C.WINTWEAKS_FILE
            else:
                C.WINTWEAKS_FILE = vecchio
        self.assertGreaterEqual(len(WP.tweak_ids()), 55)


class ValidazioneTweakTest(unittest.TestCase):
    def setUp(self):
        if not catalogo()["items"]:
            self.skipTest("catalogo delle ottimizzazioni non installato")
        self.primo = catalogo()["items"][0]["id"]

    def test_predefiniti(self):
        d = WP.defaults()
        for campo in ("tweaks", "services_extra", "features_enable", "features_disable"):
            self.assertEqual(d[campo], [], campo)

    def test_id_inesistente_rifiutato(self):
        with self.assertRaises(ValueError) as ctx:
            WP.validate(base_settings(tweaks=[self.primo, "ottimizzazione-inventata"]))
        self.assertIn("ottimizzazione sconosciuta", str(ctx.exception).lower())
        self.assertIn("ottimizzazione-inventata", str(ctx.exception))

    def test_ordine_stabile_e_senza_doppioni(self):
        ids = [t["id"] for t in catalogo()["items"]]
        scelti = [ids[5], ids[1], ids[3], ids[1]]
        v = WP.validate(base_settings(tweaks=scelti))
        self.assertEqual(v["tweaks"], [ids[1], ids[3], ids[5]])

    def test_servizi_extra(self):
        v = WP.validate(base_settings(services_extra=[{"name": "WSearch", "start": 4},
                                                      "Spooler:3",
                                                      {"name": "wsearch", "start": 2}]))
        self.assertEqual(v["services_extra"], [{"name": "WSearch", "start": 4},
                                               {"name": "Spooler", "start": 3}])
        for cattivo, atteso in (([{"name": "Nome con spazi", "start": 4}], "nome del servizio"),
                                ([{"name": "WSearch", "start": 1}], "avvio del servizio"),
                                ([{"name": "WSearch", "start": 0}], "avvio del servizio")):
            with self.assertRaises(ValueError) as ctx:
                WP.validate(base_settings(services_extra=cattivo))
            self.assertIn(atteso, str(ctx.exception).lower())

    def test_funzionalita(self):
        v = WP.validate(base_settings(features_enable=["NetFx3", "netfx3"],
                                      features_disable=["SMB1Protocol"]))
        self.assertEqual(v["features_enable"], ["NetFx3"])
        self.assertEqual(v["features_disable"], ["SMB1Protocol"])
        with self.assertRaises(ValueError) as ctx:
            WP.validate(base_settings(features_enable=["Net Fx 3"]))
        self.assertIn("nome non valido", str(ctx.exception).lower())
        with self.assertRaises(ValueError) as ctx:
            WP.validate(base_settings(features_enable=["NetFx3"], features_disable=["netfx3"]))
        self.assertIn("sia da attivare sia da disattivare", str(ctx.exception).lower())


class GenerazioneTweakTest(unittest.TestCase):
    def setUp(self):
        if not catalogo()["items"]:
            self.skipTest("catalogo delle ottimizzazioni non installato")

    def comandi(self, dom, passo):
        tag = "RunSynchronousCommand" if passo == "specialize" else "SynchronousCommand"
        campo = "Path" if passo == "specialize" else "CommandLine"
        sp = passaggio(dom, passo)
        if sp is None:
            return []
        return [testo(uno(dom, campo, c)) for c in sp.getElementsByTagName(tag)]

    def ordini(self, dom, passo):
        tag = "RunSynchronousCommand" if passo == "specialize" else "SynchronousCommand"
        sp = passaggio(dom, passo)
        if sp is None:
            return []
        return [int(testo(uno(dom, "Order", c))) for c in sp.getElementsByTagName(tag)]

    def test_tweak_hklm_in_specialize(self):
        t = voci_reg("HKLM")[0]
        r = [x for x in t["reg"] if x["scope"] == "HKLM"][0]
        xml, dom = rendi(tweaks=[t["id"]])
        righe = self.comandi(dom, "specialize")
        atteso = 'reg add "HKLM\\%s" /v "%s" /t %s /d "%s" /f' % (
            r["path"], r["name"], r["type"], r["data"])
        self.assertTrue(any(atteso in c for c in righe),
                        "manca il comando di %s:\n%s" % (t["id"], "\n".join(righe)))
        # e non finisce nei comandi al primo accesso
        self.assertFalse(any(r["name"] in c and "HKLM" in c
                             for c in self.comandi(dom, "oobeSystem")))

    def test_tweak_hkcu_un_solo_carico(self):
        """Anche con tutte le ottimizzazioni HKCU del catalogo il profilo predefinito si carica
        e si scarica una volta sola, con tutte le scritture in mezzo."""
        scelti = [t["id"] for t in voci_reg("HKCU")]
        self.assertGreaterEqual(len(scelti), 3, "servono più voci HKCU per una prova seria")
        xml, dom = rendi(tweaks=scelti)
        righe = self.comandi(dom, "specialize")
        carichi = [i for i, c in enumerate(righe) if "reg load" in c]
        scarichi = [i for i, c in enumerate(righe) if "reg unload" in c]
        self.assertEqual(len(carichi), 1, righe)
        self.assertEqual(len(scarichi), 1, righe)
        self.assertLess(carichi[0], scarichi[0])
        self.assertIn(WP.DEFAULT_NTUSER, righe[carichi[0]])
        self.assertIn(WP.DEFAULT_HIVE, righe[carichi[0]])
        self.assertIn(WP.DEFAULT_HIVE, righe[scarichi[0]])
        scritture = [c for c in righe[carichi[0] + 1:scarichi[0]] if "reg add" in c]
        self.assertGreater(len(scritture), 5)
        for c in scritture:
            self.assertIn('"' + WP.DEFAULT_HIVE + "\\", c)
        # niente HKCU letterale: si scrive sul profilo predefinito montato
        self.assertFalse(any(c.startswith('cmd /c reg add "HKCU') for c in righe))
        self.assertEqual(self.ordini(dom, "specialize"),
                         list(range(1, len(righe) + 1)))

    def test_tweak_servizio(self):
        t = [x for x in voci_con("services")][0]
        sv = t["services"][0]
        xml, dom = rendi(tweaks=[t["id"]])
        atteso = ('reg add "HKLM\\SYSTEM\\CurrentControlSet\\Services\\%s" /v "Start" '
                  '/t REG_DWORD /d "%d" /f' % (sv["name"], sv["start"]))
        self.assertTrue(any(atteso in c for c in self.comandi(dom, "specialize")))

    def test_servizio_aggiunto_a_mano(self):
        xml, dom = rendi(services_extra=[{"name": "RemoteRegistry", "start": 3}])
        self.assertTrue(any('Services\\RemoteRegistry" /v "Start" /t REG_DWORD /d "3"' in c
                            for c in self.comandi(dom, "specialize")))

    def test_funzionalita_dism(self):
        xml, dom = rendi(features_enable=["NetFx3"], features_disable=["SMB1Protocol"])
        righe = self.comandi(dom, "oobeSystem")
        self.assertTrue(any("dism /online /enable-feature /featurename:NetFx3" in c for c in righe),
                        righe)
        self.assertTrue(any("dism /online /disable-feature /featurename:SMB1Protocol" in c
                            for c in righe), righe)
        self.assertTrue(all("/norestart" in c for c in righe if "dism" in c))
        self.assertEqual(self.ordini(dom, "oobeSystem"), list(range(1, len(righe) + 1)))

    def test_funzionalita_da_tweak(self):
        t = [x for x in voci_con("features_disable")][0]
        xml, dom = rendi(tweaks=[t["id"]])
        nome = t["features_disable"][0]
        self.assertTrue(any("/featurename:" + nome in c for c in self.comandi(dom, "oobeSystem")))

    def test_comandi_del_tweak_al_primo_accesso(self):
        t = [x for x in voci_con("commands")][0]
        xml, dom = rendi(tweaks=[t["id"]])
        righe = self.comandi(dom, "oobeSystem")
        for c in t["commands"]:
            self.assertIn(c, righe)

    def test_niente_doppioni_con_i_booleani(self):
        """Un'ottimizzazione che rifà quello che fa già un campo booleano non deve generare
        due volte lo stesso comando."""
        xml, dom = rendi(tweaks=["disattiva-ibernazione", "mostra-estensioni-file"],
                         disable_hibernate=True, hide_files_ext=False)
        tutti = self.comandi(dom, "specialize") + self.comandi(dom, "oobeSystem")
        norm = [c.lower().replace("cmd /c ", "") for c in tutti]
        self.assertEqual(len([c for c in norm if "powercfg /hibernate off" in c]), 1, tutti)
        self.assertEqual(len([c for c in norm if "hidefileext" in c]), 1, tutti)
        # vince l'ottimizzazione: la scrittura va sul profilo predefinito, non su HKCU
        riga = [c for c in tutti if "HideFileExt" in c][0]
        self.assertIn(WP.DEFAULT_HIVE, riga)

    def test_ordine_indipendente_dalla_selezione(self):
        ids = [t["id"] for t in catalogo()["items"]][:8]
        a = WP.render_autounattend({"name": "a", "settings": base_settings(tweaks=ids)},
                                   "10.10.0.254", cfg=CFG_FINTA)
        b = WP.render_autounattend({"name": "a", "settings": base_settings(tweaks=list(reversed(ids)))},
                                   "10.10.0.254", cfg=CFG_FINTA)
        self.assertEqual(a, b)

    def test_catalogo_intero(self):
        """Tutte le ottimizzazioni insieme: XML valido, un solo carico del profilo, Order in fila."""
        ids = [t["id"] for t in catalogo()["items"]]
        xml, dom = rendi(tweaks=ids, remove_apps=["Microsoft.BingNews"],
                         run_commands=["cmd /c echo fine"])
        minidom.parseString(xml)
        self.assertEqual(xml.count("reg load"), 1)
        self.assertEqual(xml.count("reg unload"), 1)
        for passo in ("specialize", "oobeSystem"):
            righe = self.comandi(dom, passo)
            self.assertEqual(self.ordini(dom, passo), list(range(1, len(righe) + 1)))
            self.assertEqual(len(righe), len(set(righe)), "comandi ripetuti in " + passo)
        self.assertIn("cmd /c echo fine", self.comandi(dom, "oobeSystem"))

    def test_escaping_dei_percorsi_con_spazi(self):
        """I percorsi con spazi ("Control Panel\\Desktop", "Windows Search") restano virgolettati."""
        ids = [t["id"] for t in catalogo()["items"]]
        xml, dom = rendi(tweaks=ids)
        conspazi = [c for c in self.comandi(dom, "specialize") if "reg add" in c and " " in c]
        for c in conspazi:
            chiave = c.split('reg add ', 1)[1]
            self.assertTrue(chiave.startswith('"'), c)
            self.assertIn('" /v "', c, c)


class PresetWindowsTest(unittest.TestCase):
    """Tutti i modelli windows devono restare creabili e produrre un XML valido."""

    def setUp(self):
        C.WINPROFILES_FILE = MIO_WINPROFILES
        if os.path.exists(C.WINPROFILES_FILE):
            os.unlink(C.WINPROFILES_FILE)
        self.ids = preset_windows_ids()
        if not self.ids:
            self.skipTest("modelli non installati")

    def test_almeno_sette_modelli(self):
        for atteso in ("win-postazione-aziendale", "win-pc-singolo", "win-server", "win-laboratorio",
                       "win-minimale", "win-privacy", "win-prestazioni"):
            self.assertIn(atteso, self.ids)

    def test_creabili_e_xml_valido(self):
        noti = set(WP.tweak_ids())
        for pid in self.ids:
            p = WP.load_preset(pid)
            self.assertIsNotNone(p, pid)
            s = p["settings"]
            self.assertFalse(s.get("join_domain", {}).get("enabled"),
                             pid + ": il dominio deve essere disattivato nel modello")
            for tid in s.get("tweaks") or []:
                self.assertIn(tid, noti, f"{pid}: ottimizzazione inesistente {tid}")
            # la password è l'unico campo che il modello lascia scegliere al tecnico (vedi _nota)
            extra = {}
            if s.get("admin_user") and not s.get("admin_password"):
                extra["admin_password"] = "PasswordDiProva1"
            prof = WP.create({"name": "Modello " + pid, "preset": pid, "settings": extra})
            xml = WP.render_autounattend(prof, "10.10.0.254", cfg=CFG_FINTA)
            dom = minidom.parseString(xml)
            self.assertEqual(dom.documentElement.tagName, "unattend", pid)
            for passo in ("windowsPE", "specialize", "oobeSystem"):
                self.assertIsNotNone(passaggio(dom, passo), f"{pid}: manca il passaggio {passo}")
            self.assertLessEqual(xml.count("reg load"), 1, pid)
            self.assertEqual(xml.count("reg load"), xml.count("reg unload"), pid)
            WP.delete(prof["id"])

    def test_nuovi_modelli(self):
        minimale = WP.load_preset("win-minimale")["settings"]
        self.assertTrue(minimale["remove_apps"], "win-minimale deve rimuovere delle app")
        self.assertIn("servizi-telemetria", minimale["tweaks"])
        privacy = WP.load_preset("win-privacy")["settings"]
        for atteso in ("telemetria-minima", "disattiva-onedrive", "disattiva-posizione"):
            self.assertIn(atteso, privacy["tweaks"])
        prestazioni = WP.load_preset("win-prestazioni")["settings"]
        self.assertEqual(prestazioni["power_scheme"], "prestazioni")
        for atteso in ("effetti-visivi-ridotti", "disattiva-indicizzazione", "disattiva-sysmain"):
            self.assertIn(atteso, prestazioni["tweaks"])



def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
