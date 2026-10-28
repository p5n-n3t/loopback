/* Loopback admin dashboard — vanilla JS single page app.
 * Talks only to the JSON endpoints in docs/ADMIN-API.md. CSP forbids inline
 * script/style, so all behaviour is wired through delegated data-act /
 * data-form / data-change / data-input attributes. Every interpolated value is
 * HTML-escaped by the H`` tagged template unless explicitly wrapped in raw().
 */
'use strict';
(function () {
const doc = document;
const $ = (s, r = doc) => r.querySelector(s);
const $$ = (s, r = doc) => Array.from(r.querySelectorAll(s));
const enc = encodeURIComponent;

/* -- escaping */
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ESC[c]);
class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
const raw = s => new Raw(String(s));
const val = v => v instanceof Raw ? v.s : Array.isArray(v) ? v.map(val).join('') : (v == null || v === false) ? '' : esc(v);
function H(str, ...vs) { let o = str[0]; for (let i = 0; i < vs.length; i++) o += val(vs[i]) + str[i + 1]; return new Raw(o); }

/* Keys that may carry credentials are never rendered, whatever the backend sends. */
const SECRET_KEY = /(token|secret|password|passwd|credential|private|api[_-]?key|bearer|cookie|csrf|signing|authorization)/i;
const SAFE_KEY = /(_file|_path|_prefix|_count|_at|_set|_mcp|_enabled|_hint)$|^(api_tokens|oauth_clients|active_oauth_grants|scopes?)$/i;
const isSecretKey = k => SECRET_KEY.test(k) && !SAFE_KEY.test(k);
const SECRET_VAL = /\b(lbk_[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|-----BEGIN [A-Z ]*PRIVATE KEY-----)/;
const scrub = v => typeof v === 'string' && SECRET_VAL.test(v) ? '[redacted]' : v;

/* -- formatting */
const now = () => Date.now() / 1000;
function rel(ts) {
  if (!ts) return '—';
  const d = now() - Number(ts), a = Math.abs(d);
  const f = a < 45 ? 'just now' : a < 3600 ? Math.round(a / 60) + 'm' : a < 86400 ? Math.round(a / 3600) + 'h' : Math.round(a / 86400) + 'd';
  return f === 'just now' ? f : d >= 0 ? f + ' ago' : 'in ' + f;
}
const abs = ts => ts ? new Date(Number(ts) * 1000).toLocaleString() : '—';
const time = ts => H`<time title="${abs(ts)}">${rel(ts)}</time>`;
function bytes(n) {
  n = Number(n); if (!isFinite(n) || n <= 0) return n === 0 ? '0 B' : '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (n >= 10 || i === 0 ? Math.round(n) : n.toFixed(1)) + ' ' + u[i];
}
function dur(s) {
  s = Math.floor(Number(s) || 0); const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}
const pct = (a, b) => b ? Math.max(0, Math.min(100, a / b * 100)) : 0;
const meter = p => raw(`<svg class="meter ${p > 90 ? 'bad' : p > 75 ? 'warn' : ''}" viewBox="0 0 100 6" preserveAspectRatio="none" role="progressbar" aria-valuenow="${Math.round(p)}" aria-valuemin="0" aria-valuemax="100"><rect width="${p.toFixed(1)}" height="6"/></svg>`);
const arr = (x, ...keys) => Array.isArray(x) ? x : x && typeof x === 'object' ? (keys.map(k => x[k]).find(Array.isArray) || []) : [];
const yes = b => b ? 'yes' : 'no';
const yn = b => b === undefined ? undefined : yes(b);
const lines = s => String(s || '').split('\n').map(x => x.trim()).filter(Boolean);
const KINDS = ['local-computer', 'remote-physical', 'remote-vm', 'container', 'gateway', 'unknown'];
const STATUS = ['healthy', 'degraded', 'unreachable', 'auth_failed', 'disconnected', 'untested', 'stale'];
const STATUS_LABEL = { healthy: 'Healthy', degraded: 'Degraded', unreachable: 'Unreachable', auth_failed: 'Auth failed', disconnected: 'Disconnected', untested: 'Untested', stale: 'Stale', error: 'Error' };

/* -- UI atoms */
const al = (k, t, m, x = '') => H`<div class="alert ${k}"><span><b>${t}</b>${m}</span>${x}</div>`;
const lnk = (h, t, c = 'sm ghost') => H`<a class="btn ${c}" href="${h}">${t}</a>`;
const badge = (t, k = '') => H`<span class="badge ${k}">${t}</span>`;
const dot = s => H`<span class="dot ${s || 'untested'}" aria-hidden="true"></span>`;
const st = s => H`<span class="st">${dot(s)}${STATUS_LABEL[s] || s || 'Untested'}</span>`;
const okb = (b, t = 'yes', f = 'no') => b ? badge(t, 'ok') : badge(f, 'off');
const ob = (b, t, f) => b === undefined ? undefined : okb(b, t, f);
function table(heads, rows, empty = 'Nothing here yet.') {
  if (!rows.length) return H`<div class="empty">${empty}</div>`;
  return H`<div class="tw"><table><thead><tr>${heads.map(h => Array.isArray(h) ? H`<th class="${h[1]}">${h[0]}</th>` : H`<th>${h}</th>`)}</tr></thead><tbody>${rows}</tbody></table></div>`;
}
function kv(pairs) {
  const list = Array.isArray(pairs) ? pairs : Object.entries(pairs || {});
  return H`<dl class="kv">${list.filter(p => p && p[1] !== undefined).map(([k, v]) => H`<dt>${k}</dt><dd>${v === null || v === '' ? raw('<span class="faint">—</span>') : v}</dd>`)}</dl>`;
}
/* Render an arbitrary backend object without secrets. */
function safeKv(obj, depth = 0) {
  if (!obj || typeof obj !== 'object') return H`<span>${scrub(obj)}</span>`;
  return kv(Object.entries(obj).filter(([k]) => k !== 'history').map(([k, v]) => {
    if (isSecretKey(k) && v && typeof v !== 'boolean' && typeof v !== 'number') return [k, badge('redacted', 'off')];
    if (Array.isArray(v)) return [k, v.length && typeof v[0] === 'object' ? (depth < 2 ? H`${v.slice(0, 10).map(x => safeKv(x, depth + 1))}` : v.length + ' items') : v.map(scrub).join(', ')];
    if (v && typeof v === 'object') return [k, depth < 2 ? safeKv(v, depth + 1) : '…'];
    if (typeof v === 'boolean') return [k, yes(v)];
    if (/_at$/.test(k) && typeof v === 'number') return [k, time(v)];
    return [k, scrub(v)];
  }));
}
const card = (title, body, o = {}) => cardO(title, body, typeof o === 'string' ? { cls: o } : o);
const cardO = (title, body, { cls = 'c6', actions = '', id = '' }) =>
  H`<section class="card ${cls}"${id ? raw(` id="${esc(id)}"`) : ''}><div class="card-head"><h2>${title}</h2>${actions ? H`<div class="actions row">${actions}</div>` : ''}</div>${body}</section>`;
const head = (title, desc = '', actions = '') =>
  H`<div class="page-head"><div><h1>${title}</h1>${desc ? H`<p>${desc}</p>` : ''}</div>${actions ? H`<div class="actions row">${actions}</div>` : ''}</div>`;
const opt = (v, cur, label) => H`<option value="${v}"${String(v) === String(cur ?? '') ? raw(' selected') : ''}>${label ?? v}</option>`;
const chk = b => b ? raw(' checked') : '';
const fld = (label, id, control, hint = '') => H`<div class="field"><label for="${id}">${label}</label>${control}${hint ? H`<span class="hint">${hint}</span>` : ''}</div>`;
const inp = (name, value, { type = 'text', id = 'f_' + name, ph = '', req = false, extra = '' } = {}) =>
  H`<input id="${id}" name="${name}" type="${type}" value="${value ?? ''}" placeholder="${ph}"${req ? raw(' required') : ''}${raw(extra ? ' ' + extra : '')}>`;
const errBox = raw('<div class="form-errors" aria-live="assertive"></div>');
function errorsHtml(err) {
  const list = (err && err.errors) || [];
  return H`<div class="errors" role="alert"><b>${err ? err.message : 'Error'}</b>${list.length ? H`<ul>${list.map(e => H`<li>${typeof e === 'string' ? e : [e.field || e.loc, e.message || e.msg || JSON.stringify(e)].filter(Boolean).join(': ')}</li>`)}</ul>` : ''}</div>`;
}
function showErrors(container, err) {
  if (!container) return;
  let box = container.querySelector('.form-errors');
  if (!box && container.tagName === 'FORM') {
    // Never wipe a form's own controls: errors for a form get their own box, created on demand.
    if (!err) return;
    box = doc.createElement('div'); box.className = 'form-errors'; container.appendChild(box);
  }
  box = box || container;
  box.innerHTML = err ? String(errorsHtml(err)) : '';
}

/* -- toasts */
function toast(msg, kind = 'info', ms = 4500) {
  const el = doc.createElement('div'); el.className = 'toast ' + kind; el.textContent = msg;
  $('#toasts').appendChild(el); setTimeout(() => el.remove(), ms);
  const all = $$('#toasts .toast'); if (all.length > 4) all[0].remove();
}

/* -- API */
class ApiError extends Error { constructor(m, status, errors = [], body = null) { super(m); this.status = status; this.errors = errors; this.body = body; } }
let csrfP = null, leaving = false;
function toLogin() { if (!leaving) { leaving = true; location.assign('/admin/login?next=/admin'); } }
function session() {
  return csrfP || (csrfP = fetch('/api/admin/session', { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
    .then(async r => { if (r.status === 401) { toLogin(); throw new ApiError('Session expired', 401); } const j = await r.json(); state.session = j; return j; })
    .catch(e => { csrfP = null; throw e; }));
}
async function api(path, opts = {}) {
  const method = opts.method || 'GET', headers = { Accept: 'application/json' };
  let body;
  if (method !== 'GET') {
    headers['X-Loopback-CSRF'] = (await session()).csrf || '';
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body === undefined ? {} : opts.body);
  }
  let r;
  try { r = await fetch(path, { method, headers, body, credentials: 'same-origin', cache: 'no-store' }); }
  catch (e) { const er = new ApiError('Network error — is Loopback running?', 0); if (!opts.quiet) toast(er.message, 'error'); throw er; }
  if (r.status === 401) { toLogin(); throw new ApiError('Session expired', 401); }
  if (r.status === 403 && method !== 'GET' && !opts.retried) { csrfP = null; return api(path, { ...opts, retried: true }); }
  const text = await r.text(); let data = null;
  if (text) { try { data = JSON.parse(text); } catch { data = text; } }
  if (!r.ok) {
    const d = data && typeof data === 'object' ? data : {};
    const msg = d.detail || d.error_description || d.error || (typeof data === 'string' && data.slice(0, 200)) || 'HTTP ' + r.status;
    const er = new ApiError(typeof msg === 'string' ? msg : JSON.stringify(msg), r.status, Array.isArray(d.errors) ? d.errors : Array.isArray(d.diagnostics) ? d.diagnostics.filter(x => !x.ok).map(x => `${x.name}: ${x.detail || 'failed'}`) : [], d);
    if (!opts.quiet) toast(er.message, 'error', 7000);
    throw er;
  }
  return data;
}
const get = (p, quiet) => api(p, { quiet });
const put = (p, b) => api(p, { method: 'PUT', body: b });
const post = (p, b) => api(p, { method: 'POST', body: b });
const del = p => api(p, { method: 'DELETE' });
const soft = p => api(p, { quiet: true }).catch(() => null);
const done = (m = 'Saved') => { toast(m, 'ok'); refresh(); };

/* -- dialog */
const dlg = () => $('#dlg');
/* modal({title, body, ok, danger, typed, form}) → resolves to null on cancel, else form values (or true). */
function modal({ title, body = '', ok = 'Confirm', danger = false, typed = null, cancel = 'Cancel', okOnly = false }) {
  return new Promise(resolve => {
    const d = dlg(); d.returnValue = '';
    d.innerHTML = String(H`<form class="dlg-in" novalidate><h2 id="dlgTitle">${title}</h2><div class="dlg-body">${body}</div>
      ${typed ? H`<div class="field"><label for="dlgTyped">Type <code>${typed}</code> to confirm</label><input id="dlgTyped" autocomplete="off" spellcheck="false"></div>` : ''}
      ${errBox}<div class="row end">${okOnly ? '' : H`<button type="button" data-close>${cancel}</button>`}<button type="submit" class="${danger ? 'danger solid' : 'primary'}">${ok}</button></div></form>`);
    const f = d.querySelector('form'); let result = null;
    f.addEventListener('submit', e => {
      e.preventDefault();
      if (typed && $('#dlgTyped', f).value.trim() !== typed) { $('#dlgTyped', f).classList.add('invalid'); showErrors(f, new ApiError('Confirmation text does not match.')); return; }
      if (!f.checkValidity()) { f.reportValidity(); return; }
      result = vals(f); d.close('ok');
    });
    f.querySelector('[data-close]')?.addEventListener('click', () => d.close('cancel'));
    d.addEventListener('close', () => { const r = d.returnValue === 'ok' ? (Object.keys(result || {}).length ? result : true) : null; d.innerHTML = ''; resolve(r); }, { once: true });
    d.showModal();
    (f.querySelector('input:not([type=hidden]),select,textarea') || f.querySelector('[type=submit]')).focus();
  });
}
const confirmDo = (title, body, ok = 'Confirm', danger = true) => modal({ title, body, ok, danger });

/* Collect named form values: checkboxes → bool, numbers → Number|null, others → trimmed string. */
function vals(form) {
  const o = {};
  for (const el of form.elements) {
    if (!el.name || el.disabled) continue;
    if (el.type === 'checkbox') o[el.name] = el.checked;
    else if (el.type === 'radio') { if (el.checked) o[el.name] = el.value; }
    else if (el.type === 'number') o[el.name] = el.value === '' ? null : Number(el.value);
    else o[el.name] = el.value.trim();
  }
  return o;
}

/* -- side panel */
const panel = {
  el: () => $('#panel'),
  open(title, body) {
    const p = this.el(); $('#panelTitle').textContent = title; $('#panelBody').innerHTML = String(body);
    if (!p.classList.contains('open')) { state.panelReturn = doc.activeElement; p.classList.add('open'); p.setAttribute('aria-hidden', 'false'); p.focus(); }
  },
  set(body) { $('#panelBody').innerHTML = String(body); },
  close() {
    const p = this.el(); if (!p.classList.contains('open')) return;
    p.classList.remove('open'); p.setAttribute('aria-hidden', 'true');
    if (state.panelReturn && state.panelReturn.focus) state.panelReturn.focus();
    state.onPanelClose && state.onPanelClose(); state.onPanelClose = null;
  },
  isOpen() { return this.el().classList.contains('open'); },
};

/* -- delegation */
const A = {}, F = {}, C = {}, I = {};
async function run(btn, fn) {
  if (btn) btn.disabled = true;
  try { await fn(); } catch (e) { if (!(e instanceof ApiError)) { console.error(e); toast(String(e.message || e), 'error'); } }
  finally { if (btn && btn.isConnected) btn.disabled = false; }
}
doc.addEventListener('click', e => {
  const t = e.target.closest('[data-act]'); if (!t || t.disabled) return;
  const fn = A[t.dataset.act]; if (!fn) return; e.preventDefault(); run(t, () => fn(t, e));
});
doc.addEventListener('submit', e => {
  const f = e.target.closest('form[data-form]'); if (!f) return; e.preventDefault();
  const fn = F[f.dataset.form]; if (!fn) return; showErrors(f, null);
  run(f.querySelector('button[type=submit],button:not([type])'), async () => {
    try { await fn(f, vals(f)); } catch (er) { if (er instanceof ApiError) showErrors(f, er); throw er; }
  });
});
doc.addEventListener('change', e => { const t = e.target.closest('[data-change]'); if (t && C[t.dataset.change]) run(null, () => C[t.dataset.change](t, e)); });
doc.addEventListener('input', e => { const t = e.target.closest('[data-input]'); if (t && I[t.dataset.input]) I[t.dataset.input](t, e); });
doc.addEventListener('keydown', e => {
  if (e.key !== 'Escape' || dlg().open) return;
  if (panel.isOpen()) { panel.close(); e.preventDefault(); } else if (doc.body.classList.contains('nav-open')) setNav(false);
});
A['panel-close'] = () => panel.close();
A.refresh = () => refresh();
I['nav-search'] = t => { $$('#nav a[data-route]').forEach(a => { a.hidden = !a.textContent.toLowerCase().includes(t.value.toLowerCase()); }); };
A.go = t => { location.hash = t.dataset.href; };

/* -- theme & nav */
/* Theme: stored choice, else follow prefers-color-scheme (live). */
const mqLight = matchMedia('(prefers-color-scheme: light)');
function applyTheme() {
  const t = localStorage.getItem('lb-theme');
  doc.documentElement.dataset.theme = t === 'light' || t === 'dark' ? t : 'light';
}
mqLight.addEventListener('change', applyTheme);
const currentTheme = () => doc.documentElement.dataset.theme;
function setNav(open) {
  doc.body.classList.toggle('nav-open', open); $('#scrim').hidden = !open;
  $('#navToggle').setAttribute('aria-expanded', String(open));
}

/* -- router */
const state = { session: null, identity: null, panelReturn: null, onPanelClose: null };
const views = {};
let seq = 0, cur = null, pollT = null, pollFn = null, pollMs = 0;
function schedule() {
  clearTimeout(pollT);
  if (!pollFn || doc.visibilityState !== 'visible') return;
  const f = pollFn;
  pollT = setTimeout(async () => { try { await f(true); } catch { /* shown on next manual load */ } if (f === pollFn) schedule(); }, pollMs);
}
doc.addEventListener('visibilitychange', () => {
  if (doc.visibilityState !== 'visible' || !pollFn) { clearTimeout(pollT); return; }
  const f = pollFn; f(true).catch(() => {}).finally(() => { if (f === pollFn) schedule(); });
});
function parseHash() {
  const h = location.hash.replace(/^#\/?/, ''); const i = h.indexOf('?');
  return { name: (i < 0 ? h : h.slice(0, i)).split('/')[0], params: new URLSearchParams(i < 0 ? '' : h.slice(i + 1)) };
}
function stamp() { $('#lastUpdated').textContent = 'updated ' + new Date().toLocaleTimeString(); }
async function route() {
  const { name, params } = parseHash();
  clearTimeout(pollT); pollFn = null; state.onPanelClose = null; panel.close(); setNav(false);
  const my = ++seq, main = $('#main');
  cur = {
    name, params, main, alive: () => my === seq,
    render(h) { if (my === seq) { main.innerHTML = String(h); } },
    poll(ms, fn) { if (my !== seq) return; pollFn = fn; pollMs = ms; schedule(); },
  };
  $$('#nav a[data-route]').forEach(a => { const on = a.dataset.route === name; a.classList.toggle('active', on); on ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current'); });
  const navA = $(`#nav a[data-route="${CSS.escape(name)}"]`);
  doc.title = (navA ? navA.textContent + ' · ' : '') + 'Loopback';
  $('#routeTitle').textContent = navA ? navA.textContent : 'Overview';
  main.innerHTML = '<div class="card skeleton" aria-busy="true"><span class="spin"></span> Loading…</div>';
  try { await (views[name] || notFound)(cur); if (my === seq) stamp(); }
  catch (e) {
    if (my !== seq) return;
    main.innerHTML = String(H`${head('Something went wrong')}<div class="card">${errorsHtml(e)}<p class="muted">The endpoint may be unavailable in this runtime version.</p><button class="primary" data-act="refresh">Retry</button></div>`);
  }
}
const refresh = () => route();
function notFound(v) { v.render(H`${head('Not found')}<div class="card"><p>No such page. <a href="#/">Back to overview</a>.</p></div>`); }

/* -- shared fragments */
function exposureBadges(ing) {
  const out = [];
  if (!ing) return out;
  if (ing.local && ing.local.enabled !== false) out.push(badge('local', 'ok'));
  const ts = ing.tailscale || {}, cf = ing.cloudflare || {};
  if (ts.enabled) out.push(ts.mode === 'funnel' ? badge('public (funnel)', 'bad') : badge('tailnet', 'info'));
  if (cf.enabled) {
    out.push(badge('public (cloudflare)', 'bad'));
    if (cf.access_policy === 'cloudflare-access' || cf.access_policy === 'both') out.push(badge('access-protected', 'ok'));
  }
  return out;
}
function enforcement(p, sb) {
  const e = (p && p.enforcement) || '';
  if (e === 'os-sandbox' || (sb && sb.active && (!p || p.target === 'local'))) return badge(`OS sandbox: ${(sb && sb.backend) || 'active'} active`, 'ok');
  if (e === 'none' || e === 'unenforced') return badge('Unenforced', 'bad');
  return badge('Path checks only', 'warn');
}
function permsHtml(p, sb) {
  if (!p) return H`<div class="empty">Permissions unavailable.</div>`;
  const sh = p.shell || {};
  return H`<p><b>${p.profile_title || p.profile}</b> <span class="muted">(${p.profile})</span></p><p class="muted small">${p.profile_summary || ''}</p>
    ${kv([
      ['Enforcement', enforcement(p, sb)],
      ['Allowed', H`<div class="chips">${arr(p.allowed_roots).map(r => H`<span class="chip"><code>${r.path}</code> ${badge(r.mode, r.mode === 'rw' ? 'warn' : 'info')}${r.exists === false ? badge('missing', 'off') : ''}</span>`)}</div>`],
      ['Denied', H`${arr(p.denied_roots).length} paths`],
      ['Other locations', badge('Denied', 'ok')],
      ['Shell', H`${okb(sh.enabled, 'enabled', 'disabled')} ${sh.reason ? H`<span class="muted small">${sh.reason}</span>` : ''} ${sh.approvals ? badge('approvals: ' + sh.approvals) : ''}`],
      ['Process control', okb(p.process_control, 'allowed', 'off')],
      ['Fleet', okb(p.fleet, 'allowed', 'off')],
      ['Sandbox required', yes(p.sandbox_required)],
    ])}`;
}
function sandboxHtml(sb) {
  if (!sb) return H`<div class="empty">Sandbox status unavailable.</div>`;
  return H`${sb.active ? al('ok', 'OS sandbox active', 'Shell commands run confined to the permitted locations.')
    : H`<div class="alert ${sb.restricted_shell_permitted ? 'warning' : 'critical'}"><span><b>${sb.restricted_shell_permitted ? 'Path checks only — no OS confinement' : 'Unenforced — restricted shell is blocked'}</b>${sb.reason || 'No OS sandbox backend is active.'}</span></div>`}
    ${kv([['Backend', H`<code>${sb.backend}</code>`], ['Requested', sb.requested], ['Available', yes(sb.available)], ['Active', okb(sb.active, 'active', 'inactive')], ['ABI', sb.abi], ['Denied sub-paths enforced', yn(sb.enforces_denied_subpaths)], ['Require enforcement', yn(sb.require_enforcement)], ['Unenforced acknowledged', yn(sb.acknowledge_unenforced)]])}
    ${arr(sb.caveats).length ? H`<p class="small muted">Caveats:</p><ul class="small">${sb.caveats.map(c => H`<li>${c}</li>`)}</ul>` : ''}`;
}
function approvalsTable(list) {
  return table(['Request', 'Target', 'Requested', 'Expires', ['', 'act']], list.map(a => H`<tr><td>${a.summary}</td><td>${targetBadge(a.target)}</td><td>${time(a.created_at)}</td><td>${time(a.expires_at)}</td>
    <td class="act"><button class="sm primary" data-act="approve" data-id="${a.id}" data-uses="1">Approve once</button> <button class="sm" data-act="approve" data-id="${a.id}" data-uses="20" data-ttl="3600">Approve 1h</button> <button class="sm danger" data-act="deny" data-id="${a.id}">Deny</button></td></tr>`), 'No pending approvals.');
}
const targetBadge = t => !t || t === 'local' ? badge('local', 'accent') : badge(t, 'info');
A.approve = async t => { await post(`/api/admin/approvals/${enc(t.dataset.id)}/approve`, { uses: Number(t.dataset.uses || 1), ttl_seconds: t.dataset.ttl ? Number(t.dataset.ttl) : null }); done('Approved'); };
A.deny = async t => { await post(`/api/admin/approvals/${enc(t.dataset.id)}/deny`); done('Denied'); };
function auditTable(rows) {
  return table(['When', 'Principal', 'Tool', 'Status', ['ms', 'num'], 'Target'], rows.map(x => {
    const d = x.detail || {};
    const who = x.principal_display || x.principal || d.principal_display || d.principal || d.client || '';
    return H`<tr><td>${time(x.ts)}</td><td>${typeof who === 'object' ? who.display || who.kind : who}</td><td><code>${x.tool}</code></td><td>${badge(x.status, x.status === 'ok' ? 'ok' : x.status === 'denied' || x.status === 'error' ? 'bad' : 'warn')}</td><td class="num">${x.duration_ms ?? ''}</td><td>${targetBadge(x.target)}</td></tr>`;
  }), 'No MCP calls recorded yet.');
}
function alertsHtml(alerts) {
  return alerts.map(a => {
    let href = a.action || '';
    if (href.includes('#')) href = '#' + href.split('#')[1]; else if (href && !href.startsWith('#')) href = '';
    return H`<div class="alert ${a.level || 'info'}" role="${a.level === 'critical' || a.level === 'error' ? 'alert' : 'status'}"><span><b>${a.title || a.code || a.level}</b>${a.message}</span>${href ? H`<a class="btn sm" href="${href}">${a.code === 'legacy_token' ? 'Migrate' : 'Open'}</a>` : ''}</div>`;
  });
}

/* ============================================================ VIEWS */

/* -- Overview */
const per = x => x == null ? '—' : Number(x).toFixed(1) + '%';
function ring(value, label, detail = '') {
  const n = value == null ? 0 : Math.max(0, Math.min(100, value));
  return H`<div class="ring-stat"><svg viewBox="0 0 48 48" role="img" aria-label="${label}: ${per(value)}"><circle class="ring-track" cx="24" cy="24" r="20"/><circle class="ring-fill" cx="24" cy="24" r="20" pathLength="100" stroke-dasharray="${n} 100"/><text x="24" y="27" text-anchor="middle">${value == null ? '—' : Math.round(n) + '%'}</text></svg><span><b>${label}</b><small>${detail}</small></span></div>`;
}
function workerCards(workers) {
  return H`<div class="worker-cards">${workers.map(w => {
    const m = w.worker || {}, h = w.host || {};
    return H`<article class="worker-card" data-testid="worker-card"><div class="row"><span class="machine-icon" aria-hidden="true">▤</span><div class="grow"><b>${w.target === 'local' ? 'This machine' : w.target}</b><div class="small muted">${w.target === 'local' ? 'Controller + local services' : 'Remote worker'}</div></div>${st(w.status)}</div>
      ${w.worker ? H`<div class="worker-rings">${ring(m.cpu_percent, 'Loopback CPU', h.cpu_count + ' cores · host ' + per(h.cpu_percent))}${ring(m.memory_percent, 'Loopback memory', bytes(m.rss_bytes) + ' / ' + bytes(h.memory_total))}</div><div class="worker-network"><span>↓ ${bytes(m.rx_per_second)}/s</span><span>↑ ${bytes(m.tx_per_second)}/s</span><small>MCP payload</small></div><div class="small muted">Host RAM ${per(h.memory_percent)} · host traffic ↓ ${bytes(h.rx_per_second)}/s ↑ ${bytes(h.tx_per_second)}/s</div>` : H`<p class="muted">${w.error || 'Waiting for telemetry'}</p>`}
      <div class="worker-foot"><span class="small muted">Heartbeat ${rel(w.heartbeat_at)}</span><a href="#/services?target=${enc(w.target)}">Manage ↗</a></div></article>`;
  })}</div>`;
}
views[''] = async v => {
  const load = async quiet => {
    const [o, audit, fleet] = await Promise.all([api('/api/admin/overview', { quiet }), soft('/api/admin/audit?limit=8'), get('/api/admin/workers')]);
    if (!v.alive()) return;
    v.render(overview(o, arr(audit, 'rows', 'events', 'items'), fleet)); stamp();
  };
  await load(false); v.poll(5000, load);
};
function overview(o, audit, fleet) {
  const W = fleet.workers || [], B = fleet.bindings || [], local = W.find(w => w.target === 'local') || {};
  const live = W.filter(w => w.status === 'healthy');
  const running = W.flatMap(w => w.bindings || []).filter(b => b.running);
  const mem = live.reduce((sum, w) => sum + (w.worker.rss_bytes || 0), 0);
  const rx = live.some(w => w.worker.rx_per_second != null) ? live.reduce((sum, w) => sum + (w.worker.rx_per_second || 0), 0) : null;
  const tx = live.some(w => w.worker.tx_per_second != null) ? live.reduce((sum, w) => sum + (w.worker.tx_per_second || 0), 0) : null;
  const metric = (title, number, detail, tone, glyph) => H`<section class="card c3 summary-stat"><div class="row"><span class="metric-icon ${tone}" aria-hidden="true">${glyph}</span><b>${title}</b></div><div class="summary-value">${number}<small>${detail}</small></div></section>`;
  return H`${head('Overview', 'Your services, wherever they run.', H`<span class="live-label">${dot('healthy')} Refreshes every 5s</span><a class="btn primary" href="#/services?new=1">+ New service</a>`)}
    <div class="grid overview-grid" data-testid="fleet-overview">
      ${metric('Connected workers', live.length, 'of ' + W.length + ' registered', 'green', '▤')}
      ${metric('Running services', running.length, B.length + ' configured', 'blue', '↗')}
      ${metric('MCP throughput', bytes(rx) + '/s', '↑ ' + bytes(tx) + '/s outbound', 'purple', '⇄')}
      ${metric('Loopback memory', bytes(mem), 'across connected workers', 'rose', '◫')}
      ${card('Workers', H`<p class="section-desc">Loopback processes and their children. Percentages use the host’s total capacity.</p>${workerCards(W)}`, {cls: 'c12', actions: lnk('#/fleet', 'Manage workers')})}
      ${card('Service routes', table(['Service', 'Worker', 'Transport', 'Exposure', 'State'], B.map(b => {
        const owner = W.find(w => w.target === b.target), status = owner?.bindings?.find(x => x.name === b.name);
        return H`<tr><td><a href="#/services?edit=${enc(b.name)}"><b>${b.name}</b></a></td><td>${b.target}</td><td>${b.transport.toUpperCase()}</td><td>${badge(b.exposure, b.exposure.includes('funnel') || b.exposure === 'cloudflare' ? 'warn' : 'info')}</td><td>${owner?.status !== 'healthy' ? st('unreachable') : st(status?.running ? 'healthy' : 'disconnected')}</td></tr>`;
      }), 'No services yet. Add an HTTP endpoint or a stdio MCP command, then choose its worker.'), {cls: 'c7', actions: lnk('#/services', 'View all')})}
      ${card('Controller', H`<p class="section-desc">${o.identity?.hostname || ''} · Loopback ${o.version || ''}</p>${kv([['Local shell', lnk('#/shell', 'Review access')], ['Loopback CPU', per(local.worker?.cpu_percent)], ['Host CPU', per(local.host?.cpu_percent)], ['Host memory', bytes(local.host?.memory_used) + ' / ' + bytes(local.host?.memory_total)], ['OAuth clients', o.auth?.oauth_clients ?? 0], ['Policy', o.policy?.profile || '—']])}<hr class="sep"><p class="small muted">Remote services keep running when local shell access is disabled. SSH is used for worker control; MCP traffic goes to each worker directly.</p>`, {cls: 'c5', actions: lnk('#/topology', 'Topology ↗')})}
      ${card('Recent activity', auditTable(audit), {cls:'c12', actions:lnk('#/logs', 'View all')})}
    </div>`;
}

/* -- Topology (rendering lives in topology.js) */
views.topology = async v => {
  if (!window.LBTopology) throw new ApiError('topology.js failed to load', 0);
  await window.LBTopology.mount(v, LB);
};

/* -- Local machine */
views.local = async v => {
  const [id, perm, cfg] = await Promise.all([get('/api/admin/identity'), get('/api/admin/permissions?target=local'), get('/api/admin/config')]);
  state.identity = id;
  const flat = Object.entries(id || {}).filter(([k, x]) => x === null || typeof x !== 'object');
  v.render(H`${head('Local machine', 'The gateway machine, as the service user sees it.')}
    <div class="grid">
    ${card('Identity', safeKv(Object.fromEntries(flat)))}
    ${card('Host facts', id && id.host_facts ? safeKv(id.host_facts) : H`<div class="empty">No host facts reported.</div>`)}
    ${card('Effective permissions', permsHtml(perm, perm.sandbox), { cls: 'c6', actions: H`${lnk('#/filesystem', 'Filesystem')}${lnk('#/shell', 'Shell')}` })}
    ${card('Sandbox', H`${sandboxHtml(perm.sandbox)}<div class="row"><button class="primary sm" data-act="selftest">Run self-test</button></div><div id="selftest"></div>`)}
    ${card('Shell & initial directory', H`<form data-form="shellcwd">${errBox}
      ${fld('Shell', 'f_shell', inp('shell', cfg.shell, { ph: id.effective_shell || id.shell || 'auto-detect' }), 'Shell for commands, jobs and terminals. Empty = auto-detect.')}
      ${fld('Initial working directory', 'f_initial_cwd', inp('initial_cwd', cfg.initial_cwd, { ph: id.initial_cwd || id.home || '~' }), 'Leave empty to start in the service user\'s home directory.')}
      <div class="row end"><button type="submit" class="primary">Save</button></div></form>`)}
    </div>`);
};
A.selftest = async () => {
  const box = $('#selftest'); box.innerHTML = '<p><span class="spin"></span> Running sandbox self-test…</p>';
  try {
    const r = await post('/api/admin/sandbox/self-test');
    box.innerHTML = String(H`<hr class="sep"><div class="alert ${r.ok ? 'ok' : 'critical'}"><span><b>${r.ok ? 'Sandbox self-test passed' : 'Sandbox self-test failed'}</b>${r.reason || `backend ${r.backend}`}</span></div>
      ${table(['Check', 'Result', 'Detail'], arr(r.checks).map(c => H`<tr><td><code>${c.name}</code></td><td>${okb(c.passed, 'passed', 'failed')}</td><td class="small muted">${c.error || c.stderr || (c.returncode !== undefined ? 'exit ' + c.returncode : '')}</td></tr>`), 'No checks ran (sandbox not enforced).')}`);
  } catch (e) { box.innerHTML = String(errorsHtml(e)); }
};
F.shellcwd = async (f, o) => { await put('/api/admin/config', { shell: o.shell || null, initial_cwd: o.initial_cwd || null }); state.identity = null; done(); };

/* -- Fleet */
const kindSel = (name, curv, id) => H`<select id="${id || 'f_' + name}" name="${name}">${KINDS.map(k => opt(k, curv || 'unknown'))}</select>`;
views.fleet = async v => {
  const [nodes, ssh, workers] = await Promise.all([get('/api/admin/nodes'), soft('/api/admin/ssh'), get('/api/admin/workers')]);
  const N = arr(nodes, 'nodes'), S = arr(ssh, 'targets', 'ssh');
  const edit = v.params.get('edit'), e = N.find(n => n.name === edit) || {};
  v.render(H`${head('Fleet', 'Machines this gateway routes to. Credentials are write-only.', H`<a class="btn sm" href="#/topology">Topology</a>`)}
    <div class="grid">
    ${card('Worker health', H`<p class="section-desc">Install a small worker over SSH to run services on that machine. No public control port is opened.</p><div id="fleet-health">${workerCards(workers.workers || [])}</div><div class="row">${S.map(t => H`<button class="sm" data-act="install-worker" data-target="${t.name}">Install / update ${t.name}</button>`)}</div>`, 'c12')}
    ${card('Nodes', table(['', 'Name', 'Display name', 'URL', 'Kind', 'Group', 'State', 'Last probe', ['', 'act']], N.map(n => {
      const p = n.last_probe || {};
      return H`<tr><td>${dot(p.status || 'untested')}</td><td><b>${n.name}</b></td><td>${n.display_name || ''}</td><td><code>${n.url}</code></td><td>${n.kind || ''}</td><td>${n.group || ''}</td><td>${okb(n.enabled !== false, 'enabled', 'disabled')}</td>
        <td>${p.status ? H`${st(p.status)} <span class="muted small">${p.latency_ms != null ? p.latency_ms + ' ms · ' : ''}${rel(p.checked_at)}</span>` : st('untested')}</td>
        <td class="act"><button class="sm" data-act="probe" data-kind="nodes" data-name="${n.name}">Probe</button> <a class="btn sm" href="#/fleet?edit=${enc(n.name)}">Edit</a> <button class="sm danger" data-act="remove-machine" data-kind="nodes" data-name="${n.name}">Remove</button></td></tr>`;
    }), 'No Loopback nodes registered.'), 'c12')}
    ${card(edit ? 'Edit node ' + edit : 'Add Loopback node', H`<form data-form="node">${errBox}<div class="fields">
      ${fld('Name', 'f_name', inp('name', e.name, { req: true, ph: 'build-box', extra: edit ? 'readonly' : 'pattern="[A-Za-z0-9._-]+"' }))}
      ${fld('MCP URL', 'f_url', inp('url', e.url, { type: 'url', req: true, ph: 'https://host.example/mcp' }))}
      ${fld('Bearer token', 'f_token', inp('token', '', { type: 'password', ph: edit ? 'unchanged' : 'optional', extra: 'autocomplete="new-password"' }), 'Write-only. Stored mode 0600 and never displayed.')}
      ${fld('Display name', 'f_display_name', inp('display_name', e.display_name))}
      ${fld('Kind', 'f_kind', kindSel('kind', e.kind))}
      ${fld('Group', 'f_group', inp('group', e.group, { ph: 'e.g. oracle' }))}
      ${fld('Description', 'f_description', inp('description', e.description))}</div>
      <label class="check"><input type="checkbox" name="enabled"${chk(e.enabled !== false)}> Enabled</label>
      <div class="row end">${edit ? H`<a class="btn" href="#/fleet">Cancel</a>` : ''}<button type="submit" class="primary">${edit ? 'Save node' : 'Add node'}</button></div></form>`)}
    ${card('SSH targets', table(['', 'Name', 'Address', 'Kind', 'Group', ['', 'act']], S.map(s => H`<tr><td>${dot((s.last_probe || {}).status || 'untested')}</td><td><b>${s.name}</b>${s.display_name ? H`<div class="small muted">${s.display_name}</div>` : ''}</td><td><code>${s.user ? s.user + '@' : ''}${s.host}${s.port && s.port !== 22 ? ':' + s.port : ''}</code></td><td>${s.kind || ''}</td><td>${s.group || ''}</td>
        <td class="act"><button class="sm" data-act="probe" data-kind="ssh" data-name="${s.name}">Probe</button> <a class="btn sm" href="#/ssh?edit=${enc(s.name)}">Edit</a> <a class="btn sm" href="#/filesystem?target=${enc(s.name)}">Permissions</a></td></tr>`), 'No SSH targets. Add one under SSH config.'), { cls: 'c6', actions: H`<a class="btn sm primary" href="#/ssh">Add SSH target</a>` })}
    </div>`);
  v.poll(5000, async () => { const data = await get('/api/admin/workers'); if (v.alive()) $('#fleet-health').innerHTML = String(workerCards(data.workers || [])); stamp(); });
};
A['install-worker'] = async t => {
  if (!await confirmDo('Install worker on ' + t.dataset.target + '?', H`<p>Copy the Loopback worker and install its Python dependencies under the SSH account’s home. Existing services and tunnels stay in place.</p>`, 'Install worker', false)) return;
  const result = await post('/api/admin/workers/' + enc(t.dataset.target) + '/install');
  toast(result.setup === 'ready' ? 'Worker ready' : 'Worker installing. Check its status in a moment.', 'ok'); refresh();
};
F.node = async (f, o) => {
  const body = { name: o.name, url: o.url, enabled: o.enabled, description: o.description, display_name: o.display_name || null, kind: o.kind, group: o.group || null };
  if (o.token) body.token = o.token;
  await post('/api/admin/nodes', body); f.reset(); toast('Node saved', 'ok');
  if (parseHash().params.get('edit')) location.hash = '#/fleet'; else refresh();
};
/* Probe an SSH target or node; result opens in the side panel. */
A.probe = async t => {
  const kind = t.dataset.kind, name = t.dataset.name;
  const old = t.textContent; t.innerHTML = '<span class="spin"></span>';
  try {
    const r = await post(`/api/admin/${kind}/${enc(name)}/probe`);
    toast(`${name}: ${STATUS_LABEL[r.status] || r.status}`, r.status === 'healthy' ? 'ok' : 'error');
    if (!t.closest('#panel')) { panel.open('Probe · ' + name, probeHtml(r)); state.onPanelClose = refresh; }
    return r;
  } finally { if (t.isConnected) t.textContent = old; }
};
const probeHtml = r => H`<section><h3>Result</h3>${kv([['Status', st(r.status)], ['Latency', r.latency_ms != null ? r.latency_ms + ' ms' : null], ['Checked', time(r.checked_at || now())], ['Error', r.error || null]])}</section>
  ${r.remote ? H`<section><h3>Remote</h3>${safeKv(r.remote)}</section>` : ''}`;
A['remove-machine'] = async t => {
  const kind = t.dataset.kind, name = t.dataset.name;
  if (!await confirmDo(`Remove ${name}?`, H`<p>This removes the ${kind === 'ssh' ? 'SSH target' : 'Loopback node'} <b>${name}</b> and its stored credential.</p>`, 'Remove')) return;
  await del(`/api/admin/${kind}/${enc(name)}`); toast('Removed', 'ok'); panel.close(); refresh();
};

/* -- Independent service bindings */
views.services = async v => {
  const [fleet, ssh] = await Promise.all([get('/api/admin/workers'), get('/api/admin/ssh')]);
  const B = fleet.bindings || [], W = fleet.workers || [], S = arr(ssh, 'targets', 'ssh');
  const edit = v.params.get('edit'), b = B.find(x => x.name === edit) || {};
  const selectedTarget = b.target || v.params.get('target') || 'local';
  const drawRows = data => table(['Service', 'Runs on', 'Connection', 'Exposure', 'Status', ['', 'act']], (data.bindings || []).map(x => {
    const worker = data.workers.find(w => w.target === x.target), live = worker?.bindings?.find(r => r.name === x.name);
    const canRecover = x.desired === 'running' && worker?.status === 'healthy' && !live?.running;
    return H`<tr><td><b>${x.name}</b><div class="small muted">${live?.url || x.url || 'Not started'}</div></td><td>${x.target}</td><td>${x.transport.toUpperCase()}<div class="small muted">Port ${x.port}</div></td><td>${badge(x.exposure, x.exposure === 'cloudflare' || x.exposure.includes('funnel') ? 'warn' : 'info')}</td><td>${worker?.status !== 'healthy' ? st('unreachable') : st(live?.running ? 'healthy' : 'disconnected')}</td><td class="act">${canRecover ? H`<button class="sm" data-act="binding-action" data-name="${x.name}" data-action="start">Start</button> ` : ''}<button class="sm" data-act="binding-action" data-name="${x.name}" data-action="${x.desired === 'running' ? 'stop' : 'start'}">${x.desired === 'running' ? 'Stop' : 'Start'}</button> <button class="sm" data-act="binding-action" data-name="${x.name}" data-action="credential">Connect</button> <a class="btn sm" href="#/services?edit=${enc(x.name)}">Edit</a> <button class="sm danger" data-act="binding-action" data-name="${x.name}" data-action="delete">Delete</button></td></tr>`;
  }), 'No service bindings. Add your first service below.');
  v.render(H`${head('Services & tunnels', 'Choose what runs, where it runs, and how clients reach it.', H`<a class="btn" href="#/fleet">Manage workers</a>`)}
    <div class="grid" data-testid="services-view">
      ${card('Services', H`<div id="service-list">${drawRows(fleet)}</div>`, 'c12')}
      ${card(edit ? 'Edit ' + edit : 'New service', H`<form data-form="binding" data-testid="binding-form">${errBox}<div class="fields">
        ${fld('Service name', 'f_name', inp('name', b.name, {req:true, ph:'research-tools', extra:edit ? 'readonly' : 'pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,39}"'}))}
        ${fld('Run on', 'f_target', H`<select id="f_target" name="target">${['local', ...S.map(s => s.name)].map(t => opt(t, selectedTarget, t === 'local' ? 'This machine' : t))}</select>`, 'Install the worker from Fleet before starting a remote service.')}
        ${fld('Transport', 'f_transport', H`<select name="transport" id="f_transport" data-change="binding-transport">${opt('http', b.transport || 'http', 'HTTP / Streamable HTTP')}${opt('stdio', b.transport, 'stdio MCP command')}</select>`)}
        ${fld('Worker port', 'f_port', inp('port', b.port || 3031, {type:'number', req:true, extra:'min="1024" max="65535"'}), 'A unique loopback listener on the selected machine.')}
      </div><div id="http-fields"${b.transport === 'stdio' ? raw(' hidden') : ''}>
        ${fld('Upstream MCP URL', 'f_upstream', inp('upstream', b.upstream, {ph:'http://127.0.0.1:8080/mcp'}), 'Resolved on the worker. The controller does not carry this traffic.')}
        ${fld('Upstream bearer token file', 'f_upstream_token_file', inp('upstream_token_file', b.upstream_token_file, {ph:'optional path on worker'}), 'Client credentials are never forwarded to the upstream.')}
      </div><div id="stdio-fields"${b.transport !== 'stdio' ? raw(' hidden') : ''}>
        ${fld('Command arguments (JSON)', 'f_command', H`<textarea id="f_command" name="command" placeholder='["npx", "-y", "your-mcp-server"]'>${JSON.stringify(b.command || [], null, 2)}</textarea>`, 'Runs as the worker’s OS user. Use a dedicated account for shell-capable MCP servers.')}
        ${fld('Working directory', 'f_cwd', inp('cwd', b.cwd, {ph:'optional path on worker'}))}
      </div><div class="fields">
        ${fld('Expose through', 'f_exposure', H`<select name="exposure" id="f_exposure" data-change="binding-exposure">${['private','tailscale-serve','tailscale-funnel','cloudflare'].map(t => opt(t, b.exposure || 'private', {'private':'Loopback only','tailscale-serve':'Tailscale Serve · private tailnet','tailscale-funnel':'Tailscale Funnel · public','cloudflare':'Cloudflare Tunnel · public'}[t]))}</select>`)}
        ${fld('Tailscale HTTPS port', 'f_https_port', inp('https_port', b.https_port || 8443, {type:'number', extra:'min="1" max="65535"'}), 'Funnel allows 443, 8443 or 10000 per machine. Occupied listeners are protected.')}
      </div><div id="cloudflare-fields"${b.exposure !== 'cloudflare' ? raw(' hidden') : ''}>
        ${fld('Cloudflare hostname', 'f_hostname', inp('hostname', b.hostname, {ph:'tools.example.com'}))}
        ${fld('Tunnel token file', 'f_token_file', inp('token_file', b.token_file, {ph:'~/.config/cloudflared/service.token'}), 'Token stays on the worker. In Cloudflare, point this hostname to http://127.0.0.1:<worker port>.')}
      </div><label class="check"><input type="checkbox" name="confirm_public"${chk(b.confirm_public)}> I understand that Cloudflare and Funnel expose this service publicly, protected by its own Loopback OAuth.</label>
      <p class="small muted">Each service has independent OAuth credentials. Tunnels start only when you press Start. Existing Tailscale listeners are never replaced.</p>
      <div class="row end">${edit ? lnk('#/services', 'Cancel') : ''}<button type="submit" class="primary">Save service</button></div></form>`, 'c8')}
      ${card('Designed to run elsewhere', H`<div class="note-icon">↗</div><h3>One dashboard. Your whole fleet.</h3><p class="muted">Host a stdio MCP on an Oracle VM, publish another through Cloudflare, and keep a private Tailscale service on your laptop.</p><hr class="sep"><h3>Local access is your choice</h3><p class="muted">Disabling the controller’s shell leaves remote services available. A worker exposes only the MCP you configure.</p><hr class="sep"><h3>Existing routes stay yours</h3><p class="muted">Choose a free HTTPS port. Loopback refuses to replace existing listeners, including your orchestration endpoint.</p><a class="btn sm" href="#/topology">Explore topology ↗</a>`, 'c4')}
    </div>`);
  v.poll(5000, async () => { const data = await get('/api/admin/workers'); if (v.alive()) $('#service-list').innerHTML = String(drawRows(data)); stamp(); });
};
C['binding-transport'] = t => { $('#http-fields').hidden = t.value !== 'http'; $('#stdio-fields').hidden = t.value !== 'stdio'; };
C['binding-exposure'] = t => { $('#cloudflare-fields').hidden = t.value !== 'cloudflare'; };
F.binding = async (f, o) => {
  let command = [];
  if (o.transport === 'stdio') { try { command = JSON.parse(o.command); } catch { throw new ApiError('Command must be a JSON array of arguments.', 400); } }
  await post('/api/admin/bindings', {...o, command}); toast('Service saved. Press Start when ready.', 'ok'); location.hash = '#/services'; refresh();
};
A['binding-action'] = async t => {
  const action = t.dataset.action;
  if ((action === 'delete' || action === 'stop') && !await confirmDo(action === 'delete' ? 'Delete service?' : 'Stop service?', H`<p>${t.dataset.name}</p>`, action === 'delete' ? 'Delete' : 'Stop')) return;
  const result = await post('/api/admin/bindings/' + enc(t.dataset.name) + '/' + action);
  if (action === 'credential') {
    panel.open('Connect ' + t.dataset.name, H`<p>Open the enrollment URL and use this one-time code to set the service’s OAuth password. It expires after ten minutes.</p><p><a href="${result.url}" target="_blank" rel="noopener noreferrer">${result.url}</a></p><div class="secret-once">${result.code}</div><p class="small muted">Private services require SSH port forwarding to reach the enrollment page. Add the service’s /mcp URL in your MCP client and authorize through OAuth.</p>`);
  } else { toast(action === 'start' ? 'Service started' : action === 'stop' ? 'Service stopped' : 'Service deleted', 'ok'); refresh(); }
};

/* -- Filesystem */
const fs = { target: 'local', roots: [], denied: [], override: false, timer: null };
views.filesystem = async v => {
  const target = v.params.get('target') || 'local';
  const [policy, ssh, nodes, perm] = await Promise.all([get('/api/admin/policy'), soft('/api/admin/ssh'), soft('/api/admin/nodes'), get('/api/admin/permissions?target=' + enc(target))]);
  const tp = target === 'local' ? policy : ((policy.targets || {})[target] || null);
  fs.target = target; fs.override = target !== 'local' && !!tp;
  fs.roots = (tp && tp.allowed_roots ? tp.allowed_roots : arr(perm.allowed_roots)).map(r => typeof r === 'string' ? { path: r, mode: 'rw' } : { path: r.path, mode: r.mode === 'ro' ? 'ro' : 'rw' });
  fs.denied = (tp && tp.denied_roots ? tp.denied_roots : arr(perm.denied_roots)).slice();
  fs.perm = perm;
  const targets = ['local', ...arr(ssh, 'targets').map(s => s.name), ...arr(nodes, 'nodes').map(n => n.name)];
  if (!targets.includes(target)) targets.push(target);
  v.render(H`${head('Filesystem', 'Locations MCP clients may read or write. Everything else is denied.', H`<label for="fsTarget" class="muted">Target</label><select id="fsTarget" data-change="fs-target">${targets.map(t => opt(t, target, t === 'local' ? 'local (this machine)' : t))}</select>`)}
    ${al('info', 'Other locations: Denied', H`Only the allowed roots below are reachable on <b>${target}</b>; denied roots win over allowed roots.`, H`${enforcement(perm, perm.sandbox)}`)}
    ${perm.sandbox && !perm.sandbox.active && target === 'local' ? al('warning', 'Path checks only', H`File tools check paths, but shell commands are not OS-confined. ${perm.sandbox.reason || ''}`, H`<a class="btn sm" href="#/local">Sandbox</a>`) : ''}
    ${target !== 'local' ? H`<div class="alert ${fs.override ? 'info' : 'warning'}"><span>${fs.override ? 'This target has its own permission override.' : 'No override yet: saving creates one for this target only.'}</span>${fs.override ? H`<button class="sm danger" data-act="fs-drop">Remove override</button>` : ''}</div>` : ''}
    <div class="grid">${card('Allowed roots', H`<div id="fsRoots"></div>
      <hr class="sep"><form data-form="fs-add" class="row" autocomplete="off"><input id="fsPath" name="path" class="grow" placeholder="${target === 'local' ? '~/projects or /srv/data' : '/home/user/app'}" data-input="fs-validate" aria-label="Path to allow" required>
      <select name="mode" aria-label="Access mode"><option value="rw">read/write</option><option value="ro">read-only</option></select><button type="submit">Add path</button></form><div id="fsVal" class="small" aria-live="polite"></div>`, 'c7')}
    ${card('Denied roots', H`<p class="small muted">One path per line. Credentials (SSH, cloud, Loopback config) are denied by default.</p><textarea id="fsDenied" aria-label="Denied roots" spellcheck="false">${fs.denied.join('\n')}</textarea>`, 'c5')}
    <div class="c12"><div id="fsErr"></div><div class="row end"><button data-act="refresh">Discard changes</button><button class="primary" data-act="fs-save">Save permissions</button></div></div></div>`);
  drawRoots();
};
function drawRoots() {
  const box = $('#fsRoots'); if (!box) return;
  const ex = {}; arr(fs.perm && fs.perm.allowed_roots).forEach(r => { ex[r.path] = r; });
  box.innerHTML = String(table(['Path', 'Resolved', 'Exists', 'Mode', ['', 'act']], fs.roots.map((r, i) => {
    const e = ex[r.path];
    return H`<tr><td><code>${r.path}</code></td><td class="small muted">${e ? e.resolved : 'save to resolve'}</td><td>${e ? okb(e.exists, 'yes', 'missing') : '—'}</td>
      <td><button class="sm" data-act="fs-mode" data-i="${i}" aria-label="Toggle mode for ${r.path}">${r.mode === 'rw' ? 'read/write' : 'read-only'}</button></td><td class="act"><button class="sm danger" data-act="fs-revoke" data-i="${i}">Revoke</button></td></tr>`;
  }), 'No allowed roots: file tools cannot reach anything.'));
}
C['fs-target'] = t => { location.hash = '#/filesystem?target=' + enc(t.value); };
A['fs-mode'] = t => { const r = fs.roots[+t.dataset.i]; r.mode = r.mode === 'rw' ? 'ro' : 'rw'; drawRoots(); };
A['fs-revoke'] = t => { fs.roots.splice(+t.dataset.i, 1); drawRoots(); };
F['fs-add'] = (f, o) => {
  if (!o.path) return;
  if (fs.roots.some(r => r.path === o.path)) { toast('Already allowed', 'error'); return; }
  fs.roots.push({ path: o.path, mode: o.mode }); f.reset(); $('#fsVal').innerHTML = ''; drawRoots();
};
I['fs-validate'] = t => {
  clearTimeout(fs.timer); const p = t.value.trim(), out = $('#fsVal');
  if (!p) { out.innerHTML = ''; return; }
  if (fs.target !== 'local') { out.innerHTML = '<span class="muted">Remote paths are validated by the target when used.</span>'; return; }
  fs.timer = setTimeout(async () => {
    try {
      const r = await api('/api/admin/permissions/validate-path', { method: 'POST', body: { path: p }, quiet: true });
      if (t.value.trim() !== p) return;
      out.innerHTML = String(H`<div class="row">${r.exists ? badge(r.is_dir ? 'directory' : 'file', 'ok') : badge('does not exist', 'warn')} ${r.within_home === false ? badge('outside home', 'warn') : ''} <span>→ <code>${r.resolved}</code></span>${r.symlink_target ? H` <span class="muted">symlink to <code>${r.symlink_target}</code></span>` : ''}</div>
        ${arr(r.warnings).map(w => H`<div class="alert warning small"><span>${w}</span></div>`)}`);
      t.classList.toggle('invalid', !r.exists);
    } catch (e) { out.innerHTML = String(errorsHtml(e)); }
  }, 350);
};
A['fs-save'] = async () => {
  const denied = lines($('#fsDenied').value), box = $('#fsErr'); showErrors(box, null);
  try {
    if (fs.target === 'local') await put('/api/admin/policy', { allowed_roots: fs.roots, denied_roots: denied });
    else await put('/api/admin/permissions/' + enc(fs.target), { allowed_roots: fs.roots, denied_roots: denied });
    done('Permissions saved');
  } catch (e) { if (e instanceof ApiError) box.innerHTML = String(errorsHtml(e)); throw e; }
};
A['fs-drop'] = async () => {
  if (!await confirmDo('Remove override?', H`<p><b>${fs.target}</b> falls back to the default remote permissions.</p>`, 'Remove override')) return;
  await del('/api/admin/permissions/' + enc(fs.target)); done('Override removed');
};

/* -- Shell */
views.shell = async v => {
  const [prof, perm, policy, appr, cfg] = await Promise.all([get('/api/admin/policy/profiles'), get('/api/admin/permissions?target=local'), get('/api/admin/policy'), soft('/api/admin/approvals'), get('/api/admin/config')]);
  const profiles = (prof && prof.profiles) || {}, aliases = (prof && prof.aliases) || {};
  const curP = aliases[policy.profile] || policy.profile, sb = perm.sandbox || {}, ack = !!((cfg.sandbox || {}).acknowledge_unenforced);
  const flag = (k, t, d) => H`<label class="switch"><span><b>${t}</b><small>${d}</small></span><input type="checkbox" data-change="policy-flag" data-key="${k}"${chk(policy[k])} aria-label="${t}"></label>`;
  v.render(H`${head('Shell', 'Choose how much an MCP client may do with commands on this machine.')}
    <div class="grid">${Object.entries(profiles).map(([name, p]) => H`<section class="card c4 ${name === curP ? 'sel' : ''}"><div class="card-head"><h2>${p.title || name}</h2>${name === curP ? badge('active', 'accent') : ''}</div>
      <p class="muted">${p.summary || ''}</p><div class="chips">${p.allow_shell === false ? badge('no shell', 'off') : p.allow_shell ? badge('shell') : ''}${p.file_mode ? badge('files: ' + p.file_mode) : ''}${p.approvals ? badge('approvals: ' + p.approvals) : ''}${p.sandbox_required ? badge('sandbox required', 'info') : ''}</div>
      ${name === curP ? '' : H`<div class="row end"><button class="sm" data-act="set-profile" data-name="${name}" data-title="${p.title || name}">Use ${p.title || name}</button></div>`}</section>`)}
    ${card('Effective permissions', H`${permsHtml(perm, sb)}<p class="small muted">Profile + switches + what the OS sandbox enforces.</p>`)}
    ${card('Capabilities', H`${flag('allow_process_control', 'Process control', 'List and signal processes owned by the service user.')}${flag('allow_fleet', 'Fleet routing', 'Route MCP calls to registered nodes and SSH targets.')}${flag('allow_browser', 'Browser automation', 'Optional headless browser adapter.')}${flag('allow_desktop', 'Desktop automation', 'Native keyboard, mouse and screenshots.')}`)}
    ${card('Unenforced shell acknowledgement', H`${sb.active ? H`<div class="alert ok"><span>The OS sandbox (${sb.backend}) is active; no acknowledgement is needed.</span></div>` :
      al('critical', 'No OS sandbox is active.', H`Acknowledging lets restricted profiles run shell commands <b>without confinement</b>: they can touch anything the service user can. Prefer bubblewrap or Landlock.`)}
      <label class="check"><input type="checkbox" data-change="ack"${chk(ack)}> <span>I understand shell commands may run without OS confinement on this machine<small>Writes <code>sandbox.acknowledge_unenforced</code></small></span></label>`, { cls: 'c6', id: 'ack' })}
    ${card('Pending approvals', approvalsTable(arr(appr, 'approvals')))}</div>`);
};
A['set-profile'] = async t => {
  if (!await confirmDo('Switch profile?', H`<p>Switch this machine to <b>${t.dataset.title}</b>? It applies to new MCP calls immediately.</p>`, 'Switch profile', false)) return;
  await put('/api/admin/policy', { profile: t.dataset.name }); done('Profile changed');
};
C['policy-flag'] = async t => {
  try { await put('/api/admin/policy', { [t.dataset.key]: t.checked }); toast('Saved', 'ok'); } catch (e) { t.checked = !t.checked; throw e; }
};
C.ack = async t => {
  if (t.checked && !await confirmDo('Allow unconfined shell?', H`<p>Commands from MCP clients will run <b>without OS sandboxing</b>. Path policy cannot stop a shell command from reading other files.</p>`, 'I understand')) { t.checked = false; return; }
  try { await put('/api/admin/config', { sandbox: { acknowledge_unenforced: t.checked } }); done(); } catch (e) { t.checked = !t.checked; throw e; }
};

/* -- MCP tools */
/* The server merges every enable/disable against its authoritative lists (POST /api/admin/policy/tools) and
 * returns the resulting catalog, which is rendered back verbatim. Changes are serialized through one queue so a
 * rapid second click can never overwrite the first, and the checkbox state always comes from the server. */
const tl = { queue: Promise.resolve(), tools: [] };
function toolRows(T) {
  const groups = {};
  T.forEach(t => { (groups[t.category || 'other'] = groups[t.category || 'other'] || []).push(t); });
  const ann = t => {
    const a = t.annotations || {}, c = [];
    if (a.readOnlyHint) c.push(badge('read-only', 'ok'));
    if (a.destructiveHint) c.push(badge('destructive', 'bad'));
    if (a.idempotentHint) c.push(badge('idempotent', 'info'));
    if (t.network || a.openWorldHint) c.push(badge('network', 'warn'));
    return c;
  };
  return Object.keys(groups).sort().map(g => H`<tr class="group-row" data-group="${g}"><td colspan="6"><span class="grow">${g} <span class="muted">· ${groups[g].filter(t => t.enabled !== false).length}/${groups[g].length} on</span></span>
      <span class="group-actions"><button type="button" class="sm ghost" data-act="tools-group" data-group="${g}" data-on="1">All on</button><button type="button" class="sm ghost" data-act="tools-group" data-group="${g}" data-on="0">All off</button></span></td></tr>${groups[g].map(t => H`<tr data-tool="${(t.name + ' ' + (t.title || '') + ' ' + g).toLowerCase()}" data-tool-name="${t.name}" data-tool-group="${g}">
      <td><input type="checkbox" data-change="tool-toggle" data-name="${t.name}"${chk(t.enabled !== false)} aria-label="Enable ${t.name}"></td>
      <td><code>${t.name}</code>${t.title && t.title !== t.name ? H` <b>${t.title}</b>` : ''}<div class="small muted">${String(t.description || '').slice(0, 160)}</div></td>
      <td><div class="chips">${ann(t)}</div></td><td><code class="small">${t.scope || ''}</code></td><td>${t.target || ''}</td><td>${t.approval && t.approval !== 'none' ? badge(t.approval, 'warn') : badge('none', 'off')}</td></tr>`)}`);
}
function toolSummary(T) {
  const on = T.filter(t => t.enabled !== false).length;
  return `${on} of ${T.length} tools enabled. Disabled tools are hidden from tools/list and refused at tools/call.`;
}
views.tools = async v => {
  const tools = await get('/api/admin/tools');
  const T = tl.tools = arr(tools, 'tools');
  v.render(H`${head('MCP tools', raw(`<span data-tools-summary>${esc(toolSummary(T))}</span>`), H`<input type="search" placeholder="Filter tools" data-input="tool-filter" aria-label="Filter tools">`)}
    <div class="card"><div class="row bulk-bar" data-testid="tools-bulk"><span class="small muted grow" data-tools-scope>Bulk actions apply to all ${T.length} tools.</span>
      <button type="button" class="sm" data-act="tools-bulk" data-on="1" data-testid="tools-select-all">Select all</button>
      <button type="button" class="sm" data-act="tools-bulk" data-on="0" data-testid="tools-deselect-all">Deselect all</button></div>
    ${table(['On', 'Tool', 'Annotations', 'Scope', 'Target', 'Approval'], toolRows(T), 'No tools reported.')}</div>`);
};
const visibleToolNames = () => $$('tr[data-tool]').filter(r => !r.hidden).map(r => r.dataset.toolName);
I['tool-filter'] = t => {
  const q = t.value.trim().toLowerCase();
  $$('tr[data-tool]').forEach(r => { r.hidden = !!q && !r.dataset.tool.includes(q); });
  $$('tr.group-row').forEach(r => { r.hidden = !!q && !$$(`tr[data-tool-group="${CSS.escape(r.dataset.group)}"]`).some(x => !x.hidden); });
  const n = visibleToolNames().length, scope = $('[data-tools-scope]');
  if (scope) scope.textContent = q ? `Bulk actions apply to the ${n} tools matching “${t.value.trim()}”.` : `Bulk actions apply to all ${tl.tools.length} tools.`;
  const sa = $('[data-testid="tools-select-all"]'), da = $('[data-testid="tools-deselect-all"]');
  if (sa) sa.textContent = q ? `Select ${n} shown` : 'Select all';
  if (da) da.textContent = q ? `Deselect ${n} shown` : 'Deselect all';
};
/* Apply the server's answer to the table without re-rendering (keeps filter text and focus). */
function applyTools(res) {
  const T = tl.tools = arr(res && res.tools, 'tools').length ? arr(res.tools) : tl.tools;
  T.forEach(t => { const box = $(`input[data-change="tool-toggle"][data-name="${CSS.escape(t.name)}"]`); if (box) box.checked = t.enabled !== false; });
  $$('tr.group-row').forEach(r => { const g = r.dataset.group, rows = T.filter(t => (t.category || 'other') === g); const c = $('.muted', r); if (c) c.textContent = `· ${rows.filter(t => t.enabled !== false).length}/${rows.length} on`; });
  const sum = $('[data-tools-summary]'); if (sum) sum.textContent = toolSummary(T);
  arr(res && res.warnings).forEach(w => toast(w, 'warn', 6000));
}
/* One queue for every change: the next request only starts after the previous answer is applied. */
function toolChange(body, label) {
  const job = tl.queue.then(async () => { const res = await post('/api/admin/policy/tools', body); applyTools(res); if (label) toast(label, 'ok'); return res; });
  tl.queue = job.catch(() => {});
  return job;
}
C['tool-toggle'] = async t => {
  const name = t.dataset.name, on = t.checked;
  try { await toolChange({ changes: { [name]: on } }, `${name} ${on ? 'enabled' : 'disabled'}`); }
  catch (e) { t.checked = !on; throw e; }
};
A['tools-bulk'] = async t => {
  const on = t.dataset.on === '1', names = visibleToolNames(), all = names.length === tl.tools.length;
  if (!on && all && !await confirmDo('Disable every tool?', H`<p>All <b>${names.length}</b> tools will be hidden from clients and refused until you enable them again. The policy records an explicit deny-all.</p>`, 'Disable all')) return;
  await toolChange(all ? { all: on } : { all: on, names }, `${on ? 'Enabled' : 'Disabled'} ${names.length} tool${names.length === 1 ? '' : 's'}`);
};
A['tools-group'] = async t => {
  const on = t.dataset.on === '1', names = $$(`tr[data-tool-group="${CSS.escape(t.dataset.group)}"]`).map(r => r.dataset.toolName);
  await toolChange({ all: on, names }, `${on ? 'Enabled' : 'Disabled'} ${t.dataset.group} tools`);
};

/* -- Authentication */
views.auth = async v => {
  const a = await get('/api/admin/auth');
  const tokens = arr(a.api_tokens), clients = arr(a.clients), grants = arr(a.grants), sessions = arr(a.sessions), events = arr(a.events), methods = arr(a.methods);
  const legacy = !!a.legacy_machine_token_mcp;
  const cName = id => (clients.find(c => c.client_id === id) || {}).client_name || id;
  v.render(H`${head('Authentication', 'Who can reach the MCP endpoint and the dashboard.', H`<button class="primary" data-act="create-token">Create API token</button>`)}
    ${legacy ? al('warning big', 'Legacy shared machine token is enabled for MCP', 'Its holders get full access with no per-client revocation. Migrate, then disable it.', H`<a class="btn sm" href="#legacy">Migration checklist</a>`) : ''}
    <div class="grid">
    ${card('Methods', methods.length ? H`${methods.map(m => H`<div class="alert ${m.recommended ? 'ok' : m.discouraged || /legacy/i.test(m.id || m.name || '') ? 'warning' : 'info'}"><span><b>${m.title || m.name || m.id}</b>${m.description || m.summary || ''}</span>${m.enabled === undefined ? '' : okb(m.enabled, 'enabled', 'off')}</div>`)}` :
      H`${al('ok', 'OAuth 2.1 + PKCE (recommended)', 'Per-client, scoped, revocable grants for clients that support it.')}
      ${al('info', 'API tokens', 'For bearer-only clients. Scoped, expiring, revocable.')}
      ${al('warning', 'Legacy shared machine token (discouraged)', 'One secret for everything. Keep it for local recovery only.')}`)}
    ${card('Summary', kv([['Dashboard password', okb(a.password_set, 'set', 'not set')], ['OAuth clients', a.oauth_clients ?? clients.length], ['Active grants', a.active_oauth_grants ?? grants.length], ['API tokens', a.api_tokens ?? tokens.length], ['Issuers', arr(a.issuers).length ? H`${a.issuers.map(u => H`<div><code>${u}</code></div>`)}` : null], ['Resource URIs', arr(a.resource_uris).length ? H`${a.resource_uris.map(u => H`<div><code>${u}</code></div>`)}` : null]]))}
    ${card('API tokens', table(['Name', 'Scopes', 'Created', 'Expires', 'Last used', 'State', ['', 'act']], tokens.map(t => {
      const dead = t.revoked_at || (t.expires_at && t.expires_at < now());
      return H`<tr><td><b>${t.name}</b><div class="small muted"><code>lbk_${t.id}_…</code> ${t.note || ''}</div></td><td><div class="chips">${String(t.scope || '').split(' ').filter(Boolean).map(s => badge(s))}</div></td><td>${time(t.created_at)}</td><td>${t.expires_at ? time(t.expires_at) : 'never'}</td><td>${time(t.last_used_at)}</td>
        <td>${t.revoked_at ? badge('revoked', 'off') : dead ? badge('expired', 'off') : badge('active', 'ok')}</td><td class="act">${dead ? '' : H`<button class="sm danger" data-act="revoke" data-kind="tokens" data-id="${t.id}" data-label="${t.name}">Revoke</button>`}</td></tr>`;
    }), 'No API tokens yet.'), 'c12')}
    ${card('OAuth clients', table(['Name', 'Kind', 'Created', 'Last used', ['Grants', 'num'], 'State', ['', 'act']], clients.map(c => H`<tr><td><b>${c.client_name || c.client_id}</b><div class="small muted">${arr(c.redirect_uris).slice(0, 2).join(' · ')}</div></td><td>${c.kind}</td><td>${time(c.created_at)}</td><td>${time(c.last_used_at)}</td><td class="num">${c.active_grants ?? 0}</td>
      <td>${c.revoked_at ? badge('revoked', 'off') : badge('active', 'ok')}</td><td class="act">${c.revoked_at ? '' : H`<button class="sm danger" data-act="revoke" data-kind="clients" data-id="${c.client_id}" data-label="${c.client_name || c.client_id}">Revoke</button>`}</td></tr>`), 'No OAuth clients have connected yet.'), 'c12')}
    ${card('OAuth grants', table(['Client', 'Scopes', 'Resource', 'Created', 'Expires', ['', 'act']], grants.map(g => H`<tr><td>${cName(g.client_id)}</td><td><div class="chips">${String(g.scope || '').split(' ').filter(Boolean).map(s => badge(s))}</div></td><td><code class="small">${g.resource || ''}</code></td><td>${time(g.created_at)}</td><td>${time(g.expires_at)}</td>
      <td class="act"><button class="sm danger" data-act="revoke" data-kind="grants" data-id="${g.family_id}" data-label="grant for ${cName(g.client_id)}">Revoke</button></td></tr>`), 'No active grants.'), 'c12')}
    ${card('Dashboard sessions', H`${table(['Signed in', 'Last seen', 'Expires', 'IP', 'Browser'], sessions.map(s => H`<tr><td>${time(s.created_at)}</td><td>${time(s.last_seen_at)}</td><td>${time(s.expires_at)}</td><td>${s.ip}</td><td class="small muted">${String(s.user_agent || '').slice(0, 80)}</td></tr>`), 'No sessions.')}
      <div class="row end"><button class="danger" data-act="revoke-sessions">Sign out all sessions</button></div>`)}
    ${card('Change dashboard password', H`<form data-form="password" autocomplete="off">${errBox}
      ${fld('Current password or machine token', 'f_current', inp('current', '', { type: 'password', req: true, extra: 'autocomplete="current-password"' }))}
      ${fld('New password', 'f_new', inp('new', '', { type: 'password', req: true, extra: 'autocomplete="new-password" minlength="10"' }), 'At least 10 characters; a passphrase is best.')}
      ${fld('Repeat new password', 'f_new2', inp('new2', '', { type: 'password', req: true, extra: 'autocomplete="new-password"' }))}
      <div class="row end"><button type="submit" class="primary">Change password</button></div></form>`)}
    <section class="card c6" id="legacy"><div class="card-head"><h2>Legacy machine token</h2>${legacy ? badge('enabled', 'bad') : badge('disabled', 'ok')}</div>
      <ol class="small"><li>Give every machine-token client an API token or OAuth.</li><li>Confirm each client shows a recent “last used”.</li><li>Disable the legacy token here.</li><li>Rotate the machine token so any leaked copy is useless.</li></ol>
      <div class="row end">${legacy ? H`<button class="primary" data-act="legacy" data-on="0">Disable legacy token for MCP</button>` : H`<button class="danger" data-act="legacy" data-on="1">Re-enable legacy token</button>`}</div></section>
    <section class="card c6 danger-zone"><div class="card-head"><h2>Rotate credentials</h2></div>
      <div class="switch"><span><b>Machine token</b><small>The new value is never shown here; read it locally with <code>loopback token</code>.</small></span><button class="danger sm" data-act="rotate" data-what="machine-token">Rotate</button></div>
      <div class="switch"><span><b>OAuth signing key</b><small>Invalidates every OAuth access token; clients refresh automatically.</small></span><button class="danger sm" data-act="rotate" data-what="signing-key">Rotate</button></div></section>
    ${card('Recent auth events', table(['When', 'Event', 'Principal', 'IP'], events.slice(0, 50).map(e => H`<tr><td>${time(e.ts)}</td><td>${badge(e.event, /fail|revoked|locked/.test(e.event) ? 'warn' : '')}</td><td>${e.principal || ''}</td><td>${e.ip || ''}</td></tr>`), 'No events.'), 'c12')}
    </div>`);
};
const SCOPES = [['loopback:read', 'Read files, list directories, diagnostics'], ['loopback:write', 'Modify files in permitted locations'], ['loopback:exec', 'Run commands, jobs and terminals'], ['loopback:fleet', 'Route to nodes and SSH targets']];
A['create-token'] = async () => {
  const o = await modal({
    title: 'Create API token', ok: 'Create token', danger: false,
    body: H`${fld('Name', 'tk_name', inp('name', '', { id: 'tk_name', req: true, ph: 'e.g. perplexity-desktop' }))}
      <div class="field"><span class="label">Scopes</span>${SCOPES.map(([s, d], i) => H`<label class="check"><input type="checkbox" name="scope:${s}"${chk(i === 0)}> <span><code>${s}</code><small>${d}</small></span></label>`)}</div>
      ${fld('Expires', 'tk_ttl', raw(`<select id="tk_ttl" name="ttl"><option value="604800">in 7 days</option><option value="2592000" selected>in 30 days</option><option value="7776000">in 90 days</option><option value="31536000">in 1 year</option><option value="">never</option></select>`))}
      ${fld('Note', 'tk_note', inp('note', '', { id: 'tk_note', ph: 'optional' }))}`,
  });
  if (!o) return;
  const scopes = Object.keys(o).filter(k => k.startsWith('scope:') && o[k]).map(k => k.slice(6));
  if (!scopes.length) { toast('Pick at least one scope', 'error'); return; }
  const r = await post('/api/admin/auth/tokens', { name: o.name, scopes, ttl_seconds: o.ttl ? Number(o.ttl) : null, note: o.note || '' });
  let secret = r.token;
  await modal({
    title: 'Copy your API token now', ok: 'Done', okOnly: true,
    body: H`${al('warning', 'Shown once.', 'Only a hash is stored. If lost, revoke it and create another.')}
      <div class="secret-once" id="secretOnce">${secret}</div><div class="row"><button type="button" class="sm" data-act="copy-secret">Copy</button><span class="small muted">Use as <code>Authorization: Bearer …</code></span></div>`,
  });
  secret = null; r.token = null;
  if (parseHash().name === 'auth') refresh();
};
A['copy-secret'] = async t => {
  const el = $('#secretOnce'); if (!el) return;
  try { await navigator.clipboard.writeText(el.textContent); t.textContent = 'Copied'; }
  catch { const s = getSelection(), r = doc.createRange(); r.selectNodeContents(el); s.removeAllRanges(); s.addRange(r); toast('Press Ctrl/Cmd+C to copy'); }
};
A.revoke = async t => {
  if (!await confirmDo('Revoke?', H`<p>Revoke <b>${t.dataset.label}</b>? Clients using it lose access immediately.</p>`, 'Revoke')) return;
  await del(`/api/admin/auth/${t.dataset.kind}/${enc(t.dataset.id)}`); done('Revoked');
};
A['revoke-sessions'] = async () => {
  if (!await confirmDo('Sign out everywhere?', H`<p>Every dashboard session, including this one, is ended.</p>`, 'Sign out all')) return;
  await post('/api/admin/auth/sessions/revoke-all'); toLogin();
};
F.password = async (f, o) => {
  if (o.new !== o.new2) throw new ApiError('The new passwords do not match.', 400);
  await post('/api/admin/auth/password', { current: o.current, new: o.new }); f.reset(); toast('Password changed', 'ok');
};
A.legacy = async t => {
  const on = t.dataset.on === '1';
  if (!await confirmDo(on ? 'Re-enable legacy token?' : 'Disable legacy token?', on ? H`<p>Holders of the machine token regain full MCP access. Use only to recover an unmigrated client.</p>` : H`<p>Clients still using the machine token get 401 and must switch to OAuth or an API token.</p>`, on ? 'Enable' : 'Disable', on)) return;
  await post('/api/admin/auth/legacy-token', { enabled: on }); done(on ? 'Legacy token enabled' : 'Legacy token disabled');
};
A.rotate = async t => {
  const mt = t.dataset.what === 'machine-token';
  if (!await confirmDo(mt ? 'Rotate machine token?' : 'Rotate signing key?', mt ? H`<p>The old machine token stops working. Read the new one on this machine with <code>loopback token</code>; it is never sent to the browser.</p>` : H`<p>All OAuth access tokens become invalid; clients refresh automatically.</p>`, 'Rotate')) return;
  await post('/api/admin/auth/rotate-' + t.dataset.what); done('Rotated');
};

/* -- Ingress: Cloudflare / Tailscale */
async function ingressData() { const [ing, cfg] = await Promise.all([get('/api/admin/ingress'), get('/api/admin/config')]); return { ing: ing || {}, cfg: (cfg.ingress || {}) }; }
const bothNote = ing => (ing.cloudflare || {}).enabled && (ing.tailscale || {}).enabled
  ? al('info', 'Both ingress paths are active', H`Clients can reach this machine through Cloudflare (public) and Tailscale (${ing.tailscale.mode === 'funnel' ? 'public funnel' : 'tailnet only'}).`) : '';
async function diagInto(name) {
  const box = $('#diag'); if (!box) return;
  const d = await soft(`/api/admin/ingress/${name}/diagnostics`);
  const list = arr(d, 'checks', 'diagnostics');
  if (box.isConnected) box.innerHTML = String(list.length ? table(['', 'Check', 'Detail'], list.map(c => H`<tr><td>${dot(c.ok ? 'healthy' : 'unreachable')}</td><td>${c.name}</td><td class="small">${c.detail || ''}</td></tr>`)) : H`<div class="empty">No diagnostics available.</div>`);
}
A['ing-restart'] = async t => {
  if (!await confirmDo('Restart ' + t.dataset.name + '?', H`<p>Connections through this path drop for a few seconds.</p>`, 'Restart', false)) return;
  await post(`/api/admin/ingress/${t.dataset.name}/restart`); done('Restarted');
};
A['ing-diag'] = t => diagInto(t.dataset.name);
views.cloudflare = async v => {
  const { ing, cfg } = await ingressData(), cf = ing.cloudflare || {}, c = cfg.cloudflare || {};
  const prot = c.access_policy === 'cloudflare-access' || c.access_policy === 'both';
  v.render(H`${head('Cloudflare Tunnel', 'Publish this machine on your own hostname via an outbound tunnel.', H`<button data-act="ing-restart" data-name="cloudflare">Restart</button>`)}${bothNote(ing)}
    <div class="grid">${card('Status', H`<div class="chips">${cf.enabled ? badge('public', 'bad') : badge('off', 'off')}${cf.enabled && prot ? badge('access-protected', 'ok') : ''}</div><hr class="sep">
      ${kv([['Enabled', yes(cf.enabled)], ['Hostname', cf.hostname ? H`<code>${cf.hostname}</code>` : null], ['Tunnel', cf.tunnel_id_prefix ? H`<code>${cf.tunnel_id_prefix}…</code>` : null], ['cloudflared installed', ob(cf.installed)], ['Running', ob(cf.running, 'running', 'stopped')], ['Status', cf.status], ['DNS', ob(cf.dns_ok, 'ok', 'not resolving')], ['Public health', cf.public_health == null ? null : typeof cf.public_health === 'object' ? safeKv(cf.public_health) : cf.public_health], ['Last error', cf.last_error || null]])}`)}
    ${card('Configuration', H`<form data-form="cloudflare">${errBox}
      <label class="check"><input type="checkbox" name="enabled"${chk(c.enabled)}> <span><b>Enable Cloudflare Tunnel</b><small>Publishes the MCP endpoint on the internet.</small></span></label>
      ${fld('Public hostname', 'f_hostname', inp('hostname', c.hostname, { ph: 'loopback.example.com' }))}
      ${fld('Tunnel ID', 'f_tunnel_id', inp('tunnel_id', '', { ph: cf.tunnel_id_prefix ? cf.tunnel_id_prefix + '… (unchanged)' : 'named tunnel UUID' }), 'Empty keeps the current tunnel. Credentials are never shown.')}
      ${fld('Tunnel token file', 'f_token_file', inp('token_file', c.token_file, { ph: '~/.cloudflared/token' }), 'Path only, for token-based tunnels.')}
      ${fld('Access policy', 'f_access_policy', H`<select id="f_access_policy" name="access_policy">${opt('oauth', c.access_policy || 'oauth', 'Loopback OAuth only')}${opt('cloudflare-access', c.access_policy, 'Cloudflare Access only')}${opt('both', c.access_policy, 'Cloudflare Access + Loopback OAuth')}</select>`)}
      <div class="row end"><button type="submit" class="primary">Save &amp; apply</button></div></form>`)}
    ${card('Diagnostics', raw('<div id="diag"><span class="spin"></span></div>'), { cls: 'c12', actions: H`<button class="sm" data-act="ing-diag" data-name="cloudflare">Re-run</button>` })}</div>`);
  diagInto('cloudflare');
};
F.cloudflare = async (f, o) => {
  const body = { enabled: o.enabled, hostname: o.hostname || null, access_policy: o.access_policy, token_file: o.token_file || null };
  if (o.tunnel_id) body.tunnel_id = o.tunnel_id;
  if (o.enabled && !await confirmDo('Expose publicly?', H`<p>Loopback becomes reachable from the internet at <b>${o.hostname}</b>. OAuth${o.access_policy !== 'oauth' ? ' and Cloudflare Access' : ''} still protect it.</p>`, 'Apply', false)) return;
  await put('/api/admin/ingress/cloudflare', body); done('Cloudflare applied');
};
views.tailscale = async v => {
  const { ing, cfg } = await ingressData(), ts = ing.tailscale || {}, c = cfg.tailscale || {};
  const mode = c.mode || ts.mode || 'serve';
  v.render(H`${head('Tailscale', 'Reach this machine privately over your tailnet, or publicly through Funnel.', H`<button data-act="ing-restart" data-name="tailscale">Restart</button>`)}${bothNote(ing)}
    <div class="grid">${card('Status', H`<div class="chips">${ts.enabled ? (ts.mode === 'funnel' ? badge('public', 'bad') : badge('tailnet', 'info')) : badge('off', 'off')}</div><hr class="sep">
      ${kv([['Enabled', yes(ts.enabled)], ['Mode', ts.mode], ['Installed', ob(ts.installed)], ['Backend state', ts.backend_state], ['DNS name', ts.dns_name ? H`<code>${ts.dns_name}</code>` : null], ['Serve active', ob(ts.serve_active, 'active', 'off')], ['Funnel active', ts.funnel_active === undefined ? undefined : (ts.funnel_active ? badge('active · public', 'bad') : badge('off', 'off'))], ['Peers', ts.peers_count], ['Public health', ts.public_health == null ? null : typeof ts.public_health === 'object' ? safeKv(ts.public_health) : ts.public_health]])}`, 'c5')}
    ${card('Configuration', H`<form data-form="tailscale">${errBox}
      <label class="check"><input type="checkbox" name="enabled"${chk(c.enabled)}> <span><b>Enable Tailscale ingress</b></span></label>
      <label class="choice"><input type="radio" name="mode" value="serve"${chk(mode === 'serve')}> <b>Serve — tailnet only (private)</b><span class="muted">Only devices signed in to your tailnet can connect. Recommended.</span></label>
      <label class="choice public"><input type="radio" name="mode" value="funnel"${chk(mode === 'funnel')}> <b>Funnel — public internet</b><span class="muted">Anyone on the internet can reach it; only OAuth/API tokens protect it.</span></label>
      <label class="check"><input type="checkbox" name="confirm_public"> <span>I understand Funnel exposes Loopback to the public internet<small>Required when choosing Funnel.</small></span></label>
      ${fld('HTTPS port', 'f_https_port', H`<select id="f_https_port" name="https_port">${[443, 8443, 10000].map(p => opt(p, c.https_port || 443))}</select>`)}
      <div class="row end"><button type="submit" class="primary">Save &amp; apply</button></div></form>`, 'c7')}
    ${card('Diagnostics', raw('<div id="diag"><span class="spin"></span></div>'), { cls: 'c12', actions: H`<button class="sm" data-act="ing-diag" data-name="tailscale">Re-run</button>` })}</div>`);
  diagInto('tailscale');
};
F.tailscale = async (f, o) => {
  if (o.enabled && o.mode === 'funnel' && !o.confirm_public) throw new ApiError('Tick the confirmation to enable public Funnel.', 400);
  const body = { enabled: o.enabled, mode: o.mode || 'serve', https_port: Number(o.https_port) || 443 };
  if (o.mode === 'funnel') body.confirm_public = !!o.confirm_public;
  await put('/api/admin/ingress/tailscale', body); done('Tailscale applied');
};

/* -- SSH config */
views.ssh = async v => {
  const S = arr(await get('/api/admin/ssh'), 'targets', 'ssh'), edit = v.params.get('edit'), e = S.find(s => s.name === edit) || {};
  v.render(H`${head('SSH config', 'Managed SSH targets, probed with BatchMode. Key material never leaves this machine.')}
    <div class="grid">${card('Targets', table(['', 'Name', 'Address', 'Identity file', 'Kind', 'Group', 'Via', 'Last probe', ['', 'act']], S.map(s => {
      const p = s.last_probe || {};
      return H`<tr><td>${dot(p.status || 'untested')}</td><td><b>${s.name}</b>${s.display_name ? H`<div class="small muted">${s.display_name}</div>` : ''}</td><td><code>${s.user ? s.user + '@' : ''}${s.host}:${s.port || 22}</code></td><td><code class="small">${s.identity_file || 'default'}</code></td><td>${s.kind || ''}</td><td>${s.group || ''}</td><td>${s.via || ''}</td>
        <td>${p.status ? H`${st(p.status)}<div class="small muted">${p.latency_ms != null ? p.latency_ms + ' ms · ' : ''}${rel(p.checked_at)}${(p.remote || {}).os ? ' · ' + p.remote.os : ''}</div>${p.error ? H`<div class="small">${p.error}</div>` : ''}` : st('untested')}</td>
        <td class="act"><button class="sm" data-act="probe" data-kind="ssh" data-name="${s.name}">Probe</button> <a class="btn sm" href="#/ssh?edit=${enc(s.name)}">Edit</a> <a class="btn sm" href="#/filesystem?target=${enc(s.name)}">Permissions</a> <button class="sm danger" data-act="remove-machine" data-kind="ssh" data-name="${s.name}">Remove</button></td></tr>`;
    }), 'No SSH targets yet.'), 'c12')}
    ${card(edit ? 'Edit ' + edit : 'Add SSH target', H`<form data-form="ssh">${errBox}<label class="check"><input type="checkbox" name="use_ssh_config"${chk(e.use_ssh_config)}> Use SSH alias settings (host, port and user from this machine’s SSH config)</label><div class="fields">
      ${fld('Name', 'f_name', inp('name', e.name, { req: true, ph: 'db-1', extra: edit ? 'readonly' : 'pattern="[A-Za-z0-9._-]+"' }))}
      ${fld('Host', 'f_host', inp('host', e.host, { req: true, ph: 'db-1.tailnet.ts.net' }))}
      ${fld('Port', 'f_port', inp('port', e.port || 22, { type: 'number', extra: 'min="1" max="65535"' }))}
      ${fld('User', 'f_user', inp('user', e.user, { ph: 'remote user' }))}
      ${fld('Identity file', 'f_identity_file', inp('identity_file', e.identity_file, { ph: '~/.ssh/id_ed25519' }), 'Path on this machine; must exist and be readable by the service user.')}
      ${fld('Display name', 'f_display_name', inp('display_name', e.display_name))}
      ${fld('Kind', 'f_kind', kindSel('kind', e.kind || 'remote-vm'))}
      ${fld('Group', 'f_group', inp('group', e.group))}
      ${fld('Via', 'f_via', H`<select id="f_via" name="via">${['unknown', 'tailscale', 'lan', 'public'].map(x => opt(x, e.via || 'unknown'))}</select>`)}</div>
      <div class="row end">${edit ? H`<a class="btn" href="#/ssh">Cancel</a>` : ''}<button type="submit" class="primary">${edit ? 'Save target' : 'Add target'}</button></div></form>`, 'c12')}</div>`);
};
F.ssh = async (f, o) => {
  await post('/api/admin/ssh', { name: o.name, host: o.host, port: o.port || 22, user: o.user || null, identity_file: o.identity_file || null, display_name: o.display_name || null, kind: o.kind, group: o.group || null, via: o.via, use_ssh_config: o.use_ssh_config });
  toast('SSH target saved', 'ok'); if (parseHash().params.get('edit')) location.hash = '#/ssh'; else refresh();
};

/* -- Jobs */
views.jobs = async v => {
  const load = async quiet => {
    const d = await api('/api/admin/jobs', { quiet });
    const jobs = arr(d, 'jobs'), terms = Array.isArray(d) ? [] : arr(d && d.terminals);
    if (!v.alive()) return;
    v.render(H`${head('Jobs', 'Background jobs and persistent terminals started by MCP clients.', H`<button class="sm" data-act="refresh">Refresh</button>`)}
      <div class="grid">${card('Background jobs', table(['ID', 'Target', 'Command', 'Cwd', 'PID', 'Started', 'State', ['', 'act']], jobs.map(j => H`<tr><td><code>${j.id}</code></td><td>${targetBadge(j.target)}</td><td><code>${String(j.command || '').slice(0, 200)}</code></td><td class="small"><code>${j.cwd || ''}</code></td><td>${j.pid ?? ''}</td><td>${time(j.started_at)}</td>
        <td>${j.running ? badge('running', 'ok') : badge('exit ' + (j.returncode ?? '?'), j.returncode === 0 ? '' : 'warn')}</td><td class="act">${j.running ? H`<button class="sm danger" data-act="stop-job" data-id="${j.id}">Stop</button>` : ''}</td></tr>`), 'No background jobs.'), 'c12')}
      ${card('Persistent terminals', table(['ID', 'Target', 'Shell', 'Cwd', 'PID', 'Started', 'Output', 'State', ['', 'act']], terms.map(t => H`<tr><td><code>${t.id}</code></td><td>${targetBadge(t.target)}</td><td><code>${t.shell || ''}</code></td><td class="small"><code>${t.cwd || ''}</code></td><td>${t.pid ?? ''}</td><td>${time(t.started_at)}</td><td>${bytes(t.bytes_seen)}</td>
        <td>${t.alive ? badge('alive', 'ok') : badge('closed', 'off')}</td><td class="act">${t.alive ? H`<button class="sm danger" data-act="close-term" data-id="${t.id}">Close</button>` : ''}</td></tr>`), 'No persistent terminals.'), 'c12')}</div>`);
    stamp();
  };
  await load(false); v.poll(5000, load);
};
A['stop-job'] = async t => { if (!await confirmDo('Stop job?', H`<p>Send SIGTERM to job <code>${t.dataset.id}</code>?</p>`, 'Stop')) return; await post(`/api/admin/jobs/${enc(t.dataset.id)}/stop`); done('Stopped'); };
A['close-term'] = async t => { if (!await confirmDo('Close terminal?', H`<p>Close terminal <code>${t.dataset.id}</code>? Its processes are terminated.</p>`, 'Close')) return; await post(`/api/admin/terminals/${enc(t.dataset.id)}/close`); done('Closed'); };

/* -- Logs & diagnostics */
const lg = { file: 'server', lines: 200, auto: false };
views.logs = async v => {
  const [doctor, metrics, audit] = await Promise.all([soft('/api/admin/doctor'), soft('/api/admin/metrics'), soft('/api/admin/audit?limit=100')]);
  const checks = arr(doctor, 'checks'), m = metrics || {};
  v.render(H`${head('Logs & diagnostics', 'Health checks, redacted logs and a support bundle.', H`<button class="primary" data-act="bundle">Create diagnostic bundle</button>`)}
    <div id="bundleOut"></div><div class="grid">
    ${card('Doctor', table(['', 'Check', 'Detail', 'Fix'], checks.map(c => H`<tr><td>${dot(c.ok ? 'healthy' : c.level === 'warning' || c.level === 'warn' ? 'degraded' : 'unreachable')}</td><td>${c.name}</td><td class="small">${c.detail || ''}</td><td class="small">${c.fix ? H`<code>${c.fix}</code>` : ''}</td></tr>`), doctor ? 'No checks.' : 'Doctor endpoint unavailable.'), { cls: 'c8', actions: H`<button class="sm" data-act="refresh">Re-run</button>` })}
    ${card('Resource usage', kv([['Gateway RSS', bytes(m.rss_bytes)], ['CPU', m.cpu_percent != null ? Number(m.cpu_percent).toFixed(1) + '%' : null], ['Audit DB', bytes(m.db_bytes)], ['Auth DB', bytes(m.auth_db_bytes)], ['Job logs', bytes(m.job_logs_bytes)], ['Probe cost', m.probe_cost_ms != null ? m.probe_cost_ms + ' ms' : null]]), 'c4')}
    <section class="card c12"><div class="card-head"><h2>Log tail</h2><div class="actions row">
      <select data-change="log-file" aria-label="Log file">${['server', 'cloudflared', 'tailscale'].map(x => opt(x, lg.file, x + '.log'))}</select>
      <select data-change="log-lines" aria-label="Lines">${[100, 200, 500, 1000].map(x => opt(x, lg.lines, x + ' lines'))}</select>
      <label class="check"><input type="checkbox" data-change="log-auto"${chk(lg.auto)}> auto-refresh</label><button class="sm" data-act="log-load">Reload</button></div></div>
      <pre class="log" id="logOut" tabindex="0" aria-label="Log output">Loading…</pre></section>
    ${card('Recent MCP activity', auditTable(arr(audit, 'rows', 'events')), 'c12')}</div>`);
  await loadLog();
  if (lg.auto) v.poll(5000, () => loadLog(true));
};
async function loadLog(quiet) {
  const out = $('#logOut'); if (!out) return;
  try {
    const r = await api(`/api/admin/logs?lines=${lg.lines}&file=${enc(lg.file)}`, { quiet });
    const text = typeof r === 'string' ? r : Array.isArray(r) ? r.join('\n') : Array.isArray(r && r.lines) ? r.lines.join('\n') : (r && (r.text || r.content)) || '';
    const atEnd = out.scrollTop + out.clientHeight >= out.scrollHeight - 20;
    out.textContent = text.slice(-400000) || '(empty)';
    if (atEnd) out.scrollTop = out.scrollHeight;
  } catch (e) { out.textContent = 'Could not load log: ' + e.message; }
}
C['log-file'] = t => { lg.file = t.value; return loadLog(); };
C['log-lines'] = t => { lg.lines = Number(t.value); return loadLog(); };
C['log-auto'] = t => { lg.auto = t.checked; if (lg.auto) cur.poll(5000, () => loadLog(true)); else { pollFn = null; clearTimeout(pollT); } };
A['log-load'] = () => loadLog();
A.bundle = async t => {
  const old = t.textContent; t.innerHTML = '<span class="spin"></span> Collecting…';
  try {
    const r = await post('/api/admin/diagnostics/bundle');
    $('#bundleOut').innerHTML = String(al('ok', 'Diagnostic bundle created', H`<code>${r.path}</code> · ${bytes(r.size)} · ${r.redactions ?? 0} values redacted. Copy it from this machine.`));
  } finally { t.textContent = old; }
};

/* -- Security */
views.security = async v => {
  const [policy, auth, sess, shield] = await Promise.all([get('/api/admin/policy'), soft('/api/admin/auth'), session().catch(() => null), soft('/api/admin/shield')]);
  const ta = (id, list, label) => H`<textarea id="${id}" name="${id}" aria-label="${label}" spellcheck="false">${arr(list).join('\n')}</textarea>`;
  v.render(H`${head('Security', 'Command rules, tool lists and session protection.')}
    <form data-form="security" class="grid"><div class="c12">${errBox}</div>
    ${card('Hard-deny command patterns', H`<p class="small muted">Python regexes, one per line. Always refused, in every profile.</p>${ta('deny_command_patterns', policy.deny_command_patterns, 'Hard-deny patterns')}<div class="small" data-regex-out="deny_command_patterns"></div>`)}
    ${card('Approval patterns', H`<p class="small muted">Matching commands wait for a dashboard approval.</p>${ta('require_approval_patterns', policy.require_approval_patterns, 'Approval patterns')}<div class="small" data-regex-out="require_approval_patterns"></div>`)}
    ${card('Allowed tools', H`<p class="small muted"><code>*</code> or one tool name per line.</p>${ta('allowed_tools', policy.allowed_tools, 'Allowed tools')}`, 'c4')}
    ${card('Denied tools', H`<p class="small muted">Always wins over allowed.</p>${ta('denied_tools', policy.denied_tools, 'Denied tools')}`, 'c4')}
    ${card('Approvals & sandbox', H`${fld('Approval lifetime (seconds)', 'f_approval_ttl_seconds', inp('approval_ttl_seconds', policy.approval_ttl_seconds, { type: 'number', extra: 'min="30" max="86400"' }))}
      <label class="check"><input type="checkbox" name="sandbox_required"${chk(policy.sandbox_required)}> <span>Require an OS sandbox for restricted shell<small>Custom profile only; built-in profiles set this themselves.</small></span></label>`, 'c4')}
    <div class="c12 row end"><button type="button" data-act="refresh">Discard</button><button type="submit" class="primary">Save security policy</button></div></form>
    <div class="grid">
    ${card('Session & CSRF', kv([['Session cookie', H`<code>loopback_admin</code> · HttpOnly · SameSite=Strict`], ['CSRF protection', H`${badge('active', 'ok')} header <code>X-Loopback-CSRF</code> on every change`], ['Session expires', sess ? time(sess.expires_at) : null], ['Dashboard password', sess ? okb(sess.password_set, 'set', 'not set') : null], ['Content-Security-Policy', H`<code class="small">default-src 'self'; frame-ancestors 'none'</code>`]]), { cls: 'c6', actions: H`${lnk('#/auth', 'Sessions')}` })}
    ${card('Configuration rollback', H`<p class="muted">Restore the newest automatic backup of <code>config.json</code> (taken before every change).</p><div class="row end"><button class="danger" data-act="rollback">Roll back configuration</button></div>`)}
    ${shieldCard(shield)}
    ${card('Auth events', table(['When', 'Event', 'Principal', 'IP'], arr(auth && auth.events).slice(0, 30).map(e => H`<tr><td>${time(e.ts)}</td><td>${e.event}</td><td>${e.principal || ''}</td><td>${e.ip || ''}</td></tr>`), 'No events.'), 'c12')}</div>`);
  ['deny_command_patterns', 'require_approval_patterns'].forEach(id => { const t = $('#' + id); t.dataset.input = 'regex'; I.regex(t); });
};
/* Endpoint protection (Shield): analytics, current blocks and the last recorded events. */
function shieldCard(sh) {
  if (!sh || !sh.summary) return card('Endpoint protection', H`<p class="muted">Shield is not mounted in this runtime. Floods, brute force and scanner probes are only limited by the OAuth rate limiter.</p>`, 'c12');
  const s = sh.summary, kinds = s.events_24h || {}, blocked = arr(s.blocked_now), prefixes = arr(s.rate_limited_prefixes);
  const KIND = { flood: 'warn', brute_force: 'bad', scanner: 'warn', exploit: 'bad', rotating_ip: 'warn', blocked: 'bad' };
  const stat = (label, n, tone) => H`<div class="shield-stat"><b class="${tone || ''}">${n}</b><small>${label}</small></div>`;
  return card('Endpoint protection', H`<p class="section-desc">${s.enabled ? 'Shield is active. It throttles floods, blocks brute force and scanner sources for a while, and records every decision with the visitor country when a trusted proxy supplies it. It mitigates abuse; it does not guarantee protection.' : 'Shield is disabled in the configuration.'}</p>
    <div class="shield-stats" data-testid="shield-stats">${stat('throttled · 24h', s.throttled_24h || 0)}${stat('blocked now', blocked.length, blocked.length ? 'bad' : '')}${Object.keys(kinds).sort().map(k => stat(k.replace('_', ' ') + ' · 24h', kinds[k]))}${stat('last event', s.last_event_at ? rel(s.last_event_at) : 'none')}</div>
    ${blocked.length ? H`<h3>Blocked addresses</h3>${table(['Address', 'Until', 'Reason', ['', 'act']], blocked.map(b => H`<tr><td><code>${b.ip}</code></td><td>${time(b.until)}</td><td>${b.reason || b.kind || ''}</td><td class="act"><button class="sm" data-act="shield-unblock" data-ip="${b.ip}">Unblock</button></td></tr>`))}` : ''}
    ${prefixes.length ? H`<p class="small muted">Tightened budget for rotating-address prefixes: ${prefixes.map(p => H`<code>${p.prefix || p}</code> `)}</p>` : ''}
    <h3>Recent events</h3>${table(['When', 'Kind', 'Action', 'Address', 'Country', 'Path'], arr(sh.events).slice(0, 40).map(e => H`<tr><td>${time(e.ts)}</td><td>${badge(e.kind, KIND[e.kind] || 'info')}</td><td>${e.action}</td><td><code>${e.ip}</code></td><td>${e.country || '—'}</td><td><code class="small">${String(e.method || '')} ${String(e.path || '').slice(0, 60)}</code></td></tr>`), 'No protection events recorded yet.')}`, { cls: 'c12', actions: H`<a class="btn sm ghost" href="#/topology">Topology marker</a>` });
}
A['shield-unblock'] = async t => { await post('/api/admin/shield/unblock', { ip: t.dataset.ip }); done(`Unblocked ${t.dataset.ip}`); };
I.regex = t => {
  const out = $(`[data-regex-out="${t.id}"]`); if (!out) return;
  const bad = lines(t.value).filter(p => { try { new RegExp(p.replace(/\(\?[aiLmsux]+\)/g, '')); return false; } catch { return true; } });
  out.innerHTML = bad.length ? String(H`<span class="badge warn">${bad.length} pattern(s) look invalid</span> <span class="muted">The server validates on save.</span>`) : '';
  t.classList.toggle('invalid', bad.length > 0);
};
F.security = async (f, o) => {
  await put('/api/admin/policy', { deny_command_patterns: lines(o.deny_command_patterns), require_approval_patterns: lines(o.require_approval_patterns), allowed_tools: lines(o.allowed_tools), denied_tools: lines(o.denied_tools), approval_ttl_seconds: o.approval_ttl_seconds, sandbox_required: o.sandbox_required });
  done('Security policy saved');
};
A.rollback = async () => {
  if (!await confirmDo('Roll back configuration?', H`<p>The current <code>config.json</code> is replaced by the newest backup. Services may restart.</p>`, 'Roll back')) return;
  await post('/api/admin/config/rollback'); done('Configuration rolled back');
};

/* -- Backup & recovery */
views.backup = async v => {
  const B = arr(await get('/api/admin/backups'), 'backups');
  v.render(H`${head('Backup & recovery', 'Snapshots of configuration and policy.', H`<button class="primary" data-act="backup-create">Create backup</button>`)}
    <div class="grid">${card('Backups', table(['Name', 'Created', ['Size', 'num'], ['', 'act']], B.map(b => H`<tr><td><code>${b.name}</code></td><td>${time(b.created_at)}</td><td class="num">${bytes(b.size)}</td><td class="act"><button class="sm danger" data-act="backup-restore" data-name="${b.name}">Restore</button></td></tr>`), 'No backups yet.'), 'c8')}
    ${card('Config rollback', H`<p class="muted">Every configuration change keeps the previous <code>config.json</code> under <code>config.backups/</code>. Roll back to undo the latest change.</p><div class="row end"><button class="danger" data-act="rollback">Roll back configuration</button></div>
      <hr class="sep"><p class="small muted">If the dashboard is unreachable, use <code>loopback doctor</code> and <code>loopback backup restore</code> on the machine.</p>`, 'c4')}</div>`);
};
A['backup-create'] = async () => { const r = await post('/api/admin/backups'); done('Backup created' + (r && r.name ? ': ' + r.name : '')); };
A['backup-restore'] = async t => {
  const n = t.dataset.name;
  if (!await modal({ title: 'Restore backup?', body: H`<p>Restore <code>${n}</code>? Current configuration and policy are replaced; the gateway may restart.</p>`, ok: 'Restore', danger: true, typed: n })) return;
  await post(`/api/admin/backups/${enc(n)}/restore`, { confirm: true }); done('Backup restored');
};

/* -- Updates */
views.updates = async v => {
  const [u, cfg] = await Promise.all([get('/api/admin/updates'), get('/api/admin/config')]);
  const mt = cfg.maintenance || {};
  const notes = u.notes_url && /^https:\/\//.test(u.notes_url) ? u.notes_url : null;
  v.render(H`${head('Updates', 'The dashboard checks for releases; it never applies them.', H`<button class="primary" data-act="update-check">Check now</button>`)}
    ${u.available ? H`<div class="alert info"><span><b>Loopback ${u.latest} is available</b>Apply it on the machine with <code>loopback upgrade</code>.</span>${notes ? H`<a class="btn sm" href="${notes}" target="_blank" rel="noopener noreferrer">Release notes</a>` : ''}</div>` : ''}
    <div class="grid">${card('Version', kv([['Current', H`<b>${u.current}</b>`], ['Latest', u.latest || 'unknown'], ['Channel', u.channel], ['Last checked', time(u.checked_at)], ['Status', u.available ? badge('update available', 'info') : badge('up to date', 'ok')], ['Release notes', notes ? H`<a href="${notes}" target="_blank" rel="noopener noreferrer">${notes}</a>` : null]]))}
    ${card('Update settings', H`<form data-form="updates">${errBox}
      ${fld('Channel', 'f_update_channel', H`<select id="f_update_channel" name="update_channel">${opt('stable', mt.update_channel, 'stable')}${opt('prerelease', mt.update_channel, 'prerelease')}${opt('off', mt.update_channel, 'off (no checks)')}</select>`)}
      ${fld('Check interval (hours)', 'f_update_check_interval_hours', inp('update_check_interval_hours', mt.update_check_interval_hours, { type: 'number', extra: 'min="0" max="720"' }))}
      <p class="small muted">Updates are applied with <code>loopback upgrade</code> on the machine.</p>
      <div class="row end"><button type="submit" class="primary">Save</button></div></form>`)}</div>`);
};
A['update-check'] = async t => { t.innerHTML = '<span class="spin"></span> Checking'; await post('/api/admin/updates/check'); done('Update check complete'); };
F.updates = async (f, o) => { await put('/api/admin/config', { maintenance: { update_channel: o.update_channel, update_check_interval_hours: o.update_check_interval_hours } }); done(); };

/* -- Settings */
const MAINT = [['backup_retention', 'Backups kept', ''], ['log_max_bytes', 'Log size before rotation (bytes)', ''], ['log_backups', 'Rotated logs kept', ''], ['probe_interval_seconds', 'Probe interval (s)', '0 disables background probes; minimum 10.'], ['history_retention_days', 'Probe history (days)', ''], ['audit_retention_days', 'Audit retention (days)', ''], ['disk_warn_percent', 'Disk warning threshold (%)', ''], ['job_log_max_bytes', 'Job log cap (bytes)', ''], ['max_completed_jobs', 'Completed jobs kept', '']];
views.settings = async v => {
  const cfg = await get('/api/admin/config'), mt = cfg.maintenance || {}, th = localStorage.getItem('lb-theme') || 'auto';
  v.render(H`${head('Settings', 'Validated server-side and backed up before saving.')}
    <form data-form="settings">${errBox}<div class="grid">
    ${card('Machine', H`<div class="fields">${fld('Display name', 'f_display_name', inp('display_name', cfg.display_name, { ph: cfg.public_host || 'this machine' }))}
      ${fld('Node kind', 'f_node_kind', kindSel('node_kind', cfg.node_kind))}
      ${fld('Port', 'f_port', inp('port', cfg.port, { type: 'number', extra: 'min="1" max="65535"' }), 'Changing the port restarts the service.')}</div>`)}
    ${card('Execution limits', H`<div class="fields">${fld('Command timeout (s)', 'f_command_timeout', inp('command_timeout', cfg.command_timeout, { type: 'number', extra: 'min="1" max="86400"' }))}
      ${fld('Max output (bytes)', 'f_max_output_bytes', inp('max_output_bytes', cfg.max_output_bytes, { type: 'number', extra: 'min="1"' }))}</div>`)}
    ${card('Maintenance', H`<div class="fields">${MAINT.map(([k, l, h]) => fld(l, 'f_' + k, inp('m_' + k, mt[k], { id: 'f_' + k, type: 'number', extra: 'min="0"' }), h))}</div>`, 'c12')}
    <div class="c12 row end"><button type="button" data-act="refresh">Discard</button><button type="submit" class="primary">Save settings</button></div></div></form>
    <div class="grid">${card('Appearance', fld('Theme', 'f_theme', H`<select id="f_theme" data-change="theme">${opt('auto', th, 'Follow system')}${opt('dark', th, 'Dark')}${opt('light', th, 'Light')}</select>`))}</div>`);
};
F.settings = async (f, o) => {
  const m = {}; MAINT.forEach(([k]) => { if (o['m_' + k] != null) m[k] = o['m_' + k]; });
  await put('/api/admin/config', { display_name: o.display_name || null, node_kind: o.node_kind, port: o.port, command_timeout: o.command_timeout, max_output_bytes: o.max_output_bytes, maintenance: m });
  done('Settings saved');
};
C.theme = t => { if (t.value === 'auto') localStorage.removeItem('lb-theme'); else localStorage.setItem('lb-theme', t.value); applyTheme(); };

/* -- First-run wizard */
const wz = { step: 0 };
const WSTEPS = ['Name this machine', 'Choose ingress', 'Review filesystem', 'Connect a client', 'Done'];
views.welcome = async v => {
  const step = Math.max(0, Math.min(4, Number(v.params.get('step') || wz.step) || 0)); wz.step = step;
  const nav = H`<ol class="steps">${WSTEPS.map((s, i) => H`<li class="${i === step ? 'cur' : i < step ? 'done' : ''}"${i === step ? raw(' aria-current="step"') : ''}>${i + 1}. ${s}</li>`)}</ol>`;
  const back = step ? H`<a class="btn" href="#/welcome?step=${step - 1}">Back</a>` : '';
  let body;
  if (step === 0) {
    const cfg = await get('/api/admin/config');
    body = H`<form data-form="wiz-identity">${errBox}<p class="muted">Shown in the dashboard, topology and to other nodes.</p><div class="fields">
      ${fld('Display name', 'f_display_name', inp('display_name', cfg.display_name, { ph: cfg.public_host || 'My workstation' }))}${fld('Node kind', 'f_node_kind', kindSel('node_kind', cfg.node_kind))}</div>
      <div class="row end">${back}<button type="submit" class="primary">Save &amp; continue</button></div></form>`;
  } else if (step === 1) {
    body = H`<form data-form="wiz-ingress">${errBox}<p class="muted">How should MCP clients reach this machine?</p>
      <label class="choice"><input type="radio" name="ingress" value="local" checked> <b>Local only</b><span class="muted">Clients on this machine via 127.0.0.1. Nothing is exposed.</span></label>
      <label class="choice"><input type="radio" name="ingress" value="serve"> <b>Tailscale Serve — tailnet only</b><span class="muted">Private HTTPS for devices in your tailnet.</span></label>
      <label class="choice public"><input type="radio" name="ingress" value="funnel"> <b>Tailscale Funnel — public internet</b><span class="muted">Needed for cloud clients. Anyone can reach it; OAuth protects it.</span></label>
      <label class="choice public"><input type="radio" name="ingress" value="cloudflare"> <b>Cloudflare Tunnel — public internet</b><span class="muted">Your own hostname, optional Cloudflare Access.</span></label>
      <label class="check"><input type="checkbox" name="confirm_public"> <span>I understand public options expose Loopback to the internet</span></label>
      <div class="row end">${back}<button type="submit" class="primary">Continue</button></div></form>`;
  } else if (step === 2) {
    const perm = await get('/api/admin/permissions?target=local');
    body = H`<p class="muted">MCP clients can only reach these locations. Everything else is denied.</p>${permsHtml(perm, perm.sandbox)}
      <div class="row end">${back}<a class="btn" href="#/filesystem">Edit filesystem roots</a><a class="btn primary" href="#/welcome?step=3">Looks good</a></div>`;
  } else if (step === 3) {
    const a = await soft('/api/admin/auth') || {};
    body = H`<div class="grid"><div class="card flat c6"><h2>OAuth client (recommended)</h2><p class="muted">Add this MCP URL in an OAuth-capable client; it opens a consent page here.</p>
      ${arr(a.resource_uris).length ? H`${a.resource_uris.map(u => H`<div class="secret-once">${u}</div>`)}` : H`<p class="small muted">Enable an ingress path to get a reachable URL.</p>`}<p class="small"><a href="#/auth">Manage OAuth clients</a></p></div>
      <div class="card flat c6"><h2>API token</h2><p class="muted">For bearer-only clients and scripts. Scoped and revocable; shown once.</p><button class="primary" data-act="create-token">Create API token</button></div></div>
      <div class="row end">${back}<a class="btn primary" href="#/welcome?step=4">Continue</a></div>`;
  } else {
    body = H`${al('ok', 'Setup complete', 'Your machine is ready. Everything here can be changed later.')}
      <div class="row"><a class="btn primary" href="#/">Overview</a><a class="btn" href="#/topology">Topology</a><a class="btn" href="#/fleet">Add machines</a><a class="btn" href="#/security">Security</a></div>`;
  }
  v.render(H`${head('Welcome to Loopback', 'A few steps to make this machine useful and safe.')}${nav}<div class="card">${body}</div>`);
};
const wizGo = n => { wz.step = n; location.hash = '#/welcome?step=' + n; };
F['wiz-identity'] = async (f, o) => { await put('/api/admin/config', { display_name: o.display_name || null, node_kind: o.node_kind }); wizGo(1); };
F['wiz-ingress'] = async (f, o) => {
  if ((o.ingress === 'funnel' || o.ingress === 'cloudflare') && !o.confirm_public) throw new ApiError('Tick the confirmation to choose a public option.', 400);
  if (o.ingress === 'serve' || o.ingress === 'funnel') {
    await put('/api/admin/ingress/tailscale', { enabled: true, mode: o.ingress, https_port: 443, ...(o.ingress === 'funnel' ? { confirm_public: true } : {}) });
    toast('Tailscale ' + o.ingress + ' enabled', 'ok');
  } else if (o.ingress === 'cloudflare') { toast('Configure the tunnel, then return to the wizard'); location.hash = '#/cloudflare'; wz.step = 2; return; }
  wizGo(2);
};

/* -- boot */
const LB = { bytes, api, get, put, post, del, soft, H, raw, esc, toast, modal, confirmDo, panel, rel, abs, time, badge, dot, st, kv, safeKv, table, card, head, stamp, A, C, F, I, STATUS, STATUS_LABEL, isSecretKey, scrub, arr, enc, probeHtml, state, refresh, ApiError, errorsHtml };
window.LB = LB;
const navPaths = { '':'M3 10 12 3l9 7v10H3Z M9 20v-7h6v7', topology:'M12 3v6M4 14h16M4 14v6m16-6v6M9 3h6v6H9Z', services:'M4 4h16v6H4Zm0 10h16v6H4ZM7 7h.01M7 17h.01', fleet:'M3 4h18v12H3Zm5 16h8m-4-4v4', filesystem:'M3 6h7l2 3h9v11H3Z', shell:'m5 7 5 5-5 5m8 0h6', tools:'m9 7-5 5 5 5m6-10 5 5-5 5', auth:'M7 10V7a5 5 0 0 1 10 0v3M5 10h14v11H5Z', cloudflare:'M6 18a5 5 0 0 1-1-10 7 7 0 0 1 13 1 4 4 0 0 1 0 9Z', tailscale:'M5 5h.01M12 5h.01M19 5h.01M5 12h.01M12 12h.01M19 12h.01M5 19h.01M12 19h.01M19 19h.01', ssh:'m5 7 5 5-5 5m8 0h6', jobs:'M5 4h14v17H5ZM9 8h6m-6 4h6m-6 4h4', logs:'M4 20V4m0 16h17M8 15l4-5 4 3 5-8', security:'M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7Zm-4 9 3 3 5-6', backup:'M4 5h16v5H4Zm2 5v10h12V10m-9 4h6', updates:'M5 8a8 8 0 1 1-1 7M5 3v5h5', settings:'M9 3h6l1 4 4 1v8l-4 1-1 4H9l-1-4-4-1V8l4-1Zm0 9a3 3 0 1 0 6 0 3 3 0 1 0-6 0', local:'M3 4h18v12H3Zm5 16h8m-4-4v4', welcome:'m4 12 5 5L20 5' };
$$('#nav a[data-route]').forEach(a => { a.insertAdjacentHTML('afterbegin', '<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="' + (navPaths[a.dataset.route] || navPaths.services) + '"/></svg>'); });
applyTheme();
$('#themeToggle').addEventListener('click', () => { localStorage.setItem('lb-theme', currentTheme() === 'dark' ? 'light' : 'dark'); applyTheme(); });
$('#navToggle').addEventListener('click', () => setNav(!doc.body.classList.contains('nav-open')));
$('#scrim').addEventListener('click', () => setNav(false));
window.addEventListener('hashchange', route);
session().catch(() => {});
route();
})();
