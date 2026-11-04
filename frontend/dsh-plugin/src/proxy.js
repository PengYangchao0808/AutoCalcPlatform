/**
 * Same-origin reverse proxy for `<apiPrefix>/*` -> `<controller>/api/*`.
 *
 * DSH has no built-in proxy, so this module owns the whole `node:http`
 * exchange: it validates the request path (the security core), streams the
 * request body, forwards headers, and moves the response back — including
 * server-sent events, which must not be buffered.
 *
 * Invariants:
 *  - request/response bodies are never buffered and never logged;
 *  - the resolved API key is only ever written into an outgoing header;
 *  - every failure answers a JSON envelope `{ok:false, code, message}` built
 *    here, never an upstream body and never a credential.
 */

import http from 'node:http';
import { resolveApiKey, upstreamUrl } from './config.js';

export const ALLOWED_METHODS = Object.freeze([
  'GET',
  'HEAD',
  'POST',
  'PUT',
  'PATCH',
  'DELETE',
  'OPTIONS',
]);

/** Hop-by-hop and host-scoped request headers that must not cross the hop. */
const DROP_REQUEST_HEADERS = new Set([
  'host',
  'connection',
  'keep-alive',
  'transfer-encoding',
  'upgrade',
  'proxy-authorization',
  'proxy-connection',
  'te',
  'trailer',
  'cookie',
  /* Always re-derived from the host env, never forwarded from the browser:
   * accepting a client value would let two differing keys race on one request. */
  'x-api-key',
]);

/** Hop-by-hop response headers that must not cross the hop back. */
const DROP_RESPONSE_HEADERS = new Set([
  'connection',
  'keep-alive',
  'transfer-encoding',
  'upgrade',
  'proxy-authenticate',
  'proxy-authorization',
  'proxy-connection',
  'te',
  'trailer',
]);

const UNREACHABLE_CODES = new Set(['ECONNREFUSED', 'ENOTFOUND', 'EHOSTUNREACH']);

/* Control characters, plus the traversal forms we refuse before any socket. */
const CONTROL_CHAR_RE = /[\u0000-\u001f\u007f]/;

function jsonBody(code, message) {
  return JSON.stringify({ ok: false, code, message });
}

function writeJson(res, status, code, message, extraHeaders) {
  if (res.headersSent || res.writableEnded) {
    res.destroy();
    return;
  }
  const body = Buffer.from(jsonBody(code, message), 'utf8');
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': String(body.length),
    'cache-control': 'no-cache',
    ...extraHeaders,
  });
  res.end(body);
}

/**
 * Pure path validation — the security core. No I/O, no clock, no config copy.
 * @param {{controller:string, apiPrefix:string, allowedApiPrefixes:readonly string[]}} config
 * @param {string} rawUrl raw request url, query included, e.g. `/acp-api/v1/jobs?x=1`
 * @returns {{ok:true, upstream:string, prefix:string, path:string, query:string}
 *          |{ok:false, status:number, code:string, message:string}}
 */
export function resolveUpstream(config, rawUrl) {
  if (typeof rawUrl !== 'string' || rawUrl.length === 0) {
    return { ok: false, status: 400, code: 'bad_request', message: 'missing request url' };
  }

  /* Split at the FIRST '?'; the query is kept verbatim (no re-encoding). */
  const mark = rawUrl.indexOf('?');
  const path = mark === -1 ? rawUrl : rawUrl.slice(0, mark);
  const query = mark === -1 ? '' : rawUrl.slice(mark);

  if (!path.startsWith('/')) {
    return { ok: false, status: 400, code: 'bad_request', message: 'request path must be absolute' };
  }
  if (path.includes('..')) {
    return { ok: false, status: 400, code: 'bad_request', message: 'path traversal is not allowed' };
  }
  if (path.includes('\\')) {
    return { ok: false, status: 400, code: 'bad_request', message: 'backslash is not allowed in a path' };
  }
  if (path.includes('//')) {
    return { ok: false, status: 400, code: 'bad_request', message: 'duplicate slashes are not allowed' };
  }
  if (CONTROL_CHAR_RE.test(path)) {
    return { ok: false, status: 400, code: 'bad_request', message: 'control characters are not allowed' };
  }

  const prefixPath = `${config.apiPrefix}/`;
  if (!path.startsWith(prefixPath)) {
    return { ok: false, status: 400, code: 'bad_request', message: 'request path is outside the API prefix' };
  }

  const rest = path.slice(config.apiPrefix.length); // always starts with '/'
  const firstSegment = rest.slice(1).split('/')[0];
  if (!config.allowedApiPrefixes.includes(firstSegment)) {
    return {
      ok: false,
      status: 403,
      code: 'forbidden_prefix',
      message: 'API prefix is not on the allowlist',
    };
  }

  const upstream = upstreamUrl(config, rest) + query;
  /* Defence in depth: after concatenation the URL must still address the
   * controller's /api/ tree. This is the assertion that would catch a future
   * regression in the concatenation helper. */
  const expected = `${config.controller}/api/`;
  if (!upstream.startsWith(expected)) {
    return { ok: false, status: 400, code: 'bad_request', message: 'resolved upstream escaped the API root' };
  }

  return { ok: true, upstream, prefix: firstSegment, path, query };
}

/** True when this exchange is server-sent events and must not be buffered. */
function isEventStream(path, upstreamContentType) {
  if (path.endsWith('/events')) return true;
  return typeof upstreamContentType === 'string'
    && upstreamContentType.toLowerCase().startsWith('text/event-stream');
}

function forwardRequestHeaders(req, apiKey) {
  const headers = {};
  for (const [name, value] of Object.entries(req.headers)) {
    const key = name.toLowerCase();
    if (DROP_REQUEST_HEADERS.has(key)) continue;
    if (value === undefined) continue;
    headers[key] = value;
  }
  if (apiKey !== undefined) headers['x-api-key'] = apiKey;
  return headers;
}

/**
 * Core request handler. Exported so a test can drive it with an explicit state
 * bag; production code goes through {@link createProxyHandler}.
 * @param {import('node:http').IncomingMessage} req
 * @param {import('node:http').ServerResponse} res
 * @param {{config:object, env?:object, log?:Function}} state
 */
export function handle(req, res, state) {
  const { config } = state;
  const startedAt = Date.now();
  const method = String(req.method ?? '').toUpperCase();

  let status = 0;
  const emitLog = (finalStatus) => {
    if (status !== 0) return;
    status = finalStatus;
    if (typeof state.log === 'function') {
      /* Metadata only: never the query, never a body, never a header value. */
      let path = '/';
      try {
        path = resolveUpstream(config, req.url ?? '').path ?? '/';
      } catch {
        path = '/';
      }
      state.log({ method, path, status, ms: Date.now() - startedAt });
    }
  };
  res.on('close', () => emitLog(status === 0 ? (res.statusCode || 0) : status));

  if (!ALLOWED_METHODS.includes(method)) {
    writeJson(res, 405, 'method_not_allowed', 'method is not proxied', {
      allow: ALLOWED_METHODS.join(', '),
    });
    emitLog(405);
    return;
  }

  const target = resolveUpstream(config, req.url ?? '');
  if (!target.ok) {
    writeJson(res, target.status, target.code, target.message);
    emitLog(target.status);
    return;
  }

  const apiKey = resolveApiKey(config, state.env);
  const headers = forwardRequestHeaders(req, apiKey);

  let settled = false;
  let timedOut = false;
  let connectTimer = null;
  let idleTimer = null;

  const clearTimers = () => {
    if (connectTimer !== null) clearTimeout(connectTimer);
    if (idleTimer !== null) clearTimeout(idleTimer);
    connectTimer = null;
    idleTimer = null;
  };

  /* `agent: false` = a one-off agent per request: no pooled socket can be
   * reused after it was torn down by a timeout or by the controller, and a
   * finished exchange never keeps the process (or a shutdown) alive. */
  const upstreamReq = http.request(target.upstream, { method, headers, agent: false });

  const onTimeout = () => {
    timedOut = true;
    upstreamReq.destroy();
  };

  /* Phase 1: connect (applies to SSE too). */
  connectTimer = setTimeout(onTimeout, config.requestTimeoutMs);
  /* Phase 2 (non-SSE only, attached once streaming starts): idle timeout. */
  const armIdle = () => {
    if (idleTimer !== null) clearTimeout(idleTimer);
    idleTimer = setTimeout(onTimeout, config.requestTimeoutMs);
  };
  const disarmIdle = () => {
    if (idleTimer !== null) clearTimeout(idleTimer);
    idleTimer = null;
  };

  const failUpstream = (err) => {
    if (settled) return;
    settled = true;
    clearTimers();
    /* `res.destroyed` is the authoritative "client is gone" signal. `req`
     * cannot be used: piping into an errored upstream request auto-destroys the
     * incoming message, which would swallow every legitimate error response. */
    if (res.destroyed || res.writableEnded) return;
    if (timedOut) {
      writeJson(res, 504, 'upstream_timeout', 'the controller did not answer in time');
      emitLog(504);
      return;
    }
    const reason = err && typeof err.code === 'string' ? err.code : '';
    if (UNREACHABLE_CODES.has(reason)) {
      writeJson(res, 503, 'upstream_unreachable', 'the controller is not reachable');
      emitLog(503);
      return;
    }
    writeJson(res, 502, 'upstream_error', 'the upstream request failed');
    emitLog(502);
  };

  upstreamReq.on('error', failUpstream);

  upstreamReq.on('response', (upstreamRes) => {
    if (connectTimer !== null) clearTimeout(connectTimer);
    connectTimer = null;
    if (settled) {
      upstreamRes.resume();
      return;
    }

    const sse = isEventStream(target.path, upstreamRes.headers['content-type']);
    const outHeaders = {};
    for (const [name, value] of Object.entries(upstreamRes.headers)) {
      const key = name.toLowerCase();
      if (DROP_RESPONSE_HEADERS.has(key)) continue;
      if (value === undefined) continue;
      outHeaders[key] = value;
    }

    if (sse) {
      delete outHeaders['content-length'];
      outHeaders['cache-control'] = 'no-cache, no-transform';
      outHeaders['x-accel-buffering'] = 'no';
    } else {
      outHeaders['cache-control'] = 'no-cache';
      armIdle();
    }

    res.writeHead(upstreamRes.statusCode ?? 502, outHeaders);
    emitLog(upstreamRes.statusCode ?? 502);

    if (sse) {
      /* Flush the headers now so the client's EventSource opens immediately,
       * then push every chunk as it arrives: no buffering, no compression. */
      res.flushHeaders();
      upstreamRes.on('data', (chunk) => {
        if (res.destroyed || res.writableEnded) return;
        res.write(chunk);
      });
      upstreamRes.on('end', () => {
        if (!res.writableEnded) res.end();
      });
      /* Client closed the tab: stop pulling from the controller. */
      res.on('close', () => {
        upstreamRes.destroy();
      });
    } else {
      upstreamRes.on('data', () => armIdle());
      upstreamRes.on('end', () => disarmIdle());
      upstreamRes.pipe(res);
    }

    upstreamRes.on('error', failUpstream);
    res.on('close', () => {
      clearTimers();
      if (!upstreamRes.complete) upstreamRes.destroy();
    });
  });

  /* Body is streamed through: never buffered, so uploads keep constant memory. */
  req.pipe(upstreamReq);

  req.on('error', () => {
    /* Client aborted mid-request: stop the upstream exchange. The response
     * itself is skipped by the `res.destroyed` guard in failUpstream. */
    upstreamReq.destroy();
    clearTimers();
  });
}

/**
 * Bind a handler to one config/env/log triple.
 * @param {{config:object, env?:object, log?:Function}} options
 * @returns {(req:import('node:http').IncomingMessage, res:import('node:http').ServerResponse)=>void}
 */
export function createProxyHandler(options) {
  const state = { config: options.config, env: options.env ?? {}, log: options.log };
  return function proxyHandle(req, res) {
    handle(req, res, state);
  };
}
