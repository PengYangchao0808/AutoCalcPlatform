import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';

import { normalizeConfig } from '../src/config.js';
import { ALLOWED_METHODS, createProxyHandler, resolveUpstream } from '../src/proxy.js';

const CONTROLLER = 'http://127.0.0.1:1';

function makeConfig(patch = {}) {
  const result = normalizeConfig({ controller: CONTROLLER, ...patch });
  assert.equal(result.ok, true);
  return result.config;
}

const PURITY_CONFIG = makeConfig();

// ── pure path validation ──────────────────────────────────────────────────

test('resolveUpstream accepts a whitelisted first segment and preserves the query', () => {
  const result = resolveUpstream(PURITY_CONFIG, '/acp-api/v1/jobs?after_seq=3&x=a%20b');
  assert.equal(result.ok, true);
  assert.equal(result.upstream, 'http://127.0.0.1:1/api/v1/jobs?after_seq=3&x=a%20b');
  assert.equal(result.prefix, 'v1');
  assert.equal(result.query, '?after_seq=3&x=a%20b');
});

test('resolveUpstream rejects traversal, duplicate slashes, backslash and control chars', () => {
  for (const url of [
    '/acp-api/../etc/passwd',
    '/acp-api/v1/../../x',
    '/acp-api//v1/jobs',
    '/acp-api/v1\\jobs',
    '/acp-api/v1/jo\u0000bs',
    '/acp-api/v1/jo\tbs',
  ]) {
    const result = resolveUpstream(PURITY_CONFIG, url);
    assert.equal(result.ok, false, `expected rejection for ${JSON.stringify(url)}`);
    assert.equal(result.status, 400);
    assert.equal(result.code, 'bad_request');
  }
});

test('resolveUpstream rejects a path outside the apiPrefix', () => {
  for (const url of ['/v1/jobs', '/acp', '/acp-apix/v1', '/']) {
    const result = resolveUpstream(PURITY_CONFIG, url);
    assert.equal(result.ok, false);
    assert.equal(result.status, 400);
    assert.equal(result.code, 'bad_request');
  }
});

test('resolveUpstream refuses a first segment that is not on the allowlist', () => {
  /* `/acp-api/` has an empty first segment and `/acp-api` (no trailing slash) is
   * outside the `<apiPrefix>/` tree entirely — both are 403/400, never a proxy. */
  for (const url of ['/acp-api/admin', '/acp-api/', '/acp-api/secrets/x']) {
    const result = resolveUpstream(PURITY_CONFIG, url);
    assert.equal(result.ok, false, `expected 403 for ${url}`);
    assert.equal(result.status, 403);
    assert.equal(result.code, 'forbidden_prefix');
  }
  const bare = resolveUpstream(PURITY_CONFIG, '/acp-api');
  assert.equal(bare.ok, false);
  assert.equal(bare.status, 400);
  assert.equal(bare.code, 'bad_request');
});

test('resolveUpstream honours a custom allowlist', () => {
  const config = makeConfig({ allowedApiPrefixes: ['v1'] });
  assert.equal(resolveUpstream(config, '/acp-api/v1/jobs').ok, true);
  assert.equal(resolveUpstream(config, '/acp-api/v2/jobs').status, 403);
});

test('resolveUpstream handles an empty/garbage url', () => {
  for (const url of ['', null, undefined, 42]) {
    const result = resolveUpstream(PURITY_CONFIG, url);
    assert.equal(result.ok, false);
    assert.equal(result.status, 400);
  }
});

// ── integration against a real loopback controller ────────────────────────

function listen(handler) {
  return new Promise((resolve) => {
    const server = http.createServer(handler);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({
        server,
        port,
        base: `http://127.0.0.1:${port}`,
        close: () => new Promise((done) => {
          server.closeAllConnections();
          server.close(done);
        }),
      });
    });
  });
}

/** Fire one request at the proxy and collect the whole response. */
function call(port, path, { method = 'GET', headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    /* agent:false keeps each test socket one-shot, so a server can always close. */
    const req = http.request({ host: '127.0.0.1', port, path, method, headers, agent: false }, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => {
        resolve({
          status: res.statusCode,
          headers: res.headers,
          body: Buffer.concat(chunks).toString('utf8'),
        });
      });
    });
    req.on('error', reject);
    if (body !== undefined) req.write(body);
    req.end();
  });
}

async function withProxy(configPatch, env, run, log) {
  const config = makeConfig(configPatch);
  const handler = createProxyHandler({ config, env, log });
  const proxy = await listen(handler);
  try {
    return await run(proxy.port, config);
  } finally {
    await proxy.close();
  }
}

test('GET is forwarded with status, headers, body and query intact', async () => {
  const seen = [];
  const upstream = await listen((req, res) => {
    seen.push({ method: req.method, url: req.url, headers: req.headers });
    res.writeHead(201, { 'x-upstream': 'yes', 'content-type': 'application/json' });
    res.end(JSON.stringify({ jobs: [] }));
  });
  try {
    const config = makeConfig({ controller: upstream.base });
    const proxy = await listen(createProxyHandler({ config, env: {} }));
    const response = await call(proxy.port, '/acp-api/v1/jobs?after_seq=7');
    await proxy.close();

    assert.equal(response.status, 201);
    assert.equal(response.headers['x-upstream'], 'yes');
    assert.equal(response.body, '{"jobs":[]}');
    assert.equal(response.headers['cache-control'], 'no-cache');
    assert.equal(seen.length, 1);
    assert.equal(seen[0].method, 'GET');
    assert.equal(seen[0].url, '/api/v1/jobs?after_seq=7');
  } finally {
    await upstream.close();
  }
});

test('a POST body is streamed to the controller, not buffered away', async () => {
  let received = 0;
  let payload = '';
  const upstream = await listen((req, res) => {
    req.on('data', (chunk) => {
      received += 1;
      payload += chunk.toString('utf8');
    });
    req.on('end', () => {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end('{"ok":true}');
    });
  });
  try {
    const config = makeConfig({ controller: upstream.base });
    const proxy = await listen(createProxyHandler({ config, env: {} }));
    const body = JSON.stringify({ input: 'CCO', n: 1 });
    const response = await call(proxy.port, '/acp-api/v1/jobs', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'content-length': String(body.length) },
      body,
    });
    await proxy.close();

    assert.equal(response.status, 200);
    assert.equal(payload, body);
    assert.ok(received >= 1);
  } finally {
    await upstream.close();
  }
});

test('x-api-key comes from the host env and never from the browser', async () => {
  const keys = [];
  const upstream = await listen((req, res) => {
    keys.push(req.headers['x-api-key']);
    res.writeHead(200);
    res.end('ok');
  });
  try {
    const config = makeConfig({ controller: upstream.base, apiKeyEnv: 'ACP_TEST_KEY' });

    const withKey = await listen(createProxyHandler({ config, env: { ACP_TEST_KEY: 'host-secret' } }));
    await call(withKey.port, '/acp-api/v1/jobs');
    await withKey.close();

    const withoutKey = await listen(createProxyHandler({ config, env: {} }));
    await call(withoutKey.port, '/acp-api/v1/jobs', { headers: { 'x-api-key': 'browser-value' } });
    await withoutKey.close();

    assert.deepEqual(keys, ['host-secret', undefined]);
  } finally {
    await upstream.close();
  }
});

test('hop-by-hop, host and cookie headers are stripped', async () => {
  let headers;
  const upstream = await listen((req, res) => {
    headers = req.headers;
    res.writeHead(200);
    res.end('ok');
  });
  try {
    const config = makeConfig({ controller: upstream.base });
    const proxy = await listen(createProxyHandler({ config, env: {} }));
    await call(proxy.port, '/acp-api/v1/jobs', {
      headers: {
        cookie: 'session=abc',
        'proxy-authorization': 'Basic zzz',
        'x-keep-me': 'yes',
        connection: 'keep-alive',
      },
    });
    await proxy.close();

    assert.equal(headers.cookie, undefined);
    assert.equal(headers['proxy-authorization'], undefined);
    assert.equal(headers['x-keep-me'], 'yes');
    assert.notEqual(headers.host, '127.0.0.1');
    assert.match(headers.host, /^127\.0\.0\.1:\d+$/);
  } finally {
    await upstream.close();
  }
});

test('an event stream is forwarded unbuffered with no-transform caching', async () => {
  const writes = [];
  const upstream = await listen((req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'private' });
    writes.push(Date.now());
    res.write('data: first\n\n');
    setTimeout(() => {
      writes.push(Date.now());
      res.write('data: second\n\n');
      res.end();
    }, 30);
  });
  try {
    const config = makeConfig({ controller: upstream.base, requestTimeoutMs: 5000 });
    const proxy = await listen(createProxyHandler({ config, env: {} }));

    const arrivals = [];
    await new Promise((resolve, reject) => {
      const req = http.request(
        { host: '127.0.0.1', port: proxy.port, path: '/acp-api/v1/jobs/42/events', agent: false },
        (res) => {
          assert.equal(res.headers['cache-control'], 'no-cache, no-transform');
          assert.equal(res.headers['x-accel-buffering'], 'no');
          assert.match(res.headers['content-type'], /^text\/event-stream/);
          res.on('data', (chunk) => arrivals.push({ at: Date.now(), text: chunk.toString('utf8') }));
          res.on('end', resolve);
        },
      );
      req.on('error', reject);
      req.end();
    });
    await proxy.close();

    assert.ok(arrivals.length >= 2, 'each upstream write must reach the client separately');
    assert.match(arrivals[0].text, /first/);
    /* The first chunk must arrive before the controller writes the second one. */
    assert.ok(
      arrivals[0].at < writes[1],
      `first chunk arrived at ${arrivals[0].at}, second upstream write at ${writes[1]}`,
    );
    assert.equal(arrivals.map((a) => a.text).join(''), 'data: first\n\ndata: second\n\n');
  } finally {
    await upstream.close();
  }
});

test('an unreachable controller answers 503 upstream_unreachable', async () => {
  const dead = await listen((_req, res) => res.end('x'));
  const config = makeConfig({ controller: dead.base });
  await dead.close();

  const proxy = await listen(createProxyHandler({ config, env: {} }));
  const response = await call(proxy.port, '/acp-api/v1/jobs');
  await proxy.close();

  assert.equal(response.status, 503);
  assert.deepEqual(JSON.parse(response.body), {
    ok: false,
    code: 'upstream_unreachable',
    message: 'the controller is not reachable',
  });
});

test('a silent controller answers 504 upstream_timeout', async () => {
  const upstream = await listen(() => {
    /* never respond */
  });
  try {
    const config = makeConfig({ controller: upstream.base, requestTimeoutMs: 80 });
    const proxy = await listen(createProxyHandler({ config, env: {} }));
    const response = await call(proxy.port, '/acp-api/v1/jobs');
    await proxy.close();

    assert.equal(response.status, 504);
    assert.equal(JSON.parse(response.body).code, 'upstream_timeout');
  } finally {
    await upstream.close();
  }
});

test('an unsupported method is refused before any upstream socket', async () => {
  let reached = false;
  const upstream = await listen((_req, res) => {
    reached = true;
    res.end('x');
  });
  try {
    const config = makeConfig({ controller: upstream.base });
    const proxy = await listen(createProxyHandler({ config, env: {} }));
    const response = await call(proxy.port, '/acp-api/v1/jobs', { method: 'TRACE' });
    await proxy.close();

    assert.equal(response.status, 405);
    assert.equal(response.headers.allow, ALLOWED_METHODS.join(', '));
    assert.equal(JSON.parse(response.body).code, 'method_not_allowed');
    assert.equal(reached, false);
  } finally {
    await upstream.close();
  }
});

test('a non-whitelisted prefix answers 403 without touching the controller', async () => {
  const upstream = await listen((_req, res) => res.end('x'));
  try {
    const config = makeConfig({ controller: upstream.base });
    const proxy = await listen(createProxyHandler({ config, env: {} }));
    const response = await call(proxy.port, '/acp-api/admin/users');
    await proxy.close();

    assert.equal(response.status, 403);
    assert.equal(JSON.parse(response.body).code, 'forbidden_prefix');
  } finally {
    await upstream.close();
  }
});

test('log records metadata only — never a body or the key', async () => {
  const entries = [];
  const upstream = await listen((req, res) => {
    res.writeHead(200);
    res.end('ok');
  });
  try {
    const config = makeConfig({ controller: upstream.base, apiKeyEnv: 'ACP_TEST_KEY' });
    const proxy = await listen(
      createProxyHandler({ config, env: { ACP_TEST_KEY: 'host-secret' }, log: (meta) => entries.push(meta) }),
    );
    await call(proxy.port, '/acp-api/v1/jobs?token=leaky');
    await proxy.close();

    assert.equal(entries.length, 1);
    assert.deepEqual(Object.keys(entries[0]).sort(), ['method', 'ms', 'path', 'status']);
    assert.equal(entries[0].path, '/acp-api/v1/jobs');
    const serialised = JSON.stringify(entries);
    assert.equal(serialised.includes('host-secret'), false);
    assert.equal(serialised.includes('leaky'), false);
  } finally {
    await upstream.close();
  }
});
