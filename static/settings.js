/* Pixio – pagina Impostazioni: sorgenti (share remote), libreria locale, rete e boot, Windows, sistema.
   Espone anche P.sourceFormHtml / P.readSourceForm, riusati dal wizard di primo avvio. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  const S = { settings: null, sources: null, root: null };
  const SMB_VERS = [['', 'Automatica'], ['3.1.1', 'SMB 3.1.1'], ['3.0', 'SMB 3.0'], ['2.1', 'SMB 2.1'], ['2.0', 'SMB 2.0'], ['1.0', 'SMB 1.0 (NT1, sconsigliato)']];

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
      render();
    } catch (e) {
      if (e.status === 401) return;
      $('#set-body', S.root).innerHTML = `<div class="empty"><h3>Impostazioni non disponibili</h3><p>${esc(e.message)}</p></div>`;
    }
  }
  async function reloadSources() {
    try { S.sources = await P.get('/api/sources'); renderSources(); P.refreshStatus().catch(() => {}); } catch (e) { P.fail(e); }
  }

  // ---------------------------------------------------------------- rendering
  function toggleRow(title, desc, field, on, disabled) {
    return `<div class="toggle-row"><div><div class="tt">${title}</div><div class="td">${desc}</div></div>${P.switchHtml(!!on, `data-field="${field}" aria-label="${esc(title.replace(/<[^>]+>/g, ''))}" ${disabled ? 'disabled' : ''}`)}</div>`;
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
          ${toggleRow('Installazione Windows via rete (share SMB <span class="mono">pxe</span>)', 'Il setup di Windows (WinPE) deve leggere <span class="mono">install.wim</span> da un percorso SMB: con questa opzione Pixio ri-esporta in sola lettura e senza password le ISO Windows montate. Disattivato: WinPE parte ma il setup non trova i file.', 'win.smb_export_enabled', win.smb_export_enabled === true)}
        </div>

        <div class="card"><h3>Sistema</h3>
          <div class="toggle-row" style="border-top:0"><div><div class="tt">iPXE</div><div class="td">Ricompila i binari di boot (undionly.kpxe, ipxe.efi) con l'IP del server incorporato.</div></div><button class="btn small" type="button" data-act="rebuild">Ricompila iPXE</button></div>
          <div class="toggle-row"><div><div class="tt">Servizi</div><div class="td">Riavvia dnsmasq, nginx e Samba.</div></div><button class="btn small" type="button" data-act="restart-services">Riavvia servizi</button></div>
          <div class="toggle-row"><div><div class="tt">Server</div><div class="td">Riavvia o spegni la macchina Pixio.</div></div><span class="actions"><button class="btn small" type="button" data-act="reboot">Riavvia</button><button class="btn small danger" type="button" data-act="poweroff">Spegni</button></span></div>
          <div class="toggle-row"><div><div class="tt">Password amministratore</div><div class="td">Accesso a questa interfaccia.</div></div><button class="btn small" type="button" data-act="password">Cambia</button></div>
          <div class="toggle-row"><div><div class="tt">Sessione</div><div class="td">Esci dall'interfaccia di amministrazione.</div></div><button class="btn small" type="button" data-act="logout">Esci</button></div>
        </div>
      </div>`;
    renderSources();
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
    const payload = {
      network,
      library: { samba_share_enabled: fieldOn('lib.samba_share_enabled'), samba_share_name: shareName, web_upload_enabled: fieldOn('lib.web_upload_enabled') },
      windows: { smb_export_enabled: fieldOn('win.smb_export_enabled') },
      scan: { auto: fieldOn('scan.auto'), interval_min: interval },
    };
    P.setBusy(btn, true, 'Applicazione…');
    const box = $('#set-result', r);
    try {
      const res = await P.api('PUT', '/api/settings', payload);
      const applied = (res && res.applied) || {}; const warnings = (res && res.warnings) || [];
      const bad = Object.keys(applied).filter((k) => applied[k] !== 'ok');
      box.innerHTML = `<div class="alert ${bad.length ? 'warn' : 'ok'}"><b>${bad.length ? 'Impostazioni salvate, con avvisi' : 'Impostazioni salvate e applicate'}</b>
        <div class="result-list" style="margin-top:6px">${Object.keys(applied).map((k) => `<div>${P.pill(applied[k] === 'ok' ? 'ok' : 'errore', applied[k] === 'ok' ? 'ok' : 'bad')} <span class="mono">${esc(k)}</span>${applied[k] !== 'ok' ? ` <span>${esc(applied[k])}</span>` : ''}</div>`).join('')}</div>
        ${warnings.length ? `<ul>${warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}</div>`;
      P.toast(bad.length ? 'Salvato con avvisi: vedi il riepilogo' : 'Impostazioni salvate e applicate', bad.length ? 'warn' : 'ok');
      await P.refreshStatus().catch(() => null);
      // ricarico i valori effettivi dal server mantenendo il riepilogo appena mostrato
      S.settings = await P.get('/api/settings');
      const keep = box.innerHTML; render(); $('#set-result', r).innerHTML = keep;
    } catch (e) { P.fail(e); }
    P.setBusy(btn, false);
  }

  // ---------------------------------------------------------------- sistema
  async function systemAction(act, btn) {
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
      load();
    },
    unmount() { S.root = null; },
  };
})();
