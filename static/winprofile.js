/* Pixio – pagina Windows: profili di personalizzazione che generano un autounattend.xml.
   Elenco profili a sinistra, form diviso in sezioni a destra (Lingua e area, Account, Disco,
   Windows 11, App e comandi, Driver), anteprima dell'XML in un riquadro monospace.
   Pulsanti: Salva, Duplica, Elimina, Salva come risposta. Le password finiscono in chiaro
   dentro il file: l'avviso è fisso in cima alla pagina e ripetuto nella sezione Account. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  // Stato della pagina: meta = elenchi dal server, cur = profilo in modifica (id null = nuovo)
  const W = { root: null, meta: null, list: [], cur: null, dirty: false, loadingPreview: false };

  const idUrl = (id) => '/api/winprofiles/' + encodeURIComponent(id);
  const clone = (o) => JSON.parse(JSON.stringify(o == null ? null : o));

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

  // ---------------------------------------------------------------- caricamento

  async function load(selectId) {
    if (!W.root) return;
    try {
      const d = await P.get('/api/winprofiles');
      W.meta = d;
      W.list = d.profiles || [];
      renderList();
      if (selectId) {
        const p = W.list.find((x) => x.id === selectId);
        if (p) open(p);
      } else if (W.cur && W.cur.id) {
        const p = W.list.find((x) => x.id === W.cur.id);
        if (p) open(p); else showEmpty();
      } else if (!W.cur) {
        showEmpty();
      }
    } catch (e) {
      if (e.status === 401) return;
      const box = $('#wp-list', W.root);
      if (box) box.innerHTML = `<div class="empty"><h3>Profili non disponibili</h3><p>${esc(e.message)}</p></div>`;
    }
  }

  function presets() { return (W.meta && W.meta.presets) || []; }
  function apps() { return (W.meta && W.meta.apps) || []; }
  function defaults() { return clone((W.meta && W.meta.defaults) || {}); }
  function serverIp() { return (W.meta && W.meta.server_ip) || (P.state.status && P.state.status.server_ip) || '<ip>'; }

  // ---------------------------------------------------------------- elenco

  function renderList() {
    const box = $('#wp-list', W.root);
    if (!box) return;
    if (!W.list.length) {
      box.innerHTML = '<div class="empty"><h3>Nessun profilo</h3><p>Crea il primo profilo con "Nuovo profilo": puoi partire da un modello già pronto.</p></div>';
      return;
    }
    const sel = W.cur && W.cur.id;
    box.innerHTML = `<ul class="mlist">${W.list.map((p) => {
      const s = p.settings || {};
      const dk = s.disk || {};
      const dettagli = [s.computer_name || '—', dk.mode || 'auto-uefi', s.language || 'it-IT'].join(' · ');
      return `<li data-id="${esc(p.id)}" class="${p.id === sel ? 'sel' : ''}" style="${p.id === sel ? 'border-color:var(--accent)' : ''}">
        <div style="min-width:0;flex:1">
          <div class="drv-name">${esc(p.name)}${s.bypass_requirements ? ' <span class="pill warn">requisiti aggirati</span>' : ''}</div>
          <div class="hint">${esc(dettagli)}</div>
        </div>
        <button class="btn small" type="button" data-act="apri" data-id="${esc(p.id)}">Apri</button>
      </li>`;
    }).join('')}</ul>`;
  }

  function showEmpty() {
    W.cur = null;
    const box = $('#wp-editor', W.root);
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
      title: 'Nuovo profilo Windows',
      body: `<div class="field"><label for="wn-name">Nome del profilo</label>
               <input id="wn-name" maxlength="64" placeholder="es. Postazione ufficio Windows 11">
               <div class="hint">Serve solo a te per riconoscerlo nell'elenco.</div></div>
             <div class="field"><label for="wn-preset">Parti da un modello</label>
               <select id="wn-preset">${opzioni}</select>
               <div class="hint" id="wn-desc">I valori del modello riempiono il form e restano tutti modificabili prima del salvataggio.</div></div>`,
      buttons: [
        { label: 'Annulla', value: null },
        {
          label: 'Continua',
          cls: 'primary',
          onClick: (dlg) => {
            const name = $('#wn-name', dlg).value.trim();
            if (!name) { P.toast('Indica un nome per il profilo', 'warn'); return false; }
            return { name, preset: $('#wn-preset', dlg).value };
          },
        },
      ],
    });
    const selPreset = $('#wn-preset', m.el);
    const desc = $('#wn-desc', m.el);
    selPreset.addEventListener('change', () => {
      const p = ps.find((x) => x.id === selPreset.value);
      desc.textContent = p ? p.description
        : 'I valori del modello riempiono il form e restano tutti modificabili prima del salvataggio.';
    });
    const r = await m.done;
    if (!r || !r.name) return;
    const p = ps.find((x) => x.id === r.preset);
    W.cur = {
      id: null, name: r.name, note: '', preset: p ? p.id : '',
      settings: merge(defaults(), (p && p.settings) || {}),
    };
    W.dirty = true;
    renderList();
    renderEditor();
    P.toast(p ? `Modello "${p.name}" caricato: controlla i campi e salva` : 'Nuovo profilo: compila i campi e salva');
  }

  function open(p) {
    W.cur = { id: p.id, name: p.name, note: p.note || '', preset: '', settings: merge(defaults(), p.settings || {}) };
    W.dirty = false;
    renderList();
    renderEditor();
  }

  // ---------------------------------------------------------------- pezzi di form

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

  function password(id, label, value, hint) {
    return `<div class="field"><label for="${id}">${esc(label)}</label>
      <input id="${id}" type="password" autocomplete="new-password" value="${esc(value || '')}">
      ${hint ? `<div class="hint">${hint}</div>` : ''}</div>`;
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

  // ---------------------------------------------------------------- form

  function renderEditor() {
    const box = $('#wp-editor', W.root);
    if (!box || !W.cur) return;
    const s = W.cur.settings;
    const meta = W.meta || {};
    const dk = s.disk || {}; const eu = s.extra_user || {}; const jd = s.join_domain || {};
    const uefi = (dk.mode || 'auto-uefi') === 'auto-uefi';
    const noti = apps().map((a) => a.id);
    const altre = (s.remove_apps || []).filter((a) => noti.indexOf(a) < 0);
    const nuovoProfilo = !W.cur.id;

    box.innerHTML = `
      <div class="ph" style="margin-bottom:12px">
        <div>
          <h3 style="font-size:16px">${esc(W.cur.name)}${nuovoProfilo ? ' <span class="pill acc">non salvato</span>' : ''}</h3>
          <div class="hint">${nuovoProfilo ? 'Profilo nuovo: premi Salva per crearlo.' : 'Profilo <span class="mono">' + esc(W.cur.id) + '</span>'}</div>
        </div>
        <div class="actions">
          <button class="btn primary" type="button" data-act="salva">Salva</button>
          <button class="btn" type="button" data-act="duplica" ${nuovoProfilo ? 'disabled' : ''}>Duplica</button>
          <button class="btn danger" type="button" data-act="elimina" ${nuovoProfilo ? 'disabled' : ''}>Elimina</button>
        </div>
      </div>

      <div class="row2">
        ${campo('wp-name', 'Nome del profilo', W.cur.name, { maxlength: 64 })}
        ${campo('wp-note', 'Nota', W.cur.note, { maxlength: 200, placeholder: 'facoltativa' })}
      </div>

      <div class="card"><h3>Lingua e area</h3>
        <div class="row3">
          ${tendina('wp-language', 'Lingua di Windows', s.language, meta.languages || [{ id: 'it-IT', name: 'Italiano (Italia)' }], 'Vale per il setup e per il sistema installato. L\'immagine deve contenere questa lingua.')}
          ${campo('wp-input', 'Tastiera', s.input_locale, { mono: true, maxlength: 64, placeholder: 'it-IT', hint: 'Sigla (<span class="mono">it-IT</span>) o identificativo (<span class="mono">0410:00000410</span>). Più layout: separali con <span class="mono">;</span>.' })}
          ${tendina('wp-timezone', 'Fuso orario', s.timezone, meta.timezones || ['W. Europe Standard Time'], 'Nome Windows del fuso: per l\'Italia <span class="mono">W. Europe Standard Time</span>.')}
        </div>
        <div class="row3">
          ${tendina('wp-arch', 'Architettura', s.architecture, meta.architectures || ['amd64', 'x86'], '<span class="mono">amd64</span> per tutti i PC moderni a 64 bit.')}
          ${campo('wp-edition', 'Edizione da installare', s.edition_index, { maxlength: 64, placeholder: 'Windows 11 Pro oppure 6', hint: 'Nome esatto dell\'immagine dentro <span class="mono">install.wim</span> oppure il suo indice. Vuoto = il setup chiede.' })}
          ${campo('wp-key', 'Chiave di prodotto', s.product_key, { mono: true, maxlength: 29, placeholder: 'XXXXX-XXXXX-XXXXX-XXXXX-XXXXX', hint: 'Vuota: nessuna richiesta durante il setup (si attiva dopo o con la chiave del firmware).' })}
        </div>
      </div>

      <div class="card"><h3>Account</h3>
        <div class="alert warn">Tutte le password di questa pagina finiscono <strong>in chiaro</strong> dentro <span class="mono">autounattend.xml</span>, che i PC scaricano via HTTP senza autenticazione. È il funzionamento previsto dal setup di Windows: usa password dedicate all'installazione e cambiale subito dopo.</div>
        <div class="row3">
          ${campo('wp-computer', 'Nome del computer', s.computer_name, { maxlength: 15, mono: true, placeholder: '*', hint: 'Max 15 caratteri. <span class="mono">*</span> = nome casuale; anche <span class="mono">PC-*</span> va bene (prefisso + parte casuale).' })}
          ${campo('wp-org', 'Organizzazione', s.organization, { maxlength: 64, placeholder: 'facoltativa' })}
          ${campo('wp-owner', 'Intestatario', s.owner, { maxlength: 64, placeholder: 'facoltativo' })}
        </div>
        <div class="row2">
          ${campo('wp-admin', 'Amministratore locale', s.admin_user, { maxlength: 20, placeholder: 'amministratore', hint: 'Max 20 caratteri. Con <span class="mono">Administrator</span> viene impostata la password dell\'account predefinito invece di crearne uno nuovo.' })}
          ${password('wp-admin-pw', 'Password dell\'amministratore', s.admin_password, 'Obbligatoria se c\'è un nome utente: senza, Windows si ferma alle schermate finali.')}
        </div>
        ${spunta('wp-autologon', 'Accesso automatico dopo l\'installazione', !!s.autologon, 'Windows salva questa password anche nel registro del PC. Utile per far girare i comandi al primo accesso senza intervento.')}
        <div class="field" id="wp-autologon-box" ${s.autologon ? '' : 'hidden'}>
          <label for="wp-autologon-count">Quante volte</label>
          <input id="wp-autologon-count" type="number" min="1" max="99999" value="${esc(s.autologon_count || 9999)}">
          <div class="hint">Numero di accessi automatici prima di tornare a chiedere la password. Metti 1 per farlo una volta sola.</div>
        </div>
        <div class="row3">
          ${campo('wp-eu-name', 'Secondo utente (facoltativo)', eu.name, { maxlength: 20, placeholder: 'utente' })}
          ${password('wp-eu-pw', 'Password del secondo utente', eu.password)}
          ${tendina('wp-eu-group', 'Gruppo', eu.group || 'Users', meta.groups || ['Users', 'Administrators'])}
        </div>
        ${spunta('wp-dom-on', 'Aggiungi il computer al dominio Active Directory', !!jd.enabled, 'Serve un utente autorizzato ad aggiungere computer al dominio. Il PC deve raggiungere i controller di dominio durante l\'installazione.')}
        <div id="wp-dom-box" ${jd.enabled ? '' : 'hidden'}>
          <div class="row2">
            ${campo('wp-dom', 'Dominio', jd.domain, { mono: true, maxlength: 253, placeholder: 'azienda.local' })}
            ${campo('wp-dom-ou', 'Unità organizzativa (OU)', jd.ou, { mono: true, maxlength: 255, placeholder: 'OU=PC,DC=azienda,DC=local (facoltativa)' })}
          </div>
          <div class="row2">
            ${campo('wp-dom-user', 'Utente per l\'aggiunta al dominio', jd.user, { maxlength: 104, placeholder: 'admjoin oppure AZIENDA\\admjoin' })}
            ${password('wp-dom-pw', 'Password dell\'utente di dominio', jd.password)}
          </div>
        </div>
      </div>

      <div class="card"><h3>Disco</h3>
        ${tendina('wp-disk-mode', 'Partizionamento', dk.mode || 'auto-uefi', meta.disk_modes || [{ id: 'auto-uefi', name: 'Automatico UEFI (GPT)' }, { id: 'auto-bios', name: 'Automatico BIOS legacy (MBR)' }, { id: 'manuale', name: 'Manuale' }], 'Scegli in base a come il PC ha avviato la rete: UEFI per i PC moderni, BIOS legacy per i più vecchi. Con "Manuale" restano le schermate del disco del setup.')}
        ${spunta('wp-disk-wipe', 'Cancella il disco 0 senza chiedere conferma', dk.wipe !== false, '<strong>Tutti i dati sul primo disco vengono persi.</strong> Il setup non chiede nulla: controlla di aver scelto il PC giusto.')}
        <div class="row3" id="wp-uefi-box" ${uefi ? '' : 'hidden'}>
          ${campo('wp-efi', 'Partizione EFI (MB)', dk.efi_mb, { type: 'number', min: 100, max: 2048, hint: 'Consigliati 300 MB (100 minimo).' })}
          ${campo('wp-msr', 'Riservata Microsoft (MB)', dk.msr_mb, { type: 'number', min: 0, max: 128, hint: 'Consigliati 16 MB. 0 = non creata.' })}
          ${campo('wp-recovery', 'Ripristino (MB)', dk.recovery_mb, { type: 'number', min: 0, max: 8192, hint: '0 = nessuna partizione di ripristino; altrimenti almeno 300 MB (consigliati 750).' })}
        </div>
        <div class="hint" id="wp-bios-hint" ${uefi ? 'hidden' : ''}>In BIOS legacy Pixio crea una partizione riservata di sistema da 500 MB (attiva) e assegna tutto il resto del disco a Windows.</div>
      </div>

      <div class="card"><h3>Windows 11</h3>
        ${spunta('wp-skip-oobe', 'Salta le schermate iniziali (OOBE)', s.skip_oobe !== false, 'Niente contratto di licenza, niente schermate sulla privacy, niente account Microsoft: il PC arriva al desktop da solo.')}
        ${spunta('wp-bypass', 'Aggira i requisiti di Windows 11 (TPM, Secure Boot, RAM, CPU)', !!s.bypass_requirements, 'Scrive le voci di registro <span class="mono">LabConfig</span> prima dell\'installazione. <strong>Non è una configurazione supportata da Microsoft:</strong> il PC potrebbe non ricevere aggiornamenti e l\'installazione non è garantita. Usalo solo su macchine di prova o quando sai cosa stai facendo.')}
        ${spunta('wp-defender', 'Non chiedere l\'invio di campioni a Microsoft Defender', !!s.disable_defender_prompt, 'Toglie solo le richieste sull\'invio dei dati: l\'antivirus resta attivo.')}
        ${spunta('wp-hide-ext', 'Nascondi le estensioni dei file conosciuti', s.hide_files_ext !== false, 'È il comportamento predefinito di Windows. Togli la spunta per mostrarle: comodo su un PC usato dai tecnici.')}
        ${spunta('wp-hibernate', 'Disattiva l\'ibernazione', s.disable_hibernate !== false, 'Libera lo spazio di <span class="mono">hiberfil.sys</span> e toglie l\'avvio rapido: consigliato sui PC fissi e sulle macchine virtuali.')}
        ${tendina('wp-power', 'Schema di alimentazione', s.power_scheme || 'bilanciato', (meta.power_schemes || ['bilanciato', 'prestazioni']).map((x) => ({ id: x, name: x === 'prestazioni' ? 'Prestazioni elevate' : 'Bilanciato' })))}
      </div>

      <div class="card"><h3>App e comandi</h3>
        <div class="field"><span class="field-label">App preinstallate da rimuovere</span>
          <div class="checks" id="wp-apps" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:4px 14px">${apps().map((a) => `<label><input type="checkbox" data-app="${esc(a.id)}" ${(s.remove_apps || []).indexOf(a.id) >= 0 ? 'checked' : ''}> ${esc(a.name)}</label>`).join('')}</div>
          <div class="hint">La rimozione avviene al primo accesso, per l'utente creato e per le future installazioni. Se un pacchetto non c'è, il comando non fa nulla e non blocca niente.</div>
        </div>
        ${area('wp-apps-altre', 'Altri pacchetti Appx', altre.join('\n'), 'Un nome per riga, come lo restituisce <span class="mono">Get-AppxPackage</span> (es. <span class="mono">Microsoft.Paint</span>).', 3)}
        ${area('wp-commands', 'Comandi al primo accesso', (s.run_commands || []).join('\n'), 'Un comando per riga, eseguito al primo accesso con i diritti dell\'utente creato (es. <span class="mono">cmd /c net user pippo /active:no</span>). Massimo 40 comandi da 500 caratteri.', 4)}
      </div>

      <div class="card"><h3>Driver</h3>
        ${spunta('wp-drivers', 'Usa i driver caricati in Pixio', s.drivers_from_pixio !== false, `Aggiunge <span class="mono">\\\\${esc(serverIp())}\\pxe\\drivers</span> ai percorsi che il setup analizza: i driver delle cartelle della pagina Driver vengono installati durante la personalizzazione.`)}
        <div class="hint">Il percorso è una share di sola lettura: perché funzioni serve l'opzione <strong>Installazione Windows via rete</strong> attiva in Impostazioni, la stessa che serve per avviare il setup dalla rete. Le credenziali della share finiscono anch'esse nel file.</div>
      </div>

      <div class="panel" style="margin-top:16px">
        <div class="hd"><h3>Anteprima di autounattend.xml</h3>
          <div class="actions">
            <button class="btn small" type="button" data-act="anteprima">Aggiorna</button>
            <button class="btn small primary" type="button" data-act="risposta" ${nuovoProfilo ? 'disabled' : ''}>Salva come risposta</button>
          </div></div>
        <div class="bd"><pre class="code" id="wp-preview" style="max-height:460px">Premi "Aggiorna" per generare l'anteprima.</pre></div>
      </div>`;

    // campi che mostrano o nascondono altri campi
    $('#wp-disk-mode', box).addEventListener('change', (e) => {
      $('#wp-uefi-box', box).hidden = e.target.value !== 'auto-uefi';
      $('#wp-bios-hint', box).hidden = e.target.value !== 'auto-bios';
    });
    $('#wp-autologon', box).addEventListener('change', (e) => {
      $('#wp-autologon-box', box).hidden = !e.target.checked;
    });
    $('#wp-dom-on', box).addEventListener('change', (e) => {
      $('#wp-dom-box', box).hidden = !e.target.checked;
    });
    box.addEventListener('input', () => { W.dirty = true; });
    box.addEventListener('change', () => { W.dirty = true; });
    anteprima(true);
  }

  // ---------------------------------------------------------------- lettura del form

  function v(id) { const el = $('#' + id, W.root); return el ? el.value.trim() : ''; }
  function b(id) { const el = $('#' + id, W.root); return !!(el && el.checked); }
  function righe(id) {
    const el = $('#' + id, W.root);
    if (!el) return [];
    return el.value.split('\n').map((x) => x.trim()).filter(Boolean);
  }

  function leggiForm() {
    const s = clone(W.cur.settings) || {};
    s.language = v('wp-language');
    s.input_locale = v('wp-input') || s.language;
    s.timezone = v('wp-timezone');
    s.architecture = v('wp-arch');
    s.edition_index = v('wp-edition');
    s.product_key = v('wp-key');
    s.computer_name = v('wp-computer') || '*';
    s.organization = v('wp-org');
    s.owner = v('wp-owner');
    s.admin_user = v('wp-admin');
    s.admin_password = v('wp-admin-pw');
    s.autologon = b('wp-autologon');
    s.autologon_count = Number(v('wp-autologon-count')) || 0;
    s.extra_user = { name: v('wp-eu-name'), password: v('wp-eu-pw'), group: v('wp-eu-group') || 'Users' };
    s.join_domain = {
      enabled: b('wp-dom-on'), domain: v('wp-dom'), ou: v('wp-dom-ou'),
      user: v('wp-dom-user'), password: v('wp-dom-pw'),
    };
    s.disk = {
      mode: v('wp-disk-mode'),
      wipe: b('wp-disk-wipe'),
      efi_mb: Number(v('wp-efi')) || 0,
      msr_mb: Number(v('wp-msr')) || 0,
      recovery_mb: Number(v('wp-recovery')) || 0,
    };
    s.skip_oobe = b('wp-skip-oobe');
    s.bypass_requirements = b('wp-bypass');
    s.disable_defender_prompt = b('wp-defender');
    s.hide_files_ext = b('wp-hide-ext');
    s.disable_hibernate = b('wp-hibernate');
    s.power_scheme = v('wp-power');
    const scelte = $$('#wp-apps input[type=checkbox]', W.root).filter((x) => x.checked).map((x) => x.dataset.app);
    s.remove_apps = scelte.concat(righe('wp-apps-altre').filter((x) => scelte.indexOf(x) < 0));
    s.run_commands = righe('wp-commands');
    s.drivers_from_pixio = b('wp-drivers');
    return s;
  }

  // ---------------------------------------------------------------- azioni

  async function anteprima(silenziosa) {
    const pre = $('#wp-preview', W.root);
    if (!pre || !W.cur || W.loadingPreview) return;
    W.loadingPreview = true;
    const settings = leggiForm();
    try {
      const r = W.cur.id
        ? await P.post(idUrl(W.cur.id) + '/preview', { settings })
        : await P.post('/api/winprofiles/preview', { name: v('wp-name') || W.cur.name, preset: W.cur.preset || '', settings });
      pre.textContent = r.xml || '';
    } catch (e) {
      pre.textContent = 'Anteprima non disponibile: ' + e.message;
      if (!silenziosa) P.fail(e);
    } finally {
      W.loadingPreview = false;
    }
  }

  async function salva(btn) {
    if (!W.cur) return;
    const name = v('wp-name') || W.cur.name;
    if (!name) { P.toast('Indica un nome per il profilo', 'warn'); return; }
    const body = { name, note: v('wp-note'), settings: leggiForm() };
    P.setBusy(btn, true, 'Salvataggio…');
    try {
      let p;
      if (W.cur.id) {
        p = await P.api('PUT', idUrl(W.cur.id), body);
        P.toast('Profilo salvato');
      } else {
        if (W.cur.preset) body.preset = W.cur.preset;
        p = await P.post('/api/winprofiles', body);
        P.toast(`Profilo "${p.name}" creato`);
      }
      W.dirty = false;
      await load(p.id);
    } catch (e) {
      P.fail(e);
    } finally {
      P.setBusy(btn, false);
    }
  }

  async function duplica() {
    if (!W.cur || !W.cur.id) return;
    const m = P.modal({
      title: 'Duplica profilo',
      body: `<div class="field"><label for="wdup-name">Nome della copia</label><input id="wdup-name" maxlength="64" value="${esc(W.cur.name + ' (copia)')}"></div>`,
      buttons: [{ label: 'Annulla', value: null }, { label: 'Duplica', cls: 'primary', onClick: (dlg) => ({ name: $('#wdup-name', dlg).value.trim() }) }],
    });
    const r = await m.done;
    if (!r || !r.name) return;
    try {
      const p = await P.post(idUrl(W.cur.id) + '/duplicate', { name: r.name });
      P.toast(`Creata la copia "${p.name}"`);
      await load(p.id);
    } catch (e) { P.fail(e); }
  }

  async function elimina() {
    if (!W.cur || !W.cur.id) return;
    const ok = await P.confirm(`Eliminare il profilo "${W.cur.name}"?`, {
      ok: 'Elimina', danger: true,
      detail: 'Le risposte già generate da questo profilo restano dove sono: vanno eliminate a parte.',
    });
    if (!ok) return;
    try {
      await P.api('DELETE', idUrl(W.cur.id));
      P.toast('Profilo eliminato');
      W.cur = null;
      await load();
      showEmpty();
    } catch (e) { P.fail(e); }
  }

  async function salvaRisposta(btn) {
    if (!W.cur || !W.cur.id) return;
    if (W.dirty) {
      const ok = await P.confirm('Ci sono modifiche non salvate.', {
        ok: 'Salva e continua', title: 'Salva prima il profilo',
        detail: 'La risposta viene generata dal profilo salvato sul server, non da quello che vedi a schermo.',
      });
      if (!ok) return;
      await salva($('[data-act="salva"]', W.root));
      if (W.dirty) return;
    }
    P.setBusy(btn, true, 'Generazione…');
    try {
      const r = await P.post(idUrl(W.cur.id) + '/save-answer', {});
      P.toast(`Risposta "${r.answer_name}" salvata`, 'ok', 8000);
      await P.modal({
        title: 'Risposta creata',
        body: `<p>L'autounattend.xml è stato salvato nella risposta <strong>${esc(r.answer_name)}</strong> (<span class="mono">${esc(r.answer_id)}</span>), file principale <span class="mono">autounattend.xml</span>.</p>
               <div class="alert warn">Il file viene servito ai PC senza autenticazione: le password che hai inserito (amministratore, dominio, share dei driver) sono leggibili da chiunque sia collegato alla rete.</div>
               <p class="hint">Per usarla: apri il catalogo ISO, scegli l'immagine di Windows e associale questa risposta. Pixio inietterà il file nel WinPE all'avvio, dove il setup lo trova da solo.</p>`,
        buttons: [{ label: 'Ho capito', cls: 'primary', value: true }],
      }).done;
    } catch (e) {
      P.fail(e);
    } finally {
      P.setBusy(btn, false);
    }
  }

  // ---------------------------------------------------------------- pagina

  P.pages.windows = {
    title: 'Windows',
    mount(root, parti) {
      W.root = root;
      W.cur = null;
      W.dirty = false;
      root.innerHTML = `
        <div class="ph">
          <div><h2>Windows</h2><div class="sub">Profili di installazione automatica: generano un file <span class="mono">autounattend.xml</span> da usare come risposta.</div></div>
          <div class="actions"><button class="btn" type="button" id="wp-reload">Aggiorna</button><button class="btn primary" type="button" id="wp-new">Nuovo profilo</button></div>
        </div>
        <div class="alert warn">Le password (amministratore, secondo utente, dominio, share dei driver) finiscono <strong>in chiaro</strong> nel file di risposta, che i PC scaricano via HTTP senza autenticazione. È il funzionamento previsto dal setup di Windows: usa password dedicate all'installazione e cambiale subito dopo.</div>
        <div class="menu-grid" style="grid-template-columns:minmax(260px,1fr) minmax(0,2.6fr)">
          <div class="panel"><div class="hd"><h3>Profili</h3></div><div class="bd" id="wp-list"><div class="loading">Caricamento…</div></div></div>
          <div id="wp-editor"><div class="loading">Caricamento…</div></div>
        </div>`;
      $('#wp-reload', root).addEventListener('click', () => load(W.cur && W.cur.id));
      $('#wp-new', root).addEventListener('click', () => nuovo());
      root.addEventListener('click', (e) => {
        const li = e.target.closest('li[data-id]');
        const btn = e.target.closest('button[data-act]');
        if (btn) {
          const act = btn.dataset.act;
          if (act === 'apri') {
            const p = W.list.find((x) => x.id === btn.dataset.id);
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
          const p = W.list.find((x) => x.id === li.dataset.id);
          if (p) open(p);
        }
      });
      // #/windows/<id> apre subito quel profilo (collegamento diretto da altre pagine)
      load(parti && parti[0] ? decodeURIComponent(parti[0]) : null);
    },
    unmount() {
      W.root = null;
      W.cur = null;
      W.meta = null;
      W.list = [];
      W.dirty = false;
    },
  };
})();
