/* Pixio – pagina Log: vista monospace con polling incrementale (cursor), filtro per sorgente, pausa,
   scarico del testo visibile e scorrimento automatico. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$;

  const MAX_LINES = 3000;
  const L = { root: null, timer: null, lines: [], cursor: null, source: 'all', paused: false, autoscroll: true, busy: false, rendered: 0, trimmed: false };

  function fmtLine(l) {
    const lvl = (l.level || '').toLowerCase();
    return `<span class="ln${lvl ? ' lvl-' + esc(lvl) : ''}"><span class="ts">${esc(P.fmtTimeSec(l.ts) || '--:--:--')}</span> <span class="src">${esc(l.source || '')}</span> ${esc(l.msg || '')}</span>`;
  }

  async function poll(reset) {
    if (!L.root || L.busy || (L.paused && !reset) || document.hidden) return;
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

  function download() {
    const text = L.lines.map((l) => `${l.ts || ''} ${l.source || ''} ${l.level ? '[' + l.level + '] ' : ''}${l.msg || ''}`).join('\n') + '\n';
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const d = new Date(); const pad = (n) => (n < 10 ? '0' : '') + n;
    a.href = url; a.download = `pixio-log-${L.source}-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.txt`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  P.pages.log = {
    title: 'Log',
    mount(root) {
      L.root = root; L.lines = []; L.cursor = null; L.busy = false; L.rendered = 0; L.trimmed = false;
      root.innerHTML = `
        <div class="ph"><div><h2>Log</h2><div class="sub">dnsmasq (DHCP/TFTP), nginx (HTTP), Pixio. Aggiornamento ogni 2 secondi.</div></div>
          <div class="actions">
            <select id="log-source" aria-label="Sorgente del log" class="inline-select" style="font-size:13.5px;padding:6px 10px"><option value="all">Tutte le sorgenti</option><option value="dnsmasq">dnsmasq</option><option value="nginx">nginx</option><option value="pixio">pixio</option></select>
            <label class="hint" style="display:inline-flex;align-items:center;gap:6px"><input type="checkbox" id="log-autoscroll" checked> Scorri in automatico</label>
            <button class="btn" type="button" id="log-pause" aria-pressed="false">Pausa</button>
            <button class="btn" type="button" id="log-clear">Svuota</button>
            <button class="btn" type="button" id="log-download">Scarica</button>
          </div></div>
        <div class="hint" id="log-state" style="margin-bottom:6px">Caricamento…</div>
        <pre class="code mono logview" id="log-view" aria-live="off" tabindex="0"></pre>`;
      $('#log-source', root).value = L.source;
      $('#log-autoscroll', root).checked = L.autoscroll;
      $('#log-source', root).addEventListener('change', (e) => { L.source = e.target.value; L.cursor = null; L.lines = []; poll(true); });
      $('#log-autoscroll', root).addEventListener('change', (e) => { L.autoscroll = e.target.checked; if (L.autoscroll) render(false); });
      $('#log-pause', root).addEventListener('click', (e) => {
        L.paused = !L.paused;
        e.currentTarget.textContent = L.paused ? 'Riprendi' : 'Pausa';
        e.currentTarget.setAttribute('aria-pressed', L.paused ? 'true' : 'false');
        const st = $('#log-state', root); if (st) st.textContent = `${L.lines.length} righe${L.paused ? ' · in pausa' : ''}`;
        if (!L.paused) poll();
      });
      $('#log-clear', root).addEventListener('click', () => { L.lines = []; render(false); });
      $('#log-download', root).addEventListener('click', download);
      // se l'utente scorre verso l'alto sospendo lo scorrimento automatico (si riattiva con la casella)
      $('#log-view', root).addEventListener('scroll', (e) => {
        const v = e.target; const atBottom = v.scrollHeight - v.scrollTop - v.clientHeight < 8;
        if (!atBottom && L.autoscroll) { L.autoscroll = false; $('#log-autoscroll', root).checked = false; }
      });
      poll(true);
      L.timer = setInterval(() => poll(false), 2000);
    },
    unmount() { clearInterval(L.timer); L.timer = null; L.root = null; },
  };
})();
