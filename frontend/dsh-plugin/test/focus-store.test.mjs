import test from 'node:test';
import assert from 'node:assert/strict';

import { createFocusStore, FOCUS_SNAPSHOT_KEYS } from '../src/focus-store.js';

const SESSION = 'sess-1';

test('reopening transfers focus ownership by CAS and fences late writes', () => {
  const store = createFocusStore();
  assert.deepEqual(store.acquire({ session: SESSION, expectedGeneration: null }), { ok: true, generation: 1 });
  assert.equal(store.put(body()).ok, true);
  assert.deepEqual(store.acquire({ session: SESSION, expectedGeneration: 1 }), { ok: true, generation: 2 });
  assert.equal(store.put(body({ revision: 20 })).code, 'stale_generation');
  assert.equal(store.acquire({ session: SESSION, expectedGeneration: 1 }).code, 'stale_generation');
  assert.equal(store.put(body({ generation: 2 })).ok, true);
  assert.equal(store.get(SESSION).generation, 2);
});

function body(patch = {}) {
  return { session: SESSION, generation: 1, revision: 1, snapshot: { projectId: 'p1' }, ...patch };
}

test('a valid write is stored and readable', () => {
  const store = createFocusStore();
  assert.deepEqual(store.put(body()), { ok: true, stored: true });

  const record = store.get(SESSION);
  assert.equal(record.generation, 1);
  assert.equal(record.revision, 1);
  assert.deepEqual(record.snapshot, { projectId: 'p1' });
});

test('unknown snapshot keys are dropped and the caller object is not retained', () => {
  const store = createFocusStore();
  const input = body({
    snapshot: { projectId: 'p1', source: 'user', __proto__x: 'nope', extraField: 42 },
  });
  assert.deepEqual(store.put(input), { ok: true, stored: true });

  /* Mutation after the write must not be observable: the stored copy is fresh. */
  input.snapshot.projectId = 'mutated';
  input.session = 'other';

  const record = store.get(SESSION);
  assert.deepEqual(record.snapshot, { projectId: 'p1', source: 'user' });
  assert.deepEqual(Object.keys(record.snapshot), ['projectId', 'source']);
  assert.equal(store.get('other'), null);
  for (const key of Object.keys(record.snapshot)) {
    assert.ok(FOCUS_SNAPSHOT_KEYS.includes(key));
  }
});

test('a differing generation is fenced with 409 stale_generation', () => {
  const store = createFocusStore();
  assert.equal(store.put(body({ generation: 2, revision: 1 })).ok, true);
  const rejected = store.put(body({ generation: 3, revision: 2 }));
  assert.deepEqual(
    { ok: rejected.ok, status: rejected.status, code: rejected.code },
    { ok: false, status: 409, code: 'stale_generation' },
  );
  assert.equal(store.get(SESSION).revision, 1);
});

test('a non-increasing revision is refused with 409 not_monotonic', () => {
  const store = createFocusStore();
  store.put(body({ revision: 5 }));
  for (const revision of [5, 4, 1]) {
    const rejected = store.put(body({ revision }));
    assert.equal(rejected.status, 409);
    assert.equal(rejected.code, 'not_monotonic');
  }
  assert.equal(store.put(body({ revision: 6 })).ok, true);
  assert.equal(store.get(SESSION).revision, 6);
});

test('malformed bodies are rejected with 400 bad_focus_body', () => {
  const store = createFocusStore();
  const badBodies = [
    null,
    'text',
    [],
    {},
    body({ session: '' }),
    body({ session: 'x'.repeat(129) }),
    body({ session: 'has space' }),
    body({ session: 'slash/es' }),
    body({ session: 42 }),
    body({ generation: 0 }),
    body({ generation: 1.5 }),
    body({ generation: '1' }),
    body({ revision: -2 }),
    body({ revision: Number.NaN }),
    body({ snapshot: null }),
    body({ snapshot: [] }),
  ];
  for (const value of badBodies) {
    const result = store.put(value);
    assert.equal(result.ok, false, `expected rejection for ${JSON.stringify(value)}`);
    assert.equal(result.status, 400);
    assert.equal(result.code, 'bad_focus_body');
  }
  assert.deepEqual(store.list(), []);
});

test('the oldest session is evicted beyond maxSessions', () => {
  const store = createFocusStore({ maxSessions: 2 });
  store.put(body({ session: 'a' }));
  store.put(body({ session: 'b' }));
  store.put(body({ session: 'c' }));

  assert.deepEqual(store.list().map((entry) => entry.session), ['b', 'c']);
  assert.equal(store.get('a'), null);
});

test('reading a session makes it the most recent (LRU, not FIFO)', () => {
  const store = createFocusStore({ maxSessions: 2 });
  store.put(body({ session: 'a' }));
  store.put(body({ session: 'b' }));
  store.get('a'); // touch a
  store.put(body({ session: 'c' }));

  assert.deepEqual(store.list().map((entry) => entry.session), ['a', 'c']);
  assert.equal(store.get('b'), null);
});

test('list() exposes no snapshot contents and is sorted by session', () => {
  const store = createFocusStore();
  store.put(body({ session: 'zeta', revision: 3, generation: 9 }));
  store.put(body({ session: 'alpha', revision: 1, generation: 2 }));

  assert.deepEqual(store.list(), [
    { session: 'alpha', generation: 2, revision: 1 },
    { session: 'zeta', generation: 9, revision: 3 },
  ]);
  assert.deepEqual(Object.keys(store.list()[0]).sort(), ['generation', 'revision', 'session']);
});

test('get() on an unknown session is null, and reset() clears everything', () => {
  const store = createFocusStore();
  assert.equal(store.get('nobody'), null);
  assert.equal(store.get(undefined), null);
  store.put(body());
  store.reset();
  assert.equal(store.get(SESSION), null);
  assert.deepEqual(store.list(), []);
});
