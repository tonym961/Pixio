"""Test dei profili di personalizzazione Debian (docs/API.md, sezione 9).

Esecuzione: cd /opt/pixio && python3 -m unittest tests.test_debprofile
Non serve l'helper privilegiato né il servizio delle risposte reale: entrambi vengono sostituiti da finti.
"""
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

os.environ["PIXIO_NO_BACKGROUND"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pixio import config as C  # noqa: E402
from pixio import privileged  # noqa: E402

TMP = tempfile.mkdtemp(prefix="pixio-deb-test-")
# I moduli leggono C.<X> al momento della chiamata: basta ridefinire le costanti prima di usarli.
# Percorsi "nostri": vanno riapplicati in ApiTest, perché gli altri file di test ridefiniscono le
# stesse costanti quando vengono importati nello stesso processo (python3 -m unittest tests.*).
MIO_ETC = os.path.join(TMP, "etc")
C.ETC_DIR = MIO_ETC
C.CONFIG_FILE = os.path.join(MIO_ETC, "config.json")
C.SECRET_FILE = os.path.join(MIO_ETC, "secret")
C.VAR_DIR = os.path.join(TMP, "var")
C.JOBS_DIR = os.path.join(C.VAR_DIR, "jobs")
C.UPLOAD_TMP_DIR = os.path.join(C.VAR_DIR, "uploads")
C.CLIENTS_FILE = os.path.join(C.VAR_DIR, "clients.json")
C.CATALOG_FILE = os.path.join(C.VAR_DIR, "catalog.json")
C.DEBPROFILES_FILE = os.path.join(C.VAR_DIR, "debprofiles.json")
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

from pixio.services import debprofile as DP  # noqa: E402

CHIAVE = ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK9pQ0mVrTiXgRyLwZ8dLmNoPqRsTuVwXyZaBcDeFgHi "
          "tecnico@pixio")


def base_settings(**over):
    """Impostazioni minime valide, con le sovrascritture richieste (annidate con la notazione a punto)."""
    s = DP.defaults()
    s["user"]["password"] = "PasswordDiProva1"
    for k, v in over.items():
        parti = k.split("__")
        d = s
        for p in parti[:-1]:
            d = d[p]
        d[parti[-1]] = v
    return s


# ---------------------------------------------------------------- validazione

class ValidazioneTest(unittest.TestCase):
    def test_predefiniti_italiani(self):
        d = DP.defaults()
        self.assertEqual(d["locale"], "it_IT.UTF-8")
        self.assertEqual(d["keyboard"], "it")
        self.assertEqual(d["timezone"], "Europe/Rome")
        self.assertEqual(d["mirror"]["host"], "deb.debian.org")
        # defaults() deve restituire una copia: modificarla non tocca DP.DEFAULTS
        d["hostname"] = "toccato"
        self.assertEqual(DP.DEFAULTS["hostname"], "debian")

    def test_normalizza(self):
        v = DP.validate(base_settings(hostname="SRV-Test", domain="Azienda.Local"))
        self.assertEqual(v["hostname"], "srv-test")
        self.assertEqual(v["domain"], "azienda.local")
        self.assertTrue(v["user"]["sudo"])

    def _ko(self, messaggio_atteso, **over):
        with self.assertRaises(ValueError) as ctx:
            DP.validate(base_settings(**over))
        self.assertIn(messaggio_atteso.lower(), str(ctx.exception).lower())

    def test_hostname_non_valido(self):
        self._ko("nome host", hostname="-non-valido")
        self._ko("nome host", hostname="con spazio")
        self._ko("nome host", hostname="a" * 64)
        self._ko("nome host", hostname="fine-")
        # un a capo iniettato non deve poter spezzare il preseed
        self._ko("nome host", hostname="srv\nd-i passwd/root-login boolean true")

    def test_hostname_obbligatorio(self):
        self._ko("indica il nome host", hostname="")

    def test_password_utente_obbligatoria(self):
        s = DP.defaults()          # utente "admin" senza password
        with self.assertRaises(ValueError) as ctx:
            DP.validate(s)
        self.assertIn("password dell'utente", str(ctx.exception).lower())

    def test_password_root_obbligatoria_se_attivo(self):
        self._ko("password di root", root__enabled=True, root__password="")

    def test_nessun_accesso(self):
        s = base_settings()
        s["user"] = {"fullname": "", "username": "", "password": "", "sudo": False}
        s["root"] = {"enabled": False, "password": ""}
        with self.assertRaises(ValueError) as ctx:
            DP.validate(s)
        self.assertIn("almeno un accesso", str(ctx.exception).lower())

    def test_utente_senza_sudo_e_senza_root(self):
        self._ko("inamministrabile", user__sudo=False)

    def test_utente_riservato(self):
        self._ko("riservato", user__username="root")

    def test_disco_non_plausibile(self):
        self._ko("disco non valido", disk__device="/dev/pippo")
        self._ko("disco non valido", disk__device="sda")
        self._ko("partizione", disk__device="/dev/sda1")
        self._ko("partizione", disk__device="/dev/nvme0n1p2")
        # questi invece devono passare
        for dev in ("auto", "/dev/sda", "/dev/vdb", "/dev/nvme0n1", "/dev/disk/by-id/ata-Samsung_SSD"):
            DP.validate(base_settings(disk__device=dev))

    def test_ricetta_e_filesystem(self):
        self._ko("partizionamento", disk__recipe="raid")
        self._ko("file system", disk__filesystem="zfs")

    def test_crypto_senza_passphrase(self):
        self._ko("passphrase", disk__recipe="crypto", disk__crypto_password="")
        self._ko("troppo corta", disk__recipe="crypto", disk__crypto_password="corta")

    def test_swap_non_valida(self):
        self._ko("swap troppo piccola", disk__swap_mb=64)
        self._ko("swap non valida", disk__swap_mb=999999)

    def test_task_sconosciuto(self):
        self._ko("sconosciuto", tasks=["standard", "kubernetes-desktop"])
        # l'elenco noto passa e i doppioni vengono tolti
        v = DP.validate(base_settings(tasks=["standard", "ssh-server", "standard"]))
        self.assertEqual(v["tasks"], ["standard", "ssh-server"])

    def test_pacchetto_non_valido(self):
        self._ko("nome pacchetto", packages=["sudo", "pacchetto con spazio!"])

    def test_chiave_ssh_non_valida(self):
        self._ko("chiave ssh", ssh_keys=["non-una-chiave"])
        self._ko("chiave ssh", ssh_keys=["ssh-ed25519 AAAA"])
        # apici nel commento: rifiutati perché finirebbero dentro late_command
        self._ko("chiave ssh", ssh_keys=[CHIAVE.replace("tecnico@pixio", "tecni'co")])
        v = DP.validate(base_settings(ssh_keys=[CHIAVE, CHIAVE]))
        self.assertEqual(len(v["ssh_keys"]), 1)

    def test_rete_statica(self):
        self._ko("indirizzo ip", network__mode="static")
        s = base_settings(network__mode="static")
        s["network"].update({"ip": "192.168.1.10", "netmask": "255.255.255.0",
                             "gateway": "192.168.1.1", "dns": ""})
        with self.assertRaises(ValueError) as ctx:
            DP.validate(s)
        self.assertIn("dns", str(ctx.exception).lower())
        s["network"]["dns"] = "192.168.1.1 1.1.1.1"
        v = DP.validate(s)
        self.assertEqual(v["network"]["dns"], "192.168.1.1 1.1.1.1")
        s["network"]["ip"] = "300.1.1.1"
        with self.assertRaises(ValueError):
            DP.validate(s)

    def test_mirror_e_proxy(self):
        self._ko("cartella del mirror", mirror__directory="debian")
        self._ko("proxy", mirror__proxy="proxy.azienda.local")
        DP.validate(base_settings(mirror__proxy="http://proxy.azienda.local:3128"))

    def test_late_command_senza_barra_rovesciata(self):
        self._ko("barra rovesciata", late_command="echo ciao \\\n && echo altro")


# ---------------------------------------------------------------- generatore del preseed

class PreseedTest(unittest.TestCase):
    def righe(self, testo):
        return testo.splitlines()

    def test_struttura_di_base(self):
        t = DP.render_preseed({"name": "Prova", "settings": base_settings()})
        for atteso in (
            "d-i debian-installer/locale string it_IT.UTF-8",
            "d-i keyboard-configuration/xkb-keymap select it",
            "d-i time/zone string Europe/Rome",
            "d-i mirror/http/hostname string deb.debian.org",
            "d-i mirror/http/directory string /debian",
            "d-i netcfg/get_hostname string debian",
            "d-i passwd/username string admin",
            "d-i passwd/user-password password PasswordDiProva1",
            "d-i grub-installer/bootdev string default",
            "d-i finish-install/reboot_in_progress note",
            "popularity-contest popularity-contest/participate boolean false",
        ):
            self.assertIn(atteso, t, f"manca dal preseed: {atteso}")
        # le sezioni commentate in italiano ci sono tutte, in ordine
        titoli = [r for r in self.righe(t) if r.startswith("### ")]
        self.assertEqual(len(titoli), 12)
        self.assertTrue(titoli[0].startswith("### 1. Lingua"))
        self.assertIn("Rete", titoli[2])
        self.assertIn("Mirror", titoli[3])
        self.assertIn("Account", titoli[5])

    def test_nessuna_riga_spezzata(self):
        """Nessun valore deve contenere un a capo: ogni direttiva sta su una riga sola."""
        s = base_settings(hostname="srv-test")
        s["user"]["fullname"] = "Mario\nRossi"          # verrà appiattito su una riga
        s["user"]["password"] = "pass con spazi"
        t = DP.render_preseed({"name": "x", "settings": s})
        self.assertIn("d-i passwd/user-fullname string Mario Rossi", t)
        continua = False
        for r in self.righe(t):
            if continua:
                continua = r.endswith("\\")
                continue
            continua = r.endswith("\\")
            if r.startswith("d-i ") or r.startswith("tasksel ") or r.startswith("popularity-contest "):
                self.assertGreaterEqual(len(r.split()), 3, f"direttiva incompleta: {r}")

    def test_password_hash_usa_crypted(self):
        h = "$6$abcdefgh$0123456789abcdefghijklmnopqrstuvwxyz"
        t = DP.render_preseed({"name": "x", "settings": base_settings(user__password=h)})
        self.assertIn(f"d-i passwd/user-password-crypted password {h}", t)
        self.assertNotIn("d-i passwd/user-password password", t)

    def test_root_disattivato(self):
        t = DP.render_preseed({"name": "x", "settings": base_settings()})
        self.assertIn("d-i passwd/root-login boolean false", t)
        self.assertNotIn("d-i passwd/root-password ", t)

    def test_root_attivo(self):
        t = DP.render_preseed({"name": "x", "settings": base_settings(
            root__enabled=True, root__password="RootDiProva1")})
        self.assertIn("d-i passwd/root-login boolean true", t)
        self.assertIn("d-i passwd/root-password password RootDiProva1", t)
        self.assertIn("d-i passwd/root-password-again password RootDiProva1", t)

    def test_rete_dhcp_e_statica(self):
        t = DP.render_preseed({"name": "x", "settings": base_settings()})
        self.assertIn("d-i netcfg/disable_autoconfig boolean false", t)
        s = base_settings(network__mode="static")
        s["network"].update({"ip": "10.0.0.5", "netmask": "255.255.255.0",
                             "gateway": "10.0.0.1", "dns": "10.0.0.1"})
        t = DP.render_preseed({"name": "x", "settings": s})
        self.assertIn("d-i netcfg/disable_dhcp boolean true", t)
        self.assertIn("d-i netcfg/get_ipaddress string 10.0.0.5", t)
        self.assertIn("d-i netcfg/get_nameservers string 10.0.0.1", t)
        self.assertIn("d-i netcfg/confirm_static boolean true", t)

    def test_ricette_di_partizionamento(self):
        attesi = {
            "atomic": ("d-i partman-auto/method string regular", "d-i partman-auto/choose_recipe select atomic"),
            "home": ("d-i partman-auto/method string regular", "d-i partman-auto/choose_recipe select home"),
            "multi": ("d-i partman-auto/method string regular", "d-i partman-auto/choose_recipe select multi"),
            "lvm": ("d-i partman-auto/method string lvm", "d-i partman-auto-lvm/guided_size string max"),
            "crypto": ("d-i partman-auto/method string crypto", "d-i partman-crypto/passphrase password Passphrase123"),
        }
        for ricetta, righe in attesi.items():
            s = base_settings(disk__recipe=ricetta)
            if ricetta == "crypto":
                s["disk"]["crypto_password"] = "Passphrase123"
            t = DP.render_preseed({"name": ricetta, "settings": s})
            for r in righe:
                self.assertIn(r, t, f"ricetta {ricetta}: manca {r}")
            self.assertIn("d-i partman/confirm boolean true", t)
            self.assertIn("d-i partman/confirm_nooverwrite boolean true", t)
            self.assertIn(f"# Schema scelto: {ricetta}", t)

    def test_ricetta_esplicita_con_swap(self):
        s = base_settings(disk__recipe="multi", disk__swap_mb=4096, disk__filesystem="xfs")
        t = DP.render_preseed({"name": "x", "settings": s})
        self.assertIn("d-i partman-auto/choose_recipe select pixio", t)
        self.assertIn("d-i partman-auto/expert_recipe string", t)
        self.assertIn("4096 4096 4096 linux-swap", t)
        self.assertIn("mountpoint{ /var }", t)
        self.assertIn("mountpoint{ /tmp }", t)
        self.assertIn("mountpoint{ /home }", t)
        self.assertIn("filesystem{ xfs }", t)
        # tutte le righe della ricetta tranne l'ultima finiscono con la barra di continuazione
        blocco = t.split("d-i partman-auto/expert_recipe string", 1)[1].splitlines()
        fine = next(i for i, r in enumerate(blocco) if r.strip() == ".")
        for r in blocco[:fine]:
            self.assertTrue(r.endswith("\\"), f"riga della ricetta senza continuazione: {r!r}")

    def test_ricetta_lvm_con_swap_usa_i_volumi(self):
        s = base_settings(disk__recipe="lvm", disk__swap_mb=2048)
        t = DP.render_preseed({"name": "x", "settings": s})
        self.assertIn("method{ lvm } vg_name{ vg0 }", t)
        self.assertIn("in_vg{ vg0 } lv_name{ swap }", t)
        self.assertIn("in_vg{ vg0 } lv_name{ root }", t)
        self.assertIn("mountpoint{ /boot }", t)

    def test_cancellazione_disco(self):
        t = DP.render_preseed({"name": "x", "settings": base_settings(disk__wipe=True)})
        self.assertIn("d-i partman-lvm/device_remove_lvm boolean true", t)
        self.assertIn("d-i partman-partitioning/confirm_write_new_label boolean true", t)
        t = DP.render_preseed({"name": "x", "settings": base_settings(disk__wipe=False)})
        self.assertIn("d-i partman-lvm/device_remove_lvm boolean false", t)

    def test_disco_indicato_a_mano(self):
        t = DP.render_preseed({"name": "x", "settings": base_settings(disk__device="/dev/nvme0n1")})
        self.assertIn("d-i partman-auto/disk string /dev/nvme0n1", t)
        t = DP.render_preseed({"name": "x", "settings": base_settings(disk__device="auto")})
        self.assertIn("list-devices disk", t)

    def test_tasksel_e_pacchetti(self):
        s = base_settings(tasks=["standard", "ssh-server", "web-server"],
                          packages=["sudo", "vim", "curl"], popcon=True)
        t = DP.render_preseed({"name": "x", "settings": s})
        self.assertIn("tasksel tasksel/first multiselect standard, ssh-server, web-server", t)
        self.assertIn("d-i pkgsel/include string sudo vim curl", t)
        self.assertIn("popularity-contest popularity-contest/participate boolean true", t)

    def test_late_command_chiavi_ssh_e_comandi(self):
        s = base_settings(ssh_keys=[CHIAVE], late_command="apt-get -y install htop\nsystemctl enable ssh")
        t = DP.render_preseed({"name": "x", "settings": s})
        late = [r for r in t.splitlines() if r.startswith("d-i preseed/late_command string ")]
        self.assertEqual(len(late), 1, "late_command deve stare su una riga sola")
        riga = late[0]
        self.assertIn("/home/admin/.ssh", riga)
        self.assertIn("authorized_keys", riga)
        self.assertIn(CHIAVE, riga)
        self.assertIn("chmod 0600", riga)
        self.assertIn("in-target sh -c 'apt-get -y install htop'", riga)
        self.assertIn("in-target sh -c 'systemctl enable ssh'", riga)
        self.assertIn(" ; ", riga)

    def test_late_command_chiavi_su_root_senza_utente(self):
        s = base_settings(ssh_keys=[CHIAVE])
        s["root"] = {"enabled": True, "password": "RootDiProva1"}
        s["user"] = {"fullname": "", "username": "", "password": "", "sudo": False}
        t = DP.render_preseed({"name": "x", "settings": s})
        self.assertIn("/root/.ssh", t)
        self.assertIn("d-i passwd/make-user boolean false", t)

    def test_senza_comandi_finali(self):
        t = DP.render_preseed({"name": "x", "settings": base_settings()})
        self.assertNotIn("d-i preseed/late_command", t)
        self.assertIn("# Nessun comando personalizzato", t)

    def test_riavvio_finale(self):
        t = DP.render_preseed({"name": "x", "settings": base_settings(reboot_after=True)})
        self.assertIn("d-i debian-installer/exit/halt boolean false", t)
        t = DP.render_preseed({"name": "x", "settings": base_settings(reboot_after=False)})
        self.assertIn("d-i debian-installer/exit/halt boolean true", t)


# ---------------------------------------------------------------- CRUD e preset

class CrudTest(unittest.TestCase):
    def setUp(self):
        if os.path.exists(C.DEBPROFILES_FILE):
            os.unlink(C.DEBPROFILES_FILE)

    def test_crea_leggi_aggiorna_elimina(self):
        p = DP.create({"name": "Server di prova", "note": "nota",
                       "settings": {"user": {"password": "Pw1234567"}, "hostname": "srv1"}})
        self.assertEqual(p["id"], "server-di-prova")
        self.assertEqual(p["settings"]["hostname"], "srv1")
        self.assertEqual(p["settings"]["locale"], "it_IT.UTF-8")
        self.assertTrue(os.path.isfile(C.DEBPROFILES_FILE))
        self.assertEqual(len(DP.list_profiles()), 1)

        p2 = DP.update(p["id"], {"settings": {"hostname": "srv2"}})
        self.assertEqual(p2["settings"]["hostname"], "srv2")
        self.assertEqual(p2["settings"]["user"]["password"], "Pw1234567")   # non azzerata

        d = DP.duplicate(p["id"])
        self.assertEqual(d["name"], "Server di prova (copia)")
        self.assertNotEqual(d["id"], p["id"])
        self.assertEqual(len(DP.list_profiles()), 2)

        DP.delete(d["id"])
        self.assertEqual(len(DP.list_profiles()), 1)
        with self.assertRaises(FileNotFoundError):
            DP.delete(d["id"])

    def test_id_duplicati(self):
        a = DP.create({"name": "Uguale", "settings": {"user": {"password": "Pw1234567"}}})
        b = DP.create({"name": "Uguale", "settings": {"user": {"password": "Pw1234567"}}})
        self.assertEqual(a["id"], "uguale")
        self.assertEqual(b["id"], "uguale-2")

    def test_crea_da_preset(self):
        """Il preset fa da base e i campi di settings lo sovrascrivono uno per uno."""
        pr = DP.load_preset("deb-server-minimo")
        self.assertIsNotNone(pr)
        self.assertEqual(pr["kind"], "debian")
        p = DP.create({
            "name": "Da modello",
            "preset": "deb-server-minimo",
            "settings": {"hostname": "srv-nuovo", "user": {"password": "Pw1234567"}},
        })
        s = p["settings"]
        self.assertEqual(s["hostname"], "srv-nuovo")                       # sovrascritto
        self.assertEqual(s["disk"]["recipe"], "lvm")                       # dal preset
        self.assertEqual(s["disk"]["swap_mb"], 4096)                       # dal preset
        self.assertIn("qemu-guest-agent", s["packages"])                   # dal preset
        self.assertEqual(s["user"]["username"], "admin")                   # dal preset
        self.assertEqual(s["user"]["password"], "Pw1234567")               # sovrascritto
        t = DP.render_preseed(p)
        self.assertIn("d-i netcfg/get_hostname string srv-nuovo", t)
        self.assertIn("d-i partman-auto/method string lvm", t)

    def test_preset_inesistente(self):
        with self.assertRaises(ValueError) as ctx:
            DP.create({"name": "x", "preset": "non-esiste"})
        self.assertIn("modello non trovato", str(ctx.exception).lower())

    def test_tutti_i_preset_debian_sono_validi(self):
        with open(os.path.join(C.CODE_DIR, "data", "profile-presets.json"), encoding="utf-8") as f:
            d = json.load(f)
        deb = [p for p in d["presets"] if p["kind"] == "debian"]
        win = [p for p in d["presets"] if p["kind"] == "windows"]
        self.assertGreaterEqual(len(deb), 5)
        self.assertGreaterEqual(len(win), 4)
        for p in deb:
            s = json.loads(json.dumps(p["settings"]))
            s["user"]["password"] = "Pw1234567"
            if s["root"]["enabled"]:
                s["root"]["password"] = "Pw1234567"
            if s["disk"]["recipe"] == "crypto":
                s["disk"]["crypto_password"] = "Passphrase123"
            v = DP.validate(s)                       # non deve alzare eccezioni
            self.assertTrue(DP.render_preseed({"name": p["name"], "settings": v}).startswith("#"))


# ---------------------------------------------------------------- salvataggio come risposta (answers finto)

class FintoAnswers:
    """Sostituto minimo di pixio.services.answers: registra le chiamate ricevute."""

    def __init__(self):
        self.creati = []
        self.aggiornati = []

    def create(self, data):
        self.creati.append(data)
        return {"id": "risposta-finta", "name": data["name"], "kind": data["kind"]}

    def update(self, answer_id, data):
        self.aggiornati.append((answer_id, data))
        return {"id": answer_id, "name": "Risposta esistente", "kind": "debian"}


class SalvaRispostaTest(unittest.TestCase):
    def setUp(self):
        if os.path.exists(C.DEBPROFILES_FILE):
            os.unlink(C.DEBPROFILES_FILE)
        self.finto = FintoAnswers()
        self.mod = types.ModuleType("pixio.services.answers")
        self.mod.create = self.finto.create
        self.mod.update = self.finto.update
        self.originale = sys.modules.get("pixio.services.answers")
        sys.modules["pixio.services.answers"] = self.mod
        import pixio.services
        self.attr_originale = getattr(pixio.services, "answers", None)
        pixio.services.answers = self.mod
        self.profilo = DP.create({"name": "Per la risposta",
                                  "settings": {"user": {"password": "Pw1234567"}, "hostname": "srv-r"}})

    def tearDown(self):
        import pixio.services
        if self.originale is not None:
            sys.modules["pixio.services.answers"] = self.originale
        else:
            sys.modules.pop("pixio.services.answers", None)
        if self.attr_originale is not None:
            pixio.services.answers = self.attr_originale
        else:
            try:
                delattr(pixio.services, "answers")
            except AttributeError:
                pass

    def test_crea_nuova_risposta(self):
        r = DP.save_as_answer(self.profilo["id"])
        self.assertTrue(r["ok"])
        self.assertEqual(r["answer_id"], "risposta-finta")
        self.assertEqual(len(self.finto.creati), 1)
        d = self.finto.creati[0]
        self.assertEqual(d["kind"], "debian")
        self.assertEqual(d["filename"], "preseed.cfg")
        self.assertEqual(d["name"], "Per la risposta")
        self.assertIn("d-i netcfg/get_hostname string srv-r", d["content"])
        self.assertIn("Per la risposta", d["note"])

    def test_aggiorna_risposta_esistente(self):
        r = DP.save_as_answer(self.profilo["id"], "risposta-gia-esistente")
        self.assertEqual(r["answer_id"], "risposta-gia-esistente")
        self.assertEqual(len(self.finto.creati), 0)
        self.assertEqual(len(self.finto.aggiornati), 1)
        aid, d = self.finto.aggiornati[0]
        self.assertEqual(aid, "risposta-gia-esistente")
        self.assertEqual(d["filename"], "preseed.cfg")
        self.assertIn("preseed.cfg generato da", d["content"])

    def test_profilo_inesistente(self):
        with self.assertRaises(FileNotFoundError):
            DP.save_as_answer("non-esiste")


class SenzaAnswersTest(unittest.TestCase):
    """Se services/answers.py non c'è, il messaggio deve essere chiaro e in italiano."""

    def test_messaggio(self):
        import pixio.services
        salvato_mod = sys.modules.pop("pixio.services.answers", None)
        salvato_attr = getattr(pixio.services, "answers", None)
        if salvato_attr is not None:
            delattr(pixio.services, "answers")
        sys.modules["pixio.services.answers"] = None      # fa alzare ImportError all'import
        try:
            with self.assertRaises(RuntimeError) as ctx:
                DP._answers()
            self.assertIn("risposte", str(ctx.exception).lower())
        finally:
            sys.modules.pop("pixio.services.answers", None)
            if salvato_mod is not None:
                sys.modules["pixio.services.answers"] = salvato_mod
            if salvato_attr is not None:
                pixio.services.answers = salvato_attr


# ---------------------------------------------------------------- API

class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pixio import create_app
        from pixio.blueprints import api_debprofile, api_presets
        # la password amministratore va creata nella NOSTRA cartella etc: gli altri file di test
        # si aspettano di trovare la loro configurazione ancora vergine
        cls._etc = (C.ETC_DIR, C.CONFIG_FILE, C.SECRET_FILE)
        C.ETC_DIR = MIO_ETC
        C.CONFIG_FILE = os.path.join(MIO_ETC, "config.json")
        C.SECRET_FILE = os.path.join(MIO_ETC, "secret")
        cls.app = create_app()
        # i blueprint nuovi potrebbero non essere ancora in blueprints/__init__.py: li registro qui
        for mod, nome in ((api_debprofile, "api_debprofile"), (api_presets, "api_presets")):
            if nome not in cls.app.blueprints:
                cls.app.register_blueprint(mod.bp)
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        r = cls.client.post("/api/auth/login", json={"password": "segreta1"})
        assert r.status_code == 200, r.get_json()
        cls.h = {"X-CSRF-Token": r.get_json()["csrf"]}

    @classmethod
    def tearDownClass(cls):
        C.ETC_DIR, C.CONFIG_FILE, C.SECRET_FILE = cls._etc

    def setUp(self):
        if os.path.exists(C.DEBPROFILES_FILE):
            os.unlink(C.DEBPROFILES_FILE)

    # --- preset
    def test_presets_elenco_e_filtro(self):
        r = self.client.get("/api/presets")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertGreaterEqual(len(d["presets"]), 9)
        self.assertEqual(d["warnings"], [])
        for p in d["presets"]:
            for k in ("id", "kind", "name", "description", "settings"):
                self.assertIn(k, p)
            self.assertIn(p["kind"], ("windows", "debian"))
            self.assertTrue(p["description"].strip())

        r = self.client.get("/api/presets?kind=debian")
        deb = r.get_json()["presets"]
        self.assertGreaterEqual(len(deb), 5)
        self.assertTrue(all(p["kind"] == "debian" for p in deb))

        r = self.client.get("/api/presets?kind=windows")
        win = r.get_json()["presets"]
        self.assertGreaterEqual(len(win), 4)
        self.assertTrue(all(p["kind"] == "windows" for p in win))
        # i preset Windows hanno i campi della sezione 8 del contratto
        for p in win:
            for k in ("language", "timezone", "admin_user", "disk", "skip_oobe", "remove_apps"):
                self.assertIn(k, p["settings"], f"{p['id']}: manca {k}")

    def test_presets_kind_non_valido(self):
        r = self.client.get("/api/presets?kind=macos")
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_presets_richiede_login(self):
        anon = self.app.test_client()
        self.assertEqual(anon.get("/api/presets").status_code, 401)

    # --- profili
    def test_elenco_vuoto_con_elenchi_di_supporto(self):
        r = self.client.get("/api/debprofiles")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        for k in ("profiles", "defaults", "tasks", "mirrors", "timezones"):
            self.assertIn(k, d)
        self.assertEqual(d["profiles"], [])
        self.assertEqual(d["defaults"]["timezone"], "Europe/Rome")
        self.assertTrue(any(t["id"] == "ssh-server" for t in d["tasks"]))
        self.assertIn("Europe/Rome", d["timezones"])
        self.assertTrue(any(m["host"] == "deb.debian.org" for m in d["mirrors"]))

    def test_ciclo_completo(self):
        r = self.client.post("/api/debprofiles", headers=self.h, json={
            "name": "Api server", "preset": "deb-server-web",
            "settings": {"hostname": "web1", "user": {"password": "Pw1234567"}},
        })
        self.assertEqual(r.status_code, 201, r.get_json())
        p = r.get_json()
        self.assertEqual(p["settings"]["hostname"], "web1")
        self.assertIn("nginx", p["settings"]["packages"])

        self.assertEqual(self.client.get("/api/debprofiles/" + p["id"]).status_code, 200)
        self.assertEqual(self.client.get("/api/debprofiles/non-esiste").status_code, 404)

        r = self.client.put("/api/debprofiles/" + p["id"], headers=self.h,
                            json={"name": "Api server 2", "settings": {"popcon": True}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["name"], "Api server 2")
        self.assertTrue(r.get_json()["settings"]["popcon"])

        r = self.client.post(f"/api/debprofiles/{p['id']}/preview", headers=self.h, json={})
        self.assertEqual(r.status_code, 200)
        testo = r.get_json()["preseed"]
        self.assertIn("d-i netcfg/get_hostname string web1", testo)
        self.assertIn("popularity-contest popularity-contest/participate boolean true", testo)

        # anteprima con modifiche non salvate
        r = self.client.post(f"/api/debprofiles/{p['id']}/preview", headers=self.h,
                             json={"settings": {"hostname": "provvisorio"}})
        self.assertIn("d-i netcfg/get_hostname string provvisorio", r.get_json()["preseed"])

        r = self.client.post(f"/api/debprofiles/{p['id']}/duplicate", headers=self.h, json={"name": "Copia api"})
        self.assertEqual(r.status_code, 201)
        copia = r.get_json()["id"]

        self.assertEqual(self.client.delete("/api/debprofiles/" + copia, headers=self.h).status_code, 200)
        self.assertEqual(self.client.delete("/api/debprofiles/" + copia, headers=self.h).status_code, 404)

    def test_creazione_non_valida(self):
        r = self.client.post("/api/debprofiles", headers=self.h,
                             json={"name": "Rotto", "settings": {"hostname": "-no-"}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Nome host", r.get_json()["error"])
        r = self.client.post("/api/debprofiles", headers=self.h, json={"name": ""})
        self.assertEqual(r.status_code, 400)

    def test_anteprima_senza_profilo(self):
        r = self.client.post("/api/debprofiles/preview", headers=self.h, json={
            "preset": "deb-desktop-ufficio",
            "settings": {"user": {"password": "Pw1234567"}, "hostname": "pc-prova"},
        })
        self.assertEqual(r.status_code, 200, r.get_json())
        testo = r.get_json()["preseed"]
        self.assertIn("gnome-desktop", testo)
        self.assertIn("d-i netcfg/get_hostname string pc-prova", testo)

        r = self.client.post("/api/debprofiles/preview", headers=self.h, json={"preset": "non-esiste"})
        self.assertEqual(r.status_code, 404)

    def test_csrf_obbligatorio(self):
        r = self.client.post("/api/debprofiles", json={"name": "x"})
        self.assertEqual(r.status_code, 403)

    def test_save_answer_con_answers_finto(self):
        finto = FintoAnswers()
        mod = types.ModuleType("pixio.services.answers")
        mod.create = finto.create
        mod.update = finto.update
        import pixio.services
        originale = sys.modules.get("pixio.services.answers")
        attr = getattr(pixio.services, "answers", None)
        sys.modules["pixio.services.answers"] = mod
        pixio.services.answers = mod
        try:
            r = self.client.post("/api/debprofiles", headers=self.h, json={
                "name": "Con risposta", "settings": {"user": {"password": "Pw1234567"}, "hostname": "srv-api"}})
            pid = r.get_json()["id"]
            r = self.client.post(f"/api/debprofiles/{pid}/save-answer", headers=self.h, json={})
            self.assertEqual(r.status_code, 200, r.get_json())
            d = r.get_json()
            self.assertTrue(d["ok"])
            self.assertEqual(d["answer_id"], "risposta-finta")
            self.assertEqual(d["answer_name"], "Con risposta")
            self.assertEqual(finto.creati[0]["kind"], "debian")
            self.assertEqual(finto.creati[0]["filename"], "preseed.cfg")
            self.assertIn("d-i netcfg/get_hostname string srv-api", finto.creati[0]["content"])

            # con answer_id aggiorna invece di creare
            r = self.client.post(f"/api/debprofiles/{pid}/save-answer", headers=self.h,
                                 json={"answer_id": "vecchia"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(finto.aggiornati[0][0], "vecchia")

            self.assertEqual(
                self.client.post("/api/debprofiles/non-esiste/save-answer", headers=self.h, json={}).status_code,
                404)
        finally:
            if originale is not None:
                sys.modules["pixio.services.answers"] = originale
            else:
                sys.modules.pop("pixio.services.answers", None)
            if attr is not None:
                pixio.services.answers = attr


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


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
