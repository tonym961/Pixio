/* Pixio – pagina Driver: cartelle della libreria driver (\\ip\drivers), interruttori "Carica in WinPE all'avvio"
   e "Carica prima del setup di Windows", note, elenco file con eliminazione.

   Caricamento (docs/API.md, sezione 14): si possono scegliere o trascinare cartelle intere (anche più di una).
   Pixio crea da solo una cartella driver per ogni cartella scelta, mantiene le sottocartelle (campo "path"
   di /api/upload/init) e scarta i file che non sono driver (.exe di installazione, file di lingua, documentazione)
   mostrando quanti ne ha saltati; la casella "carica tutti i file" forza l'invio di tutto.
   Le schede cartella hanno una casella di selezione con barra delle azioni per accendere/spegnere i due
   interruttori su più cartelle insieme o eliminarle in blocco.

   Abbinamento alle immagini (docs/API.md, sezione 15): la riga "Si applica a" dice se la cartella vale per
   tutte le immagini, solo per certi gruppi del menu di boot (es. "Windows Server") o solo per certe ISO.
   Nel pannello dei file ogni file che finirebbe nel WinPE ha una casella per escluderlo, con "escludi tutti"
   e "includi tutti": così i driver RAID vanno solo sui server e non appesantiscono il WinPE dei PC. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  const D = { data: null, root: null, timer: null, busy: new Set(), openFolder: null, sel: new Set() };
  const CHUNK_DEFAULT = 8 * 1024 * 1024;
  const MAX_INJECT = 256 * 1024 * 1024;
  // estensioni accettate dal server per i file driver
  const EXT = ['inf', 'sys', 'cat', 'dll', 'exe', 'cab', 'zip', 'msi', 'txt', 'bin', 'dat', 'ini', 'cfg', 'xml', 'json', '7z', 'sepolicy'];
  const EXT_RE = new RegExp('\\.(' + EXT.join('|') + ')$', 'i');
  // estensioni che servono davvero a installare un driver: tutto il resto è scarto del pacchetto
  const USEFUL = ['inf', 'sys', 'cat', 'dll', 'bin', 'dat', 'cab', 'sepolicy'];
  const USEFUL_RE = new RegExp('\\.(' + USEFUL.join('|') + ')$', 'i');
  const WINPE_RE = /\.(inf|sys|cat|dll)$/i;
  const MAX_PATH_DEPTH = 6;        // livelli ammessi dal campo "path" (sottocartelle + nome file)
  const MAX_BATCH_FILES = 4000;    // limite di sicurezza su una singola infornata
  // Testi dei due flag: stesso significato del contratto API (docs/API.md, sezione Driver)
  const FLAGS = {
    winpe_inject: {
      label: "Carica in WinPE all'avvio",
      help: 'I file .inf/.sys/.cat/.dll al primo livello della cartella vengono iniettati nel WinPE via wimboot e caricati con drvload prima della rete. Serve per schede di rete o controller storage che WinPE non riconosce (max 256 MB totali).',
    },
    setup_load: {
      label: 'Carica prima del setup di Windows',
      help: 'Dopo aver mappato la share, drvload ricorsivo di tutti i .inf della cartella prima di setup.exe (richiede "Installazione Windows via rete" attiva).',
    },
  };
  // Modalità di abbinamento della cartella alle immagini (campo apply_to del contratto API, sezione 15)
  const APPLY = {
    all: { label: 'Tutte le immagini', help: 'La cartella vale per ogni voce del menu di boot.' },
    groups: { label: 'Solo alcuni gruppi', help: 'Solo le voci dei gruppi scelti nel menu di boot, per esempio "Windows Server".' },
    isos: { label: 'Solo alcune immagini', help: 'Solo le ISO scelte, una per una.' },
  };
  const APPLY_MODES = ['all', 'groups', 'isos'];

  const folderUrl = (name) => '/api/drivers/folders/' + encodeURIComponent(name);
  const fileUrl = (name, rel) => folderUrl(name) + '/files/' + String(rel).split('/').map(encodeURIComponent).join('/');
  const findFolder = (name) => ((D.data && D.data.folders) || []).find((f) => f.name === name);
  const sharePath = () => (D.data && D.data.samba_path) || ('\\\\' + ((P.state.status && P.state.status.server_ip) || 'pixio') + '\\drivers');
  const webUploadOff = () => !!(P.state.status && P.state.status.library && P.state.status.library.web_upload_enabled === false);
  const plural = (n, uno, molti) => `${n} ${n === 1 ? uno : molti}`;
  const isUseful = (n) => USEFUL_RE.test(n || '');
  const isAllowed = (n) => EXT_RE.test(n || '');
  const fileUseful = (x) => (x && x.useful !== undefined ? !!x.useful : isUseful(x && x.name));

  /* --- abbinamento cartella -> immagini ------------------------------------------------------------ */
  /* Scelte possibili offerte dal server: gruppi del menu di boot e ISO Windows/WinPE del catalogo. */
  function choices() {
    const d = (D.data && (D.data.apply_choices || D.data)) || {};
    return { groups: Array.isArray(d.groups) ? d.groups : [], isos: Array.isArray(d.isos) ? d.isos : [] };
  }
  /* apply_to della cartella, normalizzato: le cartelle vecchie (senza campo) valgono per tutte le immagini. */
  function applyOf(f) {
    const a = (f && f.apply_to) || {};
    return {
      mode: APPLY_MODES.indexOf(a.mode) >= 0 ? a.mode : 'all',
      groups: Array.isArray(a.groups) ? a.groups.slice() : [],
      isos: Array.isArray(a.isos) ? a.isos.slice() : [],
    };
  }
  const isoName = (slug) => {
    const x = choices().isos.find((i) => i.slug === slug);
    return (x && x.name) || slug;
  };
  /* Riassunto leggibile per la scheda: "tutte le immagini", "solo Windows Server", "solo 2 immagini". */
  function applySummary(f) {
    const a = applyOf(f);
    if (a.mode === 'all') return 'tutte le immagini';
    if (a.mode === 'groups') {
      if (!a.groups.length) return 'nessuna immagine';
      return a.groups.length === 1 ? 'solo ' + a.groups[0] : 'solo ' + plural(a.groups.length, 'gruppo', 'gruppi');
    }
    if (!a.isos.length) return 'nessuna immagine';
    return a.isos.length === 1 ? 'solo ' + isoName(a.isos[0]) : 'solo ' + plural(a.isos.length, 'immagine', 'immagini');
  }
  /* True quando la cartella è accesa ma non è abbinata a nessuna immagine: non verrebbe mai usata. */
  const applyEmpty = (f) => {
    const a = applyOf(f);
    return a.mode !== 'all' && !(a.mode === 'groups' ? a.groups : a.isos).length;
  };
  const active = (f) => !!(f.winpe_inject || f.setup_load);
  /* File della cartella che potrebbero finire nel WinPE (candidati): il server li marca con winpe_cand. */
  const candidates = (f) => (f.files || []).filter((x) => (x.winpe_cand !== undefined ? !!x.winpe_cand : (!x.name.includes('/') && WINPE_RE.test(x.name))));

  // ---------------------------------------------------------------- nomi di cartelle e sottopercorsi
  // Stesse regole del server: cartella ^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,63}$
  function cleanFolderName(s) {
    let n = String(s || '').replace(/[\\/]+/g, ' ').replace(/[^A-Za-z0-9 ._()+-]/g, '-');
    n = n.replace(/\s+/g, ' ').replace(/^[^A-Za-z0-9]+/, '').trim().slice(0, 64).trim();
    return n.replace(/[ .]+$/, '');
  }
  // Segmento di sottocartella: gli stessi caratteri ammessi nei nomi file dal server
  function cleanSeg(s) {
    return String(s || '').replace(/[^\w.\- ()+[\]]/g, '_').replace(/\s+/g, ' ').trim()
      .replace(/^\.+/, '').replace(/[ .]+$/, '');
  }
  /* Da "x64/win11/rt.inf" a {subdir:"x64/win11", deep:false}: le sottocartelle oltre il limite
     vengono accorpate, così il file arriva comunque. */
  function splitPath(rel) {
    const segs = String(rel || '').split('/').map(cleanSeg).filter(Boolean);
    segs.pop();                                    // l'ultimo segmento è il nome del file
    const max = MAX_PATH_DEPTH - 1;
    return { subdir: segs.slice(0, max).join('/'), deep: segs.length > max };
  }

  // ---------------------------------------------------------------- upload (chunked, riprendibile, in sequenza)
  let uploadSeq = 0;
  class DriverUpload {
    constructor(folder, file, subdir) {
      this.uid = ++uploadSeq; this.folder = folder; this.file = file; this.name = file.name; this.size = file.size;
      this.subdir = subdir || ''; this.rel = this.subdir ? this.subdir + '/' + this.name : this.name;
      this.sent = 0; this.status = 'queued'; this.error = null; this.speed = 0; this.eta = null;
      this.ctrl = null; this.id = null; this.result = null;
    }
    chunkLen(n, chunk) { return Math.min(chunk, this.size - n * chunk); }
    async run() {
      this.status = 'init'; this.error = null; this.sent = 0; queue.notify();
      try {
        const body = { filename: this.name, size: this.size, kind: 'driver', folder: this.folder };
        if (this.subdir) body.path = this.rel;
        const init = await P.post('/api/upload/init', body);
        this.id = init.upload_id;
        const chunk = Number(init.chunk_size) > 0 ? Number(init.chunk_size) : CHUNK_DEFAULT;
        const total = Math.max(1, Math.ceil(this.size / chunk));
        const have = new Set((init.received || []).map(Number));
        if (have.size) {
          have.forEach((n) => { if (n < total) this.sent += this.chunkLen(n, chunk); });
          P.toast(`Ripreso il caricamento di ${this.rel} dal ${P.pct(this.sent, this.size)}%`, 'info');
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
        const fin = await P.post(`/api/upload/${encodeURIComponent(this.id)}/finish`);
        this.status = 'done'; this.result = fin || {}; this.sent = this.size;
        queue.finished(this);
        if (!queue.batch.silent) {
          if (/\.zip$/i.test(this.name)) P.toast(`${this.name}: estratto ${Number(this.result.extracted) || 0} file in "${this.folder}"`);
          else P.toast(`${this.rel} caricato in "${this.folder}"`);
        }
        if (!queue.list.some((u) => u.status === 'queued')) load();
        setTimeout(() => queue.remove(this), 12000);
      } catch (e) {
        if (this.status === 'cancelled') return;
        this.status = 'error'; this.error = (e && e.message) || String(e);
        queue.batch.errors++; queue.notify();
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

  const emptyBatch = () => ({ files: 0, done: 0, bytes: 0, sentDone: 0, errors: 0, skipped: 0, refused: 0,
    deep: 0, folders: [], silent: false, closed: false });

  // Coda globale: un file alla volta, continua anche se si cambia pagina
  const queue = {
    list: [], running: false, batch: emptyBatch(),
    notify() { renderUploads(); renderQueue(); },
    remove(u) { this.list = this.list.filter((x) => x !== u); this.notify(); },
    byUid(uid) { return this.list.find((u) => u.uid === Number(uid)); },
    active() { return this.list.filter((u) => u.status !== 'done' && u.status !== 'error').length; },
    finished(u) { this.batch.done++; this.batch.sentDone += u.size; this.notify(); },
    current() { const u = this.list.find((x) => x.status !== 'queued' && x.status !== 'done' && x.status !== 'error'); return u || null; },
    resetIfIdle() { if (!this.active()) { this.batch = emptyBatch(); } },
    /* entries: [{folder, file, subdir}] – ritorna quanti ne ha accodati */
    addMany(entries, info) {
      if (webUploadOff()) { P.toast('Upload dal browser disattivato: attivalo in Impostazioni → Libreria locale', 'warn'); return 0; }
      this.resetIfIdle();
      const b = this.batch;
      b.closed = false;
      if (info) {
        b.skipped += info.skipped || 0; b.refused += info.refused || 0; b.deep += info.deep || 0;
        b.silent = !!info.silent;
        (info.folders || []).forEach((n) => { if (b.folders.indexOf(n) < 0) b.folders.push(n); });
      }
      let added = 0;
      entries.forEach((it) => {
        const f = it.file; const sub = it.subdir || '';
        const rel = sub ? sub + '/' + f.name : f.name;
        if (!isAllowed(f.name)) { b.refused++; return; }
        if (!f.size) { b.refused++; return; }
        if (this.list.some((u) => u.folder === it.folder && u.rel === rel && u.size === f.size && u.status !== 'error' && u.status !== 'done')) return;
        this.list.push(new DriverUpload(it.folder, f, sub));
        b.files++; b.bytes += f.size; added++;
      });
      this.notify();
      if (added) this.pump();
      return added;
    },
    add(folder, files) {
      const list = Array.from(files || []);
      const bad = list.filter((f) => !isAllowed(f.name));
      const empty = list.filter((f) => isAllowed(f.name) && !f.size);
      if (bad.length) P.toast(`${bad.length === 1 ? bad[0].name + ': tipo di file non ammesso' : plural(bad.length, 'file saltato', 'file saltati') + ': tipo non ammesso'} (${EXT.join(', ')})`, 'warn', 6000);
      if (empty.length) P.toast(`${plural(empty.length, 'file vuoto saltato', 'file vuoti saltati')}`, 'warn');
      const n = this.addMany(list.filter((f) => isAllowed(f.name) && f.size).map((f) => ({ folder, file: f, subdir: '' })), { silent: false, folders: [folder] });
      if (!n && !bad.length && !empty.length) P.toast('Nessun file nuovo da caricare (sono già in coda)', 'warn');
    },
    retry(u) { if (u.status === 'error') { u.status = 'queued'; this.batch.errors = Math.max(0, this.batch.errors - 1); this.notify(); this.pump(); } },
    async pump() {
      if (this.running) return;
      this.running = true;
      try {
        for (;;) {
          const u = this.list.find((x) => x.status === 'queued');
          if (!u) break;
          await u.run();
        }
      } finally { this.running = false; this.notify(); load(); }
    },
    pick(folder) {
      if (webUploadOff()) { P.toast('Upload dal browser disattivato: attivalo in Impostazioni → Libreria locale', 'warn'); return; }
      const inp = document.createElement('input');
      inp.type = 'file'; inp.multiple = true; inp.hidden = true; inp.accept = EXT.map((e) => '.' + e).join(',');
      document.body.appendChild(inp);
      inp.addEventListener('change', () => { this.add(folder, inp.files); inp.remove(); });
      inp.click();
    },
  };

  function uploadHtml(u) {
    const pct = P.pct(u.sent, u.size);
    let meta = ''; let barCls = '';
    if (u.status === 'queued') meta = 'In coda…';
    else if (u.status === 'init') meta = 'Avvio del caricamento…';
    else if (u.status === 'uploading' || u.status === 'retry') meta = `${pct}% · ${P.fmtBytes(u.sent)} di ${P.fmtBytes(u.size)} · ${u.speed ? P.fmtBytes(u.speed) + '/s' : '—'} · rimanenti ${P.fmtDuration(u.eta)}${u.status === 'retry' ? ' · nuovo tentativo…' : ''}`;
    else if (u.status === 'finishing') meta = /\.zip$/i.test(u.name) ? 'Estrazione dello zip sul server…' : 'Salvataggio del file…';
    else if (u.status === 'done') { meta = /\.zip$/i.test(u.name) ? `Completato · estratto ${Number(u.result && u.result.extracted) || 0} file` : 'Completato'; barCls = 'done'; }
    else if (u.status === 'error') { meta = 'Errore: ' + (u.error || ''); barCls = 'err'; }
    const btns = u.status === 'error'
      ? `<button class="btn small" type="button" data-up="retry" data-uid="${u.uid}">Riprova</button><button class="btn small" type="button" data-up="cancel" data-uid="${u.uid}">Rimuovi</button>`
      : (u.status === 'done' ? '' : `<button class="btn small" type="button" data-up="cancel" data-uid="${u.uid}">Annulla</button>`);
    return `<div class="upload" role="group" aria-label="Caricamento ${esc(u.rel)}"><div class="row"><span class="name">${esc(u.rel)} <span class="hint">${P.fmtBytes(u.size)}</span></span><span class="actions">${btns}</span></div>
      <div class="bar ${barCls}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></div><div class="meta">${esc(meta)}</div></div>`;
  }
  function renderUploads() {
    if (!D.root) return;
    $$('[data-uploads]', D.root).forEach((box) => {
      const items = queue.list.filter((u) => u.folder === box.dataset.uploads);
      box.innerHTML = items.map(uploadHtml).join('');
      box.hidden = !items.length;
    });
  }

  /* Avanzamento complessivo della coda: quanti file in tutto, a che punto siamo, in quale cartella
     e che cosa è stato saltato perché non è un driver. */
  function renderQueue() {
    if (!D.root) return;
    const box = $('#drv-queue', D.root); if (!box) return;
    const b = queue.batch;
    if (!b.files || b.closed) { box.hidden = true; box.innerHTML = ''; return; }
    const cur = queue.current();
    const sent = b.sentDone + queue.list.filter((u) => u.status !== 'done').reduce((a, u) => a + u.sent, 0);
    const pct = P.pct(sent, b.bytes);
    const fin = !queue.active();
    const doneN = Math.min(b.done + (fin ? 0 : 1), b.files);
    let meta;
    if (fin) meta = `${plural(b.done, 'file caricato', 'file caricati')} su ${b.files}${b.errors ? ` · ${plural(b.errors, 'errore', 'errori')}` : ''}`;
    else meta = `File ${doneN} di ${b.files} · ${P.fmtBytes(sent)} di ${P.fmtBytes(b.bytes)}${cur ? ` · cartella «${cur.folder}»` : ''}`;
    const notes = [];
    if (b.skipped) notes.push(`${plural(b.skipped, 'file ignorato', 'file ignorati')} perché non ${b.skipped === 1 ? 'è un driver' : 'sono driver'}`);
    if (b.refused) notes.push(`${plural(b.refused, 'file saltato', 'file saltati')} perché vuoto o di un tipo che il server non accetta`);
    if (b.deep) notes.push(`${plural(b.deep, 'file spostato', 'file spostati')} di livello: le sottocartelle oltre ${MAX_PATH_DEPTH - 1} livelli vengono accorpate`);
    box.hidden = false;
    box.innerHTML = `<div class="card drv-queue-card">
      <div class="drv-q-head"><b>${fin ? 'Caricamento finito' : 'Caricamento in corso'}</b>
        <span class="hint">${b.folders.length ? esc(b.folders.length === 1 ? 'cartella: ' + b.folders[0] : b.folders.length + ' cartelle: ' + b.folders.join(', ')) : ''}</span>
        ${fin ? '<button class="btn small" type="button" data-q="close">Chiudi</button>' : ''}</div>
      <div class="bar ${fin ? (b.errors ? 'err' : 'done') : ''}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></div>
      <div class="meta">${esc(meta)}</div>
      ${notes.map((t) => `<div class="drv-q-note">${esc(t)}</div>`).join('')}</div>`;
  }

  // ---------------------------------------------------------------- lettura di cartelle (scelta o trascinate)
  /* Legge ricorsivamente una voce del trascinamento (DataTransferItem.webkitGetAsEntry).
     out: [{file, path}] con path relativo che comprende il nome della cartella radice. */
  function readEntry(entry, prefix, depth, out) {
    return new Promise((resolve) => {
      if (!entry || out.length >= MAX_BATCH_FILES) { resolve(); return; }
      if (entry.isFile) {
        entry.file((f) => { out.push({ file: f, path: prefix + entry.name }); resolve(); }, () => resolve());
        return;
      }
      if (!entry.isDirectory || depth > 12) { resolve(); return; }
      const reader = entry.createReader();
      const kids = [];
      const step = () => {
        reader.readEntries((ents) => {
          if (!ents || !ents.length) {
            (async () => {
              for (let i = 0; i < kids.length; i++) await readEntry(kids[i], prefix + entry.name + '/', depth + 1, out);
              resolve();
            })();
            return;
          }
          for (let i = 0; i < ents.length; i++) kids.push(ents[i]);
          step();
        }, () => resolve());
      };
      step();
    });
  }
  /* Le voci del DataTransfer vanno lette subito (l'elenco si svuota dopo il gestore): qui si raccolgono
     le entry in modo sincrono e poi si legge il contenuto. */
  function dropEntries(dt) {
    const entries = [];
    const items = dt && dt.items ? Array.from(dt.items) : [];
    items.forEach((it) => {
      if (it.kind !== 'file') return;
      const get = it.webkitGetAsEntry || it.getAsEntry;
      const e = get ? get.call(it) : null;
      if (e) entries.push(e);
    });
    if (entries.length) {
      const out = [];
      return (async () => {
        for (let i = 0; i < entries.length; i++) await readEntry(entries[i], '', 0, out);
        return out;
      })();
    }
    return Promise.resolve(Array.from((dt && dt.files) || []).map((f) => ({ file: f, path: f.name })));
  }

  // ---------------------------------------------------------------- analisi e riepilogo di una infornata
  function analyze(rootName, items) {
    const g = { raw: rootName, name: cleanFolderName(rootName), useful: [], other: [], refused: [], deep: 0, error: null };
    items.forEach((it) => {
      const path = splitPath(it.path);
      const rec = { file: it.file, subdir: path.subdir };
      if (path.deep) g.deep++;
      if (!it.file || !it.file.size || !isAllowed(it.file.name)) { g.refused.push(rec); return; }
      (isUseful(it.file.name) ? g.useful : g.other).push(rec);
    });
    return g;
  }
  const toSend = (groups, all) => groups.reduce((a, g) => a + g.useful.length + (all ? g.other.length : 0), 0);

  function summaryHtml(groups, all) {
    const rows = groups.map((g) => {
      const send = g.useful.length + (all ? g.other.length : 0);
      const skip = (all ? 0 : g.other.length) + g.refused.length;
      return `<tr><td>${esc(g.name)}${g.name !== g.raw ? ` <span class="hint">(era “${esc(g.raw)}”)</span>` : ''}</td>
        <td class="num">${send}</td><td class="num ${skip ? 'drv-skip' : 'hint'}">${skip}</td></tr>`;
    }).join('');
    const skipped = groups.reduce((a, g) => a + (all ? 0 : g.other.length), 0);
    const refused = groups.reduce((a, g) => a + g.refused.length, 0);
    const notes = [];
    if (skipped) notes.push(`${plural(skipped, 'file ignorato', 'file ignorati')} perché non ${skipped === 1 ? 'è un driver' : 'sono driver'} (installatori .exe, file di lingua, documentazione…).`);
    if (refused) notes.push(`${plural(refused, 'file saltato', 'file saltati')} perché vuoto o di un tipo che il server non accetta.`);
    if (!skipped && !refused) notes.push('Tutti i file della cartella servono: nessuno viene saltato.');
    return `<div class="tbl-wrap"><table><thead><tr><th>Cartella</th><th class="num">Da caricare</th><th class="num">Saltati</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${notes.map((t) => `<div class="hint" style="margin-top:6px">${esc(t)}</div>`).join('')}`;
  }

  function confirmBatch(groups, target) {
    const st = { all: false };
    const tot = groups.reduce((a, g) => a + g.useful.length + g.other.length + g.refused.length, 0);
    const m = P.modal({
      title: groups.length === 1 ? 'Carica la cartella di driver' : `Carica ${groups.length} cartelle di driver`,
      wide: true,
      body: `<p>${target
        ? `I file finiscono nella cartella <b>${esc(target)}</b>, mantenendo le sottocartelle.`
        : 'Pixio crea una cartella driver per ogni cartella scelta e ci mette dentro i file, mantenendo le sottocartelle.'}
        In tutto sono stati letti ${plural(tot, 'file', 'file')}.</p>
        <div id="dq-sum"></div>
        <label class="drv-all"><input type="checkbox" id="dq-all"> Carica tutti i file (anche quelli che non sono driver)</label>
        <div class="hint">Serve solo se il pacchetto richiede file accessori (per esempio un <span class="mono">.cab</span> con firme o un <span class="mono">.exe</span> di supporto). Normalmente lascia la casella vuota: nella cartella restano solo i file che servono davvero.</div>`,
      buttons: [{ label: 'Annulla', value: null }, { label: 'Carica', cls: 'primary', value: true }],
    });
    const sum = $('#dq-sum', m.el); const cb = $('#dq-all', m.el);
    const primary = m.el.querySelector('.dlg-ft .btn.primary');
    const refresh = () => {
      st.all = cb.checked;
      sum.innerHTML = summaryHtml(groups, st.all);
      const n = toSend(groups, st.all);
      primary.textContent = n ? `Carica ${plural(n, 'file', 'file')}` : 'Nessun file da caricare';
      primary.disabled = !n;
    };
    cb.addEventListener('change', refresh);
    refresh();
    return m.done.then((v) => (v === true ? st : null));
  }

  async function ensureFolder(name) {
    try { await P.post('/api/drivers/folders', { name }); return true; }
    catch (e) { if (e.status === 409) return false; throw e; }
  }

  /* Cuore della funzione: cartelle scelte o trascinate -> cartelle Pixio + coda di caricamento.
     `target` (facoltativo) forza tutto dentro una cartella già esistente. */
  async function startBatch(items, target) {
    if (webUploadOff()) { P.toast('Upload dal browser disattivato: attivalo in Impostazioni → Libreria locale', 'warn'); return; }
    if (!items.length) { P.toast('Nessun file da caricare', 'warn'); return; }
    if (items.length >= MAX_BATCH_FILES) P.toast(`Lettura limitata ai primi ${MAX_BATCH_FILES} file`, 'warn', 6000);
    let groups; let loose = 0;
    if (target) {
      groups = [analyze(target, items)];
      groups[0].name = target; groups[0].raw = target;
    } else {
      const map = new Map();
      items.forEach((it) => {
        const segs = String(it.path || '').split('/').filter(Boolean);
        if (segs.length < 2) { loose++; return; }
        const root = segs[0];
        if (!map.has(root)) map.set(root, []);
        map.get(root).push({ file: it.file, path: segs.slice(1).join('/') });
      });
      groups = Array.from(map, ([name, its]) => analyze(name, its));
      const senza = groups.filter((g) => !g.name);
      if (senza.length) {
        P.toast(`${plural(senza.length, 'cartella saltata', 'cartelle saltate')}: il nome non è utilizzabile (${senza.map((g) => g.raw).join(', ')})`, 'warn', 8000);
        groups = groups.filter((g) => g.name);
      }
      if (!groups.length) {
        P.toast(loose ? 'Trascina una cartella, non i singoli file: per i file sciolti usa "Carica file" dentro una cartella' : 'Nessuna cartella da caricare', 'warn', 7000);
        return;
      }
    }
    const choice = await confirmBatch(groups, target);
    if (!choice) return;

    let created = 0;
    if (!target) {
      for (let i = 0; i < groups.length; i++) {
        try { if (await ensureFolder(groups[i].name)) created++; }
        catch (e) { groups[i].error = (e && e.message) || String(e); }
      }
      const ko = groups.filter((g) => g.error);
      if (ko.length) P.toast(`Cartelle non create: ${ko.map((g) => `${g.name} (${g.error})`).join('; ')}`, 'bad', 9000);
      await load();
    }
    const ok = groups.filter((g) => !g.error);
    const entries = [];
    let skipped = 0; let refused = 0; let deep = 0;
    ok.forEach((g) => {
      deep += g.deep; refused += g.refused.length;
      if (!choice.all) skipped += g.other.length;
      const src = choice.all ? g.useful.concat(g.other) : g.useful;
      src.forEach((rec) => entries.push({ folder: g.name, file: rec.file, subdir: rec.subdir }));
    });
    const n = queue.addMany(entries, { skipped, refused, deep, silent: true, folders: ok.map((g) => g.name) });
    const parti = [];
    parti.push(`${plural(n, 'file in caricamento', 'file in caricamento')} in ${plural(ok.length, 'cartella', 'cartelle')}`);
    if (created) parti.push(`${plural(created, 'cartella creata', 'cartelle create')}`);
    if (skipped) parti.push(`${plural(skipped, 'file ignorato', 'file ignorati')} perché non ${skipped === 1 ? 'è un driver' : 'sono driver'}`);
    if (refused) parti.push(`${plural(refused, 'file saltato', 'file saltati')} perché vuoto o non ammesso`);
    P.toast(parti.join(' · '), n ? 'ok' : 'warn', 8000);
    if (!n) load();
  }

  function pickFolders() {
    if (webUploadOff()) { P.toast('Upload dal browser disattivato: attivalo in Impostazioni → Libreria locale', 'warn'); return; }
    const inp = document.createElement('input');
    inp.type = 'file'; inp.multiple = true; inp.hidden = true;
    inp.setAttribute('webkitdirectory', ''); inp.setAttribute('directory', ''); inp.setAttribute('mozdirectory', '');
    inp.webkitdirectory = true;
    document.body.appendChild(inp);
    inp.addEventListener('change', () => {
      const items = Array.from(inp.files || []).map((f) => ({ file: f, path: f.webkitRelativePath || f.name }));
      inp.remove();
      if (items.length) startBatch(items, null);
    });
    inp.click();
  }

  // ---------------------------------------------------------------- dati
  async function load() {
    if (!D.root) return;
    try {
      const d = await P.get('/api/drivers');
      if (!D.root) return;
      D.data = d || { folders: [] };
      if (!Array.isArray(D.data.folders)) D.data.folders = [];
      const names = new Set(D.data.folders.map((f) => f.name));
      Array.from(D.sel).forEach((n) => { if (!names.has(n)) D.sel.delete(n); });
      render();
    } catch (e) {
      if (e.status === 401) return;
      const box = $('#drv-list', D.root);
      if (box && !D.data) box.innerHTML = `<div class="empty"><h3>Libreria driver non disponibile</h3><p>${esc(e.message)}</p></div>`;
      else P.fail(e);
    }
  }
  function editing() {
    const a = document.activeElement;
    return !!(a && D.root && D.root.contains(a) && /^(INPUT|TEXTAREA)$/.test(a.tagName));
  }

  // ---------------------------------------------------------------- rendering
  function shareStatus() {
    const st = P.state.status || {}; const smbd = (st.services || {}).smbd;
    if (D.data && D.data.samba_enabled === false) return P.pill('share disattivata', 'warn') + ' <span class="hint">attivala in <a href="#/impostazioni">Impostazioni → Libreria locale</a></span>';
    if (smbd && smbd.active === false) return P.pill('Samba non attivo', 'bad') + ' <span class="hint">la share non è raggiungibile finché smbd è fermo</span>';
    return P.pill('share attiva', 'ok') + ' <span class="hint">utente <span class="mono">pixio</span>, password di Impostazioni → Libreria locale; una sottocartella = una cartella driver</span>';
  }

  function warningsHtml(f) {
    const w = [];
    if (f.winpe_size > MAX_INJECT) w.push(`I file al primo livello della cartella pesano ${P.fmtBytes(f.winpe_size)}: oltre 256 MB WinPE ne carica solo una parte. Lascia nella cartella solo i file del driver che serve davvero.`);
    if (f.winpe_inject && !f.winpe_files) w.push(`"${FLAGS.winpe_inject.label}" è attivo ma nella cartella non ci sono file .inf/.sys/.cat/.dll al primo livello: metti i file del driver direttamente nella cartella, non in sottocartelle.`);
    if (f.setup_load && !f.inf_count) w.push(`"${FLAGS.setup_load.label}" è attivo ma nella cartella non c'è nessun file .inf.`);
    if (active(f) && applyEmpty(f)) w.push(`"Si applica a" è su "${APPLY[applyOf(f).mode].label}" ma non hai scelto niente: questa cartella non verrà usata da nessuna immagine. Scegli almeno una voce o torna a "${APPLY.all.label}".`);
    if (!f.valid_name) w.push('Nome cartella non valido (ammessi lettere, numeri, spazi, . _ - ( ) +, max 64): rinominala dalla share, altrimenti non viene usata dal setup.');
    return w.map((t) => `<div class="alert warn">${esc(t)}</div>`).join('');
  }

  /* Riga "Si applica a": tre modalità e, quando serve, l'elenco a caselle di gruppi o immagini. */
  function applyHtml(f) {
    const a = applyOf(f);
    const ch = choices();
    const off = D.busy.has(f.name) || !f.valid_name ? 'disabled' : '';
    const modes = APPLY_MODES.map((m) => `<button class="btn small${a.mode === m ? ' primary' : ''}" type="button" data-apply="${m}" aria-pressed="${a.mode === m ? 'true' : 'false'}" title="${esc(APPLY[m].help)}" ${off}>${esc(APPLY[m].label)}</button>`).join('');
    let list = '';
    if (a.mode === 'groups') {
      list = ch.groups.length
        ? ch.groups.map((g) => `<label class="drv-apply-item"><input type="checkbox" data-apply-group="${esc(g)}" ${a.groups.indexOf(g) >= 0 ? 'checked' : ''} ${off}><span>${esc(g)}</span></label>`).join('')
        : '<div class="hint">Nessun gruppo nel menu di boot: aggiungine in <a href="#/impostazioni">Impostazioni → Menu</a> o assegna un gruppo alle ISO.</div>';
    } else if (a.mode === 'isos') {
      list = ch.isos.length
        ? ch.isos.map((i) => `<label class="drv-apply-item"><input type="checkbox" data-apply-iso="${esc(i.slug)}" ${a.isos.indexOf(i.slug) >= 0 ? 'checked' : ''} ${off}><span>${esc(i.name)}${i.group ? ` <span class="hint">(${esc(i.group)})</span>` : ''}</span></label>`).join('')
        : '<div class="hint">Nel catalogo non ci sono ISO Windows o WinPE: sono le uniche che ricevono driver.</div>';
    }
    return `<div class="drv-apply">
      <div class="drv-apply-head">
        <div><div class="tt">Si applica a</div><div class="td">A quali immagini del menu di boot serve questa cartella: i driver RAID di un server non servono a un PC da ufficio.</div></div>
        <div class="actions" role="group" aria-label="Si applica a: ${esc(f.name)}">${modes}</div>
      </div>
      ${list ? `<div class="drv-apply-list">${list}</div>` : ''}
      <div class="drv-apply-sum hint">In uso su: <b>${esc(applySummary(f))}</b></div>
    </div>`;
  }

  function folderHtml(f) {
    const busy = D.busy.has(f.name);
    const sw = (flag) => P.switchHtml(!!f[flag], `data-flag="${flag}" aria-label="${esc(FLAGS[flag].label)}: ${esc(f.name)}" title="${esc(FLAGS[flag].help)}" ${busy || !f.valid_name ? 'disabled' : ''}`);
    const ign = Number(f.ignored_files || 0);
    const use = f.useful_files === undefined ? null : Number(f.useful_files);
    const cand = f.winpe_candidates === undefined ? candidates(f).length : Number(f.winpe_candidates);
    return `<div class="drv-card${D.sel.has(f.name) ? ' picked' : ''}" data-folder="${esc(f.name)}">
      <div class="drv-head">
        <div class="drv-head-l">
          <label class="drv-pick" title="Seleziona la cartella per le azioni su più cartelle"><input type="checkbox" data-pick ${D.sel.has(f.name) ? 'checked' : ''} aria-label="Seleziona la cartella ${esc(f.name)}"></label>
          <div>
            <div class="drv-name"><span>${esc(f.name)}</span>${f.valid_name ? '' : P.pill('nome non valido', 'bad')}${f.winpe_inject ? P.pill('WinPE', 'acc') : ''}${f.setup_load ? P.pill('setup', 'acc') : ''}${P.pill(applySummary(f), applyEmpty(f) ? 'warn' : (applyOf(f).mode === 'all' ? 'neutral' : 'acc'))}</div>
            <div class="drv-meta"><span>${f.count} file · ${P.fmtBytes(f.size)}</span>${use === null ? '' : P.pill(use + ' utili', use ? 'acc' : 'neutral')}${ign ? `<span class="pill warn" title="File presenti nella cartella che non servono all'installazione del driver (.exe, .txt, .ini, …)">${ign} non usati</span>` : ''}${P.pill('.inf: ' + f.inf_count, f.inf_count ? 'acc' : 'neutral')}<span class="pill ${f.excluded_files ? 'warn' : 'neutral'}" title="File .inf/.sys/.cat/.dll che finiscono davvero nel WinPE, esclusioni comprese${cand ? ` (su ${cand} possibili)` : ''}">WinPE: ${f.winpe_files}${cand && cand !== f.winpe_files ? ' su ' + cand : ''} file · ${P.fmtBytes(f.winpe_size)}${f.excluded_files ? ` · ${f.excluded_files} esclusi` : ''}</span></div>
          </div>
        </div>
        <div class="actions">
          <button class="btn small primary" type="button" data-act="upload" ${webUploadOff() ? 'disabled title="Upload dal browser disattivato (Impostazioni → Libreria locale)"' : ''}>Carica file</button>
          <button class="btn small" type="button" data-act="open">Apri</button>
          ${ign ? `<button class="btn small" type="button" data-act="clean" title="Toglie dalla cartella i ${ign} file che non servono al driver">Pulisci (${ign})</button>` : ''}
          <button class="btn small danger" type="button" data-act="delete">Elimina cartella</button>
        </div>
      </div>
      ${warningsHtml(f)}
      <div class="toggle-row"><div><div class="tt">${esc(FLAGS.winpe_inject.label)}</div><div class="td">${esc(FLAGS.winpe_inject.help)}</div></div>${sw('winpe_inject')}</div>
      <div class="toggle-row"><div><div class="tt">${esc(FLAGS.setup_load.label)}</div><div class="td">${esc(FLAGS.setup_load.help)}</div></div>${sw('setup_load')}</div>
      ${applyHtml(f)}
      <div class="drv-note"><label for="note-${f.name.replace(/\W/g, '_')}">Nota</label><input class="inline-input" id="note-${f.name.replace(/\W/g, '_')}" data-note value="${esc(f.note || '')}" maxlength="200" placeholder="es. Intel I225-V 2.5G, PC dell'aula 2 (salvata quando esci dal campo)" ${f.valid_name ? '' : 'disabled'}></div>
      <div class="uploads drv-uploads" data-uploads="${esc(f.name)}" hidden></div>
      <div class="drv-drop">Trascina qui i file o le sottocartelle del driver (anche uno .zip: viene estratto sul server) per caricarli in questa cartella</div>
    </div>`;
  }

  /* Barra delle azioni su più cartelle: compare quando almeno una scheda è selezionata. */
  function renderBulk() {
    if (!D.root) return;
    const box = $('#drv-bulk', D.root); if (!box) return;
    const n = D.sel.size;
    const folders = (D.data && D.data.folders) || [];
    if (!n) { box.hidden = true; box.innerHTML = ''; return; }
    const names = Array.from(D.sel);
    box.hidden = false;
    box.innerHTML = `<div class="drv-bulkbar" role="group" aria-label="Azioni sulle cartelle selezionate">
      <span class="drv-bulk-n">${plural(n, 'cartella selezionata', 'cartelle selezionate')}</span>
      <span class="drv-bulk-g"><span class="k">${esc(FLAGS.winpe_inject.label)}</span>
        <button class="btn small" type="button" data-bulk="winpe_inject:on">Attiva</button>
        <button class="btn small" type="button" data-bulk="winpe_inject:off">Disattiva</button></span>
      <span class="drv-bulk-g"><span class="k">${esc(FLAGS.setup_load.label)}</span>
        <button class="btn small" type="button" data-bulk="setup_load:on">Attiva</button>
        <button class="btn small" type="button" data-bulk="setup_load:off">Disattiva</button></span>
      <span class="drv-bulk-g"><span class="k">Si applica a</span>
        <button class="btn small" type="button" data-bulk="apply:all" title="Rimette le cartelle scelte su tutte le immagini">Tutte le immagini</button></span>
      <span class="drv-bulk-sp"></span>
      <button class="btn small" type="button" data-bulk="clean">Pulisci i file non usati</button>
      <button class="btn small danger" type="button" data-bulk="delete">Elimina le cartelle selezionate</button>
      ${n < folders.length ? '<button class="btn small" type="button" data-bulk="all">Seleziona tutte</button>' : ''}
      <button class="btn small" type="button" data-bulk="none">Deseleziona</button>
      <div class="drv-bulk-names hint">${esc(names.join(', '))}</div></div>`;
  }

  function render() {
    if (!D.root) return;
    const d = D.data || {}; const folders = d.folders || [];
    const nFiles = folders.reduce((a, f) => a + (f.count || 0), 0);
    const size = folders.reduce((a, f) => a + (f.size || 0), 0);
    const ign = folders.reduce((a, f) => a + (Number(f.ignored_files) || 0), 0);
    $('#drv-sub', D.root).textContent = folders.length
      ? `${folders.length} ${folders.length === 1 ? 'cartella' : 'cartelle'} · ${nFiles} file${ign ? ` (${ign} non usati)` : ''} · ${P.fmtBytes(size)} · ${folders.filter((f) => f.winpe_inject).length} in WinPE · ${folders.filter((f) => f.setup_load).length} prima del setup`
      : 'Nessuna cartella driver';
    const n = $('#n-driver'); if (n) n.textContent = folders.length ? String(folders.length) : '';
    $('#drv-path', D.root).textContent = sharePath();
    $('#drv-share-status', D.root).innerHTML = shareStatus();
    const box = $('#drv-list', D.root);
    if (!folders.length) {
      box.innerHTML = `<div class="empty"><h3>Nessuna cartella driver</h3>
        <p>Il modo più rapido: <b>Carica cartelle</b> (o trascina qui le cartelle dei driver estratti). Pixio crea una cartella per ognuna, tiene le sottocartelle e scarta da solo i file che non sono driver, come l'<span class="mono">.exe</span> di installazione o i file di lingua. In alternativa copia le cartelle da Windows in <span class="mono">${esc(sharePath())}</span>. Poi attiva "${esc(FLAGS.winpe_inject.label)}" per i driver di rete/storage che servono già nel WinPE, o "${esc(FLAGS.setup_load.label)}" per gli altri.</p>
        <div class="actions"><button class="btn primary" type="button" data-act="folders">Carica cartelle</button><button class="btn" type="button" data-act="new">Nuova cartella vuota</button></div></div>`;
    } else {
      box.innerHTML = `<div class="drv-list">${folders.map(folderHtml).join('')}</div>`;
    }
    renderBulk();
    renderQueue();
    renderUploads();
    if (D.openFolder && P.drawer.isOpen()) {
      const f = findFolder(D.openFolder);
      if (f) renderDrawer(f); else P.drawer.close();
    }
  }

  // ---------------------------------------------------------------- azioni
  async function copyPath() {
    const t = sharePath();
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(t);
      else {
        const ta = document.createElement('textarea'); ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
      }
      P.toast('Percorso copiato: ' + t, 'info');
    } catch (e) {
      P.toast('Copia non riuscita: seleziona e copia il percorso a mano', 'warn');
    }
  }

  function newFolderDialog() {
    P.modal({
      title: 'Nuova cartella driver',
      body: `<div class="field"><label for="nf-name">Nome della cartella</label><input id="nf-name" maxlength="64" placeholder="es. Intel I225-V"><div class="hint">Lettere, numeri, spazi e . _ - ( ) + (max 64). Un nome parlante aiuta: modello della scheda o del controller.</div></div>`,
      buttons: [{ label: 'Annulla', value: null }, {
        label: 'Crea', cls: 'primary',
        onClick: async (dlg) => {
          const name = $('#nf-name', dlg).value.trim();
          if (!name) throw new Error('Indica il nome della cartella');
          const r = await P.post('/api/drivers/folders', { name });
          P.toast(`Cartella "${(r && r.folder && r.folder.name) || name}" creata`);
          await load();
          return true;
        },
      }],
    });
  }

  async function setFlag(name, flag, sw) {
    const f = findFolder(name); if (!f) return;
    const want = !P.switchOn(sw);
    D.busy.add(name); sw.disabled = true; sw.classList.add('busy');
    try {
      const r = await P.api('PATCH', folderUrl(name), { [flag]: want });
      if (r && r.folder) Object.assign(f, r.folder); else f[flag] = want;
      P.toast(`"${name}": ${FLAGS[flag].label} ${want ? 'attivato' : 'disattivato'}`);
    } catch (e) { P.fail(e); }
    D.busy.delete(name);
    render();
  }

  /* --- abbinamento: salvataggio ---------------------------------------------------------------- */
  async function saveApply(name, apply_to) {
    const f = findFolder(name); if (!f) return;
    D.busy.add(name); render();
    try {
      const r = await P.api('PATCH', folderUrl(name), { apply_to });
      if (r && r.folder) Object.assign(f, r.folder); else f.apply_to = apply_to;
      P.toast(`"${name}": in uso su ${applySummary(f)}`);
    } catch (e) { P.fail(e); }
    D.busy.delete(name);
    render();
  }
  function setApplyMode(name, mode) {
    const f = findFolder(name); if (!f || APPLY_MODES.indexOf(mode) < 0) return;
    const a = applyOf(f);
    if (a.mode === mode) return;
    saveApply(name, { mode, groups: a.groups, isos: a.isos });
  }
  /* Spunta/toglie un gruppo o una ISO dall'elenco della cartella. */
  function toggleApply(name, kind, value, on) {
    const f = findFolder(name); if (!f) return;
    const a = applyOf(f);
    const list = a[kind].filter((x) => x !== value);
    if (on) list.push(value);
    a[kind] = list;
    saveApply(name, a);
  }

  /* --- esclusione dei singoli file dall'iniezione nel WinPE ------------------------------------- */
  async function setExcluded(name, rel, escluso) {
    const f = findFolder(name); if (!f) return;
    try {
      const r = await P.api('PATCH', fileUrl(name, rel), { excluded: escluso });
      if (r && r.folder) Object.assign(f, r.folder);
      render();
    } catch (e) { P.fail(e); await load(); }
  }
  /* "Escludi tutti" / "Includi tutti": una chiamata per file, solo su quelli che cambiano davvero. */
  async function excludeAll(name, escluso, btn) {
    const f = findFolder(name); if (!f) return;
    const da = candidates(f).filter((x) => !!x.excluded !== escluso).map((x) => x.name);
    if (!da.length) { P.toast(escluso ? 'Sono già esclusi tutti' : 'Non ci sono file esclusi'); return; }
    P.setBusy(btn, true, '…');
    const errs = [];
    for (let i = 0; i < da.length; i++) {
      try { await P.api('PATCH', fileUrl(name, da[i]), { excluded: escluso }); }
      catch (e) { errs.push(`${da[i]} (${(e && e.message) || e})`); }
    }
    P.setBusy(btn, false);
    P.toast(`${plural(da.length - errs.length, 'file', 'file')} ${escluso ? 'esclusi dal' : 'rimessi nel'} WinPE`, errs.length ? 'warn' : 'ok');
    if (errs.length) P.toast('Non riusciti: ' + errs.join('; '), 'bad', 9000);
    await load();
  }

  async function saveNote(name, input) {
    const f = findFolder(name); if (!f) return;
    const note = input.value.trim();
    if (note === (f.note || '')) return;
    try {
      const r = await P.api('PATCH', folderUrl(name), { note });
      if (r && r.folder) Object.assign(f, r.folder); else f.note = note;
      P.toast('Nota salvata');
    } catch (e) { P.fail(e); input.value = f.note || ''; }
  }

  /* Toglie dalla cartella i file che non servono al driver (.exe, documentazione, file di lingua...). */
  async function cleanFolder(name, btn) {
    const f = findFolder(name); if (!f) return;
    const n = Number(f.ignored_files) || 0;
    if (!n) { P.toast(`Nella cartella "${name}" non ci sono file da togliere`); return; }
    const ok = await P.confirm(`Togliere ${plural(n, 'file non usato', 'file non usati')} dalla cartella "${name}"?`, {
      title: 'Pulisci i file non usati', ok: 'Pulisci',
      detail: 'Restano solo i file che servono a installare il driver (.inf, .sys, .cat, .dll, .bin, .dat, .cab). Gli altri vengono cancellati dal disco del server.',
    });
    if (!ok) return;
    P.setBusy(btn, true, 'Pulizia…');
    try {
      const r = await P.api('POST', folderUrl(name) + '/clean');
      P.toast(`Tolti ${plural((r && r.count) || 0, 'file', 'file')} da "${name}"`);
      await reload();
    } finally { P.setBusy(btn, false); }
  }

  /* Stessa pulizia sulle cartelle selezionate. */
  async function cleanSelected(btn) {
    const names = Array.from(D.sel);
    if (!names.length) return;
    const tot = names.reduce((a, n) => a + (Number((findFolder(n) || {}).ignored_files) || 0), 0);
    if (!tot) { P.toast('Nelle cartelle scelte non ci sono file da togliere'); return; }
    const ok = await P.confirm(`Togliere ${plural(tot, 'file non usato', 'file non usati')} da ${plural(names.length, 'cartella', 'cartelle')}?`, {
      title: 'Pulisci i file non usati', ok: 'Pulisci',
      detail: names.join(', ') + '\nRestano solo i file che servono a installare il driver.',
    });
    if (!ok) return;
    P.setBusy(btn, true, 'Pulizia…');
    try {
      const r = await P.api('POST', '/api/drivers/clean', { names });
      P.toast(`Tolti ${plural((r && r.count) || 0, 'file', 'file')} da ${plural(names.length, 'cartella', 'cartelle')}`);
      const err = (r && r.errors) || {};
      Object.keys(err).forEach((k) => P.toast(`${k}: ${err[k]}`, 'warn'));
      await reload();
    } finally { P.setBusy(btn, false); }
  }

  async function deleteFolder(name, btn) {
    const f = findFolder(name); if (!f) return;
    const ok = await P.confirm(`Eliminare la cartella "${name}" e tutto il suo contenuto?`, {
      title: 'Elimina cartella driver', ok: 'Elimina', danger: true,
      detail: `Verranno cancellati ${f.count} file (${P.fmtBytes(f.size)}) dal disco del server. L'operazione non si può annullare.`,
    });
    if (!ok) return;
    P.setBusy(btn, true, 'Eliminazione…');
    try {
      await P.api('DELETE', folderUrl(name));
      P.toast(`Cartella "${name}" eliminata`);
      D.sel.delete(name);
      if (D.openFolder === name) P.drawer.close();
      await load();
    } catch (e) { P.fail(e); P.setBusy(btn, false); }
  }

  async function deleteFile(name, rel, btn) {
    const ok = await P.confirm(`Eliminare il file "${rel}" dalla cartella "${name}"?`, { title: 'Elimina file', ok: 'Elimina', danger: true });
    if (!ok) return;
    P.setBusy(btn, true, '…');
    try {
      await P.api('DELETE', folderUrl(name) + '/files/' + rel.split('/').map(encodeURIComponent).join('/'));
      P.toast('File eliminato');
      await load();
    } catch (e) { P.fail(e); P.setBusy(btn, false); }
  }

  // ---------------------------------------------------------------- azioni su più cartelle
  async function bulkFlag(flag, on) {
    const names = Array.from(D.sel);
    if (!names.length) return;
    try {
      const r = await P.api('PATCH', '/api/drivers/folders', { names, [flag]: on });
      const upd = (r && r.updated) || [];
      const errs = (r && r.errors) || {};
      const bad = Object.keys(errs);
      let msg = `${FLAGS[flag].label}: ${on ? 'attivato' : 'disattivato'} su ${plural(upd.length, 'cartella', 'cartelle')}`;
      if (bad.length) msg += ` · non riuscito su ${bad.map((k) => `${k} (${errs[k]})`).join('; ')}`;
      P.toast(msg, bad.length ? 'warn' : 'ok', bad.length ? 9000 : 4000);
    } catch (e) { P.fail(e); }
    await load();
  }

  async function bulkDelete() {
    const names = Array.from(D.sel);
    if (!names.length) return;
    const list = names.map((n) => findFolder(n)).filter(Boolean);
    const files = list.reduce((a, f) => a + (f.count || 0), 0);
    const size = list.reduce((a, f) => a + (f.size || 0), 0);
    const ok = await P.confirm(`Eliminare ${plural(names.length, 'cartella driver', 'cartelle driver')} e tutto il loro contenuto?`, {
      title: 'Elimina cartelle driver', ok: 'Elimina', danger: true,
      detail: `Verranno eliminate: ${names.join(', ')}. In tutto ${plural(files, 'file', 'file')} (${P.fmtBytes(size)}) dal disco del server. L'operazione non si può annullare.`,
    });
    if (!ok) return;
    const done = []; const errs = [];
    for (let i = 0; i < names.length; i++) {
      try { await P.api('DELETE', folderUrl(names[i])); done.push(names[i]); D.sel.delete(names[i]); }
      catch (e) { errs.push(`${names[i]} (${(e && e.message) || e})`); }
    }
    if (done.length) P.toast(`${plural(done.length, 'cartella eliminata', 'cartelle eliminate')}: ${done.join(', ')}`, errs.length ? 'warn' : 'ok', 7000);
    if (errs.length) P.toast(`Non eliminate: ${errs.join('; ')}`, 'bad', 9000);
    if (D.openFolder && done.indexOf(D.openFolder) >= 0) P.drawer.close();
    await load();
  }

  /* Riporta le cartelle selezionate su "tutte le immagini" (la sola modalità sensata in blocco). */
  async function bulkApplyAll() {
    const names = Array.from(D.sel);
    if (!names.length) return;
    try {
      const r = await P.api('PATCH', '/api/drivers/folders', { names, apply_to: { mode: 'all', groups: [], isos: [] } });
      const upd = (r && r.updated) || [];
      const errs = (r && r.errors) || {};
      const bad = Object.keys(errs);
      let msg = `Si applica a: tutte le immagini su ${plural(upd.length, 'cartella', 'cartelle')}`;
      if (bad.length) msg += ` · non riuscito su ${bad.map((k) => `${k} (${errs[k]})`).join('; ')}`;
      P.toast(msg, bad.length ? 'warn' : 'ok', bad.length ? 9000 : 4000);
    } catch (e) { P.fail(e); }
    await load();
  }

  function onBulk(cmd) {
    if (cmd === 'none') { D.sel.clear(); render(); return; }
    if (cmd === 'all') { ((D.data && D.data.folders) || []).forEach((f) => D.sel.add(f.name)); render(); return; }
    if (cmd === 'clean') { cleanSelected($('[data-bulk="clean"]', D.root)); return; }
    if (cmd === 'delete') { bulkDelete(); return; }
    if (cmd === 'apply:all') { bulkApplyAll(); return; }
    const [flag, val] = cmd.split(':');
    if (FLAGS[flag]) bulkFlag(flag, val === 'on');
  }

  // ---------------------------------------------------------------- pannello file
  function openFolder(name) {
    const f = findFolder(name); if (!f) return;
    D.openFolder = name;
    P.drawer.open(name, '');
    P.drawer.onClose = () => { D.openFolder = null; };
    renderDrawer(f);
  }

  function renderDrawer(f) {
    const files = f.files || [];
    const cand = f.winpe_candidates === undefined ? candidates(f).length : Number(f.winpe_candidates);
    const rows = files.map((x) => {
      // candidato = potrebbe finire nel WinPE; winpe = ci finisce davvero (esclusioni e doppioni a parte)
      const isCand = x.winpe_cand !== undefined ? !!x.winpe_cand : (!x.name.includes('/') && WINPE_RE.test(x.name));
      const inWinpe = x.winpe !== undefined ? !!x.winpe : (isCand && !x.excluded);
      const use = fileUseful(x);
      const box = isCand
        ? `<label class="drv-inc" title="Togli la spunta per lasciare questo file fuori dal WinPE"><input type="checkbox" data-inc="${esc(x.name)}" ${x.excluded ? '' : 'checked'} aria-label="Metti ${esc(x.name)} nel WinPE"></label>`
        : '<span class="hint">—</span>';
      const tag = !isCand ? (WINPE_RE.test(x.name) ? ' <span class="pill neutral" title="Sta in una sottocartella di un\'altra architettura (x86, arm…): nel WinPE a 64 bit non serve">altra architettura</span>' : '')
        : (x.excluded ? ' <span class="pill warn" title="Escluso a mano: non viene iniettato nel WinPE">escluso</span>'
          : (inWinpe ? ' ' + P.pill('WinPE', 'acc')
            : ' <span class="pill neutral" title="Un altro file con lo stesso nome ha la precedenza: nel WinPE i nomi sono tutti nella stessa cartella">doppione</span>'));
      return `<tr class="${use ? '' : 'drv-unused'}${x.excluded ? ' drv-excluded' : ''}"><td class="num">${box}</td><td><span class="mono" style="font-size:12.5px;overflow-wrap:anywhere">${esc(x.name)}</span>${tag}${use ? '' : ' <span class="pill neutral" title="Estensione che non serve a installare il driver: resta nella cartella ma non viene usata">non usato</span>'}</td><td class="num">${P.fmtBytes(x.size)}</td><td class="num hint">${esc(P.fmtDate(x.mtime))}</td><td class="actions-cell"><button class="btn small danger" type="button" data-file="${esc(x.name)}" aria-label="Elimina ${esc(x.name)}">Elimina</button></td></tr>`;
    }).join('');
    const ign = Number(f.ignored_files || 0);
    const use = f.useful_files === undefined ? null : Number(f.useful_files);
    P.drawer.setTitle(f.name);
    P.drawer.body.innerHTML = `
      <div class="drawer-sec">
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">${P.pill(f.count + ' file', 'neutral')}${use === null ? '' : P.pill(use + ' utili', use ? 'acc' : 'neutral')}${ign ? P.pill(ign + ' non usati', 'warn') : ''}${P.pill(P.fmtBytes(f.size), 'neutral')}${P.pill('.inf: ' + f.inf_count, f.inf_count ? 'acc' : 'neutral')}${f.winpe_inject ? P.pill(FLAGS.winpe_inject.label, 'ok') : ''}${f.setup_load ? P.pill(FLAGS.setup_load.label, 'ok') : ''}</div>
        <div class="hint" style="margin-top:8px">Percorso da Windows: <span class="mono">${esc(sharePath())}\\${esc(f.name)}</span>. I file con l'etichetta <b>WinPE</b> sono quelli iniettati all'avvio quando "${esc(FLAGS.winpe_inject.label)}" è attivo; quelli marcati <b>non usato</b> restano sul disco ma non servono a installare il driver.</div>
        <div class="hint" style="margin-top:6px">Si applica a: <b>${esc(applySummary(f))}</b>${applyEmpty(f) ? ' — nessuna immagine riceverà questi driver' : ''}. Si cambia dalla scheda della cartella.</div>
        ${f.note ? `<div class="hint" style="margin-top:6px">Nota: ${esc(f.note)}</div>` : ''}
      </div>
      <div class="drawer-sec">
        ${cand ? `<div class="drv-inc-bar"><div><b>${f.winpe_files} ${f.winpe_files === 1 ? 'file finirà' : 'file finiranno'} nel WinPE</b> su ${plural(cand, 'possibile', 'possibili')}${f.excluded_files ? ` · ${plural(f.excluded_files, 'escluso a mano', 'esclusi a mano')}` : ''} · ${P.fmtBytes(f.winpe_size)}</div>
          <div class="actions"><button class="btn small" type="button" data-dr="none">Escludi tutti</button><button class="btn small" type="button" data-dr="all">Includi tutti</button></div></div>` : ''}
        <div class="actions" style="margin-bottom:10px">${Number(f.ignored_files) ? `<button class="btn small" type="button" data-dr="clean">Pulisci i ${f.ignored_files} file non usati</button>` : ''}<button class="btn small primary" type="button" data-dr="upload" ${webUploadOff() ? 'disabled' : ''}>Carica file</button><span class="hint">${files.length >= 2000 ? 'Elenco limitato ai primi 2000 file.' : ''}</span></div>
        ${files.length ? `<div class="tbl-wrap"><table><thead><tr><th title="Spunta = il file finisce nel WinPE">WinPE</th><th>File</th><th>Dimensione</th><th>Modificato</th><th><span class="sr-only">Azioni</span></th></tr></thead><tbody>${rows}</tbody></table></div>`
        : '<div class="empty"><h3>Cartella vuota</h3><p>Carica qui i file estratti del driver (.inf, .sys, .cat) o uno .zip, oppure copiali dalla share.</p></div>'}
      </div>`;
    P.drawer.body.onclick = (e) => {
      const b = e.target.closest('[data-file],[data-dr]'); if (!b) return;
      if (b.dataset.dr === 'upload') queue.pick(f.name);
      else if (b.dataset.dr === 'clean') cleanFolder(f.name, b);
      else if (b.dataset.dr === 'none') excludeAll(f.name, true, b);
      else if (b.dataset.dr === 'all') excludeAll(f.name, false, b);
      else if (b.dataset.file != null) deleteFile(f.name, b.dataset.file, b);
    };
    P.drawer.body.onchange = (e) => {
      const c = e.target.closest('input[data-inc]'); if (!c) return;
      setExcluded(f.name, c.getAttribute('data-inc'), !c.checked);
    };
  }

  // ---------------------------------------------------------------- pagina
  P.pages.driver = {
    title: 'Driver',
    mount(root) {
      D.root = root;
      D.sel.clear();
      root.innerHTML = `
        <div class="ph"><div><h2>Driver</h2><div class="sub" id="drv-sub">Caricamento…</div></div>
          <div class="actions"><button class="btn primary" type="button" id="drv-folders">Carica cartelle</button><button class="btn" type="button" id="drv-new">Nuova cartella</button></div></div>
        <div class="card drv-intro">
          <p>I PC recenti spesso hanno schede Ethernet (2.5G) o controller storage (NVMe, RAID/VMD) che il WinPE dell'installazione di Windows non riconosce: senza driver di rete il setup non raggiunge la share, senza driver storage non vede il disco. Metti in una cartella i driver <b>estratti</b> (<span class="mono">.inf</span> / <span class="mono">.sys</span> / <span class="mono">.cat</span>, non l'<span class="mono">.exe</span> di installazione) e attiva l'interruttore adatto.</p>
          <p class="hint">Con <b>Carica cartelle</b> (o trascinandole qui sopra) puoi scegliere più cartelle in una volta: Pixio ne crea una per ognuna, mantiene le sottocartelle e lascia fuori i file che non sono driver, dicendoti quanti ne ha saltati.</p>
          <div class="url-box"><div class="k">Share in scrittura per i driver</div>
            <div class="copy-row"><span class="v" id="drv-path">${esc(sharePath())}</span><button class="btn small" type="button" id="drv-copy">Copia</button></div>
            <div class="d" id="drv-share-status"></div></div>
        </div>
        <div id="drv-queue" hidden></div>
        <div id="drv-bulk" hidden></div>
        <div id="drv-list"><div class="loading">Caricamento…</div></div>`;
      $('#drv-new', root).addEventListener('click', newFolderDialog);
      $('#drv-folders', root).addEventListener('click', pickFolders);
      $('#drv-copy', root).addEventListener('click', copyPath);
      root.addEventListener('click', (e) => {
        const b = e.target.closest('[data-act],[data-flag],[data-up],[data-bulk],[data-q],[data-apply]'); if (!b) return;
        if (b.dataset.q === 'close') { queue.batch.closed = true; renderQueue(); return; }
        if (b.dataset.bulk) { onBulk(b.dataset.bulk); return; }
        if (b.dataset.up) {
          const u = queue.byUid(b.dataset.uid); if (!u) return;
          if (b.dataset.up === 'cancel') u.cancel(); else if (b.dataset.up === 'retry') queue.retry(u);
          return;
        }
        const card = b.closest('.drv-card'); const name = card && card.dataset.folder;
        if (b.dataset.apply) { if (name && !b.disabled) setApplyMode(name, b.dataset.apply); return; }
        if (b.dataset.flag) { if (name && !b.disabled) setFlag(name, b.dataset.flag, b); return; }
        const act = b.dataset.act;
        if (act === 'new') newFolderDialog();
        else if (act === 'folders') pickFolders();
        else if (act === 'upload' && name) queue.pick(name);
        else if (act === 'open' && name) openFolder(name);
        else if (act === 'clean' && name) cleanFolder(name, b);
        else if (act === 'delete' && name) deleteFolder(name, b);
      });
      root.addEventListener('change', (e) => {
        const ap = e.target.closest('input[data-apply-group],input[data-apply-iso]');
        if (ap) {
          const card = ap.closest('.drv-card'); if (!card) return;
          const g = ap.getAttribute('data-apply-group');
          toggleApply(card.dataset.folder, g === null ? 'isos' : 'groups',
                      g === null ? ap.getAttribute('data-apply-iso') : g, ap.checked);
          return;
        }
        const pick = e.target.closest('input[data-pick]');
        if (pick) {
          const card = pick.closest('.drv-card'); if (!card) return;
          if (pick.checked) D.sel.add(card.dataset.folder); else D.sel.delete(card.dataset.folder);
          card.classList.toggle('picked', pick.checked);
          renderBulk();
          return;
        }
        const inp = e.target.closest('input[data-note]'); if (!inp) return;
        const card = inp.closest('.drv-card'); if (card) saveNote(card.dataset.folder, inp);
      });
      root.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.target.matches('input[data-note]')) { e.preventDefault(); e.target.blur(); }
      });
      // drag&drop: sulla singola card (finisce in quella cartella) o sulla pagina (una cartella Pixio per cartella trascinata)
      const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
      root.addEventListener('dragenter', (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        const card = e.target.closest('.drv-card');
        if (card) card.classList.add('drag-over'); else root.classList.add('drv-page-drag');
      });
      root.addEventListener('dragover', (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault(); e.dataTransfer.dropEffect = 'copy';
        const card = e.target.closest('.drv-card');
        if (card) { card.classList.add('drag-over'); root.classList.remove('drv-page-drag'); } else root.classList.add('drv-page-drag');
      });
      root.addEventListener('dragleave', (e) => {
        const card = e.target.closest('.drv-card');
        if (card && (!e.relatedTarget || !card.contains(e.relatedTarget))) card.classList.remove('drag-over');
        if (!e.relatedTarget || !root.contains(e.relatedTarget)) root.classList.remove('drv-page-drag');
      });
      root.addEventListener('drop', (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault(); e.stopPropagation();
        root.classList.remove('drv-page-drag');
        const card = e.target.closest('.drv-card');
        if (card) card.classList.remove('drag-over');
        const target = card ? card.dataset.folder : null;
        dropEntries(e.dataTransfer).then((items) => {
          if (!items.length) return;
          const hasDirs = items.some((it) => String(it.path || '').indexOf('/') >= 0);
          if (target && !hasDirs) queue.add(target, items.map((it) => it.file));   // file sciolti su una cartella: come prima
          else startBatch(items, target);
        }).catch((err) => P.fail(err));
      });
      this._onStatus = () => { if (D.root && D.data) { $('#drv-share-status', D.root).innerHTML = shareStatus(); } };
      document.addEventListener('pixio-status', this._onStatus);
      load();
      // aggiornamento periodico (file copiati dalla share), sospeso mentre si scrive una nota
      D.timer = setInterval(() => { if (!document.hidden && !editing()) load(); }, 8000);
    },
    unmount() {
      clearInterval(D.timer); D.timer = null; D.root = null; D.openFolder = null; D.sel.clear();
      document.removeEventListener('pixio-status', this._onStatus);
    },
  };
})();
