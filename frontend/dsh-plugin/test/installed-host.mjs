// Run with DSH's Electron in Node mode. No profile, model or Controller is used.
// ELECTRON_RUN_AS_NODE=1 <DSH executable> test/installed-host.mjs <app.asar>
import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import * as plugin from '../src/index.js';

assert.ok(process.argv[2], 'pass the installed DSH app.asar path');
const { Context } = await import(pathToFileURL(path.join(process.argv[2], 'dsh/node_modules/@deepseek-ai/cordis/lib/index.js')).href);
assert.equal(plugin.Config, undefined, 'field descriptions are not a Cordis config schema');
const ctx = new Context();
const routes = new Map();
ctx.reflect.provide('webServer', {
  register(route) {
    const key = `${route.kind}:${route.path}`;
    assert.equal(routes.has(key), false);
    routes.set(key, route);
    return () => routes.delete(key);
  }
});
const fiber = ctx.plugin(plugin, { controller: 'http://127.0.0.1:8765' });
await fiber.await();
assert.equal(routes.size, 4, 'real Cordis must activate the host half');
const rows = [];
ctx.emit('webserver/index-inject', rows);
assert.ok(rows.some((r) => r.name === '__ACP_PANELS_CONFIG__'));
assert.ok(rows.some((r) => r.src === '/acp/runtime-scope.js'));
await fiber.dispose();
assert.equal(routes.size, 0, 'all routes must unregister on dispose');
const after = []; ctx.emit('webserver/index-inject', after);
assert.equal(after.length, 0);
console.log('PASS: installed DSH Cordis activation, injection and disposal');
