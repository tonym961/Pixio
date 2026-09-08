/* Pixio – pagina Driver: cartelle della libreria driver (\\ip\drivers), interruttori "Carica in WinPE all'avvio"
   e "Carica prima del setup di Windows", note, upload a blocchi in sequenza (file singoli o .zip estratti sul
   server, anche con drag&drop sulla card), elenco file con eliminazione. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  const D = { data: null, root: null, timer: null, busy: new Set(), openFolder: null };
  const CHUNK_DEFAULT = 8 * 1024 * 1024;
  const MAX_INJECT = 256 * 1024 * 1024;
  const EXT = ['inf', 'sys', 'cat', 'dll', 'exe', 'cab', 'zip', 'msi', 'txt', 'bin', 'dat', 'ini', 'cfg', 'xml', 'json', '7z'];
  const EXT_RE = new RegExp('\\.(' + EXT.join('|') + ')$', 'i');
  const WINPE_RE = /\.(inf|sys|cat|dll)$/i;
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
  const folderUrl = (name) => '/api/drivers/folders/' + encodeURIComponent(name);
  const findFolder = (name) => ((D.data && D.data.folders) || []).find((f) => f.name === name);
  const sharePath = () => (D.data && D.data.samba_path) || ('\\\\' + ((P.state.status && P.state.status.server_ip) || 'pixio') + '\\drivers');
  const webUploadOff = () => !!(P.state.status && P.state.status.library && P.state.status.library.web_upload_enabled === false);

  // ---------------------------------------------------------------- upload (chunked, riprendibile, in sequenza)
  let uploadSeq = 0;
  class DriverUpload {
    constructor(folder, file) {
      this.uid = ++uploadSeq; this.folder = folder; this.file = file; this.name = file.name; this.size = file.size;
      this.sent = 0; this.status = 'queued'; this.error = null; this.speed = 0; this.eta = null;
      this.ctrl = null; this.id = null; this.result = null;
    }
    chunkLen(n, chunk) { return Math.min(chunk, this.size - n * chunk); }
    async run() {
      this.status = 'init'; this.error = null; this.sent = 0; queue.notify();
      try {
        const init = await P.post('/api/upload/init', { filename: this.name, size: this.size, kind: 'driver', folder: this.folder });
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
        const fin = await P.post(`/api/upload/${encodeURIComponent(this.id)}/finish`);
        this.status = 'done'; this.result = fin || {}; this.sent = this.size; queue.notify();
        if (/\.zip$/i.test(this.name)) P.toast(`${this.name}: estratto ${Number(this.result.extracted) || 0} file in "${this.folder}"`);
        else P.toast(`${this.name} caricato in "${this.folder}"`);
        load();
        setTimeout(() => queue.remove(this), 12000);
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
    add(folder, files) {
      if (webUploadOff()) { P.toast('Upload dal browser disattivato: attivalo in Impostazioni → Libreria locale', 'warn'); return; }
      let added = 0;
      Array.from(files || []).forEach((f) => {
        if (!EXT_RE.test(f.name)) { P.toast(`${f.name}: tipo di file non ammesso (${EXT.join(', ')})`, 'warn', 6000); return; }
        if (!f.size) { P.toast(`${f.name}: file vuoto`, 'warn'); return; }
        if (this.list.some((u) => u.folder === folder && u.name === f.name && u.size === f.size && u.status !== 'error' && u.status !== 'done')) { P.toast(`${f.name} è già in coda`, 'warn'); return; }
        this.list.push(new DriverUpload(folder, f)); added++;
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
    return `<div class="upload" role="group" aria-label="Caricamento ${esc(u.name)}"><div class="row"><span class="name">${esc(u.name)} <span class="hint">${P.fmtBytes(u.size)}</span></span><span class="actions">${btns}</span></div>
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

  // ---------------------------------------------------------------- dati
  async function load() {
    if (!D.root) return;
    try {
      const d = await P.get('/api/drivers');
      if (!D.root) return;
      D.data = d || { folders: [] };
      if (!Array.isArray(D.data.folders)) D.data.folders = [];
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
    if (!f.valid_name) w.push('Nome cartella non valido (ammessi lettere, numeri, spazi, . _ - ( ) +, max 64): rinominala dalla share, altrimenti non viene usata dal setup.');
    return w.map((t) => `<div class="alert warn">${esc(t)}</div>`).join('');
  }

  function folderHtml(f) {
    const busy = D.busy.has(f.name);
    const sw = (flag) => P.switchHtml(!!f[flag], `data-flag="${flag}" aria-label="${esc(FLAGS[flag].label)}: ${esc(f.name)}" title="${esc(FLAGS[flag].help)}" ${busy || !f.valid_name ? 'disabled' : ''}`);
    return `<div class="drv-card" data-folder="${esc(f.name)}">
      <div class="drv-head">
        <div>
          <div class="drv-name"><span>${esc(f.name)}</span>${f.valid_name ? '' : P.pill('nome non valido', 'bad')}${f.winpe_inject ? P.pill('WinPE', 'acc') : ''}${f.setup_load ? P.pill('setup', 'acc') : ''}</div>
          <div class="drv-meta"><span>${f.count} file · ${P.fmtBytes(f.size)}</span>${P.pill('.inf: ' + f.inf_count, f.inf_count ? 'acc' : 'neutral')}<span class="pill neutral" title="File .inf/.sys/.cat/.dll al primo livello (quelli iniettati in WinPE)">WinPE: ${f.winpe_files} file · ${P.fmtBytes(f.winpe_size)}</span></div>
        </div>
        <div class="actions">
          <button class="btn small primary" type="button" data-act="upload" ${webUploadOff() ? 'disabled title="Upload dal browser disattivato (Impostazioni → Libreria locale)"' : ''}>Carica file</button>
          <button class="btn small" type="button" data-act="open">Apri</button>
          <button class="btn small danger" type="button" data-act="delete">Elimina cartella</button>
        </div>
      </div>
      ${warningsHtml(f)}
      <div class="toggle-row"><div><div class="tt">${esc(FLAGS.winpe_inject.label)}</div><div class="td">${esc(FLAGS.winpe_inject.help)}</div></div>${sw('winpe_inject')}</div>
      <div class="toggle-row"><div><div class="tt">${esc(FLAGS.setup_load.label)}</div><div class="td">${esc(FLAGS.setup_load.help)}</div></div>${sw('setup_load')}</div>
      <div class="drv-note"><label for="note-${f.name.replace(/\W/g, '_')}">Nota</label><input class="inline-input" id="note-${f.name.replace(/\W/g, '_')}" data-note value="${esc(f.note || '')}" maxlength="200" placeholder="es. Intel I225-V 2.5G, PC dell'aula 2 (salvata quando esci dal campo)" ${f.valid_name ? '' : 'disabled'}></div>
      <div class="uploads drv-uploads" data-uploads="${esc(f.name)}" hidden></div>
      <div class="drv-drop">Trascina qui i file del driver (anche uno .zip: viene estratto sul server) per caricarli in questa cartella</div>
    </div>`;
  }

  function render() {
    if (!D.root) return;
    const d = D.data || {}; const folders = d.folders || [];
    const nFiles = folders.reduce((a, f) => a + (f.count || 0), 0);
    const size = folders.reduce((a, f) => a + (f.size || 0), 0);
    $('#drv-sub', D.root).textContent = folders.length
      ? `${folders.length} ${folders.length === 1 ? 'cartella' : 'cartelle'} · ${nFiles} file · ${P.fmtBytes(size)} · ${folders.filter((f) => f.winpe_inject).length} in WinPE · ${folders.filter((f) => f.setup_load).length} prima del setup`
      : 'Nessuna cartella driver';
    const n = $('#n-driver'); if (n) n.textContent = folders.length ? String(folders.length) : '';
    $('#drv-path', D.root).textContent = sharePath();
    $('#drv-share-status', D.root).innerHTML = shareStatus();
    const box = $('#drv-list', D.root);
    if (!folders.length) {
      box.innerHTML = `<div class="empty"><h3>Nessuna cartella driver</h3>
        <p>Crea una cartella per ogni driver (es. <b>Intel I225</b>, <b>NVMe RST</b>) e mettici i file <b>estratti</b> del driver: servono <span class="mono">.inf</span>, <span class="mono">.sys</span> e <span class="mono">.cat</span>, non l'<span class="mono">.exe</span> di installazione. Puoi caricarli da qui (anche in uno .zip: viene estratto sul server) oppure copiarli da Windows in <span class="mono">${esc(sharePath())}\\nome-cartella</span>. Poi attiva "${esc(FLAGS.winpe_inject.label)}" per i driver di rete/storage che servono già nel WinPE, o "${esc(FLAGS.setup_load.label)}" per gli altri.</p>
        <button class="btn primary" type="button" data-act="new">Nuova cartella</button></div>`;
    } else {
      box.innerHTML = `<div class="drv-list">${folders.map(folderHtml).join('')}</div>`;
    }
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
    const rows = files.map((x) => {
      const top = !x.name.includes('/');
      const winpe = top && WINPE_RE.test(x.name);
      return `<tr><td><span class="mono" style="font-size:12.5px;overflow-wrap:anywhere">${esc(x.name)}</span>${winpe ? ' ' + P.pill('WinPE', 'acc') : ''}</td><td class="num">${P.fmtBytes(x.size)}</td><td class="num hint">${esc(P.fmtDate(x.mtime))}</td><td class="actions-cell"><button class="btn small danger" type="button" data-file="${esc(x.name)}" aria-label="Elimina ${esc(x.name)}">Elimina</button></td></tr>`;
    }).join('');
    P.drawer.setTitle(f.name);
    P.drawer.body.innerHTML = `
      <div class="drawer-sec">
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">${P.pill(f.count + ' file', 'neutral')}${P.pill(P.fmtBytes(f.size), 'neutral')}${P.pill('.inf: ' + f.inf_count, f.inf_count ? 'acc' : 'neutral')}${f.winpe_inject ? P.pill(FLAGS.winpe_inject.label, 'ok') : ''}${f.setup_load ? P.pill(FLAGS.setup_load.label, 'ok') : ''}</div>
        <div class="hint" style="margin-top:8px">Percorso da Windows: <span class="mono">${esc(sharePath())}\\${esc(f.name)}</span>. I file con l'etichetta <b>WinPE</b> sono quelli al primo livello iniettati all'avvio quando "${esc(FLAGS.winpe_inject.label)}" è attivo.</div>
        ${f.note ? `<div class="hint" style="margin-top:6px">Nota: ${esc(f.note)}</div>` : ''}
      </div>
      <div class="drawer-sec">
        <div class="actions" style="margin-bottom:10px"><button class="btn small primary" type="button" data-dr="upload" ${webUploadOff() ? 'disabled' : ''}>Carica file</button><span class="hint">${files.length >= 2000 ? 'Elenco limitato ai primi 2000 file.' : ''}</span></div>
        ${files.length ? `<div class="tbl-wrap"><table><thead><tr><th>File</th><th>Dimensione</th><th>Modificato</th><th><span class="sr-only">Azioni</span></th></tr></thead><tbody>${rows}</tbody></table></div>`
        : '<div class="empty"><h3>Cartella vuota</h3><p>Carica qui i file estratti del driver (.inf, .sys, .cat) o uno .zip, oppure copiali dalla share.</p></div>'}
      </div>`;
    P.drawer.body.onclick = (e) => {
      const b = e.target.closest('[data-file],[data-dr]'); if (!b) return;
      if (b.dataset.dr === 'upload') queue.pick(f.name);
      else if (b.dataset.file != null) deleteFile(f.name, b.dataset.file, b);
    };
  }

  // ---------------------------------------------------------------- pagina
  P.pages.driver = {
    title: 'Driver',
    mount(root) {
      D.root = root;
      root.innerHTML = `
        <div class="ph"><div><h2>Driver</h2><div class="sub" id="drv-sub">Caricamento…</div></div>
          <div class="actions"><button class="btn primary" type="button" id="drv-new">Nuova cartella</button></div></div>
        <div class="card drv-intro">
          <p>I PC recenti spesso hanno schede Ethernet (2.5G) o controller storage (NVMe, RAID/VMD) che il WinPE dell'installazione di Windows non riconosce: senza driver di rete il setup non raggiunge la share, senza driver storage non vede il disco. Metti in una cartella i driver <b>estratti</b> (<span class="mono">.inf</span> / <span class="mono">.sys</span> / <span class="mono">.cat</span>, non l'<span class="mono">.exe</span> di installazione) e attiva l'interruttore adatto.</p>
          <div class="url-box"><div class="k">Share in scrittura per i driver</div>
            <div class="copy-row"><span class="v" id="drv-path">${esc(sharePath())}</span><button class="btn small" type="button" id="drv-copy">Copia</button></div>
            <div class="d" id="drv-share-status"></div></div>
        </div>
        <div id="drv-list"><div class="loading">Caricamento…</div></div>`;
      $('#drv-new', root).addEventListener('click', newFolderDialog);
      $('#drv-copy', root).addEventListener('click', copyPath);
      root.addEventListener('click', (e) => {
        const b = e.target.closest('[data-act],[data-flag],[data-up]'); if (!b) return;
        if (b.dataset.up) {
          const u = queue.byUid(b.dataset.uid); if (!u) return;
          if (b.dataset.up === 'cancel') u.cancel(); else if (b.dataset.up === 'retry') queue.retry(u);
          return;
        }
        const card = b.closest('.drv-card'); const name = card && card.dataset.folder;
        if (b.dataset.flag) { if (name && !b.disabled) setFlag(name, b.dataset.flag, b); return; }
        const act = b.dataset.act;
        if (act === 'new') newFolderDialog();
        else if (act === 'upload' && name) queue.pick(name);
        else if (act === 'open' && name) openFolder(name);
        else if (act === 'delete' && name) deleteFolder(name, b);
      });
      root.addEventListener('change', (e) => {
        const inp = e.target.closest('input[data-note]'); if (!inp) return;
        const card = inp.closest('.drv-card'); if (card) saveNote(card.dataset.folder, inp);
      });
      root.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.target.matches('input[data-note]')) { e.preventDefault(); e.target.blur(); }
      });
      // drag&drop sulla singola card
      const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
      root.addEventListener('dragenter', (e) => {
        const card = e.target.closest('.drv-card'); if (!card || !hasFiles(e)) return;
        e.preventDefault(); card.classList.add('drag-over');
      });
      root.addEventListener('dragover', (e) => {
        const card = e.target.closest('.drv-card'); if (!card || !hasFiles(e)) return;
        e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; card.classList.add('drag-over');
      });
      root.addEventListener('dragleave', (e) => {
        const card = e.target.closest('.drv-card'); if (!card) return;
        if (!e.relatedTarget || !card.contains(e.relatedTarget)) card.classList.remove('drag-over');
      });
      root.addEventListener('drop', (e) => {
        const card = e.target.closest('.drv-card'); if (!card || !hasFiles(e)) return;
        e.preventDefault(); e.stopPropagation(); card.classList.remove('drag-over');
        queue.add(card.dataset.folder, e.dataTransfer.files);
      });
      this._onStatus = () => { if (D.root && D.data) { $('#drv-share-status', D.root).innerHTML = shareStatus(); } };
      document.addEventListener('pixio-status', this._onStatus);
      load();
      // aggiornamento periodico (file copiati dalla share), sospeso mentre si scrive una nota
      D.timer = setInterval(() => { if (!document.hidden && !editing()) load(); }, 8000);
    },
    unmount() {
      clearInterval(D.timer); D.timer = null; D.root = null; D.openFolder = null;
      document.removeEventListener('pixio-status', this._onStatus);
    },
  };
})();
