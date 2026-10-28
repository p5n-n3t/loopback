'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Run the shipped services view with API fixtures, without a browser or DOM library.
const source = fs.readFileSync(path.join(__dirname, '../runtime/static/app.js'), 'utf8');
const start = source.indexOf('views.services = async v => {');
const end = source.indexOf("C['binding-transport']", start);
assert.ok(start >= 0 && end > start);
const renderValue = value => Array.isArray(value) ? value.map(renderValue).join('') : value ?? '';
const H = (parts, ...values) => parts.reduce((out, part, i) => out + part + renderValue(values[i]), '');

async function services(worker, binding = { desired: 'running' }) {
  const service = { name: 'echo', target: 'remote', transport: 'stdio', port: 3031, exposure: 'private', ...binding };
  const data = { bindings: [service], workers: [{ target: 'remote', ...worker }] };
  let html, poll, tableHtml;
  const context = {
    views: {}, get: async url => url.endsWith('/workers') ? data : { targets: [] }, soft: async () => null, discoveryHtml: () => '',
    arr: value => value.targets || [], H, raw: value => value, enc: encodeURIComponent,
    table: (heads, rows) => renderValue(rows), st: value => value, badge: value => value,
    head: () => '', card: (title, body) => body, errBox: '',
    fld: () => '', inp: () => '', opt: () => '', chk: () => '', lnk: () => '',
    $: () => ({ set innerHTML(value) { tableHtml = value; } }), stamp() {},
  };
  vm.runInNewContext(source.slice(start, end), context);
  await context.views.services({
    params: new URLSearchParams(), render: value => { html = value; }, alive: () => true,
    poll: (ms, callback) => { assert.equal(ms, 5000); poll = callback; },
  });
  return { html, data, async refresh() { await poll(); return tableHtml; } };
}
const actions = html => [...html.matchAll(/data-action="(start|stop)"/g)].map(match => match[1]);

test('a stopped process with running desired state can restart and still stop for editing', async () => {
  const view = await services({ status: 'healthy', bindings: [{ name: 'echo', running: false }] });
  assert.deepEqual(actions(view.html), ['start', 'stop']);
});

test('a rebooted worker without binding state can restart the saved service', async () => {
  const view = await services({ status: 'healthy', bindings: [] });
  assert.deepEqual(actions(view.html), ['start', 'stop']);
});

test('a live service and an unreachable worker keep the existing Stop control', async () => {
  for (const worker of [{ status: 'healthy', bindings: [{ name: 'echo', running: true }] }, { status: 'unreachable' }]) {
    const view = await services(worker);
    assert.deepEqual(actions(view.html), ['stop']);
  }
});

test('an intentionally stopped service has one Start control', async () => {
  const view = await services({ status: 'healthy', bindings: [] }, { desired: 'stopped' });
  assert.deepEqual(actions(view.html), ['start']);
});

test('polling adds recovery after a crash and removes it after a successful start', async () => {
  const view = await services({ status: 'healthy', bindings: [{ name: 'echo', running: true }] });
  view.data.workers[0].bindings[0].running = false;
  assert.deepEqual(actions(await view.refresh()), ['start', 'stop']);
  view.data.workers[0].bindings[0].running = true;
  assert.deepEqual(actions(await view.refresh()), ['stop']);
});
