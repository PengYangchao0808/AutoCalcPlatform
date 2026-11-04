/**
 * Static asset serving for `<uiBase>/*`.
 *
 * The committed `assets/` tree is the only thing served: every request is
 * constrained to a manifest entry, so an unknown file can never be reached even
 * if it exists on disk, and a path can never escape `assetsRoot`.
 *
 * Manifest semantics (deliberately distinct):
 *   - `manifest === undefined` -> membership check skipped (used by unit tests
 *     that exercise the pure path maths without a built tree);
 *   - `manifest === null`      -> the tree was never built -> 503 assets_unbuilt;
 *   - otherwise the path must be listed in `manifest.entries`.
 */

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

export const ASSET_SCHEMA = 'acp2.dsh.assets.v1';

/** Extension -> media type. Mirrors the values build.mjs records in the manifest. */
export const MEDIA_TYPES = Object.freeze({
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.html': 'text/html; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
});

const PORTABLE_PATH_RE = /^[A-Za-z0-9._/-]+$/;

export function mediaTypeFor(filePath) {
  return MEDIA_TYPES[path.extname(filePath).toLowerCase()] ?? 'application/octet-stream';
}

function fail(status, code, message) {
  return { ok: false, status, code, message };
}

/**
 * Resolve and validate an asset path. PURE: no fs access at all.
 * @param {string} assetsRoot absolute assets directory
 * @param {string} rawPath request path, either plugin-relative or `<uiBase>/`-prefixed
 * @param {{entries:Array<{path:string}>}|null|undefined} manifest
 * @returns {{ok:true, absolutePath:string, relativePath:string, entry:object|null}
 *          |{ok:false, status:number, code:string, message:string}}
 */
export function resolveAssetPath(assetsRoot, rawPath, manifest) {
  if (typeof rawPath !== 'string' || rawPath.length === 0) {
    return fail(400, 'bad_asset_path', 'missing asset path');
  }
  /* The caller may hand us a full request target; the query is never part of a path. */
  const at = rawPath.indexOf('?');
  const clean = at === -1 ? rawPath : rawPath.slice(0, at);

  if (!PORTABLE_PATH_RE.test(clean)) {
    return fail(400, 'bad_asset_path', 'asset path contains characters that are not allowed');
  }
  if (clean.includes('..')) {
    return fail(400, 'bad_asset_path', 'path traversal is not allowed');
  }

  const relativePath = clean.replace(/^\/+/, '');
  if (relativePath === '') return fail(400, 'bad_asset_path', 'missing asset path');
  if (relativePath.split('/').some((segment) => segment === '' || segment === '.')) {
    return fail(400, 'bad_asset_path', 'asset path must be a normalised relative path');
  }

  const root = path.resolve(assetsRoot);
  const absolutePath = path.resolve(root, relativePath);
  /* Second gate: the resolved absolute path must sit inside the root. */
  if (absolutePath !== root && !absolutePath.startsWith(root + path.sep)) {
    return fail(400, 'bad_asset_path', 'asset path escapes the asset root');
  }

  if (manifest === undefined) {
    return { ok: true, absolutePath, relativePath, entry: null };
  }
  if (manifest === null) {
    return fail(503, 'assets_unbuilt', 'the asset bundle has not been built');
  }
  const entry = (manifest.entries ?? []).find((candidate) => candidate.path === relativePath);
  if (!entry) {
    return fail(404, 'asset_not_found', 'asset is not listed in the manifest');
  }
  return { ok: true, absolutePath, relativePath, entry };
}

/**
 * Read and validate `assets/manifest.json`.
 * @param {string} assetsRoot
 * @returns {object|null} the parsed manifest, or null when absent/invalid.
 */
export function loadManifest(assetsRoot) {
  const manifestPath = path.join(assetsRoot, 'manifest.json');
  let text;
  try {
    text = fs.readFileSync(manifestPath, 'utf8');
  } catch {
    return null;
  }
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
  if (parsed.schema !== ASSET_SCHEMA) return null;
  if (!Array.isArray(parsed.entries)) return null;
  for (const entry of parsed.entries) {
    if (!entry || typeof entry !== 'object') return null;
    if (typeof entry.path !== 'string' || entry.path === '') return null;
    if (typeof entry.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(entry.sha256)) return null;
    if (!Number.isInteger(entry.size_bytes) || entry.size_bytes < 0) return null;
    if (typeof entry.media_type !== 'string' || entry.media_type === '') return null;
  }
  return parsed;
}

function writeJson(res, status, code, message) {
  if (res.headersSent || res.writableEnded) {
    res.destroy();
    return;
  }
  const body = Buffer.from(JSON.stringify({ ok: false, code, message }), 'utf8');
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': String(body.length),
    'cache-control': 'no-cache',
  });
  res.end(body);
}

/** ETag is the first 16 hex of the file's sha256; the manifest already carries it. */
function etagFor(resolved, absolutePath) {
  let hex = resolved.entry?.sha256;
  if (typeof hex !== 'string') {
    hex = crypto.createHash('sha256').update(fs.readFileSync(absolutePath)).digest('hex');
  }
  return `"${hex.slice(0, 16)}"`;
}

/**
 * @param {{config:{uiBase:string}, assetsRoot:string, manifest?:object|null, log?:Function}} options
 * @returns {(req:import('node:http').IncomingMessage, res:import('node:http').ServerResponse)=>void}
 */
export function createAssetHandler(options) {
  const { assetsRoot, manifest, log } = options;
  const uiBase = options.config.uiBase;
  return function assetHandle(req, res) {
    const method = String(req.method ?? '').toUpperCase();
    const startedAt = Date.now();
    const emitLog = (status) => {
      if (typeof log === 'function') log({ method, path: req.url ?? '/', status, ms: Date.now() - startedAt });
    };

    if (method !== 'GET' && method !== 'HEAD') {
      writeJson(res, 405, 'method_not_allowed', 'assets are read-only');
      emitLog(405);
      return;
    }

    /* The route is a prefix route on `uiBase`, so the request path still carries
     * it: `<uiBase>/core.bundle.js` -> plugin-relative `/core.bundle.js`. */
    const target = req.url ?? '/';
    const at = target.indexOf('?');
    const pathOnly = at === -1 ? target : target.slice(0, at);
    if (!pathOnly.startsWith(`${uiBase}/`)) {
      writeJson(res, 404, 'asset_not_found', 'not an asset path');
      emitLog(404);
      return;
    }
    const pluginPath = pathOnly.slice(uiBase.length);

    const resolved = resolveAssetPath(assetsRoot, pluginPath, manifest);
    if (!resolved.ok) {
      writeJson(res, resolved.status, resolved.code, resolved.message);
      emitLog(resolved.status);
      return;
    }

    fs.stat(resolved.absolutePath, (err, stats) => {
      if (err || !stats.isFile()) {
        writeJson(res, 404, 'asset_missing', 'asset is listed but missing on disk');
        emitLog(404);
        return;
      }

      let etag;
      try {
        etag = etagFor(resolved, resolved.absolutePath);
      } catch {
        writeJson(res, 404, 'asset_missing', 'asset is listed but missing on disk');
        emitLog(404);
        return;
      }

      const headers = {
        'content-type': resolved.entry?.media_type ?? mediaTypeFor(resolved.absolutePath),
        'cache-control': 'no-cache',
        etag,
        'content-length': String(stats.size),
      };

      const inm = req.headers['if-none-match'];
      if (typeof inm === 'string' && inm.split(',').some((part) => part.trim() === etag)) {
        res.writeHead(304, { etag, 'cache-control': 'no-cache' });
        res.end();
        emitLog(304);
        return;
      }

      res.writeHead(200, headers);
      emitLog(200);
      if (method === 'HEAD') {
        res.end();
        return;
      }
      const stream = fs.createReadStream(resolved.absolutePath);
      stream.on('error', () => {
        if (!res.writableEnded) res.destroy();
      });
      stream.pipe(res);
    });
  };
}
