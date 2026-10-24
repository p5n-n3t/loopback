/* Loopback topology view — dependency-free SVG renderer.
 * Layered top-to-bottom layout:
 *   0 external clients · 1 ingress / tunnel / network · 2 local gateway · 3 remote hosts & nodes (grouped)
 * Data: GET /api/admin/topology (schema in docs/ADMIN-API.md). Pan with drag, zoom with wheel/pinch.
 * Exposes window.LBTopology.mount(view, LB) where LB is the helper bundle from app.js.
 */
'use strict';
(function () {
const LAYER = { 'external-client': 0, tunnel: 1, network: 1, gateway: 2, 'mcp-service': 4 };
const LAYER_NAME = ['Clients', 'Ingress', 'Gateway', 'Machines', 'Services'];
const NW = 188, NH = 66, GW = 220, GH = 78, HG = 34, VG = 118, GROUP_GAP = 64, LM = 96, TM = 30;
const TRANSPORT = { ssh: 'SSH', https: 'HTTPS', http: 'HTTP', mcp: 'MCP', 'cloudflare-tunnel': 'Cloudflare Tunnel', tailscale: 'Tailscale', 'tailscale-serve': 'Tailscale', 'tailscale-funnel': 'Tailscale Funnel' };
const BAD = new Set(['unreachable', 'auth_failed', 'error']);
const T = { data: null, lay: null, sel: null, filter: 'all', legend: false, vb: null, refreshedAt: null, svg: null };
let LB = null;

const trunc = (s, n) => { s = String(s ?? ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const markerFor = s => s === 'healthy' ? 'healthy' : s === 'degraded' ? 'degraded' : BAD.has(s) ? 'bad' : 'off';
const transportLabel = e => TRANSPORT[e.transport] || String(e.transport || e.protocol || '').toUpperCase();
const machineRef = n => { const m = /^(ssh|node):(.+)$/.exec(n.id || ''); return m ? { kind: m[1] === 'ssh' ? 'ssh' : 'nodes', name: m[2] } : null; };

/* Node status: explicit status, else the probe status of the edge reaching it from the gateway side. */
function nodeStatus(n, data) {
  const own = n.status || (n.detail && n.detail.status);
  if (own) return own;
  if (n.type === 'gateway') return 'healthy';
  const inc = data.edges.filter(e => e.to === n.id);
  const out = data.edges.filter(e => e.from === n.id);
  const list = inc.length ? inc : out;
  if (!list.length) return 'untested';
  const rank = s => ({ unreachable: 6, auth_failed: 6, error: 6, degraded: 5, stale: 4, disconnected: 3, untested: 2, healthy: 1 }[s] || 2);
  return list.map(e => e.status || 'untested').sort((a, b) => rank(b) - rank(a))[0];
}

/* -- layout */
function layout(data) {
  const nodes = data.nodes.map(n => ({ ...n, layer: LAYER[n.type] ?? 3 }));
  const rows = [[], [], [], [], []];
  nodes.forEach(n => rows[n.layer].push(n));
  rows.forEach((r, i) => { if (i < 3) r.sort((a, b) => String(a.label).localeCompare(String(b.label))); });
  const groups = new Map();
  rows[3].forEach(n => { const g = n.group || 'ungrouped'; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(n); });
  const gnames = [...groups.keys()].sort((a, b) => a === 'ungrouped' ? 1 : b === 'ungrouped' ? -1 : a.localeCompare(b));
  gnames.forEach(g => groups.get(g).sort((a, b) => String(a.label).localeCompare(String(b.label))));
  const size = n => n.type === 'gateway' ? [GW, GH] : [NW, NH];
  const rowW = r => r.reduce((s, n) => s + size(n)[0], 0) + Math.max(0, r.length - 1) * HG;
  const w3 = gnames.reduce((s, g) => s + rowW(groups.get(g)), 0) + Math.max(0, gnames.length - 1) * GROUP_GAP;
  const maxW = Math.max(rowW(rows[0]), rowW(rows[1]), rowW(rows[2]), w3, rowW(rows[4]), NW);
  const used = [0, 1, 2, 3, 4].filter(i => rows[i].length);
  const rowY = {}; let y = TM;
  used.forEach(i => { if (i === 3 && gnames.length) y += 26; rowY[i] = y; y += GH + VG; });
  const place = (list, x0, ly) => { let x = x0; list.forEach(n => { const [w, h] = size(n); n.w = w; n.h = h; n.x = x; n.y = ly + (GH - h) / 2; x += w + HG; }); };
  [0, 1, 2, 4].forEach(i => { if (rows[i].length) place(rows[i], LM + (maxW - rowW(rows[i])) / 2, rowY[i]); });
  const brackets = [];
  if (rows[3].length) {
    let x = LM + (maxW - w3) / 2;
    gnames.forEach(g => { const list = groups.get(g), w = rowW(list); place(list, x, rowY[3]); brackets.push({ name: g, x0: x, x1: x + w, y: rowY[3] }); x += w + GROUP_GAP; });
  }
  const byId = {}; nodes.forEach(n => { byId[n.id] = n; n.cx = n.x + n.w / 2; n.status = nodeStatus(n, data); });
  // Spread edge anchors so parallel edges do not share one point.
  const ports = {};
  data.edges.forEach(e => {
    const a = byId[e.from], b = byId[e.to]; if (!a || !b) return;
    const down = b.layer >= a.layer;
    (ports[a.id + (down ? 'b' : 't')] = ports[a.id + (down ? 'b' : 't')] || []).push([e.id, b.cx]);
    (ports[b.id + (down ? 't' : 'b')] = ports[b.id + (down ? 't' : 'b')] || []).push([e.id, a.cx]);
  });
  const off = {};
  Object.entries(ports).forEach(([k, list]) => {
    list.sort((p, q) => p[1] - q[1]);
    const n = byId[k.slice(0, -1)], step = Math.min(16, (n.w - 30) / Math.max(1, list.length));
    list.forEach(([eid], i) => { off[eid + '@' + k] = (i - (list.length - 1) / 2) * step; });
  });
  const edges = data.edges.filter(e => byId[e.from] && byId[e.to]).map(e => {
    const a = byId[e.from], b = byId[e.to], down = b.layer >= a.layer;
    const ka = a.id + (down ? 'b' : 't'), kb = b.id + (down ? 't' : 'b');
    const sx = a.cx + (off[e.id + '@' + ka] || 0), ex = b.cx + (off[e.id + '@' + kb] || 0);
    const sy = down ? a.y + a.h : a.y, ey = down ? b.y : b.y + b.h;
    const dy = (ey - sy) / 2;
    return { e, a, b, d: `M${sx},${sy} C${sx},${sy + dy} ${ex},${ey - dy} ${ex},${ey}`, mx: (sx + ex) / 2, my: (sy + ey) / 2 };
  });
  return { nodes, byId, edges, brackets, rowY, used, width: LM + maxW + 20, height: y - VG + 20 };
}

/* -- drawing */
function shape(n) {
  const w = n.w, h = n.h;
  switch (n.type) {
    case 'gateway': return `<rect class="shape" width="${w}" height="${h}" rx="10"/>`;
    case 'loopback-node': return `<polygon class="shape" points="14,0 ${w - 14},0 ${w},${h / 2} ${w - 14},${h} 14,${h} 0,${h / 2}"/>`;
    case 'tunnel': return `<rect class="shape" width="${w}" height="${h}" rx="${h / 2}"/><rect class="icon" x="5" y="5" width="${w - 10}" height="${h - 10}" rx="${h / 2 - 5}" stroke-dasharray="3 4"/>`;
    case 'external-client': return `<rect class="shape" width="${w}" height="${h}" rx="${h / 2}"/>`;
    case 'network': {
      const r = 14, top = 8, span = w - 2 * r, bw = span / 3;
      let p = `M${r},${h} H${w - r} Q${w},${h} ${w},${h - r} V${top + r} Q${w},${top} ${w - r},${top}`;
      for (let i = 1; i <= 3; i++) p += ` A${bw},${bw} 0 0 0 ${w - r - bw * i},${top}`;
      return `<path class="shape" d="${p} Q0,${top} 0,${top + r} V${h - r} Q0,${h} ${r},${h} Z"/>`;
    }
    default: return `<rect class="shape" width="${w}" height="${h}" rx="3"/>`;
  }
}
function icon(n) {
  const y = n.h / 2;
  const g = s => `<g class="icon" transform="translate(16,${y - 10})">${s}</g>`;
  switch (n.type) {
    case 'gateway': return g('<rect x="0" y="0" width="20" height="8" rx="2"/><rect x="0" y="12" width="20" height="8" rx="2"/><circle cx="4" cy="4" r=".8"/><circle cx="4" cy="16" r=".8"/>');
    case 'loopback-node': return g('<path d="M16,6 A7,7 0 1 0 17,13"/><path d="M13,3 L16,6 L13,9"/>');
    case 'tunnel': return g('<path d="M0,10 H20 M15,5 L20,10 L15,15"/>');
    case 'network': return g('<circle cx="10" cy="10" r="9"/><ellipse cx="10" cy="10" rx="4" ry="9"/><path d="M1,10 H19"/>');
    case 'external-client': return g('<circle cx="10" cy="6" r="4"/><path d="M2,20 C2,13 18,13 18,20"/>');
    default: return g('<rect x="0" y="1" width="20" height="13" rx="2"/><path d="M6,19 H14 M10,14 V19"/>');
  }
}
function nodeSvg(n) {
  const H = LB.H, d = n.detail || {};
  const sub = [n.kind || d.machine_kind, d.os].filter(Boolean).join(' · ') || n.type;
  const m = d.worker_metrics;
  const summary = m ? 'CPU ' + (m.cpu_percent == null ? '—' : m.cpu_percent + '%') + ' · RAM ' + (m.memory_percent == null ? '—' : m.memory_percent + '%') : '';
  const l2 = summary || (n.type === 'mcp-service' ? d.target + ' · ' + d.transport : n.display_name ? n.display_name : sub), l3 = summary ? '↓ ' + LB.bytes(m.rx_per_second) + '/s ↑ ' + LB.bytes(m.tx_per_second) + '/s' : n.display_name ? sub : '';
  const ty = l3 ? [22, 38, 53] : [28, 45];
  const dim = T.filter !== 'all' && n.status !== T.filter ? ' dim' : '';
  return H`<g class="tn ${n.type} st-${n.status}${T.sel === 'n:' + n.id ? ' sel' : ''}${dim}" transform="translate(${n.x},${n.y})" data-nid="${n.id}" tabindex="0" role="button"
    aria-label="${(n.display_name || n.label) + ', ' + n.type + ', ' + (LB.STATUS_LABEL[n.status] || n.status)}">${LB.raw(shape(n) + icon(n))}
    <text class="t1" x="44" y="${ty[0]}">${trunc(n.label, 22)}</text><text class="t2" x="44" y="${ty[1]}">${trunc(l2, 26)}</text>${l3 ? H`<text class="t3" x="44" y="${ty[2]}">${trunc(l3, 28)}</text>` : ''}
    <circle class="sdot ${n.status}" cx="${n.w - 12}" cy="12" r="5"><title>${LB.STATUS_LABEL[n.status] || n.status}</title></circle>
    ${n.type === 'gateway' ? LB.raw(`<rect class="tag" x="12" y="-9" width="44" height="16" rx="4"/><text class="tagt" x="34" y="3" text-anchor="middle">LOCAL</text>`) : ''}</g>`;
}
function edgeSvg(L) {
  const H = LB.H, e = L.e, s = e.status || 'untested';
  const proxied = e.proxied || /tunnel|tailscale/.test(e.transport || '');
  const rev = e.initiator && e.initiator === e.to;
  const mk = `url(#lb-arrow-${markerFor(s)})`;
  const label = transportLabel(e) + (e.protocol && e.protocol !== e.transport && e.protocol !== 'http' ? ' · ' + String(e.protocol).toUpperCase() : '');
  const lw = label.length * 6.1 + 14, x = L.mx - lw / 2, y = L.my - 9;
  const xp = e.exposure ? String(e.exposure) : '', xw = xp.length * 6 + 10;
  const lat = e.latency_ms != null ? Math.round(e.latency_ms) + ' ms' : '';
  const jobs = Number(e.jobs_running) || 0;
  const dim = T.filter !== 'all' && s !== T.filter ? ' dim' : '';
  return H`<g class="te st-${s}${proxied ? ' proxied' : ''}${T.sel === 'e:' + e.id ? ' sel' : ''}${dim}" data-eid="${e.id}" tabindex="0" role="button" aria-label="${label + ' from ' + (L.a.label) + ' to ' + (L.b.label) + ', ' + (LB.STATUS_LABEL[s] || s)}">
    <path class="hit" d="${L.d}"/><path class="line" d="${L.d}" ${LB.raw(rev ? `marker-start="${mk}"` : `marker-end="${mk}"`)}/>
    <g class="lbl"><rect x="${x}" y="${y}" width="${lw}" height="18" rx="9"/><text x="${L.mx}" y="${y + 12.5}" text-anchor="middle">${label}</text>
    ${lat ? H`<text class="lat" x="${L.mx}" y="${y + 30}" text-anchor="middle">${lat}</text>` : ''}</g>
    ${xp ? H`<g class="xb ${xp}"><rect x="${x + lw + 4}" y="${y + 1}" width="${xw}" height="16" rx="8"/><text x="${x + lw + 4 + xw / 2}" y="${y + 12.5}" text-anchor="middle">${xp}</text></g>` : ''}
    ${jobs ? H`<g class="jobchip"><rect x="${x - 50}" y="${y + 1}" width="46" height="16" rx="8"/><text x="${x - 27}" y="${y + 12.5}" text-anchor="middle">${jobs} job${jobs > 1 ? 's' : ''}</text></g>` : ''}</g>`;
}
const DEFS = `<defs><pattern id="lb-stripes" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect class="stripe-a" width="8" height="8"/><line class="stripe-b" x1="0" y1="0" x2="0" y2="8"/></pattern>` +
  ['healthy', 'degraded', 'bad', 'off'].map(k => `<marker id="lb-arrow-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="mk-${k}" d="M0,0 L10,5 L0,10 z"/></marker>`).join('') + '</defs>';

function draw() {
  if (!T.svg || !T.data) return;
  const L = T.lay = layout(T.data), H = LB.H;
  const layers = L.used.map(i => H`<text class="layer-lbl" x="6" y="${L.rowY[i] + GH / 2 + 4}">${LAYER_NAME[i]}</text>`);
  const br = L.brackets.map(b => H`<g class="gbr"><path d="M${b.x0},${b.y - 6} V${b.y - 14} H${b.x1} V${b.y - 6}"/><text x="${b.x0}" y="${b.y - 20}">${b.name}</text></g>`);
  T.svg.innerHTML = DEFS + String(H`${layers}${br}${L.edges.map(edgeSvg)}${L.nodes.map(nodeSvg)}`);
  if (!L.nodes.length) T.svg.innerHTML += '<text class="layer-lbl" x="20" y="40">No topology data yet — press Refresh all.</text>';
  if (!T.vb) fit(); else applyVB();
}

/* -- pan & zoom */
function aspect() { const r = T.svg.getBoundingClientRect(); return r.width ? r.height / r.width : 0.6; }
function applyVB() { const v = T.vb; T.svg.setAttribute('viewBox', `${v.x} ${v.y} ${v.w} ${v.h}`); }
function fit() {
  if (!T.lay) return;
  const a = aspect(), W = T.lay.width + 40, Hh = Math.max(T.lay.height + 40, 200);
  let w = W, h = w * a; if (h < Hh) { h = Hh; w = h / a; }
  T.vb = { x: -20 - (w - W) / 2, y: -10 - (h - Hh) / 2, w, h }; applyVB();
}
function zoom(f, cx, cy) {
  const v = T.vb; if (!v) return;
  const nw = Math.max(200, Math.min(v.w * f, 20000)); f = nw / v.w;
  if (cx === undefined) { cx = v.x + v.w / 2; cy = v.y + v.h / 2; }
  T.vb = { x: cx - (cx - v.x) * f, y: cy - (cy - v.y) * f, w: v.w * f, h: v.h * f }; applyVB();
}
function toSvg(ev) { const r = T.svg.getBoundingClientRect(), v = T.vb; return [v.x + (ev.clientX - r.left) / r.width * v.w, v.y + (ev.clientY - r.top) / r.height * v.h]; }
function bindPanZoom(svg) {
  const pts = new Map(); let moved = false, start = null, pinch = null;
  svg.addEventListener('wheel', ev => { ev.preventDefault(); const [x, y] = toSvg(ev); zoom(Math.exp(ev.deltaY * 0.0015), x, y); }, { passive: false });
  svg.addEventListener('pointerdown', ev => {
    pts.set(ev.pointerId, ev); moved = false;
    if (pts.size === 1) start = { x: ev.clientX, y: ev.clientY, vb: { ...T.vb } };
    if (pts.size === 2) { const [p, q] = [...pts.values()]; pinch = { d: Math.hypot(p.clientX - q.clientX, p.clientY - q.clientY), vb: { ...T.vb } }; }
  });
  svg.addEventListener('pointermove', ev => {
    if (!pts.has(ev.pointerId)) return; pts.set(ev.pointerId, ev);
    const r = svg.getBoundingClientRect();
    if (pts.size === 2 && pinch) {
      const [p, q] = [...pts.values()], d = Math.hypot(p.clientX - q.clientX, p.clientY - q.clientY);
      if (d > 0) { T.vb = { ...pinch.vb }; const [cx, cy] = toSvg({ clientX: (p.clientX + q.clientX) / 2, clientY: (p.clientY + q.clientY) / 2 }); zoom(pinch.d / d, cx, cy); moved = true; }
      return;
    }
    if (!start) return;
    const dx = ev.clientX - start.x, dy = ev.clientY - start.y;
    if (!moved && Math.hypot(dx, dy) < 4) return;
    if (!moved) { moved = true; svg.setPointerCapture(ev.pointerId); svg.classList.add('dragging'); }
    T.vb = { ...start.vb, x: start.vb.x - dx / r.width * start.vb.w, y: start.vb.y - dy / r.height * start.vb.h }; applyVB();
  });
  const end = ev => { pts.delete(ev.pointerId); if (pts.size < 2) pinch = null; if (!pts.size) { start = null; svg.classList.remove('dragging'); } };
  svg.addEventListener('pointerup', end); svg.addEventListener('pointercancel', end);
  svg.addEventListener('click', ev => {
    if (moved) { moved = false; return; }
    const n = ev.target.closest('[data-nid]'), e = ev.target.closest('[data-eid]');
    if (n) openNode(n.dataset.nid); else if (e) openEdge(e.dataset.eid);
  });
  svg.addEventListener('keydown', ev => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const n = ev.target.closest('[data-nid]'), e = ev.target.closest('[data-eid]');
    if (n || e) { ev.preventDefault(); n ? openNode(n.dataset.nid) : openEdge(e.dataset.eid); }
  });
}

/* -- details panel */
function historyHtml(list) {
  const H = LB.H, h = LB.arr(list).slice(-60);
  if (!h.length) return H`<p class="small muted">No history recorded yet.</p>`;
  return H`<div class="hist">${h.map(x => H`<span class="dot ${x.status || 'untested'}" title="${(LB.STATUS_LABEL[x.status] || x.status) + ' · ' + LB.abs(x.checked_at || x.ts) + (x.latency_ms != null ? ' · ' + x.latency_ms + ' ms' : '')}"></span>`)}</div>
    <p class="small muted">${h.length} checks · last ${LB.rel((h[h.length - 1] || {}).checked_at || (h[h.length - 1] || {}).ts)}</p>`;
}
function select(key) { T.sel = key; LB.state.onPanelClose = () => { T.sel = null; draw(); }; draw(); }
function openNode(id) {
  const n = T.lay && T.lay.byId[id]; if (!n) return;
  const H = LB.H, ref = machineRef(n), d = n.detail || {};
  const edges = T.lay.edges.filter(L => L.e.from === id || L.e.to === id);
  const hist = n.history || d.history || (edges.find(L => L.e.to === id) || { e: {} }).e.history;
  select('n:' + id);
  LB.panel.open(n.display_name || n.label, H`<section>${LB.kv([['Label', n.label], ['Display name', n.display_name || null], ['Type', n.type], ['Kind', n.kind || null], ['Group', n.group || null], ['Status', LB.st(n.status)], ['ID', H`<code>${n.id}</code>`]])}</section>
    <section><h3>Details</h3>${Object.keys(d).length ? LB.safeKv(d) : H`<p class="muted small">No details.</p>`}</section>
    <section><h3>Status history</h3>${historyHtml(hist)}</section>
    <section><h3>Connections</h3>${edges.length ? H`${edges.map(L => H`<div class="row"><button class="sm ghost" data-act="topo-edge" data-id="${L.e.id}">${LB.st(L.e.status || 'untested')} ${L.a.label} → ${L.b.label}</button></div>`)}` : H`<p class="muted small">None.</p>`}</section>
    <section><h3>Rename & group</h3><form data-form="topo-node" data-id="${n.id}"><div class="form-errors"></div>
      <div class="field"><label for="tn_dn">Display name</label><input id="tn_dn" name="display_name" value="${n.display_name || ''}" placeholder="${n.label}"></div>
      <div class="field"><label for="tn_gr">Group</label><input id="tn_gr" name="group" value="${n.group || ''}" placeholder="e.g. oracle"></div>
      <div class="row end"><button type="submit" class="primary sm">Save</button></div></form></section>
    ${ref ? H`<section><h3>Machine</h3><div class="row"><button class="sm" data-act="topo-probe" data-kind="${ref.kind}" data-name="${ref.name}">Probe now</button>
      ${ref.kind === 'ssh' ? H`<a class="btn sm" href="#/filesystem?target=${LB.enc(ref.name)}">Permissions</a><a class="btn sm" href="#/ssh?edit=${LB.enc(ref.name)}">Edit</a>` : H`<a class="btn sm" href="#/fleet?edit=${LB.enc(ref.name)}">Edit</a>`}
      <button class="sm danger" data-act="topo-remove" data-kind="${ref.kind}" data-name="${ref.name}">Remove</button></div><div id="topoProbe"></div></section>` : ''}`);
}
function openEdge(id) {
  const L = T.lay && T.lay.edges.find(x => x.e.id === id); if (!L) return;
  const H = LB.H, e = L.e;
  select('e:' + id);
  LB.panel.open(`${L.a.label} → ${L.b.label}`, H`<section>${LB.kv([['Status', LB.st(e.status || 'untested')], ['Transport', transportLabel(e)], ['Protocol', e.protocol || null], ['Exposure', e.exposure ? LB.badge(e.exposure, e.exposure === 'public' ? 'bad' : e.exposure === 'tailnet' ? 'info' : 'ok') : null],
      ['Latency', e.latency_ms != null ? e.latency_ms + ' ms' : null], ['Checked', e.checked_at ? LB.time(e.checked_at) : 'never'], ['Proxied / tunneled', e.proxied ? 'yes' : 'no'], ['Direct', e.direct === undefined ? undefined : e.direct ? 'yes' : 'no'],
      ['Initiator', e.initiator || e.from], ['Jobs running', e.jobs_running || 0]])}</section>
    ${e.detail && Object.keys(e.detail).length ? H`<section><h3>Details</h3>${LB.safeKv(e.detail)}</section>` : ''}
    <section><h3>Status history</h3>${historyHtml(e.history || (e.detail || {}).history)}</section>
    <section><h3>Endpoints</h3><div class="row"><button class="sm" data-act="topo-node" data-id="${L.a.id}">${L.a.label}</button><span>→</span><button class="sm" data-act="topo-node" data-id="${L.b.id}">${L.b.label}</button></div></section>`);
}

/* -- data */
async function load(quiet) {
  const d = await LB.api('/api/admin/topology', { quiet });
  setData(d);
}
function setData(d) {
  d = d || {}; d.nodes = LB.arr(d.nodes); d.edges = LB.arr(d.edges);
  const stale = Number(d.stale_after_seconds) || 0;
  if (stale) d.edges.forEach(e => { if (e.checked_at && e.status && e.status !== 'untested' && Date.now() / 1000 - e.checked_at > stale) e.status = 'stale'; });
  T.data = d; draw(); stampBar();
  if (LB.panel.isOpen() && T.sel) { const [k, id] = [T.sel.slice(0, 1), T.sel.slice(2)]; k === 'n' ? openNode(id) : openEdge(id); }
}
function stampBar() {
  const el = document.getElementById('topoStamp'); if (!el || !T.data) return;
  el.textContent = `data ${LB.rel(T.data.generated_at)}` + (T.refreshedAt ? ` · last refreshed ${new Date(T.refreshedAt).toLocaleTimeString()}` : '');
  LB.stamp();
}

async function mount(v, lb) {
  LB = lb; T.vb = null; T.sel = null; register();
  const H = LB.H, statuses = (T.data && T.data.legend && T.data.legend.statuses) || LB.STATUS;
  v.render(H`${LB.head('Topology', 'How clients reach this gateway and which machines it manages. Status comes from real probes; anything never probed is marked untested.')}
    <div class="topo-wrap"><div class="topo-bar" role="toolbar" aria-label="Topology controls">
      <button class="primary sm" data-act="topo-refresh">Refresh all</button><span class="small muted" id="topoStamp" aria-live="polite"></span><span class="spacer"></span>
      <label class="small muted" for="topoFilter">Status</label><select id="topoFilter" data-change="topo-filter">${['all', ...LB.STATUS].map(s => H`<option value="${s}"${s === T.filter ? LB.raw(' selected') : ''}>${s === 'all' ? 'All' : LB.STATUS_LABEL[s] || s}</option>`)}</select>
      <button class="sm" data-act="topo-fit" title="Fit to view">Fit</button><button class="sm" data-act="topo-zoom" data-f="0.8" aria-label="Zoom in">+</button><button class="sm" data-act="topo-zoom" data-f="1.25" aria-label="Zoom out">−</button>
      <button class="sm" data-act="topo-legend" aria-pressed="${String(T.legend)}">Legend</button></div>
      <svg class="topo-svg" id="topoSvg" xmlns="http://www.w3.org/2000/svg" role="group" aria-label="Topology diagram" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="topo-legend" id="topoLegend"${T.legend ? '' : LB.raw(' hidden')}>${statuses.map(s => H`<span class="st">${LB.dot(s)}${LB.STATUS_LABEL[s] || s}${s === 'untested' ? ' (never probed)' : s === 'stale' ? ' (old probe)' : ''}</span>`)}
        <span class="st">┅ proxied / tunneled</span><span class="st">${LB.badge('public', 'bad')} ${LB.badge('tailnet', 'info')} ${LB.badge('private', 'ok')}</span>
        <span class="st small muted">▭ host · ⬡ Loopback node</span><span class="st small muted">◯ pill tunnel · ☁ network</span></div></div>`);
  T.svg = document.getElementById('topoSvg');
  bindPanZoom(T.svg);
  await load(false);
  v.poll(5000, q => v.alive() ? load(q) : null);
}

let registered = false;
function register() {
  if (registered) return; registered = true;
  const A = LB.A;
  A['topo-refresh'] = async t => {
    const old = t.textContent; t.innerHTML = '<span class="spin"></span> Refreshing…';
    try { const d = await LB.post('/api/admin/topology/refresh'); T.refreshedAt = Date.now(); setData(d && d.nodes ? d : await LB.get('/api/admin/topology')); LB.toast('Topology refreshed', 'ok'); }
    finally { if (t.isConnected) t.textContent = old; }
  };
  A['topo-fit'] = () => fit();
  A['topo-zoom'] = t => zoom(Number(t.dataset.f));
  A['topo-legend'] = t => { T.legend = !T.legend; t.setAttribute('aria-pressed', String(T.legend)); document.getElementById('topoLegend').hidden = !T.legend; };
  A['topo-node'] = t => openNode(t.dataset.id);
  A['topo-edge'] = t => openEdge(t.dataset.id);
  LB.C['topo-filter'] = t => { T.filter = t.value; draw(); };
  LB.F['topo-node'] = async (f, o) => {
    await LB.put('/api/admin/topology/nodes/' + LB.enc(f.dataset.id), { display_name: o.display_name || null, group: o.group || null });
    LB.toast('Saved', 'ok'); await load(true);
  };
  A['topo-probe'] = async t => {
    const box = document.getElementById('topoProbe'), old = t.textContent; t.innerHTML = '<span class="spin"></span> Probing';
    try { const r = await LB.post(`/api/admin/${t.dataset.kind}/${LB.enc(t.dataset.name)}/probe`); await load(true); const b = document.getElementById('topoProbe') || box; if (b) b.innerHTML = String(LB.probeHtml(r)); }
    finally { if (t.isConnected) t.textContent = old; }
  };
  A['topo-remove'] = async t => {
    const name = t.dataset.name;
    if (!await LB.confirmDo(`Remove ${name}?`, LB.H`<p>Remove the managed ${t.dataset.kind === 'ssh' ? 'SSH target' : 'Loopback node'} <b>${name}</b> from this gateway?</p>`, 'Remove')) return;
    await LB.del(`/api/admin/${t.dataset.kind}/${LB.enc(name)}`); T.sel = null; LB.panel.close(); LB.toast('Removed', 'ok'); await load(true);
  };
  window.addEventListener('resize', () => { if (T.svg && T.svg.isConnected && T.vb) { const a = aspect(), c = T.vb.y + T.vb.h / 2; T.vb.h = T.vb.w * a; T.vb.y = c - T.vb.h / 2; applyVB(); } });
}

window.LBTopology = { mount, layout };
})();
