# Pixio — PXE boot server con GUI (Debian 13 "trixie")

## Contesto macchina (fatti verificati)
- Host: PIXIO, Debian 13 trixie, kernel 6.12, 4 vCPU, 3.8 GiB RAM, 461 GB liberi su /. Root disponibile.
- Rete: interfaccia `ens18`, IP 10.10.0.254/23 (ottenuto via DHCP da 10.10.0.1 -> ESISTE GIA' UN DHCP SERVER in LAN).
- Internet OK (apt, github, boot.ipxe.org). Nessun /dev/kvm (qemu funziona solo TCG).
- Le ISO stanno su una condivisione Samba/Windows (UNC + credenziali configurate dall'utente nella GUI). Il percorso non è ancora noto.

## Obiettivo
Un server PXE completo, pronto all'uso, con una GUI web (italiano) che permette di:
1. Configurare la condivisione Samba (UNC, dominio, utente, password, opzioni) e montarla (CIFS) su `/srv/pixio/iso`.
2. Scansionare le ISO nella share, rilevarne automaticamente il tipo (Windows, Ubuntu, Debian live/installer, Fedora/RHEL-like, Arch/SystemRescue, Clonezilla/GParted, memtest, generico...), abilitarle/disabilitarle nel menu di boot, dare nome e ordine, eventualmente "copia locale" su disco (cache).
3. Generare dinamicamente il menu iPXE (BIOS + UEFI) con la ricetta di boot corretta per ogni ISO.
4. Vedere stato servizi, client PXE visti (MAC/IP/arch), log in tempo reale, spazio disco.
5. Configurare la rete: interfaccia, IP del server, modalità DHCP (proxyDHCP = default, coesiste con DHCP esistente; oppure DHCP completo con range).
6. Login con password (GUI amministrativa esposta in LAN).

## Stack scelto
- **dnsmasq** in modalità proxyDHCP (`dhcp-range=<ip>,proxy`) + TFTP integrato (`/srv/pixio/tftp`). Rilevamento architettura client (option 93): BIOS -> `undionly.kpxe`, UEFI x64 -> `ipxe.efi` (o snponly.efi), UEFI ia32 -> `ipxe32.efi`. Quando il client è già iPXE (user-class "iPXE") -> `http://<ip>/boot.ipxe`.
- **iPXE** compilato da sorgente con script embedded: `dhcp` poi `chain http://${next-server}/boot.ipxe` con fallback all'IP del server; così funziona anche se le opzioni proxyDHCP non arrivano al secondo stadio. Fallback ai binari del pacchetto Debian `ipxe` se la compilazione fallisce.
- **nginx** su :80: serve statico `/pxe/` (ISO montate in loop, file ISO via symlink, wimboot, memdisk, kernel/initrd) e fa reverse proxy della GUI (gunicorn su 127.0.0.1:8080) per tutto il resto, incluso `/boot.ipxe` generato dinamicamente.
- **Samba (smbd)** locale: ri-esporta in sola lettura/guest `/srv/pixio/http/iso` come share `pxe` -> serve a Windows Setup (WinPE) per raggiungere `sources/install.wim`.
- **CIFS client** (`cifs-utils`): unità systemd `srv-pixio-iso.mount` + `.automount` generate dalla GUI, credenziali in `/etc/pixio/smb.cred` (0600).
- **GUI**: Python 3.13 + Flask (pacchetti Debian, nessun pip), gunicorn, template Jinja, CSS proprio (nessuna CDN: la LAN potrebbe non avere internet), JS vanilla. Lingua: italiano. Login con password (hash werkzeug). Servizio systemd `pixio.service`.
- **Stato**: config JSON in `/etc/pixio/config.json`; catalogo ISO in `/var/lib/pixio/catalog.json`; log client in `/var/lib/pixio/clients.json`.
- Layout dati: `/srv/pixio/iso` (mount CIFS), `/srv/pixio/cache` (copie locali), `/srv/pixio/http/iso/<slug>/` (loop mount ISO, ro), `/srv/pixio/http/isofile/<slug>.iso` (symlink al file), `/srv/pixio/http/boot/` (wimboot, memdisk, ipxe bins), `/srv/pixio/tftp/`.
- Codice in `/opt/pixio` (repo git, remoto github.com:tonym961/Pixio), `install.sh` idempotente per riprodurre tutto su una Debian vuota.

## Ricette di boot per tipo ISO (da verificare/raffinare)
- **Windows** (bootmgr + sources/boot.wim): iPXE `kernel wimboot; initrd winpeshl.ini; initrd install.cmd; initrd <iso>/boot/bcd BCD; initrd <iso>/boot/boot.sdi boot.sdi; initrd <iso>/sources/boot.wim boot.wim; boot`. I file iniettati (`winpeshl.ini` -> lancia `install.cmd` che fa `wpeinit`, `net use \\<ip>\pxe\<slug>` e `setup.exe`) vengono messi nella root del ramdisk WinPE da wimboot. UEFI: wimboot funziona anche in UEFI (`kernel wimboot` da ipxe.efi).
- **Ubuntu** (casper/): `kernel <iso>/casper/vmlinuz initrd=initrd boot=casper netboot=url url=http://<ip>/pxe/isofile/<slug>.iso ip=dhcp ---` ; `initrd <iso>/casper/initrd`.
- **Debian live / Clonezilla / GParted** (live/vmlinuz*, live/filesystem.squashfs): `boot=live components fetch=http://<ip>/pxe/iso/<slug>/live/filesystem.squashfs`.
- **Debian installer** (install.amd/vmlinuz + initrd.gz): boot diretto, poi mirror di rete.
- **Fedora/RHEL/Rocky/Alma installer** (images/pxeboot/): `inst.stage2=http://<ip>/pxe/iso/<slug>/ inst.repo=http://<ip>/pxe/iso/<slug>/`.
- **Fedora live** (LiveOS/squashfs.img): `root=live:http://<ip>/pxe/iso/<slug>/LiveOS/squashfs.img rd.live.image`.
- **Arch / SystemRescue** (arch/boot/x86_64/ o sysresccd/): `archisobasedir=arch archiso_http_srv=http://<ip>/pxe/iso/<slug>/ ip=dhcp` (+ ucode initrd).
- **memtest86+**: binario dal pacchetto Debian (`/boot/memtest86+x64.bin` per BIOS, `.efi` per UEFI).
- **Generico** (nessuna ricetta): BIOS -> `memdisk iso raw` (syslinux-common) oppure `sanboot http://<ip>/pxe/isofile/<slug>.iso`; UEFI -> `sanboot` (funziona raramente) + avviso in GUI. Ricetta personalizzata (kernel/initrd/args) editabile dall'utente.

## Test previsti
- `dnsmasq --test`, `nginx -t`, `testparm`.
- Samba locale di prova (share "testiso" su 127.0.0.1) montata via CIFS per simulare la share Windows.
- QEMU (TCG, senza KVM): BIOS e UEFI (OVMF) con `-netdev user,tftp=/srv/pixio/tftp,bootfile=...` per verificare che iPXE parta, arrivi al menu HTTP e carichi un kernel.

## Aggiornamento requisiti (8 set 2026, dall'utente)
- Le ISO arrivano da DUE tipi di sorgente: (a) una o più share Windows/SMB remote, montate via CIFS in sola lettura; (b) una **libreria locale** su Pixio (`/srv/pixio/library`) dove si possono caricare ISO in due modi: **share Samba locale in scrittura** esposta da Pixio (share `pixio-iso`, utente dedicato con password impostata dalla GUI) e **upload dalla web UI** (upload grande a chunk/resumable, con barra di avanzamento).
- NON ricondividere via Samba le ISO montate in loop. Il supporto "installazione Windows" (WinPE deve leggere install.wim via SMB) è un'opzione disattivata di default, con spiegazione nella GUI.
- La GUI è una SPA (JS vanilla, nessuna CDN) servita su http://<ip>/ con API JSON Flask; il menu iPXE a http://<ip>/boot.ipxe.
- Il catalogo unifica le ISO di tutte le sorgenti (colonna "Sorgente": nome share o "Locale").
