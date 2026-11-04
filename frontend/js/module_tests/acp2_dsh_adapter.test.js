/* ACP × DSH adapter layer (js/adapters/**).
 *
 * Pins the bounded, self-contained adapter surface a DSH host needs:
 *   dsh/env.js              ACP_ENV producer (prefix normalisation, install guard)
 *   dsh/base-url.js         "/api/*" rewrite + transport/fetch/EventSource wrappers
 *   dsh/restricted-client.js read-only client facade
 *   dsh/theme.js            DSH custom properties → ACP custom properties
 *   dsh/focus-bridge.js     selection uplink (coalesced, monotonic, never rejects)
 *   acp-web/env.js          standalone-page env producer (never overwrites)
 */
"use strict";

const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;
  const FRONTEND = path.resolve(__dirname, "..", "..");

  function sandbox(overrides) {
    return makeSandbox(Object.assign({ URL: URL }, overrides || {}));
  }

  function loadBase(overrides) {
    const sb = sandbox(overrides);
    loadScript("js/adapters/dsh/base-url.js", sb);
    return { sb, base: sb.ACPAdapters.dsh.baseUrl };
  }

  function deferred() {
    const d = {};
    d.promise = new Promise(function (resolve, reject) { d.resolve = resolve; d.reject = reject; });
    return d;
  }

  // ── 1. dsh/env.js ──────────────────────────────────────────────────────
  await test("dsh_adapter: env.build defaults, derived bases, prefix normalisation", async () => {
    const sb = sandbox();
    loadScript("js/adapters/dsh/env.js", sb);
    const env = sb.ACPAdapters.dsh.env;
    const built = env.build();
    assertEq(built.host, "dsh");
    assertEq(built.apiPrefix, "/acp-api");
    assertEq(built.apiBase, "/acp-api/v1");
    assertEq(built.apiV2Base, "/acp-api/v2");
    assertEq(built.assetBase, "/acp");
    assertEq(built.storagePrefix, "dsh:");
    assertEq(built.manageDocument, false);
    assertEq(built.focusEndpoint, "/acp-api/ui/focus");
    assertEq(built.sessionId, null);

    const collapsed = env.build({ apiPrefix: "/acp-api/", assetBase: "/acp//img/" });
    assertEq(collapsed.apiPrefix, "/acp-api", "trailing slash collapsed");
    assertEq(collapsed.apiBase, "/acp-api/v1");
    assertEq(collapsed.assetBase, "/acp/img", "duplicate slashes collapsed");
    assertEq(env.build({ apiPrefix: "/x", focusEndpoint: "/acp-api/ui/own" }).focusEndpoint, "/acp-api/ui/own");

    [["", "E_ENV_INPUT"], ["acp-api", "E_ENV_INPUT"], [42, "E_ENV_INPUT"]].forEach((pair) => {
      const err = (function () {
        try { env.build({ apiPrefix: pair[0] }); return null; } catch (e) { return e; }
      })();
      assert(err instanceof Error, "apiPrefix " + JSON.stringify(pair[0]) + " rejected");
      assertEq(err.code, pair[1]);
    });
  });

  await test("dsh_adapter: env.install sets ACP_ENV once and refuses to mutate", async () => {
    const sb = sandbox();
    loadScript("js/adapters/dsh/env.js", sb);
    const env = sb.ACPAdapters.dsh.env;
    const installed = env.install(sb, { sessionId: "s1", generation: 2 });
    assert(sb.ACP_ENV === installed, "ACP_ENV installed");
    assertEq(sb.ACP_ENV.sessionId, "s1");
    assertEq(sb.ACP_ENV.generation, 2);

    const before = sb.ACP_ENV;
    const err = (function () { try { env.install(sb); return null; } catch (e) { return e; } })();
    assert(err instanceof Error);
    assertEq(err.code, "E_ENV_INSTALLED");
    assert(sb.ACP_ENV === before, "existing object untouched");

    const forced = env.install(sb, { force: true, apiPrefix: "/acp2" });
    assert(forced !== before && sb.ACP_ENV === forced, "force replaces the reference");
    assertEq(forced.apiBase, "/acp2/v1");
  });

  // ── 2. dsh/base-url.js ─────────────────────────────────────────────────
  await test("dsh_adapter: rewriteUrl purity table + idempotence + no foreign origin", async () => {
    const { base } = loadBase();
    const table = [
      ["/api/v1/jobs", "/acp-api/v1/jobs"],
      ["/api/backends", "/acp-api/backends"],
      ["/api/jobs/1/events", "/acp-api/jobs/1/events"],
      ["/api", "/acp-api"],
      ["/acp-api/v1/jobs", "/acp-api/v1/jobs"],
      ["/acp/js/x.js", "/acp/js/x.js"],
      ["relative", "relative"],
      ["", ""],
      ["https://other.example/api/v1/jobs", "https://other.example/api/v1/jobs"],
      ["http://127.0.0.1:8765/api/x", "http://127.0.0.1:8765/api/x"],
      ["//cdn.example/api/x", "//cdn.example/api/x"],
      ["data:text/plain,/api/x", "data:text/plain,/api/x"],
      ["blob:http://x/api", "blob:http://x/api"],
    ];
    table.forEach((pair) => assertEq(base.rewriteUrl(pair[0]), pair[1], "rewrite " + pair[0]));
    assertEq(base.rewriteUrl(42), 42, "non-string returned unchanged");
    assertEq(base.rewriteUrl(null), null);

    // custom prefix + bare "/" prefix
    assertEq(base.rewriteUrl("/api/v1/jobs", { apiPrefix: "/host/api/" }), "/host/api/v1/jobs");
    assertEq(base.rewriteUrl("/api/v1", { apiPrefix: "/" }), "/v1");

    table.forEach((pair) => {
      const once = base.rewriteUrl(pair[0]);
      assertEq(base.rewriteUrl(once), once, "idempotent for " + pair[0]);
      if (once !== pair[0]) {
        assert(!/^https?:\/\//i.test(once), "never produces a foreign origin: " + pair[0] + " → " + once);
      }
    });
  });

  await test("dsh_adapter: installTransport rewrites arg 0, keeps this/args/identity, wraps once", async () => {
    const calls = [];
    const transport = {
      request: function (url, opts) {
        calls.push({ url: url, opts: opts, self: this, count: arguments.length });
        return Promise.reject(new Error("boom"));
      },
      remote: function (url, opts) {
        calls.push({ url: url, opts: opts, self: this, count: arguments.length });
        return "sync-identity";
      },
    };
    const { base } = loadBase();
    const first = base.installTransport(transport);
    assertEq(first.installed, ["transport.request", "transport.remote"]);
    assertEq(first.ok, true);

    const owner = { tag: "owner" };
    assertEq(transport.remote.call(owner, "/api/v1/x", { a: 1 }), "sync-identity", "return value preserved verbatim");
    assertEq(calls[0].url, "/acp-api/v1/x");
    assertEq(calls[0].opts, { a: 1 }, "other args preserved");
    assert(calls[0].self === owner, "this preserved");
    assertEq(calls[0].count, 2);

    const err = await transport.request("/api/backends").then(() => null, (e) => e);
    assertEq(err.message, "boom", "rejection passes through unchanged");
    assertEq(calls.length, 2, "original called exactly once per wrapper call");

    const second = base.installTransport(transport);
    assertEq(second.installed, []);
    assertEq(second.skipped, ["transport.request", "transport.remote"]);
    assertEq(transport.remote("/api/v1/jobs"), "sync-identity");
    assertEq(calls[2].url, "/acp-api/v1/jobs", "no double prefix after a second install");
    assertEq(calls.length, 3, "double install is a single wrap");

    base.reset();
    assertEq(transport.remote("/api/v1/jobs"), "sync-identity");
    assertEq(calls[3].url, "/api/v1/jobs", "reset restores the original");
  });

  await test("dsh_adapter: installFetch handles string / URL / request-like inputs", async () => {
    const seen = [];
    function fetchStub(input, init) {
      seen.push({ input: input, init: init });
      return "fetch-result";
    }
    const { sb, base } = loadBase({ fetch: fetchStub });

    assertEq(base.installFetch(sb).installed, ["fetch"]);
    const init = { method: "GET" };

    assertEq(sb.fetch("/api/v1/jobs", init), "fetch-result");
    assertEq(seen[0].input, "/acp-api/v1/jobs");
    assert(seen[0].init === init, "init forwarded");

    const url = new URL("http://127.0.0.1:8765/api/v1/jobs?x=1");
    sb.fetch(url);
    assertEq(seen[1].input, "http://127.0.0.1:8765/acp-api/v1/jobs?x=1", "URL instance rewritten, origin kept");

    const foreign = new URL("https://other.example/api/v1/jobs");
    sb.fetch(foreign);
    assert(seen[2].input === foreign,
      "a foreign-origin URL object is forwarded verbatim, never rewritten");

    // ...but the page's own origin is a rewrite target.
    const sameOrigin = new URL("http://host.local:9999/api/v1/jobs");
    sb.location = { origin: "http://host.local:9999" };
    sb.fetch(sameOrigin);
    assertEq(seen[3].input, "http://host.local:9999/acp-api/v1/jobs",
      "URL object on the page origin is rewritten");

    const requestLike = { url: "/api/v1/jobs" };
    sb.fetch(requestLike);
    assertEq(seen[4].input, "/acp-api/v1/jobs");
    assert(seen[4].init === requestLike, "Request-like forwarded as init when no second arg");

    const requestLikeForeign = { url: "https://x/api/v1" };
    sb.fetch(requestLikeForeign, init);
    assert(seen[5].input === requestLikeForeign, "untouched Request-like forwarded unchanged");

    assertEq(base.installFetch(sb).skipped, ["fetch"], "idempotent");

    const missing = sandbox();
    loadScript("js/adapters/dsh/base-url.js", missing);
    const skipped = missing.ACPAdapters.dsh.baseUrl.installFetch(missing);
    assertEq(skipped.skipped, ["fetch"], "missing fetch skipped cleanly");
    assertEq(skipped.ok, true);
  });

  await test("dsh_adapter: installEventSource rewrites url, keeps constants, skips cleanly", async () => {
    function FakeEventSource(url, cfg) { this.url = url; this.cfg = cfg; }
    FakeEventSource.CONNECTING = 0;
    FakeEventSource.OPEN = 1;
    FakeEventSource.CLOSED = 2;

    const { sb, base } = loadBase({ EventSource: FakeEventSource });
    assertEq(base.installEventSource(sb).installed, ["EventSource"]);

    const stream = new sb.EventSource("/api/jobs/1/events", { withCredentials: true });
    assertEq(stream.url, "/acp-api/jobs/1/events", "url rewritten");
    assertEq(stream.cfg, { withCredentials: true }, "config forwarded");
    assert(stream instanceof FakeEventSource, "prototype linkage preserved");
    assertEq([sb.EventSource.CONNECTING, sb.EventSource.OPEN, sb.EventSource.CLOSED], [0, 1, 2]);
    assertEq(base.installEventSource(sb).skipped, ["EventSource"], "idempotent");

    const missing = sandbox();
    loadScript("js/adapters/dsh/base-url.js", missing);
    const res = missing.ACPAdapters.dsh.baseUrl.installEventSource(missing);
    assertEq(res.skipped, ["EventSource"], "missing EventSource skipped cleanly");
  });

  await test("dsh_adapter: installAll covers transport + fetch + EventSource once", async () => {
    function FakeEventSource(url) { this.url = url; }
    const sb = sandbox({ fetch: function () { return "f"; }, EventSource: FakeEventSource });
    sb.ACPClient = {
      transport: {
        request: function (u) { this.last = u; return "r"; },
        remote: function (u) { return "r2"; },
      },
    };
    loadScript("js/adapters/dsh/base-url.js", sb);
    const base = sb.ACPAdapters.dsh.baseUrl;
    const combined = base.installAll(sb);
    assertEq(combined.installed, ["transport.request", "transport.remote", "fetch", "EventSource"]);
    assertEq(combined.skipped, []);
    assertEq(base.installAll(sb).installed, [], "second installAll installs nothing");
  });

  // ── 3. dsh/restricted-client.js ────────────────────────────────────────
  function fullClient() {
    function ns(name) { return { name: name, fn: function () { return name; } }; }
    return {
      transport: ns("transport"),
      store: ns("store"),
      events: ns("events"),
      eventInvalidation: ns("eventInvalidation"),
      polling: ns("polling"),
      jobDetail: ns("jobDetail"),
      taskView: ns("taskView"),
      research: ns("research"),
      systemEndpoints: ns("systemEndpoints"),
      science: { get: function () {}, list: function () {}, preflight: function () {} },
      frameCandidates: { list: function () {}, save: function () {}, remove: function () {} },
      jobCommands: ns("jobCommands"),
      researchCommands: ns("researchCommands"),
      wizardEndpoints: ns("wizardEndpoints"),
    };
  }

  await test("dsh_adapter: restricted client is read-only, keeps identity, never mutates the source", async () => {
    const sb = sandbox();
    loadScript("js/adapters/dsh/restricted-client.js", sb);
    const rc = sb.ACPAdapters.dsh.restrictedClient;
    const source = fullClient();
    const snapshot = Object.keys(source).sort();
    const client = rc.create(source);

    assert(client !== source, "a new object");
    assertEq(Object.keys(source).sort(), snapshot, "source object not mutated");
    assertEq(Object.keys(client).sort(), [
      "eventInvalidation", "events", "frameCandidates", "jobDetail", "polling",
      "research", "science", "store", "systemEndpoints", "taskView", "transport",
    ], "read namespaces only");

    assertEq(client.transport, source.transport, "fully allowed namespace keeps identity");
    assertEq(client.science.preflight, undefined, "science.preflight dropped");
    assertEq(typeof client.science.get, "function");
    assertEq(Object.keys(client.frameCandidates), ["list"]);
    assertEq(typeof source.science.preflight, "function", "source science intact");
    assertEq(typeof source.frameCandidates.save, "function", "source frameCandidates intact");

    const described = rc.describe(client);
    assertEq(described.readOnly, true);
    assertEq(described.writeNamespacesPresent, []);
    assertEq(rc.assertReadOnly(client), true);

    const writeErr = (function () {
      try { rc.assertReadOnly(source); return null; } catch (e) { return e; }
    })();
    assert(writeErr instanceof Error, "full client is not read-only");
    assertEq(writeErr.code, "E_WRITE_NAMESPACE");
    assertEq(writeErr.namespace, "jobCommands");
    assertEq(rc.describe(source).readOnly, false);
  });

  await test("dsh_adapter: allowWrite restores write namespaces, allow narrows further", async () => {
    const sb = sandbox();
    loadScript("js/adapters/dsh/restricted-client.js", sb);
    const rc = sb.ACPAdapters.dsh.restrictedClient;
    const source = fullClient();

    const writable = rc.create(source, { allowWrite: true });
    assertEq(writable.jobCommands, source.jobCommands);
    assertEq(writable.science, source.science, "identity when writes are allowed");
    assertEq(writable.frameCandidates, source.frameCandidates);
    assertEq(rc.describe(writable).writeNamespacesPresent, ["jobCommands", "researchCommands", "wizardEndpoints"]);
    assertEq(rc.describe(writable).readOnly, false);

    const narrowed = rc.create(source, { allow: ["taskView", "jobCommands"] });
    assertEq(Object.keys(narrowed).sort(), ["taskView"], "allow fences further; writes still gated");
    const narrowedWritable = rc.create(source, { allow: ["taskView", "jobCommands"], allowWrite: true });
    assertEq(Object.keys(narrowedWritable).sort(), ["jobCommands", "taskView"]);

    // Namespaces the source lacks are never invented.
    const partial = rc.create({ taskView: { render: function () {} } });
    assertEq(Object.keys(partial), ["taskView"]);
    const noList = rc.create({ frameCandidates: { save: function () {} } });
    assertEq(Object.keys(noList), [], "frameCandidates without list is not exposed read-only");
  });

  // ── 4. dsh/theme.js ────────────────────────────────────────────────────
  function mappedStyles(pairs) {
    const styles = {};
    pairs.forEach((pair) => { styles[pair[0]] = pair[1]; });
    return styles;
  }

  function themeDoc(styles, options) {
    options = options || {};
    const written = {};
    const element = { style: { setProperty: function (name, value) { written[name] = value; } } };
    const doc = {
      documentElement: options.noDocumentElement ? undefined : element,
      defaultView: options.noGetComputedStyle ? {} : {
        getComputedStyle: function () {
          return { getPropertyValue: function (name) { return styles[name] || ""; } };
        },
      },
    };
    return { doc, written, element };
  }

  await test("dsh_adapter: theme.apply maps only present DSH variables (fallback off)", async () => {
    const sb = sandbox();
    loadScript("js/adapters/dsh/theme.js", sb);
    const theme = sb.ACPAdapters.dsh.theme;
    const { doc, written } = themeDoc({ "--dsh-bg": "#111", "--dsh-accent": "  #4ea1ff  ", "--dsh-danger": "" });
    const res = theme.apply(doc, { fallback: false });
    assertEq(res.applied.sort(), ["--accent", "--bg-base"], "only present variables are written");
    assertEq(written["--bg-base"], "#111");
    assertEq(written["--accent"], "#4ea1ff", "value trimmed");
    assertEq(written["--text-primary"], undefined);
    assert(res.skipped.indexOf("--text-primary") !== -1, "absent ones skipped");

    const fallbackDoc = themeDoc({});
    const fb = theme.apply(fallbackDoc.doc);
    assertEq(fb.applied.sort(), Object.keys(theme.DEFAULT_VALUES).map((k) => theme.MAPPING.filter((p) => p[0] === k)[0][1]).sort(),
      "only documented defaults are applied when everything is absent");
    assertEq(fallbackDoc.written["--accent"], theme.DEFAULT_VALUES["--dsh-accent"]);
    assertEq(theme.DEFAULT_VALUES["--dsh-radius"], "10px");
  });

  await test("dsh_adapter: theme ACP variable names exist in the real stylesheets", async () => {
    const sb = sandbox();
    loadScript("js/adapters/dsh/theme.js", sb);
    const theme = sb.ACPAdapters.dsh.theme;
    const css = [
      fs.readFileSync(path.join(FRONTEND, "css", "workbench", "base.css"), "utf8"),
      fs.readFileSync(path.join(FRONTEND, "css", "shell", "shell.css"), "utf8"),
    ].join("\n");

    const targets = theme.MAPPING.map((pair) => pair[1]);
    assertEq(targets.length, theme.MAPPING.length);
    assertEq(new Set(targets).size, targets.length, "no duplicate ACP target");
    targets.forEach((name) => {
      assert(css.indexOf(name + ":") !== -1, "ACP variable defined in CSS: " + name);
    });
    theme.MAPPING.forEach((pair) => assert(/^--dsh-/.test(pair[0]), "DSH source name: " + pair[0]));
    // Both stylesheets are actually reached by the mapping.
    assert(targets.indexOf("--bg-base") !== -1 && targets.indexOf("--shell-topbar-h") !== -1);
  });

  await test("dsh_adapter: theme.apply no-ops without documentElement/getComputedStyle; linkStyles dedupes", async () => {
    const sb = sandbox();
    loadScript("js/adapters/dsh/theme.js", sb);
    const theme = sb.ACPAdapters.dsh.theme;

    const noReader = themeDoc({ "--dsh-bg": "#111" }, { noGetComputedStyle: true });
    const res = theme.apply(noReader.doc);
    assertEq(res.ok, false);
    assertEq(res.applied, [], "no writes without getComputedStyle");
    assertEq(Object.keys(noReader.written), []);

    const noRoot = themeDoc({ "--dsh-bg": "#111" }, { noDocumentElement: true });
    assertEq(theme.apply(noRoot.doc).applied, [], "no writes without documentElement");

    function makeLinkDoc(existing) {
      const head = { children: [], appendChild: function (el) { head.children.push(el); } };
      existing.forEach(function (href) {
        head.children.push({
          getAttribute: function (name) {
            if (name === "href") return href;
            if (name === "data-acp-dsh-css") return "1";
            return null;
          },
        });
      });
      return {
        head: head,
        createElement: function (tag) {
          const el = {
            tag: tag,
            attrs: {},
            setAttribute: function (n, v) { el.attrs[n] = v; },
            getAttribute: function (n) {
              return Object.prototype.hasOwnProperty.call(el.attrs, n) ? el.attrs[n] : null;
            },
          };
          return el;
        },
      };
    }

    const doc = makeLinkDoc(["/acp/css/workbench/legacy.css"]);
    const first = theme.linkStyles(doc, { hrefs: ["css/workbench/base.css", "css/workbench/base.css", "/abs/b.css"] });
    assertEq(first.added, ["/acp/css/workbench/base.css", "/abs/b.css"], "dedupe against existing + within the call");
    assertEq(first.skipped, ["/acp/css/workbench/base.css"]);
    assertEq(doc.head.children.length, 3, "two links appended after the existing one");
    assertEq(doc.head.children[1].attrs.rel, "stylesheet");
    assertEq(doc.head.children[1].attrs.href, "/acp/css/workbench/base.css");
    assertEq(doc.head.children[2].attrs.href, "/abs/b.css", "order preserved, base only for relative hrefs");

    const again = theme.linkStyles(doc, { hrefs: ["css/workbench/base.css", "/abs/b.css"] });
    assertEq(again.added, []);
    assertEq(again.skipped, ["/acp/css/workbench/base.css", "/abs/b.css"]);
    assertEq(doc.head.children.length, 3, "no duplicates on a second call");

    const headless = theme.linkStyles({ head: null }, { hrefs: ["a.css"] });
    assertEq(headless.ok, false);
    assertEq(headless.added, []);
  });

  // ── 5. dsh/focus-bridge.js ─────────────────────────────────────────────
  function loadBridge() {
    const sb = sandbox();
    loadScript("js/adapters/dsh/focus-bridge.js", sb);
    return sb.ACPAdapters.dsh.focusBridge;
  }

  function stateOf(revision, generation, snapshotExtra) {
    return {
      sessionId: "s1",
      generation: generation === undefined ? 3 : generation,
      revision: revision,
      snapshot: Object.assign({ revision: revision, taskRef: "t" + revision }, snapshotExtra || {}),
    };
  }

  await test("dsh_adapter: focusBridge.buildBody copies references only and validates", async () => {
    const bridge = loadBridge();
    const body = bridge.buildBody({
      sessionId: "s1",
      generation: 4,
      revision: 7,
      snapshot: {
        projectId: "p", researchRef: "r", taskRef: "t", structureRef: "st",
        networkNodeId: "n", revision: 7, source: "canvas",
        molecule: { atoms: 999 }, energies: [1, 2, 3], frames: [],
      },
    });
    assertEq(body, {
      session: "s1",
      generation: 4,
      revision: 7,
      snapshot: {
        projectId: "p", researchRef: "r", taskRef: "t",
        structureRef: "st", networkNodeId: "n", revision: 7, source: "canvas",
      },
    }, "extra snapshot payloads are never uplinked");

    const cases = [
      { sessionId: "", revision: 1, snapshot: {} },
      { sessionId: "s", revision: 1.5, snapshot: {} },
      { sessionId: "s", revision: 1, snapshot: null },
      { sessionId: "s", revision: 1, snapshot: [1] },
    ];
    cases.forEach((bad) => {
      const err = (function () { try { bridge.buildBody(bad); return null; } catch (e) { return e; } })();
      assert(err instanceof Error, "rejects " + JSON.stringify(bad));
      assertEq(err.code, "E_FOCUS_BODY");
    });
  });

  await test("dsh_adapter: focusBridge.publish never rejects and drops stale/no-session states", async () => {
    const bridge = loadBridge();
    const calls = [];
    const transport = function (url, opts) {
      calls.push({ url: url, opts: opts });
      return Promise.resolve({ ok: true });
    };
    const live = bridge.create({ endpoint: "/acp-api/ui/focus", sessionId: "s1", generation: 3, transport: transport });

    const noSession = bridge.create({ endpoint: "/e", sessionId: "", generation: 3, transport: transport });
    assertEq(await noSession.publish(stateOf(1)), { ok: true, sent: false, coalesced: false, reason: "no_session" });

    const stale = await live.publish(stateOf(1, 2));
    assertEq(stale.reason, "stale_generation");
    assertEq((await live.publish(stateOf(1, 0))).reason, "stale_generation");

    const sent = await live.publish(stateOf(1, 3));
    assertEq([sent.ok, sent.sent, sent.coalesced], [true, true, false]);
    assertEq(JSON.parse(calls[0].opts.body).session, "s1");
    assertEq(calls[0].opts.method, "POST");
    assertEq(calls[0].opts.headers["content-type"], "application/json");
    assertEq(calls[0].url, "/acp-api/ui/focus");

    const drop = await live.publish(stateOf(1, 3));
    assertEq(drop.reason, "not_monotonic", "non-increasing revision dropped");
    assertEq(calls.length, 1, "dropped state never reached the transport");

    // A hostile state must still resolve, never throw.
    const garbage = await live.publish({ sessionId: "s1", generation: 3, revision: 2, snapshot: null });
    assertEq(garbage.sent, false);
    assertEq(garbage.ok, true);
    assertEq(garbage.reason, "invalid_state");
    const nonsense = await live.publish(undefined);
    assertEq(nonsense.ok, true, "publish(undefined) resolves");
    assertEq(nonsense.sent, false);

    const stats = live.stats();
    assertEq(stats.sent, 1);
    assertEq(stats.lastRevision, 1);
    assertEq(stats.rejected, 0);
    assertEq(live.pending(), 0);
  });

  await test("dsh_adapter: focusBridge coalesces to the latest queued state only", async () => {
    const bridge = loadBridge();
    const calls = [];
    const gates = [];
    const transport = function (url, opts) {
      calls.push({ url: url, opts: opts });
      const gate = deferred();
      gates.push(gate);
      return gate.promise;
    };
    const live = bridge.create({ endpoint: "/acp-api/ui/focus", sessionId: "s1", generation: 3, transport: transport });

    const p1 = live.publish(stateOf(1));
    assertEq(live.pending(), 0, "first publish is in flight, not queued");
    const p2 = live.publish(stateOf(2));
    assertEq(live.pending(), 1);
    const p3 = live.publish(stateOf(3));
    assertEq(live.pending(), 1, "only one queued slot");
    assertEq(calls.length, 1, "nothing else was sent yet");

    gates[0].resolve({ ok: true });
    assertEq((await p1).sent, true);
    const superseded = await p2;
    assertEq([superseded.sent, superseded.coalesced, superseded.reason], [false, true, "superseded"]);

    assertEq(calls.length, 2, "exactly one follow-up send for three publishes");
    assertEq(JSON.parse(calls[1].opts.body).revision, 3, "the LATEST queued state was sent");
    gates[1].resolve({ ok: true });
    const last = await p3;
    assertEq([last.sent, last.coalesced], [true, true]);

    const stats = live.stats();
    assertEq(stats.sent, 2);
    assertEq(stats.dropped, 1);
    assertEq(stats.lastRevision, 3);
    assertEq(live.pending(), 0);
  });

  await test("dsh_adapter: focusBridge counts rejected sends and keeps lastRevision", async () => {
    const bridge = loadBridge();
    const results = [Promise.resolve({ ok: true }), Promise.resolve({ ok: false }), Promise.reject(new Error("nope"))];
    const calls = [];
    const transport = function (url, opts) { calls.push(opts); return results.shift(); };
    const live = bridge.create({ endpoint: "/e", sessionId: "s1", generation: 3, transport: transport });

    assertEq((await live.publish(stateOf(4))).sent, true);
    assertEq(live.stats().lastRevision, 4);

    const refused = await live.publish(stateOf(5));
    assertEq([refused.ok, refused.sent, refused.reason], [true, false, "rejected"]);
    assertEq(live.stats().rejected, 1);
    assertEq(live.stats().lastRevision, 4, "rejected send does not advance lastRevision");

    const thrown = await live.publish(stateOf(5));
    assertEq(thrown.reason, "rejected", "a throwing transport is reported, not rethrown");
    assertEq(live.stats().rejected, 2);
    assertEq(live.stats().lastRevision, 4);
    assertEq(calls.length, 3, "no automatic retry");

    // Synchronous transport results are accepted too.
    const syncBridge = bridge.create({ endpoint: "/e", sessionId: "s1", generation: 3, transport: function () { return true; } });
    assertEq((await syncBridge.publish(stateOf(9))).sent, true);
    assertEq(syncBridge.stats().lastRevision, 9);
  });

  await test("dsh_adapter: focusBridge.attach publishes on change, detach is idempotent", async () => {
    const bridge = loadBridge();
    const calls = [];
    const transport = function (url, opts) { calls.push(opts); return Promise.resolve({ ok: true }); };
    const live = bridge.create({ endpoint: "/e", sessionId: "s1", generation: 3, transport: transport });

    const listeners = [];
    let unsubscribed = 0;
    const context = {
      subscribe: function (fn) {
        listeners.push(fn);
        return function () { unsubscribed += 1; };
      },
    };
    const detach = live.attach(context);
    assertEq(typeof detach, "function");
    assertEq(listeners.length, 1);

    listeners[0]({ revision: 8, taskRef: "t8" });
    await Promise.resolve();
    await Promise.resolve();
    assertEq(calls.length, 1, "context change published");
    const body = JSON.parse(calls[0].body);
    assertEq(body.revision, 8, "the snapshot's own revision is used");
    assertEq(body.snapshot, { taskRef: "t8", revision: 8 }, "the snapshot itself is the reference payload");
    assertEq(body.generation, 3);

    detach();
    detach();
    assertEq(unsubscribed, 1, "detach is idempotent");
    listeners[0]({ revision: 9 });
    await Promise.resolve();
    assertEq(calls.length, 1, "no publish after detach (listener was released)");

    // A context without subscribe is tolerated.
    assertEq(typeof live.attach({}), "function");
    assertEq(typeof live.attach(null), "function");
  });

  // ── 6. acp-web/env.js ──────────────────────────────────────────────────
  await test("dsh_adapter: acpWeb env defaults, unknown keys dropped, install never overwrites", async () => {
    const sb = sandbox();
    loadScript("js/adapters/acp-web/env.js", sb);
    const env = sb.ACPAdapters.acpWeb.env;

    assertEq(env.build(), {
      host: "acp-web",
      assetBase: "",
      apiBase: "/api/v1",
      apiV2Base: "/api/v2",
      manageDocument: true,
      storagePrefix: "",
      sessionId: null,
      focusBridge: null,
      degraded: false,
    });
    const custom = env.build({ apiBase: "/custom/v1", bogus: 1, host: "dsh" });
    assertEq(custom.apiBase, "/custom/v1");
    assertEq(custom.host, "acp-web", "host is fixed");
    assertEq(Object.prototype.hasOwnProperty.call(custom, "bogus"), false, "unknown keys dropped");

    assertEq(env.install(sb), { ok: true, installed: true });
    assertEq(sb.ACP_ENV.apiBase, "/api/v1");
    const again = env.install(sb, { apiBase: "/other/v1" });
    assertEq(again, { ok: true, installed: false }, "existing ACP_ENV is never overwritten");
    assertEq(sb.ACP_ENV.apiBase, "/api/v1");
  });
};
