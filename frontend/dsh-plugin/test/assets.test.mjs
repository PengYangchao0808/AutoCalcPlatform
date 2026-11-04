import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import http from 'node:http';

import {
  ASSET_SCHEMA,
  createAssetHandler,
  loadManifest,
  resolveAssetPath,
} from '../src/static-assets.js';

const UI_BASE = '/acp';

/** Build a throwaway assets tree + manifest. */
function makeTree() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'acp-dsh-assets-'));
  fs.mkdirSync(path.join(root, 'vendor'), { recursive: true });
  fs.writeFileSync(path.join(root, 'core.bundle.js'), 'window.core = 1;\n');
  fs.writeFileSync(path.join(root, 'panels.css'), 'body{}\n');
  fs.writeFileSync(path.join(root, 'vendor', '3Dmol-2.5.5.min.js'), '/* vendor */\n');
  /* Present on disk but NOT in the manifest: must never be served. */
  fs.writeFileSync(path.join(root, 'secret.js'), 'console.log("nope");\n');

  const entries = ['core.bundle.js', 'panels.css', 'vendor/3Dmol-2.5.5.min.js'].map((rel) => {
    const bytes = fs.readFileSync(path.join(root, rel));
    return {
      path: rel,
      sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
      size_bytes: bytes.length,
      media_type: rel.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/javascript; charset=utf-8',
    };
  });
  const manifest = { schema: ASSET_SCHEMA, generated_by: 'test', entries, digest: 'x' };
  fs.writeFileSync(path.join(root, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);

  return { root, manifest, cleanup: () => fs.rmSync(root, { recursive: true, force: true }) };
}

function call(port, urlPath, { method = 'GET', headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: '127.0.0.1', port, path: urlPath, method, headers, agent: false },
      (res) => {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => resolve({
          status: res.statusCode,
          headers: res.headers,
          body: Buffer.concat(chunks),
        }));
      },
    );
    req.on('error', reject);
    req.end();
  });
}

async function withServer(handler, run) {
  const server = http.createServer(handler);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  try {
    return await run(port);
  } finally {
    await new Promise((resolve) => {
      server.closeAllConnections();
      server.close(resolve);
    });
  }
}

// ── pure path resolution ──────────────────────────────────────────────────

test('resolveAssetPath accepts a manifest-listed file', () => {
  const tree = makeTree();
  try {
    const result = resolveAssetPath(tree.root, '/core.bundle.js', tree.manifest);
    assert.equal(result.ok, true);
    assert.equal(result.relativePath, 'core.bundle.js');
    assert.equal(result.absolutePath, path.join(tree.root, 'core.bundle.js'));
    assert.equal(result.entry.path, 'core.bundle.js');
  } finally {
    tree.cleanup();
  }
});

test('resolveAssetPath rejects traversal and odd characters', () => {
  const tree = makeTree();
  try {
    for (const bad of [
      '/../etc/passwd',
      '/vendor/../../etc/passwd',
      '..',
      '/a b.js',
      '/core.bundle.js\u0000',
      '/sub%2f..%2fcore.bundle.js',
      '',
    ]) {
      const result = resolveAssetPath(tree.root, bad, tree.manifest);
      assert.equal(result.ok, false, `expected rejection for ${JSON.stringify(bad)}`);
      assert.equal(result.status, 400);
      assert.equal(result.code, 'bad_asset_path');
    }
  } finally {
    tree.cleanup();
  }
});

test('resolveAssetPath rejects a file that is not in the manifest', () => {
  const tree = makeTree();
  try {
    const result = resolveAssetPath(tree.root, '/secret.js', tree.manifest);
    assert.equal(result.ok, false);
    assert.equal(result.status, 404);
    assert.equal(result.code, 'asset_not_found');
  } finally {
    tree.cleanup();
  }
});

test('resolveAssetPath reports assets_unbuilt for a null manifest and skips the check for undefined', () => {
  const tree = makeTree();
  try {
    const unbuilt = resolveAssetPath(tree.root, '/secret.js', null);
    assert.equal(unbuilt.status, 503);
    assert.equal(unbuilt.code, 'assets_unbuilt');

    const unchecked = resolveAssetPath(tree.root, '/secret.js', undefined);
    assert.equal(unchecked.ok, true);
    assert.equal(unchecked.entry, null);
  } finally {
    tree.cleanup();
  }
});

test('the query string is never part of an asset path', () => {
  const tree = makeTree();
  try {
    const result = resolveAssetPath(tree.root, '/core.bundle.js?v=2', tree.manifest);
    assert.equal(result.ok, true);
    assert.equal(result.relativePath, 'core.bundle.js');
  } finally {
    tree.cleanup();
  }
});

// ── manifest loading ──────────────────────────────────────────────────────

test('loadManifest returns null when absent and validates the schema', () => {
  const tree = makeTree();
  try {
    assert.equal(loadManifest(tree.root).schema, ASSET_SCHEMA);

    const manifestPath = path.join(tree.root, 'manifest.json');
    fs.writeFileSync(manifestPath, JSON.stringify({ schema: 'wrong', entries: [] }));
    assert.equal(loadManifest(tree.root), null);

    fs.writeFileSync(manifestPath, JSON.stringify({ schema: ASSET_SCHEMA, entries: [{ path: 'a' }] }));
    assert.equal(loadManifest(tree.root), null);

    fs.writeFileSync(manifestPath, 'not json');
    assert.equal(loadManifest(tree.root), null);

    fs.rmSync(manifestPath);
    assert.equal(loadManifest(tree.root), null);
  } finally {
    tree.cleanup();
  }
});

// ── HTTP behaviour ────────────────────────────────────────────────────────

test('GET streams a file with content-type, etag and no-cache', async () => {
  const tree = makeTree();
  try {
    const handler = createAssetHandler({
      config: { uiBase: UI_BASE },
      assetsRoot: tree.root,
      manifest: tree.manifest,
    });
    await withServer(handler, async (port) => {
      const response = await call(port, '/acp/core.bundle.js');
      assert.equal(response.status, 200);
      assert.equal(response.body.toString('utf8'), 'window.core = 1;\n');
      assert.equal(response.headers['content-type'], 'text/javascript; charset=utf-8');
      assert.equal(response.headers['cache-control'], 'no-cache');
      assert.equal(response.headers['content-length'], '17');
      assert.equal(response.headers.etag, `"${tree.manifest.entries[0].sha256.slice(0, 16)}"`);
    });
  } finally {
    tree.cleanup();
  }
});

test('HEAD answers headers and no body', async () => {
  const tree = makeTree();
  try {
    const handler = createAssetHandler({
      config: { uiBase: UI_BASE },
      assetsRoot: tree.root,
      manifest: tree.manifest,
    });
    await withServer(handler, async (port) => {
      const response = await call(port, '/acp/panels.css', { method: 'HEAD' });
      assert.equal(response.status, 200);
      assert.equal(response.body.length, 0);
      assert.equal(response.headers['content-length'], '7');
    });
  } finally {
    tree.cleanup();
  }
});

test('ETag round-trips to 304', async () => {
  const tree = makeTree();
  try {
    const handler = createAssetHandler({
      config: { uiBase: UI_BASE },
      assetsRoot: tree.root,
      manifest: tree.manifest,
    });
    await withServer(handler, async (port) => {
      const first = await call(port, '/acp/panels.css');
      const second = await call(port, '/acp/panels.css', {
        headers: { 'if-none-match': first.headers.etag },
      });
      assert.equal(second.status, 304);
      assert.equal(second.body.length, 0);
      assert.equal(second.headers.etag, first.headers.etag);
    });
  } finally {
    tree.cleanup();
  }
});

test('a manifest entry whose file is gone answers 404 asset_missing', async () => {
  const tree = makeTree();
  try {
    fs.rmSync(path.join(tree.root, 'panels.css'));
    const handler = createAssetHandler({
      config: { uiBase: UI_BASE },
      assetsRoot: tree.root,
      manifest: tree.manifest,
    });
    await withServer(handler, async (port) => {
      const response = await call(port, '/acp/panels.css');
      assert.equal(response.status, 404);
      assert.equal(JSON.parse(response.body.toString('utf8')).code, 'asset_missing');
    });
  } finally {
    tree.cleanup();
  }
});

test('a null manifest answers 503 assets_unbuilt', async () => {
  const tree = makeTree();
  try {
    const handler = createAssetHandler({ config: { uiBase: UI_BASE }, assetsRoot: tree.root, manifest: null });
    await withServer(handler, async (port) => {
      const response = await call(port, '/acp/core.bundle.js');
      assert.equal(response.status, 503);
      assert.equal(JSON.parse(response.body.toString('utf8')).code, 'assets_unbuilt');
    });
  } finally {
    tree.cleanup();
  }
});

test('writes and traversal are refused', async () => {
  const tree = makeTree();
  try {
    const handler = createAssetHandler({
      config: { uiBase: UI_BASE },
      assetsRoot: tree.root,
      manifest: tree.manifest,
    });
    await withServer(handler, async (port) => {
      const post = await call(port, '/acp/core.bundle.js', { method: 'POST' });
      assert.equal(post.status, 405);

      const traversal = await call(port, '/acp/../manifest.json');
      assert.equal(traversal.status, 400);
      assert.equal(JSON.parse(traversal.body.toString('utf8')).code, 'bad_asset_path');

      const outside = await call(port, '/other/core.bundle.js');
      assert.equal(outside.status, 404);
    });
  } finally {
    tree.cleanup();
  }
});
