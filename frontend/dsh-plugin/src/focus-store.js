/**
 * Per-session focus snapshot store (the browser -> host half of the focus
 * bridge). Dependency-free and deterministic: stored records carry no
 * timestamps, so a snapshot of the store is a pure function of the writes.
 *
 * Fencing per session:
 *   - the FIRST write fixes `generation`; a later write with a different
 *     generation is stale (a rebuilt client must not silently overwrite a newer
 *     session's state);
 *   - `revision` must strictly increase, so an out-of-order retry cannot roll a
 *     snapshot back.
 */

/** The ONLY snapshot keys retained; unknown keys are dropped, not rejected. */
export const FOCUS_SNAPSHOT_KEYS = Object.freeze([
  'projectId',
  'researchRef',
  'taskRef',
  'structureRef',
  'networkNodeId',
  'revision',
  'source',
]);

const SESSION_RE = /^[A-Za-z0-9._:-]+$/;
const MAX_SESSION_LENGTH = 128;

function reject(status, code, message) {
  return { ok: false, status, code, message };
}

function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * @param {{maxSessions?:number}} [options]
 * @returns {{put:(body:unknown)=>{ok:true,stored:true}|{ok:false,status:number,code:string,message:string},
 *            get:(session:unknown)=>object|null,
 *            list:()=>Array<{session:string,generation:number,revision:number}>,
 *            reset:()=>void}}
 */
export function createFocusStore(options = {}) {
  const maxSessions = Number.isInteger(options.maxSessions) && options.maxSessions > 0
    ? options.maxSessions
    : 32;

  /** @type {Map<string, {generation:number, revision:number, snapshot:object}>} */
  const records = new Map();

  /* Map preserves insertion order; touching a key (delete + set) makes it the
   * most recent, so the first key is always the LRU victim. */
  const touch = (session, record) => {
    records.delete(session);
    records.set(session, record);
  };

  function put(body) {
    if (!isPlainObject(body)) {
      return reject(400, 'bad_focus_body', 'body must be a JSON object');
    }
    const { session, generation, revision, snapshot } = body;

    if (typeof session !== 'string' || session.length === 0 || session.length > MAX_SESSION_LENGTH
      || !SESSION_RE.test(session)) {
      return reject(
        400,
        'bad_focus_body',
        `session must be 1..${MAX_SESSION_LENGTH} characters of [A-Za-z0-9._:-]`,
      );
    }
    if (!Number.isInteger(generation) || generation <= 0) {
      return reject(400, 'bad_focus_body', 'generation must be a positive integer');
    }
    if (!Number.isInteger(revision) || revision <= 0) {
      return reject(400, 'bad_focus_body', 'revision must be a positive integer');
    }
    if (!isPlainObject(snapshot)) {
      return reject(400, 'bad_focus_body', 'snapshot must be a JSON object');
    }

    /* Fresh object, whitelist keys: the caller's reference is never retained. */
    const filtered = {};
    for (const key of FOCUS_SNAPSHOT_KEYS) {
      if (Object.prototype.hasOwnProperty.call(snapshot, key)) filtered[key] = snapshot[key];
    }

    const existing = records.get(session);
    if (existing) {
      if (existing.generation !== generation) {
        return reject(409, 'stale_generation', 'this session is already fenced to another generation');
      }
      if (revision <= existing.revision) {
        return reject(409, 'not_monotonic', 'revision must increase on every write');
      }
    }

    touch(session, { generation, revision, snapshot: filtered });

    /* LRU eviction: drop the oldest sessions beyond the cap. */
    while (records.size > maxSessions) {
      const oldest = records.keys().next().value;
      records.delete(oldest);
    }

    return { ok: true, stored: true };
  }

  function get(session) {
    if (typeof session !== 'string') return null;
    const record = records.get(session);
    if (!record) return null;
    touch(session, record);
    /* Copy on read: a caller cannot mutate stored state. */
    return {
      generation: record.generation,
      revision: record.revision,
      snapshot: { ...record.snapshot },
    };
  }

  // Explicit CAS handoff for a reopened panel. Old put() writers remain fenced;
  // merely sending a greater generation never grants ownership.
  function acquire({ session, expectedGeneration }) {
    if (typeof session !== 'string' || !SESSION_RE.test(session) || session.length > MAX_SESSION_LENGTH) {
      return reject(400, 'bad_focus_body', 'invalid session');
    }
    const existing = records.get(session);
    if ((existing?.generation ?? null) !== expectedGeneration) {
      return reject(409, 'stale_generation', 'focus owner changed; reconcile before retry');
    }
    const generation = (existing?.generation ?? 0) + 1;
    touch(session, { generation, revision: 0, snapshot: {} });
    while (records.size > maxSessions) records.delete(records.keys().next().value);
    return { ok: true, generation };
  }

  function list() {
    return [...records.keys()]
      .sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
      .map((session) => {
        const record = records.get(session);
        return { session, generation: record.generation, revision: record.revision };
      });
  }

  function reset() {
    records.clear();
  }

  return { put, get, list, reset, acquire };
}
