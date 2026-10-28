'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Run the shipped MCP tools view and its change handlers against a mocked policy API, without a browser.
const source = fs.readFileSync(path.join(__dirname, '../runtime/static/app.js'), 'utf8');
const start = source.indexOf('/* -- MCP tools */');
const end = source.indexOf('/* -- Authentication */', start);
assert.ok(start >= 0 && end > start);
const renderValue = value => Array.isArray(value) ? value.map(renderValue).join('') : value ?? '';
const H = (parts, ...values) => parts.reduce((out, part, i) => out + part + renderValue(values[i]), '');

function harness({ serverTools, respond }) {
  const posts = [];
  let pending = [];
  const boxes = {};
  const rows = () => serverTools.map(t => ({ hidden: false, dataset: { tool: t.name, toolName: t.name, toolGroup: t.category || 'other', group: t.category || 'other' } }));
  const context = {
    views: {}, I: {}, C: {}, A: {}, H, raw: v => v, esc: v => String(v), arr: (x, ...k) => Array.isArray(x) ? x : x && typeof x === 'object' ? (k.map(key => x[key]).find(Array.isArray) || []) : [],
    get: async () => ({ tools: serverTools }),
    post: (url, body) => new Promise((resolve, reject) => { posts.push({ url, body: JSON.parse(JSON.stringify(body)) }); pending.push({ resolve, reject, body }); }),
    table: (heads, rowsHtml) => renderValue(rowsHtml), head: (t, d) => String(d), badge: v => v, chk: b => b ? ' checked' : '',
    toast() {}, confirmDo: async () => true, CSS: { escape: s => s },
    $$: sel => sel.startsWith('tr[data-tool]') ? rows() : sel.startsWith('tr.group-row') ? [] : [],
    $: sel => { const m = /data-name="([^"]+)"/.exec(sel); if (m) return boxes[m[1]] = boxes[m[1]] || { checked: true }; return null; },
  };
  vm.runInNewContext(source.slice(start, end), context);
  return {
    context, posts, boxes,
    async open() { let html; await context.views.tools({ render: v => { html = v; }, alive: () => true, poll() {} }); return html; },
    settle(i, value) { const p = pending[i]; if (value instanceof Error) p.reject(value); else p.resolve(respond(p.body, value)); },
    flush: () => new Promise(r => setImmediate(r)),
  };
}
const tools = [{ name: 'tool_one', category: 'files', enabled: true }, { name: 'tool_two', category: 'files', enabled: true }, { name: 'tool_three', category: 'shell', enabled: true }];
// The real API answers with an envelope, not a bare policy. The old handler cached this envelope and lost denials.
const respond = (body, state) => ({ policy: { allowed_tools: ['*'], denied_tools: state.denied }, tools: tools.map(t => ({ ...t, enabled: !state.denied.includes(t.name) })), warnings: [] });

test('rapid sequential toggles are serialized and each carries only its own change', async () => {
  const h = harness({ serverTools: tools, respond });
  const html = await h.open();
  assert.match(html, /tools-select-all/);
  assert.match(html, /tools-deselect-all/);
  const one = h.context.C['tool-toggle']({ dataset: { name: 'tool_one' }, checked: false });
  const two = h.context.C['tool-toggle']({ dataset: { name: 'tool_two' }, checked: false });
  await h.flush();
  assert.equal(h.posts.length, 1, 'second request waits for the first answer');
  assert.deepEqual(h.posts[0].body, { changes: { tool_one: false } });
  h.settle(0, { denied: ['tool_one'] });
  await one; await h.flush();
  assert.equal(h.posts.length, 2);
  assert.deepEqual(h.posts[1].body, { changes: { tool_two: false } });
  h.settle(1, { denied: ['tool_one', 'tool_two'] });
  await two;
  assert.equal(h.boxes.tool_one.checked, false);
  assert.equal(h.boxes.tool_two.checked, false);
  assert.equal(h.boxes.tool_three.checked, true);
});

test('a failed change rolls the checkbox back and does not block later changes', async () => {
  const h = harness({ serverTools: tools, respond });
  await h.open();
  const box = { dataset: { name: 'tool_one' }, checked: false };
  const p = h.context.C['tool-toggle'](box);
  await h.flush();
  h.settle(0, new Error('boom'));
  await assert.rejects(p);
  assert.equal(box.checked, true);
  const q = h.context.C['tool-toggle']({ dataset: { name: 'tool_two' }, checked: false });
  await h.flush();
  assert.equal(h.posts.length, 2);
  h.settle(1, { denied: ['tool_two'] });
  await q;
});

test('bulk actions send all for the unfiltered table and names for a filtered one', async () => {
  const h = harness({ serverTools: tools, respond });
  await h.open();
  const p = h.context.A['tools-bulk']({ dataset: { on: '0' } });
  await h.flush();
  assert.deepEqual(h.posts[0].body, { all: false });
  h.settle(0, { denied: ['*'] });
  await p;
  // Simulate the filter hiding tool_three, then "select shown".
  h.context.$$ = sel => sel.startsWith('tr[data-tool]') ? tools.map(t => ({ hidden: t.name === 'tool_three', dataset: { toolName: t.name, tool: t.name } }))
    : sel.startsWith('tr[data-tool-group="files"]') ? tools.filter(t => t.category === 'files').map(t => ({ dataset: { toolName: t.name } })) : [];
  const q = h.context.A['tools-bulk']({ dataset: { on: '1' } });
  await h.flush();
  assert.deepEqual(h.posts[1].body, { all: true, names: ['tool_one', 'tool_two'] });
  h.settle(1, { denied: ['tool_three'] });
  await q;
  const g = h.context.A['tools-group']({ dataset: { on: '0', group: 'files' } });
  await h.flush();
  assert.deepEqual(h.posts[2].body, { all: false, names: ['tool_one', 'tool_two'] });
  h.settle(2, { denied: ['*'] });
  await g;
});
