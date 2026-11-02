'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../runtime/static/topology.js'), 'utf8');
const ctx = { window: {} };
vm.runInNewContext(source.replace('window.LBTopology =', 'window.testLayout = layout; window.LBTopology ='), ctx);
test('large inventories wrap without overlaps or missing nodes', () => {
  const nodes = Array.from({length: 55}, (_, i) => ({id: String(i), label: 'node '+i, type: i < 30 ? 'tunnel' : 'mcp-service', status:'untested'}));
  const result = ctx.window.testLayout({nodes, edges: []});
  assert.equal(result.nodes.length, 55);
  assert.ok(result.width < 1600);
  for (let i=0; i<result.nodes.length; i++) for(let j=i+1; j<result.nodes.length; j++) {
    const a=result.nodes[i],b=result.nodes[j];
    assert.ok(a.x+a.w<=b.x || b.x+b.w<=a.x || a.y+a.h<=b.y || b.y+b.h<=a.y, `${a.id} overlaps ${b.id}`);
  }
});
