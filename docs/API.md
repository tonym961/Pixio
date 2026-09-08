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
- `static/index.html`, `static/app.js`, `static/style.css` (+ eventuali moduli). Nessuna CDN. Router ad hash: `#/dashboard`, `#/iso`, `#/menu`, `#/impostazioni`, `#/client`, `#/log`.
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
