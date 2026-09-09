/* Pixio – pagina Catalogo ISO: tabella con filtri, abilitazione nel menu, pannello dettagli,
   upload a blocchi riprendibile (drag&drop o pulsante), scansione delle sorgenti. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  // Tipi noti (usati se l'API non fornisce 'types')
  const TYPE_FALLBACK = [
    ['windows', 'Installazione Windows'], ['ubuntu-casper', 'Ubuntu (casper)'], ['debian-live', 'Debian live'],
    ['debian-installer', 'Debian installer'], ['fedora-live', 'Fedora live'], ['redhat-installer', 'Installer Fedora/RHEL'],
    ['archiso', 'Arch live'], ['alpine', 'Alpine'], ['opensuse', 'openSUSE'], ['memdisk', 'Generico (memdisk)'], ['unknown', 'Sconosciuto'],
  ];

  const CAT = { data: null, filters: { q: '', source: '', type: '', menu: '' }, root: null, timer: null, scanJob: null, busy: new Set(), groups: null };

  // ---------------------------------------------------------------- caricamento file (chunked, riprendibile)
  const CHUNK_DEFAULT = 8 * 1024 * 1024;

  class Upload {
    constructor(file) {
      this.file = file; this.name = file.name; this.size = file.size;
      this.sent = 0; this.status = 'init'; this.error = null; this.speed = 0; this.eta = null;
      this.ctrl = null; this.id = null; this.slug = null; this.startedAt = Date.now();
    }
    chunkLen(n, chunk) { return Math.min(chunk, this.size - n * chunk); }
    async run() {
      this.status = 'init'; this.error = null; this.sent = 0; uploads.notify();
      try {
        const init = await P.post('/api/upload/init', { filename: this.name, size: this.size });
        this.id = init.upload_id;
        const chunk = Number(init.chunk_size) > 0 ? Number(init.chunk_size) : CHUNK_DEFAULT;
        const total = Math.max(1, Math.ceil(this.size / chunk));
        const have = new Set((init.received || []).map(Number));
        if (have.size) {
          have.forEach((n) => { if (n < total) this.sent += this.chunkLen(n, chunk); });
          P.toast(`Ripreso il caricamento di ${this.name} dal ${P.pct(this.sent, this.size)}%`, 'info');
        }
        this.status = 'uploading'; uploads.notify();
        for (let n = 0; n < total; n++) {
          if (this.status === 'cancelled') return;
          if (have.has(n)) continue;
          const start = n * chunk;
          const blob = this.file.slice(start, Math.min(this.size, start + chunk));
          let tries = 0;
          for (;;) {
            this.ctrl = new AbortController();
            const t0 = performance.now();
            try {
              await P.api('PUT', `/api/upload/${encodeURIComponent(this.id)}/chunk/${n}`, blob, { signal: this.ctrl.signal });
              const dt = (performance.now() - t0) / 1000;
              if (dt > 0) { const inst = blob.size / dt; this.speed = this.speed ? this.speed * 0.7 + inst * 0.3 : inst; }
              break;
            } catch (e) {
              if (this.status === 'cancelled' || (e && e.name === 'AbortError')) return;
              if (e.status === 401 || e.status === 403 || e.status === 404 || e.status === 413) throw e;
              if (++tries >= 4) throw e;
              this.status = 'retry'; uploads.notify();
              await P.sleep(1500 * tries);
              this.status = 'uploading';
            }
          }
          this.sent += blob.size;
          this.eta = this.speed > 0 ? (this.size - this.sent) / this.speed : null;
          uploads.notify();
        }
        this.status = 'finishing'; uploads.notify();
        const fin = await P.post(`/api/upload/${encodeURIComponent(this.id)}/finish`);
        this.slug = fin && fin.slug; this.sent = this.size;
        if (fin && fin.job_id) {
          // il server ha avviato il rilevamento del tipo in background: attendo il job prima di dichiarare completato
          this.status = 'detecting'; this.jobMessage = null; uploads.notify();
          const job = await P.watchJob(fin.job_id, (j) => { if (j && j.message) { this.jobMessage = j.message; uploads.notify(); } });
          if (job && job.status !== 'done') P.toast(`${this.name}: rilevamento del tipo non riuscito (${job.message || job.status}). Puoi ripeterlo dai dettagli della ISO.`, 'warn', 8000);
        }
        this.status = 'done'; uploads.notify();
        P.toast(`Caricamento di ${this.name} completato`);
        if (uploads.onDone) uploads.onDone(this);
        setTimeout(() => uploads.remove(this), 10000);
      } catch (e) {
        if (this.status === 'cancelled') return;
        this.status = 'error'; this.error = (e && e.message) || String(e); uploads.notify();
        P.fail(e);
      }
    }
    async cancel() {
      const wasDone = this.status === 'done' || this.status === 'detecting';   // file già assemblato sul server
      this.status = 'cancelled';
      if (this.ctrl) { try { this.ctrl.abort(); } catch (e) { /* ignora */ } }
      if (this.id && !wasDone) { try { await P.api('DELETE', `/api/upload/${encodeURIComponent(this.id)}`); } catch (e) { /* ignora */ } }
      uploads.remove(this);
    }
  }

  const uploads = P.uploads = {
    list: [], listeners: new Set(), onDone: null,
    on(fn) { this.listeners.add(fn); }, off(fn) { this.listeners.delete(fn); },
    notify() { this.listeners.forEach((fn) => { try { fn(); } catch (e) { /* ignora */ } }); },
    remove(u) { this.list = this.list.filter((x) => x !== u); this.notify(); },
    add(files, onStart) {
      let started = 0;
      Array.from(files || []).forEach((f) => {
        if (!/\.iso$/i.test(f.name)) { P.toast(`${f.name}: sono accettati solo file .iso`, 'warn'); return; }
        if (this.list.some((u) => u.name === f.name && u.size === f.size && u.status !== 'error' && u.status !== 'done')) { P.toast(`${f.name} è già in caricamento`, 'warn'); return; }
        const u = new Upload(f);
        this.list.push(u); started++;
        u.run();
      });
      if (started && onStart) onStart();
      this.notify();
    },
    pick(onStart) {
      const inp = document.createElement('input');
      inp.type = 'file'; inp.accept = '.iso,application/x-iso9660-image'; inp.multiple = true; inp.hidden = true;
      document.body.appendChild(inp);
      inp.addEventListener('change', () => { this.add(inp.files, onStart); inp.remove(); });
      inp.click();
    },
    active() { return this.list.filter((u) => u.status !== 'done' && u.status !== 'cancelled'); },
  };
  uploads.onDone = () => { if (CAT.root) load(); P.refreshStatus().catch(() => {}); };

  // drag&drop su tutta la pagina (attivo solo nel catalogo e nel wizard)
  const dz = document.getElementById('dropzone');
  let dragDepth = 0;
  const dropAllowed = () => P.state.loggedIn && (P.state.route === 'iso' || P.state.route === 'benvenuto') && document.getElementById('modal').hidden;
  document.addEventListener('dragenter', (e) => {
    if (!dropAllowed() || !e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes('Files')) return;
    e.preventDefault(); dragDepth++; dz.hidden = false;
  });
  document.addEventListener('dragover', (e) => { if (!dz.hidden) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } });
  document.addEventListener('dragleave', () => { if (!dz.hidden && --dragDepth <= 0) { dragDepth = 0; dz.hidden = true; } });
  document.addEventListener('drop', (e) => {
    if (dz.hidden) return;
    e.preventDefault(); dragDepth = 0; dz.hidden = true;
    if (P.state.status && P.state.status.library && P.state.status.library.web_upload_enabled === false) { P.toast('Upload dal browser disattivato: attivalo in Impostazioni → Libreria locale', 'warn'); return; }
    uploads.add(e.dataTransfer.files);
  });

  function uploadsHtml() {
    const items = uploads.list.map((u, i) => {
      const pct = P.pct(u.sent, u.size);
      let meta = '';
      let barCls = '';
      if (u.status === 'init') meta = 'Avvio del caricamento…';
      else if (u.status === 'uploading' || u.status === 'retry') meta = `${pct}% · ${P.fmtBytes(u.sent)} di ${P.fmtBytes(u.size)} · ${u.speed ? P.fmtBytes(u.speed) + '/s' : '—'} · rimanenti ${P.fmtDuration(u.eta)}${u.status === 'retry' ? ' · nuovo tentativo…' : ''}`;
      else if (u.status === 'finishing') meta = 'Assemblaggio del file…';
      else if (u.status === 'detecting') meta = 'Rilevamento del tipo in corso…' + (u.jobMessage ? ' ' + u.jobMessage : '');
      else if (u.status === 'done') { meta = 'Completato'; barCls = 'done'; }
      else if (u.status === 'error') { meta = 'Errore: ' + (u.error || ''); barCls = 'err'; }
      const btns = u.status === 'error'
        ? `<button class="btn small" type="button" data-up="retry" data-i="${i}">Riprova</button><button class="btn small" type="button" data-up="cancel" data-i="${i}">Rimuovi</button>`
        : ((u.status === 'done' || u.status === 'detecting') ? '' : `<button class="btn small" type="button" data-up="cancel" data-i="${i}">Annulla</button>`);
      return `<div class="upload" role="group" aria-label="Caricamento ${esc(u.name)}"><div class="row"><span class="name">${esc(u.name)}</span><span class="actions">${btns}</span></div>
        <div class="bar ${barCls}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></div><div class="meta">${esc(meta)}</div></div>`;
    });
    return items.join('');
  }

  async function renderServerUploads() {
    const box = $('#cat-pending', CAT.root); if (!box) return;
    let list = [];
    try { list = await P.get('/api/upload'); } catch (e) { return; }
    if (!Array.isArray(list)) list = [];
    const activeIds = new Set(uploads.list.map((u) => u.id));
    const pending = list.filter((u) => !activeIds.has(u.upload_id));
    if (!pending.length) { box.innerHTML = ''; return; }
    box.innerHTML = `<div class="panel" style="margin-bottom:14px"><div class="hd"><h3>Caricamenti interrotti</h3><span class="hint">Seleziona di nuovo lo stesso file per riprendere da dove si era fermato</span></div><div class="bd">${pending.map((u) => `
      <div class="row" style="display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap;padding:4px 0">
        <span><span class="iso-name">${esc(u.filename)}</span> <span class="hint">${P.pct(u.received_bytes || 0, u.size || 1)}% · ${P.fmtBytes(u.received_bytes || 0)} di ${P.fmtBytes(u.size)}</span></span>
        <span class="actions"><button class="btn small" type="button" data-pending="resume">Riprendi…</button><button class="btn small danger" type="button" data-pending="delete" data-id="${esc(u.upload_id)}">Elimina</button></span></div>`).join('')}</div></div>`;
  }

  // ---------------------------------------------------------------- dati
  function typeList() {
    const map = new Map();
    if (CAT.data && Array.isArray(CAT.data.types)) CAT.data.types.forEach((t) => { if (t && t.id) map.set(t.id, t.name || t.id); });
    ((CAT.data && CAT.data.isos) || []).forEach((i) => { if (i.type && !map.has(i.type)) map.set(i.type, i.type_name || i.type); });
    TYPE_FALLBACK.forEach(([id, name]) => { if (!map.has(id)) map.set(id, name); });
    return Array.from(map, ([id, name]) => ({ id, name }));
  }
  function typeName(id) {
    const t = typeList().find((x) => x.id === id);
    return t ? t.name : (id || 'Sconosciuto');
  }
  function sourceList() {
    const map = new Map();
    ((CAT.data && CAT.data.isos) || []).forEach((i) => { if (!map.has(i.source)) map.set(i.source, i.source === 'local' ? 'Locale' : (i.source_name || i.source)); });
    const st = P.state.status;
    (st && st.sources || []).forEach((s) => { if (!map.has(s.id)) map.set(s.id, s.name || s.unc || s.id); });
    if (!map.has('local')) map.set('local', 'Locale');
    return Array.from(map, ([id, name]) => ({ id, name }));
  }

  function statusPill(iso) {
    if (iso.missing) return P.pill('File mancante', 'bad');
    const c = iso.cache || {};
    if (c.status === 'copying') return P.pill('Copia in corso ' + (c.progress != null ? Math.floor(c.progress) + '%' : ''), 'warn');
    if (c.status === 'error') return P.pill('Errore copia locale', 'bad');
    const noRecipe = !iso.type || iso.type === 'unknown' || !(iso.platforms && iso.platforms.length);
    if (noRecipe && !iso.custom_recipe) return P.pill('Nessuna ricetta di boot', 'warn');
    if (iso.enabled && iso.mounted) return P.pill('Montata', 'ok');
    if (iso.enabled && !iso.mounted) return P.pill('Nel menu, non montata', 'warn');
    return P.pill('Non nel menu', 'neutral');
  }
  const platBadges = (iso) => {
    const p = (iso.custom_recipe && iso.custom_recipe.platforms && iso.custom_recipe.platforms.length) ? iso.custom_recipe.platforms : (iso.platforms || []);
    return `<span class="plat" aria-label="Piattaforme"><span class="${p.includes('bios') ? 'y' : ''}">BIOS</span><span class="${p.includes('efi') ? 'y' : ''}">UEFI</span></span>`;
  };
  const sourcePill = (iso) => (iso.source === 'local' ? P.pill('Locale', 'acc') : P.pill(iso.source_name || iso.source, 'neutral'));

  function filtered() {
    const f = CAT.filters; const q = f.q.trim().toLowerCase();
    return ((CAT.data && CAT.data.isos) || []).filter((i) => {
      if (f.source && i.source !== f.source) return false;
      if (f.type && (i.type || 'unknown') !== f.type) return false;
      if (f.menu === 'yes' && !i.enabled) return false;
      if (f.menu === 'no' && i.enabled) return false;
      if (q) {
        const hay = [i.name, i.file, i.rel_path, i.type_name, i.type, i.source_name, i.source === 'local' ? 'locale' : '', i.group].join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  async function load() {
    if (!CAT.root) return;
    try {
      const d = await P.get('/api/catalog');
      if (!CAT.root) return;
      CAT.data = d || { isos: [] };
      if (!Array.isArray(CAT.data.isos)) CAT.data.isos = [];
      renderAll();
    } catch (e) {
      if (e.status === 401) return;
      const tb = $('#cat-table', CAT.root);
      if (tb && !CAT.data) tb.innerHTML = `<div class="empty"><h3>Catalogo non disponibile</h3><p>${esc(e.message)}</p></div>`;
      else P.fail(e);
    }
  }

  // ---------------------------------------------------------------- rendering
  function renderAll() {
    renderHeader(); renderFilters(); renderTable();
    const up = $('#cat-uploads', CAT.root); if (up) up.innerHTML = uploadsHtml();
  }
  function renderHeader() {
    if (!CAT.root || !$('#cat-sub', CAT.root)) return;
    const d = CAT.data || {}; const isos = d.isos || [];
    const nSrc = new Set(isos.map((i) => i.source)).size;
    const parts = [`${isos.length} ISO${nSrc ? ` da ${nSrc} ${nSrc === 1 ? 'sorgente' : 'sorgenti'}` : ''}`];
    if (d.last_scan) parts.push('ultima scansione ' + P.fmtDate(d.last_scan));
    if (d.scanning || CAT.scanJob) parts.push('<span class="pill warn">scansione in corso</span>');
    $('#cat-sub', CAT.root).innerHTML = parts.join(' · ');
    const st = P.state.status;
    const upBtn = $('#cat-upload', CAT.root);
    if (upBtn && st && st.library && st.library.web_upload_enabled === false) { upBtn.disabled = true; upBtn.title = 'Upload dal browser disattivato (Impostazioni → Libreria locale)'; }
  }
  function renderFilters() {
    const srcSel = $('#f-source', CAT.root); const typeSel = $('#f-type', CAT.root);
    const isos = (CAT.data && CAT.data.isos) || [];
    const count = (fn) => isos.filter(fn).length;
    srcSel.innerHTML = '<option value="">Tutte le sorgenti</option>' + sourceList().map((s) => `<option value="${esc(s.id)}">${esc(s.name)} (${count((i) => i.source === s.id)})</option>`).join('');
    srcSel.value = CAT.filters.source;
    if (srcSel.value !== CAT.filters.source) { CAT.filters.source = ''; srcSel.value = ''; }
    const present = new Map(); isos.forEach((i) => { const t = i.type || 'unknown'; present.set(t, (present.get(t) || 0) + 1); });
    typeSel.innerHTML = '<option value="">Tutti i tipi</option>' + typeList().filter((t) => present.has(t.id)).map((t) => `<option value="${esc(t.id)}">${esc(t.name)} (${present.get(t.id)})</option>`).join('');
    typeSel.value = CAT.filters.type;
    if (typeSel.value !== CAT.filters.type) { CAT.filters.type = ''; typeSel.value = ''; }
    $('#f-count', CAT.root).innerHTML = `${P.pill('Tutte ' + isos.length, 'acc')} ${P.pill('Nel menu ' + count((i) => i.enabled), 'neutral')} ${P.pill('Montate ' + count((i) => i.mounted), 'neutral')}`;
  }
  function renderTable() {
    const box = $('#cat-table', CAT.root);
    const all = (CAT.data && CAT.data.isos) || [];
    if (!all.length) {
      const st = P.state.status || {}; const lib = st.library || {};
      box.innerHTML = `<div class="empty"><h3>Il catalogo è vuoto</h3>
        <p>Nessuna ISO trovata. Aggiungi una share Windows nelle <a href="#/impostazioni">Impostazioni</a>, copia una ISO in <span class="mono">${esc(lib.samba_path || '\\\\' + (st.server_ip || 'pixio') + '\\iso')}</span> oppure caricala da qui trascinandola nella pagina.</p>
        <div class="actions"><button class="btn" type="button" data-act="scan">Riscansiona</button><button class="btn primary" type="button" data-act="upload">Carica ISO</button></div></div>`;
      return;
    }
    const rows = filtered();
    if (!rows.length) { box.innerHTML = '<div class="empty"><h3>Nessuna ISO corrisponde ai filtri</h3><p>Prova a cambiare il testo cercato o a togliere un filtro.</p><button class="btn" type="button" data-act="clear-filters">Azzera i filtri</button></div>'; return; }
    box.innerHTML = `<div class="tbl-wrap"><table><thead><tr><th>Nel menu</th><th>Nome</th><th>Sorgente</th><th>Tipo rilevato</th><th>Dimensione</th><th>BIOS / UEFI</th><th>Stato</th><th><span class="sr-only">Azioni</span></th></tr></thead><tbody>${rows.map((iso) => {
      const busy = CAT.busy.has(iso.slug);
      const noRecipe = (!iso.type || iso.type === 'unknown') && !iso.custom_recipe;
      return `<tr data-slug="${esc(iso.slug)}" class="${iso.missing ? 'missing' : ''}">
        <td>${P.switchHtml(!!iso.enabled, `data-act="toggle" aria-label="Nel menu: ${esc(iso.name)}" ${busy ? 'disabled' : ''} ${iso.missing ? 'disabled title="File mancante"' : ''}`)}</td>
        <td><div class="iso-name">${esc(iso.name || iso.file)}</div><div class="iso-file mono">${esc(iso.rel_path || iso.file || '')}</div></td>
        <td>${sourcePill(iso)}</td><td>${esc(iso.type_name || typeName(iso.type))}${iso.group ? `<div class="hint">${esc(iso.group)}</div>` : ''}</td>
        <td class="num">${P.fmtBytes(iso.size)}</td><td>${platBadges(iso)}</td><td>${statusPill(iso)}${iso.answer_id ? ' ' + P.pill('automatica: ' + (iso.answer_name || iso.answer_id), 'acc') : ''}</td>
        <td class="actions-cell"><button class="btn small" type="button" data-act="details">${noRecipe ? 'Ricetta manuale' : 'Dettagli'}</button></td></tr>`;
    }).join('')}</tbody></table></div>`;
  }

  // ---------------------------------------------------------------- azioni sulla tabella
  async function toggleEnabled(slug, sw) {
    const iso = (CAT.data.isos || []).find((i) => i.slug === slug); if (!iso) return;
    const want = !iso.enabled;
    CAT.busy.add(slug); sw.disabled = true; sw.classList.add('busy');
    try {
      const upd = await P.api('PATCH', `/api/catalog/${encodeURIComponent(slug)}`, { enabled: want });
      Object.assign(iso, upd || { enabled: want });
      P.toast(want ? `"${iso.name}" aggiunta al menu` : `"${iso.name}" tolta dal menu`);
      P.refreshStatus().catch(() => {});
    } catch (e) { P.fail(e); }
    CAT.busy.delete(slug);
    renderAll();
  }

  async function scan() {
    const btn = $('#cat-scan', CAT.root);
    P.setBusy(btn, true, 'Scansione…');
    try {
      const r = await P.post('/api/catalog/scan');
      CAT.scanJob = r.job_id || 'job';
      renderHeader();
      const job = await P.watchJob(r.job_id, (j) => { const s = $('#cat-sub', CAT.root); if (s && j && j.message) s.innerHTML = 'Scansione: ' + esc(j.message) + (j.progress != null ? ` (${Math.floor(j.progress)}%)` : ''); });
      CAT.scanJob = null;
      if (job) P.toast(job.status === 'done' ? 'Scansione completata' + (job.message ? ': ' + job.message : '') : 'Scansione: ' + (job.message || job.status), job.status === 'done' ? 'ok' : 'bad');
      await load();
      P.refreshStatus().catch(() => {});
    } catch (e) { CAT.scanJob = null; P.fail(e); }
    P.setBusy(btn, false);
  }

  // ---------------------------------------------------------------- pannello dettagli
  async function loadGroups() {
    if (CAT.groups) return CAT.groups;
    let groups = [];
    try { const m = await P.get('/api/menu'); groups = (m && m.settings && m.settings.groups) || []; } catch (e) { /* uso i gruppi del catalogo */ }
    ((CAT.data && CAT.data.isos) || []).forEach((i) => { if (i.group && !groups.includes(i.group)) groups.push(i.group); });
    CAT.groups = groups;
    return groups;
  }

  async function openDetails(slug) {
    P.drawer.open('Dettagli ISO', '<div class="loading">Caricamento…</div>');
    try {
      const [iso, groups] = await Promise.all([P.get('/api/catalog/' + encodeURIComponent(slug)), loadGroups(), loadAnswers()]);
      if (!P.drawer.isOpen()) return;
      renderDrawer(iso, groups);
    } catch (e) { P.fail(e); P.drawer.close(); }
  }

  /* Risposte automatiche (autounattend, preseed, cloud-init, kickstart) da collegare alla ISO. */
  const ANSWER_KINDS = {
    windows: ['windows'], 'winpe-tool': ['windows'],
    'debian-installer': ['debian'], 'debian-live': ['debian'],
    'ubuntu-casper': ['ubuntu'], 'redhat-installer': ['redhat'], 'fedora-live': ['redhat'],
  };

  async function loadAnswers() {
    if (CAT.answers) return CAT.answers;
    try {
      const r = await P.api('GET', '/api/answers');
      CAT.answers = (r && r.answers) || [];
    } catch (e) { CAT.answers = []; }
    return CAT.answers;
  }

  function answersFor(iso) {
    const kinds = ANSWER_KINDS[iso.type] || [];
    const all = CAT.answers || [];
    const buone = all.filter((a) => kinds.includes(a.kind));
    const altre = all.filter((a) => !kinds.includes(a.kind));
    return { buone, altre };
  }

  function answerFieldHtml(iso) {
    const { buone, altre } = answersFor(iso);
    if (!(CAT.answers || []).length) {
      return `<div class="field"><span class="field-label">Installazione automatica</span>
        <div class="hint">Nessuna risposta disponibile. Creane una nella pagina Risposte, oppure da Preset → Windows o Debian con "Salva come risposta".</div></div>`;
    }
    const opt = (a) => `<option value="${esc(a.id)}" ${a.id === iso.answer_id ? 'selected' : ''}>${esc(a.name)} (${esc(a.kind)})</option>`;
    return `<div class="field"><label for="d-answer">Installazione automatica</label>
      <select id="d-answer">
        <option value="">Nessuna: installazione guidata a mano</option>
        ${buone.length ? `<optgroup label="Adatte a questa immagine">${buone.map(opt).join('')}</optgroup>` : ''}
        ${altre.length ? `<optgroup label="Altre risposte">${altre.map(opt).join('')}</optgroup>` : ''}
      </select>
      <div class="hint">La risposta viene servita al PC durante l'installazione: per Windows finisce nel WinPE come autounattend.xml, per Linux diventa il file di preconfigurazione sulla riga di comando del kernel.</div></div>`;
  }

  function renderDrawer(iso, groups) {
    const cr = iso.custom_recipe || null;
    const cache = iso.cache || {};
    const det = iso.detect || {};
    const types = typeList();
    if (iso.type && !types.some((t) => t.id === iso.type)) types.push({ id: iso.type, name: iso.type_name || iso.type });
    const plats = (cr && cr.platforms) || ['bios', 'efi'];
    let cacheText = 'Nessuna copia locale: i file vengono letti dalla sorgente durante il boot.';
    if (iso.source === 'local') cacheText = 'La ISO è già nella libreria locale del server.';
    else if (cache.status === 'copying') cacheText = `Copia in corso… ${cache.progress != null ? Math.floor(cache.progress) + '%' : ''}`;
    else if (cache.status === 'ready') cacheText = 'Copia locale pronta: il boot non dipende dalla share.';
    else if (cache.status === 'error') cacheText = 'Errore durante la copia locale. Riprova disattivando e riattivando.';
    else if (cache.wanted) cacheText = 'Copia locale richiesta, in attesa.';
    const preview = iso.recipe_preview || {};
    const noRecipe = (!iso.type || iso.type === 'unknown') && !cr;

    P.drawer.setTitle(iso.name || iso.file || iso.slug);
    P.drawer.body.innerHTML = `
      <div class="drawer-sec">
        <div class="iso-file mono">${esc(iso.path || iso.rel_path || iso.file || '')}</div>
        <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">${sourcePill(iso)} ${statusPill(iso)} ${platBadges(iso)} <span class="hint">${P.fmtBytes(iso.size)} · ${esc(P.fmtDate(iso.mtime))} · <span class="mono">${esc(iso.slug)}</span></span></div>
        ${(iso.warnings || []).length ? `<div class="alert warn"><ul style="margin:0">${iso.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}
        ${iso.missing ? '<div class="alert bad">Il file non è più presente nella sorgente. Verrà tolto dal catalogo se manca anche alle prossime scansioni.</div>' : ''}
        ${noRecipe ? '<div class="alert warn">Tipo non riconosciuto: scegli un tipo, oppure definisci una ricetta personalizzata. In alternativa "Generico (memdisk)" avvia la ISO in RAM (solo BIOS, ISO piccole).</div>' : ''}
      </div>
      <div class="drawer-sec">
        <div class="field"><label for="d-name">Nome nel menu</label><input id="d-name" value="${esc(iso.name || '')}" maxlength="80"></div>
        <div class="row2">
          <div class="field"><label for="d-group">Gruppo</label><select id="d-group">${groups.map((g) => `<option value="${esc(g)}" ${g === iso.group ? 'selected' : ''}>${esc(g)}</option>`).join('')}${iso.group && !groups.includes(iso.group) ? `<option value="${esc(iso.group)}" selected>${esc(iso.group)}</option>` : ''}<option value="__new">Nuovo gruppo…</option></select><input id="d-group-new" placeholder="Nome del nuovo gruppo" hidden aria-label="Nome del nuovo gruppo"></div>
          <div class="field"><label for="d-type">Tipo (ricetta)</label><select id="d-type">${types.map((t) => `<option value="${esc(t.id)}" ${t.id === (iso.type || 'unknown') ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select></div>
        </div>
        ${answerFieldHtml(iso)}
        <dl class="kv">
          <dt>Rilevato</dt><dd>${esc(det.label || iso.type_name || '—')}${det.version ? ' · versione ' + esc(det.version) : ''}</dd>
          ${(det.files || []).length ? `<dt>File chiave</dt><dd class="mono" style="font-size:12px">${det.files.map(esc).join('<br>')}</dd>` : ''}
          <dt>Visto</dt><dd>prima volta ${esc(P.fmtDate(iso.first_seen))} · ultima ${esc(P.fmtDate(iso.last_seen))}</dd>
        </dl>
      </div>
      <div class="drawer-sec">
        <div class="toggle-row" style="border-top:0;padding-top:0"><div><div class="tt">Nel menu di boot</div><div class="td">Monta la ISO in loop e la mostra nel menu iPXE.</div></div>${P.switchHtml(!!iso.enabled, `data-d="enabled" aria-label="Nel menu di boot" ${iso.missing ? 'disabled' : ''}`)}</div>
        <div class="toggle-row"><div><div class="tt">Copia locale</div><div class="td" id="d-cache-text">${esc(cacheText)}</div>${cache.status === 'copying' ? `<div class="bar" style="margin-top:6px"><i style="width:${Math.floor(cache.progress || 0)}%"></i></div>` : ''}</div>${P.switchHtml(!!cache.wanted, `data-d="cache" aria-label="Copia locale" ${iso.source === 'local' ? 'disabled' : ''}`)}</div>
      </div>
      <div class="drawer-sec">
        <details class="custom" ${cr ? 'open' : ''}><summary>Ricetta personalizzata ${cr ? P.pill('attiva', 'acc') : ''}</summary><div class="inner">
          <div class="field check"><input type="checkbox" id="d-cr-on" ${cr ? 'checked' : ''}><label for="d-cr-on">Usa una ricetta personalizzata al posto di quella del tipo</label></div>
          <div class="field"><label for="d-cr-kernel">Kernel (percorso relativo alla ISO o URL)</label><input id="d-cr-kernel" class="mono" value="${esc(cr ? cr.kernel : '')}" placeholder="casper/vmlinuz"></div>
          <div class="field"><label for="d-cr-initrd">Initrd (uno per riga)</label><textarea id="d-cr-initrd" class="mono" placeholder="casper/initrd">${esc(cr ? (cr.initrds || []).join('\n') : '')}</textarea></div>
          <div class="field"><label for="d-cr-cmdline">Riga di comando del kernel</label><textarea id="d-cr-cmdline" class="mono" placeholder="boot=casper ip=dhcp">${esc(cr ? cr.cmdline : '')}</textarea><div class="hint">I percorsi relativi si riferiscono al contenuto della ISO montata. Dopo il salvataggio l'anteprima qui sotto mostra lo script risultante.</div></div>
          <div class="field"><span class="field-label">Piattaforme</span><div class="checks"><label><input type="checkbox" id="d-cr-bios" ${plats.includes('bios') ? 'checked' : ''}> BIOS</label><label><input type="checkbox" id="d-cr-efi" ${plats.includes('efi') ? 'checked' : ''}> UEFI</label></div></div>
        </div></details>
      </div>
      <div class="drawer-sec">
        <div class="field"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><span class="field-label">Anteprima script iPXE</span><div class="seg" role="group" aria-label="Piattaforma"><button type="button" data-plat="efi" aria-pressed="true">UEFI</button><button type="button" data-plat="bios" aria-pressed="false">BIOS</button></div></div>
        <pre class="code mono" id="d-preview" style="max-height:260px">${esc(preview.efi || '(nessuna anteprima)')}</pre></div>
      </div>
      <div class="drawer-sec actions" style="justify-content:space-between">
        <span class="actions"><button class="btn primary" type="button" data-d="save">Salva</button><button class="btn" type="button" data-d="redetect">Rileva di nuovo</button></span>
        ${iso.source === 'local' ? '<button class="btn danger" type="button" data-d="delete">Elimina file</button>' : ''}
      </div>`;

    const body = P.drawer.body;
    $('#d-group', body).addEventListener('change', (e) => { const n = $('#d-group-new', body); n.hidden = e.target.value !== '__new'; if (!n.hidden) n.focus(); });
    $$('[data-plat]', body).forEach((b) => b.addEventListener('click', () => {
      $$('[data-plat]', body).forEach((x) => x.setAttribute('aria-pressed', x === b ? 'true' : 'false'));
      $('#d-preview', body).textContent = preview[b.dataset.plat] || '(nessuna anteprima)';
    }));
    body.onclick = async (e) => {
      const b = e.target.closest('[data-d]'); if (!b) return;
      const act = b.dataset.d; const slug = iso.slug;
      if (act === 'enabled' || act === 'cache') {
        const want = !P.switchOn(b);
        b.disabled = true; b.classList.add('busy');
        try {
          const upd = await P.api('PATCH', `/api/catalog/${encodeURIComponent(slug)}`, act === 'enabled' ? { enabled: want } : { cache_wanted: want });
          P.toast(act === 'enabled' ? (want ? 'Aggiunta al menu' : 'Tolta dal menu') : (want ? 'Copia locale avviata' : 'Copia locale disattivata'));
          await load(); P.refreshStatus().catch(() => {});
          openDetails(slug);
        } catch (err) { P.fail(err); b.disabled = false; b.classList.remove('busy'); }
      } else if (act === 'save') {
        const patch = {};
        const name = $('#d-name', body).value.trim(); if (name && name !== iso.name) patch.name = name;
        let group = $('#d-group', body).value; if (group === '__new') group = $('#d-group-new', body).value.trim();
        if (group && group !== iso.group) patch.group = group;
        const type = $('#d-type', body).value; if (type && type !== iso.type) patch.type = type;
        const ansEl = $('#d-answer', body);
        if (ansEl) {
          const ans = ansEl.value || null;
          if ((ans || null) !== (iso.answer_id || null)) patch.answer_id = ans;
        }
        if ($('#d-cr-on', body).checked) {
          const kernel = $('#d-cr-kernel', body).value.trim();
          if (!kernel) { P.toast('Indica il kernel della ricetta personalizzata', 'bad'); $('#d-cr-kernel', body).focus(); return; }
          const pl = []; if ($('#d-cr-bios', body).checked) pl.push('bios'); if ($('#d-cr-efi', body).checked) pl.push('efi');
          if (!pl.length) { P.toast('Seleziona almeno una piattaforma', 'bad'); return; }
          patch.custom_recipe = { kernel, initrds: $('#d-cr-initrd', body).value.split('\n').map((s) => s.trim()).filter(Boolean), cmdline: $('#d-cr-cmdline', body).value.trim(), platforms: pl };
        } else if (cr) patch.custom_recipe = null;
        if (!Object.keys(patch).length) { P.toast('Nessuna modifica da salvare', 'info'); return; }
        P.setBusy(b, true, 'Salvataggio…');
        try {
          await P.api('PATCH', `/api/catalog/${encodeURIComponent(slug)}`, patch);
          P.toast('Modifiche salvate');
          if (patch.group && CAT.groups && !CAT.groups.includes(patch.group)) CAT.groups.push(patch.group);
          await load(); openDetails(slug);
        } catch (err) { P.fail(err); P.setBusy(b, false); }
      } else if (act === 'redetect') {
        P.setBusy(b, true, 'Rilevamento…');
        try {
          const r = await P.post(`/api/catalog/${encodeURIComponent(slug)}/redetect`);
          if (r && r.job_id) await P.watchJob(r.job_id);
          P.toast('Rilevamento eseguito');
          await load(); openDetails(slug);
        } catch (err) { P.fail(err); P.setBusy(b, false); }
      } else if (act === 'delete') {
        const ok = await P.confirm(`Eliminare definitivamente il file "${iso.file || iso.name}" dalla libreria locale?`, { title: 'Elimina ISO', ok: 'Elimina', danger: true, detail: 'La ISO verrà smontata, tolta dal menu e cancellata dal disco del server.' });
        if (!ok) return;
        P.setBusy(b, true, 'Eliminazione…');
        try {
          await P.api('DELETE', `/api/catalog/${encodeURIComponent(slug)}`);
          P.toast('ISO eliminata');
          P.drawer.close(); await load(); P.refreshStatus().catch(() => {});
        } catch (err) { P.fail(err); P.setBusy(b, false); }
      }
    };
  }

  // ---------------------------------------------------------------- pagina
  P.pages.iso = {
    title: 'Catalogo ISO',
    mount(root) {
      CAT.root = root; CAT.groups = null;
      root.innerHTML = `
        <div class="ph"><div><h2>Catalogo ISO</h2><div class="sub" id="cat-sub">Caricamento…</div></div>
          <div class="actions"><button class="btn" type="button" id="cat-scan">Riscansiona</button><button class="btn primary" type="button" id="cat-upload">Carica ISO</button></div></div>
        <div class="uploads" id="cat-uploads">${uploadsHtml()}</div>
        <div id="cat-pending"></div>
        <div class="filters">
          <input class="search" id="f-q" type="search" placeholder="Cerca per nome, file, tipo o sorgente…" aria-label="Cerca ISO" value="${esc(CAT.filters.q)}">
          <select id="f-source" aria-label="Filtra per sorgente"></select>
          <select id="f-type" aria-label="Filtra per tipo"></select>
          <select id="f-menu" aria-label="Filtra per presenza nel menu"><option value="">Nel menu e non</option><option value="yes">Solo nel menu</option><option value="no">Solo fuori dal menu</option></select>
          <span id="f-count"></span>
        </div>
        <div id="cat-table"><div class="loading">Caricamento del catalogo…</div></div>`;
      $('#f-menu', root).value = CAT.filters.menu;
      $('#cat-scan', root).addEventListener('click', scan);
      $('#cat-upload', root).addEventListener('click', () => uploads.pick());
      $('#f-q', root).addEventListener('input', (e) => { CAT.filters.q = e.target.value; renderTable(); });
      ['source', 'type', 'menu'].forEach((k) => $('#f-' + k, root).addEventListener('change', (e) => { CAT.filters[k] = e.target.value; renderTable(); }));
      root.addEventListener('click', (e) => {
        const b = e.target.closest('[data-act],[data-up],[data-pending]'); if (!b) return;
        if (b.dataset.up) {
          const u = uploads.list[Number(b.dataset.i)]; if (!u) return;
          if (b.dataset.up === 'cancel') u.cancel(); else if (b.dataset.up === 'retry') u.run();
          return;
        }
        if (b.dataset.pending) {
          if (b.dataset.pending === 'resume') uploads.pick();
          else if (b.dataset.pending === 'delete') {
            P.confirm('Eliminare il caricamento interrotto? I blocchi già ricevuti verranno scartati.', { ok: 'Elimina', danger: true }).then(async (ok) => {
              if (!ok) return;
              try { await P.api('DELETE', `/api/upload/${encodeURIComponent(b.dataset.id)}`); P.toast('Caricamento eliminato'); renderServerUploads(); } catch (err) { P.fail(err); }
            });
          }
          return;
        }
        const act = b.dataset.act;
        const tr = b.closest('tr[data-slug]'); const slug = tr && tr.dataset.slug;
        if (act === 'toggle' && slug) toggleEnabled(slug, b);
        else if (act === 'details' && slug) openDetails(slug);
        else if (act === 'scan') scan();
        else if (act === 'upload') uploads.pick();
        else if (act === 'clear-filters') { CAT.filters = { q: '', source: '', type: '', menu: '' }; $('#f-q', root).value = ''; renderFilters(); $('#f-menu', root).value = ''; renderTable(); }
      });
      this._onUp = () => { const up = $('#cat-uploads', root); if (up) up.innerHTML = uploadsHtml(); };
      uploads.on(this._onUp);
      this._onStatus = () => renderHeader();
      document.addEventListener('pixio-status', this._onStatus);
      load().then(renderServerUploads);
      // aggiornamento periodico (copie in corso, scansioni automatiche, nuove ISO dalla share)
      CAT.timer = setInterval(() => { if (!document.hidden && !P.drawer.isOpen()) load(); }, 5000);
    },
    unmount() {
      clearInterval(CAT.timer); CAT.timer = null; CAT.root = null;
      uploads.off(this._onUp);
      document.removeEventListener('pixio-status', this._onStatus);
    },
  };
})();
