/* Pixio – pagina Client: PC che hanno chiesto il boot da rete, nome modificabile, boot automatico e
   avvio una tantum per MAC, accensione via Wake-on-LAN (singola o su più client selezionati). */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  const C = { list: [], entries: [], root: null, timer: null, q: '', sel: new Set() };

  async function load(silent) {
    if (!C.root) return;
    try {
      const [list, menu] = await Promise.all([P.get('/api/clients'), P.get('/api/menu').catch(() => null)]);
      C.list = Array.isArray(list) ? list : [];
      C.entries = ((menu && menu.entries) || []).filter((e) => e.enabled !== false);
      const known = new Set(C.list.map((c) => c.mac));
      C.sel.forEach((m) => { if (!known.has(m)) C.sel.delete(m); });
      render();
    } catch (e) {
      if (e.status === 401) return;
      if (!silent) $('#cli-body', C.root).innerHTML = `<div class="empty"><h3>Elenco client non disponibile</h3><p>${esc(e.message)}</p></div>`;
    }
  }

  function entryName(slug) {
    if (!slug) return '';
    const e = C.entries.find((x) => x.slug === slug);
    return e ? e.name : slug;
  }

  // Elenco filtrato dal campo di ricerca (MAC con o senza separatori, nome, IP) e ordinato per ultimo avvio.
  function rows() {
    const q = C.q.trim().toLowerCase();
    const qm = q.replace(/[^0-9a-f]/g, '');
    const ok = (c) => {
      if (!q) return true;
      const mac = String(c.mac || '');
      if (mac.includes(q)) return true;
      if (qm && mac.replace(/[^0-9a-f]/g, '').includes(qm)) return true;
      return String(c.name || '').toLowerCase().includes(q) || String(c.ip || '').toLowerCase().includes(q);
    };
    return C.list.filter(ok).sort((a, b) => (P.parseDate(b.last_seen) || 0) - (P.parseDate(a.last_seen) || 0));
  }

  function options(sel, none) {
    const opts = [`<option value="" ${!sel ? 'selected' : ''}>${esc(none)}</option>`]
      .concat(C.entries.map((e) => `<option value="${esc(e.slug)}" ${sel === e.slug ? 'selected' : ''}>${esc(e.name)}</option>`));
    if (sel && !C.entries.some((e) => e.slug === sel)) opts.push(`<option value="${esc(sel)}" selected>${esc(sel)} (non abilitata)</option>`);
    return opts.join('');
  }

  function syncSelection() {
    const btn = $('#cli-wake-sel', C.root);
    if (!btn || btn.classList.contains('busy')) return;
    const n = C.sel.size;
    btn.disabled = !n;
    btn.textContent = n ? `Accendi selezionati (${n})` : 'Accendi selezionati';
  }

  function render() {
    const box = $('#cli-body', C.root);
    // non ridisegno mentre l'utente sta scrivendo un nome
    if (box.contains(document.activeElement) && document.activeElement.classList.contains('inline-input')) return;
    const sub = $('#cli-sub', C.root);
    const today = C.list.filter((c) => { const d = P.parseDate(c.last_seen); return d && d.toDateString() === new Date().toDateString(); }).length;
    sub.textContent = C.list.length
      ? `${C.list.length} client visti · ${today} oggi. Puoi dare un nome, assegnare una voce automatica e accendere i PC da rete.`
      : 'PC che hanno chiesto il boot da rete. Puoi assegnare una voce automatica per MAC.';
    if (!C.list.length) {
      box.innerHTML = '<div class="empty"><h3>Nessun client ancora</h3><p>Avvia un PC da rete (di solito F12 o "Network boot" nel BIOS/UEFI): comparirà qui con MAC, IP e firmware. Con Secure Boot attivo il boot da rete con iPXE non parte: disattivalo.</p></div>';
      syncSelection();
      return;
    }
    const list = rows();
    const cnt = $('#cli-count', C.root);
    if (cnt) cnt.textContent = C.q.trim() ? `${list.length} di ${C.list.length}` : '';
    if (!list.length) {
      box.innerHTML = '<div class="empty"><h3>Nessun client corrisponde alla ricerca</h3><p>Prova con una parte del MAC, del nome o dell\'indirizzo IP.</p><button class="btn" type="button" data-act="clear-q">Azzera la ricerca</button></div>';
      syncSelection();
      return;
    }
    const allSel = list.every((c) => C.sel.has(c.mac));
    box.innerHTML = `<div class="tbl-wrap"><table><thead><tr>
        <th><input type="checkbox" id="cli-all" ${allSel ? 'checked' : ''} aria-label="Seleziona tutti i client elencati"></th>
        <th>MAC</th><th>Ultimo IP</th><th>Firmware</th><th>Nome</th><th>Ultimo boot</th><th>Boot automatico</th>
        <th title="Vale per un solo avvio e ha la precedenza sul boot automatico">Avvia una volta</th><th>Volte</th><th><span class="sr-only">Azioni</span></th></tr></thead><tbody>${list.map((c) => {
      const a = P.archLabel(c.arch);
      const once = c.boot_once || '';
      return `<tr data-mac="${esc(c.mac)}">
        <td><input type="checkbox" class="cli-chk" ${C.sel.has(c.mac) ? 'checked' : ''} aria-label="Seleziona ${esc(c.mac)}"></td>
        <td class="mono">${esc(c.mac)}</td><td class="mono">${esc(c.ip || '—')}</td><td>${P.pill(a.label, a.cls)}</td>
        <td><input class="inline-input" value="${esc(c.name || '')}" placeholder="${esc(c.vendor_class || 'senza nome')}" aria-label="Nome del client ${esc(c.mac)}" maxlength="60" data-orig="${esc(c.name || '')}"></td>
        <td title="${c.last_entry ? 'Ultima voce avviata: ' + esc(entryName(c.last_entry)) : ''}">${esc(P.fmtDate(c.last_seen))}${c.last_entry ? ' · ' + esc(entryName(c.last_entry)) : ''}</td>
        <td><select class="inline-select" data-field="auto_boot" style="max-width:132px" aria-label="Boot automatico per ${esc(c.mac)}" title="${c.auto_boot ? 'Avvio automatico: ' + esc(entryName(c.auto_boot)) : 'Mostra il menu di boot'}">${options(c.auto_boot || '', 'menu')}</select></td>
        <td><select class="inline-select" data-field="boot_once" style="max-width:132px" aria-label="Avvia una volta per ${esc(c.mac)}" title="${once ? 'Al prossimo avvio: ' + esc(entryName(once)) : 'Solo per il prossimo avvio, ha la precedenza sul boot automatico'}">${options(once, 'nessuna')}</select>
          ${once ? ' ' + P.pill('una volta', 'acc') : ''}</td>
        <td class="num">${esc(c.count != null ? c.count : '—')}</td>
        <td class="actions-cell">${c.wol_supported === false ? '' : '<button class="btn small" type="button" data-act="wake" aria-label="Accendi ' + esc(c.mac) + '">Accendi</button> '}<button class="btn small danger" type="button" data-act="del" aria-label="Elimina ${esc(c.mac)}">Elimina</button></td></tr>`;
    }).join('')}</tbody></table></div>`;
    syncSelection();
  }

  async function patch(mac, data, el) {
    if (el) el.disabled = true;
    try {
      const upd = await P.api('PATCH', '/api/clients/' + encodeURIComponent(mac), data);
      const c = C.list.find((x) => x.mac === mac); if (c) Object.assign(c, upd || data);
      let msg = 'Nome salvato';
      if (data.auto_boot !== undefined) msg = data.auto_boot ? 'Boot automatico impostato' : 'Boot automatico disattivato';
      else if (data.boot_once !== undefined) msg = data.boot_once ? `Al prossimo avvio: ${entryName(data.boot_once)}` : 'Avvio una volta annullato';
      P.toast(msg);
    } catch (e) { P.fail(e); }
    if (el) el.disabled = false;
    render();
  }

  async function wakeOne(mac, btn) {
    P.setBusy(btn, true, 'Invio…');
    try {
      const r = await P.api('POST', '/api/clients/' + encodeURIComponent(mac) + '/wake');
      const c = C.list.find((x) => x.mac === mac);
      P.toast(`Magic packet inviato a ${(c && c.name) || mac}${r && r.sent ? ` (${r.sent} pacchetti)` : ''}`);
    } catch (e) { P.fail(e); }
    P.setBusy(btn, false);
  }

  async function wakeSelected(btn) {
    const macs = Array.from(C.sel);
    if (!macs.length) return;
    P.setBusy(btn, true, 'Invio…');
    try {
      const r = await P.api('POST', '/api/clients/wake', { macs });
      const res = (r && r.results) || {};
      const ok = Object.keys(res).filter((m) => res[m]);
      const bad = Object.keys(res).filter((m) => !res[m]);
      if (ok.length) P.toast(`Accensione richiesta per ${ok.length} client`);
      if (bad.length) P.toast(`Invio non riuscito per: ${bad.join(', ')}`, 'bad');
    } catch (e) { P.fail(e); }
    P.setBusy(btn, false);
    syncSelection();
  }

  P.pages.client = {
    title: 'Client',
    mount(root) {
      C.root = root; C.sel = new Set();
      root.innerHTML = `
        <div class="ph"><div><h2>Client</h2><div class="sub" id="cli-sub">Caricamento…</div></div>
          <div class="actions"><button class="btn" type="button" id="cli-wake-sel" disabled>Accendi selezionati</button><button class="btn" type="button" id="cli-reload">Aggiorna</button></div></div>
        <div class="filters">
          <input class="search" id="cli-q" type="search" placeholder="Cerca per MAC, nome o IP…" aria-label="Cerca client" value="${esc(C.q)}">
          <span id="cli-count" class="hint"></span>
        </div>
        <div id="cli-body"><div class="loading">Caricamento…</div></div>`;
      $('#cli-reload', root).addEventListener('click', () => load());
      $('#cli-wake-sel', root).addEventListener('click', (e) => wakeSelected(e.currentTarget));
      $('#cli-q', root).addEventListener('input', (e) => { C.q = e.target.value; render(); });
      root.addEventListener('change', (e) => {
        if (e.target.id === 'cli-all') {
          const on = e.target.checked;
          rows().forEach((c) => { if (on) C.sel.add(c.mac); else C.sel.delete(c.mac); });
          $$('.cli-chk', root).forEach((ch) => { ch.checked = on; });
          syncSelection();
          return;
        }
        const tr = e.target.closest('tr[data-mac]'); if (!tr) return;
        const mac = tr.dataset.mac;
        if (e.target.classList.contains('cli-chk')) {
          if (e.target.checked) C.sel.add(mac); else C.sel.delete(mac);
          const all = $('#cli-all', root);
          if (all) all.checked = rows().every((c) => C.sel.has(c.mac));
          syncSelection();
        } else if (e.target.classList.contains('inline-input')) {
          const name = e.target.value.trim();
          if (name === e.target.dataset.orig) return;
          patch(mac, { name }, e.target);
        } else if (e.target.dataset.field === 'auto_boot') {
          patch(mac, { auto_boot: e.target.value || null }, e.target);
        } else if (e.target.dataset.field === 'boot_once') {
          patch(mac, { boot_once: e.target.value || null }, e.target);
        }
      });
      root.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.classList.contains('inline-input')) e.target.blur(); });
      root.addEventListener('click', async (e) => {
        const b = e.target.closest('[data-act]'); if (!b) return;
        if (b.dataset.act === 'clear-q') { C.q = ''; $('#cli-q', root).value = ''; render(); return; }
        const tr = b.closest('tr[data-mac]'); if (!tr) return;
        const mac = tr.dataset.mac;
        if (b.dataset.act === 'wake') { wakeOne(mac, b); return; }
        if (b.dataset.act !== 'del') return;
        const ok = await P.confirm(`Eliminare il client ${mac} dall'elenco?`, { ok: 'Elimina', danger: true, detail: 'Ricomparirà al prossimo boot da rete, senza nome e senza boot automatico.' });
        if (!ok) return;
        try {
          await P.api('DELETE', '/api/clients/' + encodeURIComponent(mac));
          P.toast('Client eliminato'); C.sel.delete(mac);
          C.list = C.list.filter((c) => c.mac !== mac); render(); P.refreshStatus().catch(() => {});
        } catch (err) { P.fail(err); }
      });
      load();
      C.timer = setInterval(() => { if (!document.hidden) load(true); }, 10000);
    },
    unmount() { clearInterval(C.timer); C.timer = null; C.root = null; C.sel = new Set(); },
  };
})();
