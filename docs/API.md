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
 detect:{files:[str], version, label}, warnings:[str], first_seen, last_seen, missing (bool: file sparito dalla sorgente)}
```
- `GET /api/catalog` → `{isos:[...ordinate per group/order/name], last_scan, scanning:bool}`
- `POST /api/catalog/scan` → `{ok, job_id}` (scansione di tutte le sorgenti, rilevamento tipo delle nuove ISO)
- `GET /api/catalog/<slug>` → ISO + `{recipe_preview:{efi:"...script ipxe...", bios:"..."}}`
- `PATCH /api/catalog/<slug> {name?, enabled?, group?, order?, type?, custom_recipe?, cache_wanted?}` → ISO aggiornata. Abilitare = montare in loop (+ eventuale copia locale); disabilitare = smontare.
- `POST /api/catalog/<slug>/redetect` → rileva di nuovo il tipo
- `POST /api/catalog/reorder {order:[slug,...]}`
- `DELETE /api/catalog/<slug>` → SOLO per sorgente "local": elimina il file dalla libreria. Per sorgenti remote: 400.

## Menu di boot
- `GET /api/menu` → `{settings:{title, timeout, default, show_local, show_shell, show_reboot, show_memtest, groups:[str]}, entries:[{slug,name,group,order,enabled,platforms,type_name}], preview:{efi:"#!ipxe ...", bios:"#!ipxe ..."}}`
- `PUT /api/menu {settings}` → `{ok}`
- `GET /boot.ipxe` (pubblico) → script iPXE del menu, generato per il client (`${platform}` e `${mac}` valutati lato client via variabili iPXE: il server genera un unico script con `iseq ${platform} efi` dove serve). Query opzionali per l'anteprima: `?platform=efi|pcbios`.
- `GET /boot/<slug>.ipxe` (pubblico) → script della singola voce (kernel/initrd/boot). Il menu fa `chain http://<ip>/boot/<slug>.ipxe`. `?mac=` opzionale per il log client.

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
- Upload: `POST /api/upload/init {filename, size, kind:"driver", folder:"<name>"}` poi chunk/finish come per le ISO. Per `kind:"driver"` le estensioni ammesse sono: inf sys cat dll exe cab zip msi txt bin dat ini cfg xml json 7z; il file finisce in `/srv/pixio/http/drivers/<folder>/<filename>`; se è `.zip`, `finish` lo estrae nella cartella (sotto-cartelle incluse, path traversal rifiutato, max 2 GB estratti) e cancella lo zip; risposta `{ok, extracted:int, files:[str]}`. Senza `kind` (o `kind:"iso"`) comportamento invariato (libreria ISO).
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

## 3. Risposte automatiche (installazioni non presidiate)
Servizio `pixio/services/answers.py`, file in `/var/lib/pixio/answers/<id>/<nome file>`, metadati in `/var/lib/pixio/answers.json`.
Oggetto risposta: `{id, name, kind, files:[{name,size,mtime}], main_file, note, created, used_by:[slug]}`.
`kind`: `windows` (autounattend.xml) | `debian` (preseed.cfg) | `ubuntu` (user-data + meta-data, cloud-init) | `redhat` (kickstart .ks) | `generic`.
- `GET /api/answers` → `{answers:[...], kinds:[{id,name,hint,main_file}]}`
- `POST /api/answers {name, kind, note?, content?}` → crea (201). Con `content` scrive subito il file principale del tipo.
- `GET /api/answers/<id>` → risposta + `content` del file principale (max 512 KB) + `url` pubblico
- `PUT /api/answers/<id> {name?, note?, content?, filename?}` → aggiorna metadati e/o contenuto di un file
- `DELETE /api/answers/<id>`; `DELETE /api/answers/<id>/files/<name>`
- Upload file aggiuntivi: `POST /api/upload/init {filename, size, kind:"answer", folder:"<id risposta>"}` poi chunk/finish (estensioni: xml cfg ks yaml yml txt cmd bat ps1 reg sh conf seed json ini).
- Associazione: `PATCH /api/catalog/<slug> {answer_id: "<id>"|null}` (campo `answer_id` nell'oggetto ISO, `answer_name` in lettura).
- I file sono serviti ai client senza autenticazione su `http://<ip>/answers/<id>/<nome file>` (blueprint pubblico, solo lettura, nomi validati).
Funzioni Python richieste da `answers.py` (usate dal codice di boot):
`list_answers()`, `get(id)`, `create(data)`, `update(id, data)`, `delete(id)`, `folder_path(id)`, `public_url(server_ip, id, filename)`,
`get_for_slug(slug)` (risposta associata a una ISO o None), `kernel_args(answer, iso_type, server_ip)` (stringa da aggiungere alla cmdline:
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
