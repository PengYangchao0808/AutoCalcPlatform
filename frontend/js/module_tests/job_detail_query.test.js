/* R1 behavior tests: ACPClient.jobDetail resource query (detail/files). */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function setup(overrides) {
    const sandbox = makeSandbox(overrides);
    loadScript("js/client/resource-store.js", sandbox);
    loadScript("js/client/job-detail-query.js", sandbox);
    return sandbox;
  }

  function statusErr(status, message) {
    const err = new Error(message || ("HTTP " + status));
    err.status = status;
    return err;
  }

  await test("404 pins the legacy endpoint: exact legacy wrap, onLegacyPin fired once, straight to fallback after", async () => {
    const sandbox = setup();
    const jd = sandbox.window.ACPClient.jobDetail;
    let detailCalls = 0, legacyCalls = 0, pinCalls = 0;
    jd.init({
      v1: (path) => {
        if (path.endsWith("/detail")) {
          detailCalls++;
          return Promise.reject(statusErr(404, "Not Found"));
        }
        legacyCalls++;
        return Promise.resolve({ id: "job-1", status: "running" });
      },
      onLegacyPin: () => { pinCalls++; },
    });

    const detail = await jd.fetchDetail("job-1");
    assertEq(detailCalls, 1);
    assertEq(legacyCalls, 1);
    // Legacy wrap ported verbatim from the former page inline fallback.
    assertEq(detail, {
      job: { id: "job-1", status: "running" },
      stages: null,
      artifacts_summary: null,
      error_detail: null,
      disk_state: null,
      recovery: null,
      legacy: true,
    });
    assertEq(pinCalls, 1, "onLegacyPin fired exactly once at pin time");
    assertEq(jd.endpointMode(), "legacy");
    assert(jd.isEndpointMissing());

    // Once pinned, /detail is never retried (different job id, fresh store key).
    await jd.fetchDetail("job-2");
    assertEq(detailCalls, 1, "pinned endpoint is not retried");
    assertEq(pinCalls, 1, "pin callback not re-fired");
  });

  await test("405 also pins the legacy endpoint", async () => {
    const sandbox = setup();
    const jd = sandbox.window.ACPClient.jobDetail;
    jd.init({
      v1: (path) => path.endsWith("/detail")
        ? Promise.reject(statusErr(405, "Method Not Allowed"))
        : Promise.resolve({ id: "job-3" }),
    });
    const detail = await jd.fetchDetail("job-3");
    assert(detail.legacy === true);
    assertEq(jd.endpointMode(), "legacy");
  });

  await test("401/409/422/500 and network errors surface with status and never pin", async () => {
    for (const status of [401, 409, 422, 500]) {
      const sandbox = setup();
      const jd = sandbox.window.ACPClient.jobDetail;
      let calls = 0;
      jd.init({ v1: () => { calls++; return Promise.reject(statusErr(status)); } });
      let caught = null;
      try { await jd.fetchDetail("job-x-" + status); } catch (e) { caught = e; }
      assert(caught && caught.status === status, "status " + status + " surfaces");
      assertEq(calls, 1);
      assertEq(jd.endpointMode(), "detail", "status " + status + " must not pin");
      // Not cached as a poison entry: a retry issues a fresh request.
      await jd.fetchDetail("job-x-" + status).catch(() => {});
      assertEq(calls, 2, "error entry is retryable");
    }
    const sandbox = setup();
    const jd = sandbox.window.ACPClient.jobDetail;
    let calls = 0;
    jd.init({ v1: () => { calls++; return Promise.reject(new TypeError("Failed to fetch")); } });
    let caught = null;
    try { await jd.fetchDetail("job-net"); } catch (e) { caught = e; }
    assert(caught && caught.message === "Failed to fetch", "network error surfaces");
    assertEq(jd.endpointMode(), "detail", "network error must not pin");
    assertEq(calls, 1);
  });

  await test("concurrent fetchDetail for the same job merges into one loader call", async () => {
    const sandbox = setup();
    const jd = sandbox.window.ACPClient.jobDetail;
    let calls = 0;
    jd.init({
      v1: () => {
        calls++;
        return Promise.resolve({ job: { id: "job-d", status: "running" } });
      },
    });
    const [d1, d2] = await Promise.all([jd.fetchDetail("job-d"), jd.fetchDetail("job-d")]);
    assertEq(calls, 1, "in-flight dedupe");
    assertEq(d1.job.id, "job-d");
    assertEq(d2.job.id, "job-d");
  });

  await test("invalidateDetail(jobId) resets its snapshot; invalidateDetail(null) drops the detail group but not files", async () => {
    const sandbox = setup();
    const jd = sandbox.window.ACPClient.jobDetail;
    jd.init({
      v1: (path) => Promise.resolve(path.endsWith("/files")
        ? { files: [] }
        : { job: { id: path.includes("job-i") ? "job-i" : "job-j" } }),
    });
    await jd.fetchDetail("job-i");
    await jd.fetchDetail("job-j");
    await jd.fetchFiles("job-i", false);
    assertEq(jd.snapshotDetail("job-i").status, "fresh");
    assertEq(jd.snapshotDetail("job-j").status, "fresh");
    jd.invalidateDetail("job-i");
    assertEq(jd.snapshotDetail("job-i").status, undefined, "targeted detail drop");
    assertEq(jd.snapshotDetail("job-j").status, "fresh", "other entries survive targeted invalidation");
    jd.invalidateDetail(null);
    assertEq(jd.snapshotDetail("job-j").status, undefined, "prefix drop clears the whole detail group");
    assertEq(jd.snapshotFiles("job-i", false).status, "fresh", "files group is not touched by detail invalidation");
    // After invalidation the next query reloads and republishes.
    await jd.fetchDetail("job-j");
    assertEq(jd.snapshotDetail("job-j").status, "fresh");
  });

  await test("abort signal semantics: pre-aborted rejects without a request; mid-flight abort rejects AbortError and frees the entry", async () => {
    const sandbox = setup();
    const jd = sandbox.window.ACPClient.jobDetail;
    let calls = 0;
    jd.init({
      v1: () => {
        calls++;
        return new Promise(() => {}); // never settles on its own
      },
    });
    // Pre-aborted signal: no loader call at all.
    const pre = new sandbox.window.AbortController();
    pre.abort();
    let caught = null;
    try { await jd.fetchDetail("job-a", { signal: pre.signal }); } catch (e) { caught = e; }
    assert(caught && caught.name === "AbortError", "pre-aborted rejects with AbortError");
    assertEq(calls, 0, "pre-aborted signal never reaches the loader");
    // Mid-flight abort: caller promise rejects, store retires the entry.
    const mid = new sandbox.window.AbortController();
    const pending = jd.fetchDetail("job-b", { signal: mid.signal });
    await new Promise((r) => setTimeout(r, 0));
    mid.abort();
    caught = null;
    try { await pending; } catch (e) { caught = e; }
    assert(caught && caught.name === "AbortError", "mid-flight abort rejects with AbortError");
    assertEq(calls, 1);
    // Entry was retired: the next query starts a fresh request.
    const sandbox2 = setup();
    const jd2 = sandbox2.window.ACPClient.jobDetail;
    let calls2 = 0;
    jd2.init({ v1: () => { calls2++; return Promise.resolve({ job: { id: "job-c" } }); } });
    const ctrl = new sandbox2.window.AbortController();
    const p = jd2.fetchDetail("job-c", { signal: ctrl.signal });
    await new Promise((r) => setTimeout(r, 0));
    ctrl.abort();
    await p.catch(() => {});
    await jd2.fetchDetail("job-c");
    assertEq(calls2, 2, "aborted entry is not served from cache afterwards");
  });

  await test("200 without a job object falls back to the legacy record for that call only — no pin", async () => {
    const sandbox = setup();
    const jd = sandbox.window.ACPClient.jobDetail;
    const seen = [];
    jd.init({
      v1: (path) => {
        seen.push(path);
        return Promise.resolve(path.endsWith("/detail") ? { unexpected: "shape" } : { id: "job-m" });
      },
    });
    const detail = await jd.fetchDetail("job-m");
    assert(detail.legacy === true, "legacy record returned");
    assertEq(detail.job.id, "job-m");
    assertEq(jd.endpointMode(), "detail", "malformed 200 must not pin");
    assertEq(seen.length, 2, "both /detail and legacy record fetched once");
    // Next call retries /detail (no pin was set).
    await jd.fetchDetail("job-m2");
    assert(seen.filter((p) => p.endsWith("/detail")).length === 2, "/detail retried on the next job");
  });

  await test("fetchFiles: local passthrough with pinned default; remote remaps to the local shape", async () => {
    const sandbox = setup();
    const jd = sandbox.window.ACPClient.jobDetail;
    const seen = [];
    jd.init({
      v1: (path) => { seen.push(path); return Promise.resolve({ files: [{ path: "RESULT/x.out", size: 3, is_dir: false }], pinned: ["RESULT/summary"] }); },
      v1Remote: (path) => {
        seen.push(path);
        return Promise.resolve({ files: [{ name: "RESULT", size: 1, mtime: "2026-10-17", is_dir: true }] });
      },
    });
    const local = await jd.fetchFiles("job-f", false);
    assertEq(local, { files: [{ path: "RESULT/x.out", size: 3, is_dir: false }], pinned: ["RESULT/summary"], is_remote: false });
    const remote = await jd.fetchFiles("job-f", true);
    assertEq(remote.files, [{ path: "RESULT", name: "RESULT", size: 1, modified: "2026-10-17", is_dir: true }]);
    assert(remote.pinned.length === 0 && remote.is_remote === true, "remote pinned defaults to empty");
    assert(seen.some((p) => p.endsWith("/remote-files")) && seen.some((p) => p.endsWith("/files")));
  });

  await test("missing adapter or store rejects with a clear configuration error", async () => {
    const sandbox = setup();
    const jd = sandbox.window.ACPClient.jobDetail;
    let caught = null;
    try { await jd.fetchDetail("job-z"); } catch (e) { caught = e; }
    assert(caught && /init\(\{v1\}\) not configured/.test(caught.message), "unconfigured v1 surfaces");
    jd.init({ v1: () => Promise.resolve({ job: {} }) });
    caught = null;
    try { await jd.fetchDetail(""); } catch (e) { caught = e; }
    assert(caught && /valid jobId/.test(caught.message), "empty jobId rejected");
  });
};
