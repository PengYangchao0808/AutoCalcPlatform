/**
 * DSH host half for @acp/dsh-panels.
 *
 * `apply(ctx, config)` is the single entry point. It:
 *   1. normalises the config — on failure it renders ONE diagnostic row and
 *      registers no routes (a broken config must be visible, not silent);
 *   2. registers, on the DSH web server:
 *        GET  <apiPrefix>/health      liveness probe of the controller
 *        ANY  <apiPrefix>/ui/focus    focus-bridge store (GET/POST)
 *        *    <apiPrefix>/*           same-origin reverse proxy to the controller
 *        GET  <uiBase>/*              committed asset tree
 *   3. pushes the index-inject rows that load the client half.
 *
 * Everything is registered through `ctx.effect(...)` / `ctx.inject(...)` so the
 * whole surface is disposable when the plugin row is removed.
 */

import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { normalizeConfig, resolveApiKey, upstreamUrl } from './config.js';
import { createProxyHandler } from './proxy.js';
import { createAssetHandler, loadManifest } from './static-assets.js';
import { createFocusStore } from './focus-store.js';
import { assertNoSecret, buildInjectionRows } from './injection.js';

export const name = '@acp/dsh-panels';

/** The web server must exist before any route can be registered. */
export const inject = ['webServer'];

// Config is deliberately not exported: Cordis expects a Standard Schema
// (~standard.validate), not a field-description object. normalizeConfig below
// keeps invalid settings visible as a local diagnostic rather than killing boot.

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ASSETS_ROOT = path.resolve(HERE, '..', 'assets');
const HEALTH_PROBE_TIMEOUT_MS = 2500;
const UNREACHABLE_CODES = new Set(['ECONNREFUSED', 'ENOTFOUND', 'EHOSTUNREACH']);

function sendJson(res, status, payload) {
  if (res.headersSent || res.writableEnded) {
    res.destroy();
    return;
  }
  const body = Buffer.from(JSON.stringify(payload), 'utf8');
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': String(body.length),
    'cache-control': 'no-cache',
  });
  res.end(body);
}

function sendError(res, status, code, message, extraHeaders) {
  const body = Buffer.from(JSON.stringify({ ok: false, code, message }), 'utf8');
  if (res.headersSent || res.writableEnded) {
    res.destroy();
    return;
  }
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': String(body.length),
    'cache-control': 'no-cache',
    ...extraHeaders,
  });
  res.end(body);
}

/** `GET <apiPrefix>/health` — probe the controller without ever exposing the key. */
function createHealthHandler(config, apiKey, log) {
  return function health(req, res) {
    const method = String(req.method ?? '').toUpperCase();
    const startedAt = Date.now();
    const emit = (status) => {
      if (typeof log === 'function') log({ method, path: req.url ?? '/', status, ms: Date.now() - startedAt });
    };
    if (method !== 'GET' && method !== 'HEAD') {
      sendError(res, 405, 'method_not_allowed', 'health is read-only', {
        allow: 'GET, HEAD',
      });
      emit(405);
      return;
    }

    let done = false;
    const finish = (status, upstream) => {
      if (done) return;
      done = true;
      sendJson(res, status, { ok: true, controller: config.controller, upstream });
      emit(status);
    };

    const headers = apiKey === undefined ? {} : { 'x-api-key': apiKey };
    let timedOut = false;
    const probe = http.get(upstreamUrl(config, '/backends'), { headers, agent: false }, (probeRes) => {
      probeRes.resume(); // drain, never buffer a probe body
      finish(200, { status: probeRes.statusCode ?? 0 });
    });
    probe.setTimeout(HEALTH_PROBE_TIMEOUT_MS, () => {
      timedOut = true;
      probe.destroy();
    });
    probe.on('error', (err) => {
      if (timedOut) {
        finish(200, { code: 'upstream_timeout' });
        return;
      }
      finish(200, { code: UNREACHABLE_CODES.has(err?.code) ? 'upstream_unreachable' : 'upstream_error' });
    });
  };
}

/** `<apiPrefix>/ui/focus` — the browser -> host focus bridge. */
function createFocusHandler(config, store, log) {
  return function focus(req, res) {
    const method = String(req.method ?? '').toUpperCase();
    const startedAt = Date.now();
    const emit = (status) => {
      if (typeof log === 'function') log({ method, path: req.url ?? '/', status, ms: Date.now() - startedAt });
    };

    if (method === 'GET' || method === 'HEAD') {
      /* URL is used for QUERY parsing only; never to build an upstream target. */
      let session = null;
      try {
        session = new URL(req.url ?? '/', 'http://localhost').searchParams.get('session');
      } catch {
        session = null;
      }
      if (!session) {
        sendJson(res, 200, { ok: true, sessions: store.list() });
        emit(200);
        return;
      }
      const record = store.get(session);
      if (!record) {
        sendError(res, 404, 'not_found', 'no focus snapshot for this session');
        emit(404);
        return;
      }
      sendJson(res, 200, { ok: true, focus: record });
      emit(200);
      return;
    }

    if (method !== 'POST') {
      sendError(res, 405, 'method_not_allowed', 'focus accepts GET or POST', { allow: 'GET, HEAD, POST' });
      emit(405);
      return;
    }

    const chunks = [];
    let size = 0;
    let overflow = false;
    req.on('data', (chunk) => {
      if (overflow) return;
      size += chunk.length;
      if (size > config.maxFocusBodyBytes) {
        overflow = true;
        sendError(res, 413, 'body_too_large', 'focus snapshot exceeds the configured body limit');
        emit(413);
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (overflow) return;
      let body;
      try {
        body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      } catch {
        sendError(res, 400, 'bad_json', 'focus body must be valid JSON');
        emit(400);
        return;
      }
      const result = body?.operation === 'acquire' ? store.acquire(body) : store.put(body);
      if (result.ok) {
        sendJson(res, 200, result);
        emit(200);
        return;
      }
      sendError(res, result.status, result.code, result.message);
      emit(result.status);
    });
    req.on('error', () => {
      if (!res.writableEnded) res.destroy();
    });
  };
}

/**
 * @param {{logger?:object, env?:Record<string,string|undefined>, effect?:(fn:Function,label?:string)=>unknown,
 *          inject?:(deps:string[],cb:Function)=>unknown, on?:(event:string,fn:Function)=>unknown,
 *          webServer?:{register:(route:object)=>()=>void}}} ctx
 * @param {unknown} config raw plugin config
 */
export function apply(ctx, config) {
  const logger = ctx?.logger ?? console;
  // Environment variables belong to the Node host, not a Cordis service.
  // Accessing undeclared ctx.env throws even through optional chaining.
  const env = process.env;

  const normalised = normalizeConfig(config);
  if (!normalised.ok) {
    const { code, field, message } = normalised;
    /* Report the failure, never the value: a config error must not echo a key. */
    if (typeof logger.error === 'function') {
      logger.error(`[acp-panels] invalid config (${field}): ${message}`);
    }
    ctx.inject(['webServer'], () => {
      const row = {
        kind: 'global',
        name: '__ACP_PANELS_CONFIG__',
        value: { ok: false, code, field },
      };
      ctx.effect(
        () => ctx.on('webserver/index-inject', (table) => {
          table.push(row);
        }),
        'acp-panels: config-error-row',
      );
    });
    return;
  }

  const cfg = normalised.config;
  const apiKey = resolveApiKey(cfg, env);
  const rows = buildInjectionRows(cfg);
  assertNoSecret(rows, apiKey);

  /* Read once at apply time: a missing manifest is a first-class state (503). */
  const manifest = loadManifest(ASSETS_ROOT);
  if (manifest === null && typeof logger.warn === 'function') {
    logger.warn('[acp-panels] assets/manifest.json is missing: <uiBase>/* will answer 503 assets_unbuilt');
  }

  const log = (meta) => {
    if (typeof logger.debug === 'function') logger.debug(meta);
  };

  const focusStore = createFocusStore({ maxSessions: cfg.maxFocusSessions });
  const healthHandler = createHealthHandler(cfg, apiKey, log);
  const focusHandler = createFocusHandler(cfg, focusStore, log);
  const proxyHandler = createProxyHandler({ config: cfg, env, log });
  const assetHandler = createAssetHandler({
    config: cfg,
    assetsRoot: ASSETS_ROOT,
    manifest,
    log,
  });

  ctx.inject(['webServer'], () => {
    /* `apiPrefix` and `uiBase` are distinct by design; equal values would make
     * two identical prefix routes, which the web server refuses. */
    const clash = cfg.uiBase === cfg.apiPrefix;
    if (clash && typeof logger.error === 'function') {
      logger.error('[acp-panels] uiBase equals apiPrefix: asset route is not registered');
    }

    ctx.effect(
      () => ctx.webServer.register({ kind: 'exact', path: `${cfg.apiPrefix}/health`, handler: healthHandler }),
      'acp-panels: route-health',
    );
    ctx.effect(
      () => ctx.webServer.register({ kind: 'exact', path: `${cfg.apiPrefix}/ui/focus`, handler: focusHandler }),
      'acp-panels: route-focus',
    );
    /* Prefix routes match `path` and `path + "/"`-prefixed paths only, so the
     * mount path is registered WITHOUT a trailing slash. */
    ctx.effect(
      () => ctx.webServer.register({ kind: 'prefix', path: cfg.apiPrefix, handler: proxyHandler }),
      'acp-panels: route-api',
    );
    if (!clash) {
      ctx.effect(
        () => ctx.webServer.register({ kind: 'prefix', path: cfg.uiBase, handler: assetHandler }),
        'acp-panels: route-assets',
      );
    }

    ctx.effect(
      () => ctx.on('webserver/index-inject', (table) => {
        for (const row of rows) table.push(row);
      }),
      'acp-panels: index-inject',
    );
  });
}
