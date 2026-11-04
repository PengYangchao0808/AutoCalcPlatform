/* ACP 3.0 module test — task-wizard (Track A / A-01 … A-09).
 *
 * Loads the classic script the Shell loader consumes
 * (window.ACPNextModules["task-wizard"] = { descriptor, factory, _internals })
 * inside the zero-dependency node vm harness with a fake DOM. Every request
 * goes through an injected client seam, so no test can reach the network (the
 * sandbox has no fetch/XMLHttpRequest at all).
 *
 * Pinned contracts:
 *  1. exactly one ACPNextModules key, Shell-valid descriptor, no extra global;
 *  2. four module states (empty/loading/ready/error) and four structure states
 *     (empty/loading/ready/error) on their own attributes;
 *  3. workflow options are projected from the server catalog (active + visible
 *     only, catalog order, first active default, no retired id anywhere);
 *  4. method/basis projection comes from /method-catalog with the catalog's
 *     own option lists and profile defaults;
 *  5. step navigation: back/next, locked step buttons, invalid step state and
 *     a visible reason when next is blocked;
 *  6. A-02 input flows: SMILES → resolve → embed; paste/file → parse (embed
 *     fallback when the asset has no 3D coordinates);
 *  7. parse/validation failures show a visible reason and never advance;
 *  8. missing client fails the mount explicitly (and hides the submit surface);
 *  9. generation/dispose guards: late results never paint, dispose is
 *     idempotent, a remount starts from the empty structure state;
 * 10. A-03: charge/multiplicity seeded from the server structure facts, the
 *     ΣZ − charge parity error is explicit (odd/even, over-high charge,
 *     impossible multiplicity), the typed value is never normalized, and an
 *     invalid state blocks `next`;
 * 11. A-04: cores/memory defaults come from the published capacity block
 *     (never a literal), memory_input needs a server unit and stays inside the
 *     published ceilings, queue names are the node catalog's read-only
 *     projection, and no server authority means no invented default;
 * 12. A-05: POST /api/v1/preflight carries the frozen workflow/input/method/
 *     resources body, its plan_digest is shown as the preflight fingerprint,
 *     400/422 envelopes are mapped to their declared field, infeasible/stale
 *     results block the step, and preflight itself creates no task;
 * 13. A-06: the frozen A-05 plan + `preflight_fingerprint` is posted to the
 *     existing POST /api/v1/jobs through the injected client with an
 *     `Idempotency-Key` memoized per plan identity (retries reuse it, a
 *     receipted plan is never re-sent, a double click sends one request); the
 *     receipt consumes job_id/status/attempt/plan_digest/idempotent_replay and
 *     success navigates only through commands.openTask; 400/409/422/5xx and
 *     transport failures map to readable copy and never fake a receipt;
 * 14. A-08: the submit check dialog summarises the frozen plan, lists genuine
 *     blockers plus preflight advisories as warnings, cancels without a
 *     request, and a blocked confirm can never reach POST /api/v1/jobs;
 * 15. no direct network primitive, no upload surface, no hard-coded
 *     workflow/method id (source pins; /jobs, Idempotency-Key and the A-09
 *     `/drafts` CRUD are the only seams and all flow through the injected
 *     client);
 * 16. the A-03/A-04/A-05/A-06/A-08/A-09 chrome emits only shared i18n keys
 *     that exist in both shell dictionaries (the module adds no private key);
 * 17. the pure seams (electron count, memory grammar, resource projection,
 *     preflight body/identity/error mapping, submit body/receipt/error mapping,
 *     submit check) are total functions;
 * 18. A-09: the draft snapshot is presentation/input state only (never task
 *     refs, credentials, a resolved structure, preflight or receipt state);
 *     autosave is debounced and CAS-ed (`expected_revision`); the restore
 *     banner waits for the catalog; explicit discard and submit-success
 *     delete the row; a failed post-submit delete is non-fatal and surfaced;
 *     409 `revision_conflict` stops autosave and offers reload/fork; dispose
 *     cancels the debounce and drops every late draft result.
 */
"use strict";

const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const MODULE_PATH = "js/modules/task-wizard/module.js";
  const API = "/api/v1";
  const WATER = "3\nwater\nO 0.000 0.000 0.000\nH 0.758 0.586 0.000\nH -0.758 0.586 0.000\n";
  const ETHANOL_XYZ = "9\nethanol\nC 0.000 0.000 0.000\nC 1.500 0.000 0.000\nO 2.100 1.200 0.000\n" +
    "H -0.500 0.900 0.000\nH -0.500 -0.500 0.900\nH -0.500 -0.500 -0.900\n" +
    "H 1.900 -0.900 0.000\nH 1.900 0.500 0.900\nH 2.700 1.200 0.000\n";

  const WORKFLOWS = [
    { id: "energy", label: "Energy", status: "retired", visible: false, method_schema_id: "dft_singlepoint", default_backend: "orca" },
    { id: "optfreq", label: "Opt+Freq", status: "retired", visible: false, method_schema_id: "dft_singlepoint", default_backend: "orca" },
    {
      id: "optimize", label: "Geometry Optimization", label_zh: "几何优化",
      description: "Optimize molecular geometry", description_zh: "优化分子几何",
      category: "simple", status: "active", visible: true,
      method_schema_id: "dft_optimize", default_backend: "orca"
    },
    {
      id: "singlepoint", label: "Single Point", label_zh: "单点能",
      description: "Single point energy", description_zh: "单点能计算",
      category: "simple", status: "active", visible: true,
      method_schema_id: "dft_singlepoint", default_backend: "orca"
    },
    {
      id: "xtb_optimize", label: "xTB Optimization", label_zh: "xTB 优化",
      description: "Semi-empirical optimization", description_zh: "半经验优化",
      category: "simple", status: "active", visible: true,
      method_schema_id: "xtb_optimize", default_backend: "xtb"
    },
    { id: "benchmark", label: "Benchmark", status: "retired", visible: false, method_schema_id: "dft_singlepoint", default_backend: "orca" },
    { id: "hidden-active", label: "Hidden Active", status: "active", visible: false, method_schema_id: "dft_singlepoint", default_backend: "orca" }
  ];

  const METHOD_CATALOG = {
    basis_catalog: ["def2-SVP", "def2-TZVP", "def2-TZVPP"],
    method_meta: { "wB97M-V": {}, "PBE0": {}, "B3LYP": {} },
    field_definitions: {
      functional: {
        label: "Electronic Structure Method", label_zh: "电子结构方法",
        per_backend: { orca: ["wB97M-V", "PBE0", "B3LYP"], xtb: ["GFN2-xTB"] },
        default: { "*": "wB97M-V" }
      },
      basis: {
        label: "Basis Set", label_zh: "基组",
        per_backend: { orca: ["def2-SVP", "def2-TZVP", "def2-TZVPP"] },
        default: { "*": "def2-TZVPP" }
      },
      gfn: {
        label: "GFN", per_backend: { xtb: ["GFN1-xTB", "GFN2-xTB"] }, default: { "*": "GFN2-xTB" }
      },
      dispersion: {
        label: "Dispersion", options: ["none", "D4"], default: { "*": "none" }
      }
    },
    method_schemas: {
      dft_optimize: {
        method_levels: [{
          level_id: "optimize", label: "Optimization", label_zh: "几何优化",
          allowed_engines: ["orca"], fields: ["functional", "basis", "dispersion", "electronic_state"]
        }],
        profiles: [{
          profile_id: "default", label: "Default (wB97M-V)",
          levels: { optimize: { engine: "orca", functional: "wB97M-V", basis: "def2-TZVPP", dispersion: "none" } }
        }]
      },
      dft_singlepoint: {
        method_levels: [{
          level_id: "single_point", label: "Single Point", label_zh: "单点",
          allowed_engines: ["orca"], fields: ["functional", "basis"]
        }],
        profiles: [
          {
            profile_id: "default", label: "Default",
            levels: { single_point: { engine: "orca", functional: "wB97M-V", basis: "def2-TZVPP" } }
          },
          {
            profile_id: "fast", label: "Fast",
            levels: { single_point: { engine: "orca", functional: "PBE0", basis: "def2-SVP" } }
          }
        ]
      },
      xtb_optimize: {
        method_levels: [{
          level_id: "xtb_opt", label: "xTB Opt",
          allowed_engines: ["xtb"], fields: ["gfn", "opt_level", "solvent_model"]
        }],
        profiles: []
      }
    }
  };

  const XYZ_ASSET = {
    asset_id: "a1",
    name: "water",
    source_type: "paste",
    original_format: "xyz",
    xyz: WATER,
    has_3d: true,
    charge: 0,
    multiplicity: 1,
    atom_count: 3,
    formula: "H2O",
    smiles: null,
    warnings: [],
    errors: []
  };

  /* A-04: the only published resource authority is the Controller discovery
   * read's `capacity` block (acp.api.capacity.build_capacity_projection):
   * available = free right now, limits = hard ceilings. */
  const CAPABILITIES = {
    schema_version: 1,
    capacity: {
      schema: "acp2.capacity.view.v1",
      semantics: "admission-view",
      limits: { max_cores: 32, max_memory_mb: 65536, max_tasks: 8, max_walltime_s: 604800 },
      pool: { cores: 32, memory_mb: 65536, concurrency: 8, source: "cgroup", available: true },
      available: { cores: 6, memory_mb: 24576 },
      unknown: []
    },
    limits: { max_cores: 32, default_walltime_s: 86400, enforcement: "admission" },
    admission: { enforcement: "admission", mode: "local" }
  };

  /* A-04: LSF queue names come from the node catalog, never from the client. */
  const NODES = {
    nodes: [
      { name: "node-a", host: "a.example", status: "online", queue: "normal", running_jobs: 1, max_jobs: 8 },
      { name: "node-b", host: "b.example", status: "degraded", queue: "long", running_jobs: 3, max_jobs: 8 }
    ],
    auto_select: true
  };

  /* ── A-07 fixture: all 15 active catalog entries ───────────────────
   *
   * One catalog row + one method schema per active workflow, plus every
   * retired catalog id the wizard must never offer. The simple four reuse the
   * A-01 fixture above; the other eleven carry the same level ids, field names
   * and select/multi/int/bool widgets the real `catalog.py` publishes.
   */
  const A07_WORKFLOWS = [
    { id: "singlepoint", label: "Single Point", label_zh: "单点能", category: "simple", status: "active", visible: true, method_schema_id: "dft_singlepoint", default_backend: "orca" },
    { id: "optimize", label: "Geometry Optimization", label_zh: "几何优化", category: "simple", status: "active", visible: true, method_schema_id: "dft_optimize", default_backend: "orca" },
    { id: "frequency", label: "Frequency", label_zh: "频率", category: "simple", status: "active", visible: true, method_schema_id: "dft_frequency", default_backend: "orca" },
    { id: "scan", label: "Relaxed Scan", label_zh: "松弛扫描", category: "simple", status: "active", visible: true, method_schema_id: "dft_scan", default_backend: "orca" },
    { id: "irc", label: "Intrinsic Reaction Coordinate", label_zh: "内禀反应坐标", category: "simple", status: "active", visible: true, method_schema_id: "irc", default_backend: "orca" },
    { id: "tsmode", label: "TS Mode Optimization", label_zh: "TS 模式定向优化", category: "simple", status: "active", visible: true, method_schema_id: "tsmode", default_backend: "orca", requires_source: true },
    { id: "casscf", label: "CASSCF / NEVPT2", label_zh: "CASSCF / NEVPT2", category: "simple", status: "active", visible: true, method_schema_id: "casscf", default_backend: "orca" },
    { id: "xtb_optimize", label: "xTB Optimization", label_zh: "xTB 优化", category: "simple", status: "active", visible: true, method_schema_id: "xtb_optimize", default_backend: "xtb" },
    { id: "nmr", label: "NMR + DP4/DP5", label_zh: "NMR + DP4/DP5", category: "preset", status: "active", visible: true, method_schema_id: "nmr", default_backend: "orca" },
    { id: "Confsearch", label: "Conformer Search", label_zh: "构象搜索", category: "preset", status: "active", visible: true, method_schema_id: "confsearch_unified", default_backend: "censo" },
    { id: "PESsearch", label: "PES Search", label_zh: "势能面搜索", category: "preset", status: "active", visible: true, method_schema_id: "pes_scan", default_backend: "orca" },
    { id: "BatchOptimize", label: "Batch Optimization", label_zh: "批量优化", category: "preset", status: "active", visible: true, method_schema_id: "batch_optimize", default_backend: "orca" },
    { id: "XtbPathSearch", label: "xTB PATH Search", label_zh: "xTB PATH", category: "preset", status: "active", visible: true, method_schema_id: "xtb_path_search", default_backend: "xtb" },
    { id: "OrcaGradient", label: "ORCA Gradient", label_zh: "ORCA 梯度", category: "preset", status: "active", visible: true, method_schema_id: "orca_gradient", default_backend: "orca" },
    { id: "XtbConstrainedOpt", label: "xTB Constrained Opt", label_zh: "xTB 约束优化", category: "preset", status: "active", visible: true, method_schema_id: "xtb_constrained_opt", default_backend: "xtb" },
    { id: "hidden-active", label: "Hidden", status: "active", visible: false, method_schema_id: "dft_singlepoint", default_backend: "orca" },
    { id: "energy", label: "Energy", status: "retired", visible: false, method_schema_id: "censo_energy", default_backend: "censo" },
    { id: "optfreq", label: "Opt+Freq", status: "retired", visible: false, method_schema_id: "dft_optfreq", default_backend: "orca" },
    { id: "conformer", label: "Conformer", status: "retired", visible: false, method_schema_id: "confsearch", default_backend: "crest" },
    { id: "benchmark", label: "Benchmark", status: "retired", visible: false, method_schema_id: "benchmark", default_backend: "orca" },
    { id: "ensemble", label: "Ensemble", status: "retired", visible: false, method_schema_id: "censo_ensemble", default_backend: "censo" },
    { id: "mechanism", label: "Mechanism", status: "retired", visible: false, method_schema_id: "mechanism", default_backend: "orca" },
    { id: "Lowconfirm", label: "Low", status: "retired", visible: false, method_schema_id: "low_confirm", default_backend: "orca" },
    { id: "Highconfirm", label: "High", status: "retired", visible: false, method_schema_id: "high_confirm", default_backend: "orca" },
    { id: "optfreqsp", label: "Opt+Freq+SP", status: "retired", visible: false, method_schema_id: "dft_optfreqsp", default_backend: "orca" },
    { id: "xtbmd_censo_energy", label: "xTB-MD CENSO", status: "retired", visible: false, method_schema_id: "xtbmd_censo_energy", default_backend: "censo" },
    { id: "mech-conf", label: "Mech conf", status: "retired", visible: false, method_schema_id: "mech_conf", default_backend: "orca" },
    { id: "mech-step", label: "Mech step", status: "retired", visible: false, method_schema_id: "mech_step", default_backend: "orca" },
    { id: "mech-confirm", label: "Mech confirm", status: "retired", visible: false, method_schema_id: "mech_confirm", default_backend: "orca" },
    { id: "mech-chain", label: "Mech chain", status: "retired", visible: false, method_schema_id: "mech_chain", default_backend: "" }
  ];

  const A07_ACTIVE_IDS = A07_WORKFLOWS
    .filter((entry) => entry.status === "active" && entry.visible !== false)
    .map((entry) => entry.id);

  const A07_FIELD_DEFINITIONS = Object.assign({}, METHOD_CATALOG.field_definitions, {
    functional: Object.assign({}, METHOD_CATALOG.field_definitions.functional, { type: "select" }),
    basis: Object.assign({}, METHOD_CATALOG.field_definitions.basis, { type: "select" }),
    gfn: Object.assign({}, METHOD_CATALOG.field_definitions.gfn, { type: "select" }),
    dispersion: { type: "select", label: "Dispersion", label_zh: "色散", options: ["none", "D4"], default: { "*": "none" } },
    solvent_model: { type: "select", label: "Solvation", label_zh: "溶剂模型", options: ["none", "CPCM"], default: { "*": "none" } },
    solvent: { type: "select", label: "Solvent", label_zh: "溶剂", options: ["none", "water"], default: { "*": "none" } },
    grid: { type: "select", label: "Grid", label_zh: "网格", options: ["DefGrid1", "DefGrid2"], default: { "*": "" } },
    scf_convergence: { type: "select", label: "SCF", label_zh: "SCF", options: ["Normal", "Tight"], default: { "*": "Tight" } },
    ri_approximation: { type: "select", label: "RI", label_zh: "RI", per_backend: { orca: ["none", "RIJCOSX"] }, default: { "*": "none" } },
    aux_j_basis: { type: "select", label: "Aux J", label_zh: "辅助 /J", per_backend: { orca: ["AutoAux"] }, default: { "*": "AutoAux" } },
    aux_c_basis: { type: "select", label: "Aux C", label_zh: "辅助 /C", per_backend: { orca: ["AutoAux"] }, default: { "*": "AutoAux" } },
    max_steps: { type: "int", label: "Max Steps", label_zh: "最大步数", min: 1, max: 1000, default: { "*": 100 }, advanced: true },
    opt_convergence: { type: "select", label: "Convergence", label_zh: "收敛", options: ["Loose", "Normal", "Tight"], default: { "*": "Tight" }, advanced: true },
    recalc_hess: { type: "hessian_interval", label: "Hessian", label_zh: "Hessian", default: { "*": "auto" }, advanced: true },
    opt_level: { type: "select", label: "xTB Opt Level", label_zh: "xTB 优化精度", options: ["crude", "normal", "tight"], default: { "*": "normal" } },
    electronic_state: { type: "module", label: "Electronic State", label_zh: "电子态", default: { "*": { mode: "automatic" } } },
    scan_coordinate_kind: { type: "select", label: "Coordinate Kind", label_zh: "扫描坐标类型", options: ["distance", "angle", "dihedral"], default: { "*": "distance" } },
    scan_bond_type: { type: "select", label: "Bond Type", label_zh: "键类型", options: ["auto", "single", "double"], default: { "*": "auto" } },
    scan_coordinate_start: { type: "float", label: "Scan Start", label_zh: "扫描起点", default: { "*": 1.0 } },
    scan_coordinate_end: { type: "float", label: "Scan End", label_zh: "扫描终点", default: { "*": 3.0 } },
    scan_coordinate_points: { type: "int", label: "Scan Points", label_zh: "扫描点数", min: 3, max: 101, default: { "*": 21 } },
    scan_mode: { type: "select", label: "Scan Mode", label_zh: "扫描模式", options: ["relaxed_scan"], default: { "*": "relaxed_scan" } },
    scan_reuse_previous_geometry: { type: "bool", label: "Reuse Geometry", label_zh: "沿用几何", default: { "*": true } },
    scan_full_scan: { type: "bool", label: "Full Scan", label_zh: "完整扫描", default: { "*": true } },
    scan_use_scants: { type: "bool", label: "ScanTS", label_zh: "ScanTS", default: { "*": false } },
    scan_failure_policy: { type: "select", label: "Failure Policy", label_zh: "失败策略", options: ["retry_previous", "abort"], default: { "*": "retry_previous" } },
    scan_retry_count: { type: "int", label: "Retry Count", label_zh: "重试次数", min: 0, max: 5, default: { "*": 2 } },
    scan_optimizer_method: { type: "select", label: "Scan Optimizer Method", label_zh: "扫描优化方法", per_backend: { orca: ["r2SCAN-3c", "GFN2-xTB"] }, default: { "*": "r2SCAN-3c" } },
    scan_optimizer_basis: { type: "select", label: "Scan Optimizer Basis", label_zh: "扫描优化基组", per_backend: { orca: ["def2-SVP"] }, default: { "*": "def2-SVP" } },
    scan_optimizer_max_iterations: { type: "int", label: "Scan Optimizer Iterations", label_zh: "扫描优化迭代", min: 1, max: 1000, default: { "*": 250 } },
    scan_optimizer_convergence: { type: "select", label: "Scan Convergence", label_zh: "扫描收敛", options: ["Normal", "Tight"], default: { "*": "Normal" } },
    single_point_resume: { type: "bool", label: "Resume SP", label_zh: "单点续算", default: { "*": true } },
    maxpoints: { type: "int", label: "Max Points", label_zh: "最大步数", min: 1, max: 1000, default: { "*": 100 } },
    step: { type: "float", label: "Step", label_zh: "步长", min: 0, default: { "*": 0.1 } },
    method: { type: "select", label: "Method", label_zh: "方法", per_backend: { orca: ["r2SCAN-3c", "wB97M-V"] }, default: { "*": "r2SCAN-3c" } },
    cas_active_electrons: { type: "int", label: "Active Electrons", label_zh: "活性电子数", min: 1, default: { "*": 2 } },
    cas_active_orbitals: { type: "int", label: "Active Orbitals", label_zh: "活性轨道数", min: 1, default: { "*": 2 } },
    cas_dynamic_correlation: { type: "select", label: "Dynamic Correlation", label_zh: "动态相关", options: ["none", "sc_nevpt2"], default: { "*": "none" } },
    cas_nroots: { type: "int", label: "Roots", label_zh: "态数目", min: 1, max: 20, default: { "*": 1 } },
    ewin: { type: "float", label: "Energy Window", label_zh: "能量窗口", min: 0, default: { "*": 6.0 } },
    refinement_threshold: { type: "float", label: "Cutoff", label_zh: "截断", min: 0.01, max: 1.0, default: { "*": 0.99 } },
    nuclei: { type: "select", multi: true, label: "Nuclei", label_zh: "目标核", options: ["1H", "13C", "15N"], default: { "*": ["1H", "13C"] } },
    boltzmann_temp: { type: "float", label: "Temperature", label_zh: "温度", min: 0, default: { "*": 298.15 } },
    protocol: { type: "select", label: "Protocol", label_zh: "协议", options: ["xtb-crest", "censo-crest"], default: { "*": "censo-crest" } },
    confsearch_profile: { type: "select", label: "Quality", label_zh: "精度", options: ["light", "default", "high"], default: { "*": "default" } },
    refinement_policy: { type: "select", label: "Refinement", label_zh: "精修", options: ["screen", "rank1"], default: { "*": "screen" } },
    single_point_method: { type: "select", label: "SP Method", label_zh: "单点方法", per_backend: { orca: ["r2SCAN-3c", "B97-3c"] }, default: { "*": "r2SCAN-3c" } },
    single_point_basis: { type: "select", label: "SP Basis", label_zh: "单点基组", per_backend: { orca: ["def2-SVP"] }, default: { "*": "def2-SVP" } },
    temperature: { type: "float", label: "Temperature", label_zh: "温度", default: { "*": 298.15 } },
    pressure: { type: "float", label: "Pressure", label_zh: "压力", default: { "*": 1.0 } },
    scale_factor: { type: "float", label: "Scale Factor", label_zh: "缩放因子", default: { "*": 0.9905 } },
    opt_max_iter: { type: "int", label: "Opt Max Iter", label_zh: "优化最大迭代", min: 1, max: 1000, default: { "*": 100 } }
  });

  const A07_METHOD_CATALOG = {
    basis_catalog: METHOD_CATALOG.basis_catalog,
    method_meta: METHOD_CATALOG.method_meta,
    field_definitions: A07_FIELD_DEFINITIONS,
    method_schemas: Object.assign({}, METHOD_CATALOG.method_schemas, {
      dft_frequency: {
        method_levels: [{
          level_id: "frequency", label: "Frequency", label_zh: "频率",
          allowed_engines: ["orca"],
          fields: ["functional", "basis", "dispersion", "ri_approximation", "aux_j_basis", "aux_c_basis", "solvent_model", "solvent"]
        }],
        profiles: []
      },
      dft_scan: {
        method_levels: [{
          level_id: "scan", label: "Relaxed Scan", label_zh: "松弛扫描",
          allowed_engines: ["orca"],
          fields: ["functional", "basis", "scan_coordinate_kind", "scan_coordinate_start",
            "scan_coordinate_end", "scan_coordinate_points", "scan_use_scants"]
        }],
        profiles: [{
          profile_id: "default", label: "Default Relaxed Scan",
          levels: { scan: { engine: "orca", functional: "wB97M-V", basis: "def2-SVP", scan_coordinate_kind: "distance", scan_coordinate_start: 1.0, scan_coordinate_end: 2.5, scan_coordinate_points: 11, scan_use_scants: false } }
        }]
      },
      irc: {
        method_levels: [{ level_id: "irc", label: "IRC", allowed_engines: ["orca"], fields: ["maxpoints", "step"] }],
        profiles: [{ profile_id: "default", label: "Default IRC", levels: { irc: { engine: "orca", maxpoints: 40, step: 0.1 } } }]
      },
      tsmode: {
        method_levels: [{
          level_id: "tsmode", label: "TS Mode", allowed_engines: ["orca"],
          fields: ["method", "basis", "max_steps", "recalc_hess"]
        }],
        profiles: [{ profile_id: "default", label: "Default TS Mode", levels: { tsmode: { engine: "orca", method: "r2SCAN-3c", basis: "def2-SVP", max_steps: 80, recalc_hess: "auto" } } }]
      },
      casscf: {
        method_levels: [{
          level_id: "casscf", label: "CASSCF / NEVPT2", allowed_engines: ["orca"],
          fields: ["basis", "cas_active_electrons", "cas_active_orbitals", "cas_dynamic_correlation", "cas_nroots"]
        }],
        profiles: [{ profile_id: "default", label: "CAS(2,2)", levels: { casscf: { engine: "orca", basis: "def2-SVP", cas_active_electrons: 2, cas_active_orbitals: 2, cas_dynamic_correlation: "none", cas_nroots: 1 } } }]
      },
      nmr: {
        method_levels: [
          { level_id: "conformer", label: "Conformer", allowed_engines: ["censo"], fields: ["ewin", "refinement_threshold"] },
          { level_id: "giaoa", label: "GIAO NMR", allowed_engines: ["orca"], fields: ["functional", "basis", "nuclei", "boltzmann_temp"] }
        ],
        profiles: [{
          profile_id: "nmr-goodman", label: "Goodman",
          levels: {
            conformer: { engine: "censo", ewin: 6.0, refinement_threshold: 0.99 },
            giaoa: { engine: "orca", functional: "wB97M-V", basis: "def2-SVP", nuclei: ["1H", "13C"], boltzmann_temp: 298.15 }
          }
        }]
      },
      confsearch_unified: {
        method_levels: [
          { level_id: "confsearch", label: "Conformer Search", allowed_engines: ["crest"], fields: ["protocol", "confsearch_profile", "refinement_policy", "ewin"] },
          { level_id: "dft_opt", label: "DFT Opt", required: false, allowed_engines: ["orca"], fields: ["functional", "basis"] },
          { level_id: "refinement_sp", label: "Refinement SP", allowed_engines: ["orca"], fields: ["functional", "basis"] },
          { level_id: "thermo", label: "Thermo", required: false, allowed_engines: ["shermo"], fields: ["temperature", "pressure"] }
        ],
        profiles: [
          { profile_id: "xtb-crest", label: "xTB + CREST", levels: { confsearch: { engine: "crest", protocol: "xtb-crest", confsearch_profile: "default", refinement_policy: "screen", ewin: 6.0 } } },
          { profile_id: "censo-crest", label: "CREST + CENSO", levels: { confsearch: { engine: "crest", protocol: "censo-crest", confsearch_profile: "default", refinement_policy: "screen", ewin: 6.0 }, refinement_sp: { engine: "orca", functional: "wB97M-V", basis: "def2-TZVPP" } } }
        ]
      },
      pes_scan: {
        method_levels: [
          { level_id: "scan_coordinate", label: "Scan Coordinate", allowed_engines: ["orca"], fields: ["scan_coordinate_kind", "scan_bond_type", "scan_coordinate_start", "scan_coordinate_end", "scan_coordinate_points"] },
          { level_id: "scan_driver", label: "Scan Driver", allowed_engines: ["orca"], fields: ["scan_mode", "scan_reuse_previous_geometry", "scan_full_scan", "scan_failure_policy", "scan_retry_count", "scan_use_scants"] },
          { level_id: "scan_optimizer", label: "Per-Point Optimization", allowed_engines: ["orca"], fields: ["scan_optimizer_method", "scan_optimizer_basis", "scan_optimizer_max_iterations", "scan_optimizer_convergence"] },
          { level_id: "single_point", label: "Single Point", required: false, allowed_engines: ["orca"], fields: ["functional", "basis", "dispersion", "ri_approximation", "single_point_resume"] }
        ],
        profiles: [{
          profile_id: "default", label: "Default PES Scan",
          levels: {
            scan_coordinate: { engine: "orca", scan_coordinate_kind: "distance", scan_bond_type: "auto", scan_coordinate_start: 1.0, scan_coordinate_end: 2.5, scan_coordinate_points: 11 },
            scan_driver: { engine: "orca", scan_mode: "relaxed_scan", scan_reuse_previous_geometry: true, scan_full_scan: true, scan_failure_policy: "retry_previous", scan_retry_count: 2, scan_use_scants: false },
            scan_optimizer: { engine: "orca", scan_optimizer_method: "r2SCAN-3c", scan_optimizer_basis: "def2-SVP", scan_optimizer_max_iterations: 250, scan_optimizer_convergence: "Normal" },
            single_point: { engine: "orca", functional: "wB97M-V", basis: "def2-SVP", dispersion: "none", ri_approximation: "none", single_point_resume: true }
          }
        }]
      },
      batch_optimize: {
        method_levels: [{
          level_id: "batch", label: "Batch Optimization", allowed_engines: ["orca"],
          fields: ["functional", "basis", "single_point_method", "temperature", "pressure", "scale_factor", "opt_max_iter", "electronic_state"]
        }],
        profiles: [
          { profile_id: "opt_only", label: "Optimization Only", levels: { batch: { engine: "orca", functional: "wB97M-V", basis: "def2-SVP", opt_max_iter: 100 } } },
          { profile_id: "opt_freq", label: "Optimization + Frequency", levels: { batch: { engine: "orca", functional: "wB97M-V", basis: "def2-SVP", opt_max_iter: 100, temperature: 298.15 } } }
        ]
      },
      xtb_path_search: {
        method_levels: [{ level_id: "xtb_path", label: "xTB PATH", allowed_engines: ["xtb"], fields: [] }],
        profiles: [{ profile_id: "default", label: "Default xTB PATH", levels: { xtb_path: { engine: "xtb" } } }]
      },
      orca_gradient: {
        method_levels: [{ level_id: "orca_gradient", label: "ORCA Gradient", allowed_engines: ["orca"], fields: [] }],
        profiles: [{ profile_id: "default", label: "Default ORCA Gradient", levels: { orca_gradient: { engine: "orca" } } }]
      },
      xtb_constrained_opt: {
        method_levels: [{ level_id: "xtb_constopt", label: "xTB Constrained Opt", allowed_engines: ["xtb"], fields: [] }],
        profiles: [{ profile_id: "default", label: "Default xTB Constrained Opt", levels: { xtb_constopt: { engine: "xtb" } } }]
      }
    })
  };

  /* A-07 workflow contracts (the same shapes `workflow_contracts.py` publishes).
   * Only the keys the wizard reads are needed: required_any, input/method
   * properties and the one enum used for the PES mode. */
  const A07_CONTRACTS = {
    scan: {
      workflow: "scan", coverage: "partial",
      input: { required_any: ["input", "smiles", "source", "input_artifact", "xyz_text"], properties: { source: {}, source_type: {}, charge: {}, multiplicity: {}, scan_coordinates: {}, coordinate: {}, xyz_text: {} } },
      method: { properties: { levels: {}, schema_id: {}, profile_id: {}, scan_points: {}, scan_use_scants: {} } }
    },
    irc: {
      workflow: "irc", coverage: "partial",
      input: { required_any: ["source_job_id", "source_ref", "source_id"], properties: { source_job_id: {}, source_product_id: {}, source_id: {}, directions: {} } },
      method: { properties: { levels: {}, schema_id: {}, method: {}, functional: {}, basis: {}, maxpoints: {}, step: {} } }
    },
    tsmode: {
      workflow: "tsmode", coverage: "partial",
      input: { required_any: ["source_job_id"], properties: { source_job_id: {}, entry_id: {}, source_mode_index: {}, max_iterations: {}, recalc_hess: {}, trust_radius: {}, retry_limit: {}, final_frequency: {}, level_overrides: {} } },
      method: { properties: {}, additional_properties: false }
    },
    casscf: { workflow: "casscf", coverage: "not_provided" },
    nmr: { workflow: "nmr", coverage: "not_provided" },
    Confsearch: {
      workflow: "Confsearch", coverage: "complete",
      input: { required_any: ["input_artifact", "source", "input", "smiles"], properties: { source: {}, source_type: {}, charge: {}, multiplicity: {}, input: {}, smiles: {}, input_artifact: {} } },
      method: { properties: { levels: {}, schema_id: {}, profile_id: {}, protocol: {}, refinement_policy: {}, confsearch_profile: {}, profile: {} }, enums: { protocol: ["xtb-crest", "censo-crest"], refinement_policy: ["screen", "rank1"] } }
    },
    PESsearch: {
      workflow: "PESsearch", coverage: "partial_bond_length_scan",
      input: { required_any: ["source", "coordinate", "from", "input", "smiles", "input_artifact", "source_job_id", "from_artifact"], properties: { source: { type: "object" }, coordinate: {}, coordinates: {}, protocol: {}, charge: {}, multiplicity: {}, selection: {} } },
      method: { properties: { mode: {}, strategy: {}, select: {}, coordinate_plan: {}, scan_request: {}, schema_id: {}, profile_id: {} }, enums: { mode: ["bond_length_scan"], strategy: ["guided_scan", "reverse_peb", "direct_ts"] } }
    },
    BatchOptimize: {
      workflow: "BatchOptimize", coverage: "complete",
      input: { required_any: ["items", "from_artifact", "items_file", "input", "source"], properties: { source_type: {}, items: {}, charge: {}, multiplicity: {}, source: {} } },
      method: { properties: { levels: {}, schema_id: {}, profile_id: {}, profile: {}, batch_roles: {}, select: {} } }
    },
    XtbPathSearch: {
      workflow: "XtbPathSearch", coverage: "partial",
      input: { required_any: ["path_request"], properties: { path_request: { type: "object" }, request: {}, input: {}, source: {}, charge: {}, multiplicity: {} } },
      method: { properties: { path_request: {} }, additional_properties: false }
    },
    OrcaGradient: {
      workflow: "OrcaGradient", coverage: "partial",
      input: { required_any: ["gradient_request"], properties: { gradient_request: { type: "object" }, request: {}, input: {}, source: {} } },
      method: { properties: { gradient_request: {} }, additional_properties: false }
    },
    XtbConstrainedOpt: {
      workflow: "XtbConstrainedOpt", coverage: "partial",
      input: { required_any: ["constrained_opt_request"], properties: { constrained_opt_request: { type: "object" }, request: {}, input: {}, source: {} } },
      method: { properties: { constrained_opt_request: {} }, additional_properties: false }
    }
  };

  const A07_ADAPTER_BY_ID = {
    singlepoint: "structure", optimize: "structure", frequency: "structure",
    scan: "scan", irc: "source", tsmode: "source", casscf: "structure",
    xtb_optimize: "structure", nmr: "structure", Confsearch: "structure",
    PESsearch: "scan", BatchOptimize: "batch", XtbPathSearch: "payload",
    OrcaGradient: "payload", XtbConstrainedOpt: "payload"
  };

  const A07_METHOD_PROJECTION_BY_ID = {
    singlepoint: "minimal", optimize: "minimal", frequency: "minimal", xtb_optimize: "minimal",
    scan: "extended", irc: "extended", tsmode: "extended", casscf: "extended",
    nmr: "extended", Confsearch: "extended", PESsearch: "extended", BatchOptimize: "extended",
    XtbPathSearch: "extended", OrcaGradient: "extended", XtbConstrainedOpt: "extended"
  };

  /* A-05: the zero-side-effect preflight projection (B1-3 frozen key set). */
  const PREFLIGHT_OK = {
    schema_version: 1,
    feasible: true,
    reasons: [],
    advisories: [],
    plan_digest: "sha256:9f2c1d3a4b5e6f708192a3b4c5d6e7f8",
    plan_summary: { workflow: "optimize", steps: 1, project_id: "proj-a" },
    estimate: { nproc: 6, mem: "24576MB" },
    admission: null,
    would_create_task: false,
    would_reserve: false
  };

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function flush(n = 10) {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await tick();
    for (let i = 0; i < 6; i++) await Promise.resolve();
  }

  // ------------------------------------------------------------ fake DOM --

  function makeDom() {
    const docListeners = new Map();

    function makeEl(tag) {
      const listeners = new Map();
      const node = {
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
        name: "",
        id: "",
        files: null,
        attrs: {},
        listeners,
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
          nodes.forEach((child) => { child.parentNode = this; });
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
          if (set) [...set].forEach((fn) => fn(Object.assign({ type, target: node }, event || {})));
        }
      };
      return node;
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
      dispatchEvent(event) {
        const set = docListeners.get(event && event.type);
        if (set) [...set].forEach((fn) => fn(event));
        return true;
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

  function subtreeText(node) {
    let text = node.textContent || "";
    (node.children || []).forEach((child) => { text += " " + subtreeText(child); });
    return text;
  }

  function collectAttr(node, attr) {
    const out = [];
    walk(node).forEach((child) => {
      const value = child.getAttribute && child.getAttribute(attr);
      if (value != null) out.push(value);
    });
    return out;
  }

  // --------------------------------------------------------------- harness --

  function loadModule(sandbox) {
    loadScript(MODULE_PATH, sandbox);
    return sandbox.ACPNextModules["task-wizard"];
  }

  /* A-09: the server-side draft row (`src/acp/api/drafts.py::_summary`). */
  const DRAFT_STAMP = "2026-10-31T09:15:00.123456+00:00";

  function draftMeta(draftId, revision, stamp) {
    return {
      draft_id: draftId,
      name: "task-wizard",
      project_id: "proj-a",
      workflow: "optimize",
      structure_count: 0,
      revision: revision,
      created_at: stamp || DRAFT_STAMP,
      updated_at: stamp || DRAFT_STAMP
    };
  }

  function defaultResponse(pathname, opts) {
    const method = (opts && opts.method) || "GET";
    if (pathname === API + "/workflow-catalog") return { workflows: WORKFLOWS };
    if (pathname === API + "/method-catalog") return METHOD_CATALOG;
    if (pathname === "/api/capabilities?detail=summary") return CAPABILITIES;
    if (pathname === API + "/nodes") return NODES;
    if (pathname === API + "/preflight") return PREFLIGHT_OK;
    if (pathname === API + "/jobs") return JOB_CREATED;
    if (pathname === API + "/drafts") {
      if (method === "POST") return draftMeta("default-draft", 1);
      return { items: [] };
    }
    if (pathname.indexOf(API + "/drafts/") === 0) {
      const draftId = decodeURIComponent(pathname.slice((API + "/drafts/").length));
      if (method === "DELETE") return { deleted: true };
      return Object.assign(draftMeta(draftId, 1), { snapshot: null });
    }
    if (pathname === API + "/molecule/resolve") {
      return {
        smiles: "CCO", inchi: null, inchikey: null, formula: "C2H6O",
        charge: 0, multiplicity: 1, source: "smiles", valid: true, error: null
      };
    }
    if (pathname === API + "/molecule/embed") {
      return { xyz: ETHANOL_XYZ, smiles: "CCO", formula: "C2H6O", num_atoms: 9, error: null };
    }
    if (pathname === API + "/structures/parse") {
      return { structures: [XYZ_ASSET], errors: [], warnings: [], ok: true, detected_format: "xyz" };
    }
    throw new Error("unexpected path: " + pathname);
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

  function makeContext() {
    const listeners = new Set();
    return {
      getSnapshot() { return { projectId: "proj-a", revision: 1 }; },
      subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
      setFocus() { /* not used by this module */ },
      listenerCount() { return listeners.size; }
    };
  }

  function makeCommands() {
    const commands = { opened: [] };
    commands.openTask = function (ref) { commands.opened.push(ref); };
    return commands;
  }

  function setup(options) {
    options = options || {};
    const dom = makeDom();
    const sandbox = makeSandbox({});
    const mod = loadModule(sandbox);
    const client = options.client || makeClient(options.responder);
    const context = options.context === null ? null : (options.context || makeContext());
    const commands = options.commands === null ? null : (options.commands || makeCommands());
    const host = dom.document.createElement("div");
    const deps = { doc: dom.document, client: { request: client.request } };
    if (options.readFile) deps.readFile = options.readFile;
    if (context) deps.context = context;
    if (commands) deps.commands = commands;
    /* A-09: injected debounce/timer seams so the draft tests never sleep. */
    if (options.draftDebounceMs != null) deps.draftDebounceMs = options.draftDebounceMs;
    if (options.timers) {
      deps.setTimeout = options.timers.setTimeout;
      deps.clearTimeout = options.timers.clearTimeout;
    }
    const instance = mod.factory(deps);
    return { dom, sandbox, mod, client, context, commands, host, deps, instance };
  }

  async function mount(ctx) {
    await ctx.instance.mount(ctx.host, {});
    await flush();
  }

  function root(ctx) {
    return ctx.host.children[0] || null;
  }

  function stateOf(ctx) {
    const node = root(ctx);
    return node ? node.getAttribute("data-acp-module-state") : null;
  }

  function attr(ctx, name) {
    const node = root(ctx);
    return node ? node.getAttribute(name) : null;
  }

  function field(ctx, role) {
    return find(ctx.host, "data-role", role);
  }

  function action(ctx, name) {
    return find(ctx.host, "data-action", name);
  }

  function panelOf(ctx, id) {
    return find(ctx.host, "data-step-panel", id);
  }

  function stepState(ctx, index) {
    const node = find(ctx.host, "data-step-nav", String(index));
    return node ? node.getAttribute("data-step-state") : null;
  }

  function structureState(ctx) {
    const panel = panelOf(ctx, "input");
    return panel ? panel.getAttribute("data-acp-structure-state") : null;
  }

  function optionsOf(select) {
    return (select.children || []).map((option) => option.value);
  }

  function bodyOf(call) {
    return JSON.parse(call.opts.body);
  }

  function callsTo(ctx, endpoint) {
    return ctx.client.calls.filter((call) => call.path === API + endpoint);
  }

  const CAPABILITIES_PATH = "/api/capabilities?detail=summary";

  function callsToPath(ctx, pathname) {
    return ctx.client.calls.filter((call) => call.path === pathname);
  }

  async function advanceToInput(ctx) {
    action(ctx, "next").fire("click");
    await flush();
  }

  async function advanceToReview(ctx) {
    await advanceToInput(ctx);
    await loadSmiles(ctx, "CCO");
    action(ctx, "next").fire("click");
    await flush();
  }

  async function runPreflight(ctx) {
    const button = action(ctx, "preflight");
    assert(!!button, "missing preflight action");
    button.fire("click");
    await flush();
  }

  function setRoleValue(ctx, role, value) {
    const input = field(ctx, role);
    assert(!!input, "missing input " + role);
    input.value = value;
    input.fire("input");
  }

  function roleValue(ctx, role) {
    const input = field(ctx, role);
    return input ? input.value : null;
  }

  function preflightAttr(ctx, name) {
    return attr(ctx, name);
  }

  function reasonTexts(ctx) {
    return findAll(ctx.host, "data-role", "preflight-reason").map((node) => subtreeText(node));
  }

  function fieldError(ctx, name) {
    return walk(ctx.host).find((node) => node.getAttribute &&
      node.getAttribute("data-role") === "field-error" &&
      node.getAttribute("data-field") === name) || null;
  }

  async function switchMode(ctx, mode) {
    const tab = find(ctx.host, "data-input-mode", mode);
    assert(!!tab, "missing input mode tab " + mode);
    tab.fire("click");
    await flush();
  }

  async function loadSmiles(ctx, value) {
    const input = field(ctx, "smiles");
    input.value = value;
    input.fire("input");
    await flush();
    action(ctx, "resolve-smiles").fire("click");
    await flush();
  }

  async function loadPaste(ctx, text) {
    await switchMode(ctx, "paste");
    const area = field(ctx, "structure-text");
    area.value = text;
    area.fire("input");
    await flush();
    action(ctx, "parse-text").fire("click");
    await flush();
  }

  async function chooseFile(ctx, file) {
    await switchMode(ctx, "file");
    const input = field(ctx, "structure-file");
    input.files = [file];
    input.fire("change");
    await flush();
  }

  /* A-06: the V1JobCreatedResponse fields the receipt consumes. */
  const JOB_CREATED = {
    job_id: "20261031_001_optimize",
    status: "QUEUED",
    workflow: "optimize",
    project_id: "proj-a",
    schema_version: 1,
    attempt: 1,
    task_ref: { task_id: "20261031_001_optimize", job_id: "20261031_001_optimize", attempt: 1 },
    plan_digest: PREFLIGHT_OK.plan_digest,
    idempotency_key: "next-wizard-test",
    idempotent_replay: false,
    result: "accepted"
  };

  async function openCheck(ctx) {
    const button = action(ctx, "submit");
    assert(!!button, "missing submit action");
    button.fire("click");
    await flush();
  }

  async function confirmCheck(ctx) {
    const confirm = action(ctx, "submit-check-confirm");
    assert(!!confirm, "missing submit check confirm action");
    confirm.fire("click");
    await flush();
  }

  function checkBlockers(ctx) {
    return findAll(ctx.host, "data-role", "submit-check-blocker")
      .map((node) => ({ code: node.getAttribute("data-code"), field: node.getAttribute("data-field"), text: subtreeText(node) }));
  }

  function checkWarnings(ctx) {
    return findAll(ctx.host, "data-role", "submit-check-warning").map((node) => subtreeText(node));
  }

  function dialogOf(ctx) {
    return field(ctx, "submit-check");
  }

  function submitButton(ctx) {
    return action(ctx, "submit");
  }

  async function reachedCheckedPlan(ctx) {
    await mount(ctx);
    await advanceToReview(ctx);
    await runPreflight(ctx);
  }

  // ------------------------------------------------------- A-07 helpers --

  function a07Contract(id) {
    return A07_CONTRACTS[id] || { workflow: id, coverage: "not_provided" };
  }

  function a07Responder(pathname, opts) {
    if (pathname === API + "/workflow-catalog") return { workflows: A07_WORKFLOWS };
    if (pathname === API + "/method-catalog") return A07_METHOD_CATALOG;
    if (pathname === CAPABILITIES_PATH) return CAPABILITIES;
    if (pathname === API + "/nodes") return NODES;
    if (pathname.indexOf("/api/capabilities?workflow=") === 0) {
      const id = decodeURIComponent(pathname.slice(pathname.indexOf("workflow=") + "workflow=".length));
      return { workflow_contract: a07Contract(id) };
    }
    if (pathname === API + "/preflight") {
      const body = JSON.parse(opts.body);
      return Object.assign({}, PREFLIGHT_OK, {
        plan_summary: { workflow: body.workflow, steps: 1, project_id: "proj-a" }
      });
    }
    if (pathname === API + "/jobs") {
      const body = JSON.parse(opts.body);
      return Object.assign({}, JOB_CREATED, {
        job_id: "20261031_001_" + body.workflow,
        workflow: body.workflow
      });
    }
    return defaultResponse(pathname);
  }

  function a07Setup(options) {
    return setup(Object.assign({ responder: a07Responder }, options || {}));
  }

  async function a07Select(ctx, id) {
    const select = field(ctx, "workflow");
    assert(!!select, "missing workflow select");
    select.value = id;
    select.fire("change");
    await flush();
  }

  async function a07OpenInput(ctx, id) {
    await mount(ctx);
    await a07Select(ctx, id);
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "input", id + ": should reach the input step");
  }

  async function a07FillInput(ctx, id) {
    const adapter = A07_ADAPTER_BY_ID[id];
    if (adapter === "payload") {
      const area = field(ctx, "workflow-payload");
      assert(!!area, id + ": payload textarea");
      area.value = JSON.stringify({ request_version: "fixture", endpoints: [] });
      area.fire("input");
      await flush();
      return;
    }
    if (adapter === "source") {
      const job = field(ctx, "source-job");
      assert(!!job, id + ": source job input");
      job.value = "20261031_001_frequency";
      job.fire("input");
      const product = field(ctx, "source-product");
      if (product) {
        product.value = "ts_candidate";
        product.fire("input");
      }
      const mode = field(ctx, "source-mode-index");
      if (mode) {
        mode.value = "6";
        mode.fire("input");
      }
      await flush();
      return;
    }
    await loadSmiles(ctx, "CCO");
    if (adapter === "scan") {
      const atoms = field(ctx, "scan-atoms");
      assert(!!atoms, id + ": scan atoms input");
      atoms.value = "0,1";
      atoms.fire("input");
      await flush();
    }
  }

  async function a07ReachReview(ctx, id) {
    await a07OpenInput(ctx, id);
    await a07FillInput(ctx, id);
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "review", id + ": should reach the review step");
  }

  async function a07ReachPreflight(ctx, id) {
    await a07ReachReview(ctx, id);
    await runPreflight(ctx);
    assertEq(attr(ctx, "data-acp-preflight-state"), "ready", id + ": preflight should be feasible");
    assertEq(attr(ctx, "data-acp-preflight-valid"), "true", id + ": preflight fingerprint valid");
  }

  function contractCalls(ctx) {
    return ctx.client.calls.filter((call) => call.path.indexOf("/api/capabilities?workflow=") === 0);
  }

  function contractIds(ctx) {
    return contractCalls(ctx).map((call) => decodeURIComponent(call.path.split("workflow=")[1] || ""));
  }

  function a07PreflightBody(ctx) {
    const calls = callsTo(ctx, "/preflight");
    assert(calls.length > 0, "missing preflight request");
    return bodyOf(calls[calls.length - 1]);
  }

  function a07JobBody(ctx) {
    const calls = callsTo(ctx, "/jobs");
    assert(calls.length > 0, "missing submit request");
    return bodyOf(calls[calls.length - 1]);
  }

  // ------------------------------------------------------- A-09 helpers --

  /* A-09: a stateful stand-in for `src/acp/api/drafts.py`. It pins the real
   * endpoint shapes: GET list (newest first), GET detail (summary + snapshot),
   * POST create (revision 1), PUT with `expected_revision` (409
   * revision_conflict on a stale one), DELETE. Every verb can fail on demand. */
  function draftBackend(options) {
    options = options || {};
    const backend = {
      draftId: "11111111-1111-4111-8111-111111111111",
      exists: options.snapshot != null,
      snapshot: options.snapshot || null,
      revision: options.revision || 1,
      updatedAt: options.updatedAt || DRAFT_STAMP,
      listError: null,
      getError: null,
      postError: null,
      putError: null,
      deleteError: null,
      reads: 0,
      deletes: 0,
      writes: [],
      lastBody: null
    };
    backend.responder = function (pathname, opts) {
      const method = (opts && opts.method) || "GET";
      if (pathname === API + "/drafts" && method === "GET") {
        backend.reads += 1;
        if (backend.listError) throw backend.listError;
        if (!backend.exists) return { items: [] };
        return { items: [draftMeta(backend.draftId, backend.revision, backend.updatedAt)] };
      }
      if (pathname === API + "/drafts" && method === "POST") {
        const body = JSON.parse(opts.body);
        backend.writes.push({ method: method, path: pathname, body: body });
        backend.lastBody = body;
        if (backend.postError) throw backend.postError;
        backend.exists = true;
        backend.snapshot = body.snapshot;
        backend.revision += 1;
        return draftMeta(backend.draftId, backend.revision, backend.updatedAt);
      }
      if (pathname.indexOf(API + "/drafts/") === 0) {
        const draftId = decodeURIComponent(pathname.slice((API + "/drafts/").length));
        if (!backend.exists && method !== "DELETE") {
          const error = new Error("draft_not_found");
          error.status = 404;
          error.body = { detail: "draft_not_found" };
          throw error;
        }
        if (method === "GET") {
          backend.reads += 1;
          if (backend.getError) throw backend.getError;
          return Object.assign(draftMeta(draftId, backend.revision, backend.updatedAt), {
            snapshot: backend.snapshot
          });
        }
        if (method === "PUT") {
          const body = JSON.parse(opts.body);
          backend.writes.push({ method: method, path: pathname, body: body });
          backend.lastBody = body;
          if (backend.putError) throw backend.putError;
          if (body.expected_revision !== backend.revision) {
            const error = new Error("revision_conflict");
            error.status = 409;
            error.body = { detail: "revision_conflict" };
            throw error;
          }
          backend.snapshot = body.snapshot;
          backend.revision += 1;
          return draftMeta(draftId, backend.revision, backend.updatedAt);
        }
        if (method === "DELETE") {
          backend.deletes += 1;
          if (backend.deleteError) throw backend.deleteError;
          if (!backend.exists) {
            const error = new Error("draft_not_found");
            error.status = 404;
            error.body = { detail: "draft_not_found" };
            throw error;
          }
          backend.exists = false;
          backend.snapshot = null;
          return { deleted: true };
        }
      }
      return defaultResponse(pathname, opts);
    };
    return backend;
  }

  /* A-09: a snapshot in the module's own wire shape (singlepoint + `fast`). */
  function a09Snapshot(overrides) {
    const base = {
      kind: "task-wizard",
      version: 1,
      step: 1,
      workflow_id: "singlepoint",
      method: {
        profile_id: "fast",
        levels: { single_point: { functional: "PBE0", basis: "def2-SVP" } }
      },
      input: {
        mode: "smiles", smiles: "CCO", text: "", file_name: "",
        charge: "-1", multiplicity: "2"
      },
      resources: { cores: "8", memory: "4096MB" },
      scan_atoms: "",
      payload: ""
    };
    return Object.assign(base, overrides || {});
  }

  function fakeClock() {
    let sequence = 0;
    const timers = new Map();
    return {
      setTimeout(fn, ms) {
        sequence += 1;
        timers.set(sequence, { fn: fn, ms: ms });
        return sequence;
      },
      clearTimeout(id) { timers.delete(id); },
      pending() { return timers.size; },
      async run() {
        const queued = [...timers.entries()];
        timers.clear();
        queued.forEach(([, timer]) => timer.fn());
        await flush();
      }
    };
  }

  function a09Setup(options) {
    options = options || {};
    const clock = options.clock || fakeClock();
    const ctx = setup(Object.assign({ draftDebounceMs: 1000, timers: clock }, options));
    ctx.clock = clock;
    return ctx;
  }

  function draftState(ctx) { return attr(ctx, "data-acp-draft-state"); }
  function draftStatus(ctx) { return attr(ctx, "data-acp-draft-status"); }
  function draftBanner(ctx) { return field(ctx, "draft"); }
  function draftStatusText(ctx) { return subtreeText(field(ctx, "draft-status")); }
  function draftNoticeText(ctx) {
    const node = field(ctx, "draft-notice");
    return node && !node.hidden ? subtreeText(node) : "";
  }
  function draftButton(ctx, role) { return action(ctx, role); }
  function draftWrites(ctx, method) {
    return ctx.client.calls.filter((call) => call.path.indexOf(API + "/drafts") === 0 &&
      ((call.opts && call.opts.method) || "GET") === method);
  }
  function waitForDraft(ctx) {
    return ctx.clock ? ctx.clock.run() : flush();
  }

  // ----------------------------------------------------------------- tests --

  await test("module: one ACPNextModules key, Shell-valid descriptor, no extra global", async () => {
    const sandbox = makeSandbox({});
    loadScript("js/shell/module-registry.js", sandbox);
    const mod = loadModule(sandbox);
    assertEq(Object.keys(sandbox.ACPNextModules), ["task-wizard"], "exactly one module key");
    assertEq(typeof mod.factory, "function", "factory present");
    assertEq(sandbox.ACPShell.registry.validateDescriptor(mod.descriptor), null,
      "descriptor must pass the Shell registry validator");
    assertEq(mod.descriptor.id, "task-wizard");
    assertEq(mod.descriptor.interfaceVersion, 1);
    assertEq(mod.descriptor.version, "0.1.0");
    assertEq(mod.descriptor.hosts, ["acp-web"]);
    assertEq(mod.descriptor.binds, [], "slot-agnostic: no required context field");
    assertEq(mod.descriptor.supportedInstancing, "single");
    assertEq(mod.descriptor.error.titleKey, "next.wizard.errorTitle");
    assert(!("taskWizardState" in sandbox), "no legacy page global");
    assert(!("wizardState" in sandbox), "no classic wizard global");
    assertEq(typeof mod._internals.filterWorkflows, "function", "internals exposed for pure tests");
    assertEq(typeof mod._internals.projectMethod, "function");
  });

  await test("catalog: loading → ready, active+visible only, first active default, no structure request", async () => {
    const ctx = setup();
    const pending = ctx.instance.mount(ctx.host, {});
    assertEq(stateOf(ctx), "loading", "loading state before the catalog resolves");
    await pending;
    await flush();

    assertEq(stateOf(ctx), "ready");
    const catalogCalls = ctx.client.calls.filter((call) =>
      call.path === API + "/workflow-catalog" || call.path === API + "/method-catalog");
    assertEq(catalogCalls.length, 2, "mount issues exactly the two catalog reads");
    assertEq(callsTo(ctx, "/drafts").length, 1,
      "mount also performs the single A-09 draft lookup");
    assertEq(attr(ctx, "data-acp-draft-state"), "empty", "a fresh server has no draft");
    assertEq(field(ctx, "draft").hidden, true, "no restore banner without a draft");
    const select = field(ctx, "workflow");
    assertEq(optionsOf(select), ["optimize", "singlepoint", "xtb_optimize"],
      "retired and hidden entries are dropped, catalog order kept");
    ["energy", "optfreq", "benchmark", "hidden-active"].forEach((id) => {
      assert(optionsOf(select).indexOf(id) === -1, "must not offer " + id);
    });
    assertEq(select.value, "optimize", "default is the first active catalog entry");
    assertEq(attr(ctx, "data-acp-step"), "workflow");
    assertEq(attr(ctx, "data-acp-step-count"), "3");
    assertEq(attr(ctx, "data-acp-step-valid"), "true");
    assertEq(attr(ctx, "data-acp-can-next"), "true");
    assertEq(action(ctx, "back").disabled, true, "back disabled on the first step");
    assertEq(action(ctx, "next").disabled, false);
    assertEq(panelOf(ctx, "workflow").hidden, false);
    assertEq(panelOf(ctx, "input").hidden, true);
    assertEq(panelOf(ctx, "review").hidden, true);
    assertEq(structureState(ctx), "empty");
    assertEq(callsTo(ctx, "/molecule/resolve").length + callsTo(ctx, "/molecule/embed").length +
      callsTo(ctx, "/structures/parse").length, 0, "catalog load resolves no structure");
    assertEq(stepState(ctx, 0), "current");
    assertEq(stepState(ctx, 2), "locked");
    assertEq(find(ctx.host, "data-step-nav", "2").disabled, true);
  });

  await test("catalog: error state with a visible reason, retry recovers", async () => {
    let first = true;
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/workflow-catalog" && first) {
          first = false;
          const error = new Error("catalog down");
          error.status = 500;
          error.body = { detail: "catalog down" };
          throw error;
        }
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    assertEq(stateOf(ctx), "error");
    assert(subtreeText(field(ctx, "catalog-status")).indexOf("目录加载失败") !== -1, "visible catalog error prefix");
    assert(subtreeText(field(ctx, "catalog-status")).indexOf("catalog down") !== -1, "server reason surfaced");
    assertEq(action(ctx, "retry-catalog").hidden, false);
    assertEq(action(ctx, "next").disabled, true);
    assertEq(attr(ctx, "data-acp-step-valid"), "false");

    action(ctx, "retry-catalog").fire("click");
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(optionsOf(field(ctx, "workflow")), ["optimize", "singlepoint", "xtb_optimize"]);
  });

  await test("catalog: only retired/hidden entries is empty state, never a retired fallback", async () => {
    const ctx = setup({
      client: makeClient((pathname) => {
        if (pathname === API + "/workflow-catalog") {
          return { workflows: [WORKFLOWS[0], WORKFLOWS[1], WORKFLOWS[5], WORKFLOWS[6]] };
        }
        return METHOD_CATALOG;
      })
    });
    await mount(ctx);
    assertEq(stateOf(ctx), "empty");
    assertEq(optionsOf(field(ctx, "workflow")), [], "no option is invented");
    assertEq(field(ctx, "workflow").disabled, true);
    assertEq(attr(ctx, "data-acp-can-next"), "false");
    assertEq(action(ctx, "next").disabled, true);
    assert(subtreeText(field(ctx, "nav-hint")).indexOf("没有可用计算类型") !== -1,
      "visible empty-catalog reason");
    assertEq(ctx.mod._internals.pickDefaultWorkflow([]), null);
  });

  await test("method projection: functional/basis options and defaults come from /method-catalog", async () => {
    const ctx = setup();
    await mount(ctx);

    const level = find(ctx.host, "data-level", "optimize");
    assert(!!level, "optimize level rendered");
    const functional = find(ctx.host, "data-field", "functional");
    const basis = find(ctx.host, "data-field", "basis");
    assert(!!functional, "functional select");
    assert(!!basis, "basis select");
    assertEq(optionsOf(functional), ["wB97M-V", "PBE0", "B3LYP"], "functional list is the catalog's");
    assertEq(functional.value, "wB97M-V", "profile default");
    assertEq(optionsOf(basis), METHOD_CATALOG.basis_catalog, "basis list is the catalog's");
    assertEq(basis.value, "def2-TZVPP");
    assertEq(find(ctx.host, "data-role", "profile-wrap").hidden, true, "single profile hides the picker");
    assertEq(findAll(ctx.host, "data-field", "dispersion").length, 0,
      "only method/basis projection belongs to A-01");
    assertEq(subtreeText(root(ctx)).indexOf("wB97M-V") !== -1, true);

    // Edit one projected field through the delegated change handler; the
    // selected value is kept across a full re-render (language change).
    functional.value = "PBE0";
    find(ctx.host, "data-role", "method-levels").fire("change", { target: functional });
    await flush();
    assert(subtreeText(field(ctx, "review-method")).indexOf("PBE0") !== -1,
      "the review summary follows the projected edit");
    ctx.dom.document.dispatchEvent({ type: "acp:langchange", detail: { lang: "en-US" } });
    await flush();
    assertEq(find(ctx.host, "data-field", "functional").value, "PBE0",
      "a re-render keeps the selected method value");
  });

  await test("method projection: profile switch reseeds values; xTB projects GFN, no basis", async () => {
    const ctx = setup();
    await mount(ctx);

    const workflow = field(ctx, "workflow");
    workflow.value = "singlepoint";
    workflow.fire("change");
    await flush();
    assertEq(find(ctx.host, "data-role", "profile-wrap").hidden, false, "two profiles show the picker");
    const profile = field(ctx, "profile");
    assertEq(optionsOf(profile), ["default", "fast"]);
    assertEq(profile.value, "default");
    profile.value = "fast";
    profile.fire("change");
    await flush();
    assertEq(find(ctx.host, "data-field", "functional").value, "PBE0");
    assertEq(find(ctx.host, "data-field", "basis").value, "def2-SVP");

    workflow.value = "xtb_optimize";
    workflow.fire("change");
    await flush();
    const gfn = find(ctx.host, "data-field", "gfn");
    assert(!!gfn, "GFN select for the xTB schema");
    assertEq(optionsOf(gfn), ["GFN1-xTB", "GFN2-xTB"]);
    assertEq(gfn.value, "GFN2-xTB", "schema field default");
    assertEq(findAll(ctx.host, "data-field", "basis").length, 0, "no basis row for xTB");
    assertEq(find(ctx.host, "data-role", "profile-wrap").hidden, true, "profile-less schema");
  });

  await test("steps: back/next, locked review step, invalid state and a visible blocking reason", async () => {
    const ctx = setup();
    await mount(ctx);
    assertEq(attr(ctx, "data-acp-step"), "workflow");
    await advanceToInput(ctx);
    assertEq(attr(ctx, "data-acp-step"), "input");
    assertEq(attr(ctx, "data-acp-max-step"), "1");
    assertEq(stepState(ctx, 0), "complete");
    assertEq(stepState(ctx, 1), "invalid", "the current step shows its invalid state");
    assertEq(stepState(ctx, 2), "locked");
    assertEq(attr(ctx, "data-acp-step-valid"), "false", "input is invalid without a structure");
    assertEq(attr(ctx, "data-acp-can-next"), "false");
    assertEq(action(ctx, "next").disabled, true);
    assert(subtreeText(field(ctx, "nav-hint")).indexOf("请先载入有效的分子结构") !== -1,
      "visible next-blocking reason");
    assertEq(action(ctx, "back").disabled, false);

    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "input", "invalid step must not advance");

    find(ctx.host, "data-step-nav", "2").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "input", "a locked nav step cannot be jumped to");

    await loadSmiles(ctx, "CCO");
    assertEq(attr(ctx, "data-acp-step-valid"), "true");
    assertEq(action(ctx, "next").disabled, false);
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "review");
    assertEq(stepState(ctx, 2), "current");
    assertEq(action(ctx, "next").disabled, true, "nothing after the review step in A-01");
    assert(subtreeText(field(ctx, "nav-hint")).indexOf("尚未预检") !== -1,
      "the review step names preflight as the pending action");
    action(ctx, "back").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "input");
    find(ctx.host, "data-step-nav", "0").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "workflow", "visited steps stay reachable");
  });

  await test("SMILES: resolve → embed through the injected client, then the review summary", async () => {
    const ctx = setup();
    await mount(ctx);
    await advanceToInput(ctx);
    await loadSmiles(ctx, "CCO");

    const resolveCalls = callsTo(ctx, "/molecule/resolve");
    const embedCalls = callsTo(ctx, "/molecule/embed");
    assertEq(resolveCalls.length, 1, "one resolve call");
    assertEq(embedCalls.length, 1, "one embed call");
    assertEq(resolveCalls[0].opts.method, "POST");
    assertEq(resolveCalls[0].opts.headers["Content-Type"], "application/json");
    assertEq(bodyOf(resolveCalls[0]), { smiles: "CCO" }, "minimal declared payload");
    assertEq(resolveCalls[0].opts.body.indexOf("charge") === -1, true, "no invented request fields");
    assertEq(bodyOf(embedCalls[0]), { smiles: "CCO" }, "embed uses the canonical SMILES");
    assertEq(structureState(ctx), "ready");
    assertEq(attr(ctx, "data-acp-can-next"), "true");
    assertEq(attr(ctx, "data-acp-step"), "input", "success does not jump the step by itself");
    const status = subtreeText(field(ctx, "structure-status"));
    assert(status.indexOf("C2H6O") !== -1, status);
    assert(status.indexOf("9 个原子") !== -1, status);
    const summary = subtreeText(field(ctx, "structure-summary"));
    assert(summary.indexOf("电荷 0") !== -1, summary);
    assert(summary.indexOf("自旋多重度 1") !== -1, summary);

    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "review");
    assert(subtreeText(field(ctx, "review-workflow")).indexOf("几何优化") !== -1);
    assert(subtreeText(field(ctx, "review-method")).indexOf("wB97M-V") !== -1);
    assert(subtreeText(field(ctx, "review-method")).indexOf("def2-TZVPP") !== -1);
    assert(subtreeText(field(ctx, "review-structure")).indexOf("C2H6O") !== -1);
  });

  await test("SMILES: an invalid resolve result is visible and blocks the step", async () => {
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/molecule/resolve") {
          return { smiles: null, inchi: null, inchikey: null, formula: null, charge: 0, multiplicity: 1, source: "smiles", valid: false, error: "Invalid SMILES: C1CC" };
        }
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToInput(ctx);
    await loadSmiles(ctx, "C1CC");

    assertEq(structureState(ctx), "error");
    assert(subtreeText(field(ctx, "structure-error")).indexOf("Invalid SMILES: C1CC") !== -1,
      "server parse reason is visible");
    assertEq(callsTo(ctx, "/molecule/embed").length, 0, "invalid SMILES never reaches embed");
    assertEq(attr(ctx, "data-acp-can-next"), "false");
    assertEq(action(ctx, "next").disabled, true);
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "input", "a failed parse must not advance");
  });

  await test("SMILES: an embed failure is visible and blocks the step", async () => {
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/molecule/embed") {
          return { xyz: "", smiles: "CCO", formula: null, num_atoms: 0, error: "RDKit failed to embed input structure" };
        }
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToInput(ctx);
    await loadSmiles(ctx, "CCO");
    assertEq(structureState(ctx), "error");
    assert(subtreeText(field(ctx, "structure-error")).indexOf("RDKit failed to embed input structure") !== -1);
    assertEq(field(ctx, "structure-summary").hidden, true, "no summary for a failed structure");
    assertEq(attr(ctx, "data-acp-can-next"), "false");
  });

  await test("SMILES: a transport rejection is a visible reason, not a silent no-op", async () => {
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/molecule/resolve") throw new Error("network down");
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToInput(ctx);
    await loadSmiles(ctx, "CCO");
    assertEq(structureState(ctx), "error");
    assert(subtreeText(field(ctx, "structure-error")).indexOf("network down") !== -1);
    assertEq(action(ctx, "next").disabled, true);
  });

  await test("paste: /structures/parse with the declared body, ready state, then advance", async () => {
    const ctx = setup();
    await mount(ctx);
    await advanceToInput(ctx);
    await loadPaste(ctx, WATER);

    const parseCalls = callsTo(ctx, "/structures/parse");
    assertEq(parseCalls.length, 1);
    assertEq(bodyOf(parseCalls[0]), { content: WATER, format: "auto", filename: "" });
    assertEq(parseCalls[0].opts.method, "POST");
    assertEq(structureState(ctx), "ready");
    assert(subtreeText(field(ctx, "structure-status")).indexOf("3 个原子") !== -1);
    assert(subtreeText(field(ctx, "structure-summary")).indexOf("H2O") !== -1);
    assertEq(attr(ctx, "data-acp-structure-source"), null, "root carries no structure source");
    assertEq(field(ctx, "structure-summary").getAttribute("data-acp-structure-source"), "paste");
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "review");
  });

  await test("paste: parse failure shows the server reason and does not advance", async () => {
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/structures/parse") {
          return { structures: [], errors: ["No valid XYZ frames found"], warnings: [], ok: false, detected_format: null };
        }
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToInput(ctx);
    await loadPaste(ctx, "not a structure");
    assertEq(structureState(ctx), "error");
    assert(subtreeText(field(ctx, "structure-error")).indexOf("No valid XYZ frames found") !== -1);
    assertEq(callsTo(ctx, "/molecule/embed").length, 0);
    assertEq(attr(ctx, "data-acp-can-next"), "false");
    assertEq(action(ctx, "next").disabled, true);
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "input");
  });

  await test("paste: a 2D molfile asset falls back to /molecule/embed with the molfile", async () => {
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/structures/parse") {
          return {
            structures: [{
              name: "mol", source_type: "paste", original_format: "mol",
              xyz: null, molfile: "MOL BLOCK", has_3d: false,
              charge: 0, multiplicity: 1, atom_count: 3, formula: "C2H6O", smiles: null
            }],
            errors: [], warnings: [], ok: true, detected_format: "mol"
          };
        }
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToInput(ctx);
    await loadPaste(ctx, "MOL BLOCK");
    const embedCalls = callsTo(ctx, "/molecule/embed");
    assertEq(embedCalls.length, 1, "missing geometry triggers exactly one embed");
    assertEq(bodyOf(embedCalls[0]), { molfile: "MOL BLOCK" });
    assertEq(structureState(ctx), "ready");
    assert(subtreeText(field(ctx, "structure-status")).indexOf("9 个原子") !== -1);
  });

  await test("file: read locally then parse with the filename; no upload endpoint is invented", async () => {
    const ctx = setup();
    await mount(ctx);
    await advanceToInput(ctx);
    const file = { name: "water.xyz", text: () => Promise.resolve(WATER) };
    await chooseFile(ctx, file);

    const parseCalls = callsTo(ctx, "/structures/parse");
    assertEq(parseCalls.length, 1);
    assertEq(bodyOf(parseCalls[0]), { content: WATER, format: "auto", filename: "water.xyz" });
    assertEq(structureState(ctx), "ready");
    assert(subtreeText(field(ctx, "file-name")).indexOf("water.xyz") !== -1);
    assertEq(ctx.client.calls.filter((call) => call.path.indexOf("/uploads") !== -1).length, 0,
      "no upload path");
    assertEq(attr(ctx, "data-acp-can-next"), "true");
  });

  await test("file: a read failure is visible, performs no parse and does not advance", async () => {
    const ctx = setup();
    await mount(ctx);
    await advanceToInput(ctx);
    const file = { name: "broken.xyz", text: () => Promise.reject(new Error("disk unavailable")) };
    await chooseFile(ctx, file);
    assertEq(structureState(ctx), "error");
    const errorText = subtreeText(field(ctx, "structure-error"));
    assert(errorText.indexOf("文件读取失败") !== -1, errorText);
    assert(errorText.indexOf("disk unavailable") !== -1, errorText);
    assertEq(callsTo(ctx, "/structures/parse").length, 0);
    assertEq(attr(ctx, "data-acp-can-next"), "false");
  });

  await test("client seam: missing client fails the mount explicitly", async () => {
    const dom = makeDom();
    const sandbox = makeSandbox({});
    const mod = loadModule(sandbox);
    const host = dom.document.createElement("div");
    const instance = mod.factory({ doc: dom.document });
    let failure = null;
    try {
      await instance.mount(host);
    } catch (error) {
      failure = error;
    }
    assert(!!failure, "mount rejects without an injected structure client");
    assert(String(failure.message).indexOf("client missing") !== -1, String(failure.message));
    assertEq(host.children.length, 0, "no DOM is created for a rejected mount");
  });

  await test("invariants: only ACPNextModules is written, no forbidden layer is referenced", async () => {
    const source = fs.readFileSync(path.resolve(__dirname, "..", "modules", "task-wizard", "module.js"), "utf8");
    const writes = source.match(/window\.[A-Za-z_$][\w$]*\s*=/g) || [];
    assertEq(writes.map((write) => write.replace(/\s*=$/, "")), ["window.ACPNextModules"],
      "the module writes exactly one global registry entry");
    [
      "/molecule/resolve",
      "/molecule/embed",
      "/structures/parse",
      "/preflight",
      "/jobs",
      "/api/capabilities?detail=summary",
      "/nodes",
      "/drafts",
      "ACPClient.transport.request",
      "Idempotency-Key",
      "preflight_fingerprint",
      "commands.openTask"
    ].forEach((token) => {
      assert(source.indexOf(token) !== -1, "missing endpoint seam: " + token);
    });
    [
      "fetch(",
      "XMLHttpRequest",
      "localStorage",
      "sessionStorage",
      "$3Dmol",
      "ACPVisualization",
      "scene_instance",
      "createViewer",
      "jobCommands",
      "/uploads",
      "singlepoint",
      "xtb_optimize",
      "casscf",
      "tsmode",
      "optfreq",
      "ensemble",
      "mechanism",
      "benchmark",
      "conformer",
      "xtbmd",
      "Lowconfirm",
      "Highconfirm"
    ].forEach((token) => {
      assertEq(source.indexOf(token), -1, "module must not reference " + token);
    });
  });

  await test("dispose/late: a late structured response never paints, dispose is idempotent, remount is clean", async () => {
    const gate = deferred();
    const context = makeContext();
    const ctx = setup({
      context,
      responder: (pathname) => {
        if (pathname === API + "/molecule/resolve") return gate.promise;
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToInput(ctx);
    await loadSmiles(ctx, "CCO");
    assertEq(structureState(ctx), "loading");

    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0, "dispose removes the module DOM");
    assertEq(context.listenerCount(), 0, "context subscription removed");
    assertEq(ctx.dom.document.activeDocListeners(), 0, "document listeners removed");
    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0, "dispose is idempotent");

    gate.resolve({
      smiles: "CCO", inchi: null, inchikey: null, formula: "C2H6O",
      charge: 0, multiplicity: 1, source: "smiles", valid: true, error: null
    });
    await flush();
    assertEq(ctx.host.children.length, 0, "the late response cannot rebuild the DOM");
    assertEq(callsTo(ctx, "/molecule/embed").length, 0, "no follow-up request after dispose");

    await mount(ctx);
    assertEq(stateOf(ctx), "ready", "remount loads the catalog again");
    assertEq(structureState(ctx), "empty", "remount starts from the empty structure state");
    assertEq(attr(ctx, "data-acp-step"), "workflow", "remount resets the step");
    assertEq(ctx.host.children.length, 1);
    ctx.instance.dispose();
  });

  await test("generation: a response started before a remount cannot paint into the new mount", async () => {
    const gate = deferred();
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/structures/parse") return gate.promise;
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToInput(ctx);
    await loadPaste(ctx, WATER);
    assertEq(structureState(ctx), "loading");
    const oldRoot = root(ctx);

    ctx.instance.dispose();
    await mount(ctx);
    assertEq(root(ctx) === oldRoot, false, "a new DOM root after remount");
    gate.resolve({ structures: [XYZ_ASSET], errors: [], warnings: [], ok: true, detected_format: "xyz" });
    await flush();
    assertEq(structureState(ctx), "empty", "old-generation parse result is dropped");
    assertEq(field(ctx, "structure-error").hidden, true, "no error is fabricated either");
    assertEq(attr(ctx, "data-acp-step"), "workflow");
    ctx.instance.dispose();
  });

  await test("suspend/resume freezes rendering; updateContext is accepted without a request", async () => {
    const gate = deferred();
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/structures/parse") return gate.promise;
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToInput(ctx);
    await loadPaste(ctx, WATER);
    assertEq(structureState(ctx), "loading");

    ctx.instance.updateContext({ projectId: "proj-b", revision: 2 });
    ctx.instance.suspend();
    gate.resolve({ structures: [XYZ_ASSET], errors: [], warnings: [], ok: true, detected_format: "xyz" });
    await flush();
    assertEq(structureState(ctx), "loading", "suspended panels do not repaint");

    ctx.instance.resume();
    await flush();
    assertEq(structureState(ctx), "ready", "resume repaints the applied state");
    assertEq(attr(ctx, "data-acp-can-next"), "true");
    ctx.instance.dispose();
  });

  await test("i18n: static chrome carries keys, the zh fallback applies and a language change repaints", async () => {
    const ctx = setup();
    await mount(ctx);
    const keys = collectAttr(root(ctx), "data-i18n");
    [
      "next.wizard.title",
      "next.wizard.step_workflow",
      "next.wizard.step_input",
      "next.wizard.step_review",
      "next.wizard.back",
      "next.wizard.next",
      "next.wizard.mode_smiles",
      "next.wizard.mode_paste",
      "next.wizard.mode_file",
      "next.wizard.smiles_action",
      "next.wizard.paste_action",
      "next.wizard.review_title",
      "next.wizard.method_title",
      "next.wizard.file_hint"
    ].forEach((key) => {
      assert(keys.indexOf(key) !== -1, "missing data-i18n key: " + key);
    });
    assertEq(subtreeText(find(ctx.host, "data-i18n", "next.wizard.title")), "新建计算向导");
    assertEq(field(ctx, "smiles").getAttribute("data-i18n-ph"), "next.wizard.smiles_placeholder");
    ctx.dom.document.dispatchEvent({ type: "acp:langchange", detail: { lang: "en-US" } });
    await flush();
    assertEq(stateOf(ctx), "ready", "a language change repaints without throwing");
    assertEq(subtreeText(find(ctx.host, "data-i18n", "next.wizard.title")), "新建计算向导",
      "missing shell keys keep the zh fallback instead of a bare key");
  });

  await test("internals: catalog filter, projection and structure mapping are pure and total", async () => {
    const { sandbox } = setup();
    const internals = sandbox.ACPNextModules["task-wizard"]._internals;

    const entries = internals.filterWorkflows({ workflows: WORKFLOWS });
    assertEq(entries.map((entry) => entry.id), ["optimize", "singlepoint", "xtb_optimize"]);
    assertEq(internals.filterWorkflows(null), []);
    assertEq(internals.filterWorkflows({ workflows: [] }), []);
    assertEq(internals.pickDefaultWorkflow(entries).id, "optimize");
    assertEq(internals.findWorkflow(entries, "missing"), null);

    const noSchema = internals.projectMethod({ id: "x", schemaId: "missing" }, METHOD_CATALOG);
    assertEq(noSchema.error, "schema_missing");
    assertEq(noSchema.levels, []);
    const method = internals.projectMethod(entries[0], METHOD_CATALOG);
    assertEq(method.schemaId, "dft_optimize");
    assertEq(method.profileId, "default");
    assertEq(method.levels[0].levelId, "optimize");
    assertEq(method.levels[0].rows.map((row) => row.field), ["functional", "basis"]);
    assertEq(method.levels[0].rows[0].value, "wB97M-V");
    assertEq(internals.projectMethod(entries[2], METHOD_CATALOG).levels[0].rows[0].field, "gfn");
    assertEq(internals.fieldOptions(METHOD_CATALOG, "basis", "orca"), METHOD_CATALOG.basis_catalog);
    assertEq(internals.fieldOptions(METHOD_CATALOG, "functional", "xtb"), ["GFN2-xTB"]);
    assertEq(internals.fieldOptions(METHOD_CATALOG, "unknown", "orca"), []);

    const unknownStructure = internals.structureFromAsset({ xyz: WATER }, "paste", "x.xyz");
    assertEq(unknownStructure.charge, null, "missing charge stays unknown, never zero-filled");
    assertEq(unknownStructure.multiplicity, null);
    assertEq(unknownStructure.atomCount, null);
    const text = internals.structureSummaryText(unknownStructure);
    assert(text.indexOf("—") !== -1, "unknown fields render as an explicit dash: " + text);
    assert(text.indexOf("电荷 0") === -1, "no fabricated zero charge");
    const resolved = internals.structureFromResolve(
      { smiles: "CCO", formula: "C2H6O", charge: -1, multiplicity: 2 },
      { xyz: ETHANOL_XYZ, num_atoms: 9, formula: "C2H6O" }
    );
    assertEq(resolved.charge, -1);
    assertEq(resolved.multiplicity, 2);
    assertEq(resolved.atomCount, 9);
    assertEq(internals.describeError({ body: { detail: { code: "E_PARSE", message: "bad input" } } }),
      "E_PARSE: bad input");
  });

  // ---------------------------------------------------- A-03 electron parity --

  await test("A-03: charge/multiplicity seed from the structure and parity passes", async () => {
    const ctx = setup();
    await mount(ctx);
    await advanceToInput(ctx);
    await loadSmiles(ctx, "CCO");

    assertEq(roleValue(ctx, "charge"), "0", "charge seeded from the server structure fact");
    assertEq(roleValue(ctx, "multiplicity"), "1", "multiplicity seeded from the server fact");
    assertEq(attr(ctx, "data-acp-electronic-state"), "ready");
    const hint = subtreeText(field(ctx, "parity-hint"));
    assert(hint.indexOf("奇偶检查通过") !== -1, hint);
    assert(hint.indexOf("26") !== -1, "ethanol has 26 electrons: " + hint);
    assertEq(fieldError(ctx, "charge").hidden, true);
    assertEq(fieldError(ctx, "multiplicity").hidden, true);
    assertEq(attr(ctx, "data-acp-step-valid"), "true");
    assertEq(action(ctx, "next").disabled, false);

    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "review");
    assert(subtreeText(field(ctx, "review-state")).indexOf("26") !== -1,
      "the review names the electron count");
    assert(subtreeText(field(ctx, "review-resources")).indexOf("核数 6") !== -1,
      "the review carries the server resource default");
    ctx.instance.dispose();
  });

  await test("A-03: odd/even mismatch is explicit, never normalized, and blocks next", async () => {
    const ctx = setup();
    await mount(ctx);
    await advanceToInput(ctx);
    await loadSmiles(ctx, "CCO");

    setRoleValue(ctx, "multiplicity", "2");
    await flush();
    assertEq(roleValue(ctx, "multiplicity"), "2", "the typed value is never rewritten");
    assertEq(roleValue(ctx, "charge"), "0");
    const message = subtreeText(fieldError(ctx, "multiplicity"));
    assert(message.indexOf("奇偶不匹配") !== -1, message);
    assert(message.indexOf("26") !== -1, message);
    assertEq(attr(ctx, "data-acp-electronic-state"), "error");
    assertEq(attr(ctx, "data-acp-step-valid"), "false");
    assertEq(action(ctx, "next").disabled, true);
    assert(subtreeText(field(ctx, "nav-hint")).indexOf("奇偶") !== -1,
      "the next-blocking reason is the parity error");
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "input", "an invalid electronic state must not advance");

    setRoleValue(ctx, "multiplicity", "3");
    await flush();
    assertEq(fieldError(ctx, "multiplicity").hidden, true);
    assertEq(attr(ctx, "data-acp-step-valid"), "true");
    assertEq(action(ctx, "next").disabled, false);
    ctx.instance.dispose();
  });

  await test("A-03: over-high charge and impossible multiplicity are explicit errors", async () => {
    const ctx = setup();
    await mount(ctx);
    await advanceToInput(ctx);
    await loadSmiles(ctx, "CCO");

    setRoleValue(ctx, "charge", "30");
    await flush();
    const chargeMessage = subtreeText(fieldError(ctx, "charge"));
    assert(chargeMessage.indexOf("电荷过高") !== -1, chargeMessage);
    assertEq(attr(ctx, "data-acp-step-valid"), "false");

    setRoleValue(ctx, "charge", "0");
    setRoleValue(ctx, "multiplicity", "28");
    await flush();
    const multMessage = subtreeText(fieldError(ctx, "multiplicity"));
    assert(multMessage.indexOf("上限") !== -1, multMessage);
    assert(multMessage.indexOf("27") !== -1, "electron count + 1 is the ceiling: " + multMessage);
    assertEq(attr(ctx, "data-acp-step-valid"), "false");

    setRoleValue(ctx, "multiplicity", "1");
    await flush();
    assertEq(attr(ctx, "data-acp-step-valid"), "true");
    ctx.instance.dispose();
  });

  // ---------------------------------------------------------- A-04 resources --

  await test("A-04: cores/memory defaults and queue names come from the server", async () => {
    const ctx = setup();
    await mount(ctx);
    assertEq(callsToPath(ctx, CAPABILITIES_PATH).length, 0, "no resource read before the input step");
    assertEq(callsTo(ctx, "/nodes").length, 0, "no node catalog read before the input step");

    await advanceToInput(ctx);
    assertEq(callsToPath(ctx, CAPABILITIES_PATH).length, 1, "one admission-view read");
    assertEq(callsTo(ctx, "/nodes").length, 1, "one node catalog read");
    assertEq(attr(ctx, "data-acp-resource-state"), "ready");
    assertEq(roleValue(ctx, "cores"), "6", "available.cores is the cores default");
    assertEq(roleValue(ctx, "memory"), "24576MB", "available.memory_mb is the memory default");
    assertEq(attr(ctx, "data-acp-queue-state"), "ready");
    const chips = walk(ctx.host).filter((node) => node.getAttribute &&
      node.getAttribute("data-queue-name") != null);
    assertEq(chips.map((chip) => chip.getAttribute("data-queue-name")), ["normal", "long"],
      "queue names are the node catalog's, in server order");
    assert(subtreeText(find(ctx.host, "data-role", "resource-queue")).indexOf("node-a") !== -1);

    setRoleValue(ctx, "cores", "33");
    await flush();
    assert(subtreeText(fieldError(ctx, "cores")).indexOf("超过服务端上限") !== -1,
      "the published max_cores ceiling is enforced");
    setRoleValue(ctx, "cores", "6");
    setRoleValue(ctx, "memory", "999GB");
    await flush();
    assert(subtreeText(fieldError(ctx, "memory")).indexOf("超过服务端上限") !== -1,
      "the published max_memory_mb ceiling is enforced");
    setRoleValue(ctx, "memory", "24576MB");
    await flush();
    assertEq(fieldError(ctx, "memory").hidden, true);
    assertEq(fieldError(ctx, "cores").hidden, true);
    ctx.instance.dispose();
  });

  await test("A-04: memory_input needs a unit, invalid values block next", async () => {
    const ctx = setup();
    await mount(ctx);
    await advanceToInput(ctx);
    await loadSmiles(ctx, "CCO");

    setRoleValue(ctx, "memory", "16");
    await flush();
    assertEq(attr(ctx, "data-acp-step-valid"), "false");
    assert(subtreeText(fieldError(ctx, "memory")).indexOf("格式不符合要求") !== -1,
      "a unitless memory value is refused instead of silently defaulted to GB");
    assertEq(action(ctx, "next").disabled, true);
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "input");

    setRoleValue(ctx, "memory", "16gb");
    await flush();
    assertEq(fieldError(ctx, "memory").hidden, true);
    assertEq(attr(ctx, "data-acp-step-valid"), "true");
    assertEq(action(ctx, "next").disabled, false);

    ["0GB", "abc", "-1GB", "16XB"].forEach((value) => {
      setRoleValue(ctx, "memory", value);
    });
    await flush();
    assert(subtreeText(fieldError(ctx, "memory")).indexOf("格式不符合要求") !== -1,
      "the last invalid value keeps the field error");

    setRoleValue(ctx, "memory", "32gb");
    await flush();
    assertEq(attr(ctx, "data-acp-step-valid"), "true", "case-insensitive unit is accepted");
    ctx.instance.dispose();
  });

  await test("A-04: no server resource authority means no invented default and no block", async () => {
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === CAPABILITIES_PATH) return {};
        if (pathname === API + "/nodes") return { nodes: [], auto_select: true };
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToInput(ctx);
    assertEq(attr(ctx, "data-acp-resource-state"), "empty");
    assertEq(attr(ctx, "data-acp-queue-state"), "empty");
    assertEq(roleValue(ctx, "cores"), "");
    assertEq(roleValue(ctx, "memory"), "");
    assert(subtreeText(field(ctx, "resource-status")).indexOf("服务端没有发布资源默认值") !== -1,
      "the empty state is explained, not hidden");
    assert(subtreeText(find(ctx.host, "data-role", "resource-queue")).indexOf("未配置远程节点") !== -1);

    await loadSmiles(ctx, "CCO");
    assertEq(attr(ctx, "data-acp-step-valid"), "true", "optional resources do not block");
    action(ctx, "next").fire("click");
    await flush();
    await runPreflight(ctx);
    const body = bodyOf(callsTo(ctx, "/preflight")[0]);
    assert(!("resources" in body), "no client-invented resource numbers are sent");
    ctx.instance.dispose();
  });

  // ---------------------------------------------------------- A-05 preflight --

  await test("A-05: preflight sends the frozen plan body and shows the digest", async () => {
    const ctx = setup();
    await mount(ctx);
    await advanceToReview(ctx);

    await runPreflight(ctx);
    const calls = callsTo(ctx, "/preflight");
    assertEq(calls.length, 1, "one preflight request");
    assertEq(calls[0].opts.method, "POST");
    assertEq(calls[0].opts.headers["Content-Type"], "application/json");
    assertEq(Object.keys(calls[0].opts.headers), ["Content-Type"], "no idempotency header");
    assertEq(bodyOf(calls[0]), {
      schema_version: 1,
      workflow: "optimize",
      input: {
        source_type: "xyz_text",
        source: ETHANOL_XYZ,
        charge: 0,
        multiplicity: 1
      },
      method: {
        schema_id: "dft_optimize",
        levels: {
          optimize: { engine: "orca", functional: "wB97M-V", basis: "def2-TZVPP" }
        }
      },
      resources: { nproc: 6, mem: "24576MB" }
    }, "the preflight body is the same frozen plan a submit would carry");

    assertEq(preflightAttr(ctx, "data-acp-preflight-state"), "ready");
    assertEq(preflightAttr(ctx, "data-acp-preflight-valid"), "true");
    assertEq(preflightAttr(ctx, "data-acp-preflight-stale"), "false");
    assertEq(preflightAttr(ctx, "data-acp-plan-digest"), PREFLIGHT_OK.plan_digest);
    assertEq(preflightAttr(ctx, "data-acp-preflight-fingerprint"), PREFLIGHT_OK.plan_digest);
    assert(subtreeText(field(ctx, "preflight-fingerprint")).indexOf(PREFLIGHT_OK.plan_digest) !== -1);
    assert(subtreeText(field(ctx, "preflight-status")).indexOf("预检通过") !== -1);
    assertEq(callsToPath(ctx, API + "/preflight").length, 1);
    assertEq(ctx.client.calls.filter((call) => call.path === API + "/jobs").length, 0,
      "preflight never creates a task");
    assert(!!action(ctx, "submit"), "the A-06 submit action exists but preflight did not use it");
    ctx.instance.dispose();
  });

  await test("A-05: 422/400 rejections are mapped to their field and block the step", async () => {
    let attempts = 0;
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/preflight") {
          attempts += 1;
          if (attempts === 1) {
            const error = new Error("resources.mem must be a memory value");
            error.status = 422;
            error.body = {
              detail: {
                code: "input_invalid",
                message: "resources.mem must be a memory value",
                params: { field: "resources.mem", rule: "invalid_resource" }
              }
            };
            throw error;
          }
          if (attempts === 2) {
            const error = new Error("Unsupported workflow 'optimize'");
            error.status = 400;
            error.body = {
              detail: {
                code: "input_invalid",
                message: "Unsupported workflow 'optimize'",
                params: { field: "workflow", rule: "unknown_workflow" }
              }
            };
            throw error;
          }
        }
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToReview(ctx);

    await runPreflight(ctx);
    assertEq(preflightAttr(ctx, "data-acp-preflight-state"), "error");
    assertEq(preflightAttr(ctx, "data-acp-preflight-status-code"), "422");
    assertEq(preflightAttr(ctx, "data-acp-preflight-error-code"), "input_invalid");
    assertEq(preflightAttr(ctx, "data-acp-preflight-valid"), "false");
    const memoryError = subtreeText(fieldError(ctx, "memory"));
    assert(memoryError.indexOf("resources.mem") !== -1, memoryError);
    assert(memoryError.indexOf("memory value") !== -1, memoryError);
    assertEq(fieldError(ctx, "memory").hidden, false);
    assert(reasonTexts(ctx).join(" ").indexOf("resources.mem") !== -1);
    assertEq(attr(ctx, "data-acp-step-valid"), "false", "a field rejection blocks the step");

    await runPreflight(ctx);
    assertEq(preflightAttr(ctx, "data-acp-preflight-status-code"), "400");
    const workflowError = subtreeText(field(ctx, "workflow-error"));
    assert(workflowError.indexOf("Unsupported workflow") !== -1, workflowError);
    assertEq(attr(ctx, "data-acp-step-valid"), "false");

    await runPreflight(ctx);
    assertEq(preflightAttr(ctx, "data-acp-preflight-state"), "ready");
    assertEq(preflightAttr(ctx, "data-acp-preflight-valid"), "true");
    assertEq(attempts, 3);
    ctx.instance.dispose();
  });

  await test("A-05: an infeasible preflight shows its reasons and blocks", async () => {
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/preflight") {
          return {
            schema_version: 1,
            feasible: false,
            reasons: [{
              code: "no_capable_node",
              message: "no remote node advertises orca",
              field: "target_node",
              rule: "no_capable_node"
            }],
            advisories: [{ code: "source_cold", message: "remote source is a prepare decision" }],
            plan_digest: null,
            plan_summary: { workflow: "optimize", steps: 1 },
            would_create_task: false,
            would_reserve: false
          };
        }
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToReview(ctx);
    await runPreflight(ctx);

    assertEq(preflightAttr(ctx, "data-acp-preflight-state"), "error");
    assertEq(preflightAttr(ctx, "data-acp-preflight-status"), "infeasible");
    assert(subtreeText(field(ctx, "preflight-status")).indexOf("预检未通过") !== -1);
    assertEq(preflightAttr(ctx, "data-acp-plan-digest"), "", "no digest for an infeasible plan");
    assertEq(preflightAttr(ctx, "data-acp-preflight-valid"), "false");
    assert(reasonTexts(ctx).join(" ").indexOf("no remote node advertises orca") !== -1);
    assert(subtreeText(field(ctx, "preflight-advisories")).indexOf("prepare decision") !== -1);
    assertEq(attr(ctx, "data-acp-step-valid"), "false");
    ctx.instance.dispose();
  });

  await test("A-05: an edit after a successful preflight makes it stale and blocks", async () => {
    const ctx = setup();
    await mount(ctx);
    await advanceToReview(ctx);
    await runPreflight(ctx);
    assertEq(preflightAttr(ctx, "data-acp-preflight-valid"), "true");

    const functional = find(ctx.host, "data-field", "functional");
    functional.value = "PBE0";
    find(ctx.host, "data-role", "method-levels").fire("change", { target: functional });
    await flush();
    assertEq(preflightAttr(ctx, "data-acp-preflight-stale"), "true");
    assertEq(preflightAttr(ctx, "data-acp-preflight-valid"), "false");
    assertEq(preflightAttr(ctx, "data-acp-preflight-state"), "error");
    assertEq(attr(ctx, "data-acp-step-valid"), "false", "a stale preflight blocks");
    assert(subtreeText(field(ctx, "nav-hint")).indexOf("重新预检") !== -1);
    assertEq(preflightAttr(ctx, "data-acp-plan-digest"), PREFLIGHT_OK.plan_digest,
      "the stale digest stays visible so the user can see what expired");
    assert(subtreeText(field(ctx, "preflight-status")).indexOf("重新预检") !== -1);

    await runPreflight(ctx);
    const second = callsTo(ctx, "/preflight")[1];
    assertEq(bodyOf(second).method.levels.optimize.functional, "PBE0",
      "the re-run carries the edited method");
    assertEq(preflightAttr(ctx, "data-acp-preflight-valid"), "true");

    setRoleValue(ctx, "charge", "-1");
    await flush();
    assertEq(preflightAttr(ctx, "data-acp-preflight-stale"), "true",
      "an electronic-state edit also stales the digest");
    ctx.instance.dispose();
  });

  await test("A-05: late resource/preflight results never paint after dispose", async () => {
    const capabilityGate = deferred();
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === CAPABILITIES_PATH) return capabilityGate.promise;
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToInput(ctx);
    assertEq(attr(ctx, "data-acp-resource-state"), "loading");
    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0);
    capabilityGate.resolve(CAPABILITIES);
    await flush();
    assertEq(ctx.host.children.length, 0, "a late resource response cannot rebuild the DOM");

    await mount(ctx);
    await advanceToInput(ctx);
    assertEq(attr(ctx, "data-acp-resource-state"), "ready", "remount reloads the resources");
    assertEq(roleValue(ctx, "cores"), "6");
    ctx.instance.dispose();

    const preflightGate = deferred();
    const second = setup({
      responder: (pathname) => {
        if (pathname === API + "/preflight") return preflightGate.promise;
        return defaultResponse(pathname);
      }
    });
    await mount(second);
    await advanceToReview(second);
    action(second, "preflight").fire("click");
    await flush();
    assertEq(preflightAttr(second, "data-acp-preflight-state"), "loading");
    second.instance.dispose();
    preflightGate.resolve(PREFLIGHT_OK);
    await flush();
    assertEq(second.host.children.length, 0, "a late preflight response cannot rebuild the DOM");
    await mount(second);
    assertEq(preflightAttr(second, "data-acp-preflight-state"), "idle");
    assertEq(preflightAttr(second, "data-acp-plan-digest"), "", "remount starts with no digest");
    second.instance.dispose();
  });

  await test("A-03/A-04/A-05 chrome reuses only shared i18n keys present in both languages", async () => {
    const ctx = setup();
    await mount(ctx);
    await advanceToReview(ctx);
    const keys = collectAttr(root(ctx), "data-i18n");
    const dictSandbox = makeSandbox({});
    loadScript("js/shell/i18n.js", dictSandbox);
    const zh = dictSandbox.ACPShell.i18n.DICT["zh-CN"];
    const en = dictSandbox.ACPShell.i18n.DICT["en-US"];
    [
      "next.approval.plan.resources",
      "next.config.charge",
      "next.config.multiplicity",
      "next.config.nproc",
      "next.config.mem",
      "next.config.validate",
      "next.capabilityForm.preflight_fingerprint",
      "next.nodes.meta_queue"
    ].forEach((key) => {
      assert(keys.indexOf(key) !== -1, "missing static key: " + key);
      assert(key in zh, "shared key missing from zh-CN: " + key);
      assert(key in en, "shared key missing from en-US: " + key);
    });
    assertEq(find(ctx.host, "data-i18n", "next.config.charge").textContent, "电荷");
    ctx.dom.document.dispatchEvent({ type: "acp:langchange", detail: { lang: "en-US" } });
    await flush();
    assertEq(preflightAttr(ctx, "data-acp-preflight-state"), "idle",
      "a language change repaints without losing the new surfaces");
    ctx.instance.dispose();
  });

  await test("internals: electron/parity, memory grammar, resource projection and error mapping", async () => {
    const { sandbox } = setup();
    const internals = sandbox.ACPNextModules["task-wizard"]._internals;

    // ΣZ − charge electron count (never a guessed number).
    assertEq(internals.electronCount("C2H6O", 0), 26);
    assertEq(internals.electronCount("H2O", 0), 10);
    assertEq(internals.electronCount("H2O", 1), 9);
    assertEq(internals.electronCount("H2O", -1), 11);
    assertEq(internals.electronCount("Xx2", 0), null, "unknown element is unverifiable");
    assertEq(internals.electronCount("", 0), null);
    assertEq(internals.electronCount(null, 0), null);
    assertEq(internals.parseFormula("C2H6O"), { C: 2, H: 6, O: 1 });
    assertEq(internals.parseFormula("bad!formula"), null);
    assertEq(internals.parseFormula("H2O+"), null, "a charge suffix makes it unparseable");

    // Charge/multiplicity matrix (odd/even, over-high charge, impossible mult).
    const empty = internals.validateElectronic("", "", null);
    assertEq(empty.ok, false);
    assertEq(empty.errors.map((err) => err.field), ["charge", "multiplicity"]);
    const parity = internals.validateElectronic("0", "2", { formula: "H2O" });
    assertEq(parity.ok, false);
    assertEq(parity.parityChecked, true);
    assertEq(parity.electrons, 10);
    assertEq(parity.errors[0].key, "next.config.error.multiplicity_parity");
    assertEq(internals.validateElectronic("0", "3", { formula: "H2O" }).ok, true,
      "10 electrons admit an odd multiplicity");
    assertEq(internals.validateElectronic("1", "2", { formula: "H2O" }).ok, true,
      "9 electrons admit an even multiplicity");
    assertEq(internals.validateElectronic("0", "1", { formula: "H2O" }).ok, true);
    const impossible = internals.validateElectronic("0", "12", { formula: "H2O" });
    assertEq(impossible.ok, false, "multiplicity 12 exceeds 10 electrons + 1");
    const overCharged = internals.validateElectronic("11", "2", { formula: "H2O" });
    assertEq(overCharged.ok, false);
    assertEq(overCharged.errors[0].field, "charge");
    const unverifiable = internals.validateElectronic("0", "2", { formula: "Qq2" });
    assertEq(unverifiable.ok, true, "an unknown element must not fake a parity error");
    assertEq(unverifiable.parityChecked, false);
    assertEq(internals.validateElectronic("1.5", "1", { formula: "H2O" }).ok, false,
      "non-integer charges are refused");

    // memory_input grammar (unit required, server units only, positive).
    assertEq(internals.normalizeMemory("16gb"), "16GB");
    assertEq(internals.normalizeMemory("1.5 GB"), "1.5GB");
    assertEq(internals.normalizeMemory("2T"), "2TB");
    assertEq(internals.normalizeMemory("512MB"), "512MB");
    assertEq(internals.normalizeMemory("16"), null, "unitless is refused");
    assertEq(internals.normalizeMemory("0GB"), null);
    assertEq(internals.normalizeMemory("-1GB"), null);
    assertEq(internals.normalizeMemory("abc"), null);
    assertEq(internals.normalizeMemory("16XB"), null);

    // Optional resource validation against the published ceilings.
    assertEq(internals.validateResources("", "", {}),
      { ok: true, errors: [], cores: null, mem: null }, "unset means server default");
    assertEq(internals.validateResources("6", "24576MB", { coresMax: 32, memoryMaxMb: 65536 }),
      { ok: true, errors: [], cores: 6, mem: "24576MB" });
    assertEq(internals.validateResources("0", "", {}).ok, false);
    assertEq(internals.validateResources("2.5", "", {}).ok, false);
    assertEq(internals.validateResources("33", "", { coresMax: 32 }).errors[0].field, "cores");
    assertEq(internals.validateResources("", "64GB", { memoryMaxMb: 32768 }).errors[0].field, "memory");

    // Defaults come only from the published capacity block.
    assertEq(internals.projectResourceDefaults(null),
      { cores: "", coresMax: null, memoryMb: "", memoryMaxMb: null, unknown: [], published: false });
    assertEq(internals.projectResourceDefaults(CAPABILITIES), {
      cores: "6",
      coresMax: 32,
      memoryMb: "24576MB",
      memoryMaxMb: 65536,
      unknown: [],
      published: true
    });
    const limitsOnly = internals.projectResourceDefaults({
      capacity: { limits: { max_cores: 8, max_memory_mb: 16384 }, unknown: ["pool"] }
    });
    assertEq(limitsOnly.cores, "8", "the hard ceiling is the last server fallback");
    assertEq(limitsOnly.coresMax, 8);
    assertEq(limitsOnly.unknown, ["pool"]);

    // Queue projection is the node catalog's, deduped by node order.
    assertEq(internals.projectQueues(NODES), [
      { name: "node-a", status: "online", queue: "normal" },
      { name: "node-b", status: "degraded", queue: "long" }
    ]);
    assertEq(internals.projectQueues({}), []);

    // Frozen preflight request + plan identity.
    const request = internals.buildPreflightRequest({
      entry: { id: "optimize" },
      method: {
        schemaId: "dft_optimize",
        levels: [{ levelId: "optimize", engine: "orca", rows: [{ field: "functional", value: "wB97M-V" }, { field: "basis", value: "" }] }]
      },
      structure: { xyz: WATER },
      charge: "0",
      multiplicity: "1"
    });
    assertEq(request, {
      schema_version: 1,
      workflow: "optimize",
      input: { source_type: "xyz_text", source: WATER, charge: 0, multiplicity: 1 },
      method: { schema_id: "dft_optimize", levels: { optimize: { engine: "orca", functional: "wB97M-V" } } }
    }, "no empty row, no resources block, no project");
    assertEq(internals.buildPreflightRequest({ entry: { id: "x" }, structure: null }), null);
    assertEq(internals.buildPreflightRequest({
      entry: { id: "x" }, structure: { xyz: WATER }, charge: "", multiplicity: "1",
      method: { schemaId: "s", levels: [] }
    }), null, "an incomplete electronic state cannot build a body");
    const keyA = internals.planIdentity({ workflow: "w", input: { b: 1, a: 2 }, method: {}, resources: {} });
    const keyB = internals.planIdentity({ resources: {}, method: {}, input: { a: 2, b: 1 }, workflow: "w" });
    assertEq(keyA, keyB, "plan identity is key-order independent");
    assert(keyA !== internals.planIdentity({ workflow: "w", input: { a: 2, b: 1 }, method: {}, resources: { nproc: 6 } }));

    // 400/422 envelopes map onto the declared field.
    const envelope = internals.parsePreflightError({
      status: 422,
      message: "resources.mem must be a memory value",
      body: { detail: { code: "input_invalid", message: "resources.mem must be a memory value", params: { field: "resources.mem", rule: "invalid_resource" } } }
    });
    assertEq(envelope.status, 422);
    assertEq(envelope.code, "input_invalid");
    assertEq(envelope.field, "resources.mem");
    assertEq(envelope.rule, "invalid_resource");
    assertEq(envelope.fieldErrors["resources.mem"], "resources.mem must be a memory value");
    const validationList = internals.parsePreflightError({
      status: 422,
      message: "HTTP 422",
      body: { detail: [{ loc: ["body", "resources", "nproc"], msg: "must be >= 1", type: "greater_than_equal" }] }
    });
    assertEq(validationList.fieldErrors["resources.nproc"], "must be >= 1",
      "a FastAPI validation list maps loc[1:] to the field");
    const stringDetail = internals.parsePreflightError({ status: 400, message: "HTTP 400", body: { detail: "bad body" } });
    assertEq(stringDetail.reasons[0].message, "bad body");
    assertEq(stringDetail.fieldErrors[""], "bad body");
    const noBody = internals.parsePreflightError(new Error("network down"));
    assert(noBody.reasons[0].message.indexOf("network down") !== -1);
  });

  // ------------------------------------------------------- A-06 / A-08 submit --

  await test("A-08: submit check lists blockers/warnings; a blocked confirm sends no request", async () => {
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/preflight") {
          return Object.assign({}, PREFLIGHT_OK, {
            advisories: [{ code: "source_cold", message: "remote source is a prepare decision" }]
          });
        }
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToReview(ctx);

    // Before preflight the only genuine blocker is the missing preflight.
    await openCheck(ctx);
    assertEq(dialogOf(ctx).hidden, false, "the check dialog is open");
    assertEq(dialogOf(ctx).getAttribute("data-acp-dialog"), "open");
    assertEq(attr(ctx, "data-acp-submit-check"), "blocked");
    assertEq(attr(ctx, "data-acp-can-submit"), "false");
    assertEq(checkBlockers(ctx).map((row) => row.code), ["preflight_required"]);
    assertEq(checkWarnings(ctx), []);
    assertEq(action(ctx, "submit-check-confirm").disabled, true);
    await confirmCheck(ctx);
    assertEq(callsTo(ctx, "/jobs").length, 0, "a blocked confirm never sends a request");
    assertEq(attr(ctx, "data-acp-submit-state"), "idle");
    assertEq(ctx.commands.opened.length, 0);

    // A passing preflight with an advisory: the warning is shown, not blocking.
    await runPreflight(ctx);
    await openCheck(ctx);
    assertEq(attr(ctx, "data-acp-submit-check"), "ok");
    assertEq(attr(ctx, "data-acp-can-submit"), "true");
    assertEq(checkBlockers(ctx), []);
    assertEq(checkWarnings(ctx), ["remote source is a prepare decision"]);
    assertEq(action(ctx, "submit-check-confirm").disabled, false);
    assertEq(attr(ctx, "data-acp-submit-state"), "idle", "an open check dialog sends nothing by itself");

    // A local electronic error plus the now-stale preflight are both genuine blockers.
    setRoleValue(ctx, "multiplicity", "2");
    await flush();
    await openCheck(ctx);
    const blockers = checkBlockers(ctx);
    assert(blockers.some((row) => row.code === "multiplicity_invalid"), "the parity error is listed");
    assert(blockers.some((row) => row.code === "preflight_stale"), "the stale preflight is listed");
    assertEq(find(ctx.host, "data-role", "nav-hint").hidden, false);
    await confirmCheck(ctx);
    assertEq(callsTo(ctx, "/jobs").length, 0, "an invalid local state can never submit");
    ctx.instance.dispose();
  });

  await test("A-06: confirm posts the frozen plan + fingerprint, then shows the receipt and openTask", async () => {
    const ctx = setup({
      responder: (pathname) => (pathname === API + "/jobs" ? JOB_CREATED : defaultResponse(pathname))
    });
    await reachedCheckedPlan(ctx);
    const preflightBody = bodyOf(callsTo(ctx, "/preflight")[0]);
    assertEq(callsTo(ctx, "/jobs").length, 0, "preflight alone creates no task");

    await openCheck(ctx);
    const dialogText = subtreeText(dialogOf(ctx));
    assert(dialogText.indexOf("wB97M-V") !== -1, "the check summarises the frozen method: " + dialogText);
    assert(dialogText.indexOf(PREFLIGHT_OK.plan_digest) !== -1, "the check shows the fingerprint");
    await confirmCheck(ctx);

    const calls = callsTo(ctx, "/jobs");
    assertEq(calls.length, 1, "exactly one submit request");
    assertEq(calls[0].opts.method, "POST");
    assertEq(calls[0].opts.headers["Content-Type"], "application/json");
    const key = calls[0].opts.headers["Idempotency-Key"];
    assert(typeof key === "string" && /^next-wizard-/.test(key), "idempotency key header: " + key);
    const body = bodyOf(calls[0]);
    assertEq(body, Object.assign({}, preflightBody, { preflight_fingerprint: PREFLIGHT_OK.plan_digest }),
      "the body is the frozen preflight plan + its fingerprint");
    assert(!("plan_digest" in body), "no research plan_digest is smuggled into a legacy submit");
    assert(!("idempotency_key" in body), "the key travels in the header, not the body");

    assertEq(attr(ctx, "data-acp-submit-state"), "submitted");
    assertEq(attr(ctx, "data-acp-can-submit"), "false", "a receipted plan is not re-submittable");
    assertEq(attr(ctx, "data-acp-receipt-job"), JOB_CREATED.job_id);
    assertEq(field(ctx, "receipt").hidden, false);
    assertEq(field(ctx, "receipt").getAttribute("data-acp-receipt-state"), "submitted");
    const receiptText = subtreeText(field(ctx, "receipt-body"));
    assert(receiptText.indexOf(JOB_CREATED.job_id) !== -1, receiptText);
    assert(receiptText.indexOf(JOB_CREATED.status) !== -1, receiptText);
    assert(receiptText.indexOf("1") !== -1, receiptText);
    assert(subtreeText(field(ctx, "receipt-digest")).indexOf(PREFLIGHT_OK.plan_digest) !== -1,
      "the response plan_digest is consumed");
    assertEq(field(ctx, "receipt-replay").hidden, true);

    assertEq(ctx.commands.opened.length, 1, "success navigates exactly once");
    assertEq(ctx.commands.opened[0], { jobId: JOB_CREATED.job_id, attempt: 1, source: "task-wizard" });
    assertEq(action(ctx, "open-task").hidden, false);
    action(ctx, "open-task").fire("click");
    await flush();
    assertEq(ctx.commands.opened.length, 2, "the receipt button uses the same openTask command");
    ctx.instance.dispose();
  });

  await test("A-06: retries reuse the plan idempotency key; a receipted plan is never re-sent", async () => {
    let failures = 1;
    let failNext = false;
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/jobs") {
          if (failures > 0) {
            failures -= 1;
            const error = new Error("HTTP 503");
            error.status = 503;
            error.body = { detail: "upstream unavailable" };
            throw error;
          }
          if (failNext) {
            failNext = false;
            const error = new Error("HTTP 500");
            error.status = 500;
            error.body = { detail: "server error" };
            throw error;
          }
          return JOB_CREATED;
        }
        return defaultResponse(pathname);
      }
    });
    await reachedCheckedPlan(ctx);
    await openCheck(ctx);
    await confirmCheck(ctx);

    let calls = callsTo(ctx, "/jobs");
    assertEq(calls.length, 1);
    const firstKey = calls[0].opts.headers["Idempotency-Key"];
    assertEq(attr(ctx, "data-acp-submit-state"), "error");
    assertEq(field(ctx, "receipt").getAttribute("data-acp-receipt-state"), "error");
    assert(subtreeText(field(ctx, "receipt-body")).indexOf("upstream unavailable") !== -1);
    assertEq(ctx.commands.opened.length, 0, "a failed submit never navigates");

    // Retry of the same frozen plan: same key and the same body.
    await openCheck(ctx);
    await confirmCheck(ctx);
    calls = callsTo(ctx, "/jobs");
    assertEq(calls.length, 2, "the retry is a second attempt, not a second task");
    assertEq(calls[1].opts.headers["Idempotency-Key"], firstKey, "the retry reuses the plan key");
    assertEq(bodyOf(calls[1]), bodyOf(calls[0]), "the retry sends the same frozen body");
    assertEq(attr(ctx, "data-acp-submit-state"), "submitted");
    assertEq(ctx.commands.opened.length, 1, "only the successful receipt navigates");

    // Clicking submit for the receipted plan must not duplicate the task.
    submitButton(ctx).fire("click");
    await flush();
    await confirmCheck(ctx);
    assertEq(callsTo(ctx, "/jobs").length, 2, "no duplicate request for a receipted plan");
    assertEq(ctx.commands.opened.length, 1, "no duplicate navigation");

    // A different plan gets a fresh key and carries the edited method.
    const functional = find(ctx.host, "data-field", "functional");
    functional.value = "PBE0";
    find(ctx.host, "data-role", "method-levels").fire("change", { target: functional });
    await flush();
    await runPreflight(ctx);
    await openCheck(ctx);
    await confirmCheck(ctx);
    calls = callsTo(ctx, "/jobs");
    assertEq(calls.length, 3);
    assert(calls[2].opts.headers["Idempotency-Key"] !== firstKey, "a different plan gets a fresh key");
    assertEq(bodyOf(calls[2]).method.levels.optimize.functional, "PBE0");

    // A failed submit for a NEW plan never keeps showing the old plan's receipt.
    failNext = true;
    const basis = find(ctx.host, "data-field", "basis");
    basis.value = "def2-SVP";
    find(ctx.host, "data-role", "method-levels").fire("change", { target: basis });
    await flush();
    await runPreflight(ctx);
    await openCheck(ctx);
    await confirmCheck(ctx);
    assertEq(callsTo(ctx, "/jobs").length, 4);
    assertEq(attr(ctx, "data-acp-receipt-job"), "", "the previous receipt is not shown for the new plan");
    assertEq(field(ctx, "receipt").getAttribute("data-acp-receipt-state"), "error");
    assertEq(ctx.commands.opened.length, 2, "the failed new plan does not navigate");
    ctx.instance.dispose();
  });

  await test("A-06: 400/409/422/5xx submit failures map to readable copy, never a receipt", async () => {
    const responses = [
      {
        status: 422,
        body: {
          detail: {
            code: "input_invalid", message: "resources.mem must be a memory value",
            params: { field: "resources.mem", rule: "invalid_resource" }
          }
        }
      },
      {
        status: 409,
        body: {
          detail: {
            code: "idempotency_conflict", message: "idempotency key conflict",
            params: { idempotency_key: "next-wizard-x" }
          }
        }
      },
      {
        status: 400,
        body: {
          detail: {
            code: "preflight_stale", message: "preflight fingerprint does not match the submitted plan",
            params: { field: "preflight_fingerprint", expected: "sha256:other" }
          }
        }
      },
      { status: 503, body: { detail: "upstream unavailable" } }
    ];
    let index = 0;
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/jobs") {
          const spec = responses[index];
          index += 1;
          const error = new Error("HTTP " + spec.status);
          error.status = spec.status;
          error.body = spec.body;
          throw error;
        }
        return defaultResponse(pathname);
      }
    });
    await reachedCheckedPlan(ctx);

    await openCheck(ctx);
    await confirmCheck(ctx);
    assertEq(attr(ctx, "data-acp-submit-error-status"), "422");
    assertEq(attr(ctx, "data-acp-submit-error-code"), "input_invalid");
    assertEq(attr(ctx, "data-acp-submit-error-field"), "resources.mem");
    assertEq(attr(ctx, "data-acp-submit-error-rule"), "invalid_resource");
    let text = subtreeText(field(ctx, "receipt-body"));
    assert(text.indexOf("提交内容未通过服务端校验") !== -1, text);
    assert(text.indexOf("resources.mem must be a memory value") !== -1, text);
    assertEq(attr(ctx, "data-acp-submit-state"), "error");
    assertEq(ctx.commands.opened.length, 0);

    await openCheck(ctx);
    await confirmCheck(ctx);
    assertEq(attr(ctx, "data-acp-submit-error-status"), "409");
    assertEq(attr(ctx, "data-acp-submit-error-code"), "idempotency_conflict");
    text = subtreeText(field(ctx, "receipt-body"));
    assert(text.indexOf("预检计划或审批已在服务端变更") !== -1, text);
    assert(text.indexOf("idempotency key conflict") !== -1, text);
    assertEq(field(ctx, "receipt").getAttribute("data-acp-receipt-state"), "error");

    await openCheck(ctx);
    await confirmCheck(ctx);
    assertEq(attr(ctx, "data-acp-submit-error-status"), "400");
    assertEq(attr(ctx, "data-acp-submit-error-code"), "preflight_stale");
    text = subtreeText(field(ctx, "receipt-body"));
    assert(text.indexOf("重新预检") !== -1, text);
    assertEq(attr(ctx, "data-acp-preflight-status"), "stale", "a stale fingerprint stales the preflight");
    assertEq(attr(ctx, "data-acp-can-submit"), "false");

    // A stale preflight blocks the next confirm without any request.
    await openCheck(ctx);
    await confirmCheck(ctx);
    assertEq(callsTo(ctx, "/jobs").length, 3, "a blocked confirm after the 400 sends nothing");

    // 5xx after a fresh preflight: readable and still no receipt/navigation.
    await runPreflight(ctx);
    await openCheck(ctx);
    await confirmCheck(ctx);
    assertEq(attr(ctx, "data-acp-submit-error-status"), "503");
    text = subtreeText(field(ctx, "receipt-body"));
    assert(text.indexOf("提交失败") !== -1, text);
    assert(text.indexOf("upstream unavailable") !== -1, text);
    assertEq(field(ctx, "receipt").getAttribute("data-acp-receipt-state"), "error");
    assertEq(attr(ctx, "data-acp-receipt-job"), "");
    assertEq(ctx.commands.opened.length, 0);
    ctx.instance.dispose();
  });

  await test("A-06: double-click sends one request; a late receipt after dispose never navigates", async () => {
    const gate = deferred();
    let jobCalls = 0;
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/jobs") {
          jobCalls += 1;
          return gate.promise;
        }
        return defaultResponse(pathname);
      }
    });
    await reachedCheckedPlan(ctx);
    await openCheck(ctx);
    const confirm = action(ctx, "submit-check-confirm");
    confirm.fire("click");
    confirm.fire("click");
    await flush();
    assertEq(jobCalls, 1, "a double click during the in-flight submit sends one request");
    assertEq(attr(ctx, "data-acp-busy"), "submit");
    assertEq(attr(ctx, "data-acp-submit-state"), "loading");
    assertEq(action(ctx, "submit-check-confirm").disabled, true);

    ctx.instance.dispose();
    gate.resolve(JOB_CREATED);
    await flush();
    assertEq(ctx.host.children.length, 0, "a late receipt cannot rebuild the DOM");
    assertEq(ctx.commands.opened.length, 0, "a late receipt never navigates");
    assertEq(jobCalls, 1);

    await mount(ctx);
    assertEq(attr(ctx, "data-acp-submit-state"), "idle");
    assertEq(attr(ctx, "data-acp-can-submit"), "false");
    assertEq(field(ctx, "receipt").hidden, true);
    ctx.instance.dispose();
  });

  await test("A-08: an infeasible preflight is a blocker; advisories stay warnings", async () => {
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/preflight") {
          return {
            schema_version: 1,
            feasible: false,
            reasons: [{
              code: "no_capable_node",
              message: "no remote node advertises orca",
              field: "target_node",
              rule: "no_capable_node"
            }],
            advisories: [{ code: "source_cold", message: "remote source is a prepare decision" }],
            plan_digest: null,
            would_create_task: false,
            would_reserve: false
          };
        }
        return defaultResponse(pathname);
      }
    });
    await mount(ctx);
    await advanceToReview(ctx);
    await runPreflight(ctx);
    await openCheck(ctx);

    const blockers = checkBlockers(ctx);
    assertEq(blockers.map((row) => row.field), ["target_node"]);
    assertEq(blockers.map((row) => row.code), ["preflight_infeasible"]);
    assert(blockers[0].text.indexOf("no remote node advertises orca") !== -1, blockers[0].text);
    assertEq(checkWarnings(ctx), ["remote source is a prepare decision"],
      "an advisory is a warning, never a blocker");
    assertEq(attr(ctx, "data-acp-submit-check"), "blocked");
    await confirmCheck(ctx);
    assertEq(callsTo(ctx, "/jobs").length, 0, "an infeasible plan never reaches POST /jobs");
    assertEq(ctx.commands.opened.length, 0);
    ctx.instance.dispose();
  });

  await test("internals: submit check, submit body, receipt and error mapping are total", async () => {
    const { sandbox } = setup();
    const internals = sandbox.ACPNextModules["task-wizard"]._internals;

    assertEq(internals.newIdempotencyKey().startsWith("next-wizard-"), true);
    assert(internals.newIdempotencyKey() !== internals.newIdempotencyKey(), "keys are unique");

    const empty = internals.submitCheck({});
    assertEq(empty.ok, false);
    assert(empty.blockers.some((row) => row.code === "workflow_required"));
    assert(empty.blockers.some((row) => row.code === "structure_required"));
    assert(empty.blockers.some((row) => row.code === "preflight_required"));

    const ready = {
      entry: { id: "optimize", schemaId: "dft_optimize" },
      workflowLabel: "几何优化",
      method: { schema: { method_levels: [] }, schemaId: "dft_optimize" },
      structureStatus: "ready",
      structure: { xyz: WATER, formula: "H2O" },
      charge: "0",
      multiplicity: "1",
      resourcesStatus: "empty",
      resourcesError: null,
      cores: "",
      memory: "",
      limits: {},
      preflight: {
        status: "ok",
        digest: "sha256:abc",
        valid: true,
        reasons: [],
        advisories: ["cold remote source"]
      }
    };
    const pass = internals.submitCheck(ready);
    assertEq(pass.ok, true, "a valid frozen plan has no blocker");
    assertEq(pass.blockers, []);
    assertEq(pass.warnings, [{ code: "advisory", message: "cold remote source" }],
      "advisories are warnings and never block");

    const unknownFormula = internals.submitCheck(Object.assign({}, ready, {
      structure: { xyz: WATER, formula: "Qq2" }
    }));
    assertEq(unknownFormula.ok, true, "an unverifiable parity must not fake a blocker");

    ["idle", "loading", "blocked", "stale", "error"].forEach((status) => {
      const blocked = internals.submitCheck(Object.assign({}, ready, {
        preflight: { status: status, digest: "", valid: false, reasons: [], advisories: [] }
      }));
      assertEq(blocked.ok, false, "preflight status " + status + " must block");
    });
    const infeasible = internals.submitCheck(Object.assign({}, ready, {
      preflight: {
        status: "infeasible", digest: "", valid: false,
        reasons: [{ code: "no_capable_node", message: "no node", field: "target_node", rule: "" }],
        advisories: []
      }
    }));
    assertEq(infeasible.blockers.map((row) => row.field), ["target_node"]);
    assertEq(internals.submitCheck(Object.assign({}, ready, { cores: "0" })).ok, false,
      "an invalid resource input blocks");
    assertEq(internals.submitCheck(Object.assign({}, ready, {
      resourcesStatus: "error", resourcesError: new Error("capacity down")
    })).ok, false);
    assertEq(internals.submitCheck(Object.assign({}, ready, { multiplicity: "2" })).ok, false,
      "the local electronic check still blocks");

    const request = {
      schema_version: 1,
      workflow: "optimize",
      input: { source_type: "xyz_text", source: WATER, charge: 0, multiplicity: 1 },
      method: { schema_id: "dft_optimize", levels: {} }
    };
    const body = internals.buildSubmitBody(request, "sha256:abc");
    assertEq(body.preflight_fingerprint, "sha256:abc");
    assert(!("preflight_fingerprint" in request), "the frozen request is never mutated");
    assertEq(internals.buildSubmitBody(request, ""), null);
    assertEq(internals.buildSubmitBody(null, "sha256:abc"), null);

    assertEq(internals.normalizeReceipt(JOB_CREATED), {
      jobId: JOB_CREATED.job_id,
      status: "QUEUED",
      attempt: 1,
      planDigest: PREFLIGHT_OK.plan_digest,
      replay: false,
      idempotencyKey: "next-wizard-test"
    });
    assertEq(internals.normalizeReceipt({ status: "QUEUED" }), null, "no job_id is not a receipt");
    assertEq(internals.normalizeReceipt(null), null);
    const replay = internals.normalizeReceipt(Object.assign({}, JOB_CREATED, { idempotent_replay: true }));
    assertEq(replay.replay, true);

    const stale = internals.mapSubmitError({
      status: 400,
      body: { detail: { code: "preflight_stale", message: "fingerprint mismatch", params: { field: "preflight_fingerprint" } } }
    });
    assertEq(stale.key, "next.config.plan_stale");
    assertEq(stale.status, 400);
    assertEq(stale.code, "preflight_stale");
    assert(stale.text.indexOf("重新预检") !== -1, stale.text);
    const conflict = internals.mapSubmitError({
      status: 409,
      body: { detail: { code: "idempotency_conflict", message: "conflict", params: {} } }
    });
    assertEq(conflict.key, "next.capabilityForm.submit.conflict");
    const busy = internals.mapSubmitError({
      status: 409,
      body: { detail: { code: "idempotency_in_progress", message: "in progress", params: {} } }
    });
    assertEq(busy.key, "next.capabilityForm.code.submit_busy");
    assertEq(busy.retryable, true);
    const validation = internals.mapSubmitError({
      status: 422,
      body: { detail: { code: "input_invalid", message: "bad", params: { field: "workflow" } } }
    });
    assertEq(validation.key, "next.capabilityForm.code.validation_failed");
    assertEq(validation.field, "workflow");
    const server = internals.mapSubmitError({ status: 503, body: { detail: "upstream down" } });
    assertEq(server.key, "next.config.submit_failed");
    assert(server.text.indexOf("upstream down") !== -1, server.text);
    assertEq(server.retryable, true);
    const transport = internals.mapSubmitError(new Error("network down"));
    assertEq(transport.key, "next.capabilityForm.submit.unconfirmed");
    assertEq(transport.status, null);
    assertEq(transport.retryable, true);
    const forbidden = internals.mapSubmitError({ status: 403, message: "HTTP 403" });
    assertEq(forbidden.key, "next.config.forbidden");
  });

  await test("A-07: all 15 active entries offered, retired ids never; projection mode follows the schema", async () => {
    const ctx = a07Setup();
    await mount(ctx);
    assertEq(stateOf(ctx), "ready");
    const select = field(ctx, "workflow");
    assertEq(optionsOf(select), A07_ACTIVE_IDS, "all 15 active entries, in catalog order");
    assertEq(select.value, "singlepoint", "default is the first active catalog entry");

    const retiredIds = A07_WORKFLOWS
      .filter((entry) => entry.status !== "active" || entry.visible === false)
      .map((entry) => entry.id);
    retiredIds.forEach((id) => {
      assert(optionsOf(select).indexOf(id) === -1, "retired/hidden entry must never be offered: " + id);
    });

    for (const id of A07_ACTIVE_IDS) {
      await a07Select(ctx, id);
      assertEq(attr(ctx, "data-acp-method-projection"), A07_METHOD_PROJECTION_BY_ID[id],
        id + ": projection mode");
      assertEq(attr(ctx, "data-acp-method-support"), "ok", id + ": schema support");
      assert(subtreeText(field(ctx, "workflow-error")).indexOf("schema") === -1,
        id + ": no schema error on a supported catalog row");
      const levelCount = walk(ctx.host).filter((node) => node.getAttribute &&
        node.getAttribute("data-level") != null).length;
      if (id === "XtbPathSearch" || id === "OrcaGradient" || id === "XtbConstrainedOpt") {
        assertEq(levelCount, 1, id + ": the empty schema level is still projected");
        assertEq(walk(ctx.host).filter((node) => node.getAttribute &&
          node.getAttribute("data-field") != null && node.getAttribute("data-level") != null).length,
        0, id + ": no method field for a frozen payload");
      } else {
        assert(levelCount > 0, id + ": schema levels are rendered");
      }
      if (A07_METHOD_PROJECTION_BY_ID[id] === "extended") {
        assertEq(attr(ctx, "data-acp-contract-state"), "ready", id + ": workflow contract read");
        assertEq(attr(ctx, "data-acp-input-adapter"), A07_ADAPTER_BY_ID[id], id + ": adapter");
      } else {
        assertEq(attr(ctx, "data-acp-contract-state"), "idle", id + ": simple workflows keep the two-catalog path");
      }
    }

    const called = contractIds(ctx);
    called.forEach((id) => {
      assert(A07_ACTIVE_IDS.indexOf(id) !== -1, "contract requested only for an active catalog entry: " + id);
      assert(retiredIds.indexOf(id) === -1, "contract never requested for a retired entry: " + id);
    });
    assertEq(called.filter((id) => A07_METHOD_PROJECTION_BY_ID[id] === "extended").length,
      A07_ACTIVE_IDS.filter((id) => A07_METHOD_PROJECTION_BY_ID[id] === "extended").length,
      "exactly one contract read per selected extended workflow");
    ctx.instance.dispose();
  });

  await test("A-07: schema-driven widgets (select/bool/int/multi) and profile levels", async () => {
    const ctx = a07Setup();
    await mount(ctx);

    await a07Select(ctx, "scan");
    const kind = find(ctx.host, "data-field", "scan_coordinate_kind");
    assert(!!kind && kind.tagName === "SELECT", "scan coordinate kind is a catalog select");
    assertEq(optionsOf(kind), ["distance", "angle", "dihedral"]);
    assertEq(kind.value, "distance");
    const scants = find(ctx.host, "data-field", "scan_use_scants");
    assert(!!scants, "scan_use_scants is rendered as a checkbox");
    assertEq(scants.checked, false, "ScanTS default is false, never a truthy string");
    const points = find(ctx.host, "data-field", "scan_coordinate_points");
    assert(!!points, "scan points is rendered");
    assertEq(points.value, "11", "profile value seeds the numeric field");

    await a07Select(ctx, "nmr");
    const nuclei = findAll(ctx.host, "data-field", "nuclei");
    assertEq(nuclei.length, 3, "multi-select options are catalog checkboxes");
    assertEq(nuclei.filter((node) => node.checked).length, 2, "profile default nuclei are selected");
    assertEq(find(ctx.host, "data-field", "functional").value, "wB97M-V");
    assert(!!find(ctx.host, "data-level", "conformer"), "the conformer level is rendered");
    assert(!!find(ctx.host, "data-level", "giaoa"), "the GIAO level is rendered");

    await a07Select(ctx, "Confsearch");
    const levelWraps = (id) => walk(ctx.host).filter((node) => node.getAttribute &&
      node.getAttribute("data-level") === id && node.className.indexOf("acp-wizard-level") >= 0);
    assertEq(levelWraps("refinement_sp").length, 0,
      "a profile that names a subset must not imply the hidden optional level");
    const profileSelect = field(ctx, "profile");
    assertEq(optionsOf(profileSelect), ["xtb-crest", "censo-crest"]);
    profileSelect.value = "censo-crest";
    profileSelect.fire("change");
    await flush();
    assertEq(levelWraps("refinement_sp").length, 1,
      "switching the profile seeds and shows its levels");
    assertEq(find(ctx.host, "data-field", "functional").value, "wB97M-V");

    await a07Select(ctx, "BatchOptimize");
    assert(!!find(ctx.host, "data-field", "single_point_method"), "batch method field");
    assert(!!find(ctx.host, "data-field", "temperature"), "batch thermochemistry field");
    assertEq(findAll(ctx.host, "data-field", "electronic_state").length, 0,
      "server-side module widgets are not rendered as a generic field");

    await a07Select(ctx, "XtbPathSearch");
    assertEq(walk(ctx.host).filter((node) => node.getAttribute &&
      node.getAttribute("data-field") != null && node.getAttribute("data-level") != null).length,
    0, "frozen payload has no method fields");
    assertEq(find(ctx.host, "data-role", "workflow-payload-wrap").hidden, false,
      "the payload textarea is the workflow input");
    ctx.dom.document.dispatchEvent({ type: "acp:langchange", detail: { lang: "en-US" } });
    await flush();
    assertEq(find(ctx.host, "data-field", "functional"), null);
    assertEq(attr(ctx, "data-acp-method-projection"), "extended");
    ctx.instance.dispose();
  });

  await test("A-07: scan preflight keeps typed boolean/number values and input coordinates", async () => {
    const ctx = a07Setup();
    await a07ReachPreflight(ctx, "scan");
    const body = a07PreflightBody(ctx);
    assertEq(body.workflow, "scan");
    assertEq(body.input.scan_coordinates, ["0,1,1,2.5"],
      "compact coordinate is derived from the schema start/end and the explicit atom indices");
    assertEq(body.method.schema_id, "dft_scan");
    assertEq(body.method.levels.scan.scan_coordinate_kind, "distance");
    assertEq(body.method.levels.scan.scan_coordinate_start, "1",
      "float fields travel as canonical decimal strings (the plan digest refuses JSON floats)");
    assertEq(body.method.levels.scan.scan_coordinate_end, "2.5");
    assertEq(body.method.levels.scan.scan_coordinate_points, 11, "integer stays an integer");
    assertEq(body.method.levels.scan.scan_use_scants, false, "false survives the request builder");
    assertEq(attr(ctx, "data-acp-can-submit"), "true");
    await openCheck(ctx);
    await confirmCheck(ctx);
    assertEq(callsTo(ctx, "/jobs").length, 1);
    assertEq(attr(ctx, "data-acp-receipt-job"), "20261031_001_scan");
    assertEq(a07JobBody(ctx).method.levels.scan.scan_use_scants, false);
    ctx.instance.dispose();
  });

  await test("A-07: PES builds input.coordinate + input.protocol and never sends method.levels", async () => {
    const ctx = a07Setup();
    await a07ReachPreflight(ctx, "PESsearch");
    const body = a07PreflightBody(ctx);
    assertEq(body.workflow, "PESsearch");
    assertEq(body.method.mode, "bond_length_scan", "mode comes from the contract enum");
    assertEq(body.method.schema_id, "pes_scan");
    assert(!("levels" in body.method), "PESsearch refuses method.levels; the protocol rides in input");
    assertEq(body.input.coordinate.kind, "distance");
    assertEq(body.input.coordinate.atoms, [0, 1]);
    assertEq(body.input.coordinate.start, "1");
    assertEq(body.input.coordinate.end, "2.5");
    assertEq(body.input.coordinate.n_points, 11);
    assertEq(body.input.protocol.scan_driver.use_scants, false);
    assertEq(body.input.protocol.scan_optimizer.method, "r2SCAN-3c");
    assertEq(body.input.protocol.single_point.enabled, true);
    assertEq(body.input.protocol.single_point.method, "wB97M-V");
    assert(typeof body.input.source === "object", "the PES source is the documented object envelope");
    assertEq(body.input.source.xyz_text, ETHANOL_XYZ);
    await openCheck(ctx);
    await confirmCheck(ctx);
    assertEq(callsTo(ctx, "/jobs").length, 1);
    assertEq(attr(ctx, "data-acp-receipt-job"), "20261031_001_PESsearch");
    ctx.instance.dispose();
  });

  await test("A-07: source workflows bind a task product; method is omitted where the contract forbids it", async () => {
    const irc = a07Setup();
    await a07ReachPreflight(irc, "irc");
    const ircBody = a07PreflightBody(irc);
    assertEq(ircBody.input.source_job_id, "20261031_001_frequency");
    assertEq(ircBody.input.source_product_id, "ts_candidate");
    assertEq(ircBody.method.levels.irc.maxpoints, 40, "IRC level values are typed numbers");
    assertEq(attr(irc, "data-acp-input-adapter"), "source");
    await openCheck(irc);
    await confirmCheck(irc);
    assertEq(attr(irc, "data-acp-receipt-job"), "20261031_001_irc");
    irc.instance.dispose();

    const tsmode = a07Setup();
    await a07ReachPreflight(tsmode, "tsmode");
    const tsBody = a07PreflightBody(tsmode);
    assertEq(tsBody.input.source_job_id, "20261031_001_frequency");
    assertEq(tsBody.input.source_mode_index, 6, "native mode index is an integer");
    assertEq(tsBody.input.max_iterations, 80, "schema max_steps maps to the input consumer");
    assertEq(tsBody.input.recalc_hess, "auto");
    assert(!("method" in tsBody), "tsmode contract exposes no method keys");
    assertEq(attr(tsmode, "data-acp-can-submit"), "true");
    await openCheck(tsmode);
    await confirmCheck(tsmode);
    assertEq(attr(tsmode, "data-acp-receipt-job"), "20261031_001_tsmode");
    tsmode.instance.dispose();
  });

  await test("A-07: frozen payload is explicit and verbatim; missing/invalid JSON blocks locally", async () => {
    const ctx = a07Setup();
    await a07OpenInput(ctx, "XtbPathSearch");

    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "input", "empty payload must not advance");
    assert(subtreeText(field(ctx, "nav-hint")).indexOf("JSON") !== -1,
      "the blocking reason names the payload: " + subtreeText(field(ctx, "nav-hint")));

    const area = field(ctx, "workflow-payload");
    area.value = "{not-json";
    area.fire("input");
    await flush();
    assertEq(fieldError(ctx, "payload").hidden, false, "invalid JSON has a visible field error");
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "input");
    assertEq(callsTo(ctx, "/preflight").length, 0, "invalid JSON never reaches preflight");

    area.value = JSON.stringify({ request_version: "fixture", endpoints: [{ id: "a" }] });
    area.fire("input");
    await flush();
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "review");
    await runPreflight(ctx);
    const body = a07PreflightBody(ctx);
    assertEq(body.workflow, "XtbPathSearch");
    assert(!("method" in body), "frozen payload sends no invented method block");
    assertEq(body.input.path_request.request_version, "fixture");
    assertEq(body.input.path_request.endpoints[0].id, "a");
    await openCheck(ctx);
    await confirmCheck(ctx);
    assertEq(attr(ctx, "data-acp-receipt-job"), "20261031_001_XtbPathSearch");
    ctx.instance.dispose();
  });

  await test("A-07: workflow contract loading/error is an explicit local block; retry recovers", async () => {
    const gate = deferred();
    let failContract = false;
    const ctx = setup({
      responder: (pathname, opts) => {
        if (pathname.indexOf("/api/capabilities?workflow=") === 0) {
          if (failContract) {
            const error = new Error("contract down");
            error.status = 503;
            error.body = { detail: "contract down" };
            throw error;
          }
          return gate.promise;
        }
        return a07Responder(pathname, opts);
      }
    });
    await mount(ctx);
    await a07Select(ctx, "scan");
    assertEq(attr(ctx, "data-acp-contract-state"), "loading");
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "input");
    assertEq(attr(ctx, "data-acp-step-valid"), "false", "a loading contract blocks the input step");
    assertEq(attr(ctx, "data-acp-can-submit"), "false");
    assert(subtreeText(field(ctx, "nav-hint")).indexOf("契约") !== -1,
      "the loading reason is visible: " + subtreeText(field(ctx, "nav-hint")));

    gate.resolve({ workflow_contract: a07Contract("scan") });
    await flush();
    assertEq(attr(ctx, "data-acp-contract-state"), "ready");
    assertEq(attr(ctx, "data-acp-step-valid"), "false", "still needs a structure and scan atoms");

    ctx.instance.dispose();
    const failing = setup({
      responder: (pathname, opts) => {
        if (pathname.indexOf("/api/capabilities?workflow=") === 0 && failContract === false) {
          failContract = true;
          const error = new Error("contract down");
          error.status = 503;
          error.body = { detail: "contract down" };
          throw error;
        }
        return a07Responder(pathname, opts);
      }
    });
    await mount(failing);
    await a07Select(failing, "scan");
    assertEq(attr(failing, "data-acp-contract-state"), "error");
    assertEq(action(failing, "retry-contract").hidden, false);
    assert(subtreeText(field(failing, "contract-status")).indexOf("contract down") !== -1,
      "the server reason is visible");
    action(failing, "retry-contract").fire("click");
    await flush();
    assertEq(attr(failing, "data-acp-contract-state"), "ready");
    failing.instance.dispose();
  });

  await test("A-07: a late contract response after a switch/dispose cannot paint", async () => {
    const firstGate = deferred();
    let contractReads = 0;
    const ctx = setup({
      responder: (pathname, opts) => {
        if (pathname.indexOf("/api/capabilities?workflow=") === 0) {
          contractReads += 1;
          if (contractReads === 1) return firstGate.promise;
          return a07Responder(pathname, opts);
        }
        return a07Responder(pathname, opts);
      }
    });
    await mount(ctx);
    await a07Select(ctx, "scan");
    assertEq(attr(ctx, "data-acp-contract-state"), "loading");

    await a07Select(ctx, "PESsearch");
    assertEq(attr(ctx, "data-acp-contract-state"), "ready");
    assertEq(attr(ctx, "data-acp-input-adapter"), "scan");
    firstGate.resolve({ workflow_contract: a07Contract("scan") });
    await flush();
    assertEq(attr(ctx, "data-acp-contract-state"), "ready", "the old response is dropped");
    assertEq(attr(ctx, "data-acp-input-adapter"), "scan");
    assertEq(contractIds(ctx).slice(-1)[0], "PESsearch", "the current workflow's contract is the live one");

    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0);
    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0, "dispose is idempotent after a contract read");
  });

  await test("A-07: every active catalog entry reaches A-05 preflight and A-06 submit", async () => {
    for (const id of A07_ACTIVE_IDS) {
      const ctx = a07Setup();
      await a07ReachPreflight(ctx, id);
      assertEq(attr(ctx, "data-acp-can-submit"), "true", id + ": A-08 check pass");
      const preflight = a07PreflightBody(ctx);
      assertEq(preflight.workflow, id);
      const entry = A07_WORKFLOWS.find((row) => row.id === id);
      if (preflight.method) {
        assertEq(preflight.method.schema_id, entry.method_schema_id, id + ": schema_id projection");
      }
      await openCheck(ctx);
      await confirmCheck(ctx);
      const jobs = callsTo(ctx, "/jobs");
      assertEq(jobs.length, 1, id + ": one submit request");
      const body = bodyOf(jobs[0]);
      assertEq(body.workflow, id);
      assertEq(body.preflight_fingerprint, PREFLIGHT_OK.plan_digest,
        id + ": frozen A-05 fingerprint");
      assertEq(attr(ctx, "data-acp-submit-state"), "submitted", id + ": real receipt");
      assertEq(attr(ctx, "data-acp-receipt-job"), "20261031_001_" + id);
      assertEq(ctx.commands.opened.length, 1, id + ": openTask once");
      ctx.instance.dispose();
    }
  });

  await test("A-07: missing/empty method schema is an explicit local block, never a generic form", async () => {
    const missing = { id: "custom-no-schema", label: "No Schema", status: "active", visible: true, method_schema_id: "does_not_exist", default_backend: "orca" };
    const empty = { id: "custom-empty-schema", label: "Empty Schema", status: "active", visible: true, method_schema_id: "empty_schema", default_backend: "orca" };
    const catalog = {
      basis_catalog: METHOD_CATALOG.basis_catalog,
      method_meta: METHOD_CATALOG.method_meta,
      field_definitions: METHOD_CATALOG.field_definitions,
      method_schemas: { empty_schema: { method_levels: [], profiles: [] } }
    };
    const ctx = setup({
      responder: (pathname) => {
        if (pathname === API + "/workflow-catalog") return { workflows: [missing, empty] };
        if (pathname === API + "/method-catalog") return catalog;
        if (pathname === CAPABILITIES_PATH) return CAPABILITIES;
        if (pathname === API + "/nodes") return NODES;
        if (pathname === API + "/drafts") return { items: [] };
        throw new Error("unexpected path: " + pathname);
      }
    });
    await mount(ctx);
    assertEq(optionsOf(field(ctx, "workflow")), [missing.id, empty.id]);
    assertEq(attr(ctx, "data-acp-method-support"), "schema_missing");
    assertEq(attr(ctx, "data-acp-step-valid"), "false");
    assert(subtreeText(field(ctx, "workflow-error")).length > 0, "visible missing-schema reason");
    assertEq(findAll(ctx.host, "data-field").length, 0, "no generic method form is rendered");
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "workflow", "a missing schema cannot reach the input step");

    const select = field(ctx, "workflow");
    select.value = empty.id;
    select.fire("change");
    await flush();
    assertEq(attr(ctx, "data-acp-method-support"), "schema_empty");
    assertEq(attr(ctx, "data-acp-step-valid"), "false");
    assert(subtreeText(field(ctx, "workflow-error")).length > 0, "visible empty-schema reason");
    assertEq(findAll(ctx.host, "data-field").length, 0);
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "workflow");
    assertEq(callsTo(ctx, "/preflight").length, 0);
    assertEq(callsTo(ctx, "/jobs").length, 0);
    assertEq(contractCalls(ctx).length, 0, "an unsupported schema never reads a contract");
    ctx.instance.dispose();
  });

  await test("A-07: adapter/scan/validation internals are pure and total", async () => {
    const { sandbox } = setup();
    const internals = sandbox.ACPNextModules["task-wizard"]._internals;
    const entries = internals.filterWorkflows({ workflows: A07_WORKFLOWS });
    assertEq(entries.map((entry) => entry.id), A07_ACTIVE_IDS);

    entries.forEach((entry) => {
      const method = internals.projectMethod(entry, A07_METHOD_CATALOG);
      assertEq(internals.methodSupport(method), "", entry.id + ": method schema support");
      assertEq(method.extended ? "extended" : "minimal", A07_METHOD_PROJECTION_BY_ID[entry.id]);
      assertEq(internals.validateMethodSchema(method).ok, true, entry.id + ": seeded defaults validate");
    });

    const byId = {};
    entries.forEach((entry) => { byId[entry.id] = entry; });
    function adapterFor(id) {
      const method = internals.projectMethod(byId[id], A07_METHOD_CATALOG);
      return internals.projectInputAdapter(byId[id], method, {
        status: "ready",
        body: { workflow_contract: a07Contract(id) }
      });
    }
    assertEq(adapterFor("Confsearch").kind, "structure");
    assertEq(adapterFor("Confsearch").methodEmit, "levels");
    assertEq(adapterFor("scan").kind, "scan");
    assertEq(adapterFor("scan").pes, false);
    assertEq(adapterFor("scan").methodEmit, "levels");
    assertEq(adapterFor("PESsearch").kind, "scan");
    assertEq(adapterFor("PESsearch").pes, true);
    assertEq(adapterFor("PESsearch").methodEmit, "protocol");
    assertEq(adapterFor("BatchOptimize").kind, "batch");
    assertEq(adapterFor("irc").kind, "source");
    assertEq(adapterFor("irc").methodEmit, "levels");
    assertEq(adapterFor("tsmode").kind, "source");
    assertEq(adapterFor("tsmode").methodEmit, "none");
    assertEq(adapterFor("XtbPathSearch").kind, "payload");
    assertEq(adapterFor("XtbPathSearch").payloadKey, "path_request");
    assertEq(adapterFor("casscf").kind, "structure", "a not_provided contract falls back to the schema envelope");
    assertEq(adapterFor("casscf").coverage, "not_provided");

    const scanMethod = internals.projectMethod(byId.scan, A07_METHOD_CATALOG);
    assertEq(internals.parseScanAtoms("0,1", 2, 3).ok, true);
    assertEq(internals.parseScanAtoms("0,1", 2, 3).atoms, [0, 1]);
    assertEq(internals.parseScanAtoms("0,3", 2, 3).code, "range");
    assertEq(internals.parseScanAtoms("0,0", 2, 3).code, "distinct");
    assertEq(internals.parseScanAtoms("0", 2, 3).code, "count");
    assertEq(internals.parseScanAtoms("a,1", 2, 3).code, "integer");
    assertEq(internals.parseScanAtoms("", 2, 3).code, "required");

    const badMethod = JSON.parse(JSON.stringify(scanMethod));
    badMethod.levels[0].rows.find((row) => row.field === "scan_coordinate_points").value = "2";
    assertEq(internals.validateMethodSchema(badMethod).ok, false, "catalog min is enforced");
    const badSelect = JSON.parse(JSON.stringify(scanMethod));
    badSelect.levels[0].rows.find((row) => row.field === "scan_coordinate_kind").value = "nope";
    assertEq(internals.validateMethodSchema(badSelect).ok, false, "catalog option membership is enforced");

    const tsmodeMethod = internals.projectMethod(byId.tsmode, A07_METHOD_CATALOG);
    const tsmodeRequest = internals.buildWorkflowRequest({
      entry: byId.tsmode,
      method: tsmodeMethod,
      adapter: adapterFor("tsmode"),
      contract: { status: "ready", body: { workflow_contract: a07Contract("tsmode") } },
      structure: null,
      charge: "",
      multiplicity: "",
      cores: null,
      mem: "",
      projectId: "",
      extras: { sourceJobId: "job-1", sourceProductId: "", sourceModeIndex: "6", scanAtoms: "", payload: "" }
    });
    assertEq(tsmodeRequest.errors.length, 0);
    assertEq(tsmodeRequest.request.input.max_iterations, 80);
    assert(!("method" in tsmodeRequest.request));

    const missingSource = internals.buildWorkflowRequest({
      entry: byId.tsmode,
      method: tsmodeMethod,
      adapter: adapterFor("tsmode"),
      contract: { status: "ready", body: { workflow_contract: a07Contract("tsmode") } },
      structure: null,
      charge: "",
      multiplicity: "",
      cores: null,
      mem: "",
      projectId: "",
      extras: { sourceJobId: "", sourceProductId: "", sourceModeIndex: "", scanAtoms: "", payload: "" }
    });
    assert(missingSource.errors.some((error) => error.field === "source_job_id"));
    assertEq(missingSource.request, null, "an invalid adapter never yields a partial generic body");
  });

  await test("A-06/A-08 chrome reuses only shared i18n keys present in both languages", async () => {
    const ctx = setup();
    await reachedCheckedPlan(ctx);
    await openCheck(ctx);
    const keys = collectAttr(root(ctx), "data-i18n");
    const dictSandbox = makeSandbox({});
    loadScript("js/shell/i18n.js", dictSandbox);
    const zh = dictSandbox.ACPShell.i18n.DICT["zh-CN"];
    const en = dictSandbox.ACPShell.i18n.DICT["en-US"];
    [
      "next.config.review",
      "next.config.submit",
      "next.config.receipt",
      "next.config.open_task",
      "next.tasks.dialog.cancel",
      "next.capabilityForm.preflight_fingerprint",
      "next.approval.plan.resources"
    ].forEach((key) => {
      assert(keys.indexOf(key) !== -1, "missing static key: " + key);
      assert(key in zh, "shared key missing from zh-CN: " + key);
      assert(key in en, "shared key missing from en-US: " + key);
    });
    ctx.dom.document.dispatchEvent({ type: "acp:langchange", detail: { lang: "en-US" } });
    await flush();
    assertEq(attr(ctx, "data-acp-submit-check"), "ok", "a language change keeps the check state");
    assertEq(dialogOf(ctx).hidden, false, "the open dialog survives a repaint");
    ctx.instance.dispose();
  });

  // ------------------------------------------------------------- A-09 drafts --

  await test("A-09: mount looks up at most one draft; no draft means an empty banner and no writes", async () => {
    const ctx = a09Setup();
    await mount(ctx);
    assertEq(draftState(ctx), "empty", "a fresh server has no draft");
    assertEq(draftStatus(ctx), "none");
    assertEq(draftBanner(ctx).hidden, true);
    assertEq(draftWrites(ctx, "GET").length, 1, "exactly one list read");
    assertEq(draftWrites(ctx, "POST").length + draftWrites(ctx, "PUT").length +
      draftWrites(ctx, "DELETE").length, 0, "mount never writes");
    assertEq(attr(ctx, "data-acp-draft-busy"), "false");
    ctx.instance.dispose();

    // A failed draft lookup is surfaced but never blocks the wizard.
    const failing = a09Setup({
      responder: (pathname, opts) => {
        if (pathname === API + "/drafts") {
          const error = new Error("HTTP 503");
          error.status = 503;
          error.body = { detail: "draft store down" };
          throw error;
        }
        return defaultResponse(pathname, opts);
      }
    });
    await mount(failing);
    assertEq(draftState(failing), "error");
    assertEq(stateOf(failing), "ready", "the catalog lifecycle is unaffected");
    assert(draftNoticeText(failing).indexOf("draft store down") !== -1, draftNoticeText(failing));
    assertEq(field(failing, "workflow").disabled, false, "the wizard stays usable");
    assertEq(draftBanner(failing).hidden, false);
    assertEq(callsTo(failing, "/preflight").length, 0);
    failing.instance.dispose();
  });

  await test("A-09: autosave is debounced, creates once then PUTs with expected_revision", async () => {
    const backend = draftBackend({ snapshot: null });
    const ctx = a09Setup({ responder: backend.responder });
    await mount(ctx);
    ctx.instance.updateContext({ projectId: "proj-a" });
    await advanceToInput(ctx);

    const smiles = field(ctx, "smiles");
    ["C", "CC", "CCO"].forEach((value) => {
      smiles.value = value;
      smiles.fire("input");
    });
    setRoleValue(ctx, "charge", "0");
    await flush();
    assertEq(ctx.clock.pending(), 1, "a burst of edits arms exactly one debounce timer");
    assertEq(draftWrites(ctx, "POST").length + draftWrites(ctx, "PUT").length, 0,
      "no write before the debounce elapses");
    await waitForDraft(ctx);
    assertEq(draftWrites(ctx, "POST").length, 1, "one POST after the debounce");
    assertEq(draftWrites(ctx, "PUT").length, 0);
    assertEq(draftState(ctx), "ready");
    assertEq(draftStatus(ctx), "saved");
    assertEq(backend.writes.length, 1, "one draft row write");
    const create = backend.writes[0].body;
    assertEq(create.workflow, "optimize", "row metadata carries the workflow id");
    assertEq(create.project_id, "proj-a", "row metadata carries the project id");
    assert(create.name.indexOf("task-wizard") === 0, create.name);
    assertEq(create.snapshot.kind, "task-wizard");
    assertEq(create.snapshot.version, 1);
    assertEq(create.snapshot.workflow_id, "optimize");
    assertEq(create.snapshot.step, 1);
    assertEq(create.snapshot.input.mode, "smiles");
    assertEq(create.snapshot.input.smiles, "CCO");
    assertEq(create.snapshot.input.charge, "0");
    assertEq(create.snapshot.method.levels.optimize.functional, "wB97M-V");
    assert(!("expected_revision" in create), "a create body has no CAS revision");
    assert(!("structure" in create.snapshot), "the resolved structure is never drafted");
    assert(!("source_job_id" in create.snapshot.input), "no task references");
    assert(!("preflight" in create.snapshot));
    assertEq(backend.revision, 2, "the server revision advanced once");

    const cores = field(ctx, "cores");
    ["8", "9"].forEach((value) => {
      cores.value = value;
      cores.fire("input");
    });
    assertEq(ctx.clock.pending(), 1, "the second burst keeps one timer");
    await waitForDraft(ctx);
    const puts = draftWrites(ctx, "PUT");
    assertEq(puts.length, 1, "one PUT after the second debounce");
    assertEq(draftWrites(ctx, "POST").length, 1, "no second create");
    assertEq(puts[0].path, API + "/drafts/" + backend.draftId);
    const update = JSON.parse(puts[0].opts.body);
    assertEq(update.expected_revision, 2, "the PUT carries the server revision");
    assertEq(update.snapshot.resources.cores, "9");
    assertEq(ctx.clock.pending(), 0, "the timer is consumed by the save");
    ctx.instance.dispose();
  });

  await test("A-09: snapshot/normalize/body/error mapping are pure and presentation-only", async () => {
    const { sandbox } = setup();
    const internals = sandbox.ACPNextModules["task-wizard"]._internals;
    const input = {
      entry: { id: "irc" },
      step: 2,
      method: {
        profileId: "default",
        levels: [{
          levelId: "irc",
          rows: [
            { field: "maxpoints", type: "int", value: "40" },
            { field: "step", type: "float", value: "0.1" },
            { field: "scan_use_scants", type: "bool", value: false },
            { field: "nuclei", type: "multi", value: ["1H", "13C"] },
            { field: "left_empty", type: "text", value: "" }
          ]
        }]
      },
      input: {
        mode: "paste", smiles: "", text: WATER, fileName: "w.xyz", charge: "-1", multiplicity: "2",
        structure: { xyz: WATER, formula: "H2O" },
        token: 7
      },
      resources: { cores: "8", memory: "4096MB" },
      extras: {
        sourceJobId: "20261031_001_frequency",
        sourceProductId: "ts_candidate",
        sourceModeIndex: "6",
        scanAtoms: "0,1",
        payload: "{\"request_version\":\"x\"}"
      }
    };
    const snapshot = internals.draftSnapshot(input);
    assertEq(Object.keys(snapshot).sort(),
      ["input", "kind", "method", "payload", "resources", "scan_atoms", "step", "version", "workflow_id"]);
    assertEq(snapshot.kind, "task-wizard");
    assertEq(snapshot.version, 1);
    assertEq(snapshot.step, 2);
    assertEq(snapshot.workflow_id, "irc");
    assertEq(snapshot.method.levels.irc,
      { maxpoints: "40", step: "0.1", scan_use_scants: false, nuclei: ["1H", "13C"] });
    assertEq(snapshot.input.smiles, "");
    assertEq(snapshot.input.text, WATER);
    assertEq(snapshot.input.file_name, "w.xyz");
    assertEq(snapshot.scan_atoms, "0,1");
    assertEq(snapshot.payload, "{\"request_version\":\"x\"}");
    const flat = JSON.stringify(snapshot);
    ["source_job_id", "source_product_id", "source_mode_index", "20261031_001_frequency",
      "ts_candidate", "structure", "preflight", "idempotency", "job_id", "receipt", "token"
    ].forEach((needle) => {
      assert(flat.indexOf(needle) === -1, "the draft snapshot must not carry " + needle);
    });

    assertEq(internals.normalizeDraftSnapshot(snapshot), snapshot, "a same-shape snapshot round-trips");
    assertEq(internals.normalizeDraftSnapshot({ kind: "classic-wizard", snapshot: {} }), null,
      "a foreign draft document is refused");
    assertEq(internals.normalizeDraftSnapshot({ kind: "task-wizard", version: 9 }), null,
      "a version mismatch is refused");
    const dirty = internals.normalizeDraftSnapshot({
      kind: "task-wizard", version: 1, workflow_id: "optimize", step: 9,
      method: { profile_id: "default", levels: { optimize: { functional: "PBE0", nested: { evil: 1 } } } },
      input: { mode: "smiles", smiles: "CCO", structure: { xyz: WATER }, token: 3 },
      resources: { cores: "6", job_id: "job-1" },
      scan_atoms: "0,1", payload: "", extra: "dropped"
    });
    assertEq(dirty.step, 2, "the step clamps to the wizard range");
    assertEq(dirty.method.levels.optimize, { functional: "PBE0" },
      "non-scalar method values are dropped");
    assert(!("structure" in dirty.input), "a nested structure is dropped");
    assert(!("job_id" in dirty.resources), "unknown resource keys are dropped");
    assert(!("extra" in dirty), "unknown top-level keys are dropped");

    const body = internals.draftBody(snapshot, {
      name: "task-wizard · IRC", projectId: "proj-a", expectedRevision: 4
    });
    assertEq(body.expected_revision, 4);
    assertEq(body.workflow, "irc");
    assertEq(body.project_id, "proj-a");
    const createBody = internals.draftBody(snapshot, { projectId: "" });
    assert(!("expected_revision" in createBody), "a create body has no CAS revision");

    assertEq(internals.draftPath("a/b c"), API + "/drafts/a%2Fb%20c");
    assertEq(internals.draftWhen("2026-10-31T09:15:00.123456+00:00"), "2026-10-31 09:15");
    assertEq(internals.draftWhen("bad"), "");
    const conflict = internals.mapDraftError({ status: 409, body: { detail: "revision_conflict" } });
    assertEq(conflict.conflict, true);
    assertEq(conflict.code, "revision_conflict");
    assertEq(internals.mapDraftError({ status: 404, body: { detail: "draft_not_found" } }).missing, true);
    assertEq(internals.mapDraftError({ status: 503, body: { detail: "down" } }).retryable, true);
    assertEq(internals.mapDraftError(new Error("offline")).status, null);
  });

  await test("A-09: a saved draft shows the banner and restores inputs/method/resources/step", async () => {
    const snapshot = a09Snapshot();
    const backend = draftBackend({ snapshot: snapshot, revision: 7 });
    const ctx = a09Setup({ responder: backend.responder });
    await mount(ctx);
    assertEq(draftState(ctx), "ready");
    assertEq(draftStatus(ctx), "found");
    assertEq(draftBanner(ctx).hidden, false);
    assert(draftStatusText(ctx).indexOf("发现保存的草稿") !== -1, draftStatusText(ctx));
    assert(draftStatusText(ctx).indexOf("2026-10-31 09:15") !== -1,
      "the server updated_at is shown, never a fabricated time");
    assertEq(draftButton(ctx, "draft-restore").hidden, false);
    assertEq(draftButton(ctx, "draft-discard").hidden, false);
    assertEq(draftButton(ctx, "draft-reload").hidden, true);
    assertEq(draftButton(ctx, "draft-fork").hidden, true);
    assertEq(backend.writes.length, 0, "mount never writes");

    draftButton(ctx, "draft-restore").fire("click");
    await flush();
    assertEq(draftStatus(ctx), "restored");
    assertEq(field(ctx, "workflow").value, "singlepoint");
    assertEq(field(ctx, "profile").value, "fast", "the saved method profile is restored");
    assertEq(find(ctx.host, "data-field", "functional").value, "PBE0");
    assertEq(find(ctx.host, "data-field", "basis").value, "def2-SVP");
    assertEq(roleValue(ctx, "smiles"), "CCO");
    assertEq(roleValue(ctx, "charge"), "-1", "raw typed charge is restored verbatim");
    assertEq(roleValue(ctx, "multiplicity"), "2");
    assertEq(attr(ctx, "data-acp-step"), "input", "the review step is never restored");
    assertEq(roleValue(ctx, "cores"), "8", "restored cores survive the server defaults");
    assertEq(roleValue(ctx, "memory"), "4096MB");
    assertEq(structureState(ctx), "empty", "the resolved structure is never drafted");
    assertEq(attr(ctx, "data-acp-preflight-state"), "idle", "no preflight is restored");
    assertEq(attr(ctx, "data-acp-can-submit"), "false");
    assertEq(backend.writes.length, 0, "restore writes nothing");
    assertEq(backend.deletes, 0);
    assertEq(callsTo(ctx, "/preflight").length, 0);
    ctx.instance.dispose();
  });

  await test("A-09: restore waits for the catalog and never restores review state", async () => {
    const gate = deferred();
    const backend = draftBackend({ snapshot: a09Snapshot(), revision: 2 });
    let holdCatalog = true;
    const ctx = a09Setup({
      responder: (pathname, opts) => {
        if (pathname === API + "/workflow-catalog" && holdCatalog) {
          holdCatalog = false;
          return gate.promise;
        }
        return backend.responder(pathname, opts);
      }
    });
    await ctx.instance.mount(ctx.host, {});
    await flush();
    assertEq(attr(ctx, "data-acp-module-state"), "loading", "the catalog is still loading");
    assertEq(draftStatus(ctx), "found", "the draft lookup is independent of the catalog");
    draftButton(ctx, "draft-restore").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "workflow", "restore is queued, not half-applied");
    assertEq(draftStatus(ctx), "found");
    assertEq(field(ctx, "workflow").value, "", "no workflow is selected before the catalog");

    gate.resolve({ workflows: WORKFLOWS });
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(draftStatus(ctx), "restored", "the queued restore applies once the catalog is ready");
    assertEq(field(ctx, "workflow").value, "singlepoint");
    assertEq(structureState(ctx), "empty");
    assertEq(attr(ctx, "data-acp-step"), "input");
    ctx.instance.dispose();
  });

  await test("A-09: explicit discard deletes the draft; a failed delete is surfaced and retryable", async () => {
    const backend = draftBackend({ snapshot: a09Snapshot(), revision: 3 });
    const ctx = a09Setup({ responder: backend.responder });
    await mount(ctx);
    assertEq(backend.deletes, 0);
    draftButton(ctx, "draft-discard").fire("click");
    await flush();
    assertEq(backend.deletes, 1, "one DELETE for the explicit discard");
    assertEq(backend.exists, false);
    assertEq(draftState(ctx), "empty");
    assertEq(draftStatus(ctx), "none");
    assertEq(draftBanner(ctx).hidden, true);
    assertEq(backend.writes.length, 0, "discard writes no snapshot");
    ctx.instance.dispose();

    const failing = draftBackend({ snapshot: a09Snapshot(), revision: 4 });
    const error = new Error("HTTP 500");
    error.status = 500;
    error.body = { detail: "store unavailable" };
    failing.deleteError = error;
    const ctx2 = a09Setup({ responder: failing.responder });
    await mount(ctx2);
    draftButton(ctx2, "draft-discard").fire("click");
    await flush();
    assertEq(draftState(ctx2), "error");
    assertEq(draftBanner(ctx2).hidden, false, "the banner stays for a retry");
    assertEq(draftBanner(ctx2).getAttribute("data-acp-draft-notice"), "delete");
    assert(draftNoticeText(ctx2).indexOf("丢弃失败") !== -1, draftNoticeText(ctx2));
    assertEq(draftButton(ctx2, "draft-discard").hidden, false);
    assertEq(draftButton(ctx2, "draft-discard").disabled, false);
    failing.deleteError = null;
    draftButton(ctx2, "draft-discard").fire("click");
    await flush();
    assertEq(failing.deletes, 2, "the retry is a second DELETE");
    assertEq(draftState(ctx2), "empty");
    assertEq(draftNoticeText(ctx2), "");
    ctx2.instance.dispose();
  });

  await test("A-09: a 409 revision_conflict stops autosave and offers reload/fork resolution", async () => {
    const backend = draftBackend({ snapshot: a09Snapshot(), revision: 1 });
    const ctx = a09Setup({ responder: backend.responder });
    await mount(ctx);
    draftButton(ctx, "draft-restore").fire("click");
    await flush();
    backend.revision = 5; // another window saved behind our back
    const cores = field(ctx, "cores");
    cores.value = "9";
    cores.fire("input");
    await waitForDraft(ctx);
    assertEq(draftWrites(ctx, "PUT").length, 1, "the stale PUT is attempted once");
    assertEq(draftState(ctx), "conflict");
    assertEq(draftStatus(ctx), "conflict");
    assert(draftStatusText(ctx).indexOf("草稿已在其他地方更新") !== -1, draftStatusText(ctx));
    assertEq(draftButton(ctx, "draft-restore").hidden, true);
    assertEq(draftButton(ctx, "draft-reload").hidden, false);
    assertEq(draftButton(ctx, "draft-fork").hidden, false);

    cores.value = "10";
    cores.fire("input");
    assertEq(ctx.clock.pending(), 0, "autosave stays stopped while conflicted");
    await waitForDraft(ctx);
    assertEq(draftWrites(ctx, "PUT").length, 1, "no further PUT while conflicted");

    draftButton(ctx, "draft-reload").fire("click");
    await flush();
    assertEq(draftStatus(ctx), "restored");
    assertEq(backend.reads, 3, "list + detail + reload detail");
    assertEq(draftWrites(ctx, "PUT").length, 1);
    cores.value = "12";
    cores.fire("input");
    await waitForDraft(ctx);
    const puts = draftWrites(ctx, "PUT");
    assertEq(puts.length, 2, "after the reload a new save is allowed");
    assertEq(JSON.parse(puts[1].opts.body).expected_revision, 5,
      "the adopted revision is the new CAS base");
    ctx.instance.dispose();

    const backend2 = draftBackend({ snapshot: a09Snapshot(), revision: 1 });
    const ctx2 = a09Setup({ responder: backend2.responder });
    await mount(ctx2);
    draftButton(ctx2, "draft-restore").fire("click");
    await flush();
    backend2.revision = 6;
    const cores2 = field(ctx2, "cores");
    cores2.value = "9";
    cores2.fire("input");
    await waitForDraft(ctx2);
    assertEq(draftState(ctx2), "conflict");
    draftButton(ctx2, "draft-fork").fire("click");
    await flush();
    assertEq(backend2.deletes, 0, "fork never deletes the other window's draft");
    assertEq(draftWrites(ctx2, "POST").length, 1, "fork creates one new draft");
    assertEq(draftState(ctx2), "ready");
    assertEq(draftStatus(ctx2), "saved");
    assertEq(backend2.writes.filter((write) => write.method === "POST").length, 1);
    ctx2.instance.dispose();
  });

  await test("A-09: a successful submit deletes the draft without touching the receipt", async () => {
    const backend = draftBackend({ snapshot: a09Snapshot(), revision: 3 });
    const ctx = a09Setup({ responder: backend.responder });
    await mount(ctx);
    await advanceToInput(ctx);
    await loadSmiles(ctx, "CCO");
    setRoleValue(ctx, "charge", "0");
    setRoleValue(ctx, "multiplicity", "1");
    await flush();
    action(ctx, "next").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-step"), "review");
    await runPreflight(ctx);
    await openCheck(ctx);
    await confirmCheck(ctx);
    assertEq(callsTo(ctx, "/jobs").length, 1);
    assertEq(attr(ctx, "data-acp-submit-state"), "submitted");
    assertEq(attr(ctx, "data-acp-receipt-job"), JOB_CREATED.job_id);
    assertEq(backend.deletes, 1, "the submitted plan's draft is deleted");
    assertEq(backend.exists, false);
    assertEq(draftState(ctx), "empty");
    assertEq(draftBanner(ctx).hidden, true);
    const deleteIndex = ctx.client.calls.findIndex((call) =>
      call.path === API + "/drafts/" + backend.draftId && call.opts && call.opts.method === "DELETE");
    const jobIndex = ctx.client.calls.findIndex((call) => call.path === API + "/jobs");
    assert(deleteIndex > jobIndex, "the draft delete happens after the submit receipt");
    assertEq(ctx.commands.opened.length, 1, "navigation is unchanged");
    ctx.instance.dispose();

    // A save still in flight when the receipt arrives is deleted afterwards, so
    // a submitted plan can never leave an orphan draft behind.
    const gate = deferred();
    const backend2 = draftBackend({ snapshot: null });
    const ctx2 = a09Setup({
      responder: (pathname, opts) => {
        if (pathname === API + "/drafts" && opts && opts.method === "POST") {
          return gate.promise.then(() => {
            backend2.exists = true;
            backend2.revision = 1;
            return draftMeta(backend2.draftId, 1);
          });
        }
        return backend2.responder(pathname, opts);
      }
    });
    await mount(ctx2);
    await advanceToInput(ctx2);
    await loadSmiles(ctx2, "CCO");
    await ctx2.clock.run();
    assertEq(draftWrites(ctx2, "POST").length, 1, "the draft create is in flight");
    assertEq(draftStatus(ctx2), "saving");
    action(ctx2, "next").fire("click");
    await flush();
    await runPreflight(ctx2);
    await openCheck(ctx2);
    await confirmCheck(ctx2);
    assertEq(attr(ctx2, "data-acp-submit-state"), "submitted");
    assertEq(backend2.deletes, 0, "the delete waits for the in-flight save");
    gate.resolve();
    await flush();
    assertEq(backend2.deletes, 1, "the queued delete removes the draft created after submit");
    assertEq(draftState(ctx2), "empty");
    assertEq(attr(ctx2, "data-acp-receipt-job"), JOB_CREATED.job_id);
    ctx2.instance.dispose();
  });

  await test("A-09: a draft delete failure after submit is non-fatal and surfaced", async () => {
    const backend = draftBackend({ snapshot: a09Snapshot(), revision: 2 });
    const error = new Error("HTTP 500");
    error.status = 500;
    error.body = { detail: "draft store down" };
    backend.deleteError = error;
    const ctx = a09Setup({ responder: backend.responder });
    await mount(ctx);
    await advanceToInput(ctx);
    await loadSmiles(ctx, "CCO");
    await flush();
    action(ctx, "next").fire("click");
    await flush();
    await runPreflight(ctx);
    await openCheck(ctx);
    await confirmCheck(ctx);
    assertEq(attr(ctx, "data-acp-submit-state"), "submitted", "the receipt is not rolled back");
    assertEq(attr(ctx, "data-acp-receipt-job"), JOB_CREATED.job_id);
    assertEq(attr(ctx, "data-acp-submit-error-status"), "", "no submit error is fabricated");
    assertEq(draftBanner(ctx).getAttribute("data-acp-draft-notice"), "delete");
    assert(draftNoticeText(ctx).indexOf("丢弃失败") !== -1, draftNoticeText(ctx));
    assert(draftNoticeText(ctx).indexOf("draft store down") !== -1);
    assertEq(backend.deletes, 1);
    backend.deleteError = null;
    draftButton(ctx, "draft-discard").fire("click");
    await flush();
    assertEq(backend.deletes, 2, "the surfaced discard is retryable");
    assertEq(draftState(ctx), "empty");
    assertEq(attr(ctx, "data-acp-submit-state"), "submitted", "the receipt survives the cleanup");
    assertEq(attr(ctx, "data-acp-receipt-job"), JOB_CREATED.job_id);
    ctx.instance.dispose();
  });

  await test("A-09: dispose cancels the debounce and drops late draft results", async () => {
    const backend = draftBackend({ snapshot: a09Snapshot(), revision: 1 });
    const ctx = a09Setup({ responder: backend.responder });
    await mount(ctx);
    await advanceToInput(ctx);
    await loadSmiles(ctx, "CCO");
    assertEq(ctx.clock.pending(), 1, "an edit armed the debounce");
    ctx.instance.dispose();
    assertEq(ctx.clock.pending(), 0, "dispose clears the timer");
    await ctx.clock.run();
    assertEq(draftWrites(ctx, "POST").length + draftWrites(ctx, "PUT").length, 0,
      "a disposed instance never writes");

    const gate = deferred();
    const backend2 = draftBackend({ snapshot: null });
    const ctx2 = a09Setup({
      responder: (pathname, opts) => {
        if (pathname === API + "/drafts" && opts && opts.method === "POST") return gate.promise;
        return backend2.responder(pathname, opts);
      }
    });
    await mount(ctx2);
    await advanceToInput(ctx2);
    await loadSmiles(ctx2, "CCO");
    await ctx2.clock.run();
    assertEq(draftWrites(ctx2, "POST").length, 1, "the save is in flight");
    assertEq(draftStatus(ctx2), "saving");
    ctx2.instance.dispose();
    gate.resolve(draftMeta(backend2.draftId, 2));
    await flush();
    assertEq(ctx2.host.children.length, 0, "a late save cannot rebuild the DOM");
    assertEq(ctx2.dom.document.activeDocListeners(), 0);

    const lookupGate = deferred();
    const ctx3 = a09Setup({
      responder: (pathname, opts) => {
        if (pathname === API + "/drafts" && (!opts || !opts.method || opts.method === "GET")) {
          return lookupGate.promise;
        }
        return defaultResponse(pathname, opts);
      }
    });
    await ctx3.instance.mount(ctx3.host, {});
    await flush();
    ctx3.instance.dispose();
    lookupGate.resolve({ items: [{ draft_id: "abc", revision: 1 }] });
    await flush();
    assertEq(ctx3.host.children.length, 0, "a late draft lookup cannot paint");
  });

  await test("NEXT: explicit B → E structure handoff loads real inline XYZ without submitting", async () => {
    const ctx = setup(); await mount(ctx);
    const before = ctx.client.calls.length;
    const xyz = "2\nwater\nO 0 0 0\nH 0 0 1\n";
    assertEq(ctx.instance.loadStructure({ kind: "inline-xyz", text: xyz }).ok, true);
    assert(subtreeText(ctx.host).includes("2"), "wizard renders the imported structure");
    assertEq(ctx.instance.loadStructure({ kind: "job-file", text: xyz }).ok, false);
    assertEq(ctx.client.calls.length, before, "handoff is local and never submits");
    ctx.instance.dispose();
  });

  await test("A-09: draft chrome reuses shared zh/en keys only", async () => {
    const backend = draftBackend({ snapshot: a09Snapshot(), revision: 1 });
    const ctx = a09Setup({ responder: backend.responder });
    await mount(ctx);
    const keys = collectAttr(root(ctx), "data-i18n");
    ["next.config.draft_restore", "next.config.draft_discard",
      "next.config.draft_reload", "next.config.draft_fork"].forEach((key) => {
      assert(keys.indexOf(key) !== -1, "missing static draft key: " + key);
    });
    const dictSandbox = makeSandbox({});
    loadScript("js/shell/i18n.js", dictSandbox);
    const zh = dictSandbox.ACPShell.i18n.DICT["zh-CN"];
    const en = dictSandbox.ACPShell.i18n.DICT["en-US"];
    ["next.config.draft_found", "next.config.draft_saved", "next.config.draft_saving",
      "next.config.draft_conflict", "next.config.draft_error",
      "next.config.draft_restore", "next.config.draft_discard",
      "next.config.draft_reload", "next.config.draft_fork",
      "next.tasks.action_failed", "next.nodes.section_error",
      "next.approval.status.draft"].forEach((key) => {
      assert(key in zh, "draft key missing from zh-CN: " + key);
      assert(key in en, "draft key missing from en-US: " + key);
    });
    assert(draftStatusText(ctx).indexOf("{when}") === -1,
      "the banner copy is formatted, never a raw template");
    assert(draftStatusText(ctx).indexOf("发现保存的草稿（2026-10-31 09:15）") !== -1, draftStatusText(ctx));
    ctx.instance.dispose();
  });
};
