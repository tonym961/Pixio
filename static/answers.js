/* Pixio – pagina Risposte: file di risposta per le installazioni non presidiate (autounattend.xml,
   preseed.cfg, user-data, kickstart). Elenco delle risposte, creazione da modello, editor testuale
   monospace con salvataggio, elenco file con eliminazione e upload a blocchi (stessa logica di drivers.js).
   Le classi grafiche .drv-* sono riusate: stesso aspetto delle schede della pagina Driver. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  const A = {
    root: null, list: null, kinds: [], detail: null, file: null, dirty: false,
    timer: null, id: null, tpl: {},
  };
  const CHUNK_DEFAULT = 8 * 1024 * 1024;
  const EXT = ['xml', 'cfg', 'ks', 'yaml', 'yml', 'txt', 'cmd', 'bat', 'ps1', 'reg', 'sh', 'conf', 'seed', 'json', 'ini'];
  const NOEXT = ['user-data', 'meta-data', 'vendor-data', 'network-config'];
  const EXT_RE = new RegExp('\\.(' + EXT.join('|') + ')$', 'i');
  const okName = (n) => EXT_RE.test(n) || NOEXT.indexOf(String(n).toLowerCase()) >= 0;
  const answerUrl = (id) => '/api/answers/' + encodeURIComponent(id);
  const kindOf = (id) => A.kinds.find((k) => k.id === id) || { id: id, name: id, hint: '', main_file: '' };
  const serverIp = () => (P.state.status && P.state.status.server_ip) || 'pixio';
  const webUploadOff = () => !!(P.state.status && P.state.status.library && P.state.status.library.web_upload_enabled === false);
  const KIND_CLS = { windows: 'acc', debian: 'ok', ubuntu: 'ok', redhat: 'warn', generic: 'neutral' };

  // ---------------------------------------------------------------- upload (chunked, riprendibile, in sequenza)
  let uploadSeq = 0;
  class AnswerUpload {
    constructor(answerId, file) {
      this.uid = ++uploadSeq; this.folder = answerId; this.file = file; this.name = file.name; this.size = file.size;
      this.sent = 0; this.status = 'queued'; this.error = null; this.speed = 0; this.eta = null;
      this.ctrl = null; this.id = null; this.result = null;
    }
    chunkLen(n, chunk) { return Math.min(chunk, this.size - n * chunk); }
    async run() {
      this.status = 'init'; this.error = null; this.sent = 0; queue.notify();
      try {
        const init = await P.post('/api/upload/init', { filename: this.name, size: this.size, kind: 'answer', folder: this.folder });
        this.id = init.upload_id;
        const chunk = Number(init.chunk_size) > 0 ? Number(init.chunk_size) : CHUNK_DEFAULT;
        const total = Math.max(1, Math.ceil(this.size / chunk));
        const have = new Set((init.received || []).map(Number));
        if (have.size) {
          have.forEach((n) => { if (n < total) this.sent += this.chunkLen(n, chunk); });
          P.toast(`Ripreso il caricamento di ${this.name} dal ${P.pct(this.sent, this.size)}%`, 'info');
        }
        this.status = 'uploading'; queue.notify();
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
              this.status = 'retry'; queue.notify();
              await P.sleep(1500 * tries);
              this.status = 'uploading';
            }
          }
          this.sent += blob.size;
          this.eta = this.speed > 0 ? (this.size - this.sent) / this.speed : null;
          queue.notify();
        }
        this.status = 'finishing'; queue.notify();
        this.result = await P.post(`/api/upload/${encodeURIComponent(this.id)}/finish`);
        this.status = 'done'; this.sent = this.size; queue.notify();
        P.toast(`${this.name} caricato nella risposta`);
        if (A.id === this.folder) loadDetail(this.folder, true); else load();
        setTimeout(() => queue.remove(this), 10000);
      } catch (e) {
        if (this.status === 'cancelled') return;
        this.status = 'error'; this.error = (e && e.message) || String(e); queue.notify();
        P.fail(e);
      }
    }
    async cancel() {
      const wasDone = this.status === 'done';
      this.status = 'cancelled';
      if (this.ctrl) { try { this.ctrl.abort(); } catch (e) { /* ignora */ } }
      if (this.id && !wasDone) { try { await P.api('DELETE', `/api/upload/${encodeURIComponent(this.id)}`); } catch (e) { /* ignora */ } }
      queue.remove(this);
    }
  }

  // Coda globale: un file alla volta, continua anche se si cambia pagina
  const queue = {
    list: [], running: false,
    notify() { renderUploads(); },
    remove(u) { this.list = this.list.filter((x) => x !== u); this.notify(); },
    byUid(uid) { return this.list.find((u) => u.uid === Number(uid)); },
    add(answerId, files) {
      if (webUploadOff()) { P.toast('Upload dal browser disattivato: attivalo in Impostazioni → Libreria locale', 'warn'); return; }
      let added = 0;
      Array.from(files || []).forEach((f) => {
        if (!okName(f.name)) { P.toast(`${f.name}: tipo di file non ammesso (${EXT.join(', ')} oppure ${NOEXT.join(', ')})`, 'warn', 7000); return; }
        if (!f.size) { P.toast(`${f.name}: file vuoto`, 'warn'); return; }
        if (f.size > 8 * 1024 * 1024) { P.toast(`${f.name}: troppo grande per una risposta (max 8 MB)`, 'warn'); return; }
        if (this.list.some((u) => u.folder === answerId && u.name === f.name && u.status !== 'error' && u.status !== 'done')) { P.toast(`${f.name} è già in coda`, 'warn'); return; }
        this.list.push(new AnswerUpload(answerId, f)); added++;
      });
      if (added) { this.notify(); this.pump(); }
    },
    retry(u) { if (u.status === 'error') { u.status = 'queued'; this.notify(); this.pump(); } },
    async pump() {
      if (this.running) return;
      this.running = true;
      try {
        for (;;) {
          const u = this.list.find((x) => x.status === 'queued');
          if (!u) break;
          await u.run();
        }
      } finally { this.running = false; }
    },
    pick(answerId) {
      if (webUploadOff()) { P.toast('Upload dal browser disattivato: attivalo in Impostazioni → Libreria locale', 'warn'); return; }
      const inp = document.createElement('input');
      inp.type = 'file'; inp.multiple = true; inp.hidden = true; inp.accept = EXT.map((e) => '.' + e).join(',');
      document.body.appendChild(inp);
      inp.addEventListener('change', () => { this.add(answerId, inp.files); inp.remove(); });
      inp.click();
    },
  };

  function uploadHtml(u) {
    const pct = P.pct(u.sent, u.size);
    let meta = ''; let barCls = '';
    if (u.status === 'queued') meta = 'In coda…';
    else if (u.status === 'init') meta = 'Avvio del caricamento…';
    else if (u.status === 'uploading' || u.status === 'retry') meta = `${pct}% · ${P.fmtBytes(u.sent)} di ${P.fmtBytes(u.size)}${u.status === 'retry' ? ' · nuovo tentativo…' : ''}`;
    else if (u.status === 'finishing') meta = 'Salvataggio del file…';
    else if (u.status === 'done') { meta = 'Completato'; barCls = 'done'; }
    else if (u.status === 'error') { meta = 'Errore: ' + (u.error || ''); barCls = 'err'; }
    const btns = u.status === 'error'
      ? `<button class="btn small" type="button" data-up="retry" data-uid="${u.uid}">Riprova</button><button class="btn small" type="button" data-up="cancel" data-uid="${u.uid}">Rimuovi</button>`
      : (u.status === 'done' ? '' : `<button class="btn small" type="button" data-up="cancel" data-uid="${u.uid}">Annulla</button>`);
    return `<div class="upload" role="group" aria-label="Caricamento ${esc(u.name)}"><div class="row"><span class="name">${esc(u.name)} <span class="hint">${P.fmtBytes(u.size)}</span></span><span class="actions">${btns}</span></div>
      <div class="bar ${barCls}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></div><div class="meta">${esc(meta)}</div></div>`;
  }
  function renderUploads() {
    if (!A.root) return;
    $$('[data-uploads]', A.root).forEach((box) => {
      const items = queue.list.filter((u) => u.folder === box.dataset.uploads);
      box.innerHTML = items.map(uploadHtml).join('');
      box.hidden = !items.length;
    });
  }

  // ---------------------------------------------------------------- dati
  async function load() {
    if (!A.root || A.id) return;
    try {
      const d = await P.get('/api/answers');
      if (!A.root || A.id) return;
      A.list = (d && d.answers) || [];
      A.kinds = (d && d.kinds) || A.kinds;
      renderList();
    } catch (e) {
      if (e.status === 401) return;
      const box = $('#ans-list', A.root);
      if (box && !A.list) box.innerHTML = `<div class="empty"><h3>Risposte non disponibili</h3><p>${esc(e.message)}</p></div>`;
      else P.fail(e);
    }
  }

  async function loadKinds() {
    if (A.kinds.length) return A.kinds;
    const d = await P.get('/api/answers');
    A.kinds = (d && d.kinds) || [];
    A.list = (d && d.answers) || A.list;
    return A.kinds;
  }

  async function templateFor(kind) {
    if (A.tpl[kind]) return A.tpl[kind];
    A.tpl[kind] = await P.get('/api/answers/templates/' + encodeURIComponent(kind));
    return A.tpl[kind];
  }

  async function loadDetail(id, keepEditor) {
    try {
      const q = A.file && keepEditor ? '?file=' + encodeURIComponent(A.file) : '';
      const d = await P.get(answerUrl(id) + q);
      if (!A.root || A.id !== id) return;
      A.detail = d;
      if (!keepEditor || !A.file) A.file = d.file || d.main_file || '';
      renderDetail();
    } catch (e) {
      if (e.status === 401) return;
      if (e.status === 404) { P.toast('Risposta non trovata', 'warn'); location.hash = '#/risposte'; return; }
      P.fail(e);
    }
  }

  // ---------------------------------------------------------------- elenco
  function answerHtml(a) {
    const k = kindOf(a.kind);
    const used = (a.used_by || []);
    return `<div class="drv-card" data-answer="${esc(a.id)}">
      <div class="drv-head">
        <div>
          <div class="drv-name"><span>${esc(a.name)}</span>${P.pill(k.name, KIND_CLS[a.kind] || 'neutral')}</div>
          <div class="drv-meta">
            <span class="mono">${esc(a.main_file || '—')}</span>
            <span>${a.files.length} file · ${P.fmtBytes(a.size || 0)}</span>
            ${used.length ? P.pill(used.length === 1 ? '1 ISO associata' : used.length + ' ISO associate', 'ok') : P.pill('nessuna ISO associata', 'neutral')}
          </div>
          ${a.note ? `<div class="drv-meta">${esc(a.note)}</div>` : ''}
          ${used.length ? `<div class="drv-meta">Usata da: ${used.map((s) => `<span class="mono">${esc(s)}</span>`).join(', ')}</div>` : ''}
        </div>
        <div class="actions">
          <button class="btn small primary" type="button" data-act="open">Apri e modifica</button>
          <button class="btn small danger" type="button" data-act="delete">Elimina</button>
        </div>
      </div>
      <div class="uploads drv-uploads" data-uploads="${esc(a.id)}" hidden></div>
    </div>`;
  }

  function renderList() {
    if (!A.root || A.id) return;
    const list = A.list || [];
    const n = list.length;
    const used = list.filter((a) => (a.used_by || []).length).length;
    $('#ans-sub', A.root).textContent = n
      ? `${n} ${n === 1 ? 'risposta' : 'risposte'} · ${used} ${used === 1 ? 'associata' : 'associate'} a una ISO`
      : 'Nessuna risposta';
    const box = $('#ans-list', A.root);
    if (!n) {
      box.innerHTML = `<div class="empty"><h3>Nessuna risposta automatica</h3>
        <p>Una risposta è un file che l'installatore legge da solo: niente domande su lingua, disco, utente e password.
        Crea la prima scegliendo il sistema: Pixio prepara un modello commentato da adattare.</p>
        <button class="btn primary" type="button" data-act="new">Nuova risposta</button></div>`;
    } else {
      box.innerHTML = `<div class="drv-list">${list.map(answerHtml).join('')}</div>`;
    }
    renderUploads();
  }

  // ---------------------------------------------------------------- dettaglio ed editor
  function fileTabs(d) {
    const files = d.files || [];
    if (!files.length) return '<span class="hint">Nessun file: scrivi il contenuto qui sotto e salva, oppure caricane uno.</span>';
    return `<div class="seg" role="group" aria-label="File della risposta">${files.map((f) => `<button type="button" data-file="${esc(f.name)}" aria-pressed="${f.name === A.file ? 'true' : 'false'}">${esc(f.name)}${f.name === d.main_file ? ' ★' : ''}</button>`).join('')}</div>`;
  }

  function renderDetail() {
    const d = A.detail;
    if (!A.root || !d) return;
    const k = kindOf(d.kind);
    const used = d.used_by || [];
    const args = d.kernel_args || '';
    A.root.innerHTML = `
      <div class="ph">
        <div>
          <div class="eyebrow"><a href="#/risposte" class="linklike">← Risposte</a></div>
          <h2>${esc(d.name)}</h2>
          <div class="sub">${esc(k.name)} · <span class="mono">${esc(d.id)}</span>${d.note ? ' · ' + esc(d.note) : ''}</div>
        </div>
        <div class="actions">
          <button class="btn" type="button" id="ans-rename">Rinomina</button>
          <button class="btn danger" type="button" id="ans-del">Elimina risposta</button>
        </div>
      </div>

      <div class="card drv-intro">
        <p>${esc(k.hint)}</p>
        <div class="url-box"><div class="k">Indirizzo pubblico (lo scarica il client durante l'installazione)</div>
          <div class="copy-row"><span class="v" id="ans-url">${esc(d.url || d.folder_url || '')}</span><button class="btn small" type="button" id="ans-copy">Copia</button></div>
          <div class="d">${args ? 'Aggiunto alla riga di avvio: <span class="mono">' + esc(args) + '</span>'
            : (d.kind === 'windows' ? 'Il file viene iniettato nel WinPE come <span class="mono">autounattend.xml</span> quando la risposta è associata a una ISO di Windows.'
              : 'Nessun argomento aggiunto automaticamente: indica tu il percorso nella ricetta della ISO.')}</div>
        </div>
        <div class="drv-meta">${used.length
          ? 'Associata a: ' + used.map((s) => `<span class="mono">${esc(s)}</span>`).join(', ')
          : 'Non ancora associata a nessuna ISO: aprila dalla pagina <a href="#/iso">ISO</a> e scegli questa risposta.'}</div>
      </div>

      <div class="card">
        <div class="drv-head">
          <div id="ans-tabs">${fileTabs(d)}</div>
          <div class="actions">
            <button class="btn small" type="button" id="ans-upload" ${webUploadOff() ? 'disabled title="Upload dal browser disattivato (Impostazioni → Libreria locale)"' : ''}>Carica file</button>
            <button class="btn small" type="button" id="ans-main" ${!A.file || A.file === d.main_file ? 'disabled' : ''}>Usa come file principale</button>
            <button class="btn small danger" type="button" id="ans-delfile" ${!A.file || !(d.files || []).some((f) => f.name === A.file) ? 'disabled' : ''}>Elimina file</button>
          </div>
        </div>
        <div class="uploads drv-uploads" data-uploads="${esc(d.id)}" hidden></div>
        <div class="field">
          <label for="ans-editor">Contenuto di <span class="mono">${esc(A.file || k.main_file || 'file')}</span></label>
          <textarea id="ans-editor" class="mono" spellcheck="false" wrap="off" rows="24" aria-describedby="ans-editor-hint">${esc(d.content || '')}</textarea>
          <div class="hint" id="ans-editor-hint">Testo semplice, salvato in UTF-8 con fine riga Unix. Massimo 512 KB.
            ${d.kind === 'windows' ? ' Le password scritte qui restano in chiaro nel file e sono leggibili da chiunque sia in rete.' : ''}</div>
        </div>
        <div class="actions">
          <button class="btn primary" type="button" id="ans-save">Salva</button>
          <button class="btn" type="button" id="ans-tpl">Ricarica il modello</button>
          <span class="hint" id="ans-dirty"></span>
        </div>
      </div>
      <div class="drv-drop" id="ans-drop">Trascina qui i file aggiuntivi (script, file di post-installazione) da caricare in questa risposta</div>`;
    A.dirty = false;
    bindDetail();
    renderUploads();
  }

  function markDirty(on) {
    A.dirty = !!on;
    const el = A.root && $('#ans-dirty', A.root);
    if (el) el.textContent = on ? 'Modifiche non salvate' : '';
  }

  function bindDetail() {
    const d = A.detail;
    const ed = $('#ans-editor', A.root);
    ed.addEventListener('input', () => markDirty(true));
    // Tab inserisce due spazi invece di spostare il fuoco: comodo in XML e YAML
    ed.addEventListener('keydown', (e) => {
      if (e.key === 'Tab' && !e.shiftKey) {
        e.preventDefault();
        const s = ed.selectionStart; const t = ed.selectionEnd;
        ed.value = ed.value.slice(0, s) + '  ' + ed.value.slice(t);
        ed.selectionStart = ed.selectionEnd = s + 2;
        markDirty(true);
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveContent(); }
    });
    $('#ans-save', A.root).addEventListener('click', () => saveContent());
    $('#ans-copy', A.root).addEventListener('click', () => copyText($('#ans-url', A.root).textContent));
    $('#ans-rename', A.root).addEventListener('click', renameDialog);
    $('#ans-del', A.root).addEventListener('click', (e) => deleteAnswer(d.id, e.target));
    $('#ans-upload', A.root).addEventListener('click', () => queue.pick(d.id));
    $('#ans-tpl', A.root).addEventListener('click', reloadTemplate);
    $('#ans-main', A.root).addEventListener('click', setMainFile);
    $('#ans-delfile', A.root).addEventListener('click', (e) => deleteFile(A.file, e.target));
    const tabs = $('#ans-tabs', A.root);
    tabs.addEventListener('click', (e) => {
      const b = e.target.closest('[data-file]'); if (!b) return;
      switchFile(b.dataset.file);
    });
    // drag&drop di file aggiuntivi
    const drop = $('#ans-drop', A.root);
    const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
    ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; drop.classList.add('drag-over');
    }));
    drop.addEventListener('dragleave', () => drop.classList.remove('drag-over'));
    drop.addEventListener('drop', (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault(); e.stopPropagation(); drop.classList.remove('drag-over');
      queue.add(d.id, e.dataTransfer.files);
    });
  }

  async function switchFile(name) {
    if (name === A.file) return;
    if (A.dirty && !(await P.confirm('Ci sono modifiche non salvate: cambiando file andranno perse.', { title: 'Modifiche non salvate', ok: 'Cambia file' }))) return;
    A.file = name;
    await loadDetail(A.id, true);
  }

  async function saveContent() {
    const d = A.detail; if (!d) return;
    const btn = $('#ans-save', A.root);
    const name = A.file || kindOf(d.kind).main_file;
    if (!name) { P.toast('Indica prima il nome del file', 'warn'); return; }
    P.setBusy(btn, true, 'Salvataggio…');
    try {
      const r = await P.api('PUT', answerUrl(d.id), { filename: name, content: $('#ans-editor', A.root).value });
      P.toast(`"${name}" salvato`);
      markDirty(false);
      A.file = (r && r.answer && r.answer.written) || name;
      await loadDetail(d.id, true);
    } catch (e) { P.fail(e); P.setBusy(btn, false); }
  }

  async function reloadTemplate() {
    const d = A.detail; if (!d) return;
    if (!(await P.confirm('Sostituire il contenuto dell\'editor con il modello di partenza?', { title: 'Ricarica il modello', ok: 'Sostituisci' }))) return;
    try {
      const t = await templateFor(d.kind);
      $('#ans-editor', A.root).value = t.content || '';
      markDirty(true);
      P.toast('Modello caricato: controllalo e premi Salva', 'info');
    } catch (e) { P.fail(e); }
  }

  async function setMainFile() {
    const d = A.detail; if (!d || !A.file) return;
    try {
      await P.api('PUT', answerUrl(d.id), { filename: A.file });
      P.toast(`"${A.file}" è ora il file principale`);
      await loadDetail(d.id, true);
    } catch (e) { P.fail(e); }
  }

  async function deleteFile(name, btn) {
    const d = A.detail; if (!d || !name) return;
    if (!(await P.confirm(`Eliminare il file "${name}" dalla risposta "${d.name}"?`, { title: 'Elimina file', ok: 'Elimina', danger: true }))) return;
    P.setBusy(btn, true, '…');
    try {
      await P.api('DELETE', answerUrl(d.id) + '/files/' + encodeURIComponent(name));
      P.toast('File eliminato');
      A.file = null; A.dirty = false;
      await loadDetail(d.id, false);
    } catch (e) { P.fail(e); P.setBusy(btn, false); }
  }

  async function renameDialog() {
    const d = A.detail; if (!d) return;
    await P.modal({
      title: 'Rinomina la risposta',
      body: `<div class="field"><label for="rn-name">Nome</label><input id="rn-name" maxlength="64" value="${esc(d.name)}"></div>
        <div class="field"><label for="rn-note">Nota</label><input id="rn-note" maxlength="200" value="${esc(d.note || '')}" placeholder="es. aula 2, dischi NVMe"></div>
        <div class="hint">L'identificativo <span class="mono">${esc(d.id)}</span> e l'indirizzo pubblico non cambiano.</div>`,
      buttons: [{ label: 'Annulla', value: null }, {
        label: 'Salva', cls: 'primary',
        onClick: async (dlg) => {
          const name = $('#rn-name', dlg).value.trim();
          if (!name) throw new Error('Indica il nome della risposta');
          await P.api('PUT', answerUrl(d.id), { name, note: $('#rn-note', dlg).value.trim() });
          P.toast('Risposta aggiornata');
          await loadDetail(d.id, true);
          return true;
        },
      }],
    }).done;
  }

  async function deleteAnswer(id, btn) {
    const a = (A.list || []).find((x) => x.id === id) || A.detail || {};
    const used = (a.used_by || []).length;
    const ok = await P.confirm(`Eliminare la risposta "${a.name || id}" e tutti i suoi file?`, {
      title: 'Elimina risposta', ok: 'Elimina', danger: true,
      detail: used ? `${used} ISO la stanno usando: torneranno all'installazione manuale.` : "L'operazione non si può annullare.",
    });
    if (!ok) return;
    P.setBusy(btn, true, 'Eliminazione…');
    try {
      await P.api('DELETE', answerUrl(id));
      P.toast('Risposta eliminata');
      if (A.id === id) { location.hash = '#/risposte'; return; }
      await load();
    } catch (e) { P.fail(e); P.setBusy(btn, false); }
  }

  // ---------------------------------------------------------------- nuova risposta
  async function newAnswerDialog() {
    try { await loadKinds(); } catch (e) { P.fail(e); return; }
    const opts = A.kinds.map((k) => `<option value="${esc(k.id)}">${esc(k.name)}</option>`).join('');
    const dlg = P.modal({
      title: 'Nuova risposta',
      body: `<div class="field"><label for="na-name">Nome</label><input id="na-name" maxlength="64" placeholder="es. Aula 1 - Windows 11">
          <div class="hint">Serve solo a te per riconoscerla; l'identificativo usato negli indirizzi viene ricavato da qui.</div></div>
        <div class="field"><label for="na-kind">Sistema da installare</label><select id="na-kind">${opts}</select>
          <div class="hint" id="na-hint"></div></div>
        <div class="field"><label for="na-note">Nota (facoltativa)</label><input id="na-note" maxlength="200" placeholder="es. dischi NVMe, tastiera italiana"></div>
        <div class="field check"><input type="checkbox" id="na-tpl" checked><label for="na-tpl">Parti dal modello commentato (consigliato)</label></div>`,
      buttons: [{ label: 'Annulla', value: null }, {
        label: 'Crea', cls: 'primary',
        onClick: async (d) => {
          const name = $('#na-name', d).value.trim();
          if (!name) throw new Error('Indica il nome della risposta');
          const kind = $('#na-kind', d).value;
          const body = { name, kind, note: $('#na-note', d).value.trim() };
          if ($('#na-tpl', d).checked) body.content = (await templateFor(kind)).content;
          const r = await P.post('/api/answers', body);
          const a = (r && r.answer) || {};
          P.toast(`Risposta "${a.name || name}" creata`);
          A.file = null;
          location.hash = '#/risposte/' + encodeURIComponent(a.id);
          return true;
        },
      }],
    });
    const sel = $('#na-kind', dlg.el); const hint = $('#na-hint', dlg.el);
    const upd = () => { hint.textContent = kindOf(sel.value).hint || ''; };
    sel.addEventListener('change', upd); upd();
  }

  async function copyText(t) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(t);
      else {
        const ta = document.createElement('textarea'); ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
      }
      P.toast('Indirizzo copiato: ' + t, 'info');
    } catch (e) {
      P.toast('Copia non riuscita: seleziona e copia l\'indirizzo a mano', 'warn');
    }
  }

  // ---------------------------------------------------------------- pagina
  P.pages.risposte = {
    title: 'Risposte',
    mount(root, args) {
      A.root = root;
      A.id = (args && args[0]) ? decodeURIComponent(args[0]) : null;
      if (A.id) {
        root.innerHTML = '<div class="loading">Caricamento…</div>';
        loadKinds().catch(() => {}).then(() => loadDetail(A.id, false));
        return;
      }
      A.detail = null; A.file = null; A.dirty = false;
      root.innerHTML = `
        <div class="ph"><div><h2>Risposte</h2><div class="sub" id="ans-sub">Caricamento…</div></div>
          <div class="actions"><button class="btn primary" type="button" id="ans-new">Nuova risposta</button></div></div>
        <div class="card drv-intro">
          <p>Una <b>risposta automatica</b> è il file che l'installatore legge da solo per non fare domande: lingua, tastiera,
          partizionamento, utente e password. Pixio la pubblica su <span class="mono">http://${esc(serverIp())}/answers/&lt;id&gt;/</span>
          e la aggancia all'avvio della ISO che scegli (pagina <a href="#/iso">ISO</a>).</p>
          <p class="hint">Le risposte Windows nate da un <a href="#/windows">profilo Windows</a> vengono rigenerate al momento dell'avvio
          per l'immagine che stai installando: se l'edizione indicata nel profilo non è dentro quella immagine, Pixio non la scrive
          invece di far fallire l'installazione.</p>
          <p class="hint">Windows usa <span class="mono">autounattend.xml</span>, Debian il <span class="mono">preseed.cfg</span>,
          Ubuntu <span class="mono">user-data</span> (cloud-init), Red Hat e derivate il kickstart <span class="mono">ks.cfg</span>.
          Attenzione: i file contengono password in chiaro e sono scaricabili senza autenticazione da tutta la rete PXE.</p>
        </div>
        <div id="ans-list"><div class="loading">Caricamento…</div></div>`;
      $('#ans-new', root).addEventListener('click', newAnswerDialog);
      root.addEventListener('click', (e) => {
        const b = e.target.closest('[data-act],[data-up]'); if (!b) return;
        if (b.dataset.up) {
          const u = queue.byUid(b.dataset.uid); if (!u) return;
          if (b.dataset.up === 'cancel') u.cancel(); else if (b.dataset.up === 'retry') queue.retry(u);
          return;
        }
        const card = b.closest('.drv-card'); const id = card && card.dataset.answer;
        const act = b.dataset.act;
        if (act === 'new') newAnswerDialog();
        else if (act === 'open' && id) { A.file = null; location.hash = '#/risposte/' + encodeURIComponent(id); }
        else if (act === 'delete' && id) deleteAnswer(id, b);
      });
      load();
      A.timer = setInterval(() => { if (!document.hidden && !A.id) load(); }, 20000);
    },
    unmount() {
      clearInterval(A.timer); A.timer = null; A.root = null; A.detail = null; A.id = null; A.dirty = false;
    },
  };
})();
