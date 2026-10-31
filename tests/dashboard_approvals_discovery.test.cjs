'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../runtime/static/app.js'), 'utf8');
const slice = (from, to) => {
  const a = source.indexOf(from), b = source.indexOf(to, a);
  assert.ok(a >= 0 && b > a, `slice ${from}`);
  return source.slice(a, b);
};
// Real escaping, H, badge, kv, card and form helpers + the new pure render block, with stubs for the rest.
const code = [
  slice('const ESC =', 'const SECRET_KEY'),
  slice('const arr =', 'const yes ='), 'const yes = b => b ? "yes" : "no";',
  slice('const al = ', '/* Render an arbitrary backend object'),
  slice('const card = ', 'function errorsHtml'),
  slice('/* ==== APPROVALS-DISCOVERY BEGIN', '/* ---- staged discovery: browser state'),
  '({ approvalDetailHtml, approvalsQueueHtml, approvalsHistoryHtml, scanHostsTableHtml, scanStagesHtml, enrollPlanHtml, meshCardHtml, scanEmptyHtml, scanProvidersHtml })',
].join('\n');
const ctx = { rel: () => '1m ago', abs: () => 'ABS', enc: encodeURIComponent, targetBadge: t => `[${t || 'local'}]`, A: {}, C: {}, I: {}, F: {}, doc: {}, ApiError: Error, $: () => null, $$: () => [] };
const R = vm.runInNewContext(code, ctx);
const S = x => String(x);
const E = x => x.replace(/&/g, '&amp;').replace(/"/g, '&quot;');

const longCmd = 'sudo bash -c "' + 'echo very-long-argument-'.repeat(40) + ' --password=[REDACTED]" && ls /var/lib/some/really/deep/path';
const appr = { id: 'ap1', status: 'pending', action: 'command', summary: 'sudo bash -c …', target: 'ora1', principal: 'Claude', created_at: 1, expires_at: 2, expires_in_seconds: 250, fingerprint_short: 'abcdef012345', uses_left: 1,
  detail: { command: longCmd, cwd: '/srv/app', target: 'ora1', matched_rule: 'sudo-any', redacted_values: 2, length: longCmd.length, lines: 1, privilege: 'sudo' } };

test('approval detail shows the whole command, redaction note, fingerprint and scoped buttons', () => {
  const h = S(R.approvalDetailHtml(appr));
  assert.ok(h.includes(E(longCmd)));
  assert.doesNotMatch(h, /<pre[^>]*>[^<]*(…|\.\.\.)<\/pre>/);
  assert.match(h, /\[REDACTED\]/);
  assert.match(h, /2 value\(s\) hidden/);
  assert.match(h, /abcdef012345/);
  assert.match(h, /\/srv\/app/);
  assert.match(h, /sudo-any/);
  assert.match(h, /sudo \(root\)/);
  assert.match(h, /Expires in 4m 10s/);
  for (const label of ['Approve once', 'Approve for 1 hour \\(20 uses\\)', 'Deny…', 'Copy command', 'Confirm deny', 'Reason \\(optional\\)']) assert.match(h, new RegExp(label));
  assert.match(h, /binds this exact command, working directory, target and caller/);
  assert.match(h, /data-fp="abcdef012345"/);
});

test('no hidden-values note when nothing was redacted; decided approvals have no decision buttons', () => {
  const a = { ...appr, status: 'denied', decided_by: 'admin', reason: 'too risky', detail: { ...appr.detail, redacted_values: 0 } };
  const h = S(R.approvalDetailHtml(a));
  assert.doesNotMatch(h, /value\(s\) hidden/);
  assert.doesNotMatch(h, /Approve once/);
  assert.match(h, /too risky/);
});

test('queue rows are keyboard-expandable and render details only when open', () => {
  const closed = S(R.approvalsQueueHtml([appr], new Set()));
  assert.match(closed, /aria-expanded="false"/);
  assert.match(closed, /aria-controls="appr-d-ap1"/);
  assert.match(closed, /Review &amp; decide/);
  assert.match(closed, /<tr class="detail-row"[^>]* hidden>/);
  assert.doesNotMatch(closed, /Approve once/);
  const open = S(R.approvalsQueueHtml([appr], new Set(['ap1'])));
  assert.match(open, /aria-expanded="true"/);
  assert.match(open, /Approve once/);
  assert.match(S(R.approvalsQueueHtml([], new Set())), /No pending approvals/);
});

test('history table shows decided_by, reason and a status filter', () => {
  const rows = [{ ...appr, status: 'approved', decided_by: 'alice', decided_at: 5, reason: 'ok for deploy', grant: { uses: 20, expires_at: 9, scope: 'exact' } }];
  const h = S(R.approvalsHistoryHtml(rows, 'approved'));
  assert.match(h, /alice/);
  assert.match(h, /ok for deploy/);
  assert.match(h, /<option value="approved" selected>/);
  for (const f of ['all', 'denied', 'expired', 'consumed']) assert.match(h, new RegExp(`value="${f}"`));
  assert.ok(h.includes(E(longCmd)));
});

const host = (o) => ({ id: 'h1', display_name: 'ora1', aliases: [], addresses: ['10.0.0.1'], dns_names: [], online: true, state: 'new', next_action: 'Probe it', excluded: false, sources: [{ provider: 'tailscale', observed_at: 3, online: true }], axes: { dns: { result: 'ok', freshness: 'fresh' }, ssh: { result: 'fail', freshness: 'stale', error: 'Permission denied (publickey)' } }, ssh: { alias: 'ora1', endpoint: '10.0.0.1', port: 2222, user: 'ubuntu' }, ...o });

test('host table renders every state as text, excluded notice, ssh endpoint and stable ids', () => {
  const states = ['local', 'new', 'existing', 'excluded', 'offline', 'stale', 'auth_required', 'host_key_review', 'human_check', 'enrollment_required', 'failed'];
  const hosts = states.map(s => host({ id: 'id-' + s, display_name: 'name-' + s, state: s, excluded: s === 'excluded', exclusion_match: s === 'excluded' ? 'ora2*' : undefined }));
  const h = S(R.scanHostsTableHtml({ hosts, empty_reason: null }, { sel: new Set(['id-new']), open: new Set(['id-host_key_review']) }));
  for (const t of ['This machine', 'New', 'Existing', 'Excluded', 'Offline', 'Stale', 'Authentication required', 'Host key review', 'Needs human check', 'Enrollment required', 'Failed']) assert.ok(h.includes(`>${t}</span>`), t);
  assert.match(h, /Excluded — never contacted \(matched: ora2\*\)/);
  assert.match(h, /ubuntu@10\.0\.0\.1:2222/);
  assert.match(h, /data-id="id-new"/);
  assert.match(h, /dns: ok \(fresh\)/);
  assert.match(h, /ssh: fail \(stale\)/);
  assert.match(h, /Permission denied \(publickey\)/);
  assert.match(h, /data-id="id-new"[^>]* checked/);
  assert.match(h, /Review host key/);
  assert.match(h, /aria-sort="ascending"/);
});

test('host filter and state filter narrow the table; empty filter result differs from empty inventory', () => {
  const snap = { hosts: [host({ id: 'a', display_name: 'alpha' }), host({ id: 'b', display_name: 'beta', state: 'failed' })] };
  assert.doesNotMatch(S(R.scanHostsTableHtml(snap, { q: 'alp' })), /beta/);
  assert.doesNotMatch(S(R.scanHostsTableHtml(snap, { state: 'failed' })), /alpha/);
  assert.match(S(R.scanHostsTableHtml(snap, { q: 'zzz' })), /No hosts match/);
});

test('empty_reason variants produce distinct messages and always offer manual entry', () => {
  const out = ['not_scanned', 'no_provider', 'provider_unavailable', 'no_peers', null].map(r => S(R.scanHostsTableHtml({ hosts: [], empty_reason: r })));
  assert.equal(new Set(out).size, 5);
  assert.match(out[0], /Not scanned yet/);
  assert.match(out[1], /No discovery provider installed/);
  assert.match(out[2], /provider unavailable/i);
  assert.match(out[3], /No peers visible/);
  out.forEach(o => assert.match(o, /Add a host manually/));
});

test('stage strip lists S0-S9 with text, progress and cancel only while running', () => {
  const stages = {}; for (let i = 0; i < 10; i++) stages['S' + i] = { state: i < 3 ? 'done' : 'pending', title: 'Stage title ' + i };
  const run = { id: 'r1', status: 'running', stages, hosts: { h1: { outcome: 'ssh ok' } }, progress: { done: 1, total: 4 } };
  const h = S(R.scanStagesHtml(run));
  for (let i = 0; i < 10; i++) assert.ok(h.includes('Stage title ' + i));
  assert.match(h, /1 of 4 host\(s\) done/);
  assert.match(h, /data-act="scan-cancel"/);
  assert.match(h, /ssh ok/);
  assert.doesNotMatch(S(R.scanStagesHtml({ ...run, status: 'done' })), /scan-cancel/);
  assert.match(S(R.scanStagesHtml(null)), /No probe has run/);
});

test('provider row shows state text and remedy', () => {
  const h = S(R.scanProvidersHtml({ providers: { tailscale: { state: 'unavailable', remedy: 'Run tailscale up', candidates: 0 }, mesh: { state: 'ready', candidates: 3 } } }));
  assert.match(h, /Tailscale/); assert.match(h, /unavailable/); assert.match(h, /Run tailscale up/); assert.match(h, /Cloudflare Mesh/);
});

test('enrollment plan review lists mutations, privilege, rollback, blocked hosts and an explicit apply button', () => {
  const plan = { id: 'p1', note: 'Review first', steps: [{ host_id: 'h1', action: 'install_worker', name: 'worker-h1', mutation: 'Copies worker to ~/.loopback', privilege: 'user', requires: ['ssh'], rollback: 'rm -rf ~/.loopback' }], blocked: [{ host_id: 'h2', reason: 'host key not confirmed' }] };
  const h = S(R.enrollPlanHtml(plan));
  for (const t of ['Copies worker to ~/.loopback', 'rm -rf ~/.loopback', 'host key not confirmed', 'Apply plan', 'install_worker']) assert.ok(h.includes(t), t);
  const applied = S(R.enrollPlanHtml({ ...plan, status: 'partial', steps: [{ ...plan.steps[0], error: 'boom' }] }));
  assert.match(applied, /Failed: boom/); assert.doesNotMatch(applied, /Apply plan/);
});

test('mesh card: distinct_from, blocking coexistence warning, skipped steps, apply command', () => {
  const d = { product: 'Cloudflare Mesh', distinct_from: 'Cloudflare Tunnel (public ingress)', detection: { state: 'unregistered', remedy: 'Register with warp-cli' },
    preflight: { ok: false, findings: [{ id: 'range_overlap', severity: 'critical', detail: 'Mesh range overlaps Tailscale' }], blocking: ['range_overlap'], alternatives: [{ id: 'keep-tailscale', detail: 'Use Tailscale only' }], mesh_range: '100.96.0.0/12', tailscale_active: true },
    platform: { supported: true, distro: 'ubuntu', version: '24.04', arch: 'x86_64' },
    plan: { ready: false, blocked: ['range overlap'], manual_steps: ['Create a Mesh network in the Cloudflare dashboard'], verify: ['warp-cli status'], after: 'Add the node', steps: [{ id: 'a', title: 'Install package', privilege: 'root', skip: true }, { id: 'b', title: 'Register device', privilege: 'root', skip: false }] },
    apply_command: 'sudo loopback mesh apply --yes', live_enrollment: 'Live enrollment is not performed by the dashboard.' };
  const h = S(R.meshCardHtml(d));
  assert.match(h, /Cloudflare Tunnel \(public ingress\)/);
  assert.match(h, /Coexistence warning/);
  assert.match(h, /100\.96\.0\.0\/12/);
  assert.match(h, /Install package[\s\S]*already done/);
  assert.doesNotMatch(h, /Register device<\/b> <span class="badge ">root<\/span> <span class="badge ok">already done/);
  assert.match(h, /<code class="cmd block">sudo loopback mesh apply --yes<\/code>/);
  assert.match(h, /needs root/);
  assert.match(h, /Live enrollment is not performed/);
  assert.match(h, /Use Tailscale only/);
  assert.match(h, /Create a Mesh network/);
  assert.doesNotMatch(h, /data-act="mesh-apply"/);
  assert.match(S(R.meshCardHtml(null)), /unavailable/);
});

test('app.js escapes untrusted host data', () => {
  const h = S(R.scanHostsTableHtml({ hosts: [host({ display_name: '<img src=x onerror=alert(1)>' })] }, {}));
  assert.doesNotMatch(h, /<img src=x/);
});
