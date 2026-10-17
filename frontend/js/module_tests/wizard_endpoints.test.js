/*
 * Behavior tests for the wizard endpoint adapters (R3 slice b, plan §42.2
 * wizard row / §42.4 "命令和表单（R3）").
 *
 * Pins the shipped frontend/js/client/wizard-endpoints.js module against the
 * seven former raw-fetch() sites in features/wizard.js, per site:
 *   - exact adapter (direct/transport), URL (incl. query encoding and the
 *     two literal "/api/v1/..." preview paths where baseUrl must NOT apply),
 *   - method/headers/body construction (FormData instance passthrough vs
 *     JSON.stringify payloads),
 *   - timeoutMs: 0 everywhere (legacy raw fetch had NO timeout — the 8s api()
 *     default must never apply; §42.4 "明确无超时例外不能机械统一"),
 *   - error derivation byte-equivalent to each legacy site (transport-shaped
 *     errors with .status/.body are fed in and the caller-visible message is
 *     asserted; see the module header for the documented statusText and
 *     non-JSON-body deviations),
 *   - abort signal passthrough (pre-aborted signal → no adapter call; live
 *     signal forwarded by identity; AbortError name preserved),
 *   - the two "no resp.ok check" legacy sites (structures/parse,
 *     validate-method) RESOLVE with a non-2xx JSON error body exactly like
 *     the legacy `await resp.json()` did,
 *   - the injected v1 adapter is stored but never invoked: all seven legacy
 *     sites were raw fetch() calls that never entered the api() non-GET
 *     task-view invalidation chokepoint — that no-invalidation semantics is
 *     preserved (no new invalidation semantics added).
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  /** Build the module with a recording direct adapter. `directImpl(url, opts)`
   * resolves/rejects like ACPClient.transport.request would (already-parsed
   * success values; transport-shaped Errors with .status/.body on non-2xx). */
  function build(directImpl, overrides) {
    const sandbox = makeSandbox({
      fetch: () => { throw new Error("global fetch must not be called by wizardEndpoints"); },
    });
    loadScript("js/client/wizard-endpoints.js", sandbox);
    const we = sandbox.ACPClient.wizardEndpoints;
    const seen = [];
    we.init(Object.assign({
      v1: () => { throw new Error("v1 adapter must not be called by wizardEndpoints"); },
      direct: (url, opts) => {
        seen.push({ url, opts: Object.assign({}, opts) });
        return Promise.resolve().then(() => directImpl(url, opts));
      },
      baseUrl: "/api/v1",
    }, overrides || {}));
    return { we, seen };
  }

  /** Transport-shaped non-2xx error (mirrors transport.js: .status/.body). */
  function transportError(status, body, message) {
    const err = new Error(message || "transport-generated");
    if (status != null) err.status = status;
    if (body !== undefined) err.body = body;
    return err;
  }

  async function rejects(promise) {
    try { await promise; } catch (e) { return e; }
    throw new Error("expected the promise to reject");
  }

  await test("module exposes init + the seven per-site methods", () => {
    const sandbox = makeSandbox({});
    loadScript("js/client/wizard-endpoints.js", sandbox);
    const we = sandbox.ACPClient.wizardEndpoints;
    ["init", "uploadStructure", "parseStructure", "fetchStructureSource",
      "uploadNmrBruker", "hessianPreview", "batchConfigPreview", "validateMethod",
    ].forEach((k) => assert(typeof we[k] === "function", "missing " + k));
  });

  // ── 1. uploadStructure — POST {API_BASE}/uploads (FormData) ──────────────
  await test("uploadStructure: FormData passthrough, no Content-Type, timeoutMs 0", async () => {
    const fd = new FormData();
    const { we, seen } = build(() => ({ structures: [{ name: "m" }], detected_format: "xyz" }));
    const out = await we.uploadStructure(fd);
    assertEq(out.structures.length, 1);
    assertEq(seen.length, 1);
    assertEq(seen[0].url, "/api/v1/uploads");
    assertEq(seen[0].opts.method, "POST");
    assert(seen[0].opts.body === fd, "FormData instance must pass through untouched");
    assertEq(seen[0].opts.headers, undefined, "no explicit headers (browser sets the multipart boundary)");
    assertEq(seen[0].opts.timeoutMs, 0, "legacy raw fetch had NO timeout — never the 8s default");
  });

  await test("uploadStructure: error re-derives the RAW body.detail (site 1 legacy `errBody.detail || \"Upload failed\"`)", async () => {
    const { we } = build(() => { throw transportError(422, { detail: "Unsupported structure format" }); });
    const err = await rejects(we.uploadStructure(new FormData()));
    assertEq(err.detail, "Unsupported structure format");
    assertEq(err.status, 422);
    assertEq(err.body.detail, "Unsupported structure format");
    // Detail-less JSON body → detail undefined → caller's "Upload failed" literal.
    const { we: w2 } = build(() => { throw transportError(500, {}); });
    const err2 = await rejects(w2.uploadStructure(new FormData()));
    assertEq(err2.detail, undefined);
    assertEq(err2.message, "Upload failed");
  });

  await test("uploadStructure: non-string detail preserved RAW (not transport-flattened)", async () => {
    const raw = [{ loc: ["body", "file"], msg: "invalid file" }];
    const { we } = build(() => { throw transportError(400, { detail: raw }); });
    const err = await rejects(we.uploadStructure(new FormData()));
    assert(Array.isArray(err.detail), "detail stays the raw legacy value");
    assertEq(err.detail[0].msg, "invalid file");
  });

  await test("uploadStructure: non-HTTP (network) errors pass through untouched", async () => {
    const boom = new TypeError("fetch failed");
    const { we } = build(() => { throw boom; });
    const err = await rejects(we.uploadStructure(new FormData()));
    assert(err === boom, "must be the very same error object");
  });

  // ── 2. parseStructure — POST {API_BASE}/structures/parse ─────────────────
  await test("parseStructure: JSON body construction verbatim, timeoutMs 0", async () => {
    const { we, seen } = build(() => ({ ok: true, structures: [] }));
    await we.parseStructure({ content: "2\nH2O\n", format: "auto", filename: "" });
    assertEq(seen[0].url, "/api/v1/structures/parse");
    assertEq(seen[0].opts.method, "POST");
    assertEq(seen[0].opts.headers, { "Content-Type": "application/json" });
    assertEq(JSON.parse(seen[0].opts.body), { content: "2\nH2O\n", format: "auto", filename: "" });
    assertEq(seen[0].opts.timeoutMs, 0, "legacy raw fetch had NO timeout");
  });

  await test("parseStructure: non-2xx JSON error body RESOLVES (legacy had no resp.ok check)", async () => {
    const { we } = build(() => { throw transportError(400, { detail: "bad content" }); });
    const body = await we.parseStructure({ content: "x", format: "auto", filename: "" });
    assertEq(body, { detail: "bad content" }); // body.ok falsy → legacy ok:false presentation path
  });

  await test("parseStructure: non-JSON error body rejects with the transport error", async () => {
    const { we } = build(() => { throw transportError(502, undefined); });
    const err = await rejects(we.parseStructure({ content: "x", format: "auto", filename: "" }));
    assertEq(err.status, 502);
  });

  // ── 3. fetchStructureSource — GET {API_BASE}/structure-sources/<id> ──────
  await test("fetchStructureSource: encodeURIComponent URL, no explicit method, parsed body returned", async () => {
    const { we, seen } = build(() => ({ structure: { xyz: "2\nH2O\n" } }));
    const out = await we.fetchStructureSource("job1/a b");
    assertEq(seen[0].url, "/api/v1/structure-sources/job1%2Fa%20b");
    assertEq(seen[0].opts.method, undefined, "legacy fetch(url) sent no explicit method key");
    assertEq(seen[0].opts.timeoutMs, 0, "legacy raw fetch had NO timeout");
    assertEq(out.structure.xyz, "2\nH2O\n");
  });

  await test("fetchStructureSource: non-2xx message forced to legacy 'HTTP <status>' (body detail never leaked)", async () => {
    const { we } = build(() => { throw transportError(404, { detail: "Structure source not found" }); });
    const err = await rejects(we.fetchStructureSource("x"));
    assertEq(err.message, "HTTP 404");
    assertEq(err.status, 404);
  });

  await test("fetchStructureSource: pre-aborted signal → no adapter call, AbortError", async () => {
    const { we, seen } = build(() => { throw new Error("adapter must not be called"); });
    const ac = new AbortController();
    ac.abort();
    const err = await rejects(we.fetchStructureSource("x", { signal: ac.signal }));
    assertEq(err.name, "AbortError");
    assertEq(seen.length, 0);
  });

  await test("fetchStructureSource: live signal forwarded by identity; text body passes through", async () => {
    const { we, seen } = build(() => "plain text body");
    const ac = new AbortController();
    await we.fetchStructureSource("x", { signal: ac.signal });
    assert(seen[0].opts.signal === ac.signal, "signal must be forwarded as-is");
    const out = await we.fetchStructureSource("y");
    assertEq(out, "plain text body", "non-JSON success resolves with the transport text contract");
  });

  // ── 4. uploadNmrBruker — POST {API_BASE}/uploads?parse=false&project_id= ─
  await test("uploadNmrBruker: query string encoding + FormData passthrough, timeoutMs 0", async () => {
    const fd = new FormData();
    const { we, seen } = build(() => ({ upload_id: "u1", filename: "fid" }));
    await we.uploadNmrBruker(fd, "proj a/b&c");
    assertEq(seen[0].url, "/api/v1/uploads?parse=false&project_id=" + encodeURIComponent("proj a/b&c"));
    assertEq(seen[0].opts.method, "POST");
    assert(seen[0].opts.body === fd);
    assertEq(seen[0].opts.timeoutMs, 0, "legacy raw fetch had NO timeout");
  });

  await test("uploadNmrBruker: error message = parsed body.detail verbatim; body-less falls back to 'HTTP <status>'", async () => {
    const { we } = build(() => { throw transportError(413, { detail: "File too large" }); });
    const err = await rejects(we.uploadNmrBruker(new FormData(), "p"));
    assertEq(err.message, "File too large");
    const { we: w2 } = build(() => { throw transportError(500, undefined); });
    const err2 = await rejects(w2.uploadNmrBruker(new FormData(), "p"));
    assertEq(err2.message, "HTTP 500");
  });

  // ── 5. hessianPreview — POST /api/v1/hessian-preview (literal path) ──────
  await test("hessianPreview: literal legacy URL (baseUrl NOT applied), JSON body, signal identity, timeoutMs 0", async () => {
    const { we, seen } = build(() => ({ results: [], summary: {} }), { baseUrl: "/api/v9" });
    const payload = { schema_id: "s", level_id: "lv", recalc_hess: "auto", structures: [{ name: "a" }] };
    const ac = new AbortController();
    await we.hessianPreview(payload, { signal: ac.signal });
    assertEq(seen[0].url, "/api/v1/hessian-preview", "literal legacy path — injected baseUrl must NOT be applied");
    assertEq(seen[0].opts.method, "POST");
    assertEq(seen[0].opts.headers, { "Content-Type": "application/json" });
    assertEq(JSON.parse(seen[0].opts.body), payload);
    assert(seen[0].opts.signal === ac.signal);
    assertEq(seen[0].opts.timeoutMs, 0, "legacy raw fetch had NO timeout");
  });

  await test("hessianPreview: error text = errors[].message join (NOT FastAPI detail); empty → 'HTTP <status>'", async () => {
    const { we } = build(() => { throw transportError(400, { errors: [{ message: "e1" }, { message: "e2" }], detail: "should-not-leak" }); });
    const err = await rejects(we.hessianPreview({}));
    assertEq(err.message, "e1; e2");
    const { we: w2 } = build(() => { throw transportError(400, { errors: [], detail: "nope" }); });
    const err2 = await rejects(w2.hessianPreview({}));
    assertEq(err2.message, "HTTP 400");
    const { we: w3 } = build(() => { throw transportError(500, undefined); });
    const err3 = await rejects(w3.hessianPreview({}));
    assertEq(err3.message, "HTTP 500");
  });

  await test("hessianPreview: pre-aborted signal → no adapter call, AbortError", async () => {
    const { we, seen } = build(() => { throw new Error("adapter must not be called"); });
    const ac = new AbortController();
    ac.abort();
    const err = await rejects(we.hessianPreview({}, { signal: ac.signal }));
    assertEq(err.name, "AbortError");
    assertEq(seen.length, 0);
  });

  // ── 6. batchConfigPreview — POST /api/v1/batch-optimize/config-preview ───
  await test("batchConfigPreview: literal legacy URL, {method: payload} body, signal identity, timeoutMs 0", async () => {
    const { we, seen } = build(() => ({ schema: "batch_optimize_preview_v1", roles: {} }), { baseUrl: "/api/v9" });
    const ac = new AbortController();
    await we.batchConfigPreview({ method: { max_cycles: 50 } }, { signal: ac.signal });
    assertEq(seen[0].url, "/api/v1/batch-optimize/config-preview", "literal legacy path — baseUrl NOT applied");
    assertEq(JSON.parse(seen[0].opts.body), { method: { max_cycles: 50 } });
    assert(seen[0].opts.signal === ac.signal);
    assertEq(seen[0].opts.timeoutMs, 0, "legacy raw fetch had NO timeout");
  });

  await test("batchConfigPreview: 'HTTP <status>' forced over flattened detail; AbortError name preserved", async () => {
    const { we } = build(() => { throw transportError(422, { detail: "flattened detail text" }); });
    const err = await rejects(we.batchConfigPreview({}));
    assertEq(err.message, "HTTP 422", "legacy site never read the error body");
    const { we: w2 } = build(() => {
      const e = new Error("aborted mid-flight");
      e.name = "AbortError";
      throw e;
    });
    const err2 = await rejects(w2.batchConfigPreview({}));
    assertEq(err2.name, "AbortError", "caller's silent-cancel filter keeps working");
  });

  await test("batchConfigPreview: pre-aborted signal → no adapter call, AbortError", async () => {
    const { we, seen } = build(() => { throw new Error("adapter must not be called"); });
    const ac = new AbortController();
    ac.abort();
    const err = await rejects(we.batchConfigPreview({}, { signal: ac.signal }));
    assertEq(err.name, "AbortError");
    assertEq(seen.length, 0);
  });

  // ── 7. validateMethod — POST {API_BASE}/validate-method ──────────────────
  await test("validateMethod: JSON body construction verbatim, timeoutMs 0", async () => {
    const { we, seen } = build(() => ({ valid: true, errors: [], warnings: [] }));
    await we.validateMethod({ schema_id: "quantum", levels: { l1: { engine: "orca" } } });
    assertEq(seen[0].url, "/api/v1/validate-method");
    assertEq(seen[0].opts.method, "POST");
    assertEq(seen[0].opts.headers, { "Content-Type": "application/json" });
    assertEq(JSON.parse(seen[0].opts.body), { schema_id: "quantum", levels: { l1: { engine: "orca" } } });
    assertEq(seen[0].opts.timeoutMs, 0, "legacy raw fetch had NO timeout");
  });

  await test("validateMethod: non-2xx JSON error body RESOLVES (legacy had no resp.ok check)", async () => {
    const { we } = build(() => { throw transportError(422, { detail: [{ loc: ["body"], msg: "bad levels" }] }); });
    const body = await we.validateMethod({ schema_id: "s", levels: {} });
    assertEq(body.detail.length, 1); // normalized_levels absent → legacy skipped the write-back
  });

  // ── init contract ─────────────────────────────────────────────────────────
  await test("uninitialized module rejects with a clear error (no silent fallback to global fetch)", async () => {
    const sandbox = makeSandbox({});
    loadScript("js/client/wizard-endpoints.js", sandbox);
    const err = await rejects(sandbox.ACPClient.wizardEndpoints.validateMethod({ schema_id: "s", levels: {} }));
    assert(/init\(\{ direct \}\) missing/.test(err.message), "got: " + err.message);
  });

  await test("v1 adapter stored but NEVER invoked (legacy sites never invalidated task-view metadata)", async () => {
    const { we } = build(() => ({}));
    await we.uploadStructure(new FormData());
    await we.parseStructure({ content: "x", format: "auto", filename: "" });
    await we.fetchStructureSource("x");
    await we.uploadNmrBruker(new FormData(), "p");
    await we.hessianPreview({});
    await we.batchConfigPreview({});
    await we.validateMethod({ schema_id: "s", levels: {} });
  });
};
