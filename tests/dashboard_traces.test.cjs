'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../runtime/static/app.js'), 'utf8');
const start = source.indexOf('/* -- Execution traces');
const end = source.indexOf('const lg = { file:', start);
assert.ok(start >= 0 && end > start);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
const renderValue = value => Array.isArray(value) ? value.map(renderValue).join('') : value instanceof Raw ? value.value : esc(value ?? '');
function Raw(value) { this.value = value; this.toString = () => value; }
const H = (parts, ...values) => new Raw(parts.reduce((out, part, i) => out + part + renderValue(values[i]), ''));
const context = {
  H, raw: value => new Raw(value), arr: value => Array.isArray(value) ? value : [],
  abs: value => value == null ? 'unavailable' : new Date(Number(value) * 1000).toISOString(),
  rel: () => 'now', badge: (label, kind) => `<span class="badge ${esc(kind)}">${esc(label)}</span>`,
  enc: encodeURIComponent, URLSearchParams, views: {}, C: {}, I: {}, A: {}, cur: { poll() {} },
  soft: async () => null, head: () => new Raw(''), table: () => new Raw(''),
};
vm.runInNewContext(source.slice(start, end) + '\nglobalThis.traceStateLabel = traceStateLabel; globalThis.traceStatusParam = traceStatusParam; globalThis.traceUtc = traceUtc; globalThis.traceFilters = traceFilters;', context);

test('trace outcome keeps pre-dispatch gaps unresolved and only explicit safety reports blocked', () => {
  assert.equal(context.traceStateLabel({ state: 'unobserved_at_gateway', classification: 'unobserved_at_gateway_unknown_cause' }), 'unresolved');
  assert.equal(context.traceStateLabel({ state: 'unobserved_at_gateway', classification: 'client_reported_openai_safety_block' }), 'blocked');
  assert.equal(context.traceStateLabel({ state: 'policy_denied', classification: 'loopback_policy_rejection' }), 'blocked');
  assert.equal(context.traceStateLabel({ state: 'completed', classification: 'successful_execution', returncode: 0 }), 'successful');
  assert.equal(context.traceStatusParam('successful'), 'success');
  assert.equal(context.traceStatusParam('unresolved'), 'unresolved');
  assert.equal(context.traceUtc(Date.parse('2026-11-05T00:43:20Z') / 1000), '2026-11-05T00:43:20Z');
});

test('trace rows show gateway and client evidence separately, group repeats and escape values', () => {
  const traces = [
    { trace_id: 'safe-id', created_at: Date.parse('2026-11-05T01:00:00Z') / 1000, state: 'unobserved_at_gateway', classification: 'unobserved_at_gateway_unknown_cause', tool: 'ssh_execute', target: '<script>alert(1)</script>', client: 'client', remote_execution: 'not_observed', events: [] },
    { trace_id: 'other-id', created_at: Date.parse('2026-11-05T00:59:59Z') / 1000, state: 'unobserved_at_gateway', classification: 'unobserved_at_gateway_unknown_cause', tool: 'ssh_execute', target: '<script>alert(1)</script>', client: 'client', remote_execution: 'not_observed', events: [] },
    { trace_id: 'blocked-id', created_at: Date.parse('2026-11-05T00:43:20Z') / 1000, state: 'unobserved_at_gateway', classification: 'client_reported_openai_safety_block', tool: 'ssh_execute', target: 'ora1', client: 'chatgpt-web', remote_execution: 'not_observed', client_reports: [{ outcome: 'safety_block', error_message: 'This tool call was blocked by OpenAI safety checks.', reported_at: Date.parse('2026-11-05T00:43:20Z') / 1000 }], events: [] },
  ];
  const html = context.traceRows(traces).join('');
  assert.match(html, /No gateway receipt observed/);
  assert.match(html, /2026-11-05T01:00:00Z/);
  assert.match(html, /2 similar/);
  assert.match(html, /same classification, tool and target/);
  assert.match(html, /JSON report/);
  assert.match(html, /Markdown report/);
  assert.match(html, /blocked/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>alert\(1\)<\/script>/);
  assert.match(html, /Missing gateway evidence alone cannot identify why a request did not arrive/);
});

test('view maps successful status to backend group and discards stale route results', async () => {
  let requested = '', rendered = false, poll;
  context.traceFilters.status = 'successful';
  context.soft = async url => { requested = url; return { traces: [], coverage: {} }; };
  context.views.traces && await context.views.traces({ alive: () => false, render: () => { rendered = true; }, poll: (_, fn) => { poll = fn; } });
  assert.match(requested, /status=success/);
  assert.equal(rendered, false);
  assert.equal(typeof poll, 'undefined');
});

