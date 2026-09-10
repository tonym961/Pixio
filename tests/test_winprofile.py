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
# Libreria driver: da quando il file di risposta elenca le cartelle percorribili dal programma di
# installazione (docs/API.md, sezione 24) la generazione la legge davvero, quindi i test devono avere
# la loro, vuota, e non quella del server su cui girano.
MIO_DRIVERS_DIR = os.path.join(C.SRV_DIR, "http", "drivers")
MIO_DRIVERS_FILE = os.path.join(MIO_VAR, "drivers.json")
C.HTTP_DIR = os.path.join(C.SRV_DIR, "http")
C.DRIVERS_DIR = MIO_DRIVERS_DIR
C.DRIVERS_FILE = MIO_DRIVERS_FILE
for _d in (C.ETC_DIR, C.VAR_DIR, C.JOBS_DIR, C.UPLOAD_TMP_DIR, C.LOG_DIR, C.LIBRARY_DIR, C.CACHE_DIR, C.TFTP_DIR,
           C.DRIVERS_DIR):
    os.makedirs(_d, exist_ok=True)


def miei_driver():
    """Riapplica i percorsi della libreria driver: gli altri file di test riscrivono le stesse costanti."""
    C.DRIVERS_DIR = MIO_DRIVERS_DIR
    C.DRIVERS_FILE = MIO_DRIVERS_FILE
    os.makedirs(C.DRIVERS_DIR, exist_ok=True)


def cartella_driver(nome, file_dict, flags=None):
    """Crea una cartella driver di prova ({percorso relativo: contenuto}) e i suoi flag. Ritorna il percorso."""
    import json as _json
    miei_driver()
    base = os.path.join(C.DRIVERS_DIR, nome)
    for rel, testo_file in file_dict.items():
        full = os.path.join(base, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(testo_file)
    if flags is not None:
        d = {}
        if os.path.exists(C.DRIVERS_FILE):
            with open(C.DRIVERS_FILE, encoding="utf-8") as fh:
                d = _json.load(fh)
        d.setdefault("folders", {})[nome] = flags
        with open(C.DRIVERS_FILE, "w", encoding="utf-8") as fh:
            _json.dump(d, fh)
    return base


def svuota_driver():
    """Toglie tutte le cartelle driver di prova e i loro flag."""
    miei_driver()
    shutil.rmtree(C.DRIVERS_DIR, ignore_errors=True)
    os.makedirs(C.DRIVERS_DIR, exist_ok=True)
    if os.path.exists(C.DRIVERS_FILE):
        os.unlink(C.DRIVERS_FILE)


# .inf finti: uno completo (ha accanto tutto quello che dichiara) e uno incompleto come il vero
# RAID_drivers/iaStorVD.inf, che promette RstMwService.exe e non ce l'ha.
INF_COMPLETO = """[Version]
Signature = "$Windows NT$"
CatalogFile = buono.cat
[SourceDisksFiles]
buono.sys = 1,,
[Copia]
buono.sys
"""
INF_INCOMPLETO = """[Version]
Signature = "$Windows NT$"
CatalogFile = rotto.cat
[SourceDisksFiles]
rotto.sys = 1,,
RstMwService.exe = 1,,
[Copia]
rotto.sys
RstMwService.exe
"""


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


def rendi(iso=None, **over):
    """XML generato dalle impostazioni di prova, già letto con minidom."""
    miei_driver()
    xml = WP.render_autounattend({"name": "Prova", "settings": base_settings(**over)},
                                 "10.10.0.254", cfg=CFG_FINTA, iso=iso)
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

    def test_password_facoltativa_sui_client(self):
        """Sui client l'account senza password è legittimo; su Server il criterio di complessità lo vieta."""
        s = WP.defaults()          # admin_user impostato, password vuota, target client
        out = WP.validate(s)
        self.assertEqual(out["admin_password"], "")
        self.assertEqual(out["admin_user"], WP.DEFAULTS["admin_user"])
        # secondo utente senza password: ammesso sui client
        s2 = base_settings(extra_user={"name": "ospite", "password": "", "group": "Users"})
        self.assertEqual(WP.validate(s2)["extra_user"]["name"], "ospite")
        # su Windows Server serve la password, per l'amministratore e per il secondo utente
        with self.assertRaises(ValueError) as ctx:
            WP.validate(base_settings(target="server", admin_user="amministratore", admin_password=""))
        self.assertIn("obbligatoria", str(ctx.exception).lower())
        with self.assertRaises(ValueError):
            WP.validate(base_settings(target="server", admin_password="Prova1234!",
                                      extra_user={"name": "ospite", "password": "", "group": "Users"}))
        # utente vuoto ma password presente: incoerente
        self._ko("nessun nome utente", admin_user="")

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

    def test_disco_automatico_senza_cancellazione_rifiutato(self):
        """Partizionamento automatico + "non cancellare" è una combinazione impossibile.

        Pixio scrive quattro CreatePartition che valgono solo su un disco vuoto: senza azzeramento
        il setup le aggiunge alla tabella già presente e fallisce (0x80042565) dopo aver comunque
        toccato il disco, oppure formatta le partizioni preesistenti perché i PartitionID non
        corrispondono più. Al salvataggio si rifiuta."""
        for modo in ("auto-uefi", "auto-bios"):
            self._ko("azzerato", disk__mode=modo, disk__wipe=False)
        # in modalità manuale non si crea nessuna partizione: la spunta non ha effetto e passa
        v = WP.validate(base_settings(disk__mode="manuale", disk__wipe=False))
        self.assertEqual(v["disk"]["mode"], "manuale")

    def test_profilo_gia_salvato_corretto_invece_che_rifiutato(self):
        """I profili già in /var/lib/pixio devono continuare a produrre un file valido.

        La generazione valida con rifiuta_incompatibili=False: lì la combinazione impossibile non
        fa fallire niente, viene corretta e finisce un avviso nei log."""
        with self.assertLogs("pixio.winprofile", level="WARNING"):
            v = WP.validate(base_settings(disk__mode="auto-uefi", disk__wipe=False),
                            rifiuta_incompatibili=False)
        self.assertTrue(v["disk"]["wipe"])

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

        # specialize: nome computer e fuso orario. "PC-*" non arriva mai cosi' com'e' al PC:
        # Windows accetta l'asterisco solo da solo, e un nome illegale fa fallire l'installazione
        # dopo che l'immagine e' gia' stata copiata.
        shell = componente(dom, "specialize", "Microsoft-Windows-Shell-Setup")
        nome = testo(uno(dom, "ComputerName", shell))
        self.assertTrue(nome.startswith("PC-"), nome)
        self.assertNotIn("*", nome)
        self.assertLessEqual(len(nome), 15)
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

    def test_disco_sempre_azzerato_nelle_modalita_automatiche(self):
        """WillWipeDisk è true anche per un profilo salvato con la spunta tolta."""
        for modo in ("auto-uefi", "auto-bios"):
            xml, dom = rendi(disk__mode=modo, disk__wipe=False)
            setup = componente(dom, "windowsPE", "Microsoft-Windows-Setup")
            self.assertEqual(testo(uno(dom, "WillWipeDisk", setup)), "true", modo)
            self.assertNotIn("<WillWipeDisk>false</WillWipeDisk>", xml)
        # con la spunta messa non cambia niente
        _, dom = rendi(disk__mode="auto-uefi", disk__wipe=True)
        setup = componente(dom, "windowsPE", "Microsoft-Windows-Setup")
        self.assertEqual(testo(uno(dom, "WillWipeDisk", setup)), "true")
        # in modalità manuale non c'è nessuna sezione da azzerare
        xml3, _ = rendi(disk__mode="manuale", disk__wipe=False)
        self.assertNotIn("WillWipeDisk", xml3)

    def test_bypassnro_in_specialize_non_in_windowspe(self):
        """La chiave la legge l'OOBE del sistema installato: in windowsPE finirebbe nell'alveare
        del disco RAM del WinPE, che al riavvio non esiste più."""
        xml, dom = rendi(skip_oobe=True)
        setup = componente(dom, "windowsPE", "Microsoft-Windows-Setup")
        in_pe = [testo(uno(dom, "Path", c))
                 for c in (setup.getElementsByTagName("RunSynchronousCommand") if setup else [])]
        self.assertFalse([c for c in in_pe if "BypassNRO" in c], in_pe)
        dep = componente(dom, "specialize", "Microsoft-Windows-Deployment")
        self.assertIsNotNone(dep)
        in_spec = [testo(uno(dom, "Path", c))
                   for c in dep.getElementsByTagName("RunSynchronousCommand")]
        self.assertTrue([c for c in in_spec if "BypassNRO" in c and "CurrentVersion" in c], in_spec)
        # senza "salta l'OOBE" la chiave non si scrive da nessuna parte
        xml2, _ = rendi(skip_oobe=False)
        self.assertNotIn("BypassNRO", xml2)

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

        # i driver si chiedono in windowsPE, mai in specialize (sezione 24)
        self.assertIsNone(componente(dom, "specialize", "Microsoft-Windows-PnpCustomizationsNonWinPE"))

        # driver disattivati: il componente non c'è in nessun passaggio
        xml2, dom2 = rendi(drivers_from_pixio=False)
        self.assertNotIn("PnpCustomizations", xml2)

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

    def test_targets_nell_elenco(self):
        """GET /api/winprofiles espone targets e le editions di ogni voce (docs/API.md, sez. 11 e 12)."""
        d = self.client.get("/api/winprofiles").get_json()
        self.assertEqual([t["id"] for t in d["targets"]],
                         ["client", "10-ltsc", "11-ltsc", "server"])
        self.assertTrue(all(t.get("name") for t in d["targets"]))
        self.assertEqual(d["defaults"]["target"], "client")
        for t in d["tweaks"]["items"]:
            self.assertTrue(set(t["editions"]) & set(WP.PLATFORMS),
                            t["id"] + ": nessuna piattaforma valida")

    def test_profilo_server_via_api(self):
        """Con target server la creazione rifiuta le voci che su Server non esistono."""
        solo_client = [t for t in WP.tweaks_catalog()["items"] if "server" not in t["editions"]]
        if not solo_client:
            self.skipTest("catalogo delle ottimizzazioni non installato")
        r = self.client.post("/api/winprofiles", headers=self.h, json={
            "name": "Server con voce sbagliata",
            "settings": {"admin_user": "tec", "admin_password": "Pw1234567", "target": "server",
                         "tweaks": [solo_client[0]["id"]]}})
        self.assertEqual(r.status_code, 400)
        self.assertIn(solo_client[0]["id"], r.get_json()["error"])
        r = self.client.post("/api/winprofiles", headers=self.h, json={
            "name": "Server", "preset": "winserver",
            "settings": {"admin_password": "Pw1234567"}})
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(r.get_json()["settings"]["target"], "server")

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

        # senza password va bene sui client, ma non con target server
        r = self.client.post("/api/winprofiles/preview", headers=self.h,
                             json={"settings": {"admin_user": "tec", "admin_password": ""}})
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/winprofiles/preview", headers=self.h,
                             json={"settings": {"target": "server", "admin_user": "tec", "admin_password": ""}})
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
            self.assertTrue(set(t["editions"]) <= set(WP.PLATFORMS),
                            t["id"] + ": piattaforme ammesse " + ", ".join(WP.PLATFORMS))
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
        for atteso in ("win-postazione-aziendale", "win-pc-singolo", "winserver", "win-laboratorio",
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

    def test_target_dei_modelli(self):
        """Ogni modello windows dichiara un tipo di Windows valido e non contiene voci
        incompatibili (docs/API.md, sezioni 11 e 12): winserver è server, i modelli generici sono
        client, quelli per edizione dichiarano il proprio (10-ltsc, 11-ltsc, server)."""
        generici = ("win-postazione-aziendale", "win-pc-singolo", "win-laboratorio",
                    "win-minimale", "win-privacy", "win-prestazioni")
        indice = {t["id"]: t for t in catalogo()["items"]}
        if not indice:
            self.skipTest("catalogo delle ottimizzazioni non installato")
        for pid in self.ids:
            s = WP.load_preset(pid)["settings"]
            atteso = s.get("target")
            self.assertIn(atteso, WP.TARGETS, pid + ": tipo di Windows sbagliato")
            if pid in generici:
                self.assertEqual(atteso, "client", pid + ": i modelli generici sono per i client")
            if pid == "winserver":
                self.assertEqual(atteso, "server", pid + ": tipo di Windows sbagliato")
            if not WP.target_ha_store(atteso):
                self.assertEqual(s.get("remove_apps") or [], [],
                                 pid + ": senza Microsoft Store non si rimuovono app")
            fuori = [t for t in (s.get("tweaks") or [])
                     if t in indice and not WP.tweak_compatibile(indice[t], atteso)]
            self.assertEqual(fuori, [], f"{pid}: voci incompatibili con il tipo {atteso}: {fuori}")
            # il modello deve restare valido: la validazione rifiuta le voci incompatibili
            WP.validate(dict(s, admin_password=s.get("admin_password") or "PasswordDiProva1"))
        server = WP.load_preset("winserver")["settings"]
        self.assertNotIn("ricerca-solo-locale", server["tweaks"])
        self.assertIn("attiva-desktop-remoto", server["tweaks"])
        self.assertEqual(server["remove_apps"], [],
                         "winserver non deve rimuovere app: su Server non ci sono")


# ------------------------------------------------- tipo di Windows: client oppure server (sez. 11)

class TargetClientServerTest(unittest.TestCase):
    def setUp(self):
        self.cat = catalogo()
        if not self.cat["items"]:
            self.skipTest("catalogo delle ottimizzazioni non installato")
        self.solo_client = [t for t in self.cat["items"] if "server" not in t["editions"]]
        self.da_server = [t for t in self.cat["items"] if "server" in t["editions"]]

    def test_ogni_voce_ha_una_piattaforma_valida(self):
        """Nessuna voce può restare senza piattaforme: sarebbe invisibile in ogni profilo."""
        for t in self.cat["items"]:
            piattaforme = set(t["editions"]) & set(WP.PLATFORMS)
            self.assertTrue(piattaforme, t["id"] + ": nessuna piattaforma valida in editions")
            self.assertTrue(WP.tweak_compatibile(t, "client") or WP.tweak_compatibile(t, "server"),
                            t["id"] + ": non è compatibile con nessun tipo di Windows")

    def test_catalogo_diviso_davvero(self):
        """La distinzione deve esistere sul serio: voci solo client e voci valide anche su Server."""
        self.assertTrue(self.solo_client, "nessuna voce esclusa da Windows Server")
        self.assertTrue(self.da_server, "nessuna voce valida su Windows Server")
        # le voci che toccano componenti assenti su Server non possono avere "server"
        for tid in ("disattiva-cortana", "disattiva-copilot", "disattiva-widget",
                    "disattiva-servizi-xbox", "niente-esperienze-consumer",
                    "store-senza-aggiornamenti-automatici", "start-senza-consigliati",
                    "barra-applicazioni-a-sinistra", "menu-contestuale-classico",
                    "rimuovi-onedrive", "disattiva-cronologia-attivita",
                    "niente-sincronizzazione-impostazioni"):
            v = WP.tweak(tid)
            if v:
                self.assertNotIn("server", v["editions"], tid + ": non esiste su Windows Server")
        # le voci di sistema devono restare disponibili anche sui server
        for tid in ("telemetria-minima", "servizi-telemetria", "disattiva-indicizzazione",
                    "disattiva-smartscreen", "disattiva-uac", "attiva-desktop-remoto",
                    "niente-riavvio-automatico", "disattiva-smb1"):
            v = WP.tweak(tid)
            if v:
                self.assertIn("server", v["editions"], tid + ": vale anche su Windows Server")

    def test_predefinito_e_elenco(self):
        self.assertEqual(WP.defaults()["target"], "client")
        self.assertEqual(WP.validate(base_settings())["target"], "client")
        elenco = WP.targets_list()
        self.assertEqual([t["id"] for t in elenco], ["client", "10-ltsc", "11-ltsc", "server"])
        for t in elenco:
            self.assertTrue(t["name"])
        self.assertEqual(WP.validate(base_settings(target="server"))["target"], "server")
        with self.assertRaises(ValueError) as ctx:
            WP.validate(base_settings(target="windows-server-2022"))
        self.assertIn("tipo di windows non valido", str(ctx.exception).lower())

    def test_validazione_rifiuta_voce_solo_client_su_server(self):
        t = self.solo_client[0]
        with self.assertRaises(ValueError) as ctx:
            WP.validate(base_settings(target="server", tweaks=[t["id"]]))
        msg = str(ctx.exception)
        self.assertIn(t["id"], msg)                       # quale voce
        self.assertIn("non è compatibile", msg.lower())   # e perché
        self.assertIn("Server", msg)
        # la stessa voce con il target client passa senza problemi
        self.assertEqual(WP.validate(base_settings(target="client", tweaks=[t["id"]]))["tweaks"],
                         [t["id"]])
        # e una voce buona per i server passa con entrambi (nel catalogo non ci sono voci
        # valide solo su Server, ma se ci fossero il target client le rifiuterebbe)
        buona = self.da_server[0]["id"]
        self.assertEqual(WP.validate(base_settings(target="server", tweaks=[buona]))["tweaks"],
                         [buona])

    def test_validazione_rifiuta_voce_solo_server_su_client(self):
        """Voce finta valida solo su Server: con il target client la validazione la rifiuta."""
        finto = os.path.join(TMP, "tweaks-solo-server.json")
        with open(finto, "w", encoding="utf-8") as f:
            json.dump({"categories": [{"id": "prova", "name": "Prova"}],
                       "tweaks": [{"id": "solo-server", "category": "prova", "name": "Solo server",
                                   "description": "Voce di prova valida solo su Windows Server.",
                                   "impact": "sicuro", "editions": ["server"],
                                   "reg": [{"scope": "HKLM", "path": "SOFTWARE\\Prova",
                                            "name": "Valore", "type": "REG_DWORD", "data": "1"}]}]},
                      f)
        vecchio = getattr(C, "WINTWEAKS_FILE", "")
        C.WINTWEAKS_FILE = finto
        try:
            self.assertEqual(WP.validate(base_settings(target="server",
                                                       tweaks=["solo-server"]))["tweaks"],
                             ["solo-server"])
            with self.assertRaises(ValueError) as ctx:
                WP.validate(base_settings(target="client", tweaks=["solo-server"]))
            self.assertIn("solo-server", str(ctx.exception))
            self.assertIn("solo su Windows Server", str(ctx.exception))
        finally:
            C.WINTWEAKS_FILE = vecchio

    def test_generazione_server_salta_le_voci_escluse(self):
        """Con target server l'XML si genera lo stesso e non contiene i comandi delle voci escluse."""
        ids = [t["id"] for t in self.cat["items"]]
        xml, dom = rendi(target="server", tweaks=ids)
        minidom.parseString(xml)
        comandi = []
        for passo, tag, campo in (("specialize", "RunSynchronousCommand", "Path"),
                                  ("oobeSystem", "SynchronousCommand", "CommandLine")):
            sp = passaggio(dom, passo)
            if sp is not None:
                comandi += [testo(uno(dom, campo, c)) for c in sp.getElementsByTagName(tag)]
        testo_comandi = "\n".join(comandi)
        for t in self.solo_client:
            for r in (t.get("reg") or []):
                self.assertNotIn(r["name"] + '" /t', testo_comandi,
                                 f"{t['id']}: valore {r['name']} generato con target server")
            for sv in (t.get("services") or []):
                self.assertNotIn("Services\\" + sv["name"], testo_comandi,
                                 f"{t['id']}: servizio {sv['name']} generato con target server")
            for c in (t.get("commands") or []):
                self.assertNotIn(c, comandi, t["id"] + ": comando generato con target server")
            for f in (t.get("features_enable") or []) + (t.get("features_disable") or []):
                self.assertNotIn("/featurename:" + f, testo_comandi,
                                 f"{t['id']}: funzionalità {f} generata con target server")
        # le voci compatibili invece ci sono
        self.assertIn("AllowTelemetry", testo_comandi)
        self.assertIn("Services\\DiagTrack", testo_comandi)
        # e con il target client le voci solo-client tornano
        xml_client, _ = rendi(target="client", tweaks=ids)
        self.assertIn("AllowCortana", xml_client)
        self.assertNotIn("AllowCortana", xml)

    def test_app_ignorate_con_target_server(self):
        """Le app Appx non hanno senso su Server: nessun comando, ma un commento che lo spiega."""
        xml, dom = rendi(target="server", remove_apps=["Microsoft.BingNews"])
        self.assertNotIn("Remove-AppxPackage", xml)
        self.assertIn("Le app da rimuovere sono state ignorate", xml)
        xml_client, _ = rendi(target="client", remove_apps=["Microsoft.BingNews"])
        self.assertIn("Remove-AppxPackage", xml_client)
        # l'elenco resta scritto nel profilo: tornando al tipo client si ritrova
        self.assertEqual(WP.validate(base_settings(target="server",
                                                   remove_apps=["Microsoft.BingNews"]))["remove_apps"],
                         ["Microsoft.BingNews"])

    def test_generazione_non_alza_su_profilo_incompatibile(self):
        """Un profilo salvato prima della distinzione (o cambiato a mano) continua a generare."""
        t = self.solo_client[0]["id"]
        st = base_settings(tweaks=[t])          # validato con target client
        st["target"] = "server"                 # poi il tecnico cambia tipo di Windows
        xml = WP.render_autounattend({"name": "Vecchio", "settings": st}, "10.10.0.254",
                                     cfg=CFG_FINTA)
        minidom.parseString(xml)


# ------------------------------------------------- edizioni Enterprise LTSC (docs/API.md, sez. 12)

# Voci che toccano componenti che nelle LTSC non ci sono: niente "10-ltsc" né "11-ltsc"
SENZA_LTSC = (
    "disattiva-cortana", "disattiva-copilot", "disattiva-widget", "disattiva-notizie-interessi",
    "disattiva-chat-teams", "disattiva-servizi-xbox", "niente-esperienze-consumer",
    "niente-contenuti-consigliati", "start-senza-consigliati", "niente-spotlight",
    "niente-app-automatiche", "schermata-blocco-pulita", "niente-suggerimenti-windows",
    "store-senza-aggiornamenti-automatici", "niente-ricerca-app-nello-store",
    "disattiva-onedrive", "rimuovi-onedrive", "disattiva-id-pubblicita",
    "niente-notifiche-feedback", "niente-esperienze-personalizzate",
    "niente-evidenziazioni-ricerca", "rinvia-aggiornamenti-funzionalita",
)
# Voci di sistema che nelle LTSC restano valide (telemetria, ricerca, Defender, UAC, rete...)
CON_LTSC = (
    "telemetria-minima", "servizi-telemetria", "ricerca-solo-locale", "disattiva-indicizzazione",
    "disattiva-smartscreen", "disattiva-defender", "disattiva-uac", "attiva-desktop-remoto",
    "niente-riavvio-automatico", "disattiva-smb1", "effetti-visivi-ridotti",
    "piano-prestazioni-elevate", "disattiva-llmnr", "attiva-net-35", "mostra-estensioni-file",
)
# Voci proprie di Windows 11 che non toccano componenti assenti: valgono anche su 11 LTSC
SOLO_11_MA_LTSC = ("barra-applicazioni-a-sinistra", "menu-contestuale-classico", "ricerca-solo-icona")


def voci_solo_11():
    """Voci del catalogo valide solo su Windows 11 con Store (niente 10, niente 11-ltsc)."""
    return [t for t in catalogo()["items"] if t["editions"] == ["11"]]


class LtscTest(unittest.TestCase):
    """Piattaforme "10-ltsc" e "11-ltsc" nel catalogo, nei target e nella generazione."""

    def setUp(self):
        self.cat = catalogo()
        if not self.cat["items"]:
            self.skipTest("catalogo delle ottimizzazioni non installato")
        self.indice = {t["id"]: t for t in self.cat["items"]}

    def test_ogni_voce_ha_almeno_una_piattaforma(self):
        """Nessuna voce può restare senza piattaforme valide né fuori da tutti i tipi di Windows."""
        for t in self.cat["items"]:
            piattaforme = set(t["editions"]) & set(WP.PLATFORMS)
            self.assertTrue(piattaforme, t["id"] + ": nessuna piattaforma valida in editions")
            self.assertEqual(set(t["editions"]) - set(WP.PLATFORMS), set(),
                             t["id"] + ": piattaforme sconosciute in editions")
            compatibili = [x for x in WP.TARGETS if WP.tweak_compatibile(t, x)]
            self.assertTrue(compatibili, t["id"] + ": non è compatibile con nessun tipo di Windows")

    def test_catalogo_diviso_per_ltsc(self):
        """La distinzione LTSC deve esistere sul serio, nei due sensi."""
        con = [t["id"] for t in self.cat["items"]
               if {"10-ltsc", "11-ltsc"} & set(t["editions"])]
        senza = [t["id"] for t in self.cat["items"]
                 if not {"10-ltsc", "11-ltsc"} & set(t["editions"])]
        self.assertTrue(con, "nessuna voce valida sulle edizioni LTSC")
        self.assertTrue(senza, "nessuna voce esclusa dalle edizioni LTSC")
        for tid in SENZA_LTSC:
            v = self.indice.get(tid)
            if v:
                self.assertNotIn("10-ltsc", v["editions"], tid + ": non esiste nelle LTSC")
                self.assertNotIn("11-ltsc", v["editions"], tid + ": non esiste nelle LTSC")
        for tid in CON_LTSC:
            v = self.indice.get(tid)
            if v:
                self.assertIn("10-ltsc", v["editions"], tid + ": vale anche su Windows 10 LTSC")
                self.assertIn("11-ltsc", v["editions"], tid + ": vale anche su Windows 11 LTSC")
        for tid in SOLO_11_MA_LTSC:
            v = self.indice.get(tid)
            if v:
                self.assertEqual(["11", "11-ltsc"], v["editions"],
                                 tid + ": voce di Windows 11 valida anche sulla LTSC")

    def test_ltsc_implica_la_versione_base(self):
        """Una LTSC è pur sempre quel Windows: "10-ltsc" senza "10" (o "11-ltsc" senza "11")
        sarebbe un errore di battitura. Se un giorno servisse una voce solo-LTSC, questa prova va
        cambiata insieme al catalogo."""
        for t in self.cat["items"]:
            if "10-ltsc" in t["editions"]:
                self.assertIn("10", t["editions"], t["id"] + ": 10-ltsc senza 10")
            if "11-ltsc" in t["editions"]:
                self.assertIn("11", t["editions"], t["id"] + ": 11-ltsc senza 11")

    def test_target_ltsc_validi(self):
        elenco = WP.targets_list()
        self.assertEqual([t["id"] for t in elenco], ["client", "10-ltsc", "11-ltsc", "server"])
        for t in elenco:
            self.assertTrue(t["name"])
            self.assertIn("LTSC" if "ltsc" in t["id"] else "Windows", t["name"])
        for t in ("10-ltsc", "11-ltsc"):
            self.assertEqual(WP.validate(base_settings(target=t))["target"], t)
        # i tipi senza Microsoft Store
        self.assertFalse(WP.target_ha_store("10-ltsc"))
        self.assertFalse(WP.target_ha_store("11-ltsc"))
        self.assertFalse(WP.target_ha_store("server"))
        self.assertTrue(WP.target_ha_store("client"))
        with self.assertRaises(ValueError) as ctx:
            WP.validate(base_settings(target="ltsc"))
        self.assertIn("tipo di windows non valido", str(ctx.exception).lower())

    def test_voce_solo_11_rifiutata_con_target_10_ltsc(self):
        """Una voce valida solo su Windows 11 non passa con il target 10-ltsc."""
        solo11 = voci_solo_11()
        self.assertTrue(solo11, "il catalogo non ha voci valide solo su Windows 11")
        t = solo11[0]
        with self.assertRaises(ValueError) as ctx:
            WP.validate(base_settings(target="10-ltsc", tweaks=[t["id"]]))
        msg = str(ctx.exception)
        self.assertIn(t["id"], msg)                        # quale voce
        self.assertIn("non è compatibile", msg.lower())    # e perché
        self.assertIn("Windows 10 Enterprise LTSC", msg)
        self.assertIn("vale solo su Windows 11", msg)
        # la stessa voce con il target client passa senza problemi
        self.assertEqual(WP.validate(base_settings(target="client", tweaks=[t["id"]]))["tweaks"],
                         [t["id"]])
        # e una voce di sistema passa con tutti e due i target LTSC
        for target in ("10-ltsc", "11-ltsc"):
            self.assertEqual(
                WP.validate(base_settings(target=target, tweaks=["telemetria-minima"]))["tweaks"],
                ["telemetria-minima"])
        # su 11-ltsc passano anche le voci proprie di Windows 11 che restano nella LTSC
        for tid in SOLO_11_MA_LTSC:
            if tid in self.indice:
                self.assertEqual(
                    WP.validate(base_settings(target="11-ltsc", tweaks=[tid]))["tweaks"], [tid])
                with self.assertRaises(ValueError):
                    WP.validate(base_settings(target="10-ltsc", tweaks=[tid]))

    def test_generazione_10_ltsc_senza_store_cortana_copilot_widget(self):
        """Con target 10-ltsc l'XML si genera lo stesso, senza i comandi delle voci escluse."""
        ids = [t["id"] for t in self.cat["items"]]
        xml, dom = rendi(target="10-ltsc", tweaks=ids, remove_apps=[])
        minidom.parseString(xml)
        comandi = []
        for passo, tag, campo in (("specialize", "RunSynchronousCommand", "Path"),
                                  ("oobeSystem", "SynchronousCommand", "CommandLine")):
            sp = passaggio(dom, passo)
            if sp is not None:
                comandi += [testo(uno(dom, campo, c)) for c in sp.getElementsByTagName(tag)]
        testo_comandi = "\n".join(comandi)
        # Store, Cortana, Copilot, widget e compagnia non devono generare nulla
        for chiave in ("AllowCortana", "TurnOffWindowsCopilot", "ShowCopilotButton",
                       "AllowNewsAndInterests", "EnableFeeds", "ChatIcon", "AutoDownload",
                       "DisableWindowsConsumerFeatures", "ContentDeliveryManager",
                       "DisableWindowsSpotlightFeatures", "NoUseStoreOpenWith",
                       "DisableFileSyncNGSC", "OneDriveSetup", "Services\\XblAuthManager",
                       "DisabledByGroupPolicy", "DeferFeatureUpdates"):
            self.assertNotIn(chiave, testo_comandi,
                             chiave + ": generato con il target 10-ltsc")
        for t in self.cat["items"]:
            if WP.tweak_compatibile(t, "10-ltsc"):
                continue
            for r in (t.get("reg") or []):
                # chiave e nome insieme: un nome di valore corto (Enabled) comparirebbe da solo
                # anche dentro il nome di un altro valore (HiberbootEnabled)
                self.assertNotIn('%s" /v "%s"' % (r["path"], r["name"]), testo_comandi,
                                 f"{t['id']}: valore {r['name']} generato con target 10-ltsc")
            for sv in (t.get("services") or []):
                self.assertNotIn("Services\\" + sv["name"], testo_comandi,
                                 f"{t['id']}: servizio {sv['name']} generato con target 10-ltsc")
            for c in (t.get("commands") or []):
                self.assertNotIn(c, comandi, t["id"] + ": comando generato con target 10-ltsc")
            for f in (t.get("features_enable") or []) + (t.get("features_disable") or []):
                self.assertNotIn("/featurename:" + f, testo_comandi,
                                 f"{t['id']}: funzionalità {f} generata con target 10-ltsc")
        # le voci di sistema invece ci sono
        self.assertIn("AllowTelemetry", testo_comandi)
        self.assertIn("Services\\DiagTrack", testo_comandi)
        self.assertIn("Services\\WSearch", testo_comandi)
        self.assertIn("EnableSmartScreen", testo_comandi)
        # e con il target client le voci escluse tornano
        xml_client, _ = rendi(target="client", tweaks=ids, remove_apps=[])
        self.assertIn("AllowCortana", xml_client)
        # le voci solo Windows 11 restano fuori anche dal target 11-ltsc quando non sono LTSC
        xml11, _ = rendi(target="11-ltsc", tweaks=ids, remove_apps=[])
        self.assertNotIn("TurnOffWindowsCopilot", xml11)
        self.assertIn("TaskbarAl", xml11)          # barra applicazioni a sinistra: c'è anche su LTSC

    def test_app_ignorate_con_i_target_ltsc(self):
        """Senza Microsoft Store le app Appx non si rimuovono: nessun comando, ma un commento."""
        for target in ("10-ltsc", "11-ltsc"):
            xml, _ = rendi(target=target, remove_apps=["Microsoft.BingNews", "Clipchamp.Clipchamp"])
            self.assertNotIn("Remove-AppxPackage", xml, target)
            self.assertNotIn("Remove-AppxProvisionedPackage", xml, target)
            self.assertIn("Le app da rimuovere sono state ignorate", xml, target)
            self.assertIn("LTSC", xml, target)
            # l'elenco resta scritto nel profilo: tornando al tipo client si ritrova
            self.assertEqual(
                WP.validate(base_settings(target=target,
                                          remove_apps=["Microsoft.BingNews"]))["remove_apps"],
                ["Microsoft.BingNews"])
        xml_client, _ = rendi(target="client", remove_apps=["Microsoft.BingNews"])
        self.assertIn("Remove-AppxPackage", xml_client)

    def test_generazione_non_alza_su_profilo_ltsc_incompatibile(self):
        """Profilo salvato come client e poi passato a LTSC: l'XML si genera lo stesso."""
        st = base_settings(tweaks=["disattiva-cortana", "telemetria-minima"])
        st["target"] = "11-ltsc"
        xml = WP.render_autounattend({"name": "Vecchio", "settings": st}, "10.10.0.254",
                                     cfg=CFG_FINTA)
        minidom.parseString(xml)
        self.assertNotIn("AllowCortana", xml)
        self.assertIn("AllowTelemetry", xml)


# ------------------------------------------------- lingua installata dopo il setup (sezione 13)

def primo_accesso(dom):
    """FirstLogonCommands dell'XML: [{order, cmd, descr}] nell'ordine in cui stanno nel file."""
    flc = uno(dom, "FirstLogonCommands")
    if flc is None:
        return []
    fuori = []
    for c in flc.getElementsByTagName("SynchronousCommand"):
        fuori.append({"order": int(testo(uno(dom, "Order", c)) or 0),
                      "cmd": testo(uno(dom, "CommandLine", c)),
                      "descr": testo(uno(dom, "Description", c))})
    return fuori


def dove(comandi, pezzo):
    """Posizione del primo comando che contiene quel pezzo di testo (-1 se non c'è)."""
    for i, c in enumerate(comandi):
        if pezzo in c:
            return i
    return -1


def lingua(**campi):
    """settings.language_install con l'interruttore acceso e i campi indicati."""
    d = {"enabled": True}
    d.update(campi)
    return d


class LinguaInstallataTest(unittest.TestCase):
    """settings.language_install: le ISO in una lingua sola escono nella lingua voluta.

    È il caso di Windows Server 2022 English, il cui install.wim contiene solo en-US: senza questa
    funzione il pacchetto della lingua italiana va installato a mano dopo ogni installazione.
    """

    def setUp(self):
        C.WINPROFILES_FILE = MIO_WINPROFILES
        if os.path.exists(C.WINPROFILES_FILE):
            os.unlink(C.WINPROFILES_FILE)

    def _ko(self, atteso, **campi):
        with self.assertRaises(ValueError) as ctx:
            WP.validate(base_settings(language_install=lingua(**campi)))
        self.assertIn(atteso.lower(), str(ctx.exception).lower())

    # ---------------------------------------------------------------- elenchi e predefiniti

    def test_elenco_lingue_con_geoid(self):
        """languages_install_list(): lingue più comuni in Italia con il GeoId giusto."""
        el = WP.languages_install_list()
        self.assertEqual({x["tag"]: x["geo_id"] for x in el},
                         {"it-IT": 118, "en-GB": 242, "en-US": 244,
                          "de-DE": 94, "fr-FR": 84, "es-ES": 217})
        self.assertEqual(el[0]["tag"], "it-IT", "l'italiano deve essere il primo della tendina")
        self.assertTrue(all(x.get("name") for x in el))
        # deve essere una copia: chi la modifica non tocca l'elenco del modulo
        el[0]["geo_id"] = 0
        self.assertEqual(WP.languages_install_list()[0]["geo_id"], 118)
        self.assertEqual([s["id"] for s in WP.lang_sources_list()], ["windows-update", "file"])
        self.assertTrue(all(s.get("name") for s in WP.lang_sources_list()))

    def test_predefinito_spento_e_italiano(self):
        li = WP.defaults()["language_install"]
        self.assertFalse(li["enabled"], "di norma non serve: le ISO italiane hanno già la lingua")
        self.assertEqual(li["languages"], ["it-IT"])
        self.assertEqual(li["source"], "windows-update")
        self.assertEqual(li["geo_id"], 118)
        self.assertTrue(li["set_system"])
        # la validazione di un profilo qualsiasi restituisce la stessa forma
        self.assertEqual(WP.validate(base_settings())["language_install"], li)

    # ---------------------------------------------------------------- validazione

    def test_tag_non_valido(self):
        self._ko("lingua da installare non valida", languages=["italiano"])
        self._ko("lingua da installare non valida", languages=["IT-it"])
        self._ko("lingua da installare non valida", languages=["it_IT"])
        self._ko("lingua da installare non valida", languages=["it-IT & shutdown"])
        self._ko("lingua da installare non valida", languages=["it-IT", "en_US"])
        # forme ammesse (anche i tag lunghi in stile BCP-47)
        for buono in ("it-IT", "en-US", "de", "sr-Latn-RS"):
            v = WP.validate(base_settings(language_install=lingua(languages=[buono])))
            self.assertEqual(v["language_install"]["languages"], [buono])

    def test_troppe_lingue(self):
        self._ko("troppe lingue",
                 languages=["it-IT", "en-US", "de-DE", "fr-FR", "es-ES", "pt-PT"])
        cinque = ["it-IT", "en-US", "de-DE", "fr-FR", "es-ES"]
        v = WP.validate(base_settings(language_install=lingua(languages=cinque)))
        self.assertEqual(v["language_install"]["languages"], cinque)
        # i doppioni non contano e non si ripetono
        v = WP.validate(base_settings(language_install=lingua(languages=["it-IT", "it-IT"])))
        self.assertEqual(v["language_install"]["languages"], ["it-IT"])

    def test_indirizzo_non_http(self):
        for cattivo in ("ftp://srv/it.cab", "\\\\srv\\lang\\it.cab", "/srv/pixio/it.cab",
                        "srv/it.cab", "file:///srv/it.cab", 'http://srv/it.cab" & shutdown'):
            self._ko("indirizzo del pacchetto lingua non valido", source="file", file_url=cattivo)
        # con la sorgente "file" l'indirizzo è obbligatorio
        self._ko("indica l'indirizzo", source="file", file_url="")
        buono = "https://10.10.0.254/pxe/lang/it-IT.cab"
        v = WP.validate(base_settings(language_install=lingua(source="file", file_url=buono)))
        self.assertEqual(v["language_install"]["file_url"], buono)

    def test_sorgente_sconosciuta(self):
        self._ko("sorgente della lingua non valida", source="internet")
        self._ko("sorgente della lingua non valida", source="smb")
        self._ko("sorgente della lingua non valida", source="windowsupdate")
        for buona in ("windows-update", "file"):
            campi = {"source": buona}
            if buona == "file":
                campi["file_url"] = "http://10.10.0.254/pxe/lang/it.cab"
            v = WP.validate(base_settings(language_install=lingua(**campi)))
            self.assertEqual(v["language_install"]["source"], buona)

    def test_altri_controlli(self):
        self._ko("geoid", geo_id=999999)
        self._ko("geoid", geo_id=-1)
        self._ko("indica almeno una lingua", languages=[])
        self._ko("tastiera", keyboard="tastiera italiana")
        self._ko("troppo lungo", source="file", file_url="http://10.10.0.254/" + "a" * 600)
        # i controlli sulla forma valgono anche con l'interruttore spento: un valore sbagliato non
        # deve saltare fuori la prima volta che qualcuno accende la funzione
        with self.assertRaises(ValueError) as ctx:
            WP.validate(base_settings(language_install={"enabled": False,
                                                        "languages": ["italiano"]}))
        self.assertIn("lingua da installare non valida", str(ctx.exception).lower())
        # con l'interruttore spento, invece, l'indirizzo mancante non è un errore
        v = WP.validate(base_settings(language_install={"enabled": False, "source": "file"}))
        self.assertEqual(v["language_install"]["source"], "file")

    # ---------------------------------------------------------------- generazione

    def test_spento_nessun_comando(self):
        """Con enabled falso non viene generato niente (docs/API.md, sezione 13)."""
        xml, dom = rendi()
        for pezzo in ("Install-Language", "Set-SystemPreferredUILanguage", "Set-WinHomeLocation",
                      "lang.cab", "curl.exe"):
            self.assertNotIn(pezzo, xml)
        # nemmeno con lingua, sorgente e indirizzo già scritti nel profilo
        xml, _ = rendi(language_install={"enabled": False, "languages": ["it-IT"], "source": "file",
                                         "file_url": "http://10.10.0.254/pxe/lang/it.cab"})
        for pezzo in ("Install-Language", "curl.exe", "add-package", "Set-Culture"):
            self.assertNotIn(pezzo, xml)

    def test_windows_update(self):
        """Sorgente windows-update: Install-Language e poi le impostazioni di sistema."""
        xml, dom = rendi(language_install=lingua(languages=["it-IT"]))
        voci = primo_accesso(dom)
        cmd = [x["cmd"] for x in voci]
        tutto = "\n".join(cmd)
        self.assertIn('powershell -NoProfile -ExecutionPolicy Bypass -Command '
                      '"Install-Language -Language it-IT -CopyToSettings"', cmd)
        for atteso in ("Set-SystemPreferredUILanguage it-IT",
                       "Set-WinUILanguageOverride -Language it-IT",
                       "Set-WinUserLanguageList it-IT -Force",
                       "Set-Culture it-IT",
                       "Set-WinHomeLocation -GeoId 118"):
            self.assertIn('powershell -NoProfile -ExecutionPolicy Bypass -Command "%s"' % atteso,
                          cmd, atteso)
        self.assertNotIn("curl", tutto)
        self.assertNotIn("lang.cab", tutto)
        # ordine giusto: prima si installa la lingua, poi la si imposta
        self.assertLess(dove(cmd, "Install-Language"), dove(cmd, "Set-SystemPreferredUILanguage"))
        self.assertLess(dove(cmd, "Set-SystemPreferredUILanguage"), dove(cmd, "Set-WinHomeLocation"))
        # e i comandi della lingua vengono prima di tutti gli altri
        self.assertEqual(dove(cmd, "Install-Language"), 0)
        self.assertEqual([x["order"] for x in voci], list(range(1, len(voci) + 1)))
        # descrizioni in italiano
        descr = [x["descr"] for x in voci if "Install-Language" in x["cmd"]
                 or "Set-SystemPreferredUILanguage" in x["cmd"]]
        self.assertEqual(len(descr), 2)
        self.assertIn("Installa la lingua it-IT da Windows Update", descr)
        self.assertTrue(all(d and not d.startswith("<") for d in descr))
        self.assertIn("lingua del sistema", " ".join(descr))
        # il commento nell'XML dice a cosa serve
        self.assertIn("Lingua installata al primo accesso", xml)

    def test_windows_update_piu_lingue(self):
        """Più lingue: una Install-Language ciascuna, le impostazioni solo per la prima."""
        xml, dom = rendi(language_install=lingua(languages=["it-IT", "en-US"], geo_id=118))
        cmd = [x["cmd"] for x in primo_accesso(dom)]
        self.assertEqual(len([c for c in cmd if "Install-Language" in c]), 2)
        self.assertLess(dove(cmd, "Install-Language -Language it-IT"),
                        dove(cmd, "Install-Language -Language en-US"))
        self.assertEqual(len([c for c in cmd if "Set-SystemPreferredUILanguage" in c]), 1)
        self.assertIn("Set-SystemPreferredUILanguage it-IT", "\n".join(cmd))
        self.assertNotIn("Set-SystemPreferredUILanguage en-US", "\n".join(cmd))
        # le impostazioni vengono dopo l'installazione di tutte le lingue
        self.assertLess(dove(cmd, "Install-Language -Language en-US"),
                        dove(cmd, "Set-SystemPreferredUILanguage"))

    def test_sorgente_file(self):
        """Sorgente file: curl.exe e dism con l'indirizzo indicato, niente Windows Update."""
        url = "http://10.10.0.254/pxe/lang/it-IT_LanguagePack.cab"
        xml, dom = rendi(language_install=lingua(source="file", file_url=url))
        cmd = [x["cmd"] for x in primo_accesso(dom)]
        tutto = "\n".join(cmd)
        self.assertIn("curl.exe", tutto)
        self.assertIn(url, tutto)
        self.assertIn("dism /online /add-package", tutto)
        self.assertIn("%TEMP%\\lang.cab", tutto)
        self.assertNotIn("Install-Language", tutto)
        self.assertEqual(dove(cmd, "curl.exe"), 0)
        self.assertLess(dove(cmd, "curl.exe"), dove(cmd, "add-package"))
        self.assertLess(dove(cmd, "add-package"), dove(cmd, "Set-SystemPreferredUILanguage"))
        # virgolette: l'indirizzo e il percorso del file stanno fra virgolette doppie, così gli
        # spazi e i caratteri strani di cmd non spezzano il comando
        scarica = cmd[dove(cmd, "curl.exe")]
        self.assertIn('-o "%TEMP%\\lang.cab"', scarica)
        self.assertIn('"%s"' % url, scarica)
        descr = [x["descr"] for x in primo_accesso(dom)][:2]
        self.assertIn("Scarica il pacchetto della lingua it-IT da Pixio", descr)

    def test_indirizzo_con_caratteri_speciali_nell_xml(self):
        """Un indirizzo con & resta valido nell'XML (l'escaping lo fa ElementTree)."""
        url = "https://10.10.0.254/pxe/lang.cab?v=1&lang=it-IT"
        xml, dom = rendi(language_install=lingua(source="file", file_url=url))
        self.assertIn("&amp;", xml)
        minidom.parseString(xml)
        self.assertIn(url, "\n".join(x["cmd"] for x in primo_accesso(dom)))

    def test_senza_impostazioni_di_sistema(self):
        """set_system falso: la lingua si installa e basta."""
        xml, dom = rendi(language_install=lingua(languages=["it-IT"], set_system=False))
        cmd = [x["cmd"] for x in primo_accesso(dom)]
        self.assertIn("Install-Language", "\n".join(cmd))
        for pezzo in ("Set-SystemPreferredUILanguage", "Set-WinUILanguageOverride",
                      "Set-WinUserLanguageList", "Set-Culture", "Set-WinHomeLocation"):
            self.assertNotIn(pezzo, "\n".join(cmd))

    def test_geoid_della_lingua_scelta(self):
        xml, dom = rendi(language_install=lingua(languages=["en-US"], geo_id=244))
        self.assertIn("Set-WinHomeLocation -GeoId 244",
                      "\n".join(x["cmd"] for x in primo_accesso(dom)))
        self.assertEqual(WP.geo_id_di("de-DE"), 94)
        self.assertEqual(WP.geo_id_di("xx-XX"), 118, "lingua fuori elenco: si resta sull'Italia")

    def test_xml_sempre_valido(self):
        """Qualunque combinazione produce un autounattend.xml valido secondo minidom."""
        combinazioni = [
            {"enabled": False},
            lingua(languages=["it-IT"]),
            lingua(languages=["it-IT", "en-GB", "de-DE", "fr-FR", "es-ES"]),
            lingua(languages=["it-IT"], set_system=False),
            lingua(source="file", file_url="http://10.10.0.254/pxe/lang/it.cab"),
            lingua(source="file", file_url="https://10.10.0.254/l.cab?a=1&b=2", set_system=False),
            lingua(languages=["en-US"], geo_id=244, keyboard="0409:00000409"),
        ]
        for li in combinazioni:
            for target in ("client", "server"):
                xml, dom = rendi(language_install=li, target=target, remove_apps=[])
                self.assertEqual(dom.documentElement.tagName, "unattend")
                for passo in ("windowsPE", "specialize", "oobeSystem"):
                    self.assertIsNotNone(passaggio(dom, passo), passo)
                # i comandi al primo accesso restano numerati senza buchi
                ordini = [x["order"] for x in primo_accesso(dom)]
                self.assertEqual(ordini, list(range(1, len(ordini) + 1)), str(li))

    def test_lingua_prima_dei_comandi_del_tecnico(self):
        """La lingua si installa prima delle app, delle ottimizzazioni e dei comandi del profilo."""
        xml, dom = rendi(language_install=lingua(languages=["it-IT"]),
                         remove_apps=["Microsoft.BingNews"],
                         run_commands=["cmd /c echo ciao"],
                         features_enable=["NetFx3"])
        cmd = [x["cmd"] for x in primo_accesso(dom)]
        self.assertEqual(dove(cmd, "Install-Language"), 0)
        for dopo in ("Remove-AppxPackage", "/featurename:NetFx3", "echo ciao"):
            self.assertLess(dove(cmd, "Set-WinHomeLocation"), dove(cmd, dopo), dopo)

    # ---------------------------------------------------------------- modelli

    def test_modello_server_con_lingua_attiva(self):
        """Il modello di Windows Server esce già in italiano anche da una ISO in inglese."""
        ids = preset_windows_ids()
        if not ids:
            self.skipTest("modelli non installati")
        server = [p for p in (WP.load_preset(i) for i in ids)
                  if p["settings"].get("target") == "server"]
        self.assertTrue(server, "manca il modello di Windows Server")
        for p in server:
            li = p["settings"].get("language_install") or {}
            self.assertTrue(li.get("enabled"), p["id"] + ": la lingua deve essere già attiva")
            self.assertEqual(li.get("languages"), ["it-IT"], p["id"])
            self.assertEqual(li.get("source"), "windows-update", p["id"])
            self.assertEqual(li.get("geo_id"), 118, p["id"])
            self.assertTrue(li.get("set_system"), p["id"])
            # la descrizione spiega perché qui serve e negli altri modelli no
            d = p["description"].lower()
            self.assertIn("inglese", d, p["id"] + ": la descrizione deve spiegare a cosa serve")
            self.assertIn("windows update", d, p["id"])
        # gli altri modelli non ce l'hanno: le ISO italiane contengono già la lingua
        for pid in ids:
            s = WP.load_preset(pid)["settings"]
            if s.get("target") == "server":
                continue
            self.assertFalse((s.get("language_install") or {}).get("enabled"),
                             pid + ": con una ISO italiana il pacchetto lingua non serve")

    def test_profilo_dal_modello_server_genera_i_comandi(self):
        ids = [i for i in preset_windows_ids()
               if WP.load_preset(i)["settings"].get("target") == "server"]
        if not ids:
            self.skipTest("modelli non installati")
        pid = ids[0]
        prof = WP.create({"name": "Server dal modello", "preset": pid,
                          "settings": {"admin_password": "PasswordDiProva1"}})
        try:
            self.assertTrue(prof["settings"]["language_install"]["enabled"])
            xml = WP.render_autounattend(prof, "10.10.0.254", cfg=CFG_FINTA)
            minidom.parseString(xml)
            self.assertIn("Install-Language -Language it-IT -CopyToSettings", xml)
            self.assertIn("Set-SystemPreferredUILanguage it-IT", xml)
            self.assertIn("Set-WinHomeLocation -GeoId 118", xml)
        finally:
            WP.delete(prof["id"])


class ApiLinguaTest(unittest.TestCase):
    """GET /api/winprofiles espone languages_install (docs/API.md, sezione 13)."""

    @classmethod
    def setUpClass(cls):
        from pixio import create_app
        from pixio.blueprints import api_winprofile
        cls._etc = (C.ETC_DIR, C.CONFIG_FILE, C.SECRET_FILE)
        C.ETC_DIR = MIO_ETC
        C.CONFIG_FILE = os.path.join(MIO_ETC, "config.json")
        C.SECRET_FILE = os.path.join(MIO_ETC, "secret")
        cls.app = create_app()
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

    def test_languages_install_nell_elenco(self):
        d = self.client.get("/api/winprofiles").get_json()
        self.assertIn("languages_install", d)
        self.assertEqual({x["tag"]: x["geo_id"] for x in d["languages_install"]},
                         {"it-IT": 118, "en-GB": 242, "en-US": 244,
                          "de-DE": 94, "fr-FR": 84, "es-ES": 217})
        self.assertTrue(all(x.get("name") for x in d["languages_install"]))
        self.assertEqual([s["id"] for s in d["lang_sources"]], ["windows-update", "file"])
        self.assertFalse(d["defaults"]["language_install"]["enabled"])
        self.assertEqual(d["defaults"]["language_install"]["languages"], ["it-IT"])

    def test_profilo_con_lingua_via_api(self):
        r = self.client.post("/api/winprofiles", headers=self.h, json={
            "name": "Server inglese", "settings": {
                "admin_user": "amministratore", "admin_password": "Pw1234567",
                "target": "server",
                "language_install": {"enabled": True, "languages": ["it-IT"],
                                     "source": "windows-update"}}})
        self.assertEqual(r.status_code, 201, r.get_json())
        pid = r.get_json()["id"]
        self.assertTrue(r.get_json()["settings"]["language_install"]["enabled"])
        x = self.client.post("/api/winprofiles/%s/preview" % pid, headers=self.h, json={})
        self.assertEqual(x.status_code, 200)
        self.assertIn("Install-Language -Language it-IT", x.get_json()["xml"])
        # sorgente sconosciuta: errore in italiano, non un 500
        r = self.client.put("/api/winprofiles/" + pid, headers=self.h,
                            json={"settings": {"language_install": {"source": "internet"}}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("sorgente", r.get_json()["error"].lower())
        # indirizzo non http con la sorgente file: stesso trattamento
        r = self.client.put("/api/winprofiles/" + pid, headers=self.h, json={
            "settings": {"language_install": {"source": "file", "file_url": "ftp://srv/it.cab"}}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("indirizzo", r.get_json()["error"].lower())
        self.client.delete("/api/winprofiles/" + pid, headers=self.h)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class NomeComputer(unittest.TestCase):
    """Windows accetta l'asterisco solo da solo: "PC-*" e' un nome illegale e ferma
    l'installazione nel passaggio specialize, cioe' dopo la copia dell'immagine.
    Pixio ha sempre suggerito quella forma, quindi il prefisso lo risolve lui."""

    def test_asterisco_da_solo_resta(self):
        self.assertEqual(WP.risolvi_nome_computer("*"), "*")

    def test_prefisso_diventa_un_nome_vero(self):
        n = WP.risolvi_nome_computer("PC-*")
        self.assertTrue(n.startswith("PC-"))
        self.assertNotIn("*", n)
        self.assertLessEqual(len(n), 15)

    def test_nomi_diversi_a_ogni_generazione(self):
        nomi = {WP.risolvi_nome_computer("PC-*") for _ in range(20)}
        self.assertGreater(len(nomi), 15, "la parte casuale deve essere davvero casuale")

    def test_nome_fisso_non_viene_toccato(self):
        self.assertEqual(WP.risolvi_nome_computer("UFFICIO01"), "UFFICIO01")

    def test_prefisso_lungo_viene_troncato_a_quindici(self):
        n = WP.risolvi_nome_computer("AZIENDA-FILIALE-*")
        self.assertLessEqual(len(n), 15)
        self.assertNotIn("*", n)

    def test_vuoto_o_none(self):
        self.assertEqual(WP.risolvi_nome_computer(""), "")
        self.assertEqual(WP.risolvi_nome_computer(None), "")


class DriverNelPassaggioGiusto(unittest.TestCase):
    """PnpCustomizationsNonWinPE vale solo in auditSystem e offlineServicing, e in specialize
    la rete del sistema appena installato puo' non essere pronta: i driver si chiedono in
    windowsPE, dove la condivisione e' gia' montata dallo script di Pixio."""

    def setUp(self):
        svuota_driver()

    def tearDown(self):
        svuota_driver()

    def test_driver_in_windowspe_e_non_in_specialize(self):
        cartella_driver("Rete", {"buono.inf": INF_COMPLETO, "buono.sys": "x", "buono.cat": "x"})
        prof = {"name": "Prova driver", "settings": {"drivers_from_pixio": True}}
        xml = WP.render_autounattend(prof, "10.10.0.254", cfg=CFG_FINTA)
        self.assertIn("Microsoft-Windows-PnpCustomizationsWinPE", xml)
        self.assertNotIn("Microsoft-Windows-PnpCustomizationsNonWinPE", xml)
        dom = minidom.parseString(xml)
        for s in dom.getElementsByTagName("settings"):
            if s.getAttribute("pass") == "windowsPE":
                nomi = [c.getAttribute("name") for c in s.getElementsByTagName("component")]
                self.assertIn("Microsoft-Windows-PnpCustomizationsWinPE", nomi)


class PercorsiDriverNelFileDiRisposta(unittest.TestCase):
    """docs/API.md, sezione 24: nel file di risposta finiscono solo le cartelle che il programma di
    installazione puo' percorrere per intero.

    Il guasto vero: il file di risposta scriveva la sola radice \\<ip>\pxe\drivers, il setup scendeva
    fino a RAID_drivers\iaStorVD.inf, non trovava RstMwService.exe e chiudeva tutto con 0x80070002 ->
    0xC190011F, senza toccare il disco."""

    def setUp(self):
        svuota_driver()

    def tearDown(self):
        svuota_driver()

    def percorsi(self, dom):
        pnp = componente(dom, "windowsPE", "Microsoft-Windows-PnpCustomizationsWinPE")
        if pnp is None:
            return []
        return [testo(x) for x in pnp.getElementsByTagName("Path")]

    def test_la_radice_della_libreria_non_si_scrive_mai(self):
        """Il percorso di prima (\\ip\pxe\drivers) non deve comparire nemmeno con la libreria sana."""
        cartella_driver("Rete", {"buono.inf": INF_COMPLETO, "buono.sys": "x", "buono.cat": "x"})
        xml, dom = rendi()
        self.assertIn("\\\\10.10.0.254\\pxe\\drivers\\Rete", xml)
        self.assertNotIn("<Path>\\\\10.10.0.254\\pxe\\drivers</Path>", xml)
        self.assertEqual(self.percorsi(dom), ["\\\\10.10.0.254\\pxe\\drivers\\Rete"])

    def test_pacchetto_incompleto_non_arriva_al_setup(self):
        """Il caso vero: un .inf che dichiara un file che non c'e' tiene fuori la sua cartella."""
        cartella_driver("RAID_drivers", {"iaStorVD.inf": INF_INCOMPLETO, "rotto.sys": "x",
                                         "rotto.cat": "x"})
        xml, dom = rendi()
        self.assertEqual(self.percorsi(dom), [])
        self.assertNotIn("RAID_drivers", "".join(self.percorsi(dom)))
        # niente componente e niente DriverPaths vuoto, ma il motivo resta scritto nel file
        self.assertNotIn("<DriverPaths", xml)
        self.assertNotIn("PnpCustomizationsWinPE", xml)
        self.assertIn("0x80070002", xml)
        self.assertIn("RstMwService.exe", xml)

    def test_la_sottocartella_sana_entra_anche_se_la_radice_e_rotta(self):
        """Si spezza fin dove la struttura del pacchetto separa il buono dal rotto, e non oltre."""
        cartella_driver("Misto", {
            "iaStorVD.inf": INF_INCOMPLETO, "rotto.sys": "x", "rotto.cat": "x",
            "Rete/buono.inf": INF_COMPLETO, "Rete/buono.sys": "x", "Rete/buono.cat": "x",
            "Rete/x64/buono.inf": INF_COMPLETO, "Rete/x64/buono.sys": "x", "Rete/x64/buono.cat": "x",
        })
        xml, dom = rendi()
        # una sola voce: Rete e' offribile per intero, non si scende in Rete\x64 (ci pensa il setup)
        self.assertEqual(self.percorsi(dom), ["\\\\10.10.0.254\\pxe\\drivers\\Misto\\Rete"])

    def test_una_cartella_mista_esce_intera_e_lo_dice(self):
        """Un .inf sano che sta accanto a uno rotto si perde: <Path> accetta una cartella, non un file."""
        cartella_driver("Misto", {
            "Drivers/iaStorVD.inf": INF_INCOMPLETO, "Drivers/rotto.sys": "x", "Drivers/rotto.cat": "x",
            "Drivers/buono.inf": INF_COMPLETO, "Drivers/buono.sys": "x", "Drivers/buono.cat": "x",
        })
        xml, dom = rendi()
        self.assertEqual(self.percorsi(dom), [])
        self.assertIn("iaStorVD.inf", xml)

    def test_piu_percorsi_hanno_keyvalue_progressivo(self):
        """Con piu' voci un wcm:keyValue fisso "1" produrrebbe un XML che il setup rifiuta."""
        cartella_driver("Rete", {"buono.inf": INF_COMPLETO, "buono.sys": "x", "buono.cat": "x"})
        cartella_driver("Storage", {"buono.inf": INF_COMPLETO, "buono.sys": "x", "buono.cat": "x"})
        xml, dom = rendi()
        pnp = componente(dom, "windowsPE", "Microsoft-Windows-PnpCustomizationsWinPE")
        voci = pnp.getElementsByTagName("PathAndCredentials")
        self.assertEqual(len(voci), 2)
        self.assertEqual([v.getAttribute("wcm:keyValue") for v in voci], ["1", "2"])
        # le credenziali si ripetono in ogni voce, come vuole lo schema
        for v in voci:
            self.assertEqual(testo(uno(dom, "Username", v)), "pxe")
            self.assertEqual(testo(uno(dom, "Password", v)), "segreta")

    def test_massimalita_non_si_spezza_quando_non_serve(self):
        """Tre sottocartelle sane sotto la stessa cartella danno UNA voce, non tre."""
        cartella_driver("HP", {"cp1/buono.inf": INF_COMPLETO, "cp1/buono.sys": "x", "cp1/buono.cat": "x",
                               "cp2/buono.inf": INF_COMPLETO, "cp2/buono.sys": "x", "cp2/buono.cat": "x",
                               "cp3/buono.inf": INF_COMPLETO, "cp3/buono.sys": "x", "cp3/buono.cat": "x"})
        xml, dom = rendi()
        self.assertEqual(self.percorsi(dom), ["\\\\10.10.0.254\\pxe\\drivers\\HP"])

    def test_ordine_deterministico(self):
        """Due generazioni con la stessa libreria devono dare byte identici."""
        cartella_driver("Zeta", {"buono.inf": INF_COMPLETO, "buono.sys": "x", "buono.cat": "x"})
        cartella_driver("Alfa", {"buono.inf": INF_COMPLETO, "buono.sys": "x", "buono.cat": "x"})
        a, doma = rendi()
        b, _ = rendi()
        self.assertEqual([p.split("\\")[-1] for p in self.percorsi(doma)], ["Alfa", "Zeta"])
        blocco = lambda x: x[x.index("<DriverPaths>"):x.index("</DriverPaths>")]  # noqa: E731
        self.assertEqual(blocco(a), blocco(b))

    def test_solo_le_cartelle_abbinate_all_immagine(self):
        """apply_to vale anche per i percorsi driver del file di risposta (sezioni 15 e 24)."""
        cartella_driver("SoloServer", {"buono.inf": INF_COMPLETO, "buono.sys": "x", "buono.cat": "x"},
                        flags={"apply_to": {"mode": "groups", "groups": ["Windows Server"], "isos": []}})
        cartella_driver("Tutte", {"buono.inf": INF_COMPLETO, "buono.sys": "x", "buono.cat": "x"})
        xml, dom = rendi(iso={"slug": "win11", "group": "Windows"})
        self.assertEqual([p.split("\\")[-1] for p in self.percorsi(dom)], ["Tutte"])
        xml2, dom2 = rendi(iso={"slug": "srv", "group": "Windows Server"})
        self.assertEqual([p.split("\\")[-1] for p in self.percorsi(dom2)], ["SoloServer", "Tutte"])

    def test_setup_offer_mai_e_sempre(self):
        """"mai" tiene la cartella fuori dal file di risposta, "sempre" la scrive anche se e' rotta."""
        cartella_driver("Rotta", {"iaStorVD.inf": INF_INCOMPLETO, "rotto.sys": "x", "rotto.cat": "x"},
                        flags={"setup_offer": "sempre"})
        cartella_driver("Sana", {"buono.inf": INF_COMPLETO, "buono.sys": "x", "buono.cat": "x"},
                        flags={"setup_offer": "mai"})
        xml, dom = rendi()
        self.assertEqual([p.split("\\")[-1] for p in self.percorsi(dom)], ["Rotta"])

    def test_libreria_vuota_nessun_componente(self):
        """Requisito: meglio un'installazione senza driver aggiunti che una che abortisce."""
        xml, dom = rendi()
        self.assertIsNone(componente(dom, "windowsPE", "Microsoft-Windows-PnpCustomizationsWinPE"))
        self.assertNotIn("<DriverPaths", xml)
        self.assertIn("Nessun percorso driver scritto", xml)

    def test_driver_disattivati_nessun_commento(self):
        cartella_driver("Rete", {"buono.inf": INF_COMPLETO, "buono.sys": "x", "buono.cat": "x"})
        xml, dom = rendi(drivers_from_pixio=False)
        self.assertNotIn("PnpCustomizations", xml)
        self.assertNotIn("Nessun percorso driver scritto", xml)

    def test_due_trattini_nel_nome_non_rompono_il_commento(self):
        """"--" dentro un commento XML e' vietato: il file verrebbe rifiutato alla rilettura."""
        cartella_driver("Rete--vecchia", {"iaStorVD.inf": INF_INCOMPLETO, "rotto.sys": "x",
                                          "rotto.cat": "x"})
        xml, dom = rendi()          # rendi() rilegge l'XML con minidom: se il commento e' rotto solleva
        self.assertIn("Nessun percorso driver scritto", xml)
        self.assertNotIn("--vecchia", xml)

    def test_nomi_con_spazi_e_parentesi(self):
        """"Matrox G200eW (Nuvoton) WDDM 2.0" esiste davvero: il valore e' testo XML, niente virgolette."""
        cartella_driver("Matrox G200eW (Nuvoton) WDDM 2.0",
                        {"buono.inf": INF_COMPLETO, "buono.sys": "x", "buono.cat": "x"})
        xml, dom = rendi()
        self.assertEqual(self.percorsi(dom),
                         ["\\\\10.10.0.254\\pxe\\drivers\\Matrox G200eW (Nuvoton) WDDM 2.0"])
