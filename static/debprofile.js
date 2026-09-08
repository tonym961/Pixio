/* Pixio – pagina Debian: profili di personalizzazione che generano un preseed.cfg.
   Elenco profili a sinistra, form diviso in sezioni a destra (Sistema e lingua, Rete, Mirror, Disco,
   Account, Pacchetti, Comandi finali), anteprima del preseed in riquadro monospace.
   Pulsanti: Salva, Duplica, Elimina, Salva come risposta. Le password finiscono in chiaro nel preseed:
   l'avviso è sempre visibile in cima alla pagina e ripetuto nella sezione Account. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  // Stato della pagina: meta = elenchi dal server, cur = profilo in modifica (id null = nuovo)
  const D = { root: null, meta: null, list: [], cur: null, dirty: false, previewTimer: null, loadingPreview: false };

  const idUrl = (id) => '/api/debprofiles/' + encodeURIComponent(id);
  const clone = (o) => JSON.parse(JSON.stringify(o == null ? null : o));

  // ---------------------------------------------------------------- caricamento

  async function load(selectId) {
    if (!D.root) return;
    try {
      const d = await P.get('/api/debprofiles');
      D.meta = d;
      D.list = d.profiles || [];
      renderList();
      if (selectId) {
        const p = D.list.find((x) => x.id === selectId);
        if (p) open(p);
      } else if (D.cur && D.cur.id) {
        const p = D.list.find((x) => x.id === D.cur.id);
        if (p) open(p); else showEmpty();
      } else if (!D.cur) {
        showEmpty();
      }
    } catch (e) {
      if (e.status === 401) return;
      const box = $('#dp-list', D.root);
      if (box) box.innerHTML = `<div class="empty"><h3>Profili non disponibili</h3><p>${esc(e.message)}</p></div>`;
    }
  }

  function presets() { return (D.meta && D.meta.presets) || []; }
  function tasks() { return (D.meta && D.meta.tasks) || []; }
  function defaults() { return clone((D.meta && D.meta.defaults) || {}); }

  // ---------------------------------------------------------------- elenco

  function renderList() {
    const box = $('#dp-list', D.root);
    if (!box) return;
    if (!D.list.length) {
      box.innerHTML = '<div class="empty"><h3>Nessun profilo</h3><p>Crea il primo profilo con "Nuovo profilo": puoi partire da un modello già pronto.</p></div>';
      return;
    }
    const sel = D.cur && D.cur.id;
    box.innerHTML = `<ul class="mlist">${D.list.map((p) => {
      const s = p.settings || {};
      const dk = s.disk || {};
      const dettagli = [s.hostname || '—', (dk.recipe || 'atomic'), ((s.tasks || []).length + ' task')].join(' · ');
      return `<li data-id="${esc(p.id)}" class="${p.id === sel ? 'sel' : ''}" style="${p.id === sel ? 'border-color:var(--accent)' : ''}">
        <div style="min-width:0;flex:1">
          <div class="drv-name">${esc(p.name)}</div>
          <div class="hint">${esc(dettagli)}</div>
        </div>
        <button class="btn small" type="button" data-act="apri" data-id="${esc(p.id)}">Apri</button>
      </li>`;
    }).join('')}</ul>`;
  }

  function showEmpty() {
    D.cur = null;
    const box = $('#dp-editor', D.root);
    if (box) {
      box.innerHTML = '<div class="empty"><h3>Nessun profilo selezionato</h3><p>Scegli un profilo dall\'elenco oppure premi "Nuovo profilo" per crearne uno partendo da un modello.</p></div>';
    }
  }

  // ---------------------------------------------------------------- nuovo profilo (modello)

  async function nuovo() {
    const ps = presets();
    const opzioni = ['<option value="">Nessun modello (valori predefiniti)</option>']
      .concat(ps.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`)).join('');
    const m = P.modal({
      title: 'Nuovo profilo Debian',
      body: `<div class="field"><label for="np-name">Nome del profilo</label>
               <input id="np-name" maxlength="64" placeholder="es. Server minimo ufficio">
               <div class="hint">Serve solo a te per riconoscerlo nell'elenco.</div></div>
             <div class="field"><label for="np-preset">Parti da un modello</label>
               <select id="np-preset">${opzioni}</select>
               <div class="hint" id="np-desc">I valori del modello riempiono il form e restano tutti modificabili prima del salvataggio.</div></div>`,
      buttons: [
        { label: 'Annulla', value: null },
        {
          label: 'Continua',
          cls: 'primary',
          onClick: (dlg) => {
            const name = $('#np-name', dlg).value.trim();
            if (!name) { P.toast('Indica un nome per il profilo', 'warn'); return false; }
            return { name, preset: $('#np-preset', dlg).value };
          },
        },
      ],
    });
    const selPreset = $('#np-preset', m.el);
    const desc = $('#np-desc', m.el);
    selPreset.addEventListener('change', () => {
      const p = ps.find((x) => x.id === selPreset.value);
      desc.textContent = p ? p.description
        : 'I valori del modello riempiono il form e restano tutti modificabili prima del salvataggio.';
    });
    const r = await m.done;
    if (!r || !r.name) return;
    const p = ps.find((x) => x.id === r.preset);
    D.cur = {
      id: null,
      name: r.name,
      note: '',
      preset: p ? p.id : '',
      settings: merge(defaults(), (p && p.settings) || {}),
    };
    renderList();
    renderEditor();
    P.toast(p ? `Modello "${p.name}" caricato: controlla i campi e salva` : 'Nuovo profilo: compila i campi e salva');
  }

  // merge ricorsivo (stessa logica di storage.deep_merge lato server)
  function merge(base, over) {
    const out = Object.assign({}, base);
    Object.keys(over || {}).forEach((k) => {
      const v = over[k];
      if (v && typeof v === 'object' && !Array.isArray(v) && out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])) {
        out[k] = merge(out[k], v);
      } else {
        out[k] = Array.isArray(v) ? v.slice() : v;
      }
    });
    return out;
  }

  function open(p) {
    D.cur = { id: p.id, name: p.name, note: p.note || '', preset: '', settings: merge(defaults(), p.settings || {}) };
    renderList();
    renderEditor();
  }

  // ---------------------------------------------------------------- form

  function campo(id, label, value, opts) {
    opts = opts || {};
    const tipo = opts.type || 'text';
    const attr = [
      `id="${id}"`, `type="${tipo}"`, `value="${esc(value == null ? '' : value)}"`,
      opts.placeholder ? `placeholder="${esc(opts.placeholder)}"` : '',
      opts.maxlength ? `maxlength="${opts.maxlength}"` : '',
      opts.min !== undefined ? `min="${opts.min}"` : '',
      opts.max !== undefined ? `max="${opts.max}"` : '',
      opts.mono ? 'class="mono"' : '',
      opts.autocomplete ? `autocomplete="${opts.autocomplete}"` : '',
    ].filter(Boolean).join(' ');
    return `<div class="field"><label for="${id}">${esc(label)}</label><input ${attr}>${opts.hint ? `<div class="hint">${opts.hint}</div>` : ''}</div>`;
  }

  function tendina(id, label, value, opzioni, hint) {
    return `<div class="field"><label for="${id}">${esc(label)}</label><select id="${id}">${opzioni.map((o) => {
      const v = typeof o === 'string' ? o : o.id;
      const n = typeof o === 'string' ? o : o.name;
      return `<option value="${esc(v)}" ${String(v) === String(value) ? 'selected' : ''}>${esc(n)}</option>`;
    }).join('')}</select>${hint ? `<div class="hint">${hint}</div>` : ''}</div>`;
  }

  function spunta(id, label, on, hint) {
    return `<div class="field check"><input type="checkbox" id="${id}" ${on ? 'checked' : ''}><label for="${id}">${esc(label)}</label></div>${hint ? `<div class="hint" style="margin:-4px 0 10px 26px">${hint}</div>` : ''}`;
  }

  function area(id, label, value, hint, rows) {
    return `<div class="field"><label for="${id}">${esc(label)}</label><textarea id="${id}" class="mono" rows="${rows || 4}">${esc(value || '')}</textarea>${hint ? `<div class="hint">${hint}</div>` : ''}</div>`;
  }

  function renderEditor() {
    const box = $('#dp-editor', D.root);
    if (!box || !D.cur) return;
    const s = D.cur.settings;
    const meta = D.meta || {};
    const net = s.network || {}; const mir = s.mirror || {}; const dk = s.disk || {};
    const root = s.root || {}; const usr = s.user || {};
    const statico = net.mode === 'static';
    const listaTask = tasks();
    const nuovoProfilo = !D.cur.id;

    box.innerHTML = `
      <div class="ph" style="margin-bottom:12px">
        <div>
          <h3 style="font-size:16px">${esc(D.cur.name)}${nuovoProfilo ? ' <span class="pill acc">non salvato</span>' : ''}</h3>
          <div class="hint">${nuovoProfilo ? 'Profilo nuovo: premi Salva per crearlo.' : 'Profilo <span class="mono">' + esc(D.cur.id) + '</span>'}</div>
        </div>
        <div class="actions">
          <button class="btn primary" type="button" data-act="salva">Salva</button>
          <button class="btn" type="button" data-act="duplica" ${nuovoProfilo ? 'disabled' : ''}>Duplica</button>
          <button class="btn danger" type="button" data-act="elimina" ${nuovoProfilo ? 'disabled' : ''}>Elimina</button>
        </div>
      </div>

      <div class="row2">
        ${campo('dp-name', 'Nome del profilo', D.cur.name, { maxlength: 64 })}
        ${campo('dp-note', 'Nota', D.cur.note, { maxlength: 200, placeholder: 'facoltativa' })}
      </div>

      <div class="card"><h3>Sistema e lingua</h3>
        <div class="row2">
          ${campo('dp-hostname', 'Nome host', s.hostname, { maxlength: 63, placeholder: 'srv-debian', hint: 'Lettere, cifre e trattino (RFC 1123), senza trattino iniziale o finale.' })}
          ${campo('dp-domain', 'Dominio', s.domain, { maxlength: 253, placeholder: 'azienda.local (facoltativo)' })}
        </div>
        <div class="row3">
          ${tendina('dp-locale', 'Lingua (locale)', s.locale, meta.locales || ['it_IT.UTF-8'])}
          ${tendina('dp-keyboard', 'Tastiera', s.keyboard, meta.keyboards || ['it'])}
          ${tendina('dp-timezone', 'Fuso orario', s.timezone, meta.timezones || ['Europe/Rome'])}
        </div>
        ${campo('dp-suite', 'Versione di Debian (suite)', s.suite, { maxlength: 32, mono: true, placeholder: 'stable', hint: 'es. <span class="mono">stable</span>, <span class="mono">trixie</span>, <span class="mono">bookworm</span>. Deve corrispondere alla ISO usata per l\'avvio.' })}
      </div>

      <div class="card"><h3>Rete</h3>
        ${tendina('dp-net-mode', 'Indirizzo', net.mode || 'dhcp', [{ id: 'dhcp', name: 'Automatico (DHCP)' }, { id: 'static', name: 'Fisso (statico)' }], 'Con DHCP il PC prende l\'indirizzo dalla rete; con indirizzo fisso compila tutti i campi qui sotto.')}
        <div id="dp-static" ${statico ? '' : 'hidden'}>
          <div class="row2">
            ${campo('dp-ip', 'Indirizzo IP', net.ip, { mono: true, placeholder: '192.168.1.10' })}
            ${campo('dp-netmask', 'Maschera di rete', net.netmask, { mono: true, placeholder: '255.255.255.0' })}
          </div>
          <div class="row2">
            ${campo('dp-gateway', 'Gateway', net.gateway, { mono: true, placeholder: '192.168.1.1' })}
            ${campo('dp-dns', 'DNS', net.dns, { mono: true, placeholder: '192.168.1.1 1.1.1.1', hint: 'Uno o più indirizzi separati da spazio (max 3).' })}
          </div>
        </div>
      </div>

      <div class="card"><h3>Mirror</h3>
        <div class="row2">
          ${campo('dp-mirror-host', 'Host del mirror', mir.host, { mono: true, placeholder: 'deb.debian.org' })}
          ${campo('dp-mirror-dir', 'Cartella', mir.directory, { mono: true, placeholder: '/debian' })}
        </div>
        <div class="field"><label for="dp-mirror-sugg">Mirror suggeriti</label>
          <select id="dp-mirror-sugg"><option value="">Scegli per compilare i campi…</option>${(meta.mirrors || []).map((m) => `<option value="${esc(m.host)}|${esc(m.directory)}">${esc(m.name)}</option>`).join('')}</select></div>
        ${campo('dp-proxy', 'Proxy HTTP per apt', mir.proxy, { mono: true, placeholder: 'http://proxy.azienda.local:3128 (facoltativo)' })}
      </div>

      <div class="card"><h3>Disco</h3>
        <div class="row2">
          ${campo('dp-disk-dev', 'Disco', dk.device, { mono: true, placeholder: 'auto', hint: 'Scrivi <span class="mono">auto</span> per il primo disco trovato, oppure <span class="mono">/dev/sda</span>, <span class="mono">/dev/nvme0n1</span>, <span class="mono">/dev/vda</span>. Il disco intero, non una partizione.' })}
          ${tendina('dp-disk-recipe', 'Partizionamento', dk.recipe, meta.recipes || [{ id: 'atomic', name: 'Tutto in una partizione' }])}
        </div>
        <div class="row2">
          ${tendina('dp-disk-fs', 'File system', dk.filesystem, meta.filesystems || ['ext4'])}
          ${campo('dp-disk-swap', 'Swap (MB)', dk.swap_mb, { type: 'number', min: 0, max: 131072, hint: '0 = decide l\'installatore. Con un valore diverso da 0 Pixio genera una ricetta di partizionamento esplicita.' })}
        </div>
        <div class="field" id="dp-crypto-box" ${dk.recipe === 'crypto' ? '' : 'hidden'}>
          <label for="dp-crypto">Passphrase del disco cifrato (LUKS)</label>
          <input id="dp-crypto" type="password" autocomplete="new-password" value="${esc(dk.crypto_password || '')}">
          <div class="hint">Almeno 8 caratteri. Va digitata a ogni avvio del PC: senza, il sistema non parte.</div>
        </div>
        ${spunta('dp-disk-wipe', 'Cancella il disco senza chiedere conferma', dk.wipe !== false, 'Elimina LVM, RAID e tabella delle partizioni esistenti. <strong>Tutti i dati sul disco vanno persi.</strong> Se lo disattivi e il disco non è vuoto, l\'installazione si ferma e chiede.')}
      </div>

      <div class="card"><h3>Account</h3>
        <div class="alert warn">Le password inserite qui finiscono <strong>in chiaro</strong> dentro il file preseed.cfg, che i PC scaricano via HTTP senza autenticazione. Usa password dedicate all'installazione e cambiale dopo, oppure incolla un hash già pronto (<span class="mono">mkpasswd -m yescrypt</span>).</div>
        ${spunta('dp-root-on', 'Attiva l\'accesso root', !!root.enabled, 'Se lo lasci spento si amministra il sistema con sudo dall\'utente qui sotto: è la scelta consigliata.')}
        <div class="field" id="dp-root-box" ${root.enabled ? '' : 'hidden'}>
          <label for="dp-root-pw">Password di root</label>
          <input id="dp-root-pw" type="password" autocomplete="new-password" value="${esc(root.password || '')}">
        </div>
        <div class="row2">
          ${campo('dp-user-name', 'Nome utente', usr.username, { maxlength: 32, mono: true, placeholder: 'admin', hint: 'Minuscole, cifre, trattino e underscore. Lascia vuoto per non creare nessun utente (serve root attivo).' })}
          ${campo('dp-user-full', 'Nome completo', usr.fullname, { maxlength: 100, placeholder: 'Amministratore' })}
        </div>
        <div class="field"><label for="dp-user-pw">Password dell'utente</label><input id="dp-user-pw" type="password" autocomplete="new-password" value="${esc(usr.password || '')}"></div>
        ${spunta('dp-user-sudo', 'L\'utente può amministrare il sistema (gruppo sudo)', usr.sudo !== false)}
      </div>

      <div class="card"><h3>Pacchetti</h3>
        <div class="field"><span class="field-label">Gruppi di pacchetti (tasksel)</span>
          <div class="checks" id="dp-tasks">${listaTask.map((t) => `<label><input type="checkbox" data-task="${esc(t.id)}" ${(s.tasks || []).indexOf(t.id) >= 0 ? 'checked' : ''}> ${esc(t.name)}</label>`).join('')}</div>
          <div class="hint">Sono i gruppi proposti dall'installatore Debian. Per un server bastano <span class="mono">standard</span> e <span class="mono">ssh-server</span>.</div>
        </div>
        ${area('dp-packages', 'Pacchetti aggiuntivi', (s.packages || []).join(' '), 'Nomi separati da spazio o a capo, es. <span class="mono">sudo vim curl htop</span>. Massimo 100.', 3)}
        <div class="row2">
          ${campo('dp-grub', 'Disco per GRUB', s.grub_device, { mono: true, placeholder: 'default', hint: '<span class="mono">default</span> va bene quasi sempre (ESP in UEFI, MBR del primo disco in BIOS).' })}
          <div></div>
        </div>
        ${spunta('dp-popcon', 'Partecipa alle statistiche d\'uso (popularity-contest)', !!s.popcon, 'Invia a Debian l\'elenco anonimo dei pacchetti installati. In azienda di solito si lascia spento.')}
      </div>

      <div class="card"><h3>Comandi finali</h3>
        ${area('dp-ssh', 'Chiavi SSH pubbliche', (s.ssh_keys || []).join('\n'), 'Una chiave per riga (<span class="mono">ssh-ed25519 AAAA… utente@pc</span>). Finiscono in <span class="mono">authorized_keys</span> dell\'utente creato, o di root se non c\'è.', 4)}
        ${area('dp-late', 'Comandi eseguiti a fine installazione', s.late_command, 'Un comando per riga, eseguito dentro il sistema appena installato (<span class="mono">in-target</span>). Niente barra rovesciata.', 4)}
        ${spunta('dp-reboot', 'Riavvia da solo alla fine, senza chiedere conferma', s.reboot_after !== false)}
      </div>

      <div class="panel" style="margin-top:16px">
        <div class="hd"><h3>Anteprima di preseed.cfg</h3>
          <div class="actions">
            <button class="btn small" type="button" data-act="anteprima">Aggiorna</button>
            <button class="btn small primary" type="button" data-act="risposta" ${nuovoProfilo ? 'disabled' : ''}>Salva come risposta</button>
          </div></div>
        <div class="bd"><pre class="code" id="dp-preview" style="max-height:460px">Premi "Aggiorna" per generare l'anteprima.</pre></div>
      </div>`;

    // reazioni ai campi che mostrano/nascondono altri campi
    $('#dp-net-mode', box).addEventListener('change', (e) => {
      $('#dp-static', box).hidden = e.target.value !== 'static';
    });
    $('#dp-disk-recipe', box).addEventListener('change', (e) => {
      $('#dp-crypto-box', box).hidden = e.target.value !== 'crypto';
    });
    $('#dp-root-on', box).addEventListener('change', (e) => {
      $('#dp-root-box', box).hidden = !e.target.checked;
    });
    $('#dp-mirror-sugg', box).addEventListener('change', (e) => {
      if (!e.target.value) return;
      const parti = e.target.value.split('|');
      $('#dp-mirror-host', box).value = parti[0];
      $('#dp-mirror-dir', box).value = parti[1] || '/debian';
      e.target.value = '';
    });
    box.addEventListener('input', () => { D.dirty = true; });
    anteprima(true);
  }

  // ---------------------------------------------------------------- lettura del form

  function v(id) { const el = $('#' + id, D.root); return el ? el.value.trim() : ''; }
  function b(id) { const el = $('#' + id, D.root); return !!(el && el.checked); }
  function righe(id) {
    const el = $('#' + id, D.root);
    if (!el) return [];
    return el.value.split('\n').map((x) => x.trim()).filter(Boolean);
  }

  function leggiForm() {
    const s = clone(D.cur.settings) || {};
    s.hostname = v('dp-hostname');
    s.domain = v('dp-domain');
    s.locale = v('dp-locale');
    s.keyboard = v('dp-keyboard');
    s.timezone = v('dp-timezone');
    s.suite = v('dp-suite');
    s.mirror = { host: v('dp-mirror-host'), directory: v('dp-mirror-dir'), proxy: v('dp-proxy') };
    s.network = {
      mode: v('dp-net-mode'),
      ip: v('dp-ip'), netmask: v('dp-netmask'), gateway: v('dp-gateway'), dns: v('dp-dns'),
    };
    s.root = { enabled: b('dp-root-on'), password: v('dp-root-pw') };
    s.user = {
      username: v('dp-user-name'), fullname: v('dp-user-full'),
      password: v('dp-user-pw'), sudo: b('dp-user-sudo'),
    };
    s.disk = {
      device: v('dp-disk-dev') || 'auto',
      recipe: v('dp-disk-recipe'),
      filesystem: v('dp-disk-fs'),
      swap_mb: Number(v('dp-disk-swap')) || 0,
      wipe: b('dp-disk-wipe'),
      crypto_password: v('dp-crypto'),
    };
    s.tasks = $$('#dp-tasks input[type=checkbox]', D.root).filter((x) => x.checked).map((x) => x.dataset.task);
    s.packages = (v('dp-packages') || '').split(/[\s,;]+/).filter(Boolean);
    s.popcon = b('dp-popcon');
    s.grub_device = v('dp-grub') || 'default';
    s.ssh_keys = righe('dp-ssh');
    s.late_command = righe('dp-late').join('\n');
    s.reboot_after = b('dp-reboot');
    return s;
  }

  // ---------------------------------------------------------------- azioni

  async function anteprima(silenziosa) {
    const pre = $('#dp-preview', D.root);
    if (!pre || !D.cur || D.loadingPreview) return;
    D.loadingPreview = true;
    const settings = leggiForm();
    try {
      const r = D.cur.id
        ? await P.post(idUrl(D.cur.id) + '/preview', { settings })
        : await P.post('/api/debprofiles/preview', { name: v('dp-name') || D.cur.name, preset: D.cur.preset || '', settings });
      pre.textContent = r.preseed || '';
    } catch (e) {
      pre.textContent = 'Anteprima non disponibile: ' + e.message;
      if (!silenziosa) P.fail(e);
    } finally {
      D.loadingPreview = false;
    }
  }

  async function salva(btn) {
    if (!D.cur) return;
    const name = v('dp-name') || D.cur.name;
    if (!name) { P.toast('Indica un nome per il profilo', 'warn'); return; }
    const body = { name, note: v('dp-note'), settings: leggiForm() };
    P.setBusy(btn, true, 'Salvataggio…');
    try {
      let p;
      if (D.cur.id) {
        p = await P.api('PUT', idUrl(D.cur.id), body);
        P.toast('Profilo salvato');
      } else {
        if (D.cur.preset) body.preset = D.cur.preset;
        p = await P.post('/api/debprofiles', body);
        P.toast(`Profilo "${p.name}" creato`);
      }
      D.dirty = false;
      await load(p.id);
    } catch (e) {
      P.fail(e);
    } finally {
      P.setBusy(btn, false);
    }
  }

  async function duplica() {
    if (!D.cur || !D.cur.id) return;
    const m = P.modal({
      title: 'Duplica profilo',
      body: `<div class="field"><label for="dup-name">Nome della copia</label><input id="dup-name" maxlength="64" value="${esc(D.cur.name + ' (copia)')}"></div>`,
      buttons: [{ label: 'Annulla', value: null }, { label: 'Duplica', cls: 'primary', onClick: (dlg) => ({ name: $('#dup-name', dlg).value.trim() }) }],
    });
    const r = await m.done;
    if (!r || !r.name) return;
    try {
      const p = await P.post(idUrl(D.cur.id) + '/duplicate', { name: r.name });
      P.toast(`Creata la copia "${p.name}"`);
      await load(p.id);
    } catch (e) { P.fail(e); }
  }

  async function elimina() {
    if (!D.cur || !D.cur.id) return;
    const ok = await P.confirm(`Eliminare il profilo "${D.cur.name}"?`, {
      ok: 'Elimina', danger: true,
      detail: 'Le risposte già generate da questo profilo restano dove sono: vanno eliminate a parte.',
    });
    if (!ok) return;
    try {
      await P.api('DELETE', idUrl(D.cur.id));
      P.toast('Profilo eliminato');
      D.cur = null;
      await load();
      showEmpty();
    } catch (e) { P.fail(e); }
  }

  async function salvaRisposta(btn) {
    if (!D.cur || !D.cur.id) return;
    if (D.dirty) {
      const ok = await P.confirm('Ci sono modifiche non salvate.', {
        ok: 'Salva e continua', title: 'Salva prima il profilo',
        detail: 'La risposta viene generata dal profilo salvato sul server, non da quello che vedi a schermo.',
      });
      if (!ok) return;
      await salva($('[data-act="salva"]', D.root));
      if (D.dirty) return;
    }
    P.setBusy(btn, true, 'Generazione…');
    try {
      const r = await P.post(idUrl(D.cur.id) + '/save-answer', {});
      P.toast(`Risposta "${r.answer_name}" salvata: associala a una ISO Debian dal catalogo`, 'info', 8000);
      await P.modal({
        title: 'Risposta creata',
        body: `<p>Il preseed è stato salvato nella risposta <strong>${esc(r.answer_name)}</strong> (<span class="mono">${esc(r.answer_id)}</span>), file principale <span class="mono">preseed.cfg</span>.</p>
               <div class="alert warn">Il file è servito ai PC senza autenticazione: le password che hai inserito sono leggibili da chiunque sia collegato alla rete.</div>
               <p class="hint">Per usarla: apri il catalogo ISO, scegli l'immagine Debian e associale questa risposta. Pixio aggiungerà da solo <span class="mono">auto=true priority=critical url=…</span> alla riga di avvio.</p>`,
        buttons: [{ label: 'Ho capito', cls: 'primary', value: true }],
      }).done;
    } catch (e) {
      P.fail(e);
    } finally {
      P.setBusy(btn, false);
    }
  }

  // ---------------------------------------------------------------- pagina

  P.pages.debian = {
    title: 'Debian',
    mount(root) {
      D.root = root;
      D.cur = null;
      D.dirty = false;
      root.innerHTML = `
        <div class="ph">
          <div><h2>Debian</h2><div class="sub">Profili di installazione automatica: generano un file <span class="mono">preseed.cfg</span> da usare come risposta.</div></div>
          <div class="actions"><button class="btn" type="button" id="dp-reload">Aggiorna</button><button class="btn primary" type="button" id="dp-new">Nuovo profilo</button></div>
        </div>
        <div class="alert warn">Le password (root, utente e disco cifrato) finiscono <strong>in chiaro</strong> nel file di risposta, che i PC scaricano via HTTP senza autenticazione. È il funzionamento previsto dall'installatore Debian: usa password dedicate all'installazione e cambiale subito dopo.</div>
        <div class="menu-grid" style="grid-template-columns:minmax(260px,1fr) minmax(0,2.6fr)">
          <div class="panel"><div class="hd"><h3>Profili</h3></div><div class="bd" id="dp-list"><div class="loading">Caricamento…</div></div></div>
          <div id="dp-editor"><div class="loading">Caricamento…</div></div>
        </div>`;
      $('#dp-reload', root).addEventListener('click', () => load(D.cur && D.cur.id));
      $('#dp-new', root).addEventListener('click', () => nuovo());
      root.addEventListener('click', (e) => {
        const li = e.target.closest('li[data-id]');
        const btn = e.target.closest('button[data-act]');
        if (btn) {
          const act = btn.dataset.act;
          if (act === 'apri') {
            const p = D.list.find((x) => x.id === btn.dataset.id);
            if (p) open(p);
            return;
          }
          if (act === 'salva') { salva(btn); return; }
          if (act === 'duplica') { duplica(); return; }
          if (act === 'elimina') { elimina(); return; }
          if (act === 'anteprima') { anteprima(); return; }
          if (act === 'risposta') { salvaRisposta(btn); return; }
          return;
        }
        if (li) {
          const p = D.list.find((x) => x.id === li.dataset.id);
          if (p) open(p);
        }
      });
      load();
    },
    unmount() {
      clearTimeout(D.previewTimer);
      D.previewTimer = null;
      D.root = null;
      D.cur = null;
      D.meta = null;
      D.list = [];
    },
  };
})();
