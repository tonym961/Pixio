/* Pixio – SPA di amministrazione.
   Nucleo: utilità, chiamate API (CSRF, 401), notifiche, finestre modali, pannello laterale,
   router ad hash, login, wizard di primo avvio e dashboard. Le altre pagine sono nei moduli
   catalog.js, menu.js, drivers.js, settings.js, clients.js, logs.js e si registrano in Pixio.pages. */
'use strict';
(function () {
  const P = window.Pixio = window.Pixio || {};
  P.pages = P.pages || {};
  const state = P.state = {
    csrf: null, loggedIn: false, passwordSet: true,
    status: null, statusAt: 0,      // ultimo /api/system/status e quando è arrivato
    current: null, route: '',       // pagina montata e nome route
  };

  // ------------------------------------------------------------------ utilità DOM e formattazione
  const esc = P.esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = P.$ = (sel, root) => (root || document).querySelector(sel);
  const $$ = P.$$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  P.sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  P.fmtBytes = function (n, digits) {
    if (n == null || isNaN(n)) return '—';
    n = Number(n);
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    const d = digits != null ? digits : (i >= 3 ? 1 : 0);
    return n.toLocaleString('it-IT', { minimumFractionDigits: 0, maximumFractionDigits: d }) + ' ' + units[i];
  };

  P.parseDate = function (v) {
    if (!v) return null;
    if (typeof v === 'number') return new Date(v < 1e12 ? v * 1000 : v);
    const d = new Date(v);
    return isNaN(d.getTime()) ? null : d;
  };
  const two = (n) => (n < 10 ? '0' : '') + n;
  P.fmtTime = function (v) {
    const d = P.parseDate(v);
    return d ? two(d.getHours()) + ':' + two(d.getMinutes()) : '—';
  };
  P.fmtTimeSec = function (v) {
    const d = P.parseDate(v);
    return d ? two(d.getHours()) + ':' + two(d.getMinutes()) + ':' + two(d.getSeconds()) : '';
  };
  P.fmtDate = function (v) {
    const d = P.parseDate(v);
    if (!d) return '—';
    const now = new Date();
    const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    const yest = new Date(now); yest.setDate(now.getDate() - 1);
    const hm = two(d.getHours()) + ':' + two(d.getMinutes());
    if (sameDay(d, now)) return 'oggi ' + hm;
    if (sameDay(d, yest)) return 'ieri ' + hm;
    const opts = { day: 'numeric', month: 'short' };
    if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric';
    return d.toLocaleDateString('it-IT', opts) + ' ' + hm;
  };
  P.fmtAgo = function (ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return s + ' s fa';
    const m = Math.round(s / 60);
    if (m < 60) return m + ' min fa';
    const h = Math.round(m / 60);
    return h + ' h fa';
  };
  P.fmtDuration = function (sec) {
    if (sec == null || !isFinite(sec)) return '—';
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return sec + ' s';
    const m = Math.floor(sec / 60);
    if (m < 60) return m + ' min';
    const h = Math.floor(m / 60);
    return h + ' h ' + (m % 60) + ' min';
  };
  P.pct = (a, b) => (b > 0 ? Math.min(100, Math.floor(a * 100 / b)) : 0);

  P.archLabel = function (arch) {
    switch (arch) {
      case 'bios': return { label: 'BIOS', cls: 'neutral' };
      case 'efi64': return { label: 'UEFI x64', cls: 'acc' };
      case 'efi32': return { label: 'UEFI ia32', cls: 'acc' };
      case 'arm64': return { label: 'UEFI ARM64', cls: 'acc' };
      default: return { label: arch || '?', cls: 'neutral' };
    }
  };
  P.pill = (label, cls) => `<span class="pill ${cls || 'neutral'}">${esc(label)}</span>`;

  // Interruttore accessibile (button role=switch). Con data-field il click lo commuta da solo (usato nei form);
  // senza data-field è la pagina a gestire il click (es. abilitazione ISO, che richiede una chiamata API).
  P.switchHtml = function (on, attrs) {
    return `<button type="button" class="switch${on ? ' on' : ''}" role="switch" aria-checked="${on ? 'true' : 'false'}" ${attrs || ''}></button>`;
  };
  P.switchOn = (el) => el.getAttribute('aria-checked') === 'true';
  P.setSwitch = function (el, on) {
    el.setAttribute('aria-checked', on ? 'true' : 'false');
    el.classList.toggle('on', !!on);
  };
  document.addEventListener('click', (e) => {
    const sw = e.target.closest('.switch[data-field]');
    if (!sw || sw.disabled) return;
    P.setSwitch(sw, !P.switchOn(sw));
    sw.dispatchEvent(new CustomEvent('switch-change', { bubbles: true }));
  });

  P.setBusy = function (btn, busy, label) {
    if (!btn) return;
    if (busy) {
      btn.dataset.label = btn.dataset.label || btn.textContent;
      if (label) btn.textContent = label;
      btn.classList.add('busy'); btn.disabled = true;
    } else {
      if (btn.dataset.label) btn.textContent = btn.dataset.label;
      delete btn.dataset.label;
      btn.classList.remove('busy'); btn.disabled = false;
    }
  };

  // ------------------------------------------------------------------ notifiche
  P.toast = function (msg, kind, ms) {
    const box = document.getElementById('toasts');
    const t = document.createElement('div');
    t.className = 'toast ' + (kind || 'ok');
    t.setAttribute('role', 'status');
    t.textContent = msg;
    box.appendChild(t);
    const ttl = ms || (kind === 'bad' ? 7000 : 4000);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 320); }, ttl);
    return t;
  };
  P.fail = function (e) {
    if (e && (e.status === 401 || e.name === 'AbortError')) return;
    if (window.console) console.error(e);
    P.toast((e && e.message) || String(e), 'bad');
  };

  // ------------------------------------------------------------------ API
  class ApiError extends Error {
    constructor(message, status, data) { super(message); this.name = 'ApiError'; this.status = status; this.data = data; }
  }
  P.ApiError = ApiError;

  P.api = async function (method, path, body, opts) {
    opts = opts || {};
    const headers = Object.assign({}, opts.headers || {});
    let payload;
    if (body !== undefined && body !== null) {
      if (body instanceof Blob || body instanceof ArrayBuffer) { headers['Content-Type'] = 'application/octet-stream'; payload = body; }
      else { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
    }
    if (method !== 'GET' && method !== 'HEAD') headers['X-CSRF-Token'] = state.csrf || '';
    let resp;
    try {
      resp = await fetch(path, { method, headers, body: payload, credentials: 'same-origin', cache: 'no-store', signal: opts.signal });
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      throw new ApiError('Server non raggiungibile', 0);
    }
    let data = null;
    const ct = resp.headers.get('content-type') || '';
    try { data = ct.includes('application/json') ? await resp.json() : await resp.text(); } catch (e) { data = null; }
    if (resp.status === 401 && !opts.noAuthRedirect) {
      P.sessionLost();
      throw new ApiError('Sessione scaduta: accedi di nuovo', 401, data);
    }
    if (!resp.ok) {
      let msg = (data && typeof data === 'object' && data.error) || ('Errore ' + resp.status + (resp.statusText ? ' ' + resp.statusText : ''));
      if (resp.status === 403 && !opts._retried && /csrf/i.test(msg)) {
        // token CSRF cambiato (es. nuova sessione): lo rileggo e ritento una volta
        try {
          const st = await fetch('/api/auth/status', { credentials: 'same-origin', cache: 'no-store' }).then((r) => r.json());
          if (st && st.csrf) state.csrf = st.csrf;
          return P.api(method, path, body, Object.assign({}, opts, { _retried: true }));
        } catch (e2) { /* si prosegue con l'errore originale */ }
      }
      if (resp.status === 413) msg = 'File troppo grande per il server';
      throw new ApiError(msg, resp.status, data);
    }
    return data;
  };
  P.get = (p, o) => P.api('GET', p, undefined, o);
  P.post = (p, b, o) => P.api('POST', p, b, o);

  // Segue un job in background (/api/jobs/<id>) fino alla fine. Ritorna il job finale.
  P.watchJob = async function (jobId, onUpdate, intervalMs) {
    if (!jobId) return null;
    for (;;) {
      await P.sleep(intervalMs || 2000);
      if (!state.loggedIn) return null;
      let job;
      try { job = await P.get('/api/jobs/' + encodeURIComponent(jobId)); }
      catch (e) { if (e.status === 404) return null; if (e.status === 401) return null; continue; }
      if (onUpdate) onUpdate(job);
      if (job && job.status !== 'running') return job;
    }
  };

  // ------------------------------------------------------------------ finestre modali
  // Focus trap minimo: con Tab/Shift+Tab il fuoco resta tra gli elementi attivabili del contenitore.
  const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
  P.trapTab = function (e, container) {
    const items = $$(FOCUSABLE, container).filter((el) => !el.hidden && el.offsetParent !== null);
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0]; const last = items[items.length - 1];
    const active = document.activeElement;
    if (e.shiftKey) {
      if (active === first || !container.contains(active)) { e.preventDefault(); last.focus(); }
    } else if (active === last || !container.contains(active)) { e.preventDefault(); first.focus(); }
  };
  let modalPrevFocus = null;
  P.modal = function (opts) {
    const overlay = document.getElementById('modal');
    overlay.innerHTML = '';
    modalPrevFocus = document.activeElement;
    const dlg = document.createElement('div');
    dlg.className = 'dialog' + (opts.wide ? ' wide' : '');
    dlg.setAttribute('role', 'dialog'); dlg.setAttribute('aria-modal', 'true'); dlg.setAttribute('aria-labelledby', 'dlg-title');
    dlg.innerHTML = `<div class="dlg-hd"><h3 id="dlg-title">${esc(opts.title || '')}</h3><button class="icon-btn" type="button" data-close aria-label="Chiudi">×</button></div><div class="dlg-body"></div><div class="dlg-ft"></div>`;
    const body = dlg.querySelector('.dlg-body');
    if (typeof opts.body === 'string') body.innerHTML = opts.body; else if (opts.body) body.appendChild(opts.body);
    const ft = dlg.querySelector('.dlg-ft');
    let resolve; const done = new Promise((r) => { resolve = r; });
    let closed = false;
    const close = (v) => {
      if (closed) return; closed = true;
      overlay.hidden = true; overlay.innerHTML = '';
      document.removeEventListener('keydown', onKey);
      resolve(v);
      if (modalPrevFocus && modalPrevFocus.focus) { try { modalPrevFocus.focus(); } catch (e) { /* ignora */ } }
    };
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); close(null); return; }
      if (e.key === 'Tab') P.trapTab(e, dlg);
    };
    const buttons = opts.buttons || [{ label: 'Chiudi', value: null }];
    buttons.forEach((b) => {
      const btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'btn ' + (b.cls || ''); btn.textContent = b.label;
      btn.addEventListener('click', async () => {
        if (b.onClick) {
          const all = $$('button', ft); all.forEach((x) => { x.disabled = true; });
          let r;
          try { r = await b.onClick(dlg, close); }
          catch (e) { P.fail(e); r = false; }
          all.forEach((x) => { x.disabled = false; });
          if (r === false) return;
          close(r === undefined ? (b.value === undefined ? true : b.value) : r);
          return;
        }
        close(b.value === undefined ? true : b.value);
      });
      ft.appendChild(btn);
    });
    dlg.querySelector('[data-close]').addEventListener('click', () => close(null));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(null); });
    document.addEventListener('keydown', onKey);
    overlay.appendChild(dlg);
    overlay.hidden = false;
    // Invio nei campi di testo attiva il pulsante principale
    dlg.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
        const primary = ft.querySelector('.btn.primary,.btn.danger');
        if (primary) { e.preventDefault(); primary.click(); }
      }
    });
    setTimeout(() => {
      const f = dlg.querySelector('input:not([type=hidden]),select,textarea,button.primary,button');
      if (f) f.focus();
    }, 0);
    return { el: dlg, close, done };
  };
  P.confirm = function (msg, o) {
    o = o || {};
    return P.modal({
      title: o.title || 'Conferma',
      body: `<p>${esc(msg)}</p>${o.detail ? `<p class="hint">${esc(o.detail)}</p>` : ''}`,
      buttons: [{ label: 'Annulla', value: false }, { label: o.ok || 'Conferma', cls: o.danger ? 'danger' : 'primary', value: true }],
    }).done.then((v) => v === true);
  };

  // ------------------------------------------------------------------ pannello laterale
  const drawerEl = document.getElementById('drawer');
  const drawerOverlay = document.getElementById('drawer-overlay');
  let drawerPrevFocus = null;
  P.drawer = {
    el: drawerEl,
    body: document.getElementById('drawer-body'),
    isOpen: () => !drawerEl.hidden,
    open(title, html) {
      drawerPrevFocus = document.activeElement;
      $('#drawer-title').textContent = title || 'Dettagli';
      this.body.innerHTML = html || '';
      drawerEl.hidden = false; drawerOverlay.hidden = false;
      setTimeout(() => $('#drawer-close').focus(), 0);
    },
    setTitle(t) { $('#drawer-title').textContent = t; },
    close() {
      if (drawerEl.hidden) return;
      drawerEl.hidden = true; drawerOverlay.hidden = true;
      this.body.innerHTML = '';
      if (this.onClose) { const f = this.onClose; this.onClose = null; f(); }
      if (drawerPrevFocus && drawerPrevFocus.focus) { try { drawerPrevFocus.focus(); } catch (e) { /* ignora */ } }
    },
    onClose: null,
  };
  $('#drawer-close').addEventListener('click', () => P.drawer.close());
  drawerOverlay.addEventListener('click', () => P.drawer.close());
  document.addEventListener('keydown', (e) => {
    if (drawerEl.hidden || !document.getElementById('modal').hidden) return;
    if (e.key === 'Escape') P.drawer.close();
    else if (e.key === 'Tab') P.trapTab(e, drawerEl);
  });

  // ------------------------------------------------------------------ autenticazione
  const loginEl = document.getElementById('login');
  const appEl = document.getElementById('app');
  const loginForm = document.getElementById('login-form');
  const loginPw = document.getElementById('login-pw');
  const loginPw2 = document.getElementById('login-pw2');
  const loginErr = document.getElementById('login-error');

  function loginError(msg) {
    loginErr.textContent = msg || '';
    loginErr.hidden = !msg;
  }

  function showLogin(hint) {
    stopCurrentPage();
    P.drawer.close();
    appEl.hidden = true; loginEl.hidden = false;
    const first = !state.passwordSet;
    $('#login-title').textContent = first ? "Imposta la password dell'amministratore" : 'Accesso';
    $('#login-hint').textContent = hint || (first
      ? 'Primo avvio: scegli la password con cui accedere a Pixio (almeno 6 caratteri).'
      : 'Inserisci la password amministratore per gestire il server PXE.');
    $('#login-confirm-field').hidden = !first;
    loginPw.autocomplete = first ? 'new-password' : 'current-password';
    $('#login-btn').textContent = first ? 'Imposta e accedi' : 'Accedi';
    loginPw.disabled = false; $('#login-btn').disabled = false;
    loginPw.value = ''; loginPw2.value = '';
    loginError('');
    setTimeout(() => loginPw.focus(), 0);
  }

  P.sessionLost = function () {
    if (!state.loggedIn) return;
    state.loggedIn = false;
    showLogin('La sessione è scaduta: accedi di nuovo.');
  };

  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const pw = loginPw.value;
    if (!state.passwordSet) {
      if (pw.length < 6) { loginError('La password deve avere almeno 6 caratteri.'); loginPw.focus(); return; }
      if (pw !== loginPw2.value) { loginError('Le due password non coincidono.'); loginPw2.focus(); return; }
    } else if (!pw) { loginError('Inserisci la password.'); loginPw.focus(); return; }
    const btn = $('#login-btn');
    P.setBusy(btn, true, 'Accesso…');
    loginError('');
    try {
      const r = await P.post('/api/auth/login', { password: pw }, { noAuthRedirect: true });
      state.csrf = r.csrf || state.csrf;
      state.loggedIn = true; state.passwordSet = true;
      P.setBusy(btn, false);
      await enterApp();
    } catch (err) {
      P.setBusy(btn, false);
      loginError(err.message || 'Accesso non riuscito');
      loginPw.select();
    }
  });

  P.logout = async function () {
    try { await P.post('/api/auth/logout'); } catch (e) { /* comunque esco */ }
    state.loggedIn = false;
    sessionStorage.removeItem('pixio.wizard.skip');
    showLogin('Sei uscito. Accedi di nuovo per continuare.');
  };
  $('#logout-link').addEventListener('click', (e) => { e.preventDefault(); P.logout(); });

  // ------------------------------------------------------------------ stato di sistema condiviso (barra laterale)
  P.setStatus = function (st) {
    state.status = st; state.statusAt = Date.now();
    const host = (st.hostname || 'pixio') + (st.server_ip ? ' · ' + st.server_ip : '');
    $('#side-host').textContent = host;
    $('#login-host').textContent = host;
    if (st.version) $('#side-version').textContent = 'v' + st.version;
    const cat = st.catalog || {};
    $('#n-iso').textContent = cat.total != null ? String(cat.total) : '';
    $('#n-menu').textContent = cat.enabled != null ? cat.enabled + (cat.enabled === 1 ? ' voce' : ' voci') : '';
    const cl = st.clients || {};
    $('#n-client').textContent = cl.today != null ? cl.today + ' oggi' : '';
    document.dispatchEvent(new CustomEvent('pixio-status', { detail: st }));
  };
  P.refreshStatus = async function () {
    const st = await P.get('/api/system/status');
    P.setStatus(st);
    return st;
  };
  // aggiornamento leggero dei contatori quando la pagina corrente non lo fa già
  setInterval(() => {
    if (!state.loggedIn || document.hidden) return;
    if (Date.now() - state.statusAt < 12000) return;
    P.refreshStatus().catch(() => {});
  }, 15000);

  const WIZARD_DONE_KEY = 'pixio.wizardDone';
  const wizardDone = () => { try { return !!localStorage.getItem(WIZARD_DONE_KEY); } catch (e) { return false; } };
  P.markWizardDone = function () { try { localStorage.setItem(WIZARD_DONE_KEY, '1'); } catch (e) { /* storage non disponibile */ } };
  function needsWizard(st) {
    if (!st) return false;
    if (sessionStorage.getItem('pixio.wizard.skip')) return false;
    if (wizardDone()) return false;                       // l'utente lo ha già chiuso in passato
    if (st.catalog && Number(st.catalog.total) > 0) return false;   // c'è già almeno una ISO nel catalogo
    const noSources = !st.sources || st.sources.length === 0;
    const noLocal = !st.library || !st.library.iso_count;
    return noSources && noLocal;
  }

  async function enterApp() {
    loginEl.hidden = true; appEl.hidden = false;
    try { await P.refreshStatus(); } catch (e) { P.fail(e); }
    // Se cambio l'hash è l'evento hashchange a chiamare route(), così la pagina viene montata una sola volta
    if (needsWizard(state.status) && !/^#\/(benvenuto|impostazioni)/.test(location.hash)) {
      location.hash = '#/benvenuto'; return;
    }
    if (!location.hash || location.hash === '#' || location.hash === '#/') { location.hash = '#/dashboard'; return; }
    route();
  }

  // ------------------------------------------------------------------ router
  const ROUTES = ['dashboard', 'iso', 'menu', 'driver', 'impostazioni', 'client', 'log', 'benvenuto'];
  const mainEl = document.getElementById('main');

  function stopCurrentPage() {
    if (state.current && state.current.unmount) { try { state.current.unmount(); } catch (e) { /* ignora */ } }
    state.current = null;
    mainEl.innerHTML = '';
  }

  function route() {
    if (!state.loggedIn) { showLogin(); return; }
    const raw = location.hash.replace(/^#\/?/, '');
    const parts = raw.split('/');
    let name = parts[0] || 'dashboard';
    if (!ROUTES.includes(name)) name = 'dashboard';
    const page = P.pages[name];
    P.drawer.close();
    stopCurrentPage();
    state.route = name;
    $$('.nav').forEach((a) => {
      const sel = a.dataset.route === name;
      a.removeAttribute('aria-selected');
      if (sel) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    appEl.classList.toggle('wizard-mode', name === 'benvenuto');
    document.title = (page && page.title ? page.title + ' · ' : '') + 'Pixio';
    if (!page) {
      mainEl.innerHTML = `<div class="empty"><h3>Sezione non disponibile</h3><p>Il modulo "${esc(name)}" non è stato caricato.</p></div>`;
      return;
    }
    state.current = page;
    try { page.mount(mainEl, parts.slice(1)); } catch (e) { P.fail(e); }
    mainEl.focus({ preventScroll: true });
  }
  window.addEventListener('hashchange', () => { if (state.loggedIn) route(); });

  // ------------------------------------------------------------------ wizard di primo avvio
  P.pages.benvenuto = {
    title: 'Benvenuto',
    mount(root) {
      let step = 1;
      const st = state.status || {};
      const lib = st.library || {};
      const shareName = lib.samba_share_name || 'iso';
      const sambaPath = lib.samba_path || ('\\\\' + (st.server_ip || '<ip>') + '\\' + shareName);
      let addedSource = null;
      let uploadStarted = false;

      const stepsHtml = () => [['Share Windows'], ['ISO locali'], ['Fine']].map(([t], i) => {
        const n = i + 1;
        return `<div class="step${n < step ? ' done' : ''}" ${n === step ? 'aria-current="step"' : ''}><span class="num">${n < step ? '✓' : n}</span>${t}</div>`;
      }).join('');

      const render = () => {
        let body = '';
        if (step === 1) {
          body = `
            <h3>Da dove arrivano le ISO?</h3>
            <p class="lede">Se le ISO stanno su una condivisione Windows (o Samba), Pixio la monta in sola lettura e la tiene d'occhio. Puoi anche saltare questo passo e usare solo la libreria locale.</p>
            ${P.sourceFormHtml ? P.sourceFormHtml(null) : '<p class="hint">Modulo impostazioni non caricato: aggiungi la share dalla pagina Impostazioni.</p>'}
            <div class="actions"><button class="btn" type="button" data-act="skip">Salta, non ho una share</button><button class="btn primary" type="button" data-act="add" ${P.sourceFormHtml ? '' : 'disabled'}>Aggiungi share e continua</button></div>`;
        } else if (step === 2) {
          body = `
            <h3>Come caricare ISO sul server</h3>
            <p class="lede">La libreria locale di Pixio accetta ISO in due modi. Sono entrambi facoltativi: puoi farlo anche più tardi.</p>
            <div class="url-box"><div class="k">Da Windows, con Esplora file</div><div class="v">${esc(sambaPath)}</div>
              <div class="d">${lib.samba_enabled === false ? 'La share Samba è disattivata: puoi attivarla in Impostazioni → Libreria locale.' : 'Utente <span class="mono">pixio</span>. La password si imposta in Impostazioni → Libreria locale. Copia lì la ISO e comparirà nel catalogo alla prossima scansione.'}</div></div>
            <div class="url-box"><div class="k">Dal browser</div><div class="v" style="font-family:var(--font);font-size:15px">Carica il file da questa pagina o trascinalo nel Catalogo</div>
              <div class="d">Upload a blocchi: se si interrompe riprende da dove era rimasto. ${lib.web_upload_enabled === false ? 'Al momento è disattivato in Impostazioni.' : ''}</div>
              <div class="actions" style="margin-top:6px;justify-content:flex-start"><button class="btn" type="button" data-act="upload" ${(P.uploads && lib.web_upload_enabled !== false) ? '' : 'disabled'}>Carica ISO dal browser…</button><span class="hint" id="wz-upload-hint">${uploadStarted ? 'Caricamento avviato: lo trovi nel Catalogo.' : ''}</span></div></div>
            <div class="actions"><button class="btn" type="button" data-act="back">Indietro</button><button class="btn primary" type="button" data-act="next">Continua</button></div>`;
        } else {
          body = `
            <h3>Tutto pronto</h3>
            <p class="lede">${addedSource ? `La share <b>${esc(addedSource.name || addedSource.unc || '')}</b> è configurata e la scansione è partita.` : 'Nessuna share configurata: le ISO arriveranno dalla libreria locale.'} Da qui in poi:</p>
            <ol style="margin:0 0 6px 18px;padding:0;display:grid;gap:6px">
              <li>Nel <b>Catalogo</b> attiva "Nel menu" per le ISO da rendere avviabili: Pixio le monta e genera la ricetta di boot.</li>
              <li>In <b>Menu di boot</b> scegli ordine, gruppi, voce predefinita e timeout.</li>
              <li>Sul PC premi il tasto di boot da rete (spesso F12): dopo pochi secondi compare il menu. Con Secure Boot attivo va disattivato.</li>
            </ol>
            <p class="hint">Il DHCP esistente resta com'è: Pixio lavora in modalità proxy. Modalità e interfaccia si cambiano in Impostazioni → Rete e boot.</p>
            <div class="actions"><button class="btn" type="button" data-act="back">Indietro</button><button class="btn primary" type="button" data-act="finish">Vai al catalogo</button></div>`;
        }
        root.innerHTML = `<div class="wizard">
          <div class="eyebrow">Primo avvio</div><h2 style="margin-top:6px">Benvenuto in Pixio</h2>
          <div class="steps">${stepsHtml()}</div>
          <div class="card">${body}</div>
          <p class="hint" style="margin-top:12px"><button class="linklike" type="button" data-act="finish">Salta la configurazione guidata</button></p>
        </div>`;
      };

      root.addEventListener('click', async (e) => {
        const b = e.target.closest('[data-act]');
        if (!b) return;
        const act = b.dataset.act;
        if (act === 'skip' || act === 'next') { step = Math.min(3, step + 1); render(); }
        else if (act === 'back') { step = Math.max(1, step - 1); render(); }
        else if (act === 'finish') { sessionStorage.setItem('pixio.wizard.skip', '1'); P.markWizardDone(); location.hash = '#/iso'; }
        else if (act === 'upload') { if (P.uploads) { P.uploads.pick(() => { uploadStarted = true; const h = $('#wz-upload-hint', root); if (h) h.textContent = 'Caricamento avviato: lo trovi nel Catalogo.'; }); } }
        else if (act === 'add') {
          let form;
          try { form = P.readSourceForm(root); } catch (err) { P.toast(err.message, 'bad'); return; }
          P.setBusy(b, true, 'Connessione e montaggio…');
          try {
            const r = await P.post('/api/sources', form);
            addedSource = (r && r.source) || form;
            const warnings = (r && Array.isArray(r.warnings)) ? r.warnings : [];
            if (warnings.length) warnings.forEach((w) => P.toast('Share aggiunta, ma: ' + w, 'warn', 8000));
            else P.toast('Share aggiunta e scansione avviata');
            P.refreshStatus().catch(() => {});
            step = 2; render();
          } catch (err) { P.setBusy(b, false); P.fail(err); }
        }
      });
      render();
    },
    unmount() {},
  };

  // ------------------------------------------------------------------ dashboard
  P.pages.dashboard = {
    title: 'Dashboard',
    timer: null, agoTimer: null, root: null,
    mount(root) {
      this.root = root;
      root.innerHTML = `
        <div class="ph"><div><h2>Dashboard</h2><div class="sub" id="db-sub">Stato del server PXE · caricamento…</div></div>
          <div class="actions"><button class="btn" type="button" id="db-scan">Riscansiona</button><button class="btn primary" type="button" id="db-apply">Rigenera</button></div></div>
        <div id="db-alerts"></div>
        <div class="tiles" id="db-tiles"><div class="loading">Caricamento…</div></div>
        <div class="grid2">
          <div class="panel"><div class="hd"><h3>Ultimi client PXE</h3><a href="#/client" class="pill neutral">tutti</a></div><div class="tbl-wrap" id="db-clients"><div class="loading">Caricamento…</div></div></div>
          <div class="panel"><div class="hd"><h3>Attività recente</h3><a href="#/log" class="pill neutral">log</a></div><div class="log mono" id="db-log"><div class="loading">Caricamento…</div></div></div>
        </div>`;
      $('#db-scan').addEventListener('click', () => this.scan());
      $('#db-apply').addEventListener('click', () => this.apply());
      this.tick();
      this.timer = setInterval(() => this.tick(), 5000);
      this.agoTimer = setInterval(() => this.updateAgo(), 1000);
    },
    unmount() { clearInterval(this.timer); clearInterval(this.agoTimer); this.root = null; },
    updateAgo() {
      const el = $('#db-sub'); if (!el || !state.statusAt) return;
      el.textContent = 'Stato del server PXE · aggiornato ' + P.fmtAgo(Date.now() - state.statusAt);
    },
    async tick() {
      if (document.hidden || !this.root) return;
      try {
        const [st, clients, logs] = await Promise.all([
          P.refreshStatus(),
          P.get('/api/clients').catch(() => null),
          P.get('/api/logs?source=all&limit=8').catch(() => null),
        ]);
        if (!this.root) return;
        this.renderTiles(st);
        this.renderAlerts(st);
        if (clients) this.renderClients(clients);
        if (logs) this.renderLog(logs.lines || []);
        this.updateAgo();
      } catch (e) {
        if (e.status === 401) return;
        const sub = $('#db-sub'); if (sub) sub.textContent = 'Stato del server PXE · errore: ' + e.message;
      }
    },
    renderTiles(st) {
      const srcs = st.sources || [];
      const mounted = srcs.filter((s) => s.mounted).length;
      const errs = srcs.filter((s) => s.error).length;
      const lib = st.library || {};
      const sv = st.services || {};
      const dn = sv.dnsmasq || {}; const ng = sv.nginx || {};
      const disk = st.disk || {};
      const cat = st.catalog || {};
      const t1cls = srcs.length === 0 ? 'neutral' : (errs ? 'warn' : (mounted === 0 ? 'bad' : ''));
      const t1v = srcs.length === 0 ? 'Solo libreria locale' : (mounted === srcs.length ? `${mounted} ${mounted === 1 ? 'attiva' : 'attive'}` : `${mounted} di ${srcs.length} montate`);
      const t1d = srcs.map((s) => `<span class="mono">${esc(s.unc || s.name)}</span> ${s.mounted ? 'montata' : (s.error ? '<span class="bad-text">errore</span>' : 'non montata')}`).join(' · ')
        + (srcs.length ? ' · ' : '') + `Libreria locale ${lib.iso_count || 0} ISO`;
      const dhcpLabel = st.dhcp_mode === 'full' ? 'DHCP completo + TFTP' : 'DHCP proxy + TFTP';
      const t2cls = dn.active ? '' : 'bad';
      const t2d = dn.active ? (st.dhcp_mode === 'full' ? 'assegna gli indirizzi ai client' : 'coesiste con il DHCP esistente') : `stato: ${esc(dn.state || 'sconosciuto')}`;
      const t3cls = ng.active ? '' : 'bad';
      const free = disk.free;
      const t4cls = (free != null && free < 10 * 1024 * 1024 * 1024) ? 'warn' : 'neutral';
      $('#db-tiles').innerHTML = `
        <div class="tile ${t1cls}"><div class="k">Sorgenti ISO</div><div class="v">${esc(t1v)}</div><div class="d">${t1d}</div></div>
        <div class="tile ${t2cls}"><div class="k">${esc(dhcpLabel)}</div><div class="v">${dn.active ? 'Attivo su ' + esc(st.interface || '?') : 'Non attivo'}</div><div class="d">${t2d}</div></div>
        <div class="tile ${t3cls}"><div class="k">HTTP (nginx)</div><div class="v">${ng.active ? 'Attivo' : 'Non attivo'}</div><div class="d">porta 80 · ${cat.mounted || 0} ISO montate · ${cat.enabled || 0} nel menu</div></div>
        <div class="tile ${t4cls}"><div class="k">Cache e disco</div><div class="v num">${P.fmtBytes(disk.cache_used || 0)}</div><div class="d">libreria ${P.fmtBytes(disk.library_used || 0)} · ${free != null ? P.fmtBytes(free) + ' liberi' : 'spazio sconosciuto'}</div></div>`;
    },
    renderAlerts(st) {
      const w = (st.warnings || []).slice();
      const ipxe = st.ipxe || {};
      if (ipxe.building) w.push('Compilazione di iPXE in corso.');
      else if (ipxe.built === false) w.push('iPXE non è ancora stato compilato: i client non possono avviarsi. Usa "Ricompila iPXE" in Impostazioni → Sistema.');
      const sv = st.services || {};
      if (sv.smbd && !sv.smbd.active && st.library && st.library.samba_enabled) w.push('Samba (smbd) non è attivo: la share della libreria locale non è raggiungibile.');
      const box = $('#db-alerts');
      if (!w.length) { box.innerHTML = ''; return; }
      box.innerHTML = `<div class="alert warn"><b>Da controllare</b><ul>${w.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>`;
    },
    renderClients(list) {
      const box = $('#db-clients');
      const rows = (list || []).slice().sort((a, b) => (P.parseDate(b.last_seen) || 0) - (P.parseDate(a.last_seen) || 0)).slice(0, 6);
      if (!rows.length) { box.innerHTML = '<div class="empty" style="border:0;border-radius:0"><h3>Nessun client</h3><p>Nessun PC ha ancora chiesto il boot da rete. Avvia un PC in PXE (tasto F12) per vederlo qui.</p></div>'; return; }
      box.innerHTML = `<table><thead><tr><th>Quando</th><th>MAC</th><th>IP</th><th>Firmware</th><th>Voce avviata</th></tr></thead><tbody>${rows.map((c) => {
        const a = P.archLabel(c.arch);
        return `<tr><td class="num">${esc(P.fmtDate(c.last_seen))}</td><td class="mono">${esc(c.mac)}</td><td class="mono">${esc(c.ip || '—')}</td><td>${P.pill(a.label, a.cls)}</td><td>${esc(c.last_entry || '—')}${c.name ? ` <span class="hint">· ${esc(c.name)}</span>` : ''}</td></tr>`;
      }).join('')}</tbody></table>`;
    },
    renderLog(lines) {
      const box = $('#db-log');
      if (!lines.length) { box.innerHTML = '<div class="hint">Nessuna attività registrata.</div>'; return; }
      box.innerHTML = lines.slice(-8).map((l) => `<div><span class="t">${esc(P.fmtTimeSec(l.ts))}</span> <b>${esc(l.source || '')}</b> ${esc(l.msg || '')}</div>`).join('');
    },
    async scan() {
      const btn = $('#db-scan');
      P.setBusy(btn, true, 'Scansione…');
      try {
        const r = await P.post('/api/catalog/scan');
        P.toast('Scansione avviata', 'info');
        const job = await P.watchJob(r.job_id);
        if (job) P.toast(job.status === 'done' ? ('Scansione completata' + (job.message ? ': ' + job.message : '')) : ('Scansione: ' + (job.message || job.status)), job.status === 'done' ? 'ok' : 'bad');
        this.tick();
      } catch (e) { P.fail(e); }
      P.setBusy(btn, false);
    },
    async apply() {
      const btn = $('#db-apply');
      P.setBusy(btn, true, 'Rigenerazione…');
      try {
        const r = await P.post('/api/system/apply', { what: 'all' });
        const res = (r && r.results) || {};
        const bad = Object.keys(res).filter((k) => res[k] !== 'ok');
        if (bad.length) P.toast('Rigenerazione con errori: ' + bad.map((k) => k + ' → ' + res[k]).join('; '), 'bad', 9000);
        else P.toast('Configurazione e menu rigenerati');
        this.tick();
      } catch (e) { P.fail(e); }
      P.setBusy(btn, false);
    },
  };

  // ------------------------------------------------------------------ avvio
  async function boot() {
    try {
      const st = await P.get('/api/auth/status', { noAuthRedirect: true });
      state.csrf = st.csrf; state.loggedIn = !!st.logged_in; state.passwordSet = !!st.password_set;
    } catch (e) {
      $('#login-hint').textContent = 'Impossibile contattare il server: ' + e.message + '. Ricarica la pagina.';
      loginPw.disabled = true; $('#login-btn').disabled = true;
      setTimeout(boot, 5000);
      return;
    }
    if (state.loggedIn) await enterApp(); else showLogin();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
