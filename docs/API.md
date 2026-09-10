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
 detect:{files:[str], version, label}, editions ([{index,name,display_name}] dentro install.wim, sezione 19),
 editions_info:{file, updated, error}, warnings:[str], first_seen, last_seen, missing (bool: file sparito dalla sorgente)}
```
- `GET /api/catalog` → `{isos:[...ordinate per group/order/name], last_scan, scanning:bool}`
- `POST /api/catalog/scan` → `{ok, job_id}` (scansione di tutte le sorgenti, rilevamento tipo delle nuove ISO)
- `GET /api/catalog/<slug>` → ISO + `{recipe_preview:{efi:"...script ipxe...", bios:"..."}}`
- `PATCH /api/catalog/<slug> {name?, enabled?, group?, order?, type?, custom_recipe?, cache_wanted?, answers?, answer_id?, answer_manual?}` → ISO aggiornata. Abilitare = montare in loop (+ eventuale copia locale); disabilitare = smontare.
- `POST /api/catalog/<slug>/redetect` → rileva di nuovo il tipo
- `GET /api/catalog/<slug>/editions` → edizioni dentro `install.wim` (dalla cache, sezione 19); `POST` sullo stesso indirizzo le rilegge in un job
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
  (per ogni cartella anche `winpe_missing`, sezione 21, e `setup_offer` / `setup_paths` / `setup_skipped` /
  `setup_truncated`, sezione 24; per ogni file `useful`, `winpe_cand`, `excluded`, `winpe` e, sui `.inf`,
  `inf_missing` e `setup_missing`)
- `GET /api/drivers/setup-paths[?iso=<slug>]` → `{iso, paths:[{folder, rel, unc}], skipped:[{folder, dir, inf, missing, lost, reason}], dropped:[...], truncated:bool, folders:int, offered:int, max_paths:int}` — quello che il file di risposta consegna al programma di installazione per quell'immagine (sezione 24)
- `POST /api/drivers/folders {name}` → `{ok, folder}` (nome: lettere/numeri/spazi/. _ - ( ) +, max 64; 409 se esiste)
- `PATCH /api/drivers/folders/<name> {winpe_inject?, setup_load?, apply_to?, setup_offer?, note?}` → `{ok, folder}`
- `DELETE /api/drivers/folders/<name>` → `{ok}` (cancella cartella e file; conferma nella UI)
- `DELETE /api/drivers/folders/<name>/files/<path:file>` → `{ok}`
- Upload: `POST /api/upload/init {filename, size, kind:"driver", folder:"<name>"}` poi chunk/finish come per le ISO. Per `kind:"driver"` le estensioni ammesse sono: inf sys cat dll exe cab zip msi txt bin dat ini cfg xml json 7z sepolicy; il file finisce in `/srv/pixio/http/drivers/<folder>/<filename>`; se è `.zip`, `finish` lo estrae nella cartella (sotto-cartelle incluse, path traversal rifiutato, max 2 GB estratti) e cancella lo zip; risposta `{ok, extracted:int, files:[str]}`. Senza `kind` (o `kind:"iso"`) comportamento invariato (libreria ISO).
Significato dei flag (spiegazione da mostrare nella UI):
- **winpe_inject** "Carica in WinPE all'avvio": i file .inf/.sys/.cat/.dll al primo livello della cartella vengono iniettati nel WinPE via wimboot e caricati con drvload prima della rete. Serve per schede di rete o controller storage che WinPE non riconosce (max 256 MB totali).
- **setup_load** "Carica prima del setup di Windows": dopo aver mappato la share, drvload ricorsivo di tutti i .inf della cartella prima di setup.exe (richiede "Installazione Windows via rete" attiva).
- **setup_offer** "Offerta al programma di installazione" (`"auto"` predefinito | `"mai"` | `"sempre"`): se le cartelle di questa cartella driver possono finire fra i percorsi driver dell'`autounattend.xml` (sezione 24). `"mai"` la tiene fuori sempre, `"sempre"` scrive la radice senza controllare i pacchetti.

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
`disk:{mode: "auto-uefi"|"auto-bios"|"manuale", wipe (bool), efi_mb, msr_mb, recovery_mb}`
(con `mode` automatico `wipe` deve essere `true`: vedi sezione 23),
`skip_oobe` (privacy, EULA, rete, account Microsoft), `bypass_requirements` (TPM/SecureBoot/RAM/CPU per Windows 11),
`disable_defender_prompt`, `hide_files_ext`, `disable_hibernate`, `power_scheme` ("bilanciato"|"prestazioni"),
`remove_apps` (lista di pacchetti Appx da rimuovere), `run_commands` (comandi FirstLogon), `drivers_from_pixio` (bool: aggiunge il percorso della share driver in DriverPaths).
Il generatore produce un `autounattend.xml` valido con i passaggi windowsPE (locale, disco, immagine, product key), specialize (nome computer, dominio, tweak),
oobeSystem (utente locale, autologon, OOBE saltato, FirstLogonCommands). Le password vengono scritte in chiaro nel file (avvisare nella GUI).
- `GET /api/winprofiles` → `{profiles:[...], defaults:{...}, timezones:[...], languages:[...], apps:[{id,name}]}`
- `POST /api/winprofiles {name, settings}` → 201; `GET|PUT|DELETE /api/winprofiles/<id>`
- `POST /api/winprofiles/<id>/preview {settings?, iso?}` → `{xml}` (anteprima del file generato, senza salvare)
- `POST /api/winprofiles/<id>/save-answer {answer_id?, iso?}` → genera l'XML e lo salva come risposta di tipo windows (`services/answers.py`), creandola se manca; risposta `{ok, answer_id, answer_name}`
- `iso` (slug del catalogo) fa generare il file per quell'immagine: l'edizione da installare viene confrontata con quelle che contiene davvero (sezione 19)

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
- `drivers.winpe_inject_files(iso=None)`, `drivers.setup_load_folders(iso=None)` e `drivers.setup_paths(server_ip, iso=None)`
  accettano la voce di catalogo che si sta avviando (dict con `slug` e `group`) e restituiscono solo le cartelle abbinate;
  senza argomento si comportano come prima (utile per l'anteprima generica).
- **Cambio di comportamento (settembre 2026):** `apply_to` vale adesso anche per i percorsi driver scritti
  nell'`autounattend.xml` (sezione 24). Prima il file di risposta offriva al programma di installazione l'intera
  libreria, `apply_to` compreso; adesso una cartella abbinata a un solo gruppo non compare più nel file di risposta
  delle altre immagini. È la lettura corretta del campo ed è coerente con `winpe_inject_files()` e
  `setup_load_folders()`, ma chi aveva usato `apply_to` solo per il WinPE deve rivederlo.
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
La scelta fra due file con lo stesso nome non guarda più solo la cartella: fra più copie di uno stesso `.inf`
vince quella completa, e i file che l'`.inf` dichiara si prendono dalla sua cartella (sezione 21).

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

## 19. Edizione da installare scelta da un elenco, non a memoria
`settings.edition_index` di un profilo Windows finisce in `<InstallFrom>` come nome dell'immagine
(`/IMAGE/NAME`) o come indice (`/IMAGE/INDEX`) dentro `sources/install.wim`. Scritto a mano è la
causa più frequente di installazioni che si fermano a metà: una ISO Enterprise LTSC, per esempio,
non contiene nessun "Windows 11 Pro" e il programma di installazione si pianta con "impossibile
trovare l'immagine". Pixio legge quindi le edizioni davvero presenti e le propone.

### Rilevamento e cache
- `detect.wim_images(path)` legge con `wiminfo` l'elenco delle immagini: `[{index, name, display_name}]`
  nell'ordine di wiminfo, con l'indice 1-based, più la versione del formato. `wim_info()` resta
  com'era (nomi e versione) e ora si appoggia a questa.
- Il rilevamento di una ISO registra l'elenco in `detect.images`; la cache vera sta nel record del
  catalogo, campo `editions: {images, file, updated, error, size, mtime}`.
- La lettura è lenta (fino a 4 GB su una share CIFS) e non si fa mai dentro una richiesta della GUI:
  si ricalcola quando la ISO viene montata (in un thread, il mount risponde subito), quando l'ISO
  viene rilevata di nuovo, e su richiesta esplicita. `size`/`mtime` dicono se la cache vale ancora.
- Se `install.wim` non c'è si prova `install.esd` (e le stesse sotto `x64/`); se non si riesce a
  leggere niente l'elenco resta vuoto, l'errore finisce in `editions.error` e tutto continua a
  funzionare come prima, con il campo a testo libero.

### API
- Ogni ISO del catalogo espone `editions: [{index, name, display_name}]` e
  `editions_info: {file, updated, error}`.
- `GET /api/catalog/<slug>/editions` → `{slug, name, type, editions, file, updated, error}` (dalla cache).
- `POST /api/catalog/<slug>/editions` → `{ok, job_id}`: rilegge davvero il file, in un job (tipo `editions`).
  400 se la ISO non è un'installazione Windows.
- `GET /api/winprofiles` (e `GET /api/winprofiles/<id>`) aggiunge a ogni profilo
  `isos: [{slug, name, answer_id, editions}]`: le ISO su cui girerà, cioè quelle a cui è collegata la
  risposta generata dal profilo. Il legame è il campo `profile` della risposta, scritto da
  `save-answer`; per le risposte create prima vale l'identificativo o il nome uguale a quello del profilo.
- `POST /api/winprofiles/<id>/preview`, `POST /api/winprofiles/preview` e
  `POST /api/winprofiles/<id>/save-answer` accettano `iso` (slug): l'XML viene generato per
  quell'immagine.
- `targets` di `GET /api/winprofiles` porta anche `editions`: i nomi di immagine tipici di quel tipo
  di Windows, usati come suggerimento quando non c'è nessuna ISO da cui leggere quelli veri.

### Generazione dell'autounattend.xml
`render_autounattend(profile, server_ip, cfg=None, editions=None)`. Con `editions` (cioè quando si sa
per quale immagine si sta generando) il valore di `edition_index` viene confrontato con le edizioni
presenti — numero = indice, testo = nome o nome visualizzato, senza distinzione fra maiuscole e
minuscole — e se non corrisponde si ripiega su `edition_fallback(editions, target)`: la prima
edizione (indice più basso) coerente con il tipo di Windows del profilo, e se nessuna lo è la prima
dell'immagine. `InstallFrom` viene quindi generato sempre, tranne quando `edition_index` è vuoto.
In tutti i casi finisce un avviso nei log di Pixio (`pixio.winprofile`). Senza `editions` il
comportamento è quello di prima.

**Perché non si omette `InstallFrom`.** Fino al 10 settembre 2026 con più di un'edizione Pixio
ometteva il blocco, "perché una domanda in più è sempre meglio di un'installazione che si pianta".
Per il programma di installazione un `ImageInstall/OSImage` senza `InstallFrom` non significa
"scegli tu": con la chiave di prodotto vuota abbina tutte le immagini del `.wim`
(`ProductKey: Matching Install Wim: No edition provided, matching all images`), ne trova più di una
e apre la pagina **Selezione immagine** aspettando che qualcuno prema Avanti
(`SelectImageIndex: Found multiple matching images. Querying for image index`). Su un PC avviato
dalla rete davanti allo schermo non c'è nessuno: quella domanda È l'installazione che si pianta.
`WillShowUI=OnError` non evita niente, è il valore predefinito e vale solo per gli errori.
Il valore vuoto invece resta vuoto: "Chiedi durante l'installazione" è una scelta esplicita del
tecnico e la GUI avvisa che su un'immagine con più edizioni ferma l'installazione automatica.

**Controllo al salvataggio.** `winprofile.check_edition_for_isos(profile_id, settings)`, chiamata da
`update()`, rifiuta con `ValueError` (400 dall'API) un profilo la cui edizione non è in nessuna delle
ISO abbinate, elencando quelle presenti. Serve a non far scoprire l'errore davanti a un PC in
installazione. Basta che l'edizione sia in una delle ISO abbinate; alla creazione il profilo non è
ancora legato a nessuna risposta, quindi non c'è niente da confrontare e il controllo non scatta.

Il file salvato come risposta però resta buono per una ISO sola: al boot l'XML viene rigenerato per
l'immagine che sta partendo (sezione 20).

### Segnalazione all'utente
- Pagina Windows: "Edizione da installare" diventa una tendina con le edizioni dell'immagine abbinata
  (ognuna con il suo indice), più "Chiedi durante l'installazione" e "Scrivi un valore a mano…" per
  chi usa lo stesso profilo su ISO diverse. Senza ISO abbinata resta un campo libero, con i nomi
  tipici del tipo di Windows come suggerimento e la spiegazione di dove trovare quello giusto.
- Se il valore salvato non è fra le edizioni dell'immagine compare un avviso con l'elenco di quelle
  buone (e la spiegazione che il salvataggio verrà rifiutato finché non si sceglie), e l'elenco dei
  profili mostra la pillola "edizione non nell'immagine". Con "Chiedi durante l'installazione" su
  un'immagine che contiene più di un'edizione compare l'avviso che il setup si fermerà ad aspettare. Senza ISO abbinata
  vale la regola `winprofile.edition_target_warning()`, che riconosce i casi palesi (un nome non
  LTSC su un profilo LTSC, un nome di Server su un profilo client).
- Dettagli della ISO: riga "Edizioni" con nomi e indici, e negli avvisi in cima al pannello una riga
  per ogni profilo collegato che installa un'edizione che lì non c'è.

### Preset
I modelli per edizione portano un `edition_index` coerente con il proprio `target`
(`win11-ltsc` → "Windows 11 Enterprise LTSC 2024", `win10-ltsc` → "Windows 10 Enterprise LTSC 2021",
`win11-pro` → "Windows 11 Pro"). `winserver` copre più versioni di Windows Server, il cui nome
immagine cambia con l'anno: il campo è vuoto e l'edizione si sceglie dalla tendina della ISO.

## 20. Risposta Windows generata all'avvio, per la ISO che sta partendo
Le risposte Windows sono file statici (`/var/lib/pixio/answers/<id>/autounattend.xml`) generati una
volta sola dal profilo, ma la stessa risposta si collega a più ISO (sezione 17) e l'edizione scritta
dentro vale solo per una di quelle. Da quando `install.cmd` passa davvero il file al setup con
`/unattend:`, un'edizione che nell'immagine non esiste non viene più ignorata: ferma l'installazione.
Il controllo della sezione 19 serve quindi al momento dell'avvio, l'unico in cui si sa quale ISO
sta partendo: l'XML si genera lì, non una volta per tutte.

### Indirizzo
`GET /boot/answer/<slug>/<answer_id>/autounattend.xml` (pubblico come gli altri `/boot/`, nessuna
autenticazione, `Cache-Control: no-store`, `text/plain` come i file di `/answers/`). Genera l'XML
adesso, dal profilo che ha creato la risposta, con
`render_autounattend(prof, server_ip, editions=winprofile.editions_for_iso(slug))`:
- l'edizione del profilo è nell'immagine → si scrive com'è;
- immagine con una sola edizione → si installa quella;
- immagine con più edizioni → si installa la prima coerente con il `target` del profilo
  (`11-ltsc` su una ISO LTSC 2024 → indice 1, "Windows 11 Enterprise LTSC 2024"), mai un
  `InstallFrom` assente: senza, il setup si ferma sulla pagina "Selezione immagine" (sezione 19).

Ogni scarto lascia due righe nei log: `pixio.boot` dice quale profilo e quale ISO (nome del catalogo)
con l'elenco delle edizioni presenti, `pixio.winprofile` dice la decisione presa (sezione 19).
Codici: 400 slug non valido, 404 risposta inesistente, 200 negli altri casi.

### Quando la ricetta usa questo indirizzo
Decide `answers.winpe_files(answer, server_ip, slug)`, che riceve lo slug da `recipes._apply_answer`.
Con lo slug e con una risposta che nasce da un profilo ancora esistente
(`answers.from_profile()` → `winprofile.profile_for_answer()`: campo `profile` della risposta, con i
ripieghi su id e nome uguali per le risposte create prima della sezione 19) la riga della ricetta
diventa `initrd http://<ip>/boot/answer/<slug>/<id>/autounattend.xml autounattend.xml`.
Restano invece sul file statico `http://<ip>/answers/<id>/autounattend.xml`, come prima:
- le risposte scritte o caricate a mano dal tecnico, che non nascono da un profilo;
- quelle il cui profilo è stato cancellato;
- le risposte `generic` che contengono un `autounattend.xml`;
- le chiamate senza slug (`winpe_files(answer, ip)` da sola non cambia comportamento).

### Il file statico resta com'è
Non viene cancellato né riscritto: `/answers/<id>/<file>` continua a servirlo, la GUI a mostrarlo e a
lasciarlo modificare, e `save-answer` a rigenerarlo quando lo si chiede. È anche il ripiego
dell'indirizzo dinamico: se il profilo non c'è più (o la generazione fallisce) il client scarica
quel contenuto, così un PC in avvio non resta mai senza file di risposta.


## 21. Il driver iniettato deve avere accanto i file che dichiara
Motivo: due prove in macchina virtuale con `iaStorVD.inf` (Intel RST/VMD, il driver che serve a vedere il disco)
si chiudevano con "non caricato". La libreria contiene più copie dello stesso `.inf`, di versioni diverse;
Pixio ne sceglieva una sola per nome guardando solo la cartella (`_inject_rank`: prima la radice, poi le cartelle
a 64 bit). Ma un `.inf` dichiara i propri file, e wimboot appiattisce tutto in `X:\Windows\System32`: `drvload`
li cerca per nome. Se si inietta l'`.inf` di una copia e il `.sys` di un'altra — o se quel `.sys` non c'è — il
driver non si carica, e l'errore si scopre solo davanti a un PC in installazione.

Dopo la prima versione due prove ulteriori in macchina virtuale hanno dato ancora "non caricato": la copia
iniettata (Intel RST 19.5.1.1040) dichiara, oltre a `iaStorVD.sys`, anche `RstMwEventLogMsg.dll` (`%11%`) e
`RstMwService.exe` (`%13%`). La `.dll` stava nella cartella ma non veniva iniettata, perché `WINPE_EXT`
ammetteva solo `.inf .sys .cat`; l'`.exe` non c'è in nessuna copia del pacchetto. `drvload` fallisce anche
solo per un file di `CopyFiles` che non riesce a mettere a posto, quindi da qui in avanti **un `.inf`
iniettato si porta dietro tutti i file che dichiara**, qualunque sia l'estensione.

### Lettura dei .inf
Attenzione: questa sezione parla di **`drvload` nel WinPE**. Il programma di installazione ha regole diverse
(pretende il `.cat`, non pretende i file che compaiono solo come `ServiceBinary`, cerca nel sottoalbero del
pacchetto e non solo accanto all'`.inf`): quelle stanno nella sezione 24 e usano funzioni diverse
(`inf_setup_files`, `inf_setup_missing`). Le funzioni qui sotto non cambiano.

`drivers.inf_declared_files(path)` restituisce i nomi (minuscoli, senza percorso) dei file che l'`.inf` dichiara
come propri; `drivers.inf_needed_files(path)` è il sottoinsieme con estensione da WinPE (`.inf .sys .cat`),
`drivers.inf_extra_files(path)` quello con le altre estensioni (`.dll .exe .bin .dat`...) e
`drivers.inf_all_files(path)` i due elenchi in fila, nell'ordine in cui vengono iniettati.
Non è un parser INF completo, serve solo l'elenco dei nomi citati:
- sezioni `[SourceDisksFiles*]`: il nome sta nella chiave (`iaStorVD.sys = 1,,,`);
- righe `CopyFiles=`: i nomi diretti (`@RstMwService.exe`) e le sezioni di copia referenziate, dove ogni riga è
  `file-destinazione, file-sorgente, ...` (conta il file sorgente, se c'è);
- righe `ServiceBinary`, togliendo il prefisso di cartella (`%12%\iaStorAfs.sys` → `iaStorAfs.sys`).

I file INF di Windows sono UTF-16 con BOM oppure ANSI (cp1252), hanno righe di continuazione con `\` a fine riga
e commenti dopo `;` (non dentro le virgolette): tutto questo viene gestito. Un `.inf` illeggibile o più grande di
`INF_MAX_BYTES` (4 MB) non dichiara nulla, e non fa fallire l'iniezione. Il risultato è in memoria per
(percorso, mtime, dimensione), perché `list_folders()` gira a ogni aggiornamento della pagina.

### Anche i file dichiarati che non sono .inf/.sys/.cat
`WINPE_EXT` resta `.inf .sys .cat`: una `.dll` qualsiasi trovata in una cartella driver **non** viene iniettata.
Il motivo per cui erano escluse vale ancora: wimboot appiattisce tutto in `X:\Windows\System32` e un file con un
nome comune (era successo con un `generic.dll`) sovrascriverebbe un file di sistema del WinPE.
La regola nuova è diversa: **lo inietto perché quell'`.inf` lo chiede**, non perché si trova lì. Un file entra nel
WinPE solo se un `.inf` che viene iniettato lo dichiara come proprio e se sta nella sua stessa cartella; l'elenco
lo scrive il produttore del driver, quindi il rischio resta quello di prima.
- Il file dichiarato che sta accanto all'`.inf` diventa `winpe_cand` come i `.inf/.sys/.cat` e ha in GUI la
  stessa casella per escluderlo a mano. Un `.inf` escluso a mano non tira dentro niente.
- Un file dichiarato che porta il nome di un file già presente in `\Windows\System32` del WinPE **non** viene
  iniettato: lo sostituirebbe. `drivers.is_winpe_system_file(nome)` decide, e finisce fra gli avvisi
  (`winpe_shadowed`). L'elenco dei nomi è `WINPE_SYSTEM32` (scritto nel codice, i più comuni) unito a quelli
  letti da un `boot.wim` del catalogo con `wimdir <boot.wim> 2`, filtrando i file al primo livello di
  `\Windows\System32`. La lettura del `.wim` si fa **una volta sola** per processo (`winpe_system32_names()`,
  cache in memoria): `list_folders()` gira a ogni aggiornamento della pagina. Se wimlib non c'è o nessuna ISO è
  montata resta valido il solo elenco scritto nel codice.

### Scelta della copia e coerenza
- Fra più copie dello stesso `.inf` vince quella **completa**, cioè quella che ha accanto tutti i file che dichiara.
  Contano anche i file dichiarati con altra estensione, ma solo se esistono da qualche parte nella cartella: un
  file che non c'è in nessuna copia (come `RstMwService.exe`) non può far preferire una copia all'altra, e viene
  solo segnalato. Una copia completa batte una monca anche se sta in una cartella meno preferita; a parità di
  completezza vale l'ordine di prima (`_inject_rank`).
- Insieme all'`.inf` scelto vengono iniettati i file che dichiara **presi dalla sua stessa cartella** (prima i
  `.inf/.sys/.cat`, poi gli altri), anche se un file con quel nome era già stato scelto da un'altra cartella
  o da un'altra cartella driver: la coerenza fra
  `.inf` e i suoi file viene prima della preferenza di cartella. Se due `.inf` diversi chiedono lo stesso nome,
  se lo tiene il primo (scelta stabile). Il limite `MAX_INJECT_BYTES` resta valido: se il file di ricambio non ci
  sta, resta quello già scelto.
- I file esclusi a mano (sezione 15) restano fuori comunque: la scelta del tecnico vale anche qui.

### Segnalazione
Ogni file `.inf` espone:
- `inf_missing`: i file che dichiara e che non gli stanno accanto ma che un'altra copia della cartella ha
  (più i `.inf/.sys/.cat` dichiarati e mai presenti). È quello che rende la copia "monca" e decide la scelta.
- `inf_absent`: i file che dichiara e che **non esistono in nessuna copia** della cartella, di qualsiasi
  estensione (nel caso reale `RstMwService.exe`): nessuna copia può fornirli, il pacchetto è incompleto.
- `inf_shadowed`: i file che dichiara, che ci sono, ma che portano il nome di un file di sistema del WinPE e
  quindi non vengono iniettati.
- `inf_better`: percorso di un'altra copia dello stesso `.inf` che invece è un set coerente (vuoto se non c'è).

Ogni cartella espone, solo per gli `.inf` che finiscono davvero nel WinPE:
- `winpe_missing: [{inf: "<percorso relativo>", missing: ["nome", ...]}]` — `inf_missing` più `inf_absent`, senza
  doppioni: tutto quello che l'`.inf` dichiara e che nel WinPE non ci sarà.
- `winpe_shadowed: [{inf: "<percorso>", files: ["nome", ...]}]`.
- `winpe_better: [{inf: "<percorso>", copy: "<percorso dell'altra copia>", folder: "<sua cartella>"}]` — la copia
  che verrebbe iniettata non è un set coerente (le manca un file dichiarato, o ne ha uno escluso a mano o
  oscurato da un file di sistema) mentre un'altra copia dello stesso `.inf` lo è. Le esclusioni fatte a mano non
  vengono toccate: si dice soltanto quale cartella conviene preferire.

La pagina Driver mostra gli avvisi sulla scheda della cartella, nello stile degli altri
(`"iaStorVD.inf (in RAID/RAPIDSTORAGE/Drivers) dichiara un file che non c'è nella sua cartella: manca
RstMwService.exe. drvload carica il driver solo se trova accanto all'.inf tutti i file che dichiara..."`), e nel
pannello dei file le pillole `manca <nome>` e `già nel WinPE: <nome>` sulla riga dell'`.inf`.

## 22. I log del programma di installazione arrivano da soli su Pixio
Motivo: il 10 settembre 2026 un OptiPlex avviato in PXE si è fermato con "installazione non riuscita" e
l'unica traccia disponibile era una fotografia dello schermo. I log che spiegano il guasto
(`setupact.log`, `setuperr.log`) stanno dentro il WinPE del PC, che al riavvio sparisce: se non vengono
copiati prima, l'informazione è persa. Da qui in avanti è il PC stesso a depositarli su Pixio, e si leggono
dalla GUI.

### Dove finiscono
`/srv/pixio/setuplogs/<cartella>/`, esportata da Samba come share **`pxelog`** in scrittura (utente
`windows.smb_user`, la stessa password della share `pxe`). La cartella sta **fuori** da `/srv/pixio/http`:
quell'albero è servito in sola lettura da nginx e dalla share `[pxe]`, e un client in installazione non
deve poter scrivere dove gli altri client leggono. La crea l'helper (`pixio-helper apply samba` chiama
`ensure_setuplogs_dir()`) con proprietario `pixio:pixio` e permessi `0750`; la share ha
`force user = pixio`, così i file arrivano già dell'utente del servizio e la GUI può leggerli ed
eliminarli senza passare dall'helper.

Il nome della cartella lo decide il **server** quando genera `install.cmd`:
`<AAAAMMGG>-<hhmmss>-<ip del client>-<slug>` (`winpe.log_folder()`). Il WinPE non ha un orologio
attendibile e `%DATE%` cambia formato con la lingua: comporre il nome in `cmd` sarebbe fragile.
Ordinabile per nome = ordinabile per momento dell'avvio.

### Cosa deposita il PC
`install.cmd` (generato da `pixio/services/winpe.py`) chiama il sottoprogramma `:pixio_log`:
- quando `setup.exe` è terminato (`:fine_setup`), qualunque sia l'esito;
- quando la share è collegata ma manca `setup.exe` (`:senzasetup`), per avere almeno il riepilogo.

Contenuto della cartella:
- `riepilogo.txt` — righe `chiave: valore` scritte metà dal server (immagine, slug, ora dell'avvio, IP e MAC
  del client, nome del PC) e metà dal PC (`esito setup.exe` = `%ERRORLEVEL%`, se il file di risposta era
  attivo, indirizzo ottenuto);
- `rete.txt` (`ipconfig /all`) e `disco.txt` (`diskpart`: `list disk`, `list volume`);
- `autounattend.xml` e `install.cmd` **davvero usati**, copiati da `X:\Windows\System32`;
- `panther-winpe/`, `winpe-panther/`, `panther-C|D|E/`, `windows-C|D|E/` — i `*.log`, `*.xml` e `*.txt`
  di `%SYSTEMDRIVE%\$WINDOWS.~BT\Sources\Panther` e `\Windows\Panther`, sul WinPE e sui dischi.

Regole a cui lo script deve obbedire, sempre:
- **non blocca l'installazione**: la copia parte a setup finito, `net use` ha tre tentativi e basta,
  `xcopy` gira con `/c` (va avanti sui file in uso) e ogni comando ha lo sfogo su `nul`;
- **non termina mai**: ogni ramo torna al chiamante con `goto :eof` e si finisce comunque nel prompt di
  Pixio. Se lo script uscisse, Windows PE riavvierebbe il PC;
- **dice come è andata**: "fatto: i log sono su Pixio" oppure "non riesco a collegare \\\\ip\\pxelog".
Limite noto: se l'installazione riesce, `setup.exe` riavvia il PC e lo script non riprende il controllo;
si raccolgono i log dei tentativi che **finiscono male**, che sono quelli che interessano.

### Impostazioni
`windows.setup_logs_enabled` (predefinito `true`, interruttore in Impostazioni → Windows),
`windows.setup_logs_share_name` (predefinito `pxelog`) e `windows.setup_logs_keep` (predefinito 50:
le cartelle più vecchie oltre questo numero le elimina il thread di manutenzione, `setuplogs.prune()`).
La raccolta richiede `windows.smb_export_enabled`: l'helper esporta `[pxelog]` solo insieme a `[pxe]`.

### API
- `GET /api/setuplogs` → `{logs: [...], enabled, share, dir}`. Ogni voce:
  `{name, received (epoch), files, size, image, slug, client, mac, pc, started, esito, ok, errors, error_file, has_summary}`.
  `errors` sono le ultime righe con `, Error` / `, Warning` di `setuperr.log` (o di `setupact.log`): è la
  riga che si legge nell'elenco, quella che di solito contiene il codice del guasto (es. `0x80042565`).
- `GET /api/setuplogs/<cartella>[?file=<percorso relativo>]` → la stessa voce più `file_list`
  (`[{name, size, mtime, text}]`), `file`, `content` (coda del file, al massimo 256 KB, UTF-8 o UTF-16) e
  `truncated`.
- `DELETE /api/setuplogs/<cartella>` → elimina una cartella. `DELETE /api/setuplogs` → le elimina tutte
  (`{ok, deleted}`).

Tutto quello che sta lì dentro l'ha scritto un client, quindi non ci si fida di niente: il nome della
cartella deve corrispondere a `NAME_RE`, il percorso del file a `REL_RE`, entrambi vengono risolti con
`realpath` e confrontati con la radice, i collegamenti simbolici non vengono seguiti e i file si leggono
solo in coda e con un tetto.

### GUI
La pagina **Log** ha due schede: "In diretta" (i log del server, invariata) e "Installazioni", con
l'elenco di quello che i PC hanno depositato — momento, PC, immagine, esito, la riga di errore e quanti
file — il pannello laterale che apre il contenuto di ogni singolo file (con l'elenco a tendina per
passare da `setupact.log` a `autounattend.xml`) e i pulsanti per eliminare una cartella o svuotare tutto.

## 23. Partizionamento automatico e cancellazione del disco sono la stessa scelta
Motivo: guasto del 10 settembre 2026, `0x80042565`. Il profilo dell'utente aveva
`disk.mode = "auto-uefi"` con `disk.wipe = false`, e il generatore traduceva quel flag pari pari in
`<WillWipeDisk>false</WillWipeDisk>` scrivendo subito dopo quattro `CreatePartition` che ricostruiscono
il disco da zero (ripristino 750 MB, EFI 300 MB, MSR 16 MB, Windows con `Extend`). Le due cose insieme
non stanno in piedi:

- senza azzeramento il programma di installazione non crea nessuna tabella nuova, lavora su quella che
  trova (`ResolvePartitionTypeToCreate: disk 0 already has 1 allocated partitions`). Su un disco già
  usato — il caso normale di Pixio, che reinstalla PC in servizio — crea la prima partizione e poi si
  ferma sulla EFI (`CreatePartition: Disk 0 doesn't support creation of partitions of the specified
  type`, `hr = 0x80042565`), col disco già modificato;
- i `PartitionID` sono numeri di posizione sul disco ("The first partition on a disk has the value of
  1"), non l'ordine di creazione: `_partizioni_uefi` li conta da 1 e li usa in `ModifyPartition` e in
  `InstallTo`. Senza azzeramento le partizioni nuove prendono i numeri successivi a quelle esistenti e
  quei `ModifyPartition` formattano le partizioni che c'erano prima. È il caso peggiore, perché non si
  ferma: distrugge dati in silenzio, e capita proprio a chi ha tolto la spunta per proteggerli.

Regola: `wipe` ha senso solo con `mode = "manuale"`, che non genera alcun `DiskConfiguration` e lascia
le schermate del disco al setup. Con `mode` automatico il disco 0 viene sempre azzerato.

- `validate()` rifiuta `wipe=false` con `mode` automatico (`ValueError`, 400 dall'API), quindi un
  profilo così non si salva più.
- `validate(..., rifiuta_incompatibili=False)` — cioè la validazione della generazione — invece lo
  corregge a `true` e lascia un avviso in `pixio.winprofile`: i profili già salvati in
  `/var/lib/pixio/winprofiles.json` devono continuare a produrre un `autounattend.xml` che funziona,
  senza che l'utente debba riaprirli uno per uno.
- Il generatore scrive comunque `<WillWipeDisk>true</WillWipeDisk>` nelle modalità automatiche, con un
  commento XML che spiega che lo schema sotto vale solo su un disco vuoto.
- GUI (pagina Windows, riquadro Disco): con il partizionamento automatico la spunta "Cancella il disco
  0" è mostrata bloccata su sì, con la spiegazione; con quello manuale sparisce, sostituita dalla riga
  che dice che Pixio non tocca nessuna partizione. Un profilo salvato con la combinazione impossibile
  mostra un avviso nel riquadro e la pillola "disco: incoerenza corretta" nell'elenco dei profili.

### Nota: BypassNRO
Lo stesso giro ha spostato `BypassNRO` dal passaggio `windowsPE` a `specialize`. In `windowsPE` i
`RunSynchronousCommand` girano dentro Windows PE, dove `HKLM\SOFTWARE` è l'alveare del disco RAM:
sparisce al riavvio e l'OOBE del sistema installato non ha mai visto quel valore (le `LabConfig`
restano invece in `windowsPE`, perché lì le legge il programma di installazione). `specialize` gira nel
Windows appena applicato, prima dell'OOBE: è dove il modello scritto a mano di `services/answers.py` la
metteva da sempre.

## 24. Nel file di risposta solo le cartelle driver che il setup può percorrere
Motivo (guasto vero del 10 settembre 2026, log del PC in `/srv/pixio/setuplogs/`):

```
11:01:01, Info  MOUPG  Driver: Received driver inf path [\\10.10.0.254\pxe\drivers\RAID_drivers\iaStorVD.inf].
11:01:02, Error MOUPG  CDlpActionDriverInstallation::ExecuteDriverInstall(1055): Result = 0x80070002
11:01:02, Error MOUPG  CDlpActionDriverInstallation::ExecuteUnattendDriverInstall(1393): Result = 0x80070002
11:01:02, Error MOUPG  CSetupManager::ExecuteDownlevelMode(609): Result = 0x80070002
```

Il file di risposta scriveva **un solo** `DriverPaths` verso la radice della libreria
(`\\<ip>\pxe\drivers`). Il programma di installazione percorre il percorso che riceve **con tutte le sue
sottocartelle**, mette in staging ogni `.inf` che trova — anche quelli per hardware che il PC non ha — e al
primo file dichiarato che non trova si ferma con `0x80070002` (file non trovato) **abortendo l'installazione
intera** (`0xC190011F` dopo due minuti e mezzo, senza toccare il disco). `RAID_drivers/iaStorVD.inf` dichiara
`iaStorAfsService.exe`, `iaStorAfsNative.exe` e `RstMwService.exe`, che nella sua cartella non ci sono: un solo
pacchetto incompleto e **nessun PC riusciva più a installarsi**. Nella libreria dell'utente 9 `.inf` su 24 sono
in quello stato.

Da qui in avanti Pixio non offre più la libreria: elenca nell'XML soltanto le cartelle che ha verificato.

### La regola, in tre definizioni
1. **`.inf` completo** — ogni file che il pacchetto **promette di copiare** si trova nel pacchetto.
   I nomi li legge `drivers.inf_setup_files(path)`: chiavi delle sezioni `[SourceDisksFiles*]`, `CopyFiles=@nome`,
   le sezioni di copia referenziate da `CopyFiles`, più il `CatalogFile=` di `[Version]` (il setup, a differenza
   di `drvload`, pretende la firma). **Non** conta un nome che compare solo come `ServiceBinary`: di norma il file
   lo fornisce Windows — il vero `SERVER_drivers/Matrox G200eW (Nuvoton) WDDM 2.0/oem0.inf` dichiara
   `ServiceBinary = %12%\pci.sys`, e pretenderlo accanto all'`.inf` sarebbe un falso allarme (quella cartella
   resta fuori lo stesso, ma per `Matrox.WddmUninstaller.exe`, che sta in una sezione di copia).
   "Si trova nel pacchetto" vuol dire **nel sottoalbero della cartella dell'`.inf`**, non solo accanto: un `.inf`
   può dichiarare i propri file in una sottocartella (`[SourceDisksNames]` con il campo percorso) e la regola
   stretta lo boccerebbe a torto. `drivers.inf_setup_missing(path)` dà i nomi che mancano (vuoto = completo).
2. **Cartella offribile** — tutti gli `.inf` del suo **sottoalbero** sono completi e ce n'è almeno uno. Il
   sottoalbero, non la sola directory, perché il setup scende da solo: nel guasto vero il percorso scritto era la
   radice e il setup è andato a pescare `RAID_drivers\iaStorVD.inf` due livelli più sotto.
3. **Scelta delle voci** — da ogni cartella driver si scende in ampiezza: la prima directory offribile si scrive
   e lì ci si ferma (dentro ci pensa il setup), una non offribile si scavalca e si esaminano le sue figlie. Ne
   esce un'anticatena di cartelle massimali: nessun percorso contiene un `.inf` incompleto e nessuno è annidato
   in un altro (`SERVER_drivers\HP` con tre pacchetti sani è **una** voce, non tre).

Filtri applicati prima: **abbinamento all'immagine** (`apply_matches(apply_to, iso)`, sezione 15 — è un cambio
di comportamento, prima il file di risposta ignorava `apply_to`), **nome cartella valido** (`FOLDER_RE`) e il
flag di cartella **`setup_offer`** (`auto` predefinito | `mai`: non finisce mai nel file di risposta, per i
driver che servono solo al WinPE | `sempre`: scrive la radice senza controllare, scappatoia per un falso allarme
del parser, con avviso rosso in GUI).

Cosa **non** entra nella regola, di proposito:
- le esclusioni a mano (`excluded`, sezione 15) tolgono il file dall'iniezione nel WinPE ma **non** dal disco: il
  setup che percorre la cartella lo trova lo stesso. Per rendere offribile una cartella bisogna completare il
  pacchetto o cancellare l'`.inf` incompleto (`DELETE .../files/<percorso>`);
- `inf_shadowed` / `is_winpe_system_file` non contano: riguardano l'appiattimento in `X:\Windows\System32` del
  WinPE, non la copia offline che fa il setup;
- non si guarda l'architettura della sottocartella (`SKIP_DIRS` serve al WinPE): un `.inf` di architettura
  sbagliata il setup lo ignora in silenzio, mentre uno scarto sbagliato toglierebbe un driver che serve;
- non si guarda se il driver serve all'hardware del PC: con `PnpCustomizationsWinPE` il setup mette in staging
  **tutti** gli `.inf` che trova, quindi un pacchetto rotto rompe l'installazione anche se è per hardware assente.

Limiti di scansione (la generazione gira a ogni avvio di un PC): profondità 8 livelli, 500 directory e 5000 file
per cartella driver; oltre il limite la cartella è trattata come non offribile e la GUI lo dice. La lettura degli
`.inf` è coperta dalla memoria `_INF_CACHE` (percorso + mtime + dimensione) già usata da `list_folders()`.

### L'XML generato
Cambia solo il blocco dentro `Microsoft-Windows-PnpCustomizationsWinPE` nel passaggio `windowsPE`.
`_driver_path(server_ip, cfg)` è diventata `_driver_paths(server_ip, cfg, iso)` e si scrive una
`PathAndCredentials` per voce, con `wcm:keyValue` **progressivo** (con più voci un valore fisso `"1"` sarebbe un
XML rifiutato) e le stesse `Credentials` ripetute in ogni voce, come vuole lo schema. Un commento XML dice
quante cartelle sono state scelte e quali sono rimaste fuori, con i file che mancano.

Ordine deterministico: cartella driver in ordine alfabetico (senza distinzione di maiuscole, come
`list_folders()`), poi percorso relativo in ordine alfabetico — due generazioni con la stessa libreria danno
byte identici. Tetto `MAX_SETUP_PATHS = 64`: ogni voce è una connessione SMB autenticata più una scansione
ricorsiva, in serie, prima che il disco venga toccato. Oltre il tetto entrano per prime le cartelle che il
tecnico ha abbinato apposta a questa immagine (`isos`, poi `groups`, poi `all`) e le altre finiscono in un
commento XML e in un avviso nei log: niente troncamenti silenziosi. Un percorso UNC più lungo di
`MAX_SETUP_UNC = 255` caratteri viene scartato con avviso, non scritto e basta.

### Nessuna cartella utilizzabile
Se l'elenco è vuoto **non si scrive il componente** `Microsoft-Windows-PnpCustomizationsWinPE`, e al suo posto
va un commento XML che spiega perché, con i pacchetti scartati e i file che mancano. Un `<DriverPaths/>` vuoto
è una sezione che alcune versioni del setup segnalano come errore di schema, e il ripiego "allora rimetto la
radice" è esattamente il guasto: mai, per nessun motivo. Senza il componente l'installazione prosegue con i
driver che Windows ha dentro — un'installazione senza driver aggiunti è un problema piccolo e visibile,
un'installazione che si ferma a `0xC190011F` dopo due minuti e mezzo è un problema grosso e opaco.
Il caso viene **gridato, non subito**: `log.warning` in `pixio.winprofile` alla generazione e in `pixio.boot`
quando il file viene servito a un PC (con id della risposta, immagine, cartelle scartate e file mancanti),
riquadro rosso nella pagina Driver e riga rossa nel Personalizzatore Windows.

Attenzione al **caso a metà**, che è il più insidioso: elenco non vuoto ma senza la cartella RAID/VMD.
L'installazione parte e magari riesce, ma il disco NVMe dietro un controller Intel VMD può non comparire.
L'avviso ha lo stesso peso visivo del caso vuoto, e ricorda che il flag `setup_load` (drvload nel WinPE, meno
esigente) può far vedere il disco anche quando la cartella non è offerta al file di risposta.

Restano invariati: il commento sulla share SMB non attiva (`smb_export_enabled` spento), il comportamento con
`drivers_from_pixio` spento (nessun blocco) e le **risposte statiche** caricate a mano, che Pixio non riscrive —
se contengono un `DriverPaths` verso la radice della libreria il guasto resta, e il percorso
`\\<ip>\pxe\drivers` continua a esistere.

### API e GUI
- `GET /api/drivers` espone per ogni cartella `setup_offer`, `setup_paths` (percorsi relativi, `""` = radice
  della cartella), `setup_skipped` (`[{dir, inf, missing, lost, reason}]`, dove `lost` sono gli `.inf` sani che
  si perdono insieme a quello incompleto perché stanno nella stessa directory) e `setup_truncated`; ogni `.inf`
  espone `setup_missing`.
- `GET /api/drivers/setup-paths[?iso=<slug>]` dà l'elenco già calcolato per un'immagine, con gli UNC veri.
  `PATCH /api/drivers/folders[/<name>]` accetta `setup_offer`.
- `render_autounattend(profile, server_ip, cfg, editions, iso)` e `save_as_answer(..., iso_slug)` ricevono la
  voce di catalogo da `winprofile.iso_entry(slug)`; l'anteprima del profilo passa la stessa ISO, altrimenti
  mostrerebbe un file diverso da quello che finisce sul PC.
- Pagina Driver: riquadro **"Cosa riceve il programma di installazione"** in cima (si sceglie l'immagine e si
  vedono i percorsi veri e i pacchetti scartati), pillola "al setup: N percorsi" su ogni scheda, elenco dei
  percorsi in chiaro, un avviso per ogni pacchetto scartato con i file che mancano e i due rimedi ("Elimina
  l'.inf incompleto", "Carica i file mancanti" puntato su quella sottocartella), selettore `auto` / `mai` /
  `sempre` e, nel pannello dei file, la pillola rossa "non offerto al setup: manca <nome>" sulla riga dell'`.inf`
  (cosa diversa da "manca <nome>", che parla del WinPE).
- Pagina Personalizzatore Windows: la casella "Usa i driver caricati in Pixio" non promette più di aggiungere
  `\\<ip>\pxe\drivers`, ma elenca le cartelle percorribili, con il dettaglio richiudibile dei percorsi.
