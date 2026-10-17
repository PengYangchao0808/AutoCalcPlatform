/*
 * Behavior tests for the wizard's candidateApi FE-07 migration (step 2).
 *
 * wizard.js is a 7700-line browser module whose top level touches the DOM,
 * so loading it wholesale in the sandbox is not practical. Instead this
 * test extracts the shipped `candidateApi` function source from wizard.js
 * at runtime (brace-matched slice) and evaluates it against a stubbed
 * window.ACPClient.transport — pinning the real production source:
 *   - pure delegation to window.ACPClient.transport.request("/api/v2"+path)
 *     with timeoutMs 0 (legacy no-budget semantics; no 8s default) and the
 *     global fetch never called;
 *   - caller opts forwarded untouched (no pollution of the caller object);
 *   - legacy error-message adapters per-case identical to pre-migration:
 *     string detail verbatim, 422 arrays as raw JSON.stringify(detail),
 *     detail-less bodies as JSON.stringify(body), non-JSON error bodies as
 *     "{}" (the legacy `.catch(() => ({}))` parse fallback);
 *   - err.status preserved, err.body additive, non-HTTP errors untouched.
 */
"use strict";

const fs = require("fs");
const path = require("path");

function extractCandidateApiSource() {
  const file = path.resolve(__dirname, "..", "workbench", "features", "wizard.js");
  const src = fs.readFileSync(file, "utf8");
  const marker = "async function candidateApi(path, options) {";
  const start = src.indexOf(marker);
  if (start < 0) throw new Error("candidateApi function missing from wizard.js");
  let depth = 0;
  for (let i = start + marker.length - 1; i < src.length; i++) {
    const ch = src[i];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error("candidateApi closing brace not found");
}

function makeTransportError(status, body) {
  const err = new Error("transport-generated");
  if (status != null) err.status = status;
  if (body !== undefined) err.body = body;
  return err;
}

module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox } = h;
  const fnSource = extractCandidateApiSource();

  function build(transportImpl) {
    const sandbox = makeSandbox({
      // candidateApi must NEVER fall back to the global fetch after the
      // FE-07 migration — any call here is a delegation failure.
      fetch: () => { throw new Error("global fetch must not be called by candidateApi"); },
    });
    sandbox.ACPClient = {
      transport: {
        request: function (url, opts) {
          return Promise.resolve().then(() => transportImpl(url, opts));
        },
      },
    };
    // Evaluate the extracted source in the sandbox and bind it as a value.
    const vm = require("vm");
    const factory = vm.runInContext(
      "(" + fnSource + ")",
      vm.createContext(sandbox),
      { filename: "wizard.js#candidateApi" }
    );
    return { candidateApi: factory, sandbox };
  }

  await test("extracted candidateApi is the migrated delegation source", () => {
    assert(fnSource.includes("ACPClient.transport.request"), "must delegate to ACPClient.transport.request");
    assert(fnSource.includes('"/api/v2" + path'), "must own the /api/v2 prefix");
    assert(fnSource.includes("timeoutMs = 0"), "must pass timeoutMs 0 (no-budget legacy semantics)");
  });

  await test("delegates to transport with /api/v2 prefix, timeoutMs 0; global fetch never called", async () => {
    let seen = null;
    const { candidateApi } = build((url, opts) => {
      seen = { url, opts: Object.assign({}, opts) };
      return { candidate: { uid: "u1" } };
    });
    const out = await candidateApi("/structure-sources/ss_x/candidate");
    assertEq(out.candidate.uid, "u1");
    assertEq(seen.url, "/api/v2/structure-sources/ss_x/candidate");
    assertEq(seen.opts.timeoutMs, 0, "explicit no-timeout budget forwarded");
    assert(seen.opts.fetchImpl === undefined, "no fetchImpl seam introduced");
  });

  await test("204 / non-JSON 2xx body: legacy {} parse fallback restored", async () => {
    const { candidateApi: api204 } = build(() => "");
    assertEq(await api204("/x"), {}, "204 empty body must resolve to {} like legacy json().catch(()=>({}))");
    const { candidateApi: apiText } = build(() => "plain text");
    assertEq(await apiText("/x"), {}, "non-JSON 2xx text must resolve to {} (legacy fallback)");
  });

  await test("caller opts forwarded untouched; caller object not polluted", async () => {
    let seen = null;
    const { candidateApi } = build((url, opts) => { seen = opts; return {}; });
    const caller = Object.freeze({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: '{"usage_status":"candidate"}',
    });
    await candidateApi("/structure-sources/ss_x/candidate/status", caller);
    assertEq(seen.method, "POST");
    assertEq(seen.headers, { "Content-Type": "application/json" });
    assertEq(seen.body, '{"usage_status":"candidate"}');
    assertEq(seen.timeoutMs, 0);
    assertEq(Object.keys(caller).sort(), ["body", "headers", "method"],
      "caller options object must not gain timeoutMs/mutations");
    assert(Object.isFrozen(caller) && seen !== caller, "transport opts must be a merged copy");
  });

  await test("string detail: message verbatim, err.status + err.body preserved", async () => {
    const { candidateApi } = build(() => { throw makeTransportError(409, { detail: "name revision conflict" }); });
    let err = null;
    await candidateApi("/structure-sources/ss_x").catch((e) => { err = e; });
    assertEq(err.message, "name revision conflict");
    assertEq(err.status, 409);
    assertEq(err.body, { detail: "name revision conflict" }, "err.body additive contract kept");
  });

  await test("422 validation array: legacy raw JSON.stringify(detail) message (not loc-flattened)", async () => {
    const detail = [
      { loc: ["body", "usage_status"], msg: "unexpected value", type: "value_error" },
    ];
    const { candidateApi } = build(() => { throw makeTransportError(422, { detail }); });
    let err = null;
    await candidateApi("/x").catch((e) => { err = e; });
    assertEq(err.message, JSON.stringify(detail), "legacy message kept verbatim");
    assertEq(err.status, 422);
  });

  await test("object detail: legacy JSON.stringify(detail) message", async () => {
    const { candidateApi } = build(() => { throw makeTransportError(409, { detail: { current: { metadata_revision: 7 } } }); });
    let err = null;
    await candidateApi("/x").catch((e) => { err = e; });
    assertEq(err.message, '{"current":{"metadata_revision":7}}');
  });

  await test("detail-less error body: legacy JSON.stringify(body) message (not 'HTTP N')", async () => {
    const { candidateApi } = build(() => { throw makeTransportError(503, { nope: 1 }); });
    let err = null;
    await candidateApi("/x").catch((e) => { err = e; });
    assertEq(err.message, '{"nope":1}');
  });

  await test("non-JSON error body: legacy parse-fallback message '{}' (not 'HTTP N')", async () => {
    const { candidateApi } = build(() => { throw makeTransportError(500, undefined); });
    let err = null;
    await candidateApi("/x").catch((e) => { err = e; });
    assertEq(err.message, "{}", "legacy .catch(() => ({})) fallback mirrored");
    assertEq(err.status, 500);
  });

  await test("non-HTTP errors (caller abort etc.) rethrown untouched", async () => {
    const abortErr = new Error("The operation was aborted");
    abortErr.name = "AbortError";
    const { candidateApi } = build(() => { throw abortErr; });
    let err = null;
    await candidateApi("/x").catch((e) => { err = e; });
    assert(err === abortErr, "same error object rethrown");
    assertEq(err.name, "AbortError", "silent-cancel filters keep working");
  });
};
