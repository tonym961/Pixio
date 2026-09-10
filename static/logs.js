/* Pixio – pagina Log. Due schede:
   - "In diretta": vista monospace dei log del server (dnsmasq, nginx, Pixio) con polling incrementale
     (cursor), filtro per sorgente, pausa, scarico del testo visibile e scorrimento automatico.
   - "Installazioni": i log che i PC depositano da soli sulla share pxelog quando il programma di
     installazione di Windows finisce (docs/API.md, sezione 22). Elenco, apertura del contenuto,
     eliminazione. È qui che si legge un'installazione fallita, senza fotografare lo schermo. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  const MAX_LINES = 3000;
  const L = { root: null, timer: null, lines: [], cursor: null, source: 'all', paused: false, autoscroll: true, busy: false, rendered: 0, trimmed: false };
  const I = { list: [], enabled: false, share: '', dir: '', loaded: false, error: '' };
  const T = { tab: 'live' };

  // ------------------------------------------------------------------ log del server (in diretta)
  function fmtLine(l) {
    const lvl = (l.level || '').toLowerCase();
    return `<span class="ln${lvl ? ' lvl-' + esc(lvl) : ''}"><span class="ts">${esc(P.fmtTimeSec(l.ts) || '--:--:--')}</span> <span class="src">${esc(l.source || '')}</span> ${esc(l.msg || '')}</span>`;
  }

  async function poll(reset) {
    if (!L.root || T.tab !== 'live' || L.busy || (L.paused && !reset) || document.hidden) return;
    L.busy = true;
    try {
      const q = new URLSearchParams({ source: L.source, limit: reset ? '300' : '200' });
      if (L.cursor && !reset) q.set('cursor', L.cursor);
      const r = await P.get('/api/logs?' + q.toString());
      const lines = (r && r.lines) || [];
      if (r && r.cursor) L.cursor = r.cursor;
      if (reset) L.lines = lines; else if (lines.length) L.lines = L.lines.concat(lines);
      if (L.lines.length > MAX_LINES) { L.lines = L.lines.slice(-MAX_LINES); L.trimmed = true; }
      if (reset || lines.length) render(reset ? false : true);
      const st = $('#log-state', L.root); if (st) st.textContent = `${L.lines.length} righe${L.paused ? ' · in pausa' : ''}`;
    } catch (e) {
      if (e.status !== 401) { const st = $('#log-state', L.root); if (st) st.textContent = 'Errore: ' + e.message; }
    }
    L.busy = false;
  }

  function render(append) {
    const view = $('#log-view', L.root); if (!view) return;
    if (!L.lines.length) { view.innerHTML = '<span class="c">Nessuna riga di log per questa sorgente. Le righe compaiono quando un client fa il boot o Pixio esegue una scansione.</span>'; L.rendered = 0; return; }
    // Aggiungo solo le righe nuove quando l'elenco è cresciuto in coda; altrimenti ridisegno tutto
    if (append && L.rendered > 0 && L.rendered < L.lines.length && !L.trimmed) {
      view.insertAdjacentHTML('beforeend', '\n' + L.lines.slice(L.rendered).map(fmtLine).join('\n'));
    } else {
      view.innerHTML = L.lines.map(fmtLine).join('\n');
    }
    L.rendered = L.lines.length; L.trimmed = false;
    if (L.autoscroll) view.scrollTop = view.scrollHeight;
  }

  function saveText(text, name) {
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  function download() {
    const text = L.lines.map((l) => `${l.ts || ''} ${l.source || ''} ${l.level ? '[' + l.level + '] ' : ''}${l.msg || ''}`).join('\n') + '\n';
    const d = new Date(); const pad = (n) => (n < 10 ? '0' : '') + n;
    saveText(text, `pixio-log-${L.source}-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.txt`);
  }

  function liveHtml() {
    return `<div class="actions" style="justify-content:flex-end;margin-bottom:8px">
        <select id="log-source" aria-label="Sorgente del log" class="inline-select" style="font-size:13.5px;padding:6px 10px"><option value="all">Tutte le sorgenti</option><option value="dnsmasq">dnsmasq</option><option value="nginx">nginx</option><option value="pixio">pixio</option></select>
        <label class="hint" style="display:inline-flex;align-items:center;gap:6px"><input type="checkbox" id="log-autoscroll" checked> Scorri in automatico</label>
        <button class="btn" type="button" id="log-pause" aria-pressed="false">Pausa</button>
        <button class="btn" type="button" id="log-clear">Svuota</button>
        <button class="btn" type="button" id="log-download">Scarica</button>
      </div>
      <div class="hint" id="log-state" style="margin-bottom:6px">Caricamento…</div>
      <pre class="code mono logview" id="log-view" aria-live="off" tabindex="0"></pre>`;
  }

  function mountLive(panel) {
    panel.innerHTML = liveHtml();
    $('#log-source', panel).value = L.source;
    $('#log-autoscroll', panel).checked = L.autoscroll;
    $('#log-source', panel).addEventListener('change', (e) => { L.source = e.target.value; L.cursor = null; L.lines = []; poll(true); });
    $('#log-autoscroll', panel).addEventListener('change', (e) => { L.autoscroll = e.target.checked; if (L.autoscroll) render(false); });
    $('#log-pause', panel).addEventListener('click', (e) => {
      L.paused = !L.paused;
      e.currentTarget.textContent = L.paused ? 'Riprendi' : 'Pausa';
      e.currentTarget.setAttribute('aria-pressed', L.paused ? 'true' : 'false');
      const st = $('#log-state', panel); if (st) st.textContent = `${L.lines.length} righe${L.paused ? ' · in pausa' : ''}`;
      if (!L.paused) poll();
    });
    $('#log-clear', panel).addEventListener('click', () => { L.lines = []; render(false); });
    $('#log-download', panel).addEventListener('click', download);
    // se l'utente scorre verso l'alto sospendo lo scorrimento automatico (si riattiva con la casella)
    $('#log-view', panel).addEventListener('scroll', (e) => {
      const v = e.target; const atBottom = v.scrollHeight - v.scrollTop - v.clientHeight < 8;
      if (!atBottom && L.autoscroll) { L.autoscroll = false; $('#log-autoscroll', panel).checked = false; }
    });
    L.lines = []; L.cursor = null; L.rendered = 0; L.trimmed = false;
    poll(true);
  }

  // ------------------------------------------------------------------ log delle installazioni
  async function loadSetup(silent) {
    try {
      const r = await P.get('/api/setuplogs');
      I.list = (r && r.logs) || []; I.enabled = !!(r && r.enabled);
      I.share = (r && r.share) || ''; I.dir = (r && r.dir) || '';
      I.error = ''; I.loaded = true;
    } catch (e) {
      if (e.status === 401) return;
      I.error = e.message; I.loaded = true;
    }
    updateTabCount();
    if (T.tab === 'setup' && !silent) renderSetup();
  }

  function updateTabCount() {
    const n = $('#log-n-setup', L.root);
    if (n) n.textContent = I.list.length ? ` (${I.list.length})` : '';
  }

  function esitoPill(d) {
    if (d.ok) return P.pill('setup terminato', 'ok');
    if (d.esito) return P.pill('esito ' + d.esito, 'bad');
    return P.pill('esito ignoto', 'neutral');
  }

  function renderSetup() {
    const panel = $('#log-panel', L.root); if (!panel) return;
    const sub = $('#log-sub', L.root);
    if (sub) {
      sub.textContent = I.enabled
        ? `I PC depositano qui i log del programma di installazione (${I.share || 'share pxelog'}). ${I.list.length} ricevuti.`
        : 'La raccolta automatica è disattivata: si attiva in Impostazioni → Windows, insieme all\'installazione via rete.';
    }
    const toolbar = `<div class="actions" style="justify-content:flex-end;margin-bottom:8px">
        <button class="btn" type="button" id="slog-reload">Aggiorna</button>
        <button class="btn danger" type="button" id="slog-clear" ${I.list.length ? '' : 'disabled'}>Elimina tutti</button>
      </div>`;
    let body;
    if (I.error) {
      body = `<div class="empty"><h3>Elenco non disponibile</h3><p>${esc(I.error)}</p></div>`;
    } else if (!I.loaded) {
      body = '<div class="hint">Caricamento…</div>';
    } else if (!I.list.length) {
      body = `<div class="empty"><h3>Nessun log ricevuto</h3>
        <p>Quando il programma di installazione di Windows termina, il PC copia da solo i propri log in
        <span class="mono">${esc(I.share || '\\\\<server>\\pxelog')}</span> e la cartella compare qui: file di risposta usato,
        <span class="mono">setupact.log</span>, <span class="mono">setuperr.log</span>, stato dei dischi e della rete.</p>
        ${I.enabled ? '' : '<p class="hint">La raccolta è disattivata: attiva "Installazione Windows via rete" e "Raccolta dei log di installazione" in Impostazioni.</p>'}</div>`;
    } else {
      body = `<div class="tbl-wrap"><table><thead><tr>
        <th>Ricevuto</th><th>PC</th><th>Immagine</th><th>Esito</th><th>Contenuto</th><th>File</th><th><span class="sr-only">Azioni</span></th>
        </tr></thead><tbody>${I.list.map((d) => `<tr data-name="${esc(d.name)}">
          <td>${esc(P.fmtDate(d.received * 1000))}<div class="hint mono">${esc(d.name)}</div></td>
          <td>${esc(d.pc || d.client || '—')}${d.mac && d.mac !== 'sconosciuto' ? `<div class="hint mono">${esc(d.mac)}</div>` : ''}</td>
          <td>${esc(d.image || '—')}</td>
          <td>${esitoPill(d)}</td>
          <td style="max-width:520px">${d.errors && d.errors.length
            ? `<div class="mono" style="font-size:12px;overflow-wrap:anywhere">${esc(d.errors[d.errors.length - 1])}</div>${d.error_file ? `<div class="hint mono">${esc(d.error_file)}</div>` : ''}`
            : '<span class="hint">nessuna riga di errore</span>'}</td>
          <td class="num">${d.files} · ${esc(P.fmtBytes(d.size))}</td>
          <td class="actions-cell"><button class="btn small" type="button" data-act="open">Apri</button>
            <button class="btn small danger" type="button" data-act="del" aria-label="Elimina ${esc(d.name)}">Elimina</button></td>
        </tr>`).join('')}</tbody></table></div>`;
    }
    panel.innerHTML = toolbar + body;
    $('#slog-reload', panel).addEventListener('click', async (e) => { P.setBusy(e.currentTarget, true, 'Aggiorno…'); await loadSetup(); });
    $('#slog-clear', panel).addEventListener('click', async (e) => {
      if (!await P.confirm(`Eliminare tutti i ${I.list.length} log ricevuti?`, { title: 'Log delle installazioni', ok: 'Elimina tutti', danger: true, detail: 'I file vengono tolti dal server: i PC ne depositeranno di nuovi alla prossima installazione.' })) return;
      P.setBusy(e.currentTarget, true, 'Elimino…');
      try { const r = await P.api('DELETE', '/api/setuplogs'); P.toast(`${(r && r.deleted) || 0} log eliminati`); } catch (err) { P.fail(err); }
      await loadSetup();
    });
    $$('[data-act]', panel).forEach((b) => b.addEventListener('click', (e) => {
      const name = e.target.closest('tr').dataset.name;
      if (e.target.dataset.act === 'open') openSetup(name);
      else delSetup(name, e.target);
    }));
  }

  async function delSetup(name, btn) {
    if (!await P.confirm(`Eliminare il log ${name}?`, { title: 'Log dell\'installazione', ok: 'Elimina', danger: true })) return;
    P.setBusy(btn, true, 'Elimino…');
    try { await P.api('DELETE', '/api/setuplogs/' + encodeURIComponent(name)); P.toast('Log eliminato'); } catch (e) { P.fail(e); }
    await loadSetup();
  }

  // Riepilogo + contenuto di un file, nel pannello laterale. Il file si sceglie dall'elenco a tendina.
  const RIEP = [['pc', 'PC'], ['client', 'Indirizzo IP'], ['mac', 'MAC'], ['image', 'Immagine'],
    ['started', 'Avvio da rete'], ['esito', 'Esito di setup.exe']];

  function detailHtml(d) {
    const righe = RIEP.filter(([k]) => d[k]).map(([k, lab]) => `<tr><th style="text-transform:none;letter-spacing:0">${esc(lab)}</th><td class="mono">${esc(d[k])}</td></tr>`).join('');
    const files = (d.file_list || []).map((f) => `<option value="${esc(f.name)}" ${f.name === d.file ? 'selected' : ''}>${esc(f.name)} (${esc(P.fmtBytes(f.size))})</option>`).join('');
    return `<div class="tbl-wrap" style="margin-bottom:12px"><table><tbody>${righe}
        <tr><th style="text-transform:none;letter-spacing:0">Ricevuto</th><td>${esc(P.fmtDate(d.received * 1000))}</td></tr>
        <tr><th style="text-transform:none;letter-spacing:0">File</th><td>${d.files} · ${esc(P.fmtBytes(d.size))}</td></tr></tbody></table></div>
      ${d.errors && d.errors.length ? `<div class="alert bad"><b>Righe di errore${d.error_file ? ' in ' + esc(d.error_file) : ''}</b>
        <pre class="code mono" style="margin-top:6px;white-space:pre-wrap">${esc(d.errors.join('\n'))}</pre></div>` : ''}
      <div class="actions" style="margin:10px 0 8px">
        <select class="inline-select" id="slog-file" aria-label="File da mostrare" style="flex:1 1 auto;min-width:0">${files}</select>
        <button class="btn small" type="button" id="slog-save">Scarica</button>
      </div>
      ${d.truncated ? '<div class="hint">File lungo: mostrata solo la parte finale.</div>' : ''}
      <pre class="code mono logview" id="slog-content" style="max-height:52vh">${esc(d.content || '(vuoto)')}</pre>`;
  }

  async function openSetup(name, file) {
    P.drawer.open(name, '<div class="hint">Caricamento…</div>');
    let d;
    try {
      d = await P.get('/api/setuplogs/' + encodeURIComponent(name) + (file ? '?file=' + encodeURIComponent(file) : ''));
    } catch (e) {
      P.drawer.body.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`;
      return;
    }
    P.drawer.setTitle(d.image ? `${d.image} · ${d.client || d.name}` : name);
    P.drawer.body.innerHTML = detailHtml(d);
    const sel = $('#slog-file', P.drawer.body);
    if (sel) sel.addEventListener('change', (e) => openSetup(name, e.target.value));
    const save = $('#slog-save', P.drawer.body);
    if (save) save.addEventListener('click', () => saveText(d.content || '', `${name}-${(d.file || 'log').replace(/[/\\]/g, '-')}`));
  }

  // ------------------------------------------------------------------ schede
  function showTab(tab) {
    T.tab = tab === 'setup' ? 'setup' : 'live';
    $$('#log-tabs button', L.root).forEach((b) => b.setAttribute('aria-pressed', b.dataset.tab === T.tab ? 'true' : 'false'));
    const panel = $('#log-panel', L.root);
    if (T.tab === 'live') {
      const sub = $('#log-sub', L.root);
      if (sub) sub.textContent = 'dnsmasq (DHCP/TFTP), nginx (HTTP), Pixio. Aggiornamento ogni 2 secondi.';
      mountLive(panel);
    } else {
      renderSetup();
      loadSetup(true).then(() => { if (T.tab === 'setup') renderSetup(); });
    }
  }

  P.pages.log = {
    title: 'Log',
    mount(root) {
      L.root = root; L.lines = []; L.cursor = null; L.busy = false; L.rendered = 0; L.trimmed = false;
      I.loaded = false; I.list = []; I.error = '';
      root.innerHTML = `
        <div class="ph"><div><h2>Log</h2><div class="sub" id="log-sub"></div></div>
          <div class="actions"><div class="seg" id="log-tabs">
            <button type="button" data-tab="live" aria-pressed="true">In diretta</button>
            <button type="button" data-tab="setup" aria-pressed="false">Installazioni<span id="log-n-setup"></span></button>
          </div></div></div>
        <div id="log-panel"></div>`;
      $$('#log-tabs button', root).forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));
      showTab(T.tab);
      // il conteggio nella scheda si vede anche restando sui log in diretta
      loadSetup(true);
      L.timer = setInterval(() => poll(false), 2000);
    },
    unmount() { clearInterval(L.timer); L.timer = null; L.root = null; },
  };
})();
