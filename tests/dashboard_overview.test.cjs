'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Render the shipped overview gauges and rate bars with telemetry fixtures, without a browser.
const source = fs.readFileSync(path.join(__dirname, '../runtime/static/app.js'), 'utf8');
const start = source.indexOf('/* -- Overview */');
const end = source.indexOf("views[''] = async v => {", start);
assert.ok(start >= 0 && end > start);
const renderValue = value => Array.isArray(value) ? value.map(renderValue).join('') : value ?? '';
const H = (parts, ...values) => parts.reduce((out, part, i) => out + part + renderValue(values[i]), '');
const context = { H, raw: v => v, arr: x => Array.isArray(x) ? x : [], st: s => s, rel: () => 'now', enc: encodeURIComponent, ICON: { machine: '<svg/>' },
  bytes: n => n == null ? '—' : n + ' B', badge: v => v };
vm.runInNewContext(source.slice(start, end), context);

test('gauges keep unknown, measured zero and tiny values distinct', () => {
  assert.match(context.ring(null, 'Host CPU'), /unknown/);
  assert.match(context.ring(null, 'Host CPU'), />—</);
  assert.match(context.ring(0, 'Host CPU'), />0%</);
  assert.match(context.ring(0.4, 'Host CPU'), />0\.4%</);
  assert.match(context.ring(72, 'Host CPU'), /ring-stat lg warn/);
  assert.match(context.ring(95, 'Host memory'), /ring-stat lg bad/);
  assert.match(context.ring(95, 'Host memory'), /ring-host-memory/);
});

test('worker cards show whole-host gauges, separate Loopback use and KB/s bars per sample state', () => {
  const host = { cpu_count: 4, cpu_percent: 12.5, memory_percent: 11.6, memory_total: 25136533504, memory_used: 2928607232,
    rx_per_second: 2048, tx_per_second: 0, net_interfaces: ['eth0'], net_excluded: ['lo', 'tailscale0'] };
  const worker = { cpu_percent: 0.3, rss_bytes: 85696512, memory_percent: 0.34, process_count: 2, rx_per_second: 0, tx_per_second: 0 };
  const measured = context.workerCards([{ target: 'ora1', status: 'healthy', host, worker, rates: { state: 'measured', interval_seconds: 5 } }]);
  assert.match(measured, /data-sample-state="measured"/);
  assert.match(measured, /Host CPU/);
  assert.match(measured, /2928607232 B of 25136533504 B used/);
  assert.match(measured, /Loopback on this host/);
  assert.match(measured, /RSS 85696512 B \(0\.34%\)/);
  assert.match(measured, /2\.0 KB\/s/);
  assert.match(measured, /0 KB\/s \(idle\)/);
  assert.match(measured, /2 virtual excluded/);
  const first = context.workerCards([{ target: 'ora3', status: 'healthy', host: { ...host, cpu_percent: null, rx_per_second: null, tx_per_second: null }, worker, rates: { state: 'first' } }]);
  assert.match(first, /data-sample-state="first"/);
  assert.match(first, /first sample/);
  assert.doesNotMatch(first, /0 KB\/s \(idle\)/);
  const stale = context.workerCards([{ target: 'ora5', status: 'stale', host, worker, rates: { state: 'measured', interval_seconds: 5 } }]);
  assert.match(stale, /data-sample-state="stale"/);
  const down = context.workerCards([{ target: 'aws1', status: 'unreachable', error: 'Worker unavailable' }]);
  assert.match(down, /Worker unavailable/);
});
