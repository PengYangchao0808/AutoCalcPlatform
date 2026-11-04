/*
 * ACP adapter layer — URL rewrite engine for a DSH-hosted Workbench.
 *
 * Every ACP client module is handed the COMPLETE url (transport.request,
 * transport.remote, EventSourceClass(url), ctx.fetchFn) and most of them pass
 * absolute "/api/..." literals. A DSH host therefore must rewrite urls, not
 * merely inject a base. This module does exactly that:
 *
 *   rewriteUrl("/api/v1/jobs")            → "/acp-api/v1/jobs"
 *   rewriteUrl("/api/backends")           → "/acp-api/backends"
 *   rewriteUrl("/api")                    → "/acp-api"
 *   rewriteUrl("/api/v1/jobs") twice      → identical (idempotent)
 *   rewriteUrl("/acp/js/x.js")            → unchanged
 *   rewriteUrl("relative")                → unchanged
 *   rewriteUrl("https://x/api/v1")        → unchanged (foreign origin never rewritten)
 *
 * installTransport / installFetch / installEventSource wrap the real seams and
 * keep delegation semantics: `this`, every extra argument and the returned
 * value/promise identity are preserved (no extra `.then` layers). All three
 * are idempotent and return {ok, installed, skipped}; reset() undoes them.
 *
 * installFetch input shapes: a string is rewritten directly; a URL object is
 * rewritten from pathname+search+hash but keeps its OWN origin (only `/api/…`
 * paths are ever touched); a Request-like `{url}` object is forwarded as the
 * rewritten url with the original input as `init` (the only shape fetch
 * accepts without a Request constructor), and is left untouched when the
 * rewrite would be a no-op.
 *
 * Foreign-origin invariant: an absolute http(s) URL is only a rewrite target
 * when its host is loopback (the ACP Controller is loopback by design — the
 * plan forbids any other upstream) or when it matches `win.location.origin`.
 * Every other absolute URL is forwarded verbatim, in BOTH the string and the
 * URL-object form, so the adapter can never redirect a request to, or rewrite
 * a path on, an origin the page does not own.
 */
window.ACPAdapters = window.ACPAdapters || {};
ACPAdapters.dsh = ACPAdapters.dsh || {};

ACPAdapters.dsh.baseUrl = (function () {
  "use strict";

  var DEFAULT_PREFIX = "/acp-api";
  var MARK = "__acpDshWrapped";
  var records = [];

  function normalisePrefix(options) {
    var prefix = (options && typeof options.apiPrefix === "string" && options.apiPrefix !== "")
      ? options.apiPrefix
      : DEFAULT_PREFIX;
    prefix = prefix.replace(/\/{2,}/g, "/");
    if (prefix.length > 1 && prefix.charAt(prefix.length - 1) === "/") prefix = prefix.slice(0, -1);
    return prefix;
  }

  /* "/" + "/v1" would yield "//v1"; a bare "/" prefix joins without doubling. */
  function joinPrefix(prefix, rest) {
    return (prefix === "/") ? rest : prefix + rest;
  }

  function isForeignUrl(url) {
    return /^https?:\/\//i.test(url) ||
      url.slice(0, 2) === "//" ||
      /^data:/i.test(url) ||
      /^blob:/i.test(url);
  }

  /* The ACP Controller is loopback-only by plan; a loopback absolute URL is
     therefore the one absolute form this adapter may rewrite. */
  function isLoopbackHost(host) {
    var bare = String(host || "").toLowerCase().replace(/:\d+$/, "");
    return bare === "127.0.0.1" || bare === "localhost" || bare === "::1" || bare === "[::1]";
  }

  /* A URL object is a rewrite target only when it points at loopback or at the
     page's own origin. Anything else is forwarded verbatim — the adapter must
     never rewrite a path on an origin the page does not own. */
  function isRewriteTarget(urlobj, win) {
    var protocol = (typeof urlobj.protocol === "string") ? urlobj.protocol : "";
    if (!/^https?:$/i.test(protocol)) return true;
    var host = (typeof urlobj.host === "string") ? urlobj.host : "";
    if (!host) return true;
    if (isLoopbackHost(host)) return true;
    var loc = win && win.location;
    var pageOrigin = (loc && typeof loc.origin === "string") ? loc.origin : "";
    return pageOrigin !== "" && pageOrigin === protocol + "//" + host;
  }

  function rewriteUrl(url, options) {
    if (typeof url !== "string") return url;
    var prefix = normalisePrefix(options);
    // 1. idempotence: already under the host prefix (also covers prefix "/api").
    if (url === prefix || url.indexOf(prefix + "/") === 0) return url;
    // 2. never touch a foreign origin or an inline scheme.
    if (isForeignUrl(url)) return url;
    // 3. the ACP wire prefix.
    if (url.indexOf("/api/") === 0) return joinPrefix(prefix, url.slice(4));
    // 4. bare "/api".
    if (url === "/api") return prefix;
    // 5. anything else ("/acp/js/x.js", "relative", "").
    return url;
  }

  function mark(fn) {
    try {
      Object.defineProperty(fn, MARK, { value: true, configurable: true, enumerable: false });
    } catch (e) {
      fn[MARK] = true;
    }
    return fn;
  }

  function isMarked(fn) {
    return !!fn && fn[MARK] === true;
  }

  function remember(restore) {
    records.push(restore);
  }

  function installTransport(transport, options) {
    var result = { ok: true, installed: [], skipped: [] };
    if (!transport || typeof transport !== "object") {
      result.skipped.push("transport");
      return result;
    }
    var opts = { apiPrefix: normalisePrefix(options) };
    ["request", "remote"].forEach(function (name) {
      var label = "transport." + name;
      var real = transport[name];
      if (typeof real !== "function") { result.skipped.push(label); return; }
      if (isMarked(real)) { result.skipped.push(label); return; }
      var wrapped = mark(function (url) {
        var args = Array.prototype.slice.call(arguments);
        args[0] = rewriteUrl(url, opts);
        return real.apply(this, args);
      });
      transport[name] = wrapped;
      remember(function () { transport[name] = real; });
      result.installed.push(label);
    });
    return result;
  }

  function installFetch(win, options) {
    var result = { ok: true, installed: [], skipped: [] };
    if (!win || typeof win !== "object") { result.skipped.push("fetch"); return result; }
    var real = win.fetch;
    if (typeof real !== "function") { result.skipped.push("fetch"); return result; }
    if (isMarked(real)) { result.skipped.push("fetch"); return result; }
    var opts = { apiPrefix: normalisePrefix(options) };

    var wrapped = mark(function (input, init) {
      var arg = input;
      if (typeof input === "string") {
        arg = rewriteUrl(input, opts);
      } else if (input && typeof input === "object" && typeof input.href === "string" && typeof input.pathname === "string") {
        // URL instance — duck-typed, `instanceof` is realm-bound.
        // A URL object outside loopback / the page origin is never rewritten.
        if (!isRewriteTarget(input, win)) return real.call(this, input, init);
        var rel = input.pathname + (input.search || "") + (input.hash || "");
        var rewritten = rewriteUrl(rel, opts);
        if (rewritten !== rel) {
          var origin = (typeof input.protocol === "string" && /^https?:$/i.test(input.protocol) &&
            typeof input.host === "string" && input.host) ? input.protocol + "//" + input.host : "";
          arg = origin + rewritten;
        }
      } else if (input && typeof input === "object" && typeof input.url === "string") {
        // Request-like: only touched when the rewrite actually changes the url;
        // a Request cannot be re-created here, so it is forwarded as `init`.
        var target = rewriteUrl(input.url, opts);
        if (target === input.url) return real.call(this, input, init);
        return real.call(this, target, (init === undefined) ? input : init);
      }
      return real.call(this, arg, init);
    });

    win.fetch = wrapped;
    remember(function () { win.fetch = real; });
    result.installed.push("fetch");
    return result;
  }

  function installEventSource(win, options) {
    var result = { ok: true, installed: [], skipped: [] };
    if (!win || typeof win !== "object") { result.skipped.push("EventSource"); return result; }
    var Real = win.EventSource;
    if (!Real || (typeof Real !== "function" && typeof Real !== "object")) {
      result.skipped.push("EventSource");
      return result;
    }
    if (isMarked(Real)) { result.skipped.push("EventSource"); return result; }
    var opts = { apiPrefix: normalisePrefix(options) };

    var Wrapped = mark(function WrappedEventSource(url, cfg) {
      return new Real(rewriteUrl(url, opts), cfg);
    });
    Wrapped.prototype = Real.prototype;
    Wrapped.CONNECTING = Real.CONNECTING;
    Wrapped.OPEN = Real.OPEN;
    Wrapped.CLOSED = Real.CLOSED;

    win.EventSource = Wrapped;
    remember(function () { win.EventSource = Real; });
    result.installed.push("EventSource");
    return result;
  }

  function installAll(win, options) {
    var target = win || window;
    var result = { ok: true, installed: [], skipped: [] };
    var transport = target.ACPClient && target.ACPClient.transport;
    var parts = [];
    if (transport) {
      parts.push(installTransport(transport, options));
    } else {
      result.skipped.push("ACPClient.transport");
    }
    parts.push(installFetch(target, options));
    parts.push(installEventSource(target, options));
    parts.forEach(function (part) {
      if (!part || part.ok === false) result.ok = false;
      result.installed = result.installed.concat(part.installed || []);
      result.skipped = result.skipped.concat(part.skipped || []);
    });
    return result;
  }

  /* Tests only: restore every original and forget the markers. */
  function reset() {
    while (records.length) {
      var restore = records.pop();
      try { restore(); } catch (e) { /* already gone */ }
    }
  }

  return {
    DEFAULT_PREFIX: DEFAULT_PREFIX,
    rewriteUrl: rewriteUrl,
    installTransport: installTransport,
    installFetch: installFetch,
    installEventSource: installEventSource,
    installAll: installAll,
    reset: reset
  };
})();
