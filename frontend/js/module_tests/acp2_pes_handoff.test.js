/* ACP 3.0 module test — pes-handoff (Track A / A-51 · PES/scan → BatchOptimize).
 *
 * Loads the module as the classic script the Shell loader consumes
 * (window.ACPNextModules["pes-handoff"] = { descriptor, factory, _internals })
 * inside the zero-dependency node vm harness with a fake DOM. Every request goes
 * through an injected request seam, so no test can reach the network (the
 * sandbox has no fetch/XMLHttpRequest at all).
 *
 * Pinned contracts:
 *  1. exactly one ACPNextModules key, Shell-valid descriptor, no extra global;
 *  2. four states (empty/loading/ready/error) + retry; a missing read endpoint
 *     stops the panel explicitly (no invented fallback route);
 *  3. candidates come only from server records (`pes/review` selected rows or
 *     `frame-candidates`); rank/label/energy/provenance are server fields and
 *     the module never sorts or computes a science rank;
 *  4. the BatchOptimize hand-off uses the existing preview/submit routes:
 *     batch-optimize config-preview, POST /preflight and POST /jobs (one task
 *     per candidate because the server pins single_flat); no other POST exists;
 *  5. explicit confirmation: a preview never submits; only the confirm dialog
 *     emits POST /jobs, each with an Idempotency-Key + preflight fingerprint,
 *     and the receipt is shown per candidate;
 *  6. submit failures never become a fake success (partial receipts stay
 *     visible, transport failures are "unconfirmed");
 *  7. generation/dispose guards drop late results; dispose is idempotent;
 *  8. source pins forbid direct network/storage/path building/viewer coupling.
 */
"use strict";

const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const MODULE_PATH = "js/modules/pes-handoff/module.js";
  const API = "/api/v1";
  const JOB = (id) => API + "/jobs/" + encodeURIComponent(id);
  const CONFIG_PREVIEW = API + "/batch-optimize/config-preview";
  const PREFLIGHT = API + "/preflight";
  const SUBMIT = API + "/jobs";
  const METHOD_CATALOG = API + "/method-catalog";
  const TS_ID = "pes_ts_frame_005";
  const INT_ID = "pes_int_frame_008";
  const DIGEST = {};
  DIGEST[TS_ID] = "sha256:" + "a".repeat(64);
  DIGEST[INT_ID] = "sha256:" + "b".repeat(64);
  const DIGEST_SCAN = "sha256:" + "c".repeat(64);

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function flush(n = 16) {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await tick();
    for (let i = 0; i < 10; i++) await Promise.resolve();
  }

  function httpError(status, detail) {
    const error = new Error(typeof detail === "string" ? detail : "HTTP " + status);
    error.status = status;
    error.body = { detail: detail != null ? detail : "HTTP " + status };
    return error;
  }

  // ------------------------------------------------------------ fake DOM --

  function makeDom() {
    const docListeners = new Map();

    function makeEl(tag) {
      const listeners = new Map();
      return {
        tagName: String(tag).toUpperCase(),
        children: [],
        parentNode: null,
        className: "",
        textContent: "",
        hidden: false,
        disabled: false,
        value: "",
        checked: false,
        type: "",
        attrs: {},
        setAttribute(key, value) { this.attrs[key] = String(value); },
        getAttribute(key) { return key in this.attrs ? this.attrs[key] : null; },
        removeAttribute(key) { delete this.attrs[key]; },
        appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
        removeChild(child) {
          const index = this.children.indexOf(child);
          if (index >= 0) this.children.splice(index, 1);
          child.parentNode = null;
          return child;
        },
        replaceChildren(...nodes) {
          this.children.forEach((child) => { child.parentNode = null; });
          this.children = nodes.slice();
          nodes.forEach((node) => { node.parentNode = this; });
        },
        addEventListener(type, fn) {
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type).add(fn);
        },
        removeEventListener(type, fn) {
          if (listeners.has(type)) listeners.get(type).delete(fn);
        },
        fire(type, event) {
          const set = listeners.get(type);
          if (set) [...set].forEach((fn) => fn(Object.assign({ type, target: this }, event || {})));
        }
      };
    }

    const document = {
      createElement: makeEl,
      addEventListener(type, fn) {
        if (!docListeners.has(type)) docListeners.set(type, new Set());
        docListeners.get(type).add(fn);
      },
      removeEventListener(type, fn) {
        if (docListeners.has(type)) docListeners.get(type).delete(fn);
      },
      documentElement: makeEl("html"),
      activeDocListeners() {
        let count = 0;
        docListeners.forEach((set) => { count += set.size; });
        return count;
      }
    };
    return { document };
  }

  function walk(node, out = []) {
    (node.children || []).forEach((child) => { out.push(child); walk(child, out); });
    return out;
  }

  function find(root, attr, value) {
    return walk(root).find((node) => node.getAttribute && node.getAttribute(attr) === value) || null;
  }

  function findAll(root, attr, value) {
    return walk(root).filter((node) => node.getAttribute && node.getAttribute(attr) === value);
  }

  function withAttr(root, attr) {
    return walk(root).filter((node) => node.getAttribute && node.getAttribute(attr) != null);
  }

  function subtreeText(node) {
    let text = node.textContent || "";
    (node.children || []).forEach((child) => { text += " " + subtreeText(child); });
    return text;
  }

  // ------------------------------------------------------------ fixtures --

  function jobPayload(id, workflow) {
    return {
      id: id,
      status: "COMPLETED",
      attempt: 1,
      project_id: "proj-1",
      spec: {
        workflow: workflow || "PESsearch",
        task_name: "pes-task",
        molecule_name: "CCO",
        input: { charge: 0, multiplicity: 1 }
      }
    };
  }

  function reviewPayload(overrides) {
    return Object.assign({
      job_id: "pes-1",
      status: "confirmed",
      review: {
        schema_version: "pes_review_v1",
        job_id: "pes-1",
        status: "confirmed",
        confirmed_at: "2026-10-31T12:00:00+08:00",
        revision: 2,
        profile_sha256: "abc",
        source: {
          task_status: "COMPLETED",
          frame_source: "final_profile",
          scan_complete: true,
          data_source: "RESULT/pes_search/pes_profile.json"
        },
        selected: [
          {
            candidate_id: TS_ID,
            frame_index: 5,
            role: "TS",
            name: "frame_005__TAG_TS",
            selection_source: "manual",
            structure_path: "structures/" + TS_ID + ".xyz",
            frame_converged: true,
            rank: 1
          },
          {
            candidate_id: INT_ID,
            frame_index: 8,
            role: "INT",
            name: "frame_008__TAG_INT",
            selection_source: "manual",
            structure_path: "structures/" + INT_ID + ".xyz",
            frame_converged: true
          }
        ]
      },
      backups: []
    }, overrides || {});
  }

  function energyPayload(overrides) {
    return Object.assign({
      job_id: "pes-1",
      view_type: "scan",
      default_series: "relative_energy",
      series: [{
        id: "relative_energy", label: "相对能量", unit: "kcal/mol",
        values: [0.0, 12.1, 3.2], x_values: [1.0, 1.4, 1.8]
      }],
      nodes: [
        { id: "frame_0", frame_index: 0, label: "Frame 1", energy: 0.0, status: "converged" },
        { id: "frame_5", frame_index: 5, label: "Frame 6", energy: 12.1, status: "converged" },
        { id: "frame_8", frame_index: 8, label: "Frame 9", energy: 3.2, status: "converged" }
      ],
      annotations: [
        { candidate_id: TS_ID, frame_index: 5, y: 12.1, selection_source: "manual", saved: true },
        { candidate_id: INT_ID, frame_index: 8, y: 3.2, selection_source: "manual", saved: true }
      ],
      provenance: { jobId: "pes-1" }
    }, overrides || {});
  }

  function methodCatalogPayload() {
    return {
      method_schemas: {
        batch_optimize: {
          profiles: [
            { profile_id: "opt_only", label: "Optimization Only" },
            { profile_id: "opt_freq", label: "Optimization + Frequency" },
            { profile_id: "opt_freq_sp", label: "Optimization + Frequency + SP" },
            { profile_id: "opt_freq_sp_thermo", label: "Optimization + Frequency + SP + Thermo" }
          ]
        }
      }
    };
  }

  function frameCandidatesPayload() {
    return {
      job_id: "scan-1",
      revision: 3,
      candidates: [
        {
          candidate_id: "scan_frame_2",
          view_type: "scan",
          frame_index: 2,
          role: "INT",
          name: "scan frame 2",
          structure_path: "structures/scan_frame_2.xyz",
          saved_at: "2026-10-31T10:00:00+08:00",
          role_index: 1,
          display_label: "INT1",
          created_seq: 1
        }
      ]
    };
  }

  function scanEnergyPayload() {
    return {
      job_id: "scan-1",
      view_type: "scan",
      default_series: "scan_energy",
      series: [{ id: "scan_energy", label: "扫描能量", unit: "Eh", values: [-154.1], x_values: [1.0] }],
      nodes: [{ id: "frame_2", frame_index: 2, label: "Frame 3", energy: -154.1, status: "converged" }],
      annotations: [],
      provenance: {}
    };
  }

  function xyzText(candidateId, tag) {
    const role = tag || "TS";
    return "3\n" +
      "TAG: " + role + " | candidate_id=" + candidateId + " | source=PESsearch | frame=005 | selection_source=manual\n" +
      "C 0.000000 0.000000 0.000000\n" +
      "O 1.200000 0.000000 0.000000\n" +
      "H -0.300000 0.900000 0.000000\n";
  }

  function configPreviewPayload() {
    return {
      schema: "batch_optimize_preview_v1",
      config_key: '{"optimization_method":"r2SCAN-3c"}',
      roles: {
        int: { effective: { opt_convergence: "Tight", scf_max_iter: 300 }, sources: {} },
        ts: { effective: { opt_convergence: "Tight", opt_trust_radius: 0.3 }, sources: {} }
      },
      orca_summary: { int: ["TightOpt"], ts: ["TightOpt", "Calc_Hess"] }
    };
  }

  function preflightPayload(candidateId, overrides) {
    return Object.assign({
      schema_version: 1,
      feasible: true,
      reasons: [],
      advisories: [],
      plan_digest: DIGEST[candidateId] || DIGEST_SCAN,
      plan_summary: { workflow: "BatchOptimize", steps: 1 },
      would_create_task: false,
      would_reserve: false
    }, overrides || {});
  }

  function receiptPayload(candidateId, key) {
    const ts = candidateId === TS_ID;
    return {
      job_id: ts ? "batch-ts-1" : (candidateId === INT_ID ? "batch-int-2" : "batch-scan-3"),
      status: "QUEUED",
      workflow: "BatchOptimize",
      attempt: 1,
      plan_digest: DIGEST[candidateId] || DIGEST_SCAN,
      idempotent_replay: false,
      idempotency_key: key
    };
  }

  // ------------------------------------------------------------- client --

  function defaultResponder(pathname, opts) {
    const method = (opts && opts.method) || "GET";
    if (method === "GET") {
      if (pathname === JOB("pes-1")) return jobPayload("pes-1", "PESsearch");
      if (pathname === JOB("pes-1") + "/pes/review") return reviewPayload();
      if (pathname === JOB("pes-1") + "/energy-graph") return energyPayload();
      if (pathname === METHOD_CATALOG) return methodCatalogPayload();
      if (pathname === JOB("pes-1") + "/files/RESULT/structures/" + TS_ID + ".xyz") return xyzText(TS_ID, "TS");
      if (pathname === JOB("pes-1") + "/files/RESULT/structures/" + INT_ID + ".xyz") return xyzText(INT_ID, "INT");
      if (pathname === JOB("scan-1")) return jobPayload("scan-1", "scan");
      if (pathname === JOB("scan-1") + "/frame-candidates") return frameCandidatesPayload();
      if (pathname === JOB("scan-1") + "/energy-graph") return scanEnergyPayload();
      if (pathname === JOB("scan-1") + "/files/RESULT/structures/scan_frame_2.xyz") {
        return xyzText("scan_frame_2", "INT");
      }
    }
    if (method === "POST") {
      if (pathname === CONFIG_PREVIEW) return configPreviewPayload();
      if (pathname === PREFLIGHT) {
        const body = JSON.parse(opts.body);
        return preflightPayload(body.input.selected_ids[0]);
      }
      if (pathname === SUBMIT) {
        const body = JSON.parse(opts.body);
        return receiptPayload(body.input.selected_ids[0], (opts.headers || {})["Idempotency-Key"]);
      }
    }
    throw httpError(404, "unexpected " + method + " " + pathname);
  }

  function makeClient(responder) {
    const calls = [];
    function request(pathname, opts) {
      calls.push({ path: pathname, opts: opts || {} });
      try {
        return Promise.resolve((responder || defaultResponder)(pathname, opts, calls.length));
      } catch (error) {
        return Promise.reject(error);
      }
    }
    return { calls, request };
  }

  function makeContext(jobId) {
    const listeners = new Set();
    let snapshot = {
      projectId: "proj-1",
      taskRef: jobId ? { jobId: jobId, attempt: 1 } : null,
      structureRef: null,
      networkNodeId: null,
      revision: 1
    };
    return {
      getSnapshot() { return snapshot; },
      subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
      setFocus(patch) {
        snapshot = Object.assign({}, snapshot, patch, { revision: snapshot.revision + 1 });
        listeners.forEach((fn) => fn(snapshot));
      },
      listenerCount() { return listeners.size; }
    };
  }

  function setup(options) {
    options = options || {};
    const dom = makeDom();
    const sandbox = makeSandbox({});
    loadScript(MODULE_PATH, sandbox);
    const mod = sandbox.ACPNextModules["pes-handoff"];
    const client = options.client || makeClient(options.responder);
    const context = options.context === undefined ? null : options.context;
    const host = dom.document.createElement("div");
    const deps = { doc: dom.document, request: client.request };
    if (context) deps.context = context;
    Object.assign(deps, options.deps || {});
    const instance = mod.factory(deps);
    return { dom, sandbox, mod, client, context, host, deps, instance };
  }

  async function mount(ctx) {
    await ctx.instance.mount(ctx.host, { context: ctx.context });
    await flush();
  }

  function root(ctx) {
    return ctx.host.children[0] || null;
  }

  function stateOf(ctx) {
    return root(ctx).getAttribute("data-acp-module-state");
  }

  function attr(ctx, name) {
    return root(ctx).getAttribute(name);
  }

  function field(ctx, role) {
    return find(ctx.host, "data-role", role);
  }

  function action(ctx, name) {
    return find(ctx.host, "data-action", name);
  }

  function candidateRows(ctx) {
    return withAttr(ctx.host, "data-candidate-id").filter((node) =>
      node.getAttribute("data-candidate-role") != null);
  }

  function postCalls(ctx) {
    return ctx.client.calls.filter((call) => call.opts && call.opts.method === "POST");
  }

  function getPaths(ctx) {
    return ctx.client.calls.filter((call) => !call.opts || call.opts.method !== "POST")
      .map((call) => call.path);
  }

  function parseBody(call) {
    return JSON.parse(call.opts.body);
  }

  function loadViaUi(ctx, id) {
    const input = field(ctx, "job-id");
    input.value = id;
    action(ctx, "load-job").fire("click");
    return flush();
  }

  async function loadReady(options) {
    const ctx = setup(options);
    await mount(ctx);
    await loadViaUi(ctx, "pes-1");
    return ctx;
  }

  async function previewReady(options) {
    const ctx = await loadReady(options);
    action(ctx, "preview").fire("click");
    await flush();
    return ctx;
  }

  async function openConfirm(ctx) {
    action(ctx, "request-confirm").fire("click");
    await flush(2);
  }

  async function submitConfirmed(ctx) {
    action(ctx, "confirm-submit").fire("click");
    await flush();
  }

  // ------------------------------------------------------------- tests --

  await test("module: one ACPNextModules key, Shell-valid descriptor, no legacy global", async () => {
    const sandbox = makeSandbox({});
    loadScript("js/shell/module-registry.js", sandbox);
    loadScript(MODULE_PATH, sandbox);
    const mod = sandbox.ACPNextModules["pes-handoff"];
    assertEq(Object.keys(sandbox.ACPNextModules), ["pes-handoff"], "exactly one module key");
    assertEq(typeof mod.factory, "function");
    assertEq(sandbox.ACPShell.registry.validateDescriptor(mod.descriptor), null,
      "descriptor must pass the Shell registry validator");
    assertEq(mod.descriptor.id, "pes-handoff");
    assertEq(mod.descriptor.interfaceVersion, 1);
    assertEq(mod.descriptor.version, "0.1.0");
    assertEq(mod.descriptor.hosts, ["acp-web"]);
    assertEq(mod.descriptor.binds, ["taskRef"]);
    assertEq(mod.descriptor.provides, ["batch.handoff"]);
    assertEq(mod.descriptor.supportedInstancing, "single");
    assertEq(mod.descriptor.error.titleKey, "next.handoff.errorTitle");
    assert(!("pesHandoffState" in sandbox), "no legacy page global");
    assertEq(typeof mod._internals.buildHandoffBodies, "function");
    assertEq(typeof mod._internals.validateXyz, "function");
    assertEq(typeof mod._internals.i18n, "object");
    const instance = mod.factory({ doc: makeDom().document });
    ["mount", "updateContext", "suspend", "resume", "dispose"].forEach((name) => {
      assertEq(typeof instance[name], "function", "ModuleInstance." + name);
    });
  });

  await test("mount: empty state, zero requests, preview/confirm disabled", async () => {
    const ctx = setup();
    await mount(ctx);
    assertEq(root(ctx).className, "acp-handoff");
    assertEq(stateOf(ctx), "empty");
    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(attr(ctx, "data-acp-preview-state"), "idle");
    assertEq(attr(ctx, "data-acp-handoff-state"), "idle");
    assertEq(attr(ctx, "data-acp-candidate-count"), "0");
    assertEq(attr(ctx, "data-acp-receipt-count"), "0");
    assertEq(ctx.client.calls.length, 0, "mount performs no network call");
    assertEq(field(ctx, "empty-state").hidden, false);
    assert(subtreeText(field(ctx, "empty-state")).indexOf("尚未载入候选") !== -1, true);
    assertEq(action(ctx, "preview").disabled, true);
    assertEq(action(ctx, "request-confirm").disabled, true);
    assertEq(field(ctx, "confirm-box").hidden, true, "confirmation is closed by default");
    assert(subtreeText(root(ctx)).indexOf("single_flat") !== -1, true, "layout limit disclosed");
    assert(subtreeText(root(ctx)).indexOf("不计算科学排名") !== -1, true, "no-rank disclosure");
  });

  await test("manual load: loading -> ready with exact read paths and candidate rows", async () => {
    const jobDeferred = deferred();
    let jobCalls = 0;
    const client = makeClient((pathname, opts) => {
      if (pathname === JOB("pes-1")) {
        jobCalls += 1;
        if (jobCalls === 1) return jobDeferred.promise;
      }
      return defaultResponder(pathname, opts);
    });
    const ctx = setup({ client });
    await mount(ctx);
    const input = field(ctx, "job-id");
    input.value = "pes-1";
    action(ctx, "load-job").fire("click");
    await flush(2);
    assertEq(stateOf(ctx), "loading");
    assertEq(attr(ctx, "data-acp-busy"), "load");
    assertEq(field(ctx, "overlay").hidden, false);

    jobDeferred.resolve(jobPayload("pes-1", "PESsearch"));
    await flush();

    assertEq(stateOf(ctx), "ready");
    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(getPaths(ctx), [
      JOB("pes-1"),
      METHOD_CATALOG,
      JOB("pes-1") + "/pes/review",
      JOB("pes-1") + "/energy-graph"
    ]);
    assertEq(attr(ctx, "data-acp-candidate-count"), "2");
    assertEq(attr(ctx, "data-acp-selection-count"), "2");
    assertEq(attr(ctx, "data-acp-config-state"), "ready");
    assertEq(attr(ctx, "data-acp-profile"), "opt_freq");
    assertEq(subtreeText(field(ctx, "summary-job")), "pes-1");
    assertEq(subtreeText(field(ctx, "summary-source")).indexOf("PES 人工确认") !== -1, true);
    assertEq(subtreeText(field(ctx, "summary-review")).indexOf("revision=2") !== -1, true);

    const rows = candidateRows(ctx);
    assertEq(rows.map((row) => row.getAttribute("data-candidate-id")), [TS_ID, INT_ID],
      "server order preserved");
    assertEq(rows[0].getAttribute("data-candidate-role"), "ts");
    assertEq(find(rows[0], "data-role", "candidate-frame").textContent, "5");
    assertEq(find(rows[0], "data-role", "candidate-energy").textContent,
      "相对能量 12.1000 kcal/mol");
    assertEq(find(rows[0], "data-role", "candidate-provenance").textContent.indexOf(TS_ID) === -1,
      true, "provenance text carries source/time, not a fabricated path");
    const select = field(ctx, "profile");
    assertEq(select.children.length, 5, "placeholder + four server profiles");
    assertEq(select.value, "opt_freq", "default profile from server catalog");
    ctx.instance.dispose();
  });

  await test("candidate ranking: only server fields are shown; missing rank is explicit", async () => {
    const ctx = await loadReady();
    const rows = candidateRows(ctx);
    const tsRank = find(rows[0], "data-role", "candidate-rank");
    assertEq(tsRank.getAttribute("data-rank"), "1");
    assertEq(tsRank.textContent, "第 1");
    const intRank = find(rows[1], "data-role", "candidate-rank");
    assertEq(intRank.getAttribute("data-rank"), "");
    assert(subtreeText(intRank).indexOf("服务端未提供") !== -1, true);
    assertEq(candidateRows(ctx).map((row) => row.getAttribute("data-candidate-id")),
      [TS_ID, INT_ID], "no client sorting");
    ctx.instance.dispose();
  });

  await test("frame-candidate source: scan job reads frame-candidates and joins energy", async () => {
    const ctx = setup();
    await mount(ctx);
    await loadViaUi(ctx, "scan-1");
    assertEq(stateOf(ctx), "ready");
    assertEq(getPaths(ctx), [
      JOB("scan-1"),
      METHOD_CATALOG,
      JOB("scan-1") + "/frame-candidates",
      JOB("scan-1") + "/energy-graph"
    ]);
    const rows = candidateRows(ctx);
    assertEq(rows.length, 1);
    assertEq(rows[0].getAttribute("data-candidate-id"), "scan_frame_2");
    assertEq(find(rows[0], "data-role", "candidate-energy").textContent, "扫描能量 -154.100 Eh");
    assert(subtreeText(find(rows[0], "data-role", "candidate-provenance")).indexOf("帧候选") !== -1, true);
    assertEq(find(rows[0], "data-role", "candidate-server-label").textContent, "标签 INT1（服务端序 1）");
    ctx.instance.dispose();
  });

  await test("PES pending review: explicit empty state with no invented candidates", async () => {
    const ctx = setup({
      responder: (pathname, opts) => {
        if (pathname === JOB("pes-1") + "/pes/review") {
          return { job_id: "pes-1", status: "pending", review: {}, backups: [] };
        }
        if (pathname === JOB("pes-1") + "/energy-graph") {
          return energyPayload({ nodes: [], annotations: [], series: [] });
        }
        return defaultResponder(pathname, opts);
      }
    });
    await mount(ctx);
    await loadViaUi(ctx, "pes-1");
    assertEq(stateOf(ctx), "empty");
    assertEq(attr(ctx, "data-acp-candidate-count"), "0");
    assert(subtreeText(field(ctx, "empty-state")).indexOf("尚未确认选点") !== -1, true);
    assertEq(action(ctx, "preview").disabled, true);
    assertEq(postCalls(ctx).length, 0);
    ctx.instance.dispose();
  });

  await test("preview: exact per-candidate bodies, config preview, and preflight digests", async () => {
    const ctx = await previewReady();
    assertEq(attr(ctx, "data-acp-preview-state"), "previewed");
    assertEq(attr(ctx, "data-acp-busy"), "none");

    const posts = postCalls(ctx);
    assertEq(posts.map((call) => call.path), [
      CONFIG_PREVIEW, PREFLIGHT, PREFLIGHT
    ]);
    assertEq(posts.filter((call) => call.path === SUBMIT).length, 0, "preview never submits");

    const xyzPaths = getPaths(ctx).filter((pathname) => pathname.indexOf("/files/") !== -1);
    assertEq(xyzPaths, [
      JOB("pes-1") + "/files/RESULT/structures/" + TS_ID + ".xyz",
      JOB("pes-1") + "/files/RESULT/structures/" + INT_ID + ".xyz"
    ]);

    const configBody = parseBody(posts[0]);
    assertEq(configBody, { method: { profile: "opt_freq" } });

    const tsBody = parseBody(posts[1]);
    assertEq(tsBody.workflow, "BatchOptimize");
    assertEq(tsBody.schema_version, 1);
    assertEq(tsBody.input.source_type, "xyz_text");
    assertEq(tsBody.input.source, xyzText(TS_ID, "TS"));
    assertEq(tsBody.input.selected_ids, [TS_ID]);
    assertEq(tsBody.input.source_job_id, "pes-1");
    assertEq(tsBody.input.charge, 0);
    assertEq(tsBody.input.multiplicity, 1);
    assertEq(tsBody.method, { profile: "opt_freq" });

    const intBody = parseBody(posts[2]);
    assertEq(intBody.input.selected_ids, [INT_ID]);
    assertEq(intBody.input.source, xyzText(INT_ID, "INT"));
    assertEq(posts[1].opts.headers["Content-Type"], "application/json");

    const previewRows = withAttr(ctx.host, "data-preview-candidate");
    assertEq(previewRows.length, 2);
    assertEq(previewRows[0].getAttribute("data-plan-digest"), DIGEST[TS_ID]);
    assertEq(previewRows[0].getAttribute("data-feasible"), "true");
    assertEq(previewRows[1].getAttribute("data-plan-digest"), DIGEST[INT_ID]);
    assert(subtreeText(field(ctx, "config-key")).indexOf("batch_optimize_preview_v1") === -1, true);
    assert(subtreeText(field(ctx, "config-key")).indexOf("r2SCAN-3c") !== -1, true);
    assert(subtreeText(field(ctx, "config-roles")).indexOf("Tight") !== -1, true);
    assertEq(action(ctx, "request-confirm").disabled, false);
    ctx.instance.dispose();
  });

  await test("preview guard: no selection is a local error with zero POST", async () => {
    const ctx = await loadReady();
    candidateRows(ctx).forEach((row) => {
      const box = find(row, "data-role", "select");
      box.checked = false;
      box.fire("change");
    });
    await flush(1);
    assertEq(attr(ctx, "data-acp-selection-count"), "0");
    const before = postCalls(ctx).length;
    action(ctx, "preview").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-preview-state"), "error");
    assert(subtreeText(field(ctx, "preview-status")).indexOf("请先选择") !== -1, true);
    assertEq(postCalls(ctx).length, before, "no POST for an invalid preview");
    ctx.instance.dispose();
  });

  await test("selection change invalidates a preview and requires re-preview", async () => {
    const ctx = await previewReady();
    const box = find(candidateRows(ctx)[1], "data-role", "select");
    box.checked = false;
    box.fire("change");
    await flush(1);
    assertEq(attr(ctx, "data-acp-preview-state"), "stale");
    assertEq(action(ctx, "request-confirm").disabled, true);
    action(ctx, "preview").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-preview-state"), "previewed");
    assertEq(candidateRows(ctx).length, 2, "rows still rendered (selection is not deletion)");
    const preflights = postCalls(ctx).filter((call) => call.path === PREFLIGHT);
    assertEq(preflights.length, 3, "one preflight for the re-preview only");
    ctx.instance.dispose();
  });

  await test("confirm gate: opening the dialog sends zero POST /jobs; cancel does not submit", async () => {
    const ctx = await previewReady();
    const before = postCalls(ctx).filter((call) => call.path === SUBMIT).length;
    await openConfirm(ctx);
    assertEq(attr(ctx, "data-acp-confirm-open"), "true");
    assertEq(field(ctx, "confirm-box").hidden, false);
    assert(subtreeText(field(ctx, "confirm-body")).indexOf(TS_ID) !== -1, true);
    assert(subtreeText(field(ctx, "confirm-body")).indexOf(INT_ID) !== -1, true);
    assertEq(field(ctx, "confirm-body").textContent.indexOf("profile=opt_freq") !== -1, true);
    assertEq(postCalls(ctx).filter((call) => call.path === SUBMIT).length, before,
      "confirmation dialog alone never submits");

    action(ctx, "confirm-cancel").fire("click");
    await flush(1);
    assertEq(attr(ctx, "data-acp-confirm-open"), "false");
    assertEq(field(ctx, "confirm-box").hidden, true);
    assertEq(postCalls(ctx).filter((call) => call.path === SUBMIT).length, 0);
    ctx.instance.dispose();
  });

  await test("submit: explicit confirm posts one idempotent job per candidate and shows receipts", async () => {
    const ctx = await previewReady();
    await openConfirm(ctx);
    await submitConfirmed(ctx);

    const submits = postCalls(ctx).filter((call) => call.path === SUBMIT);
    assertEq(submits.length, 2, "one BatchOptimize job per candidate (single_flat)");
    const bodies = submits.map(parseBody);
    assertEq(bodies[0].workflow, "BatchOptimize");
    assertEq(bodies[0].input.selected_ids, [TS_ID]);
    assertEq(bodies[0].preflight_fingerprint, DIGEST[TS_ID]);
    assertEq(bodies[1].input.selected_ids, [INT_ID]);
    assertEq(bodies[1].preflight_fingerprint, DIGEST[INT_ID]);
    assertEq(submits[0].opts.headers["Idempotency-Key"],
      "pes-handoff:pes-1:" + TS_ID + ":" + DIGEST[TS_ID]);
    assertEq(submits[1].opts.headers["Idempotency-Key"],
      "pes-handoff:pes-1:" + INT_ID + ":" + DIGEST[INT_ID]);
    assertEq(submits[0].opts.headers["Content-Type"], "application/json");

    assertEq(attr(ctx, "data-acp-handoff-state"), "submitted");
    assertEq(attr(ctx, "data-acp-receipt-count"), "2");
    assertEq(attr(ctx, "data-acp-busy"), "none");
    const receipts = withAttr(ctx.host, "data-receipt-job-id");
    assertEq(receipts.map((row) => row.getAttribute("data-receipt-job-id")),
      ["batch-ts-1", "batch-int-2"]);
    assertEq(receipts[0].getAttribute("data-receipt-candidate"), TS_ID);
    assert(subtreeText(receipts[0]).indexOf("第 1 次") !== -1, true);
    assertEq(action(ctx, "request-confirm").disabled, true, "a submitted preview cannot be re-submitted");
    ctx.instance.dispose();
  });

  await test("receipt: commands.openTask is offered when injected", async () => {
    const opened = [];
    const commands = { openTask: (ref) => { opened.push(ref); return { ok: true }; } };
    const ctx = await previewReady({ deps: { commands } });
    await openConfirm(ctx);
    await submitConfirmed(ctx);
    const openButtons = findAll(ctx.host, "data-action", "open-task");
    assertEq(openButtons.length, 2);
    openButtons[0].fire("click");
    assertEq(opened.length, 1);
    assertEq(opened[0].source, "pes-handoff");
    assertEq(opened[0].jobId, "batch-ts-1");
    assertEq(opened[0].attempt, 1);
    ctx.instance.dispose();
  });

  await test("submit partial failure: prior receipt stays visible, no fake success", async () => {
    let submitCount = 0;
    const ctx = await previewReady({
      responder: (pathname, opts) => {
        if (pathname === SUBMIT && ((opts && opts.method) === "POST")) {
          submitCount += 1;
          if (submitCount === 2) throw httpError(500, { message: "engine rejected", code: "server_error" });
        }
        return defaultResponder(pathname, opts);
      }
    });
    await openConfirm(ctx);
    await submitConfirmed(ctx);

    const submits = postCalls(ctx).filter((call) => call.path === SUBMIT);
    assertEq(submits.length, 2, "submission stops after the first failure");
    assertEq(attr(ctx, "data-acp-handoff-state"), "error");
    assertEq(attr(ctx, "data-acp-receipt-count"), "1");
    assert(subtreeText(field(ctx, "handoff-status")).indexOf("部分候选未提交") !== -1, true);
    assert(subtreeText(field(ctx, "handoff-status")).indexOf("engine rejected") !== -1, true);
    assertEq(withAttr(ctx.host, "data-receipt-job-id").length, 1);
    assertEq(withAttr(ctx.host, "data-receipt-job-id")[0].getAttribute("data-receipt-job-id"), "batch-ts-1");
    ctx.instance.dispose();
  });

  await test("submit transport failure: unconfirmed wording, never success", async () => {
    const ctx = await previewReady({
      responder: (pathname, opts) => {
        if (pathname === SUBMIT && (opts && opts.method) === "POST") {
          return Promise.reject(new Error("network reset"));
        }
        return defaultResponder(pathname, opts);
      }
    });
    await openConfirm(ctx);
    await submitConfirmed(ctx);
    assertEq(attr(ctx, "data-acp-handoff-state"), "error");
    assertEq(attr(ctx, "data-acp-receipt-count"), "0");
    const text = subtreeText(field(ctx, "handoff-status"));
    assert(text.indexOf("提交结果未确认") !== -1, true);
    assert(text.indexOf("勿盲目重复提交") !== -1, true);
    assert(text.indexOf("已提交") === -1, "never claims success");
    ctx.instance.dispose();
  });

  await test("submit preflight_stale: preview is invalidated and must be re-run", async () => {
    const ctx = await previewReady({
      responder: (pathname, opts) => {
        if (pathname === SUBMIT && (opts && opts.method) === "POST") {
          throw httpError(400, {
            message: "preflight fingerprint does not match the submitted plan",
            code: "preflight_stale",
            params: { field: "preflight_fingerprint" }
          });
        }
        return defaultResponder(pathname, opts);
      }
    });
    await openConfirm(ctx);
    await submitConfirmed(ctx);
    assertEq(attr(ctx, "data-acp-handoff-state"), "error");
    assertEq(attr(ctx, "data-acp-preview-state"), "stale");
    assert(subtreeText(field(ctx, "handoff-status")).indexOf("预览已过期") !== -1, true);
    assertEq(action(ctx, "request-confirm").disabled, true);
    ctx.instance.dispose();
  });

  await test("missing endpoint: method catalog failure blocks preview with zero submit", async () => {
    const ctx = await loadReady({
      responder: (pathname, opts) => {
        if (pathname === METHOD_CATALOG) throw httpError(404, "Not Found");
        return defaultResponder(pathname, opts);
      }
    });
    assertEq(attr(ctx, "data-acp-config-state"), "error");
    assertEq(stateOf(ctx), "ready", "candidates are still visible");
    action(ctx, "preview").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-preview-state"), "error");
    assert(subtreeText(field(ctx, "preview-error")).indexOf("方法目录端点不可用") !== -1, true);
    assert(subtreeText(field(ctx, "preview-error")).indexOf("404") !== -1, true);
    assertEq(postCalls(ctx).filter((call) => call.path === SUBMIT).length, 0, "no job submission");
    assertEq(postCalls(ctx).filter((call) => call.path === PREFLIGHT).length, 0);
    ctx.instance.dispose();
  });

  await test("missing endpoint: config-preview 404 is explicit and never submits", async () => {
    const ctx = await previewReady({
      responder: (pathname, opts) => {
        if (pathname === CONFIG_PREVIEW) throw httpError(404, { message: "no route" });
        return defaultResponder(pathname, opts);
      }
    });
    assertEq(attr(ctx, "data-acp-preview-state"), "error");
    assert(subtreeText(field(ctx, "preview-error")).indexOf("批处理预览端点不可用") !== -1, true);
    assert(subtreeText(field(ctx, "preview-error")).indexOf("404") !== -1, true);
    assertEq(postCalls(ctx).filter((call) => call.path === SUBMIT).length, 0);
    assertEq(postCalls(ctx).filter((call) => call.path === PREFLIGHT).length, 0);
    ctx.instance.dispose();
  });

  await test("missing endpoint: preflight 404 is explicit and never submits", async () => {
    const ctx = await previewReady({
      responder: (pathname, opts) => {
        if (pathname === PREFLIGHT) throw httpError(404, { message: "no preflight" });
        return defaultResponder(pathname, opts);
      }
    });
    assertEq(attr(ctx, "data-acp-preview-state"), "error");
    assert(subtreeText(field(ctx, "preview-error")).indexOf("预检端点不可用") !== -1, true);
    assert(subtreeText(field(ctx, "preview-error")).indexOf("404") !== -1, true);
    assertEq(postCalls(ctx).filter((call) => call.path === SUBMIT).length, 0);
    ctx.instance.dispose();
  });

  await test("missing endpoint: candidate read 404 stops loading with an explicit error", async () => {
    const ctx = setup({
      responder: (pathname, opts) => {
        if (pathname === JOB("pes-1") + "/pes/review") throw httpError(404, "Not Found");
        return defaultResponder(pathname, opts);
      }
    });
    await mount(ctx);
    await loadViaUi(ctx, "pes-1");
    assertEq(stateOf(ctx), "error");
    const text = subtreeText(field(ctx, "overlay-message"));
    assert(text.indexOf("候选端点不可用") !== -1, true);
    assert(text.indexOf("pes/review HTTP 404") !== -1, true);
    assertEq(attr(ctx, "data-acp-candidate-count"), "0");
    assertEq(postCalls(ctx).length, 0, "no preview/submit after a failed candidate read");
    ctx.instance.dispose();
  });

  await test("candidate geometry read failure blocks the hand-off before any POST /jobs", async () => {
    const ctx = await loadReady({
      responder: (pathname, opts) => {
        if (pathname.indexOf("/files/RESULT/structures/" + TS_ID) !== -1) {
          throw httpError(404, "structure file missing");
        }
        return defaultResponder(pathname, opts);
      }
    });
    action(ctx, "preview").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-preview-state"), "error");
    const text = subtreeText(field(ctx, "preview-error"));
    assert(text.indexOf("无法读取候选结构") !== -1, true);
    assert(text.indexOf(TS_ID) !== -1, true);
    assertEq(postCalls(ctx).filter((call) => call.path === CONFIG_PREVIEW).length, 0,
      "no config preview when geometry is unavailable");
    assertEq(postCalls(ctx).filter((call) => call.path === SUBMIT).length, 0);
    ctx.instance.dispose();
  });

  await test("candidate XYZ without candidate_id cannot be bound to --select", async () => {
    const ctx = await loadReady({
      responder: (pathname, opts) => {
        if (pathname.indexOf("/files/RESULT/structures/" + TS_ID) !== -1) {
          return "3\nTAG: TS | source=PESsearch | frame=005\nC 0 0 0\nO 1.2 0 0\nH -0.3 0.9 0\n";
        }
        return defaultResponder(pathname, opts);
      }
    });
    action(ctx, "preview").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-preview-state"), "error");
    assert(subtreeText(field(ctx, "preview-error")).indexOf("缺少 candidate_id") !== -1, true);
    assertEq(postCalls(ctx).filter((call) => call.path === CONFIG_PREVIEW).length, 0);
    assertEq(postCalls(ctx).filter((call) => call.path === SUBMIT).length, 0);
    ctx.instance.dispose();
  });

  await test("infeasible preflight is a blocking preview error with the server reason", async () => {
    const ctx = await loadReady({
      responder: (pathname, opts) => {
        if (pathname === PREFLIGHT) {
          const body = JSON.parse(opts.body);
          const candidateId = body.input.selected_ids[0];
          if (candidateId === TS_ID) {
            return preflightPayload(candidateId, {
              feasible: false,
              plan_digest: null,
              reasons: [{
                code: "input_invalid",
                message: "not a valid batch plan",
                field: "input.items",
                rule: "layout_limit"
              }]
            });
          }
        }
        return defaultResponder(pathname, opts);
      }
    });
    action(ctx, "preview").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-preview-state"), "error");
    const text = subtreeText(field(ctx, "preview-error"));
    assert(text.indexOf("预检未通过") !== -1, true);
    assert(text.indexOf(TS_ID) !== -1, true);
    assert(text.indexOf("input.items") !== -1, true);
    assert(text.indexOf("not a valid batch plan") !== -1, true);
    assertEq(postCalls(ctx).filter((call) => call.path === SUBMIT).length, 0);
    ctx.instance.dispose();
  });

  await test("late job response after dispose never paints and starts no secondary read", async () => {
    const jobDeferred = deferred();
    const ctx = setup({
      responder: (pathname, opts) => {
        if (pathname === JOB("pes-1")) return jobDeferred.promise;
        return defaultResponder(pathname, opts);
      }
    });
    await mount(ctx);
    const input = field(ctx, "job-id");
    input.value = "pes-1";
    action(ctx, "load-job").fire("click");
    await flush(2);
    assertEq(stateOf(ctx), "loading");
    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0);
    jobDeferred.resolve(jobPayload("pes-1", "PESsearch"));
    await flush();
    assertEq(ctx.host.children.length, 0);
    assertEq(getPaths(ctx).filter((pathname) => pathname === METHOD_CATALOG).length, 0,
      "no secondary requests after dispose");
  });

  await test("late preview after dispose cannot reach config-preview or submit", async () => {
    const xyzDeferred = deferred();
    const ctx = await loadReady({
      responder: (pathname, opts) => {
        if (pathname.indexOf("/files/RESULT/structures/" + TS_ID) !== -1) return xyzDeferred.promise;
        return defaultResponder(pathname, opts);
      }
    });
    action(ctx, "preview").fire("click");
    await flush(2);
    assertEq(attr(ctx, "data-acp-preview-state"), "previewing");
    ctx.instance.dispose();
    xyzDeferred.resolve(xyzText(TS_ID, "TS"));
    await flush();
    assertEq(postCalls(ctx).filter((call) => call.path === CONFIG_PREVIEW).length, 0);
    assertEq(postCalls(ctx).filter((call) => call.path === PREFLIGHT).length, 0);
    assertEq(postCalls(ctx).filter((call) => call.path === SUBMIT).length, 0);
    assertEq(ctx.host.children.length, 0);
  });

  await test("job switch during preview drops the stale preview result", async () => {
    const configDeferred = deferred();
    const ctx = await loadReady({
      responder: (pathname, opts) => {
        if (pathname === CONFIG_PREVIEW) return configDeferred.promise;
        return defaultResponder(pathname, opts);
      }
    });
    action(ctx, "preview").fire("click");
    await flush(2);
    assertEq(attr(ctx, "data-acp-preview-state"), "previewing");

    const input = field(ctx, "job-id");
    input.value = "scan-1";
    action(ctx, "load-job").fire("click");
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(candidateRows(ctx)[0].getAttribute("data-candidate-id"), "scan_frame_2");

    configDeferred.resolve(configPreviewPayload());
    await flush();
    assertEq(attr(ctx, "data-acp-preview-state"), "idle", "old preview result dropped");
    assertEq(postCalls(ctx).filter((call) => call.path === PREFLIGHT).length, 0);
    assertEq(postCalls(ctx).filter((call) => call.path === SUBMIT).length, 0);
    ctx.instance.dispose();
  });

  await test("invalidate during preview releases the busy state and emits no POST", async () => {
    const xyzDeferred = deferred();
    const ctx = await loadReady({
      responder: (pathname, opts) => {
        if (pathname.indexOf("/files/RESULT/structures/" + TS_ID) !== -1) return xyzDeferred.promise;
        return defaultResponder(pathname, opts);
      }
    });
    action(ctx, "preview").fire("click");
    await flush(2);
    assertEq(attr(ctx, "data-acp-preview-state"), "previewing");
    assertEq(attr(ctx, "data-acp-busy"), "preview");

    const box = find(candidateRows(ctx)[1], "data-role", "select");
    box.checked = false;
    box.fire("change");
    await flush(1);
    assertEq(attr(ctx, "data-acp-preview-state"), "stale");
    xyzDeferred.resolve(xyzText(TS_ID, "TS"));
    await flush();
    assertEq(attr(ctx, "data-acp-busy"), "none", "busy is released after a stale preview");
    assertEq(attr(ctx, "data-acp-preview-state"), "stale");
    assertEq(postCalls(ctx).filter((call) => call.path === CONFIG_PREVIEW).length, 0);
    assertEq(postCalls(ctx).filter((call) => call.path === SUBMIT).length, 0);
    ctx.instance.dispose();
  });

  await test("focus change during submit is ignored until the receipts complete", async () => {
    const firstSubmit = deferred();
    let submitCount = 0;
    const context = makeContext("pes-1");
    const ctx = setup({
      context,
      responder: (pathname, opts) => {
        if (pathname === SUBMIT && (opts && opts.method) === "POST") {
          submitCount += 1;
          if (submitCount === 1) return firstSubmit.promise;
        }
        return defaultResponder(pathname, opts);
      }
    });
    await mount(ctx);
    await flush();
    assertEq(stateOf(ctx), "ready");
    action(ctx, "preview").fire("click");
    await flush();
    await openConfirm(ctx);
    action(ctx, "confirm-submit").fire("click");
    await flush(2);
    assertEq(attr(ctx, "data-acp-handoff-state"), "submitting");

    context.setFocus({ taskRef: { jobId: "scan-1", attempt: 1 } });
    await flush();
    assertEq(attr(ctx, "data-acp-handoff-state"), "submitting", "focus change does not cancel submit");
    assertEq(candidateRows(ctx)[0].getAttribute("data-candidate-id"), TS_ID,
      "the submitted job stays on screen");
    const warnings = findAll(ctx.host, "data-role", "warning").map((node) => node.textContent);
    assert(warnings.some((text) => text.indexOf("已忽略任务焦点变化") !== -1), true);

    firstSubmit.resolve(receiptPayload(TS_ID, "key-1"));
    await flush();
    assertEq(attr(ctx, "data-acp-handoff-state"), "submitted");
    assertEq(attr(ctx, "data-acp-receipt-count"), "2");
    ctx.instance.dispose();
  });

  await test("context: taskRef auto-loads, clears without requests, and switches jobs", async () => {
    const context = makeContext("pes-1");
    const ctx = setup({ context });
    await mount(ctx);
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(ctx.client.calls[0].path, JOB("pes-1"));

    const before = ctx.client.calls.length;
    context.setFocus({ taskRef: null });
    await flush();
    assertEq(stateOf(ctx), "empty");
    assertEq(ctx.client.calls.length, before, "clearing focus sends no request");

    context.setFocus({ taskRef: { jobId: "scan-1", attempt: 1 } });
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(candidateRows(ctx)[0].getAttribute("data-candidate-id"), "scan_frame_2");
    ctx.instance.dispose();
  });

  await test("dispose is idempotent, unsubscribes, and allows a clean remount", async () => {
    const context = makeContext("pes-1");
    const ctx = setup({ context });
    await mount(ctx);
    await flush();
    assertEq(context.listenerCount(), 1);
    assertEq(ctx.dom.document.activeDocListeners(), 1);
    ctx.instance.dispose();
    ctx.instance.dispose();
    assertEq(context.listenerCount(), 0);
    assertEq(ctx.dom.document.activeDocListeners(), 0);
    assertEq(ctx.host.children.length, 0);

    const host2 = ctx.dom.document.createElement("div");
    await ctx.instance.mount(host2);
    await flush();
    assertEq(host2.children[0].getAttribute("data-acp-module-state"), "ready");
    assertEq(host2.children[0].getAttribute("data-acp-candidate-count"), "2");
    ctx.instance.dispose();
    assertEq(host2.children.length, 0);
  });

  await test("no client seam: explicit error with no request", async () => {
    const dom = makeDom();
    const sandbox = makeSandbox({});
    loadScript(MODULE_PATH, sandbox);
    const instance = sandbox.ACPNextModules["pes-handoff"].factory({ doc: dom.document });
    const host = dom.document.createElement("div");
    await instance.mount(host);
    const input = find(host, "data-role", "job-id");
    input.value = "pes-1";
    find(host, "data-action", "load-job").fire("click");
    await flush();
    const rootNode = host.children[0];
    assertEq(rootNode.getAttribute("data-acp-module-state"), "error");
    assert(subtreeText(rootNode).indexOf("客户端未注入") !== -1, true);
    assertEq(rootNode.getAttribute("data-acp-candidate-count"), "0");
    instance.dispose();
  });

  await test("pure: paths encode ids and only the existing routes are built", async () => {
    const internals = setup().mod._internals;
    assertEq(internals.jobPath("a b/c"), API + "/jobs/a%20b%2Fc");
    assertEq(internals.pesReviewPath("j 1"), API + "/jobs/j%201/pes/review");
    assertEq(internals.frameCandidatesPath("j1"), API + "/jobs/j1/frame-candidates");
    assertEq(internals.energyPath("j1"), API + "/jobs/j1/energy-graph");
    assertEq(internals.structureFilePath("j 1", "c/1"),
      API + "/jobs/j%201/files/RESULT/structures/c%2F1.xyz");
    assertEq(Object.keys(internals.ROUTES).sort(),
      ["configPreview", "energyGraph", "files", "frameCandidates", "jobs", "methodCatalog",
        "pesReview", "preflight", "submit"]);
  });

  await test("pure: normalizers are defensive and keep server truth", async () => {
    const internals = setup().mod._internals;

    const job = internals.normalizeJob(jobPayload("pes-1", "PESsearch"));
    assertEq(job.id, "pes-1");
    assertEq(job.workflow, "PESsearch");
    assertEq(job.charge, 0);
    assertEq(job.multiplicity, 1);
    assertEq(internals.normalizeJob(null), null);

    const catalog = internals.normalizeMethodCatalog(methodCatalogPayload());
    assertEq(catalog.profiles.length, 4);
    assertEq(catalog.profiles[1].id, "opt_freq");
    assertEq(internals.defaultProfileFor(catalog.profiles), "opt_freq");
    assertEq(internals.defaultProfileFor([{ id: "opt_only" }]), "opt_only");
    assertEq(internals.defaultProfileFor([]), "");
    assertEq(internals.normalizeMethodCatalog(null).profiles.length, 0);

    const energy = internals.normalizeEnergyGraph(energyPayload());
    assertEq(energy.defaultSeries.id, "relative_energy");
    assertEq(internals.energyForFrame(energy, 5, TS_ID).value, 12.1);
    assertEq(internals.energyForFrame(energy, 5, TS_ID).unit, "kcal/mol");
    assertEq(internals.energyForFrame(energy, 99, "missing").value, null);
    assertEq(internals.normalizeEnergyGraph("bad").frames.length, 0);

    const pes = internals.normalizePesCandidates(reviewPayload(), energy);
    assertEq(pes.status, "confirmed");
    assertEq(pes.revision, 2);
    assertEq(pes.candidates.length, 2);
    assertEq(pes.candidates[0].role, "ts");
    assertEq(pes.candidates[0].rank, 1);
    assertEq(pes.candidates[1].rank, null);
    assertEq(pes.candidates[0].energy.value, 12.1);
    assertEq(internals.normalizePesCandidates(null, null).status, "pending");

    const frames = internals.normalizeFrameCandidates(frameCandidatesPayload(), null);
    assertEq(frames.candidates[0].candidateId, "scan_frame_2");
    assertEq(frames.candidates[0].role, "intermediate");
    assertEq(frames.candidates[0].displayLabel, "INT1");
    assertEq(frames.candidates[0].energy.value, null);

    assertEq(internals.roleToken("TS"), "ts");
    assertEq(internals.roleToken("INT"), "intermediate");
    assertEq(internals.roleToken("weird"), "");
  });

  await test("pure: XYZ validation requires atoms and a bound candidate id", async () => {
    const internals = setup().mod._internals;
    assertEq(internals.validateXyz(xyzText(TS_ID, "TS"), TS_ID).ok, true);
    assertEq(internals.validateXyz(xyzText(TS_ID, "TS"), TS_ID).bound, true);
    assertEq(internals.validateXyz(xyzText(TS_ID, "TS"), "other").bound, false);
    assertEq(internals.validateXyz("2\n\nH 0 0 0\n", "x").ok, false, "truncated");
    assertEq(internals.validateXyz("nope\n\n", "x").ok, false);
    assertEq(internals.validateXyz("", "x").ok, false);
  });

  await test("pure: hand-off bodies use only the BatchOptimize contract keys", async () => {
    const internals = setup().mod._internals;
    const candidates = internals.normalizePesCandidates(reviewPayload(), null).candidates;
    const bodies = internals.buildHandoffBodies({
      job: internals.normalizeJob(jobPayload("pes-1", "PESsearch")),
      candidates: candidates,
      profile: "opt_freq",
      xyzByCandidate: { [TS_ID]: xyzText(TS_ID, "TS"), [INT_ID]: xyzText(INT_ID, "INT") }
    });
    assertEq(bodies.length, 2);
    assertEq(bodies.map((item) => item.candidateId), [TS_ID, INT_ID]);
    const body = bodies[0].body;
    assertEq(Object.keys(body).sort(), ["input", "method", "schema_version", "workflow"]);
    assertEq(Object.keys(body.input).sort(),
      ["charge", "multiplicity", "selected_ids", "source", "source_job_id", "source_type"]);
    assertEq(body.input.selected_ids, [TS_ID]);
    assertEq(body.input.source_job_id, "pes-1");
    assertEq(body.method.profile, "opt_freq");
    assertEq(body.input.source, xyzText(TS_ID, "TS"));

    const withoutXyz = internals.buildHandoffBodies({
      job: internals.normalizeJob(jobPayload("pes-1", "PESsearch")),
      candidates: candidates,
      profile: "opt_freq",
      xyzByCandidate: { [TS_ID]: xyzText(TS_ID, "TS") }
    });
    assertEq(withoutXyz.length, 1, "missing geometry is skipped, not fabricated");

    const submit = internals.buildSubmitBody(body, DIGEST[TS_ID]);
    assertEq(submit.preflight_fingerprint, DIGEST[TS_ID]);
    assertEq(submit.input.selected_ids, [TS_ID]);
    assertEq(internals.buildSubmitBody(body, ""), null);

    assertEq(internals.selectionKey("j", "p", [{ candidateId: "a" }, { candidateId: "b" }]),
      "j|p|a,b");
  });

  await test("pure: preflight/receipt/error mapping", async () => {
    const internals = setup().mod._internals;
    const ok = internals.normalizePreflight(preflightPayload(TS_ID));
    assertEq(ok.feasible, true);
    assertEq(ok.digest, DIGEST[TS_ID]);
    assertEq(ok.reasons.length, 0);
    const bad = internals.normalizePreflight({
      feasible: false,
      reasons: [{ code: "input_invalid", message: "bad", field: "input.items", rule: "x" }],
      advisories: [{ code: "W", message: "watch" }]
    });
    assertEq(bad.feasible, false);
    assertEq(bad.reasons[0].field, "input.items");
    assertEq(bad.advisories, ["watch"]);

    const receipt = internals.normalizeReceipt({
      job_id: "batch-ts-1", status: "QUEUED", attempt: 2, plan_digest: DIGEST[TS_ID],
      idempotent_replay: true
    });
    assertEq(receipt.jobId, "batch-ts-1");
    assertEq(receipt.attempt, 2);
    assertEq(receipt.replay, true);
    assertEq(internals.normalizeReceipt({ status: "QUEUED" }), null);

    const stale = internals.mapSubmitError(httpError(400, {
      code: "preflight_stale", message: "stale", params: { field: "preflight_fingerprint" }
    }));
    assertEq(stale.key, "next.handoff.err.submit_stale");
    assertEq(stale.code, "preflight_stale");
    const forbidden = internals.mapSubmitError(httpError(403, { code: "access_denied", message: "no" }));
    assertEq(forbidden.key, "next.handoff.err.submit_forbidden");
    assertEq(forbidden.vars.status, "403");
    const unconfirmed = internals.mapSubmitError(new Error("socket closed"));
    assertEq(unconfirmed.key, "next.handoff.err.submit_unconfirmed");
    assertEq(unconfirmed.retryable, true);

    assertEq(internals.idempotencyKeyFor("j", "c", DIGEST[TS_ID]),
      "pes-handoff:j:c:" + DIGEST[TS_ID]);
  });

  await test("i18n: every literal key exists in the local dictionary", async () => {
    const ctx = setup();
    await mount(ctx);
    const mod = ctx.mod;
    const source = fs.readFileSync(path.resolve(__dirname, "../../js/modules/pes-handoff/module.js"), "utf8");
    const literal = new Set((source.match(/next\.handoff\.[A-Za-z0-9_.]+/g) || [])
      .filter((key) => key.charAt(key.length - 1) !== "."));
    literal.forEach((key) => {
      assert(Object.prototype.hasOwnProperty.call(mod._internals.i18n, key), "missing local key " + key);
    });
    const missing = withAttr(ctx.host, "data-i18n").filter((node) =>
      !node.getAttribute("data-i18n").startsWith("next.handoff."));
    assertEq(missing.length, 0, "all data-i18n keys are module-owned");
    ctx.instance.dispose();
  });

  await test("source pins: only the documented routes, no direct network/viewer coupling", async () => {
    const source = fs.readFileSync(path.resolve(__dirname, "../../js/modules/pes-handoff/module.js"), "utf8");
    [
      "fetch(", "XMLHttpRequest", "sendBeacon", "window.fetch", "localStorage",
      "sessionStorage", "innerHTML", "insertAdjacentHTML", "document.write",
      "$3Dmol", "ACPVisualization", "scene_instance", "createViewer", "canvas",
      "ACPGeometryStore", "structure_editor.js", "geometry_store", "jobCommands",
      "/api/v2", ".sort("
    ].forEach((needle) => {
      assert(source.indexOf(needle) === -1, "forbidden source token: " + needle);
    });
    assert(source.indexOf("deps.commands") !== -1, "navigation is limited to the injected command surface");
    assert(source.indexOf("deps.commands.openTask") !== -1);
    assert(source.indexOf('"next.handoff.err.candidates_endpoint"') !== -1);
    const posts = source.match(/method: "POST"/g) || [];
    assertEq(posts.length, 3, "only config-preview + preflight + jobs POST");
    assert(source.indexOf("ROUTES.configPreview") !== -1);
    assert(source.indexOf("ROUTES.preflight") !== -1);
    assert(source.indexOf("ROUTES.submit") !== -1);
    assert(source.indexOf("RESULT/structures/") !== -1, "documented candidate artifact path");
  });
};
