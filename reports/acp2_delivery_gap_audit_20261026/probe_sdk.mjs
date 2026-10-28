// Use the SDK actually installed for the locked DSH checkout, not a handwritten client.
import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL, fileURLToPath } from 'node:url';
const audit = JSON.parse(readFileSync(new URL('./evidence/python-probes.json', import.meta.url), 'utf8'));
const clientPath = audit.sdk_client_paths[0];
const { Client } = await import(pathToFileURL(clientPath));
const { StdioClientTransport } = await import(pathToFileURL(clientPath.replace('/client/index.js', '/client/stdio.js')));
const transport = new StdioClientTransport({ command: '/usr/bin/python3.11', args: [fileURLToPath(new URL('./probe_python.py', import.meta.url)), '--stdio'], stderr: 'pipe' });
let stderr = '';
const client = new Client({ name: 'audit-locked-dsh-sdk', version: '1' }, { capabilities: {}, versionNegotiation: { mode: 'auto' } });
let result;
try {
  await client.connect(transport);
  result = { connected: true, tools: await client.listTools() };
} catch (error) {
  result = { connected: false, code: error.code, message: error.message, data: error.data };
} finally {
  if (transport.stderr) transport.stderr.on('data', chunk => { stderr += chunk.toString(); });
  await client.close();
}
result = { scope: 'actual DSH-installed SDK to actual ACP stdio/dispatch with no-effects backend ports; no DSH Agent/model/Controller', sdk_client: clientPath, ...result, stderr };
writeFileSync(new URL('./evidence/sdk-probe.json', import.meta.url), JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify(result));
