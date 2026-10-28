'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../runtime/static/app.js'), 'utf8');
const start = source.indexOf('const OWN = {');
const end = source.indexOf('views.services = async v => {', start);
assert.ok(start >= 0 && end > start);
const renderValue = value => Array.isArray(value) ? value.map(renderValue).join('') : value ?? '';
const H = (parts, ...values) => parts.reduce((out, part, i) => out + part + renderValue(values[i]), '');
const context = { H, raw: v => v, arr: x => Array.isArray(x) ? x : [], st: s => s, rel: () => 'just now', badge: (v, k) => `[${v}:${k || ''}]`,
  table: (heads, rows, empty) => rows.length ? renderValue(rows) : empty, A: {}, $: () => null, toast() {}, post: async () => ({}) };
vm.runInNewContext(source.slice(start, end), context);

test('discovered routes show RotoXY exactly as configured and mark ownership and host states', () => {
  const html = context.discoveryHtml({ generated_at: 1, excluded_targets: ['ora2', 'ora4'], note: 'read-only',
    summary: { hosts: 3, reachable: 1, routes: 2, managed: 1, external: 1, gateways: 1 },
    hosts: [
      { target: 'local', status: 'ok', hostname: 'jq-xubuntu1', tailscale: { status: 'ok' }, routes: [
        { id: 'a', provider: 'tailscale', mode: 'funnel', exposure: 'public', hostname: 'jq-xubuntu1.bigeye-escalator.ts.net', port: 443, path: '/', target: 'http://127.0.0.1:2030', owner: 'external', note: 'Existing route. Loopback shows it and never changes it.' },
        { id: 'b', provider: 'tailscale', mode: 'serve', exposure: 'tailnet', hostname: 'jq-xubuntu1.bigeye-escalator.ts.net', port: 8443, path: '/mcp', target: 'http://127.0.0.1:3031', owner: 'managed', owned_by: 'ora1-system', kind: 'mcp-gateway' }],
        listeners: [{ address: '127.0.0.1', port: 2030, owner: 'unknown', mcp_candidate: true }, { address: '0.0.0.0', port: 22, owner: 'unknown', mcp_candidate: false }] },
      { target: 'ora3', status: 'unreachable', error: 'Host unreachable; check SSH and worker setup', last_known: true, routes: [], listeners: [] },
      { target: 'ora2', status: 'excluded', error: 'Excluded by discovery policy; never contacted', routes: [], listeners: [] },
    ] });
  assert.match(html, /jq-xubuntu1\.bigeye-escalator\.ts\.net\//);
  assert.match(html, /http:\/\/127\.0\.0\.1:2030/);
  assert.match(html, /\[external:warn\]/);
  assert.match(html, /\[managed:ok\] <span class="small muted">ora1-system<\/span>/);
  assert.match(html, /\[MCP gateway:info\]/);
  assert.match(html, /never changes it/);
  assert.match(html, /127\.0\.0\.1:2030<\/code>/);
  assert.doesNotMatch(html, /0\.0\.0\.0:22/);
  assert.match(html, /showing last known/);
  assert.match(html, /\[excluded:off\]/);
  assert.match(html, /Never contacted: ora2, ora4/);
});
