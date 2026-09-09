/* Pixio – pagina Menu di boot: impostazioni del menu iPXE, ordine delle voci (drag&drop o frecce)
   e anteprima dello script generato per UEFI / BIOS. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  const M = { data: null, root: null, platform: 'efi', dragging: null, saving: false, bgStamp: Date.now() };

  // ---------------------------------------------------------------- tema (aspetto del menu iPXE)
  const THEME_DEFAULT = { bg: '#0B1220', accent: '#3FC1CF', fg: '#E6ECF2', muted: '#7C8A99', logo_text: 'PIXIO', subtitle: 'Avvio da rete', style: 'testo', resolution: '1024x768' };
  const THEME_COLORS = [['bg', 'Sfondo', 'Colore di fondo del menu'], ['accent', 'Accento', 'Titoli dei gruppi e voce selezionata'], ['fg', 'Testo', 'Voci normali'], ['muted', 'Testo attenuato', 'Note e piè di pagina']];
  const HEX_RE = /^#?([0-9a-fA-F]{6})$/;
  // Stile della console del menu di boot. I tempi qui sotto sono misurati in QEMU (BIOS e UEFI,
  // menu di 11 voci, 5 ripetizioni per configurazione, tempo fra il tasto e il cambio sullo schermo).
  const STYLES = [
    ['testo', 'Testo (consigliato) · colori a tutto schermo'],
    ['grafico', 'Grafico · con lo sfondo disegnato dal server'],
    ['compatibile', 'Compatibilità · console di testo del firmware'],
  ];
  const STYLE_HINT = {
    testo: 'iPXE prende il framebuffer video e scrive direttamente in memoria: la console del firmware viene spenta. Misurato a 1024×768: menu pronto in 0,17 s (BIOS) e 0,15 s (UEFI), 38 ms per spostamento della selezione, zero chiamate al firmware. L’evidenziazione della voce scelta si vede sempre, anche in UEFI.',
    grafico: 'Come "Testo" più lo sfondo. Alla stessa risoluzione lo spostamento della selezione costa uguale (38 ms a 1024×768): lo sfondo non pesa sui tasti. Cambia solo la comparsa del menu, che passa da 0,17/0,15 s a 0,28/0,25 s (BIOS/UEFI) a 1024×768, perché il PNG va scaricato e decodificato una volta sola; a 640×480 sono 0,18/0,17 s contro 0,12/0,13 s.',
    compatibile: 'Usa la console di testo del firmware: ogni carattere è una chiamata al BIOS (fino a 3 INT 10h) o allo UEFI (ConOut), e a ogni spostamento della selezione iPXE riscrive due righe intere, cioè circa 170 chiamate. Su PC con Console Redirection, Serial-over-LAN, BMC o AMT attivi si arriva ai 5 secondi per tasto. In UEFI, misurato: i colori del tema non arrivano e premere le frecce non cambia niente sullo schermo, la voce selezionata non si evidenzia. Da usare solo se sul PC il framebuffer non parte.',
  };
  const RESOLUTIONS = [['1024x768', '1024 × 768'], ['800x600', '800 × 600'], ['640x480', '640 × 480']];
  const RES_HINT = 'Vale per gli stili Testo e Grafico. Comparsa del menu con lo sfondo (BIOS/UEFI): 1024×768 0,28/0,25 s · 800×600 0,22/0,21 s · 640×480 0,18/0,17 s. Tempo per tasto: 39/38 · 32/32 · 29/29 ms. Senza sfondo: 0,17/0,15 s a 1024×768 e 0,12/0,13 s a 640×480. Sotto i 1024×768 il menu ha meno righe e meno colonne: con molte ISO conviene restare a 1024×768.';
  const PREV_HINT = {
    testo: 'Anteprima dello stile Testo: nessuna immagine, solo i colori del tema disegnati da iPXE nel framebuffer.',
    grafico: 'Anteprima in scala dello sfondo. L’immagine viene rigenerata dal server a ogni salvataggio.',
    compatibile: 'Console di testo del firmware: 80×25 caratteri e i 16 colori standard VGA. I colori del tema qui non si applicano.',
  };
  const VGA = { bg: '#000000', accent: '#00AAAA', fg: '#AAAAAA', muted: '#555555' };   // resa tipica della console del firmware

  function normHex(v, fallback) {
    const m = HEX_RE.exec(String(v || '').trim());
    return m ? ('#' + m[1]).toUpperCase() : fallback;
  }

  function currentTheme() {
    const t = Object.assign({}, THEME_DEFAULT, (M.data && M.data.settings && M.data.settings.theme) || {});
    THEME_COLORS.forEach(([k]) => { t[k] = normHex(t[k], THEME_DEFAULT[k]); });
    if (!STYLES.some(([v]) => v === t.style)) t.style = THEME_DEFAULT.style;
    if (!RESOLUTIONS.some(([v]) => v === t.resolution)) t.resolution = THEME_DEFAULT.resolution;
    return t;
  }

  /** Colori usati dall'anteprima: con la console del firmware i colori del tema non arrivano. */
  function prevColors(t) {
    return t.style === 'compatibile' ? VGA : t;
  }

  function bgUrl() {
    return ((M.data && M.data.settings && M.data.settings.theme_bg_url) || '/pxe/inject/theme/bg-1024x768.png') + '?t=' + M.bgStamp;
  }

  function themeHtml(s, entries, groups) {
    const t = currentTheme();
    const c = prevColors(t);
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
        <div class="theme-hd"><div><h3>Aspetto e reattività del menu</h3><div class="hint">Lo stile decide dove iPXE disegna il menu: nel framebuffer video (veloce) o nella console del firmware (lenta su molti PC). L'anteprima è indicativa: si applica con "Salva".</div></div>
          <button class="btn small" type="button" id="m-th-reset">Ripristina predefiniti</button></div>
        <div class="theme-grid">
          <div>
            <div class="row2">
              <div class="field"><label for="m-th-style">Stile del menu</label>
                <select id="m-th-style" data-th-sel="style">${STYLES.map(([v, l]) => `<option value="${esc(v)}" ${v === t.style ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>
                <div class="hint" id="m-th-style-hint">${esc(STYLE_HINT[t.style])}</div></div>
              <div class="field" id="m-th-res-field" ${t.style === 'compatibile' ? 'hidden' : ''}><label for="m-th-res">Risoluzione</label>
                <select id="m-th-res" data-th-sel="resolution">${RESOLUTIONS.map(([v, l]) => `<option value="${esc(v)}" ${v === t.resolution ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>
                <div class="hint">${esc(RES_HINT)}</div></div>
            </div>
            <div class="swatches">${swatches}</div>
            <div class="row2">
              <div class="field"><label for="m-th-logo">Logo (testo)</label><input id="m-th-logo" data-th-text="logo_text" value="${esc(t.logo_text)}" maxlength="16"><div class="hint">Max 16 caratteri. Disegnato sullo sfondo: si vede solo con lo stile Grafico.</div></div>
              <div class="field"><label for="m-th-sub">Sottotitolo</label><input id="m-th-sub" data-th-text="subtitle" value="${esc(t.subtitle)}" maxlength="40"><div class="hint">Max 40 caratteri. Anche questo fa parte dello sfondo grafico.</div></div>
            </div>
          </div>
          <div class="tprev-wrap">
            <div class="tprev st-${esc(t.style)}" id="m-th-prev" aria-label="Anteprima del menu di boot" style="--t-bg:${esc(c.bg)};--t-accent:${esc(c.accent)};--t-fg:${esc(c.fg)};--t-muted:${esc(c.muted)}">
              <img id="m-th-img" src="${esc(bgUrl())}" alt="" width="512" height="384" draggable="false">
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
            <div class="hint" id="m-th-prev-hint">${esc(PREV_HINT[t.style])} <span id="m-th-prev-res">${esc(t.style === 'compatibile' ? '' : t.resolution.replace('x', '×'))}</span></div>
          </div>
        </div>
      </div>`;
  }

  function bindTheme() {
    const r = M.root; const prev = $('#m-th-prev', r); if (!prev) return;
    const img = $('#m-th-img', r);
    img.addEventListener('error', () => { img.hidden = true; });
    const apply = (k, v) => { if (readStyle() !== 'compatibile') prev.style.setProperty('--t-' + k, v); };
    const readStyle = () => { const e = $('#m-th-style', r); return e ? e.value : THEME_DEFAULT.style; };
    const readRes = () => { const e = $('#m-th-res', r); return e ? e.value : THEME_DEFAULT.resolution; };
    // l'anteprima segue lo stile: immagine e fascia del logo solo in "grafico", colori VGA in "compatibile"
    const syncStyle = () => {
      const st = readStyle();
      prev.className = 'tprev st-' + st;
      $('#m-th-style-hint', r).textContent = STYLE_HINT[st] || '';
      $('#m-th-res-field', r).hidden = st === 'compatibile';
      const c = st === 'compatibile' ? VGA : readColors();
      ['bg', 'accent', 'fg', 'muted'].forEach((k) => prev.style.setProperty('--t-' + k, c[k]));
      $('#m-th-prev-hint', r).firstChild.nodeValue = (PREV_HINT[st] || '') + ' ';
      $('#m-th-prev-res', r).textContent = st === 'compatibile' ? '' : readRes().replace('x', '×');
      if (st === 'grafico') { img.hidden = false; img.src = bgUrl(); }
    };
    const readColors = () => {
      const c = {};
      THEME_COLORS.forEach(([k]) => { const h = $(`[data-th-hex="${k}"]`, r); c[k] = normHex(h ? h.value : '', THEME_DEFAULT[k]); });
      return c;
    };
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
    $$('[data-th-sel]', r).forEach((sl) => sl.addEventListener('change', syncStyle));
    $$('[data-th-text]', r).forEach((i) => i.addEventListener('input', () => {
      const el = $(`[data-tp="${i.dataset.thText}"]`, r); if (el) el.textContent = i.value;
    }));
    $('#m-th-reset', r).addEventListener('click', () => {
      THEME_COLORS.forEach(([k]) => {
        $(`[data-th-color="${k}"]`, r).value = THEME_DEFAULT[k];
        const h = $(`[data-th-hex="${k}"]`, r); h.value = THEME_DEFAULT[k]; h.classList.remove('invalid');
      });
      $('#m-th-style', r).value = THEME_DEFAULT.style;
      $('#m-th-res', r).value = THEME_DEFAULT.resolution;
      $$('[data-th-text]', r).forEach((i) => { i.value = THEME_DEFAULT[i.dataset.thText]; const el = $(`[data-tp="${i.dataset.thText}"]`, r); if (el) el.textContent = i.value; });
      syncStyle();
      P.toast('Valori predefiniti ripristinati: premi "Salva" per applicarli', 'info');
    });
    syncStyle();
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
    const st = $('#m-th-style', r); t.style = st && STYLES.some(([v]) => v === st.value) ? st.value : THEME_DEFAULT.style;
    const rs = $('#m-th-res', r); t.resolution = rs && RESOLUTIONS.some(([v]) => v === rs.value) ? rs.value : THEME_DEFAULT.resolution;
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
    const subMode = SUBMENU_MODES.some(([v]) => v === s.submenus) ? s.submenus : 'auto';
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
              <div class="field"><label for="m-title">Titolo</label><input id="m-title" value="${esc(s.title || '')}" maxlength="60"><div class="hint">Max 60 caratteri.</div></div>
              <div class="field"><label for="m-timeout">Timeout (s)</label><input id="m-timeout" type="number" min="0" max="600" value="${esc(s.timeout != null ? s.timeout : 30)}"><div class="hint">0 = attende una scelta. Massimo 600 s.</div></div>
            </div>
            <div class="field"><label for="m-default">Voce predefinita allo scadere</label><select id="m-default">${defaultOpts.map(([v, l]) => `<option value="${esc(v)}" ${v === (s.default || 'local') ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select><div class="hint">Consigliato: disco locale, così un PC acceso per sbaglio in PXE riparte normalmente.</div></div>
            <div class="field"><span class="field-label">Voci di sistema</span><div class="checks">
              <label><input type="checkbox" id="m-show_local" ${s.show_local !== false ? 'checked' : ''}> Disco locale</label>
              <label><input type="checkbox" id="m-show_shell" ${s.show_shell !== false ? 'checked' : ''}> Shell iPXE</label>
              <label><input type="checkbox" id="m-show_reboot" ${s.show_reboot !== false ? 'checked' : ''}> Riavvia</label>
              <label><input type="checkbox" id="m-show_memtest" ${s.show_memtest !== false ? 'checked' : ''}> Memtest86+</label>
            </div></div>
            <div class="field"><label for="m-groups">Gruppi del menu (uno per riga, nell'ordine di visualizzazione)</label><textarea id="m-groups">${esc((s.groups || []).join('\n'))}</textarea><div class="hint">Max 20 gruppi da 60 caratteri.</div></div>
            <div class="row2">
              <div class="field"><label for="m-submenus">Sottomenu per gruppo</label>
                <select id="m-submenus">${SUBMENU_MODES.map(([v, l]) => `<option value="${esc(v)}" ${v === subMode ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>
                <div class="hint">Con i sottomenu il menu di boot mostra un elemento per gruppo (con il numero di voci); le ISO compaiono nel secondo livello, le voci di sistema restano nel menu principale.</div></div>
              <div class="field" id="m-subthr-field" ${subMode === 'auto' ? '' : 'hidden'}><label for="m-submenu_threshold">Soglia (numero di voci)</label>
                <input id="m-submenu_threshold" type="number" min="1" max="100" value="${esc(s.submenu_threshold != null ? s.submenu_threshold : 8)}">
                <div class="hint">I sottomenu compaiono quando le voci avviabili superano questo numero. Da 1 a 100.</div></div>
            </div>
            <div class="field"><label for="m-answer_timeout">Scelta dell'installazione: timeout (s)</label>
              <input id="m-answer_timeout" type="number" min="0" max="120" value="${esc(s.answer_timeout != null ? s.answer_timeout : 10)}">
              <div class="hint">Quando una ISO ha più di un'installazione automatica collegata (Catalogo → Dettagli), dopo averla scelta il PC mostra un menu con le installazioni disponibili: allo scadere di questi secondi parte da sola la predefinita. 0 = attende la scelta. Massimo 120 s.</div></div>
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
    const sub = $('#m-submenus', M.root);
    if (sub) sub.addEventListener('change', () => { $('#m-subthr-field', M.root).hidden = sub.value !== 'auto'; });
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
    if (timeout > 600) { P.toast('Timeout troppo alto: massimo 600 secondi', 'bad'); $('#m-timeout', r).focus(); return; }
    const title = $('#m-title', r).value.trim();
    if (title.length > 60) { P.toast('Titolo troppo lungo: massimo 60 caratteri', 'bad'); $('#m-title', r).focus(); return; }
    const groups = $('#m-groups', r).value.split('\n').map((s) => s.trim()).filter(Boolean);
    if (groups.length > 20) { P.toast('Troppi gruppi: massimo 20', 'bad'); $('#m-groups', r).focus(); return; }
    const longGroup = groups.find((g) => g.length > 60);
    if (longGroup) { P.toast(`Nome del gruppo troppo lungo (max 60 caratteri): "${longGroup.slice(0, 30)}…"`, 'bad'); $('#m-groups', r).focus(); return; }
    const submenus = $('#m-submenus', r).value;
    const threshold = parseInt($('#m-submenu_threshold', r).value, 10);
    if (submenus === 'auto' && (isNaN(threshold) || threshold < 1 || threshold > 100)) {
      P.toast('Soglia dei sottomenu non valida: da 1 a 100 voci', 'bad'); $('#m-submenu_threshold', r).focus(); return;
    }
    const answerTimeout = parseInt($('#m-answer_timeout', r).value, 10);
    if (isNaN(answerTimeout) || answerTimeout < 0 || answerTimeout > 120) {
      P.toast('Timeout della scelta dell\'installazione non valido: da 0 a 120 secondi', 'bad');
      $('#m-answer_timeout', r).focus(); return;
    }
    const theme = readTheme(); if (!theme) return;
    const settings = {
      title: title || 'PIXIO - Avvio da rete',
      timeout,
      default: $('#m-default', r).value,
      show_local: $('#m-show_local', r).checked,
      show_shell: $('#m-show_shell', r).checked,
      show_reboot: $('#m-show_reboot', r).checked,
      show_memtest: $('#m-show_memtest', r).checked,
      groups,
      submenus,
      submenu_threshold: isNaN(threshold) ? 8 : Math.min(100, Math.max(1, threshold)),
      answer_timeout: answerTimeout,
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
