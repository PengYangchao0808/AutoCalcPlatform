/* file: js/modules/task-config/model.js */
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

;
/* file: js/modules/task-config/module.js */
/*
 * ACP Next — task-config module (UI-03).
 *
 * Real configuration panel for the Next entry: structure intake, workflow +
 * method profile (catalog driven), charge/multiplicity with a parity check,
 * server-side method validation, the two-step `/preflight` → `POST /jobs`
 * submit, durable create-task drafts and the submit receipt.
 *
 * Contract (UI-00 recipe):
 *   window.ACPNextModules["task-config"] = { descriptor, factory }
 *   factory(hostApi) -> { mount(el, api), updateContext, suspend, resume, dispose }
 *
 * Ownership rules:
 *   - no science is invented here: workflows, schemas, profiles, field options,
 *     defaults and semantic locks all come from GET /workflow-catalog and
 *     GET /method-catalog; the server's `/validate-method` is the authority for
 *     method errors and `/preflight` is the authority for feasibility + digest;
 *   - the POST /jobs envelope is not re-owned: `model.buildEnvelope()` delegates
 *     to the wizard's frozen `ACPWizardSubmitEdit.buildStructureJobBody` when
 *     that classic slice is loaded;
 *   - every async result is guarded by the module generation and a per-action
 *     token, so a slow response can never overwrite a newer edit;
 *   - data flows through the shell client (ACPClient.transport.request); this
 *     module starts no timer and no polling loop.
 */
(function () {
  "use strict";

  var MODULE_ID = "task-config";
  var API = "/api/v1";
  var VALIDATE_DEBOUNCE_MS = 400;
  var DRAFT_DEBOUNCE_MS = 1500;
  /* A-13/A-14/A-15: structure-source picker groups and the frozen
   * `structureRef` vocabulary owned by molecule-viewer (README §2). The panel
   * never invents a fifth kind and never writes the viewer's DOM. */
  var SOURCE_GROUPS = ["history", "local", "remote"];
  var SOURCE_LIMIT = 20;
  var SOURCE_CONTRACT_KINDS = ["job-file", "job-remote-file", "inline-xyz", "structure-entry"];

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.2.0",
    provides: ["task.config"],
    binds: ["projectId"],
    capabilities: { requires: [], provides: ["task.config"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 1,
    error: { titleKey: "next.config.errorTitle", messageKey: "next.config.errorMessage" },
  };

  /* zh fallbacks: used only when the shell dictionary has no entry yet. The
   * static chrome also carries data-i18n so a language switch retranslates it. */
  var ZH = {
    "next.config.title": "任务配置",
    "next.config.structure": "1 · 结构",
    "next.config.structure_placeholder": "粘贴 SMILES、XYZ 或 molfile 文本，或上传文件",
    "next.config.parse": "解析结构",
    "next.config.upload": "上传文件",
    "next.config.parsing": "正在解析结构…",
    "next.config.structure_hint": "尚未载入结构。支持 SMILES / XYZ / SDF / MOL / GJF / ORCA 输入。",
    "next.config.structure_summary": "{formula} · {atoms} 个原子 · 电荷 {charge} · 自旋 {multiplicity}",
    "next.config.structure_error": "结构解析失败：{message}",
    "next.config.workflow": "2 · 计算类型与方法",
    "next.config.workflow_label": "计算类型",
    "next.config.workflow_unsupported": "（需经典向导）",
    "next.config.profile_label": "方法预设",
    "next.config.profile_fields": "字段默认值",
    "next.config.advanced": "高级参数",
    "next.config.state": "3 · 体系与资源",
    "next.config.charge": "电荷",
    "next.config.multiplicity": "自旋多重度",
    "next.config.molecule_name": "分子名",
    "next.config.project": "项目",
    "next.config.nproc": "核数",
    "next.config.mem": "内存",
    "next.config.review": "4 · 预检与提交",
    "next.config.summary": "配置摘要",
    "next.config.summary_empty": "载入结构并选择计算类型后显示摘要。",
    "next.config.validate": "预检",
    "next.config.preflighting": "预检中…",
    "next.config.preflight_ok": "预检通过：{digest}",
    "next.config.preflight_failed": "预检未通过",
    "next.config.preflight_retry": "重试预检",
    "next.config.submit": "提交任务",
    "next.config.submitting": "提交中…",
    "next.config.submit_blocked": "预检通过后才能提交",
    "next.config.receipt": "已提交",
    "next.config.receipt_body": "任务 {jobId} · 状态 {status} · 第 {attempt} 次尝试",
    "next.config.receipt_digest": "计划摘要 {digest}",
    "next.config.open_task": "在任务面板打开",
    "next.config.open_classic": "在经典工作台打开",
    "next.config.open_classic_hint": "此计算类型需要额外输入（源任务、坐标或专用文件），请用经典向导完成配置。",
    "next.config.validate_running": "校验方法参数…",
    "next.config.validate_ok": "方法参数有效",
    "next.config.validate_invalid": "方法参数有误",
    "next.config.validate_error": "方法校验请求失败：{message}",
    "next.config.warnings": "警告",
    "next.config.errors": "需要修正",
    "next.config.draft_found": "发现保存的草稿（{when}）",
    "next.config.draft_restore": "恢复",
    "next.config.draft_discard": "丢弃",
    "next.config.draft_saving": "正在保存草稿…",
    "next.config.draft_saved": "草稿已保存",
    "next.config.draft_conflict": "草稿已在其他地方更新",
    "next.config.draft_reload": "载入最新",
    "next.config.draft_fork": "另存为新草稿",
    "next.config.draft_error": "草稿保存失败：{message}",
    "next.config.errorTitle": "任务配置不可用",
    "next.config.errorMessage": "task-config 模块加载失败，请查看控制台。",
    "next.config.load_error": "目录加载失败：{message}",
    "next.config.retry": "重试",
    "next.config.no_projects": "全部项目",
    "next.config.note.composite_ri": "复合方法使用内置 RI 近似，RI/辅助基组由方法决定。",
    "next.config.note.builtin_dispersion": "该方法已内置 {dispersion} 色散校正，色散项固定为 none。",
    "next.config.error.structure_required": "请先载入结构。",
    "next.config.error.workflow_required": "请选择计算类型。",
    "next.config.error.workflow_unsupported": "该计算类型需要经典向导的专用输入。",
    "next.config.error.schema_missing": "目录中缺少该方法 schema。",
    "next.config.error.schema_empty": "该方法 schema 没有可配置层级。",
    "next.config.error.level_engine": "层级 {level} 缺少 engine。",
    "next.config.error.charge_integer": "电荷必须是整数。",
    "next.config.error.multiplicity_integer": "自旋多重度必须是 ≥1 的整数。",
    "next.config.error.multiplicity_parity": "电子数为 {electrons}，自旋多重度奇偶不匹配。",
    "next.config.error.nproc": "核数必须是 1–256 的整数。",
    "next.config.error.mem": "请填写内存（例如 2000MB）。",
    "next.config.warning.structure": "结构提示：{message}",
    "next.config.stale": "配置已修改，请重新预检。",
    "next.config.submit_failed": "提交失败：{message}",
    "next.config.plan_stale": "预检之后配置发生变化，请重新预检。",
    "next.config.forbidden": "当前身份无权提交任务。",
    "next.config.unsupported_selected": "已选择需要经典向导的计算类型：{workflow}",
    /* A-13 structure source list + A-14 frames + A-15 bridge. Read-only:
     * picking a source only emits a structureRef; it never submits a task. */
    "next.config.sources_title": "从结构来源载入",
    "next.config.sources_hint": "本地任务产物、远程结构与历史已完成任务的结构；选择后只注入结构引用，不提交任务。",
    "next.config.sources_tab_history": "历史任务结构",
    "next.config.sources_tab_local": "任务产物",
    "next.config.sources_tab_remote": "远程文件",
    "next.config.sources_idle": "展开后加载结构来源。",
    "next.config.sources_loading": "正在加载结构来源…",
    "next.config.sources_empty_history": "没有可引用的历史结构（已完成任务的结构会自动出现在这里）。",
    "next.config.sources_empty_local": "该任务没有可用的结构条目。",
    "next.config.sources_empty_remote": "远程目录没有 XYZ 结构文件。",
    "next.config.sources_empty_no_task": "未绑定任务：在任务面板打开一个任务后可列出任务产物与远程文件。",
    "next.config.sources_error": "结构来源加载失败：{message}",
    "next.config.sources_retry": "重试",
    "next.config.sources_pending": "需取回",
    "next.config.sources_remote_chip": "远程",
    "next.config.sources_unavailable": "不可用",
    "next.config.sources_source_loading": "正在载入结构来源…",
    "next.config.sources_source_error": "结构来源不可用：{message}",
    "next.config.sources_row_error": "载入失败：{message}",
    "next.config.sources_no_text": "结构来源没有返回结构文本。",
    "next.config.sources_parser_missing": "多帧解析不可用：view_model.js 未加载。",
    "next.config.sources_parse_error": "结构解析失败（无有效 XYZ 帧）。",
    "next.config.sources_no_geometry": "该条目没有几何引用。",
    "next.config.sources_pending_catalog": "远程结构目录尚未取回（pending_fetch），请稍后重试。",
    "next.config.sources_remote_not_remote": "该任务不是远程任务，请使用“任务产物”。",
    "next.config.sources_remote_disabled": "服务器未启用远程执行。",
    "next.config.sources_source_frame": "源帧 {index}",
    "next.config.sources_atoms": "{count} 个原子",
    "next.config.frames_title": "帧",
    "next.config.frames_count": "共 {count} 帧",
    "next.config.frames_option": "帧 {index}",
    "next.config.frames_prev": "上一帧",
    "next.config.frames_next": "下一帧",
    "next.config.frame_identity": "帧 {index}/{count} · {kind} · {jobId} · {path}",
    "next.config.frame_identity_entry": "帧 {index}/{count} · {kind} · {jobId} · {entryId}",
    "next.config.sources_clamped": "请求帧 {requested} 越界，已钳制到末帧 {count}。",
    "next.config.sources_emitted": "已注入结构引用",
    "next.config.sources_not_emitted": "无可用的上下文/命令通道，结构引用未注入。",
  };

  function t(key, vars) {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    var value = key;
    if (shell && typeof shell.t === "function") {
      var translated = shell.t(key, vars);
      if (translated && translated !== key) return translated;
    }
    value = Object.prototype.hasOwnProperty.call(ZH, key) ? ZH[key] : key;
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        value = value.split("{" + name + "}").join(String(vars[name]));
      });
    }
    return value;
  }

  function el(doc, tag, cls, text) {
    var node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function i18nEl(doc, tag, cls, key, vars) {
    var node = el(doc, tag, cls, t(key, vars));
    node.setAttribute("data-i18n", key);
    return node;
  }

  function lang() {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    if (shell && typeof shell.getLang === "function") return shell.getLang() === "en-US" ? "en" : "zh";
    return "zh";
  }

  function nowIso() {
    try { return new Date().toISOString(); } catch (e) { return ""; }
  }

  function formatWhen(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return String(iso);
      return d.toLocaleString();
    } catch (e) {
      return String(iso);
    }
  }

  function describeError(error) {
    if (!error) return "unknown error";
    var body = error.body;
    if (body && typeof body === "object") {
      var detail = body.detail;
      if (detail && typeof detail === "object") {
        var code = detail.code ? String(detail.code) : "";
        var message = detail.message ? String(detail.message) : "";
        if (code || message) return (code ? code + ": " : "") + (message || JSON.stringify(detail));
      }
      if (typeof detail === "string") return detail;
    }
    return error.message ? String(error.message) : String(error);
  }

  function isForbidden(error) {
    return !!error && (error.status === 401 || error.status === 403);
  }

  function newIdempotencyKey() {
    try {
      if (typeof crypto !== "undefined" && crypto && typeof crypto.randomUUID === "function") {
        return "next-" + crypto.randomUUID();
      }
    } catch (e) { /* fall through */ }
    return "next-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function createTaskConfig(deps) {
    deps = deps || {};
    var model = deps.model || (typeof window !== "undefined" ? window.ACPNextTaskConfigModel : null);
    var requestFn = deps.request || (typeof window !== "undefined" && window.ACPClient && window.ACPClient.transport
      ? function (path, opts) { return window.ACPClient.transport.request(path, opts); }
      : null);
    var doc = deps.doc || (typeof document !== "undefined" ? document : null);
    var context = deps.context || null;
    var commands = deps.commands || null;

    var state = {
      generation: 0,
      mounted: false,
      suspended: false,
      disposed: false,
      host: null,
      snapshot: null,
      projectId: "",
      catalogLoading: false,
      catalogError: null,
      workflows: [],
      methodCatalog: null,
      entry: null,
      schema: null,
      profileId: "",
      levels: {},
      asset: null,
      structureText: "",
      parseStatus: "idle",
      parseError: null,
      charge: 0,
      multiplicity: 1,
      nproc: 4,
      mem: "2000MB",
      moleculeName: "",
      configRevision: 0,
      notes: [],
      validation: { status: "idle", errors: [], warnings: [], token: 0, revision: -1 },
      preflight: { status: "idle", fingerprint: null, planKey: null, reasons: [], token: 0, revision: -1 },
      submit: { status: "idle", receipt: null, error: null, idempotencyKey: null, keyPlan: null, token: 0 },
      draft: { id: null, revision: 0, status: "idle", savedAt: null, error: null, conflict: false, timer: null, banner: null, name: "" },
      /* A-13/A-14/A-15: per-group source lists + the one selected source's
       * frame table. `token` invalidates every in-flight load/selection on a
       * task switch, group switch, dispose or newer request (late results are
       * dropped, never painted). */
      sources: {
        ready: false,
        group: "history",
        taskKey: "",
        groups: {
          history: { name: "history", status: "idle", rows: [], error: null, loaded: false, reason: "", token: 0 },
          local: { name: "local", status: "idle", rows: [], error: null, loaded: false, reason: "", token: 0 },
          remote: { name: "remote", status: "idle", rows: [], error: null, loaded: false, reason: "", token: 0 },
        },
        selection: { status: "idle", row: null, text: "", frames: [], frameIndex: 0, frameCount: 0, token: 0, error: null, formula: "", atomCount: 0, clamped: null, emitted: false },
      },
      ui: {},
    };

    var listenerCleanup = [];

    /* ── DOM ─────────────────────────────────────────────────────────── */

    function buildDom() {
      var root = el(doc, "div", "acp-cfg");
      root.setAttribute("data-acp-module-state", "loading");
      root.setAttribute("data-module", MODULE_ID);

      var banner = el(doc, "div", "acp-cfg-banner");
      banner.hidden = true;
      banner.setAttribute("role", "status");
      root.appendChild(banner);
      state.ui.banner = banner;

      var body = el(doc, "div", "acp-cfg-body");
      root.appendChild(body);

      body.appendChild(buildStructureSection());
      body.appendChild(buildWorkflowSection());
      body.appendChild(buildStateSection());
      body.appendChild(buildReviewSection());

      var actions = el(doc, "div", "acp-cfg-actions");
      var preflightBtn = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.validate"));
      preflightBtn.type = "button";
      preflightBtn.setAttribute("data-action", "preflight");
      preflightBtn.setAttribute("data-i18n", "next.config.validate");
      var submitBtn = el(doc, "button", "acp-btn acp-btn--primary", t("next.config.submit"));
      submitBtn.type = "button";
      submitBtn.setAttribute("data-action", "submit");
      submitBtn.setAttribute("data-i18n", "next.config.submit");
      submitBtn.disabled = true;
      actions.appendChild(preflightBtn);
      actions.appendChild(submitBtn);
      root.appendChild(actions);
      state.ui.preflightBtn = preflightBtn;
      state.ui.submitBtn = submitBtn;

      var draftStatus = el(doc, "p", "acp-cfg-draft-status");
      draftStatus.setAttribute("data-role", "draft-status");
      draftStatus.setAttribute("aria-live", "polite");
      root.appendChild(draftStatus);
      state.ui.draftStatus = draftStatus;

      state.ui.classicLink = el(doc, "a", "acp-cfg-classic-link", t("next.config.open_classic"));
      state.ui.classicLink.setAttribute("href", "/#new-calculation");
      state.ui.classicLink.setAttribute("data-i18n", "next.config.open_classic");
      state.ui.classicLink.hidden = true;
      root.appendChild(state.ui.classicLink);

      state.host.replaceChildren(root);
      state.ui.root = root;

      on(preflightBtn, "click", function () { runPreflight(); });
      on(submitBtn, "click", function () { submit(); });
    }

    function buildStructureSection() {
      var section = el(doc, "section", "acp-cfg-section");
      section.setAttribute("data-section", "structure");
      section.appendChild(i18nEl(doc, "h3", "acp-cfg-heading", "next.config.structure"));
      section.appendChild(buildSourcePicker());

      var textarea = el(doc, "textarea", "acp-cfg-text");
      textarea.setAttribute("rows", "4");
      textarea.setAttribute("data-role", "structure-text");
      textarea.setAttribute("placeholder", t("next.config.structure_placeholder"));
      textarea.setAttribute("data-i18n-ph", "next.config.structure_placeholder");
      textarea.value = state.structureText;
      section.appendChild(textarea);
      state.ui.structureText = textarea;

      var row = el(doc, "div", "acp-cfg-row");
      var parseBtn = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.parse"));
      parseBtn.type = "button";
      parseBtn.setAttribute("data-action", "parse");
      parseBtn.setAttribute("data-i18n", "next.config.parse");
      var fileLabel = el(doc, "label", "acp-cfg-file");
      fileLabel.appendChild(el(doc, "span", "", t("next.config.upload")));
      var fileInput = el(doc, "input", "");
      fileInput.type = "file";
      fileInput.setAttribute("data-role", "structure-file");
      fileLabel.appendChild(fileInput);
      row.appendChild(parseBtn);
      row.appendChild(fileLabel);
      section.appendChild(row);
      state.ui.parseBtn = parseBtn;
      state.ui.fileInput = fileInput;

      var summary = el(doc, "p", "acp-cfg-note", t("next.config.structure_hint"));
      summary.setAttribute("data-i18n", "next.config.structure_hint");
      summary.setAttribute("data-role", "structure-summary");
      summary.setAttribute("aria-live", "polite");
      section.appendChild(summary);
      state.ui.structureSummary = summary;

      on(parseBtn, "click", function () { parseStructure(); });
      on(textarea, "input", function () {
        state.structureText = textarea.value;
        markDirty();
      });
      on(fileInput, "change", function () { parseFile(fileInput); });
      return section;
    }

    function buildWorkflowSection() {
      var section = el(doc, "section", "acp-cfg-section");
      section.setAttribute("data-section", "workflow");
      section.appendChild(i18nEl(doc, "h3", "acp-cfg-heading", "next.config.workflow"));

      section.appendChild(i18nEl(doc, "label", "acp-cfg-label", "next.config.workflow_label"));
      var workflowSelect = el(doc, "select", "acp-cfg-select");
      workflowSelect.setAttribute("data-role", "workflow");
      section.appendChild(workflowSelect);
      state.ui.workflowSelect = workflowSelect;
      on(workflowSelect, "change", function () {
        var entry = model.findWorkflow(state.workflows, workflowSelect.value);
        selectWorkflow(entry, { keepStructure: true });
      });

      var desc = el(doc, "p", "acp-cfg-note");
      desc.setAttribute("data-role", "workflow-desc");
      section.appendChild(desc);
      state.ui.workflowDesc = desc;

      section.appendChild(i18nEl(doc, "label", "acp-cfg-label", "next.config.profile_label"));
      var profileSelect = el(doc, "select", "acp-cfg-select");
      profileSelect.setAttribute("data-role", "profile");
      section.appendChild(profileSelect);
      state.ui.profileSelect = profileSelect;
      on(profileSelect, "change", function () { selectProfile(profileSelect.value); });

      var fields = el(doc, "div", "acp-cfg-fields");
      section.appendChild(fields);
      state.ui.fields = fields;
      state.ui.fieldInputs = {};

      var notes = el(doc, "ul", "acp-cfg-notes");
      notes.setAttribute("data-role", "semantic-notes");
      section.appendChild(notes);
      state.ui.semanticNotes = notes;
      return section;
    }

    function buildStateSection() {
      var section = el(doc, "section", "acp-cfg-section");
      section.setAttribute("data-section", "state");
      section.appendChild(i18nEl(doc, "h3", "acp-cfg-heading", "next.config.state"));

      var grid = el(doc, "div", "acp-cfg-grid");

      grid.appendChild(numberField("next.config.charge", "charge", "1"));
      grid.appendChild(numberField("next.config.multiplicity", "multiplicity", "1"));
      grid.appendChild(numberField("next.config.nproc", "nproc", "1"));
      grid.appendChild(textField("next.config.mem", "mem"));
      var nameField = textField("next.config.molecule_name", "moleculeName");
      nameField.className = "acp-cfg-field acp-cfg-field--wide";
      grid.appendChild(nameField);
      section.appendChild(grid);

      var hint = el(doc, "p", "acp-cfg-note");
      hint.setAttribute("data-role", "parity-hint");
      section.appendChild(hint);
      state.ui.parityHint = hint;
      return section;
    }

    function numberField(key, role, step) {
      var wrap = el(doc, "label", "acp-cfg-field");
      wrap.appendChild(i18nEl(doc, "span", "acp-cfg-label", key));
      var input = el(doc, "input", "acp-cfg-input");
      input.type = "number";
      input.setAttribute("step", step);
      input.setAttribute("data-role", role);
      wrap.appendChild(input);
      state.ui[role] = input;
      on(input, "input", function () {
        var value = Number(input.value);
        if (role === "charge") state.charge = isFinite(value) ? value : 0;
        else if (role === "multiplicity") state.multiplicity = isFinite(value) ? value : 1;
        else if (role === "nproc") state.nproc = isFinite(value) ? value : 1;
        markDirty();
      });
      return wrap;
    }

    function textField(key, role) {
      var wrap = el(doc, "label", "acp-cfg-field");
      wrap.appendChild(i18nEl(doc, "span", "acp-cfg-label", key));
      var input = el(doc, "input", "acp-cfg-input");
      input.type = "text";
      input.setAttribute("data-role", role);
      wrap.appendChild(input);
      state.ui[role] = input;
      on(input, "input", function () {
        if (role === "mem") state.mem = input.value;
        else if (role === "moleculeName") state.moleculeName = input.value;
        markDirty();
      });
      return wrap;
    }

    function buildReviewSection() {
      var section = el(doc, "section", "acp-cfg-section");
      section.setAttribute("data-section", "review");
      section.appendChild(i18nEl(doc, "h3", "acp-cfg-heading", "next.config.review"));

      var summary = el(doc, "dl", "acp-cfg-summary");
      summary.setAttribute("data-role", "summary");
      section.appendChild(summary);
      state.ui.summary = summary;

      var status = el(doc, "p", "acp-cfg-status");
      status.setAttribute("data-role", "validation-status");
      status.setAttribute("aria-live", "polite");
      section.appendChild(status);
      state.ui.validationStatus = status;

      var errors = el(doc, "ul", "acp-cfg-errors");
      errors.setAttribute("data-role", "errors");
      section.appendChild(errors);
      state.ui.errors = errors;

      var preflightStatus = el(doc, "p", "acp-cfg-status");
      preflightStatus.setAttribute("data-role", "preflight-status");
      preflightStatus.setAttribute("aria-live", "polite");
      section.appendChild(preflightStatus);
      state.ui.preflightStatus = preflightStatus;

      var reasons = el(doc, "ul", "acp-cfg-errors");
      reasons.setAttribute("data-role", "preflight-reasons");
      section.appendChild(reasons);
      state.ui.preflightReasons = reasons;

      var receipt = el(doc, "div", "acp-cfg-receipt");
      receipt.hidden = true;
      receipt.setAttribute("role", "status");
      receipt.setAttribute("data-role", "receipt");
      section.appendChild(receipt);
      state.ui.receipt = receipt;
      return section;
    }

    /* ── helpers ─────────────────────────────────────────────────────── */

    function on(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return;
      node.addEventListener(type, handler);
      listenerCleanup.push(function () {
        if (typeof node.removeEventListener === "function") node.removeEventListener(type, handler);
      });
    }

    function guard(gen) {
      return !state.disposed && state.mounted && state.generation === gen;
    }

    function bumpRevision() {
      state.configRevision += 1;
      state.validation = { status: "idle", errors: [], warnings: [], token: state.validation.token + 1, revision: -1 };
      state.preflight = { status: "idle", fingerprint: null, planKey: null, reasons: [], token: state.preflight.token + 1, revision: -1 };
    }

    function markDirty() {
      bumpRevision();
      renderReview();
      renderActions();
      scheduleDraftSave();
    }

    function setModuleState(name) {
      if (state.ui.root) state.ui.root.setAttribute("data-acp-module-state", name);
    }

    function empty(node) {
      if (node) node.replaceChildren();
    }

    function listItem(node, cls, text) {
      var li = el(doc, "li", cls, text);
      node.appendChild(li);
      return li;
    }

    /* ── catalog + defaults ──────────────────────────────────────────── */

    function loadCatalogs() {
      var gen = state.generation;
      state.catalogLoading = true;
      state.catalogError = null;
      setModuleState("loading");
      renderBanner();
      return Promise.all([
        requestFn(API + "/workflow-catalog", { method: "GET" }),
        requestFn(API + "/method-catalog", { method: "GET" }),
      ]).then(function (results) {
        if (!guard(gen)) return;
        state.workflows = model.filterWorkflows(results[0]);
        state.methodCatalog = results[1] || {};
        state.catalogLoading = false;
        if (!state.workflows.length) throw new Error("workflow catalog is empty");
        var current = model.findWorkflow(state.workflows, state.entry && state.entry.id);
        var entry = current || model.pickDefaultWorkflow(state.workflows);
        selectWorkflow(entry, { keepStructure: true, silent: true });
        setModuleState("ready");
        renderAll();
        return loadDraftPointer();
      }).catch(function (error) {
        if (!guard(gen)) return;
        state.catalogLoading = false;
        state.catalogError = error;
        setModuleState("error");
        renderBanner();
        renderCatalogError(error);
      });
    }

    function renderCatalogError(error) {
      if (!state.ui.errors) return;
      empty(state.ui.errors);
      listItem(state.ui.errors, "acp-cfg-error", t("next.config.load_error", { message: describeError(error) }));
      var retry = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.retry"));
      retry.type = "button";
      retry.setAttribute("data-i18n", "next.config.retry");
      retry.setAttribute("data-action", "retry-catalog");
      on(retry, "click", function () { resetAndReload(); });
      state.ui.errors.appendChild(retry);
    }

    function resetAndReload() {
      if (!state.host) return;
      buildDom();
      loadCatalogs();
    }

    function selectWorkflow(entry, options) {
      options = options || {};
      state.entry = entry || null;
      state.schema = entry ? model.schemaFor(state.methodCatalog, entry.schemaId) : null;
      state.notes = [];
      if (!entry) {
        state.levels = {};
        state.profileId = "";
      } else if (state.schema) {
        var profile = model.pickDefaultProfile(state.schema);
        state.profileId = profile ? String(profile.profile_id || "") : "";
        state.levels = model.buildDefaultLevels(state.schema, state.methodCatalog, entry, state.profileId);
        var locked = model.applySemanticLocks(state.levels, state.methodCatalog);
        state.levels = locked.levels;
        state.notes = locked.notes;
      } else {
        state.levels = {};
        state.profileId = "";
      }
      bumpRevision();
      if (!options.silent) {
        renderAll();
        scheduleDraftSave();
      }
    }

    function selectProfile(profileId) {
      if (!state.schema) return;
      state.profileId = String(profileId || "");
      state.levels = model.buildDefaultLevels(state.schema, state.methodCatalog, state.entry, state.profileId);
      var locked = model.applySemanticLocks(state.levels, state.methodCatalog);
      state.levels = locked.levels;
      state.notes = locked.notes;
      bumpRevision();
      renderMethod();
      renderReview();
      renderActions();
      scheduleDraftSave();
      scheduleValidation();
    }

    /* ── structure intake ────────────────────────────────────────────── */

    function parseStructure() {
      var content = String((state.ui.structureText && state.ui.structureText.value) || "").trim();
      if (!content) return;
      state.structureText = content;
      state.parseStatus = "loading";
      state.parseError = null;
      renderStructure();
      var gen = state.generation;
      requestFn(API + "/structures/parse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: content, format: "auto", filename: "" }),
        timeoutMs: 0,
      }).then(function (body) {
        if (!guard(gen)) return;
        if (!body || body.ok === false || !Array.isArray(body.structures) || !body.structures.length) {
          var detail = body && body.errors && body.errors.length ? body.errors[0] : "no structure parsed";
          throw new Error(detail);
        }
        applyAsset(body.structures[0]);
      }).catch(function (error) {
        if (!guard(gen)) return;
        state.parseStatus = "error";
        state.parseError = error;
        renderStructure();
        renderReview();
      });
    }

    function parseFile(input) {
      var file = input && input.files && input.files[0];
      if (!file) return;
      var gen = state.generation;
      var read = function (text) {
        if (!guard(gen)) return;
        state.ui.structureText.value = text;
        state.structureText = text;
        parseStructure();
      };
      try {
        if (typeof file.text === "function") {
          file.text().then(read).catch(function () { parseStructure(); });
        } else if (typeof FileReader !== "undefined") {
          var reader = new FileReader();
          reader.onload = function () { read(String(reader.result || "")); };
          reader.readAsText(file);
        }
      } catch (e) {
        state.parseStatus = "error";
        state.parseError = e;
        renderStructure();
      }
    }

    function applyAsset(asset) {
      state.asset = asset || null;
      state.parseStatus = "ok";
      state.parseError = null;
      if (asset) {
        if (asset.formula) state.moleculeName = state.moleculeName || String(asset.molecule_name || asset.name || "");
        if (asset.charge != null) state.charge = Number(asset.charge) || 0;
        if (asset.multiplicity != null) state.multiplicity = Number(asset.multiplicity) || 1;
      }
      bumpRevision();
      renderStructure();
      renderStateInputs();
      renderReview();
      renderActions();
      scheduleDraftSave();
      scheduleValidation();
      publishStructure();
    }

    /* ── structure sources (A-13) + frames (A-14) + ref bridge (A-15) ────
     *
     * Three read-only origins, each backed by an existing endpoint:
     *   history → GET /api/v1/structure-sources/recent
     *             GET /api/v1/structure-sources/{source_id}           (text)
     *   local   → GET /api/v1/jobs/{jobId}/structure-viewer          (catalog)
     *             GET …/structure-viewer/entries/{entryId}/geometry  (text)
     *   remote  → GET /api/v1/jobs/{jobId}/remote-files               (listing)
     *             GET …/remote-files/{path}/preview?mode=structure   (text)
     *
     * The frame table is always derived from the ONE text response of the
     * chosen source: selecting a frame never issues a second geometry request
     * and never builds a second cache. Picking a source/frame only emits a
     * canonical structureRef through the shell context/commands seam — this
     * module never writes viewer DOM, never creates a viewer and never submits
     * a task from this surface.
     */

    function taskJobId() {
      var ref = state.snapshot && state.snapshot.taskRef;
      if (ref && typeof ref.jobId === "string" && ref.jobId) return ref.jobId;
      return "";
    }

    function encodePath(path) {
      return String(path || "").split("/").map(encodeURIComponent).join("/");
    }

    function formatBytes(value) {
      var n = Number(value);
      if (!isFinite(n) || n < 0) return "";
      if (n < 1024) return n + " B";
      if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
      return (n / (1024 * 1024)).toFixed(1) + " MB";
    }

    function formatEpoch(value) {
      var n = Number(value);
      if (!isFinite(n) || n <= 0) return "";
      try {
        return new Date(n > 1e12 ? n : n * 1000).toLocaleString();
      } catch (e) {
        return "";
      }
    }

    function errorDetail(error) {
      var body = error && error.body;
      if (body && typeof body === "object" && typeof body.detail === "string") return body.detail;
      return "";
    }

    /** Shared XYZ frame parser (view_model.js). No local second parser. */
    function frameParser() {
      var api = typeof window !== "undefined" && window.ACPView ? window.ACPView.model : null;
      if (api && typeof api.parseXyzFrames === "function") return api;
      return null;
    }

    /** Accept api-style pre-parsed bodies and raw text (transport returns
     *  text for the non-JSON geometry endpoints). */
    function sourceText(body) {
      if (typeof body === "string") return body;
      if (!body || typeof body !== "object") return "";
      if (typeof body.content === "string") return body.content;
      if (typeof body.xyz === "string") return body.xyz;
      if (typeof body.text === "string") return body.text;
      return "";
    }

    /** Validate + normalize to the frozen molecule-viewer structureRef shapes. */
    function canonicalRef(ref) {
      if (!ref || typeof ref !== "object") return null;
      var kind = String(ref.kind || "");
      if (SOURCE_CONTRACT_KINDS.indexOf(kind) < 0) return null;
      var frameIndex = typeof ref.frameIndex === "number" && isFinite(ref.frameIndex) && ref.frameIndex >= 0
        ? Math.floor(ref.frameIndex)
        : 0;
      if (kind === "inline-xyz") {
        if (typeof ref.text !== "string" || !ref.text.trim()) return null;
        return { kind: kind, text: ref.text, frameIndex: frameIndex };
      }
      if (typeof ref.jobId !== "string" || !ref.jobId) return null;
      if (kind === "structure-entry") {
        if (typeof ref.entryId !== "string" || !ref.entryId) return null;
        return { kind: kind, jobId: ref.jobId, entryId: ref.entryId, frameIndex: frameIndex };
      }
      if (typeof ref.path !== "string" || !ref.path) return null;
      return { kind: kind, jobId: ref.jobId, path: ref.path, frameIndex: frameIndex };
    }

    /**
     * A-15 bridge: publish one canonical structureRef. Shell command first
     * (it writes the workspace context AND delivers to the mounted viewer),
     * plain context focus as the fallback host seam. Returns whether the ref
     * left the module; never touches viewer DOM.
     */
    function emitStructureRef(ref) {
      var canonical = canonicalRef(ref);
      if (!canonical) return false;
      var payload = { source: MODULE_ID };
      Object.keys(canonical).forEach(function (key) { payload[key] = canonical[key]; });
      if (commands && typeof commands.openStructure === "function") {
        try {
          commands.openStructure(payload);
          return true;
        } catch (e) { /* fall through to the plain context seam */ }
      }
      if (context && typeof context.setFocus === "function") {
        try {
          context.setFocus({ structureRef: canonical }, MODULE_ID);
          return true;
        } catch (e) {
          return false;
        }
      }
      return false;
    }

    /** Hand the parsed geometry to the shared viewer (UI-05 assembly). */
    function publishStructure() {
      var xyz = state.asset && state.asset.xyz ? String(state.asset.xyz) : "";
      if (!xyz) return;
      emitStructureRef({ kind: "inline-xyz", text: xyz });
    }

    function rowKind(row) {
      if (!row) return "";
      if (row.group === "local") return "structure-entry";
      if (row.group === "remote") return "job-remote-file";
      return row.remote ? "job-remote-file" : "job-file";
    }

    function baseRowState(row) {
      if (!row) return "error";
      if (row.group === "history") {
        if (row.needsFetch) return "pending";
        return row.remote ? "remote" : "ready";
      }
      return row.state === "error" && row.disabled ? "error" : "ready";
    }

    function rowRef(row, frameIndex) {
      if (!row) return null;
      if (row.group === "local") {
        return canonicalRef({ kind: "structure-entry", jobId: row.jobId, entryId: row.entryId, frameIndex: frameIndex });
      }
      if (row.group === "remote") {
        return canonicalRef({ kind: "job-remote-file", jobId: row.jobId, path: row.path, frameIndex: frameIndex });
      }
      return canonicalRef({
        kind: row.remote ? "job-remote-file" : "job-file",
        jobId: row.jobId,
        path: row.path,
        frameIndex: frameIndex,
      });
    }

    function sourceRefLabel(row, frameIndex) {
      if (!row) return "";
      if (row.group === "local") return "structure-entry:" + row.jobId + ":entry:" + row.entryId + "#" + frameIndex;
      return rowKind(row) + ":" + row.jobId + ":" + row.path + "#" + frameIndex;
    }

    /* ── source picker DOM ───────────────────────────────────────────── */

    function buildSourcePicker() {
      var details = el(doc, "details", "acp-cfg-sources");
      details.setAttribute("data-role", "source-picker");
      state.ui.sourcePicker = details;

      var summary = el(doc, "summary", "acp-cfg-sources-summary", t("next.config.sources_title"));
      summary.setAttribute("data-role", "source-summary");
      summary.setAttribute("data-i18n", "next.config.sources_title");
      details.appendChild(summary);
      state.ui.sourceSummary = summary;
      on(summary, "click", function () {
        state.sources.ready = true;
        ensureSourceGroup(state.sources.group);
      });

      var body = el(doc, "div", "acp-cfg-sources-body");
      body.setAttribute("data-role", "source-panel");
      body.setAttribute("data-acp-source-state", "idle");
      details.appendChild(body);
      state.ui.sourcePanel = body;

      var tabs = el(doc, "div", "acp-cfg-source-tabs");
      tabs.setAttribute("role", "tablist");
      SOURCE_GROUPS.forEach(function (name) {
        var button = el(doc, "button", "acp-cfg-source-tab" + (name === state.sources.group ? " is-active" : ""), t("next.config.sources_tab_" + name));
        button.type = "button";
        button.setAttribute("role", "tab");
        button.setAttribute("data-source-tab", name);
        button.setAttribute("data-i18n", "next.config.sources_tab_" + name);
        button.setAttribute("aria-selected", name === state.sources.group ? "true" : "false");
        on(button, "click", function () { selectSourceGroup(name); });
        tabs.appendChild(button);
      });
      body.appendChild(tabs);
      state.ui.sourceTabs = tabs;

      var hint = el(doc, "p", "acp-cfg-note", t("next.config.sources_hint"));
      hint.setAttribute("data-i18n", "next.config.sources_hint");
      body.appendChild(hint);

      var status = el(doc, "p", "acp-cfg-status");
      status.setAttribute("data-role", "source-status");
      status.setAttribute("aria-live", "polite");
      body.appendChild(status);
      state.ui.sourceStatus = status;

      var list = el(doc, "ul", "acp-cfg-source-list");
      list.setAttribute("data-role", "source-list");
      body.appendChild(list);
      state.ui.sourceList = list;

      var frames = el(doc, "div", "acp-cfg-frames");
      frames.setAttribute("data-role", "source-frames");
      frames.hidden = true;
      body.appendChild(frames);
      state.ui.sourceFrames = frames;

      var preview = el(doc, "p", "acp-cfg-source-preview");
      preview.setAttribute("data-role", "source-preview");
      preview.setAttribute("aria-live", "polite");
      frames.appendChild(preview);
      state.ui.sourcePreview = preview;

      var frameRow = el(doc, "div", "acp-cfg-row");
      var frameField = el(doc, "label", "acp-cfg-field");
      frameField.appendChild(i18nEl(doc, "span", "acp-cfg-label", "next.config.frames_title"));
      var select = el(doc, "select", "acp-cfg-select");
      select.setAttribute("data-role", "frame-select");
      frameField.appendChild(select);
      on(select, "change", function () { selectFrame(Number(select.value)); });
      frameRow.appendChild(frameField);
      state.ui.frameSelect = select;

      var count = el(doc, "span", "acp-cfg-frame-count");
      count.setAttribute("data-role", "frame-count");
      frameRow.appendChild(count);
      state.ui.frameCount = count;

      var prev = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.frames_prev"));
      prev.type = "button";
      prev.setAttribute("data-action", "frame-prev");
      prev.setAttribute("data-i18n", "next.config.frames_prev");
      on(prev, "click", function () {
        var current = state.sources.selection ? state.sources.selection.frameIndex : 0;
        selectFrame((Number(current) || 0) - 1);
      });
      frameRow.appendChild(prev);
      state.ui.framePrev = prev;

      var next = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.frames_next"));
      next.type = "button";
      next.setAttribute("data-action", "frame-next");
      next.setAttribute("data-i18n", "next.config.frames_next");
      on(next, "click", function () {
        var current = state.sources.selection ? state.sources.selection.frameIndex : 0;
        selectFrame((Number(current) || 0) + 1);
      });
      frameRow.appendChild(next);
      state.ui.frameNext = next;

      frames.appendChild(frameRow);
      return details;
    }

    /* ── source loading (server-backed, no fixture rows) ─────────────── */

    function sourceGuard(group, token, gen) {
      return !state.disposed && state.mounted && state.generation === gen &&
        state.sources.groups[group.name] === group && group.token === token;
    }

    function selectionGuard(token, gen) {
      return !state.disposed && state.mounted && state.generation === gen &&
        state.sources.selection.token === token;
    }

    function emptySelection() {
      return {
        status: "idle", row: null, text: "", frames: [], frameIndex: 0, frameCount: 0,
        token: state.sources.selection.token + 1, error: null, formula: "", atomCount: 0,
        clamped: null, emitted: false,
      };
    }

    /** Reset a group in place: bump its token so an in-flight result is dropped. */
    function resetSourceGroup(group) {
      group.token += 1;
      group.status = "idle";
      group.rows = [];
      group.error = null;
      group.loaded = false;
      group.reason = "";
    }

    function resetHistorySource() {
      resetSourceGroup(state.sources.groups.history);
      if (!state.sources.ready) return;
      if (state.sources.group === "history") ensureSourceGroup("history");
      else renderSourcePanel();
    }

    function resetTaskBoundSources() {
      resetSourceGroup(state.sources.groups.local);
      resetSourceGroup(state.sources.groups.remote);
      var selection = state.sources.selection;
      if (selection && selection.row && (selection.row.group === "local" || selection.row.group === "remote")) {
        state.sources.selection = emptySelection();
      }
      if (!state.sources.ready) return;
      renderSourcePanel();
      if (state.sources.group === "local" || state.sources.group === "remote") {
        ensureSourceGroup(state.sources.group);
      }
    }

    function selectSourceGroup(name) {
      if (SOURCE_GROUPS.indexOf(name) < 0) return;
      state.sources.group = name;
      renderSourcePanel();
      ensureSourceGroup(name);
    }

    function ensureSourceGroup(name, force) {
      var group = state.sources.groups[name];
      if (!group) return;
      state.sources.group = name;
      if (!force && group.loaded) {
        renderSourcePanel();
        return;
      }
      if (group.status === "loading") {
        renderSourcePanel();
        return;
      }
      group.status = "loading";
      group.rows = [];
      group.error = null;
      group.reason = "";
      group.loaded = false;
      group.token += 1;
      var token = group.token;
      var gen = state.generation;
      renderSourcePanel();
      if (!requestFn) {
        group.status = "error";
        group.loaded = true;
        group.error = new Error("task-config: ACPClient.transport missing");
        renderSourcePanel();
        return;
      }
      var jobId = taskJobId();
      if (name !== "history" && !jobId) {
        group.status = "empty";
        group.loaded = true;
        group.reason = "next.config.sources_empty_no_task";
        renderSourcePanel();
        return;
      }
      var pending;
      if (name === "history") pending = requestHistorySources();
      else if (name === "local") pending = requestLocalSources(jobId);
      else pending = requestRemoteSources(jobId);
      pending.then(function (result) {
        if (!sourceGuard(group, token, gen)) return;
        group.rows = (result && result.rows) || [];
        group.reason = (result && result.reason) || "";
        group.status = result && result.status
          ? result.status
          : (group.rows.length ? "ready" : "empty");
        group.error = null;
        group.loaded = true;
        renderSourcePanel();
      }).catch(function (error) {
        if (!sourceGuard(group, token, gen)) return;
        group.status = "error";
        group.error = error;
        group.reason = "";
        group.rows = [];
        group.loaded = true;
        renderSourcePanel();
      });
    }

    function requestHistorySources() {
      var query = "limit=" + SOURCE_LIMIT;
      if (state.projectId) query += "&project_id=" + encodeURIComponent(state.projectId);
      else query += "&all_projects=true";
      return requestFn(API + "/structure-sources/recent?" + query, { method: "GET" }).then(function (body) {
        var raw = body && Array.isArray(body.sources) ? body.sources : [];
        var rows = [];
        raw.forEach(function (item) {
          var row = historyRow(item);
          if (row) rows.push(row);
        });
        return { rows: rows, status: rows.length ? "ready" : "empty" };
      });
    }

    function historyRow(raw) {
      if (!raw || typeof raw !== "object" || !raw.source_id || !raw.job_id) return null;
      var path = String(raw.path || "");
      var remote = !!raw.remote;
      var needsFetch = !!raw.needs_fetch;
      var label = String(
        raw.custom_name || raw.resolved_name || raw.label || raw.molecule_name ||
        raw.job_name || raw.path || raw.source_id
      );
      var meta = [];
      if (raw.workflow) meta.push(String(raw.workflow));
      if (raw.formula) meta.push(String(raw.formula));
      if (raw.job_status) meta.push(String(raw.job_status));
      if (raw.completed_at) meta.push(String(raw.completed_at).slice(0, 10));
      if (!path) meta.push(String(raw.source_id));
      return {
        id: "history:" + String(raw.source_id),
        group: "history",
        sourceId: String(raw.source_id),
        jobId: String(raw.job_id),
        path: path,
        remote: remote,
        needsFetch: needsFetch,
        state: needsFetch ? "pending" : (remote ? "remote" : "ready"),
        label: label,
        meta: meta,
        chip: needsFetch ? t("next.config.sources_pending") : (remote ? t("next.config.sources_remote_chip") : ""),
        error: null,
        formula: String(raw.formula || ""),
        atomCount: Number(raw.atom_count || 0),
      };
    }

    function requestLocalSources(jobId) {
      return requestFn(API + "/jobs/" + encodeURIComponent(jobId) + "/structure-viewer", { method: "GET" }).then(function (body) {
        if (!body || typeof body !== "object") return { rows: [], status: "empty" };
        var warnings = Array.isArray(body.warnings) ? body.warnings.map(String) : [];
        if (body.availability === "pending_fetch" || warnings.indexOf("pending_fetch") !== -1) {
          return { rows: [], status: "empty", reason: "next.config.sources_pending_catalog" };
        }
        var groupLabels = {};
        (Array.isArray(body.groups) ? body.groups : []).forEach(function (group) {
          if (group && group.id) groupLabels[String(group.id)] = String(group.label || group.id);
        });
        var entries = Array.isArray(body.entries) ? body.entries : [];
        var rows = [];
        entries.forEach(function (entry) {
          var row = localRow(entry, groupLabels, jobId);
          if (row) rows.push(row);
        });
        return { rows: rows, status: rows.length ? "ready" : "empty" };
      });
    }

    function localRow(entry, groupLabels, jobId) {
      if (!entry || typeof entry !== "object" || !entry.id) return null;
      var geometry = entry.geometry && typeof entry.geometry === "object" ? entry.geometry : {};
      var hasGeometry = typeof geometry.endpoint === "string" && geometry.endpoint.length > 0;
      var source = entry.source && typeof entry.source === "object" ? entry.source : {};
      var meta = [];
      if (entry.group_id && groupLabels[String(entry.group_id)]) meta.push(groupLabels[String(entry.group_id)]);
      if (entry.role) meta.push(String(entry.role));
      if (entry.status) meta.push(String(entry.status));
      if (typeof source.frame_index === "number" && source.frame_index >= 0) {
        meta.push(t("next.config.sources_source_frame", { index: Math.floor(source.frame_index) + 1 }));
      }
      if (entry.energy && typeof entry.energy.value === "number" && isFinite(entry.energy.value)) {
        meta.push(entry.energy.value.toFixed(6) + " " + String(entry.energy.unit || "hartree"));
      }
      return {
        id: "local:" + String(entry.id),
        group: "local",
        jobId: jobId,
        entryId: String(entry.id),
        label: String(entry.label || entry.id),
        meta: meta,
        state: hasGeometry ? "ready" : "error",
        disabled: !hasGeometry,
        chip: hasGeometry ? "" : t("next.config.sources_unavailable"),
        error: hasGeometry ? null : t("next.config.sources_no_geometry"),
        remote: false,
        needsFetch: false,
        formula: "",
        atomCount: 0,
      };
    }

    function requestRemoteSources(jobId) {
      return requestFn(API + "/jobs/" + encodeURIComponent(jobId) + "/remote-files", { method: "GET" })
        .then(function (body) {
          var files = body && Array.isArray(body.files) ? body.files : [];
          var rows = [];
          files.forEach(function (item) {
            var row = remoteRow(item, jobId);
            if (row) rows.push(row);
          });
          return { rows: rows, status: rows.length ? "ready" : "empty" };
        })
        .catch(function (error) {
          var status = error && error.status;
          if (status === 400) {
            return { rows: [], status: "empty", reason: "next.config.sources_remote_not_remote" };
          }
          if (status === 503) {
            return { rows: [], status: "empty", reason: "next.config.sources_remote_disabled" };
          }
          throw error;
        });
    }

    function remoteRow(item, jobId) {
      if (!item || typeof item !== "object" || !item.name || item.is_dir) return null;
      var name = String(item.name);
      if (name.toLowerCase().slice(-4) !== ".xyz") return null;
      var meta = [];
      var size = formatBytes(item.size);
      if (size) meta.push(size);
      var modified = formatEpoch(item.mtime);
      if (modified) meta.push(modified);
      return {
        id: "remote:" + name,
        group: "remote",
        jobId: jobId,
        path: name,
        label: name,
        meta: meta,
        state: "ready",
        chip: "",
        error: null,
        remote: true,
        needsFetch: false,
        formula: "",
        atomCount: 0,
      };
    }

    /* ── selecting one source + its frames ───────────────────────────── */

    function loadSourceText(row) {
      if (row.group === "history") {
        return requestFn(API + "/structure-sources/" + encodeURIComponent(row.sourceId), { method: "GET" })
          .then(function (body) {
            var structure = body && body.structure ? body.structure : null;
            if (!structure || typeof structure.xyz !== "string") {
              throw new Error(t("next.config.sources_no_text"));
            }
            return {
              text: structure.xyz,
              formula: String(structure.formula || row.formula || ""),
              atomCount: Number(structure.atom_count || 0),
            };
          });
      }
      if (row.group === "local") {
        return requestFn(
          API + "/jobs/" + encodeURIComponent(row.jobId) + "/structure-viewer/entries/" +
            encodeURIComponent(row.entryId) + "/geometry",
          { method: "GET" }
        ).then(function (body) {
          return { text: sourceText(body), formula: "", atomCount: 0 };
        });
      }
      return requestFn(
        API + "/jobs/" + encodeURIComponent(row.jobId) + "/remote-files/" +
          encodePath(row.path) + "/preview?mode=structure",
        { method: "GET" }
      ).then(function (body) {
        return { text: sourceText(body), formula: "", atomCount: 0 };
      });
    }

    function selectSource(row) {
      if (!row || state.disposed) return;
      if (row.disabled) return;
      var gen = state.generation;
      var previous = state.sources.selection;
      var token = (previous ? previous.token : 0) + 1;
      state.sources.selection = {
        status: "loading", row: row, text: "", frames: [], frameIndex: 0, frameCount: 0,
        token: token, error: null, formula: row.formula || "", atomCount: 0, clamped: null, emitted: false,
      };
      row.state = baseRowState(row);
      row.error = null;
      renderSourcePanel();
      loadSourceText(row).then(function (result) {
        if (!selectionGuard(token, gen)) return;
        var text = String((result && result.text) || "");
        if (!text.trim()) throw new Error(t("next.config.sources_no_text"));
        var parser = frameParser();
        if (!parser) throw new Error(t("next.config.sources_parser_missing"));
        var frames = parser.parseXyzFrames(text);
        if (!frames.length) throw new Error(t("next.config.sources_parse_error"));
        state.sources.selection = {
          status: "ready",
          row: row,
          text: text,
          frames: frames,
          frameIndex: 0,
          frameCount: frames.length,
          token: token,
          error: null,
          formula: String((result && result.formula) || row.formula || ""),
          atomCount: Number((result && result.atomCount) || (frames[0].atoms ? frames[0].atoms.length : 0)),
          clamped: null,
          emitted: false,
        };
        row.state = baseRowState(row);
        row.error = null;
        renderSourcePanel();
        publishFrameSelection(0);
      }).catch(function (error) {
        if (!selectionGuard(token, gen)) return;
        row.state = "error";
        row.error = describeError(error);
        state.sources.selection = {
          status: "error", row: row, text: "", frames: [], frameIndex: 0, frameCount: 0,
          token: token, error: error, formula: "", atomCount: 0, clamped: null, emitted: false,
        };
        renderSourcePanel();
      });
    }

    function selectFrame(index) {
      var selection = state.sources.selection;
      if (!selection || selection.status !== "ready" || !selection.frames.length) return;
      var count = selection.frames.length;
      var requested = Math.floor(Number(index));
      if (!isFinite(requested)) requested = 0;
      var clamped = requested < 0 ? 0 : (requested >= count ? count - 1 : requested);
      selection.frameIndex = clamped;
      selection.clamped = requested !== clamped ? { requested: requested, count: count } : null;
      renderSourceSelection();
      publishFrameSelection(clamped);
    }

    function publishFrameSelection(frameIndex) {
      var selection = state.sources.selection;
      if (!selection || selection.status !== "ready") return;
      var ref = rowRef(selection.row, frameIndex);
      var emitted = ref ? emitStructureRef(ref) : false;
      selection.emitted = emitted;
      if (state.ui.sourcePreview && state.ui.sourcePreview.setAttribute) {
        state.ui.sourcePreview.setAttribute("data-acp-source-emitted", emitted ? "true" : "false");
      }
    }

    /* ── source rendering ────────────────────────────────────────────── */

    function renderSourcePanel() {
      var panel = state.ui.sourcePanel;
      if (!panel) return;
      var name = state.sources.group;
      var group = state.sources.groups[name] || state.sources.groups.history;
      var stateName = group.status === "loading" ? "loading" : (group.status === "error" ? "error" : (group.rows.length ? "ready" : "empty"));
      panel.setAttribute("data-acp-source-state", stateName);
      renderSourceTabs();

      var status = state.ui.sourceStatus;
      if (status) {
        empty(status);
        status.hidden = false;
        if (group.status === "loading") {
          status.textContent = t("next.config.sources_loading");
        } else if (group.status === "error") {
          status.textContent = t("next.config.sources_error", { message: describeError(group.error) });
          var retry = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.sources_retry"));
          retry.type = "button";
          retry.setAttribute("data-action", "source-retry");
          retry.setAttribute("data-i18n", "next.config.sources_retry");
          on(retry, "click", function () { ensureSourceGroup(name, true); });
          status.appendChild(el(doc, "span", "", " "));
          status.appendChild(retry);
        } else {
          status.hidden = true;
        }
      }

      var list = state.ui.sourceList;
      if (list) {
        empty(list);
        list.setAttribute("data-acp-source-state", stateName);
        group.rows.forEach(function (row) {
          list.appendChild(buildSourceRow(row));
        });
        if (!group.rows.length && group.status !== "loading" && group.status !== "error") {
          list.appendChild(el(doc, "li", "acp-cfg-source-empty", sourceEmptyHint(name, group)));
        }
      }
      renderSourceSelection();
    }

    function renderSourceTabs() {
      var tabs = state.ui.sourceTabs;
      if (!tabs) return;
      (tabs.children || []).forEach(function (button) {
        if (!button.getAttribute) return;
        var name = button.getAttribute("data-source-tab");
        var active = name === state.sources.group;
        if (active) button.className = "acp-cfg-source-tab is-active";
        else button.className = "acp-cfg-source-tab";
        button.setAttribute("aria-selected", active ? "true" : "false");
      });
    }

    function sourceEmptyHint(name, group) {
      if (group.reason) return t(group.reason);
      if (name === "history") return t("next.config.sources_empty_history");
      if (name === "local") return t("next.config.sources_empty_local");
      if (name === "remote") return t("next.config.sources_empty_remote");
      return t("next.config.sources_idle");
    }

    function buildSourceRow(row) {
      var li = el(doc, "li", "acp-cfg-source");
      li.setAttribute("data-source-row", row.id);
      li.setAttribute("data-acp-source-state", row.state === "error" ? "error" : (row.state === "pending" ? "pending" : "ready"));
      var button = el(doc, "button", "acp-cfg-source-row");
      button.type = "button";
      button.setAttribute("data-source-id", row.id);
      button.setAttribute("data-source-group", row.group);
      if (row.disabled) button.disabled = true;
      button.appendChild(el(doc, "span", "acp-cfg-source-label", row.label));
      if (row.meta && row.meta.length) {
        button.appendChild(el(doc, "span", "acp-cfg-source-meta", row.meta.join(" · ")));
      }
      var chipText = row.chip || "";
      if (chipText) {
        var chip = el(doc, "span", "acp-cfg-source-chip", chipText);
        chip.setAttribute("data-acp-source-chip", row.state === "pending" ? "pending" : (row.state === "error" ? "error" : "ready"));
        button.appendChild(chip);
      }
      on(button, "click", function () { selectSource(row); });
      li.appendChild(button);
      if (row.error) li.appendChild(el(doc, "p", "acp-cfg-source-error", t("next.config.sources_row_error", { message: row.error })));
      return li;
    }

    function renderSourceSelection() {
      var box = state.ui.sourceFrames;
      var preview = state.ui.sourcePreview;
      var select = state.ui.frameSelect;
      var countEl = state.ui.frameCount;
      if (!box || !preview || !select || !countEl) return;
      var selection = state.sources.selection || { status: "idle" };
      if (state.ui.framePrev) state.ui.framePrev.disabled = true;
      if (state.ui.frameNext) state.ui.frameNext.disabled = true;
      if (!selection.status || selection.status === "idle") {
        box.hidden = true;
        preview.textContent = "";
        preview.setAttribute("data-acp-source-state", "idle");
        if (preview.removeAttribute) {
          preview.removeAttribute("data-acp-frame-index");
          preview.removeAttribute("data-acp-frame-count");
          preview.removeAttribute("data-acp-source-ref");
          preview.removeAttribute("data-acp-source-kind");
          preview.removeAttribute("data-acp-source-emitted");
        }
        empty(select);
        select.disabled = true;
        countEl.textContent = "";
        return;
      }
      box.hidden = false;
      if (selection.status === "loading") {
        preview.textContent = t("next.config.sources_source_loading");
        preview.setAttribute("data-acp-source-state", "loading");
        empty(select);
        select.disabled = true;
        countEl.textContent = "";
        return;
      }
      if (selection.status === "error") {
        preview.textContent = t("next.config.sources_source_error", { message: describeError(selection.error) });
        preview.setAttribute("data-acp-source-state", "error");
        empty(select);
        select.disabled = true;
        countEl.textContent = "";
        return;
      }
      var frames = selection.frames || [];
      if (select.children.length !== frames.length) {
        empty(select);
        frames.forEach(function (frame, i) {
          var label = t("next.config.frames_option", { index: i + 1 });
          if (frame && typeof frame.energy === "number" && isFinite(frame.energy)) {
            label += " · " + frame.energy.toFixed(6) + " Eh";
          }
          var option = el(doc, "option", "", label);
          option.value = String(i);
          select.appendChild(option);
        });
      }
      select.disabled = frames.length <= 1;
      select.value = String(selection.frameIndex);
      if (state.ui.framePrev) state.ui.framePrev.disabled = selection.frameIndex <= 0;
      if (state.ui.frameNext) state.ui.frameNext.disabled = selection.frameIndex >= frames.length - 1;
      countEl.textContent = t("next.config.frames_count", { count: frames.length });
      preview.setAttribute("data-acp-source-state", "ready");
      preview.setAttribute("data-acp-frame-index", String(selection.frameIndex));
      preview.setAttribute("data-acp-frame-count", String(frames.length));
      preview.setAttribute("data-acp-source-kind", rowKind(selection.row));
      preview.setAttribute("data-acp-source-ref", sourceRefLabel(selection.row, selection.frameIndex));
      preview.setAttribute("data-acp-source-emitted", selection.emitted ? "true" : "false");
      preview.textContent = frameIdentity(selection);
    }

    function frameIdentity(selection) {
      var row = selection.row || {};
      var count = selection.frames.length;
      var index = selection.frameIndex + 1;
      var text;
      if (row.group === "local") {
        text = t("next.config.frame_identity_entry", {
          index: index, count: count, kind: rowKind(row), jobId: row.jobId, entryId: row.entryId,
        });
      } else {
        text = t("next.config.frame_identity", {
          index: index, count: count, kind: rowKind(row), jobId: row.jobId, path: row.path,
        });
      }
      if (selection.formula) text += " · " + selection.formula;
      if (selection.atomCount) text += " · " + t("next.config.sources_atoms", { count: selection.atomCount });
      if (selection.clamped) {
        text += " · " + t("next.config.sources_clamped", {
          requested: selection.clamped.requested + 1,
          count: selection.clamped.count,
        });
      }
      return text;
    }

    /* ── validation + preflight ──────────────────────────────────────── */

    function scheduleValidation() {
      if (state.validation.timer) clearTimeout(state.validation.timer);
      var gen = state.generation;
      state.validation.timer = setTimeout(function () {
        state.validation.timer = null;
        if (!guard(gen)) return;
        validateMethod();
      }, VALIDATE_DEBOUNCE_MS);
    }

    function methodPayload() {
      return { schema_id: (state.entry && state.entry.schemaId) || "", levels: model.buildLevels(state.levels, state.schema, state.methodCatalog) };
    }

    function validateMethod() {
      if (!state.schema || !state.entry) return Promise.resolve({ ok: false });
      var payload = methodPayload();
      if (!payload.schema_id || !Object.keys(payload.levels).length) return Promise.resolve({ ok: false });
      var gen = state.generation;
      var token = state.validation.token + 1;
      state.validation.token = token;
      state.validation.status = "validating";
      renderValidation();
      return requestFn(API + "/validate-method", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        timeoutMs: 0,
      }).then(function (body) {
        if (!guard(gen) || state.validation.token !== token) return { ok: false };
        var errors = (body && body.errors) || [];
        var warnings = (body && body.warnings) || [];
        applyNormalizedLevels(body && body.normalized_levels);
        state.validation.status = errors.length ? "invalid" : "ok";
        state.validation.errors = errors.map(function (text) { return { text: String(text) }; });
        state.validation.warnings = warnings.map(function (text) { return { text: String(text) }; });
        state.validation.revision = state.configRevision;
        renderValidation();
        renderReview();
        renderActions();
        return { ok: state.validation.status === "ok" };
      }).catch(function (error) {
        if (!guard(gen) || state.validation.token !== token) return { ok: false };
        state.validation.status = "error";
        state.validation.errors = [{ text: t("next.config.validate_error", { message: describeError(error) }) }];
        state.validation.revision = state.configRevision;
        renderValidation();
        renderActions();
        return { ok: false };
      });
    }

    /** Sparse write-back: only fill keys the user has not set. Never overwrite. */
    function applyNormalizedLevels(normalized) {
      if (!normalized || typeof normalized !== "object") return;
      Object.keys(normalized).forEach(function (levelId) {
        var target = state.levels[levelId];
        var source = normalized[levelId];
        if (!target || !source || typeof source !== "object") return;
        Object.keys(source).forEach(function (key) {
          var value = source[key];
          if (value === undefined || value === null || typeof value === "object") return;
          if (target[key] === undefined || target[key] === null || target[key] === "") target[key] = value;
        });
      });
      refreshFieldInputs();
    }

    /** Update bound inputs in place: never rebuild the form under the caret. */
    function refreshFieldInputs() {
      var bound = state.ui.fieldInputs || {};
      Object.keys(bound).forEach(function (levelId) {
        var level = state.levels[levelId] || {};
        var fields = bound[levelId] || {};
        Object.keys(fields).forEach(function (field) {
          var input = fields[field];
          var value = level[field];
          if (!input || value === undefined || value === null) return;
          var text = String(value);
          if (input.value !== text) input.value = text;
        });
      });
    }

    function clientValidation() {
      return model.validateConfig({
        asset: state.asset,
        entry: state.entry,
        schema: state.schema,
        levels: state.levels,
        charge: state.charge,
        multiplicity: state.multiplicity,
        nproc: state.nproc,
        mem: state.mem,
      });
    }

    function currentRequest() {
      return model.buildRequest({
        entry: state.entry,
        schema: state.schema,
        workflowId: state.entry && state.entry.id,
        profileId: state.profileId,
        levels: state.levels,
        methodCatalog: state.methodCatalog,
        asset: state.asset,
        charge: state.charge,
        multiplicity: state.multiplicity,
        nproc: state.nproc,
        mem: state.mem,
        moleculeName: state.moleculeName,
        projectId: state.projectId,
      });
    }

    function runPreflight() {
      if (state.preflight.status === "running" || state.submit.status === "submitting") return;
      var check = clientValidation();
      renderClientErrors(check.errors);
      if (!check.ok) {
        state.preflight.status = "blocked";
        renderPreflight();
        renderActions();
        return Promise.resolve({ ok: false });
      }
      var validated = state.validation.status === "ok" && state.validation.revision === state.configRevision
        ? Promise.resolve({ ok: true })
        : validateMethod();
      return validated.then(function (methodResult) {
        if (!methodResult || !methodResult.ok) {
          state.preflight.status = "blocked";
          renderPreflight();
          renderActions();
          return { ok: false };
        }
        var request = currentRequest();
        var planKey = model.planIdentity(request);
        var gen = state.generation;
        var token = state.preflight.token + 1;
        state.preflight.token = token;
        state.preflight.status = "running";
        state.preflight.reasons = [];
        renderPreflight();
        renderActions();
        return requestFn(API + "/preflight", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
          timeoutMs: 0,
        }).then(function (body) {
          if (!guard(gen) || state.preflight.token !== token) return { ok: false };
          var digest = body && body.plan_digest;
          if (!body || body.feasible !== true || !digest) {
            state.preflight.status = "infeasible";
            state.preflight.reasons = ((body && body.reasons) || []).map(function (reason) {
              return String((reason && (reason.message || reason.code)) || reason);
            });
            if (!state.preflight.reasons.length) state.preflight.reasons = [t("next.config.preflight_failed")];
            renderPreflight();
            renderActions();
            return { ok: false };
          }
          state.preflight.status = "ok";
          state.preflight.fingerprint = String(digest);
          state.preflight.planKey = planKey;
          state.preflight.revision = state.configRevision;
          state.preflight.reasons = [];
          renderPreflight();
          renderActions();
          return { ok: true, fingerprint: digest };
        }).catch(function (error) {
          if (!guard(gen) || state.preflight.token !== token) return { ok: false };
          state.preflight.status = "error";
          state.preflight.reasons = [describeError(error)];
          renderPreflight();
          renderActions();
          return { ok: false };
        });
      });
    }

    function canSubmit() {
      if (!state.asset || !state.entry || !state.entry.supported) return false;
      if (state.submit.status === "submitting" || state.preflight.status === "running") return false;
      if (state.preflight.status !== "ok" || !state.preflight.fingerprint) return false;
      if (state.preflight.revision !== state.configRevision) return false;
      try {
        return state.preflight.planKey === model.planIdentity(currentRequest());
      } catch (e) {
        return false;
      }
    }

    function submit() {
      if (!canSubmit() || state.submit.status === "submitting") return Promise.resolve({ ok: false });
      var request = currentRequest();
      var planKey = model.planIdentity(request);
      if (!state.submit.idempotencyKey || state.submit.keyPlan !== planKey) {
        state.submit.idempotencyKey = newIdempotencyKey();
        state.submit.keyPlan = planKey;
      }
      var body = model.buildEnvelope(request, { projectId: state.projectId });
      body.preflight_fingerprint = state.preflight.fingerprint;
      body.schema_version = model.SCHEMA_VERSION;
      var gen = state.generation;
      var token = state.submit.token + 1;
      state.submit.token = token;
      state.submit.status = "submitting";
      state.submit.error = null;
      renderActions();
      renderSubmitState();
      return requestFn(API + "/jobs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": state.submit.idempotencyKey,
        },
        body: JSON.stringify(body),
        timeoutMs: 0,
      }).then(function (receipt) {
        if (!guard(gen) || state.submit.token !== token) return { ok: false };
        state.submit.status = "submitted";
        state.submit.receipt = receipt || {};
        state.submit.idempotencyKey = null;
        state.submit.keyPlan = null;
        discardDraft(true);
        renderSubmitState();
        renderActions();
        deliverReceipt(receipt || {});
        return { ok: true, receipt: receipt || {} };
      }).catch(function (error) {
        if (!guard(gen) || state.submit.token !== token) return { ok: false };
        state.submit.status = "error";
        var status = error && error.status;
        if (status === 400 && error.body && error.body.detail && error.body.detail.code === "preflight_stale") {
          state.submit.error = t("next.config.plan_stale");
          state.preflight = { status: "idle", fingerprint: null, planKey: null, reasons: [], token: state.preflight.token + 1, revision: -1 };
        } else if (status === 403 || status === 401) {
          state.submit.error = t("next.config.forbidden");
        } else {
          state.submit.error = t("next.config.submit_failed", { message: describeError(error) });
        }
        renderSubmitState();
        renderPreflight();
        renderActions();
        return { ok: false, error: error };
      });
    }

    function deliverReceipt(receipt) {
      if (!commands || typeof commands.openTask !== "function" || !receipt.job_id) return;
      try {
        commands.openTask({ jobId: String(receipt.job_id), attempt: Number(receipt.attempt || 1), source: MODULE_ID });
      } catch (e) {
        if (typeof console !== "undefined" && console.error) console.error("openTask after submit failed", e);
      }
    }

    /* ── drafts (server-side /drafts) ────────────────────────────────── */

    function pointerKey() {
      return model.draftPointerKey(state.projectId);
    }

    function readPointer() {
      try {
        var raw = window.localStorage && window.localStorage.getItem(pointerKey());
        if (!raw) return null;
        var parsed = JSON.parse(raw);
        return parsed && parsed.draft_id ? parsed : null;
      } catch (e) {
        return null;
      }
    }

    function writePointer(value) {
      try {
        if (!window.localStorage) return;
        if (value) window.localStorage.setItem(pointerKey(), JSON.stringify(value));
        else window.localStorage.removeItem(pointerKey());
      } catch (e) { /* storage unavailable: drafts still work, restore hint is lost */ }
    }

    function loadDraftPointer() {
      state.draft.banner = null;
      renderBanner();
      var pointer = readPointer();
      if (!pointer) return Promise.resolve();
      var gen = state.generation;
      return requestFn(API + "/drafts/" + encodeURIComponent(pointer.draft_id), { method: "GET" })
        .then(function (draft) {
          if (!guard(gen) || !draft || !draft.snapshot) return;
          state.draft.banner = {
            draftId: String(draft.draft_id),
            revision: Number(draft.revision) || 1,
            workflow: String(draft.workflow || ""),
            updatedAt: String(draft.updated_at || ""),
            snapshot: draft.snapshot,
          };
          state.draft.id = state.draft.banner.draftId;
          state.draft.revision = state.draft.banner.revision;
          renderBanner();
        })
        .catch(function () {
          writePointer(null);
          state.draft.banner = null;
          renderBanner();
        });
    }

    function restoreDraft() {
      var banner = state.draft.banner;
      if (!banner) return;
      var restored = model.draftToState(banner.snapshot);
      if (!restored) {
        state.draft.banner = null;
        renderBanner();
        return;
      }
      var entry = model.findWorkflow(state.workflows, restored.workflowId);
      if (entry) {
        state.entry = entry;
        state.schema = model.schemaFor(state.methodCatalog, entry.schemaId);
        state.profileId = restored.profileId;
        state.levels = restored.levels;
        var locked = model.applySemanticLocks(state.levels, state.methodCatalog);
        state.levels = locked.levels;
        state.notes = locked.notes;
      }
      state.charge = restored.charge;
      state.multiplicity = restored.multiplicity;
      state.nproc = restored.nproc;
      state.mem = restored.mem;
      state.moleculeName = restored.moleculeName;
      state.asset = restored.asset;
      state.structureText = restored.asset && restored.asset.xyz ? String(restored.asset.xyz) : "";
      state.parseStatus = restored.asset ? "ok" : "idle";
      state.draft.id = banner.draftId;
      state.draft.revision = banner.revision;
      state.draft.savedAt = banner.updatedAt;
      state.draft.banner = null;
      bumpRevision();
      renderAll();
      scheduleValidation();
      publishStructure();
      return;
    }

    function discardDraft(quiet) {
      var draftId = state.draft.id || (state.draft.banner && state.draft.banner.draftId);
      writePointer(null);
      state.draft.banner = null;
      state.draft.id = null;
      state.draft.revision = 0;
      state.draft.savedAt = null;
      state.draft.conflict = false;
      if (state.draft.timer) { clearTimeout(state.draft.timer); state.draft.timer = null; }
      if (!draftId) {
        if (!quiet) renderBanner();
        return Promise.resolve();
      }
      var gen = state.generation;
      return requestFn(API + "/drafts/" + encodeURIComponent(draftId), { method: "DELETE" })
        .catch(function () { return null; })
        .then(function () {
          if (!quiet && guard(gen)) renderBanner();
        });
    }

    function scheduleDraftSave() {
      if (state.disposed || !state.asset) return;
      if (state.draft.timer) clearTimeout(state.draft.timer);
      state.draft.timer = setTimeout(function () {
        state.draft.timer = null;
        saveDraft();
      }, DRAFT_DEBOUNCE_MS);
    }

    function saveDraft() {
      if (state.disposed || !state.asset || state.draft.conflict) return Promise.resolve();
      var snapshot = model.draftSnapshot({
        entry: state.entry,
        profileId: state.profileId,
        levels: state.levels,
        charge: state.charge,
        multiplicity: state.multiplicity,
        nproc: state.nproc,
        mem: state.mem,
        moleculeName: state.moleculeName,
        asset: state.asset,
      });
      var payload = {
        snapshot: snapshot,
        name: model.draftName(state.entry),
        project_id: state.projectId || "",
        workflow: (state.entry && state.entry.id) || "",
      };
      var gen = state.generation;
      state.draft.status = "saving";
      renderDraftStatus();
      var useUpdate = !!state.draft.id;
      var path = useUpdate ? API + "/drafts/" + encodeURIComponent(state.draft.id) : API + "/drafts";
      var method = useUpdate ? "PUT" : "POST";
      if (useUpdate) payload.expected_revision = state.draft.revision;
      return requestFn(path, {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        timeoutMs: 0,
      }).then(function (summary) {
        if (!guard(gen) || !summary) return;
        state.draft.id = String(summary.draft_id);
        state.draft.revision = Number(summary.revision) || 1;
        state.draft.savedAt = String(summary.updated_at || nowIso());
        state.draft.status = "saved";
        state.draft.error = null;
        writePointer({
          draft_id: state.draft.id,
          revision: state.draft.revision,
          project_id: state.projectId || "",
          workflow: (state.entry && state.entry.id) || "",
          updated_at: state.draft.savedAt,
        });
        renderDraftStatus();
      }).catch(function (error) {
        if (!guard(gen)) return;
        if (error && error.status === 409) {
          state.draft.conflict = true;
          state.draft.status = "conflict";
          renderBanner();
          return;
        }
        state.draft.status = "error";
        state.draft.error = describeError(error);
        renderDraftStatus();
      });
    }

    /* ── rendering ───────────────────────────────────────────────────── */

    function renderAll() {
      renderBanner();
      renderWorkflowOptions();
      renderProfileOptions();
      renderMethodValues();
      renderStructure();
      renderSourcePanel();
      renderStateInputs();
      renderReview();
      renderValidation();
      renderPreflight();
      renderSubmitState();
      renderActions();
      renderDraftStatus();
      renderSemanticNotes();
    }

    function renderBanner() {
      var banner = state.ui.banner;
      if (!banner) return;
      empty(banner);
      if (state.draft.conflict) {
        banner.hidden = false;
        banner.appendChild(el(doc, "span", "acp-cfg-banner-text", t("next.config.draft_conflict")));
        var reload = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.draft_reload"));
        reload.type = "button";
        on(reload, "click", function () {
          state.draft.conflict = false;
          state.draft.banner = null;
          loadDraftPointer().then(function () {
            if (state.draft.banner) restoreDraft();
          });
        });
        var fork = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.draft_fork"));
        fork.type = "button";
        on(fork, "click", function () {
          state.draft.conflict = false;
          writePointer(null);
          state.draft.id = null;
          state.draft.revision = 0;
          saveDraft();
          renderBanner();
        });
        banner.appendChild(reload);
        banner.appendChild(fork);
        return;
      }
      if (!state.draft.banner) {
        banner.hidden = true;
        return;
      }
      banner.hidden = false;
      banner.appendChild(el(doc, "span", "acp-cfg-banner-text", t("next.config.draft_found", { when: formatWhen(state.draft.banner.updatedAt) })));
      var restore = el(doc, "button", "acp-btn acp-btn--primary", t("next.config.draft_restore"));
      restore.type = "button";
      restore.setAttribute("data-action", "draft-restore");
      on(restore, "click", function () { restoreDraft(); });
      var discard = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.draft_discard"));
      discard.type = "button";
      discard.setAttribute("data-action", "draft-discard");
      on(discard, "click", function () { discardDraft(false); });
      banner.appendChild(restore);
      banner.appendChild(discard);
    }

    function renderDraftStatus() {
      var label = state.ui.draftStatus;
      if (!label) return;
      if (state.draft.status === "saving") label.textContent = t("next.config.draft_saving");
      else if (state.draft.status === "saved") label.textContent = t("next.config.draft_saved");
      else if (state.draft.status === "error") label.textContent = t("next.config.draft_error", { message: state.draft.error || "" });
      else label.textContent = "";
    }

    function renderWorkflowOptions() {
      var select = state.ui.workflowSelect;
      if (!select) return;
      var previous = (state.entry && state.entry.id) || select.value;
      empty(select);
      state.workflows.forEach(function (entry) {
        var option = el(doc, "option", "", languageLabel(entry) + (entry.supported ? "" : " " + t("next.config.workflow_unsupported")));
        option.value = entry.id;
        option.disabled = !entry.supported;
        option.setAttribute("data-supported", entry.supported ? "true" : "false");
        select.appendChild(option);
      });
      select.value = previous || (state.entry && state.entry.id) || "";
      if (state.ui.workflowDesc) {
        var entry = state.entry;
        var text = entry ? (lang() === "en" ? entry.description : (entry.descriptionZh || entry.description)) : "";
        state.ui.workflowDesc.textContent = text || "";
        state.ui.workflowDesc.hidden = !text;
      }
      if (state.ui.classicLink) {
        var unsupported = !!(state.entry && !state.entry.supported);
        state.ui.classicLink.hidden = !unsupported;
        if (unsupported) state.ui.classicLink.textContent = t("next.config.open_classic_hint");
      }
    }

    function languageLabel(entry) {
      return lang() === "en" ? (entry.label || entry.id) : (entry.labelZh || entry.label || entry.id);
    }

    function renderProfileOptions() {
      var select = state.ui.profileSelect;
      if (!select) return;
      empty(select);
      var profiles = model.profilesOf(state.schema);
      if (!profiles.length) {
        var option = el(doc, "option", "", t("next.config.profile_fields"));
        option.value = "";
        select.appendChild(option);
        select.value = "";
        select.disabled = true;
        return;
      }
      select.disabled = false;
      profiles.forEach(function (profile) {
        var label = lang() === "en" ? (profile.label || profile.profile_id) : (profile.label_zh || profile.label || profile.profile_id);
        var opt = el(doc, "option", "", label + (profile.summary ? " — " + profile.summary : ""));
        opt.value = profile.profile_id;
        select.appendChild(opt);
      });
      select.value = state.profileId || "";
    }

    function renderMethodValues() {
      var container = state.ui.fields;
      if (!container) return;
      empty(container);
      state.ui.fieldInputs = {};
      if (!state.schema) return;
      var ids = model.levelIds(state.schema);
      ids.forEach(function (levelId) {
        var card = el(doc, "div", "acp-cfg-level");
        var levelLabel = levelId;
        var defs = model.levelsOf(state.schema);
        for (var i = 0; i < defs.length; i++) {
          if (defs[i].level_id === levelId) {
            levelLabel = lang() === "en" ? (defs[i].label || levelId) : (defs[i].label_zh || defs[i].label || levelId);
            break;
          }
        }
        card.appendChild(el(doc, "h4", "acp-cfg-level-title", levelLabel));
        var split = model.editableFields(state.schema, levelId);
        split.primary.forEach(function (field) {
          card.appendChild(buildFieldRow(levelId, field));
        });
        if (split.secondary.length) {
          var details = el(doc, "details", "acp-cfg-advanced");
          var summary = el(doc, "summary", "", t("next.config.advanced"));
          summary.setAttribute("data-i18n", "next.config.advanced");
          details.appendChild(summary);
          split.secondary.forEach(function (field) { details.appendChild(buildFieldRow(levelId, field)); });
          card.appendChild(details);
        }
        container.appendChild(card);
      });
    }

    function buildFieldRow(levelId, field) {
      var def = model.fieldDefinition(state.methodCatalog, field);
      var level = state.levels[levelId] || {};
      var engine = level.engine || "orca";
      var wrap = el(doc, "label", "acp-cfg-field");
      var label = el(doc, "span", "acp-cfg-label", model.fieldLabel(def, field, lang()));
      wrap.appendChild(label);
      var options = model.fieldOptions(state.methodCatalog, field, engine);
      var current = level[field] === undefined || level[field] === null ? "" : String(level[field]);
      var input;
      if (options.length && field !== "max_steps") {
        input = el(doc, "select", "acp-cfg-select");
        if (current === "" || options.indexOf(current) === -1) {
          var blank = el(doc, "option", "", current ? current : "—");
          blank.value = current;
          input.appendChild(blank);
        }
        options.forEach(function (value) {
          var opt = el(doc, "option", "", String(value));
          opt.value = String(value);
          input.appendChild(opt);
        });
        input.value = current;
        on(input, "change", function () { setLevelValue(levelId, field, input.value); });
      } else {
        input = el(doc, "input", "acp-cfg-input");
        input.type = field === "max_steps" ? "number" : "text";
        input.value = current;
        on(input, "input", function () {
          var raw = input.value;
          setLevelValue(levelId, field, field === "max_steps" ? Number(raw) || 0 : raw);
        });
      }
      input.setAttribute("data-field", field);
      input.setAttribute("data-level", levelId);
      wrap.appendChild(input);
      if (!state.ui.fieldInputs[levelId]) state.ui.fieldInputs[levelId] = {};
      state.ui.fieldInputs[levelId][field] = input;
      var help = model.fieldHelp(def, lang());
      if (help) {
        var helpNode = el(doc, "small", "acp-cfg-help", help);
        wrap.appendChild(helpNode);
      }
      return wrap;
    }

    function setLevelValue(levelId, field, value) {
      if (!state.levels[levelId]) state.levels[levelId] = {};
      state.levels[levelId][field] = value;
      if (field === "functional") {
        var locked = model.applySemanticLocks(state.levels, state.methodCatalog);
        state.levels = locked.levels;
        state.notes = locked.notes;
        renderMethodValues();
        renderSemanticNotes();
      }
      bumpRevision();
      renderReview();
      renderActions();
      scheduleDraftSave();
      scheduleValidation();
    }

    function renderSemanticNotes() {
      var list = state.ui.semanticNotes;
      if (!list) return;
      empty(list);
      (state.notes || []).forEach(function (note) {
        var text = t(note.key, note.vars || {});
        listItem(list, "acp-cfg-note-item", text);
      });
      list.hidden = !(state.notes && state.notes.length);
    }

    function renderStructure() {
      var summary = state.ui.structureSummary;
      if (!summary) return;
      if (state.parseStatus === "loading") {
        summary.textContent = t("next.config.parsing");
        summary.setAttribute("data-i18n", "next.config.parsing");
        return;
      }
      if (state.parseStatus === "error") {
        summary.textContent = t("next.config.structure_error", { message: describeError(state.parseError) });
        summary.setAttribute("role", "alert");
        return;
      }
      summary.removeAttribute && summary.removeAttribute("role");
      if (state.asset) {
        summary.textContent = t("next.config.structure_summary", {
          formula: state.asset.formula || "?",
          atoms: state.asset.atom_count || 0,
          charge: state.charge,
          multiplicity: state.multiplicity,
        });
        return;
      }
      summary.textContent = t("next.config.structure_hint");
      summary.setAttribute("data-i18n", "next.config.structure_hint");
    }

    function renderStateInputs() {
      if (state.ui.charge) state.ui.charge.value = String(state.charge);
      if (state.ui.multiplicity) state.ui.multiplicity.value = String(state.multiplicity);
      if (state.ui.nproc) state.ui.nproc.value = String(state.nproc);
      if (state.ui.mem) state.ui.mem.value = String(state.mem);
      if (state.ui.moleculeName) state.ui.moleculeName.value = String(state.moleculeName || "");
      if (state.ui.parityHint) {
        var check = model.validateState(state.charge, state.multiplicity, state.asset);
        var parityError = check.errors.filter(function (item) { return item.key === "next.config.error.multiplicity_parity"; })[0];
        state.ui.parityHint.textContent = parityError ? t(parityError.key, parityError.vars) : "";
        state.ui.parityHint.hidden = !parityError;
      }
    }

    function renderReview() {
      var summary = state.ui.summary;
      if (!summary) return;
      empty(summary);
      if (!state.asset || !state.entry) {
        summary.appendChild(el(doc, "dd", "acp-cfg-summary-empty", t("next.config.summary_empty")));
        return;
      }
      summaryRow(summary, "next.config.workflow_label", languageLabel(state.entry));
      var methodText = describeMethod();
      if (methodText) summaryRow(summary, "next.config.profile_label", methodText);
      summaryRow(summary, "next.config.charge", String(state.charge));
      summaryRow(summary, "next.config.multiplicity", String(state.multiplicity));
      summaryRow(summary, "next.config.nproc", String(state.nproc) + " · " + String(state.mem || ""));
      if (state.projectId) summaryRow(summary, "next.config.project", state.projectId);
    }

    function summaryRow(list, key, value) {
      var dt = el(doc, "dt", "", t(key));
      dt.setAttribute("data-i18n", key);
      var dd = el(doc, "dd", "", String(value == null ? "" : value));
      list.appendChild(dt);
      list.appendChild(dd);
    }

    function describeMethod() {
      if (!state.schema) return "";
      var ids = model.levelIds(state.schema);
      var parts = [];
      ids.forEach(function (levelId) {
        var level = state.levels[levelId] || {};
        var bits = [];
        if (level.functional) bits.push(String(level.functional));
        if (level.basis) bits.push(String(level.basis));
        if (level.gfn) bits.push(String(level.gfn));
        if (level.opt_level) bits.push(String(level.opt_level));
        if (bits.length) parts.push(bits.join(" / "));
      });
      return parts.join(" · ");
    }

    function renderValidation() {
      var status = state.ui.validationStatus;
      if (!status) return;
      var value = "";
      if (state.validation.status === "validating") value = t("next.config.validate_running");
      else if (state.validation.status === "ok") value = t("next.config.validate_ok");
      else if (state.validation.status === "invalid") value = t("next.config.validate_invalid");
      else if (state.validation.status === "error") value = state.validation.errors[0] ? state.validation.errors[0].text : "";
      status.textContent = value;
      status.hidden = !value;
      if (!state.ui.errors) return;
      empty(state.ui.errors);
      if (state.validation.status === "invalid" || state.validation.status === "error") {
        state.validation.errors.forEach(function (item) { listItem(state.ui.errors, "acp-cfg-error", item.text); });
        state.validation.warnings.forEach(function (item) { listItem(state.ui.errors, "acp-cfg-warning", item.text); });
      }
    }

    function renderClientErrors(errors) {
      if (!state.ui.errors) return;
      if (!errors || !errors.length) return;
      empty(state.ui.errors);
      errors.forEach(function (item) { listItem(state.ui.errors, "acp-cfg-error", t(item.key, item.vars || {})); });
    }

    function renderPreflight() {
      var status = state.ui.preflightStatus;
      var reasons = state.ui.preflightReasons;
      if (!status || !reasons) return;
      empty(reasons);
      if (state.preflight.status === "running") {
        status.textContent = t("next.config.preflighting");
        status.hidden = false;
        return;
      }
      if (state.preflight.status === "ok") {
        status.textContent = t("next.config.preflight_ok", { digest: String(state.preflight.fingerprint || "").slice(0, 23) });
        status.hidden = false;
        return;
      }
      if (state.preflight.status === "infeasible" || state.preflight.status === "error") {
        status.textContent = t("next.config.preflight_failed");
        status.hidden = false;
        (state.preflight.reasons || []).forEach(function (text) { listItem(reasons, "acp-cfg-error", String(text)); });
        return;
      }
      if (state.preflight.status === "blocked") {
        status.textContent = t("next.config.submit_blocked");
        status.hidden = false;
        return;
      }
      status.textContent = "";
      status.hidden = true;
    }

    function renderSubmitState() {
      var receipt = state.ui.receipt;
      if (!receipt) return;
      empty(receipt);
      if (state.submit.status === "error" && state.submit.error) {
        receipt.hidden = false;
        receipt.appendChild(el(doc, "p", "acp-cfg-error", state.submit.error));
        return;
      }
      if (state.submit.status !== "submitted" || !state.submit.receipt) {
        receipt.hidden = true;
        return;
      }
      receipt.hidden = false;
      var data = state.submit.receipt;
      receipt.appendChild(el(doc, "h4", "acp-cfg-receipt-title", t("next.config.receipt")));
      receipt.appendChild(el(doc, "p", "", t("next.config.receipt_body", {
        jobId: String(data.job_id || ""),
        status: String(data.status || ""),
        attempt: Number(data.attempt || 1),
      })));
      if (data.plan_digest) {
        receipt.appendChild(el(doc, "p", "acp-cfg-mono", t("next.config.receipt_digest", { digest: String(data.plan_digest) })));
      }
      var open = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.open_task"));
      open.type = "button";
      open.setAttribute("data-i18n", "next.config.open_task");
      on(open, "click", function () { deliverReceipt(data); });
      receipt.appendChild(open);
    }

    function renderActions() {
      if (!state.ui.preflightBtn || !state.ui.submitBtn) return;
      var busy = state.preflight.status === "running" || state.submit.status === "submitting";
      state.ui.preflightBtn.disabled = busy || !state.entry || !state.entry.supported;
      state.ui.submitBtn.disabled = !canSubmit() || busy;
      state.ui.preflightBtn.textContent = state.preflight.status === "running"
        ? t("next.config.preflighting")
        : t("next.config.validate");
      state.ui.submitBtn.textContent = state.submit.status === "submitting"
        ? t("next.config.submitting")
        : t("next.config.submit");
    }

    /* ── lifecycle ───────────────────────────────────────────────────── */

    function applySnapshot(snapshot) {
      state.snapshot = snapshot || null;
      var projectId = snapshot && snapshot.projectId ? String(snapshot.projectId) : "";
      var projectChanged = projectId !== state.projectId;
      state.projectId = projectId;
      if (projectChanged && !state.catalogLoading && state.methodCatalog) {
        loadDraftPointer().then(renderBanner);
      }
      if (projectChanged) resetHistorySource();
      var jobId = taskJobId();
      if (jobId !== state.sources.taskKey) {
        state.sources.taskKey = jobId;
        resetTaskBoundSources();
      }
      var ref = snapshot && snapshot.structureRef;
      if (ref && ref.kind === "inline-xyz" && ref.text && state.parseStatus !== "ok") {
        state.structureText = String(ref.text);
        if (state.ui.structureText) state.ui.structureText.value = state.structureText;
        parseStructure();
      }
    }

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl) throw new Error("task-config: mount host missing");
      if (!model) throw new Error("task-config: ACPNextTaskConfigModel missing");
      if (!requestFn) throw new Error("task-config: ACPClient.transport missing");
      if (!doc) throw new Error("task-config: document missing");
      state.host = hostEl;
      state.generation += 1;
      state.mounted = true;
      state.disposed = false;
      var contextApi = api.context || context;
      buildDom();
      if (contextApi && typeof contextApi.getSnapshot === "function") applySnapshot(contextApi.getSnapshot());
      if (contextApi && typeof contextApi.subscribe === "function") {
        var unsubscribe = contextApi.subscribe(function (snapshot) {
          if (state.disposed) return;
          applySnapshot(snapshot);
          renderReview();
        });
        listenerCleanup.push(unsubscribe);
      }
      var langHandler = function () { renderAll(); };
      if (doc.addEventListener) {
        doc.addEventListener("acp:langchange", langHandler);
        listenerCleanup.push(function () { doc.removeEventListener("acp:langchange", langHandler); });
      }
      renderAll();
      return loadCatalogs();
    }

    function updateContext(snapshot) {
      if (state.disposed || !state.mounted) return;
      applySnapshot(snapshot);
      renderReview();
    }

    function suspend() { state.suspended = true; }

    function resume() { state.suspended = false; renderAll(); }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      if (state.validation.timer) { clearTimeout(state.validation.timer); state.validation.timer = null; }
      if (state.draft.timer) { clearTimeout(state.draft.timer); state.draft.timer = null; }
      listenerCleanup.splice(0).forEach(function (fn) {
        try { fn(); } catch (e) { /* listener already gone */ }
      });
      SOURCE_GROUPS.forEach(function (name) {
        var group = state.sources.groups[name];
        group.token += 1;
        group.status = "idle";
        group.rows = [];
        group.error = null;
        group.loaded = false;
        group.reason = "";
      });
      state.sources.selection.token += 1;
      state.sources.selection = emptySelection();
      state.sources.ready = false;
      state.sources.taskKey = "";
      if (state.host && typeof state.host.replaceChildren === "function") {
        try { state.host.replaceChildren(); } catch (e) { /* host gone */ }
      }
      state.host = null;
      state.ui = {};
      state.asset = null;
      state.levels = {};
      state.workflows = [];
    }

    return {
      mount: mount,
      updateContext: updateContext,
      suspend: suspend,
      resume: resume,
      dispose: dispose,
    };
  }

  window.ACPNextModules = window.ACPNextModules || {};
  window.ACPNextModules[MODULE_ID] = {
    descriptor: DESCRIPTOR,
    factory: createTaskConfig,
  };
})();

