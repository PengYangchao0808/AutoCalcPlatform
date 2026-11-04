/**
 * Pure configuration normalisation for the ACP DSH panel host half.
 *
 * No I/O, no `node:` imports: every function here is total, deterministic and
 * trivially unit-testable. `apply()` is the only place that consumes it.
 */

/**
 * First path segments the browser half actually requests through
 * `<apiPrefix>/`. Every forwarded request must have one of these as its first
 * segment after the prefix; anything else is refused with 403 before a socket
 * is opened. Sorted (byte order) and frozen so the order can be asserted.
 */
export const DEFAULT_ALLOWED_API_PREFIXES = Object.freeze(
  [
    'artifacts',
    'backends',
    'batch-optimize',
    'capabilities',
    'drafts',
    'frame-candidate',
    'frame-candidates',
    'hessian-preview',
    'jobs',
    'mechanism-studies',
    'molecules',
    'nodes',
    'preflight',
    'projects',
    'software',
    'structure-assets',
    'structure-sources',
    'structures',
    'tasks',
    'uploads',
    'v1',
    'v2',
    'validate-method',
    'workflows',
  ].sort(),
);

export const DEFAULTS = Object.freeze({
  uiBase: '/acp',
  apiPrefix: '/acp-api',
  apiKeyEnv: 'ACP_API_KEY',
  readOnly: true,
  requestTimeoutMs: 30_000,
  maxFocusBodyBytes: 65_536,
  maxFocusSessions: 32,
  allowedApiPrefixes: DEFAULT_ALLOWED_API_PREFIXES,
});

const ENV_NAME_RE = /^[A-Z][A-Z0-9_]*$/;
const API_PREFIX_SEGMENT_RE = /^[a-z0-9-]+$/;
/* Control characters plus the whitespace/path-metachar set we refuse in a mount
 * path. Mount paths are only ever concatenated, so this is a guard rail, not
 * the security boundary (resolveUpstream owns that). */
const MOUNT_PATH_RE = /^\/[A-Za-z0-9._~\-/]*$/;

const KNOWN_KEYS = Object.freeze([
  'controller',
  'uiBase',
  'apiPrefix',
  'apiKeyEnv',
  'readOnly',
  'requestTimeoutMs',
  'maxFocusBodyBytes',
  'maxFocusSessions',
  'allowedApiPrefixes',
]);

/** @returns {boolean} true for a plain non-array object (null prototype allowed). */
function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function invalid(code, field, message) {
  return { ok: false, code, field, message };
}

/**
 * Validate one mount path (`uiBase` / `apiPrefix`).
 * @returns {string|null} null when valid, else the failure message.
 */
function mountPathProblem(value) {
  if (typeof value !== 'string' || value.length === 0) return 'must be a non-empty string';
  if (!value.startsWith('/')) return 'must start with "/"';
  if (value.length > 1 && value.endsWith('/')) return 'must not end with "/"';
  if (value === '/') return 'must not be the site root "/"';
  if (value.includes('..')) return 'must not contain ".."';
  if (value.includes('//')) return 'must not contain "//"';
  if (!MOUNT_PATH_RE.test(value)) return 'must contain only unreserved URL path characters';
  return null;
}

/**
 * Validate the controller base URL.
 * @returns {{value?: string, message?: string}} normalised value or a problem.
 */
function controllerProblem(raw) {
  if (typeof raw !== 'string' || raw.trim() === '') {
    return { message: 'must be a non-empty string' };
  }
  const text = raw.trim();
  /* Reject query/fragment on the raw text: `new URL('http://h/?').search` is
   * '' for an empty query, so URL fields alone would let `?`/`#` through. */
  if (text.includes('?')) return { message: 'must not carry a query string' };
  if (text.includes('#')) return { message: 'must not carry a fragment' };

  let parsed;
  try {
    parsed = new URL(text);
  } catch {
    return { message: 'must be an absolute http(s) URL' };
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return { message: 'must use the http: or https: scheme' };
  }
  if (!parsed.hostname) return { message: 'must include a host' };
  if (parsed.username !== '' || parsed.password !== '') {
    return { message: 'must not carry username/password credentials' };
  }

  let value = text;
  while (value.length > 1 && value.endsWith('/')) {
    const stripped = value.slice(0, -1);
    /* Stop before emptying the authority (`http://` has nothing left to strip). */
    if (stripped.endsWith('//')) break;
    value = stripped;
  }
  return { value };
}

/**
 * Normalise and validate a raw plugin config.
 * Unknown keys are ignored so a newer DSH/bundle may pass extra fields.
 * @param {unknown} raw
 * @returns {{ok:true, config:object}|{ok:false, code:string, field:string, message:string}}
 */
export function normalizeConfig(raw) {
  if (!isPlainObject(raw)) {
    return invalid('bad_config', 'config', 'plugin config must be an object');
  }

  const config = {};

  const controller = controllerProblem(raw.controller);
  if (controller.message) {
    const field = 'controller';
    const absent = raw.controller === undefined || raw.controller === null || raw.controller === '';
    return invalid(absent ? 'controller_required' : 'invalid_controller', field, controller.message);
  }
  config.controller = controller.value;

  for (const key of ['uiBase', 'apiPrefix']) {
    /* An omitted mount path falls back to the default; a provided one must be valid. */
    if (raw[key] === undefined) {
      config[key] = DEFAULTS[key];
      continue;
    }
    const problem = mountPathProblem(raw[key]);
    if (problem) return invalid(`invalid_${key}`, key, `${key} ${problem}`);
    config[key] = raw[key];
  }

  if (raw.apiKeyEnv === undefined) {
    config.apiKeyEnv = DEFAULTS.apiKeyEnv;
  } else {
    if (typeof raw.apiKeyEnv !== 'string' || !ENV_NAME_RE.test(raw.apiKeyEnv)) {
      return invalid(
        'invalid_apiKeyEnv',
        'apiKeyEnv',
        'apiKeyEnv must match /^[A-Z][A-Z0-9_]*$/ (an environment variable NAME, never a value)',
      );
    }
    config.apiKeyEnv = raw.apiKeyEnv;
  }

  if (raw.readOnly === undefined) {
    config.readOnly = DEFAULTS.readOnly;
  } else {
    if (typeof raw.readOnly !== 'boolean') {
      return invalid('invalid_readOnly', 'readOnly', 'readOnly must be a boolean');
    }
    config.readOnly = raw.readOnly;
  }

  for (const key of ['requestTimeoutMs', 'maxFocusBodyBytes', 'maxFocusSessions']) {
    if (raw[key] === undefined) {
      config[key] = DEFAULTS[key];
      continue;
    }
    if (!Number.isInteger(raw[key]) || raw[key] <= 0) {
      return invalid(`invalid_${key}`, key, `${key} must be a positive integer`);
    }
    config[key] = raw[key];
  }

  if (raw.allowedApiPrefixes === undefined) {
    config.allowedApiPrefixes = DEFAULT_ALLOWED_API_PREFIXES;
  } else {
    if (!Array.isArray(raw.allowedApiPrefixes)) {
      return invalid(
        'invalid_allowedApiPrefixes',
        'allowedApiPrefixes',
        'allowedApiPrefixes must be an array of lowercase path segments',
      );
    }
    for (const entry of raw.allowedApiPrefixes) {
      if (typeof entry !== 'string' || !API_PREFIX_SEGMENT_RE.test(entry)) {
        return invalid(
          'invalid_allowedApiPrefixes',
          'allowedApiPrefixes',
          'each allowedApiPrefixes entry must match /^[a-z0-9-]+$/',
        );
      }
    }
    /* A supplied list REPLACES the default list (it is not merged). */
    config.allowedApiPrefixes = Object.freeze([...raw.allowedApiPrefixes].sort());
  }

  for (const key of Object.keys(config)) {
    if (!KNOWN_KEYS.includes(key)) delete config[key];
  }

  return { ok: true, config: Object.freeze(config) };
}

/**
 * Build the upstream URL for a request path remainder by string concatenation
 * ONLY. `new URL(rest)` is never used: a remainder of `//evil.example/x` would
 * otherwise re-target the authority.
 * @param {{controller:string}} config
 * @param {string} rest `""` or a path starting with a single `"/"`.
 * @returns {string}
 */
export function upstreamUrl(config, rest) {
  if (typeof rest !== 'string') throw new TypeError('upstreamUrl: rest must be a string');
  if (rest !== '' && !rest.startsWith('/')) {
    throw new TypeError('upstreamUrl: rest must be "" or start with "/"');
  }
  if (rest.startsWith('//')) {
    throw new TypeError('upstreamUrl: rest must not start with "//"');
  }
  return `${config.controller}/api${rest}`;
}

/**
 * Resolve the API key from an environment bag. Never logs, never embeds the
 * value in a message; returns `undefined` when absent or empty.
 * @param {{apiKeyEnv:string}} config
 * @param {Record<string,string|undefined>} env
 * @returns {string|undefined}
 */
export function resolveApiKey(config, env) {
  if (!config || typeof config.apiKeyEnv !== 'string') return undefined;
  const bag = env ?? {};
  const value = bag[config.apiKeyEnv];
  if (typeof value !== 'string' || value.length === 0) return undefined;
  return value;
}
