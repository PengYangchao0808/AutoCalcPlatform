import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULTS,
  DEFAULT_ALLOWED_API_PREFIXES,
  normalizeConfig,
  resolveApiKey,
  upstreamUrl,
} from '../src/config.js';
import { apply } from '../src/index.js';

const BASE = { controller: 'http://127.0.0.1:8765' };

function normalise(patch = {}) {
  const result = normalizeConfig({ ...BASE, ...patch });
  assert.equal(result.ok, true, `expected a valid config, got ${JSON.stringify(result)}`);
  return result.config;
}

test('defaults are applied to an otherwise empty config', () => {
  const config = normalise();
  assert.equal(config.controller, 'http://127.0.0.1:8765');
  assert.equal(config.uiBase, DEFAULTS.uiBase);
  assert.equal(config.apiPrefix, DEFAULTS.apiPrefix);
  assert.equal(config.apiKeyEnv, DEFAULTS.apiKeyEnv);
  assert.equal(config.readOnly, DEFAULTS.readOnly);
  assert.equal(config.requestTimeoutMs, DEFAULTS.requestTimeoutMs);
  assert.equal(config.maxFocusBodyBytes, DEFAULTS.maxFocusBodyBytes);
  assert.equal(config.maxFocusSessions, DEFAULTS.maxFocusSessions);
  assert.deepEqual(config.allowedApiPrefixes, DEFAULT_ALLOWED_API_PREFIXES);
  assert.ok(Object.isFrozen(config));
  assert.ok(Object.isFrozen(DEFAULT_ALLOWED_API_PREFIXES));
});

test('the default API prefix allowlist is sorted and covers the browser surface', () => {
  assert.deepEqual([...DEFAULT_ALLOWED_API_PREFIXES], [...DEFAULT_ALLOWED_API_PREFIXES].sort());
  for (const segment of ['v1', 'v2', 'jobs', 'backends', 'frame-candidates', 'structure-assets']) {
    assert.ok(DEFAULT_ALLOWED_API_PREFIXES.includes(segment), `missing ${segment}`);
  }
});

test('a non-object config is rejected', () => {
  for (const raw of [null, undefined, [], 'x', 42]) {
    const result = normalizeConfig(raw);
    assert.equal(result.ok, false);
    assert.equal(result.code, 'bad_config');
  }
});

test('controller must be an absolute http(s) URL', () => {
  const cases = [
    [{ controller: '' }, 'controller_required'],
    [{ controller: undefined }, 'controller_required'],
    [{ controller: 'ftp://files.example' }, 'invalid_controller'],
    [{ controller: 'ws://files.example' }, 'invalid_controller'],
    [{ controller: 'http://user:pw@files.example' }, 'invalid_controller'],
    [{ controller: 'http://user@files.example' }, 'invalid_controller'],
    [{ controller: 'http://files.example/?token=1' }, 'invalid_controller'],
    [{ controller: 'http://files.example/#frag' }, 'invalid_controller'],
    [{ controller: '/relative/path' }, 'invalid_controller'],
    [{ controller: 'files.example:8765' }, 'invalid_controller'],
  ];
  for (const [patch, code] of cases) {
    const result = normalizeConfig(patch);
    assert.equal(result.ok, false, `expected rejection for ${JSON.stringify(patch)}`);
    assert.equal(result.code, code);
    assert.equal(result.field, 'controller');
  }
});

test('a trailing slash on the controller is stripped', () => {
  assert.equal(normalise({ controller: 'http://127.0.0.1:8765/' }).controller, 'http://127.0.0.1:8765');
  assert.equal(normalise({ controller: 'https://gw.example/acp/' }).controller, 'https://gw.example/acp');
  assert.equal(normalise({ controller: 'http://127.0.0.1:8765' }).controller, 'http://127.0.0.1:8765');
});

test('uiBase and apiPrefix must be normalised absolute paths', () => {
  for (const key of ['uiBase', 'apiPrefix']) {
    for (const value of ['acp', '/acp/', '/', '/acp/../etc', '/a b', '/acp//x', 42, '']) {
      const result = normalizeConfig({ ...BASE, [key]: value });
      assert.equal(result.ok, false, `${key}=${JSON.stringify(value)} should be rejected`);
      assert.equal(result.field, key);
    }
    assert.equal(normalise({ [key]: '/mount-point' })[key], '/mount-point');
  }
});

test('apiKeyEnv must be an env var NAME', () => {
  for (const value of ['acp_key', '1KEY', 'A-B', 'A B', '', 'ACP-KEY']) {
    const result = normalizeConfig({ ...BASE, apiKeyEnv: value });
    assert.equal(result.ok, false, `apiKeyEnv=${JSON.stringify(value)} should be rejected`);
    assert.equal(result.field, 'apiKeyEnv');
  }
  assert.equal(normalise({ apiKeyEnv: 'ACP_API_KEY_2' }).apiKeyEnv, 'ACP_API_KEY_2');
});

test('readOnly and the numeric limits are type-checked', () => {
  assert.equal(normalizeConfig({ ...BASE, readOnly: 'yes' }).ok, false);
  assert.equal(normalise({ readOnly: false }).readOnly, false);
  for (const key of ['requestTimeoutMs', 'maxFocusBodyBytes', 'maxFocusSessions']) {
    for (const value of [0, -1, 1.5, '100', null]) {
      assert.equal(normalizeConfig({ ...BASE, [key]: value }).ok, false, `${key}=${value}`);
    }
    assert.equal(normalise({ [key]: 7 })[key], 7);
  }
});

test('allowedApiPrefixes replaces (not merges) the default list', () => {
  const config = normalise({ allowedApiPrefixes: ['v1', 'jobs'] });
  assert.deepEqual([...config.allowedApiPrefixes], ['jobs', 'v1']);
  assert.equal(config.allowedApiPrefixes.includes('backends'), false);
  assert.equal(normalizeConfig({ ...BASE, allowedApiPrefixes: 'v1' }).ok, false);
  assert.equal(normalizeConfig({ ...BASE, allowedApiPrefixes: ['V1'] }).ok, false);
  assert.equal(normalizeConfig({ ...BASE, allowedApiPrefixes: ['v1/../x'] }).ok, false);
});

test('unknown keys are ignored (forward compatible)', () => {
  const config = normalise({ futureFlag: true, another: { nested: 1 } });
  assert.equal('futureFlag' in config, false);
  assert.equal('another' in config, false);
});

test('upstreamUrl concatenates and never re-parses the remainder', () => {
  const config = normalise();
  assert.equal(upstreamUrl(config, ''), 'http://127.0.0.1:8765/api');
  assert.equal(upstreamUrl(config, '/v1/jobs'), 'http://127.0.0.1:8765/api/v1/jobs');
  assert.equal(upstreamUrl(config, '/v1/jobs?x=1'), 'http://127.0.0.1:8765/api/v1/jobs?x=1');
  /* A protocol-relative remainder must not be able to re-target the authority. */
  assert.throws(() => upstreamUrl(config, '//evil.example/x'), TypeError);
  assert.throws(() => upstreamUrl(config, 'v1/jobs'), TypeError);
});

test('resolveApiKey reads the env by name and never invents a value', () => {
  const config = normalise({ apiKeyEnv: 'ACP_API_KEY' });
  assert.equal(resolveApiKey(config, { ACP_API_KEY: 'secret-value' }), 'secret-value');
  assert.equal(resolveApiKey(config, { OTHER: 'x' }), undefined);
  assert.equal(resolveApiKey(config, { ACP_API_KEY: '' }), undefined);
  assert.equal(resolveApiKey(config, undefined), undefined);
});

// ── index.js smoke test (invalid config only) ──────────────────────────────
// `apply()` cannot be integration-tested without a running DSH host. This proves
// the one behaviour that is fully ours: an invalid config reports itself on the
// index-inject channel and registers no routes.
function stubCtx() {
  const routes = [];
  const listeners = [];
  const ctx = {
    logger: { error() {}, warn() {}, debug() {} },
    effect(fn) {
      return fn();
    },
    inject(_deps, callback) {
      callback();
      return () => {};
    },
    on(event, fn) {
      listeners.push({ event, fn });
      return () => {};
    },
    webServer: {
      register(route) {
        routes.push(route);
        return () => {};
      },
    },
  };
  return { ctx, routes, listeners };
}

test('apply() with an invalid config pushes one error row and registers nothing', () => {
  const { ctx, routes, listeners } = stubCtx();
  apply(ctx, { controller: 'ftp://nope.example' });

  assert.equal(routes.length, 0, 'no route may be registered for an invalid config');
  assert.equal(listeners.length, 1);
  assert.equal(listeners[0].event, 'webserver/index-inject');

  const table = [];
  listeners[0].fn(table);
  assert.equal(table.length, 1);
  assert.deepEqual(table[0], {
    kind: 'global',
    name: '__ACP_PANELS_CONFIG__',
    value: { ok: false, code: 'invalid_controller', field: 'controller' },
  });
});
