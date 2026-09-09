# Pixio — contratto API (JSON) tra backend Flask e SPA

Base: `http://<ip>/`. Tutte le route `/api/*` richiedono sessione (cookie `pixio_session`) TRANNE: `/api/health`, `/api/auth/status`, `/api/auth/login`.
Ogni richiesta POST/PUT/PATCH/DELETE deve inviare l'header `X-CSRF-Token` (valore da `/api/auth/status` o dalla risposta del login).
Errori: `{"error": "messaggio in italiano"}` con status 4xx/5xx. Successo: `{"ok": true, ...}` o l'oggetto richiesto.
Pubbliche (per i client PXE, no cookie): `GET /boot.ipxe`, `GET /boot/<slug>.ipxe`, `GET /pxe/...` (nginx statico).

## Auth
- `GET /api/auth/status` → `{logged_in, password_set, csrf}`
- `POST /api/auth/login {password}` → `{ok, csrf}`. Se `password_set` è false, la prima password inviata diventa quella dell'amministratore (primo avvio).
- `POST /api/auth/logout`
- `POST /api/auth/password {current, new}`

## Sistema
- `GET /api/system/status` →
  `{hostname, version, server_ip, interface, dhcp_mode, services:{dnsmasq:{active:bool, state:"active"|"failed"|..., since}, nginx:{...}, smbd:{...}},
    sources:[{id,name,unc,mounted,iso_count,error}], library:{path, iso_count, samba_enabled, samba_path:"\\\\10.10.0.254\\iso", web_upload_enabled},
    disk:{total, free, used, library_used, cache_used}, catalog:{total, enabled, mounted, unknown}, ipxe:{built:bool, built_at, building:bool},
    clients:{today, total}, jobs_running:int, last_scan, warnings:[str]}`
- `GET /api/system/interfaces` → `[{name, ip}]`
- `POST /api/system/apply {what:"dnsmasq"|"nginx"|"samba"|"all"}` → `{ok, results}`
- `POST /api/system/service {name:"dnsmasq"|"nginx"|"smbd", action:"start"|"stop"|"restart"}`
- `POST /api/system/rebuild-ipxe` → `{ok, job_id}`
- `POST /api/system/power {action:"reboot"|"poweroff"}`

## Sorgenti (share Windows/SMB remote, sola lettura)
- `GET /api/sources` → `[{id, name, unc, domain, username, vers, mounted, iso_count, last_scan, error}]`
- `POST /api/sources {name, unc, domain?, username?, password?, vers?}` → `{ok, source}` (id derivato dal nome; UNC accettato sia `\\srv\share\dir` sia `//srv/share/dir`; senza username = guest). Salva, testa la connessione, monta e avvia una scansione.
- `PUT /api/sources/<id> {name?, unc?, domain?, username?, password?, vers?}` (password vuota/assente = invariata) → rimonta.
- `DELETE /api/sources/<id>` → smonta e dimentica (le ISO di quella sorgente escono dal catalogo).
- `POST /api/sources/<id>/test` → `{ok, output:[str]}`
- `POST /api/sources/<id>/mount` / `POST /api/sources/<id>/umount`

## Catalogo ISO (tutte le sorgenti + libreria locale)
Oggetto ISO:
```
{slug, name, file (nome file), rel_path (relativo alla sorgente), path (assoluto sul server), source ("local" | <source_id>), source_name,
 size, mtime, type (id ricetta es. "windows"|"ubuntu-casper"|"debian-live"|"debian-installer"|"fedora-live"|"redhat-installer"|"archiso"|"alpine"|"opensuse"|"memdisk"|"unknown"),
 type_name (etichetta italiana), category ("os"|"tool"|"unknown"), platforms (["bios","efi"] su cui la ricetta funziona),
 enabled (nel menu), mounted, cache:{wanted:bool, status:"none"|"copying"|"ready"|"error", path, progress},
 group (nome gruppo menu), order (int), custom_recipe (null | {kernel, initrds:[str], cmdline, platforms:[..]}),
 answers ([id risposta], max 8), answer_id (predefinita, dentro answers), answer_manual (bool), answers_info ([{id,name,kind}] in lettura),
 detect:{files:[str], version, label}, warnings:[str], first_seen, last_seen, missing (bool: file sparito dalla sorgente)}
```
- `GET /api/catalog` → `{isos:[...ordinate per group/order/name], last_scan, scanning:bool}`
- `POST /api/catalog/scan` → `{ok, job_id}` (scansione di tutte le sorgenti, rilevamento tipo delle nuove ISO)
- `GET /api/catalog/<slug>` → ISO + `{recipe_preview:{efi:"...script ipxe...", bios:"..."}}`
- `PATCH /api/catalog/<slug> {name?, enabled?, group?, order?, type?, custom_recipe?, cache_wanted?, answers?, answer_id?, answer_manual?}` → ISO aggiornata. Abilitare = montare in loop (+ eventuale copia locale); disabilitare = smontare.
- `POST /api/catalog/<slug>/redetect` → rileva di nuovo il tipo
- `POST /api/catalog/reorder {order:[slug,...]}`
- `DELETE /api/catalog/<slug>` → SOLO per sorgente "local": elimina il file dalla libreria. Per sorgenti remote: 400.

## Menu di boot
- `GET /api/menu` → `{settings:{title, timeout, default, show_local, show_shell, show_reboot, show_memtest, groups:[str], submenus, submenu_threshold, answer_timeout, theme:{bg,accent,fg,muted,logo_text,subtitle,style,resolution}, theme_bg_url, theme_styles, theme_resolutions}, entries:[{slug,name,group,order,enabled,platforms,type_name}], preview:{efi:"#!ipxe ...", bios:"#!ipxe ..."}}`
- `PUT /api/menu {settings}` → `{ok}`
- `GET /boot.ipxe` (pubblico) → script iPXE del menu, generato per il client (`${platform}` e `${mac}` valutati lato client via variabili iPXE: il server genera un unico script con `iseq ${platform} efi` dove serve). Query opzionali per l'anteprima: `?platform=efi|pcbios`.
- `GET /boot/<slug>.ipxe` (pubblico) → script della singola voce (kernel/initrd/boot). Il menu fa `chain http://<ip>/boot/<slug>.ipxe`. `?mac=` opzionale per il log client. `?answer=<id>` sceglie l'installazione automatica fra quelle collegate (vuoto = nessuna); senza il parametro, con due o piu' voci si riceve il menu di scelta (sezione 17).

## Client PXE
- `GET /api/clients` → `[{mac, ip, arch ("bios"|"efi64"|"efi32"|"arm64"|"?"), vendor_class, name, first_seen, last_seen, count, last_entry, auto_boot (slug|null)}]`
- `PATCH /api/clients/<mac> {name?, auto_boot?}` (auto_boot = slug o null: al boot salta il menu e avvia quella voce)
- `DELETE /api/clients/<mac>`

## Log
- `GET /api/logs?source=all|dnsmasq|nginx|pixio&cursor=<str>&limit=200` → `{lines:[{ts (ISO), source, level, msg}], cursor}` (polling ogni 2-3 s dalla SPA)

## Upload ISO dalla web UI (chunked, riprendibile)
- `POST /api/upload/init {filename, size}` → `{upload_id, chunk_size, received:[int]}` (se esiste già un upload con stesso nome+size riprende)
- `PUT /api/upload/<upload_id>/chunk/<n>` (body binario, `Content-Type: application/octet-stream`) → `{ok, received:int}`
- `POST /api/upload/<upload_id>/finish` → `{ok, slug}` (assembla in `/srv/pixio/library/<filename>`, avvia rilevamento)
- `DELETE /api/upload/<upload_id>`
- `GET /api/upload` → `[{upload_id, filename, size, received_bytes, started}]`

## Job in background
- `GET /api/jobs` → `[{id, type ("scan"|"copy"|"detect"|"ipxe-build"), status ("running"|"done"|"error"|"cancelled"), progress (0-100|null), message, target (slug|source_id|null), started, finished}]`
- `GET /api/jobs/<id>`; `POST /api/jobs/<id>/cancel`

## Impostazioni
- `GET /api/settings` → `{network:{interface, server_ip, dhcp_mode, dhcp_range_start, dhcp_range_end, dhcp_netmask, dhcp_router, dhcp_dns, dhcp_lease}, menu:{...}, library:{samba_share_enabled, samba_share_name, web_upload_enabled, samba_password_set:bool}, windows:{smb_export_enabled}, scan:{auto, interval_min}, interfaces:[{name,ip}]}`
- `PUT /api/settings {network?, menu?, library?, windows?, scan?}` → valida, salva, applica (helper `apply all`) → `{ok, applied:{dnsmasq:"ok"|err, nginx:..., samba:...}, warnings:[str]}`
- `POST /api/settings/samba-password {password}` → password dell'utente `pixio` per la share `\\<ip>\iso`

## SPA (static/)
- `static/index.html`, `static/app.js`, `static/style.css` (+ eventuali moduli). Nessuna CDN. Router ad hash: `#/dashboard`, `#/iso`, `#/menu`, `#/driver`, `#/impostazioni`, `#/client`, `#/log`.
- Schermata di login (se `password_set` false: "Imposta la password dell'amministratore"). Wizard primo avvio quando non ci sono sorgenti né ISO locali.
- Lingua italiana. Tema chiaro/scuro automatico. Design come nell'anteprima `docs/preview.html`.

## Driver (libreria driver: `/srv/pixio/http/drivers`, share `\\<ip>\drivers` in scrittura, HTTP `/pxe/drivers/`)
Servizio: `pixio/services/drivers.py` (list_folders, create_folder, delete_folder, set_flags, delete_file, winpe_inject_files, setup_load_folders).
- `GET /api/drivers` → `{root, samba_path:"\\\\10.10.0.254\\drivers", samba_enabled, folders:[{name, files:[{name (relativo, può contenere /), size, mtime}], count, size, inf_count, winpe_files, winpe_size, winpe_inject:bool, setup_load:bool, note, valid_name:bool}]}`
- `POST /api/drivers/folders {name}` → `{ok, folder}` (nome: lettere/numeri/spazi/. _ - ( ) +, max 64; 409 se esiste)
- `PATCH /api/drivers/folders/<name> {winpe_inject?, setup_load?, note?}` → `{ok, folder}`
- `DELETE /api/drivers/folders/<name>` → `{ok}` (cancella cartella e file; conferma nella UI)
- `DELETE /api/drivers/folders/<name>/files/<path:file>` → `{ok}`
- Upload: `POST /api/upload/init {filename, size, kind:"driver", folder:"<name>"}` poi chunk/finish come per le ISO. Per `kind:"driver"` le estensioni ammesse sono: inf sys cat dll exe cab zip msi txt bin dat ini cfg xml json 7z sepolicy; il file finisce in `/srv/pixio/http/drivers/<folder>/<filename>`; se è `.zip`, `finish` lo estrae nella cartella (sotto-cartelle incluse, path traversal rifiutato, max 2 GB estratti) e cancella lo zip; risposta `{ok, extracted:int, files:[str]}`. Senza `kind` (o `kind:"iso"`) comportamento invariato (libreria ISO).
Significato dei flag (spiegazione da mostrare nella UI):
- **winpe_inject** "Carica in WinPE all'avvio": i file .inf/.sys/.cat/.dll al primo livello della cartella vengono iniettati nel WinPE via wimboot e caricati con drvload prima della rete. Serve per schede di rete o controller storage che WinPE non riconosce (max 256 MB totali).
- **setup_load** "Carica prima del setup di Windows": dopo aver mappato la share, drvload ricorsivo di tutti i .inf della cartella prima di setup.exe (richiede "Installazione Windows via rete" attiva).

---

# Contratto delle funzioni aggiunte (settembre 2026)

## 2. Sottomenu per gruppo (menu di boot)
Impostazioni in `menu`: `submenus` ("auto" | "always" | "never", default "auto") e `submenu_threshold` (int, default 8).
Con "auto" i sottomenu compaiono quando le voci avviabili superano la soglia. Il menu principale mostra un elemento per gruppo
(`item grp:<slug-gruppo> Nome gruppo (N)`), che porta a un sottomenu con le voci di quel gruppo più "Torna al menu principale".
Le voci di sistema (memtest, disco locale, shell, riavvia, esci) restano sempre nel menu principale.
`GET /api/menu` restituisce anche `settings.submenus` e `settings.submenu_threshold`; `PUT /api/menu` li accetta e li valida
(`submenus` tra i tre valori, soglia 1-100).

## 2b. Stile del menu di boot (reattivita')
Impostazioni in `menu.theme`: `style` (`testo` | `grafico` | `compatibile`, default `testo`) e `resolution`
(`1024x768` | `800x600` | `640x480`, default `1024x768`).
Decidono *dove* iPXE disegna il menu, che e' il fattore dominante sul tempo di risposta ai tasti:
- `testo` e `grafico` emettono `console -x W -y H ...`: iPXE prende il framebuffer video e spegne la console
  del firmware (`efi_fbcon.c`, `vesafb.c`). Scritture dirette in memoria video, 0 chiamate al firmware.
- `grafico` emette `console ... --picture http://<ip>/pxe/inject/theme/bg-<W>x<H>.png || console -x W -y H ...`:
  il ripiego sta **sulla stessa riga dopo `||`**, cosi' se il PNG non arriva o non si decodifica (`console_cmd.c`
  esce prima di `console_configure()`) iPXE esegue il secondo comando e resta comunque sul framebuffer, invece
  di ricadere sulla console del firmware. Sulla stessa riga il ripiego non costa nulla quando l'immagine c'e';
  su una riga separata costava 30-60 ms a ogni comparsa del menu (misurato).
- `compatibile` non emette nessun `console`: resta la console di testo del firmware (una chiamata per carattere,
  circa 170 per ogni spostamento della selezione). E' il percorso che sui PC con Console Redirection / SOL / BMC / AMT
  produce i ~5 secondi per tasto. Da usare solo se il framebuffer non parte.
Lo sfondo e' un PNG **indicizzato a 256 colori** generato da `theme.render_background()` alla risoluzione scelta
(un terzo dei dati da scompattare rispetto al PNG a colori pieni, -27% di file, differenza visiva impercettibile);
la risoluzione sta nel nome del file, cosi' non si riusa mai uno sfondo di misura sbagliata.
`GET /api/menu` restituisce `settings.theme` sempre completo piu' `settings.theme_bg_url`, `settings.theme_styles`
e `settings.theme_resolutions`; `PUT /api/menu` valida `style` e `resolution` (400 se fuori elenco) e rigenera lo
sfondo solo con lo stile `grafico`.

Tempi misurati in QEMU (TCG, BIOS e UEFI, menu di 11 voci, 5 ripetizioni), comparsa del menu / tasto:
| stile | 1024x768 | 800x600 | 640x480 |
|---|---|---|---|
| testo (framebuffer) | 0,168/0,151 s - 38 ms | - | 0,115/0,128 s - 29 ms |
| grafico (framebuffer + sfondo) | 0,283/0,246 s - 39/38 ms | 0,219/0,213 s - 32 ms | 0,182/0,172 s - 29 ms |
| compatibile (console firmware) | 0,175/0,087 s - 30 ms BIOS, in UEFI nessun cambiamento visibile | | |
Prima dell'ottimizzazione: stile grafico 1024x768 con PNG a colori pieni e senza ripiego 0,463/0,329 s.

## 3. Risposte automatiche (installazioni non presidiate)
Servizio `pixio/services/answers.py`, file in `/var/lib/pixio/answers/<id>/<nome file>`, metadati in `/var/lib/pixio/answers.json`.
Oggetto risposta: `{id, name, kind, files:[{name,size,mtime}], main_file, note, created, used_by:[slug]}`.
`kind`: `windows` (autounattend.xml) | `debian` (preseed.cfg) | `ubuntu` (user-data + meta-data, cloud-init) | `redhat` (kickstart .ks) | `generic`.
- `GET /api/answers` → `{answers:[...], kinds:[{id,name,hint,main_file}]}`
- `POST /api/answers {name, kind, note?, content?}` → crea (201). Con `content` scrive subito il file principale del tipo.
- `GET /api/answers/<id>` → risposta + `content` del file principale (max 512 KB) + `url` pubblico
- `PUT /api/answers/<id> {name?, note?, content?, filename?}` → aggiorna metadati e/o contenuto di un file
- `DELETE /api/answers/<id>`; `DELETE /api/answers/<id>/files/<name>`
- Upload file aggiuntivi: `POST /api/upload/init {filename, size, kind:"answer", folder:"<id risposta>"}` poi chunk/finish (estensioni risposte: xml cfg ks yaml yml txt cmd bat ps1 reg sh conf seed json ini).
- Associazione: `PATCH /api/catalog/<slug> {answer_id: "<id>"|null}` (campo `answer_id` nell'oggetto ISO, `answer_name` in lettura).
  Dalla sezione 17 una ISO puo' averne piu' di una (`answers`): `answer_id` resta la predefinita e continua a funzionare da sola.
- I file sono serviti ai client senza autenticazione su `http://<ip>/answers/<id>/<nome file>` (blueprint pubblico, solo lettura, nomi validati).
Funzioni Python richieste da `answers.py` (usate dal codice di boot):
`list_answers()`, `get(id)`, `create(data)`, `update(id, data)`, `delete(id)`, `folder_path(id)`, `public_url(server_ip, id, filename)`,
`get_for_slug(slug, answer_id=None)` (risposta di una ISO: la predefinita, oppure quella scelta al boot; None se non c'e'), `kernel_args(answer, iso_type, server_ip)` (stringa da aggiungere alla cmdline:
Debian `auto=true priority=critical url=<url preseed>`, Ubuntu `autoinstall ds=nocloud-net;s=<url cartella con slash finale>`,
RHEL `inst.ks=<url kickstart>`, altrimenti ""), `winpe_files(answer, server_ip)` (lista `[(nome_destinazione, url)]` da iniettare nel WinPE, es. `autounattend.xml`).

## 4. Wake-on-LAN e avvio una tantum
- `POST /api/clients/<mac>/wake` → invia il magic packet (UDP broadcast porte 9 e 7 sull'interfaccia configurata) → `{ok, sent:2}`
- `POST /api/clients/wake {macs:[...]}` → risveglio multiplo → `{ok, results:{mac:bool}}`
- `PATCH /api/clients/<mac> {name?, auto_boot?, boot_once?}`: `boot_once` è uno slug valido solo per il prossimo avvio.
  Il campo si azzera quando il client richiede quella voce; ha precedenza su `auto_boot`.
- `GET /api/clients` include `boot_once` e `wol_supported` (sempre true: il pacchetto si invia comunque).
Funzioni richieste da `clients.py`: `wake(mac, broadcast=None)`, `set_boot_once(mac, slug)`, `take_boot_once(mac)` (legge e azzera).

## 5. Copia locale automatica (cache)
Impostazioni in `cache`: `auto` (bool), `min_size_gb` (0.1-100), `only_enabled` (bool), `keep_free_gb` (1-500).
Servizio `pixio/services/autocache.py`: `plan()` → `{to_copy:[slug], to_free:[slug], free_gb, reason}`;
`run(job=None)` esegue il piano (usa `catalog.set_cache`), rispetta `keep_free_gb` eliminando le copie meno usate (ultimo boot più vecchio);
`stats()` → `{cached:int, cached_bytes, free_bytes, candidates:int}`.
- `GET /api/cache` → `{settings, stats, plan}`
- `POST /api/cache/run` → `{ok, job_id}`
- `POST /api/cache/clear {slug?}` → libera una copia o tutte.

## 6. Backup della configurazione e aggiornamento
Servizio `pixio/services/backup.py`:
- `GET /api/backup` → file `.tar.gz` (config.json senza hash password e senza credenziali SMB, catalogo, client, driver metadati, risposte, menu) con `Content-Disposition`
- `POST /api/backup/restore` (multipart o corpo binario del tar.gz) → `{ok, restored:[str], warnings:[str]}`; non tocca le credenziali delle share
- `GET /api/update/check` → `{current, remote, behind:int, dirty:bool, can_update:bool}` (git ls-remote + rev-list)
- `POST /api/update/apply` → `{ok, job_id}`; il job esegue `pixio-helper update` (git pull + install.sh) e riavvia il servizio
Comando helper aggiunto: `pixio-helper update` (git -C /opt/pixio pull --ff-only && /opt/pixio/install.sh), avviato con systemd-run come per la build iPXE.

## 7. HTTPS per la GUI
Impostazioni in `web`: `https_enabled`, `redirect_http`.
Comando helper `pixio-helper cert [<nome host>]`: genera (se manca) un certificato autofirmato in `/etc/pixio/tls/{cert.pem,key.pem}`
valido 10 anni, con SAN per IP e hostname; `pixio-helper apply nginx` aggiunge il server TLS sulla 443 e, se `redirect_http`,
il reindirizzamento dalla 80 (esclusi i percorsi dei client PXE `/boot.ipxe`, `/boot/`, `/pxe/`, `/answers/`, che restano in HTTP).
- `GET /api/system/cert` → `{enabled, exists, subject, not_after, fingerprint}`
- `POST /api/system/cert/regenerate` → rigenera il certificato

## 8. Personalizzatore Windows (profili che generano autounattend.xml)
Servizio `pixio/services/winprofile.py`, profili in `/var/lib/pixio/winprofiles.json`.
Profilo: `{id, name, note, updated, settings:{...}}`. Campi di `settings` (tutti con valore predefinito sensato per l'Italia):
`language` (it-IT), `input_locale`, `timezone` (W. Europe Standard Time), `architecture` (amd64|x86), `edition_index` (indice o nome immagine in install.wim),
`product_key`, `computer_name` (supporta `*` = casuale), `organization`, `owner`,
`admin_user`, `admin_password`, `autologon` (bool), `autologon_count`,
`extra_user` (nome, password, gruppo), `join_domain:{enabled, domain, ou, user, password}`,
`disk:{mode: "auto-uefi"|"auto-bios"|"manuale", wipe (bool), efi_mb, msr_mb, recovery_mb}`,
`skip_oobe` (privacy, EULA, rete, account Microsoft), `bypass_requirements` (TPM/SecureBoot/RAM/CPU per Windows 11),
`disable_defender_prompt`, `hide_files_ext`, `disable_hibernate`, `power_scheme` ("bilanciato"|"prestazioni"),
`remove_apps` (lista di pacchetti Appx da rimuovere), `run_commands` (comandi FirstLogon), `drivers_from_pixio` (bool: aggiunge il percorso della share driver in DriverPaths).
Il generatore produce un `autounattend.xml` valido con i passaggi windowsPE (locale, disco, immagine, product key), specialize (nome computer, dominio, tweak),
oobeSystem (utente locale, autologon, OOBE saltato, FirstLogonCommands). Le password vengono scritte in chiaro nel file (avvisare nella GUI).
- `GET /api/winprofiles` → `{profiles:[...], defaults:{...}, timezones:[...], languages:[...], apps:[{id,name}]}`
- `POST /api/winprofiles {name, settings}` → 201; `GET|PUT|DELETE /api/winprofiles/<id>`
- `POST /api/winprofiles/<id>/preview` → `{xml}` (anteprima del file generato, senza salvare)
- `POST /api/winprofiles/<id>/save-answer {answer_id?}` → genera l'XML e lo salva come risposta di tipo windows (`services/answers.py`), creandola se manca; risposta `{ok, answer_id, answer_name}`

## 9. Personalizzazione Debian e preset
Servizio `pixio/services/debprofile.py`, profili in `/var/lib/pixio/debprofiles.json`, stessa struttura dei profili Windows
(`{id, name, note, updated, settings:{...}}`). Campi di `settings`: `hostname`, `domain`, `locale` (it_IT.UTF-8), `keyboard` (it),
`timezone` (Europe/Rome), `mirror:{host, directory, proxy}`, `suite` (stable/trixie/...),
`root:{enabled, password}` (se disattivato: solo utente con sudo), `user:{fullname, username, password, sudo}`,
`disk:{device (auto|/dev/sda|...), recipe ("atomic"|"home"|"multi"|"lvm"|"crypto"), swap_mb, filesystem (ext4|xfs|btrfs), wipe (bool)}`,
`tasks` (lista tasksel: standard, ssh-server, web-server, gnome-desktop, xfce-desktop...), `packages` (lista aggiuntiva),
`popcon` (bool), `grub_device`, `late_command` (comandi eseguiti a fine installazione), `reboot_after` (bool),
`ssh_keys` (chiavi pubbliche da mettere nell'utente), `network:{mode: dhcp|static, ip, netmask, gateway, dns}`.
Il generatore produce un `preseed.cfg` valido e commentato in italiano; `kernel_args` della risposta lo aggancia con
`auto=true priority=critical url=<url>` (già previsto dalla sezione 3).
- `GET /api/debprofiles` → `{profiles, defaults, tasks, mirrors, timezones}`; `POST` crea; `GET|PUT|DELETE /api/debprofiles/<id>`
- `POST /api/debprofiles/<id>/preview` → `{preseed}`; `POST /api/debprofiles/<id>/save-answer {answer_id?}` → salva come risposta di tipo debian

### Preset (modelli pronti, selezionabili e modificabili)
File `data/profile-presets.json`, sola lettura, con preset per `windows` e `debian`.
Ogni preset: `{id, kind, name, description, settings:{...}}` (le impostazioni sono un profilo completo).
- `GET /api/presets?kind=windows|debian` → `{presets:[...]}`
- `POST /api/winprofiles {name, preset:"<id>", settings?}` e `POST /api/debprofiles {...}`: se `preset` è indicato, i valori del preset
  fanno da base e `settings` li sovrascrive campo per campo. Nella GUI: menu a tendina "Parti da un modello" con descrizione,
  che riempie il form lasciando tutto modificabile prima del salvataggio.
Preset richiesti (almeno): Windows -> "Postazione aziendale" (dominio, OOBE saltato, app inutili rimosse), "PC singolo" (utente locale, accesso automatico),
"Windows Server" (nessun accesso automatico, RDP attivo), "Laboratorio/collaudo" (bypass requisiti, chiave generica, cancellazione disco);
Debian -> "Server minimo" (solo standard + ssh-server, LVM), "Desktop ufficio" (GNOME, utente non root), "Server web" (ssh + nginx + certbot),
"Host Proxmox/virtualizzazione" (partizionamento LVM ampio, nessun desktop), "Postazione tecnica" (Xfce, strumenti di rete).

## 10. Ottimizzazioni Windows in stile nLite (catalogo di tweak)
Catalogo in `data/windows-tweaks.json` (sola lettura, versionato):
```
{
  "categories": [{"id": "privacy", "name": "Privacy e telemetria", "description": "..."}],
  "tweaks": [{
    "id": "disattiva-telemetria",            // slug stabile: finisce nei profili
    "category": "privacy",
    "name": "Disattiva la telemetria",
    "description": "Una frase in italiano che spiega cosa cambia e cosa si perde.",
    "impact": "sicuro" | "attenzione" | "rischioso",
    "editions": ["10", "11"],                 // versioni di Windows su cui ha effetto
    "reg": [{"scope": "HKLM"|"HKCU", "path": "SOFTWARE\\...", "name": "Valore", "type": "REG_DWORD"|"REG_SZ", "data": "0"}],
    "services": [{"name": "DiagTrack", "start": 4}],       // 4 = disabilitato, 3 = manuale
    "commands": ["comando eseguito al primo accesso"],
    "features_enable": ["NetFx3"], "features_disable": ["MicrosoftWindowsPowerShellV2"]
  }]
}
```
Regole di applicazione nell'`autounattend.xml`:
- `reg` con scope HKLM → `RunSynchronousCommand` nel passaggio *specialize* (`reg add ... /f`).
- `reg` con scope HKCU → applicato al profilo predefinito, così vale per tutti gli utenti creati dopo:
  `reg load HKU\PixioDef C:\Users\Default\NTUSER.DAT`, i vari `reg add HKU\PixioDef\...`, poi `reg unload HKU\PixioDef` (un solo carico/scarico per tutto il profilo).
- `services` → `reg add HKLM\SYSTEM\CurrentControlSet\Services\<nome> /v Start /t REG_DWORD /d <start> /f` in *specialize*.
- `commands` e rimozione app → `FirstLogonCommands` in *oobeSystem*; le app vengono rimosse sia per l'utente sia dal provisioning
  (`Get-AppxPackage -AllUsers <id> | Remove-AppxPackage` e `Get-AppxProvisionedPackage -Online | Where-Object DisplayName -eq '<id>' | Remove-AppxProvisionedPackage -Online`).
- `features_enable`/`features_disable` → `dism /online /enable-feature|/disable-feature /featurename:<nome> /norestart` in `FirstLogonCommands`.
Campi nuovi in `settings` del profilo Windows: `tweaks` (lista di id del catalogo), `services_extra` (lista `{name, start}` aggiunti a mano),
`features_enable`, `features_disable`. I campi booleani già esistenti (`disable_defender_prompt`, `hide_files_ext`, `disable_hibernate`, `power_scheme`)
restano e non devono duplicare i tweak equivalenti.
- `GET /api/winprofiles` restituisce anche `tweaks:{categories:[...], items:[...]}` (il catalogo) e `presets` con i tweak preselezionati.
- `POST /api/winprofiles/<id>/preview` deve mostrare nell'XML i comandi generati dai tweak scelti.
Il catalogo deve coprire almeno queste aree, con voci reali e verificate: telemetria e raccolta dati, Cortana e ricerca online nel menu Start,
suggerimenti e pubblicità (contenuti consigliati, schermata di blocco, app installate automaticamente), OneDrive, Copilot e widget,
Esplora file (estensioni, file nascosti, apertura su "Questo PC", barra applicazioni a sinistra, menu contestuale classico di Windows 11),
prestazioni (effetti visivi, SysMain, indicizzazione, avvio rapido, ibernazione, piano energetico), servizi inutili in ambito aziendale (Xbox, Fax, stampa remota),
aggiornamenti (rinvio funzionalità, niente riavvio automatico con utente connesso, driver esclusi da Windows Update),
sicurezza e accesso (UAC, SmartScreen, RDP attivo con firewall, richiesta password al risveglio),
rete (rilevamento rete, IPv6, condivisione password protetta), componenti opzionali (.NET 3.5, Hyper-V, client Telnet, SSH server, sandbox).

## 11. Distinzione client / server nelle ottimizzazioni Windows
Nel catalogo `data/windows-tweaks.json` il campo `editions` diventa l'elenco delle piattaforme su cui la voce ha davvero effetto,
scelte fra `"10"`, `"11"` e `"server"` (Windows Server 2016/2019/2022/2025). Una voce che tocca componenti assenti su Server
(Cortana, Copilot, widget, Xbox, app del Microsoft Store, barra applicazioni di Windows 11, esperienze consumer) NON deve avere `"server"`.
Nel profilo Windows arriva il campo `settings.target`: `"client"` (predefinito) oppure `"server"`.
- La validazione rifiuta i tweak non compatibili con il target scelto, con messaggio in italiano che dice quale voce e perché.
- La generazione dell'autounattend salta comunque le voci non compatibili, senza errori.
- `GET /api/winprofiles` restituisce `targets:[{id,name}]`; ogni voce del catalogo espone `editions` così la GUI può filtrare.
- Nella GUI: selettore "Tipo di Windows" (Client oppure Server) nella sezione Lingua e area; cambiandolo la sezione Ottimizzazioni
  mostra solo le voci compatibili e avvisa se il profilo ne aveva di incompatibili, offrendo di toglierle.
- I preset dichiarano `settings.target` coerente (`win-server` è server, gli altri client) e contengono solo voci compatibili.

## 12. Preset dedicati per edizione (Server, 10 LTSC, 11 Pro, 11 LTSC)
Il campo `editions` del catalogo si estende con due valori: `"10-ltsc"` e `"11-ltsc"`.
Regola: una voce vale per l'edizione LTSC solo se il componente esiste in quell'edizione. Nelle edizioni Enterprise LTSC
(Windows 10 LTSC 2019/2021 e Windows 11 LTSC 2024) NON sono presenti Microsoft Store e le app che ne dipendono, Cortana,
Copilot, widget e notizie, Teams/Chat, Xbox e Game Bar, esperienze consumer, contenuti consigliati del menu Start;
restano invece telemetria, Windows Search, Defender, SmartScreen, UAC, Windows Update, servizi, rete, effetti visivi,
energia e componenti opzionali. Su Windows 11 LTSC valgono anche le voci specifiche di Windows 11 che non riguardano i
componenti assenti (per esempio allineamento della barra applicazioni e menu contestuale classico).
`settings.target` accetta quindi: `"client"` (10 e 11 con Store), `"10-ltsc"`, `"11-ltsc"`, `"server"`.
Il filtro vale sia in validazione sia in generazione sia nella GUI; con i target LTSC e server la rimozione delle app dello Store è disattivata.

### Preset richiesti
Quattro preset Windows tarati per edizione, con `target`, `edition_index`, chiave generica di installazione documentata da Microsoft
(chiave KMS client pubblica, che non attiva nulla da sola: va indicata nella descrizione) e selezione di ottimizzazioni coerente:
- `win11-pro`: Windows 11 Pro. `edition_index` "Windows 11 Pro", chiave `W269N-WFGWX-YVC9B-4J6C9-T83GX`, target client.
  Ottimizzazioni: privacy e telemetria, niente Copilot, widget, contenuti consigliati e app consigliate, Store ripulito
  (rimozione app consumer), Esplora file da tecnico, prestazioni moderate. Niente bypass requisiti.
- `win11-ltsc`: Windows 11 Enterprise LTSC 2024. `edition_index` "Windows 11 Enterprise LTSC 2024", chiave `M7XTQ-FN8P6-TTKYV-9D4CC-J462D`,
  target 11-ltsc, nessuna app da rimuovere, niente voci su Store/Copilot/widget/Teams. Bypass requisiti attivo (le LTSC finiscono spesso
  su macchine più vecchie) e spiegato nella descrizione.
- `win10-ltsc`: Windows 10 Enterprise LTSC 2021. `edition_index` "Windows 10 Enterprise LTSC 2021", chiave `M7XTQ-FN8P6-TTKYV-9D4CC-J462D`,
  target 10-ltsc, nessuna app da rimuovere, nessuna voce solo-Windows-11.
- `winserver`: Windows Server 2022/2025 Standard con interfaccia grafica. `edition_index` "Windows Server 2022 SERVERSTANDARD",
  chiave `VDYBN-27WPP-V4HQT-9VMD4-VMK7H`, target server, desktop remoto attivo, spooler senza connessioni remote, SMB1 disattivato,
  piano prestazioni elevate, niente sospensione, aggiornamenti senza riavvio automatico.
I preset generici esistenti (`win-postazione-aziendale`, `win-pc-singolo`, `win-laboratorio`, `win-minimale`, `win-privacy`, `win-prestazioni`)
restano, con `target` coerente. Nella GUI i preset vanno mostrati raggruppati: prima quelli per edizione, poi quelli generici.

## 13. Lingua di Windows installata in automatico
Nuovo campo `settings.language_install` del profilo Windows:
```
{"enabled": false, "languages": ["it-IT"], "source": "windows-update" | "file",
 "file_url": "", "set_system": true, "geo_id": 118, "keyboard": "it-IT"}
```
Serve alle ISO in inglese (per esempio Windows Server 2022 English, il cui install.wim contiene solo en-US):
l'installazione parte in inglese e si ritrova in italiano da sola, senza interventi manuali.
Comandi generati in `FirstLogonCommands` (verificati sulla documentazione Microsoft, modulo LanguagePackManagement
presente in Windows 10/11 e Windows Server 2019/2022/2025):
- sorgente `windows-update` (nessun file da procurarsi, serve accesso a Windows Update):
  `powershell -NoProfile -ExecutionPolicy Bypass -Command "Install-Language -Language <tag> -CopyToSettings"`
- sorgente `file` (pacchetto già caricato in Pixio, per le reti senza accesso a Windows Update):
  `curl.exe -L -o %TEMP%\lang.cab <file_url>` seguito da
  `dism /online /add-package /packagepath:%TEMP%\lang.cab /norestart`
- con `set_system` a vero, dopo l'installazione della prima lingua:
  `Set-SystemPreferredUILanguage <tag>`, `Set-WinUILanguageOverride -Language <tag>`,
  `Set-WinUserLanguageList <tag> -Force`, `Set-Culture <tag>`, `Set-WinHomeLocation -GeoId <geo_id>`
  (Italia = 118). Il cambio diventa effettivo al riavvio successivo, che il setup fa comunque.
Validazione: tag BCP-47 (`^[a-z]{2}(-[A-Za-z]{2,8})*$`), massimo 5 lingue, `file_url` solo http/https,
`geo_id` intero fra 0 e 100000, sorgente fra i due valori ammessi. Con `enabled` falso non viene generato nulla.
`GET /api/winprofiles` espone `languages_install:[{tag,name,geo_id}]` con l'elenco delle lingue più comuni in Italia
(italiano, inglese, tedesco, francese, spagnolo) per riempire la tendina.
Nella GUI: riquadro "Lingua da installare" nella sezione Lingua e area, con interruttore, scelta della lingua,
scelta della sorgente e campo per l'indirizzo del pacchetto, e una nota che spiega quando serve (ISO in una lingua diversa
da quella voluta) e che con Windows Update il server deve poter raggiungere internet.
Il preset `winserver` deve avere questa funzione già attiva su `it-IT` con sorgente Windows Update.

## 14. Caricamento di cartelle driver e scarto dei file inutili
Motivo: caricando un pacchetto driver arrivano anche eseguibili di installazione, file di lingua e documentazione,
che non servono e sporcano l'elenco; e le cartelle vanno create a mano una per una.

### Upload di cartelle (anche più di una alla volta)
- Il selettore file della pagina Driver usa `webkitdirectory` e `multiple`, e il trascinamento accetta cartelle
  (`DataTransferItem.webkitGetAsEntry`, lettura ricorsiva). Per ogni cartella radice trascinata o scelta,
  Pixio crea la cartella corrispondente (`POST /api/drivers/folders`, nome ripulito, se esiste si riusa) e vi carica dentro i file,
  mantenendo le sottocartelle.
- `POST /api/upload/init` accetta per `kind:"driver"` il campo facoltativo `path`: sottopercorso relativo dentro la cartella
  (per esempio `x64/rt.inf`). Validazione: nessun percorso assoluto, nessun `..`, nessun nome nascosto, massimo 6 livelli,
  ogni segmento con i caratteri ammessi per i nomi file; il file finisce in `DRIVERS_DIR/<folder>/<path>`.
- La barra di avanzamento mostra il totale dei file della coda e la cartella in corso.

### Scarto dei file che non sono driver
- Estensioni utili: `inf sys cat dll bin dat cab sepolicy`. Tutto il resto (in particolare `exe msi zip 7z txt ini pdf htm html chm ico jpg png xml json`)
  viene saltato durante il caricamento di una cartella, con un riepilogo del tipo "12 file ignorati perché non sono driver".
  Casella "carica tutti i file" per forzare l'invio anche del resto (serve quando il pacchetto richiede file accessori).
  Il caricamento del singolo file scelto a mano resta libero come oggi (comprese le estensioni non driver e gli archivi `.zip` che vengono estratti).
- `GET /api/drivers` aggiunge per ogni cartella `useful_files` (numero di file con estensione utile) e `ignored_files`
  (numero di file presenti ma non usati per l'iniezione), e per ogni file il campo `useful` (booleano).
  La GUI mostra i file non utili in grigio con la dicitura "non usato", e nel riepilogo della cartella indica quanti file servono davvero.
- L'iniezione nel WinPE prende `.inf .sys .cat` anche dalle sottocartelle (nomi appiattiti, 32 bit escluse). Le `.dll` sono escluse di proposito: finirebbero in X:\Windows\System32 e nomi comuni come generic.dll possono sovrascrivere file di sistema e far riavviare il PC.

### Azioni su più cartelle insieme
- Casella di selezione su ogni scheda cartella, con barra delle azioni: attiva o disattiva "Carica in WinPE all'avvio"
  e "Carica prima del setup di Windows" sulle cartelle selezionate, oppure eliminale (con conferma che elenca i nomi).
- `PATCH /api/drivers/folders` (senza nome) accetta `{names:[...], winpe_inject?, setup_load?}` e applica la stessa modifica a tutte,
  restituendo `{ok, updated:[nomi], errors:{nome:messaggio}}`.

## 15. Driver abbinati alle ISO (server, PC, singola immagine) e scelta dei file
Motivo: i driver RAID di un server non servono su un PC da ufficio e viceversa; caricarli tutti sempre appesantisce
il WinPE e può creare conflitti.

### Abbinamento
Ogni cartella driver ha il campo `apply_to`:
```
{"mode": "all" | "groups" | "isos",
 "groups": ["Windows", "Windows Server", "Linux", ...],   // gruppi del menu di boot
 "isos": ["slug-iso", ...]}
```
`all` è il valore predefinito e mantiene il comportamento di oggi. Con `groups` la cartella vale solo per le voci
di quei gruppi (per esempio "Windows Server"); con `isos` solo per le immagini indicate.
- `drivers.winpe_inject_files(iso=None)` e `drivers.setup_load_folders(iso=None)` accettano la voce di catalogo che si sta
  avviando (dict con `slug` e `group`) e restituiscono solo le cartelle abbinate; senza argomento si comportano come prima
  (utile per l'anteprima generica).
- `services/winpe.flags(cfg, iso=None)` e la generazione dello script iPXE passano la ISO corrente, così ogni voce del menu
  riceve i propri driver. L'anteprima nella pagina ISO mostra i driver che quella voce riceverà davvero.
- `GET /api/drivers` espone `apply_to` per ogni cartella e l'elenco delle scelte possibili in
  `apply_choices:{groups:[nome], isos:[{slug,name,group,type,enabled}]}` (ripetuto per comodità in `groups`
  e `isos` al primo livello), più `apply_modes:["all","groups","isos"]`. I gruppi sono quelli del menu di boot
  (Impostazioni) più quelli già usati dal catalogo; le ISO sono quelle di tipo `windows`, `windows-legacy`,
  `winpe-tool`, le uniche che avviano un WinPE e quindi ricevono driver.
- `PATCH /api/drivers/folders/<name>` accetta `apply_to`; la PATCH multipla lo accetta allo stesso modo.
  L'oggetto si sostituisce per intero (la SPA rimanda sempre tutti e tre i campi, così cambiando modalità
  non si perde l'elenco già scelto). Validazione: `mode` fra i tre valori, massimo 50 gruppi di 60 caratteri,
  massimo 500 slug validi secondo `config.SLUG_RE`. Un elenco vuoto è ammesso e vuol dire "nessuna immagine":
  la GUI lo segnala con un avviso sulla scheda invece di rifiutare la modifica.

### Scelta dei singoli file (già implementata, da mostrare nella GUI)
`PATCH /api/drivers/folders/<name>/files/<path:file>` con `{excluded: true|false}` esclude o rimette un file
nell'iniezione WinPE; l'elenco degli esclusi sta in `apply_to`-fratello `excluded` della cartella e ogni file
dell'elenco espone `excluded`. Ogni file espone anche `winpe_cand` (potrebbe essere iniettato: estensione
`.inf/.sys/.cat/.dll` fuori dalle sottocartelle di altre architetture) e `winpe` (ci finisce davvero: non è
escluso e nessun altro file con lo stesso nome ha la precedenza). La cartella espone `winpe_files` (quanti ci
finiscono davvero, esclusioni comprese), `winpe_candidates` ed `excluded_files`.

### GUI
- Sulla scheda della cartella: riga "Si applica a" con le tre modalità; scegliendo gruppi o ISO compare l'elenco con caselle,
  e la scheda mostra un riassunto ("solo Windows Server", "solo 2 immagini").
- Nel pannello dei file: casella per ogni file che finirebbe nel WinPE, per escluderlo, con "escludi tutti" e "includi tutti"
  e il conteggio aggiornato dei file iniettati.

## 16. Catalogo ISO organizzato per cartelle
Con 163 immagini in 40 cartelle diverse l'elenco piatto è inutilizzabile. La pagina ISO deve mostrare le immagini
raggruppate come stanno nella sorgente, usando `rel_path` (il percorso relativo dentro la share o la libreria).
- Selettore di vista in alto: **Cartelle** (predefinita) oppure **Elenco** (il comportamento di oggi), scelta ricordata in `localStorage`.
- In vista Cartelle: un albero a due livelli di intestazioni, prima la sorgente (`source_name`), poi il percorso della cartella
  (`Microsoft/Desktop/Win11`), ognuna richiudibile con `<details>` e con il conteggio delle immagini e di quante sono nel menu.
  Le immagini nella radice della sorgente stanno sotto una voce "(radice)". Le cartelle sono ordinate per nome, le immagini dentro per nome.
- Stato di apertura ricordato per cartella (localStorage); pulsanti "Espandi tutto" e "Comprimi tutto".
- Quando c'è un filtro attivo (testo, sorgente, tipo, nel menu) le cartelle con risultati si aprono da sole e quelle vuote spariscono;
  l'intestazione mostra "N di M" quando il filtro nasconde qualcosa.
- Le colonne e le azioni delle righe restano quelle di oggi, compreso l'interruttore "Nel menu" e il pulsante Dettagli.
- Sopra l'elenco resta la riga dei riepiloghi (totale, nel menu, per sorgente) già presente.
La modifica riguarda solo `static/catalog.js` e `static/style.css`: nessuna API nuova, `rel_path` e `source_name` sono già esposti.

## 17. Più risposte per la stessa ISO, con scelta al boot
Oggi una ISO ha una sola risposta collegata (`answer_id`). Serve poterne collegare più di una e scegliere al momento
dell'avvio, con una predefinita che parte da sola allo scadere del timeout.

### Dati
Nell'oggetto ISO del catalogo:
- `answers`: elenco ordinato di id di risposta collegati (lista di stringhe, massimo 8);
- `answer_id`: la predefinita, che deve stare dentro `answers` (se `answers` è vuoto vale come oggi: nessuna installazione automatica);
- `answer_manual` (bool, predefinito vero): mostra anche la voce "Installazione guidata a mano", cioè avvio senza risposta.
`PATCH /api/catalog/<slug>` accetta `answers`, `answer_id` e `answer_manual` con validazione (id esistenti, predefinita coerente).
In lettura ogni ISO espone anche `answers_info: [{id, name, kind}]` per la GUI.

### Comportamento al boot
- `GET /boot/<slug>.ipxe` (senza parametri): se la ISO ha meno di due voci fra risposte e avvio manuale, si comporta come oggi.
  Con due o più, restituisce un piccolo menu iPXE: una voce per ogni risposta (nome della risposta), più "Installazione guidata a mano"
  se `answer_manual`, con `choose --default <predefinita> --timeout <menu.answer_timeout, predefinito 10 s>`; ogni voce fa
  `chain http://<ip>/boot/<slug>.ipxe?answer=<id>` (oppure `?answer=` vuoto per l'avvio senza risposta) e torna al menu principale in caso di errore.
- `GET /boot/<slug>.ipxe?answer=<id>`: genera lo script vero usando quella risposta (o nessuna se il parametro è vuoto),
  ignorando la predefinita. `answer` non valido o non collegato alla ISO: si usa la predefinita.
- `menu.answer_timeout` (intero 0-120, predefinito 10) è una nuova impostazione del menu, con 0 = attendi la scelta.

### GUI
- Pannello dettagli della ISO: al posto del menu a tendina singolo, un elenco delle risposte collegate con il segno di spunta
  per la predefinita, pulsante per aggiungerne una (dalle risposte esistenti o generandola da un profilo, come oggi),
  pulsante per toglierla, e l'interruttore "Mostra anche l'installazione guidata a mano".
- Nella tabella, il badge mostra il numero di installazioni automatiche disponibili quando sono più di una.
- Pagina "Menu di boot": campo per `answer_timeout` con spiegazione.

## 18. Driver abbinati al modello del PC
iPXE conosce produttore e modello del PC (SMBIOS: `${manufacturer}`, `${product}`) e Pixio li riceve già come parametri
nella richiesta del menu, dove finiscono nella scheda del client (per esempio "Dell Inc. OptiPlex 7060").
Ogni cartella driver può quindi valere solo per certi modelli.
- `apply_to` guadagna il campo `models`: elenco di testi liberi (massimo 30, 60 caratteri l'uno). La corrispondenza è
  per sottostringa, senza distinzione fra maiuscole e minuscole e ignorando gli spazi doppi, sul testo
  "<produttore> <modello>" del PC che sta avviando (per esempio "optiplex 7060" corrisponde a "Dell Inc. OptiPlex 7060").
- La modalità `mode` accetta anche il valore `models`. Con `models` la cartella vale solo per i PC riconosciuti;
  le altre modalità (`all`, `groups`, `isos`) restano come sono e si combinano con `models` quando questo non è vuoto:
  se `models` è valorizzato, oltre alla condizione della modalità il PC deve corrispondere a uno dei modelli.
- `drivers.winpe_inject_files(iso=None, machine=None)` e `setup_load_folders(iso=None, machine=None)` accettano il testo
  del modello; senza, il filtro sui modelli non viene applicato (anteprime e chiamate generiche restano come prima).
- Il menu passa il modello alla voce: `chain .../boot/<slug>.ipxe?platform=...&mac=...&machine=${manufacturer:uristring}%20${product:uristring}`;
  `GET /boot/<slug>.ipxe` accetta `machine` (massimo 120 caratteri, ripulito) e lo usa per scegliere i driver.
- `GET /api/drivers` espone i modelli già visti dai client (da `clients.json`, campo `hw`) per riempire un elenco di scelta
  rapida nella GUI, in modo da non doverli scrivere a mano.
- GUI: nella riga "Si applica a" della cartella, in aggiunta alle scelte attuali, un campo "Solo su questi modelli"
  con i modelli visti selezionabili e la possibilità di aggiungerne a mano; il riassunto della scheda lo riporta
  ("solo Windows Server · solo OptiPlex 7060").
