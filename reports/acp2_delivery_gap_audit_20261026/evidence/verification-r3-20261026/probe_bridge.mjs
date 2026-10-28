// Real compiled Bridge; recording port replaces all Controller/science effects.
import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { Bridge } from '../../../../rph/packages/bridge/dist/index.js';

function session() {
  const calls = [];
  const bridge = new Bridge({ callTool: async (tool, args) => {
    calls.push({ tool, args });
    return { isError: false, structuredContent: { status: 'ok', data: {
      reference: { kind: tool === 'acp_submit' ? 'job' : 'operation', id: `ref-${calls.length}` },
    } } };
  } });
  return { bridge, calls };
}
const base = { project: 'r3-project', workflow: 'singlepoint', input: { source: 'C' },
  method: {}, idempotencyKey: 'r3-shared-key' };
const a = session();
const validate = await a.bridge.execute({ ...base, operation: 'validate' });
const submit = await a.bridge.execute({ ...base, operation: 'submit' });
assert.equal(a.calls.length, 1);
assert.equal(submit.operation, 'validate');
assert.equal(submit, validate);
const b = session();
const first = await b.bridge.execute({ operation: 'evidence', task: 'job-a', attempt: 1,
  resource: 'evidence', project: base.project, idempotencyKey: 'r3-evidence-key' });
const other = await b.bridge.execute({ operation: 'evidence', task: 'job-b', attempt: 2,
  resource: 'different-evidence', project: base.project, idempotencyKey: 'r3-evidence-key' });
assert.equal(b.calls.length, 1);
assert.equal(first, other);
const c = session();
await c.bridge.execute({ ...base, operation: 'validate' });
const separateKey = await c.bridge.execute({ ...base, operation: 'submit', idempotencyKey: 'r3-different-key' });
assert.equal(c.calls.length, 2);
assert.equal(separateKey.operation, 'submit');
const result = { scope: 'Real Bridge, recording PlatformClient; no MCP transport/model/QC',
  validate_then_submit: { calls: a.calls, validate, submit },
  evidence_different_task_attempt_resource: { calls: b.calls, first, other },
  control_different_key: { calls: c.calls, submit: separateKey } };
writeFileSync(new URL('./bridge.json', import.meta.url), JSON.stringify(result, null, 2) + '\n');
console.log('3 Bridge cases reproduced, including distinct-key negative control');
