/* Pixio – pagina Menu di boot: impostazioni del menu iPXE, ordine delle voci (drag&drop o frecce)
   e anteprima dello script generato per UEFI / BIOS. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  const M = { data: null, root: null, platform: 'efi', dragging: null, saving: false, bgStamp: Date.now() };

  // ---------------------------------------------------------------- tema (aspetto del menu iPXE)
  const THEME_DEFAULT = { bg: '#0B1220', accent: '#3FC1CF', fg: '#E6ECF2', muted: '#7C8A99', logo_text: 'PIXIO', subtitle: 'Avvio da rete' };
  const THEME_COLORS = [['bg', 'Sfondo', 'Colore di fondo del menu'], ['accent', 'Accento', 'Titoli dei gruppi e voce selezionata'], ['fg', 'Testo', 'Voci normali'], ['muted', 'Testo attenuato', 'Note e piè di pagina']];
  const HEX_RE = /^#?([0-9a-fA-F]{6})$/;
  const BG_URL = '/pxe/inject/theme/bg.png';

  function normHex(v, fallback) {
    const m = HEX_RE.exec(String(v || '').trim());
    return m ? ('#' + m[1]).toUpperCase() : fallback;
  }

  function currentTheme() {
    const t = Object.assign({}, THEME_DEFAULT, (M.data && M.data.settings && M.data.settings.theme) || {});
    THEME_COLORS.forEach(([k]) => { t[k] = normHex(t[k], THEME_DEFAULT[k]); });
    return t;
  }

  function themeHtml(s, entries, groups) {
    const t = currentTheme();
    const swatches = THEME_COLORS.map(([k, label, hint]) => `
      <div class="field swatch">
        <label for="m-th-${k}">${esc(label)}</label>
        <div class="swatch-row">
          <input type="color" id="m-th-${k}" data-th-color="${k}" value="${esc(t[k])}" aria-label="${esc(label)} (selettore)">
          <input type="text" class="mono" data-th-hex="${k}" value="${esc(t[k])}" maxlength="7" spellcheck="false" autocomplete="off" pattern="#[0-9a-fA-F]{6}" aria-label="${esc(label)} (esadecimale)">
        </div>
        <div class="hint">${esc(hint)}</div>
      </div>`).join('');
    // finto menu: primo gruppo + prime voci reali (o segnaposto), una voce selezionata
    const g = groups[0] || 'Sistemi';
    const items = entries.filter((e) => (e.group || 'Altro') === g).slice(0, 3).map((e) => e.name);
    while (items.length < 3) items.push(['Installazione', 'Strumenti di ripristino', 'Memtest86+'][items.length]);
    const sel = s.default && s.default !== 'local' && s.default !== 'shell' ? (entries.find((e) => e.slug === s.default) || {}).name : null;
    const selIdx = Math.max(0, items.indexOf(sel));
    const rows = items.map((n, i) => `<div class="${i === selIdx ? 'sel' : 'it'}">${esc(n)}</div>`).join('');
    return `
      <div class="card theme-card">
        <div class="theme-hd"><div><h3>Aspetto del menu</h3><div class="hint">Colori, logo e sottotitolo dello sfondo grafico. L'anteprima è indicativa: si applica con "Salva".</div></div>
          <button class="btn small" type="button" id="m-th-reset">Ripristina predefiniti</button></div>
        <div class="theme-grid">
          <div>
            <div class="swatches">${swatches}</div>
            <div class="row2">
              <div class="field"><label for="m-th-logo">Logo (testo)</label><input id="m-th-logo" data-th-text="logo_text" value="${esc(t.logo_text)}" maxlength="16"><div class="hint">Max 16 caratteri.</div></div>
              <div class="field"><label for="m-th-sub">Sottotitolo</label><input id="m-th-sub" data-th-text="subtitle" value="${esc(t.subtitle)}" maxlength="40"><div class="hint">Max 40 caratteri.</div></div>
            </div>
          </div>
          <div class="tprev-wrap">
            <div class="tprev" id="m-th-prev" aria-label="Anteprima del menu di boot" style="--t-bg:${esc(t.bg)};--t-accent:${esc(t.accent)};--t-fg:${esc(t.fg)};--t-muted:${esc(t.muted)}">
              <img id="m-th-img" src="${BG_URL}?t=${M.bgStamp}" alt="" width="512" height="384" draggable="false">
              <div class="tprev-logo"><span class="tl" data-tp="logo_text">${esc(t.logo_text)}</span><span class="ts" data-tp="subtitle">${esc(t.subtitle)}</span></div>
              <div class="tprev-menu mono">
                <div class="ttl">${esc(s.title || 'PIXIO - Avvio da rete')}</div>
                <div class="grp">${esc(g)}</div>
                ${rows}
                <div class="grp">Sistema</div>
                <div class="it">Avvia dal disco locale</div>
                <div class="dim">Avvio automatico tra ${esc(s.timeout != null ? s.timeout : 30)} s…</div>
              </div>
            </div>
            <div class="hint">Anteprima in scala 1:2 (1024×768). L'immagine di sfondo viene rigenerata dal server a ogni salvataggio.</div>
          </div>
        </div>
      </div>`;
  }

  function bindTheme() {
    const r = M.root; const prev = $('#m-th-prev', r); if (!prev) return;
    const img = $('#m-th-img', r);
    img.addEventListener('error', () => { img.hidden = true; });
    const apply = (k, v) => { prev.style.setProperty('--t-' + k, v); };
    $$('[data-th-color]', r).forEach((c) => c.addEventListener('input', () => {
      const k = c.dataset.thColor; const v = normHex(c.value, THEME_DEFAULT[k]);
      const h = $(`[data-th-hex="${k}"]`, r); h.value = v; h.classList.remove('invalid');
      apply(k, v);
    }));
    $$('[data-th-hex]', r).forEach((h) => {
      const sync = () => {
        const k = h.dataset.thHex; const m = HEX_RE.exec(h.value.trim());
        if (!m) { h.classList.add('invalid'); return; }
        const v = ('#' + m[1]).toUpperCase();
        h.classList.remove('invalid'); $(`[data-th-color="${k}"]`, r).value = v; apply(k, v);
      };
      h.addEventListener('input', sync);
      h.addEventListener('blur', () => { const m = HEX_RE.exec(h.value.trim()); if (m) h.value = ('#' + m[1]).toUpperCase(); });
    });
    $$('[data-th-text]', r).forEach((i) => i.addEventListener('input', () => {
      const el = $(`[data-tp="${i.dataset.thText}"]`, r); if (el) el.textContent = i.value;
    }));
    $('#m-th-reset', r).addEventListener('click', () => {
      THEME_COLORS.forEach(([k]) => {
        $(`[data-th-color="${k}"]`, r).value = THEME_DEFAULT[k];
        const h = $(`[data-th-hex="${k}"]`, r); h.value = THEME_DEFAULT[k]; h.classList.remove('invalid');
        apply(k, THEME_DEFAULT[k]);
      });
      $$('[data-th-text]', r).forEach((i) => { i.value = THEME_DEFAULT[i.dataset.thText]; const el = $(`[data-tp="${i.dataset.thText}"]`, r); if (el) el.textContent = i.value; });
      P.toast('Valori predefiniti ripristinati: premi "Salva" per applicarli', 'info');
    });
  }

  /** Legge il tema dal form; ritorna null (e mette a fuoco il campo) se un colore non è valido. */
  function readTheme() {
    const r = M.root; const t = {};
    for (const [k, label] of THEME_COLORS) {
      const h = $(`[data-th-hex="${k}"]`, r); if (!h) return currentTheme();
      const m = HEX_RE.exec(h.value.trim());
      if (!m) { P.toast(`Colore "${label}" non valido: usa il formato #RRGGBB`, 'bad'); h.focus(); return null; }
      t[k] = ('#' + m[1]).toUpperCase();
    }
    t.logo_text = ($('#m-th-logo', r).value.trim() || 'PIXIO').slice(0, 16);
    t.subtitle = $('#m-th-sub', r).value.trim().slice(0, 40);
    return t;
  }

  async function load() {
    if (!M.root) return;
    try {
      M.data = await P.get('/api/menu');
      render();
    } catch (e) {
      if (e.status === 401) return;
      $('#menu-body', M.root).innerHTML = `<div class="empty"><h3>Menu non disponibile</h3><p>${esc(e.message)}</p></div>`;
    }
  }

  function enabledEntries() {
    return ((M.data && M.data.entries) || []).filter((e) => e.enabled !== false).slice().sort((a, b) => (a.order || 0) - (b.order || 0));
  }

  function render() {
    const s = (M.data && M.data.settings) || {};
    const entries = enabledEntries();
    const groups = (s.groups || []).slice();
    entries.forEach((e) => { const g = e.group || 'Altro'; if (!groups.includes(g)) groups.push(g); });
    const defaultOpts = [['local', 'Avvia dal disco locale'], ['shell', 'Shell iPXE']].concat(entries.map((e) => [e.slug, e.name]));
    if (s.default && !defaultOpts.some((o) => o[0] === s.default)) defaultOpts.push([s.default, s.default + ' (non abilitata)']);

    const listHtml = entries.length ? groups.map((g) => {
      const items = entries.filter((e) => (e.group || 'Altro') === g);
      return `<li class="group" data-group="${esc(g)}">${esc(g)}</li>` + items.map((e) => `
        <li draggable="true" data-slug="${esc(e.slug)}">
          <span class="handle" aria-hidden="true">⋮⋮</span>
          <span class="lbl">${esc(e.name)}<span class="hint" style="font-weight:400"> · ${esc(e.type_name || '')}</span></span>
          <span class="pill neutral mono">${esc(e.slug)}</span>
          <span class="arrows"><button type="button" data-move="-1" aria-label="Sposta su ${esc(e.name)}">↑</button><button type="button" data-move="1" aria-label="Sposta giù ${esc(e.name)}">↓</button></span>
        </li>`).join('');
    }).join('') : '';
    const sysItems = [];
    if (s.show_memtest !== false) sysItems.push(['Memtest86+', 'integrato']);
    if (s.show_local !== false) sysItems.push(['Avvia dal disco locale', s.default === 'local' ? 'predefinita' : '']);
    if (s.show_shell !== false) sysItems.push(['Shell iPXE', s.default === 'shell' ? 'predefinita' : '']);
    if (s.show_reboot !== false) sysItems.push(['Riavvia', '']);
    const sysHtml = sysItems.length ? `<li class="group">Sistema</li>` + sysItems.map(([n, p]) => `<li class="static"><span class="handle" style="visibility:hidden" aria-hidden="true">⋮⋮</span><span class="lbl">${esc(n)}</span>${p ? P.pill(p, 'acc') : ''}</li>`).join('') : '';

    $('#menu-body', M.root).innerHTML = `
      <div class="menu-grid">
        <div>
          <form id="menu-form" novalidate>
            <div class="row2">
              <div class="field"><label for="m-title">Titolo</label><input id="m-title" value="${esc(s.title || '')}" maxlength="80"></div>
              <div class="field"><label for="m-timeout">Timeout (s)</label><input id="m-timeout" type="number" min="0" max="3600" value="${esc(s.timeout != null ? s.timeout : 30)}"><div class="hint">0 = attende una scelta.</div></div>
            </div>
            <div class="field"><label for="m-default">Voce predefinita allo scadere</label><select id="m-default">${defaultOpts.map(([v, l]) => `<option value="${esc(v)}" ${v === (s.default || 'local') ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select><div class="hint">Consigliato: disco locale, così un PC acceso per sbaglio in PXE riparte normalmente.</div></div>
            <div class="field"><label>Voci di sistema</label><div class="checks">
              <label><input type="checkbox" id="m-show_local" ${s.show_local !== false ? 'checked' : ''}> Disco locale</label>
              <label><input type="checkbox" id="m-show_shell" ${s.show_shell !== false ? 'checked' : ''}> Shell iPXE</label>
              <label><input type="checkbox" id="m-show_reboot" ${s.show_reboot !== false ? 'checked' : ''}> Riavvia</label>
              <label><input type="checkbox" id="m-show_memtest" ${s.show_memtest !== false ? 'checked' : ''}> Memtest86+</label>
            </div></div>
            <div class="field"><label for="m-groups">Gruppi del menu (uno per riga, nell'ordine di visualizzazione)</label><textarea id="m-groups">${esc((s.groups || []).join('\n'))}</textarea></div>
          </form>
          ${themeHtml(s, entries, groups)}
          <div class="eyebrow" style="margin:6px 0 8px">Ordine delle voci</div>
          ${entries.length
            ? `<ul class="mlist" id="mlist" aria-label="Voci del menu, trascina per riordinare">${listHtml}${sysHtml}</ul><p class="hint" style="margin-top:8px">Trascina una voce (o usa le frecce) per cambiare posizione o gruppo. L'ordine viene salvato subito.</p>`
            : `<div class="empty"><h3>Nessuna voce abilitata</h3><p>Attiva "Nel menu" sulle ISO nel <a href="#/iso">Catalogo</a>: compariranno qui e nel menu di boot.</p></div>`}
        </div>
        <div>
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap">
            <div class="eyebrow">Anteprima script iPXE</div>
            <span class="actions"><div class="seg" role="group" aria-label="Piattaforma"><button type="button" data-plat="efi" aria-pressed="${M.platform === 'efi'}">UEFI</button><button type="button" data-plat="bios" aria-pressed="${M.platform === 'bios'}">BIOS</button></div>
            <a class="btn small" href="/boot.ipxe?platform=${M.platform === 'efi' ? 'efi' : 'pcbios'}" target="_blank" rel="noopener">Apri /boot.ipxe</a></span>
          </div>
          <pre class="code mono" id="m-preview" style="max-height:70vh">${esc(((M.data && M.data.preview) || {})[M.platform] || '(anteprima non disponibile)')}</pre>
        </div>
      </div>`;

    $$('[data-plat]', M.root).forEach((b) => b.addEventListener('click', () => {
      M.platform = b.dataset.plat;
      $$('[data-plat]', M.root).forEach((x) => x.setAttribute('aria-pressed', x === b ? 'true' : 'false'));
      $('#m-preview', M.root).textContent = ((M.data && M.data.preview) || {})[M.platform] || '(anteprima non disponibile)';
      const a = $('a[href^="/boot.ipxe"]', M.root); if (a) a.href = '/boot.ipxe?platform=' + (M.platform === 'efi' ? 'efi' : 'pcbios');
    }));
    bindList();
    bindTheme();
  }

  // ---------------------------------------------------------------- riordino
  function bindList() {
    const ul = $('#mlist', M.root); if (!ul) return;
    ul.addEventListener('dragstart', (e) => {
      const li = e.target.closest('li[data-slug]'); if (!li) { e.preventDefault(); return; }
      M.dragging = li; li.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', li.dataset.slug); } catch (err) { /* ignora */ }
    });
    ul.addEventListener('dragover', (e) => {
      if (!M.dragging) return;
      const over = e.target.closest('li'); if (!over || over === M.dragging || over.classList.contains('static')) return;
      e.preventDefault(); e.dataTransfer.dropEffect = 'move';
      const r = over.getBoundingClientRect();
      const after = (e.clientY - r.top) > r.height / 2;
      if (over.classList.contains('group') && !after && !over.previousElementSibling) return; // mai sopra il primo gruppo
      if (over.classList.contains('group') && over.dataset.group === undefined && after) return; // mai dentro "Sistema"
      if (after) over.parentNode.insertBefore(M.dragging, over.nextSibling); else over.parentNode.insertBefore(M.dragging, over);
    });
    ul.addEventListener('drop', (e) => { e.preventDefault(); });
    ul.addEventListener('dragend', () => {
      const li = M.dragging; M.dragging = null; if (!li) return;
      li.classList.remove('dragging');
      persist(li.dataset.slug);
    });
    ul.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-move]'); if (!b) return;
      const li = b.closest('li[data-slug]'); const dir = Number(b.dataset.move);
      if (dir < 0) {
        const prev = li.previousElementSibling; if (!prev) return;
        if (prev.classList.contains('group') && !prev.previousElementSibling) return;
        li.parentNode.insertBefore(li, prev);
      } else {
        const next = li.nextElementSibling; if (!next || next.classList.contains('static') || (next.classList.contains('group') && !next.dataset.group)) return;
        li.parentNode.insertBefore(next, li);
      }
      persist(li.dataset.slug, b);
    });
  }

  async function persist(movedSlug, focusBtn) {
    if (M.saving) return;
    const ul = $('#mlist', M.root); if (!ul) return;
    const order = []; let group = null; let newGroup = null;
    $$('li', ul).forEach((li) => {
      if (li.dataset.group !== undefined) group = li.dataset.group;
      else if (li.dataset.slug) { order.push(li.dataset.slug); if (li.dataset.slug === movedSlug) newGroup = group; }
    });
    const entry = enabledEntries().find((e) => e.slug === movedSlug);
    M.saving = true;
    try {
      if (entry && newGroup != null && newGroup !== (entry.group || 'Altro')) await P.api('PATCH', `/api/catalog/${encodeURIComponent(movedSlug)}`, { group: newGroup });
      await P.post('/api/catalog/reorder', { order });
      M.saving = false;
      await load();
      P.toast('Ordine salvato');
      if (focusBtn) { const nb = $(`li[data-slug="${CSS.escape(movedSlug)}"] button[data-move="${focusBtn.dataset.move}"]`, M.root); if (nb) nb.focus(); }
    } catch (e) { M.saving = false; P.fail(e); load(); }
  }

  // ---------------------------------------------------------------- salvataggio impostazioni
  async function save() {
    const r = M.root; const btn = $('#menu-save', r);
    const timeout = parseInt($('#m-timeout', r).value, 10);
    if (isNaN(timeout) || timeout < 0) { P.toast('Timeout non valido', 'bad'); $('#m-timeout', r).focus(); return; }
    const theme = readTheme(); if (!theme) return;
    const settings = {
      title: $('#m-title', r).value.trim() || 'PIXIO - Avvio da rete',
      timeout,
      default: $('#m-default', r).value,
      show_local: $('#m-show_local', r).checked,
      show_shell: $('#m-show_shell', r).checked,
      show_reboot: $('#m-show_reboot', r).checked,
      show_memtest: $('#m-show_memtest', r).checked,
      groups: $('#m-groups', r).value.split('\n').map((s) => s.trim()).filter(Boolean),
      theme,
    };
    P.setBusy(btn, true, 'Salvataggio…');
    try {
      const res = await P.api('PUT', '/api/menu', { settings });
      M.bgStamp = Date.now();                       // forza il ricaricamento dello sfondo rigenerato
      P.toast('Menu salvato e rigenerato');
      ((res && res.warnings) || []).forEach((w) => P.toast(w, 'warn', 8000));
      await load();
      P.refreshStatus().catch(() => {});
    } catch (e) { P.fail(e); }
    P.setBusy(btn, false);
  }

  P.pages.menu = {
    title: 'Menu di boot',
    mount(root) {
      M.root = root;
      root.innerHTML = `
        <div class="ph"><div><h2>Menu di boot</h2><div class="sub">Ordine, gruppi, voce predefinita e timeout. L'anteprima a destra è lo script iPXE reale servito ai client.</div></div>
          <div class="actions"><button class="btn" type="button" id="menu-reload">Aggiorna</button><button class="btn primary" type="button" id="menu-save">Salva</button></div></div>
        <div id="menu-body"><div class="loading">Caricamento…</div></div>`;
      $('#menu-save', root).addEventListener('click', save);
      $('#menu-reload', root).addEventListener('click', load);
      root.addEventListener('submit', (e) => { e.preventDefault(); save(); });
      load();
    },
    unmount() { M.root = null; M.data = null; M.dragging = null; },
  };
})();
