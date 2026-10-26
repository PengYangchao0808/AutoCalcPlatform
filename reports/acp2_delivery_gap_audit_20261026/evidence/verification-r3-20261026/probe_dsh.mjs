// Resolve from the actual DSH plugin, never from a convenient adjacent SDK.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
const plugin = '/ACPServer/acp2-tools/deepseek-harness/packages/mcp/mcp-client/package.json';
const require = createRequire(plugin);
const packageRoot = dirname(dirname(require.resolve('@modelcontextprotocol/client')));
const sdkPackage = JSON.parse(readFileSync(resolve(packageRoot, 'package.json')));
// Match the plugin's ESM imports after resolving the package from its location.
const clientPath = resolve(packageRoot, sdkPackage.exports['.'].import.default);
const stdioPath = resolve(packageRoot, sdkPackage.exports['./stdio'].import.default);
const { Client } = await import(pathToFileURL(clientPath));
const { StdioClientTransport } = await import(pathToFileURL(stdioPath));
const sent = [];
const transport = new StdioClientTransport({ command: '/usr/bin/python3.11',
  args: [fileURLToPath(new URL('../../probe_python.py', import.meta.url)), '--stdio'], stderr: 'pipe' });
const send = transport.send.bind(transport);
transport.send = async message => { sent.push(message); return send(message); };
const client = new Client({ name: 'dsh-mcp-client', version: '0.0.1' },
  { capabilities: {}, versionNegotiation: { mode: 'auto' } });
let observation;
try { await client.connect(transport); observation = { connected: true }; }
catch (error) { observation = { connected: false, code: error.code, message: error.message, data: error.data }; }
finally { await client.close(); }
assert.equal(observation.code, -32602);
const result = { scope: 'Actual plugin-resolved client with production constructor options to real ACP stdio and no-effects ports; no DSH Agent/model/Controller',
  plugin, dependency: JSON.parse(readFileSync(plugin)).dependencies['@modelcontextprotocol/client'],
  clientPath, stdioPath, sent, observation };
writeFileSync(new URL('./dsh.json', import.meta.url), JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify(result));
