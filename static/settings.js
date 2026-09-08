/* Pixio – pagina Impostazioni: sorgenti (share remote), libreria locale, copia locale automatica,
   rete e boot, Windows, accesso HTTPS, backup/ripristino, aggiornamento, sistema.
   Espone anche P.sourceFormHtml / P.readSourceForm, riusati dal wizard di primo avvio. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  const S = {
    settings: null, sources: null, root: null,
    cache: null, cacheErr: '',      // GET /api/cache  {settings, stats, plan}
    cert: null, certErr: '',        // GET /api/system/cert
    upd: null, updErr: '',          // GET /api/update/check
  };
  const SMB_VERS = [['', 'Automatica'], ['3.1.1', 'SMB 3.1.1'], ['3.0', 'SMB 3.0'], ['2.1', 'SMB 2.1'], ['2.0', 'SMB 2.0'], ['1.0', 'SMB 1.0 (NT1, sconsigliato)']];
  const CACHE_DEFAULTS = { auto: false, min_size_gb: 2, only_enabled: true, keep_free_gb: 20 };
  const MAX_BACKUP = 64 * 1024 * 1024;      // stesso limite del server (services/backup.py)

  // ---------------------------------------------------------------- form share (condiviso con il wizard)
  P.sourceFormHtml = function (src) {
    src = src || {};
    return `
      <div class="row2">
        <div class="field"><label for="sf-name">Nome</label><input id="sf-name" value="${esc(src.name || '')}" placeholder="es. File server" maxlength="40"></div>
        <div class="field"><label for="sf-vers">Versione SMB</label><select id="sf-vers">${SMB_VERS.map(([v, l]) => `<option value="${v}" ${(src.vers || '') === v ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
      </div>
      <div class="field"><label for="sf-unc">Percorso UNC</label><input id="sf-unc" class="mono" value="${esc(src.unc || '')}" placeholder="\\\\SRV-FILE\\ISO"><div class="hint">Anche una sottocartella: <span class="mono">\\\\SRV-FILE\\Software\\ISO</span>. Senza utente l'accesso è guest.</div></div>
      <div class="row2">
        <div class="field"><label for="sf-domain">Dominio / workgroup</label><input id="sf-domain" value="${esc(src.domain || '')}" placeholder="facoltativo"></div>
        <div class="field"><label for="sf-user">Utente</label><input id="sf-user" value="${esc(src.username || '')}" placeholder="vuoto = guest" autocomplete="off"></div>
      </div>
      <div class="field"><label for="sf-pass">Password</label><input id="sf-pass" type="password" autocomplete="new-password" placeholder="${src.id ? 'lascia vuoto per non cambiarla (obbligatoria se modifichi server/utente)' : ''}"><div class="hint">Salvata sul server in un file leggibile solo da root.</div></div>`;
  };
  P.readSourceForm = function (root) {
    const v = (id) => ($('#' + id, root) || { value: '' }).value;
    const name = v('sf-name').trim(); const unc = v('sf-unc').trim();
    if (!name) throw new Error('Indica un nome per la share');
    if (!unc || !/^(\\\\|\/\/)[^\\\/]+[\\\/][^\\\/]+/.test(unc)) throw new Error('Percorso UNC non valido: atteso \\\\server\\condivisione[\\cartella]');
    const out = { name, unc, domain: v('sf-domain').trim(), username: v('sf-user').trim(), vers: v('sf-vers') };
    const pw = v('sf-pass'); if (pw) out.password = pw;
    return out;
  };

  // ---------------------------------------------------------------- dati
  async function load() {
    if (!S.root) return;
    try {
      const [settings, sources] = await Promise.all([P.get('/api/settings'), P.get('/api/sources').catch((e) => { if (e.status === 401) throw e; return []; })]);
      S.settings = settings || {}; S.sources = Array.isArray(sources) ? sources : [];
      await loadExtras();
      render();
    } catch (e) {
      if (e.status === 401) return;
      $('#set-body', S.root).innerHTML = `<div class="empty"><h3>Impostazioni non disponibili</h3><p>${esc(e.message)}</p></div>`;
    }
  }
  async function reloadSources() {
    try { S.sources = await P.get('/api/sources'); renderSources(); P.refreshStatus().catch(() => {}); } catch (e) { P.fail(e); }
  }

  // Stato di copia locale, certificato e aggiornamento: se una di queste API manca la pagina
  // resta usabile e la card mostra il motivo.
  async function loadExtras() {
    const grab = async (path, key) => {
      try { S[key] = await P.get(path); S[key + 'Err'] = ''; }
      catch (e) {
        if (e.status === 401) throw e;
        S[key] = null;
        S[key + 'Err'] = e.status === 404 ? 'funzione non disponibile su questa versione del server' : e.message;
      }
    };
    await Promise.all([grab('/api/cache', 'cache'), grab('/api/system/cert', 'cert'), grab('/api/update/check', 'upd')]);
  }

  async function refreshCache() {
    if (!S.root) return;
    try { S.cache = await P.get('/api/cache'); S.cacheErr = ''; } catch (e) { if (e.status === 401) return; S.cache = null; S.cacheErr = e.message; }
    renderCacheStats();
  }

  // ---------------------------------------------------------------- rendering
  function toggleRow(title, desc, field, on, disabled) {
    return `<div class="toggle-row"><div><div class="tt">${title}</div><div class="td">${desc}</div></div>${P.switchHtml(!!on, `data-field="${field}" aria-label="${esc(title.replace(/<[^>]+>/g, ''))}" ${disabled ? 'disabled' : ''}`)}</div>`;
  }

  // ---------------------------------------------------------------- copia locale automatica (cache)
  function cacheSettings() {
    const c = (S.cache && S.cache.settings) || S.settings.cache || {};
    const out = {};
    Object.keys(CACHE_DEFAULTS).forEach((k) => { out[k] = c[k] != null ? c[k] : CACHE_DEFAULTS[k]; });
    return out;
  }

  function cacheCardHtml() {
    const c = cacheSettings();
    return `
      <div class="card" id="card-cache"><h3>Copia locale automatica</h3>
        <div class="eyebrow">ISO copiate dalle share sul disco di Pixio</div>
        <p class="hint">Una ISO copiata in locale si avvia più in fretta e continua a funzionare anche se il file server è spento o irraggiungibile. Le copie le esegue un job in background, visibile nel Log.</p>
        ${toggleRow('Copia automatica', 'Dopo ogni scansione Pixio copia in locale le ISO che rispettano i criteri qui sotto ed elimina le copie che non servono più.', 'cache.auto', c.auto === true)}
        <div class="row2" style="margin-top:8px">
          <div class="field"><label for="s-cache-min">Copia solo sopra (GB)</label><input id="s-cache-min" type="number" min="0.1" max="100" step="0.1" value="${esc(c.min_size_gb)}"><div class="hint">Le ISO più piccole restano sulla share.</div></div>
          <div class="field"><label for="s-cache-free">Spazio da lasciare libero (GB)</label><input id="s-cache-free" type="number" min="1" max="500" step="1" value="${esc(c.keep_free_gb)}"><div class="hint">Sotto questa soglia elimina le copie usate meno di recente.</div></div>
        </div>
        ${toggleRow('Solo le ISO nel menu', 'Disattivandolo copia anche le ISO non ancora abilitate nel menu di boot.', 'cache.only_enabled', c.only_enabled !== false)}
        <div id="cache-stats"></div>
        <div class="actions" style="margin-top:10px"><button class="btn" type="button" data-act="cache-run">Applica adesso</button><button class="btn danger" type="button" data-act="cache-clear">Libera tutto</button></div>
      </div>`;
  }

  function renderCacheStats() {
    const box = S.root && $('#cache-stats', S.root); if (!box) return;
    if (!S.cache) {
      box.innerHTML = `<div class="alert warn">Stato della copia locale non disponibile: ${esc(S.cacheErr || 'nessuna risposta dal server')}</div>`;
      return;
    }
    const st = S.cache.stats || {}; const plan = S.cache.plan || {};
    box.innerHTML = `
      <div class="ministats">
        <div class="ministat"><div class="k">Copie locali</div><div class="v">${st.cached != null ? esc(st.cached) : '—'}</div></div>
        <div class="ministat"><div class="k">Spazio occupato</div><div class="v">${esc(P.fmtBytes(st.cached_bytes))}</div></div>
        <div class="ministat"><div class="k">Spazio libero</div><div class="v">${esc(P.fmtBytes(st.free_bytes))}</div></div>
        <div class="ministat"><div class="k">Candidate</div><div class="v">${st.candidates != null ? esc(st.candidates) : '—'}</div></div>
      </div>
      ${plan.reason ? `<div class="hint">${esc(plan.reason)}</div>` : ''}`;
  }

  // ---------------------------------------------------------------- backup e ripristino
  function backupCardHtml() {
    return `
      <div class="card" id="card-backup"><h3>Backup e ripristino</h3>
        <div class="eyebrow">Configurazione, catalogo, client, driver e risposte</div>
        <p class="hint">L'archivio <span class="mono">.tar.gz</span> contiene le impostazioni, il catalogo delle ISO, i client PXE, i flag delle cartelle driver e le risposte automatiche. Non contiene le ISO, la password di amministratore né le credenziali delle share.</p>
        <div class="toggle-row"><div><div class="tt">Scarica una copia</div><div class="td">Da conservare fuori da questo server: basta per rimettere in piedi Pixio su una macchina nuova.</div></div><button class="btn small" type="button" data-act="backup-download">Scarica backup</button></div>
        <div class="toggle-row"><div><div class="tt">Ripristina da un archivio</div><div class="td">Sostituisce la configurazione attuale con quella del backup. Serve una conferma esplicita.</div></div><button class="btn small" type="button" data-act="backup-restore">Carica backup…</button></div>
        <input id="bk-file" type="file" accept=".gz,.tgz,application/gzip" hidden>
        <div id="bk-result"></div>
      </div>`;
  }

  // ---------------------------------------------------------------- aggiornamento
  function updateCardHtml() {
    return `
      <div class="card" id="card-update"><h3>Aggiornamento</h3>
        <div class="eyebrow">Software Pixio (git + install.sh)</div>
        <div id="upd-info"></div>
        <p class="hint" id="upd-why"></p>
        <div class="actions" style="margin-top:10px"><button class="btn" type="button" data-act="update-check">Controlla di nuovo</button><button class="btn primary" type="button" id="upd-apply" data-act="update-apply">Aggiorna adesso</button></div>
      </div>`;
  }

  function renderUpdate() {
    const box = S.root && $('#upd-info', S.root); if (!box) return;
    const u = S.upd || {}; const ver = (P.state.status || {}).version;
    let badge; const notes = [];
    if (!S.upd) { badge = P.pill('stato sconosciuto', 'warn'); notes.push('Controllo non riuscito: ' + (S.updErr || 'nessuna risposta dal server') + '.'); }
    else if (u.error) { badge = P.pill('non disponibile', 'warn'); notes.push(u.error.charAt(0).toUpperCase() + u.error.slice(1) + '.'); }
    else if (u.behind > 0) { badge = P.pill(u.behind === 1 ? '1 aggiornamento' : u.behind + ' aggiornamenti', 'acc'); }
    else { badge = P.pill('aggiornato', 'ok'); notes.push('Nessun aggiornamento disponibile.'); }
    if (u.dirty) notes.push("Ci sono modifiche locali in /opt/pixio: l'aggiornamento automatico è bloccato finché non vengono annullate.");
    box.innerHTML = `<dl class="kv" style="margin:10px 0 2px">
      <dt>Versione</dt><dd>${ver ? 'v' + esc(ver) : '—'} ${badge}</dd>
      <dt>Revisione</dt><dd><span class="mono">${esc(u.current || '—')}</span>${u.branch ? ' · ramo ' + esc(u.branch) : ''}</dd>
      ${u.remote ? `<dt>Sul server git</dt><dd><span class="mono">${esc(u.remote)}</span></dd>` : ''}
      ${u.checked ? `<dt>Controllato</dt><dd>${esc(P.fmtDate(u.checked))}</dd>` : ''}
    </dl>`;
    const can = !!u.can_update;
    if (can) { notes.length = 0; notes.push("Pixio scarica il codice, rilancia install.sh e riavvia il servizio: l'interfaccia resta irraggiungibile per circa un minuto."); }
    else if (!notes.length) notes.push("Aggiornamento automatico non disponibile: il pulsante resta disattivato.");
    const why = $('#upd-why', S.root); if (why) why.textContent = notes.join(' ');
    const btn = $('#upd-apply', S.root); if (btn) btn.disabled = !can;
  }

  // ---------------------------------------------------------------- accesso HTTPS
  function httpsCardHtml() {
    const web = S.settings.web || {}; const cert = S.cert || {};
    // il certificato riporta lo stato effettivo di web.https_enabled / web.redirect_http
    const on = cert.enabled != null ? !!cert.enabled : web.https_enabled === true;
    const redirect = cert.redirect_http != null ? !!cert.redirect_http : web.redirect_http !== false;
    return `
      <div class="card" id="card-https"><h3>Accesso HTTPS</h3>
        <div class="eyebrow">Solo per questa interfaccia di amministrazione</div>
        ${toggleRow('HTTPS sulla porta 443', 'Il certificato è autofirmato: il browser mostrerà un avviso di sicurezza da accettare una volta (non è un errore di configurazione).', 'web.https_enabled', on)}
        <div id="web-redirect-row" ${on ? '' : 'hidden'}>${toggleRow('Reindirizza HTTP a HTTPS', 'La porta 80 rimanda a HTTPS per la sola interfaccia web.', 'web.redirect_http', redirect)}</div>
        <div class="alert">I client PXE continuano a usare HTTP: <span class="mono">/boot.ipxe</span>, <span class="mono">/boot/</span>, <span class="mono">/pxe/</span> e <span class="mono">/answers/</span> restano in chiaro anche con il reindirizzamento attivo, perché iPXE e WinPE non parlano HTTPS.</div>
        <div id="cert-info"></div>
        <div class="actions" style="margin-top:10px"><button class="btn" type="button" data-act="cert-regen">Rigenera certificato</button></div>
      </div>`;
  }

  function renderCert() {
    const box = S.root && $('#cert-info', S.root); if (!box) return;
    const c = S.cert;
    if (!c) { box.innerHTML = `<div class="alert warn">Stato del certificato non disponibile: ${esc(S.certErr || 'nessuna risposta dal server')}</div>`; return; }
    if (c.error) { box.innerHTML = `<div class="alert warn">${esc(c.error)}</div>`; return; }
    if (!c.exists) { box.innerHTML = '<div class="alert warn">Nessun certificato presente: viene creato quando attivi HTTPS, oppure subito con "Rigenera certificato".</div>'; return; }
    const days = c.days_left;
    box.innerHTML = `<dl class="kv" style="margin:10px 0 2px">
      <dt>Soggetto</dt><dd><span class="mono">${esc(c.subject || '—')}</span></dd>
      <dt>Scadenza</dt><dd>${esc(P.fmtDate(c.not_after))}${days != null ? ' · ' + esc(days) + ' giorni' : ''} ${days != null && days < 30 ? P.pill('in scadenza', 'warn') : ''}</dd>
      <dt>Impronta</dt><dd><span class="mono fp">${esc(c.fingerprint || '—')}</span></dd>
      ${c.path ? `<dt>File</dt><dd><span class="mono">${esc(c.path)}</span></dd>` : ''}
    </dl>`;
  }

  function render() {
    const s = S.settings; const net = s.network || {}; const lib = s.library || {}; const win = s.windows || {}; const scan = s.scan || {};
    const st = P.state.status || {};
    const ifaces = (s.interfaces || []).slice();
    if (net.interface && !ifaces.some((i) => i.name === net.interface)) ifaces.push({ name: net.interface, ip: net.server_ip });
    const sambaPath = (st.library && st.library.samba_path) || ('\\\\' + (net.server_ip || 'pixio') + '\\' + (lib.samba_share_name || 'iso'));
    const full = net.dhcp_mode === 'full';

    $('#set-body', S.root).innerHTML = `
      <div id="set-result"></div>
      <div class="settings">
        <div class="card" id="card-sources"><h3>Sorgenti ISO</h3>
          <div class="eyebrow">Share Windows remote (sola lettura)</div>
          <div id="src-list"></div>
          <div class="actions" style="margin-top:10px"><button class="btn" type="button" data-src="add">+ Aggiungi share</button></div>
        </div>

        <div class="card"><h3>Libreria locale</h3>
          <div class="eyebrow">ISO caricate su Pixio</div>
          ${toggleRow(`Share Samba in scrittura <span class="mono">${esc(sambaPath)}</span>`, 'Per copiare ISO da Windows con Esplora file. Utente <span class="mono">pixio</span>, password impostata qui sotto.', 'lib.samba_share_enabled', lib.samba_share_enabled !== false)}
          <div class="row2" style="margin-top:8px">
            <div class="field"><label for="s-share-name">Nome della share</label><input id="s-share-name" value="${esc(lib.samba_share_name || 'iso')}" maxlength="32" pattern="[A-Za-z0-9._-]+"></div>
            <div class="field"><span class="field-label">Password Samba (utente pixio)</span><div class="actions"><button class="btn" type="button" data-act="samba-password">${lib.samba_password_set ? 'Cambia password' : 'Imposta password'}</button>${lib.samba_password_set ? P.pill('impostata', 'ok') : P.pill('non impostata', 'warn')}</div></div>
          </div>
          ${toggleRow('Upload dalla web UI', 'Trascina la ISO nella pagina Catalogo. Riprende da dove si era interrotto.', 'lib.web_upload_enabled', lib.web_upload_enabled !== false)}
          ${toggleRow('Scansione automatica', 'Cerca nuove ISO in tutte le sorgenti a intervalli regolari.', 'scan.auto', scan.auto !== false)}
          <div class="field" style="margin-top:8px"><label for="s-scan-int">Intervallo di scansione (minuti)</label><input id="s-scan-int" type="number" min="1" max="1440" value="${esc(scan.interval_min || 10)}" style="max-width:160px"></div>
        </div>

        ${cacheCardHtml()}

        <div class="card"><h3>Rete e boot</h3>
          <div class="row2">
            <div class="field"><label for="s-iface">Interfaccia</label><select id="s-iface">${ifaces.map((i) => `<option value="${esc(i.name)}" ${i.name === net.interface ? 'selected' : ''}>${esc(i.name)}${i.ip ? ' · ' + esc(i.ip) : ''}</option>`).join('')}</select></div>
            <div class="field"><label for="s-ip">IP del server</label><input id="s-ip" class="mono" value="${esc(net.server_ip || '')}" placeholder="10.10.0.254"><div class="hint">Indirizzo che i client usano per TFTP e HTTP.</div></div>
          </div>
          <div class="field"><label for="s-dhcp">Modalità DHCP</label><select id="s-dhcp"><option value="proxy" ${!full ? 'selected' : ''}>Proxy (consigliata: il DHCP esistente resta com'è)</option><option value="full" ${full ? 'selected' : ''}>Completa (Pixio assegna anche gli IP)</option></select></div>
          <div id="dhcp-full" ${full ? '' : 'hidden'}>
            <div class="alert bad"><b>Attiva solo se in rete NON c'è già un server DHCP.</b> Due server DHCP nella stessa rete causano conflitti di indirizzi e PC senza rete.</div>
            <div class="row2">
              <div class="field"><label for="s-r1">Inizio range</label><input id="s-r1" class="mono" value="${esc(net.dhcp_range_start || '')}" placeholder="10.10.0.100"></div>
              <div class="field"><label for="s-r2">Fine range</label><input id="s-r2" class="mono" value="${esc(net.dhcp_range_end || '')}" placeholder="10.10.0.200"></div>
            </div>
            <div class="row2">
              <div class="field"><label for="s-mask">Netmask</label><input id="s-mask" class="mono" value="${esc(net.dhcp_netmask || '')}" placeholder="255.255.255.0"></div>
              <div class="field"><label for="s-router">Gateway (router)</label><input id="s-router" class="mono" value="${esc(net.dhcp_router || '')}" placeholder="10.10.0.1"></div>
            </div>
            <div class="row2">
              <div class="field"><label for="s-dns">DNS</label><input id="s-dns" class="mono" value="${esc(net.dhcp_dns || '')}" placeholder="10.10.0.1"></div>
              <div class="field"><label for="s-lease">Durata lease</label><input id="s-lease" class="mono" value="${esc(net.dhcp_lease || '12h')}" placeholder="12h"><div class="hint">es. 30m, 12h, 1d</div></div>
            </div>
          </div>
          <div class="hint" style="margin-top:4px">Stato iPXE: ${st.ipxe ? (st.ipxe.building ? 'compilazione in corso' : (st.ipxe.built ? 'compilato' + (st.ipxe.built_at ? ' il ' + esc(P.fmtDate(st.ipxe.built_at)) : '') : '<span class="bad-text">non compilato</span>')) : 'sconosciuto'}.</div>
        </div>

        <div class="card"><h3>Windows</h3>
          ${toggleRow('Installazione Windows via rete (share SMB <span class="mono">pxe</span>)', 'Il setup di Windows (WinPE) deve leggere <span class="mono">install.wim</span> da un percorso SMB: con questa opzione Pixio ri-esporta in sola lettura le ISO Windows montate (utente Samba dedicato <span class="mono">pxe</span>, password generata automaticamente). Disattivato: WinPE parte ma il setup non trova i file.', 'win.smb_export_enabled', win.smb_export_enabled === true)}
        </div>

        ${httpsCardHtml()}

        ${backupCardHtml()}

        ${updateCardHtml()}

        <div class="card"><h3>Sistema</h3>
          <div class="toggle-row" style="border-top:0"><div><div class="tt">iPXE</div><div class="td">Ricompila i binari di boot (undionly.kpxe, ipxe.efi) con l'IP del server incorporato.</div></div><button class="btn small" type="button" data-act="rebuild">Ricompila iPXE</button></div>
          <div class="toggle-row"><div><div class="tt">Servizi</div><div class="td">Riavvia dnsmasq, nginx e Samba.</div></div><button class="btn small" type="button" data-act="restart-services">Riavvia servizi</button></div>
          <div class="toggle-row"><div><div class="tt">Server</div><div class="td">Riavvia o spegni la macchina Pixio.</div></div><span class="actions"><button class="btn small" type="button" data-act="reboot">Riavvia</button><button class="btn small danger" type="button" data-act="poweroff">Spegni</button></span></div>
          <div class="toggle-row"><div><div class="tt">Password amministratore</div><div class="td">Accesso a questa interfaccia.</div></div><button class="btn small" type="button" data-act="password">Cambia</button></div>
          <div class="toggle-row"><div><div class="tt">Sessione</div><div class="td">Esci dall'interfaccia di amministrazione.</div></div><button class="btn small" type="button" data-act="logout">Esci</button></div>
        </div>
      </div>`;
    renderSources();
    renderCacheStats();
    renderCert();
    renderUpdate();
    $('#s-dhcp', S.root).addEventListener('change', (e) => { $('#dhcp-full', S.root).hidden = e.target.value !== 'full'; });
  }

  function renderSources() {
    const box = $('#src-list', S.root); if (!box) return;
    if (!S.sources.length) { box.innerHTML = '<p class="hint" style="padding:6px 0">Nessuna share configurata. Aggiungine una per leggere le ISO da un file server Windows/Samba.</p>'; return; }
    box.innerHTML = S.sources.map((s) => `
      <div class="src-item" data-id="${esc(s.id)}">
        <div>
          <div class="tt">${esc(s.name || s.id)} ${s.mounted ? P.pill('Montata', 'ok') : (s.error ? P.pill('Errore', 'bad') : P.pill('Non montata', 'neutral'))}</div>
          <div class="td mono">${esc(s.unc)}</div>
          <div class="td">${s.iso_count != null ? s.iso_count + ' ISO' : ''}${s.username ? ' · utente ' + esc((s.domain ? s.domain + '\\' : '') + s.username) : ' · accesso guest'}${s.vers ? ' · SMB ' + esc(s.vers) : ''}${s.last_scan ? ' · scansione ' + esc(P.fmtDate(s.last_scan)) : ''}</div>
          ${s.error ? `<div class="td bad-text">${esc(s.error)}</div>` : ''}
        </div>
        <div class="actions">
          <button class="btn small" type="button" data-src="test">Verifica</button>
          <button class="btn small" type="button" data-src="${s.mounted ? 'umount' : 'mount'}">${s.mounted ? 'Smonta' : 'Monta'}</button>
          <button class="btn small" type="button" data-src="edit">Modifica</button>
          <button class="btn small danger" type="button" data-src="del">Elimina</button>
        </div>
      </div>`).join('');
  }

  // ---------------------------------------------------------------- azioni sorgenti
  function sourceDialog(src) {
    P.modal({
      title: src ? 'Modifica share' : 'Aggiungi share Windows',
      body: P.sourceFormHtml(src),
      buttons: [{ label: 'Annulla', value: null }, {
        label: src ? 'Salva e rimonta' : 'Aggiungi e monta', cls: 'primary',
        onClick: async (dlg) => {
          const form = P.readSourceForm(dlg);
          if (src && src.username && !form.password) {
            // l'API rifiuta di cambiare server/utente/dominio/versione SMB senza la password per sorgenti con utente
            const norm = (x) => String(x || '').trim();
            const changed = ['unc', 'domain', 'username', 'vers'].some((k) => norm(form[k]) !== norm(src[k]));
            if (changed) { P.toast('Per cambiare server, utente, dominio o versione SMB reinserisci la password', 'bad'); const pw = $('#sf-pass', dlg); if (pw) pw.focus(); return false; }
          }
          let r;
          if (src) r = await P.api('PUT', '/api/sources/' + encodeURIComponent(src.id), form);
          else r = await P.post('/api/sources', form);
          const warnings = (r && Array.isArray(r.warnings)) ? r.warnings : [];
          if (warnings.length) warnings.forEach((w) => P.toast((src ? 'Share aggiornata, ma: ' : 'Share aggiunta, ma: ') + w, 'warn', 8000));
          else P.toast(src ? 'Share aggiornata' : 'Share aggiunta: montaggio e scansione avviati');
          reloadSources();
          return true;
        },
      }],
    });
  }

  async function sourceAction(id, act, btn) {
    const src = S.sources.find((s) => s.id === id); if (!src) return;
    if (act === 'edit') { sourceDialog(src); return; }
    if (act === 'del') {
      const ok = await P.confirm(`Eliminare la share "${src.name || src.unc}"?`, { title: 'Elimina share', ok: 'Elimina', danger: true, detail: 'Verrà smontata e le sue ISO usciranno dal catalogo e dal menu. I file sul server remoto non vengono toccati.' });
      if (!ok) return;
      P.setBusy(btn, true, 'Eliminazione…');
      try { await P.api('DELETE', '/api/sources/' + encodeURIComponent(id)); P.toast('Share eliminata'); } catch (e) { P.fail(e); }
      reloadSources(); return;
    }
    if (act === 'test') {
      P.setBusy(btn, true, 'Verifica…');
      try {
        const r = await P.post(`/api/sources/${encodeURIComponent(id)}/test`);
        const lines = (r && r.output) || [];
        P.modal({ title: (r && r.ok ? 'Connessione riuscita' : 'Connessione non riuscita') + ' · ' + (src.name || src.unc), wide: true,
          body: `<div class="alert ${r && r.ok ? 'ok' : 'bad'}">${r && r.ok ? 'La share risponde e la cartella è leggibile.' : 'Impossibile leggere la share con le credenziali indicate.'}</div><pre class="code mono" style="max-height:50vh">${esc(lines.join('\n') || '(nessun output)')}</pre>` });
      } catch (e) { P.fail(e); }
      P.setBusy(btn, false); return;
    }
    if (act === 'mount' || act === 'umount') {
      P.setBusy(btn, true, act === 'mount' ? 'Montaggio…' : 'Smontaggio…');
      try { await P.post(`/api/sources/${encodeURIComponent(id)}/${act}`); P.toast(act === 'mount' ? 'Share montata' : 'Share smontata'); } catch (e) { P.fail(e); }
      reloadSources();
    }
  }

  // ---------------------------------------------------------------- salva e applica
  function fieldOn(name) { const el = $(`.switch[data-field="${name}"]`, S.root); return el ? P.switchOn(el) : false; }
  const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

  // Le sezioni cache e web viaggiano dentro PUT /api/settings. Qui si controlla che il server
  // le abbia davvero recepite: per la cache si ritenta con POST /api/cache, per web si avvisa.
  async function syncExtraSections(cacheReq, webReq) {
    const warnings = [];
    const cur = S.cache && S.cache.settings;
    const same = cur && !!cur.auto === cacheReq.auto && !!cur.only_enabled === cacheReq.only_enabled
      && Number(cur.min_size_gb) === cacheReq.min_size_gb && Number(cur.keep_free_gb) === cacheReq.keep_free_gb;
    if (cur && !same) {
      try { await P.post('/api/cache', cacheReq); S.cache = await P.get('/api/cache'); }
      catch (e) { warnings.push('Copia locale automatica non salvata: ' + e.message); }
    }
    const c = S.cert;
    if (c && (!!c.enabled !== webReq.https_enabled || !!c.redirect_http !== webReq.redirect_http)) {
      warnings.push("Accesso HTTPS: il server non ha applicato le nuove impostazioni web (https_enabled, redirect_http).");
    }
    return warnings;
  }

  async function save() {
    const r = S.root; const btn = $('#set-save', r);
    const v = (id) => $('#' + id, r).value.trim();
    const dhcp_mode = v('s-dhcp');
    // in modalità proxy si inviano solo interface/server_ip/dhcp_mode; i campi DHCP solo con dhcp_mode === 'full'
    const network = { interface: v('s-iface'), server_ip: v('s-ip'), dhcp_mode };
    if (!IPV4.test(network.server_ip)) { P.toast('IP del server non valido', 'bad'); $('#s-ip', r).focus(); return; }
    if (dhcp_mode === 'full') {
      Object.assign(network, { dhcp_range_start: v('s-r1'), dhcp_range_end: v('s-r2'), dhcp_netmask: v('s-mask'), dhcp_router: v('s-router'), dhcp_dns: v('s-dns'), dhcp_lease: v('s-lease') || '12h' });
      for (const [k, lbl] of [['dhcp_range_start', 'Inizio range'], ['dhcp_range_end', 'Fine range']]) {
        if (!IPV4.test(network[k])) { P.toast(lbl + ' non valido', 'bad'); return; }
      }
      for (const [k, lbl] of [['dhcp_netmask', 'Netmask'], ['dhcp_router', 'Gateway'], ['dhcp_dns', 'DNS']]) {
        if (network[k] && !IPV4.test(network[k])) { P.toast(lbl + ' non valido', 'bad'); return; }
      }
      if (!/^\d{1,4}[mhd]?$/.test(network.dhcp_lease)) { P.toast('Durata lease non valida (es. 12h)', 'bad'); return; }
      const prev = (S.settings.network || {}).dhcp_mode;
      if (prev !== 'full') {
        const ok = await P.confirm('Attivare il DHCP completo? Pixio assegnerà gli indirizzi IP a tutti i PC della rete.', { title: 'DHCP completo', ok: 'Attiva DHCP completo', danger: true, detail: "Procedi solo se in rete NON c'è già un server DHCP (router, firewall, Windows Server…)." });
        if (!ok) return;
      }
    }
    const shareName = v('s-share-name') || 'iso';
    if (!/^[A-Za-z0-9._-]{1,32}$/.test(shareName)) { P.toast('Nome della share non valido (lettere, numeri, . _ -)', 'bad'); $('#s-share-name', r).focus(); return; }
    const interval = parseInt(v('s-scan-int'), 10);
    if (isNaN(interval) || interval < 1) { P.toast('Intervallo di scansione non valido', 'bad'); return; }
    const gb = (id) => parseFloat(v(id).replace(',', '.'));
    const minGb = gb('s-cache-min');
    if (isNaN(minGb) || minGb < 0.1 || minGb > 100) { P.toast('Copia locale: la dimensione minima va da 0,1 a 100 GB', 'bad'); $('#s-cache-min', r).focus(); return; }
    const keepGb = gb('s-cache-free');
    if (isNaN(keepGb) || keepGb < 1 || keepGb > 500) { P.toast('Copia locale: lo spazio da lasciare libero va da 1 a 500 GB', 'bad'); $('#s-cache-free', r).focus(); return; }
    const cacheReq = { auto: fieldOn('cache.auto'), min_size_gb: minGb, only_enabled: fieldOn('cache.only_enabled'), keep_free_gb: keepGb };
    const webReq = { https_enabled: fieldOn('web.https_enabled'), redirect_http: fieldOn('web.redirect_http') };
    if (webReq.https_enabled && !(S.cert && S.cert.enabled)) {
      const ok = await P.confirm("Attivare l'accesso HTTPS a questa interfaccia?", { title: 'Accesso HTTPS', ok: 'Attiva HTTPS', detail: "Il certificato è autofirmato: il browser avviserà che il sito non è attendibile e andrà accettata l'eccezione. I client PXE continuano a usare HTTP." });
      if (!ok) return;
    }
    const payload = {
      network,
      library: { samba_share_enabled: fieldOn('lib.samba_share_enabled'), samba_share_name: shareName, web_upload_enabled: fieldOn('lib.web_upload_enabled') },
      windows: { smb_export_enabled: fieldOn('win.smb_export_enabled') },
      scan: { auto: fieldOn('scan.auto'), interval_min: interval },
      cache: cacheReq,
      web: webReq,
    };
    P.setBusy(btn, true, 'Applicazione…');
    const box = $('#set-result', r);
    try {
      const res = await P.api('PUT', '/api/settings', payload);
      const applied = (res && res.applied) || {};
      await P.refreshStatus().catch(() => null);
      // ricarico i valori effettivi dal server mantenendo il riepilogo appena mostrato
      S.settings = await P.get('/api/settings');
      await loadExtras().catch(() => null);
      const warnings = ((res && res.warnings) || []).concat(await syncExtraSections(cacheReq, webReq));
      const bad = Object.keys(applied).filter((k) => applied[k] !== 'ok');
      box.innerHTML = `<div class="alert ${bad.length || warnings.length ? 'warn' : 'ok'}"><b>${bad.length || warnings.length ? 'Impostazioni salvate, con avvisi' : 'Impostazioni salvate e applicate'}</b>
        <div class="result-list" style="margin-top:6px">${Object.keys(applied).map((k) => `<div>${P.pill(applied[k] === 'ok' ? 'ok' : 'errore', applied[k] === 'ok' ? 'ok' : 'bad')} <span class="mono">${esc(k)}</span>${applied[k] !== 'ok' ? ` <span>${esc(applied[k])}</span>` : ''}</div>`).join('')}</div>
        ${warnings.length ? `<ul>${warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}</div>`;
      P.toast(bad.length || warnings.length ? 'Salvato con avvisi: vedi il riepilogo' : 'Impostazioni salvate e applicate', bad.length || warnings.length ? 'warn' : 'ok');
      const keep = box.innerHTML; render(); $('#set-result', r).innerHTML = keep;
    } catch (e) { P.fail(e); }
    P.setBusy(btn, false);
  }

  // ---------------------------------------------------------------- backup: scarico e ripristino
  async function downloadBackup() {
    const resp = await fetch('/api/backup', { credentials: 'same-origin', cache: 'no-store' });
    if (resp.status === 401) { P.sessionLost(); throw new P.ApiError('Sessione scaduta: accedi di nuovo', 401); }
    if (!resp.ok) {
      let msg = '';
      try { const d = await resp.json(); msg = d && d.error; } catch (e) { /* risposta non JSON */ }
      throw new Error(msg || 'Backup non riuscito (errore ' + resp.status + ')');
    }
    // nome del file dall'header Content-Disposition, con ripiego se manca
    const cd = resp.headers.get('Content-Disposition') || '';
    const m = /filename\*=UTF-8''([^;]+)/i.exec(cd) || /filename="?([^";]+)"?/i.exec(cd);
    let name = 'pixio-backup.tar.gz';
    if (m) { try { name = decodeURIComponent(m[1].trim()); } catch (e) { name = m[1].trim(); } }
    name = name.replace(/[\\/]/g, '_');
    const url = URL.createObjectURL(await resp.blob());
    const a = document.createElement('a');
    a.href = url; a.download = name; a.style.display = 'none';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    return name;
  }

  async function restoreBackup(file) {
    if (!file) return;
    if (file.size > MAX_BACKUP) { P.toast('Archivio troppo grande (massimo 64 MB)', 'bad'); return; }
    const ok = await P.modal({
      title: 'Ripristina la configurazione',
      body: `<p>Stai per ripristinare <b>${esc(file.name)}</b> (${esc(P.fmtBytes(file.size))}).</p>
        <p class="hint">Impostazioni, catalogo ISO, client PXE, flag delle cartelle driver e risposte automatiche vengono <b>sostituiti</b> con quelli dell'archivio. Restano invariate la password di amministratore e le credenziali delle share; le ISO sui dischi non vengono toccate.</p>
        <div class="field check"><input id="bk-ack" type="checkbox"><label for="bk-ack">Ho capito: sostituisci la configurazione attuale</label></div>`,
      buttons: [{ label: 'Annulla', value: false }, { label: 'Ripristina', cls: 'danger', onClick: (dlg) => {
        if (!$('#bk-ack', dlg).checked) { P.toast('Spunta la conferma per procedere', 'warn'); return false; }
        return true;
      } }],
    }).done;
    if (ok !== true) return;
    const box = $('#bk-result', S.root);
    if (box) box.innerHTML = '<div class="alert">Ripristino in corso…</div>';
    let html;
    try {
      const r = await P.api('POST', '/api/backup/restore', file);      // File = Blob: corpo binario
      const restored = (r && r.restored) || []; const warns = (r && r.warnings) || [];
      html = `<div class="alert ${warns.length ? 'warn' : 'ok'}"><b>Ripristino completato</b>
        <div class="result-list" style="margin-top:6px">${restored.map((x) => `<div>${P.pill('ok', 'ok')} <span>${esc(x)}</span></div>`).join('') || '<div>Niente da ripristinare</div>'}</div>
        ${warns.length ? `<ul>${warns.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}</div>`;
      P.toast('Configurazione ripristinata', warns.length ? 'warn' : 'ok');
    } catch (e) {
      P.fail(e);
      html = `<div class="alert bad"><b>Ripristino non riuscito</b><div>${esc(e.message)}</div></div>`;
    }
    await load();
    P.refreshStatus().catch(() => {});
    const nb = S.root && $('#bk-result', S.root); if (nb) nb.innerHTML = html;
  }

  // ---------------------------------------------------------------- copia locale, backup, aggiornamento, certificato
  const EXTRA_ACTS = ['cache-run', 'cache-clear', 'backup-download', 'backup-restore', 'update-check', 'update-apply', 'cert-regen'];

  async function extraAction(act, btn) {
    if (act === 'cache-run') {
      P.setBusy(btn, true, 'Avvio…');
      try {
        const r = await P.post('/api/cache/run');
        P.toast('Copia locale avviata: le ISO grandi richiedono qualche minuto', 'info');
        P.setBusy(btn, true, 'Copia in corso…');
        const job = await P.watchJob(r && r.job_id, null, 3000);
        if (job) P.toast(job.status === 'done' ? 'Copia locale completata' + (job.message ? ': ' + job.message : '') : 'Copia locale: ' + (job.message || job.status), job.status === 'done' ? 'ok' : 'bad');
      } catch (e) { P.fail(e); }
      P.setBusy(btn, false);
      await refreshCache();
      P.refreshStatus().catch(() => {});
      return;
    }
    if (act === 'cache-clear') {
      const st = (S.cache && S.cache.stats) || {};
      if (!st.cached) { P.toast('Non ci sono copie locali da liberare', 'info'); return; }
      const ok = await P.confirm('Eliminare tutte le copie locali delle ISO?', { title: 'Libera le copie locali', ok: 'Libera tutto', danger: true,
        detail: `Vengono liberati ${P.fmtBytes(st.cached_bytes)} (${st.cached} ${st.cached === 1 ? 'copia' : 'copie'}). Le ISO restano sulle share di origine e il boot tornerà a leggerle da lì; con la copia automatica attiva verranno ricopiate alla prossima scansione.` });
      if (!ok) return;
      P.setBusy(btn, true, 'Pulizia…');
      try { const r = await P.post('/api/cache/clear', {}); P.toast(`Copie liberate: ${r.freed || 0} (${P.fmtBytes(r.bytes)})`); } catch (e) { P.fail(e); }
      P.setBusy(btn, false);
      await refreshCache();
      P.refreshStatus().catch(() => {});
      return;
    }
    if (act === 'backup-download') {
      P.setBusy(btn, true, 'Preparazione…');
      try { const name = await downloadBackup(); P.toast('Backup scaricato: ' + name); } catch (e) { P.fail(e); }
      P.setBusy(btn, false);
      return;
    }
    if (act === 'backup-restore') {
      const f = $('#bk-file', S.root);
      if (f) { f.value = ''; f.click(); }
      return;
    }
    if (act === 'update-check') {
      P.setBusy(btn, true, 'Controllo…');
      try { S.upd = await P.get('/api/update/check'); S.updErr = ''; } catch (e) { S.upd = null; S.updErr = e.message; P.fail(e); }
      P.setBusy(btn, false);
      renderUpdate();
      return;
    }
    if (act === 'update-apply') {
      const u = S.upd || {};
      const ok = await P.confirm(`Aggiornare Pixio ${u.current ? 'da ' + u.current + ' ' : ''}alla revisione ${u.remote || 'più recente'}?`, {
        title: 'Aggiorna Pixio', ok: 'Aggiorna adesso', danger: true,
        detail: "Pixio scarica il codice dal repository git, rilancia install.sh e riavvia il servizio: l'interfaccia resta irraggiungibile per circa un minuto e i client che stanno avviandosi in questo momento possono fallire. Scarica prima un backup.",
      });
      if (!ok) return;
      P.setBusy(btn, true, 'Avvio…');
      try {
        const r = await P.post('/api/update/apply');
        P.toast('Aggiornamento avviato: il servizio si riavvierà da solo', 'warn', 9000);
        P.setBusy(btn, true, 'Aggiornamento…');
        const job = await P.watchJob(r && r.job_id, (j) => { const w = S.root && $('#upd-why', S.root); if (w && j && j.message) w.textContent = j.message; }, 4000);
        if (job) P.toast(job.status === 'done' ? 'Aggiornamento completato: ricarica la pagina per usare la nuova versione' : 'Aggiornamento: ' + (job.message || job.status), job.status === 'done' ? 'ok' : 'bad', 9000);
      } catch (e) { P.fail(e); }
      P.setBusy(btn, false);
      try { S.upd = await P.get('/api/update/check'); S.updErr = ''; } catch (e) { /* il servizio potrebbe essere ancora in riavvio */ }
      P.refreshStatus().catch(() => {});
      renderUpdate();
      return;
    }
    if (act === 'cert-regen') {
      const ok = await P.confirm('Rigenerare il certificato HTTPS?', { title: 'Rigenera certificato', ok: 'Rigenera', danger: true,
        detail: "Il certificato attuale viene sostituito: i browser che lo avevano accettato mostreranno di nuovo l'avviso di sicurezza e andrà accettata la nuova impronta. Il certificato è autofirmato e vale 10 anni." });
      if (!ok) return;
      P.setBusy(btn, true, 'Generazione…');
      try {
        const r = await P.post('/api/system/cert/regenerate');
        S.cert = Object.assign({}, S.cert || {}, r); S.certErr = '';
        P.toast('Certificato rigenerato');
      } catch (e) { P.fail(e); }
      P.setBusy(btn, false);
      renderCert();
    }
  }

  // ---------------------------------------------------------------- sistema
  async function systemAction(act, btn) {
    if (EXTRA_ACTS.indexOf(act) !== -1) { extraAction(act, btn); return; }
    if (act === 'logout') { P.logout(); return; }
    if (act === 'password') {
      P.modal({ title: 'Cambia password amministratore',
        body: `<div class="field"><label for="pw-cur">Password attuale</label><input id="pw-cur" type="password" autocomplete="current-password"></div>
               <div class="field"><label for="pw-new">Nuova password</label><input id="pw-new" type="password" autocomplete="new-password"><div class="hint">Almeno 6 caratteri.</div></div>
               <div class="field"><label for="pw-new2">Conferma nuova password</label><input id="pw-new2" type="password" autocomplete="new-password"></div>`,
        buttons: [{ label: 'Annulla', value: null }, { label: 'Cambia password', cls: 'primary', onClick: async (dlg) => {
          const cur = $('#pw-cur', dlg).value; const nw = $('#pw-new', dlg).value;
          if (nw.length < 6) throw new Error('La nuova password deve avere almeno 6 caratteri');
          if (nw !== $('#pw-new2', dlg).value) throw new Error('Le due password non coincidono');
          await P.post('/api/auth/password', { current: cur, new: nw });
          P.toast('Password aggiornata');
          return true;
        } }] });
      return;
    }
    if (act === 'samba-password') {
      P.modal({ title: 'Password Samba per la libreria locale',
        body: `<p class="hint">Password dell'utente <span class="mono">pixio</span> per accedere alla share in scrittura da Windows.</p>
               <div class="field"><label for="smb-pw">Password</label><input id="smb-pw" type="password" autocomplete="new-password"><div class="hint">Almeno 4 caratteri.</div></div>
               <div class="field"><label for="smb-pw2">Conferma</label><input id="smb-pw2" type="password" autocomplete="new-password"></div>`,
        buttons: [{ label: 'Annulla', value: null }, { label: 'Imposta', cls: 'primary', onClick: async (dlg) => {
          const pw = $('#smb-pw', dlg).value;
          if (pw.length < 4) throw new Error('La password deve avere almeno 4 caratteri');
          if (pw !== $('#smb-pw2', dlg).value) throw new Error('Le due password non coincidono');
          await P.post('/api/settings/samba-password', { password: pw });
          P.toast('Password Samba impostata');
          load();
          return true;
        } }] });
      return;
    }
    if (act === 'rebuild') {
      P.setBusy(btn, true, 'Avvio…');
      try {
        const r = await P.post('/api/system/rebuild-ipxe');
        P.toast('Compilazione di iPXE avviata (richiede qualche minuto)', 'info');
        P.setBusy(btn, true, 'Compilazione…');
        const job = await P.watchJob(r && r.job_id, null, 4000);
        if (job) P.toast(job.status === 'done' ? 'iPXE compilato' : 'Compilazione iPXE: ' + (job.message || job.status), job.status === 'done' ? 'ok' : 'bad');
        P.refreshStatus().catch(() => {});
      } catch (e) { P.fail(e); }
      P.setBusy(btn, false); return;
    }
    if (act === 'restart-services') {
      const ok = await P.confirm('Riavviare dnsmasq, nginx e Samba? I client che stanno avviandosi in questo momento potrebbero fallire.', { ok: 'Riavvia servizi' });
      if (!ok) return;
      P.setBusy(btn, true, 'Riavvio…');
      const errs = [];
      for (const name of ['dnsmasq', 'nginx', 'smbd']) {
        try { await P.post('/api/system/service', { name, action: 'restart' }); } catch (e) { errs.push(name + ': ' + e.message); }
      }
      P.setBusy(btn, false);
      if (errs.length) P.toast('Riavvio con errori: ' + errs.join('; '), 'bad', 9000); else P.toast('Servizi riavviati');
      P.refreshStatus().catch(() => {}); return;
    }
    if (act === 'reboot' || act === 'poweroff') {
      const ok = await P.confirm(act === 'reboot' ? 'Riavviare il server Pixio?' : 'Spegnere il server Pixio?', { ok: act === 'reboot' ? 'Riavvia' : 'Spegni', danger: true, detail: act === 'reboot' ? "L'interfaccia tornerà disponibile tra circa un minuto." : 'Per riaccenderlo servirà accesso fisico (o Wake-on-LAN).' });
      if (!ok) return;
      P.setBusy(btn, true, '…');
      try { await P.post('/api/system/power', { action: act }); P.toast(act === 'reboot' ? 'Riavvio in corso…' : 'Spegnimento in corso…', 'warn', 10000); } catch (e) { P.fail(e); }
      P.setBusy(btn, false);
    }
  }

  // ---------------------------------------------------------------- pagina
  P.pages.impostazioni = {
    title: 'Impostazioni',
    mount(root) {
      S.root = root;
      root.innerHTML = `
        <div class="ph"><div><h2>Impostazioni</h2><div class="sub">Share Windows, libreria locale, rete, opzioni avanzate, accesso.</div></div>
          <div class="actions"><button class="btn" type="button" id="set-reload">Annulla modifiche</button><button class="btn primary" type="button" id="set-save">Salva e applica</button></div></div>
        <div id="set-body"><div class="loading">Caricamento…</div></div>`;
      $('#set-save', root).addEventListener('click', save);
      $('#set-reload', root).addEventListener('click', load);
      root.addEventListener('click', (e) => {
        const b = e.target.closest('[data-src],[data-act]'); if (!b) return;
        if (b.dataset.src) {
          if (b.dataset.src === 'add') { sourceDialog(null); return; }
          const item = b.closest('.src-item'); if (item) sourceAction(item.dataset.id, b.dataset.src, b);
        } else systemAction(b.dataset.act, b);
      });
      root.addEventListener('change', (e) => {
        if (e.target.id !== 'bk-file') return;
        const f = e.target.files && e.target.files[0];
        e.target.value = '';
        restoreBackup(f);
      });
      // il reindirizzamento da HTTP ha senso solo con HTTPS attivo
      root.addEventListener('switch-change', (e) => {
        if (e.target.dataset.field !== 'web.https_enabled') return;
        const row = $('#web-redirect-row', root);
        if (row) row.hidden = !P.switchOn(e.target);
      });
      load();
    },
    unmount() { S.root = null; },
  };
})();
