# Pixio — server PXE con GUI web

Pixio trasforma una Debian pulita in un server di boot da rete (BIOS + UEFI) con una interfaccia web in italiano.
Le ISO possono stare su una o più **share Windows/SMB** (montate in sola lettura) e/o nella **libreria locale** del server,
dove si caricano via la share Samba `\\<ip>\iso` o direttamente dalla web UI.

## Installazione (Debian 12/13, come root)
```bash
git clone git@github.com:tonym961/Pixio.git /opt/pixio
/opt/pixio/install.sh
```
Poi apri `http://<ip-del-server>/`: al primo accesso imposti la password dell'amministratore.

## Come funziona
| Componente | Ruolo |
|---|---|
| dnsmasq | proxyDHCP (convive con il DHCP esistente) + TFTP. Manda `undionly.kpxe` ai BIOS e `ipxe.efi` agli UEFI. |
| iPXE (compilato in `ipxe/`) | script embedded: DHCP → `http://<server>/boot.ipxe` |
| nginx | `/pxe/` = contenuto delle ISO montate in loop, file ISO interi, wimboot/memdisk; `/` = GUI e API |
| Flask + gunicorn (`pixio/`) | GUI (SPA in `static/`), API JSON, generazione dinamica del menu iPXE |
| `pixio-helper` (root, via sudo) | le sole operazioni privilegiate: mount CIFS/loop, config di dnsmasq/nginx/samba, riavvio servizi |
| samba | share locali `iso` e `drivers` in scrittura per caricare ISO e driver da Windows (opzionale); share `pxe` in sola lettura per il setup di Windows (opzionale) |

Percorsi: codice `/opt/pixio`, dati `/srv/pixio` (sources, library, cache, tftp, http), config `/etc/pixio`, stato `/var/lib/pixio`, log `/var/log/pixio`.

## Driver per WinPE / setup di Windows
I PC recenti hanno bisogno di driver Ethernet e storage (Intel VMD/RST, NVMe RAID) che il WinPE della ISO non ha.
Pagina **Driver** della GUI (o share `\\<ip>\drivers`): una cartella per pacchetto con i file estratti (.inf .sys .cat).
- "Carica in WinPE all'avvio": i file vengono iniettati via wimboot e caricati con `drvload` prima della rete.
- "Carica prima del setup di Windows": `drvload` dalla share prima di `setup.exe` (richiede "Installazione Windows via rete").

## Tipi di ISO riconosciuti
Windows (installazione via wimboot), WinPE (Hiren's ecc.), Ubuntu e derivate (casper), Debian live / Clonezilla / GParted,
Debian installer, Fedora/RHEL/Rocky/Alma, Fedora live, Arch, SystemRescue, Manjaro, Alpine, openSUSE, ESXi (UEFI).
Per le altre: memdisk (BIOS), sanboot (sperimentale) o ricetta personalizzata dalla GUI. Le ricette sono in `data/recipes.json`.

## Test
```bash
/opt/pixio/tests/qemu-pxe-test.sh bios     # iPXE via TFTP -> menu Pixio (rete user-mode di QEMU)
/opt/pixio/tests/qemu-pxe-test.sh uefi     # idem con OVMF
python3 -m unittest discover -s /opt/pixio/tests
```

## Strumenti
`pixio-admin set-password | apply [dnsmasq|nginx|samba|all] | status | rebuild-ipxe [ip] | logs`
