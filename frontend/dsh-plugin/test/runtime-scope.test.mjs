import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function fixture() {
  const clock = new Map(); let seq = 0;
  const host = new EventTarget();
  Object.assign(host, { document: new EventTarget(), AbortController,
    setTimeout(fn) { const id = ++seq; clock.set(id, fn); return id; },
    clearTimeout(id) { clock.delete(id); },
    requestAnimationFrame(fn) { const id = ++seq; clock.set(id, fn); return id; },
    cancelAnimationFrame(id) { clock.delete(id); },
    fetch() { return Promise.resolve({ ok: true }); } });
  const ownNode = {};
  const container = { querySelector(selector) { assert.equal(selector, '[id="same-id"]'); return ownNode; } };
  host.document.getElementById = () => ({ otherMount: true });
  vm.runInNewContext(fs.readFileSync(new URL('../client/runtime-scope.js', import.meta.url), 'utf8'), { window: host, Proxy, Map, Set, Promise, Date });
  return { host, clock, ownNode, scope: host.ACPDSHScope.create(host, container) };
}

test('retained scope pauses timers, resumes the same handles and disposes them', () => {
  const { clock, scope } = fixture(); let calls = 0;
  const handle = scope.window.setInterval(() => calls++, 100);
  assert.equal(clock.size, 1);
  scope.pause(); assert.equal(clock.size, 0);
  scope.resume(); assert.equal(clock.size, 1);
  const run = clock.values().next().value; clock.clear(); run();
  assert.equal(calls, 1); assert.equal(clock.size, 1);
  scope.window.clearInterval(handle); assert.equal(clock.size, 0);
  scope.window.setTimeout(() => calls++, 100);
  scope.pause(); scope.dispose(); scope.resume(); assert.equal(clock.size, 0);
});

test('retained scopes resolve duplicate ids inside their own mount only', () => {
  const { scope, ownNode } = fixture();
  assert.equal(scope.window.document.getElementById('same-id'), ownNode);
  scope.dispose();
});

test('global listeners are inert while parked and removable using original callbacks', () => {
  const { scope, host } = fixture(); let calls = 0;
  const listener = () => calls++;
  scope.window.addEventListener('resize', listener);
  host.dispatchEvent(new Event('resize')); assert.equal(calls, 1);
  scope.pause(); host.dispatchEvent(new Event('resize')); assert.equal(calls, 1);
  scope.resume(); host.dispatchEvent(new Event('resize')); assert.equal(calls, 2);
  scope.window.removeEventListener('resize', listener);
  host.dispatchEvent(new Event('resize')); assert.equal(calls, 2);
  scope.dispose();
});

test('parked scopes reject new work and resume it only on activation', async () => {
  const { scope } = fixture();
  scope.pause(); await assert.rejects(scope.window.fetch('/api/test'), /inactive/);
  assert.equal(scope.window.requestAnimationFrame(() => {}), null);
  scope.resume(); assert.equal((await scope.window.fetch('/api/test')).ok, true);
  scope.dispose();
});
