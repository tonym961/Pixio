/* Pixio – pagina Client: PC che hanno chiesto il boot da rete, nome modificabile, boot automatico per MAC. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  const C = { list: [], entries: [], root: null, timer: null };

  async function load(silent) {
    if (!C.root) return;
    try {
      const [list, menu] = await Promise.all([P.get('/api/clients'), P.get('/api/menu').catch(() => null)]);
      C.list = Array.isArray(list) ? list : [];
      C.entries = ((menu && menu.entries) || []).filter((e) => e.enabled !== false);
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

  function render() {
    const box = $('#cli-body', C.root);
    // non ridisegno mentre l'utente sta scrivendo un nome
    if (box.contains(document.activeElement) && document.activeElement.classList.contains('inline-input')) return;
    const sub = $('#cli-sub', C.root);
    const today = C.list.filter((c) => { const d = P.parseDate(c.last_seen); return d && d.toDateString() === new Date().toDateString(); }).length;
    sub.textContent = C.list.length ? `${C.list.length} client visti · ${today} oggi. Puoi dare un nome e assegnare una voce automatica per MAC.` : 'PC che hanno chiesto il boot da rete. Puoi assegnare una voce automatica per MAC.';
    if (!C.list.length) {
      box.innerHTML = '<div class="empty"><h3>Nessun client ancora</h3><p>Avvia un PC da rete (di solito F12 o "Network boot" nel BIOS/UEFI): comparirà qui con MAC, IP e firmware. Con Secure Boot attivo il boot da rete con iPXE non parte: disattivalo.</p></div>';
      return;
    }
    const rows = C.list.slice().sort((a, b) => (P.parseDate(b.last_seen) || 0) - (P.parseDate(a.last_seen) || 0));
    box.innerHTML = `<div class="tbl-wrap"><table><thead><tr><th>MAC</th><th>Ultimo IP</th><th>Firmware</th><th>Nome</th><th>Ultimo boot</th><th>Boot automatico</th><th>Volte</th><th><span class="sr-only">Azioni</span></th></tr></thead><tbody>${rows.map((c) => {
      const a = P.archLabel(c.arch);
      const auto = c.auto_boot || '';
      const opts = [`<option value="" ${!auto ? 'selected' : ''}>menu</option>`].concat(C.entries.map((e) => `<option value="${esc(e.slug)}" ${auto === e.slug ? 'selected' : ''}>${esc(e.name)}</option>`));
      if (auto && !C.entries.some((e) => e.slug === auto)) opts.push(`<option value="${esc(auto)}" selected>${esc(auto)} (non abilitata)</option>`);
      return `<tr data-mac="${esc(c.mac)}">
        <td class="mono">${esc(c.mac)}</td><td class="mono">${esc(c.ip || '—')}</td><td>${P.pill(a.label, a.cls)}</td>
        <td><input class="inline-input" value="${esc(c.name || '')}" placeholder="${esc(c.vendor_class || 'senza nome')}" aria-label="Nome del client ${esc(c.mac)}" maxlength="60" data-orig="${esc(c.name || '')}"></td>
        <td>${esc(P.fmtDate(c.last_seen))}${c.last_entry ? ' · ' + esc(entryName(c.last_entry)) : ''}</td>
        <td><select class="inline-select" aria-label="Boot automatico per ${esc(c.mac)}">${opts.join('')}</select></td>
        <td class="num">${esc(c.count != null ? c.count : '—')}</td>
        <td class="actions-cell"><button class="btn small danger" type="button" data-act="del" aria-label="Elimina ${esc(c.mac)}">Elimina</button></td></tr>`;
    }).join('')}</tbody></table></div>`;
  }

  async function patch(mac, data, el) {
    if (el) el.disabled = true;
    try {
      const upd = await P.api('PATCH', '/api/clients/' + encodeURIComponent(mac), data);
      const c = C.list.find((x) => x.mac === mac); if (c) Object.assign(c, upd || data);
      P.toast(data.name !== undefined ? 'Nome salvato' : (data.auto_boot ? 'Boot automatico impostato' : 'Boot automatico disattivato'));
    } catch (e) { P.fail(e); }
    if (el) el.disabled = false;
    render();
  }

  P.pages.client = {
    title: 'Client',
    mount(root) {
      C.root = root;
      root.innerHTML = `
        <div class="ph"><div><h2>Client</h2><div class="sub" id="cli-sub">Caricamento…</div></div><div class="actions"><button class="btn" type="button" id="cli-reload">Aggiorna</button></div></div>
        <div id="cli-body"><div class="loading">Caricamento…</div></div>`;
      $('#cli-reload', root).addEventListener('click', () => load());
      root.addEventListener('change', (e) => {
        const tr = e.target.closest('tr[data-mac]'); if (!tr) return;
        if (e.target.classList.contains('inline-input')) {
          const name = e.target.value.trim();
          if (name === e.target.dataset.orig) return;
          patch(tr.dataset.mac, { name }, e.target);
        } else if (e.target.classList.contains('inline-select')) {
          patch(tr.dataset.mac, { auto_boot: e.target.value || null }, e.target);
        }
      });
      root.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.classList.contains('inline-input')) e.target.blur(); });
      root.addEventListener('click', async (e) => {
        const b = e.target.closest('[data-act="del"]'); if (!b) return;
        const tr = b.closest('tr[data-mac]'); const mac = tr.dataset.mac;
        const ok = await P.confirm(`Eliminare il client ${mac} dall'elenco?`, { ok: 'Elimina', danger: true, detail: 'Ricomparirà al prossimo boot da rete, senza nome e senza boot automatico.' });
        if (!ok) return;
        try { await P.api('DELETE', '/api/clients/' + encodeURIComponent(mac)); P.toast('Client eliminato'); C.list = C.list.filter((c) => c.mac !== mac); render(); P.refreshStatus().catch(() => {}); } catch (err) { P.fail(err); }
      });
      load();
      C.timer = setInterval(() => { if (!document.hidden) load(true); }, 10000);
    },
    unmount() { clearInterval(C.timer); C.timer = null; C.root = null; },
  };
})();
