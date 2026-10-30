/*
 * ACP Next — task-config model (UI-03).
 *
 * Pure functions only: workflow-catalog projection, method-schema defaults,
 * semantic locks, client validation, canonical POST /jobs request assembly and
 * create-task draft mapping. No DOM, no network, no timers, no globals besides
 * this single export — the module (`module.js`) owns every side effect and the
 * node tests exercise this file directly.
 *
 * Authority:
 *   - docs/reference/next-change/ACP_Frontend_UI01_Shell_Design_20261030.md
 *   - docs/reference/next-change/ACP_Frontend_UI01_UI02_UI03A_Closure_20261030.md
 *   - src/acp/api/v1_routes.py (POST /preflight, POST /jobs, /drafts)
 *
 * The two-step submit contract is the server's, not ours: `/preflight` returns
 * a canonical `plan_digest` and `POST /jobs` refuses (`preflight_stale`) when
 * the submitted plan no longer matches the fingerprint. Every request built
 * here is therefore deterministic and compared by `planIdentity()`.
 */
window.ACPNextTaskConfigModel = (function () {
  "use strict";

  var SCHEMA_VERSION = 1;
  var DRAFT_VERSION = 1;
  var DRAFT_POINTER_KEY = "acp.next.task-config.draft";

  /*
   * Workflows this panel can assemble end to end: one structure in, a single
   * method level, no extra required file/coordinate argument. Everything else
   * stays visible-but-disabled in the picker (catalog entries are never
   * hidden) with the classic wizard as the explicit path — a Next panel must
   * not pretend to support PES/batch/IRC/tsmode inputs it cannot collect.
   */
  var SIMPLE_WORKFLOWS = ["singlepoint", "optimize", "frequency", "xtb_optimize"];

  /* Fields rendered inline per level. Order matters: the first two are the
   * ones users actually change; the rest live under "advanced". */
  var PRIMARY_FIELDS = ["functional", "basis", "gfn", "opt_level"];
  var SECONDARY_FIELDS = [
    "dispersion",
    "solvent_model",
    "solvent",
    "grid",
    "scf_convergence",
    "max_steps",
    "ri_approximation",
  ];
  /* Server-side modules / preview-only fields: not editable from this panel. */
  var SKIP_FIELDS = ["electronic_state", "recalc_hess", "aux_j_basis", "aux_c_basis"];

  /* Atomic numbers for the electron-count parity check. Unknown elements make
   * the check "unverifiable", never a false error. */
  var Z = {
    H: 1, He: 2, Li: 3, Be: 4, B: 5, C: 6, N: 7, O: 8, F: 9, Ne: 10,
    Na: 11, Mg: 12, Al: 13, Si: 14, P: 15, S: 16, Cl: 17, Ar: 18, K: 19,
    Ca: 20, Sc: 21, Ti: 22, V: 23, Cr: 24, Mn: 25, Fe: 26, Co: 27, Ni: 28,
    Cu: 29, Zn: 30, Ga: 31, Ge: 32, As: 33, Se: 34, Br: 35, Kr: 36, Rb: 37,
    Sr: 38, Y: 39, Zr: 40, Nb: 41, Mo: 42, Tc: 43, Ru: 44, Rh: 45, Pd: 46,
    Ag: 47, Cd: 48, In: 49, Sn: 50, Sb: 51, Te: 52, I: 53, Xe: 54, Cs: 55,
    Ba: 56, La: 57, Ce: 58, Pr: 59, Nd: 60, Pm: 61, Sm: 62, Eu: 63, Gd: 64,
    Tb: 65, Dy: 66, Ho: 67, Er: 68, Tm: 69, Yb: 70, Lu: 71, Hf: 72, Ta: 73,
    W: 74, Re: 75, Os: 76, Ir: 77, Pt: 78, Au: 79, Hg: 80, Tl: 81, Pb: 82,
    Bi: 83, Th: 90, U: 92,
  };

  function clone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  /* Stable, key-sorted JSON: the client-side identity of a frozen plan. */
  function stableStringify(value) {
    if (value == null) return "null";
    if (Array.isArray(value)) {
      return "[" + value.map(stableStringify).join(",") + "]";
    }
    if (typeof value === "object") {
      var keys = Object.keys(value).sort();
      var parts = [];
      for (var i = 0; i < keys.length; i++) {
        var key = keys[i];
        if (value[key] === undefined) continue;
        parts.push(JSON.stringify(key) + ":" + stableStringify(value[key]));
      }
      return "{" + parts.join(",") + "}";
    }
    return JSON.stringify(value);
  }

  /* ── workflow catalog ─────────────────────────────────────────────── */

  /** Project the raw catalog into actionable entries, in catalog order. */
  function filterWorkflows(catalog) {
    var raw = (catalog && catalog.workflows) || [];
    var out = [];
    for (var i = 0; i < raw.length; i++) {
      var entry = raw[i];
      if (!entry || entry.status !== "active" || entry.visible === false) continue;
      out.push({
        id: String(entry.id || ""),
        label: String(entry.label || entry.id || ""),
        labelZh: String(entry.label_zh || entry.label || entry.id || ""),
        category: String(entry.category || ""),
        description: String(entry.description || ""),
        descriptionZh: String(entry.description_zh || ""),
        schemaId: String(entry.method_schema_id || ""),
        backend: String(entry.default_backend || ""),
        requiresBinaries: Array.isArray(entry.requires_binaries) ? entry.requires_binaries.slice() : [],
        requiresSource: Boolean(entry.requires_source),
        supported: SIMPLE_WORKFLOWS.indexOf(entry.id) !== -1,
      });
    }
    return out;
  }

  function findWorkflow(entries, workflowId) {
    for (var i = 0; i < (entries || []).length; i++) {
      if (entries[i].id === workflowId) return entries[i];
    }
    return null;
  }

  function pickDefaultWorkflow(entries) {
    for (var i = 0; i < (entries || []).length; i++) {
      if (entries[i].supported) return entries[i];
    }
    return (entries || [])[0] || null;
  }

  /* ── method schema ────────────────────────────────────────────────── */

  function schemaFor(methodCatalog, schemaId) {
    var schemas = (methodCatalog && methodCatalog.method_schemas) || {};
    return schemas[schemaId] || null;
  }

  function levelsOf(schema) {
    return (schema && Array.isArray(schema.method_levels) ? schema.method_levels : []).slice();
  }

  function profilesOf(schema) {
    return (schema && Array.isArray(schema.profiles) ? schema.profiles : []).slice();
  }

  function pickDefaultProfile(schema) {
    var profiles = profilesOf(schema);
    for (var i = 0; i < profiles.length; i++) {
      if (profiles[i] && profiles[i].profile_id === "default") return profiles[i];
    }
    return profiles[0] || null;
  }

  function profileById(schema, profileId) {
    var profiles = profilesOf(schema);
    for (var i = 0; i < profiles.length; i++) {
      if (profiles[i] && profiles[i].profile_id === profileId) return profiles[i];
    }
    return null;
  }

  function fieldDefinition(methodCatalog, field) {
    var defs = (methodCatalog && methodCatalog.field_definitions) || {};
    return defs[field] || null;
  }

  function defaultFor(def) {
    if (!def || !isObject(def.default)) return undefined;
    if (Object.prototype.hasOwnProperty.call(def.default, "*")) return def.default["*"];
    var keys = Object.keys(def.default);
    return keys.length ? def.default[keys[0]] : undefined;
  }

  /**
   * Engine defaults come from the schema level's allowed_engines first (the
   * catalog is authoritative), then the workflow entry.
   */
  function engineFor(level, entry) {
    var allowed = (level && Array.isArray(level.allowed_engines)) ? level.allowed_engines : [];
    if (allowed.length) return String(allowed[0]);
    if (entry && entry.backend) return String(entry.backend);
    return "orca";
  }

  /**
   * Build the initial `levels` object for a workflow.
   *
   * Profile-backed schemas clone the profile levels (the catalog already froze
   * those defaults). Profile-less schemas (frequency / xtb_optimize) are seeded
   * from FIELD_DEFINITIONS defaults — the same source the classic wizard reads
   * through CatalogUtils.getFieldDefault, never a hardcoded method literal.
   */
  function buildDefaultLevels(schema, methodCatalog, entry, profileId) {
    var levels = {};
    var profile = profileId ? profileById(schema, profileId) : pickDefaultProfile(schema);
    if (profile && isObject(profile.levels)) return clone(profile.levels);
    var defs = levelsOf(schema);
    for (var i = 0; i < defs.length; i++) {
      var levelDef = defs[i];
      if (!levelDef || !levelDef.level_id) continue;
      var level = { engine: engineFor(levelDef, entry) };
      var fields = Array.isArray(levelDef.fields) ? levelDef.fields : [];
      for (var f = 0; f < fields.length; f++) {
        var field = fields[f];
        if (SKIP_FIELDS.indexOf(field) !== -1) continue;
        var value = defaultFor(fieldDefinition(methodCatalog, field));
        if (value !== undefined && typeof value !== "object") level[field] = value;
      }
      levels[levelDef.level_id] = level;
    }
    return levels;
  }

  function levelIds(schema) {
    var out = [];
    var defs = levelsOf(schema);
    for (var i = 0; i < defs.length; i++) {
      if (defs[i] && defs[i].level_id) out.push(defs[i].level_id);
    }
    return out;
  }

  /** Fields a user may edit for one level, split into primary/secondary. */
  function editableFields(schema, levelId) {
    var defs = levelsOf(schema);
    var picked = null;
    for (var i = 0; i < defs.length; i++) {
      if (defs[i] && defs[i].level_id === levelId) { picked = defs[i]; break; }
    }
    if (!picked) return { primary: [], secondary: [] };
    var fields = (Array.isArray(picked.fields) ? picked.fields : []).filter(function (field) {
      return SKIP_FIELDS.indexOf(field) === -1;
    });
    var primary = fields.filter(function (field) { return PRIMARY_FIELDS.indexOf(field) !== -1; });
    var secondary = fields.filter(function (field) { return SECONDARY_FIELDS.indexOf(field) !== -1; });
    if (!primary.length) primary = fields.slice(0, 2);
    if (!secondary.length) secondary = fields.filter(function (field) { return primary.indexOf(field) === -1; });
    return { primary: primary, secondary: secondary };
  }

  /** Resolve select options for one field/engine from the catalog. */
  function fieldOptions(methodCatalog, field, engine) {
    var def = fieldDefinition(methodCatalog, field);
    var catalog = methodCatalog || {};
    if (field === "functional") {
      var meta = catalog.method_meta;
      if (isObject(meta)) return Object.keys(meta).sort();
    }
    if (field === "basis") {
      var basis = catalog.basis_catalog;
      if (Array.isArray(basis) && basis.length) return basis.slice();
    }
    if (def) {
      var perBackend = isObject(def.per_backend) ? def.per_backend[engine] : null;
      if (Array.isArray(perBackend) && perBackend.length) return perBackend.slice();
      if (Array.isArray(def.options) && def.options.length) return def.options.slice();
      if (def.options_source === "dynamic_aux_basis") return ["AutoAux"];
    }
    return [];
  }

  function fieldLabel(def, field, lang) {
    if (def) {
      if (lang === "en") return String(def.label || field);
      return String(def.label_zh || def.label || field);
    }
    return field;
  }

  function fieldHelp(def, lang) {
    if (!def) return "";
    if (lang === "en") return String(def.help || "");
    return String(def.help_zh || def.help || "");
  }

  function functionalMeta(methodCatalog, functional) {
    var meta = (methodCatalog && methodCatalog.method_meta) || {};
    if (!functional) return null;
    if (meta[functional]) return meta[functional];
    var keys = Object.keys(meta);
    for (var i = 0; i < keys.length; i++) {
      if (keys[i].toLowerCase() === String(functional).toLowerCase()) return meta[keys[i]];
    }
    return null;
  }

  /**
   * Apply the catalog semantics the server enforces anyway, so the panel never
   * shows a value the run will not use:
   *   - ri_support !== "user"  → RI/aux bases are not user-selectable;
   *   - builtin_dispersion set → dispersion is baked into the composite method.
   * Returns human-readable notes for the UI (empty when nothing changed).
   */
  function applySemanticLocks(levels, methodCatalog) {
    var notes = [];
    var out = clone(levels || {});
    Object.keys(out).forEach(function (levelId) {
      var level = out[levelId];
      if (!isObject(level)) return;
      var meta = functionalMeta(methodCatalog, level.functional);
      if (!meta) return;
      if (meta.ri_support && String(meta.ri_support) !== "user") {
        if ("ri_approximation" in level) level.ri_approximation = "none";
        if ("aux_j_basis" in level) level.aux_j_basis = "";
        if ("aux_c_basis" in level) level.aux_c_basis = "";
        if (meta.ri_support === "composite") {
          notes.push({ levelId: levelId, key: "next.config.note.composite_ri" });
        }
      }
      if (meta.builtin_dispersion) {
        if ("dispersion" in level) level.dispersion = "none";
        notes.push({ levelId: levelId, key: "next.config.note.builtin_dispersion", vars: { dispersion: meta.builtin_dispersion } });
      }
    });
    return { levels: out, notes: notes };
  }

  /* ── structure + state validation ─────────────────────────────────── */

  /** Parse a formula such as "C2H6O" / "C6H5Br" into {element: count}. */
  function parseFormula(formula) {
    var text = String(formula || "").trim();
    if (!text) return null;
    var counts = {};
    var re = /([A-Z][a-z]?)(\d*)/g;
    var matched = 0;
    var match;
    while ((match = re.exec(text)) !== null) {
      var symbol = match[1];
      var count = match[2] ? parseInt(match[2], 10) : 1;
      if (!isFinite(count) || count <= 0) return null;
      counts[symbol] = (counts[symbol] || 0) + count;
      matched += match[0].length;
    }
    // Any leftover character (charges, dots, parentheses) makes it unparsable.
    if (!matched || matched !== text.length) return null;
    return counts;
  }

  function electronCount(asset, charge) {
    var counts = parseFormula(asset && asset.formula);
    if (!counts) return null;
    var total = 0;
    var keys = Object.keys(counts);
    for (var i = 0; i < keys.length; i++) {
      var z = Z[keys[i]];
      if (z === undefined) return null;
      total += z * counts[keys[i]];
    }
    return total - (Number(charge) || 0);
  }

  /**
   * Charge/multiplicity consistency: every multiplicity must be an integer ≥ 1
   * and, when the electron count is known, its parity must match the electron
   * parity (even e⁻ → odd multiplicity, odd e⁻ → even multiplicity).
   * Returns {ok, errors[], parityChecked}.
   */
  function validateState(charge, multiplicity, asset) {
    var errors = [];
    var chargeNum = Number(charge);
    var multNum = Number(multiplicity);
    if (!isFinite(chargeNum) || Math.floor(chargeNum) !== chargeNum) {
      errors.push({ field: "charge", key: "next.config.error.charge_integer" });
    }
    if (!isFinite(multNum) || Math.floor(multNum) !== multNum || multNum < 1) {
      errors.push({ field: "multiplicity", key: "next.config.error.multiplicity_integer" });
    }
    var parityChecked = false;
    if (!errors.length && asset) {
      var electrons = electronCount(asset, chargeNum);
      if (electrons !== null && electrons >= 0) {
        parityChecked = true;
        var expectedParity = electrons % 2 === 0 ? 1 : 0; // odd mult for even e⁻
        if (multNum % 2 !== expectedParity) {
          errors.push({ field: "multiplicity", key: "next.config.error.multiplicity_parity", vars: { electrons: electrons } });
        }
      }
    }
    return { ok: errors.length === 0, errors: errors, parityChecked: parityChecked };
  }

  function validateResources(nproc, mem) {
    var errors = [];
    var cores = Number(nproc);
    if (!isFinite(cores) || Math.floor(cores) !== cores || cores < 1 || cores > 256) {
      errors.push({ field: "nproc", key: "next.config.error.nproc" });
    }
    if (!String(mem || "").trim()) errors.push({ field: "mem", key: "next.config.error.mem" });
    return { ok: errors.length === 0, errors: errors };
  }

  /** Full pre-flight client validation before anything touches the server. */
  function validateConfig(state) {
    var errors = [];
    var warnings = [];
    if (!state || !state.asset || !state.asset.xyz) {
      errors.push({ field: "structure", key: "next.config.error.structure_required" });
    }
    var entry = state.entry || null;
    if (!entry) errors.push({ field: "workflow", key: "next.config.error.workflow_required" });
    else if (!entry.supported) errors.push({ field: "workflow", key: "next.config.error.workflow_unsupported" });
    if (!state.schema) errors.push({ field: "workflow", key: "next.config.error.schema_missing" });
    var ids = levelIds(state.schema);
    if (state.schema && !ids.length) errors.push({ field: "workflow", key: "next.config.error.schema_empty" });
    for (var i = 0; i < ids.length; i++) {
      var level = (state.levels || {})[ids[i]];
      if (!isObject(level) || !level.engine) {
        errors.push({ field: "levels", key: "next.config.error.level_engine", vars: { level: ids[i] } });
      }
    }
    var stateCheck = validateState(state.charge, state.multiplicity, state.asset);
    errors = errors.concat(stateCheck.errors);
    var resourceCheck = validateResources(state.nproc, state.mem);
    errors = errors.concat(resourceCheck.errors);
    if (state.asset && state.asset.warnings && state.asset.warnings.length) {
      warnings = warnings.concat(state.asset.warnings.map(function (text) {
        return { key: "next.config.warning.structure", vars: { message: String(text) } };
      }));
    }
    return { ok: errors.length === 0, errors: errors, warnings: warnings, parityChecked: stateCheck.parityChecked };
  }

  /* ── request assembly ─────────────────────────────────────────────── */

  /** The `input` block of a structure submission (frozen server shape). */
  function buildInput(asset, charge, multiplicity, moleculeName) {
    var name = String(moleculeName || (asset && (asset.molecule_name || asset.name)) || "mol_1");
    return {
      source_type: "xyz_text",
      name: name,
      molecule_name: name,
      original_format: String((asset && asset.original_format) || "xyz"),
      xyz: String((asset && asset.xyz) || ""),
      has_3d: true,
      charge: Number(charge) || 0,
      multiplicity: Number(multiplicity) || 1,
      atom_count: Number((asset && asset.atom_count) || 0),
      formula: String((asset && asset.formula) || ""),
      smiles: String((asset && asset.smiles) || ""),
    };
  }

  /** Only the fields the server consumes; no `_disabled`/UI bookkeeping. */
  function buildLevels(levels, schema, methodCatalog) {
    var locked = applySemanticLocks(levels, methodCatalog).levels;
    var out = {};
    var ids = levelIds(schema);
    for (var i = 0; i < ids.length; i++) {
      var levelId = ids[i];
      var source = locked[levelId];
      if (!isObject(source)) continue;
      var clean = {};
      Object.keys(source).forEach(function (key) {
        var value = source[key];
        if (value === undefined || value === null || value === "") return;
        if (typeof value === "object") return;
        clean[key] = value;
      });
      if (!clean.engine) continue;
      out[levelId] = clean;
    }
    return out;
  }

  function buildMethod(schemaId, profileId, levels, schema, methodCatalog) {
    var method = { schema_id: String(schemaId || ""), levels: buildLevels(levels, schema, methodCatalog) };
    if (profileId) method.profile_id = String(profileId);
    return method;
  }

  /**
   * Canonical preflight/submit request. `project_id` is included only when set;
   * the digest ignores it either way, but the submit must carry the same body
   * shape as the preflight (the server compares the frozen four-field plan).
   */
  function buildRequest(state) {
    var req = {
      schema_version: SCHEMA_VERSION,
      workflow: String((state.entry && state.entry.id) || state.workflowId || ""),
      input: buildInput(state.asset, state.charge, state.multiplicity, state.moleculeName),
      method: buildMethod(
        state.entry && state.entry.schemaId,
        state.profileId,
        state.levels,
        state.schema,
        state.methodCatalog
      ),
      resources: { nproc: Number(state.nproc) || 1, mem: String(state.mem || "").trim() },
    };
    if (state.projectId) req.project_id = String(state.projectId);
    return req;
  }

  /** POST /jobs body: the same four fields plus the preflight fingerprint. */
  function buildSubmitBody(request, preflightFingerprint) {
    var body = clone(request);
    if (preflightFingerprint) body.preflight_fingerprint = String(preflightFingerprint);
    return body;
  }

  /** Client-side identity of the frozen plan (workflow/input/method/resources). */
  function planIdentity(request) {
    return stableStringify({
      workflow: request && request.workflow,
      input: request && request.input,
      method: request && request.method,
      resources: request && request.resources,
    });
  }

  /* ── compatibility with the frozen envelope owner ──────────────────── */

  /**
   * Hand the request to the wizard's frozen envelope builder when the classic
   * slice and its DOM globals are present (single payload owner), otherwise
   * shape the identical five-key body here. The builder is a pure function of
   * its spec; the DOM-touching call sites stay in the classic page.
   */
  function buildEnvelope(request, options) {
    options = options || {};
    if (typeof window !== "undefined" && window.ACPWizardSubmitEdit &&
        typeof window.ACPWizardSubmitEdit.buildStructureJobBody === "function") {
      return window.ACPWizardSubmitEdit.buildStructureJobBody({
        workflow: request.workflow,
        moleculeName: (request.input && request.input.molecule_name) || "",
        remark: options.remark || "",
        input: request.input,
        method: request.method,
        resources: request.resources,
        projectId: options.projectId || request.project_id || "",
        taskName: options.taskName || "",
      });
    }
    var body = {
      workflow: request.workflow,
      molecule_name: (request.input && request.input.molecule_name) || "",
      remark: options.remark || "",
      input: request.input,
      method: request.method,
      resources: request.resources,
    };
    var projectId = options.projectId || request.project_id;
    if (projectId) body.project_id = projectId;
    if (options.taskName) body.task_name = options.taskName;
    return body;
  }

  /* ── drafts ───────────────────────────────────────────────────────── */

  function draftSnapshot(state) {
    return {
      version: DRAFT_VERSION,
      workflow: (state.entry && state.entry.id) || "",
      schema_id: (state.entry && state.entry.schemaId) || "",
      profile_id: state.profileId || "",
      levels: clone(state.levels || {}),
      charge: Number(state.charge) || 0,
      multiplicity: Number(state.multiplicity) || 1,
      nproc: Number(state.nproc) || 1,
      mem: String(state.mem || ""),
      molecule_name: String(state.moleculeName || ""),
      structure: state.asset ? clone(state.asset) : null,
      structures: state.asset ? [clone(state.asset)] : [],
      saved_at: new Date().toISOString(),
    };
  }

  /** Validate + extract a draft snapshot; returns null when not ours. */
  function draftToState(snapshot) {
    if (!isObject(snapshot) || snapshot.version !== DRAFT_VERSION) return null;
    var structure = isObject(snapshot.structure) ? snapshot.structure : null;
    return {
      workflowId: String(snapshot.workflow || ""),
      profileId: String(snapshot.profile_id || ""),
      levels: isObject(snapshot.levels) ? clone(snapshot.levels) : {},
      charge: Number(snapshot.charge) || 0,
      multiplicity: Number(snapshot.multiplicity) || 1,
      nproc: Number(snapshot.nproc) || 1,
      mem: String(snapshot.mem || ""),
      moleculeName: String(snapshot.molecule_name || ""),
      asset: structure,
    };
  }

  function draftName(entry) {
    var label = entry ? (entry.labelZh || entry.label || entry.id) : "";
    return label ? ("Next · " + label) : "Next 草稿";
  }

  function draftPointerKey(projectId) {
    return DRAFT_POINTER_KEY + "." + (projectId || "global");
  }

  return {
    SCHEMA_VERSION: SCHEMA_VERSION,
    DRAFT_VERSION: DRAFT_VERSION,
    SIMPLE_WORKFLOWS: SIMPLE_WORKFLOWS.slice(),
    // catalog
    filterWorkflows: filterWorkflows,
    findWorkflow: findWorkflow,
    pickDefaultWorkflow: pickDefaultWorkflow,
    schemaFor: schemaFor,
    levelsOf: levelsOf,
    profilesOf: profilesOf,
    pickDefaultProfile: pickDefaultProfile,
    profileById: profileById,
    levelIds: levelIds,
    editableFields: editableFields,
    // method defaults + semantics
    fieldDefinition: fieldDefinition,
    fieldOptions: fieldOptions,
    fieldLabel: fieldLabel,
    fieldHelp: fieldHelp,
    functionalMeta: functionalMeta,
    buildDefaultLevels: buildDefaultLevels,
    applySemanticLocks: applySemanticLocks,
    // validation
    parseFormula: parseFormula,
    electronCount: electronCount,
    validateState: validateState,
    validateResources: validateResources,
    validateConfig: validateConfig,
    // request
    buildInput: buildInput,
    buildLevels: buildLevels,
    buildMethod: buildMethod,
    buildRequest: buildRequest,
    buildSubmitBody: buildSubmitBody,
    buildEnvelope: buildEnvelope,
    planIdentity: planIdentity,
    stableStringify: stableStringify,
    // drafts
    draftSnapshot: draftSnapshot,
    draftToState: draftToState,
    draftName: draftName,
    draftPointerKey: draftPointerKey,
  };
})();
