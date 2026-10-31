/* ACP 3.0 module test — path-view (Track A / A-50 · P17 path structure view).
 *
 * Loads the module as the classic script the Shell loader consumes
 * (window.ACPNextModules["path-view"] = { descriptor, factory, _internals })
 * inside the zero-dependency node vm harness with a fake DOM. Every request goes
 * through an injected request seam, so no test can reach the network (the
 * sandbox has no fetch/XMLHttpRequest at all).
 *
 * Pinned contracts:
 *  1. exactly one ACPNextModules key, Shell-valid descriptor, no extra global;
 *  2. four states (empty/loading/ready/error) + retry; a missing/unusable
 *     catalog stops the panel instead of inventing a fallback route;
 *  3. PES path: pes/review selected entries + energy-graph energies + catalog
 *     entries, with review provenance; pending review is an explicit empty state;
 *  4. mechanism path: study list + report nodes/edges, word-form role
 *     classification only, no chemistry inference;
 *  5. structure selection emits the frozen canonical structureRef through the
 *     injected commands.openStructure and nothing else (no 3D scene, no second
 *     viewer, no measurement/edit controls — those belong to molecule-viewer);
 *  6. generation/dispose guards drop late results; dispose is idempotent;
 *  7. route whitelist: only the six existing read routes are ever requested and
 *     no POST is sent; source pins forbid direct network/storage/path building.
 */
"use strict";

const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const MODULE_PATH = "js/modules/path-view/module.js";
  const API = "/api/v1";
  const JOB = (id) => API + "/jobs/" + encodeURIComponent(id);
  const STUDIES = API + "/mechanism-studies";

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function flush(n = 12) {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await tick();
    for (let i = 0; i < 8; i++) await Promise.resolve();
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
        listenerCount(type) {
          const set = listeners.get(type);
          return set ? set.size : 0;
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

  function jobPayload(id, workflow, overrides) {
    return Object.assign({
      id: id,
      status: "COMPLETED",
      attempt: 1,
      spec: {
        workflow: workflow || "PESsearch",
        task_name: "pes-task",
        molecule_name: "CCO",
        input: { charge: 0, multiplicity: 1 }
      },
      project_id: "proj-1"
    }, overrides || {});
  }

  function catalogPayload(overrides) {
    return Object.assign({
      schema_version: "structure_viewer_v1",
      job_id: "pes-1",
      workflow: "PESsearch",
      job_status: "COMPLETED",
      revision: "rev-7",
      default_entry_id: "pes_pes_ts_frame_005",
      availability: "ready",
      groups: [
        { id: "pes_confirmed", label: "人工确认", kind: "confirmed" }
      ],
      entries: [
        {
          id: "pes_pes_ts_frame_005",
          group_id: "pes_confirmed",
          label: "frame_005__TAG_TS",
          role: "ts",
          status: "completed",
          energy: { value: null, unit: "hartree", kind: "electronic" },
          source: {
            kind: "manual_review",
            frame_index: 5,
            geometry_ref: "structures/pes_ts_frame_005.xyz",
            confirmed: true
          },
          badges: ["TS"]
        },
        {
          id: "pes_pes_int_frame_008",
          group_id: "pes_confirmed",
          label: "frame_008__TAG_INT",
          role: "endpoint",
          status: "completed",
          source: {
            kind: "manual_review",
            frame_index: 8,
            geometry_ref: "structures/pes_int_frame_008.xyz",
            confirmed: true
          },
          badges: ["INT"]
        }
      ],
      warnings: [],
      ensemble_thermo: null
    }, overrides || {});
  }

  function energyPayload(overrides) {
    return Object.assign({
      schema_version: "energy_graph_v1",
      job_id: "pes-1",
      view_type: "scan",
      default_series: "relative_energy",
      series: [
        {
          id: "relative_energy",
          label: "相对能量",
          unit: "kcal/mol",
          values: [0.0, 8.4, 12.1, 3.2],
          x_values: [1.0, 1.4, 1.8, 2.2]
        }
      ],
      nodes: [
        { id: "frame_0", label: "Frame 1", type: "scan", frame_index: 0, x: 1.0, energy: 0.0, status: "converged", geometry_ref: "frame_000.xyz" },
        { id: "frame_5", label: "Frame 6", type: "scan", frame_index: 5, x: 1.4, energy: 12.1, status: "converged", geometry_ref: "frame_005.xyz" },
        { id: "frame_8", label: "Frame 9", type: "scan", frame_index: 8, x: 1.8, energy: 3.2, status: "converged", geometry_ref: "frame_008.xyz" }
      ],
      annotations: [
        {
          id: "pes_ts_frame_005",
          candidate_id: "pes_ts_frame_005",
          type: "ts",
          label: "TS1",
          frame_index: 5,
          x: 1.4,
          y: 12.1,
          selected: true,
          saved: true,
          selection_source: "manual",
          confidence: "high",
          reason: "manual pick"
        },
        {
          id: "pes_int_frame_008",
          candidate_id: "pes_int_frame_008",
          type: "intermediate",
          label: "INT1",
          frame_index: 8,
          x: 1.8,
          y: 3.2,
          selected: true,
          saved: true,
          selection_source: "manual"
        }
      ],
      provenance: { jobId: "pes-1", viewType: "scan" },
      warnings: []
    }, overrides || {});
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
        profile_sha256: "abc123",
        note: "stepwise",
        source: {
          task_status: "COMPLETED",
          frame_source: "final_profile",
          scan_complete: true,
          data_source: "RESULT/pes_search/pes_profile.json",
          data_sha256: "def456"
        },
        selected: [
          {
            candidate_id: "pes_ts_frame_005",
            frame_index: 5,
            role: "TS",
            name: "frame_005__TAG_TS",
            selection_source: "manual",
            structure_path: "structures/pes_ts_frame_005.xyz",
            frame_converged: true
          },
          {
            candidate_id: "pes_int_frame_008",
            frame_index: 8,
            role: "INT",
            name: "frame_008__TAG_INT",
            selection_source: "manual",
            structure_path: "structures/pes_int_frame_008.xyz",
            frame_converged: true
          }
        ]
      },
      backups: [
        { n: 1, confirmed_at: "2026-10-30T09:00:00+08:00", note: "", selected_count: 1 }
      ]
    }, overrides || {});
  }

  function studyListPayload() {
    return [
      { id: "study-1", job_id: "mech-1", status: "reaction_confirmed", unified_status: "RESUMABLE" }
    ];
  }

  function mechanismReportPayload() {
    return {
      study_id: "study-1",
      job_id: "mech-1",
      reaction_network: {
        nodes: [
          { state_id: "s1", role: "reactant", label: "反应物", canonical_energy_hartree: -100.5 },
          {
            state_id: "ts1",
            role: "transition_state",
            label: "TS1",
            canonical_energy_hartree: -99.1,
            structure_ref: { kind: "structure-entry", jobId: "mech-1", entryId: "mech_ts_1" }
          },
          { state_id: "s2", role: "intermediate", label: "INT1", canonical_energy_hartree: -100.2 },
          {
            state_id: "ts2",
            role: "ts",
            label: "TS2",
            canonical_energy_hartree: -98.8,
            entry_id: "mech_ts_2"
          },
          { state_id: "s3", role: "product", label: "产物", canonical_energy_hartree: -101.0 }
        ],
        edges: [
          {
            step_id: "step-1",
            source_state_id: "s1",
            sink_state_id: "s2",
            ts_id: "ts1",
            barrier_forward: 12.5,
            barrier_reverse: 3.1,
            fidelity: "s3",
            path_strategy: "guided-scan",
            status: "confirmed"
          },
          {
            step_id: "step-2",
            source_state_id: "s2",
            sink_state_id: "s3",
            ts_id: "ts2",
            barrier_forward: 9.2,
            barrier_reverse: 4.0,
            fidelity: "s3",
            path_strategy: "guided-scan",
            status: "confirmed"
          }
        ]
      },
      stationary_points: {
        stationary_points: [
          {
            point_id: "ts1",
            role: "transition_state",
            kind: "ts",
            energy_hartree: -99.1,
            structure_ref: { kind: "structure-entry", jobId: "mech-1", entryId: "mech_ts_1" }
          },
          { point_id: "s2", role: "intermediate", energy_hartree: -100.2 }
        ]
      },
      provenance: {
        study_input_signature: "sig-1",
        count: 2,
        records: [{ id: "rec-1", kind: "sbatch" }, { id: "rec-2", kind: "opt" }]
      }
    };
  }

  // ------------------------------------------------------------- client --

  function defaultResponse(pathname) {
    if (pathname === JOB("pes-1")) return jobPayload("pes-1", "PESsearch");
    if (pathname === JOB("pes-1") + "/structure-viewer") return catalogPayload();
    if (pathname === JOB("pes-1") + "/energy-graph") return energyPayload();
    if (pathname === JOB("pes-1") + "/pes/review") return reviewPayload();
    if (pathname === JOB("mech-1")) return jobPayload("mech-1", "mechanism", { id: "mech-1" });
    if (pathname === JOB("mech-1") + "/structure-viewer") {
      return catalogPayload({
        job_id: "mech-1",
        workflow: "mechanism",
        revision: "empty",
        groups: [],
        entries: [],
        default_entry_id: null,
        availability: "ready",
        warnings: []
      });
    }
    if (pathname === JOB("mech-1") + "/energy-graph") {
      return { view_type: "unsupported", series: [], nodes: [], annotations: [], provenance: {} };
    }
    if (pathname === STUDIES + "?job_id=mech-1&limit=5") return studyListPayload();
    if (pathname === STUDIES + "/study-1/report") return mechanismReportPayload();
    if (pathname === JOB("scan-1")) return jobPayload("scan-1", "scan", { id: "scan-1" });
    if (pathname === JOB("scan-1") + "/structure-viewer") {
      return catalogPayload({
        job_id: "scan-1",
        workflow: "scan",
        entries: [
          {
            id: "scan_frame_0",
            group_id: "",
            label: "扫描帧 0",
            role: "minimum",
            status: "completed",
            energy: { value: -154.1, unit: "hartree", kind: "electronic" },
            source: { kind: "formal_result", frame_index: 0, geometry_ref: "RESULT/frames/frame_000.xyz" }
          }
        ]
      });
    }
    if (pathname === JOB("scan-1") + "/energy-graph") {
      return energyPayload({
        job_id: "scan-1",
        default_series: "scan_energy",
        series: [{ id: "scan_energy", label: "扫描能量", unit: "Eh", values: [-154.1], x_values: [1.0] }],
        nodes: [{ id: "frame_0", frame_index: 0, label: "Frame 1", energy: -154.1, status: "converged", geometry_ref: "RESULT/frames/frame_000.xyz" }],
        annotations: []
      });
    }
    throw httpError(404, "unexpected path: " + pathname);
  }

  function makeClient(responder) {
    const calls = [];
    function request(pathname, opts) {
      calls.push({ path: pathname, opts: opts || {} });
      try {
        return Promise.resolve((responder || defaultResponse)(pathname, opts, calls.length));
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
    const mod = sandbox.ACPNextModules["path-view"];
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

  function structureRows(ctx) {
    return withAttr(ctx.host, "data-structure-id");
  }

  function loadViaUi(ctx, id) {
    const input = field(ctx, "job-id");
    input.value = id;
    action(ctx, "load-job").fire("click");
    return flush();
  }

  async function mountReady(options) {
    const ctx = setup(options);
    await mount(ctx);
    await loadViaUi(ctx, "pes-1");
    return ctx;
  }

  // ------------------------------------------------------------- tests --

  await test("module: one ACPNextModules key, Shell-valid descriptor, no legacy global", async () => {
    const sandbox = makeSandbox({});
    loadScript("js/shell/module-registry.js", sandbox);
    loadScript(MODULE_PATH, sandbox);
    const mod = sandbox.ACPNextModules["path-view"];
    assertEq(Object.keys(sandbox.ACPNextModules), ["path-view"], "exactly one module key");
    assertEq(typeof mod.factory, "function", "factory present");
    assertEq(sandbox.ACPShell.registry.validateDescriptor(mod.descriptor), null,
      "descriptor must pass the Shell registry validator");
    assertEq(mod.descriptor.id, "path-view");
    assertEq(mod.descriptor.interfaceVersion, 1);
    assertEq(mod.descriptor.version, "0.1.0");
    assertEq(mod.descriptor.hosts, ["acp-web"]);
    assertEq(mod.descriptor.binds, ["taskRef", "structureRef"]);
    assertEq(mod.descriptor.provides, ["path.structures"]);
    assertEq(mod.descriptor.supportedInstancing, "single");
    assertEq(mod.descriptor.error.titleKey, "next.pathview.errorTitle");
    assert(!("pathViewState" in sandbox), "no legacy page global");
    assert(!("acpPathView" in sandbox), "no second helper global");
    assertEq(typeof mod._internals.buildPathModel, "function");
    assertEq(typeof mod._internals.classifyRole, "function");
    assertEq(typeof mod._internals.i18n, "object");
    const instance = mod.factory({ doc: makeDom().document });
    ["mount", "updateContext", "suspend", "resume", "dispose"].forEach((name) => {
      assertEq(typeof instance[name], "function", "ModuleInstance." + name);
    });
  });

  await test("mount: empty state, zero requests, no measurement/edit control rendered", async () => {
    const ctx = setup();
    await mount(ctx);
    assertEq(root(ctx).className, "acp-pathview");
    assertEq(stateOf(ctx), "empty");
    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(attr(ctx, "data-acp-structure-count"), "0");
    assertEq(attr(ctx, "data-acp-selection-state"), "none");
    assertEq(ctx.client.calls.length, 0, "mount performs no network call");
    assertEq(field(ctx, "empty-state").hidden, false);
    assert(subtreeText(field(ctx, "empty-state")).indexOf("尚未载入路径") !== -1, "zh empty copy");
    assert(subtreeText(root(ctx)).indexOf("路径结构视图") !== -1, "zh title fallback");
    assertEq(findAll(ctx.host, "data-role", "measure").length, 0, "no measurement controls");
    assertEq(findAll(ctx.host, "data-action", "apply-edit").length, 0, "no edit controls");
    assertEq(walk(ctx.host).filter((node) => node.tagName === "CANVAS").length, 0, "no canvas");
  });

  await test("manual load: loading -> ready with the exact read paths and joined rows", async () => {
    const jobDeferred = deferred();
    let jobCalls = 0;
    const client = makeClient((pathname) => {
      if (pathname === JOB("pes-1")) {
        jobCalls += 1;
        if (jobCalls === 1) return jobDeferred.promise;
      }
      return defaultResponse(pathname);
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
    assert(subtreeText(field(ctx, "overlay-message")).indexOf("正在载入") !== -1, "loading copy");

    jobDeferred.resolve(jobPayload("pes-1", "PESsearch"));
    await flush();

    assertEq(stateOf(ctx), "ready");
    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(field(ctx, "overlay").hidden, true);
    assertEq(ctx.client.calls.map((call) => call.path), [
      JOB("pes-1"),
      JOB("pes-1") + "/structure-viewer",
      JOB("pes-1") + "/energy-graph",
      JOB("pes-1") + "/pes/review"
    ]);
    assertEq(attr(ctx, "data-acp-path-kind"), "pes");
    assertEq(attr(ctx, "data-acp-structure-count"), "2");
    assertEq(attr(ctx, "data-acp-step-count"), "3");
    assertEq(subtreeText(field(ctx, "summary-kind")).indexOf("PES 路径") !== -1, true);
    assertEq(subtreeText(field(ctx, "summary-job")), "pes-1");
    assertEq(subtreeText(field(ctx, "summary-workflow")), "PESsearch");
    assertEq(subtreeText(field(ctx, "summary-status")), "COMPLETED");
    assertEq(subtreeText(field(ctx, "summary-revision")), "rev-7");
    assertEq(subtreeText(field(ctx, "summary-review")), "confirmed");
    assertEq(subtreeText(field(ctx, "summary-series")).indexOf("相对能量") !== -1, true);

    const rows = structureRows(ctx);
    assertEq(rows.length, 2, "two stationary structures");
    const tsRow = rows[0];
    assertEq(tsRow.getAttribute("data-structure-role"), "ts");
    assertEq(tsRow.getAttribute("data-structure-id"), "pes_ts_frame_005");
    assertEq(find(tsRow, "data-role", "structure-frame").textContent, "5");
    const tsEnergy = find(tsRow, "data-role", "structure-energy");
    assertEq(tsEnergy.textContent, "12.1000 kcal/mol");
    assertEq(tsEnergy.getAttribute("data-energy-value"), "12.1");
    assertEq(find(tsRow, "data-role", "structure-source").getAttribute("data-candidate-id"),
      "pes_ts_frame_005");
    assertEq(rows[1].getAttribute("data-structure-role"), "intermediate");
    assertEq(find(rows[1], "data-role", "structure-frame").textContent, "8");

    assertEq(withAttr(ctx.host, "data-step-key").length, 3, "three frame steps");
    const provenance = findAll(ctx.host, "data-role", "provenance-value").map((node) => node.textContent);
    assert(provenance.some((text) => text.indexOf("revision=2") !== -1), "review revision provenance");
    assert(provenance.some((text) => text.indexOf("final_profile") !== -1), "review frame source");
    assert(provenance.some((text) => text.indexOf("scan_complete=true") !== -1), "review scan completeness");
    assert(provenance.some((text) => text.indexOf("相对能量") !== -1), "energy series provenance");
    ctx.instance.dispose();
  });

  await test("PES pending: explicit empty state, no invented candidate rows", async () => {
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === JOB("pes-1") + "/pes/review") {
          return { job_id: "pes-1", status: "pending", review: {}, backups: [] };
        }
        if (pathname === JOB("pes-1") + "/structure-viewer") {
          return catalogPayload({ entries: [], groups: [], default_entry_id: null, warnings: ["No PES entries found in recommendations or review"] });
        }
        if (pathname === JOB("pes-1") + "/energy-graph") {
          return energyPayload({ nodes: [], annotations: [], series: [] });
        }
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await loadViaUi(ctx, "pes-1");
    assertEq(stateOf(ctx), "empty");
    assertEq(attr(ctx, "data-acp-structure-count"), "0");
    assert(subtreeText(field(ctx, "empty-state")).indexOf("尚未确认选点") !== -1, "pending review hint");
    assertEq(withAttr(ctx.host, "data-structure-id").length, 0, "no fake candidates");
    ctx.instance.dispose();
  });

  await test("selection emits the canonical structureRef through commands.openStructure", async () => {
    const calls = [];
    const commands = {
      openStructure(ref) {
        calls.push(ref);
        return { ok: true, via: "instance", targetInstanceId: "viewer-1" };
      }
    };
    const ctx = await mountReady({ deps: { commands } });
    assertEq(attr(ctx, "data-acp-selection-state"), "none");

    const rows = structureRows(ctx);
    find(rows[0], "data-action", "open-structure").fire("click");
    await flush(1);

    assertEq(calls.length, 1, "one navigation command");
    assertEq(calls[0].source, "path-view");
    assertEq(calls[0].kind, "structure-entry");
    assertEq(calls[0].jobId, "pes-1");
    assertEq(calls[0].entryId, "pes_pes_ts_frame_005");
    assertEq(calls[0].frameIndex, 5);
    assertEq(attr(ctx, "data-acp-selection-state"), "sent");
    assertEq(JSON.parse(attr(ctx, "data-acp-selected-ref")), {
      kind: "structure-entry",
      jobId: "pes-1",
      entryId: "pes_pes_ts_frame_005",
      frameIndex: 5
    });
    assert(subtreeText(field(ctx, "selection")).indexOf("已请求打开") !== -1, "selection receipt");
    assert(subtreeText(field(ctx, "selection")).indexOf("A-45/A-46") !== -1, "ownership note");
    assertEq(attr(ctx, "data-acp-focus"), "", "module never writes the shell focus itself");
    ctx.instance.dispose();
  });

  await test("selection without the injected command is explicit and inert", async () => {
    const ctx = await mountReady({});
    find(structureRows(ctx)[0], "data-action", "open-structure").fire("click");
    await flush(1);
    assertEq(attr(ctx, "data-acp-selection-state"), "unavailable");
    assert(subtreeText(field(ctx, "selection")).indexOf("未注入 commands.openStructure") !== -1, true);
    assertEq(ctx.client.calls.filter((call) => (call.opts.method || "GET") !== "GET").length, 0,
      "no request is sent by a selection");
    ctx.instance.dispose();
  });

  await test("mechanism path: report nodes/edges/roles + provenance, no chemistry inference", async () => {
    const ctx = setup();
    await mount(ctx);
    await loadViaUi(ctx, "mech-1");

    assertEq(stateOf(ctx), "ready");
    assertEq(attr(ctx, "data-acp-path-kind"), "mechanism");
    assertEq(ctx.client.calls.map((call) => call.path), [
      JOB("mech-1"),
      JOB("mech-1") + "/structure-viewer",
      JOB("mech-1") + "/energy-graph",
      STUDIES + "?job_id=mech-1&limit=5",
      STUDIES + "/study-1/report"
    ]);
    const rows = structureRows(ctx);
    assertEq(rows.map((row) => row.getAttribute("data-structure-role")),
      ["reactant", "ts", "intermediate", "ts", "product"]);
    assertEq(withAttr(ctx.host, "data-step-key").length, 2, "two elementary steps");
    const step = withAttr(ctx.host, "data-step-key")[0];
    assertEq(find(step, "data-role", "step-source").textContent, "s1");
    assertEq(find(step, "data-role", "step-sink").textContent, "s2");
    assertEq(find(step, "data-role", "step-ts").textContent, "ts1");
    assertEq(find(step, "data-role", "step-barriers").textContent, "12.5000 / 3.1000");
    const provenance = findAll(ctx.host, "data-role", "provenance-value").map((node) => node.textContent);
    assert(provenance.some((text) => text.indexOf("study-1") !== -1), "study provenance");
    assert(provenance.some((text) => text.indexOf("study_input_signature") !== -1), "report provenance JSON");

    // explicit structure_ref opens; the node without any ref stays disabled.
    find(rows[1], "data-action", "open-structure").fire("click");
    assertEq(attr(ctx, "data-acp-selection-state"), "unavailable",
      "no commands injected -> explicit unavailable");
    assertEq(find(rows[0], "data-action", "open-structure").disabled, true,
      "reactant has no structure ref -> disabled");
    assertEq(find(rows[0], "data-action", "open-structure").getAttribute("data-ref-problem"),
      "next.pathview.ref_missing_title");
    assertEq(find(rows[3], "data-action", "open-structure").disabled, false,
      "entry_id-backed node is openable");
    ctx.instance.dispose();
  });

  await test("mechanism ref: explicit structure_ref/entry_id become canonical refs", async () => {
    const mod = setup().mod;
    const report = mod._internals.normalizeMechanismReport(mechanismReportPayload());
    const ts = report.nodes.find((node) => node.id === "ts1");
    const ts2 = report.nodes.find((node) => node.id === "ts2");
    const s2 = report.nodes.find((node) => node.id === "s2");
    assertEq(mod._internals.structureRefForNode(ts, "mech-1"), {
      kind: "structure-entry", jobId: "mech-1", entryId: "mech_ts_1", frameIndex: 0
    });
    assertEq(mod._internals.structureRefForNode(ts2, "mech-1"), {
      kind: "structure-entry", jobId: "mech-1", entryId: "mech_ts_2", frameIndex: 0
    });
    assertEq(mod._internals.structureRefForNode(s2, "mech-1"), null, "no invented ref");
    assertEq(mod._internals.classifyRole("transition_state"), "ts");
    assertEq(mod._internals.classifyRole("INT"), "intermediate");
    assertEq(mod._internals.classifyRole(""), "unknown");
  });

  await test("scan path: catalog entry energy + frame step order preserved (no client sort)", async () => {
    const ctx = setup();
    await mount(ctx);
    await loadViaUi(ctx, "scan-1");
    assertEq(stateOf(ctx), "ready");
    assertEq(attr(ctx, "data-acp-path-kind"), "scan");
    const rows = structureRows(ctx);
    assertEq(rows.length, 1);
    assertEq(rows[0].getAttribute("data-structure-role"), "intermediate");
    assertEq(find(rows[0], "data-role", "structure-energy").textContent, "-154.100 hartree");
    assertEq(withAttr(ctx.host, "data-step-key").length, 1);
    ctx.instance.dispose();
  });

  await test("empty job path: empty state with the server-empty copy", async () => {
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === JOB("empty-1")) return jobPayload("empty-1", "optimize", { id: "empty-1" });
        if (pathname === JOB("empty-1") + "/structure-viewer") {
          return catalogPayload({ job_id: "empty-1", workflow: "optimize", entries: [], groups: [], warnings: [] });
        }
        if (pathname === JOB("empty-1") + "/energy-graph") {
          return energyPayload({ job_id: "empty-1", nodes: [], series: [], annotations: [] });
        }
        throw httpError(404, "unexpected " + pathname);
      }
    });
    await mount(ctx);
    await loadViaUi(ctx, "empty-1");
    assertEq(stateOf(ctx), "empty");
    assert(subtreeText(field(ctx, "empty-hint")).indexOf("没有为这个任务返回") !== -1, true);
    ctx.instance.dispose();
  });

  await test("error: a failed job read is retryable and recovers", async () => {
    let fail = true;
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === JOB("pes-1") && fail) throw httpError(500, "boom");
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await loadViaUi(ctx, "pes-1");
    assertEq(stateOf(ctx), "error");
    assert(subtreeText(field(ctx, "overlay-message")).indexOf("boom") !== -1, true);
    assertEq(action(ctx, "retry").hidden, false);

    fail = false;
    action(ctx, "retry").fire("click");
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(attr(ctx, "data-acp-structure-count"), "2");
    assert(ctx.client.calls.filter((call) => call.path === JOB("pes-1")).length >= 2, "retry re-fetched");
    ctx.instance.dispose();
  });

  await test("missing endpoint: a failing catalog stops the panel with an explicit error", async () => {
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === JOB("pes-1") + "/structure-viewer") throw httpError(404, "Not Found");
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await loadViaUi(ctx, "pes-1");
    assertEq(stateOf(ctx), "error");
    const text = subtreeText(field(ctx, "overlay-message"));
    assert(text.indexOf("结构目录端点不可用") !== -1, "explicit endpoint error");
    assert(text.indexOf("404") !== -1, "status surfaced");
    assert(text.indexOf("未使用任何替代端点") !== -1, "no invented fallback");
    assertEq(ctx.client.calls.map((call) => call.path), [
      JOB("pes-1"),
      JOB("pes-1") + "/structure-viewer",
      JOB("pes-1") + "/energy-graph",
      JOB("pes-1") + "/pes/review"
    ], "only the whitelisted read routes were requested");
    assertEq(withAttr(ctx.host, "data-structure-id").length, 0, "no fake rows");
    ctx.instance.dispose();
  });

  await test("partial failure: energy/review failures keep the catalog visible as warnings", async () => {
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === JOB("pes-1") + "/energy-graph") throw httpError(500, "graph down");
        if (pathname === JOB("pes-1") + "/pes/review") throw httpError(400, "not a PES job");
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await loadViaUi(ctx, "pes-1");
    assertEq(stateOf(ctx), "ready");
    assertEq(attr(ctx, "data-acp-structure-count"), "2");
    assertEq(attr(ctx, "data-acp-warning-count"), "2");
    const warnings = findAll(ctx.host, "data-role", "warning").map((node) => node.textContent);
    assert(warnings.some((text) => text.indexOf("graph down") !== -1), "energy failure warning");
    assert(warnings.some((text) => text.indexOf("not a PES job") !== -1), "review failure warning");
    assertEq(find(structureRows(ctx)[0], "data-role", "structure-energy").textContent, "—",
      "no fabricated energy when the graph failed");
    ctx.instance.dispose();
  });

  await test("late result after dispose never paints and cannot throw", async () => {
    const jobDeferred = deferred();
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === JOB("pes-1")) return jobDeferred.promise;
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    const input = field(ctx, "job-id");
    input.value = "pes-1";
    action(ctx, "load-job").fire("click");
    await flush(2);
    assertEq(stateOf(ctx), "loading");
    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0, "dispose removed the panel");
    jobDeferred.resolve(jobPayload("pes-1", "PESsearch"));
    await flush();
    assertEq(ctx.host.children.length, 0, "late result did not remount or paint");
    assertEq(ctx.client.calls.filter((call) => call.path.indexOf("/energy-graph") !== -1).length, 0,
      "no secondary requests after dispose");
  });

  await test("late result from a previous job cannot overwrite the newer path", async () => {
    const first = deferred();
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === JOB("pes-1")) return first.promise;
        if (pathname === JOB("scan-1")) return defaultResponse(pathname);
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    const input = field(ctx, "job-id");
    input.value = "pes-1";
    action(ctx, "load-job").fire("click");
    await flush(2);
    input.value = "scan-1";
    action(ctx, "load-job").fire("click");
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(subtreeText(field(ctx, "summary-job")), "scan-1");
    first.resolve(jobPayload("pes-1", "PESsearch"));
    await flush();
    assertEq(subtreeText(field(ctx, "summary-job")), "scan-1", "stale job response dropped");
    assertEq(attr(ctx, "data-acp-path-kind"), "scan");
    ctx.instance.dispose();
  });

  await test("dispose is idempotent and remount restores the default state", async () => {
    const context = makeContext("pes-1");
    const ctx = setup({ context });
    await mount(ctx);
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(context.listenerCount(), 1, "context subscribed");
    assertEq(ctx.dom.document.activeDocListeners(), 1, "language listener registered");

    ctx.instance.dispose();
    ctx.instance.dispose();
    assertEq(context.listenerCount(), 0, "context unsubscribed");
    assertEq(ctx.dom.document.activeDocListeners(), 0, "language listener removed");
    assertEq(ctx.host.children.length, 0);

    const host2 = ctx.dom.document.createElement("div");
    await ctx.instance.mount(host2);
    await flush();
    assertEq(host2.children[0].getAttribute("data-acp-module-state"), "ready");
    assertEq(host2.children[0].getAttribute("data-acp-structure-count"), "2");
    assertEq(context.listenerCount(), 1, "remount re-subscribed");
    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0);
    assertEq(host2.children.length, 0);
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
    assertEq(ctx.client.calls.length, before, "clearing the focus sends no request");

    context.setFocus({ taskRef: { jobId: "scan-1", attempt: 1 } });
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(subtreeText(field(ctx, "summary-job")), "scan-1");
    assertEq(attr(ctx, "data-acp-path-kind"), "scan");
    ctx.instance.dispose();
  });

  await test("focus: a structure-entry structureRef highlights the matching row", async () => {
    const context = makeContext("pes-1");
    const ctx = setup({ context });
    await mount(ctx);
    await flush();
    context.setFocus({
      taskRef: { jobId: "pes-1", attempt: 1 },
      structureRef: { kind: "structure-entry", jobId: "pes-1", entryId: "pes_pes_int_frame_008" }
    });
    await flush();
    assertEq(attr(ctx, "data-acp-focus"), "pes_pes_int_frame_008");
    const focused = findAll(ctx.host, "data-focus", "true");
    assertEq(focused.length, 1, "one highlighted row");
    assertEq(focused[0].getAttribute("data-structure-id"), "pes_int_frame_008");
    ctx.instance.dispose();
  });

  await test("no client seam: explicit error with zero requests", async () => {
    const dom = makeDom();
    const sandbox = makeSandbox({});
    loadScript(MODULE_PATH, sandbox);
    const instance = sandbox.ACPNextModules["path-view"].factory({ doc: dom.document });
    const host = dom.document.createElement("div");
    await instance.mount(host);
    const input = find(host, "data-role", "job-id");
    input.value = "pes-1";
    find(host, "data-action", "load-job").fire("click");
    await flush();
    const rootNode = host.children[0];
    assertEq(rootNode.getAttribute("data-acp-module-state"), "error");
    assertEq(rootNode.getAttribute("data-acp-structure-count"), "0");
    assert(subtreeText(rootNode).indexOf("客户端未注入") !== -1, true);
    instance.dispose();
  });

  await test("pure: paths encode ids and only the six existing routes exist", async () => {
    const mod = setup().mod;
    const internals = mod._internals;
    assertEq(internals.jobPath("a b/c"), API + "/jobs/a%20b%2Fc");
    assertEq(internals.catalogPath("j 1"), API + "/jobs/j%201/structure-viewer");
    assertEq(internals.energyPath("j1"), API + "/jobs/j1/energy-graph");
    assertEq(internals.pesReviewPath("j1"), API + "/jobs/j1/pes/review");
    assertEq(internals.studiesQuery("j 1"), STUDIES + "?job_id=j%201&limit=5");
    assertEq(internals.studyReportPath("s/1"), STUDIES + "/s%2F1/report");
    assertEq(Object.keys(internals.ROUTES).sort(),
      ["catalog", "energy", "jobs", "pesReview", "report", "studies"]);
  });

  await test("pure: catalog/energy/review/mechanism normalization is defensive", async () => {
    const mod = setup().mod;
    const internals = mod._internals;
    const catalog = internals.normalizeCatalog(catalogPayload());
    assertEq(catalog.availability, "ready");
    assertEq(catalog.entries.length, 2);
    assertEq(catalog.entries[0].frameIndex, 5);
    assertEq(catalog.entries[1].role, "endpoint");

    const energy = internals.normalizeEnergyGraph(energyPayload());
    assertEq(energy.defaultSeries.id, "relative_energy");
    assertEq(energy.frames.length, 3);
    assertEq(energy.frames[1].energy, 12.1);
    assertEq(energy.annotations[0].candidateId, "pes_ts_frame_005");

    const review = internals.normalizePesReview(reviewPayload());
    assertEq(review.status, "confirmed");
    assertEq(review.revision, 2);
    assertEq(review.selected[0].role, "ts");
    assertEq(review.source.scanComplete, true);
    assertEq(review.backups.length, 1);

    const report = internals.normalizeMechanismReport(mechanismReportPayload());
    assertEq(report.nodes.length, 5, "network + stationary rows merged by id");
    assertEq(report.steps.length, 2);
    assertEq(report.nodes[1].ref.entryId, "mech_ts_1");
    assertEq(internals.normalizeMechanismReport(null).nodes.length, 0);
    assertEq(internals.normalizeCatalog(null).entries.length, 0);
    assertEq(internals.normalizeEnergyGraph("nope").frames.length, 0);
    assertEq(internals.normalizePesReview(null).status, "pending");
    assertEq(internals.normalizeStudyList({ studies: [{ study_id: "s" }] })[0].id, "s");
  });

  await test("pure: buildPathModel joins without sorting and marks ref problems", async () => {
    const mod = setup().mod;
    const internals = mod._internals;
    const model = internals.buildPathModel({
      job: internals.normalizeJob(jobPayload("pes-1", "PESsearch")),
      catalog: internals.normalizeCatalog(catalogPayload()),
      energy: internals.normalizeEnergyGraph(energyPayload()),
      review: internals.normalizePesReview(reviewPayload())
    });
    assertEq(model.kind, "pes");
    assertEq(model.structures.map((row) => row.key),
      ["catalog:pes_pes_ts_frame_005", "catalog:pes_pes_int_frame_008"], "server order preserved");
    assertEq(model.structures[0].energyValue, 12.1);
    assertEq(model.structures[0].energyUnit, "kcal/mol");
    assertEq(model.structures[0].candidateId, "pes_ts_frame_005");
    assertEq(model.structures[0].ref, {
      kind: "structure-entry", jobId: "pes-1", entryId: "pes_pes_ts_frame_005", frameIndex: 5
    });
    assertEq(model.steps.length, 3);

    const noJob = internals.buildPathModel({
      catalog: internals.normalizeCatalog(catalogPayload()),
      energy: null,
      review: null
    });
    assertEq(noJob.structures[0].ref, null, "no jobId -> no fabricated entry ref");
    assert(noJob.structures[0].refProblem.indexOf("ref_missing") !== -1, true);
  });

  await test("pure: canonicalRef accepts the frozen kinds and rejects invented ones", async () => {
    const mod = setup().mod;
    const internals = mod._internals;
    assertEq(internals.canonicalRef({ kind: "structure-entry", jobId: "j", entryId: "e" }), {
      kind: "structure-entry", jobId: "j", entryId: "e", frameIndex: 0
    });
    assertEq(internals.canonicalRef({ kind: "job-file", jobId: "j", path: "RESULT/a.xyz", frameIndex: 2 }), {
      kind: "job-file", jobId: "j", path: "RESULT/a.xyz", frameIndex: 2
    });
    assertEq(internals.canonicalRef({ kind: "inline-xyz", text: "1\n\nH 0 0 0\n" }), {
      kind: "inline-xyz", text: "1\n\nH 0 0 0\n", frameIndex: 0
    });
    assertEq(internals.canonicalRef({ kind: "batch-item", jobId: "j", entryId: "e" }), null);
    assertEq(internals.canonicalRef({ kind: "structure-entry", jobId: "j" }), null);
    assertEq(internals.canonicalRef(null), null);
    assertEq(internals.refKey({ kind: "structure-entry", jobId: "j", entryId: "e", frameIndex: 0 }),
      "structure-entry|j|e|0");
  });

  await test("i18n: every literal key exists in the local dictionary and mounts resolve", async () => {
    const ctx = setup();
    await mount(ctx);
    const mod = ctx.mod;
    const source = fs.readFileSync(path.resolve(__dirname, "../../js/modules/path-view/module.js"), "utf8");
    const literal = new Set((source.match(/next\.pathview\.[A-Za-z0-9_.]+/g) || [])
      .filter((key) => key.charAt(key.length - 1) !== "."));
    literal.forEach((key) => {
      assert(Object.prototype.hasOwnProperty.call(mod._internals.i18n, key), "missing local key " + key);
    });
    const missing = withAttr(ctx.host, "data-i18n").filter((node) =>
      !node.getAttribute("data-i18n").startsWith("next.pathview."));
    assertEq(missing.length, 0, "all data-i18n keys are module-owned");
    ctx.instance.dispose();
  });

  await test("source pins: no direct network, no storage, no path build, no second viewer", async () => {
    const source = fs.readFileSync(path.resolve(__dirname, "../../js/modules/path-view/module.js"), "utf8");
    [
      "fetch(", "XMLHttpRequest", "sendBeacon", "window.fetch", "localStorage",
      "sessionStorage", "innerHTML", "insertAdjacentHTML", "document.write",
      "$3Dmol", "ACPVisualization", "scene_instance", "createViewer", "canvas",
      "ACPGeometryStore", "structure_editor.js", "geometry_store", "jobCommands",
      "/api/v2", "/preflight", "/frame-candidate", "/batch-optimize",
      "method: \"POST\"", "method: 'POST'"
    ].forEach((needle) => {
      assert(source.indexOf(needle) === -1, "forbidden source token: " + needle);
    });
    assert(source.indexOf("commands.openStructure") !== -1, "the one viewer command is used");
    assertEq(source.match(/method: "GET"/g).length, 6, "the six read calls use GET");
  });
};
