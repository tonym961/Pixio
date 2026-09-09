/* Pixio – pagina Windows: profili di personalizzazione che generano un autounattend.xml.
   Elenco profili a sinistra, form diviso in sezioni a destra (Lingua e area, Account, Disco,
   Windows 11, Ottimizzazioni, App e comandi, Driver), anteprima dell'XML in un riquadro monospace.
   Pulsanti: Salva, Duplica, Elimina, Salva come risposta. Le password finiscono in chiaro
   dentro il file: l'avviso è fisso in cima alla pagina e ripetuto nella sezione Account.

   La sezione "Ottimizzazioni" (docs/API.md, sezione 10) mostra il catalogo di data/windows-tweaks.json
   così come arriva da GET /api/winprofiles (campo tweaks: {categories, items}): categorie richiudibili,
   ricerca, filtro per impatto, contatori, più gli elenchi a mano dei servizi e delle funzionalità
   Windows. Le scelte finiscono in settings.tweaks / services_extra / features_enable / features_disable.

   Il selettore "Tipo di Windows" della sezione "Lingua e area" (settings.target: "client",
   "10-ltsc", "11-ltsc" oppure "server", docs/API.md sezioni 11 e 12) decide quali voci del catalogo
   hanno senso: ogni voce dichiara in "editions" le piattaforme su cui ha effetto ("10", "10-ltsc",
   "11", "11-ltsc", "server") e la GUI mostra solo quelle compatibili, avvisa se il profilo ne
   conteneva di incompatibili e disattiva la rimozione delle app del Microsoft Store, che su Windows
   Server e nelle edizioni Enterprise LTSC non esiste.

   La finestra "Nuovo profilo" mostra i modelli di data/profile-presets.json raggruppati (campo
   "group"): prima "Per edizione di Windows" (Windows 11 Pro, le due LTSC, Windows Server), poi
   "Generici" (per tipo di postazione), ognuno con la descrizione e il tipo di Windows a cui si
   riferisce. Scegliendone uno il form si riempie per intero, selettore "Tipo di Windows" compreso.

   Il riquadro "Lingua da installare" della stessa sezione (settings.language_install, docs/API.md
   sezione 13) serve alle ISO che contengono una lingua sola: la ISO parte in inglese e il PC si
   ritrova in italiano da solo, senza installare a mano il pacchetto lingua. Interruttore, lingua,
   sorgente del pacchetto (Windows Update oppure un file caricato in Pixio) e indirizzo del file;
   i comandi generati si vedono nell'anteprima dell'XML in fondo alla pagina. */
'use strict';
(function () {
  const P = window.Pixio;
  const esc = P.esc; const $ = P.$; const $$ = P.$$;

  // Stato della pagina: meta = elenchi dal server, cur = profilo in modifica (id null = nuovo),
  // tw = stato della sezione Ottimizzazioni (selezione, filtri, elenchi a mano)
  const W = { root: null, meta: null, list: [], cur: null, dirty: false, previewSeq: 0, tw: null };

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
  // lingue installabili dopo il setup, con il GeoId che vuole Set-WinHomeLocation (sezione 13)
  function lingueInstall() {
    return (W.meta && W.meta.languages_install)
      || [{ tag: 'it-IT', name: 'Italiano (Italia)', geo_id: 118 }];
  }
  function sorgentiLingua() {
    return (W.meta && W.meta.lang_sources) || [
      { id: 'windows-update', name: 'Windows Update (il PC deve raggiungere internet)' },
      { id: 'file', name: 'Pacchetto caricato in Pixio (indirizzo http/https)' },
    ];
  }
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
      const tipo = s.target && s.target !== 'client' ? ` <span class="pill neutral">${esc(targetPill(s.target))}</span>` : '';
      return `<li data-id="${esc(p.id)}" class="${p.id === sel ? 'sel' : ''}" style="${p.id === sel ? 'border-color:var(--accent)' : ''}">
        <div style="min-width:0;flex:1">
          <div class="drv-name">${esc(p.name)}${tipo}${s.bypass_requirements ? ' <span class="pill warn">requisiti aggirati</span>' : ''}</div>
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
  /* I modelli arrivano da GET /api/winprofiles (campo "presets", da data/profile-presets.json).
     Vanno mostrati in due gruppi — prima quelli tarati su un'edizione di Windows, poi quelli
     generici per tipo di postazione — con la descrizione sempre visibile e il tipo di Windows di
     ognuno, così si sceglie leggendo invece di aprire una tendina alla cieca. */

  // identificativi dei modelli per edizione: servono solo come ripiego se il campo "group" non
  // arriva dal server (l'API dei modelli passa i campi che conosce)
  const PRESET_EDIZIONE = ['win11-pro', 'win11-ltsc', 'win10-ltsc', 'winserver'];

  function presetGruppo(p) {
    const g = String((p && p.group) || '').toLowerCase();
    if (g === 'edizione' || g === 'generico') return g;
    return PRESET_EDIZIONE.indexOf(p && p.id) >= 0 ? 'edizione' : 'generico';
  }

  /** Tipo di Windows del modello, con il nome per esteso preso da /api/winprofiles. */
  function presetTarget(p) { return String((p && p.settings && p.settings.target) || 'client'); }

  function presetCardHtml(p, sel) {
    const t = presetTarget(p);
    const s = p.settings || {};
    const extra = [];
    if (s.edition_index) extra.push('edizione ' + s.edition_index);
    if (s.product_key) extra.push('chiave di installazione già pronta');
    if (s.bypass_requirements) extra.push('requisiti di Windows 11 aggirati');
    if (s.autologon) extra.push('accesso automatico');
    return `<label class="wn-card${sel ? ' on' : ''}" data-preset="${esc(p.id)}">
      <input type="radio" name="wn-preset" value="${esc(p.id)}" ${sel ? 'checked' : ''}>
      <span class="b">
        <span class="t">${esc(p.name)} <span class="pill neutral wn-tipo">${esc(twTargetNome(t))}</span></span>
        <span class="d">${esc(p.description || '')}</span>
        ${extra.length ? `<span class="m">${esc(extra.join(' · '))}</span>` : ''}
      </span>
    </label>`;
  }

  function presetListaHtml(ps) {
    const gruppi = [
      { id: 'edizione', titolo: 'Per edizione di Windows',
        nota: 'Tarati su un\'edizione precisa: tipo di Windows, edizione da installare, chiave pubblica di installazione e ottimizzazioni che su quell\'edizione hanno davvero effetto.' },
      { id: 'generico', titolo: 'Generici',
        nota: 'Per tipo di postazione, senza legarsi a un\'edizione: l\'edizione la scegli tu o la chiede il programma di installazione.' },
    ];
    const blocchi = gruppi.map((g) => {
      const voci = ps.filter((p) => presetGruppo(p) === g.id);
      if (!voci.length) return '';
      return `<div class="wn-group">
        <div class="wn-gtitle">${esc(g.titolo)} <span class="pill neutral">${voci.length}</span></div>
        <div class="wn-gnota">${esc(g.nota)}</div>
        ${voci.map((p) => presetCardHtml(p, false)).join('')}
      </div>`;
    }).join('');
    return `<div class="wn-list">
      <label class="wn-card wn-vuoto on" data-preset="">
        <input type="radio" name="wn-preset" value="" checked>
        <span class="b">
          <span class="t">Nessun modello <span class="pill neutral wn-tipo">Windows client (10 e 11)</span></span>
          <span class="d">Parti dai valori predefiniti di Pixio: italiano, disco UEFI automatico, OOBE saltato, nessuna ottimizzazione selezionata.</span>
        </span>
      </label>
      ${blocchi}
    </div>`;
  }

  async function nuovo() {
    const ps = presets();
    const m = P.modal({
      title: 'Nuovo profilo Windows',
      wide: true,
      body: `<div class="field"><label for="wn-name">Nome del profilo</label>
               <input id="wn-name" maxlength="64" placeholder="es. Postazione ufficio Windows 11">
               <div class="hint">Serve solo a te per riconoscerlo nell'elenco.</div></div>
             <div class="field"><span class="field-label">Parti da un modello</span>
               <div class="hint" style="margin:0 0 8px">I valori del modello riempiono tutto il form — tipo di Windows, edizione, chiave, account, disco e ottimizzazioni — e restano modificabili prima del salvataggio.</div>
               ${ps.length ? presetListaHtml(ps) : '<div class="hint">Nessun modello disponibile: il file dei modelli non è installato. Si parte dai valori predefiniti.</div>'}</div>`,
      buttons: [
        { label: 'Annulla', value: null },
        {
          label: 'Continua',
          cls: 'primary',
          onClick: (dlg) => {
            const name = $('#wn-name', dlg).value.trim();
            if (!name) { P.toast('Indica un nome per il profilo', 'warn'); return false; }
            const scelto = dlg.querySelector('input[name="wn-preset"]:checked');
            return { name, preset: scelto ? scelto.value : '' };
          },
        },
      ],
    });
    // evidenzia la scheda scelta (le caselle radio da sole non si vedono abbastanza)
    m.el.addEventListener('change', (e) => {
      if (e.target.name !== 'wn-preset') return;
      $$('.wn-card', m.el).forEach((c) => c.classList.toggle('on', c.dataset.preset === e.target.value));
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

  // ---------------------------------------------------------------- lingua da installare
  /* Riquadro "Lingua da installare" (settings.language_install, docs/API.md sezione 13).
     Serve quando la ISO è in una lingua diversa da quella voluta — il caso tipico è Windows
     Server 2022 in inglese su un server che deve parlare italiano: finora il pacchetto lingua
     andava installato a mano dopo ogni installazione. Qui si scelgono lingua e sorgente; i
     comandi finiscono in FirstLogonCommands e si vedono nell'anteprima dell'XML. */
  function riquadroLingua(s) {
    const li = s.language_install || {};
    const on = !!li.enabled;
    const scelte = (li.languages || []).filter(Boolean);
    const tag = scelte[0] || 'it-IT';
    const sorgente = li.source || 'windows-update';
    const opz = lingueInstall().map((x) => ({ id: x.tag, name: `${x.name} — ${x.tag}` }));
    if (!opz.some((o) => o.id === tag)) opz.unshift({ id: tag, name: tag });
    const altre = scelte.slice(1);
    return `<div class="wp-lang" id="wp-li">
      ${spunta('wp-li-on', 'Installa la lingua di Windows dopo l\'installazione', on,
        'Da attivare quando la ISO è in una lingua diversa da quella che vuoi: per esempio una ISO '
        + 'di Windows Server in inglese su un server che deve parlare italiano. Il pacchetto lingua '
        + 'viene installato da solo al primo accesso, senza doverlo più fare a mano.')}
      <div id="wp-li-box" ${on ? '' : 'hidden'}>
        <div class="row3">
          ${tendina('wp-li-lang', 'Lingua da installare', tag, opz,
            'La lingua che il PC deve parlare alla fine. Con le ISO già in italiano questo riquadro non serve.')}
          ${tendina('wp-li-source', 'Da dove prendere il pacchetto', sorgente, sorgentiLingua(),
            'Windows Update: nessun file da procurarsi, ma il PC deve raggiungere internet durante '
            + 'il primo accesso. Pacchetto caricato in Pixio: per le reti chiuse.')}
          <div id="wp-li-url-box" ${sorgente === 'file' ? '' : 'hidden'}>
            ${campo('wp-li-url', 'Indirizzo del pacchetto', li.file_url, {
              mono: true, maxlength: 500, placeholder: 'http://' + serverIp() + '/pxe/lang/it-IT.cab',
              hint: 'Indirizzo <span class="mono">http://</span> o <span class="mono">https://</span> del file '
                + '<span class="mono">.cab</span> (o <span class="mono">.esd</span>) del pacchetto lingua: '
                + 'lo scarica il PC in installazione, quindi deve essere raggiungibile da lui.',
            })}
          </div>
        </div>
        ${spunta('wp-li-system', 'Rendila anche la lingua del sistema, dei formati e dell\'area geografica',
          li.set_system !== false,
          'Imposta lingua dell\'interfaccia, formati di data e ora e area geografica (GeoId) per '
          + 'l\'utente creato, per i nuovi utenti e per le schermate di accesso. Senza questa spunta '
          + 'la lingua viene solo installata e resta da scegliere a mano.')}
        ${altre.length ? `<div class="hint">Il profilo installa anche: <span class="mono">${esc(altre.join(', '))}</span>. Restano salvate; la lingua qui sopra è quella che il sistema userà.</div>` : ''}
        <div class="alert">Serve <strong>solo</strong> se l'immagine di Windows non contiene già la
          lingua che vuoi (le ISO italiane non ne hanno bisogno). Con <strong>Windows Update</strong>
          il PC deve poter raggiungere internet durante il primo accesso; senza collegamento il
          comando non installa niente e l'installazione prosegue nella lingua della ISO.
          Il cambio di lingua <strong>si vede dopo il riavvio successivo</strong>.</div>
      </div>
    </div>`;
  }

  // ---------------------------------------------------------------- ottimizzazioni (stile nLite)
  /* Il catalogo arriva da GET /api/winprofiles nel campo "tweaks" ({categories:[{id,name,description}],
     items:[{id,category,name,description,impact,editions,reg,services,commands,features_*}]}).
     La selezione vive in un insieme (W.tw.sel) e NON nelle caselle di spunta: con un filtro attivo le
     voci nascoste non stanno nel DOM e rileggerle da lì cancellerebbe le scelte fatte prima. */

  function twCatalogo() { return (W.meta && W.meta.tweaks) || { categories: [], items: [] }; }
  function twVoci() { return twCatalogo().items || []; }
  function twCategorie() { return twCatalogo().categories || []; }

  // colore dell'etichetta di impatto: verde = sicuro, giallo = attenzione, rosso = rischioso
  const TW_PILL = { sicuro: 'ok', attenzione: 'warn', rischioso: 'bad' };

  /* Tipo di Windows del profilo (settings.target) e piattaforme del catalogo (campo "editions"):
     stessa tabella di PLATFORMS / TARGET_PLATFORMS in services/winprofile.py. Un tipo che il server
     dovesse aggiungere e che qui non è in elenco non filtra niente: meglio mostrare tutto che far
     sparire voci già scelte. */
  const TW_PLATFORMS = ['10', '10-ltsc', '11', '11-ltsc', 'server'];
  const TW_TARGET_PLATFORMS = {
    client: ['10', '11'],           // Windows 10 e 11 "normali", con Microsoft Store
    '10-ltsc': ['10-ltsc'],         // Windows 10 Enterprise LTSC 2019 e 2021
    '11-ltsc': ['11-ltsc'],         // Windows 11 Enterprise LTSC 2024
    server: ['server'],             // Windows Server 2016-2025 con interfaccia grafica
  };
  /* Pillola discreta con le edizioni su cui la voce ha effetto: la chiave è l'elenco delle
     piattaforme in ordine, così si nomina il gruppo invece di elencare cinque sigle. L'elenco
     completo non porta pillola: quella voce vale ovunque. */
  const TW_SOLO = {
    10: 'solo Windows 10',
    '10-ltsc': 'solo Windows 10 LTSC',
    11: 'solo Windows 11',
    '11-ltsc': 'solo Windows 11 LTSC',
    server: 'solo Windows Server',
    '10,11': 'solo Windows 10 e 11 con Store',
    '10,10-ltsc': 'solo Windows 10',
    '11,11-ltsc': 'solo Windows 11',
    '10,10-ltsc,11,11-ltsc': 'solo Windows client',
    '10,11,server': 'non nelle LTSC',
  };
  // etichetta breve del tipo di Windows, per le pillole dell'elenco dei profili
  const TW_TARGET_PILL = { server: 'Server', '10-ltsc': 'Windows 10 LTSC', '11-ltsc': 'Windows 11 LTSC' };
  /* Perché una voce non ha effetto su un certo tipo di Windows: stessa spiegazione dei messaggi
     del server (TARGET_MOTIVI in services/winprofile.py), da mettere nell'avviso. */
  const TW_MOTIVI = {
    server: 'Cortana, Copilot, widget, Xbox, app del Microsoft Store, esperienze consumer, barra applicazioni di Windows 11',
    '10-ltsc': 'Microsoft Store e app che ne dipendono, Cortana, Copilot, widget, Teams, Xbox, esperienze consumer, contenuti consigliati, OneDrive preinstallato',
    '11-ltsc': 'Microsoft Store e app che ne dipendono, Cortana, Copilot, widget, Teams, Xbox, esperienze consumer, contenuti consigliati, OneDrive preinstallato',
    client: 'componenti presenti solo nelle edizioni LTSC o su Windows Server',
  };

  function targetPill(t) { return TW_TARGET_PILL[String(t)] || String(t); }

  function targets() {
    return (W.meta && W.meta.targets && W.meta.targets.length)
      ? W.meta.targets
      : [{ id: 'client', name: 'Windows client (10 e 11)' },
         { id: '10-ltsc', name: 'Windows 10 Enterprise LTSC (2019 e 2021)' },
         { id: '11-ltsc', name: 'Windows 11 Enterprise LTSC (2024)' },
         { id: 'server', name: 'Windows Server (2016-2025, con interfaccia grafica)' }];
  }

  /** Tipo scelto adesso: quello della sezione Ottimizzazioni, che il selettore tiene aggiornato. */
  function twTarget() {
    return (W.tw && W.tw.target) || (W.cur && W.cur.settings && W.cur.settings.target) || 'client';
  }

  function twTargetNome(t) {
    const id = t || twTarget();
    const v = targets().find((x) => x.id === id);
    return v ? v.name : id;
  }

  /** Il Microsoft Store (e quindi la rimozione delle app preinstallate) non c'è su Server. */
  function targetHaStore(t) {
    const id = String(t || twTarget());
    return id !== 'server' && id.indexOf('ltsc') < 0;
  }

  /** Piattaforme dichiarate da una voce. Voce senza "editions" (o con valori sconosciuti):
      compatibile con tutto, esattamente come fa il server. */
  function twPiattaforme(t) {
    const p = (((t && t.editions) || []).map(String)).filter((x) => TW_PLATFORMS.indexOf(x) >= 0);
    return p.length ? p : TW_PLATFORMS.slice();
  }

  function twCompatibile(t, target) {
    const coperte = TW_TARGET_PLATFORMS[target || twTarget()] || TW_PLATFORMS;
    return twPiattaforme(t).some((p) => coperte.indexOf(p) >= 0);
  }

  /** Voci del catalogo che hanno effetto sul tipo scelto. */
  function twVociCompatibili() { return twVoci().filter((t) => twCompatibile(t, twTarget())); }

  /** Voci spuntate che sul tipo scelto non farebbero nulla (il server rifiuta il salvataggio). */
  function twIncompatibiliScelte() {
    return twVoci().filter((t) => W.tw.sel.has(t.id) && !twCompatibile(t, twTarget()));
  }

  function twInit(s) {
    const noti = Object.create(null);
    twVoci().forEach((t) => { noti[t.id] = true; });
    const elenco = (s.tweaks || []).map(String);
    W.tw = {
      sel: new Set(elenco.filter((x) => noti[x])),
      // identificativi non più presenti nel catalogo: si segnalano e non si salvano (il server li rifiuta)
      ignoti: elenco.filter((x) => !noti[x]),
      servizi: (s.services_extra || []).map((x) => (typeof x === 'string'
        ? { name: x, start: 4 }
        : { name: (x && x.name) || '', start: Number(x && x.start) || 4 })),
      funz: (s.features_enable || []).map((n) => ({ name: String(n), on: true }))
        .concat((s.features_disable || []).map((n) => ({ name: String(n), on: false }))),
      // tipo di Windows corrente: da qui dipende quali voci si vedono e quali contano
      target: String(s.target || 'client'),
      q: '', imp: '', stato: '', open: Object.create(null),
    };
  }

  function twFiltroAttivo() { return !!(W.tw.q.trim() || W.tw.imp || W.tw.stato); }

  function twVisibile(t) {
    // il tipo di Windows viene prima di ogni altro filtro: le voci che non hanno effetto non si mostrano
    if (!twCompatibile(t, W.tw.target)) return false;
    if (W.tw.imp && t.impact !== W.tw.imp) return false;
    if (W.tw.stato === 'on' && !W.tw.sel.has(t.id)) return false;
    if (W.tw.stato === 'off' && W.tw.sel.has(t.id)) return false;
    const q = W.tw.q.trim().toLowerCase();
    if (!q) return true;
    const testo = ((t.name || '') + ' ' + (t.description || '')).toLowerCase();
    return q.split(/\s+/).every((parola) => testo.indexOf(parola) >= 0);
  }

  function twDiCategoria(cid) { return twVoci().filter((t) => t.category === cid); }
  /** Voci di una categoria che hanno effetto sul tipo scelto: sono queste che i contatori contano. */
  function twDiCategoriaCompat(cid) { return twDiCategoria(cid).filter((t) => twCompatibile(t, W.tw.target)); }
  function twAttive() { return twVociCompatibili().filter((t) => W.tw.sel.has(t.id)); }

  function twVoceHtml(t) {
    const on = W.tw.sel.has(t.id);
    const imp = t.impact || 'sicuro';
    const n = (a) => (Array.isArray(a) ? a.length : 0);
    const dett = [];
    if (n(t.reg)) dett.push(n(t.reg) === 1 ? '1 chiave di registro' : n(t.reg) + ' chiavi di registro');
    if (n(t.services)) dett.push(n(t.services) === 1 ? '1 servizio' : n(t.services) + ' servizi');
    if (n(t.commands)) dett.push(n(t.commands) === 1 ? '1 comando' : n(t.commands) + ' comandi');
    const nf = n(t.features_enable) + n(t.features_disable);
    if (nf) dett.push(nf === 1 ? '1 funzionalità Windows' : nf + ' funzionalità Windows');
    // pillola discreta quando la voce vale su una piattaforma sola (solo Windows 11, solo Server…)
    const piatt = twPiattaforme(t);
    const chiave = TW_PLATFORMS.filter((x) => piatt.indexOf(x) >= 0).join(',');
    const etichetta = piatt.length === TW_PLATFORMS.length ? '' : (TW_SOLO[chiave] || '');
    const soloEd = etichetta ? ` <span class="pill neutral tw-ed">${esc(etichetta)}</span>` : '';
    return `<label class="tw-item imp-${esc(imp)}${on ? ' on' : ''}" title="${esc(t.description || '')}">
      <input type="checkbox" data-tweak="${esc(t.id)}" ${on ? 'checked' : ''}>
      <span class="b">
        <span class="t">${esc(t.name)} <span class="pill ${TW_PILL[imp] || 'neutral'}">${esc(imp)}</span>${soloEd}</span>
        <span class="d">${esc(t.description || '')}</span>
        ${dett.length ? `<span class="m">${esc(dett.join(' · '))} · ${esc(t.id)}</span>` : `<span class="m">${esc(t.id)}</span>`}
      </span>
    </label>`;
  }

  function twCategorieHtml() {
    const filtro = twFiltroAttivo();
    const blocchi = [];
    twCategorie().forEach((c) => {
      // solo le voci valide per il tipo di Windows scelto: una categoria che resta senza sparisce
      const tutte = twDiCategoriaCompat(c.id);
      if (!tutte.length) return;
      const viste = tutte.filter(twVisibile);
      if (!viste.length) return;
      const nsel = tutte.filter((t) => W.tw.sel.has(t.id)).length;
      // con un filtro attivo le categorie si aprono da sole (si vede subito cosa corrisponde);
      // altrimenti restano aperte quelle già scelte dal tecnico o con almeno una voce attiva
      const aperta = filtro ? true
        : (W.tw.open[c.id] !== undefined ? W.tw.open[c.id] : nsel > 0);
      blocchi.push(`<details class="tw-cat" data-cat="${esc(c.id)}"${aperta ? ' open' : ''}>
        <summary>
          <span class="cname">${esc(c.name)}</span>
          <span class="pill ${nsel ? 'acc' : 'neutral'}" data-cat-count="${esc(c.id)}">${nsel} / ${tutte.length}</span>
          <span class="cbtns">
            <button class="btn small" type="button" data-tw-act="tutti" data-cat="${esc(c.id)}" title="Spunta tutte le voci mostrate di questa categoria">Seleziona tutto</button>
            <button class="btn small" type="button" data-tw-act="nessuno" data-cat="${esc(c.id)}" title="Togli la spunta a tutte le voci mostrate di questa categoria">Deseleziona tutto</button>
          </span>
        </summary>
        <div class="tw-inner">
          <div class="tw-cdesc">${esc(c.description || '')}${filtro && viste.length < tutte.length
            ? ` <span class="muted">— ${viste.length} voci su ${tutte.length} corrispondono al filtro</span>` : ''}</div>
          <div class="tw-items">${viste.map(twVoceHtml).join('')}</div>
        </div>
      </details>`);
    });
    if (!blocchi.length) {
      if (!twVociCompatibili().length) {
        return `<div class="empty"><h3>Nessuna ottimizzazione per questo tipo di Windows</h3>
          <p>Nel catalogo non c'è nessuna voce valida per ${esc(twTargetNome())}.</p></div>`;
      }
      return `<div class="empty"><h3>Nessuna ottimizzazione trovata</h3>
        <p>Nessuna voce corrisponde al testo cercato o al filtro scelto, fra quelle valide per
        ${esc(twTargetNome())}. Svuota la ricerca oppure rimetti "Tutti gli impatti".</p></div>`;
    }
    return blocchi.join('');
  }

  /** Avviso in cima alla sezione: il profilo ha voci spuntate che su questo tipo di Windows non
      farebbero nulla. Il server rifiuta il salvataggio finché restano, quindi si offre di toglierle. */
  function twAvvisoHtml() {
    if (!W.tw) return '';
    const inc = twIncompatibiliScelte();
    if (!inc.length) return '';
    const uno = inc.length === 1;
    return `<div class="alert warn tw-avviso">
      <strong>${inc.length} ${uno ? 'ottimizzazione selezionata non ha' : 'ottimizzazioni selezionate non hanno'}
      effetto su ${esc(twTargetNome())}.</strong>
      ${uno ? 'Tocca componenti che su questo tipo di Windows non esistono' : 'Toccano componenti che su questo tipo di Windows non esistono'}
      (${esc(TW_MOTIVI[twTarget()] || 'componenti assenti su questo tipo di Windows')}…):
      finché ${uno ? 'resta selezionata' : 'restano selezionate'} il salvataggio viene rifiutato.
      <div class="tw-inc">${inc.map((t) => `<span class="pill neutral">${esc(t.name)}</span>`).join('')}</div>
      <div class="actions" style="margin-top:8px">
        <button class="btn small primary" type="button" data-tw-act="pulisci">Togli le voci incompatibili</button>
      </div>
    </div>`;
  }

  function twRenderAvviso() {
    const box = $('#wp-tw-avviso', W.root);
    if (box) box.innerHTML = twAvvisoHtml();
  }

  function twTestataHtml() {
    const imps = (W.meta && W.meta.impacts) || ['sicuro', 'attenzione', 'rischioso'];
    return `<div class="tw-sum">
        <div class="tw-num"><div class="k">Attive</div><div class="n" id="wp-tw-n">0</div></div>
        <div class="tw-num"><div class="k">Rischiose</div><div class="n" id="wp-tw-nrisk">0</div></div>
        <div class="grow hint" id="wp-tw-hint"></div>
        <button class="btn small" type="button" data-tw-act="azzera">Azzera tutte</button>
      </div>
      <div class="tw-filters">
        <input class="search" id="wp-tw-q" type="search" data-nodirty
               aria-label="Cerca fra le ottimizzazioni"
               placeholder="Cerca fra le ottimizzazioni (nome o descrizione)" value="${esc(W.tw.q)}">
        <select id="wp-tw-imp" data-nodirty aria-label="Filtra per impatto">
          <option value="">Tutti gli impatti</option>
          ${imps.map((i) => `<option value="${esc(i)}"${W.tw.imp === i ? ' selected' : ''}>Solo impatto ${esc(i)}</option>`).join('')}
        </select>
        <select id="wp-tw-stato" data-nodirty aria-label="Mostra solo le voci attive o non attive">
          <option value="">Attive e non attive</option>
          <option value="on"${W.tw.stato === 'on' ? ' selected' : ''}>Solo le attive</option>
          <option value="off"${W.tw.stato === 'off' ? ' selected' : ''}>Solo le non attive</option>
        </select>
        <button class="btn small" type="button" data-tw-act="espandi">Espandi tutte</button>
        <button class="btn small" type="button" data-tw-act="comprimi">Comprimi tutte</button>
      </div>`;
  }

  function twServiziHtml() {
    const avvii = (W.meta && W.meta.service_starts)
      || [{ id: 4, name: 'disabilitato' }, { id: 3, name: 'in avvio manuale' }, { id: 2, name: 'in avvio automatico' }];
    const righe = W.tw.servizi;
    return `${righe.length ? `<div class="tw-rows">${righe.map((r, i) => `
        <div class="tw-row">
          <input class="mono" maxlength="64" placeholder="DiagTrack" value="${esc(r.name)}" aria-label="Sigla del servizio ${i + 1}">
          <select aria-label="Avvio del servizio ${i + 1}">${avvii.map((o) => `<option value="${esc(o.id)}"${Number(r.start) === Number(o.id) ? ' selected' : ''}>${esc(o.name)}</option>`).join('')}</select>
          <button class="btn small danger" type="button" data-tw-act="serv-del" data-i="${i}">Togli</button>
        </div>`).join('')}</div>`
      : '<div class="hint">Nessun servizio aggiunto a mano: qui finiscono solo quelli che imposti tu, oltre a quelli già portati dalle ottimizzazioni spuntate sopra.</div>'}
      <div class="actions" style="margin-top:8px"><button class="btn small" type="button" data-tw-act="serv-add">Aggiungi servizio</button></div>`;
  }

  function twFunzHtml() {
    const righe = W.tw.funz;
    return `${righe.length ? `<div class="tw-rows">${righe.map((r, i) => `
        <div class="tw-row">
          <input class="mono" maxlength="80" placeholder="NetFx3" value="${esc(r.name)}" aria-label="Nome della funzionalità ${i + 1}">
          <select aria-label="Cosa fare della funzionalità ${i + 1}">
            <option value="on"${r.on ? ' selected' : ''}>da attivare</option>
            <option value="off"${r.on ? '' : ' selected'}>da disattivare</option>
          </select>
          <button class="btn small danger" type="button" data-tw-act="feat-del" data-i="${i}">Togli</button>
        </div>`).join('')}</div>`
      : '<div class="hint">Nessuna funzionalità aggiunta a mano.</div>'}
      <div class="actions" style="margin-top:8px"><button class="btn small" type="button" data-tw-act="feat-add">Aggiungi funzionalità</button></div>`;
  }

  /** Contenitori della sezione: la testata resta ferma (la ricerca non deve perdere il fuoco),
      l'elenco delle categorie e i due elenchi a mano si ridisegnano da soli. */
  function twRender() {
    const box = $('#wp-tw', W.root);
    if (!box || !W.tw) return;
    if (!twVoci().length) {
      box.innerHTML = `<div class="alert warn">Il catalogo delle ottimizzazioni non è disponibile
        (<span class="mono">data/windows-tweaks.json</span> mancante o illeggibile). Le ottimizzazioni già
        salvate nel profilo restano dove sono, ma da qui non se ne possono scegliere di nuove.</div>`;
      return;
    }
    box.innerHTML = `${W.tw.ignoti.length ? `<div class="alert warn">Questo profilo contiene
        ${W.tw.ignoti.length} ottimizzazioni che non sono più nel catalogo
        (<span class="mono">${esc(W.tw.ignoti.join(', '))}</span>): salvando il profilo vengono tolte.</div>` : ''}
      <div id="wp-tw-avviso">${twAvvisoHtml()}</div>
      <div id="wp-tw-head">${twTestataHtml()}</div>
      <div class="tw-cats" id="wp-tw-cats"></div>
      <div class="tw-extra">
        <div class="field"><span class="field-label">Servizi da disabilitare a mano</span>
          <div class="hint">Sigla del servizio (quella di <span class="mono">sc.exe</span>: <span class="mono">DiagTrack</span>, <span class="mono">WSearch</span>…), non il nome visualizzato. Diventa una voce <span class="mono">Start</span> nel registro durante l'installazione.</div>
          <div id="wp-tw-serv"></div>
        </div>
        <div class="field"><span class="field-label">Funzionalità di Windows da attivare o disattivare</span>
          <div class="hint">Nome usato da <span class="mono">dism</span> (<span class="mono">NetFx3</span>, <span class="mono">SMB1Protocol</span>, <span class="mono">Microsoft-Hyper-V-All</span>…). Vengono applicate al primo accesso; la stessa funzionalità non può essere sia da attivare sia da disattivare.</div>
          <div id="wp-tw-feat"></div>
        </div>
      </div>`;
    twRenderCategorie();
    twRenderElenchi();
    twConteggi();
  }

  function twRenderCategorie() {
    const cont = $('#wp-tw-cats', W.root);
    if (!cont) return;
    cont.innerHTML = twCategorieHtml();
    // l'evento toggle di <details> non risale: si aggancia a ogni categoria dopo il disegno
    $$('details.tw-cat', cont).forEach((d) => {
      d.addEventListener('toggle', () => { W.tw.open[d.dataset.cat] = d.open; });
    });
  }

  function twRenderElenchi() {
    const s = $('#wp-tw-serv', W.root);
    const f = $('#wp-tw-feat', W.root);
    if (s) s.innerHTML = twServiziHtml();
    if (f) f.innerHTML = twFunzHtml();
  }

  /** Aggiorna i contatori (riepilogo in cima e pillola di ogni categoria) senza ridisegnare l'elenco. */
  function twConteggi() {
    const box = $('#wp-tw', W.root);
    if (!box || !W.tw) return;
    const attive = twAttive();
    const risch = attive.filter((t) => t.impact === 'rischioso');
    const n = $('#wp-tw-n', box);
    if (n) n.textContent = String(attive.length);
    const nr = $('#wp-tw-nrisk', box);
    if (nr) { nr.textContent = String(risch.length); nr.className = 'n' + (risch.length ? ' bad-text' : ' muted'); }
    const h = $('#wp-tw-hint', box);
    if (h) {
      const comp = twVociCompatibili().length;
      const tot = twVoci().length;
      h.innerHTML = `Su ${comp} ottimizzazioni valide per ${esc(twTargetNome())}`
        + (comp < tot ? ` (${tot} in tutto il catalogo: le altre non hanno effetto su questo tipo di Windows)` : '')
        + '.'
        + (risch.length
          ? ` <span class="bad-text">Attive rischiose: ${esc(risch.map((t) => t.name).join(', '))}.</span>`
          : ' Nessuna voce rischiosa selezionata.');
    }
    $$('[data-cat-count]', box).forEach((el) => {
      const tutte = twDiCategoriaCompat(el.dataset.catCount);
      const s = tutte.filter((t) => W.tw.sel.has(t.id)).length;
      el.textContent = s + ' / ' + tutte.length;
      el.className = 'pill ' + (s ? 'acc' : 'neutral');
    });
  }

  /** Rilegge nei due elenchi a mano quello che il tecnico ha scritto (le caselle di spunta no:
      quelle stanno in W.tw.sel, che è già aggiornato a ogni click). */
  function twSync() {
    const box = $('#wp-tw', W.root);
    if (!box || !W.tw) return;
    const leggi = (sel) => $$(sel + ' .tw-row', box).map((r) => {
      const i = r.querySelector('input');
      const s = r.querySelector('select');
      return { name: i ? i.value.trim() : '', val: s ? s.value : '' };
    });
    W.tw.servizi = leggi('#wp-tw-serv').map((r) => ({ name: r.name, start: Number(r.val) || 4 }));
    W.tw.funz = leggi('#wp-tw-feat').map((r) => ({ name: r.name, on: r.val !== 'off' }));
  }

  function twAggiungiInSettings(s) {
    if (!W.tw) return;
    if (!twVoci().length) return;   // catalogo non disponibile: non si tocca quello che c'è già
    twSync();
    s.tweaks = twVoci().map((t) => t.id).filter((id) => W.tw.sel.has(id));
    s.services_extra = W.tw.servizi.filter((r) => r.name);
    s.features_enable = W.tw.funz.filter((r) => r.on && r.name).map((r) => r.name);
    s.features_disable = W.tw.funz.filter((r) => !r.on && r.name).map((r) => r.name);
  }

  function twBind(box) {
    const cont = $('#wp-tw', box);
    if (!cont) return;
    cont.addEventListener('click', async (e) => {
      const b = e.target.closest('button[data-tw-act]');
      if (!b) return;
      // i pulsanti di categoria stanno dentro <summary>: senza questo aprirebbero/chiuderebbero il blocco
      e.preventDefault();
      e.stopPropagation();
      const act = b.dataset.twAct;
      if (act === 'tutti' || act === 'nessuno') {
        twDiCategoria(b.dataset.cat).filter(twVisibile).forEach((t) => {
          if (act === 'tutti') W.tw.sel.add(t.id); else W.tw.sel.delete(t.id);
        });
        W.dirty = true;
        twRenderCategorie();
        twConteggi();
        return;
      }
      if (act === 'pulisci') {
        const inc = twIncompatibiliScelte();
        if (!inc.length) return;
        inc.forEach((t) => W.tw.sel.delete(t.id));
        W.dirty = true;
        twRenderAvviso();
        twRenderCategorie();
        twConteggi();
        anteprima(true);
        P.toast(inc.length === 1
          ? 'Tolta 1 ottimizzazione non compatibile con ' + twTargetNome()
          : `Tolte ${inc.length} ottimizzazioni non compatibili con ` + twTargetNome());
        return;
      }
      if (act === 'azzera') {
        if (!W.tw.sel.size) { P.toast('Non c\'è nessuna ottimizzazione attiva', 'info'); return; }
        const ok = await P.confirm(`Togliere tutte le ${W.tw.sel.size} ottimizzazioni attive?`, {
          ok: 'Azzera', danger: true,
          detail: 'I servizi e le funzionalità aggiunti a mano restano: si tolgono dai loro elenchi.',
        });
        if (!ok) return;
        W.tw.sel.clear();
        W.dirty = true;
        twRenderCategorie();
        twConteggi();
        return;
      }
      if (act === 'espandi' || act === 'comprimi') {
        const apri = act === 'espandi';
        twCategorie().forEach((c) => { W.tw.open[c.id] = apri; });
        $$('details.tw-cat', cont).forEach((d) => { d.open = apri; });
        return;
      }
      if (act === 'serv-add') { twSync(); W.tw.servizi.push({ name: '', start: 4 }); W.dirty = true; twRenderElenchi(); return; }
      if (act === 'feat-add') { twSync(); W.tw.funz.push({ name: '', on: true }); W.dirty = true; twRenderElenchi(); return; }
      if (act === 'serv-del') { twSync(); W.tw.servizi.splice(Number(b.dataset.i), 1); W.dirty = true; twRenderElenchi(); return; }
      if (act === 'feat-del') { twSync(); W.tw.funz.splice(Number(b.dataset.i), 1); W.dirty = true; twRenderElenchi(); }
    });
    cont.addEventListener('change', (e) => {
      const c = e.target.closest('input[data-tweak]');
      if (c) {
        if (c.checked) W.tw.sel.add(c.dataset.tweak); else W.tw.sel.delete(c.dataset.tweak);
        const riga = c.closest('.tw-item');
        if (riga) riga.classList.toggle('on', c.checked);
        twConteggi();
        return;
      }
      if (e.target.id === 'wp-tw-imp') { W.tw.imp = e.target.value; twRenderCategorie(); twConteggi(); return; }
      if (e.target.id === 'wp-tw-stato') { W.tw.stato = e.target.value; twRenderCategorie(); twConteggi(); }
    });
    cont.addEventListener('input', (e) => {
      if (e.target.id !== 'wp-tw-q') return;
      W.tw.q = e.target.value;
      twRenderCategorie();
      twConteggi();
    });
  }

  /** Cambio del selettore "Tipo di Windows": l'elenco delle ottimizzazioni, i contatori, l'avviso
      delle voci incompatibili e la rimozione delle app si aggiornano subito, senza ricaricare nulla.
      La selezione non viene toccata: le voci incompatibili si tolgono dall'avviso, di proposito. */
  function cambiaTipo(t) {
    if (!W.tw) return;
    W.tw.target = String(t || 'client');
    if (W.cur && W.cur.settings) W.cur.settings.target = W.tw.target;
    twRenderAvviso();
    twRenderCategorie();
    twConteggi();
    appsAggiorna();
    anteprima(true);
  }

  /** Su Windows Server il Microsoft Store non esiste: le app preinstallate da rimuovere restano
      salvate nel profilo (il server le ignora) ma non si possono più cambiare, con la spiegazione. */
  function appsAggiorna() {
    const box = $('#wp-editor', W.root);
    if (!box) return;
    const off = !targetHaStore();
    const nota = $('#wp-apps-nota', box);
    if (nota) {
      nota.hidden = !off;
      if (off) {
        nota.innerHTML = `Con <strong>${esc(twTargetNome())}</strong> non c'è il Microsoft Store:
          le app preinstallate qui sotto non esistono e la loro rimozione viene saltata. L'elenco resta
          come l'hai lasciato — torna a un tipo client per modificarlo — e non finisce
          nell'<span class="mono">autounattend.xml</span>.`;
      }
    }
    const campo = $('#wp-apps-field', box);
    if (campo) campo.classList.toggle('wp-off', off);
    const altre = $('#wp-apps-altre', box);
    if (altre) {
      altre.disabled = off;
      const f = altre.closest('.field');
      if (f) f.classList.toggle('wp-off', off);
    }
    $$('#wp-apps input[type=checkbox]', box).forEach((c) => { c.disabled = off; });
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
    twInit(s);   // stato della sezione Ottimizzazioni: va preparato prima del disegno

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
        ${tendina('wp-target', 'Tipo di Windows', s.target || 'client', targets(),
          'Windows Server e le edizioni <strong>Enterprise LTSC</strong> non hanno Microsoft Store, '
          + 'Cortana, Copilot, widget, Teams, Xbox né le esperienze consumer (su Server manca anche la '
          + 'barra applicazioni di Windows 11): molte ottimizzazioni pensate per i PC lì non fanno nulla. '
          + 'Cambiando tipo la sezione Ottimizzazioni mostra solo le voci che hanno davvero effetto e, '
          + 'con Server e LTSC, la rimozione delle app preinstallate viene disattivata. Il modo più '
          + 'rapido di impostarlo bene è partire da un modello "per edizione" quando crei il profilo.')}
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
        ${riquadroLingua(s)}
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

      <div class="card"><h3>Ottimizzazioni</h3>
        <div class="hint" style="margin-bottom:12px">Catalogo in stile nLite: ogni voce scrive criteri di registro, imposta servizi o esegue comandi durante l'installazione. Le voci <strong>sicure</strong> tolgono solo fastidi, quelle con <strong>attenzione</strong> fanno perdere qualche funzione, quelle <strong>rischiose</strong> abbassano la sicurezza del PC: usale solo se sai cosa comportano. Tutto quello che spunti qui finisce nell'anteprima dell'XML in fondo alla pagina.</div>
        <div id="wp-tw"></div>
      </div>

      <div class="card"><h3>App e comandi</h3>
        <div class="alert warn" id="wp-apps-nota" hidden></div>
        <div class="field" id="wp-apps-field"><span class="field-label">App preinstallate da rimuovere</span>
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
    // lingua da installare: l'interruttore apre il riquadro, la sorgente "file" chiede l'indirizzo
    $('#wp-li-on', box).addEventListener('change', (e) => {
      $('#wp-li-box', box).hidden = !e.target.checked;
    });
    $('#wp-li-source', box).addEventListener('change', (e) => {
      $('#wp-li-url-box', box).hidden = e.target.value !== 'file';
    });
    // tipo di Windows: ridisegna ottimizzazioni, contatori, avviso e sezione App
    $('#wp-target', box).addEventListener('change', (e) => cambiaTipo(e.target.value));
    // ricerca e filtri delle ottimizzazioni non sono modifiche del profilo: non sporcano il form
    const sporca = (e) => { if (!e.target.closest('[data-nodirty]')) W.dirty = true; };
    box.addEventListener('input', sporca);
    box.addEventListener('change', sporca);
    twRender();
    twBind(box);
    appsAggiorna();
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
    s.target = v('wp-target') || 'client';
    s.language = v('wp-language');
    s.input_locale = v('wp-input') || s.language;
    s.timezone = v('wp-timezone');
    // lingua da installare dopo il setup: la tendina mostra la prima lingua, le eventuali altre
    // (profilo scritto via API con più lingue) restano salvate in coda
    const liTag = v('wp-li-lang') || 'it-IT';
    const liVecchie = ((s.language_install || {}).languages || []).filter((x) => x && x !== liTag);
    const liVoce = lingueInstall().find((x) => x.tag === liTag);
    s.language_install = {
      enabled: b('wp-li-on'),
      languages: [liTag].concat(liVecchie),
      source: v('wp-li-source') || 'windows-update',
      file_url: v('wp-li-url'),
      set_system: b('wp-li-system'),
      geo_id: liVoce ? liVoce.geo_id : ((s.language_install || {}).geo_id || 118),
      keyboard: liTag,
    };
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
    // ottimizzazioni scelte + servizi e funzionalità aggiunti a mano (sezione Ottimizzazioni)
    twAggiungiInSettings(s);
    return s;
  }

  // ---------------------------------------------------------------- azioni

  /* Ogni richiesta prende un gettone: quando ne parte una nuova (tipico dopo il salvataggio, che
     ridisegna il form e quindi crea un altro <pre>) la risposta di quella vecchia viene buttata.
     Con molte ottimizzazioni l'XML supera i 40 KB e la richiesta precedente può ancora essere in
     volo: scartandola il riquadro non resta mai fermo al messaggio iniziale. */
  async function anteprima(silenziosa) {
    const pre = $('#wp-preview', W.root);
    if (!pre || !W.cur) return;
    const gettone = W.previewSeq = (W.previewSeq || 0) + 1;
    let settings;
    try {
      settings = leggiForm();
    } catch (e) {
      pre.textContent = 'Anteprima non disponibile: ' + e.message;
      if (!silenziosa) P.fail(e);
      return;
    }
    try {
      const r = W.cur.id
        ? await P.post(idUrl(W.cur.id) + '/preview', { settings })
        : await P.post('/api/winprofiles/preview', { name: v('wp-name') || W.cur.name, preset: W.cur.preset || '', settings });
      if (gettone !== W.previewSeq) return;      // ne è partita una più recente
      pre.textContent = r.xml || '';
    } catch (e) {
      if (gettone !== W.previewSeq) return;
      pre.textContent = 'Anteprima non disponibile: ' + e.message;
      if (!silenziosa) P.fail(e);
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
      W.tw = null;
      root.innerHTML = `
        <div class="ph">
          <div><h2>Windows</h2><div class="sub">Profili di installazione automatica: generano un file <span class="mono">autounattend.xml</span> da usare come risposta.</div></div>
          <div class="actions"><button class="btn" type="button" id="wp-reload">Aggiorna</button><button class="btn primary" type="button" id="wp-new">Nuovo profilo</button></div>
        </div>
        <div class="alert warn">Le password (amministratore, secondo utente, dominio, share dei driver) finiscono <strong>in chiaro</strong> nel file di risposta, che i PC scaricano via HTTP senza autenticazione. È il funzionamento previsto dal setup di Windows: usa password dedicate all'installazione e cambiale subito dopo.</div>
        <div class="menu-grid wp-grid">
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
      W.tw = null;
    },
  };
})();
