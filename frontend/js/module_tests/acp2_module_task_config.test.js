/* ACP 2.0 module test — task-config (UI-03).
 *
 * Loads the real model + module in the zero-dependency vm sandbox and drives
 * them through a fake DOM and a routed fake client. `fetch` is never defined,
 * so no test can reach the network.
 *
 * Pinned contracts:
 *  1. catalog projection: only active + visible entries, the four simple
 *     workflows marked supported, everything else visible-but-disabled;
 *  2. defaults: profile levels are cloned; profile-less schemas (frequency /
 *     xtb_optimize) are seeded from FIELD_DEFINITIONS, never literals;
 *  3. semantic locks: composite methods force RI/aux off and builtin dispersion
 *     to none, and report notes instead of silently changing values;
 *  4. charge/multiplicity parity is checked against the parsed formula;
 *  5. the request/plan identity is deterministic and the submit body carries
 *     the preflight fingerprint + one Idempotency-Key;
 *  6. module flow: mount → catalog → parse → validate → preflight → submit;
 *     submit is impossible before a preflight, an edit after preflight
 *     invalidates it, and a 400 preflight_stale clears it with a message;
 *  7. dispose removes every listener and late responses write nothing.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const MODEL_PATH = "js/modules/task-config/model.js";
  const MODULE_PATH = "js/modules/task-config/module.js";

  const flush = async (n = 10) => {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
    for (let i = 0; i < 4; i++) await Promise.resolve();
  };

  // ------------------------------------------------------------ fixtures --
  function methodCatalog() {
    const field = (label, zh, type, extra) => Object.assign({ label, label_zh: zh, type }, extra || {});
    return {
      method_meta: {
        "B97-3c": { ri_support: "composite", builtin_dispersion: "D3BJ", default_basis: "mTZVP" },
        "r2SCAN-3c": { ri_support: "composite", builtin_dispersion: null, default_basis: "" },
        "wB97M-V": { ri_support: "user", builtin_dispersion: null, default_basis: "def2-TZVPP" },
      },
      basis_catalog: ["def2-SVP", "def2-TZVP", "mTZVP"],
      field_definitions: {
        functional: field("Functional", "泛函", "select", { supports_custom: true, default: { "*": "r2SCAN-3c" } }),
        basis: field("Basis Set", "基组", "select", { supports_custom: true, default: { "*": "" } }),
        dispersion: field("Dispersion", "色散", "select", { options: ["none", "D3", "D3BJ", "D4"], default: { "*": "D4" } }),
        ri_approximation: field("RI", "RI 近似", "select", { options: ["none", "RIJCOSX"], default: { "*": "none" } }),
        aux_j_basis: field("Aux /J", "辅助 /J", "select", { options: ["AutoAux", "def2/J"], default: { "*": "AutoAux" } }),
        aux_c_basis: field("Aux /C", "辅助 /C", "select", { options: ["AutoAux"], default: { "*": "AutoAux" } }),
        solvent_model: field("Solvent model", "溶剂模型", "select", { options: ["none", "CPCM"], default: { "*": "none" } }),
        solvent: field("Solvent", "溶剂", "select", { options: ["none", "water"], default: { "*": "none" } }),
        grid: field("Grid", "格点", "select", { options: ["", "DEFGRID2"], default: { "*": "" } }),
        scf_convergence: field("SCF", "SCF 收敛", "select", { options: ["Normal", "Tight"], default: { "*": "Tight" } }),
        max_steps: field("Max steps", "最大步数", "int", { default: { "*": 100 } }),
        gfn: field("GFN", "GFN", "select", { options: ["GFN2-xTB", "GFN1-xTB"], default: { "*": "GFN2-xTB" } }),
        opt_level: field("Opt level", "优化级别", "select", { options: ["normal", "tight"], default: { "*": "normal" } }),
      },
      method_schemas: {
        dft_optimize: {
          method_levels: [{
            level_id: "optimize",
            label: "Optimization",
            label_zh: "几何优化",
            required: true,
            allowed_engines: ["orca"],
            fields: ["functional", "basis", "dispersion", "ri_approximation", "aux_j_basis", "aux_c_basis", "solvent_model", "solvent"],
          }],
          profiles: [{
            profile_id: "default",
            label: "Default",
            summary: "r2SCAN-3c Optimization",
            levels: { optimize: { engine: "orca", functional: "r2SCAN-3c", basis: "", dispersion: "none", solvent_model: "none", solvent: "" } },
          }],
        },
        dft_singlepoint: {
          method_levels: [{
            level_id: "single_point",
            label: "Single Point",
            label_zh: "单点",
            required: true,
            allowed_engines: ["orca"],
            fields: ["functional", "basis", "dispersion", "solvent_model", "solvent"],
          }],
          profiles: [{
            profile_id: "default",
            label: "Default",
            summary: "wB97M-V Single Point",
            levels: { single_point: { engine: "orca", functional: "wB97M-V", basis: "def2-TZVPP", dispersion: "none", solvent_model: "none" } },
          }],
        },
        dft_frequency: {
          method_levels: [{
            level_id: "frequency",
            label: "Frequency",
            label_zh: "频率",
            required: true,
            allowed_engines: ["orca"],
            fields: ["functional", "basis", "dispersion", "solvent_model", "solvent"],
          }],
          profiles: [],
        },
        xtb_optimize: {
          method_levels: [{
            level_id: "xtb_opt",
            label: "xTB optimization",
            label_zh: "xTB 优化",
            required: true,
            allowed_engines: ["xtb"],
            fields: ["gfn", "opt_level", "solvent", "max_steps"],
          }],
          profiles: [],
        },
      },
    };
  }

  function workflowCatalog() {
    const entry = (id, schema, extra) => Object.assign({
      id, label: id, label_zh: id, status: "active", visible: true,
      method_schema_id: schema, default_backend: "orca", description: "",
    }, extra || {});
    return {
      workflows: [
        entry("PESsearch", "pes_scan", { requires_source: true, category: "analysis" }),
        entry("singlepoint", "dft_singlepoint", { category: "simple" }),
        entry("optimize", "dft_optimize", { category: "simple" }),
        entry("frequency", "dft_frequency", { category: "simple" }),
        entry("xtb_optimize", "xtb_optimize", { category: "simple", default_backend: "xtb" }),
        entry("mechanism", "mechanism", { status: "retired", visible: false }),
      ],
    };
  }

  const ASSET = {
    asset_id: "str_0001",
    name: "ethanol",
    molecule_name: "ethanol",
    source_type: "smiles",
    original_format: "smiles",
    xyz: "9\n\nC 0.0 0.0 0.0\nC 1.5 0.0 0.0\nO 2.2 1.2 0.0\nH 0.0 1.0 0.0\nH 0.0 -1.0 0.0\nH -0.9 -0.4 0.0\nH 1.5 -1.0 0.0\nH 1.5 0.6 -0.9\nH 2.8 1.2 0.5\n",
    molfile: null,
    has_3d: true,
    charge: 0,
    multiplicity: 1,
    atom_count: 9,
    formula: "C2H6O",
    smiles: "CCO",
    warnings: [],
    errors: [],
  };

  // --------------------------------------------------------------- fake DOM --
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
        innerHTML: "",
        value: "",
        hidden: false,
        disabled: false,
        type: "",
        files: [],
        attrs: {},
        listeners,
        setAttribute(k, v) { this.attrs[k] = String(v); },
        getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
        removeAttribute(k) { delete this.attrs[k]; },
        appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
        replaceChildren(...nodes) {
          this.children.forEach((c) => { c.parentNode = null; });
          this.children = nodes.slice();
          nodes.forEach((n) => { n.parentNode = this; });
        },
        addEventListener(type, fn) {
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type).add(fn);
        },
        removeEventListener(type, fn) {
          if (listeners.has(type)) listeners.get(type).delete(fn);
        },
        fire(type, ev) {
          const set = listeners.get(type);
          if (set) [...set].forEach((fn) => fn(Object.assign({ type, target: this, preventDefault() {} }, ev || {})));
        },
        activeListeners() {
          let n = 0;
          listeners.forEach((s) => { n += s.size; });
          return n;
        },
      };
    }
    const document = {
      createElement: (tag) => makeEl(tag),
      addEventListener(type, fn) {
        if (!docListeners.has(type)) docListeners.set(type, new Set());
        docListeners.get(type).add(fn);
      },
      removeEventListener(type, fn) {
        if (docListeners.has(type)) docListeners.get(type).delete(fn);
      },
      dispatch(type) {
        const set = docListeners.get(type);
        if (set) [...set].forEach((fn) => fn({ type }));
      },
      documentElement: makeEl("html"),
      activeDocListeners() {
        let n = 0;
        docListeners.forEach((s) => { n += s.size; });
        return n;
      },
    };
    return { document };
  }

  function walk(el, out = []) {
    (el.children || []).forEach((c) => { out.push(c); walk(c, out); });
    return out;
  }

  function find(root, attr, value) {
    return walk(root).find((n) => n.getAttribute && n.getAttribute(attr) === value) || null;
  }

  function findAll(root, attr, value) {
    return walk(root).filter((n) => n.getAttribute && n.getAttribute(attr) === value);
  }

  // ------------------------------------------------------------ fake client --
  function makeClient(overrides) {
    overrides = overrides || {};
    const calls = [];
    const drafts = new Map();
    let draftSeq = 0;
    function respond(path, opts) {
      if (overrides.handler) {
        const custom = overrides.handler(path, opts, calls);
        if (custom !== undefined) return custom;
      }
      if (path === "/api/v1/workflow-catalog") return Promise.resolve(workflowCatalog());
      if (path === "/api/v1/method-catalog") return Promise.resolve(methodCatalog());
      if (path === "/api/v1/structures/parse") {
        return Promise.resolve({ ok: true, structures: [ASSET], errors: [], warnings: [], detected_format: "smiles" });
      }
      if (path === "/api/v1/validate-method") {
        return Promise.resolve({ valid: true, errors: [], warnings: [], normalized_levels: {} });
      }
      if (path === "/api/v1/preflight") {
        return Promise.resolve({ feasible: true, reasons: [], plan_digest: "sha256:" + "a".repeat(64), plan_summary: {}, estimate: {} });
      }
      if (path === "/api/v1/jobs") {
        return Promise.resolve({ job_id: "20261030_001_optimize_demo", status: "queued", workflow: "optimize", attempt: 1, plan_digest: "sha256:" + "a".repeat(64) });
      }
      if (path.indexOf("/api/v1/drafts") === 0) {
        if (opts && opts.method === "POST") {
          draftSeq += 1;
          const id = "11111111-1111-4111-8111-" + String(draftSeq).padStart(12, "0");
          drafts.set(id, { draft_id: id, revision: 1, updated_at: "2026-10-30T11:00:00+00:00" });
          return Promise.resolve(drafts.get(id));
        }
        if (opts && opts.method === "PUT") {
          const id = path.split("/").pop();
          const current = drafts.get(id) || { draft_id: id, revision: 1 };
          const next = { draft_id: id, revision: current.revision + 1, updated_at: "2026-10-30T11:05:00+00:00" };
          drafts.set(id, next);
          return Promise.resolve(next);
        }
        if (opts && opts.method === "DELETE") return Promise.resolve({ deleted: true });
        const id = path.split("/").pop();
        if (drafts.has(id)) return Promise.resolve(drafts.get(id));
        const error = new Error("draft not found");
        error.status = 404;
        return Promise.reject(error);
      }
      return Promise.resolve({});
    }
    return {
      calls,
      drafts,
      request(path, opts) {
        calls.push({ path, opts: opts || {} });
        return respond(path, opts || {});
      },
    };
  }

  function makeContext(projectId) {
    const subscribers = [];
    const focus = [];
    let snapshot = { projectId: projectId || "proj-a", revision: 0, taskRef: null, structureRef: null, researchRef: null, networkNodeId: null };
    return {
      focus,
      getSnapshot: () => snapshot,
      subscribe(fn) { subscribers.push(fn); return () => { const i = subscribers.indexOf(fn); if (i >= 0) subscribers.splice(i, 1); }; },
      setFocus(patch) { focus.push(patch); snapshot = Object.assign({}, snapshot, patch, { revision: snapshot.revision + 1 }); subscribers.forEach((fn) => fn(snapshot)); return snapshot; },
      subscriberCount: () => subscribers.length,
    };
  }

  function makeCommands() {
    const opened = [];
    return { opened, openTask(ref) { opened.push(ref); return { ok: true }; } };
  }

  function loadSandbox() {
    const dom = makeDom();
    const storage = (() => {
      const map = new Map();
      return {
        getItem: (k) => (map.has(k) ? map.get(k) : null),
        setItem: (k, v) => { map.set(k, String(v)); },
        removeItem: (k) => { map.delete(k); },
        map,
      };
    })();
    // Debounces fire immediately: the module's only timers are validations and
    // draft autosaves, and a 400/1500 ms wait would just slow the suite down.
    const sandbox = makeSandbox({
      document: dom.document,
      localStorage: storage,
      setTimeout: (fn) => { fn(); return 1; },
      clearTimeout: () => {},
    });
    loadScript(MODEL_PATH, sandbox);
    loadScript(MODULE_PATH, sandbox);
    return { sandbox, document: dom.document };
  }

  // ------------------------------------------------------------------ model --
  await test("model: catalog projection keeps retired/unsupported entries out of the runnable set", () => {
    const { sandbox } = loadSandbox();
    const model = sandbox.ACPNextTaskConfigModel;
    const entries = model.filterWorkflows(workflowCatalog());
    assertEq(entries.map((e) => e.id), ["PESsearch", "singlepoint", "optimize", "frequency", "xtb_optimize"]);
    assertEq(entries.filter((e) => e.supported).map((e) => e.id), ["singlepoint", "optimize", "frequency", "xtb_optimize"]);
    assertEq(model.pickDefaultWorkflow(entries).id, "singlepoint");
    assertEq(model.findWorkflow(entries, "mechanism"), null);
  });

  await test("model: profile levels are cloned; profile-less schemas seed from FIELD_DEFINITIONS", () => {
    const { sandbox } = loadSandbox();
    const model = sandbox.ACPNextTaskConfigModel;
    const catalog = methodCatalog();
    const optimize = model.schemaFor(catalog, "dft_optimize");
    const levels = model.buildDefaultLevels(optimize, catalog, { id: "optimize" }, "default");
    assertEq(levels.optimize.functional, "r2SCAN-3c");
    levels.optimize.functional = "MUTATED";
    assertEq(model.buildDefaultLevels(optimize, catalog, { id: "optimize" }, "default").optimize.functional, "r2SCAN-3c");

    const frequency = model.schemaFor(catalog, "dft_frequency");
    const seeded = model.buildDefaultLevels(frequency, catalog, { id: "frequency" }, "");
    assertEq(seeded.frequency.functional, "r2SCAN-3c");
    assertEq(seeded.frequency.dispersion, "D4");
    assertEq(seeded.frequency.engine, "orca");

    const xtb = model.schemaFor(catalog, "xtb_optimize");
    const xtbLevels = model.buildDefaultLevels(xtb, catalog, { id: "xtb_optimize", backend: "xtb" }, "");
    assertEq(xtbLevels.xtb_opt.engine, "xtb");
    assertEq(xtbLevels.xtb_opt.max_steps, 100);
  });

  await test("model: composite methods lock RI/aux and builtin dispersion, and say so", () => {
    const { sandbox } = loadSandbox();
    const model = sandbox.ACPNextTaskConfigModel;
    const locked = model.applySemanticLocks({
      optimize: { engine: "orca", functional: "B97-3c", basis: "mTZVP", dispersion: "D4", ri_approximation: "RIJCOSX", aux_j_basis: "def2/J", aux_c_basis: "AutoAux" },
    }, methodCatalog());
    assertEq(locked.levels.optimize.ri_approximation, "none");
    assertEq(locked.levels.optimize.aux_j_basis, "");
    assertEq(locked.levels.optimize.aux_c_basis, "");
    assertEq(locked.levels.optimize.dispersion, "none");
    assert(locked.notes.some((n) => n.key === "next.config.note.builtin_dispersion"), "builtin dispersion note");
    assert(locked.notes.some((n) => n.key === "next.config.note.composite_ri"), "composite RI note");
  });

  await test("model: charge/multiplicity parity uses the formula electron count", () => {
    const { sandbox } = loadSandbox();
    const model = sandbox.ACPNextTaskConfigModel;
    assertEq(model.electronCount(ASSET, 0), 26);
    assertEq(model.validateState(0, 1, ASSET).ok, true);
    assertEq(model.validateState(0, 2, ASSET).ok, false);
    assertEq(model.validateState(1, 2, ASSET).ok, true);   // 25 e- → even multiplicity
    assertEq(model.validateState(1.5, 1, ASSET).ok, false);
    assertEq(model.validateState(0, 0, ASSET).ok, false);
    assertEq(model.validateState(0, 1, null).parityChecked, false);
  });

  await test("model: request + plan identity are deterministic; submit adds the fingerprint only", () => {
    const { sandbox } = loadSandbox();
    const model = sandbox.ACPNextTaskConfigModel;
    const catalog = methodCatalog();
    const schema = model.schemaFor(catalog, "dft_optimize");
    const state = {
      entry: { id: "optimize", schemaId: "dft_optimize", supported: true },
      schema,
      profileId: "default",
      levels: { optimize: { engine: "orca", functional: "r2SCAN-3c", basis: "", dispersion: "none", solvent_model: "none" } },
      methodCatalog: catalog,
      asset: ASSET,
      charge: 0,
      multiplicity: 1,
      nproc: 4,
      mem: "2000MB",
      moleculeName: "ethanol",
      projectId: "proj-a",
    };
    const request = model.buildRequest(state);
    assertEq(request.workflow, "optimize");
    assertEq(request.method.schema_id, "dft_optimize");
    assertEq(request.method.profile_id, "default");
    assertEq(request.input.source_type, "xyz_text");
    assertEq(request.input.formula, "C2H6O");
    assertEq(request.input.charge, 0);
    assertEq(request.input.multiplicity, 1);
    assertEq(request.project_id, "proj-a");
    const clone = JSON.parse(JSON.stringify(request));
    assertEq(model.planIdentity(clone), model.planIdentity(request));
    clone.resources.nproc = 8;
    assert(model.planIdentity(clone) !== model.planIdentity(request), "resources participate in the identity");
    const body = model.buildSubmitBody(request, "sha256:" + "b".repeat(64));
    assertEq(body.preflight_fingerprint, "sha256:" + "b".repeat(64));
    assertEq(body.workflow, request.workflow);
  });

  await test("model: draft snapshot round-trips and rejects foreign versions", () => {
    const { sandbox } = loadSandbox();
    const model = sandbox.ACPNextTaskConfigModel;
    const snapshot = model.draftSnapshot({
      entry: { id: "optimize", schemaId: "dft_optimize" },
      profileId: "default",
      levels: { optimize: { engine: "orca", functional: "r2SCAN-3c" } },
      charge: 1,
      multiplicity: 2,
      nproc: 8,
      mem: "8000MB",
      moleculeName: "ethanol",
      asset: ASSET,
    });
    const restored = model.draftToState(JSON.parse(JSON.stringify(snapshot)));
    assertEq(restored.workflowId, "optimize");
    assertEq(restored.charge, 1);
    assertEq(restored.multiplicity, 2);
    assertEq(restored.asset.formula, "C2H6O");
    assertEq(model.draftToState({ version: 99 }), null);
    assertEq(model.draftName({ labelZh: "几何优化" }), "Next · 几何优化");
  });

  // ----------------------------------------------------------------- module --
  await test("module: mount loads catalogs, defaults to a supported workflow and renders the form", async () => {
    const { sandbox, document } = loadSandbox();
    const dom = makeDom();
    const host = dom.document.createElement("div");
    const client = makeClient();
    const context = makeContext("proj-a");
    const commands = makeCommands();
    const instance = sandbox.ACPNextModules["task-config"].factory({ doc: dom.document, request: client.request, context, commands });
    await instance.mount(host, { context, commands });
    await flush();

    assertEq(host.children.length, 1, "one root");
    const root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assert(find(host, "data-role", "workflow") !== null, "workflow select");
    assert(find(host, "data-role", "profile") !== null, "profile select");
    assert(find(host, "data-role", "charge") !== null, "charge input");
    assert(find(host, "data-action", "preflight") !== null, "preflight button");
    assert(find(host, "data-action", "submit") !== null, "submit button");

    const workflowSelect = find(host, "data-role", "workflow");
    assertEq(workflowSelect.children.length, 5, "five active+visible entries");
    assertEq(workflowSelect.children.filter((o) => o.disabled).length, 1, "PESsearch is disabled");
    assertEq(workflowSelect.value, "singlepoint", "default is the first supported workflow");
    assertEq(find(host, "data-action", "submit").disabled, true, "submit starts disabled");
    instance.dispose();
  });

  await test("module: parse → validate → preflight → submit sends the frozen envelope with fingerprint", async () => {
    const { sandbox } = loadSandbox();
    const dom = makeDom();
    const host = dom.document.createElement("div");
    const client = makeClient();
    const context = makeContext("proj-a");
    const commands = makeCommands();
    const instance = sandbox.ACPNextModules["task-config"].factory({ doc: dom.document, request: client.request, context, commands });
    await instance.mount(host, { context, commands });
    await flush();

    const textarea = find(host, "data-role", "structure-text");
    textarea.value = "CCO";
    textarea.fire("input");
    find(host, "data-action", "parse").fire("click");
    await flush();

    assert(client.calls.some((c) => c.path === "/api/v1/structures/parse"), "parse requested");
    const charge = find(host, "data-role", "charge");
    assertEq(charge.value, "0");
    const summary = find(host, "data-role", "structure-summary");
    assert(summary.textContent.indexOf("C2H6O") !== -1, "formula summary");
    assert(context.focus.some((patch) => patch.structureRef && patch.structureRef.kind === "inline-xyz"),
      "parsed geometry is published to the shared viewer");

    find(host, "data-action", "preflight").fire("click");
    await flush();

    const preflight = client.calls.filter((c) => c.path === "/api/v1/preflight").pop();
    assert(!!preflight, "preflight requested");
    const plan = JSON.parse(preflight.opts.body);
    assertEq(plan.workflow, "singlepoint");
    assertEq(plan.input.source_type, "xyz_text");
    assertEq(plan.method.schema_id, "dft_singlepoint");
    assertEq(plan.resources.nproc, 4);
    assert(plan.project_id === "proj-a", "project carried");

    const submitBtn = find(host, "data-action", "submit");
    assertEq(submitBtn.disabled, false, "submit enabled after preflight");
    submitBtn.fire("click");
    await flush();

    const submit = client.calls.filter((c) => c.path === "/api/v1/jobs").pop();
    assert(!!submit, "submit requested");
    const body = JSON.parse(submit.opts.body);
    assertEq(body.preflight_fingerprint, "sha256:" + "a".repeat(64));
    assertEq(body.workflow, "singlepoint");
    assertEq(submit.opts.headers["Idempotency-Key"].indexOf("next-"), 0, "idempotency key");
    const receipt = find(host, "data-role", "receipt");
    assertEq(receipt.hidden, false, "receipt visible");
    assertEq(commands.opened.length, 1, "openTask dispatched");
    assertEq(commands.opened[0].jobId, "20261030_001_optimize_demo");
    instance.dispose();
  });

  await test("module: submit is impossible before preflight and an edit after preflight invalidates it", async () => {
    const { sandbox } = loadSandbox();
    const dom = makeDom();
    const host = dom.document.createElement("div");
    const client = makeClient();
    const context = makeContext("proj-a");
    const instance = sandbox.ACPNextModules["task-config"].factory({ doc: dom.document, request: client.request, context, commands: makeCommands() });
    await instance.mount(host, { context, commands: makeCommands() });
    await flush();

    const textarea = find(host, "data-role", "structure-text");
    textarea.value = "CCO";
    textarea.fire("input");
    find(host, "data-action", "parse").fire("click");
    await flush();

    // No preflight yet: the click must be a no-op, not a POST /jobs.
    find(host, "data-action", "submit").fire("click");
    await flush();
    assertEq(client.calls.filter((c) => c.path === "/api/v1/jobs").length, 0, "no submit before preflight");

    find(host, "data-action", "preflight").fire("click");
    await flush();
    assertEq(find(host, "data-action", "submit").disabled, false);

    // Editing the charge invalidates the accepted preflight immediately.
    const charge = find(host, "data-role", "charge");
    charge.value = "1";
    charge.fire("input");
    assertEq(find(host, "data-action", "submit").disabled, true, "edit disables submit");
    find(host, "data-action", "submit").fire("click");
    await flush();
    assertEq(client.calls.filter((c) => c.path === "/api/v1/jobs").length, 0, "still no submit");
    instance.dispose();
  });

  await test("module: 400 preflight_stale clears the fingerprint and shows the recovery hint", async () => {
    const { sandbox } = loadSandbox();
    const dom = makeDom();
    const host = dom.document.createElement("div");
    const client = makeClient({
      handler(path, opts) {
        if (path === "/api/v1/jobs") {
          const error = new Error("preflight fingerprint does not match the submitted plan");
          error.status = 400;
          error.body = { detail: { code: "preflight_stale", message: "preflight fingerprint does not match" } };
          return Promise.reject(error);
        }
        return undefined;
      },
    });
    const context = makeContext("proj-a");
    const instance = sandbox.ACPNextModules["task-config"].factory({ doc: dom.document, request: client.request, context, commands: makeCommands() });
    await instance.mount(host, { context, commands: makeCommands() });
    await flush();
    const textarea = find(host, "data-role", "structure-text");
    textarea.value = "CCO";
    textarea.fire("input");
    find(host, "data-action", "parse").fire("click");
    await flush();
    find(host, "data-action", "preflight").fire("click");
    await flush();
    find(host, "data-action", "submit").fire("click");
    await flush();
    assertEq(find(host, "data-action", "submit").disabled, true, "fingerprint cleared");
    const receipt = find(host, "data-role", "receipt");
    assertEq(receipt.hidden, false);
    assert(receipt.children.some((n) => String(n.textContent).indexOf("预检之后") !== -1), "recovery hint shown");
    instance.dispose();
  });

  await test("module: dispose unsubscribes, drops listeners and ignores late responses", async () => {
    const { sandbox } = loadSandbox();
    const dom = makeDom();
    const host = dom.document.createElement("div");
    let release;
    const client = makeClient({
      handler(path) {
        if (path === "/api/v1/preflight") {
          return new Promise((resolve) => { release = () => resolve({ feasible: true, reasons: [], plan_digest: "sha256:" + "c".repeat(64) }); });
        }
        return undefined;
      },
    });
    const context = makeContext("proj-a");
    const commands = makeCommands();
    const instance = sandbox.ACPNextModules["task-config"].factory({ doc: dom.document, request: client.request, context, commands });
    await instance.mount(host, { context, commands });
    await flush();
    const textarea = find(host, "data-role", "structure-text");
    textarea.value = "CCO";
    textarea.fire("input");
    find(host, "data-action", "parse").fire("click");
    await flush();
    find(host, "data-action", "preflight").fire("click");
    await flush();

    instance.dispose();
    assertEq(host.children.length, 0, "host cleared");
    assertEq(context.subscriberCount(), 0, "context unsubscribed");
    assertEq(dom.document.activeDocListeners(), 0, "language listener removed");
    if (release) release();
    await flush();
    assertEq(find(host, "data-role", "receipt"), null, "no late DOM write");
  });
};
