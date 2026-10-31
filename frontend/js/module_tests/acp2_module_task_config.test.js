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
 *
 * A-13/A-14/A-15 additions (2026-10-31):
 *  8. structure-source list: history (/structure-sources/recent), local task
 *     artifacts (/jobs/{id}/structure-viewer catalog) and remote files
 *     (/jobs/{id}/remote-files) render ready/empty/error with no fixture rows;
 *  9. multi-frame selection derives every frame from the ONE text response of
 *     the chosen source, clamps out-of-range indexes to the last frame, and
 *     never issues a second geometry request;
 * 10. the emitted structureRef uses exactly the molecule-viewer vocabulary
 *     (job-file / job-remote-file / inline-xyz / structure-entry) through the
 *     context/commands seam, survives task switches (generation guard) and
 *     never POSTs /jobs from the source surface.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const MODEL_PATH = "js/modules/task-config/model.js";
  const MODULE_PATH = "js/modules/task-config/module.js";
  const VIEW_MODEL_PATH = "js/visualization/view_model.js";

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

  // ------------------------------------------------ A-13/A-14 source fixtures --
  const MULTI_XYZ = [
    "3", "frame 1", "C 0.0 0.0 0.0", "C 1.5 0.0 0.0", "O 2.2 1.2 0.0",
    "3", "frame 2", "C 0.0 0.1 0.0", "C 1.6 0.0 0.0", "O 2.3 1.2 0.0",
    "3", "frame 3", "C 0.0 0.2 0.0", "C 1.7 0.0 0.0", "O 2.4 1.2 0.0",
  ].join("\n") + "\n";

  function historySources() {
    return [
      {
        source_id: "job_20261030_001_optimize:RESULT/optimized.xyz",
        job_id: "20261030_001_optimize",
        job_name: "ethanol optimize",
        molecule_name: "ethanol",
        workflow: "optimize",
        project_id: "proj-a",
        completed_at: "2026-10-30T10:00:00+00:00",
        label: "Optimized structure",
        path: "RESULT/optimized.xyz",
        formula: "C2H6O",
        atom_count: 9,
        charge: 0,
        multiplicity: 1,
        remote: false,
        needs_fetch: false,
        job_status: "COMPLETED",
        source_kind: "final",
      },
      {
        source_id: "job_20261030_002_optimize:RESULT/remote.xyz",
        job_id: "20261030_002_optimize",
        job_name: "remote optimize",
        molecule_name: "ethanol-remote",
        workflow: "optimize",
        project_id: "proj-a",
        completed_at: "2026-10-30T09:00:00+00:00",
        label: "Remote optimized",
        path: "RESULT/remote.xyz",
        formula: "C2H6O",
        atom_count: 9,
        charge: 0,
        multiplicity: 1,
        remote: true,
        needs_fetch: false,
        job_status: "COMPLETED",
        source_kind: "final",
      },
    ];
  }

  function structureCatalog() {
    return {
      availability: "ready",
      warnings: [],
      groups: [{ id: "g-result", label: "正式产物", kind: "result" }],
      entries: [
        {
          id: "entry-optimized",
          group_id: "g-result",
          label: "优化结构",
          role: "minimum",
          status: "completed",
          geometry: {
            endpoint: "/api/v1/jobs/20261030_001_optimize/structure-viewer/entries/entry-optimized/geometry",
            format: "xyz",
          },
          energy: { value: -154.1, unit: "hartree", kind: "electronic" },
          source: { kind: "formal_result", geometry_ref: "RESULT/optimized.xyz", frame_index: null },
        },
        {
          id: "entry-no-geometry",
          group_id: "g-result",
          label: "缺少几何",
          role: "",
          status: "failed",
          geometry: { endpoint: "", format: "xyz" },
          energy: { value: -154.0, unit: "hartree", kind: "electronic" },
          source: { kind: "formal_result", geometry_ref: "", frame_index: null },
        },
      ],
    };
  }

  function remoteFiles() {
    return {
      job_id: "20261030_001_optimize",
      node: "node-a",
      remote_dir: "/remote/20261030_001_optimize",
      files: [
        { name: "traj.xyz", size: 4028, mtime: 1761800000, is_dir: false },
        { name: "run.log", size: 120, mtime: 1761800000, is_dir: false },
        { name: "WORK", size: 0, mtime: 1761800000, is_dir: true },
      ],
      truncated: false,
    };
  }

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
      // A-13 structure sources (existing endpoints only; no invented paths).
      if (path.indexOf("/api/v1/structure-sources/recent") === 0) {
        return Promise.resolve({ sources: historySources() });
      }
      if (path.indexOf("/api/v1/structure-sources/") === 0) {
        const sourceId = decodeURIComponent(path.slice("/api/v1/structure-sources/".length));
        return Promise.resolve({
          source_id: sourceId,
          checksum: "sha256:" + "d".repeat(64),
          structure: Object.assign({}, ASSET, {
            name: "source.xyz",
            xyz: MULTI_XYZ,
            formula: "C2H6O",
            atom_count: 3,
          }),
          job_id: "20261030_001_optimize",
          path: "RESULT/optimized.xyz",
          job_status: "COMPLETED",
        });
      }
      if (path.indexOf("/structure-viewer/entries/") !== -1) {
        if (path.indexOf("entry-no-geometry") !== -1) {
          const missing = new Error("Entry entry-no-geometry has no geometry reference");
          missing.status = 404;
          return Promise.reject(missing);
        }
        return Promise.resolve(MULTI_XYZ);
      }
      if (path.indexOf("/structure-viewer") !== -1) return Promise.resolve(structureCatalog());
      if (path.indexOf("/remote-files") !== -1 && path.indexOf("/preview") !== -1) {
        return Promise.resolve({ content: MULTI_XYZ, format: "xyz", truncated: false });
      }
      if (/^\/api\/v1\/jobs\/[^/]+\/remote-files$/.test(path)) return Promise.resolve(remoteFiles());
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

  function makeCommands(context) {
    const opened = [];
    const structures = [];
    return {
      opened,
      structures,
      openTask(ref) { opened.push(ref); return { ok: true }; },
      // Mirrors the shell command: writes the context focus AND would deliver
      // to a mounted viewer. The fake only records + focuses.
      openStructure(ref) {
        structures.push(ref);
        if (context && typeof context.setFocus === "function") {
          context.setFocus({ structureRef: ref }, "task-config");
        }
        return { ok: true };
      },
    };
  }

  function loadSandbox(options) {
    const opts = options || {};
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
    if (opts.viewModel !== false) loadScript(VIEW_MODEL_PATH, sandbox);
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
    const commands = makeCommands(context);
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
    const commands = makeCommands(context);
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
    const commands = makeCommands(context);
    const instance = sandbox.ACPNextModules["task-config"].factory({ doc: dom.document, request: client.request, context, commands });
    await instance.mount(host, { context, commands });
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
    const commands = makeCommands(context);
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
    const commands = makeCommands(context);
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

  // ------------------------------------- A-13/A-14/A-15 structure sources --
  function allByAttr(root, attr) {
    return walk(root).filter((n) => n.getAttribute && n.getAttribute(attr) !== null);
  }

  function sourceIds(host) {
    return allByAttr(host, "data-source-id").map((n) => n.getAttribute("data-source-id"));
  }

  function sourceLabel(host, id) {
    const button = find(host, "data-source-id", id);
    return button ? button.children[0].textContent : null;
  }

  function sourcePanelState(host) {
    const panel = find(host, "data-role", "source-panel");
    return panel ? panel.getAttribute("data-acp-source-state") : null;
  }

  function openSourcePicker(host) {
    const summary = find(host, "data-role", "source-summary");
    assert(!!summary, "source summary rendered");
    summary.fire("click");
  }

  function lastRef(context) {
    const patches = context.focus.filter((p) => p.structureRef);
    return patches.length ? patches[patches.length - 1].structureRef : null;
  }

  async function mountForSources(options) {
    const opts = options || {};
    const env = loadSandbox({ viewModel: opts.viewModel !== false });
    const dom = makeDom();
    const host = dom.document.createElement("div");
    const client = opts.client || makeClient(opts.clientOptions);
    const context = makeContext("proj-a");
    const commands = makeCommands(context);
    const instance = env.sandbox.ACPNextModules["task-config"].factory({
      doc: dom.document, request: client.request, context, commands,
    });
    await instance.mount(host, { context, commands });
    await flush();
    if (opts.jobId) {
      context.setFocus({ taskRef: { jobId: opts.jobId, attempt: 1 } }, "test");
      await flush();
    }
    return { env, dom, host, client, context, commands, instance };
  }

  await test("sources: history list renders completed-task structures with per-source availability", async () => {
    const t = await mountForSources();
    openSourcePicker(t.host);
    await flush();

    assertEq(sourcePanelState(t.host), "ready");
    const ids = sourceIds(t.host);
    assertEq(ids, [
      "history:job_20261030_001_optimize:RESULT/optimized.xyz",
      "history:job_20261030_002_optimize:RESULT/remote.xyz",
    ]);
    const rows = allByAttr(t.host, "data-source-row");
    assertEq(rows.map((row) => row.getAttribute("data-acp-source-state")), ["ready", "ready"]);
    assertEq(sourceLabel(t.host, ids[0]), "Optimized structure");
    const remoteButton = find(t.host, "data-source-id", ids[1]);
    const chip = remoteButton.children.find((child) => child.getAttribute && child.getAttribute("data-acp-source-chip"));
    assert(!!chip, "remote availability chip");
    assertEq(chip.textContent, "远程");
    assertEq(allByAttr(t.host, "data-acp-fixture").length, 0, "no fixture marker on source rows");
    assertEq(t.client.calls.filter((call) => call.path === "/api/v1/jobs").length, 0, "no task submission");
    t.instance.dispose();
  });

  await test("sources: history empty list is an explicit empty state (no fake rows)", async () => {
    const client = makeClient({
      handler(path) {
        if (path.indexOf("/api/v1/structure-sources/recent") === 0) return Promise.resolve({ sources: [] });
        return undefined;
      },
    });
    const t = await mountForSources({ client });
    openSourcePicker(t.host);
    await flush();

    assertEq(sourcePanelState(t.host), "empty");
    assertEq(sourceIds(t.host).length, 0, "no fabricated rows");
    const list = find(t.host, "data-role", "source-list");
    assertEq(list.children.length, 1);
    assertEq(list.children[0].className, "acp-cfg-source-empty");
    assert(list.children[0].textContent.indexOf("没有可引用") !== -1, "empty hint shown");
    t.instance.dispose();
  });

  await test("sources: history error is visible and retry reloads the group", async () => {
    let attempts = 0;
    const client = makeClient({
      handler(path) {
        if (path.indexOf("/api/v1/structure-sources/recent") === 0) {
          attempts += 1;
          if (attempts === 1) {
            const failure = new Error("HTTP 500");
            failure.status = 500;
            failure.body = { detail: "backend down" };
            return Promise.reject(failure);
          }
          return Promise.resolve({ sources: historySources() });
        }
        return undefined;
      },
    });
    const t = await mountForSources({ client });
    openSourcePicker(t.host);
    await flush();

    assertEq(sourcePanelState(t.host), "error");
    const status = find(t.host, "data-role", "source-status");
    assert(status.textContent.indexOf("backend down") !== -1, status.textContent);
    const retry = find(t.host, "data-action", "source-retry");
    assert(!!retry, "retry button rendered");
    retry.fire("click");
    await flush();
    assertEq(attempts, 2);
    assertEq(sourcePanelState(t.host), "ready");
    assertEq(sourceIds(t.host).length, 2);
    t.instance.dispose();
  });

  await test("sources: selecting history rows emits job-file / job-remote-file refs with frames from one text fetch", async () => {
    const t = await mountForSources();
    openSourcePicker(t.host);
    await flush();

    const localId = "history:job_20261030_001_optimize:RESULT/optimized.xyz";
    find(t.host, "data-source-id", localId).fire("click");
    await flush();

    const preview = find(t.host, "data-role", "source-preview");
    assertEq(preview.getAttribute("data-acp-frame-count"), "3");
    assertEq(preview.getAttribute("data-acp-frame-index"), "0");
    assertEq(preview.getAttribute("data-acp-source-kind"), "job-file");
    assertEq(preview.getAttribute("data-acp-source-emitted"), "true");
    assert(preview.textContent.indexOf("1/3") !== -1, preview.textContent);
    assert(preview.textContent.indexOf("20261030_001_optimize") !== -1);
    assert(preview.textContent.indexOf("RESULT/optimized.xyz") !== -1);

    const ref = lastRef(t.context);
    assertEq(ref.kind, "job-file");
    assertEq(ref.jobId, "20261030_001_optimize");
    assertEq(ref.path, "RESULT/optimized.xyz");
    assertEq(ref.frameIndex, 0);
    assertEq(t.commands.structures.length, 1);
    assertEq(t.commands.structures[0].source, "task-config");
    assertEq(t.client.calls.filter((call) =>
      call.path.indexOf("/api/v1/structure-sources/") === 0 &&
      call.path.indexOf("/recent") === -1).length, 1, "one source text fetch");

    const remoteId = "history:job_20261030_002_optimize:RESULT/remote.xyz";
    find(t.host, "data-source-id", remoteId).fire("click");
    await flush();
    const remoteRef = lastRef(t.context);
    assertEq(remoteRef.kind, "job-remote-file");
    assertEq(remoteRef.jobId, "20261030_002_optimize");
    assertEq(remoteRef.frameIndex, 0);

    // Source picking is read-only: no form/parse/submit side effects.
    assertEq(find(t.host, "data-role", "structure-text").value, "");
    assertEq(t.client.calls.filter((call) => call.path === "/api/v1/structures/parse").length, 0);
    assertEq(t.client.calls.filter((call) => call.path === "/api/v1/jobs" && call.opts.method === "POST").length, 0);
    t.instance.dispose();
  });

  await test("sources: local task artifacts come from the structure-viewer catalog and emit structure-entry refs", async () => {
    const t = await mountForSources({ jobId: "20261030_001_optimize" });
    openSourcePicker(t.host);
    await flush();
    find(t.host, "data-source-tab", "local").fire("click");
    await flush();

    assertEq(sourcePanelState(t.host), "ready");
    assertEq(sourceIds(t.host), ["local:entry-optimized", "local:entry-no-geometry"]);
    const missing = find(t.host, "data-source-id", "local:entry-no-geometry");
    assertEq(missing.disabled, true, "entry without geometry is not selectable");
    const missingRow = find(t.host, "data-source-row", "local:entry-no-geometry");
    assertEq(missingRow.getAttribute("data-acp-source-state"), "error");
    assert(missingRow.children.some((child) => child.className === "acp-cfg-source-error"), "per-source error text");

    find(t.host, "data-source-id", "local:entry-optimized").fire("click");
    await flush();
    const preview = find(t.host, "data-role", "source-preview");
    assertEq(preview.getAttribute("data-acp-source-kind"), "structure-entry");
    assertEq(preview.getAttribute("data-acp-frame-count"), "3");
    const ref = lastRef(t.context);
    assertEq(ref, {
      source: "task-config",
      kind: "structure-entry",
      jobId: "20261030_001_optimize",
      entryId: "entry-optimized",
      frameIndex: 0,
    });
    assertEq(t.client.calls.filter((call) => call.path === "/api/v1/jobs").length, 0);
    t.instance.dispose();
  });

  await test("sources: remote structure sources list XYZ files and emit job-remote-file refs", async () => {
    const t = await mountForSources({ jobId: "20261030_001_optimize" });
    openSourcePicker(t.host);
    await flush();
    find(t.host, "data-source-tab", "remote").fire("click");
    await flush();

    assertEq(sourcePanelState(t.host), "ready");
    assertEq(sourceIds(t.host), ["remote:traj.xyz"], "only the remote XYZ structure is listed");
    const button = find(t.host, "data-source-id", "remote:traj.xyz");
    assert(button.children.some((child) => child.className === "acp-cfg-source-meta" &&
      child.textContent.indexOf("KB") !== -1), "size metadata");

    button.fire("click");
    await flush();
    const preview = find(t.host, "data-role", "source-preview");
    assertEq(preview.getAttribute("data-acp-source-kind"), "job-remote-file");
    assertEq(preview.getAttribute("data-acp-frame-count"), "3");
    const ref = lastRef(t.context);
    assertEq(ref, {
      source: "task-config",
      kind: "job-remote-file",
      jobId: "20261030_001_optimize",
      path: "traj.xyz",
      frameIndex: 0,
    });
    assertEq(t.client.calls.filter((call) =>
      call.path.indexOf("/remote-files") !== -1 &&
      call.path.indexOf("/preview") !== -1).length, 1, "one remote preview fetch");
    t.instance.dispose();
  });

  await test("sources: frame selection clamps out-of-range to the last frame and never refetches geometry", async () => {
    const t = await mountForSources({ jobId: "20261030_001_optimize" });
    openSourcePicker(t.host);
    await flush();
    find(t.host, "data-source-tab", "remote").fire("click");
    await flush();
    find(t.host, "data-source-id", "remote:traj.xyz").fire("click");
    await flush();

    const preview = find(t.host, "data-role", "source-preview");
    const select = find(t.host, "data-role", "frame-select");
    const previewCalls = () => t.client.calls.filter((call) =>
      call.path.indexOf("/remote-files") !== -1 && call.path.indexOf("/preview") !== -1).length;
    const before = previewCalls();
    assertEq(before, 1);

    select.value = "99";
    select.fire("change");
    assertEq(preview.getAttribute("data-acp-frame-index"), "2", "clamped to last frame");
    assertEq(preview.getAttribute("data-acp-frame-count"), "3");
    assert(preview.textContent.indexOf("3/3") !== -1, preview.textContent);
    assert(preview.textContent.indexOf("越界") !== -1, "clamp is stated, not silent");
    assertEq(lastRef(t.context).frameIndex, 2);

    select.value = "-5";
    select.fire("change");
    assertEq(preview.getAttribute("data-acp-frame-index"), "0", "negative clamps to first frame");
    assertEq(lastRef(t.context).frameIndex, 0);

    find(t.host, "data-action", "frame-next").fire("click");
    assertEq(lastRef(t.context).frameIndex, 1);
    find(t.host, "data-action", "frame-next").fire("click");
    assertEq(lastRef(t.context).frameIndex, 2);
    find(t.host, "data-action", "frame-next").fire("click");
    assertEq(lastRef(t.context).frameIndex, 2, "clamped at the last frame");
    find(t.host, "data-action", "frame-prev").fire("click");
    assertEq(lastRef(t.context).frameIndex, 1);
    assertEq(previewCalls(), before, "frame switching issues no second geometry request");

    // Selection identity always names the frame AND the source ref.
    assert(preview.getAttribute("data-acp-source-ref").indexOf("job-remote-file:20261030_001_optimize:traj.xyz#1") !== -1);
    t.instance.dispose();
  });

  await test("sources: task switch drops a late local-source response (generation guard)", async () => {
    let releaseStale;
    const stale = structureCatalog();
    stale.entries[0].label = "STALE A";
    stale.entries[1].label = "STALE A 2";
    const client = makeClient({
      handler(path) {
        if (path === "/api/v1/jobs/job-a/structure-viewer") {
          return new Promise((resolve) => { releaseStale = () => resolve(stale); });
        }
        return undefined;
      },
    });
    const t = await mountForSources({ jobId: "job-a", client });
    openSourcePicker(t.host);
    await flush();
    find(t.host, "data-source-tab", "local").fire("click");
    await flush();
    assertEq(sourcePanelState(t.host), "loading");

    t.context.setFocus({ taskRef: { jobId: "job-b", attempt: 1 } }, "test");
    await flush();
    assertEq(sourcePanelState(t.host), "ready");
    if (releaseStale) releaseStale();
    await flush();

    const ids = sourceIds(t.host);
    assert(ids.length > 0, "the new task's rows are painted");
    assertEq(sourceLabel(t.host, ids[0]), "优化结构", "late task-A rows were dropped");
    assert(ids.every((id) => id.indexOf("local:") === 0), "only structure-entry rows");
    t.instance.dispose();
  });

  await test("sources: dispose drops an in-flight source selection (no late DOM write)", async () => {
    let release;
    const client = makeClient({
      handler(path) {
        if (path.indexOf("/api/v1/structure-sources/") === 0 && path.indexOf("/recent") === -1) {
          return new Promise((resolve) => {
            release = () => resolve({ structure: { xyz: MULTI_XYZ, formula: "C2H6O", atom_count: 3 } });
          });
        }
        return undefined;
      },
    });
    const t = await mountForSources({ client });
    openSourcePicker(t.host);
    await flush();
    find(t.host, "data-source-id", "history:job_20261030_001_optimize:RESULT/optimized.xyz").fire("click");
    await flush();

    t.instance.dispose();
    assertEq(t.host.children.length, 0, "host cleared");
    if (release) release();
    await flush();
    assertEq(find(t.host, "data-role", "source-preview"), null, "no late DOM write");
    assertEq(t.commands.structures.length, 0, "no late structureRef emission");
  });

  await test("sources: missing client fails the mount explicitly", async () => {
    const env = loadSandbox();
    const dom = makeDom();
    const host = dom.document.createElement("div");
    const instance = env.sandbox.ACPNextModules["task-config"].factory({ doc: dom.document });
    let failure = null;
    try {
      await instance.mount(host);
    } catch (error) {
      failure = error;
    }
    assert(!!failure, "mount rejects without a transport client");
    assert(String(failure.message).indexOf("ACPClient.transport missing") !== -1, String(failure.message));
  });

  await test("sources: missing view_model parser is a visible error, not a fabricated frame list", async () => {
    const t = await mountForSources({ viewModel: false });
    openSourcePicker(t.host);
    await flush();
    find(t.host, "data-source-id", "history:job_20261030_001_optimize:RESULT/optimized.xyz").fire("click");
    await flush();

    const preview = find(t.host, "data-role", "source-preview");
    assertEq(preview.getAttribute("data-acp-source-state"), "error");
    assert(preview.textContent.indexOf("view_model.js") !== -1, preview.textContent);
    assertEq(find(t.host, "data-role", "frame-select").children.length, 0, "no fabricated frames");
    assertEq(t.commands.structures.length, 0);
    assertEq(t.context.focus.filter((patch) => patch.structureRef).length, 0);
    t.instance.dispose();
  });

  await test("sources: parsed inline-xyz still uses the same structureRef bridge", async () => {
    const t = await mountForSources();
    const textarea = find(t.host, "data-role", "structure-text");
    textarea.value = "CCO";
    textarea.fire("input");
    find(t.host, "data-action", "parse").fire("click");
    await flush();

    assertEq(t.commands.structures.length, 1);
    const ref = t.commands.structures[0];
    assertEq(ref.kind, "inline-xyz");
    assertEq(ref.source, "task-config");
    assert(ref.text.indexOf("C 0.0") !== -1, "inline geometry carried");
    assertEq(lastRef(t.context).kind, "inline-xyz");
    assertEq(t.client.calls.filter((call) => call.path === "/api/v1/jobs" && call.opts.method === "POST").length, 0);
    t.instance.dispose();
  });
};
