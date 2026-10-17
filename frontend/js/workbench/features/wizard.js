// ── CatalogUtils — IIFE module for catalog-helper functions ──────────
// Phase 4.1: extracted from openMethodConfig() closure so that the
// catalog-access utilities (lookupCI, isFieldAdvanced, getFieldDefault,
// getFieldOptions) are independently testable and do not pollute the
// global scope with individual function names.
// All functions receive the catalog state as explicit parameters
// (methodCatalogCache / field_definitions / method_meta) rather than
// capturing a closure variable, eliminating the R26 stale-reference risk.
var CatalogUtils = (function() {
  'use strict';

  // Case-insensitive lookup for catalogs whose keys preserve canonical
  // casing (e.g. METHOD_META / functional_options_map use "wB97M-V",
  // "DLPNO-CCSD(T)"). Replaces direct subscript access so user input /
  // dropdown value casing differences do not cause lookup failures.
  // See DevDoc §2.2 / §8.1.1.
  function lookupCI(obj, key) {
    if (!obj || key == null) return undefined;
    if (Object.prototype.hasOwnProperty.call(obj, key)) return obj[key];
    var kl = String(key).toLowerCase();
    for (var k in obj) {
      if (Object.prototype.hasOwnProperty.call(obj, k) && String(k).toLowerCase() === kl) return obj[k];
    }
    return undefined;
  }

  function isFieldAdvanced(catalog, fieldName) {
    var fd = ((catalog && catalog.field_definitions) || {})[fieldName];
    return !!(fd && fd.advanced);
  }

  // ── method-family field availability (T15) ──────────────────────────
  // Mirrors acp.catalog._family_locked_options / _resolve_field_options
  // over the catalog payload ONLY. The family signal is the empty option
  // offer: a family that carries no DFT basis layer advertises
  // functional_options_map[method].basis === [] (T11 locked/empty set).
  // Never keyed on method names — the catalog/registry is the single
  // source (AGENTS anti-pattern #27).

  // Level-scoped field -> shared base field name, mirroring
  // acp.catalog._LEVEL_SCOPED_FIELD_BASE + the scan_optimizer_ prefix
  // strip (scan_optimizer_basis -> basis, scan_optimizer_grid -> grid).
  function fieldBaseName(fieldName) {
    var scoped = {
      "scan_optimizer_basis": "basis",
      "scan_optimizer_dispersion": "dispersion",
      "scan_optimizer_ri_approximation": "ri_approximation"
    };
    if (scoped[fieldName]) return scoped[fieldName];
    if (fieldName.indexOf("scan_optimizer_") === 0) return fieldName.slice("scan_optimizer_".length);
    return fieldName;
  }

  // True when the selected method family offers no options for this field
  // (the locked/empty set), mirroring acp.catalog._family_locked_options:
  // a family without a DFT basis layer governs no basis/dispersion/grid and
  // no SCF dropdowns (grid / scf_* selects only — scalar caps such as
  // scf_max_iterations pass through untouched).
  function isFieldFamilyLocked(catalog, fieldName, functional) {
    if (!functional) return false;
    var funcFilter = lookupCI((catalog && catalog.functional_options_map) || {}, functional);
    if (!funcFilter) return false;
    var base = fieldBaseName(fieldName);
    // basis / dispersion: the family's own offer is the gate (empty = locked).
    if (base === "basis" || base === "dispersion") {
      var offer = funcFilter[base];
      return Array.isArray(offer) && offer.length === 0;
    }
    // RI family (T16): a method whose RI layer is composite/automatic
    // (METHOD_META.ri_support !== "user") accepts no user RI/aux choice —
    // the same payload signal buildFieldRow uses for the RI immutable
    // badge and the method-change handlers use for their RI reset step.
    if (base === "ri_approximation" || base === "aux_j_basis" || base === "aux_c_basis") {
      var riMeta = lookupCI((catalog && catalog.method_meta) || {}, functional) || {};
      return (riMeta.ri_support || "user") !== "user";
    }
    // grid / scf_* selects: no DFT basis layer ⇒ no integration grid or
    // SCF dropdowns (mirrors the backend's is_applicable("basis") rule).
    var noBasisLayer = Array.isArray(funcFilter.basis) && funcFilter.basis.length === 0;
    if (!noBasisLayer) return false;
    if (base === "grid") return true;
    if (base.indexOf("scf_") === 0) {
      var fd = ((catalog && catalog.field_definitions) || {})[fieldName] || {};
      return !!(fd.options || fd.per_backend);
    }
    return false;
  }

  // After a method change: clear/lock family-locked fields and reset any
  // value the new family's scoped option set no longer admits back to the
  // METHOD_META/FIELD_DEFINITIONS default. `fieldNames` is the level's
  // catalog field list (lvDef.fields) — never a hardcoded field list.
  function applyMethodFamilyState(catalog, levelState, fieldNames, engine, functional) {
    if (!levelState) return;
    (fieldNames || []).forEach(function(fn) {
      if (isFieldFamilyLocked(catalog, fn, functional)) {
        // Cleared spelling mirrors buildFieldRow / the method-change
        // handlers: dispersion and ri_approximation clear to "none", the
        // remaining family-locked fields (basis / grid / scf_* / aux) to "".
        var lockedBase = fieldBaseName(fn);
        levelState[fn] = (lockedBase === "dispersion" || lockedBase === "ri_approximation") ? "none" : "";
        return;
      }
      var fd = ((catalog && catalog.field_definitions) || {})[fn] || {};
      if (fd.supports_custom || fd.multi) return;
      var cur = levelState[fn];
      if (cur == null || cur === "" || cur === "__custom__") return;
      if (typeof cur !== "string") return;
      var opts = getFieldOptions(catalog, fn, engine, functional,
        levelState.basis || levelState.scan_optimizer_basis);
      if (!opts || !opts.length) return;
      for (var i = 0; i < opts.length; i++) {
        if (String(opts[i]).toLowerCase() === cur.toLowerCase()) return;
      }
      levelState[fn] = getFieldDefault(catalog, fn, engine, functional,
        levelState.basis || levelState.scan_optimizer_basis);
    });
  }

  // ── copy scan level → single_point (T16) ─────────────────────────────
  // Copy the per-point optimization level into the single-point level, then
  // reconcile EVERY destination field against the COPIED method's family —
  // the method travels with the copy (scan_optimizer_method → functional),
  // so the pre-copy target method must never decide what stays valid. A GFN
  // scan level therefore yields a GFN single-point level with basis/
  // dispersion/RI/aux/grid/SCF cleared, even when the destination held a
  // stale DFT configuration (empty source values are not copied, so only the
  // family sweep can retire those). The gate is the catalog-driven
  // applyMethodFamilyState above — never a hardcoded inapplicable-field list
  // (AGENTS anti-pattern #27). The copy field projection is DERIVED from the
  // catalog level field lists (T23g) — see the body below.
  function copyScanLevelFields(catalog, srcState, dstState, fieldNames, engine) {
    if (!srcState || !dstState) return dstState;
    // (T23g) The copy projection is DERIVED from the catalog level field
    // lists: every scan_optimizer_* source field whose base name also
    // exists on the destination level travels with the copy. Two documented
    // semantic exceptions (feature contract, locked by
    // tests/test_frontend_sync.py):
    //   1. the method field travels as the destination's "functional";
    //   2. the RI family (ri_approximation / aux_j_basis / aux_c_basis) is
    //      deliberately NOT copied — it is method-family owned and the
    //      family sweep below decides it (T16 contract).
    // For the current catalog the derived projection is exactly the
    // historical 7-pair map (method/basis/dispersion/solvent_model/solvent/
    // grid/scf_convergence); the retired inline copyMap literal must never
    // come back (AGENTS anti-pattern #27).
    var dstFieldSet = {};
    (fieldNames || []).forEach(function(fn) { dstFieldSet[fn] = true; });
    var skipCopyBases = { "ri_approximation": true, "aux_j_basis": true, "aux_c_basis": true };
    Object.keys(srcState).forEach(function(sk) {
      if (sk.indexOf("scan_optimizer_") !== 0) return;
      var base = fieldBaseName(sk);
      if (skipCopyBases[base]) return;
      var dstKey = base === "method" ? "functional" : base;
      if (!dstFieldSet[dstKey]) return;
      if (srcState[sk] !== undefined && srcState[sk] !== "") {
        dstState[dstKey] = srcState[sk];
      }
    });
    if (!dstState.engine) dstState.engine = engine || "orca";
    delete dstState._disabled;
    // Reconcile against the COPIED method (read AFTER the copy loop) so the
    // sweep retires every family-inapplicable field — copied or stale.
    var copiedMethod = dstState.functional || "";
    applyMethodFamilyState(catalog, dstState, fieldNames, engine, copiedMethod);
    return dstState;
  }

  // ── legacy grid migration (T23f) ────────────────────────────────────
  // HISTORICAL-COMPAT (T23g): legacy Gaussian-era grid alias mirror of
  // cccp.qc.keyword_registry._ENUM_TABLES["grid"] legacy rows. The registry
  // is the single authority (its aliases stay legal INPUTS by design); this
  // mirror only lets draft hydration migrate + warn before submission, and
  // is parity-locked to the registry by tests/test_frontend_sync.py. Method
  // and field literals may live ONLY in this historical-compat branch.
  var HISTORICAL_COMPAT_GRID_ALIASES = {
    "sg1": "DefGrid1",
    "fine": "DefGrid2",
    "ultrafine": "DefGrid3",
    "superfine": "DefGrid3",
  };

  // Canonicalize one legacy grid value. Returns {from, to} when the value is
  // a legacy alias needing migration, else null (canonical DefGrid* / blank
  // / non-grid values are left untouched — never invent a target).
  function migrateLegacyGridValue(catalog, value) {
    if (typeof value !== "string" || !value) return null;
    var gridFd = ((catalog && catalog.field_definitions) || {}).grid || {};
    var opts = gridFd.options || [];
    for (var i = 0; i < opts.length; i++) {
      if (String(opts[i]).toLowerCase() === value.toLowerCase()) return null;
    }
    var target = HISTORICAL_COMPAT_GRID_ALIASES[value.toLowerCase().replace(/\s+/g, "")];
    if (!target) return null;
    return { from: value, to: target };
  }

  // Sweep every level state of `stages` ({level_id: state}), migrating
  // legacy grid fields (fieldBaseName == "grid") in place. Returns the hint
  // list [{level, field, from, to}] the hydration path renders (T23f).
  function migrateLegacyGridInStates(catalog, stages) {
    var hints = [];
    if (!stages || typeof stages !== "object") return hints;
    Object.keys(stages).forEach(function(levelId) {
      var st = stages[levelId];
      if (!st || typeof st !== "object") return;
      Object.keys(st).forEach(function(fieldName) {
        if (fieldBaseName(fieldName) !== "grid") return;
        var mig = migrateLegacyGridValue(catalog, st[fieldName]);
        if (!mig) return;
        st[fieldName] = mig.to;
        hints.push({ level: levelId, field: fieldName, from: mig.from, to: mig.to });
      });
    });
    return hints;
  }

  function getFieldOptions(catalog, fieldName, engine, functional, basis) {
    var fd = ((catalog && catalog.field_definitions) || {})[fieldName];
    if (!fd) return null;

    // Family gate first (mirrors acp.catalog._resolve_field_options, which
    // consults _family_locked_options before any option lookup).
    if (isFieldFamilyLocked(catalog, fieldName, functional)) return [];

    // Method-family-scoped solvent models: scan_optimizer_solvent_model (and
    // solvent_model) resolve to METHOD_META.solvent_models when the family
    // restricts the offer (e.g. ["none","ALPB"] for GFN under ORCA).
    if (functional && fieldBaseName(fieldName) === "solvent_model") {
      var smMeta = lookupCI((catalog.method_meta || {}), functional);
      if (smMeta && Array.isArray(smMeta.solvent_models)) return smMeta.solvent_models.slice();
    }

    // Method-scoped basis / dispersion offers (FUNCTIONAL_OPTIONS_MAP),
    // mirroring acp.catalog._resolve_field_options so every consumer shares
    // one resolution point. An empty set is the family-locked offer (T11).
    var scopedBase = fieldBaseName(fieldName);
    if (functional && (scopedBase === "basis" || scopedBase === "dispersion")) {
      var famFilter = lookupCI((catalog.functional_options_map || {}), functional);
      var scopedOffer = famFilter ? famFilter[scopedBase] : undefined;
      if (Array.isArray(scopedOffer)) return scopedOffer.slice();
    }

    if ((fieldName === "aux_j_basis" || fieldName === "aux_c_basis") && fd.options_source === "dynamic_aux_basis") {
      var auxKind = fd.aux_kind;
      var fallback = (fd.per_backend || {})[engine] || [];
      if (!Array.isArray(fallback)) fallback = [];

      if (functional) {
        var funcMeta = lookupCI((catalog.method_meta || {}), functional);
        if (funcMeta) {
          var riSupport = funcMeta.ri_support || "user";
          if (riSupport !== "user") return [];
          if (fieldName === "aux_c_basis" && !funcMeta.needs_aux_c) return [];
        }
      }

      var basisCatalog = (catalog && catalog.basis_catalog_v2) || {};
      var basisKey = "aux_" + auxKind;
      if (basis && basisCatalog[basis] && basisCatalog[basis][basisKey]) {
        var tailored = basisCatalog[basis][basisKey];
        if (fallback.indexOf(tailored) < 0) {
          return [tailored].concat(fallback);
        }
      }
      return fallback.slice();
    }

    if (fd.options) return fd.options;
    if (fd.per_backend) return fd.per_backend[engine] || null;
    return null;
  }

  // Resolution priority:
  //   1. METHOD_META[functional] — functional-level defaults for
  //      basis / dispersion; RI/aux derived from ri_support three-state
  //      and basis_catalog_v2 metadata.
  //   2. FIELD_DEFINITIONS — engine-specific → wildcard "*" → scalar.
  function getFieldDefault(catalog, fieldName, engine, functional, basis) {
    var fieldDefs = (catalog && catalog.field_definitions) || {};
    var methodMeta = (catalog && catalog.method_meta) || {};
    var basisCatalog = (catalog && catalog.basis_catalog_v2) || {};
    // Level-scoped fields (scan_optimizer_basis, ...) derive METHOD_META
    // defaults through their base name, like the backend resolver.
    var base = fieldBaseName(fieldName);

    // ── Priority 1: functional-level defaults (METHOD_META) ──
    if (functional) {
      var funcMeta = lookupCI(methodMeta, functional);
      if (funcMeta) {
        var riSupport = funcMeta.ri_support || "user";

        if (riSupport !== "user") {
          if (base === "ri_approximation") return "none";
          if (fieldName === "aux_j_basis") return "";
          if (fieldName === "aux_c_basis") return "";
        }

        if (fieldName === "aux_j_basis" && basis && basisCatalog[basis]) {
          return basisCatalog[basis].aux_j || "AutoAux";
        }

        if (fieldName === "aux_c_basis") {
          if (!funcMeta.needs_aux_c) return "";
          if (basis && basisCatalog[basis]) {
            return basisCatalog[basis].aux_c || "AutoAux";
          }
        }

        if (base === "basis" && funcMeta.default_basis !== undefined) return funcMeta.default_basis;
        if (base === "dispersion" && funcMeta.default_dispersion !== undefined) return funcMeta.default_dispersion;
      }
    }

    // ── Priority 2: global default (FIELD_DEFINITIONS) ──
    var fd = fieldDefs[fieldName];
    if (!fd) return "";
    var dflt = fd.default || {};
    if (typeof dflt === "object" && !Array.isArray(dflt)) {
      if (dflt[engine] !== undefined) return dflt[engine];
      if (dflt["*"] !== undefined) return dflt["*"];
    }
    return typeof dflt === "string" || typeof dflt === "number" ? dflt : "";
  }

  return {
    lookupCI: lookupCI,
    isFieldAdvanced: isFieldAdvanced,
    fieldBaseName: fieldBaseName,
    isFieldFamilyLocked: isFieldFamilyLocked,
    applyMethodFamilyState: applyMethodFamilyState,
    copyScanLevelFields: copyScanLevelFields,
    HISTORICAL_COMPAT_GRID_ALIASES: HISTORICAL_COMPAT_GRID_ALIASES,
    migrateLegacyGridValue: migrateLegacyGridValue,
    migrateLegacyGridInStates: migrateLegacyGridInStates,
    getFieldOptions: getFieldOptions,
    getFieldDefault: getFieldDefault
  };
})();

// Backward-compatible global alias (used extensively in openMethodConfig).
// When the functions are fully modularised (Phase 4.4 Vite migration),
// the global alias can be dropped in favour of explicit imports.
var lookupCI = CatalogUtils.lookupCI;

// R3 slice (b): wizard endpoint adapters (plan §42.2 wizard row / §42.4 R3).
// All seven former raw-fetch sites (structure upload, structures/parse,
// structure-source preview, NMR Bruker upload, hessian preview, batch config
// preview, validate-method) go through ACPClient.wizardEndpoints, which
// freezes each legacy call contract (URL/body/timeoutMs:0/error text)
// verbatim. Init lives at module scope — this file loads after compat/api.js,
// so the page `api()` and API_BASE exist here (same init-in-feature pattern
// as features/queue.js wiring ACPClient.jobCommands).
if (window.ACPClient && window.ACPClient.wizardEndpoints) {
  window.ACPClient.wizardEndpoints.init({
    v1: api,
    direct: window.ACPClient.transport.request,
    baseUrl: API_BASE,
  });
}

const wizardState = {
  // Default workflow is resolved at init from the visible catalog
  // (see the DOMContentLoaded initialiser). An empty id must never be
  // substituted with a retired entry such as the legacy "energy" default.
  workflow: { id: "", label: "" },
  method: {
    profile_id: "",
    profile_label: "",
    stages: {}
  }
};

var wizardDraft = null;
var wizardDraftMeta = null;
var wizardDraftSaveQueue = Promise.resolve();
var wizardDraftAutosaveTimer = null;
var wizardDraftEpoch = 0;

function _wizardDraftName(snapshot) {
  var fields = snapshot.fields || {};
  var task = String((fields["modal-task-name"] || {}).value || "").trim();
  var workflow = String(((snapshot.workflowState || {}).workflow || {}).label || "").trim();
  return task || workflow || (currentLang === "zh-CN" ? "未命名草稿" : "Untitled draft");
}

function _wizardDraftErrorText(err) {
  var message = err && err.message ? err.message : String(err || "");
  if (message === "Not Found" || message === "HTTP 404") {
    return currentLang === "zh-CN"
      ? "草稿接口返回 404。请重启 ACP 后端，并确认已加载 /api/v1/drafts 路由。"
      : "Draft API returned 404. Restart the ACP backend and confirm /api/v1/drafts is registered.";
  }
  if (message === "draft_not_found") {
    return currentLang === "zh-CN" ? "这份草稿已不存在，请刷新草稿箱。" : "This draft no longer exists. Refresh the draft list.";
  }
  if (message === "revision_conflict") {
    return currentLang === "zh-CN"
      ? "这份草稿已在其他窗口更新。请重新载入草稿后继续编辑。"
      : "This draft changed in another window. Reload it before continuing.";
  }
  return message || (currentLang === "zh-CN" ? "未知错误" : "Unknown error");
}

function _saveWizardDraft() {
  if (window.ACPJobEditor && ACPJobEditor.isActive()) return Promise.resolve();
  var epoch = wizardDraftEpoch;
  var statusEl = document.getElementById("wizard-draft-status");
  if (statusEl) statusEl.textContent = currentLang === "zh-CN" ? "保存中…" : "Saving…";
  var snapshot = _captureWizardDraft();
  wizardDraft = snapshot;
  var projectId = String((snapshot.fields["modal-project-select"] || {}).value || "");
  var body = {
    name: _wizardDraftName(snapshot), project_id: projectId,
    workflow: String(((snapshot.workflowState || {}).workflow || {}).id || ""),
    snapshot: snapshot
  };
  wizardDraftSaveQueue = wizardDraftSaveQueue.catch(function() {}).then(function() {
    if (epoch !== wizardDraftEpoch) return null;
    var existing = wizardDraftMeta;
    var creating = !existing;
    var path = existing ? "/drafts/" + encodeURIComponent(existing.draft_id) : "/drafts";
    if (existing) body.expected_revision = existing.revision;
    return api(path, {
      method: existing ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function(meta) {
      if (epoch !== wizardDraftEpoch) {
        // A stale POST created an orphan that is safe to remove. A stale PUT
        // updated the user's existing draft and must never delete it.
        if (creating) {
          return api("/drafts/" + encodeURIComponent(meta.draft_id), { method: "DELETE" }).then(function() { return null; });
        }
        return null;
      }
      wizardDraftMeta = meta;
      if (statusEl) statusEl.textContent = currentLang === "zh-CN" ? "草稿已保存" : "Draft saved";
      return meta;
    });
  }).catch(function(err) {
    if (statusEl && epoch === wizardDraftEpoch) {
      statusEl.textContent = (currentLang === "zh-CN" ? "草稿保存失败：" : "Draft save failed: ") + _wizardDraftErrorText(err);
    }
    throw err;
  });
  return wizardDraftSaveQueue;
}

function _scheduleWizardDraftAutosave() {
  if (window.ACPJobEditor && ACPJobEditor.isActive()) return;
  if (document.getElementById("job-modal").style.display === "none") return;
  if (wizardDraftAutosaveTimer) clearTimeout(wizardDraftAutosaveTimer);
  wizardDraftAutosaveTimer = setTimeout(function() {
    wizardDraftAutosaveTimer = null;
    _saveWizardDraft().catch(function(err) { console.warn("Draft autosave failed", err); });
  }, 1500);
}

function _discardSavedWizardDraft() {
  wizardDraftEpoch++;
  if (wizardDraftAutosaveTimer) clearTimeout(wizardDraftAutosaveTimer);
  wizardDraftAutosaveTimer = null;
  var old = wizardDraftMeta;
  wizardDraftMeta = null;
  wizardDraft = null;
  if (old) wizardDraftSaveQueue.catch(function() {}).then(function() {
    return api("/drafts/" + encodeURIComponent(old.draft_id), { method: "DELETE" });
  }).catch(function(err) {
    console.warn("Could not remove submitted draft", err);
  });
}

function _captureWizardDraft() {
  var modal = document.getElementById("job-modal");
  var fields = {};
  if (modal) modal.querySelectorAll("input[id], select[id], textarea[id]").forEach(function(el) {
    if (el.type === "file") return;
    fields[el.id] = el.type === "checkbox" || el.type === "radio"
      ? {checked:!!el.checked} : {value:el.value};
  });
  return {
    structures: wizardStructures.map(function(entry) { return JSON.parse(JSON.stringify(entry)); }),
    selectedIndex: wizardSelectedStructureIndex,
    activeTab: wizardInputMode,
    step: createWizardStep,
    fields: fields,
    workflowState: JSON.parse(JSON.stringify(wizardState)),
    // File objects cannot survive JSON storage. Parsed structures carry the
    // geometry needed for resuming an uploaded input.
    uploadFile: null,
    uploadName: wizardUploadFile ? wizardUploadFile.name : "",
    lastParsedStructureInput: lastParsedStructureInput,
    lastParsedUploadName: lastParsedUploadName,
    pesSelection: {
      mode: pesSelectionState.mode,
      selectionKind: pesSelectionState.selectionKind,
      coordinateKind: pesSelectionState.coordinateKind,
      selectedAtoms: pesSelectionState.selectedAtoms.slice(),
      structureKey: pesSelectionState.structureKey
    },
    scanSelection: {
      selectionKind: scanSelectionState.selectionKind,
      selectedAtoms: scanSelectionState.selectedAtoms.slice(),
      structureKey: scanSelectionState.structureKey
    },
    mechanism: {
      reactionDefinition: reactionDefinition ? JSON.parse(JSON.stringify(reactionDefinition)) : null,
      productStructure: mechProductStructure ? JSON.parse(JSON.stringify(mechProductStructure)) : null,
      tsGuessStructure: mechTsGuessStructure ? JSON.parse(JSON.stringify(mechTsGuessStructure)) : null,
      coordinatePlans: mechCoordinatePlans.map(cloneMechanismCoordinateSpec),
      studyId: mechStudyId || "",
      strategy: mechStrategy,
      fidelity: mechFidelity,
      conformerMode: mechConformerMode,
      maxSteps: mechMaxSteps,
      promotionPolicy: mechPromotionPolicy,
      scanPoints: mechScanPoints,
      ircPoints: mechIrcPoints,
      scanPointsDirty: mechScanPointsDirty,
      ircPointsDirty: mechIrcPointsDirty
    },
    restored: false
  };
}

function _restoreWizardDraft(draft) {
  if (!draft) return;
  if (draft.workflowState) {
    Object.keys(wizardState).forEach(function(key) { delete wizardState[key]; });
    Object.assign(wizardState, JSON.parse(JSON.stringify(draft.workflowState)));
  }
  Object.keys(draft.fields || {}).forEach(function(id) {
    var el = document.getElementById(id); var saved = draft.fields[id];
    if (!el || !saved) return;
    if (Object.prototype.hasOwnProperty.call(saved, "checked")) el.checked = !!saved.checked;
    if (Object.prototype.hasOwnProperty.call(saved, "value")) el.value = saved.value;
  });
  wizardUploadFile = draft.uploadFile || null;
  lastParsedStructureInput = draft.lastParsedStructureInput == null ? null : draft.lastParsedStructureInput;
  lastParsedUploadName = draft.lastParsedUploadName == null ? null : draft.lastParsedUploadName;
  if (draft.pesSelection) Object.assign(pesSelectionState, draft.pesSelection);
  if (draft.scanSelection) {
    scanSelectionState.selectionKind = draft.scanSelection.selectionKind || "distance";
    scanSelectionState.selectedAtoms = Array.isArray(draft.scanSelection.selectedAtoms)
      ? draft.scanSelection.selectedAtoms.slice() : [];
    scanSelectionState.structureKey = draft.scanSelection.structureKey || "";
    updateScanKindButtons(scanSelectionState.selectionKind);
  }
  var savedMechanism = draft.mechanism;
  if (savedMechanism && typeof savedMechanism === "object") {
    reactionDefinition = savedMechanism.reactionDefinition
      ? JSON.parse(JSON.stringify(savedMechanism.reactionDefinition))
      : createReactionDefinitionState();
    mechProductStructure = savedMechanism.productStructure
      ? JSON.parse(JSON.stringify(savedMechanism.productStructure)) : null;
    mechTsGuessStructure = savedMechanism.tsGuessStructure
      ? JSON.parse(JSON.stringify(savedMechanism.tsGuessStructure)) : null;
    mechCoordinatePlans = Array.isArray(savedMechanism.coordinatePlans)
      ? savedMechanism.coordinatePlans.map(cloneMechanismCoordinateSpec) : [];
    mechStudyId = typeof savedMechanism.studyId === "string" ? savedMechanism.studyId : "";
    mechStrategy = savedMechanism.strategy || "guided-scan";
    mechFidelity = savedMechanism.fidelity || "s3";
    mechConformerMode = savedMechanism.conformerMode || "censo-lite";
    mechMaxSteps = Number(savedMechanism.maxSteps) >= 1 ? Number(savedMechanism.maxSteps) : 3;
    mechPromotionPolicy = savedMechanism.promotionPolicy || "all_confirmed";
    mechScanPoints = Number(savedMechanism.scanPoints) >= 5 ? Number(savedMechanism.scanPoints) : 21;
    mechIrcPoints = Number(savedMechanism.ircPoints) >= 4 ? Number(savedMechanism.ircPoints) : 30;
    mechScanPointsDirty = !!savedMechanism.scanPointsDirty;
    mechIrcPointsDirty = !!savedMechanism.ircPointsDirty;
  }
  // ── legacy grid migration hint (T23f) ────────────────────────────────
  // Drafts saved before the ORCA-native grid migration (T12) can carry
  // legacy Gaussian grid aliases (e.g. UltraFine → DefGrid3). Migrate them
  // in place on hydration and surface the migration hint — never fail at
  // hydrate and never keep an ORCA-illegal value to blow up at submit.
  var gridMigrations = CatalogUtils.migrateLegacyGridInStates(
    methodCatalogCache, wizardState.method && wizardState.method.stages);
  if (gridMigrations.length) {
    var _gridHint = gridMigrations.map(function(m) {
      return m.field + ": " + m.from + " → " + m.to;
    }).join("; ");
    window.alert(t("draft.grid_migration_hint", { hint: _gridHint }));
  }
  updateConfigCards();
  if (wizardState.workflow && wizardState.workflow.id === "mechanism") {
    var mechPanel = document.getElementById("mech-builder-panel");
    if (mechPanel) mechPanel.style.display = "block";
    hookMechanismBuilder({ preserveState: true });
  }
  if (draft.uploadName && !(draft.structures || []).length) {
    window.alert(currentLang === "zh-CN"
      ? "草稿中的上传文件尚未解析成结构，请重新上传：" + draft.uploadName
      : "The uploaded file was not parsed into a structure. Please upload it again: " + draft.uploadName);
  }
}

async function _revalidateDraftSources() {
  var stale = 0;
  await Promise.all(wizardStructures.map(async function(entry) {
    if (!entry || !entry.source_uid) return;
    try {
      var current = await candidateApi("/structure-sources/" + encodeURIComponent(entry.source_uid));
      var oldVersion = entry.version_id || entry.geometry_hash || "";
      var newVersion = current.version_id || current.geometry_hash || "";
      if (oldVersion && newVersion && oldVersion !== newVersion) {
        entry.version_changed = true;
        stale++;
      }
    } catch (err) {
      if (err.status === 404) { entry.version_changed = true; stale++; }
    }
  }));
  if (stale) {
    renderLoadedStructuresList();
    window.alert((currentLang === "zh-CN" ? "草稿中有 " + stale + " 个来源已删除或版本变化，请重新确认输入。" : stale + " draft sources were removed or changed. Please review the inputs."));
  }
}

function openModal() {
  var draftToRestore = window._pendingNewTask ? null : wizardDraft;
  if (window._pendingNewTask) wizardDraftMeta = null;
  var draftButton = document.getElementById("wizard-drafts-open");
  if (draftButton) draftButton.textContent = currentLang === "zh-CN" ? "草稿箱" : "Drafts";
  if (draftToRestore) {
    wizardStructures = draftToRestore.structures || [];
    wizardSelectedStructureIndex = draftToRestore.selectedIndex || 0;
    draftToRestore.restored = true;
  } else {
    wizardStructures = [];
    wizardSelectedStructureIndex = 0;
  }
  wizardParseWarnings = [];
  var previewShell = document.getElementById("structure-preview");
  if (previewShell) previewShell.classList.remove("preview-expanded");
  var previewExpandBtn = document.getElementById("preview-expand-view");
  if (previewExpandBtn) {
    previewExpandBtn.setAttribute("data-i18n", "edit.expand_preview");
    previewExpandBtn.textContent = t("edit.expand_preview");
    previewExpandBtn.setAttribute("aria-pressed", "false");
  }
  wizardState.s2_mode = "path_search";
  s2scanResetState();
  resetPESSelection();
  ensureScanSelectionListeners();
  s2scanCloseReport();
  document.getElementById("parse-errors").style.display = "none";
  document.getElementById("parse-status").textContent = "";
  document.getElementById("modal-structure-input").value = "";
  lastParsedStructureInput = null;
  lastParsedUploadName = null;
  setDetectedFormatChip(null);
  renderPreviewWaiting();
  document.getElementById("upload-file-input").value = "";
  document.getElementById("charge-spin-row").style.display = "none";
  document.getElementById("modal-charge").value = "0";
  document.getElementById("modal-mult").value = "1";
  document.getElementById("modal-remark").value = "";
  document.getElementById("modal-task-name").value = "";
  document.getElementById("modal-parallelism").value = "1";
  if (!(window.ACPJobEditor && ACPJobEditor.isActive())) {
    ACPMemoryInput.set("modal-mem", "modal-mem-unit", "8GB");
  }
  wizardUploadFile = null;
  var dzSpan = document.querySelector(".upload-dropzone span");
  if (dzSpan) dzSpan.textContent = t("modal.upload_hint");
  resetResultsPanel();
  resetCandidatePanel();
  setWizardInputMode((draftToRestore && draftToRestore.activeTab) || "task");
  updateConfigCards();
  renderModalProjects();
  setModalProjectDefault();
  renderResultsProjectOptions();
  // Mechanism builder reset: cleared per open so stale plans/routes never leak.
  mechProductStructure = null;
  mechTsGuessStructure = null;
  mechCoordinatePlans = [];
  mechStudyId = generateMechanismStudyId();
  mechStrategy = "guided-scan";
  mechFidelity = "s3";
  mechConformerMode = "censo-lite";
  mechMaxSteps = 3;
  mechPromotionPolicy = "all_confirmed";
  mechScanPoints = 21;
  mechIrcPoints = 30;
  mechScanPointsDirty = false;
  mechIrcPointsDirty = false;
  reactionDefinition = createReactionDefinitionState();
  // Manual bond edit mode reset: fresh state already clears editMode and
  // manualEdits; also drop the transient pick buffer and viewer refs.
  reactionManualPick = null;
  reactionHoverShape = null;
  reactionModel = null;
  var mechPanel = document.getElementById("mech-builder-panel");
  if (mechPanel) mechPanel.style.display = "none";
  var mechPlan = document.getElementById("mech-plan-panel");
  if (mechPlan) mechPlan.innerHTML = "";
  var mechReactionBody = document.getElementById("mech-reaction-body");
  if (mechReactionBody) mechReactionBody.innerHTML = "";
  var mechReactionEmpty = document.getElementById("mech-reaction-empty");
  if (mechReactionEmpty) mechReactionEmpty.style.display = "block";
  // Reaction-changes 3D viewer reset (P1): drop stale models/shapes/labels,
  // hide the viewer wrap and restore the default reactant view mode.
  disposeViewerFraming(reactionViewerFraming);
  disposeViewerFraming(previewViewerFraming);
  reactionChangesViewMode = "reactant";
  updateReactionViewToggleButtons();
  var mechReaction3dWrap = document.getElementById("mech-reaction-3d-wrap");
  if (mechReaction3dWrap) mechReaction3dWrap.style.display = "none";
  if (reactionViewer) {
    try {
      reactionViewer.removeAllModels();
      reactionViewer.removeAllShapes();
      reactionViewer.removeAllLabels();
      reactionViewer.render();
    } catch (e) {}
  }
  var mechStatsBar = document.getElementById("mech-change-stats");
  if (mechStatsBar) mechStatsBar.innerHTML = "";
  renderReactionDefinitionPanel();
  // Reset NMR enumerate toggle on each open (P2) and register its change
  // listener once (guarded so repeated opens don't stack handlers).
  var enumToggle = document.getElementById("nmr-enumerate-toggle");
  if (enumToggle) {
    enumToggle.checked = false;
    var centersInp = document.getElementById("nmr-enumerate-centers");
    if (centersInp) centersInp.value = "";
    if (!enumToggle._acpWired) {
      enumToggle._acpWired = true;
      enumToggle.addEventListener("change", function() {
        var extra = document.getElementById("nmr-enumerate-extra");
        if (extra) extra.style.display = enumToggle.checked ? "block" : "none";
      });
    }
  }
  // Reset atom label toggle state
  wizardAtomLabelsOn = false;
  var atomBtn = document.getElementById("preview-atom-numbers");
  if (atomBtn) { atomBtn.classList.remove("active"); atomBtn.setAttribute("aria-pressed", "false"); }
  // Reset small-screen toggle
  var shell = document.getElementById("structure-preview");
  if (shell) { shell.classList.remove("wizard-show-detail"); shell.classList.add("wizard-show-list"); }
  updateFooterSelectedCount();
  var modal = document.getElementById("job-modal");
  if (window.ACPJobEditor && ACPJobEditor.isActive()) {
    // Editing uses the full form independently of the saved create-wizard step.
    if (modal) modal.setAttribute("data-create-step", "1");
  } else {
    setCreateWizardStep((draftToRestore && draftToRestore.step) || 1);
  }
  // Mount candidate details panel (right column, §5.4)
  var cdHost = document.getElementById("candidate-details-host");
  if (cdHost && typeof ACPCandidateDetails !== "undefined") {
    if (candidateDetailsPanel) { candidateDetailsPanel.destroy(); candidateDetailsPanel = null; }
    candidateDetailsPanel = ACPCandidateDetails.mount(cdHost, {
      t: t,
      candidateApi: candidateApi,
      onSaved: function(source) {
        wizardStructures.forEach(function(entry) {
          if (entry.source_uid === source.source_uid && !entry.input_modified) entry.name = source.resolved_name || source.custom_name || entry.name;
        });
        if (resultsPicker) resultsPicker.refresh();
        if (candidatePicker) candidatePicker.refresh();
        renderLoadedStructuresList();
      },
      onOpenJob: function(jobId) { if (!jobId) return; closeJobModalGuarded(); selectJob(jobId); }
    });
  }
  resetNodeSelector();
  _restoreWizardDraft(draftToRestore);
  if (draftToRestore && draftToRestore.step === 3) renderCreateReview();
  renderLoadedStructuresList();
  if (draftToRestore && wizardStructures.length) {
    renderStructurePreview(wizardStructures, wizardParseWarnings);
    _syncPickerSelection();
    _loadPendingGeometry();
    _revalidateDraftSources();
  }
  document.getElementById("job-modal").style.display = "flex";
  updatePESSelectionVisibility();
  if (!draftToRestore) applyPendingNewTask();
  if (draftToRestore && !wizardStructures.length && wizardInputMode === "structure" &&
      document.getElementById("modal-structure-input").value.trim()) {
    // A draft can be captured while parsing is still in flight. Re-parse its
    // saved text on resume when no parsed geometry made it into the snapshot.
    scheduleParseStructures();
  }
  // Node selector: fresh tag selection per open; refresh fires after the
  // modal is visible (refreshNodeMatching no-ops while it is hidden).
  scheduleNodeMatchingRefresh();
}

function closeModal() {
  _closeInputListOverlay();
  if (candidateDetailsPanel) { candidateDetailsPanel.destroy(); candidateDetailsPanel = null; }
  document.getElementById("job-modal").style.display = "none";
}

var createWizardStep = 1;
function renderCreateReview() {
  var target = document.getElementById("create-review-summary");
  if (!target) return;
  var projectSelect = document.getElementById("modal-project-select");
  var projectName = projectSelect && projectSelect.selectedOptions.length
    ? projectSelect.selectedOptions[0].textContent : "--";
  var workflowName = (wizardState.workflow && wizardState.workflow.label) || "--";
  var protocolName = (wizardState.method && wizardState.method.profile_label) || t("wizard.review_custom");
  var nproc = document.getElementById("modal-nproc").value || "--";
  var memory = ACPMemoryInput.format("modal-mem", "modal-mem-unit") || "--";
  var parallelism = document.getElementById("modal-parallelism").value || "1";
  var reviewFlags = wizardStructures.map(function(s) {
    if (s.usage_status && s.usage_status !== "active") return {kind:"failed", label:t("wizard.review_inactive_short")};
    if (s.version_changed) return {kind:"warn", label:t("wizard.review_version_changed")};
    var geometry = s.geometry_state || "ready";
    if (geometry === "failed") return {kind:"failed", label:t("wizard.review_geometry_failed")};
    if (geometry === "pending" || geometry === "loading") return {kind:"warn", label:t("wizard.review_geometry_pending")};
    return {kind:"ready", label:t("wizard.review_ready")};
  });
  var flaggedCount = reviewFlags.filter(function(item) { return item.kind !== "ready"; }).length;
  var statusHtml = flaggedCount
    ? '<span class="create-review-status warn" title="' + escapeAttr(t("wizard.review_risk")) + '">' + t("wizard.review_attention_count", {count: flaggedCount}) + '</span>'
    : '<span class="create-review-status" title="' + escapeAttr(t("wizard.review_risk")) + '">' + t("wizard.review_inputs_ready") + '</span>';
  var rows = wizardStructures.map(function(s, i) {
    var source = s.source_ref || {};
    var sourceKind = s.source_kind || "";
    var sourceKindLabel = sourceKind === "saved_candidate" ? t("wizard.source_candidate")
      : sourceKind === "task_result" ? t("wizard.source_task_result")
      : sourceKind === "upload" ? t("wizard.source_upload")
      : sourceKind === "last_structure" ? t("wizard.source_last_structure")
      : t("wizard.source_manual");
    var sourceJobLabel = s.job_resolved_name || (source && (source.job_resolved_name || source.job_name)) || s.source_job_name || source.job_id;
    var sourceName = source.job_id
      ? (sourceJobLabel + (source.path ? " · " + source.path : ""))
      : sourceKindLabel;
    var rawRole = String(s.tag || s.role || "");
    var role = rawRole === "TS" ? "TS" : rawRole === "INT" ? "INT" : t("wizard.review_role_normal");
    var roleClass = rawRole === "TS" ? " ts" : rawRole === "INT" ? " int" : "";
    var state = reviewFlags[i];
    var charge = s.charge === undefined || s.charge === null ? 0 : s.charge;
    var multiplicity = s.multiplicity || 1;
    return '<tr>' +
      '<td class="create-review-index">' + (i + 1) + '</td>' +
      '<td class="create-review-structure" title="' + escapeAttr(s.name || s.asset_name || ("mol_" + (i + 1))) + '">' + escapeHtml(s.name || s.asset_name || ("mol_" + (i + 1))) + '</td>' +
      '<td class="create-review-source" title="' + escapeAttr(sourceName) + '">' + escapeHtml(sourceName) + '</td>' +
      '<td><span class="create-review-role' + roleClass + '">' + escapeHtml(role) + '</span></td>' +
      '<td>' + escapeHtml("q " + charge + " · M " + multiplicity) + '</td>' +
      '<td><span class="create-review-state ' + state.kind + '">' + escapeHtml(state.label) + '</span></td>' +
      '</tr>';
  }).join("");
  target.innerHTML =
    '<div class="create-review-heading"><div><p class="create-review-subtitle">' + t("wizard.review_intro") + '</p></div>' + statusHtml + '</div>' +
    '<div class="create-review-stats">' +
      '<div class="create-review-stat"><div class="create-review-stat-label">' + t("wizard.review_project") + '</div><div class="create-review-stat-value" title="' + escapeAttr(projectName) + '">' + escapeHtml(projectName) + '</div></div>' +
      '<div class="create-review-stat"><div class="create-review-stat-label">' + t("wizard.review_workflow") + '</div><div class="create-review-stat-value" title="' + escapeAttr(workflowName) + '">' + escapeHtml(workflowName) + '</div></div>' +
      '<div class="create-review-stat"><div class="create-review-stat-label">' + t("wizard.review_protocol") + '</div><div class="create-review-stat-value" title="' + escapeAttr(protocolName) + '">' + escapeHtml(protocolName) + '</div></div>' +
      '<div class="create-review-stat"><div class="create-review-stat-label">' + t("wizard.review_resources") + '</div><div class="create-review-stat-value">' + escapeHtml(nproc + " " + t("wizard.review_cores") + " · " + memory) + '</div><div class="create-review-stat-meta">' + t("wizard.review_parallelism", {count: parallelism}) + '</div></div>' +
    '</div>' +
    '<section class="create-review-section" aria-label="' + escapeAttr(t("wizard.review_input")) + '">' +
      '<div class="create-review-section-head"><span class="create-review-section-title">' + t("wizard.review_input") + '</span><span class="create-review-section-note">' + t("wizard.review_input_count", {count: wizardStructures.length}) + '</span></div>' +
      (rows ? '<div class="create-review-table-wrap"><table class="create-review-table"><thead><tr>' +
        '<th>' + t("wizard.review_index") + '</th><th>' + t("wizard.review_structure") + '</th><th>' + t("wizard.review_source") + '</th><th>' + t("wizard.review_role") + '</th><th>' + t("wizard.review_charge_spin") + '</th><th>' + t("wizard.review_state") + '</th>' +
        '</tr></thead><tbody>' + rows + '</tbody></table></div>' : '<div class="create-review-empty">' + t("wizard.review_no_input") + '</div>') +
    '</section>';
}

function renderStepCompactSummary() {
  var summary = document.getElementById("wizard-step-summary");
  var inner = document.getElementById("wizard-step-summary-inner");
  if (!summary || !inner) return;
  if (createWizardStep < 2) { summary.style.display = "none"; return; }
  summary.style.display = "flex";
  var count = wizardStructures.length;
  var names = wizardStructures.map(function(s) { return s.name || s.asset_name || "--"; });
  var listHtml = names.slice(0, 20).map(function(n) {
    return '<div class="wizard-step-summary-item">' + escapeHtml(n) + '</div>';
  }).join("");
  if (names.length > 20) listHtml += '<div class="wizard-step-summary-item muted">… ' + (names.length - 20) + ' ' + t("wizard.summary_more") + '</div>';
  inner.innerHTML =
    '<div class="wizard-step-summary-label">' + t("wizard.summary_step1_selection") + '</div>' +
    '<div class="wizard-step-summary-count">' + count + '</div>' +
    '<div class="wizard-step-summary-label">' + (count === 1 ? t("wizard.summary_structure") : t("wizard.summary_structures")) + '</div>' +
    (listHtml ? '<div class="wizard-step-summary-list">' + listHtml + '</div>' : '') +
    '<div class="wizard-step-summary-back">' +
    '<button type="button" class="btn ghost" id="wizard-step-summary-back-btn" aria-label="' + escapeHtml(t("wizard.summary_back")) + '">' + t("wizard.summary_back") + '</button>' +
    '</div>';
  var backBtn = document.getElementById("wizard-step-summary-back-btn");
  if (backBtn) backBtn.addEventListener("click", function() { setCreateWizardStep(1); });
}

function _syncCreateStepButton(btn) {
  var buttonStep = Number(btn.getAttribute("data-create-step"));
  btn.classList.toggle("active", buttonStep === createWizardStep);
  btn.classList.toggle("completed", buttonStep < createWizardStep);
  if (buttonStep === createWizardStep) btn.setAttribute("aria-current", "step");
  else btn.removeAttribute("aria-current");
}

function setCreateWizardStep(step) {
  var modal = document.getElementById("job-modal");
  if (!modal || (window.ACPJobEditor && ACPJobEditor.isActive())) return;
  if (Number(step) > 1 && !wizardStructures.length) return;
  _closeInputListOverlay();
  createWizardStep = Math.max(1, Math.min(3, Number(step) || 1));
  modal.setAttribute("data-create-step", String(createWizardStep));
  modal.querySelectorAll(".create-step").forEach(_syncCreateStepButton);
  updatePESSelectionVisibility();
  var back = document.getElementById("modal-back");
  var primary = document.getElementById("modal-submit");
  if (back) back.style.display = createWizardStep > 1 ? "inline-flex" : "none";
  if (primary) primary.textContent = createWizardStep === 3 ? t("wizard.submit_task") : t("wizard.next_step");
  if (primary) primary.disabled = createWizardStep === 1 && wizardStructures.length === 0;
  updateFooterSelectedCount();
  if (createWizardStep === 3) renderCreateReview();
  renderStepCompactSummary();
  var body = modal.querySelector(".modal-body");
  if (body) body.scrollTop = 0;
  if (previewViewer && createWizardStep < 3) {
    requestAnimationFrame(function() {
      try { previewViewer.resize(); previewViewer.render(); } catch (e) {}
    });
  }
}

// Closing with an edit draft in flight routes through ACPJobEditor so a
// dirty draft asks 留在页面/放弃草稿 instead of silently vanishing (plan §4.2).
async function closeJobModalGuarded() {
  if (window.ACPJobEditor && ACPJobEditor.isActive()) {
    ACPJobEditor.cancelEditor(false);
    return;
  }
  if (wizardDraftAutosaveTimer) clearTimeout(wizardDraftAutosaveTimer);
  wizardDraftAutosaveTimer = null;
  try {
    await _saveWizardDraft();
  } catch (err) {
    window.alert((currentLang === "zh-CN" ? "草稿保存失败，窗口仍保持打开：" : "Draft save failed; the window is still open: ") + _wizardDraftErrorText(err));
    return;
  }
  closeModal();
}

async function openWizardDrafts() {
  if (window.ACPJobEditor && ACPJobEditor.isActive()) return;
  var overlay = document.getElementById("wizard-drafts-modal");
  var list = document.getElementById("wizard-drafts-list");
  if (!overlay || !list) return;
  document.getElementById("wizard-drafts-title").textContent = currentLang === "zh-CN" ? "草稿箱" : "Drafts";
  document.getElementById("wizard-drafts-new").textContent = currentLang === "zh-CN" ? "新建空白" : "New blank task";
  overlay.style.display = "flex";
  list.textContent = currentLang === "zh-CN" ? "正在加载…" : "Loading…";
  try {
    var data = await api("/drafts");
    list.innerHTML = "";
    if (!data.items || !data.items.length) {
      list.textContent = currentLang === "zh-CN" ? "暂无草稿" : "No saved drafts";
      return;
    }
    data.items.forEach(function(item) {
      var row = document.createElement("div");
      row.style.cssText = "display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--border-subtle,#333);";
      var info = document.createElement("div");
      info.style.cssText = "flex:1;min-width:0;";
      var title = document.createElement("strong");
      title.textContent = item.name || (currentLang === "zh-CN" ? "未命名草稿" : "Untitled draft");
      var detail = document.createElement("div");
      detail.className = "muted";
      detail.style.fontSize = "11px";
      detail.textContent = [item.workflow || "", String(item.structure_count || 0) + (currentLang === "zh-CN" ? " 个结构" : " structures"), new Date(item.updated_at).toLocaleString()].filter(Boolean).join(" · ");
      info.appendChild(title); info.appendChild(detail);
      var resume = document.createElement("button");
      resume.type = "button"; resume.className = "btn primary";
      resume.textContent = currentLang === "zh-CN" ? "继续" : "Resume";
      resume.addEventListener("click", async function() {
        try {
          if (wizardDraftAutosaveTimer) clearTimeout(wizardDraftAutosaveTimer);
          wizardDraftAutosaveTimer = null;
          // Flush the open editor even when it is the same draft. Otherwise a
          // change inside the debounce window is lost when Resume reloads it.
          if (document.getElementById("job-modal").style.display !== "none") await _saveWizardDraft();
          await wizardDraftSaveQueue.catch(function() {});
          var saved = await api("/drafts/" + encodeURIComponent(item.draft_id));
          wizardDraft = saved.snapshot;
          wizardDraftMeta = saved;
          var status = document.getElementById("wizard-draft-status");
          if (status) status.textContent = currentLang === "zh-CN" ? "草稿已恢复" : "Draft restored";
          overlay.style.display = "none";
          openModal();
        } catch (err) { window.alert(_wizardDraftErrorText(err)); }
      });
      var remove = document.createElement("button");
      remove.type = "button"; remove.className = "btn ghost";
      remove.textContent = currentLang === "zh-CN" ? "删除" : "Delete";
      remove.addEventListener("click", async function() {
        if (!window.confirm(currentLang === "zh-CN" ? "删除这份草稿？" : "Delete this draft?")) return;
        try {
          var removingCurrent = !!(wizardDraftMeta && wizardDraftMeta.draft_id === item.draft_id);
          if (removingCurrent) {
            if (wizardDraftAutosaveTimer) clearTimeout(wizardDraftAutosaveTimer);
            wizardDraftAutosaveTimer = null;
            wizardDraftEpoch++;
            wizardDraftMeta = null;
            wizardDraft = null;
            await wizardDraftSaveQueue.catch(function() {});
          }
          await api("/drafts/" + encodeURIComponent(item.draft_id), { method: "DELETE" });
          if (removingCurrent) {
            var defaultWorkflow = resolveDefaultWorkflow("");
            if (defaultWorkflow) {
              wizardState.workflow = {
                id: defaultWorkflow.id, label: defaultWorkflow.label,
                schema_id: canonicalMethodSchemaId(defaultWorkflow.id, defaultWorkflow.method_schema_id)
              };
              loadDefaultMethodProfile(wizardState.workflow.schema_id);
            }
            openModal();
          }
          row.remove();
          if (!list.children.length) list.textContent = currentLang === "zh-CN" ? "暂无草稿" : "No saved drafts";
        } catch (err) { window.alert(_wizardDraftErrorText(err)); }
      });
      row.appendChild(info); row.appendChild(resume); row.appendChild(remove);
      list.appendChild(row);
    });
  } catch (err) {
    list.textContent = (currentLang === "zh-CN" ? "加载失败：" : "Could not load drafts: ") + _wizardDraftErrorText(err);
  }
}

async function startBlankWizardDraft() {
  try {
    if (wizardDraftAutosaveTimer) clearTimeout(wizardDraftAutosaveTimer);
    wizardDraftAutosaveTimer = null;
    if (document.getElementById("job-modal").style.display !== "none") await _saveWizardDraft();
    wizardDraftEpoch++;
    wizardDraft = null;
    wizardDraftMeta = null;
    var status = document.getElementById("wizard-draft-status");
    if (status) status.textContent = "";
    var defaultWorkflow = resolveDefaultWorkflow("");
    if (defaultWorkflow) {
      wizardState.workflow = {
        id: defaultWorkflow.id,
        label: defaultWorkflow.label,
        schema_id: canonicalMethodSchemaId(defaultWorkflow.id, defaultWorkflow.method_schema_id)
      };
      loadDefaultMethodProfile(wizardState.workflow.schema_id);
    }
    document.getElementById("wizard-drafts-modal").style.display = "none";
    openModal();
  } catch (err) { window.alert(err.message); }
}

function setWizardInputMode(mode) {
  // Pre-0.3 drafts used a separate candidate tab. Both library projections now
  // live under the task mode and are selected with the in-library filters.
  if (mode === "candidate") mode = "task";
  wizardInputMode = mode;
  document.querySelectorAll(".input-mode-tab").forEach(function(tab) {
    var active = tab.getAttribute("data-input-mode") === mode;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  var taskPanel = document.getElementById("input-panel-task");
  if (taskPanel) taskPanel.style.display = mode === "task" ? "flex" : "none";
  document.getElementById("input-panel-structure").style.display = mode === "structure" ? "flex" : "none";
  document.getElementById("input-panel-upload").style.display = mode === "upload" ? "flex" : "none";
  var parseMeta = document.getElementById("source-parse-meta");
  if (parseMeta) parseMeta.style.display = mode === "task" ? "none" : "flex";
  var browser = document.getElementById("task-results-browser");
  if (browser) browser.style.display = mode === "task" ? "flex" : "none";
  var sourceShell = document.getElementById("structure-preview");
  if (sourceShell) sourceShell.setAttribute("data-source-mode", mode);
  var toolbarHost = document.getElementById("tiw-toolbar-host");
  if (toolbarHost) toolbarHost.style.display = mode === "task" ? "flex" : "none";
  if (toolbarHost) {
    toolbarHost.querySelectorAll(".sp-toolbar").forEach(function(toolbar) {
      toolbar.style.display = mode === "task" ? "flex" : "none";
    });
  }
  var listHeader = document.getElementById("tiw-list-header");
  if (listHeader) listHeader.style.display = mode === "task" ? "grid" : "none";
  // Invalidate any in-flight parse from the previous tab (slow-response race).
  parseSeqToken++;
  if (parseDebounceTimer) { clearTimeout(parseDebounceTimer); parseDebounceTimer = null; }
  if (mode === "task") loadStructureSources(false);
  if (mode === "task" && wizardStructures.length === 0) {
    var taskEmptyEl = document.getElementById("preview-3d-empty");
    if (taskEmptyEl && taskEmptyEl.style.display !== "none") taskEmptyEl.textContent = t("wizard.select_hint");
  }
  if (window.ACPJobEditor && ACPJobEditor.isActive()) {
    ACPJobEditor.onSourceTabChange(mode);
  }
  updatePreviewActionState();
  if (mode === "task") return;
  // Parse-race fix: the token bump above cancels any parse still in flight,
  // so returning to a tab whose input was never applied must re-parse or
  // the preview stays stuck on "等待输入" forever.
  if (mode === "structure") {
    var txt = document.getElementById("modal-structure-input").value;
    if (txt.trim() && txt !== lastParsedStructureInput) scheduleParseStructures();
  } else if (mode === "upload" && wizardUploadFile && wizardUploadFile.name !== lastParsedUploadName) {
    scheduleParseStructures();
  }
}

var parseSeqToken = 0;
var parseDebounceTimer = null;
// Content/name of the last parse whose result was actually applied. Null
// means the current input has no applied parse result yet.
var lastParsedStructureInput = null;
var lastParsedUploadName = null;

function scheduleParseStructures() {
  if (parseDebounceTimer) clearTimeout(parseDebounceTimer);
  parseDebounceTimer = setTimeout(function() {
    parseDebounceTimer = null;
    parseStructuresPreview();
  }, 350);
}

function setDetectedFormatChip(fmt) {
  var chip = document.getElementById("detected-format-chip");
  if (!chip) return;
  if (!fmt) {
    chip.style.display = "none";
    return;
  }
  chip.textContent = t("parse.detected", { format: String(fmt).toUpperCase() });
  chip.style.display = "inline-flex";
}

function renderPreviewWaiting() {
  _refreshWizardInputForm(null);
  var container = document.getElementById("structure-preview");
  var emptyEl = document.getElementById("preview-3d-empty");
  if (emptyEl) {
    emptyEl.textContent = wizardInputMode === "task" ? t("wizard.select_hint") : t("parse.no_3d_waiting");
    emptyEl.title = "";
    emptyEl.style.display = "grid";
  }
  if (container) container.style.display = "grid";
  var title = document.getElementById("preview-structure-title");
  if (title) title.textContent = t("modal.preview_title");
  var coords = document.getElementById("source-coordinate-text");
  if (coords) coords.textContent = "";
  updatePreviewActionState();
  if (previewViewer) {
    try {
      previewViewer.removeAllModels();
      previewModel = null;
      clearPESPreviewSelectionShapes();
      previewViewer.render();
    } catch (e) {}
  }
  resetPESSelection();
}

async function parseStructuresPreview() {
  var content = "";
  var fmt = "auto";
  var filename = "";

  if (wizardInputMode === "structure") {
    content = document.getElementById("modal-structure-input").value;
  } else if (wizardInputMode === "upload") {
    if (!wizardUploadFile) {
      showParseStatus(t("modal.select_file_first") || "请先选择文件", true);
      return;
    }
    filename = wizardUploadFile.name;
  } else {
    // "results" mode: nothing to parse from text input.
    return;
  }

  var myToken = ++parseSeqToken;

  if (wizardInputMode === "structure" && !content.trim()) {
    wizardStructures = [];
    wizardParseWarnings = [];
    setDetectedFormatChip(null);
    showParseStatus(t("parse.waiting"), false);
    renderPreviewWaiting();
    return;
  }

  try {
    showParseStatus(t("parse.parsing"), false);
    if (wizardInputMode === "upload" && wizardUploadFile) {
      var formData = new FormData();
      formData.append("file", wizardUploadFile);
      var uploadBody;
      try {
        uploadBody = await window.ACPClient.wizardEndpoints.uploadStructure(formData);
      } catch (e) {
        // Legacy derivation preserved: the adapter re-derives the raw
        // `body.detail` from the parsed error body onto err.detail (it may be
        // a non-string, exactly like legacy); the "Upload failed" literal
        // stays here exactly like the pre-migration call site.
        if (myToken !== parseSeqToken) return;
        showParseStatus(e.detail || "Upload failed", true);
        return;
      }
      if (myToken !== parseSeqToken) return;
      if (wizardUploadFile) lastParsedUploadName = wizardUploadFile.name;
      wizardStructures = uploadBody.structures || [];
      setDetectedFormatChip(uploadBody.detected_format);
      handleParseResult(uploadBody);
      return;
    }

    // Legacy called fetch() + resp.json() with NO resp.ok check: a JSON error
    // body flowed through the ok:false presentation path. The adapter
    // reproduces that (resolves with the parsed error body).
    var body = await window.ACPClient.wizardEndpoints.parseStructure({
      content: content, format: fmt, filename: filename
    });
    if (myToken !== parseSeqToken) return;
    if (body.ok) lastParsedStructureInput = content;
    wizardStructures = body.structures || [];
    setDetectedFormatChip(body.detected_format);
    handleParseResult(body);
  } catch (e) {
    if (myToken !== parseSeqToken) return;
    showParseStatus(e.message, true);
  }
}

function handleParseResult(body) {
  wizardParseWarnings = body.warnings || [];
  var errBox = document.getElementById("parse-errors");
  if (body.errors && body.errors.length) {
    errBox.style.display = "block";
    errBox.innerHTML = body.errors.map(function(e) { return "<div>" + escapeHtml(e) + "</div>"; }).join("");
  } else {
    errBox.style.display = "none";
  }

  var csRow = document.getElementById("charge-spin-row");
  if (wizardStructures.length > 0) {
    var allSameCharge = true, allSameMult = true;
    var refCharge = wizardStructures[0].charge, refMult = wizardStructures[0].multiplicity;
    for (var i = 1; i < wizardStructures.length; i++) {
      if (wizardStructures[i].charge !== refCharge) allSameCharge = false;
      if (wizardStructures[i].multiplicity !== refMult) allSameMult = false;
    }
    document.getElementById("modal-charge").value = (allSameCharge && refCharge !== undefined) ? refCharge : 0;
    document.getElementById("modal-mult").value = (allSameMult && refMult !== undefined) ? refMult : 1;
    csRow.style.display = "flex";
  } else {
    csRow.style.display = "none";
  }

  renderStructurePreview(wizardStructures, wizardParseWarnings);
  if (body.ok && wizardStructures.length && window.ACPJobEditor && ACPJobEditor.isActive()) {
    ACPJobEditor.onParsedSourceApplied(wizardInputMode, wizardStructures);
  }
  showParseStatus(
    body.ok ? t("parse.success", { count: wizardStructures.length }) : t("parse.failed"),
    !body.ok
  );

  if (wizardState.workflow && wizardState.workflow.id === "mechanism") {
    var mechPanel = document.getElementById("mech-builder-panel");
    if (mechPanel) mechPanel.style.display = "block";
    hookMechanismBuilder({ preserveState: true });
  }
   if (isPESsearchTask()) {
     syncPESSelectionFromWizard(true);
     attachPESPreviewSelection();
   }
   _scheduleWizardDraftAutosave();
}

function showParseStatus(msg, isError) {
  var el = document.getElementById("parse-status");
  el.textContent = msg;
  el.className = "parse-status " + (isError ? "err" : "ok");
}

function renderStructurePreview(structures, warnings) {
  var container = document.getElementById("structure-preview");

  if (!structures.length) {
    renderPreviewWaiting();
    return;
  }

  structures.forEach(function(s, i) {
    var moleculeName = String(s.name || ("mol_" + (i + 1))).trim();
    if (!moleculeName) moleculeName = "mol_" + (i + 1);
    s.name = moleculeName;
  });

  container.style.display = "grid";
  initPreviewViewer();
  selectPreviewStructure(wizardSelectedStructureIndex, false);
  renderLoadedStructuresList();
  if (wizardState.workflow && wizardState.workflow.id === "mechanism") {
    renderReactionDefinitionPanel();
  }
  updatePreviewActionState();
}

function selectPreviewStructure(index, notifyEditor) {
  structureSourceLoadToken++;
  if (!Array.isArray(wizardStructures) || !wizardStructures.length) return;
  wizardSelectedStructureIndex = Math.max(0, Math.min(Number(index) || 0, wizardStructures.length - 1));
  var structure = wizardStructures[wizardSelectedStructureIndex];
  _refreshWizardInputForm(structure);
  renderPreviewStructure3D(structure);
  renderLoadedStructuresList();
  updateFooterSelectedCount();
  if (notifyEditor !== false && window.ACPJobEditor && ACPJobEditor.isActive()) {
    ACPJobEditor.onPreviewStructureSelected(structure);
  }
  if (isPESsearchTask()) {
    syncPESSelectionFromWizard(true);
    attachPESPreviewSelection();
  }
}

function _refreshWizardInputForm(structure, previewOnly) {
  if (structure && !structure.input_id) structure.input_id = "inp_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2);

  ["wif-name", "wif-role", "wif-charge", "wif-mult"].forEach(function(id) {
    var input = document.getElementById(id);
    if (input) input.disabled = !structure || !!previewOnly;
  });
  var infoPanel = document.getElementById("tiw-info-panel");
  if (infoPanel) infoPanel.hidden = !structure;
  var sourceAction = document.getElementById("wif-open-source");
  if (sourceAction) {
    var sourceJobId = structure && structure.source_ref && structure.source_ref.job_id;
    sourceAction.hidden = !sourceJobId;
    sourceAction.onclick = sourceJobId ? function() {
      closeJobModalGuarded();
      selectJob(sourceJobId);
    } : null;
  }
  var copyAction = document.getElementById("wif-copy-candidate");
  if (copyAction) copyAction.onclick = async function() {
    var value = structure && structure.candidate_id;
    if (!value) return;
    try { await navigator.clipboard.writeText(value); _showSimpleToast(t("wizard.id_copied"), false); }
    catch (error) { _showSimpleToast(error.message || "Copy failed", true); }
  };

  var nameInput = document.getElementById("wif-name");
  var roleSelect = document.getElementById("wif-role");
  var chargeInput = document.getElementById("wif-charge");
  var multInput = document.getElementById("wif-mult");
  var sourceSpan = document.getElementById("wif-source");
  var candIdSpan = document.getElementById("wif-candidate-id");
  var sourceRow = document.getElementById("wif-source-row");
  var candidateRow = document.getElementById("wif-candidate-row");
  if (!nameInput) return;
  if (!structure) {
    nameInput.value = ""; if (roleSelect) roleSelect.value = "";
    if (chargeInput) chargeInput.value = "0"; if (multInput) multInput.value = "1";
    if (sourceSpan) { sourceSpan.textContent = ""; sourceSpan.removeAttribute("title"); }
    if (candIdSpan) { candIdSpan.textContent = ""; candIdSpan.removeAttribute("title"); }
    if (sourceRow) sourceRow.hidden = true; if (candidateRow) candidateRow.hidden = true;
    return;
  }
  nameInput.dataset.inputId = structure.input_id;
  var desiredName = structure.name || structure.molecule_name || "";
  if (nameInput.value !== desiredName) nameInput.value = desiredName;
  if (roleSelect) roleSelect.value = structure.tag || structure.role || "";
  if (chargeInput) chargeInput.value = structure.charge !== undefined ? structure.charge : 0;
  if (multInput) multInput.value = structure.multiplicity || 1;
  var src = structure.source_ref || {};
  var sourceJobLabel = structure.job_resolved_name || (src && (src.job_resolved_name || src.job_name)) || structure.source_job_name || src.job_id;
  var sourceText = src.job_id ? (sourceJobLabel + (src.path ? " · " + src.path : "")) : (t("wizard.source_manual") || "手动输入");
  var candidateText = structure.candidate_id || "";
  if (sourceSpan) { sourceSpan.textContent = sourceText; sourceSpan.title = sourceText; }
  if (candIdSpan) { candIdSpan.textContent = candidateText; candIdSpan.title = candidateText; }
  if (sourceRow) sourceRow.hidden = !sourceText;
  if (candidateRow) candidateRow.hidden = !candidateText;
}

function escapeAttr(value) { return escapeHtml(String(value == null ? "" : value)); }

function updateFooterSelectedCount() {
  var countEl = document.getElementById("footer-selected-count");
  var inputBtn = document.getElementById("footer-input-count");
  var inputBadge = document.getElementById("footer-input-badge");
  var count = wizardStructures.length;
  var hint = document.getElementById("footer-selection-hint");
  if (hint) hint.hidden = count > 0 || createWizardStep !== 1;
  var primary = document.getElementById("modal-submit");
  if (primary && !(window.ACPJobEditor && ACPJobEditor.isActive()) && createWizardStep === 1) primary.disabled = count === 0;
  if (countEl) { countEl.style.display = count > 0 ? "inline-flex" : "none"; countEl.textContent = t("wizard.selected_count_footer", { count: count }) || ("已选择 " + count + " 项"); }
  if (inputBtn) inputBtn.style.display = count > 0 ? "inline-flex" : "none";
  if (inputBadge) inputBadge.textContent = String(count);
}

// Overlay controller for input list
function _openInputListOverlay() {
  var overlay = document.getElementById("input-list-overlay");
  if (!overlay) return;
  overlay.classList.add("open");
  var trigger = document.getElementById("footer-input-count");
  if (trigger) trigger.setAttribute("aria-expanded", "true");
  renderLoadedStructuresList();
}
function _closeInputListOverlay() {
  var overlay = document.getElementById("input-list-overlay");
  if (overlay) overlay.classList.remove("open");
  var trigger = document.getElementById("footer-input-count");
  if (trigger) trigger.setAttribute("aria-expanded", "false");
}
function _toggleInputListOverlay() {
  var overlay = document.getElementById("input-list-overlay");
  if (!overlay) return;
  if (overlay.classList.contains("open")) _closeInputListOverlay();
  else _openInputListOverlay();
}

// Wire footer input count button to toggle overlay
(function() {
  var footerBtn = document.getElementById("footer-input-count");
  if (footerBtn && !footerBtn._acpWired) {
    footerBtn._acpWired = true;
    footerBtn.addEventListener("click", function() { _toggleInputListOverlay(); });
  }
})();

// Close overlay on outside mousedown / Escape / step change
(function() {
  document.addEventListener("mousedown", function(event) {
    var overlay = document.getElementById("input-list-overlay");
    if (!overlay || !overlay.classList.contains("open")) return;
    if (overlay.contains(event.target)) return;
    var footerBtn = document.getElementById("footer-input-count");
    if (footerBtn && footerBtn.contains(event.target)) return;
    _closeInputListOverlay();
  });
  document.addEventListener("keydown", function(event) {
    if (event.key === "Escape") _closeInputListOverlay();
  });
})();

// Wire clear-draft button
(function() {
  var clearBtn = document.getElementById("wizard-clear-draft");
  if (clearBtn && !clearBtn._acpWired) {
    clearBtn._acpWired = true;
    clearBtn.addEventListener("click", function() {
      _discardSavedWizardDraft();
      _closeInputListOverlay();
      var defaultWorkflow = resolveDefaultWorkflow("");
      if (defaultWorkflow) {
        wizardState.workflow = {
          id: defaultWorkflow.id,
          label: defaultWorkflow.label,
          schema_id: canonicalMethodSchemaId(defaultWorkflow.id, defaultWorkflow.method_schema_id)
        };
        loadDefaultMethodProfile(wizardState.workflow.schema_id);
      }
      openModal();
    });
  }
})();

// Wire wizard-input-form fields
(function() {
  var nameInput = document.getElementById("wif-name");
  var roleSelect = document.getElementById("wif-role");
  var chargeInput = document.getElementById("wif-charge");
  var multInput = document.getElementById("wif-mult");
  function _current() {
    var id = nameInput && nameInput.dataset.inputId;
    return wizardStructures.find(function(entry) { return entry.input_id === id; }) || null;
  }
  function _apply(fn) {
    var s = _current();
    if (!s) return;
    fn(s);
    s.input_modified = true;
    renderLoadedStructuresList();
    if (window.ACPJobEditor && ACPJobEditor.isActive()) {
      ACPJobEditor.onTaskStructureChanged(wizardSelectedStructureIndex, s);
    }
  }
  if (nameInput && !nameInput._acpWired) {
    nameInput._acpWired = true;
    nameInput.addEventListener("input", function() { _apply(function(s) { s.name = nameInput.value.trim() || s.name || "mol_" + (wizardSelectedStructureIndex + 1); }); });
  }
  if (roleSelect && !roleSelect._acpWired) {
    roleSelect._acpWired = true;
    roleSelect.addEventListener("change", function() { _apply(function(s) { s.tag = roleSelect.value; }); });
  }
  if (chargeInput && !chargeInput._acpWired) {
    chargeInput._acpWired = true;
    chargeInput.addEventListener("change", function() { _apply(function(s) { s.charge = Number(chargeInput.value) || 0; }); });
  }
  if (multInput && !multInput._acpWired) {
    multInput._acpWired = true;
    multInput.addEventListener("change", function() { _apply(function(s) { s.multiplicity = Math.max(1, Number(multInput.value) || 1); }); });
  }
})();

// Wire atom-number toggle
(function() {
  var atomBtn = document.getElementById("preview-atom-numbers");
  if (atomBtn && !atomBtn._acpWired) {
    atomBtn._acpWired = true;
    atomBtn.addEventListener("click", function() {
      wizardAtomLabelsOn = !wizardAtomLabelsOn;
      atomBtn.classList.toggle("active", wizardAtomLabelsOn);
      atomBtn.setAttribute("aria-pressed", String(wizardAtomLabelsOn));
      _togglePreviewAtomLabels();
    });
  }
})();

function _togglePreviewAtomLabels() {
  if (!previewViewer) return;
  try {
    previewViewer.removeAllLabels();
    if (wizardAtomLabelsOn && previewModel) {
      var atoms = previewModel.selectedAtoms({});
      for (var i = 0; i < atoms.length; i++) {
        var a = atoms[i];
        previewViewer.addLabel(String(a.atom || a.elem || (i + 1)), {
          fontSize: 10, fontColor: "white", backgroundColor: "black",
          backgroundOpacity: 0.6, showBackground: true,
          alignment: "center", inFront: true,
        }, { model: previewModel, serial: a.serial != null ? a.serial : i });
      }
    }
    previewViewer.render();
  } catch (e) {}
}

// Wire small-screen list/detail toggle
(function() {
  var listBtn = document.getElementById("wizard-mobile-list-btn");
  var detailBtn = document.getElementById("wizard-mobile-detail-btn");
  if (listBtn && !listBtn._acpWired) {
    listBtn._acpWired = true;
    listBtn.addEventListener("click", function() {
      var shell = document.getElementById("structure-preview");
      if (shell) { shell.classList.remove("wizard-show-detail"); shell.classList.add("wizard-show-list"); }
    });
  }
  if (detailBtn && !detailBtn._acpWired) {
    detailBtn._acpWired = true;
    detailBtn.addEventListener("click", function() {
      var shell = document.getElementById("structure-preview");
      if (shell) { shell.classList.remove("wizard-show-list"); shell.classList.add("wizard-show-detail"); }
    });
  }
})();

// The drawer is intentionally a view over wizardStructures.  It never mutates
// source metadata or source jobs; all changes are scoped to this submission.
function renderLoadedStructuresList() {
  var list = document.getElementById("loaded-structures-list");
  var title = document.getElementById("loaded-structures-title");
  if (!list) return;
  if (title) title.textContent = "本次输入清单（" + wizardStructures.length + "）";
  list.innerHTML = wizardStructures.map(function(s, i) {
    var source = s.source_ref || {};
    var sourceText = source.job_id ? ("来源：" + source.job_id + (source.path ? " · " + source.path : "")) : "本次任务输入";
    var role = String(s.tag || s.role || "");
    var disabledBadge = s.usage_status === "trash"
      ? '<span class="loaded-structure-disabled-badge">已移入垃圾箱</span>'
      : "";
    var versionBadge = s.version_changed
      ? '<span class="loaded-structure-disabled-badge" style="background:#5a3e00;color:#ffb454;">' + (t("wizard.version_changed") || "版本已变化·需重新确认") + '</span>'
      : "";
    var geoState = s.geometry_state || "ready";
    var geoBadge = "";
    if (geoState === "pending") geoBadge = '<span class="loaded-structure-geo-badge" style="color:#6ea8fe;">⏳ ' + (t("wizard.geometry_pending") || "待加载") + '</span>';
    else if (geoState === "loading") geoBadge = '<span class="loaded-structure-geo-badge" style="color:#6ea8fe;">⟳ ' + (t("wizard.geometry_loading") || "加载中…") + '</span>';
    else if (geoState === "failed") geoBadge = '<span class="loaded-structure-geo-badge" style="color:#f87171;">✗ ' + (t("wizard.geometry_failed") || "加载失败") + '</span>';
    var sourceKindLabel = "";
    var sk = s.source_kind || "";
    if (sk === "saved_candidate") sourceKindLabel = t("wizard.source_candidate") || "候选";
    else if (sk === "task_result") sourceKindLabel = t("wizard.source_task_result") || "任务结果";
    else if (sk === "last_structure") sourceKindLabel = (typeof currentLang !== "undefined" && currentLang !== "zh-CN") ? "Last calculation result" : "上次计算结果";
    else if (sk === "upload") sourceKindLabel = t("wizard.source_upload") || "上传";
    else if (sk === "manual_input") sourceKindLabel = t("wizard.source_manual") || "手动输入";
    var sourceKindBadge = sourceKindLabel ? '<span class="loaded-structure-source-kind">' + escapeHtml(sourceKindLabel) + '</span>' : "";
    var restoreButton = s.usage_status === "trash"
      ? '<button type="button" class="btn ghost loaded-structure-restore" data-index="' + i + '">恢复并保留</button>'
      : "";
    return '<div class="loaded-structure-row' + (i === wizardSelectedStructureIndex ? ' selected' : '') + '" data-index="' + i + '">' +
      '<span title="提交顺序">' + (i + 1) + '.</span>' +
      '<div><input class="loaded-structure-name" data-index="' + i + '" value="' + escapeAttr(s.name || s.molecule_name || ("mol_" + (i + 1))) + '" aria-label="本次输入结构名称">' + disabledBadge + versionBadge + geoBadge + sourceKindBadge + '<div class="loaded-structure-meta" title="' + escapeAttr(sourceText) + '">' + escapeHtml(sourceText) + (s.candidate_id ? ' · Candidate ID: ' + escapeHtml(s.candidate_id) : '') + '</div></div>' +
      '<div class="loaded-structure-controls"><select class="loaded-structure-role" data-index="' + i + '" aria-label="角色"><option value=""' + (!role ? ' selected' : '') + '>普通</option><option value="TS"' + (role === 'TS' ? ' selected' : '') + '>TS</option><option value="INT"' + (role === 'INT' ? ' selected' : '') + '>INT</option></select><input class="loaded-structure-charge" data-index="' + i + '" type="number" value="' + escapeAttr(s.charge === undefined ? 0 : s.charge) + '" aria-label="电荷"><input class="loaded-structure-mult" data-index="' + i + '" type="number" min="1" value="' + escapeAttr(s.multiplicity || 1) + '" aria-label="自旋多重度"></div>' +
      restoreButton + '<button type="button" class="btn ghost loaded-structure-remove" data-index="' + i + '">' + (t("wizard.remove_from_input") || "移出本次输入") + '</button></div>';
  }).join("") || '<div class="results-list-note">尚未添加输入；可从左侧结构库中勾选。</div>';
  list.querySelectorAll('.loaded-structure-row').forEach(function(row) { row.addEventListener('click', function(event) { if (!event.target.matches('input,select,button')) selectPreviewStructure(Number(row.dataset.index), true); }); });
  list.querySelectorAll('.loaded-structure-name').forEach(function(el) { el.addEventListener('change', function() { var index = Number(el.dataset.index); var s = wizardStructures[index]; if (s) { s.name = el.value.trim() || 'mol_' + (index + 1); s.input_modified = true; if (window.ACPJobEditor && ACPJobEditor.isActive()) ACPJobEditor.onTaskStructureChanged(index, s); selectPreviewStructure(index, true); } }); });
  list.querySelectorAll('.loaded-structure-role, .loaded-structure-charge, .loaded-structure-mult').forEach(function(el) { el.addEventListener('change', function() { var index = Number(el.dataset.index); var s = wizardStructures[index]; if (!s) return; if (el.classList.contains('loaded-structure-role')) s.tag = el.value; else if (el.classList.contains('loaded-structure-charge')) s.charge = Number(el.value) || 0; else s.multiplicity = Math.max(1, Number(el.value) || 1); s.input_modified = true; if (window.ACPJobEditor && ACPJobEditor.isActive()) ACPJobEditor.onTaskStructureChanged(index, s); selectPreviewStructure(index, true); }); });
  list.querySelectorAll('.loaded-structure-remove').forEach(function(el) { el.addEventListener('click', function() {
    var idx = Number(el.dataset.index);
    var entry = wizardStructures[idx];
    if (!entry) return;
    if (window.ACPJobEditor && ACPJobEditor.isActive()) ACPJobEditor.onTaskStructureRemoved(idx, entry);
    wizardStructures.splice(idx, 1);
    wizardSelectedStructureIndex = Math.min(idx, Math.max(wizardStructures.length - 1, 0));
    if (!wizardStructures.length) {
      wizardParseWarnings = [];
      renderPreviewWaiting();
      showParseStatus(t("parse.waiting"), false);
    } else {
      renderStructurePreview(wizardStructures, wizardParseWarnings);
    }
    updateInputModeVisibility();
    renderLoadedStructuresList();
    _syncPickerSelection();
  }); });
  list.querySelectorAll('.loaded-structure-restore').forEach(function(el) { el.addEventListener('click', function() {
    var structure = wizardStructures[Number(el.dataset.index)];
    if (!structure || !structure.source_uid) return;
    candidateApi("/structure-sources/" + encodeURIComponent(structure.source_uid) + "/candidate/status", {
      method:"PATCH", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({status:"active", expected_revision:structure.status_revision || 0})
    }).then(function(updated) {
      structure.usage_status = "active";
      structure.status_revision = updated.status_revision;
      renderLoadedStructuresList();
      if (resultsPicker) resultsPicker.refresh();
      if (candidatePicker) candidatePicker.refresh();
    }).catch(function(err) { window.alert("恢复候选失败：" + err.message); });
  }); });
  if (resultsPicker && typeof resultsPicker.render === "function") resultsPicker.render();
  if (candidatePicker && typeof candidatePicker.render === "function") candidatePicker.render();
  refreshIrcSourceSummary();
}

function updatePreviewActionState() {
  var hasStructures = Array.isArray(wizardStructures) && wizardStructures.length > 0;
  ["preview-rename-structure", "preview-edit-structure", "preview-remove-current"].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.disabled = !hasStructures;
  });
  var moreMenu = document.getElementById("preview-more-menu");
  if (moreMenu) moreMenu.style.display = hasStructures ? "block" : "none";
}

function removeSelectedStructure() {
  if (!Array.isArray(wizardStructures) || !wizardStructures.length) return;
  var removedIndex = Math.min(
    Math.max(Number(wizardSelectedStructureIndex) || 0, 0),
    wizardStructures.length - 1
  );
  wizardStructures.splice(removedIndex, 1);
  wizardSelectedStructureIndex = Math.min(removedIndex, Math.max(wizardStructures.length - 1, 0));
  if (!wizardStructures.length) {
    wizardParseWarnings = [];
    var csRow = document.getElementById("charge-spin-row");
    if (csRow) csRow.style.display = "none";
    renderPreviewWaiting();
    showParseStatus(t("parse.waiting"), false);
  } else {
    renderStructurePreview(wizardStructures, wizardParseWarnings);
    showParseStatus(t("parse.success", { count: wizardStructures.length }), false);
  }
  updateInputModeVisibility();
  if (isPESsearchTask()) {
    syncPESSelectionFromWizard(true);
    attachPESPreviewSelection();
  }
}

function clearParsedStructures() {
  if (!Array.isArray(wizardStructures) || !wizardStructures.length) return;
  wizardStructures = [];
  wizardParseWarnings = [];
  wizardSelectedStructureIndex = 0;
  var csRow = document.getElementById("charge-spin-row");
  if (csRow) csRow.style.display = "none";
  renderPreviewWaiting();
  showParseStatus(t("parse.waiting"), false);
  updateInputModeVisibility();
  renderLoadedStructuresList();
  _syncPickerSelection();
}

function initPreviewViewer() {
  if (previewViewer) return;
  var container = document.getElementById("structure-preview-3d");
  if (!container) return;
  // Shared adapter (FE-09): returns null when 3Dmol is missing/failed —
  // callers guard on the falsy handle (renderPreviewStructure3D etc.).
  previewViewer = ACPVisualization.mol3d.create(container, { backgroundColor: "0x0e1419", antialias: true });
}

function renderPreviewStructure3D(structure) {
  if (!previewViewer) initPreviewViewer();
  if (!previewViewer) return;
  previewViewer.removeAllModels();
  clearPESPreviewSelectionShapes();
  clearScanPreviewSelectionShapes();
  previewModel = null;
  previewModelStructureKey = structure && structure.xyz ? String(structure.xyz).trim() : "";
  var emptyEl = document.getElementById("preview-3d-empty");
  var titleEl = document.getElementById("preview-structure-title");
  var coordinateEl = document.getElementById("source-coordinate-text");
  var titleParts = [];
  if (structure) {
    titleParts.push(structure.name || structure.molecule_name || "结构预览");
    if (structure.tag) titleParts.push(structure.tag);
    if (wizardStructures.length > 1) titleParts.push((wizardSelectedStructureIndex + 1) + "/" + wizardStructures.length);
  }
  if (titleEl) titleEl.textContent = titleParts.join(" · ") || t("modal.preview_title");
  if (coordinateEl) coordinateEl.textContent = structure ? (structure.xyz || "") : "";
  if (!structure || !structure.has_3d) {
    if (emptyEl) {
      // Parsed OK but no 3D coordinates is a different situation from
      // "nothing parsed" — say so explicitly (P2) instead of a bare
      // "无 3D 坐标" that reads like a parse failure.
      if (structure) {
        var geometryReason = (structure.errors || [])[0] || (structure.warnings || [])[0];
        emptyEl.textContent = geometryReason || t("wizard.no_3d_coord") || t("parse.no_3d_embedded");
        emptyEl.title = geometryReason || "";
      } else if (wizardInputMode === "task") {
        emptyEl.textContent = t("wizard.select_hint");
        emptyEl.title = "";
      } else {
        emptyEl.textContent = t("parse.no_3d_waiting");
        emptyEl.title = "";
      }
      emptyEl.style.display = "grid";
    }
    previewViewer.render();
    return;
  }
  if (emptyEl) emptyEl.style.display = "none";
  var modelData = structure.xyz || structure.molfile || "";
  var fmt = structure.xyz ? "xyz" : "mol";
  if (modelData) {
    previewModel = previewViewer.addModel(modelData, fmt);
    previewViewer.setStyle({}, { stick: { radius: 0.14 }, sphere: { scale: 0.26 } });
    attachPESPreviewSelection();
    previewViewer.render();
    scheduleViewerFraming(
      document.getElementById("structure-preview-3d"),
      previewViewerFraming,
      function() { return previewViewer; },
      { zoomFactor: PREVIEW_ZOOM_FACTOR }
    );
  }
}

function isPESsearchTask() {
  return !!(wizardState && wizardState.workflow && wizardState.workflow.id === "PESsearch");
}

function currentPESStructure() {
  return wizardStructures[wizardSelectedStructureIndex] || wizardStructures[0] || null;
}

function stagePESsearchSource(item) {
  if (!isPESsearchTask() || !item || !item.source_uid) return;
  var TIW = window.ACPTaskInputWorkspace;
  if (!TIW || typeof TIW.entryFromSnapshot !== "function") return;
  var existing = wizardStructures.find(function(entry) {
    return entry && entry.source_uid === item.source_uid;
  });
  var entry = existing || TIW.entryFromSnapshot(item);
  if (!entry) return;

  // PESsearch has a single input. Choosing a source for atom picking makes it
  // the sole staged input, so the visible preview and scan geometry agree.
  entry.source_id = item.source_id || entry.source_id || "";
  entry.source_ref = entry.source_ref || {};
  entry.source_ref.source_uid = item.source_uid;
  if (entry.source_id) entry.source_ref.source_id = entry.source_id;
  if (entry.xyz) entry.geometry_state = "ready";
  else if (entry.geometry_state !== "pending" && entry.geometry_state !== "loading") entry.geometry_state = "pending";
  wizardStructures = [entry];
  wizardSelectedStructureIndex = 0;
  wizardParseWarnings = entry.warnings || [];
  if (entry.charge !== undefined) document.getElementById("modal-charge").value = entry.charge;
  if (entry.multiplicity) document.getElementById("modal-mult").value = entry.multiplicity;
  renderLoadedStructuresList();
  _refreshWizardInputForm(entry);
  updateFooterSelectedCount();
  _syncPickerSelection();
  syncPESSelectionFromWizard(true);
  attachPESPreviewSelection();
  updatePESSelectionStatus();
  _loadPendingGeometry();
}

function structureMoleculeName(structure) {
  if (!structure) return "";
  var value = String(structure.molecule_name || structure.inherited_molecule_name || structure.name || "").trim();
  // Imported artifact labels are a compatibility fallback only.  The API
  // now supplies molecule_name separately, so task IDs and source IDs never
  // need to be parsed in the browser.
  return value.replace(/\.(xyz|gjf|sdf|mol|com|inp|log|out|json)$/i, "").trim();
}

function clearPESPreviewSelectionShapes() {
  if (!previewViewer) return;
  (pesSelectionState.shapes || []).forEach(function(shape) {
    try { previewViewer.removeShape(shape); } catch (e) {}
  });
  pesSelectionState.shapes = [];
}

function resetPESSelection() {
  clearPESPreviewSelectionShapes();
  pesSelectionState.selectionKind = "bond_stretch";
  pesSelectionState.mode = "functional";
  pesSelectionState.coordinateKind = "distance";
  pesSelectionState.selectedAtoms = [];
  pesSelectionState.structureKey = "";
  if (typeof s2scanState !== "undefined" && s2scanState) {
    s2scanState.selectedAtoms = [];
    s2scanState.selA = null;
    s2scanState.selB = null;
    s2scanState.sourceXyz = "";
  }
  updatePESSelectionSummary();
  updatePESConfigNote();
}

function syncPESSelectionFromWizard(forceReset) {
  var structure = currentPESStructure();
  var xyz = structure && structure.xyz ? String(structure.xyz).trim() : "";
  var atoms = xyz ? parseXyzAtoms(xyz) : [];
  var changed = xyz !== pesSelectionState.structureKey;
  if (!xyz || !atoms.length) {
    resetPESSelection();
    if (typeof s2scanState !== "undefined" && s2scanState) s2scanState.atoms = [];
    return;
  }
  if (forceReset || changed) {
    pesSelectionState.selectedAtoms = [];
    clearPESPreviewSelectionShapes();
  }
  pesSelectionState.structureKey = xyz;
  if (typeof s2scanState !== "undefined" && s2scanState) {
    s2scanState.atoms = atoms;
    s2scanState.xyzText = xyz;
    s2scanState.sourceXyz = xyz;
    s2scanState.charge = parseInt(document.getElementById("modal-charge").value, 10) || 0;
    s2scanState.mult = parseInt(document.getElementById("modal-mult").value, 10) || 1;
    s2scanState.selectedAtoms = pesSelectionState.selectedAtoms.slice();
  }
  updatePESSelectionSummary();
  updatePESConfigNote();
  attachPESPreviewSelection();
}

function getPESCoordinateConfig() {
  var stages = (wizardState.method && wizardState.method.stages) || {};
  var raw = stages.scan_coordinate || {};
  var kind = ["distance", "angle", "dihedral"].indexOf(String(raw.scan_coordinate_kind || raw.kind)) >= 0
    ? String(raw.scan_coordinate_kind || raw.kind)
    : "distance";
  var start = parseFloat(raw.scan_coordinate_start !== undefined ? raw.scan_coordinate_start : raw.start);
  var end = parseFloat(raw.scan_coordinate_end !== undefined ? raw.scan_coordinate_end : raw.end);
  var points = parseInt(raw.scan_coordinate_points !== undefined ? raw.scan_coordinate_points : raw.n_points, 10);
  return {
    kind: kind,
    bond_type: String(raw.scan_bond_type || raw.bond_type || "auto"),
    start: isFinite(start) ? start : (kind === "distance" ? 1.0 : 0.0),
    end: isFinite(end) ? end : (kind === "distance" ? 3.0 : (kind === "angle" ? 180.0 : 360.0)),
    n_points: isFinite(points) && points >= 3 ? points : 21,
  };
}

function pesCoordinateAtomCount() {
  var kind = pesSelectionState.selectionKind || "bond_stretch";
  return kind === "bond_stretch" ? 2 : kind === "angle" ? 3 : 4;
}

function pesCoordinateUnit() {
  return pesSelectionState.selectionKind === "bond_stretch" || pesSelectionState.selectionKind === "double_bond_scan" ? "Å" : "°";
}

function pesSelectionKindLabel(kind) {
  return ({
    bond_stretch: "距离扫描",
    angle: "键角扫描",
    dihedral: "二面角扫描",
    double_bond_scan: "双键同步扫描",
  })[kind] || kind;
}

function pesSelectionKindForCoordinate(kind) {
  return kind === "angle" ? "angle" : kind === "dihedral" ? "dihedral" : "bond_stretch";
}

function setPESSelectionKind(kind) {
  if (["bond_stretch", "angle", "dihedral", "double_bond_scan"].indexOf(kind) < 0) return;
  pesSelectionState.selectionKind = kind;
  pesSelectionState.mode = "functional";
  pesSelectionState.coordinateKind = kind === "angle" ? "angle" : kind === "dihedral" ? "dihedral" : "distance";
  if (!wizardState.method.stages.scan_coordinate) wizardState.method.stages.scan_coordinate = {};
  wizardState.method.stages.scan_coordinate.scan_coordinate_kind = pesSelectionState.coordinateKind;
  if (kind === "double_bond_scan") wizardState.method.stages.scan_coordinate.scan_bond_type = "double";
  pesSelectionState.selectedAtoms = [];
  if (typeof s2scanState !== "undefined" && s2scanState) s2scanState.selectedAtoms = [];
  document.querySelectorAll(".pes-function-btn").forEach(function(button) {
    button.classList.toggle("active", button.getAttribute("data-selection-kind") === kind);
  });
  renderPESPreviewSelection();
  updatePESSelectionSummary();
  updatePESTaskBadge();
}

function formatPESAtom(index) {
  var atoms = (typeof s2scanState !== "undefined" && s2scanState && s2scanState.atoms) || [];
  var atom = atoms[index];
  return atom ? String(atom.elem || "?") + "#" + (index + 1) : "#" + (index + 1);
}

function measurePESCoordinate(indices) {
  var atoms = (typeof s2scanState !== "undefined" && s2scanState && s2scanState.atoms) || [];
  if (!indices || indices.length !== pesCoordinateAtomCount()) return null;
  if (indices.some(function(index) { return !atoms[index]; })) return null;
  var a = atoms[indices[0]], b = atoms[indices[1]];
  if (indices.length === 2) return s2scanDist(a, b);
  if (indices.length === 3) return s2scanAngle(a, b, atoms[indices[2]]);
  return s2scanDihedral(a, b, atoms[indices[2]], atoms[indices[3]]);
}

function updatePESSelectionSummary() {
  var summary = document.getElementById("pes-selection-summary");
  if (!summary) return;
  var selected = pesSelectionState.selectedAtoms || [];
  var count = pesCoordinateAtomCount();
  if (selected.length !== count) {
    summary.innerHTML = '<span class="selection-empty">' + escapeHtml(pesSelectionKindLabel(pesSelectionState.selectionKind)) +
      "：已选择 " + selected.length + " / " + count + " 个原子</span>";
    return;
  }
  var separator = pesSelectionState.selectionKind === "double_bond_scan" ? " · " : " — ";
  var label = selected.map(formatPESAtom).join(separator);
  var lengthNote = "";
  if (pesSelectionState.selectionKind === "double_bond_scan") {
    label = selected.slice(0, 2).map(formatPESAtom).join(" — ") + "  +  " + selected.slice(2).map(formatPESAtom).join(" — ");
    var lenAtoms = (typeof s2scanState !== "undefined" && s2scanState && s2scanState.atoms) || [];
    var p0 = lenAtoms[selected[0]], p1 = lenAtoms[selected[1]], p2 = lenAtoms[selected[2]], p3 = lenAtoms[selected[3]];
    if (p0 && p1 && p2 && p3) {
      lengthNote = '<span>当前键长：' + s2scanDist(p0, p1).toFixed(3) + " Å / " + s2scanDist(p2, p3).toFixed(3) + " Å（提交时各自从当前键长同步拉伸）</span>";
    }
  }
  summary.innerHTML = '<span class="selection-badge">' + escapeHtml(pesSelectionKindLabel(pesSelectionState.selectionKind)) +
    '：' + escapeHtml(label) + '</span>' +
    '<span>内部索引：[' + selected.join(", ") + "]</span>" + lengthNote;
}

function updatePESConfigNote(message) {
  updatePESTaskBadge();
}

function pesTaskKindLabel(config) {
  if (pesSelectionState.selectionKind === "double_bond_scan") return "双键同步扫描 · 两组各自拉伸";
  if (pesSelectionState.selectionKind === "angle") return "键角扫描";
  if (pesSelectionState.selectionKind === "dihedral") return "二面角扫描";
  var bondLabels = {
    auto: "键型自动识别",
    single: "单键",
    double: "双键",
    multiple: "多键",
    aromatic: "芳香键",
  };
  return "键长扫描 · " + (bondLabels[config.bond_type] || config.bond_type || "键型自动识别");
}

function updatePESTaskBadge() {
  var badge = document.getElementById("pes-task-badge");
  if (!badge) return;
  var config = getPESCoordinateConfig();
  var selected = pesSelectionState.selectedAtoms || [];
  var taskText = pesTaskKindLabel(config);
  var atomsText = selected.length === pesCoordinateAtomCount()
    ? (pesSelectionState.selectionKind === "double_bond_scan"
      ? selected.slice(0, 2).map(formatPESAtom).join(" — ") + "  +  " + selected.slice(2).map(formatPESAtom).join(" — ")
      : selected.map(formatPESAtom).join(pesSelectionState.selectionKind === "bond_stretch" ? " — " : " - "))
    : "尚未完成坐标选择";
  badge.innerHTML = '<strong>本次 PESsearch 任务</strong>' +
    '<span class="task-kind">' + escapeHtml(taskText) + '</span>' +
    '<span style="display:block;color:var(--text-tertiary);">' + escapeHtml(atomsText) + "</span>";
}

function updatePESSelectionForProtocol() {
  if (!isPESsearchTask()) return;
  var config = getPESCoordinateConfig();
  var previousKind = pesSelectionState.coordinateKind || "distance";
  var selectionKind = pesSelectionState.selectionKind || "bond_stretch";
  var doubleMode = selectionKind === "double_bond_scan" && config.kind === "distance";
  if (previousKind !== config.kind && !doubleMode) {
    pesSelectionState.selectedAtoms = [];
    if (typeof s2scanState !== "undefined" && s2scanState) s2scanState.selectedAtoms = [];
    pesSelectionState.coordinateKind = config.kind;
    pesSelectionState.selectionKind = pesSelectionKindForCoordinate(config.kind);
  }
  document.querySelectorAll(".pes-function-btn").forEach(function(button) {
    button.classList.toggle("active", button.getAttribute("data-selection-kind") === pesSelectionState.selectionKind);
  });
  renderPESPreviewSelection();
  updatePESSelectionSummary();
  updatePESTaskBadge();
}

function renderPESPreviewSelection() {
  if (!previewViewer) return;
  clearPESPreviewSelectionShapes();
  if (!isPESsearchTask()) return;
  previewViewer.setStyle({}, { stick: { radius: 0.14 }, sphere: { scale: 0.26 } });
  var atoms = (typeof s2scanState !== "undefined" && s2scanState && s2scanState.atoms) || [];
  var selected = pesSelectionState.selectedAtoms || [];
  var colors = ["#4ea1ff", "#38c172", "#f2b84b", "#c084fc"];
  selected.forEach(function(index, position) {
    var atom = atoms[index];
    if (!atom) return;
    var sphere = previewViewer.addSphere({
      center: { x: atom.x, y: atom.y, z: atom.z },
      radius: 0.4,
      color: colors[position],
      opacity: 0.82,
    });
    pesSelectionState.shapes.push(sphere);
    previewViewer.addStyle({ index: index }, { sphere: { scale: 0.38, color: colors[position] } });
  });
  var linePairs = pesSelectionState.selectionKind === "double_bond_scan"
    ? [[0, 1], [2, 3]]
    : selected.map(function(_, index) { return [index - 1, index]; }).slice(1);
  linePairs.forEach(function(pair) {
    var i = pair[0], j = pair[1];
    var first = atoms[selected[i]], second = atoms[selected[j]];
    if (!first || !second) return;
    pesSelectionState.shapes.push(previewViewer.addCylinder({
      start: { x: first.x, y: first.y, z: first.z },
      end: { x: second.x, y: second.y, z: second.z },
      radius: 0.09,
      color: "#f2b84b",
      dashed: true,
      dashLength: 0.12,
      gapLength: 0.08,
      opacity: 0.95,
    }));
  });
  previewViewer.render();
}

function attachPESPreviewSelection() {
  if (!previewModel) return;
  var canPick = canPickPESAtoms() || canPickScanAtoms();
  try {
    previewModel.setClickable({}, canPick, function(atom) {
      canPickPESAtoms() ? handlePESPreviewAtomClick(atom) : handleScanPreviewAtomClick(atom);
    });
  } catch (e) {
    console.warn("PES atom selection unavailable:", e);
  }
  updatePESSelectionStatus();
  var selectionPanel = document.getElementById("pes-selection-panel");
  if (selectionPanel) selectionPanel.classList.toggle("picking-disabled", !canPick);
  renderPESPreviewSelection();
  if (canPickScanAtoms()) renderScanPreviewSelection();
}

function canPickPESAtoms() {
  var modal = document.getElementById("job-modal");
  var structure = currentPESStructure();
  var xyz = structure && structure.xyz ? String(structure.xyz).trim() : "";
  var atoms = (typeof s2scanState !== "undefined" && s2scanState && s2scanState.atoms) || [];
  return !!(
    modal && modal.style.display !== "none" &&
    createWizardStep === 2 && isPESsearchTask() && wizardStructures.length === 1 &&
    structure && structure.has_3d && xyz && previewModel &&
    previewModelStructureKey === xyz && atoms.length
  );
}

function updatePESSelectionStatus() {
  var status = document.getElementById("pes-selection-status");
  if (!status) return;
  var isChinese = currentLang === "zh-CN";
  var structure = currentPESStructure();
  if (!isPESsearchTask()) {
    status.textContent = isChinese ? "请在结构预览中选择扫描原子。" : "Pick scan atoms in the structure preview.";
    return;
  }
  if (wizardStructures.length !== 1) {
    status.textContent = isChinese
      ? "势能面搜索需要且只接受一个结构，请返回步骤 1，仅保留一个输入结构。"
      : "PESsearch requires exactly one input. Return to step 1 and keep one structure.";
  } else if (structure && (structure.geometry_state === "pending" || structure.geometry_state === "loading")) {
    status.textContent = isChinese
      ? "本次输入的结构坐标正在加载，请稍候。"
      : "The staged structure coordinates are loading. Please wait.";
  } else if (structure && structure.geometry_state === "failed") {
    status.textContent = isChinese
      ? "本次输入结构坐标加载失败，请重新选择该结构后重试。"
      : "Could not load coordinates for the staged structure. Reselect it and try again.";
  } else if (!structure || !structure.has_3d || !structure.xyz) {
    status.textContent = isChinese
      ? "本次输入没有可用的 3D 坐标，请选择或导入带坐标的结构。"
      : "The staged input has no usable 3D coordinates. Select or import a structure with coordinates.";
  } else if (!previewModel || previewModelStructureKey !== String(structure.xyz).trim()) {
    status.textContent = isChinese
      ? "预览中的结构不是本次输入结构；请选择本次输入中的结构后再点选。"
      : "The preview is not showing the staged input. Select the staged structure before picking atoms.";
  } else {
    status.textContent = isChinese
      ? "在右侧结构中依次点击原子；距离扫描可选择任意两个原子，不要求已有化学键。"
      : "Click atoms in order. A distance scan accepts any two atoms, whether or not they are bonded.";
  }
}

function handlePESPreviewAtomClick(atom) {
  if (!canPickPESAtoms() || !atom || typeof atom.index !== "number") return;
  var index = Number(atom.index);
  var selected = pesSelectionState.selectedAtoms.slice();
  var existing = selected.indexOf(index);
  if (existing >= 0) selected.splice(existing, 1);
  else if (selected.length < pesCoordinateAtomCount()) selected.push(index);
  else selected = [index];
  pesSelectionState.selectedAtoms = selected;
  if (typeof s2scanState !== "undefined" && s2scanState) {
    s2scanState.selectedAtoms = selected.slice();
    s2scanState.selA = selected.length ? selected[0] : null;
    s2scanState.selB = selected.length > 1 ? selected[1] : null;
  }
  renderPESPreviewSelection();
  updatePESSelectionSummary();
  updatePESConfigNote();
}

function clearScanPreviewSelectionShapes() {
  if (!previewViewer) return;
  (scanSelectionState.shapes || []).forEach(function(shape) {
    try { previewViewer.removeShape(shape); } catch (e) {}
  });
  scanSelectionState.shapes = [];
}

function renderScanPreviewSelection() {
  if (!previewViewer) return;
  clearScanPreviewSelectionShapes();
  previewViewer.setStyle({}, { stick: { radius: 0.14 }, sphere: { scale: 0.26 } });
  var structure = scanWizardCurrentStructure();
  var atoms = parseXyzAtoms(structure && structure.xyz ? String(structure.xyz) : "");
  var selected = scanSelectionState.selectedAtoms || [];
  var colors = ["#4ea1ff", "#38c172", "#f2b84b", "#c084fc"];
  selected.forEach(function(index, position) {
    var atom = atoms[index];
    if (!atom) return;
    var sphere = previewViewer.addSphere({
      center: { x: atom.x, y: atom.y, z: atom.z },
      radius: 0.4,
      color: colors[position],
      opacity: 0.82,
    });
    scanSelectionState.shapes.push(sphere);
    previewViewer.addStyle({ index: index }, { sphere: { scale: 0.38, color: colors[position] } });
  });
  var linePairs = selected.map(function(_, index) { return [index - 1, index]; }).slice(1);
  linePairs.forEach(function(pair) {
    var i = pair[0], j = pair[1];
    var first = atoms[selected[i]], second = atoms[selected[j]];
    if (!first || !second) return;
    scanSelectionState.shapes.push(previewViewer.addCylinder({
      start: { x: first.x, y: first.y, z: first.z },
      end: { x: second.x, y: second.y, z: second.z },
      radius: 0.09,
      color: "#f2b84b",
      dashed: true,
      dashLength: 0.12,
      gapLength: 0.08,
      opacity: 0.95,
    }));
  });
  previewViewer.render();
}

function canPickScanAtoms() {
  var modal = document.getElementById("job-modal");
  var structure = scanWizardCurrentStructure();
  var xyz = structure && structure.xyz ? String(structure.xyz).trim() : "";
  return !!(
    modal && modal.style.display !== "none" &&
    createWizardStep === 2 && wizardState && wizardState.workflow &&
    wizardState.workflow.id === "scan" &&
    structure && structure.has_3d && xyz && previewModel &&
    previewModelStructureKey === xyz
  );
}

function handleScanPreviewAtomClick(atom) {
  if (!canPickScanAtoms() || !atom || typeof atom.index !== "number") return;
  var nAtoms = scanWizardAtomCount(scanSelectionState.selectionKind) || 2;
  var index = Number(atom.index);
  var selected = scanSelectionState.selectedAtoms.slice();
  var existing = selected.indexOf(index);
  if (existing >= 0) selected.splice(existing, 1);
  else if (selected.length < nAtoms) selected.push(index);
  else selected = [index];
  scanSelectionState.selectedAtoms = selected;
  if (wizardState && wizardState.method && wizardState.method.stages) {
    if (!wizardState.method.stages.scan) wizardState.method.stages.scan = {};
    // Click handler is the single writer of the 1-based atom mirror (it
    // round-trips through the method-protocol dialog / edit drafts).
    wizardState.method.stages.scan.scan_coordinate_atoms = selected.map(function(i) { return i + 1; });
  }
  renderScanPreviewSelection();
  updateScanSelectionSummary();
  updateScanTaskBadge();
  setScanSelectionError(null);
}

function updatePESSelectionVisibility() {
  var active = isPESsearchTask();
  var visible = active && createWizardStep === 2;
  var selectionPanel = document.getElementById("pes-selection-panel");
  if (selectionPanel) selectionPanel.classList.toggle("active", visible);
  var rightPane = document.getElementById("tiw-right");
  if (rightPane) rightPane.classList.toggle("pes-selection-active", visible);
  var methodCard = document.getElementById("method-card");
  // PESsearch keeps the shared method card visible. The inline area is only
  // the structure/coordinate picker; all scan and QC details live in the
  // common method protocol dialog.
  if (methodCard) methodCard.style.display = "";
  if (active) {
    pesSelectionState.active = true;
    updatePESSelectionForProtocol();
    syncPESSelectionFromWizard(false);
    if (previewModel) attachPESPreviewSelection();
    updatePESSelectionStatus();
    if (selectionPanel) selectionPanel.classList.toggle("picking-disabled", !canPickPESAtoms());
  } else {
    if (pesSelectionState.active) {
      pesSelectionState.selectedAtoms = [];
      if (typeof s2scanState !== "undefined" && s2scanState) s2scanState.selectedAtoms = [];
    }
    pesSelectionState.active = false;
    clearPESPreviewSelectionShapes();
    updatePESSelectionSummary();
    updatePESTaskBadge();
    updatePESSelectionStatus();
    if (selectionPanel) selectionPanel.classList.remove("picking-disabled");
  }
  // Scan reuses this visibility pass: its right-pane picking panel and the
  // click-to-pick gate are driven from updateScanSelectionVisibility so both
  // PES and scan branches reach it (a scan task never blanket-disables the
  // model while its step-2 picking is allowed).
  updateScanSelectionVisibility();
}

function setPESSelectionMode(mode) {
  // Compatibility shim for older callers; the picker is now functional.
  setPESSelectionKind(mode === "atoms"
    ? pesSelectionKindForCoordinate(getPESCoordinateConfig().kind)
    : "bond_stretch");
}

function setPESCoordinateKind(kind) {
  if (["distance", "angle", "dihedral"].indexOf(kind) < 0) return;
  if (!wizardState.method.stages.scan_coordinate) wizardState.method.stages.scan_coordinate = {};
  wizardState.method.stages.scan_coordinate.scan_coordinate_kind = kind;
  setPESSelectionKind(pesSelectionKindForCoordinate(kind));
}

// --- Structure sources ("任务结果" tab, Structure Sources DevDoc Part B) ----
var resultsSources = [];        // cached list for the current modal open
var resultsLoaded = false;      // per-modal-open fetch guard
var resultsSearchTimer = null;
var resultsPicker = null;       // ACPSourcePicker instance for results tab
var candidatePicker = null;     // ACPSourcePicker instance for candidate tab
var candidateLoaded = false;    // per-modal-open fetch guard for candidate picker
var candidateDetailsPanel = null; // ACPCandidateDetails instance for right-column panel
var librarySourceGroup = null; // null | "candidate" | "task_result"
var _inputReconciling = false;  // re-entrancy guard for picker→reconciler→wizardStructures loop

// --- Trash closed-loop state (plan §7) ---
var _trashUndoState = null;     // {targets:[{source_uid,status_revision,priorEntry,wasInInput}]}
var _trashActiveSet = new Set(); // source_uids currently mid-flight (disable duplicate submission)
var _activeRowMenu = null;      // currently open row-menu {el, cleanup}
var _wizardTrashView = false;   // whether the wizard source panel is in trash mode
var _wizardTrashSelection = new Set(); // separate selection for trash-mode checkboxes
var _wizardTrashSnapshots = {}; // source_uid -> server snapshot for project/revision-safe actions
var _wizardSavedViewState = null; // {sourceTab, scrollTop, ...} saved before entering trash

/** Bounded concurrency with per-item settlement — NEVER Promise.all fail-fast. */
function _boundedSettle(items, maxConcurrency, worker) {
  maxConcurrency = Math.max(1, Math.min(maxConcurrency || 4, 8));
  return new Promise(function(resolve) {
    var results = new Array(items.length);
    var idx = 0; var done = 0;
    function next(slot) {
      if (idx >= items.length) { if (done >= items.length) resolve(results); return; }
      var i = idx++;
      Promise.resolve(worker(items[i], i)).then(function(val) { results[i] = {ok:true, value:val}; }).catch(function(err) { results[i] = {ok:false, error:err}; }).finally(function() { done++; next(slot); });
    }
    for (var s = 0; s < Math.min(maxConcurrency, items.length); s++) next(s);
  });
}

function _closeActiveRowMenu() {
  if (_activeRowMenu && _activeRowMenu.cleanup) { try { _activeRowMenu.cleanup(); } catch(_){} }
  _activeRowMenu = null;
}

function _openRowMenu(snapshot, anchorEl) {
  _closeActiveRowMenu();
  var inInput = wizardStructures.some(function(s) { return s && s.source_uid === snapshot.source_uid; });
  var menu = document.createElement("div");
  menu.className = "sp-row-popup-menu";
  menu.setAttribute("role", "menu");
  menu.style.cssText = "position:absolute;z-index:200;min-width:180px;padding:4px 0;background:var(--bg-elevated,#1e1e2e);border:1px solid var(--border-default,#333);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.45);font-size:12px;";
  var items = [
    {key:"rename_source", label:"重命名结构库名称"},
    {key:"rename_task", label:"重命名来源任务", disabled:!snapshot.job_id},
    {key:"input_settings", label:"本次输入设置", disabled:!inInput}
  ];
  if (inInput) items.push({key:"remove_input", label:t("trash.row_menu.remove_input")||"移出本次输入"});
  items.push({key:"move_trash", label:t("trash.row_menu.move_to_trash")||"移入垃圾箱"});
  items.push({key:"open_source", label:t("trash.row_menu.open_source")||"打开来源任务", disabled:!(snapshot.job_id || (snapshot.source_ref && snapshot.source_ref.job_id))});
  menu.innerHTML = items.map(function(m, i) {
    return '<div role="menuitem" tabindex="' + (i===0?'0':'-1') + '" data-action="' + m.key + '"' + (m.disabled?' aria-disabled="true" style="opacity:.45;pointer-events:none;"':'') + ' style="padding:6px 14px;cursor:pointer;color:var(--text-primary,#eee);white-space:nowrap;">' + escapeHtml(m.label) + '</div>';
  }).join("");
  var rect = anchorEl.getBoundingClientRect();
  menu.style.left = (rect.left + window.scrollX) + "px"; menu.style.top = (rect.bottom + window.scrollY + 4) + "px";
  document.body.appendChild(menu);
  // Position correction if overflows right edge
  var mrect = menu.getBoundingClientRect(); if (mrect.right > window.innerWidth - 8) menu.style.left = (window.innerWidth - mrect.width - 8 + window.scrollX) + "px";
  // Focus first item
  var firstItem = menu.querySelector('[role="menuitem"]'); if (firstItem) firstItem.focus();
  // Keyboard navigation
  function onKey(e) {
    var items = Array.from(menu.querySelectorAll('[role="menuitem"]:not([aria-disabled])'));
    var cur = document.activeElement; var ci = items.indexOf(cur);
    if (e.key === "Escape") { e.preventDefault(); cleanup(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); items[(ci+1)%items.length].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); items[(ci-1+items.length)%items.length].focus(); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (cur && cur.dataset.action) { cleanup(); _handleRowMenuAction(cur.dataset.action, snapshot); } }
  }
  // Click outside + item click
  function onClick(e) {
    if (menu.contains(e.target)) {
      var mi = e.target.closest('[role="menuitem"]');
      if (mi && mi.dataset.action && !mi.getAttribute("aria-disabled")) { cleanup(); _handleRowMenuAction(mi.dataset.action, snapshot); }
      return;
    }
    cleanup();
  }
  function cleanup() { menu.remove(); document.removeEventListener("keydown", onKey, true); document.removeEventListener("mousedown", onClick, true); _activeRowMenu = null; }
  document.addEventListener("keydown", onKey, true);
  document.addEventListener("mousedown", onClick, true);
  _activeRowMenu = {el:menu, cleanup:cleanup};
}

function _handleRowMenuAction(action, snapshot) {
  if (action === "rename_source") {
    if (candidateDetailsPanel) candidateDetailsPanel.rename(snapshot);
  } else if (action === "rename_task") {
    var sourceJob = jobsCache.find(function(j) { return j.id === snapshot.job_id; });
    if (sourceJob) openTaskRenameModal(sourceJob);
    else api("/jobs/" + encodeURIComponent(snapshot.job_id)).then(function(job) { openTaskRenameModal(job); }).catch(function(e) { showToast(e.message, "error"); });
  } else if (action === "input_settings") {
    var selectedIndex = wizardStructures.findIndex(function(e) { return e.source_uid === snapshot.source_uid; });
    if (selectedIndex >= 0) { selectPreviewStructure(selectedIndex); document.getElementById("wif-name").focus(); }
  } else if (action === "remove_input") {
    var focusedInputId = (wizardStructures[wizardSelectedStructureIndex] || {}).input_id;
    wizardStructures.forEach(function(entry, index) {
      if (entry && entry.source_uid === snapshot.source_uid && window.ACPJobEditor) ACPJobEditor.onTaskStructureRemoved(index, entry);
    });
    wizardStructures = wizardStructures.filter(function(s) { return !(s && s.source_uid === snapshot.source_uid); });
    var retainedFocus = wizardStructures.findIndex(function(entry) { return entry.input_id === focusedInputId; });
    wizardSelectedStructureIndex = retainedFocus >= 0 ? retainedFocus : Math.max(0, Math.min(wizardSelectedStructureIndex, wizardStructures.length - 1));
    structureSourceLoadToken++;
    _refreshWizardInputForm(wizardStructures[wizardSelectedStructureIndex] || null);
    renderLoadedStructuresList(); _syncPickerSelection();
  } else if (action === "move_trash") {
    _wizardMoveToTrash([snapshot]);
  } else if (action === "open_source") {
    var jobId = snapshot.job_id || (snapshot.source_ref && snapshot.source_ref.job_id);
    if (jobId) { closeJobModalGuarded(); selectJob(jobId); }
  }
}

/** Move items to trash with bounded concurrency + per-item settlement. */
function _wizardMoveToTrash(targets) {
  if (!targets || !targets.length) return;
  // Freeze: dedup + filter out in-flight
  var frozen = []; var skipped = 0;
  var operation = {operationId:"trash_" + Date.now().toString(36), targets:[]};
  for (var i = 0; i < targets.length; i++) {
    var uid = targets[i].source_uid;
    if (!uid || _trashActiveSet.has(uid)) { skipped++; continue; }
    var inputIndex = wizardStructures.findIndex(function(entry) { return entry && entry.source_uid === uid; });
    var priorEntry = inputIndex >= 0 ? JSON.parse(JSON.stringify(wizardStructures[inputIndex])) : null;
    frozen.push({uid:uid, expected_revision:targets[i].status_revision || 0, snapshot:targets[i], priorEntry:priorEntry, inputIndex:inputIndex});
    _trashActiveSet.add(uid);
  }
  if (!frozen.length) return;
  var moved = 0, failed = 0, conflicts = 0, inputRemoved = 0;
  _boundedSettle(frozen, 4, function(target) {
    return candidateApi("/structure-sources/" + encodeURIComponent(target.uid) + "/candidate/status", {
      method:"PATCH", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({status:"trash", expected_revision:target.expected_revision})
    }).then(function(updated) {
      // Synchronously remove from input
      var wasInInput = false;
      wizardStructures = wizardStructures.filter(function(s) {
        if (s && s.source_uid === target.uid) { wasInInput = true; return false; }
        return true;
      });
      if (wasInInput) inputRemoved++;
      // Update undo state
      operation.targets.push({source_uid:target.uid, status_revision:updated.status_revision, priorEntry:target.priorEntry, inputIndex:target.inputIndex, wasInInput:wasInInput});
      return updated;
    });
  }).then(function(results) {
    var succeededUids = [];
    for (var j = 0; j < results.length; j++) {
      _trashActiveSet.delete(frozen[j].uid);
      if (results[j] && results[j].ok) { moved++; succeededUids.push(frozen[j].uid); }
      else {
        var err = results[j] ? results[j].error : new Error("unknown");
        if (err && err.status === 409) { conflicts++; } else { failed++; }
      }
    }
    // Refresh pickers for succeeded items
    if (moved > 0) {
      _trashUndoState = operation;
      renderLoadedStructuresList(); _syncPickerSelection();
      if (resultsPicker) resultsPicker.refresh();
      if (candidatePicker) candidatePicker.refresh();
    }
    // Notification
    var msg = "";
    if (failed === 0 && conflicts === 0) {
      msg = t("trash.moved_ok", {moved:moved, input:inputRemoved}) || ("已移入 " + moved + " 项，已从输入移除 " + inputRemoved + " 项");
    } else {
      var cStr = conflicts > 0 ? (t("trash.moved_conflict", {n:conflicts}) || ("，冲突 " + conflicts + " 项")) : "";
      msg = t("trash.moved_partial", {moved:moved, failed:failed+conflicts, conflict:cStr}) || ("部分成功：移入 " + moved + " 项，失败 " + (failed+conflicts) + " 项" + cStr);
    }
    _showTrashNotification(msg, moved > 0);
  });
}

function _showTrashNotification(msg, canUndo) {
  var bar = document.createElement("div");
  bar.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:9999;display:flex;align-items:center;gap:10px;padding:10px 18px;background:var(--bg-elevated,#1e1e2e);border:1px solid var(--border-default,#333);border-radius:10px;box-shadow:0 6px 20px rgba(0,0,0,.4);font-size:13px;color:var(--text-primary,#eee);";
  bar.setAttribute("role","status");
  bar.innerHTML = '<span>' + escapeHtml(msg) + '</span>' +
    (canUndo ? '<button type="button" class="btn ghost" style="font-size:12px;padding:3px 10px;" id="_trash-undo-btn">' + escapeHtml(t("trash.undo_hint")||"撤销") + '</button>' : '') +
    '<button type="button" class="btn ghost" style="font-size:12px;padding:3px 10px;" id="_trash-view-btn">' + escapeHtml(t("trash.view_trash")||"查看垃圾箱") + '</button>' +
    '<button type="button" style="background:none;border:none;color:var(--text-tertiary,#888);cursor:pointer;font-size:14px;" aria-label="Close">&times;</button>';
  document.body.appendChild(bar);
  bar.querySelector('[aria-label="Close"]').addEventListener("click", function() { bar.remove(); });
  var undoBtn = bar.querySelector("#_trash-undo-btn");
  if (undoBtn) undoBtn.addEventListener("click", function() { bar.remove(); _wizardUndoTrash(); });
  var viewBtn = bar.querySelector("#_trash-view-btn");
  if (viewBtn) viewBtn.addEventListener("click", function() { bar.remove(); _enterWizardTrashView(); });
  setTimeout(function() { if (bar.parentNode) bar.remove(); }, 12000);
}

function _wizardUndoTrash() {
  if (!_trashUndoState || !_trashUndoState.targets.length) return;
  var targets = _trashUndoState.targets.slice();
  _trashUndoState = null;
  var ok = 0, fail = 0;
  _boundedSettle(targets, 4, function(target) {
    return candidateApi("/structure-sources/" + encodeURIComponent(target.source_uid) + "/candidate/status", {
      method:"PATCH", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({status:"active", expected_revision:target.status_revision})
    }).then(function(updated) {
      if (target.wasInInput && target.priorEntry) {
        var existing = wizardStructures.some(function(s) { return s && s.source_uid === target.source_uid; });
        if (!existing) {
          var restored = Object.assign({}, target.priorEntry, {status_revision:updated.status_revision, usage_status:"active"});
          var at = Math.max(0, Math.min(Number(target.inputIndex) || 0, wizardStructures.length));
          wizardStructures.splice(at, 0, restored);
        }
      }
      return updated;
    });
  }).then(function(results) {
    for (var i = 0; i < results.length; i++) { if (results[i] && results[i].ok) ok++; else fail++; }
    renderLoadedStructuresList(); _syncPickerSelection();
    if (resultsPicker) resultsPicker.refresh();
    if (candidatePicker) candidatePicker.refresh();
    if (fail === 0) { _showSimpleToast(t("trash.undo_ok") || "已撤销"); }
    else { _showSimpleToast(t("trash.undo_failed", {message:fail+" failed"}) || ("撤销失败：" + fail + " 项"), true); }
  });
}

function _showSimpleToast(msg, isError) {
  var el = document.createElement("div");
  el.setAttribute("role", isError ? "alert" : "status");
  el.setAttribute("aria-live", isError ? "assertive" : "polite");
  el.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:9999;padding:10px 18px;background:" + (isError?"#3a1c1c":"var(--bg-elevated,#1e1e2e)") + ";border:1px solid " + (isError?"var(--red,#f87171)":"var(--border-default,#333)") + ";border-radius:10px;font-size:13px;color:var(--text-primary,#eee);";
  el.textContent = msg; document.body.appendChild(el);
  setTimeout(function() { if (el.parentNode) el.remove(); }, 4000);
}

// --- Trash view for wizard step-1 workspace ---
function _enterWizardTrashView() {
  if (_wizardTrashView) return;
  var sourceTab = wizardInputMode;
  var scrollEl = document.getElementById("results-list") || document.getElementById("candidate-list");
  _wizardSavedViewState = {sourceTab:sourceTab, sourceGroup:librarySourceGroup, scrollTop:scrollEl ? scrollEl.scrollTop : 0};
  _wizardTrashView = true;
  _wizardTrashSelection.clear();
  _wizardTrashSnapshots = {};
  librarySourceGroup = null;
  document.querySelectorAll(".library-filter").forEach(function(button) {
    button.classList.toggle("active", !(button.getAttribute("data-source-group") || null));
    button.setAttribute("aria-pressed", !(button.getAttribute("data-source-group") || null) ? "true" : "false");
  });
  var picker = sourceTab === "candidate" ? candidatePicker : resultsPicker;
  // Guard: setUsageStatus clears the picker's internal selection, which fires onChanged.
  // Suppress reconcile to protect wizardStructures.
  _inputReconciling = true;
  try {
    if (picker) {
      if (typeof picker.setFilters === "function") {
        picker.setFilters({sourceGroup:null, usageStatus:"trash"});
      } else {
        picker.setSourceGroup(null);
        picker.setUsageStatus("trash");
      }
    }
  } finally { _inputReconciling = false; }
  _renderTrashHeaderView();
}

function _exitWizardTrashView() {
  if (!_wizardTrashView) return;
  _wizardTrashView = false;
  _wizardTrashSelection.clear();
  _wizardTrashSnapshots = {};
  var saved = _wizardSavedViewState;
  _wizardSavedViewState = null;
  librarySourceGroup = saved && saved.sourceGroup ? saved.sourceGroup : null;
  var picker = (saved && saved.sourceTab === "candidate") ? candidatePicker : resultsPicker;
  if (picker) {
    _inputReconciling = true;
    try {
      picker.setUsageStatus("active");
      picker.setSourceGroup(librarySourceGroup);
      var TIW = window.ACPTaskInputWorkspace;
      if (TIW) {
        picker.setSelection(wizardStructures, {silent:true});
      }
    } finally { _inputReconciling = false; }
    if (saved && saved.scrollTop) {
      var scrollEl = document.getElementById("results-list") || document.getElementById("candidate-list");
      if (scrollEl) scrollEl.scrollTop = saved.scrollTop;
    }
  }
  document.querySelectorAll(".library-filter").forEach(function(button) {
    var active = (button.getAttribute("data-source-group") || null) === librarySourceGroup;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  _renderTrashHeaderView();
}

function _renderTrashHeaderView() {
  var header = document.getElementById("wizard-trash-header");
  if (!header) return;
  if (!_wizardTrashView) { header.style.display = "none"; header.innerHTML = ""; return; }
  header.style.display = "flex";
  header.innerHTML = '<button type="button" class="btn ghost" id="wizard-trash-back">' + escapeHtml(t("trash.view_back")||"返回结构列表") + '</button>' +
    '<span id="wizard-trash-count" style="flex:1;font-size:12px;color:var(--text-secondary);"></span>' +
    '<button type="button" class="btn ghost" id="wizard-trash-batch-restore" style="display:none;">' + escapeHtml(t("trash.batch_restore")||"恢复所选") + '</button>' +
    '<button type="button" class="btn danger" id="wizard-trash-batch-purge" style="display:none;">' + escapeHtml(t("trash.batch_purge")||"彻底移除所选") + '</button>' +
    '<button type="button" class="btn danger" id="wizard-trash-empty-project">' + escapeHtml(t("trash.empty_project_trash")||"清空当前项目垃圾箱") + '</button>';
  var backBtn = document.getElementById("wizard-trash-back");
  if (backBtn) backBtn.addEventListener("click", function() { _exitWizardTrashView(); });
  var trashRestoreBtn = document.getElementById("wizard-trash-batch-restore");
  var trashPurgeBtn = document.getElementById("wizard-trash-batch-purge");
  if (trashRestoreBtn) trashRestoreBtn.addEventListener("click", function() { _wizardTrashRestoreSelected(); });
  if (trashPurgeBtn) trashPurgeBtn.addEventListener("click", function() { _wizardTrashPurgeSelected(); });
  var emptyBtn = document.getElementById("wizard-trash-empty-project");
  if (emptyBtn) emptyBtn.addEventListener("click", function() { _wizardEmptyProjectTrash(); });
  _updateTrashBatchButtons();
}

function _updateTrashBatchButtons() {
  var count = _wizardTrashSelection.size;
  var countEl = document.getElementById("wizard-trash-count");
  var picker = wizardInputMode === "candidate" ? candidatePicker : resultsPicker;
  var total = picker && typeof picker.getTotal === "function" ? picker.getTotal() : 0;
  if (countEl) countEl.textContent = "垃圾箱 " + total + " 项 · 已选 " + count + " 项";
  var restoreBtn = document.getElementById("wizard-trash-batch-restore");
  var purgeBtn = document.getElementById("wizard-trash-batch-purge");
  if (restoreBtn) restoreBtn.style.display = count > 0 ? "inline-flex" : "none";
  if (purgeBtn) purgeBtn.style.display = count > 0 ? "inline-flex" : "none";
}

function _setWizardTrashSelection(picker, snapshots) {
  snapshots = Array.isArray(snapshots) ? snapshots : [];
  _wizardTrashSelection.clear();
  _wizardTrashSnapshots = {};
  snapshots.forEach(function(snapshot) {
    var uid = typeof snapshot === "string" ? snapshot : (snapshot && snapshot.source_uid) || "";
    if (!uid) return;
    _wizardTrashSelection.add(uid);
    if (snapshot && typeof snapshot === "object") _wizardTrashSnapshots[uid] = snapshot;
  });
  if (picker && typeof picker.setSelection === "function") picker.setSelection(snapshots, {silent:true});
  _updateTrashBatchButtons();
}

function _wizardTrashRestoreSelected() {
  var uids = Array.from(_wizardTrashSelection);
  if (!uids.length) return;
  var ok = 0, fail = 0;
  _boundedSettle(uids, 4, function(uid) {
    // Use a GET to fetch current state
    return candidateApi("/structure-sources/" + encodeURIComponent(uid) + "/candidate").then(function(current) {
      return candidateApi("/structure-sources/" + encodeURIComponent(uid) + "/candidate/status", {
        method:"PATCH", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({status:"active", expected_revision:current.status_revision || 0})
      });
    });
  }).then(function(results) {
    for (var i = 0; i < results.length; i++) { if (results[i] && results[i].ok) ok++; else fail++; }
    var picker = wizardInputMode === "candidate" ? candidatePicker : resultsPicker;
    var failedSnapshots = uids.filter(function(uid, i) { return !results[i] || !results[i].ok; }).map(function(uid) { return _wizardTrashSnapshots[uid] || uid; });
    _setWizardTrashSelection(picker, failedSnapshots);
    if (picker) picker.refresh();
    if (fail === 0) { _showSimpleToast(t("trash.restore_ok", {count:ok}) || ("已恢复 " + ok + " 项")); }
    else { _showSimpleToast(t("trash.restore_partial", {ok:ok, failed:fail}) || ("恢复 " + ok + " 项，失败 " + fail + " 项"), true); }
  });
}

function _wizardTrashPurgeSelected() {
  var uids = Array.from(_wizardTrashSelection);
  if (!uids.length) return;
  var message = t("trash.purge_confirm", {count:uids.length}) || ("将从当前项目永久移除 " + uids.length + " 个候选。无法通过垃圾箱恢复；来源任务和原始文件保留。");
  if (!window.confirm(message)) return;
  var groups = {};
  uids.forEach(function(uid) {
    var snap = _wizardTrashSnapshots[uid] || {};
    var pid = snap.project_id || resultsProjectTargetId() || "uncategorized";
    if (!groups[pid]) groups[pid] = [];
    groups[pid].push(uid);
  });
  var frozenGroups = Object.keys(groups).map(function(pid) { return {project_id:pid, source_uids:groups[pid].slice()}; });
  _boundedSettle(frozenGroups, 3, function(group) {
    return candidateApi("/structure-sources/trash/purge", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify(group)
    });
  }).then(function(results) {
    var removedUids = new Set();
    results.forEach(function(result) {
      if (!result || !result.ok) return;
      var removed = result.value && Array.isArray(result.value.removed) ? result.value.removed : [];
      removed.forEach(function(uid) { removedUids.add(uid); });
    });
    var failed = results.filter(function(result) { return !result || !result.ok; }).length;
    var notRemoved = uids.length - removedUids.size;
    var failedSnapshots = uids.filter(function(uid) { return !removedUids.has(uid); }).map(function(uid) { return _wizardTrashSnapshots[uid] || uid; });
    var picker = wizardInputMode === "candidate" ? candidatePicker : resultsPicker;
    _setWizardTrashSelection(picker, failedSnapshots);
    if (picker) picker.refresh();
    if (failed || notRemoved) _showSimpleToast("部分删除：已永久移除 " + removedUids.size + " 项，" + notRemoved + " 项仍在垃圾箱", true);
    else _showSimpleToast(t("trash.purge_ok", {count:removedUids.size}) || ("已永久移除 " + removedUids.size + " 项"));
  }).catch(function(err) { window.alert("Purge failed: " + err.message); });
}

function _wizardEmptyProjectTrash() {
  var projectForEmpty = resultsProjectTargetId();
  if (!projectForEmpty) { window.alert(t("trash.purge_no_project") || "请先选择一个项目再清空垃圾箱"); return; }
  // Resolve all trash UIDs for this project at confirm time
  var allUids = []; var seenUids = {}; var pageSize = 100; var cursor = null;
  function fetchPage() {
    var params = new URLSearchParams({usage_status:"trash", limit:String(pageSize), project_id:projectForEmpty});
    if (cursor) params.set("cursor", cursor);
    return candidateApi("/structure-sources?" + params.toString()).then(function(response) {
      var items = (response && response.items) || [];
      for (var i = 0; i < items.length; i++) {
        var uid = items[i] && items[i].source_uid;
        if (uid && !seenUids[uid]) { seenUids[uid] = true; allUids.push(uid); }
      }
      cursor = response && response.next_cursor ? response.next_cursor : null;
      if (cursor) return fetchPage();
      return allUids;
    });
  }
  fetchPage().then(function(uids) {
    if (!uids.length) { window.alert(t("trash.purge_empty") || "当前项目垃圾箱为空"); return; }
    var message = t("trash.purge_confirm", {count:uids.length}) || ("将从当前项目永久移除 " + uids.length + " 个候选。无法通过垃圾箱恢复；来源任务和原始文件保留。");
    if (!window.confirm(message)) return;
    return candidateApi("/structure-sources/trash/purge", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({project_id:projectForEmpty, source_uids:uids})
    }).then(function(result) {
      var removed = result && Array.isArray(result.removed) ? result.removed : [];
      var remainingSnapshots = Array.from(_wizardTrashSelection).filter(function(uid) { return removed.indexOf(uid) < 0; }).map(function(uid) { return _wizardTrashSnapshots[uid] || uid; });
      var picker = wizardInputMode === "candidate" ? candidatePicker : resultsPicker;
      _setWizardTrashSelection(picker, remainingSnapshots);
      if (picker) picker.refresh();
      _showSimpleToast(t("trash.purge_ok", {count:(result && result.count) || removed.length}) || ("已永久移除 " + removed.length + " 项"));
    });
  }).catch(function(err) { window.alert("Purge failed: " + err.message); });
}

function _wizardTrashRowMenu(snapshot, anchorEl) {
  _closeActiveRowMenu();
  var menu = document.createElement("div");
  menu.className = "sp-row-popup-menu";
  menu.setAttribute("role", "menu");
  menu.style.cssText = "position:absolute;z-index:200;min-width:180px;padding:4px 0;background:var(--bg-elevated,#1e1e2e);border:1px solid var(--border-default,#333);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.45);font-size:12px;";
  var items = [
    {key:"restore", label:t("trash.row_menu.restore")||"恢复候选"},
    {key:"purge", label:t("trash.row_menu.purge")||"彻底移除", danger:true}
  ];
  menu.innerHTML = items.map(function(m, i) {
    return '<div role="menuitem" tabindex="' + (i===0?'0':'-1') + '" data-action="' + m.key + '"' + (m.danger?' style="padding:6px 14px;cursor:pointer;color:var(--red,#f87171);white-space:nowrap;"':' style="padding:6px 14px;cursor:pointer;color:var(--text-primary,#eee);white-space:nowrap;"') + '>' + escapeHtml(m.label) + '</div>';
  }).join("");
  var rect = anchorEl.getBoundingClientRect();
  menu.style.left = (rect.left + window.scrollX) + "px"; menu.style.top = (rect.bottom + window.scrollY + 4) + "px";
  document.body.appendChild(menu);
  var mrect = menu.getBoundingClientRect(); if (mrect.right > window.innerWidth - 8) menu.style.left = (window.innerWidth - mrect.width - 8 + window.scrollX) + "px";
  var firstItem = menu.querySelector('[role="menuitem"]'); if (firstItem) firstItem.focus();
  function onKey(e) {
    var its = Array.from(menu.querySelectorAll('[role="menuitem"]'));
    var ci = its.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); cleanup(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); its[(ci+1)%its.length].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); its[(ci-1+its.length)%its.length].focus(); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (document.activeElement && document.activeElement.dataset.action) { cleanup(); _handleTrashRowMenuAction(document.activeElement.dataset.action, snapshot); } }
  }
  function onClick(e) {
    if (menu.contains(e.target)) { var mi = e.target.closest('[role="menuitem"]'); if (mi && mi.dataset.action) { cleanup(); _handleTrashRowMenuAction(mi.dataset.action, snapshot); } return; }
    cleanup();
  }
  function cleanup() { menu.remove(); document.removeEventListener("keydown", onKey, true); document.removeEventListener("mousedown", onClick, true); _activeRowMenu = null; }
  document.addEventListener("keydown", onKey, true);
  document.addEventListener("mousedown", onClick, true);
  _activeRowMenu = {el:menu, cleanup:cleanup};
}

function _handleTrashRowMenuAction(action, snapshot) {
  if (action === "restore") {
    candidateApi("/structure-sources/" + encodeURIComponent(snapshot.source_uid) + "/candidate").then(function(current) {
      return candidateApi("/structure-sources/" + encodeURIComponent(snapshot.source_uid) + "/candidate/status", {
        method:"PATCH", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({status:"active", expected_revision:current.status_revision || 0})
      });
    }).then(function() {
      var picker = wizardInputMode === "candidate" ? candidatePicker : resultsPicker;
      var remaining = Array.from(_wizardTrashSelection).filter(function(uid) { return uid !== snapshot.source_uid; }).map(function(uid) { return _wizardTrashSnapshots[uid] || uid; });
      _setWizardTrashSelection(picker, remaining);
      if (picker) picker.refresh();
      _showSimpleToast(t("trash.restore_ok", {count:1}) || "已恢复 1 项");
    }).catch(function(err) { window.alert("Restore failed: " + err.message); });
  } else if (action === "purge") {
    var projectForPurge = snapshot.project_id || resultsProjectTargetId() || "uncategorized";
    var msg = t("trash.purge_confirm", {count:1}) || "将永久移除此候选。无法通过垃圾箱恢复；来源任务和原始文件保留。";
    if (!window.confirm(msg)) return;
    candidateApi("/structure-sources/trash/purge", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({project_id:projectForPurge, source_uids:[snapshot.source_uid]})
    }).then(function(result) {
      var picker = wizardInputMode === "candidate" ? candidatePicker : resultsPicker;
      var removed = result && Array.isArray(result.removed) ? result.removed : [];
      var remaining = Array.from(_wizardTrashSelection).filter(function(uid) { return removed.indexOf(uid) < 0; }).map(function(uid) { return _wizardTrashSnapshots[uid] || uid; });
      _setWizardTrashSelection(picker, remaining);
      if (picker) picker.refresh();
      _showSimpleToast(t("trash.purge_ok", {count:(result && result.count) || removed.length}) || ("已永久移除 " + removed.length + " 项"));
    }).catch(function(err) { window.alert("Purge failed: " + err.message); });
  }
}

function _syncPickerSelection() {
  var TIW = window.ACPTaskInputWorkspace;
  if (!TIW) return;
  // Preserve complete snapshots across source tabs and pages. Passing only UIDs
  // makes the inactive picker forget entries it cannot currently resolve.
  var snapshots = wizardStructures.filter(function(entry) { return entry && entry.source_uid; });
  if (candidatePicker) candidatePicker.setSelection(snapshots, { silent: true });
  if (resultsPicker) resultsPicker.setSelection(snapshots, { silent: true });
}
var batchPreviewItems = [];     // batch structure-source panel items
var structureSourceLoadController = null;
var structureSourceLoadToken = 0;

function resetResultsPanel() {
  if (resultsPicker) { resultsPicker.destroy(); resultsPicker = null; }
  resultsSources = [];
  resultsLoaded = false;
  _wizardTrashView = false; _wizardTrashSelection.clear(); _closeActiveRowMenu();
  var trashHeader = document.getElementById("wizard-trash-header");
  if (trashHeader) { trashHeader.style.display = "none"; trashHeader.innerHTML = ""; }
  var searchEl = document.getElementById("results-search");
  if (searchEl) searchEl.value = "";
  var projEl = document.getElementById("results-project");
  if (projEl) projEl.value = "target";
  var hintEl = document.getElementById("results-cross-hint");
  if (hintEl) { hintEl.style.display = "none"; hintEl.textContent = ""; }
  var list = document.getElementById("results-list");
  if (list) list.innerHTML = "";
}

function resetCandidatePanel() {
  if (candidatePicker) { candidatePicker.destroy(); candidatePicker = null; }
  if (candidateDetailsPanel) candidateDetailsPanel.setEntry(null);
  candidateLoaded = false;
  _wizardTrashView = false; _wizardTrashSelection.clear(); _closeActiveRowMenu();
  var trashHeader = document.getElementById("wizard-trash-header");
  if (trashHeader) { trashHeader.style.display = "none"; trashHeader.innerHTML = ""; }
  var list = document.getElementById("candidate-list");
  if (list) list.innerHTML = "";
}

async function loadCandidateSources(force) {
  if (candidateLoaded && !force && candidatePicker) return;
  var list = document.getElementById("candidate-list");
  if (!list) return;

  if (!candidatePicker && typeof ACPSourcePicker !== "undefined") {
    candidatePicker = ACPSourcePicker.mount(list, {
      mode: "multi",
      density: "editor",
      sourceGroup: "candidate",
      toolbarLabel: t("modal.mode_candidate") || "候选结构",
      toolbarHost: document.getElementById("tiw-toolbar-host"),
      projectId: resultsProjectTargetId(),
      onChanged: function(selection) {
        if (_inputReconciling) return;
        // In trash mode, track selections separately — never reconcile into wizardStructures
        if (_wizardTrashView) {
          _wizardTrashSelection.clear();
          _wizardTrashSnapshots = {};
          if (Array.isArray(selection)) selection.forEach(function(s) { var uid = typeof s === "string" ? s : (s && s.source_uid) || ""; if (uid) { _wizardTrashSelection.add(uid); if (s && typeof s === "object") _wizardTrashSnapshots[uid] = s; } });
          _updateTrashBatchButtons();
          return;
        }
        _inputReconciling = true;
        try {
          var TIW = window.ACPTaskInputWorkspace;
          structureSourceLoadToken++;
          var focusedInput = wizardStructures[wizardSelectedStructureIndex];
          var priorUids = TIW.derivedSelectedUids(wizardStructures);
          wizardStructures = TIW.reconcileSelection(wizardStructures, selection);
          var added = wizardStructures.filter(function(e) { return e.source_uid && priorUids.indexOf(e.source_uid) < 0; });
          var focus = added.length === 1 ? added[0] : focusedInput;
          var focusIndex = wizardStructures.findIndex(function(e) { return focus && e.input_id === focus.input_id; });
          wizardSelectedStructureIndex = focusIndex >= 0 ? focusIndex : 0;
          renderLoadedStructuresList();
          _refreshWizardInputForm(wizardStructures[wizardSelectedStructureIndex] || null);
          updateFooterSelectedCount();
          _loadPendingGeometry();
          _syncPickerSelection();
        } finally {
          _inputReconciling = false;
        }
      },
      onPreviewItem: function(item) {
        if (item.pin_kind === "input") {
          var index = wizardStructures.findIndex(function(e) { return e.input_id === item.input_id; });
          if (index >= 0) selectPreviewStructure(index);
          return true;
        }
        if (candidateDetailsPanel && candidateDetailsPanel.setEntry(item) === false) return false;
        stagePESsearchSource(item);
        previewStructureSource(item.source_id || item.source_uid, item.source_uid);
        return true;
      },
      onBatchTrash: function(items) { _wizardMoveToTrash(items || []); },
      onDataChanged: function() { if (_wizardTrashView) _updateTrashBatchButtons(); },
      onRowMenu: function(snapshot, anchorEl) {
        if (_wizardTrashView) { _wizardTrashRowMenu(snapshot, anchorEl); }
        else { _openRowMenu(snapshot, anchorEl); }
      },
      isItemLoaded: function(item) {
        return wizardStructures.some(function(structure) {
          return structure && structure.source_uid === item.source_uid;
        });
      },
    });
    candidatePicker.setSelection(wizardStructures, {silent:true});
    _loadPendingGeometry();
    candidateLoaded = true;
    return;
  }

  if (candidatePicker) candidatePicker.refresh();
}

async function loadStructureSources(force) {
  if (resultsLoaded && !force && resultsPicker) {
    if (window.ACPJobEditor && ACPJobEditor.isActive()) {
      resultsPicker.setSelection(wizardStructures, {silent:true});
    }
    return;
  }
  var list = document.getElementById("results-list");
  if (!list) return;

  // Mount picker if not already mounted
  if (!resultsPicker && typeof ACPSourcePicker !== "undefined") {
    resultsPicker = ACPSourcePicker.mount(list, {
      mode: "multi",
      density: "editor",
      sourceGroup: null,
      toolbarLabel: t("modal.mode_library") || "结构库",
      toolbarHost: document.getElementById("tiw-toolbar-host"),
      projectId: resultsProjectTargetId(),
      getPinnedSources: function() { return window.ACPJobEditor ? ACPJobEditor.getPinnedSources() : []; },
      onUsePinnedOutput: function(item) { ACPJobEditor.usePreviousOutput(item); },
      getContextWorkflow: function() { return wizardState.workflow && wizardState.workflow.id || null; },
      getRowAnnotation: function(item) {
        if (window.ACPJobEditor && ACPJobEditor.isActive() && typeof ACPJobEditor.getRecalcSourceAnnotation === "function") {
          return ACPJobEditor.getRecalcSourceAnnotation(item);
        }
        var isInput = Array.isArray(wizardStructures) && wizardStructures.some(function(s) {
          return s && (s.source_uid === item.source_uid || (s.source_id && s.source_id === item.source_id));
        });
        var refJobId = (window._pendingNewTask && window._pendingNewTask.sourceJobId) ||
                       (typeof lastSelectedJob !== "undefined" && lastSelectedJob && (lastSelectedJob.id || lastSelectedJob.job_id)) || "";
        var isFromRefJob = !!(refJobId && (item.job_id === refJobId || item.job_name === refJobId));
        var isCandidate = !!(item.candidate_id && item.candidate_id !== "--") || item.source_group === "candidate" ||
          (item.structure_facts && item.structure_facts.source_kind === "manual_frame");
        return {
          input: isInput,
          output: isFromRefJob,
          previous_result: isFromRefJob,
          is_candidate: isCandidate,
        };
      },
      onChanged: function(selection) {
        if (_inputReconciling) return;
        if (_wizardTrashView) {
          _wizardTrashSelection.clear();
          _wizardTrashSnapshots = {};
          if (Array.isArray(selection)) selection.forEach(function(s) { var uid = typeof s === "string" ? s : (s && s.source_uid) || ""; if (uid) { _wizardTrashSelection.add(uid); if (s && typeof s === "object") _wizardTrashSnapshots[uid] = s; } });
          _updateTrashBatchButtons();
          return;
        }
        _inputReconciling = true;
        try {
          var TIW = window.ACPTaskInputWorkspace;
          structureSourceLoadToken++;
          var focusedInput = wizardStructures[wizardSelectedStructureIndex];
          var priorUids = TIW.derivedSelectedUids(wizardStructures);
          wizardStructures = TIW.reconcileSelection(wizardStructures, selection);
          var added = wizardStructures.filter(function(e) { return e.source_uid && priorUids.indexOf(e.source_uid) < 0; });
          var focus = added.length === 1 ? added[0] : focusedInput;
          var focusIndex = wizardStructures.findIndex(function(e) { return focus && e.input_id === focus.input_id; });
          wizardSelectedStructureIndex = focusIndex >= 0 ? focusIndex : 0;
          renderLoadedStructuresList();
          _refreshWizardInputForm(wizardStructures[wizardSelectedStructureIndex] || null);
          updateFooterSelectedCount();
          _loadPendingGeometry();
          _syncPickerSelection();
        } finally {
          _inputReconciling = false;
        }
      },
      onPreviewItem: function(item) {
        if (item.pin_kind === "input") {
          var index = wizardStructures.findIndex(function(e) { return e.input_id === item.input_id; });
          if (index >= 0) selectPreviewStructure(index);
          return true;
        }
        if (candidateDetailsPanel && candidateDetailsPanel.setEntry(item) === false) return false;
        stagePESsearchSource(item);
        if (window.ACPJobEditor && typeof ACPJobEditor.onExternalSourcePreview === "function") {
          ACPJobEditor.onExternalSourcePreview(item);
        }
        previewStructureSource(item.source_id || item.source_uid, item.source_uid);
        return true;
      },
      onBatchTrash: function(items) { _wizardMoveToTrash(items || []); },
      onDataChanged: function() { if (_wizardTrashView) _updateTrashBatchButtons(); },
      onRowMenu: function(snapshot, anchorEl) {
        if (_wizardTrashView) { _wizardTrashRowMenu(snapshot, anchorEl); }
        else { _openRowMenu(snapshot, anchorEl); }
      },
      isItemLoaded: function(item) {
        return wizardStructures.some(function(structure) {
          return structure && structure.source_uid === item.source_uid;
        });
      },
    });
    resultsPicker.setSelection(wizardStructures, {silent:true});
    _loadPendingGeometry();
    // Wire existing search input into picker
    var searchEl = document.getElementById("results-search");
    if (searchEl) {
      searchEl.addEventListener("input", function() {
        if (resultsPicker) resultsPicker.refresh();
      });
    }
    // Wire existing project select into picker
    var projEl = document.getElementById("results-project");
    if (projEl) {
      projEl.addEventListener("change", function() {
        var mode = projEl.value;
        if (mode === "all") {
          if (resultsPicker) resultsPicker.setProject(null);
        } else if (mode === "target") {
          if (resultsPicker) resultsPicker.setProject(resultsProjectTargetId());
        } else {
          if (resultsPicker) resultsPicker.setProject(mode);
        }
      });
    }
    resultsLoaded = true;
    return;
  }

  // Fallback: if picker not available, use legacy behavior
  if (!resultsPicker) {
    list.innerHTML = '<div class="results-list-note">' + escapeHtml(t("results.loading")) + "</div>";
    var url = "/structure-sources/recent?limit=50&_t=" + Date.now();
    var projEl = document.getElementById("results-project");
    var mode = projEl && projEl.value ? projEl.value : "target";
    if (mode === "all") {
      url += "&all_projects=true";
    } else if (mode === "target") {
      url += "&project_id=" + encodeURIComponent(resultsProjectTargetId());
    } else {
      url += "&project_id=" + encodeURIComponent(mode);
    }
    try {
      var body = await apiRemote(url);
      resultsSources = (body && body.sources) || [];
      resultsLoaded = true;
      renderResultsList();
    } catch (e) {
      list.innerHTML = '<div class="results-list-note err">' + escapeHtml(t("results.error")) + ": " + escapeHtml(e.message || String(e)) + "</div>";
    }
    return;
  }

  // Picker already mounted, just refresh
  resultsPicker.refresh();
  resultsLoaded = true;
}

function setLibrarySourceGroup(group) {
  librarySourceGroup = (group === "candidate" || group === "task_result") ? group : null;
  document.querySelectorAll(".library-filter").forEach(function(button) {
    var active = (button.getAttribute("data-source-group") || null) === librarySourceGroup;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  if (_wizardTrashView) {
    var picker = wizardInputMode === "candidate" ? candidatePicker : resultsPicker;
    if (picker) picker.setSourceGroup(librarySourceGroup);
  } else if (resultsPicker) {
    resultsPicker.setSourceGroup(librarySourceGroup);
  }
}

function scheduleResultsSearch() {
  if (resultsSearchTimer) clearTimeout(resultsSearchTimer);
  resultsSearchTimer = setTimeout(function() {
    resultsSearchTimer = null;
    renderResultsList();
  }, 300);
}

function sourceMoleculeName(source) {
  if (!source) return "";
  var name = String(source.molecule_name || source.label || source.job_name || "").trim();
  var candidateId = String(source.candidate_id || "").trim();
  if (!candidateId || !name) return name;
  // Older servers returned the Job-level molecule name in the source list.
  // Keep the candidate identity visible in that compatibility path without
  // duplicating the suffix returned by the current API.
  if (name.toLowerCase().indexOf(candidateId.toLowerCase()) < 0) {
    name += "__" + candidateId;
  }
  return name;
}

function renderResultsList() {
  var list = document.getElementById("results-list");
  if (!list) return;
  var searchEl = document.getElementById("results-search");
  var q = (searchEl && searchEl.value || "").trim().toLowerCase();
  var items = resultsSources.filter(function(src) {
    if (!q) return true;
    var hay = (src.molecule_name || "") + " " + (src.job_name || "") + " " + (src.label || "") + " " + (src.formula || "") + " " + (src.workflow || "") + " " + (src.candidate_id || "") + " " + (src.job_status || "") + " " + projectNameOf(src.project_id);
    return hay.toLowerCase().indexOf(q) >= 0;
  });
  var projEl = document.getElementById("results-project");
  var mode = projEl && projEl.value ? projEl.value : "target";
  updateResultsCrossHint(mode);
  if (!items.length) {
    // Empty state must explain WHY: effective project scope + the
    // manifest-only source policy (P2: silent filtering confused users).
    var scopeText = mode === "all"
      ? t("results.scope_all")
      : t("results.scope_project", { project: projectNameOf(mode === "target" ? resultsProjectTargetId() : mode) });
    list.innerHTML = '<div class="results-list-note">' + escapeHtml(t("results.empty")) +
      '<br><span class="muted">' + escapeHtml(scopeText) + " · " + escapeHtml(t("results.empty_hint")) + "</span></div>";
    return;
  }
  var html = "";
  if (mode === "all") {
    // 全部项目: group rows by project so cross-project sources stay scannable.
    var groups = {};
    items.forEach(function(src) {
      var pid = String(src.project_id || "uncategorized");
      (groups[pid] = groups[pid] || []).push(src);
    });
    Object.keys(groups)
      .sort(function(a, b) { return projectNameOf(a).localeCompare(projectNameOf(b)); })
      .forEach(function(pid) {
        html += '<div class="results-group-label">' + escapeHtml(projectNameOf(pid)) + "</div>";
        html += groups[pid].map(function(src) { return resultSourceRowMarkup(src, true); }).join("");
      });
  } else {
    html = items.map(function(src) { return resultSourceRowMarkup(src, false); }).join("");
  }
  list.innerHTML = html;
  list.querySelectorAll(".result-load-btn").forEach(function(btn) {
    btn.addEventListener("click", function() {
      var row = btn.closest(".result-source-row");
      var sid = row ? row.getAttribute("data-source-id") : "";
      if (sid) loadStructureSource(sid, row);
    });
  });
}

function updateResultsCrossHint(mode) {
  var el = document.getElementById("results-cross-hint");
  if (!el) return;
  var targetId = resultsProjectTargetId();
  var text = "";
  if (mode !== "all" && mode !== "target" && mode !== targetId) {
    text = t("results.cross_project", { source: projectNameOf(mode), target: projectNameOf(targetId) });
  }
  el.textContent = text;
  el.style.display = text ? "block" : "none";
}

function resultStatusBadge(src) {
  var status = String(src.job_status || "");
  if (!status) return "";
  if (status === "failed") {
    return ' <span class="badge failed">' + escapeHtml(t("results.status_failed_saved")) + "</span>";
  }
  if (status === "cancelled") {
    return ' <span class="badge cancelled">' + escapeHtml(t("results.status_cancelled_saved")) + "</span>";
  }
  if (status === "completed") {
    return ' <span class="badge completed">' + escapeHtml(t("results.status_completed")) + "</span>";
  }
  return "";
}

function resultSourceRowMarkup(src, showProject) {
  var when = (src.available_at || src.completed_at) ? formatDateTime(src.available_at || src.completed_at) : "--";
  var sourceTitle = [
    src.label || "",
    src.formula ? (src.formula + " · " + (src.atom_count || 0) + " atoms") : "",
    "q=" + (src.charge !== undefined ? src.charge : 0) + " / mult=" + (src.multiplicity || 1)
  ].filter(Boolean).join(" · ");
  var locBadge = src.remote
    ? '<span class="badge waiting_review">' + escapeHtml(t("results.remote")) + "</span>"
    : '<span class="badge completed">' + escapeHtml(t("results.local")) + "</span>";
  var tagBadge = "";
  if (src.tag === "TS") {
    tagBadge = ' <span class="badge" style="color:#0f0">TS</span>';
  }
  var projectBadge = showProject && src.project_id
    ? ' <span class="badge">' + escapeHtml(projectNameOf(src.project_id)) + "</span>"
    : "";
  return '<div class="result-source-row' + (src.needs_fetch ? " needs-fetch" : "") + '" data-source-id="' + escapeHtml(src.source_id) + '" title="' + escapeHtml(sourceTitle) + '">' +
    '<div class="result-source-main">' +
      "<strong>" + escapeHtml(sourceMoleculeName(src) || src.job_id || "--") + "</strong>" +
      '<span class="badge">' + escapeHtml(src.workflow || "--") + "</span>" +
      locBadge +
      resultStatusBadge(src) +
      projectBadge +
      tagBadge +
      '<span class="muted">' + escapeHtml(when) + "</span>" +
    "</div>" +
    '<div class="result-source-actions">' +
      '<span class="result-row-error" style="display:none;"></span>' +
      '<button type="button" class="btn ghost result-load-btn"' + (src.needs_fetch ? " disabled" : "") + '>' + escapeHtml(t("results.load")) + "</button>" +
    "</div>" +
  "</div>";
}

function structureFromSourceDetail(a, sourceId, sourceMetaOverride) {
  var jobId = "";
  var relPath = "";
  var sep = String(sourceId).indexOf(":");
  if (sep > 0) {
    jobId = String(sourceId).slice(0, sep).replace(/^job_/, "");
    relPath = String(sourceId).slice(sep + 1);
  }
  var sourceMeta = sourceMetaOverride || resultsSources.find(function(source) { return source.source_id === sourceId; });
  var importedMoleculeName = String(a.molecule_name || "").trim();
  if (!importedMoleculeName && sourceMeta) {
    importedMoleculeName = sourceMoleculeName(sourceMeta);
  }
  if (!importedMoleculeName && a.name) {
    importedMoleculeName = String(a.name).replace(/\.(xyz|gjf|sdf|mol|com|inp|log|out|json)$/i, "").trim();
  }
  return {
    asset_id: a.asset_id || "",
    source_id: sourceId,
    name: importedMoleculeName || "mol_1",
    molecule_name: importedMoleculeName || "mol_1",
    asset_name: a.name || "",
    source_type: "xyz_text",
    original_format: a.original_format || "xyz",
    xyz: a.xyz || "",
    molfile: a.molfile || "",
    has_3d: a.has_3d !== false,
    charge: a.charge !== undefined ? a.charge : 0,
    multiplicity: a.multiplicity || 1,
    atom_count: a.atom_count || 0,
    formula: a.formula || "",
    smiles: a.smiles || "",
    warnings: a.warnings || [],
    errors: a.errors || [],
    tag: a.tag || (sourceMeta && sourceMeta.tag) || "",
    candidate_id: a.candidate_id || (sourceMeta && sourceMeta.candidate_id) || "",
    source_uid: (sourceMeta && (sourceMeta.source_uid || sourceMeta.source_id)) || sourceId,
    candidate_record_id: (sourceMeta && sourceMeta.candidate_record_id) || "",
    version_id: (sourceMeta && sourceMeta.version_id) || "",
    geometry_hash: (sourceMeta && sourceMeta.geometry_hash) || "",
    assessment: (sourceMeta && sourceMeta.assessment) || "unreviewed",
    usage_status: (sourceMeta && sourceMeta.usage_status) || "active",
    status_reason: (sourceMeta && sourceMeta.status_reason) || "",
    source_ref: {
      origin: "job_artifact",
      job_id: jobId,
      source_id: sourceId,
      project_id: (sourceMeta && sourceMeta.project_id) || a.project_id || "",
      path: relPath,
      checksum: "",
      job_status: (sourceMeta && sourceMeta.job_status) || a.job_status || "",
      allow_disabled_use: !!(sourceMeta && sourceMeta.allow_disabled_use),
      disabled_use_reason: (sourceMeta && sourceMeta.disabled_use_reason) || ""
    }
  };
}

async function _resolveStructureSourceId(entry) {
  var sourceRef = entry && entry.source_ref ? entry.source_ref : {};
  var sourceId = String((entry && entry.source_id) || sourceRef.source_id || "").trim();
  var sourceUid = String((entry && entry.source_uid) || sourceRef.source_uid || "").trim();
  var isLegacySourceId = function(value) {
    return value.indexOf("job_") === 0 && value.indexOf(":") > 4;
  };
  if (!isLegacySourceId(sourceId) && isLegacySourceId(sourceUid)) sourceId = sourceUid;

  // Older saved drafts may contain only a v2 source_uid. Resolve the file
  // identity before calling the v1 geometry endpoint.
  if (!isLegacySourceId(sourceId)) {
    if (!sourceUid) throw new Error("结构来源缺少 source_uid/source_id");
    var projection = await apiV2("/structure-sources/" + encodeURIComponent(sourceUid));
    sourceId = String((projection && projection.source_id) || "").trim();
  }
  if (!isLegacySourceId(sourceId)) {
    throw new Error("无法从结构来源解析文件 source_id");
  }
  entry.source_id = sourceId;
  entry.source_ref = entry.source_ref || {};
  entry.source_ref.source_id = sourceId;
  return sourceId;
}

function _mergeLoadedGeometry(entry, struct, sourceId) {
  var xyz = String((struct && struct.xyz) || "").trim();
  if (!xyz || !parseXyzAtoms(xyz).length) {
    throw new Error("来源文件没有可用的 XYZ 三维坐标");
  }
  entry.source_id = sourceId;
  entry.xyz = struct.xyz;
  entry.smiles = struct.smiles || null;
  entry.normalized_path = struct.normalized_path || null;
  entry.has_3d = struct.has_3d !== false;
  entry.atom_count = struct.atom_count || entry.atom_count;
  entry.formula = struct.formula || entry.formula;
  if (!entry.input_modified) {
    entry.charge = struct.charge !== undefined ? struct.charge : entry.charge;
    entry.multiplicity = struct.multiplicity || entry.multiplicity;
  }
  entry.warnings = struct.warnings || [];
  entry.geometry_state = "ready";
  entry.geometry_error = null;
  entry.source_ref = Object.assign({}, entry.source_ref || {}, struct.source_ref || {}, {
    source_uid: entry.source_uid || (entry.source_ref && entry.source_ref.source_uid) || "",
    source_id: sourceId,
    version_id: entry.version_id || (entry.source_ref && entry.source_ref.version_id) || "",
    geometry_hash: entry.geometry_hash || (entry.source_ref && entry.source_ref.geometry_hash) || "",
  });
}

async function _loadPendingGeometry() {
  for (var i = 0; i < wizardStructures.length; i++) {
    var entry = wizardStructures[i];
    if (!entry || (entry.geometry_state !== "pending" && entry.geometry_state !== "failed") || !entry.source_uid || entry.xyz) continue;
    entry.geometry_state = "loading";
    renderLoadedStructuresList();
    updatePESSelectionStatus();
    try {
      var sourceId = await _resolveStructureSourceId(entry);
      var body = await api("/structure-sources/" + encodeURIComponent(sourceId));
      var a = body.structure || {};
      var struct = structureFromSourceDetail(a, sourceId, entry);
      struct.source_ref.checksum = body.checksum || struct.source_ref.checksum || "";
      _mergeLoadedGeometry(entry, struct, sourceId);
    } catch (e) {
      entry.geometry_state = "failed";
      entry.geometry_error = e && e.message ? e.message : String(e);
    }
    renderLoadedStructuresList();
    if (wizardStructures[wizardSelectedStructureIndex] === entry) {
      renderStructurePreview(wizardStructures, wizardParseWarnings);
    }
    _scheduleWizardDraftAutosave();
  }
}

async function loadStructureSource(sourceId, rowEl, sourceMeta) {
  if (structureSourceLoadController) structureSourceLoadController.abort();
  structureSourceLoadController = typeof AbortController !== "undefined" ? new AbortController() : null;
  var loadToken = ++structureSourceLoadToken;
  var errEl = rowEl ? rowEl.querySelector(".result-row-error") : null;
  var btn = rowEl ? rowEl.querySelector(".result-load-btn") : null;
  if (errEl) { errEl.style.display = "none"; errEl.textContent = ""; }
  if (btn) { btn.disabled = true; btn.textContent = t("results.loading_structure"); }
  showParseStatus(t("results.loading_structure"), false);
  try {
    var body = await apiRemote("/structure-sources/" + encodeURIComponent(sourceId),
      structureSourceLoadController ? { signal: structureSourceLoadController.signal } : undefined);
    if (loadToken !== structureSourceLoadToken) return;
    var a = body.structure || {};
    var struct = structureFromSourceDetail(a, sourceId, sourceMeta);
    struct.source_ref.checksum = body.checksum || "";
    struct.source_ref.project_id = struct.source_ref.project_id || body.project_id || "";
    struct.source_ref.job_status = struct.source_ref.job_status || body.job_status || "";
    // Ensure geometry_state is set for the new entry model
    if (!struct.geometry_state) struct.geometry_state = "ready";
    if (!struct.input_id) struct.input_id = (window.ACPTaskInputWorkspace && window.ACPTaskInputWorkspace.entryFromSnapshot)
      ? window.ACPTaskInputWorkspace.entryFromSnapshot({}).input_id : ("inp_" + Date.now().toString(36));
    if (!struct.source_kind) struct.source_kind = struct.source_uid ? "task_result" : "manual_input";
    if (window.ACPJobEditor && ACPJobEditor.isActive()) {
      // Edit mode is a source replacement, not an additive import.
      wizardStructures = [struct];
      wizardSelectedStructureIndex = 0;
    } else {
      var existingIndex = wizardStructures.findIndex(function(existing) {
        return existing && existing.source_uid && existing.source_uid === struct.source_uid;
      });
      if (existingIndex >= 0) {
        wizardSelectedStructureIndex = existingIndex;
      } else {
        wizardStructures.push(struct);
        wizardSelectedStructureIndex = wizardStructures.length - 1;
      }
    }
    wizardParseWarnings = wizardStructures.reduce(function(warnings, item) {
      return warnings.concat(item.warnings || []);
    }, []);
    setDetectedFormatChip(null);
    if (struct.charge !== undefined) document.getElementById("modal-charge").value = struct.charge;
    if (struct.multiplicity) document.getElementById("modal-mult").value = struct.multiplicity;
    var csRow = document.getElementById("charge-spin-row");
    if (csRow) csRow.style.display = "flex";
    var errBox = document.getElementById("parse-errors");
    if (errBox) errBox.style.display = "none";
    renderStructurePreview(wizardStructures, wizardParseWarnings);
    if (btn) { btn.textContent = t("picker.loaded") || "已载入 ✓"; }
    if (window.ACPJobEditor && ACPJobEditor.isActive()) {
      ACPJobEditor.onJobResultLoaded(struct);
    }
    showParseStatus(t("parse.success", { count: wizardStructures.length }), false);
    var loadedMeta = resultsSources.find(function(s) { return s.source_id === sourceId; });
    var loadedProject = (loadedMeta && loadedMeta.project_id) || body.project_id || "";
    var targetId = resultsProjectTargetId();
    if (loadedProject && String(loadedProject) !== String(targetId)) {
      // Cross-project reuse: make 来源 → 目标 explicit before submission.
      showParseStatus(t("results.cross_project", { source: projectNameOf(loadedProject), target: projectNameOf(targetId) }), false);
    }
    if (wizardState.workflow && wizardState.workflow.id === "mechanism") {
      var mechPanel = document.getElementById("mech-builder-panel");
      if (mechPanel) mechPanel.style.display = "block";
      hookMechanismBuilder();
    }
    if (isPESsearchTask()) {
      syncPESSelectionFromWizard(true);
      attachPESPreviewSelection();
    }
    updateInputModeVisibility();
    _scheduleWizardDraftAutosave();
  } catch (e) {
    if (loadToken !== structureSourceLoadToken || (e && e.name === "AbortError")) return;
    if (errEl) {
      errEl.textContent = t("results.error") + ": " + (e.message || String(e));
      errEl.style.display = "inline";
    }
    showParseStatus(t("results.error") + ": " + (e.message || String(e)), true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = t("results.load"); }
  }
}

async function previewStructureSource(sourceId, sourceUid) {
  var previewToken = ++structureSourceLoadToken;
  try {
    var body = await window.ACPClient.wizardEndpoints.fetchStructureSource(sourceId);
    if (previewToken !== structureSourceLoadToken) return;
    var stagedIndex = wizardStructures.findIndex(function(entry) {
      return (sourceUid && entry.source_uid === sourceUid) || entry.source_uid === sourceId ||
        entry.source_id === sourceId || (entry.source_ref && entry.source_ref.source_id === sourceId);
    });
    var stagedEntry = stagedIndex >= 0 ? wizardStructures[stagedIndex] : null;
    var struct = structureFromSourceDetail(body.structure || {}, sourceId, stagedEntry);
    if (stagedEntry && struct.xyz) {
      _mergeLoadedGeometry(stagedEntry, struct, sourceId);
      selectPreviewStructure(stagedIndex, false);
      return;
    }
    renderPreviewStructure3D(struct);
    _refreshWizardInputForm(struct, true);
    var moreMenu = document.getElementById("preview-more-menu");
    if (moreMenu) moreMenu.style.display = "block";
    var title = document.getElementById("preview-structure-title");
    if (title) title.textContent = "正在预览 · " + (struct.name || "结构");
    var meta = document.getElementById("parse-status");
    if (meta) {
      meta.textContent = "正在预览（尚未加入本次任务）";
      meta.className = "parse-status";
    }
  } catch (err) {
    if (previewToken !== structureSourceLoadToken) return;
    showParseStatus("预览失败：" + (err.message || String(err)), true);
  }
}

// --- Scan wizard coordinate fields (distance / angle / dihedral) ----------
// Atom indices are entered 1-based for display and converted to the 0-based
// wire form EXACTLY ONCE — the single conversion site is the marked loop
// inside buildScanWizardBodyParts.  The builder is pure (no DOM) so tests
// can execute it against real structures.

function scanWizardCountAtoms(xyzText) {
  if (typeof xyzText !== "string") return null;
  var lines = xyzText.split(/\r?\n/);
  if (!lines.length || !String(lines[0]).trim()) return null;
  var declared = Number(String(lines[0]).trim());
  if (Number.isInteger(declared) && declared > 0) return declared;
  var count = 0;
  for (var i = 0; i < lines.length; i++) {
    var parts = String(lines[i]).trim().split(/\s+/);
    if (parts.length >= 4 && isFinite(Number(parts[1])) && isFinite(Number(parts[2])) &&
        isFinite(Number(parts[3]))) count++;
  }
  return count > 0 ? count : null;
}

function scanWizardAtomCount(kind) {
  if (kind === "distance") return 2;
  if (kind === "angle") return 3;
  if (kind === "dihedral") return 4;
  return null;
}

function buildScanWizardBodyParts(fields, structureEntry) {
  function fail(code) { return { ok: false, code: code }; }
  var entry = structureEntry || {};
  var xyz = entry.xyz || entry.xyz_text || "";
  var atomCount = scanWizardCountAtoms(xyz);
  if (!atomCount) return fail("no_confirmed_structure");
  function raw(name) {
    var value = fields && fields[name];
    return String(value === undefined || value === null ? "" : value).trim();
  }
  // An empty kind keeps distance-only callers valid; any explicit kind
  // outside the three supported values fails before the field checks.
  var kind = raw("kind") || "distance";
  var nAtoms = scanWizardAtomCount(kind);
  if (!nAtoms) return fail("kind_invalid");
  var atomNames = ["atomA", "atomB", "atomC", "atomD"];
  var rawAtoms = [];
  for (var i = 0; i < nAtoms; i++) rawAtoms.push(raw(atomNames[i]));
  var rawStart = raw("start");
  var rawEnd = raw("end");
  var rawPoints = raw("points");
  for (var i = 0; i < nAtoms; i++) {
    if (!rawAtoms[i]) return fail("fields_required");
  }
  if (!rawStart || !rawEnd || !rawPoints) return fail("fields_required");
  for (var i = 0; i < nAtoms; i++) {
    if (!/^[+-]?\d+$/.test(rawAtoms[i])) return fail("atom_not_integer");
  }
  var atoms = [];
  for (var i = 0; i < nAtoms; i++) {
    var parsedAtom = parseInt(rawAtoms[i], 10);
    if (parsedAtom < 1) return fail("atom_not_positive");
    atoms.push(parsedAtom);
  }
  for (var i = 0; i < nAtoms; i++) {
    for (var j = i + 1; j < nAtoms; j++) {
      if (atoms[i] === atoms[j]) return fail("atoms_not_distinct");
    }
  }
  for (var i = 0; i < nAtoms; i++) {
    if (atoms[i] > atomCount) return fail("atom_out_of_range");
  }
  var start = Number(rawStart);
  var end = Number(rawEnd);
  if (!isFinite(start) || !isFinite(end)) return fail("range_not_finite");
  if (kind === "distance") {
    if (start <= 0 || end <= 0) return fail("range_not_positive");
  } else if (kind === "angle") {
    if (start < 0 || start > 180 || end < 0 || end > 180) return fail("range_angle_invalid");
  } else if (kind === "dihedral") {
    if (start < -360 || start > 360 || end < -360 || end > 360) {
      return fail("range_dihedral_invalid");
    }
  }
  if (start === end) return fail("range_equal");
  if (!/^[+-]?\d+$/.test(rawPoints)) return fail("points_invalid");
  var points = parseInt(rawPoints, 10);
  if (points < 2) return fail("points_invalid");
  // scan-index-base-convert: the ONLY 1-based -> 0-based conversion site.
  var wire = [];
  for (var i = 0; i < nAtoms; i++) {
    wire.push(String(parseInt(raw(atomNames[i]), 10) - 1));
  }
  return {
    ok: true,
    atomCount: atomCount,
    input: {
      scan_coordinates: [wire.join(",") + "," + rawStart + "," + rawEnd]
    },
    method: { scan_points: points },
    mirror: {
      scan_coordinate_kind: kind,
      scan_coordinate_atoms: atoms,
      scan_coordinate_start: start,
      scan_coordinate_end: end,
      scan_coordinate_points: points
    }
  };
}

function scanWizardErrorMessage(code) {
  return t("modal.scan_err_" + code) || String(code || "invalid");
}

// Per-kind default scan ranges as display strings. A kind switch swaps the
// start/end inputs to the new kind's defaults ONLY while the current values
// still equal the previous kind's defaults — user edits are never destroyed.
var scanWizardKindDefaults = {
  distance: { start: "1.0", end: "3.0" },
  angle: { start: "100.0", end: "160.0" },
  dihedral: { start: "0.0", end: "180.0" }
};

function updateScanKindButtons(kind) {
  document.querySelectorAll(".scan-function-btn").forEach(function(button) {
    button.classList.toggle("active", button.getAttribute("data-scan-kind") === kind);
  });
  var hint = document.getElementById("scan-pick-hint");
  if (hint) {
    hint.setAttribute("data-i18n", "modal.scan_pick_hint_" + kind);
    hint.textContent = t("modal.scan_pick_hint_" + kind);
  }
}

function scanKindLabel(kind) {
  return ({
    distance: "距离扫描",
    angle: "键角扫描",
    dihedral: "二面角扫描"
  })[kind] || String(kind || "distance");
}

function formatScanAtom(index) {
  var structure = scanWizardCurrentStructure();
  var atoms = parseXyzAtoms(structure && structure.xyz ? String(structure.xyz) : "");
  var atom = atoms[index];
  return atom ? String(atom.elem || "?") + "#" + (index + 1) : "#" + (index + 1);
}

function updateScanSelectionSummary() {
  var summary = document.getElementById("scan-selection-summary");
  if (!summary) return;
  var kind = scanSelectionState.selectionKind || "distance";
  var selected = scanSelectionState.selectedAtoms || [];
  var count = scanWizardAtomCount(kind) || 2;
  if (selected.length !== count) {
    summary.innerHTML = '<span class="selection-empty">' + escapeHtml(scanKindLabel(kind)) +
      "：已选择 " + selected.length + " / " + count + " 个原子</span>";
    return;
  }
  summary.innerHTML = '<span class="selection-badge">' + escapeHtml(scanKindLabel(kind)) +
    "：" + escapeHtml(selected.map(formatScanAtom).join(" — ")) + "</span>" +
    "<span>内部索引：[" + selected.join(", ") + "]</span>";
}

function updateScanTaskBadge() {
  var badge = document.getElementById("scan-task-badge");
  if (!badge) return;
  var kind = scanSelectionState.selectionKind || "distance";
  var scanLevel = (wizardState && wizardState.method && wizardState.method.stages && wizardState.method.stages.scan) || {};
  var unit = kind === "distance" ? "Å" : "°";
  var startText = scanLevel.scan_coordinate_start === undefined || scanLevel.scan_coordinate_start === null
    ? "?" : String(scanLevel.scan_coordinate_start);
  var endText = scanLevel.scan_coordinate_end === undefined || scanLevel.scan_coordinate_end === null
    ? "?" : String(scanLevel.scan_coordinate_end);
  var pointsText = scanLevel.scan_coordinate_points === undefined || scanLevel.scan_coordinate_points === null
    ? "?" : String(scanLevel.scan_coordinate_points);
  var rangeText = startText + " → " + endText + " " + unit + " · " + pointsText + " 点";
  var selected = scanSelectionState.selectedAtoms || [];
  var atomsText = selected.length === (scanWizardAtomCount(kind) || 2)
    ? selected.map(formatScanAtom).join(" — ")
    : (t("scan.task.incomplete") || "尚未完成扫描坐标选择");
  badge.innerHTML = '<strong>' + escapeHtml(t("scan.task.title") || "本次 Scan 任务") + "</strong>" +
    '<span class="task-kind">' + escapeHtml(scanKindLabel(kind) + " · " + rangeText) + "</span>" +
    '<span style="display:block;color:var(--text-tertiary);">' + escapeHtml(atomsText) + "</span>";
}

function updateScanSelectionStatus() {
  var status = document.getElementById("scan-selection-status");
  if (!status) return;
  var isChinese = currentLang === "zh-CN";
  if (!(wizardState && wizardState.workflow && wizardState.workflow.id === "scan")) {
    status.textContent = isChinese ? "请在结构预览中选择扫描原子。" : "Pick scan atoms in the structure preview.";
    return;
  }
  var structure = scanWizardCurrentStructure();
  if (structure && (structure.geometry_state === "pending" || structure.geometry_state === "loading")) {
    status.textContent = isChinese
      ? "本次输入的结构坐标正在加载，请稍候。"
      : "The staged structure coordinates are loading. Please wait.";
  } else if (structure && structure.geometry_state === "failed") {
    status.textContent = isChinese
      ? "本次输入结构坐标加载失败，请重新选择该结构后重试。"
      : "Could not load coordinates for the staged structure. Reselect it and try again.";
  } else if (!structure || !structure.has_3d || !structure.xyz) {
    status.textContent = isChinese
      ? "本次输入没有可用的 3D 坐标，请选择或导入带坐标的结构。"
      : "The staged input has no usable 3D coordinates. Select or import a structure with coordinates.";
  } else if (!previewModel || previewModelStructureKey !== String(structure.xyz).trim()) {
    status.textContent = isChinese
      ? "预览中的结构不是本次输入结构；请选择本次输入中的结构后再点选。"
      : "The preview is not showing the staged input. Select the staged structure before picking atoms.";
  } else {
    status.textContent = (isChinese
      ? "在右侧结构中依次点击原子。"
      : "Click atoms in order in the structure preview.") + " " +
      t("modal.scan_pick_hint_" + (scanSelectionState.selectionKind || "distance"));
  }
}

function setScanSelectionError(code) {
  var el = document.getElementById("scan-selection-error");
  if (!el) return;
  el.textContent = code ? (scanWizardErrorMessage(code) || "") : "";
  el.style.display = code ? "block" : "none";
}

function scanSelectionFieldValues() {
  var scanLevel = (wizardState && wizardState.method && wizardState.method.stages && wizardState.method.stages.scan) || {};
  var selected = scanSelectionState.selectedAtoms || [];
  return {
    kind: String(scanLevel.scan_coordinate_kind || scanSelectionState.selectionKind || "distance"),
    atomA: selected.length > 0 ? String(selected[0] + 1) : "",
    atomB: selected.length > 1 ? String(selected[1] + 1) : "",
    atomC: selected.length > 2 ? String(selected[2] + 1) : "",
    atomD: selected.length > 3 ? String(selected[3] + 1) : "",
    start: scanLevel.scan_coordinate_start === undefined || scanLevel.scan_coordinate_start === null
      ? "" : String(scanLevel.scan_coordinate_start),
    end: scanLevel.scan_coordinate_end === undefined || scanLevel.scan_coordinate_end === null
      ? "" : String(scanLevel.scan_coordinate_end),
    points: scanLevel.scan_coordinate_points === undefined || scanLevel.scan_coordinate_points === null
      ? "" : String(scanLevel.scan_coordinate_points)
  };
}

function setScanSelectionKind(kind) {
  if (!scanWizardAtomCount(kind)) return;
  var previousKind = scanSelectionState.selectionKind || "distance";
  scanSelectionState.selectionKind = kind;
  if (wizardState && wizardState.method && wizardState.method.stages) {
    if (!wizardState.method.stages.scan) wizardState.method.stages.scan = {};
    wizardState.method.stages.scan.scan_coordinate_kind = kind;
    // Swap start/end to the new kind's defaults ONLY while the current values
    // still equal the previous kind's defaults — user edits are never destroyed.
    var previousDefaults = scanWizardKindDefaults[previousKind] || scanWizardKindDefaults.distance;
    var nextDefaults = scanWizardKindDefaults[kind] || scanWizardKindDefaults.distance;
    var scanLevel = wizardState.method.stages.scan;
    var startAtDefault = scanLevel.scan_coordinate_start === undefined || scanLevel.scan_coordinate_start === null ||
      Number(scanLevel.scan_coordinate_start) === Number(previousDefaults.start);
    var endAtDefault = scanLevel.scan_coordinate_end === undefined || scanLevel.scan_coordinate_end === null ||
      Number(scanLevel.scan_coordinate_end) === Number(previousDefaults.end);
    if (startAtDefault && endAtDefault) {
      scanLevel.scan_coordinate_start = Number(nextDefaults.start);
      scanLevel.scan_coordinate_end = Number(nextDefaults.end);
    }
  }
  if (previousKind !== kind) {
    scanSelectionState.selectedAtoms = [];
    clearScanPreviewSelectionShapes();
    renderScanPreviewSelection();
    updateScanSelectionSummary();
  }
  updateScanKindButtons(kind);
  updateScanTaskBadge();
  updateScanSelectionStatus();
}

function scanWizardCurrentStructure() {
  if (typeof wizardStructures === "undefined" || !wizardStructures || !wizardStructures.length) return null;
  return wizardStructures[wizardSelectedStructureIndex] || wizardStructures[0] || null;
}

function clearScanSelectionAtomMirror() {
  var scanLevel = wizardState && wizardState.method && wizardState.method.stages && wizardState.method.stages.scan;
  if (scanLevel && scanLevel.scan_coordinate_atoms !== undefined) {
    delete scanLevel.scan_coordinate_atoms;
  }
}

function updateScanSelectionForProtocol() {
  var scanLevel = (wizardState && wizardState.method && wizardState.method.stages && wizardState.method.stages.scan) || {};
  var kind = String(scanLevel.scan_coordinate_kind || "");
  if (!scanWizardAtomCount(kind)) kind = scanSelectionState.selectionKind || "distance";
  if (kind !== (scanSelectionState.selectionKind || "distance")) {
    scanSelectionState.selectionKind = kind;
    scanSelectionState.selectedAtoms = [];
    clearScanPreviewSelectionShapes();
    clearScanSelectionAtomMirror();
    updateScanKindButtons(kind);
    renderScanPreviewSelection();
  }
  updateScanSelectionSummary();
  updateScanTaskBadge();
}

function syncScanSelectionFromWizard(forceReset) {
  var structure = scanWizardCurrentStructure();
  var xyz = structure && structure.xyz ? String(structure.xyz).trim() : "";
  var atoms = xyz ? parseXyzAtoms(xyz) : [];
  if (!xyz || !atoms.length) {
    scanSelectionState.selectedAtoms = [];
    scanSelectionState.structureKey = "";
    clearScanPreviewSelectionShapes();
    updateScanSelectionSummary();
    updateScanTaskBadge();
    return;
  }
  var changed = xyz !== scanSelectionState.structureKey;
  if (forceReset || changed) {
    scanSelectionState.selectedAtoms = [];
    clearScanPreviewSelectionShapes();
    scanSelectionState.structureKey = xyz;
  }
  var scanLevel = (wizardState && wizardState.method && wizardState.method.stages && wizardState.method.stages.scan) || {};
  var kind = String(scanLevel.scan_coordinate_kind || scanSelectionState.selectionKind || "distance");
  if (!scanWizardAtomCount(kind)) kind = "distance";
  scanSelectionState.selectionKind = kind;
  updateScanKindButtons(kind);
  var picked = scanLevel.scan_coordinate_atoms;
  var nAtoms = scanWizardAtomCount(kind) || 2;
  if (Array.isArray(picked) && picked.length === nAtoms && scanSelectionState.selectedAtoms.length === 0) {
    var restored = [];
    var valid = true;
    for (var i = 0; i < picked.length; i++) {
      var value = Number(picked[i]);
      if (!isFinite(value) || Math.floor(value) !== value || value < 1 || value > atoms.length) {
        valid = false;
        break;
      }
      if (restored.indexOf(value - 1) >= 0) {
        valid = false;
        break;
      }
      restored.push(value - 1);
    }
    if (valid) scanSelectionState.selectedAtoms = restored;
  }
  updateScanSelectionSummary();
  updateScanTaskBadge();
  if (previewModel) attachPESPreviewSelection();
}

function resetScanSelection() {
  clearScanPreviewSelectionShapes();
  scanSelectionState.selectedAtoms = [];
  scanSelectionState.structureKey = "";
  scanSelectionState.active = false;
  clearScanSelectionAtomMirror();
  setScanSelectionError(null);
  updateScanSelectionSummary();
  updateScanTaskBadge();
}

function ensureScanSelectionListeners() {
  document.querySelectorAll(".scan-function-btn").forEach(function(button) {
    if (button._scanSelBound) return;
    button._scanSelBound = true;
    button.addEventListener("click", function() {
      setScanSelectionKind(button.dataset.scanKind);
    });
  });
  var clearBtn = document.getElementById("scan-clear-selection");
  if (clearBtn && !clearBtn._scanSelBound) {
    clearBtn._scanSelBound = true;
    clearBtn.addEventListener("click", function() {
      scanSelectionState.selectedAtoms = [];
      clearScanPreviewSelectionShapes();
      clearScanSelectionAtomMirror();
      renderScanPreviewSelection();
      updateScanSelectionSummary();
      updateScanTaskBadge();
      setScanSelectionError(null);
      updateScanSelectionStatus();
    });
  }
}

function updateScanSelectionVisibility() {
  var isScan = wizardState && wizardState.workflow && wizardState.workflow.id === "scan";
  var visible = isScan && createWizardStep === 2;
  var panel = document.getElementById("scan-selection-panel");
  if (panel) panel.classList.toggle("active", visible);
  var rightPane = document.getElementById("tiw-right");
  if (rightPane) rightPane.classList.toggle("scan-selection-active", visible);
  if (isScan) {
    scanSelectionState.active = true;
    updateScanSelectionForProtocol();
    syncScanSelectionFromWizard(false);
    if (previewModel) attachPESPreviewSelection();
    updateScanSelectionStatus();
    if (panel) panel.classList.toggle("picking-disabled", !canPickScanAtoms());
    return;
  }
  if (scanSelectionState.active) resetScanSelection();
  scanSelectionState.active = false;
  if (!canPickPESAtoms() && !canPickScanAtoms()) {
    if (previewModel) {
      try { previewModel.setClickable({}, false); } catch (e) {}
    }
  }
  updateScanSelectionStatus();
}

function updateInputModeVisibility() {
  updatePESSelectionVisibility();
  // NMR experiment panel: visible only for the nmr workflow (DevDoc §11.1).
  var nmrPanel = document.getElementById("nmr-experiment-panel");
  if (nmrPanel) {
    var isNmr = wizardState && wizardState.workflow && wizardState.workflow.id === "nmr";
    nmrPanel.style.display = isNmr ? "block" : "none";
  }
  // NMR enumerate row (P2): only for nmr, and only meaningful with a single
  // candidate. Explicit multi-candidate input and enumeration are mutually
  // exclusive, so grey it out when >1 structure is staged.
  var nmrEnumRow = document.getElementById("nmr-enumerate-row");
  if (nmrEnumRow) {
    var isNmrWf = wizardState && wizardState.workflow && wizardState.workflow.id === "nmr";
    var nStaged = wizardStructures.length;
    var singleOk = nStaged <= 1;
    nmrEnumRow.style.display = isNmrWf ? "block" : "none";
    nmrEnumRow.style.opacity = singleOk ? "1" : "0.45";
    var enumToggle = document.getElementById("nmr-enumerate-toggle");
    if (enumToggle && !singleOk) {
      enumToggle.checked = false;
      enumToggle.disabled = true;
    } else if (enumToggle) {
      enumToggle.disabled = false;
    }
    var enumExtra = document.getElementById("nmr-enumerate-extra");
    if (enumExtra && enumToggle) enumExtra.style.display = enumToggle.checked ? "block" : "none";
  }
  updateTaskNamePlaceholder();
}

// Task-name placeholder mirrors the backend default: omitted task_name
// falls back to the workflow id, so the placeholder example follows the
// selected workflow (mechanism suggests "mechanism" rather than "energy").
function updateTaskNamePlaceholder() {
  var taskNameInput = document.getElementById("modal-task-name");
  if (!taskNameInput) return;
  var isMech = wizardState && wizardState.workflow && wizardState.workflow.id === "mechanism";
  var phKey = isMech ? "modal.task_name_ph_mech" : "modal.task_name_ph";
  var ph = t(phKey) || t("modal.task_name_ph");
  if (ph) taskNameInput.setAttribute("placeholder", ph);
}

// --- NMR Bruker raw-data input (P3, DevDoc §5 stage 0a) --------------------
var nmrExperimentMode = "text"; // "text" | "bruker"
var nmrBrukerAsset = null;      // {upload_id, filename, project_id}

function setNmrExperimentMode(mode) {
  nmrExperimentMode = mode === "bruker" ? "bruker" : "text";
  var textPane = document.getElementById("nmr-exp-pane-text");
  var brukerPane = document.getElementById("nmr-exp-pane-bruker");
  if (textPane) textPane.style.display = nmrExperimentMode === "text" ? "block" : "none";
  if (brukerPane) brukerPane.style.display = nmrExperimentMode === "bruker" ? "block" : "none";
  var tabText = document.getElementById("nmr-exp-tab-text");
  var tabBruker = document.getElementById("nmr-exp-tab-bruker");
  if (tabText) tabText.style.opacity = nmrExperimentMode === "text" ? "1" : "0.55";
  if (tabBruker) tabBruker.style.opacity = nmrExperimentMode === "bruker" ? "1" : "0.55";
}

async function uploadNmrBruker(input) {
  var file = input && input.files && input.files[0];
  if (!file) return;
  var status = document.getElementById("nmr-bruker-status");
  if (status) status.textContent = t("modal.nmr_bruker_uploading") || "上传中…";
  var projectSelect = document.getElementById("modal-project-select");
  var projectId = (projectSelect && projectSelect.value) || "uncategorized";
  var formData = new FormData();
  formData.append("file", file);
  try {
    // Legacy threw `new Error(body.detail || resp.statusText)`; the adapter
    // reuses the parsed error body's detail verbatim (the statusText
    // fallback becomes "HTTP <status>" — see client/wizard-endpoints.js).
    var body = await window.ACPClient.wizardEndpoints.uploadNmrBruker(formData, projectId);
    nmrBrukerAsset = {
      upload_id: body.upload_id,
      filename: body.filename || file.name,
      project_id: projectId,
    };
    if (status) {
      status.textContent = (t("modal.nmr_bruker_ready") || "已上传: {name}")
        .replace("{name}", nmrBrukerAsset.filename);
    }
  } catch (e) {
    nmrBrukerAsset = null;
    if (status) status.textContent = "Upload failed: " + e.message;
  }
}

// Per-stage summary line for the method card (extracted from
// updateConfigCards; behavior unchanged).
function stageSummaryPart(lv, st) {
  if (st.functional) {
    var s = lv.label + ": " + st.functional;
    var basisStr = st.basis;
    if (!basisStr) {
      var fMeta = (methodCatalogCache.method_meta || {})[st.functional];
      if (!fMeta) fMeta = lookupCI(methodCatalogCache.method_meta || {}, st.functional);
      if (fMeta && fMeta.default_basis) basisStr = fMeta.default_basis;
    }
    if (basisStr) s += "/" + basisStr;
    return s;
  }
  if (st.gfn) return lv.label + ": " + st.gfn;
  if (st.scan_coordinate_kind) {
    var kindLabels = { distance: "键长扫描", angle: "键角扫描", dihedral: "二面角扫描" };
    var bondLabels = { auto: "自动键型", single: "单键", double: "双键", multiple: "多键", aromatic: "芳香键" };
    var coordText = kindLabels[st.scan_coordinate_kind] || st.scan_coordinate_kind;
    if (st.scan_coordinate_kind === "distance") {
      coordText += " · " + (bondLabels[st.scan_bond_type] || "自动键型");
    }
    var startText = st.scan_coordinate_start !== undefined ? st.scan_coordinate_start : "?";
    var endText = st.scan_coordinate_end !== undefined ? st.scan_coordinate_end : "?";
    var pointText = st.scan_coordinate_points !== undefined ? st.scan_coordinate_points : "?";
    return (lv.label_zh || lv.label) + ": " + coordText + " · " + startText + " → " + endText + " · " + pointText + "点";
  }
  if (st.scan_optimizer_method) return (lv.label_zh || lv.label) + ": " + st.scan_optimizer_method;
  if (st.scan_mode) return (lv.label_zh || lv.label) + ": " + st.scan_mode;
  if (st.temperature !== undefined && st.temperature !== "") return lv.label + ": " + st.temperature + "K";
  return lv.label + ": " + st.engine;
}

function renderWorkflowDetail(wfEntry) {
  var heading = document.getElementById("workflow-detail-title");
  var description = document.getElementById("workflow-detail-description");
  var meta = document.getElementById("workflow-detail-meta");
  var binaries = document.getElementById("workflow-detail-binaries");
  if (!description || !meta || !binaries) return;
  var isChinese = currentLang === "zh-CN";
  if (heading) heading.textContent = isChinese ? "工作流详情" : "Workflow details";
  description.textContent = wfEntry
    ? (isChinese ? (wfEntry.description_zh || wfEntry.description || "") : (wfEntry.description || ""))
    : (isChinese ? "选择工作流后显示用途和输入要求。" : "Select a workflow to view its purpose and input requirements.");
  if (wfEntry && wfEntry.id === "PESsearch") {
    meta.textContent = isChinese
      ? "输入要求：单个带 3D 坐标的结构；扫描原子在右侧结构预览中选择。"
      : "Input: one structure with 3D coordinates. Pick scan atoms in the right-hand preview.";
  } else if (wfEntry && wfEntry.id === "BatchOptimize") {
    meta.textContent = isChinese
      ? "可对多个输入结构应用统一的优化与分析协议。"
      : "Apply one optimization and analysis protocol to multiple input structures.";
  } else {
    meta.textContent = wfEntry && wfEntry.method_schema_id
      ? (isChinese ? "配置方案：" : "Protocol: ") + wfEntry.method_schema_id
      : "";
  }
  binaries.replaceChildren();
  (wfEntry && Array.isArray(wfEntry.requires_binaries) ? wfEntry.requires_binaries : []).forEach(function(name) {
    var chip = document.createElement("span");
    chip.className = "config-detail-chip";
    chip.textContent = name;
    binaries.appendChild(chip);
  });
}

function renderMethodDetail(schema, stages, loadState) {
  var heading = document.getElementById("method-detail-title");
  var description = document.getElementById("method-detail-description");
  var list = document.getElementById("method-detail-stages");
  if (!description || !list) return;
  var isChinese = currentLang === "zh-CN";
  if (heading) heading.textContent = isChinese ? "计算协议详情" : "Protocol details";
  list.replaceChildren();
  if (loadState === "failed") {
    description.textContent = t("modal.catalog_load_failed") || (isChinese ? "计算协议目录加载失败，请稍后重试。" : "Protocol catalog failed to load. Try again later.");
    return;
  }
  if (loadState === "loading") {
    description.textContent = t("modal.loading") || "正在加载计算协议…";
    return;
  }
  stages = stages || {};
  var profiles = schema && Array.isArray(schema.profiles) ? schema.profiles : [];
  var profileId = wizardState.method && wizardState.method.profile_id;
  var profile = profiles.find(function(item) { return item.profile_id === profileId; });
  var summary = profile && (isChinese ? (profile.summary_zh || profile.summary) : (profile.summary || profile.summary_zh));
  description.textContent = summary || resolveCurrentProfileLabel() || (isChinese ? "当前协议由下方阶段设置组成。" : "The protocol is defined by the stages below.");
  var levels = schema && Array.isArray(schema.method_levels) ? schema.method_levels : [];
  levels.forEach(function(level) {
    var state = stages[level.level_id];
    if (level.level_id === "batch" && stages.batch_roles) {
      var roles = stages.batch_roles;
      var roleText = "INT: " + (((roles.int || {}).method) || "r2SCAN-3c") + " · TS: " + (((roles.ts || {}).method) || "r2SCAN-3c");
      var roleItem = document.createElement("li");
      roleItem.textContent = roleText;
      list.appendChild(roleItem);
      return;
    }
    if (!state || state._disabled === true) return;
    var label = (isChinese ? (level.label_zh || level.label) : (level.label || level.label_zh)) || level.level_id;
    var detail = stageSummaryPart(level, state);
    var separator = detail.indexOf(":");
    if (separator >= 0) detail = detail.slice(separator + 1).trim();
    var item = document.createElement("li");
    item.textContent = label + " · " + detail;
    list.appendChild(item);
  });
  if (!list.children.length) {
    var fallback = document.createElement("li");
    fallback.textContent = isChinese ? "阶段参数按所选协议默认值执行。" : "Stage parameters use the selected protocol defaults.";
    list.appendChild(fallback);
  }
  // T24: surface method-validation migration/canonicalization warnings
  // (legacy grid aliases, method aliases, case folding) in the wizard
  // summary — non-blocking, mirrors the modal's amber warning block.
  var methodWarnings = (wizardState.method && wizardState.method.warnings) || [];
  methodWarnings.forEach(function(w) {
    var warnItem = document.createElement("li");
    warnItem.className = "method-detail-warning";
    warnItem.style.color = "var(--warn, #d29922)";
    warnItem.textContent = "\u26a0 " + w;
    list.appendChild(warnItem);
  });
}

var ircSourcePreviewSequence = 0;
function refreshIrcSourceSummary() {
  var seq = ++ircSourcePreviewSequence;
  if (!wizardState.workflow || wizardState.workflow.id !== "irc") return;
  var desc = document.getElementById("method-card-desc");
  if (!desc) return;
  var entry = wizardStructures.length === 1 ? wizardStructures[0] : null;
  var sourceId = entry && String((entry.source_ref && entry.source_ref.source_id) || entry.source_id || "").trim();
  if (!sourceId) {
    desc.textContent = wizardStructures.length > 1
      ? "IRC 每次只能选择一个已验证的 TS 任务结果"
      : "请选择已完成并通过频率验证的 TS 任务结果；计算方法将自动继承";
    return;
  }
  desc.textContent = "正在核对 TS 结果与计算方法…";
  api("/irc/ts-source?source_id=" + encodeURIComponent(sourceId)).then(function(source) {
    if (seq !== ircSourcePreviewSequence) return;
    desc.textContent = "已锁定来源 TS：" + source.method + (source.basis ? "/" + source.basis : "") +
      " · 虚频 " + Number(source.imaginary_frequency_cm1).toFixed(2) + " cm⁻¹";
  }).catch(function(error) {
    if (seq !== ircSourcePreviewSequence) return;
    desc.textContent = "不可用于 IRC：" + (error.message || String(error));
  });
}

function updateConfigCards() {
  ensureWizardWorkflowValid();
  syncBatchOptimizeProfileSummary();
  scheduleNodeMatchingRefresh();
  var wfTitle = document.getElementById("workflow-card-title");
  var wfDesc = document.getElementById("workflow-card-desc");
  var cat = currentLang === "zh-CN" ? "label_zh" : "label";
  var wfEntry = workflowCatalogCache.find(function(w) { return w.id === wizardState.workflow.id; });
  if (wfEntry) {
    wfTitle.textContent = wfEntry[cat] || wfEntry.label;
    wfDesc.textContent = currentLang === "zh-CN"
      ? (wfEntry.description_zh || wfEntry.description || "")
      : (wfEntry.description || "");
  }
  renderWorkflowDetail(wfEntry);

  var mtTitle = document.getElementById("method-card-title");
  var mtDesc = document.getElementById("method-card-desc");
  mtTitle.textContent = resolveCurrentProfileLabel();

  var stages = wizardState.method.stages || {};
  var schemaId = canonicalMethodSchemaId(wizardState.workflow.id, wizardState.workflow.schema_id);
  wizardState.workflow.schema_id = schemaId;
  var schema = (methodCatalogCache.method_schemas || {})[schemaId];
  var parts = [];

  // R5/R16: when the catalog isn't ready yet (or failed to load), show a
  // loading / retry hint instead of an opaque "--" so the user understands
  // the empty summary is transient rather than a broken card.
  var catalogEmpty = !methodCatalogCache.method_schemas;
  renderMethodDetail(schema, stages, catalogEmpty ? (methodCatalogCache._loadFailed ? "failed" : "loading") : "");
  if (catalogEmpty && methodCatalogCache._loadFailed) {
    mtDesc.innerHTML = '<span style="color:var(--danger)">' +
      (t("modal.catalog_load_failed") || "目录加载失败") +
      ' — <a href="#" onclick="loadMethodCatalog().then(updateConfigCards);return false;">' +
      (t("modal.retry") || "重试") + '</a></span>';
    updateInputModeVisibility();
    return;
  }
  if (catalogEmpty) {
    mtDesc.innerHTML = '<span class="muted">' + (t("modal.loading") || "加载中...") + '</span>';
    updateInputModeVisibility();
    return;
  }

  if (schema && schema.method_levels) {
    schema.method_levels.forEach(function(lv) {
      var st = stages[lv.level_id];
      // Batch level first: INT/TS methods from batch_roles must win over the
      // engine-less "(default)" placeholder (profile levels carry steps only).
      if (lv.level_id === "batch" && stages.batch_roles) {
        var br = stages.batch_roles;
        var intM = (br.int || {}).method || "r2SCAN-3c";
        var tsM = (br.ts || {}).method || "r2SCAN-3c";
        parts.push("INT: " + intM + " | TS: " + tsM);
        return;
      }
      if (!st || !st.engine || st._disabled === true) {
        var label = lv.label_zh || lv.label || lv.level_id;
        parts.push('<span style="color:var(--text-tertiary)">' + label + " (default)</span>");
        return;
      }
      parts.push(stageSummaryPart(lv, st));
    });
  } else {
    Object.keys(stages).forEach(function(lid) {
      var st = stages[lid];
      if (!st || typeof st !== "object" || st._disabled === true) return;
      if (st.functional) {
        var s = st.functional;
        var basisStr = st.basis;
        if (!basisStr) {
          var fMeta2 = (methodCatalogCache.method_meta || {})[st.functional];
          if (!fMeta2) fMeta2 = lookupCI(methodCatalogCache.method_meta || {}, st.functional);
          if (fMeta2 && fMeta2.default_basis) basisStr = fMeta2.default_basis;
        }
        if (basisStr) s += "/" + basisStr;
        parts.push(lid + ": " + s);
      } else if (st.gfn) {
        parts.push(lid + ": " + st.gfn);
      }
    });
  }
  mtDesc.innerHTML = parts.join(" | ") || "--";
  refreshIrcSourceSummary();

  updateInputModeVisibility();
}

function syncBatchOptimizeProfileSummary() {
  var summary = document.getElementById("batch-optimize-profile-summary");
  if (!summary) return;
  var label = resolveCurrentProfileLabel();
  summary.textContent = (t("modal.batch_profile") || "计算档案") + "：" + label + "（由计算协议统一控制）";
}

async function loadWorkflowCatalog() {
  try {
    var data = await api("/workflow-catalog");
    workflowCatalogCache = data.workflows || [];
    // Filter by the catalog's `visible` flag — single source of truth in
    // acp.catalog.WORKFLOW_CATALOG. Retired/hidden workflows
    // (e.g. conformer/nmr/benchmark) are dropped here automatically.
    workflowCatalogCache = workflowCatalogCache.filter(function(w) { return w.visible !== false; });
    setWorkbenchLoadError("Workflows", null);
  } catch(e) {
    console.error("Failed to load workflow catalog:", e);
    setWorkbenchLoadError("Workflows", e, initializeMethodDefaults);
  }
}

async function loadMethodCatalog() {
  try {
    var parsed = await api("/method-catalog");
    // fetch() only rejects on network errors; HTTP 5xx (e.g. catalog.py raising
    // at import time) resolves successfully with an error body that resp.json()
    // may either parse (non-catalog dict) or throw on. Guard explicitly so the
    // cache never gets silently populated with garbage (audit A15).

    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Unexpected catalog response shape");
    }
    methodCatalogCache = parsed;
    methodCatalogCache._loadFailed = false;
    setWorkbenchLoadError("Methods", null);
  } catch(e) {
    // Mark the cache so downstream consumers (updateConfigCards, openMethodConfig)
    // can surface a retry path instead of silently degrading to "--". (R5 / R16)
    // Preserve any previously-loaded good data so a hot-reload failure doesn't
    // tear down a working UI — only first-load failures leave the cache empty.
    console.error("Failed to load method catalog:", e);
    methodCatalogCache._loadFailed = true;
    setWorkbenchLoadError("Methods", e, initializeMethodDefaults);
  }
}

// Catalog-driven: no hardcoded stage workflow IDs or default artifacts.
// BatchOptimize and IRC are resolved from the workflow catalog at runtime.

function isS2CoordinateScan() {
  return isPESsearchTask();
}

function updateS2InlineVisibility() {
  updatePESSelectionVisibility();
  configureStageSourcePanel(wizardState.workflow ? wizardState.workflow.id : "");
}

function canonicalMethodSchemaId(workflowId, schemaId) {
  // PESsearch is a standalone coordinate-scan task. Never let a stale
  // catalog entry route it back to the retired mechanism/path-search schema.
  if (workflowId === "PESsearch") return "pes_scan";
  return schemaId || "";
}

function pickDefaultProfile(schema) {
  if (!schema || !Array.isArray(schema.profiles) || !schema.profiles.length) return null;
  // Unified Confsearch schema lists protocols in xtb-crest-first order;
  // the default protocol is the recommended CREST + CENSO one (rank1
  // refinement), falling back to the first entry for other schemas.
  var preferred = schema.profiles.find(function(p) { return p.profile_id === "censo-crest"; });
  return preferred || schema.profiles[0];
}

// Catalog-driven default workflow resolution. The wizard default must never
// come from a hardcoded id: retired/hidden entries (e.g. the legacy
// "energy"/"ensemble" defaults) are absent from workflowCatalogCache, so any
// stale id falls back to Confsearch (the flagship conformer-search workflow),
// then any active entry, then null (catalog not loaded yet).
function resolveDefaultWorkflow(preferredId) {
  var id = preferredId || "";
  var entry = workflowCatalogCache.find(function(w) { return w.id === id && w.status === "active"; });
  return entry
    || workflowCatalogCache.find(function(w) { return w.id === "Confsearch" && w.status === "active"; })
    || workflowCatalogCache.find(function(w) { return w.status === "active"; })
    || null;
}

// Self-healing guard: if wizardState.workflow is missing, stale, or refers to
// an id absent from the visible catalog (any code path may leave it so —
// hardcoded defaults, pending-new-task payloads, state snapshots), repair it
// to the resolved default so consumers never render retired-schema protocols.
// Returns false only when the catalog is not loaded yet (R5/R16 loading and
// retry paths then handle the empty state).
function ensureWizardWorkflowValid() {
  var entry = resolveDefaultWorkflow(wizardState.workflow && wizardState.workflow.id);
  if (!entry) return false;
  if (wizardState.workflow.id !== entry.id || !wizardState.workflow.schema_id) {
    wizardState.workflow = {
      id: entry.id,
      label: entry.label,
      schema_id: canonicalMethodSchemaId(entry.id, entry.method_schema_id)
    };
  }
  return true;
}

function applyPendingNewTask() {
  var pending = window._pendingNewTask;
  if (!pending) return;
  window._pendingNewTask = null;
  window._mechProjectId = pending.projectId || "";
  // Fresh context for a pending job/editor hydrate: drop any scan selection
  // left over from a previous session; hydration refills it from the draft's
  // method stages via syncScanSelectionFromWizard().
  resetScanSelection();
  // Resolve through the catalog so an invalid/stale pending workflow id
  // (e.g. a retired entry) self-heals to the current default instead of
  // rendering a legacy schema.
  var wf = resolveDefaultWorkflow(pending.workflow);
  var pendingSchemaId = wf ? wf.method_schema_id : "";
  wizardState.workflow = {
    id: wf ? wf.id : pending.workflow,
    label: wf ? wf.label : pending.workflow,
    schema_id: canonicalMethodSchemaId(wf ? wf.id : pending.workflow, pendingSchemaId)
  };
  loadDefaultMethodProfile(wizardState.workflow.schema_id);
  var projectSelect = document.getElementById("modal-project-select");
  setWizardInputMode("structure");
  configureStageSourcePanel(wizardState.workflow.id);
  updateS2InlineVisibility();
  updateInputModeVisibility();
  updateConfigCards();
}

function configureStageSourcePanel(workflowId) {
  if (resultsPicker) resultsPicker.refresh();
  if (candidatePicker) candidatePicker.refresh();
  var panel = document.getElementById("stage-source-panel");
  if (!panel) return;
  var wf = workflowCatalogCache.find(function(w) { return w.id === workflowId; });
  var isStage = wf && wf.requires_source;
  panel.style.display = isStage ? "block" : "none";
  if (!isStage) {
    // Left the stage chain: drop any programmatic node mirror (manual picks
    // survive — preselectNodeFromSourceJob keeps userTouched values).
    preselectNodeFromSourceJob("");
    return;
  }
  var artifactInput = document.getElementById("stage-artifact");
  if (workflowId === "BatchOptimize") {
    // Confirmed-candidate manifest is the only sanctioned PES hand-off;
    // recommendations never appear in it, so this default cannot leak guesses.
    artifactInput.value = "RESULT/result_manifest.json";
  } else {
    artifactInput.value = "";
  }
  var pesRow = document.getElementById("stage-pes-row");
  pesRow.style.display = "none";
  var ircRow = document.getElementById("stage-irc-row");
  ircRow.style.display = workflowId === "irc" ? "flex" : "none";
  var batchModeRow = document.getElementById("batch-optimize-mode-row");
  if (batchModeRow) {
    var batchSupported = workflowId === "BatchOptimize";
    batchModeRow.style.display = batchSupported ? "flex" : "none";
  }
  // Reset batch panel for BatchOptimize
  batchPreviewItems = [];
  var batchMode = document.getElementById("batch-optimize-mode");
  if (batchMode) {
    batchMode.checked = workflowId === "BatchOptimize";
    stageBatchSetMode(batchMode.checked);
  }
  // The structures parsed in step 1 are the canonical BatchOptimize input;
  // do not make the user select the same structures a second time.
  if (workflowId === "BatchOptimize" && wizardStructures.length) {
    stageBatchFromWizard();
  }
  renderStageBatchPreview();
  // T12: mirror the source job's explicit node pin into the node selector
  // (no-op when the source field is empty or unchanged).
  var stageSourceInput = document.getElementById("stage-source-job");
  preselectNodeFromSourceJob(stageSourceInput ? stageSourceInput.value : "");
}

var _batchKeyCounter = 0;
function stageBatchItemKey() {
  _batchKeyCounter++;
  return "b" + batchPreviewItems.length + "_" + _batchKeyCounter + "_" + Date.now();
}

function stageBatchAddItem(name, tag, xyz, charge, multiplicity, atomCount, formula, sourceType, sourceRef, sourceMeta) {
  batchPreviewItems.push({
    key: stageBatchItemKey(),
    include: true,
    name: name || "structure",
    tag: tag || "",
    tagAuto: !tag,
    xyz: xyz || "",
    charge: charge !== undefined ? charge : 0,
    multiplicity: multiplicity || 1,
    atomCount: atomCount || 0,
    formula: formula || "",
    sourceType: sourceType || "upload",
    sourceRef: sourceRef || "",
    sourceMeta: sourceMeta && typeof sourceMeta === "object" ? Object.assign({}, sourceMeta) : null,
    allowDisabledUse: !!(sourceMeta && sourceMeta.allow_disabled_use),
    disabledUseReason: (sourceMeta && sourceMeta.disabled_use_reason) || ""
  });
  renderStageBatchPreview();
}

// Atomic numbers for client-side electron-count validation of batch items.
var _ELEMENT_Z = { H:1,He:2,Li:3,Be:4,B:5,C:6,N:7,O:8,F:9,Ne:10,Na:11,Mg:12,Al:13,Si:14,P:15,S:16,Cl:17,Ar:18,K:19,Ca:20,Sc:21,Ti:22,V:23,Cr:24,Mn:25,Fe:26,Co:27,Ni:28,Cu:29,Zn:30,Ga:31,Ge:32,As:33,Se:34,Br:35,Kr:36,Rb:37,Sr:38,Y:39,Zr:40,Nb:41,Mo:42,Tc:43,Ru:44,Rh:45,Pd:46,Ag:47,Cd:48,In:49,Sn:50,Sb:51,Te:52,I:53,Xe:54,Cs:55,Ba:56,La:57,Ce:58,Pr:59,Nd:60,Pm:61,Sm:62,Eu:63,Gd:64,Tb:65,Dy:66,Ho:67,Er:68,Tm:69,Yb:70,Lu:71,Hf:72,Ta:73,W:74,Re:75,Os:76,Ir:77,Pt:78,Au:79,Hg:80,Tl:81,Pb:82,Bi:83,Po:84,At:85,Rn:86 };

function _batchItemSymbols(item) {
  if (Array.isArray(item.symbols) && item.symbols.length) return item.symbols;
  if (item.xyz) {
    try {
      var lines = item.xyz.split(/\r?\n/);
      var n = parseInt(lines[0], 10);
      var syms = [];
      for (var i = 0; i < n && i + 2 < lines.length; i++) {
        var parts = lines[i + 2].trim().split(/\s+/);
        if (parts.length >= 4) syms.push(parts[0]);
      }
      if (syms.length) return syms;
    } catch (_e) { /* fall through to formula */ }
  }
  if (item.formula) {
    var m, re = /([A-Z][a-z]?)(\d*)/g;
    var syms2 = [];
    while ((m = re.exec(item.formula))) {
      if (!m[1]) continue;
      var cnt = m[2] ? parseInt(m[2], 10) : 1;
      for (var k = 0; k < cnt; k++) syms2.push(m[1]);
    }
    if (syms2.length) return syms2;
  }
  return null;
}

function _batchCheckItem(item) {
  var zh = currentLang === "zh-CN";
  var symbols = _batchItemSymbols(item);
  if (!symbols) return { ok: true, text: "--" };
  var electrons = 0, unknown = false;
  symbols.forEach(function(s) {
    if (_ELEMENT_Z[s] != null) electrons += _ELEMENT_Z[s];
    else unknown = true;
  });
  if (unknown) return { ok: true, text: zh ? "未检查" : "n/a" };
  electrons -= item.charge || 0;
  var mult = item.multiplicity || 1;
  if (electrons <= 0) return { ok: false, text: zh ? "电荷过高：无剩余电子" : "no electrons left" };
  if (mult > electrons + 1) {
    return { ok: false, text: zh ? "多重度 " + mult + " 超出上限 " + (electrons + 1) : "multiplicity too high" };
  }
  if ((electrons - (mult - 1)) % 2 !== 0) {
    return { ok: false, text: zh ? "多重度 " + mult + " 与 " + electrons + "e 奇偶不符" : "parity mismatch" };
  }
  return { ok: true, text: "\u2713 " + electrons + "e" };
}

function renderStageBatchPreview() {
  var tbody = document.getElementById("batch-optimize-tbody");
  var emptyEl = document.getElementById("batch-optimize-empty");
  if (!tbody) return;
  if (!batchPreviewItems.length) {
    tbody.innerHTML = "";
    if (emptyEl) emptyEl.style.display = "block";
    return;
  }
  if (emptyEl) emptyEl.style.display = "none";
  var sourceLabels = { s2_candidate: "S2 候选", job_artifact: "任务结果", upload: "上传 XYZ", paste: "上传 XYZ", pes_candidate: "PES 确认候选" };
  tbody.innerHTML = batchPreviewItems.map(function(item, idx) {
    var tagTsStyle = item.tag === "TS" ? "opacity:1;font-weight:bold" : "opacity:0.45";
    var tagNote = item.tagAuto && !item.tag ? '<div class="muted" style="font-size:10px;">未检测到 TAG</div>' : "";
    var chk = _batchCheckItem(item);
    var chargeBadge = item.chargeMultCustom ? ' <span class="mc-badge mc-badge-modified" style="font-size:9px;">单独</span>' : "";
    return '<tr data-key="' + escapeHtml(item.key) + '">' +
      '<td><input type="checkbox" class="batch-include"' + (item.include ? " checked" : "") + '></td>' +
      '<td>' + (idx + 1) + '</td>' +
      '<td><input type="text" class="modal-input batch-name" style="width:140px;font-size:12px;" value="' + escapeHtml(item.name) + '"></td>' +
      '<td><span class="muted" style="font-size:11px;">' + (item.tag === "TS" ? "TS" : "默认") + '</span> ' +
          '<button type="button" class="btn-secondary batch-tag-btn" data-tag="TS" style="' + tagTsStyle + '">' + (item.tag === "TS" ? "取消 TS" : "标记 TS") + '</button>' +
          tagNote + '</td>' +
      '<td>' + escapeHtml(sourceLabels[item.sourceType] || item.sourceType) + '</td>' +
      '<td>' + (item.atomCount || '--') + '</td>' +
      '<td><input type="number" class="modal-input batch-charge" style="width:48px;font-size:12px;" value="' + item.charge + '">' + chargeBadge + '</td>' +
      '<td><input type="number" class="modal-input batch-mult" style="width:48px;font-size:12px;" value="' + item.multiplicity + '"></td>' +
      '<td class="batch-check" style="font-size:11px;' + (chk.ok ? "color:var(--text-secondary,#999)" : "color:#f66;font-weight:bold") + '">' + escapeHtml(chk.text) + '</td>' +
      '<td><button type="button" class="btn ghost batch-del-btn">' + escapeHtml(t("modal.stage_batch_del")) + '</button></td>' +
    '</tr>';
  }).join("");
}

function stageBatchSetMode(on) {
  var batchPanel = document.getElementById("batch-optimize-panel");
  var selectRow = document.getElementById("stage-select-row");
  var sourceJobRow = document.getElementById("stage-source-job");
  // The source job/artifact row is the flex row containing stage-source-job
  var sourceJobParent = sourceJobRow ? sourceJobRow.closest("div[style]") : null;
  if (batchPanel) batchPanel.style.display = on ? "block" : "none";
  if (selectRow) selectRow.style.display = on ? "none" : "flex";
  if (sourceJobParent) sourceJobParent.style.display = on ? "none" : "flex";
}

function stageBatchFromWizard() {
  var structs = wizardStructures || [];
  if (!structs.length) {
    window.alert(t("results.empty") || "暂无可复用的结构");
    return;
  }
  for (var i = 0; i < structs.length; i++) {
    var s = structs[i];
    var tag = (s.tag && s.tag !== "INT") ? s.tag : "";
    var sourceRef = s.source_ref || {};
    var reusableSourceId = sourceRef.job_id && sourceRef.path
      ? "job_" + sourceRef.job_id + ":" + sourceRef.path : "";
    var sourceMeta = Object.assign({}, sourceRef, {
      source_uid: s.source_uid || sourceRef.source_uid || "",
      version_id: s.version_id || sourceRef.version_id || "",
      geometry_hash: s.geometry_hash || sourceRef.geometry_hash || "",
    });
    stageBatchAddItem(
      s.name || s.molecule_name || "mol_" + (i + 1),
      tag,
      s.xyz || "",
      s.charge !== undefined ? s.charge : 0,
      s.multiplicity || 1,
      s.atom_count || 0,
      s.formula || "",
      reusableSourceId ? "job_artifact" : "upload",
      reusableSourceId,
      sourceMeta
    );
  }
}

async function stageBatchFromResults() {
  var picker = document.getElementById("batch-optimize-picker");
  if (!picker) return;
  if (picker.style.display !== "none") {
    picker.style.display = "none";
    // Destroy any existing picker instance
    if (picker._acpPicker) { picker._acpPicker.destroy(); picker._acpPicker = null; }
    return;
  }
  picker.style.display = "block";

  // Mount multi picker if ACPSourcePicker available
  if (typeof ACPSourcePicker !== "undefined") {
    picker.innerHTML = "";
    picker._acpPicker = ACPSourcePicker.mount(picker, {
      mode: "multi",
      projectId: selectedProjectId || null,
      allowBatchTags: false,
      onLoadSelection: function(items) {
        items.forEach(function(item) {
          stageBatchAddItem(
            item.resolved_name || item.default_name || "mol",
            item.role === "TS" ? "TS" : "",
            null,
            item.charge !== undefined ? item.charge : 0,
            item.multiplicity || 1,
            item.atom_count || 0,
            item.formula || "",
            "job_artifact",
            item.source_id || "",
            item
          );
        });
      },
      loadLabel: t("picker.batch.load") || "载入所选",
    });
    return;
  }

  // Legacy fallback
  picker.innerHTML = '<div class="muted" style="font-size:11px;">' + escapeHtml(t("results.loading")) + '</div>';
  try {
    var pickerUrl = "/structure-sources/recent?limit=50&_t=" + Date.now();
    if (selectedProjectId) pickerUrl += "&project_id=" + encodeURIComponent(selectedProjectId);
    else pickerUrl += "&all_projects=true";
    var body = await api(pickerUrl);
    var sources = (body && body.sources) || [];
    if (!sources.length) {
      picker.innerHTML = '<div class="muted" style="font-size:11px;">' + escapeHtml(t("results.empty")) + '</div>';
      return;
    }
    var html = '<div style="display:flex;justify-content:flex-end;margin-bottom:4px;">' +
      '<button type="button" class="btn ghost" id="batch-optimize-picker-close">' + escapeHtml(t("modal.close") || "关闭") + '</button></div>';
    for (var i = 0; i < sources.length; i++) {
      var src = sources[i];
      var tagStr = src.tag === "TS" ? ' [TS]' : '';
      html += '<div class="result-source-row" data-idx="' + i + '" style="cursor:pointer;padding:4px 6px;font-size:12px;">' +
        escapeHtml(sourceMoleculeName(src) || "--") +
        tagStr +
        ' <span class="muted">' + escapeHtml(src.workflow || "") + '</span>' +
        ' <span class="muted">' + escapeHtml(src.formula || "") + '</span>' +
      '</div>';
    }
    picker.innerHTML = html;
    picker.querySelectorAll(".result-source-row").forEach(function(row) {
      row.addEventListener("click", function() {
        var idx = parseInt(row.getAttribute("data-idx"), 10);
        var src = sources[idx];
        if (!src) return;
        stageBatchAddItem(
          sourceMoleculeName(src) || "mol",
          src.tag === "TS" ? "TS" : "",
          null,
          src.charge !== undefined ? src.charge : 0,
          src.multiplicity || 1,
          src.atom_count || 0,
          src.formula || "",
          "job_artifact",
          src.source_id || ""
        );
      });
    });
    var closeBtn = document.getElementById("batch-optimize-picker-close");
    if (closeBtn) {
      closeBtn.addEventListener("click", function() {
        picker.style.display = "none";
      });
    }
  } catch (e) {
    picker.innerHTML = '<div class="muted" style="font-size:11px;color:#f66;">' + escapeHtml(e.message || String(e)) + '</div>';
  }
}

function setupStageBatchListeners() {
  var batchModeEl = document.getElementById("batch-optimize-mode");
  if (batchModeEl) {
    batchModeEl.addEventListener("change", function() {
      stageBatchSetMode(batchModeEl.checked);
    });
  }
  var useWizardBtn = document.getElementById("batch-optimize-use-wizard");
  if (useWizardBtn) {
    useWizardBtn.addEventListener("click", function() {
      stageBatchFromWizard();
    });
  }
  var assignBtn = document.getElementById("batch-optimize-assign");
  if (assignBtn) {
    assignBtn.addEventListener("click", function() {
      var gc = parseInt(document.getElementById("modal-charge").value, 10) || 0;
      var gm = parseInt(document.getElementById("modal-mult").value, 10) || 1;
      batchPreviewItems.forEach(function(it) { it.charge = gc; it.multiplicity = gm; it.chargeMultCustom = false; });
      renderStageBatchPreview();
    });
  }
  var pickResultsBtn = document.getElementById("batch-optimize-pick-results");
  if (pickResultsBtn) {
    pickResultsBtn.addEventListener("click", function() {
      stageBatchFromResults();
    });
  }
  // Event delegation for batch table
  var batchTbody = document.getElementById("batch-optimize-tbody");
  if (batchTbody) {
    batchTbody.addEventListener("change", function(e) {
      var target = e.target;
      var tr = target.closest("tr");
      if (!tr) return;
      var key = tr.getAttribute("data-key");
      var item = batchPreviewItems.find(function(it) { return it.key === key; });
      if (!item) return;
      if (target.classList.contains("batch-include")) {
        item.include = target.checked;
        if (window.ACPJobEditor && ACPJobEditor.isActive()) ACPJobEditor.onBatchItemChanged(item);
      } else if (target.classList.contains("batch-name")) {
        item.name = target.value;
        if (window.ACPJobEditor && ACPJobEditor.isActive()) ACPJobEditor.onBatchItemChanged(item);
      } else if (target.classList.contains("batch-charge")) {
        item.charge = parseInt(target.value, 10) || 0;
        item.chargeMultCustom = true;
        if (window.ACPJobEditor && ACPJobEditor.isActive()) ACPJobEditor.onBatchItemChanged(item);
        renderStageBatchPreview();
      } else if (target.classList.contains("batch-mult")) {
        item.multiplicity = parseInt(target.value, 10) || 1;
        item.chargeMultCustom = true;
        if (window.ACPJobEditor && ACPJobEditor.isActive()) ACPJobEditor.onBatchItemChanged(item);
        renderStageBatchPreview();
      }
    });
    batchTbody.addEventListener("click", function(e) {
      var target = e.target;
      var tr = target.closest("tr");
      if (!tr) return;
      var key = tr.getAttribute("data-key");
      if (target.classList.contains("batch-tag-btn")) {
        var item = batchPreviewItems.find(function(it) { return it.key === key; });
        if (item) {
          var newTag = target.getAttribute("data-tag");
          item.tag = item.tag === newTag ? "" : newTag;
          item.tagAuto = false;
          if (window.ACPJobEditor && ACPJobEditor.isActive()) ACPJobEditor.onBatchItemChanged(item);
          renderStageBatchPreview();
        }
      } else if (target.classList.contains("batch-del-btn")) {
        var item = batchPreviewItems.find(function(it) { return it.key === key; });
        if (window.ACPJobEditor && ACPJobEditor.isActive() && item && item.itemId) {
          item.include = false;
          ACPJobEditor.onBatchItemChanged(item);
        } else {
          batchPreviewItems = batchPreviewItems.filter(function(it) { return it.key !== key; });
        }
        renderStageBatchPreview();
      }
    });
  }
}

function openPESsearchWizard(projectId) {
  // The mechanism-section shortcut now opens the common task creator and
  // selects PESsearch; structure input and MOL.js selection stay in that form.
  window._pendingNewTask = {
    workflow: "PESsearch",
    projectId: ""
  };
  openModal();
}

// Static INT/TS role defaults come from the shared factory (FE-06) in
// js/workbench/contracts/batch_role_defaults.js; wizard consumes it via a
// local alias. Method-dependent keys (method/basis/sp_*/dispersion/
// aux_j_basis) are patched per-context by callers. The window export is
// kept for legacy consumers (ACPJobEditor).
var batchRoleStaticDefaults = window.ACPWorkbench.contracts.batchRoleStaticDefaults;
window.batchRoleStaticDefaults = batchRoleStaticDefaults;

function applyBatchOptimizeMethodFields(methodPayload) {
  var batchRoles = (wizardState.method.stages || {}).batch_roles || {};
  var profileId = wizardState.method.profile_id || methodPayload.profile_id || "opt_freq";
  if (profileId === "__custom__") profileId = "opt_freq";
  methodPayload.profile = profileId;
  methodPayload.profile_id = profileId;
  methodPayload.temperature = 298.15;
  methodPayload.pressure = 1.0;
  methodPayload.scale_factor = 0.9905;

  var ROLE_KEYS = [
    "method", "basis", "sp_method", "sp_basis",
    "dispersion", "aux_j_basis", "aux_c_basis", "ri_approximation",
    "opt_max_iter", "opt_convergence", "opt_trust_radius", "opt_initial_hessian", "opt_recalc_hess",
    "scf_max_iter", "scf_convergence", "scf_strategy", "scf_orbital_inherit",
    "scf_damp", "scf_damp_fac", "scf_shift", "scf_shift_fac",
    "opt_rescue_policy", "opt_max_rescue",
    "temperature", "pressure", "scale_factor"
  ];

  methodPayload.batch_roles = {};
  ["int", "ts"].forEach(function(role) {
    var src = batchRoles[role] || {};
    var dest = {};
    ROLE_KEYS.forEach(function(k) {
      dest[k] = (src[k] !== undefined) ? src[k] : null;
    });
    methodPayload.batch_roles[role] = dest;
  });

  // Backward-compatible top-level fields from the INT role for legacy consumers.
  var intRole = methodPayload.batch_roles.int || {};
  methodPayload.optimization_method = intRole.method || "r2SCAN-3c";
  methodPayload.optimization_basis = intRole.basis || "";
  methodPayload.single_point_method = intRole.sp_method || "wB97M-V";
  methodPayload.single_point_basis = intRole.sp_basis || "def2-TZVPP";
  return methodPayload;
}

function collectStageJobPayload(workflow, methodBase) {
  var batchModeEl = document.getElementById("batch-optimize-mode");
  var batchMode = batchModeEl && batchModeEl.checked;
  if (workflow === "BatchOptimize" && !batchMode && wizardStructures.length) {
    if (!batchPreviewItems.length) stageBatchFromWizard();
    batchMode = true;
  }
  if (workflow === "BatchOptimize" && batchMode) {
    if (!batchPreviewItems.length) {
      throw new Error("请先加载至少一个结构；BatchOptimize 直接使用上一步已解析的结构。");
    }
    var items = [];
    for (var bi = 0; bi < batchPreviewItems.length; bi++) {
      var bItem = batchPreviewItems[bi];
      if (bItem.include === false) continue;
      var entry = { name: bItem.name, tag: bItem.tag || "", charge: bItem.charge, multiplicity: bItem.multiplicity, include: true };
      if (bItem.sourceType === "job_artifact" && bItem.sourceRef) {
        entry.source_id = bItem.sourceRef;
        var sourceMeta = bItem.sourceMeta || {};
        var nestedSourceRef = sourceMeta.source_ref || {};
        entry.source_ref = Object.assign({}, nestedSourceRef, sourceMeta, {
          source_id: bItem.sourceRef,
          source_uid: sourceMeta.source_uid || nestedSourceRef.source_uid || "",
          job_id: sourceMeta.job_id || nestedSourceRef.job_id || "",
          version_id: sourceMeta.version_id || nestedSourceRef.version_id || "",
          geometry_hash: sourceMeta.geometry_hash || nestedSourceRef.geometry_hash || "",
          checksum: sourceMeta.checksum || nestedSourceRef.checksum || "",
        });
        if (bItem.allowDisabledUse) {
          entry.allow_disabled_use = true;
          entry.disabled_use_reason = bItem.disabledUseReason;
        }
      } else {
        entry.xyz = bItem.xyz || "";
      }
      items.push(entry);
    }
    var globalCharge = parseInt(document.getElementById("modal-charge").value, 10) || 0;
    var globalMult = parseInt(document.getElementById("modal-mult").value, 10) || 1;
    var batchMethod = applyBatchOptimizeMethodFields(Object.assign({}, methodBase || {}));
    return {
      input: { source_type: "batch_structures", charge: globalCharge, multiplicity: globalMult, items: items },
      method: batchMethod
    };
  }
  var sourceJob = String(document.getElementById("stage-source-job").value || "").trim();
  var artifact = String(document.getElementById("stage-artifact").value || "").trim();
  if (!sourceJob) {
    throw new Error(t("modal.stage_need_source") || "需要源任务 Job ID");
  }
  var inputPayload = {
    source_type: "stage_artifact",
    source_job_id: sourceJob,
    from_artifact: artifact
  };
  var method = methodBase || {};
  var select = String(document.getElementById("stage-select").value || "").trim();
  if (select) method.select = select.split(",").map(function(s) { return s.trim(); }).filter(Boolean);
  if (workflow === "PESsearch") {
    var product = String(document.getElementById("stage-product").value || "").trim();
    if (product) inputPayload.product = product;
    var tsGuess = String(document.getElementById("stage-ts-guess").value || "").trim();
    if (tsGuess) inputPayload.ts_guess = tsGuess;
    var planText = String(document.getElementById("stage-plan").value || "").trim();
    if (planText) {
      try {
        inputPayload.coordinate_plan = JSON.parse(planText);
      } catch (e) {
        throw new Error(t("modal.stage_bad_plan") || "反应坐标计划不是合法 JSON");
      }
    }
  }
  return { input: inputPayload, method: method };
}

async function submitStageWorkflowJob(workflow) {
  var nproc = parseInt(document.getElementById("modal-nproc").value, 10) || 4;
  var mem = ACPMemoryInput.read("modal-mem", "modal-mem-unit");
  if (!mem) return;
  var remark = String(document.getElementById("modal-remark").value || "").trim();
  var taskName = String(document.getElementById("modal-task-name").value || "").trim();
  var methodBase = {
    schema_id: wizardState.workflow.schema_id || "",
    profile_id: wizardState.method.profile_id
  };
  var payload;
  try {
    payload = collectStageJobPayload(workflow, methodBase);
  } catch (err) {
    jobFormValidationFailed(String(err.message || err));
    return;
  }
  var body = {
    workflow: workflow,
    remark: remark,
    input: payload.input,
    method: payload.method,
    resources: { nproc: nproc, mem: mem }
  };
  // The server resolves molecule_name from source_job_id.  Do not copy the
  // source Job ID into the new task identity.
  if (taskName) body.task_name = taskName;
  var modalProjectId = document.getElementById("modal-project-select").value;
  if (modalProjectId) body.project_id = modalProjectId;
  // T12: node selection — only an explicit user pick is sent for stage
  // chains; the server inherits the source job's pin otherwise (T6).
  applyNodeSelectionToBody(body, { requireUserChoice: true });
  closeModal();
  try {
    var created = await api("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (created && created.__editCollected) return;
    await refreshJobs();
    if (created && created.job_id) await selectJob(created.job_id);
  } catch (err) {
    window.alert((t("modal.submit_failed") || "提交失败") + ": " + formatSubmitError(err));
  }
}

async function openWorkflowConfig() {
  var container = document.getElementById("workflow-categories");
  document.getElementById("workflow-config-modal").style.display = "flex";

  if (!workflowCatalogCache.length) {
    container.innerHTML = '<div class="muted">' + (t("modal.loading") || "\u52a0\u8f7d\u4e2d...") + '</div>';
    try {
      await loadWorkflowCatalog();
    } catch(e) {
      container.innerHTML = '<div class="parse-errors" style="display:block">' + escapeHtml(e.message) +
        '<br><button class="btn ghost" onclick="openWorkflowConfig()">' + (t("modal.retry") || "\u91cd\u8bd5") + '</button></div>';
      return;
    }
    if (!workflowCatalogCache.length) {
      container.innerHTML = '<div class="muted">' + (t("modal.catalog_empty") || "\u76ee\u5f55\u4e3a\u7a7a") + '</div>';
      return;
    }
  }

  container.innerHTML = "";
  var workflowLockHint = window.ACPJobEditor && ACPJobEditor.workflowSelectionError
    ? workflowCatalogCache.map(function(wf) { return ACPJobEditor.workflowSelectionError(wf.id); }).find(Boolean)
    : "";
  if (workflowLockHint) {
    var hint = document.createElement("div");
    hint.className = "edit-banner-hint";
    hint.textContent = workflowLockHint;
    container.appendChild(hint);
  }
  var categories = {};
  workflowCatalogCache.forEach(function(wf) {
    if (!categories[wf.category]) categories[wf.category] = [];
    categories[wf.category].push(wf);
  });

  var catLabels = {
    simple: t("modal.cat_simple") || "\u7b80\u5355\u8ba1\u7b97",
    preset: t("modal.cat_preset") || "\u9884\u8bbe\u5de5\u4f5c\u6d41",
    custom: t("modal.cat_custom") || "\u81ea\u5b9a\u4e49",
    mechanism: t("modal.cat_mechanism") || "\u673a\u7406\u5de5\u4f5c\u6d41",
    stages: t("modal.cat_stages") || "\u9636\u6bb5\u5de5\u4f5c\u6d41"
  };

  Object.keys(categories).forEach(function(cat) {
    var catDiv = document.createElement("div");
    catDiv.className = "workflow-category";
    var label = document.createElement("div");
    label.className = "workflow-category-label";
    label.textContent = catLabels[cat] || cat;
    catDiv.appendChild(label);

    var grid = document.createElement("div");
    grid.className = "workflow-grid";
    categories[cat].forEach(function(wf) {
      var opt = document.createElement("div");
      opt.className = "workflow-option";
      opt.setAttribute("data-workflow-id", wf.id);
      if (wf.id === wizardState.workflow.id) opt.classList.add("selected");
      var selectionError = window.ACPJobEditor && ACPJobEditor.workflowSelectionError
        ? ACPJobEditor.workflowSelectionError(wf.id) : "";
      if (wf.status === "planned" || selectionError) {
        opt.classList.add("disabled");
        opt.setAttribute("aria-disabled", "true");
        if (selectionError) opt.title = selectionError;
      }
      var cat2 = currentLang === "zh-CN" ? "label_zh" : "label";
      opt.innerHTML =
        '<div class="workflow-option-title">' + escapeHtml(wf[cat2] || wf.label) +
          (wf.status === "planned" ? ' <span style="font-size:10px;color:var(--amber)">(' + (t("modal.planned") || "\u8ba1\u5212\u4e2d") + ')</span>' : '') +
        '</div>' +
        '<div class="workflow-option-desc">' + escapeHtml(
          currentLang === "zh-CN" ? (wf.description_zh || wf.description || "") : (wf.description || "")
        ) + '</div>';
      if (wf.status !== "planned" && !selectionError) {
        opt.addEventListener("click", function() {
          wizardState.workflow = {
            id: wf.id,
            label: wf.label,
            schema_id: canonicalMethodSchemaId(wf.id, wf.method_schema_id)
          };
           if (wf.id === "PESsearch") resetPESSelection();
          container.querySelectorAll(".workflow-option").forEach(function(o) { o.classList.remove("selected"); });
          opt.classList.add("selected");
          loadDefaultMethodProfile(wf.method_schema_id);
          // Mechanism Builder: show the coordinate-plan panel, hide for others.
          var mechPanel = document.getElementById("mech-builder-panel");
          if (mechPanel) {
            mechPanel.style.display = wf.id === "mechanism" ? "block" : "none";
            if (wf.id === "mechanism") hookMechanismBuilder();
          }
          configureStageSourcePanel(wf.id);
          updateS2InlineVisibility();
          updateInputModeVisibility();
          if (window.ACPJobEditor && ACPJobEditor.isActive()) ACPJobEditor.refreshUi();
        });
      }
      grid.appendChild(opt);
    });
    catDiv.appendChild(grid);
    container.appendChild(catDiv);
  });
}

function loadDefaultMethodProfile(schemaId) {
  schemaId = canonicalMethodSchemaId(wizardState.workflow && wizardState.workflow.id, schemaId);
  if (wizardState.workflow) wizardState.workflow.schema_id = schemaId;
  var schema = (methodCatalogCache.method_schemas || {})[schemaId];
  if (!schema) {
    wizardState.method.profile_id = "";
    wizardState.method.profile_label = "";
    wizardState.method.stages = {};
    if (wizardState.workflow && wizardState.workflow.id === "mechanism") syncMechanismPanelFromProfile(null);
    updateConfigCards();
    return;
  }
  var profile = pickDefaultProfile(schema);
  if (profile) {
    wizardState.method.profile_id = profile.profile_id;
    wizardState.method.profile_label = currentLang === "zh-CN"
      ? (profile.label_zh || profile.label)
      : profile.label;
    wizardState.method.stages = JSON.parse(JSON.stringify(profile.levels || {}));
    if (wizardState.workflow && wizardState.workflow.id === "mechanism") syncMechanismPanelFromProfile(profile);
  } else {
    wizardState.method.profile_id = "";
    var firstLevel = (schema.method_levels || [])[0];
    wizardState.method.profile_label = firstLevel ? (firstLevel.label_zh || firstLevel.label || firstLevel.level_id) : "";
    wizardState.method.stages = {};
    if (wizardState.workflow && wizardState.workflow.id === "mechanism") syncMechanismPanelFromProfile(null);
  }
  updateConfigCards();
}

async function openMethodConfig() {
  ensureWizardWorkflowValid();
  // Draft semantics (P-fix §2⑤): every field handler mutates
  // wizardState.method directly, so snapshot it on open. Cancel/close
  // restores the snapshot; only the confirm button keeps the changes.
  var _methodDraft = JSON.parse(JSON.stringify(wizardState.method));
  function revertMethodDraft() {
    wizardState.method = _methodDraft;
    document.getElementById("method-config-modal").style.display = "none";
    updateConfigCards();
  }
  window._revertMethodDraft = revertMethodDraft;
  var title = document.getElementById("mc-modal-title");
  var subtitle = document.getElementById("mc-modal-subtitle");
  var wfEntry = workflowCatalogCache.find(function(w) { return w.id === wizardState.workflow.id; });
  var wfLabel = wfEntry
    ? (currentLang === "zh-CN" ? (wfEntry.label_zh || wfEntry.label || wfEntry.id) : (wfEntry.label || wfEntry.id))
    : "";
  if (wizardState.workflow.id === "BatchOptimize") {
    title.textContent = currentLang === "zh-CN" ? "\u6279\u91cf\u4f18\u5316\u8bbe\u7f6e" : "Batch Optimization Settings";
    subtitle.textContent = "";
    subtitle.style.display = "none";
  } else {
    title.textContent = (wizardState.workflow.id === "PESsearch"
      ? "配置计算协议"
      : (t("modal.method_config") || "\u914d\u7f6e\u8ba1\u7b97\u65b9\u6cd5")) + " - " + wfLabel;
    subtitle.style.display = "none";
  }
  // rank1-only default applies only on first entry per workflow; a user's
  // explicit choice must survive modal re-open (otherwise it is silently
  // reset to the energy-workflow default on every open).
  if (wizardState.method.rank1_wf !== wizardState.workflow.id) {
    wizardState.method.rank1_only = (wizardState.workflow.id === "energy");
    wizardState.method.rank1_wf = wizardState.workflow.id;
  }

  document.getElementById("method-config-modal").style.display = "flex";

  // Add "恢复当前页默认" button for batch workflow
  var existingRestoreBtn = document.getElementById("mt-config-restore-default");
  if (existingRestoreBtn) existingRestoreBtn.remove();
  if (wizardState.workflow.id === "BatchOptimize") {
    var footerEl = document.querySelector("#method-config-modal .modal-footer");
    if (footerEl) {
      var restoreBtn = document.createElement("button");
      restoreBtn.type = "button";
      restoreBtn.className = "btn ghost";
      restoreBtn.id = "mt-config-restore-default";
      restoreBtn.textContent = currentLang === "zh-CN" ? "\u6062\u590d\u5f53\u524d\u9875\u9ed8\u8ba4" : "Restore page defaults";
      restoreBtn.style.marginRight = "auto";
      restoreBtn.addEventListener("click", function() {
        var br = window._batchRoles;
        if (!br) return;
        var activeRole = window._batchRoleActive_ref ? window._batchRoleActive_ref() : "int";
        var defaults = window._BATCH_ROLE_DEFAULTS;
        if (defaults && br[activeRole]) {
          br[activeRole] = JSON.parse(JSON.stringify(defaults[activeRole]));
          if (typeof window._renderBatchRoleForm === "function") window._renderBatchRoleForm();
        }
      });
      footerEl.insertBefore(restoreBtn, footerEl.firstChild);
    }
  }

  try { await loadMethodCatalog(); } catch(e) {
    document.getElementById("mc-levels-container").innerHTML = '<div class="parse-errors" style="display:block">' + escapeHtml(e.message) + '</div>';
    return;
  }
  if (!methodCatalogCache.method_schemas || !methodCatalogCache.functional_options_map) {
    // R16: surface a retry control instead of dead-end plain text so the user
    // can recover without a full page reload.
    var retryHtml = '<div class="muted">' + (t("modal.catalog_load_failed") || "目录加载失败") +
      '<br><button class="btn ghost" style="margin-top:8px" onclick="openMethodConfig()">' +
      (t("modal.retry") || "重试") + '</button></div>';
    document.getElementById("mc-levels-container").innerHTML = retryHtml;
    return;
  }

  var schemaId = canonicalMethodSchemaId(
    wizardState.workflow.id,
    wizardState.workflow.schema_id || (wfEntry && wfEntry.method_schema_id) || ""
  );
  wizardState.workflow.schema_id = schemaId;
  var schema = (methodCatalogCache.method_schemas || {})[schemaId];
  if (!schema) {
    document.getElementById("mc-levels-container").innerHTML = '<div class="muted">' + (t("modal.no_method_schema") || "\u65e0\u65b9\u6cd5\u914d\u7f6e") + '</div>';
    document.getElementById("method-config-modal").style.display = "flex";
    return;
  }

  // Phase 4.1: closure helpers delegate to CatalogUtils IIFE so the
  // catalog-access functions live in a single, independently-testable
  // module. The delegates here keep existing call sites unchanged while
  // automatically supplying the current methodCatalogCache state (R26).

  function getFieldOptions(fieldName, engine, functional, basis) {
    return CatalogUtils.getFieldOptions(methodCatalogCache, fieldName, engine, functional, basis);
  }

  function getFieldDefault(fieldName, engine, functional, basis) {
    return CatalogUtils.getFieldDefault(methodCatalogCache, fieldName, engine, functional, basis);
  }

  function isFieldAdvanced(fieldName) {
    return CatalogUtils.isFieldAdvanced(methodCatalogCache, fieldName);
  }

  function fieldBaseName(fieldName) {
    return CatalogUtils.fieldBaseName(fieldName);
  }

  function isFieldFamilyLocked(fieldName, functional) {
    return CatalogUtils.isFieldFamilyLocked(methodCatalogCache, fieldName, functional);
  }

  function applyMethodFamilyState(levelState, fieldNames, engine, functional) {
    return CatalogUtils.applyMethodFamilyState(methodCatalogCache, levelState, fieldNames, engine, functional);
  }

  function buildLevelCard(lvDef, stateData) {
    var engine = (stateData && stateData.engine) || lvDef.allowed_engines[0] || "";
    var expanded = true;
    var isOptional = lvDef.required === false;
    var stored = wizardState.method.stages[lvDef.level_id];
    var levelDisabled = isOptional && ((stateData && stateData._disabled === true) || !stored || !stored.engine);
    if (levelDisabled) expanded = false;
    var card = document.createElement("div");
    card.className = "mc-level-card";
    if (lvDef.level_id === "batch") card.classList.add("mc-batch-flat");
    card.setAttribute("data-level-id", lvDef.level_id);

    var hdr = document.createElement("div");
    hdr.className = "mc-level-header";

    var expBtn = document.createElement("span");
    expBtn.className = "mc-level-expand" + (expanded ? " open" : "");
    expBtn.innerHTML = '<svg viewBox="0 0 16 16" width="12" height="12" stroke="currentColor" fill="none" stroke-width="2"><path d="M6 4l4 4-4 4"/></svg>';
    hdr.appendChild(expBtn);

    if (isOptional) {
      var enableBox = document.createElement("input");
      enableBox.type = "checkbox";
      enableBox.className = "mc-level-enable";
      enableBox.checked = !levelDisabled;
      enableBox.title = levelDisabled
        ? (t("modal.level_enable") || "\u542f\u7528\u6b64\u9636\u6bb5")
        : (t("modal.level_disable") || "\u53d6\u6d88\u542f\u7528\uff08\u7b49\u4ef7 --no-opt \u8bed\u4e49\uff09");
      enableBox.addEventListener("click", function(ev) { ev.stopPropagation(); });
      enableBox.addEventListener("change", function(ev) {
        ev.stopPropagation();
        var st = wizardState.method.stages[lvDef.level_id];
        if (enableBox.checked) {
          if (!st) { st = {}; wizardState.method.stages[lvDef.level_id] = st; }
          delete st._disabled;
          if (!st.engine) st.engine = lvDef.allowed_engines[0];
        } else if (st) {
          st._disabled = true;
        }
        rebuildLevelCard(lvDef.level_id);
        scheduleValidate();
      });
      hdr.appendChild(enableBox);
    }

    var nm = document.createElement("span");
    nm.className = "mc-level-name";
    nm.textContent = lvDef.label_zh || lvDef.label || lvDef.level_id;
    if (levelDisabled) nm.style.opacity = "0.45";
    hdr.appendChild(nm);

    if (lvDef.allowed_engines.length === 1 && !lvDef.hide_engine) {
      var engBadge = document.createElement("span");
      engBadge.className = "mc-level-engine";
      engBadge.textContent = lvDef.allowed_engines[0];
      hdr.appendChild(engBadge);
    }

    var statusDot = document.createElement("span");
    statusDot.className = "mc-level-status ok";
    hdr.appendChild(statusDot);

    // ── Copy scan level button (Item 4) ──
    // For single_point level in pes_scan schema: button copies scan_optimizer
    // level fields into single_point, then rebuilds the card.
    if (lvDef.level_id === "single_point" && wizardState.workflow.schema_id === "pes_scan") {
      var copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = "btn ghost mc-copy-scan-level";
      copyBtn.style.cssText = "margin-left:auto;font-size:11px;padding:2px 8px;white-space:nowrap;";
      copyBtn.textContent = t("modal.copy_scan_level") || "复制扫描计算级别";
      copyBtn.title = t("modal.copy_scan_level_hint") || "将扫描点优化的计算级别复制到单点能";
      var _scanOptState = wizardState.method.stages.scan_optimizer || {};
      var _hasScanState = _scanOptState.scan_optimizer_method || _scanOptState.engine;
      copyBtn.disabled = !_hasScanState;
      copyBtn.addEventListener("click", function(ev) {
        ev.stopPropagation();
        var _src = wizardState.method.stages.scan_optimizer || {};
        var _dst = getLevelState("single_point");
        // T16: copy + family reconciliation in one catalog-driven step.
        // The method travels with the copy (scan_optimizer_method →
        // functional), so copyScanLevelFields reconciles every destination
        // field against the COPIED method's family — copying a GFN scan
        // level must yield a GFN single-point level with basis/dispersion/
        // RI/aux/grid/SCF cleared, never a stale DFT configuration.
        CatalogUtils.copyScanLevelFields(methodCatalogCache, _src, _dst, lvDef.fields || [], engine);
        rebuildLevelCard("single_point");
        scheduleValidate();
      });
      hdr.appendChild(copyBtn);
    }

    card.appendChild(hdr);

    var bodyDiv = document.createElement("div");
    bodyDiv.className = "mc-level-body";
    bodyDiv.style.display = expanded ? "grid" : "none";

    if (levelDisabled) {
      card.appendChild(bodyDiv);
      hdr.addEventListener("click", function() { /* disabled level: no expand */ });
      return card;
    }

    if (lvDef.allowed_engines.length > 1 && !lvDef.hide_engine) {
      var engField = document.createElement("div");
      engField.className = "mc-field";
      var engLbl = document.createElement("label");
      engLbl.textContent = "engine";
      engField.appendChild(engLbl);
      var engSel = document.createElement("select");
      lvDef.allowed_engines.forEach(function(e) {
        var o = document.createElement("option");
        o.value = e;
        o.textContent = e;
        if (e === engine) o.selected = true;
        engSel.appendChild(o);
      });
      engSel.addEventListener("change", function() {
        var newEngine = engSel.value;
        var levelState = getLevelState(lvDef.level_id);
        levelState.engine = newEngine;
        rebuildLevelCard(lvDef.level_id);
        scheduleValidate();
      });
      engField.appendChild(engSel);
      bodyDiv.appendChild(engField);
    }

    var coreFields = [];
    var advancedFields = [];
    var batchSteps = (lvDef.level_id === "batch" && stateData && stateData.steps) || [];
    function batchHasStep(stepName) {
      if (batchSteps.indexOf(stepName) >= 0) return true;
      if (stepName === "singlepoint" && batchSteps.indexOf("single_point") >= 0) return true;
      var profileSteps = {
        opt_only: ["optimize"],
        opt_freq: ["optimize", "frequency"],
        opt_freq_sp: ["optimize", "frequency", "singlepoint"],
        opt_freq_sp_thermo: ["optimize", "frequency", "singlepoint", "thermochemistry"],
      }[wizardState.method.profile_id] || [];
      return profileSteps.indexOf(stepName) >= 0;
    }
    var isBatchFieldVisible = function(fieldName) {
      if (lvDef.level_id !== "batch") return true;
      if (["single_point_method", "single_point_basis"].indexOf(fieldName) >= 0) {
        return batchHasStep("singlepoint");
      }
      if (["temperature", "pressure", "scale_factor"].indexOf(fieldName) >= 0) {
        return batchHasStep("thermochemistry");
      }
      return true;
    };
    (lvDef.fields || []).forEach(function(fn) {
      // BatchOptimize has no shared method form.  Every editable field is
      // rendered below against the selected batch_roles.{int,ts} draft.
      if (lvDef.level_id === "batch") return;
      if (!isBatchFieldVisible(fn)) return;
      if (isFieldAdvanced(fn)) advancedFields.push(fn);
      else coreFields.push(fn);
    });

    function renderRiImmutableBadge(fDiv, riSupport) {
      var badge = document.createElement("span");
      badge.className = "mc-builtin-badge";
      if (riSupport === "composite") {
        badge.textContent = (t("modal.ri_composite") || "复合方法 · 参数已固化");
        badge.title = (t("modal.ri_composite_hint") || "3c 复合方法已内置 RI 体系，无需用户配置");
      } else if (riSupport === "automatic") {
        badge.textContent = (t("modal.ri_automatic") || "RI 自动 · 后 HF 方法内置");
        badge.title = (t("modal.ri_automatic_hint") || "DLPNO 等后 HF 方法自带 RI 体系，参数自动处理");
      }
      fDiv.appendChild(badge);
    }

    function buildFieldRow(fieldName, dest) {
      // Hessian field is rendered by a dedicated tri-state control that
      // also drives a backend preview (plan §13). Bypass the generic
      // select/number renderer entirely.
      if (fieldName === "recalc_hess" || fieldName === "opt_recalc_hess") {
        buildHessianFieldRow(dest);
        return;
      }
      var fDiv = document.createElement("div");
      fDiv.className = "mc-field";
      var lbl = document.createElement("label");
      var fieldFdLbl = ((methodCatalogCache.field_definitions || {})[fieldName] || {});
      var lblKey = currentLang === "zh-CN" ? "label_zh" : "label";
      var batchLabelOverrides = {
        functional: { label: "Optimization + Frequency Method", label_zh: "\u4f18\u5316\u4e0e\u9891\u7387\u65b9\u6cd5" },
        basis: { label: "Optimization + Frequency Basis", label_zh: "\u4f18\u5316\u4e0e\u9891\u7387\u57fa\u7ec4" },
        opt_max_iter: { label: "Max Optimization Iterations", label_zh: "\u6700\u5927\u4f18\u5316\u5faa\u73af" },
        opt_convergence: { label: "Convergence Criteria", label_zh: "\u6536\u655b\u6807\u51c6" },
        opt_trust_radius: { label: "Initial Trust Radius", label_zh: "\u521d\u59cb\u4fe1\u8d56\u534a\u5f84" },
        opt_recalc_hess: { label: "Recalculate Every N Optimization Cycles", label_zh: "\u6bcf N \u4e2a\u4f18\u5316\u5faa\u73af\u91cd\u7b97" },
        scf_max_iter: { label: "Max SCF Iterations", label_zh: "\u6700\u5927 SCF \u5faa\u73af" },
        scf_convergence: { label: "SCF Convergence", label_zh: "\u6536\u655b\u6807\u51c6" },
        scf_strategy: { label: "SCF Strategy", label_zh: "\u6536\u655b\u7b56\u7565" },
        temperature: { label: "Temperature", label_zh: "\u6e29\u5ea6" },
        pressure: { label: "Pressure", label_zh: "\u538b\u529b" },
        scale_factor: { label: "Frequency Scale Factor", label_zh: "\u9891\u7387\u7f29\u653e\u56e0\u5b50" },
      };
      var labelOverride = lvDef.level_id === "batch" ? batchLabelOverrides[fieldName] : null;
      lbl.textContent = (labelOverride && labelOverride[lblKey]) || fieldFdLbl[lblKey] || fieldName.replace(/_/g, " ");
      fDiv.appendChild(lbl);

      var levelState = getLevelState(lvDef.level_id);
      // ── Method-context resolution (generalized for scan_optimizer level) ──
      // The level's method field determines basis/dispersion/RI linkage.
      // scan_optimizer uses its own prefixed field names; single_point uses
      // single_point_method; everything else uses "functional".
      var _levelMethodFieldMap = { "scan_optimizer": "scan_optimizer_method" };
      var _levelBasisFieldMap = { "scan_optimizer": "scan_optimizer_basis" };
      var _levelDispersionFieldMap = { "scan_optimizer": "scan_optimizer_dispersion" };
      var _levelRiFieldMap = { "scan_optimizer": "scan_optimizer_ri_approximation" };
      var methodFieldName = _levelMethodFieldMap[lvDef.level_id]
        || (fieldName === "single_point_method" ? "single_point_method" : "functional");
      var basisFieldName = _levelBasisFieldMap[lvDef.level_id]
        || (fieldName === "single_point_basis" ? "single_point_basis" : "basis");
      var functional = levelState[methodFieldName] || getFieldDefault(methodFieldName, engine) || "";
      var basis = levelState[basisFieldName] || getFieldDefault(basisFieldName, engine, functional) || "";
      var funcMeta = lookupCI(methodCatalogCache.method_meta || {}, functional);
      var riSupport = (funcMeta && funcMeta.ri_support) || "user";

      if ((fieldName === "ri_approximation" || fieldName === (_levelRiFieldMap[lvDef.level_id] || "__none__")) && riSupport !== "user") {
        renderRiImmutableBadge(fDiv, riSupport);
        getLevelState(lvDef.level_id)[fieldName] = "none";
        dest.appendChild(fDiv);
        return;
      }
      if ((fieldName === "aux_j_basis") && riSupport !== "user") {
        renderRiImmutableBadge(fDiv, riSupport);
        getLevelState(lvDef.level_id).aux_j_basis = "";
        dest.appendChild(fDiv);
        return;
      }
      if (fieldName === "aux_c_basis") {
        if (riSupport !== "user" || !funcMeta || !funcMeta.needs_aux_c) {
          renderRiImmutableBadge(fDiv, riSupport);
          getLevelState(lvDef.level_id).aux_c_basis = "";
          dest.appendChild(fDiv);
          return;
        }
      }

      var rawVal = levelState[fieldName];
      var curVal = (rawVal !== undefined && rawVal !== "")
          ? rawVal
          : getFieldDefault(
              fieldName,
              engine,
              ["functional", "single_point_method"].indexOf(fieldName) >= 0 ? undefined : functional,
              basis
            );

      // ── builtin_dispersion lock (DevDoc §6) ──
      // Functionals that hardwire dispersion into the method name (wB97X-D4, wB97M-V)
      // or parametrisation (r2SCAN-3c, PBEh-3c, B97-3c) must never emit an
      // explicit ORCA route keyword — the functional already handles it.
      // The badge shows which dispersion is built in; the value sent to the
      // backend is always "none".
      if (fieldName === "dispersion" || fieldName === (_levelDispersionFieldMap[lvDef.level_id] || "__none__")) {
        if (funcMeta && funcMeta.builtin_dispersion) {
          var badgeSpan = document.createElement("span");
          badgeSpan.className = "mc-builtin-badge";
          badgeSpan.textContent = funcMeta.builtin_dispersion + " (" + (t("modal.builtin") || "内置") + ")";
          badgeSpan.title = t("modal.builtin_dispersion_hint") || "此泛函已内置色散校正，无需额外指定";
          fDiv.appendChild(badgeSpan);
          getLevelState(lvDef.level_id)[fieldName] = "none";
          dest.appendChild(fDiv);
          return;
        }
      }

      // ── family-unavailable fields (T15) ─────────────────────────────
      // Driven entirely by the catalog payload: isFieldFamilyLocked mirrors
      // acp.catalog._family_locked_options — the empty option offer
      // (functional_options_map[method].basis === []) marks a method family
      // with no DFT layer (T11). Basis locks to its built-in value,
      // dispersion keeps its built-in badge (handled above), RI locks to
      // "none" (handled above via ri_support), and grid/SCF selects are
      // hidden with their values cleared so a stale selection can never
      // reach the protocol payload. Never keyed on method names
      // (AGENTS anti-pattern #27) — the catalog is the single source.
      if (isFieldFamilyLocked(fieldName, functional)) {
        var famBase = fieldBaseName(fieldName);
        var famState = getLevelState(lvDef.level_id);
        if (famBase === "basis") {
          famState[fieldName] = "";
          var famBasisSpan = document.createElement("span");
          famBasisSpan.className = "mc-basis-readonly";
          famBasisSpan.textContent = (currentLang === "zh-CN" ? "内置：" : "Built-in: ")
            + ((funcMeta && funcMeta.default_basis) || (currentLang === "zh-CN" ? "无独立基组" : "none"));
          famBasisSpan.title = (currentLang === "zh-CN" ? "由 " + functional + " 固定" : "Fixed by " + functional);
          fDiv.appendChild(famBasisSpan);
          dest.appendChild(fDiv);
          return;
        }
        if (famBase === "dispersion") {
          famState[fieldName] = "none";
          var famDispSpan = document.createElement("span");
          famDispSpan.className = "mc-builtin-badge";
          famDispSpan.textContent = ((funcMeta && funcMeta.builtin_dispersion) || (currentLang === "zh-CN" ? "内置" : "built in"))
            + " (" + (t("modal.builtin") || "内置") + ")";
          fDiv.appendChild(famDispSpan);
          dest.appendChild(fDiv);
          return;
        }
        // grid / scf_* selects: hidden entirely for this family.
        famState[fieldName] = "";
        return;
      }

      var funcOptsMap = (methodCatalogCache && methodCatalogCache.functional_options_map) || {};
      var funcFilter = lookupCI(funcOptsMap, functional);

      // (T23g) base-name resolution delegates to the single authority,
      // CatalogUtils.fieldBaseName (scan_optimizer_basis → basis, …) — the
      // inline _scopedFieldBase duplicate is retired.
      var filterFieldName = fieldBaseName(fieldName);

      var opts = null;
      if ((filterFieldName === "basis" || filterFieldName === "dispersion") && funcFilter && funcFilter[filterFieldName]) {
        opts = funcFilter[filterFieldName];
      }
      if (!opts) {
        opts = getFieldOptions(fieldName, engine, functional, basis);
      }

      var isSingleLocked = opts && opts.length === 1;
      var isBasisField = ["basis", "single_point_basis", "scan_optimizer_basis"].indexOf(fieldName) >= 0;
      var isBasisSingleLocked = isSingleLocked && isBasisField;
      var fieldFd = (methodCatalogCache.field_definitions || {})[fieldName];
      var optLabelsZh = fieldFd && fieldFd.option_labels_zh ? fieldFd.option_labels_zh : {};
      var useOptZh = currentLang === "zh-CN" && Object.keys(optLabelsZh).length > 0;
      // Optional per-level override fields ("不覆盖（使用统一设置）" empty
      // option). T23g audit: field-name list kept — it encodes UI override
      // rendering semantics, not method data (the catalog declares no
      // override flag for these).
      var isOptionalOverride = ["minimum_method", "minimum_basis", "transition_state_method", "transition_state_basis"].indexOf(fieldName) >= 0;
      // (T23g) custom-input support is the catalog's supports_custom flag
      // gated by the override / basis-single-locked render states — the old
      // hardcoded basis-family allowlist duplicated exactly this (the
      // catalog flag minus the override fields) and is retired.
      var supportsCustom = fieldFd && fieldFd.supports_custom && !isOptionalOverride && !isBasisSingleLocked;
      var isCustomActive = supportsCustom && curVal && (
        String(curVal) === "__custom__" || (opts && opts.indexOf(String(curVal)) < 0)
      );

      // Multi-select fields (e.g. NMR "nuclei"): value is an array of options.
      if (fieldFd && fieldFd.multi && opts && opts.length) {
        var msel = document.createElement("select");
        msel.multiple = true;
        var curArr = Array.isArray(curVal) ? curVal.map(String) : (curVal ? [String(curVal)] : []);
        opts.forEach(function(o) {
          var mopt = document.createElement("option");
          mopt.value = o;
          mopt.textContent = useOptZh && optLabelsZh[o] ? optLabelsZh[o] : o;
          if (curArr.indexOf(String(o)) >= 0) mopt.selected = true;
          msel.appendChild(mopt);
        });
        msel.addEventListener("change", function() {
          var vals = [];
          for (var mi = 0; mi < msel.options.length; mi++) {
            if (msel.options[mi].selected) vals.push(msel.options[mi].value);
          }
          getLevelState(lvDef.level_id)[fieldName] = vals;
          scheduleValidate();
        });
        fDiv.appendChild(msel);
        dest.appendChild(fDiv);
        return;
      }

      if (opts && opts.length) {
        var sel = document.createElement("select");
        var hasMatch = false;
        var optionMeta = ((methodCatalogCache.field_definitions || {})[fieldName] || {}).option_meta || {};
        if (isOptionalOverride && !curVal) {
          var emptyOpt = document.createElement("option");
          emptyOpt.value = "";
          emptyOpt.textContent = currentLang === "zh-CN" ? "不覆盖（使用统一设置）" : "No override (use shared setting)";
          emptyOpt.selected = true;
          hasMatch = true;
          sel.appendChild(emptyOpt);
        }
        // ── explicit empty default (T15) ──
        // Selects whose catalog default is "" (e.g. grid: DefGrid1/2/3 with
        // default "" = keep the ORCA default) must be able to represent that
        // default; otherwise the browser silently displays the first option
        // while the stored value stays "".
        if (!isOptionalOverride && !supportsCustom && curVal === "" && opts.indexOf("") < 0) {
          var defaultEmptyOpt = document.createElement("option");
          defaultEmptyOpt.value = "";
          defaultEmptyOpt.textContent = currentLang === "zh-CN" ? "默认（留空）" : "Default (empty)";
          defaultEmptyOpt.selected = true;
          hasMatch = true;
          sel.appendChild(defaultEmptyOpt);
        }
        // ── optgroup rendering (Item 1: catalog-driven option_groups) ──
        // When the field definition carries option_groups, render <optgroup>
        // elements grouping those options; remaining options render ungrouped.
        var fieldDefForGroups = (methodCatalogCache.field_definitions || {})[fieldName] || {};
        var optionGroups = fieldDefForGroups.option_groups;
        if (optionGroups && optionGroups.length) {
          var groupedOptionSet = {};
          var grpLblKey = currentLang === "zh-CN" ? "label_zh" : "label";
          optionGroups.forEach(function(grp) {
            var og = document.createElement("optgroup");
            og.label = grp[grpLblKey] || grp.label || grp.group_id;
            (grp.options || []).forEach(function(o) {
              groupedOptionSet[String(o)] = true;
              var opt = document.createElement("option");
              opt.value = o;
              opt.textContent = useOptZh && optLabelsZh[o] ? optLabelsZh[o] : o;
              if (optionMeta[o]) opt.title = optionMeta[o];
              if (String(curVal).toLowerCase() === String(o).toLowerCase()) { opt.selected = true; hasMatch = true; }
              og.appendChild(opt);
            });
            sel.appendChild(og);
          });
          // Ungrouped options (not in any group)
          opts.forEach(function(o) {
            if (groupedOptionSet[String(o)]) return;
            var opt = document.createElement("option");
            opt.value = o;
            opt.textContent = useOptZh && optLabelsZh[o] ? optLabelsZh[o] : o;
            if (optionMeta[o]) opt.title = optionMeta[o];
            if (String(curVal).toLowerCase() === String(o).toLowerCase()) { opt.selected = true; hasMatch = true; }
            sel.appendChild(opt);
          });
        } else {
          opts.forEach(function(o) {
            var opt = document.createElement("option");
            opt.value = o;
            opt.textContent = useOptZh && optLabelsZh[o] ? optLabelsZh[o] : o;
            if (optionMeta[o]) {
              opt.title = optionMeta[o];
            }
            if (String(curVal).toLowerCase() === String(o).toLowerCase()) { opt.selected = true; hasMatch = true; }
            sel.appendChild(opt);
          });
        }
        if (supportsCustom) {
          var customOpt = document.createElement("option");
          customOpt.value = "__custom__";
          customOpt.textContent = "Custom...";
          if (!hasMatch && curVal) customOpt.selected = true;
          sel.appendChild(customOpt);
        }
        var showBuiltinNote = ["functional", "basis", "single_point_method", "single_point_basis", "dispersion", "ri_approximation", "scan_optimizer_method", "scan_optimizer_basis", "scan_optimizer_dispersion", "scan_optimizer_ri_approximation"].indexOf(fieldName) >= 0;
        if (isSingleLocked && !supportsCustom && showBuiltinNote) {
          sel.disabled = true;
        }
        sel.addEventListener("change", function() {
          if (supportsCustom && sel.value === "__custom__") {
            getLevelState(lvDef.level_id)[fieldName] = "__custom__";
            rebuildLevelCard(lvDef.level_id);
            scheduleValidate();
            return;
          }
          getLevelState(lvDef.level_id)[fieldName] = sel.value;
          if (isCustomActive) rebuildLevelCard(lvDef.level_id);
          scheduleValidate();
        });
        if (fieldName === "functional") {
          sel.addEventListener("change", function() {
            var newFunctional = sel.value;
            var st = getLevelState(lvDef.level_id);
            // Re-read funcOptsMap from the live cache (R15: avoid stale closure)
            // and use case-insensitive lookup (R4 / DevDoc §7).
            var liveFuncOptsMap = (methodCatalogCache && methodCatalogCache.functional_options_map) || {};
            var liveFuncMeta = lookupCI(methodCatalogCache.method_meta || {}, newFunctional);
            var newFuncFilter = lookupCI(liveFuncOptsMap, newFunctional);

            // 1. Basis legality check + default-value fill (prefer METHOD_META.default_basis).
            //    Use an explicit `!== undefined` check rather than `||` so a future
            //    functional whose default_basis is "" but whose basis list has a
            //    non-empty first element doesn't silently pick the wrong fallback
            //    (audit A10).
            if (newFuncFilter && newFuncFilter.basis) {
              var curBasis = st.basis;
              if (!curBasis || curBasis === "__custom__" || newFuncFilter.basis.indexOf(String(curBasis)) < 0) {
                st.basis = (liveFuncMeta && liveFuncMeta.default_basis !== undefined)
                    ? liveFuncMeta.default_basis
                    : newFuncFilter.basis[0];
              }
            }

            // 2. Dispersion legality check + default-value fill (same pattern; audit A11)
            if (newFuncFilter && newFuncFilter.dispersion) {
              var curDisp = st.dispersion;
              if (!curDisp || newFuncFilter.dispersion.indexOf(String(curDisp)) < 0) {
                st.dispersion = (liveFuncMeta && liveFuncMeta.default_dispersion !== undefined)
                    ? liveFuncMeta.default_dispersion
                    : newFuncFilter.dispersion[0];
              }
            }

            // 3. RI / aux_j / aux_c: based on ri_support three-state.
            //    composite/automatic: force-clear all RI/aux fields.
            //    user: backfill defaults using current basis for aux derivation.
            var newRiSupport = (liveFuncMeta && liveFuncMeta.ri_support) || "user";
            if (newRiSupport !== "user") {
              st.ri_approximation = "none";
              st.aux_j_basis = "";
              st.aux_c_basis = "";
            } else {
              if (st.ri_approximation === undefined || st.ri_approximation === "" || st.ri_approximation === "none") {
                st.ri_approximation = getFieldDefault("ri_approximation", engine, newFunctional);
              }
              if (st.aux_j_basis === undefined || st.aux_j_basis === "") {
                st.aux_j_basis = getFieldDefault("aux_j_basis", engine, newFunctional, st.basis);
              }
              if (liveFuncMeta && liveFuncMeta.needs_aux_c) {
                if (st.aux_c_basis === undefined || st.aux_c_basis === "") {
                  st.aux_c_basis = getFieldDefault("aux_c_basis", engine, newFunctional, st.basis);
                }
              } else {
                st.aux_c_basis = "";
              }
            }

            // 4. Family sweep (T15): clear fields the new method family does
            //    not offer and re-default any value its scoped option set no
            //    longer admits. Field list comes from the level schema
            //    (lvDef.fields) — never a hardcoded field list.
            applyMethodFamilyState(st, lvDef.fields || [], engine, newFunctional);

            rebuildLevelCard(lvDef.level_id);
            scheduleValidate();
          });
        }
        if (fieldName === "single_point_method") {
          sel.addEventListener("change", function() {
            var newMethod = sel.value;
            var st = getLevelState(lvDef.level_id);
            var liveMeta = lookupCI(methodCatalogCache.method_meta || {}, newMethod);
            var liveFilter = lookupCI((methodCatalogCache && methodCatalogCache.functional_options_map) || {}, newMethod);
            if (liveFilter && liveFilter.basis) {
              var currentBasis = st.single_point_basis;
              if (!currentBasis || currentBasis === "__custom__" || liveFilter.basis.indexOf(String(currentBasis)) < 0) {
                st.single_point_basis = (liveMeta && liveMeta.default_basis !== undefined)
                    ? liveMeta.default_basis
                    : liveFilter.basis[0];
              }
            }
            // Family sweep (T15): lock/clear fields the new family omits.
            applyMethodFamilyState(st, lvDef.fields || [], engine, newMethod);
            rebuildLevelCard(lvDef.level_id);
            scheduleValidate();
          });
        }
        // ── scan_optimizer_method change handler (Item 2: DFT scan linkage) ──
        // Mirrors the functional change handler but uses scan_optimizer-prefixed fields.
        if (fieldName === "scan_optimizer_method") {
          sel.addEventListener("change", function() {
            var newMethod = sel.value;
            var st = getLevelState(lvDef.level_id);
            var liveFuncOptsMap = (methodCatalogCache && methodCatalogCache.functional_options_map) || {};
            var liveFuncMeta = lookupCI(methodCatalogCache.method_meta || {}, newMethod);
            var newFuncFilter = lookupCI(liveFuncOptsMap, newMethod);

            // 1. Basis legality check + default-value fill
            if (newFuncFilter && newFuncFilter.basis) {
              var curBasis = st.scan_optimizer_basis;
              if (!curBasis || curBasis === "__custom__" || newFuncFilter.basis.indexOf(String(curBasis)) < 0) {
                st.scan_optimizer_basis = (liveFuncMeta && liveFuncMeta.default_basis !== undefined)
                    ? liveFuncMeta.default_basis
                    : newFuncFilter.basis[0];
              }
            }

            // 2. Dispersion legality check + default-value fill
            if (newFuncFilter && newFuncFilter.dispersion) {
              var curDisp = st.scan_optimizer_dispersion;
              if (!curDisp || newFuncFilter.dispersion.indexOf(String(curDisp)) < 0) {
                st.scan_optimizer_dispersion = (liveFuncMeta && liveFuncMeta.default_dispersion !== undefined)
                    ? liveFuncMeta.default_dispersion
                    : newFuncFilter.dispersion[0];
              }
            }

            // 3. RI / aux: based on ri_support three-state
            var newRiSupport = (liveFuncMeta && liveFuncMeta.ri_support) || "user";
            if (newRiSupport !== "user") {
              st.scan_optimizer_ri_approximation = "none";
            } else {
              if (st.scan_optimizer_ri_approximation === undefined || st.scan_optimizer_ri_approximation === "" || st.scan_optimizer_ri_approximation === "none") {
                st.scan_optimizer_ri_approximation = getFieldDefault("scan_optimizer_ri_approximation", engine, newMethod);
              }
            }

            // 4. Family sweep (T15): clear fields the new method family does
            //    not offer (grid/SCF under a family with no DFT layer) and
            //    re-default any value its scoped option set no longer admits.
            applyMethodFamilyState(st, lvDef.fields || [], engine, newMethod);

            rebuildLevelCard(lvDef.level_id);
            scheduleValidate();
          });
        }
        if (fieldName === "basis" || fieldName === "single_point_basis") {
          sel.addEventListener("change", function() {
            var newBasis = sel.value;
            var st = getLevelState(lvDef.level_id);
            st[fieldName] = newBasis;
            if (fieldName === "single_point_basis") {
              scheduleValidate();
              return;
            }

            var curFuncMeta = lookupCI(methodCatalogCache.method_meta || {}, st.functional);
            var curRiSupport = (curFuncMeta && curFuncMeta.ri_support) || "user";
            if (curRiSupport === "user") {
              st.aux_j_basis = getFieldDefault("aux_j_basis", engine, st.functional, newBasis);
              if (curFuncMeta && curFuncMeta.needs_aux_c) {
                st.aux_c_basis = getFieldDefault("aux_c_basis", engine, st.functional, newBasis);
              }
            }

            rebuildLevelCard(lvDef.level_id);
            scheduleValidate();
          });
        }
        fDiv.appendChild(sel);
        if (isBasisSingleLocked) {
          // Single-option locked basis (e.g. 3c composite): render read-only display
          sel.style.display = "none";
          var roSpan = document.createElement("span");
          roSpan.className = "mc-basis-readonly";
          roSpan.textContent = (currentLang === "zh-CN" ? "\u5185\u7f6e\uff1a" : "Built-in: ") + (opts[0] || curVal);
          roSpan.title = (currentLang === "zh-CN" ? "\u7531 " + functional + " \u56fa\u5b9a" : "Fixed by " + functional);
          fDiv.appendChild(roSpan);
        }
        if (isCustomActive) {
          var customWrap = document.createElement("div");
          customWrap.style.marginTop = "4px";
          var cInp = document.createElement("input");
          cInp.type = "text";
          cInp.placeholder = "e.g. def2-QZVP";
          cInp.value = String(curVal) === "__custom__" ? "" : String(curVal);
          cInp.style.width = "100%";
          cInp.style.boxSizing = "border-box";
          cInp.addEventListener("input", function() {
            getLevelState(lvDef.level_id)[fieldName] = cInp.value;
            scheduleValidate();
          });
          customWrap.appendChild(cInp);
          fDiv.appendChild(customWrap);
        }
      } else if ((methodCatalogCache.field_definitions || {})[fieldName] && (methodCatalogCache.field_definitions || {})[fieldName].type === "module" && (methodCatalogCache.field_definitions || {})[fieldName].renderer === "electronic_state") {
        buildElectronicStateFieldRow(fieldName, fDiv, (methodCatalogCache.field_definitions || {})[fieldName], curVal);
      } else if ((methodCatalogCache.field_definitions || {})[fieldName] && ((methodCatalogCache.field_definitions || {})[fieldName].type === "float" || (methodCatalogCache.field_definitions || {})[fieldName].type === "int")) {
        var fdNum = (methodCatalogCache.field_definitions || {})[fieldName];
        var inp = document.createElement("input");
        inp.type = "number";
        var isIntField = fdNum.type === "int";
        if (fdNum.step) {
          inp.step = String(fdNum.step);
        } else {
          inp.step = isIntField ? "1" : "any";
        }
        if (fdNum.min !== undefined) inp.min = String(fdNum.min);
        if (fdNum.max !== undefined) inp.max = String(fdNum.max);
        // Batch nullable fields: show 自动 placeholder when null
        var isBatchNullable = lvDef.level_id === "batch" && ["opt_max_iter", "opt_trust_radius"].indexOf(fieldName) >= 0;
        if (isBatchNullable && (curVal === null || curVal === undefined || curVal === "")) {
          inp.value = "";
          inp.placeholder = currentLang === "zh-CN" ? "\u81ea\u52a8" : "Auto";
        } else {
          inp.value = curVal;
        }
        inp.addEventListener("change", function() {
          if (inp.value === "") {
            // Batch nullable: store null; others store ""
            if (isBatchNullable) {
              delete getLevelState(lvDef.level_id)[fieldName];
            } else {
              getLevelState(lvDef.level_id)[fieldName] = "";
            }
          } else if (isIntField) {
            getLevelState(lvDef.level_id)[fieldName] = parseInt(inp.value, 10);
          } else {
            getLevelState(lvDef.level_id)[fieldName] = parseFloat(inp.value) || inp.value;
          }
          scheduleValidate();
        });
        var numberWrap = document.createElement("div");
        numberWrap.className = "mc-number-with-unit";
        numberWrap.appendChild(inp);
        if (fdNum.unit) {
          var unitSpan = document.createElement("span");
          unitSpan.className = "mc-number-unit";
          unitSpan.textContent = fdNum.unit;
          unitSpan.setAttribute("aria-label", fdNum.unit);
          numberWrap.appendChild(unitSpan);
        }
        fDiv.appendChild(numberWrap);
      } else if ((methodCatalogCache.field_definitions || {})[fieldName] && (methodCatalogCache.field_definitions || {})[fieldName].type === "bool") {
        // Catalog-driven boolean field (e.g. xtbmd_censo_energy
        // md_shake / md_nvt / conv_check / resume) → checkbox.
        var boolWrap = document.createElement("div");
        boolWrap.className = "mc-bool-wrap";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = !!curVal;
        cb.addEventListener("change", function() {
          getLevelState(lvDef.level_id)[fieldName] = cb.checked;
          scheduleValidate();
        });
        boolWrap.appendChild(cb);
        fDiv.appendChild(boolWrap);
      } else {
        var inp = document.createElement("input");
        inp.type = "text";
        if (curVal !== null && typeof curVal === "object") {
          inp.value = JSON.stringify(curVal);
          inp.title = inp.value;
        } else {
          inp.value = curVal;
        }
        inp.addEventListener("change", function() {
          getLevelState(lvDef.level_id)[fieldName] = inp.value;
          scheduleValidate();
        });
        fDiv.appendChild(inp);
      }
      var fieldHelpKey = currentLang === "zh-CN" ? "help_zh" : "help";
      var fieldHelp = fieldFdLbl[fieldHelpKey] || fieldFdLbl.help;
      if (fieldHelp) {
        // Use ⓘ tooltip instead of always-visible help text
        var helpTip = document.createElement("span");
        helpTip.className = "mc-info-tip";
        helpTip.textContent = "\u24d8";
        helpTip.title = fieldHelp;
        var labelEl = fDiv.querySelector("label");
        if (labelEl) { labelEl.appendChild(helpTip); }
        else {
          var helpEl = document.createElement("div");
          helpEl.className = "mc-field-help";
          helpEl.textContent = fieldHelp;
          fDiv.appendChild(helpEl);
        }
      }
      dest.appendChild(fDiv);
    }

    // ── Electronic-state module renderer ───────────────────────────────
    // Compact preset selector + summary for type:"module" / renderer:"electronic_state".
    // Preset labels mirror ELECTRONIC_STATE_PRESETS keys in catalog.py.
    var _ES_PRESET_LABELS = {
      auto_ground_state: { label: "Automatic", label_zh: "自动" },
      closed_shell_singlet: { label: "Closed-shell singlet", label_zh: "闭壳层单重态" },
      unrestricted_doublet: { label: "Unrestricted doublet", label_zh: "非限制性二重态" },
      unrestricted_triplet: { label: "Unrestricted triplet", label_zh: "非限制性三重态" },
      bs_singlet_guessmix: { label: "BS singlet (GuessMix)", label_zh: "破缺对称单重态 (GuessMix)" },
      bs_singlet_flipspin: { label: "BS singlet (FlipSpin)", label_zh: "破缺对称单重态 (FlipSpin)" },
      singlet_triplet_pair: { label: "Singlet + Triplet pair", label_zh: "单重态 + 三重态对" },
      bs_singlet_triplet_pair: { label: "BS singlet + Triplet pair", label_zh: "破缺对称单重态 + 三重态对" },
      spin_ladder_1_3_5: { label: "Spin ladder 1/3/5", label_zh: "自旋阶梯 1/3/5" },
    };

    function _esDetectPreset(curVal, presets) {
      if (!curVal || typeof curVal !== "object") return presets[0] || "auto_ground_state";
      if (curVal.preset_id && presets.indexOf(curVal.preset_id) >= 0) return curVal.preset_id;
      if (curVal.mode === "automatic") return "auto_ground_state";
      // Heuristic: match execution_mode + state count against known presets.
      var em = curVal.execution_mode;
      var sc = (curVal.states && curVal.states.length) || 0;
      if (em === "single" && sc === 1) {
        var sm = (curVal.states[0] && curVal.states[0].spin_mode) || "";
        if (sm === "restricted") return "closed_shell_singlet";
        if (sm === "unrestricted") {
          var mult = (curVal.states[0] && curVal.states[0].target_multiplicity) || 0;
          return mult === 3 ? "unrestricted_triplet" : "unrestricted_doublet";
        }
        if (sm === "broken_symmetry") {
          var guess = (curVal.states[0].guess && curVal.states[0].guess.strategy) || "";
          return guess === "flipspin" ? "bs_singlet_flipspin" : "bs_singlet_guessmix";
        }
      }
      if (em === "state_sweep" && sc === 2) {
        var firstSm = (curVal.states[0] && curVal.states[0].spin_mode) || "";
        return firstSm === "restricted" ? "singlet_triplet_pair" : "bs_singlet_triplet_pair";
      }
      if (em === "state_sweep" && sc === 3) return "spin_ladder_1_3_5";
      return presets[0] || "auto_ground_state";
    }

    var _ES_PRESET_DETAILS = {
      auto_ground_state: {
        zh: "\u5e38\u89c4\u8ba1\u7b97\uff1a\u6309\u5df2\u786e\u8ba4\u7684\u7535\u8377\u548c\u591a\u91cd\u5ea6\u9009\u62e9\u9ed8\u8ba4\u6ce2\u51fd\u6570\u5904\u7406\u65b9\u5f0f\uff08\u4e0d\u4f1a\u81ea\u52a8\u641c\u7d22\u57fa\u6001\uff09",
        en: "Regular: default wavefunction treatment from the confirmed charge and multiplicity (no automatic ground-state search)",
      },
      closed_shell_singlet: {
        zh: "\u95ed\u58f3\u5c42\u5355\u91cd\u6001\uff1a\u591a\u91cd\u5ea6 1\uff0c\u9650\u5236\u6027\u5904\u7406",
        en: "Closed-shell singlet: multiplicity 1, restricted",
      },
      unrestricted_doublet: {
        zh: "\u975e\u9650\u5236\u6027\u4e8c\u91cd\u6001\uff1a\u591a\u91cd\u5ea6 2",
        en: "Unrestricted doublet: multiplicity 2",
      },
      unrestricted_triplet: {
        zh: "\u975e\u9650\u5236\u6027\u4e09\u91cd\u6001\uff1a\u591a\u91cd\u5ea6 3",
        en: "Unrestricted triplet: multiplicity 3",
      },
      bs_singlet_guessmix: {
        zh: "\u7834\u7f3a\u5bf9\u79f0\u5355\u91cd\u6001\uff1aGuessMix \u521d\u731c\uff08\u9700\u6838\u5bf9\u53c2\u8003\u6001\u4e0e\u81ea\u65cb\u5e03\u5c45\uff09",
        en: "Broken-symmetry singlet: GuessMix initial guess (check reference state and spin population)",
      },
      bs_singlet_flipspin: {
        zh: "\u7834\u7f3a\u5bf9\u79f0\u5355\u91cd\u6001\uff1aFlipSpin \u521d\u731c\uff08\u9700\u6838\u5bf9\u53c2\u8003\u6001\u4e0e\u81ea\u65cb\u5e03\u5c45\uff09",
        en: "Broken-symmetry singlet: FlipSpin initial guess (check reference state and spin population)",
      },
      singlet_triplet_pair: {
        zh: "\u6bd4\u8f83\u591a\u4e2a\u81ea\u65cb\u6001\uff1a\u5c06\u751f\u6210 2 \u4e2a\u8ba1\u7b97\u5206\u652f\uff08\u5355\u91cd\u6001 + \u4e09\u91cd\u6001\uff09",
        en: "Spin comparison: 2 branches will be generated (singlet + triplet)",
      },
      bs_singlet_triplet_pair: {
        zh: "\u6bd4\u8f83\u591a\u4e2a\u81ea\u65cb\u6001\uff1a\u5c06\u751f\u6210 2 \u4e2a\u5206\u652f\uff08\u7834\u7f3a\u5bf9\u79f0\u5355\u91cd\u6001 + \u4e09\u91cd\u6001\uff09",
        en: "Spin comparison: 2 branches (BS singlet + triplet)",
      },
      spin_ladder_1_3_5: {
        zh: "\u81ea\u65cb\u9636\u68af\uff1a\u5c06\u751f\u6210 3 \u4e2a\u8ba1\u7b97\u5206\u652f\uff08\u591a\u91cd\u5ea6 1/3/5\uff09",
        en: "Spin ladder: 3 branches will be generated (multiplicity 1/3/5)",
      },
    };

    function _esSummaryText(curVal) {
      if (!curVal || typeof curVal !== "object") return "";
      var zh = currentLang === "zh-CN";
      if (curVal.preset_id) {
        var detail = _ES_PRESET_DETAILS[curVal.preset_id];
        if (detail) return zh ? detail.zh : detail.en;
        var meta = _ES_PRESET_LABELS[curVal.preset_id];
        return (meta && (zh ? meta.label_zh : meta.label)) || curVal.preset_id;
      }
      if (curVal.mode === "automatic" || !curVal.states || !curVal.states.length) {
        return zh
          ? "\u5e38\u89c4\u8ba1\u7b97\uff1a\u6309\u5df2\u786e\u8ba4\u7684\u7535\u8377\u548c\u591a\u91cd\u5ea6\u9009\u62e9\u9ed8\u8ba4\u6ce2\u51fd\u6570\u5904\u7406\u65b9\u5f0f"
          : "Regular: default wavefunction treatment from the confirmed charge and multiplicity";
      }
      var parts = [];
      for (var i = 0; i < curVal.states.length; i++) {
        var st = curVal.states[i];
        if (!st) continue;
        var lbl = st.label || ("S" + (i + 1));
        var mult = st.target_multiplicity != null ? ("(2S+1=" + st.target_multiplicity + ")") : "";
        var sm = st.spin_mode || "";
        var tag = sm === "restricted" ? "R" : sm === "broken_symmetry" ? "BS" : sm === "unrestricted" ? "U" : sm;
        parts.push(lbl + (mult ? " " + mult : "") + (tag ? " [" + tag + "]" : ""));
      }
      var modeLabel = curVal.execution_mode === "state_sweep"
        ? (zh ? "\u81ea\u65cb\u6bd4\u8f83\uff0c\u5171 " + curVal.states.length + " \u4e2a\u5206\u652f: " : "sweep, " + curVal.states.length + " branches: ")
        : (zh ? "\u5355\u4e00\u7535\u5b50\u6001: " : "single: ");
      return modeLabel + parts.join(", ");
    }

    function buildElectronicStateFieldRow(fieldName, fDiv, fieldFd, curVal) {
      fDiv.className = "mc-field mc-es-row";
      var presets = fieldFd.presets || ["auto_ground_state"];
      var detected = _esDetectPreset(curVal, presets);

      var sel = document.createElement("select");
      var lblKey = currentLang === "zh-CN" ? "label_zh" : "label";
      presets.forEach(function(pid) {
        var opt = document.createElement("option");
        opt.value = pid;
        var meta = _ES_PRESET_LABELS[pid];
        opt.textContent = (meta && meta[lblKey]) || pid.replace(/_/g, " ");
        if (pid === detected) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener("change", function() {
        getLevelState(lvDef.level_id)[fieldName] = { preset_id: sel.value };
        // Re-render summary
        var nextVal = getLevelState(lvDef.level_id)[fieldName];
        summaryDiv.textContent = _esSummaryText(nextVal);
        scheduleValidate();
      });
      fDiv.appendChild(sel);

      var summaryDiv = document.createElement("div");
      summaryDiv.className = "mc-es-summary";
      summaryDiv.textContent = _esSummaryText(curVal);
      fDiv.appendChild(summaryDiv);
    }

    // ── Hessian tri-state control (plan §13) ────────────────────────────
    // Renders an Auto / Initial-only / Custom segmented control plus a
    // backend-driven preview that classifies each wizard structure.
    // Preview requests are debounced and only the latest is kept.
    var hessianPreviewTimer = null;
    var hessianPreviewToken = 0;

    function buildHessianFieldRow(dest) {
      var fDiv = document.createElement("div");
      fDiv.className = "mc-field mc-field-hessian";
      var zh = currentLang === "zh-CN";

      var st = getLevelState(lvDef.level_id);
      // Canonical field is opt_recalc_hess (the field serialization and the
      // backend both read). Older sessions wrote recalc_hess — migrate once,
      // then drop the legacy key so only one source of truth remains.
      if (st.opt_recalc_hess === undefined && st.recalc_hess !== undefined) {
        st.opt_recalc_hess = st.recalc_hess;
      }
      delete st.recalc_hess;
      // Normalise persisted value into one of: "auto" / 0 / <int>.
      var rawVal = st.opt_recalc_hess;
      var mode = "auto";
      var customVal = 10;
      if (rawVal === 0) { mode = "off"; }
      else if (rawVal !== undefined && rawVal !== "" && rawVal !== null && rawVal !== "auto") {
        var parsed = parseInt(rawVal, 10);
        if (!isNaN(parsed) && parsed > 0) { mode = "custom"; customVal = parsed; }
      }
      st.opt_recalc_hess = (mode === "auto") ? "auto" : (mode === "off" ? 0 : customVal);

      // Combined horizontal row: initial Hessian select + recalc segmented
      var hessianRow = document.createElement("div");
      hessianRow.className = "mc-hessian-row";
      var isBatchLevel = lvDef.level_id === "batch";

      // Initial Hessian select (batch only — non-batch keeps pure recalc control)
      if (isBatchLevel) {
        var initDiv = document.createElement("div");
        initDiv.className = "mc-field";
        var initLbl = document.createElement("label");
        initLbl.textContent = zh ? "\u521d\u59cb\u7b56\u7565" : "Initial Strategy";
        initDiv.appendChild(initLbl);
        var initSel = document.createElement("select");
        var initOpts = [
          { value: "", text: zh ? "\u81ea\u52a8" : "Auto" },
          { value: "model", text: zh ? "\u6a21\u578b Hessian" : "Model Hessian" },
          { value: "calculate", text: zh ? "\u542f\u52a8\u65f6\u8ba1\u7b97" : "Calculate at start" },
        ];
        initOpts.forEach(function(io) {
          var o = document.createElement("option");
          o.value = io.value;
          o.textContent = io.text;
          if ((st.opt_initial_hessian || "") === io.value) o.selected = true;
          initSel.appendChild(o);
        });
        initSel.addEventListener("change", function() {
          st.opt_initial_hessian = initSel.value || null;
          updateHessianEffective();
          if (typeof _onAdvancedFieldChange === "function") _onAdvancedFieldChange();
          scheduleValidate();
        });
        initDiv.appendChild(initSel);
        hessianRow.appendChild(initDiv);
      }

      // Recalc segmented
      var recalcDiv = document.createElement("div");
      recalcDiv.className = "mc-field";
      var recalcLbl = document.createElement("label");
      recalcLbl.textContent = zh ? "\u91cd\u7b97\u7b56\u7565" : "Recalc Strategy";
      recalcDiv.appendChild(recalcLbl);

      var segWrap = document.createElement("div");
      segWrap.className = "mc-hessian-seg";

      function makeSeg(segMode, label) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "mc-hessian-seg-btn" + (mode === segMode ? " active" : "");
        btn.textContent = label;
        btn.addEventListener("click", function() {
          mode = segMode;
          segWrap.querySelectorAll(".mc-hessian-seg-btn").forEach(function(b) { b.classList.remove("active"); });
          btn.classList.add("active");
          if (mode === "auto") { st.opt_recalc_hess = "auto"; customRow.style.display = "none"; }
          else if (mode === "off") { st.opt_recalc_hess = 0; customRow.style.display = "none"; }
          else { st.opt_recalc_hess = customVal; customRow.style.display = ""; }
          updateHessianEffective();
          if (typeof _onAdvancedFieldChange === "function") _onAdvancedFieldChange();
          scheduleHessianPreview();
          scheduleValidate();
        });
        return btn;
      }

      segWrap.appendChild(makeSeg("auto", zh ? "\u81ea\u52a8" : "Auto"));
      segWrap.appendChild(makeSeg("off", zh ? "\u4e0d\u91cd\u7b97" : "No recalc"));
      segWrap.appendChild(makeSeg("custom", zh ? "\u6bcf N \u4e2a\u4f18\u5316\u5faa\u73af" : "Every N cycles"));
      recalcDiv.appendChild(segWrap);

      // Custom numeric input (only visible in Custom mode).
      var customRow = document.createElement("div");
      customRow.className = "mc-hessian-custom";
      customRow.style.display = (mode === "custom") ? "" : "none";
      var numInp = document.createElement("input");
      numInp.type = "number";
      numInp.min = "1";
      numInp.max = "1000";
      numInp.step = "1";
      numInp.value = String(customVal);
      numInp.addEventListener("change", function() {
        var v = parseInt(numInp.value, 10);
        if (isNaN(v) || v < 1) v = 1;
        if (v > 1000) v = 1000;
        numInp.value = String(v);
        customVal = v;
        if (mode === "custom") { st.opt_recalc_hess = v; updateHessianEffective(); if (typeof _onAdvancedFieldChange === "function") _onAdvancedFieldChange(); scheduleHessianPreview(); scheduleValidate(); }
      });
      var unitLbl = document.createElement("span");
      unitLbl.className = "unit";
      unitLbl.textContent = zh ? "\u4e2a\u4f18\u5316\u5faa\u73af" : "optimization cycles";
      customRow.appendChild(numInp);
      customRow.appendChild(unitLbl);
      recalcDiv.appendChild(customRow);
      hessianRow.appendChild(recalcDiv);
      fDiv.appendChild(hessianRow);

      // Effective strategy line
      var effectiveLine = document.createElement("div");
      effectiveLine.className = "mc-hessian-effective";
      fDiv.appendChild(effectiveLine);

      function updateHessianEffective() {
        var initVal = st.opt_initial_hessian || "";
        var recalcVal = st.opt_recalc_hess;
        var parts = [];
        if (initVal === "calculate") {
          parts.push(zh ? "\u542f\u52a8\u65f6\u8ba1\u7b97" : "Calculate at start");
        } else if (initVal === "model") {
          parts.push(zh ? "\u6a21\u578b Hessian" : "Model Hessian");
        }
        if (recalcVal === "auto") {
          parts.push(zh ? "\u81ea\u52a8\u6309\u5143\u7d20\u5224\u5b9a" : "Auto by element");
        } else if (recalcVal === 0 || recalcVal === "0") {
          parts.push(zh ? "\u4e0d\u91cd\u7b97" : "No recalculation");
        } else {
          parts.push(zh ? "\u6bcf " + recalcVal + " \u4e2a\u4f18\u5316\u5faa\u73af\u91cd\u7b97\u4e00\u6b21" : "Recalc every " + recalcVal + " optimization cycles");
        }
        effectiveLine.textContent = (zh ? "\u5f53\u524d\u6709\u6548\u7b56\u7565\uff1a" : "Current effective strategy: ") + (parts.join(zh ? "\uff0c" : ", ") || (zh ? "\u81ea\u52a8" : "Auto"));
      }
      updateHessianEffective();

      // Preview container
      var previewBox = document.createElement("div");
      previewBox.className = "mc-hessian-preview";
      previewBox.style.display = "none";
      fDiv.appendChild(previewBox);

      // Hint for no-structures case
      var hint = document.createElement("div");
      hint.className = "mc-hessian-hint";
      hint.style.display = "none";
      fDiv.appendChild(hint);

      function setPreviewVisible(visible) {
        previewBox.style.display = visible ? "" : "none";
        hint.style.display = visible ? "" : "none";
      }

      function renderPreviewLoading() {
        setPreviewVisible(mode === "auto");
        if (mode !== "auto") return;
        previewBox.innerHTML = "";
        var row = document.createElement("div");
        row.className = "mc-hessian-preview-row";
        row.textContent = zh ? "\u6b63\u5728\u89e3\u6790\u2026" : "Parsing\u2026";
        previewBox.appendChild(row);
      }

      function renderPreviewRows(results, summary) {
        setPreviewVisible(true);
        previewBox.innerHTML = "";
        var sumEl = document.createElement("div");
        sumEl.className = "mc-hessian-preview-summary";
        var enabledN = (summary && summary.enabled) || 0;
        var totalN = (summary && summary.total) || 0;
        sumEl.textContent = (zh ? "{total} \u4e2a\u7ed3\u6784\uff1a{enabled} \u4e2a\u5c06\u91cd\u7b97 Hessian" : "{total} structures: {enabled} will recalc Hessian")
          .replace("{enabled}", enabledN).replace("{total}", totalN);
        previewBox.appendChild(sumEl);
        (results || []).forEach(function(r) {
          var row = document.createElement("div");
          row.className = "mc-hessian-preview-row";
          var dot = document.createElement("span");
          dot.className = "dot " + (r.enabled ? "on" : "off");
          var name = document.createElement("span");
          name.className = "name";
          name.textContent = r.name || "?";
          var val = document.createElement("span");
          val.className = "val";
          val.textContent = r.enabled
            ? (zh ? "\u6bcf {n} \u6b65" : "Every {n} steps").replace("{n}", r.interval)
            : (zh ? "\u4e0d\u8ba1\u7b97" : "No calc");
          row.appendChild(dot); row.appendChild(name); row.appendChild(val);
          if (r.heavy_elements && r.heavy_elements.length) {
            var elems = document.createElement("span");
            elems.className = "elems";
            elems.textContent = "(" + r.heavy_elements.join(", ") + ")";
            row.appendChild(elems);
          }
          previewBox.appendChild(row);
        });
      }

      function scheduleHessianPreview() {
        if (hessianPreviewTimer) clearTimeout(hessianPreviewTimer);
        renderPreviewLoading();
        if (mode !== "auto") return;
        hessianPreviewTimer = setTimeout(function() {
          hessianPreviewTimer = null;
          fetchHessianPreview();
        }, 300);
      }

      function fetchHessianPreview() {
        if (mode !== "auto") return;
        var myToken = ++hessianPreviewToken;
        var structures = (wizardStructures || []).map(function(s) {
          var out = { name: s.name || "" };
          if (Array.isArray(s.symbols) && s.symbols.length) out.symbols = s.symbols;
          else if (s.formula) out.formula = s.formula;
          return out;
        });
        if (!structures.length) {
          setPreviewVisible(true);
          previewBox.innerHTML = "";
          var hintRow = document.createElement("div");
          hintRow.className = "mc-hessian-preview-row";
          hintRow.textContent = zh ? "\u6dfb\u52a0\u7ed3\u6784\u540e\u53ef\u9884\u89c8\u81ea\u52a8 Hessian \u7b56\u7565\uff1b\u5f53\u524d\u8bbe\u7f6e\u4ecd\u53ef\u4fdd\u5b58\u3002" : "Add structures to preview auto Hessian strategy; current settings are still saved.";
          previewBox.appendChild(hintRow);
          return;
        }
        var body = {
          schema_id: wizardState.method.schema_id || "",
          level_id: lvDef.level_id,
          recalc_hess: st.opt_recalc_hess,
          structures: structures,
        };
        // R3(b): endpoint adapter — URL/body/timeout and the errors[]
        // .message-join error text are frozen inside
        // ACPClient.wizardEndpoints.hessianPreview (the legacy fetch had no
        // timeout and never surfaced the FastAPI detail).
        window.ACPClient.wizardEndpoints.hessianPreview(body).then(function(data) {
          if (myToken !== hessianPreviewToken) return;
          renderPreviewRows(data.results || [], data.summary || {});
        }).catch(function(err) {
          if (myToken !== hessianPreviewToken) return;
          previewBox.innerHTML = "";
          var errRow = document.createElement("div");
          errRow.className = "mc-hessian-preview-row";
          errRow.textContent = (zh ? "\u9884\u89c8\u5931\u8d25" : "Preview failed") + ": " + err.message;
          previewBox.appendChild(errRow);
        });
      }

      scheduleHessianPreview();
      dest.appendChild(fDiv);
    }

    function getLevelState(lid) {
      if (!wizardState.method.stages[lid]) wizardState.method.stages[lid] = {};
      if (!wizardState.method.stages[lid].engine) wizardState.method.stages[lid].engine = lvDef.allowed_engines[0];
      return wizardState.method.stages[lid];
    }

    // Engine selector for single-engine levels
    if (lvDef.allowed_engines.length === 1 && !getLevelState(lvDef.level_id).engine) {
      getLevelState(lvDef.level_id).engine = lvDef.allowed_engines[0];
    }

    // ── ORCA availability warning (Item 3) ──
    // For scan_optimizer level: when a DFT method is selected and backendsCache
    // shows orca unavailable, show a non-blocking warning banner.
    if (lvDef.level_id === "scan_optimizer") {
      var _soMethod = (getLevelState(lvDef.level_id) || {}).scan_optimizer_method || "";
      // ── catalog family gate (T23d) ──
      // Methods whose family offers a DFT basis layer (conventional DFT /
      // composite) run on the ORCA stack and need the ORCA-availability
      // warning; xTB-family methods (locked/empty basis offer) run on the
      // standalone xTB path. Payload-derived via the T15 family gate — the
      // retired hardcoded _xtbMethods list must never come back
      // (AGENTS anti-pattern #27).
      var _isDftMethod = _soMethod && !isFieldFamilyLocked("basis", _soMethod);
      if (_isDftMethod) {
        var _orcaAvailable = false;
        if (typeof backendsCache !== "undefined" && backendsCache.length) {
          for (var _bi = 0; _bi < backendsCache.length; _bi++) {
            if (String(backendsCache[_bi].name || "").toLowerCase() === "orca" && backendsCache[_bi].available) {
              _orcaAvailable = true;
              break;
            }
          }
        }
        if (!_orcaAvailable) {
          var _orcaWarn = document.createElement("div");
          _orcaWarn.className = "mc-orca-warning";
          _orcaWarn.style.cssText = "background:rgba(255,160,40,0.12);border:1px solid rgba(255,160,40,0.3);border-radius:6px;padding:8px 12px;margin-bottom:8px;font-size:12px;color:#ffb347;";
          _orcaWarn.textContent = t("modal.orca_unavailable_dft_scan") || "当前节点未检测到 ORCA，DFT 扫描将无法执行";
          bodyDiv.appendChild(_orcaWarn);
        }
      }
      // ── Recovery differentiation note (Item 5) ──
      // Single-coordinate native ORCA scan does not support per-point retry.
      // Show an explanatory note and mark the state so buildFieldRow can disable.
      var _coordCount = 1;
      if (pesSelectionState.selectionKind === "double_bond_scan") _coordCount = 2;
      var _soState = getLevelState("scan_optimizer");
      _soState._native_single_coord = (_coordCount === 1);
      if (_coordCount === 1) {
        var _nativeNote = document.createElement("div");
        _nativeNote.className = "mc-native-scan-note";
        _nativeNote.style.cssText = "font-size:11px;color:var(--text-muted, #888);margin-bottom:6px;padding:4px 0;";
        _nativeNote.textContent = t("modal.native_scan_no_per_point_retry") || "单坐标原生 ORCA 扫描不支持逐点重试，仅整跑重试生效";
        bodyDiv.appendChild(_nativeNote);
      }
    }

    var coreDiv = document.createElement("div");
    coreDiv.className = "method-fields";
    coreFields.forEach(function(fn) { buildFieldRow(fn, coreDiv); });
    if (coreFields.length) bodyDiv.appendChild(coreDiv);

    if (advancedFields.length || lvDef.level_id === "batch") {
      var advWrap = document.createElement("div");
      advWrap.className = "mc-level-advanced";
      advWrap.style.position = "relative";
      var advToggle = document.createElement("div");
      advToggle.className = "mc-level-advanced-toggle";
      var pesAdvancedVisible = wizardState.workflow.id === "PESsearch";
      var batchAdvancedVisible = lvDef.level_id === "batch";
        var advDefaultExpanded = pesAdvancedVisible || batchAdvancedVisible;
        advToggle.textContent = (advDefaultExpanded ? "- " : "+ ") + (t("modal.advanced") || "\u9ad8\u7ea7");
        // Do not add a second "advanced controls" shell around the batch
        // role form: role tabs are the top-level navigation for this card.
        if (lvDef.level_id !== "batch") advWrap.appendChild(advToggle);

      var advDiv = document.createElement("div");
      advDiv.className = "mc-level-advanced-fields";
      advDiv.style.display = advDefaultExpanded ? "grid" : "none";

      // ── BatchOptimize: INT/TS role toggle + per-role config form ──
      if (lvDef.level_id === "batch") {
        // Static base shared with ACPJobEditor; method-dependent keys are
        // patched just below from the current level state.
        var _BATCH_ROLE_DEFAULTS = batchRoleStaticDefaults();
        var batchMethodDefault = stateData.functional || stateData.method || getFieldDefault("functional", engine) || "r2SCAN-3c";
        var batchBasisDefault = stateData.basis;
        if (batchBasisDefault === undefined || batchBasisDefault === null) {
          batchBasisDefault = getFieldDefault("basis", engine, batchMethodDefault) || "";
        }
        var batchSpMethodDefault = stateData.single_point_method || stateData.sp_method || "wB97M-V";
        var batchSpBasisDefault = stateData.single_point_basis || stateData.sp_basis;
        if (batchSpBasisDefault === undefined || batchSpBasisDefault === null) {
          batchSpBasisDefault = getFieldDefault("basis", engine, batchSpMethodDefault) || "def2-TZVPP";
        }
        ["int", "ts"].forEach(function(role) {
          var roleDefaults = _BATCH_ROLE_DEFAULTS[role];
          roleDefaults.method = batchMethodDefault;
          roleDefaults.basis = batchBasisDefault;
          roleDefaults.sp_method = batchSpMethodDefault;
          roleDefaults.sp_basis = batchSpBasisDefault;
          roleDefaults.dispersion = getFieldDefault("dispersion", engine, batchMethodDefault, batchBasisDefault) || "none";
          roleDefaults.aux_j_basis = getFieldDefault("aux_j_basis", engine, batchMethodDefault, batchBasisDefault) || "";
        });

        // Ensure batch_roles draft exists with full defaults
        if (!wizardState.method.stages.batch_roles) {
          wizardState.method.stages.batch_roles = {
            int: JSON.parse(JSON.stringify(_BATCH_ROLE_DEFAULTS.int)),
            ts: JSON.parse(JSON.stringify(_BATCH_ROLE_DEFAULTS.ts)),
          };
        }
        var _batchRoles = wizardState.method.stages.batch_roles;
        // Existing drafts created before role-specific method support may be
        // sparse.  Fill only missing keys; never overwrite either role.
        ["int", "ts"].forEach(function(role) {
          _batchRoles[role] = _batchRoles[role] || {};
          Object.keys(_BATCH_ROLE_DEFAULTS[role]).forEach(function(key) {
            if (_batchRoles[role][key] === undefined) _batchRoles[role][key] = _BATCH_ROLE_DEFAULTS[role][key];
          });
        });
        window._batchRoles = _batchRoles;
        window._BATCH_ROLE_DEFAULTS = _BATCH_ROLE_DEFAULTS;

        // Electronic state belongs to the batch level and applies to every
        // structure/role in this submission. Keep it outside the INT/TS form.
        var batchSpinField = document.createElement("div");
        var batchSpinLabel = document.createElement("label");
        batchSpinLabel.textContent = currentLang === "zh-CN" ? "电子态与自旋（全部结构）" : "Electronic state and spin (all structures)";
        batchSpinField.appendChild(batchSpinLabel);
        buildElectronicStateFieldRow(
          "electronic_state",
          batchSpinField,
          (methodCatalogCache.field_definitions || {}).electronic_state || { presets: ["auto_ground_state"] },
          getLevelState("batch").electronic_state
        );
        advDiv.appendChild(batchSpinField);
        var batchSpinNote = document.createElement("div");
        batchSpinNote.className = "muted";
        batchSpinNote.style.cssText = "font-size:11px;margin:-4px 0 12px;";
        batchSpinNote.textContent = currentLang === "zh-CN"
          ? "应用于本次提交的全部结构和计算步骤。FlipSpin 预设翻转第 1 个原子；其他原子请用 CLI 的 --spin-config 自定义。"
          : "Applies to all structures and steps. FlipSpin preset flips atom 1; use CLI --spin-config for another atom.";
        advDiv.appendChild(batchSpinNote);

        // ── Role toggle buttons with counts ──
        var _batchRoleActive = "int";
        var intCount = 0, tsCount = 0;
        if (typeof batchPreviewItems !== "undefined" && batchPreviewItems) {
          batchPreviewItems.forEach(function(item) {
            if (item.include === false) return;
            if (item.tag === "TS") tsCount++;
            else if (item.tag === "INT") intCount++;
          });
        }
        if (intCount === 0 && tsCount > 0) _batchRoleActive = "ts";
        window._batchRoleActive_ref = function() { return _batchRoleActive; };
        function _updateBatchRestoreLabel() {
          var restore = document.getElementById("mt-config-restore-default");
          if (!restore) return;
          restore.textContent = currentLang === "zh-CN"
            ? "恢复 " + _batchRoleActive.toUpperCase() + " 默认"
            : "Restore " + _batchRoleActive.toUpperCase() + " defaults";
        }
        _updateBatchRestoreLabel();

        var roleToolbar = document.createElement("div");
        roleToolbar.className = "mc-batch-role-toolbar";
        var roleBar = document.createElement("div");
        roleBar.className = "mc-batch-role-bar";
        var roleButtons = {};
        var copyRoleBtn = document.createElement("button");
        copyRoleBtn.type = "button";
        copyRoleBtn.className = "mc-copy-role-method";
        function _otherBatchRole() { return _batchRoleActive === "int" ? "ts" : "int"; }
        function _updateCopyRoleLabel() {
          var otherRole = _otherBatchRole();
          copyRoleBtn.textContent = currentLang === "zh-CN"
            ? "复制" + otherRole.toUpperCase() + "的方法"
            : "Copy " + otherRole.toUpperCase() + " method";
        }
        copyRoleBtn.addEventListener("click", function() {
          var otherRole = _otherBatchRole();
          var src = _batchRoles[otherRole] || {};
          var dst = _batchRoles[_batchRoleActive];
          if (!dst) return;
          ["method", "basis", "dispersion", "aux_j_basis", "aux_c_basis", "ri_approximation", "sp_method", "sp_basis"].forEach(function(k) {
            dst[k] = src[k] !== undefined ? JSON.parse(JSON.stringify(src[k])) : _BATCH_ROLE_DEFAULTS[_batchRoleActive][k];
          });
          _renderBatchRoleForm();
          _fetchBatchConfigPreview();
        });
        _updateCopyRoleLabel();
        function _makeRoleBtn(role, label, count) {
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "mc-batch-role-btn" + (role === _batchRoleActive ? " active" : "");
          btn.setAttribute("data-role", role);
          var countSpan = document.createElement("span");
          countSpan.className = "mc-batch-role-count";
          countSpan.textContent = batchPreviewItems.length ? "\u00b7 " + count : "";
          btn.appendChild(document.createTextNode(label + " "));
          btn.appendChild(countSpan);
          btn.addEventListener("click", function() {
            if (role === _batchRoleActive) return;
            _batchRoleActive = role;
            roleButtons.int.classList.toggle("active", role === "int");
            roleButtons.ts.classList.toggle("active", role === "ts");
            _updateBatchRestoreLabel();
            _updateCopyRoleLabel();
            _renderBatchRoleForm();
          });
          return btn;
        }
        roleButtons.int = _makeRoleBtn("int", currentLang === "zh-CN" ? "INT \u666e\u901a\u9a7b\u70b9" : "INT Intermediate", intCount);
        roleButtons.ts = _makeRoleBtn("ts", currentLang === "zh-CN" ? "TS \u8fc7\u6e21\u6001" : "TS Transition State", tsCount);
        roleBar.appendChild(roleButtons.int);
        roleBar.appendChild(roleButtons.ts);
        roleToolbar.appendChild(roleBar);
        roleToolbar.appendChild(copyRoleBtn);
        advDiv.appendChild(roleToolbar);

        // ── Role form container ──
        var roleFormDiv = document.createElement("div");
        roleFormDiv.className = "mc-batch-role-form";
        advDiv.appendChild(roleFormDiv);

        function _makeBatchDisclosure(labelZh, labelEn, extraClass) {
          var wrap = document.createElement("div");
          wrap.className = "mc-adv-group mc-batch-disclosure" + (extraClass ? " " + extraClass : "");
          var header = document.createElement("div");
          header.className = "mc-adv-group-header";
          var chevron = document.createElement("span");
          chevron.className = "mc-adv-group-chevron";
          chevron.innerHTML = '<svg viewBox="0 0 16 16" width="10" height="10" stroke="currentColor" fill="none" stroke-width="2"><path d="M6 4l4 4-4 4"/></svg>';
          header.appendChild(chevron);
          header.appendChild(document.createTextNode(currentLang === "zh-CN" ? labelZh : labelEn));
          var fields = document.createElement("div");
          fields.className = "mc-adv-group-fields mc-batch-detail-fields collapsed";
          header.addEventListener("click", function() {
            var opening = fields.classList.contains("collapsed");
            fields.classList.toggle("collapsed", !opening);
            chevron.classList.toggle("open", opening);
          });
          wrap.appendChild(header);
          wrap.appendChild(fields);
          advDiv.appendChild(wrap);
          return { wrap: wrap, header: header, fields: fields, chevron: chevron };
        }

        var scfDetailSection = _makeBatchDisclosure("SCF 详细设置", "SCF Details", "mc-scf-detail-section");
        var thermoSection = batchHasStep("thermochemistry")
          ? _makeBatchDisclosure("热化学", "Thermochemistry", "mc-thermo-section") : null;
        var retrySection = _makeBatchDisclosure("失败重试", "Failure Retry", "mc-retry-section");

        // The endpoint currently exposes the same normalised options consumed
        // by submission, not a structure-specific ORCA file.  Name it
        // accurately until a real input-generator endpoint is available.
        var previewSection = _makeBatchDisclosure("参数摘要", "Parameter Summary", "mc-summary-section");
        var previewWrap = previewSection.wrap;
        var previewHdr = previewSection.header;
        var previewChevron = previewSection.chevron;
        var previewFields = previewSection.fields;

        // Server preview wiring
        var _previewDebounceTimer = null;
        var _previewAbortCtrl = null;
        var _PREVIEW_DEBOUNCE_MS = 400;
        var _previewGeneration = 0;

        function _fetchBatchConfigPreview() {
          if (_previewDebounceTimer) { clearTimeout(_previewDebounceTimer); _previewDebounceTimer = null; }
          previewFields.setAttribute("data-stale", "true");
          _previewDebounceTimer = setTimeout(function() {
            _previewDebounceTimer = null;
            if (_previewAbortCtrl) { try { _previewAbortCtrl.abort(); } catch(_e) {} }
            var abortCtrl = null;
            if (typeof AbortController !== "undefined") { abortCtrl = new AbortController(); }
            _previewAbortCtrl = abortCtrl;
            var myGen = ++_previewGeneration;
            var tmpPayload = {};
            try { applyBatchOptimizeMethodFields(tmpPayload); } catch(_e) { return; }
            // R3(b): endpoint adapter freezes URL/body/timeout and the
            // "HTTP <status>" error text; the AbortController orchestration
            // (stale-preview abort) stays here, signal forwarded as-is.
            window.ACPClient.wizardEndpoints
              .batchConfigPreview({ method: tmpPayload }, abortCtrl ? { signal: abortCtrl.signal } : undefined)
              .then(function(data) {
                if (myGen !== _previewGeneration) return;
                if (data.schema !== "batch_optimize_preview_v1") return;
                previewFields.removeAttribute("data-stale");
                _renderServerPreview(data);
              })
              .catch(function(err) {
                if (err && err.name === "AbortError") return;
                if (myGen !== _previewGeneration) return;
                _renderPreviewFailure((err && err.message) || String(err));
              });
          }, _PREVIEW_DEBOUNCE_MS);
        }

        function _renderServerPreview(data) {
          previewFields.innerHTML = "";
          var zh = currentLang === "zh-CN";
          var role = _batchRoleActive;
          var roleData = (data.roles || {})[role];
          if (!roleData) { _renderLocalSummary(); return; }
          var eff = roleData.effective || {};
          var effLabels = {
            max_cycles: zh ? "\u4f18\u5316\u6700\u5927\u5faa\u73af" : "opt max cycles",
            opt_level: zh ? "\u4f18\u5316\u6536\u655b" : "opt level",
            opt_trust_radius: zh ? "\u4fe1\u8d56\u534a\u5f84" : "trust",
            opt_initial_hessian: zh ? "\u521d\u59cb Hessian" : "initial Hessian",
            opt_recalc_hess: zh ? "Hessian \u91cd\u7b97" : "Hessian recalc",
            scf_maxiter: zh ? "SCF \u6700\u5927\u5faa\u73af" : "SCF max cycles",
            scf_convergence: zh ? "SCF \u6536\u655b" : "SCF conv",
            scf_strategy: zh ? "SCF \u7b56\u7565" : "SCF strategy",
          };
          var bits = [];
          Object.keys(effLabels).forEach(function(k) {
            if (eff[k] === undefined || eff[k] === null) return;
            bits.push(effLabels[k] + " " + String(eff[k]));
          });
          if (bits.length) {
            var line = document.createElement("div");
            line.textContent = bits.join(zh ? "\uff0c" : ", ");
            previewFields.appendChild(line);
          }
          var orcaKw = ((data.orca_summary || {})[role]) || [];
          if (orcaKw.length) {
            var pre = document.createElement("pre");
            pre.className = "mc-preview-orca-kw";
            pre.textContent = role.toUpperCase() + ": ! " + orcaKw.join(" ");
            previewFields.appendChild(pre);
          }
        }

        function _renderPreviewFailure(errMsg) {
          previewFields.innerHTML = "";
          var zh = currentLang === "zh-CN";
          var failDiv = document.createElement("div");
          failDiv.className = "mc-preview-failure";
          failDiv.textContent = (zh ? "\u540e\u7aef\u9884\u89c8\u5931\u8d25\uff1a" : "Backend preview failed: ") + errMsg;
          previewFields.appendChild(failDiv);
          var noteDiv = document.createElement("div");
          noteDiv.className = "mc-preview-unverified";
          noteDiv.textContent = zh
            ? "\u4ee5\u4e0b\u4e3a\u672c\u5730\u63a8\u65ad\uff0c\u672a\u7ecf\u540e\u7aef\u9a8c\u8bc1\u3002"
            : "Local inference below is NOT backend-verified.";
          previewFields.appendChild(noteDiv);
          _renderLocalSummary(false);
        }

        function _renderLocalSummary() {
          var zh = currentLang === "zh-CN";
          var role = _batchRoleActive;
          var rd = _batchRoles[role] || {};
          var convKey = String(rd.opt_convergence || "tight").toLowerCase();
          var convLabel = { "tight": "TightOpt", "verytight": "VeryTightOpt", "loose": "LooseOpt", "normal": "NormalOpt" }[convKey] || convKey;
          var scfConvKey = String(rd.scf_convergence || "tight").toLowerCase();
          var scfLabel = { "tight": "TightSCF", "verytight": "VeryTightSCF", "normal": "NormalSCF" }[scfConvKey] || scfConvKey;
          var maxIter = rd.opt_max_iter != null ? String(rd.opt_max_iter) : (zh ? "\u81ea\u52a8" : "auto");
          var trustStr = rd.opt_trust_radius != null ? ("Trust " + rd.opt_trust_radius) : (zh ? "Trust \u81ea\u52a8" : "Trust auto");
          var line1 = (zh ? "\u4f18\u5316\uff1a" : "Opt: ") + convLabel
            + (zh ? "\uff0c\u6700\u5927\u5faa\u73af " : ", max cycles ") + maxIter
            + ", " + trustStr;
          var line2 = (zh ? "SCF\uff1a" : "SCF: ") + scfLabel
            + (zh ? "\uff0cSCF \u6700\u5927\u5faa\u73af " : ", SCF max cycles ") + (rd.scf_max_iter || 300);
          var summaryLine = document.createElement("div");
          summaryLine.textContent = line1 + " | " + line2;
          previewFields.appendChild(summaryLine);
        }

        advDiv.appendChild(previewWrap);
        _renderLocalSummary();
        _fetchBatchConfigPreview();

        function _appendMethodNote(dest, meta, basisValue, prefix) {
          var parts = [];
          if (basisValue) parts.push((currentLang === "zh-CN" ? "基组 " : "Basis ") + basisValue);
          if (meta.builtin_dispersion) parts.push((currentLang === "zh-CN" ? "色散 " : "Dispersion ") + meta.builtin_dispersion);
          if (meta.default_aux_j) parts.push((currentLang === "zh-CN" ? "辅助基组 " : "Aux basis ") + meta.default_aux_j);
          if (meta.ri_support === "composite" || meta.ri_support === "automatic") {
            parts.push(currentLang === "zh-CN" ? "方法内置" : "method built-in");
          }
          if (!parts.length) return;
          var note = document.createElement("div");
          note.className = "mc-method-note";
          note.textContent = (prefix || "") + parts.join(" · ");
          dest.appendChild(note);
        }

        // ── _renderBatchRoleForm: renders the form for _batchRoleActive ──
        function _renderBatchRoleForm() {
          roleFormDiv.innerHTML = "";
          var rd = _batchRoles[_batchRoleActive];
          if (!rd) return;

          // Method group.  It deliberately does not call buildFieldRow():
          // that renderer is for a shared stage and writes stages.batch.
          // These controls write the active role draft exclusively.
          var methodGrp = document.createElement("div");
          methodGrp.className = "mc-adv-group";
          var methodHdr = document.createElement("div");
          methodHdr.className = "mc-adv-group-header";
          methodHdr.appendChild(document.createTextNode(currentLang === "zh-CN" ? "计算方法" : "Methods"));
          methodGrp.appendChild(methodHdr);
          var methodFields = document.createElement("div");
          methodFields.className = "mc-adv-group-fields mc-role-method-fields";
          var optMethodMeta = lookupCI(methodCatalogCache.method_meta || {}, rd.method || "") || {};
          _buildBatchRoleMethodField(methodFields, "method", { label_zh: "优化／频率", label: "Opt / Frequency" });
          if (optMethodMeta.basis_inline !== false) {
            _buildBatchRoleMethodField(methodFields, "basis", { label_zh: "基组", label: "Basis" });
          } else {
            _appendMethodNote(methodFields, optMethodMeta, rd.basis, "");
          }
          if (batchHasStep("singlepoint")) {
            var spMethodMeta = lookupCI(methodCatalogCache.method_meta || {}, rd.sp_method || "") || {};
            _buildBatchRoleMethodField(methodFields, "sp_method", { label_zh: "单点能", label: "Single-point", singlePoint: true });
            if (spMethodMeta.basis_inline !== false) {
              _buildBatchRoleMethodField(methodFields, "sp_basis", { label_zh: "单点基组", label: "SP Basis", singlePoint: true });
              if (spMethodMeta.builtin_dispersion || spMethodMeta.ri_support !== "user") {
                _appendMethodNote(methodFields, spMethodMeta, "", currentLang === "zh-CN" ? "单点能：" : "SP: ");
              }
            } else {
              _appendMethodNote(methodFields, spMethodMeta, rd.sp_basis, currentLang === "zh-CN" ? "单点能：" : "SP: ");
            }
          }
          methodGrp.appendChild(methodFields);
          if (optMethodMeta.basis_inline !== false && (optMethodMeta.builtin_dispersion || optMethodMeta.ri_support !== "user")) {
            _appendMethodNote(methodFields, optMethodMeta, "", "");
          }
          if (optMethodMeta.basis_inline !== false && (!optMethodMeta.builtin_dispersion || optMethodMeta.ri_support === "user")) {
            var methodDetail = document.createElement("div");
            methodDetail.className = "mc-inline-method-detail";
            var methodDetailBtn = document.createElement("button");
            methodDetailBtn.type = "button";
            methodDetailBtn.className = "mc-inline-detail-toggle";
            methodDetailBtn.textContent = currentLang === "zh-CN" ? "▸ 方法详细设置" : "▸ Method details";
            var methodDetailFields = document.createElement("div");
            methodDetailFields.className = "mc-adv-group-fields mc-batch-detail-fields collapsed";
            if (!optMethodMeta.builtin_dispersion) _buildBatchRoleMethodField(methodDetailFields, "dispersion", { label_zh: "色散校正", label: "Dispersion" });
            if (optMethodMeta.ri_support === "user") {
              _buildBatchRoleMethodField(methodDetailFields, "aux_j_basis", { label_zh: "辅助 /J 基组", label: "Aux /J Basis" });
              if (optMethodMeta.needs_aux_c) _buildBatchRoleMethodField(methodDetailFields, "aux_c_basis", { label_zh: "辅助 /C 基组", label: "Aux /C Basis" });
            }
            methodDetailBtn.addEventListener("click", function() {
              var opening = methodDetailFields.classList.contains("collapsed");
              methodDetailFields.classList.toggle("collapsed", !opening);
              methodDetailBtn.textContent = (opening ? "▾ " : "▸ ") + (currentLang === "zh-CN" ? "方法详细设置" : "Method details");
            });
            methodDetail.appendChild(methodDetailBtn);
            methodDetail.appendChild(methodDetailFields);
            methodGrp.appendChild(methodDetail);
          }
          roleFormDiv.appendChild(methodGrp);

          // Optimizer group
          var optGrp = document.createElement("div");
          optGrp.className = "mc-adv-group";
          var optHdr = document.createElement("div");
          optHdr.className = "mc-adv-group-header";
          var optChevron = document.createElement("span");
          optChevron.className = "mc-adv-group-chevron open";
          optChevron.innerHTML = '<svg viewBox="0 0 16 16" width="10" height="10" stroke="currentColor" fill="none" stroke-width="2"><path d="M6 4l4 4-4 4"/></svg>';
          optHdr.appendChild(optChevron);
          optHdr.appendChild(document.createTextNode(currentLang === "zh-CN" ? "几何优化" : "Geometry Optimization"));
          optGrp.appendChild(optHdr);
          var optFields = document.createElement("div");
          optFields.className = "mc-adv-group-fields";

          _buildBatchRoleField(optFields, "opt_max_iter", { type: "int", min: 1, label_zh: "\u6700\u5927\u5faa\u73af", label: "Max Iterations" });
          _buildBatchRoleField(optFields, "opt_convergence", { type: "select", options: ["loose", "normal", "tight", "verytight"], label_zh: "收敛标准", label: "Convergence", option_labels_zh: { loose: "宽松", normal: "标准", tight: "严格", verytight: "非常严格" } });
          _buildBatchRoleField(optFields, "opt_trust_radius", { type: "float", min: 0.01, max: 1.0, step: 0.01, label_zh: "\u4fe1\u8d56\u534a\u5f84", label: "Trust Radius" });
          _buildBatchRoleField(optFields, "opt_initial_hessian", { type: "select", options: ["auto", "model", "calculate"], label_zh: "初始 Hessian", label: "Initial Hessian", option_labels_zh: { auto: "引擎默认", model: "模型 Hessian", calculate: "启动时计算" } });
          // Hessian recalc: custom UI with segmented control
          _buildHessianRecalcField(optFields);

          optGrp.appendChild(optFields);
          roleFormDiv.appendChild(optGrp);

          // SCF group
          var scfGrp = document.createElement("div");
          scfGrp.className = "mc-adv-group";
          var scfHdr = document.createElement("div");
          scfHdr.className = "mc-adv-group-header";
          var scfChevron = document.createElement("span");
          scfChevron.className = "mc-adv-group-chevron open";
          scfChevron.innerHTML = '<svg viewBox="0 0 16 16" width="10" height="10" stroke="currentColor" fill="none" stroke-width="2"><path d="M6 4l4 4-4 4"/></svg>';
          scfHdr.appendChild(scfChevron);
          scfHdr.appendChild(document.createTextNode("SCF"));
          scfGrp.appendChild(scfHdr);
          var scfFields = document.createElement("div");
          scfFields.className = "mc-adv-group-fields";
          _buildBatchRoleField(scfFields, "scf_max_iter", { type: "int", min: 1, label_zh: "\u6700\u5927\u5faa\u73af", label: "Max Iterations" });
          _buildBatchRoleField(scfFields, "scf_convergence", { type: "select", options: ["loose", "normal", "tight", "verytight"], label_zh: "收敛标准", label: "Convergence", option_labels_zh: { loose: "宽松", normal: "标准", tight: "严格", verytight: "非常严格" } });
          scfGrp.appendChild(scfFields);
          roleFormDiv.appendChild(scfGrp);

          // Purpose-specific disclosures replace the former mixed "More options" list.
          scfDetailSection.fields.innerHTML = "";
          _buildBatchRoleField(scfDetailSection.fields, "scf_strategy", { type: "select", options: ["normal", "slowconv", "soscf"], label_zh: "收敛算法", label: "SCF Strategy", option_labels_zh: { normal: "标准", slowconv: "慢收敛", soscf: "SOSCF" } });
          _buildBatchRoleField(scfDetailSection.fields, "scf_orbital_inherit", { type: "toggle", label_zh: "轨道初猜继承", label: "Orbital Inherit" });
          _buildBatchRoleField(scfDetailSection.fields, "scf_damp", { type: "toggle", label_zh: "阻尼", label: "Damping" });
          if (rd.scf_damp === true) _buildBatchRoleField(scfDetailSection.fields, "scf_damp_fac", { type: "float", min: 0, max: 1, step: 0.01, label_zh: "阻尼系数", label: "Damping Factor" });
          _buildBatchRoleField(scfDetailSection.fields, "scf_shift", { type: "toggle", label_zh: "能级移动", label: "Level Shift" });
          if (rd.scf_shift === true) _buildBatchRoleField(scfDetailSection.fields, "scf_shift_fac", { type: "float", min: 0, max: 10, step: 0.01, label_zh: "移动幅度", label: "Shift Value" });

          if (thermoSection) {
            thermoSection.fields.innerHTML = "";
            _buildBatchRoleField(thermoSection.fields, "temperature", { type: "float", min: 0, max: 10000, step: 0.01, label_zh: "温度 (K)", label: "Temperature (K)" });
            _buildBatchRoleField(thermoSection.fields, "pressure", { type: "float", min: 0, max: 1000, step: 0.01, label_zh: "压力 (atm)", label: "Pressure (atm)" });
            _buildBatchRoleField(thermoSection.fields, "scale_factor", { type: "float", min: 0.9, max: 1.0, step: 0.0001, label_zh: "频率缩放因子", label: "Frequency Scale Factor" });
          }

          retrySection.fields.innerHTML = "";
          _buildBatchRoleField(retrySection.fields, "opt_rescue_policy", { type: "select", options: ["off", "adaptive"], label_zh: "重试策略", label: "Retry Policy", option_labels_zh: { off: "关闭", adaptive: "自适应" } });
          if (rd.opt_rescue_policy !== "off") _buildBatchRoleField(retrySection.fields, "opt_max_rescue", { type: "int", min: 0, max: 10, label_zh: "次数上限", label: "Max Retries" });
        }

        // Role-aware method selector.  Catalog choices are retained instead
        // of falling back to a free-text field, and dependent basis/RI values
        // stay in the same role draft as the method itself.
        function _buildBatchRoleMethodField(dest, key, def) {
          var rd = _batchRoles[_batchRoleActive];
          if (!rd) return;
          var fDiv = document.createElement("div");
          fDiv.className = "mc-field";
          var lbl = document.createElement("label");
          lbl.textContent = currentLang === "zh-CN" ? (def.label_zh || def.label) : def.label;
          fDiv.appendChild(lbl);
          var method = def.singlePoint ? (rd.sp_method || rd.method || "") : (rd.method || "");
          var catalogKey = key === "method" || key === "sp_method" ? "functional" : key;
          var opts = getFieldOptions(catalogKey, engine, method, rd.basis) || [];
          var meta = lookupCI(methodCatalogCache.method_meta || {}, method) || {};
          if (key === "dispersion" && meta.builtin_dispersion) {
            var badge = document.createElement("span");
            badge.className = "mc-builtin-badge";
            badge.textContent = meta.builtin_dispersion + " (" + (currentLang === "zh-CN" ? "内置" : "built in") + ")";
            rd.dispersion = "none";
            fDiv.appendChild(badge);
            dest.appendChild(fDiv);
            return;
          }
          if (key === "aux_j_basis" && meta.ri_support && meta.ri_support !== "user") {
            var riBadge = document.createElement("span");
            riBadge.className = "mc-builtin-badge";
            riBadge.textContent = currentLang === "zh-CN" ? "方法内置" : "Built into method";
            rd.aux_j_basis = "";
            fDiv.appendChild(riBadge);
            dest.appendChild(fDiv);
            return;
          }
          var sel = document.createElement("select");
          if ((key === "basis" || key === "sp_basis") && !opts.length) opts = [""];
          opts.forEach(function(optValue) {
            var option = document.createElement("option");
            option.value = optValue;
            option.textContent = optValue || (currentLang === "zh-CN" ? "内置基组" : "Built-in basis");
            sel.appendChild(option);
          });
          var value = rd[key];
          if (value != null && opts.map(String).indexOf(String(value)) < 0 && value !== "") {
            var legacy = document.createElement("option"); legacy.value = value; legacy.textContent = value; sel.appendChild(legacy);
          }
          if (value != null) sel.value = String(value);
          sel.addEventListener("change", function() {
            rd[key] = sel.value;
            if (key === "method" || key === "sp_method") {
              var nextMethod = sel.value;
              var nextBasisKey = key === "method" ? "basis" : "sp_basis";
              var nextBasis = getFieldDefault("basis", engine, nextMethod) || "";
              rd[nextBasisKey] = nextBasis;
              if (key === "method") {
                rd.dispersion = getFieldDefault("dispersion", engine, nextMethod, nextBasis) || "none";
                rd.aux_j_basis = getFieldDefault("aux_j_basis", engine, nextMethod, nextBasis) || "";
              }
              _renderBatchRoleForm();
            }
            if (typeof scheduleValidate === "function") scheduleValidate();
            _fetchBatchConfigPreview();
          });
          fDiv.appendChild(sel);
          dest.appendChild(fDiv);
          if (key === "method" && meta.builtin_basis) {
            var note = document.createElement("div");
            note.className = "mc-method-basis-note";
            note.textContent = (currentLang === "zh-CN" ? "内置基组：" : "Built-in basis: ") + meta.builtin_basis;
            dest.appendChild(note);
          }
        }

        // ── _buildBatchRoleField: compact field renderer for role config ──
        function _buildBatchRoleField(dest, key, def) {
          var rd = _batchRoles[_batchRoleActive];
          if (!rd) return;
          var fDiv = document.createElement("div");
          fDiv.className = "mc-field";
          var lbl = document.createElement("label");
          lbl.textContent = currentLang === "zh-CN" ? (def.label_zh || def.label) : def.label;
          fDiv.appendChild(lbl);

          if (def.type === "toggle") {
            var toggleWrap = document.createElement("div");
            toggleWrap.className = "mc-toggle-wrap";
            var switchLabel = document.createElement("label");
            switchLabel.className = "mc-switch";
            switchLabel.setAttribute("aria-label", lbl.textContent);
            var toggle = document.createElement("input");
            toggle.type = "checkbox";
            toggle.checked = rd[key] === true;
            var track = document.createElement("span");
            track.className = "mc-switch-track";
            toggle.addEventListener("change", function() {
              rd[key] = toggle.checked;
              _renderBatchRoleForm();
              if (typeof scheduleValidate === "function") scheduleValidate();
              _fetchBatchConfigPreview();
            });
            switchLabel.appendChild(toggle);
            switchLabel.appendChild(track);
            toggleWrap.appendChild(switchLabel);
            fDiv.appendChild(toggleWrap);
          } else if (def.type === "select") {
            var sel = document.createElement("select");
            (def.options || []).forEach(function(opt) {
              var o = document.createElement("option");
              o.value = opt;
              o.textContent = (currentLang === "zh-CN" && def.option_labels_zh) ? (def.option_labels_zh[opt] || opt) : opt;
              sel.appendChild(o);
            });
            var curVal = rd[key];
            if (curVal === true) curVal = "true";
            if (curVal === false) curVal = "false";
            if (curVal != null) sel.value = String(curVal);
            sel.addEventListener("change", function() {
              var v = sel.value;
              if (v === "true") v = true;
              else if (v === "false") v = false;
              else {
                var parsed = parseInt(v, 10);
                if (!isNaN(parsed) && String(parsed) === v) v = parsed;
              }
              rd[key] = v;
              if (["scf_damp", "scf_shift", "opt_rescue_policy"].indexOf(key) >= 0) _renderBatchRoleForm();
              if (typeof scheduleValidate === "function") scheduleValidate();
              _fetchBatchConfigPreview();
            });
            fDiv.appendChild(sel);
          } else if (def.type === "int" || def.type === "float") {
            var inp = document.createElement("input");
            inp.type = "number";
            inp.min = String(def.min);
            if (def.max != null) inp.max = String(def.max);
            inp.step = def.type === "int" ? "1" : String(def.step || 0.01);
            inp.placeholder = currentLang === "zh-CN" ? "引擎默认" : "Engine default";
            if (rd[key] != null) inp.value = String(rd[key]);
            inp.addEventListener("change", function() {
              if (inp.value === "") {
                rd[key] = null;
              } else {
                var v = def.type === "int" ? parseInt(inp.value, 10) : parseFloat(inp.value);
                if (isNaN(v) || (def.min != null && v < def.min) || (def.max != null && v > def.max)) {
                  inp.setCustomValidity(currentLang === "zh-CN" ? "请输入范围内的有效数值" : "Enter a valid value in the allowed range");
                  inp.reportValidity();
                  return;
                }
                inp.setCustomValidity("");
                rd[key] = v;
              }
              if (typeof scheduleValidate === "function") scheduleValidate();
              _fetchBatchConfigPreview();
            });
            fDiv.appendChild(inp);
          } else {
            var textInp = document.createElement("input");
            textInp.type = "text";
            textInp.placeholder = currentLang === "zh-CN" ? "引擎默认" : "Engine default";
            if (rd[key] != null) textInp.value = String(rd[key]);
            textInp.addEventListener("change", function() {
              rd[key] = textInp.value || null;
              if (typeof scheduleValidate === "function") scheduleValidate();
              _fetchBatchConfigPreview();
            });
            fDiv.appendChild(textInp);
          }
      // ── Disable retry fields for native single-coordinate scan (Item 5) ──
      // scan_optimizer_retries and scan_optimizer_retry_strategy are meaningless
      // for native ORCA single-coordinate scans (no per-point retry capability).
      if (lvDef.level_id === "scan_optimizer" &&
          (fieldName === "scan_optimizer_retries" || fieldName === "scan_optimizer_retry_strategy") &&
          (getLevelState(lvDef.level_id) || {})._native_single_coord) {
        var _inp = fDiv.querySelector("select, input");
        if (_inp) { _inp.disabled = true; _inp.title = t("modal.native_scan_no_per_point_retry") || ""; }
        var _note = document.createElement("div");
        _note.style.cssText = "font-size:10px;color:var(--text-muted,#888);margin-top:2px;";
        _note.textContent = t("modal.scan_summary_native_no_retry") || "整跑重试，无逐点重试（单坐标原生扫描）";
        fDiv.appendChild(_note);
      }
      dest.appendChild(fDiv);
    }

        // ── Hessian recalc field: one explicit selector + optional interval ──
        function _buildHessianRecalcField(dest) {
          var rd = _batchRoles[_batchRoleActive];
          if (!rd) return;
          var fDiv = document.createElement("div");
          fDiv.className = "mc-field mc-field-hessian";
          var lbl = document.createElement("label");
          lbl.textContent = currentLang === "zh-CN" ? "Hessian \u91cd\u7b97" : "Hessian Recalc";
          fDiv.appendChild(lbl);

          var rawVal = rd.opt_recalc_hess;
          var mode = "engine_default";
          var customVal = 10;
          if (rawVal === 0) { mode = "off"; }
          else if (rawVal !== undefined && rawVal !== "" && rawVal !== null && rawVal !== "auto") {
            var parsed = parseInt(rawVal, 10);
            if (!isNaN(parsed) && parsed > 0) { mode = "custom"; customVal = parsed; }
          }

          var selectWrap = document.createElement("div");
          selectWrap.className = "mc-hessian-select-wrap";
          var modeSelect = document.createElement("select");
          [["engine_default", currentLang === "zh-CN" ? "引擎默认" : "Engine default"], ["off", currentLang === "zh-CN" ? "不重算" : "No recalc"], ["custom", currentLang === "zh-CN" ? "每 N 次循环" : "Every N cycles"]].forEach(function(pair) {
            var opt = document.createElement("option"); opt.value = pair[0]; opt.textContent = pair[1]; modeSelect.appendChild(opt);
          });
          modeSelect.value = mode;
          selectWrap.appendChild(modeSelect);
          fDiv.appendChild(selectWrap);
          dest.appendChild(fDiv);

          var intervalField = document.createElement("div");
          intervalField.className = "mc-field mc-hessian-interval";
          intervalField.hidden = mode !== "custom";
          var intervalLabel = document.createElement("label");
          intervalLabel.textContent = currentLang === "zh-CN" ? "间隔" : "Interval";
          intervalField.appendChild(intervalLabel);
          var intervalControl = document.createElement("div");
          intervalControl.className = "mc-hessian-interval-control";
          var numInp = document.createElement("input");
          numInp.type = "number";
          numInp.min = "1";
          numInp.max = "1000";
          numInp.step = "1";
          numInp.value = String(customVal);
          numInp.addEventListener("change", function() {
            var v = parseInt(numInp.value, 10);
            if (isNaN(v) || v < 1 || v > 1000) {
              numInp.setCustomValidity(currentLang === "zh-CN" ? "请输入 1 到 1000 的整数" : "Enter an integer from 1 to 1000");
              numInp.reportValidity();
              return;
            }
            numInp.setCustomValidity("");
            customVal = v;
            if (mode === "custom") { rd.opt_recalc_hess = v; if (typeof scheduleValidate === "function") scheduleValidate(); _fetchBatchConfigPreview(); }
          });
          var unitLbl = document.createElement("span");
          unitLbl.className = "unit";
          unitLbl.textContent = currentLang === "zh-CN" ? "次循环" : "cycles";
          intervalControl.appendChild(numInp);
          intervalControl.appendChild(unitLbl);
          intervalField.appendChild(intervalControl);
          dest.appendChild(intervalField);
          modeSelect.addEventListener("change", function() {
            mode = modeSelect.value;
            if (mode === "engine_default") rd.opt_recalc_hess = "auto";
            else if (mode === "off") rd.opt_recalc_hess = 0;
            else rd.opt_recalc_hess = customVal;
            intervalField.hidden = mode !== "custom";
            if (typeof scheduleValidate === "function") scheduleValidate();
            _fetchBatchConfigPreview();
          });
        }

        _renderBatchRoleForm();
        window._renderBatchRoleForm = _renderBatchRoleForm;
      } else {
        // Non-batch levels: flat advanced list (original behavior)
        advancedFields.forEach(function(fn) { buildFieldRow(fn, advDiv); });
      }

      advWrap.appendChild(advDiv);

      advToggle.addEventListener("click", function() {
        var shown = advDiv.style.display !== "none";
        advDiv.style.display = shown ? "none" : "grid";
        var toggleLabel = lvDef.level_id === "batch" ? (currentLang === "zh-CN" ? "\u9ad8\u7ea7\u8ba1\u7b97\u63a7\u5236" : "Advanced Calculation Controls") : (t("modal.advanced") || "\u9ad8\u7ea7");
        advToggle.textContent = shown ? "+ " + toggleLabel : "- " + toggleLabel;
      });

      bodyDiv.appendChild(advWrap);
      // Append summary block outside advWrap (always visible in all scopes)
      if (typeof batchSummaryBlock !== "undefined" && batchSummaryBlock) {
        bodyDiv.appendChild(batchSummaryBlock);
      }
    }

    card.appendChild(bodyDiv);

    hdr.addEventListener("click", function() {
      var expanded2 = bodyDiv.style.display !== "none";
      bodyDiv.style.display = expanded2 ? "none" : "grid";
      expBtn.classList.toggle("open", !expanded2);
    });

    return card;
  }

  function rebuildLevelCard(lid) {
    var ml = schema.method_levels || [];
    var lvDef = null;
    for (var idx = 0; idx < ml.length; idx++) {
      if (ml[idx].level_id === lid) { lvDef = ml[idx]; break; }
    }
    if (!lvDef) return;
    var container = document.getElementById("mc-levels-container");
    var oldCard = container.querySelector('[data-level-id="' + lid + '"]');
    var stateData = wizardState.method.stages[lid] || {};
    stateData.engine = stateData.engine || lvDef.allowed_engines[0];

    var wasExpanded = true;
    if (oldCard) {
      var oldBody = oldCard.querySelector(".mc-level-body");
      if (oldBody) wasExpanded = oldBody.style.display !== "none";
    }

    var newCard = buildLevelCard(lvDef, stateData);
    if (oldCard) oldCard.replaceWith(newCard);

    if (!wasExpanded) {
      var newBody = newCard.querySelector(".mc-level-body");
      var newExpBtn = newCard.querySelector(".mc-level-expand");
      if (newBody) newBody.style.display = "none";
      if (newExpBtn) newExpBtn.classList.remove("open");
    }
  }

  var validateTimer = null;
  var _lastValidationResult = null;
  function scheduleValidate() {
    if (validateTimer) clearTimeout(validateTimer);
    // The outer PES picker only selects structure atoms/bonds. Keep its
    // selection mode and read-only task badge synchronized with this shared
    // protocol dialog.
    if (typeof updatePESSelectionForProtocol === "function") updatePESSelectionForProtocol();
    validateTimer = setTimeout(function() {
      doValidate().then(refreshMethodValidationState);
    }, 400);
  }

  async function doValidate() {
    var levels = {};
    (schema.method_levels || []).forEach(function(lvDef) {
      var state = wizardState.method.stages[lvDef.level_id];
      if (state && state.engine && state._disabled !== true) {
        var copy = JSON.parse(JSON.stringify(state));
        delete copy._disabled;
        levels[lvDef.level_id] = copy;
      }
    });
    try {
      // Legacy had no resp.ok check — a JSON error body flowed through the
      // normalized write-back skip (body.normalized_levels absent); the
      // adapter resolves with the parsed error body identically.
      var body = await window.ACPClient.wizardEndpoints.validateMethod({
        schema_id: schemaId, levels: levels
      });
      // Skip the normalized write-back once the modal has been closed
      // (cancel already restored the draft) — a late validation response
      // must not mutate restored state.
      var modalHidden = document.getElementById("method-config-modal").style.display === "none";
      if (body.normalized_levels && !modalHidden) {
        Object.keys(body.normalized_levels).forEach(function(lid) {
          var norm = body.normalized_levels[lid];
          if (!wizardState.method.stages[lid]) return; // don't resurrect disabled/absent levels
          Object.keys(norm).forEach(function(k) {
            if (wizardState.method.stages[lid][k] === undefined || wizardState.method.stages[lid][k] === null || wizardState.method.stages[lid][k] === "") {
              wizardState.method.stages[lid][k] = norm[k];
            }
          });
        });
      }
      _lastValidationResult = body;
      return body;
    } catch(e) { return { valid: false, errors: [e.message], warnings: [] }; }
  }

  function refreshMethodValidationState() {
    var vr = _lastValidationResult;
    var errEl = document.getElementById("mc-validation-errors");
    var warnEl = document.getElementById("mc-validation-warnings");

    // Show validation errors
    if (vr && vr.errors && vr.errors.length) {
      errEl.style.display = "block";
      errEl.innerHTML = vr.errors.map(function(e) {
        return "<div>\u2717 " + escapeHtml(e) + "</div>";
      }).join("");
    } else {
      errEl.style.display = "none";
      errEl.innerHTML = "";
    }

    // T24: migration/canonicalization warnings are non-blocking — show them
    // (amber block) and remember them so the wizard summary card can render
    // the same list after the modal closes.
    var methodWarnings = (vr && vr.warnings) || [];
    if (wizardState.method) wizardState.method.warnings = methodWarnings;
    if (warnEl) {
      if (methodWarnings.length) {
        warnEl.style.display = "block";
        warnEl.innerHTML = methodWarnings.map(function(w) {
          return "<div>\u26a0 " + escapeHtml(w) + "</div>";
        }).join("");
      } else {
        warnEl.style.display = "none";
        warnEl.innerHTML = "";
      }
    }

    // Collect per-level errors for status dots
    var levelErrors = {};
    if (vr && vr.errors) {
      vr.errors.forEach(function(e) {
        var lid = "";
        var m = e.match(/Level '([^']+)'/);
        if (m) lid = m[1];
        if (!levelErrors[lid]) levelErrors[lid] = [];
        levelErrors[lid].push(e);
      });
    }

    // Update status dots
    (schema.method_levels || []).forEach(function(lvDef) {
      var cards = document.querySelectorAll('.mc-level-card[data-level-id="' + lvDef.level_id + '"]');
      cards.forEach(function(c) {
        var dot = c.querySelector(".mc-level-status");
        var state = wizardState.method.stages[lvDef.level_id];
        var hasErrors = levelErrors[lvDef.level_id] && levelErrors[lvDef.level_id].length > 0;
        var okState = !hasErrors && ((state && state.engine && state._disabled !== true) || lvDef.required === false);
        if (dot) dot.className = "mc-level-status " + (okState ? "ok" : "err");
        if (hasErrors && dot) {
          dot.title = levelErrors[lvDef.level_id].join("\n");
        }
      });
    });
  }

  // ---- Build initial UI ----
  var container = document.getElementById("mc-levels-container");
  container.innerHTML = "";

  // Profile selector
  var profileSel = document.getElementById("mc-profile-select");
  profileSel.innerHTML = "";
  var profileLabelEl = document.querySelector(".mc-profile-label");
  if (wizardState.workflow.id === "BatchOptimize" && profileLabelEl) {
    profileLabelEl.textContent = currentLang === "zh-CN" ? "\u8ba1\u7b97\u6d41\u7a0b" : "Calculation flow";
  } else if (profileLabelEl) {
    profileLabelEl.textContent = t("modal.profile") || "\u8ba1\u7b97\u65b9\u6848";
  }
  var profiles = schema.profiles || [];
  if (profiles.length) {
    var topicOpt = document.createElement("optgroup");
    topicOpt.label = t("modal.profile") || "\u9884\u8bbe\u65b9\u6848";
    profiles.forEach(function(p) {
      var o = document.createElement("option");
      o.value = p.profile_id;
      var profileLabel = currentLang === "zh-CN" ? (p.label_zh || p.label) : p.label;
      var profileSummary = currentLang === "zh-CN" ? (p.summary_zh || p.summary) : p.summary;
      if (wizardState.workflow.id === "BatchOptimize") {
        o.textContent = profileLabel;
        o.title = profileSummary || "";
      } else {
        o.textContent = profileLabel + " - " + profileSummary;
      }
      if (p.profile_id === wizardState.method.profile_id) o.selected = true;
      topicOpt.appendChild(o);
    });
    profileSel.appendChild(topicOpt);
  }
  var customOpt = document.createElement("optgroup");
  customOpt.label = "\u81ea\u5b9a\u4e49";
  var co = document.createElement("option");
  co.value = "__custom__";
  co.textContent = t("modal.custom") || "\u81ea\u5b9a\u4e49";
  if (wizardState.method.profile_id === "__custom__") co.selected = true;
  customOpt.appendChild(co);
  profileSel.appendChild(customOpt);

  profileSel.addEventListener("change", function() {
    var pid = profileSel.value;
    if (pid === "__custom__") {
      wizardState.method.profile_id = "__custom__";
      wizardState.method.profile_label = t("modal.custom") || "Custom";
      if (wizardState.workflow && wizardState.workflow.id === "mechanism") syncMechanismPanelFromProfile(null);
      updateConfigCards();
      doValidate().then(function() { refreshMethodValidationState(); });
      return;
    }
    var chosen = null;
    for (var pi = 0; pi < profiles.length; pi++) {
      if (profiles[pi].profile_id === pid) { chosen = profiles[pi]; break; }
    }
    if (chosen) {
      wizardState.method.profile_id = chosen.profile_id;
      wizardState.method.profile_label = currentLang === "zh-CN"
        ? (chosen.label_zh || chosen.label)
        : chosen.label;
      wizardState.method.stages = JSON.parse(JSON.stringify(chosen.levels || {}));
      if (wizardState.workflow && wizardState.workflow.id === "mechanism") syncMechanismPanelFromProfile(chosen);
      // Rebuild level cards
      var container2 = document.getElementById("mc-levels-container");
      container2.innerHTML = "";
      (schema.method_levels || []).forEach(function(lvDef) {
        if (!lvDef.required && !(lvDef.level_id in wizardState.method.stages)) return;
        var stateData = wizardState.method.stages[lvDef.level_id] || {};
        container2.appendChild(buildLevelCard(lvDef, stateData));
      });
      appendRank1OnlyToggle();
      doValidate().then(function() { refreshMethodValidationState(); });
    }
  });

  // Build level cards
  (schema.method_levels || []).forEach(function(lvDef) {
    if (!lvDef.required && !(lvDef.level_id in wizardState.method.stages)) return;
    var stateData = wizardState.method.stages[lvDef.level_id] || {};
    if (!stateData.engine) stateData.engine = lvDef.allowed_engines[0];
    container.appendChild(buildLevelCard(lvDef, stateData));
  });

  // rank1-only option card (energy / xtbmd_censo_energy workflows): only
  // the CENSO/xTB rank1 conformer receives the fine DFT treatment; the
  // ensemble total free energy is then G_total = G1 + RT*ln(p1) from the
  // screening Boltzmann table.  Inserted AFTER the level cards so
  // container.innerHtml="" rebuilds do not wipe it; also re-inserted on
  // profile switch above.
  function appendRank1OnlyToggle() {
    if (wizardState.workflow.id !== "energy" && wizardState.workflow.id !== "xtbmd_censo_energy") return;
    var card = document.createElement("div");
    card.className = "mc-rank1-option" + (wizardState.method.rank1_only ? " selected" : "");

    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "mc-rank1-check";
    cb.checked = !!wizardState.method.rank1_only;

    var text = document.createElement("div");
    text.className = "mc-rank1-text";
    var title = document.createElement("div");
    title.className = "mc-rank1-title";
    title.textContent = t("modal.rank1_only_title") || "\u4ec5\u8ba1\u7b97 rank1 \u7cbe\u7ec6 DFT";
    var desc = document.createElement("div");
    desc.className = "mc-rank1-desc";
    var descKey = (wizardState.workflow.id === "energy")
      ? "modal.rank1_only_desc"
      : "modal.rank1_only_desc_xtbmd";
    desc.innerHTML = (t(descKey) || "") +
      ' <code>G_total = G\u2081 + RT\u00b7ln p\u2081</code>';

    function apply() {
      wizardState.method.rank1_only = cb.checked;
      card.classList.toggle("selected", cb.checked);
      scheduleValidate();
    }
    cb.addEventListener("change", function(ev) { ev.stopPropagation(); apply(); });
    card.addEventListener("click", function(ev) {
      if (ev.target === cb) return;
      cb.checked = !cb.checked;
      apply();
    });

    text.appendChild(title);
    text.appendChild(desc);
    card.appendChild(cb);
    card.appendChild(text);
    document.getElementById("mc-levels-container").prepend(card);
  }
  appendRank1OnlyToggle();

  document.getElementById("method-config-modal").style.display = "flex";
  doValidate().then(function() { refreshMethodValidationState(); });
}

function jobFormValidationFailed(message) {
  if (window.ACPJobEditor && ACPJobEditor.isCollecting()) throw new Error(message);
  window.alert(message);
}

async function submitJobBatch(bodies) {
  // The scheduler owns execution concurrency.  Submit every molecule now so
  // all JobRecords are durable before the first workflow finishes; the
  // server-side batch gate keeps excess jobs in `queued` state.
  await Promise.all(bodies.map(function(body) {
    return api("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }));
}

function buildPESProtocolFromMethod(charge, multiplicity) {
  var stages = (wizardState.method && wizardState.method.stages) || {};
  var driver = stages.scan_driver || {};
  var optimizer = stages.scan_optimizer || {};
  var singlePoint = stages.single_point || {};
  var maxIterations = parseInt(driver.scan_max_iterations, 10);
  if (!isFinite(maxIterations) || maxIterations < 1) maxIterations = 250;
  var optimizerMaxIterations = parseInt(optimizer.scan_optimizer_max_iterations, 10);
  if (!isFinite(optimizerMaxIterations) || optimizerMaxIterations < 1) optimizerMaxIterations = maxIterations;
  var singlePointHasState = Object.keys(singlePoint).length > 0;
  var singlePointEnabled = singlePoint._disabled !== true &&
    (!singlePointHasState || !!singlePoint.engine);
  // (T23g) method defaults resolve from the catalog (FIELD_DEFINITIONS) —
  // no hardcoded method literals outside historical-compat branches.
  var scanOptimizerDefaultMethod = String(
    CatalogUtils.getFieldDefault(methodCatalogCache, "scan_optimizer_method", "orca", null) || ""
  );
  // HISTORICAL-COMPAT (T23g): empty single_point fallback "B97-3c" is a
  // behavior-preserving historical default (predates and diverges from
  // FIELD_DEFINITIONS.functional.default "r2SCAN-3c" — do NOT re-default).
  var singlePointMethod = String(singlePoint.functional || singlePoint.method || "B97-3c").trim();
  var scanOptimizerMethod = String(optimizer.scan_optimizer_method || scanOptimizerDefaultMethod).trim();
  var scanOptimizerMeta = lookupCI(methodCatalogCache.method_meta || {}, scanOptimizerMethod) || {};
  var scanOptimizerIsComposite = scanOptimizerMeta.ri_support === "composite";
  // ── serialization defense (T23a) ──────────────────────────────────────
  // Family-locked methods (xTB family: no DFT basis/dispersion offer — the
  // T15 catalog family gate) must never serialize basis/dispersion/RI/aux
  // overrides: stale wizard state (pre-gating drafts, copy leaks) must not
  // reach the job payload. Defense-in-depth on top of T11's
  // ri_support:"composite" basis nulling and of the T15/T16 UI gating —
  // these frontend checks never replace backend validation (T13/T14).
  var scanOptimizerFamilyLocked = CatalogUtils.isFieldFamilyLocked(methodCatalogCache, "basis", scanOptimizerMethod)
    || CatalogUtils.isFieldFamilyLocked(methodCatalogCache, "dispersion", scanOptimizerMethod);
  var scanOptimizerRiLocked = CatalogUtils.isFieldFamilyLocked(methodCatalogCache, "ri_approximation", scanOptimizerMethod);
  var scanOptimizerBasis = scanOptimizerIsComposite || scanOptimizerFamilyLocked || !optimizer.scan_optimizer_basis
    ? null
    : String(optimizer.scan_optimizer_basis);
  var scanOptimizerDispersion = scanOptimizerFamilyLocked
    ? null
    : (optimizer.scan_optimizer_dispersion ? String(optimizer.scan_optimizer_dispersion) : null);
  var scanOptimizerRi = scanOptimizerRiLocked
    ? "none"
    : String(optimizer.scan_optimizer_ri_approximation || "none");
  // (T23a) same family-locked serialization defense for the single-point
  // level — a GFN level arrives here via copy-scan-level (T16) or a stale
  // pre-gating draft, and must not leak overrides into the job payload.
  var singlePointFamilyLocked = CatalogUtils.isFieldFamilyLocked(methodCatalogCache, "basis", singlePointMethod)
    || CatalogUtils.isFieldFamilyLocked(methodCatalogCache, "dispersion", singlePointMethod);
  var singlePointRiLocked = CatalogUtils.isFieldFamilyLocked(methodCatalogCache, "ri_approximation", singlePointMethod);
  return {
    scan_driver: {
      software: String(driver.engine || "orca"),
      mode: String(driver.scan_mode || "relaxed_scan"),
      reuse_previous_geometry: driver.scan_reuse_previous_geometry !== false,
      full_scan: driver.scan_full_scan !== false,
      use_scants: !!driver.scan_use_scants,
      max_iterations: maxIterations,
      failure_policy: String(driver.scan_failure_policy || "retry_previous"),
      retry_count: Math.max(0, parseInt(driver.scan_retry_count, 10) || 0),
    },
    scan_optimizer: {
      method: scanOptimizerMethod,
      // The UI may display a composite method's built-in basis label, but
      // it must not be serialized as a separate basis override
      // (T23a: family-locked xTB methods null basis/dispersion/RI too).
      basis: scanOptimizerBasis,
      dispersion: scanOptimizerDispersion,
      solvent_model: String(optimizer.scan_optimizer_solvent_model || "none"),
      solvent: optimizer.scan_optimizer_solvent ? String(optimizer.scan_optimizer_solvent) : null,
      grid: optimizer.scan_optimizer_grid ? String(optimizer.scan_optimizer_grid) : null,
      scf_convergence: optimizer.scan_optimizer_scf_convergence ? String(optimizer.scan_optimizer_scf_convergence) : null,
      scf_max_iterations: optimizer.scan_optimizer_scf_max_iterations ? parseInt(optimizer.scan_optimizer_scf_max_iterations, 10) : null,
      ri_approximation: scanOptimizerRi,
      max_iterations: optimizerMaxIterations,
      convergence: String(optimizer.scan_optimizer_convergence || "normal"),
      retry_count: Math.max(0, parseInt(optimizer.scan_optimizer_retries, 10) || 0),
      retry_strategy: String(optimizer.scan_optimizer_retry_strategy || "previous_geometry"),
    },
    single_point: {
      enabled: singlePointEnabled,
      software: String(singlePoint.engine || "orca"),
      method: singlePointMethod,
      // (T23a) family-locked serialization defense: basis/dispersion/RI/aux
      // overrides never reach the job payload for xTB-family methods.
      basis: singlePointFamilyLocked ? null : (singlePoint.basis ? String(singlePoint.basis) : null),
      dispersion: singlePointFamilyLocked ? null : (singlePoint.dispersion ? String(singlePoint.dispersion) : null),
      ri_approximation: singlePointRiLocked ? "none" : String(singlePoint.ri_approximation || "none"),
      aux_j_basis: singlePointRiLocked ? null : (singlePoint.aux_j_basis ? String(singlePoint.aux_j_basis) : null),
      aux_c_basis: singlePointRiLocked ? null : (singlePoint.aux_c_basis ? String(singlePoint.aux_c_basis) : null),
      solvent_model: String(singlePoint.solvent_model || "none"),
      solvent: singlePoint.solvent ? String(singlePoint.solvent) : null,
      grid: singlePoint.grid ? String(singlePoint.grid) : null,
      scf_convergence: singlePoint.scf_convergence ? String(singlePoint.scf_convergence) : null,
      charge: charge,
      multiplicity: multiplicity,
      resume: singlePoint.single_point_resume !== false,
    },
  };
}

async function submitPESsearchTask() {
  var structure = currentPESStructure();
  var selected = (pesSelectionState.selectedAtoms || []).slice();
  var coordinate = getPESCoordinateConfig();
  var selectionKind = pesSelectionState.selectionKind || pesSelectionKindForCoordinate(coordinate.kind);
  var kind = selectionKind === "angle" ? "angle" : selectionKind === "dihedral" ? "dihedral" : "distance";
  var expected = selectionKind === "bond_stretch" ? 2 : selectionKind === "angle" ? 3 : 4;
  if (!structure || !structure.xyz) {
    jobFormValidationFailed(t("modal.parse_first") || "请先解析结构");
    return;
  }
  if (wizardStructures.length !== 1) {
    jobFormValidationFailed("PESsearch 每个任务需要且只接受一个结构，请返回步骤 1，仅保留一个输入结构后再提交。");
    return;
  }
  var sourceXyz = String(structure.xyz).trim();
  if (!pesSelectionState.structureKey || pesSelectionState.structureKey !== sourceXyz) {
    jobFormValidationFailed("扫描原子选择与当前输入结构不一致，请返回步骤 2，在右侧结构预览中重新选择。");
    return;
  }
  if (selected.length !== expected) {
    jobFormValidationFailed("请先在结构预览中选择 " + expected + " 个扫描原子");
    return;
  }
  var sourceAtoms = parseXyzAtoms(sourceXyz);
  var distinctSelection = new Set(selected);
  if (distinctSelection.size !== expected || selected.some(function(index) {
    return !Number.isInteger(index) || index < 0 || index >= sourceAtoms.length;
  })) {
    jobFormValidationFailed("扫描原子编号无效或重复，请返回步骤 2 重新选择。");
    return;
  }
  var start = coordinate.start;
  var end = coordinate.end;
  var points = coordinate.n_points;
  var bond2Range = null;
  if (!isFinite(start) || !isFinite(end) || !isFinite(points) || points < 3 || points > 101 || start === end) {
    jobFormValidationFailed("请在计算协议中完整填写有效的 PESsearch 扫描范围和点数");
    return;
  }
  if (selectionKind === "double_bond_scan") {
    // Synchronized progress over per-bond windows: each bond stretches from
    // its own current length by the same delta taken from the shared range.
    var selAtoms = (typeof s2scanState !== "undefined" && s2scanState && s2scanState.atoms) || [];
    var a0 = selAtoms[selected[0]], a1 = selAtoms[selected[1]], a2 = selAtoms[selected[2]], a3 = selAtoms[selected[3]];
    if (!a0 || !a1 || !a2 || !a3) {
      jobFormValidationFailed("无法读取选中原子的坐标，请在结构预览中重新选择扫描原子");
      return;
    }
    var delta = end - start;
    var len1 = Number(s2scanDist(a0, a1).toFixed(4));
    var len2 = Number(s2scanDist(a2, a3).toFixed(4));
    var end1 = Number((len1 + delta).toFixed(4));
    var end2 = Number((len2 + delta).toFixed(4));
    if (len1 + delta <= 0 || len2 + delta <= 0) {
      jobFormValidationFailed("双键同步扫描的目标键长必须大于 0，请调整扫描范围（当前键长 " + len1 + " Å / " + len2 + " Å）");
      return;
    }
    start = len1;
    end = end1;
    bond2Range = { start: len2, end: end2 };
  }
  var taskName = String(document.getElementById("modal-task-name").value || "").trim()
    .replace(/[/\\:*?"<>|]/g, "")
    .replace(/\s+/g, "_")
    .replace(/^_+|_+$/g, "");
  var charge = parseInt(document.getElementById("modal-charge").value, 10) || 0;
  var multiplicity = parseInt(document.getElementById("modal-mult").value, 10) || 1;
  var nproc = parseInt(document.getElementById("modal-nproc").value, 10) || 4;
  var mem = ACPMemoryInput.read("modal-mem", "modal-mem-unit");
  if (!mem) return;
  var body = {
    workflow: "PESsearch",
    name: taskName || "PESsearch",
    task_name: taskName || "PESsearch",
    molecule_name: structureMoleculeName(structure) || "mol_1",
    remark: String(document.getElementById("modal-remark").value || "").trim(),
    input: {
      source: {
        source_type: "xyz_text",
        xyz_text: String(structure.xyz),
        charge: charge,
        multiplicity: multiplicity,
      },
      coordinate: {
        kind: kind,
        atoms: selectionKind === "double_bond_scan" ? selected.slice(0, 2) : selected,
        unit: kind === "distance" ? "angstrom" : "degree",
        start: start,
        end: end,
        n_points: points,
        bond_type: coordinate.bond_type,
      },
      selection: {
        mode: "functional",
        kind: selectionKind,
        atom_indices: selected,
        groups: selectionKind === "double_bond_scan" ? [selected.slice(0, 2), selected.slice(2, 4)] : [],
        selected_bond: selectionKind === "bond_stretch" && selected.length === 2,
      },
      protocol: buildPESProtocolFromMethod(charge, multiplicity),
    },
    method: {
      mode: "bond_length_scan",
      schema_id: wizardState.workflow.schema_id || "pes_scan",
      profile_id: wizardState.method.profile_id || "default",
    },
    resources: { nproc: nproc, mem: mem, parallelism: 1 },
  };
  if (selectionKind === "double_bond_scan") {
    body.input.coordinates = [
      { kind: "distance", atoms: selected.slice(0, 2), unit: "angstrom", start: start, end: end, n_points: points },
      { kind: "distance", atoms: selected.slice(2, 4), unit: "angstrom", start: bond2Range.start, end: bond2Range.end, n_points: points },
    ];
  }
  var projectId = String(document.getElementById("modal-project-select").value || "").trim();
  if (!projectId) projectId = "uncategorized";
  body.project_id = projectId;
  applyNodeSelectionToBody(body);
  // ── Submit confirmation summary (Item 5) ──
  // Show a summary grid before POSTing so the user can verify the configuration.
  var _protocol = body.input.protocol;
  var _so = _protocol.scan_optimizer || {};
  var _sp = _protocol.single_point || {};
  var _isNativeSingleCoord = selectionKind !== "double_bond_scan";
  // Build summary text
  // (T23g) fallback resolves from the catalog default (the protocol builder
  // already guarantees a non-empty method; this is pure defense).
  var _scanMethodLabel = _so.method || String(
    CatalogUtils.getFieldDefault(methodCatalogCache, "scan_optimizer_method", "orca", null) || ""
  );
  var _scanSolventSummary = (_so.solvent_model && _so.solvent_model !== "none")
    ? (_so.solvent_model + "(" + (_so.solvent || "?") + ")") : "";
  var _scanConvLabel = _so.convergence || "normal";
  var _scanMaxIter = _so.max_iterations || 250;
  var _geomLine = _scanMethodLabel
    + (_scanSolventSummary ? " / " + _scanSolventSummary : "")
    + " · " + _scanConvLabel + " · " + _scanMaxIter + " " + (currentLang === "zh-CN" ? "步" : "steps")
    + " · " + points + " " + (currentLang === "zh-CN" ? "点" : "points");
  var _spLine;
  if (_sp.enabled) {
    _spLine = (_sp.method || "?")
      + (_sp.basis ? "/" + _sp.basis : "")
      + (_sp.dispersion && _sp.dispersion !== "none" ? "-D" + _sp.dispersion : "")
      + (_sp.solvent_model && _sp.solvent_model !== "none" ? " / " + _sp.solvent_model + "(" + (_sp.solvent || "?") + ")" : "");
  } else {
    _spLine = t("modal.scan_summary_sp_disabled") || "已关闭";
  }
  var _recoveryLine;
  if (_isNativeSingleCoord) {
    _recoveryLine = t("modal.scan_summary_native_no_retry") || "整跑重试，无逐点重试（单坐标原生扫描）";
  } else {
    var _retryCount = _so.retry_count || 0;
    var _retryStrategy = _so.retry_strategy || "previous_geometry";
    _recoveryLine = (t("modal.scan_summary_per_point_retry") || "逐点重试")
      + " ×" + _retryCount + "（" + _retryStrategy + "）";
  }
  var _summaryLines = [
    [t("modal.scan_summary_geometry") || "几何扫描", _geomLine],
    [t("modal.scan_summary_sp") || "单点能", _spLine],
    [t("modal.scan_summary_recovery") || "恢复策略", _recoveryLine],
  ];
  var _confirmMsg = (t("modal.scan_summary_title") || "提交确认") + "\n\n"
    + _summaryLines.map(function(pair) { return pair[0] + "：" + pair[1]; }).join("\n")
    + "\n\n" + (currentLang === "zh-CN" ? "确认提交？" : "Confirm submission?");
  if (!(window.ACPJobEditor && ACPJobEditor.isCollecting()) && !window.confirm(_confirmMsg)) {
    if (submitButton) submitButton.disabled = false;
    return;
  }
  var submitButton = document.getElementById("modal-submit");
  if (submitButton) submitButton.disabled = true;
  try {
    var result = await api("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (result && result.__editCollected) return;
    _discardSavedWizardDraft();
    closeModal();
    await refreshJobs();
    if (result && result.job_id) await selectJob(result.job_id);
  } catch (err) {
    window.alert((t("modal.submit_failed") || "提交失败") + ": " + formatSubmitError(err));
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
}

// Single submit entry: the footer #modal-submit dispatches to the editor's
// check-and-submit chain while a draft is open, otherwise the normal create
// flow. The editor never adds a second submit button.
function handleModalSubmit() {
  if (window.ACPJobEditor && ACPJobEditor.isActive()) {
    return ACPJobEditor.checkAndSubmit();
  }
  if (createWizardStep < 3) {
    setCreateWizardStep(createWizardStep + 1);
    return;
  }
  return submitJobModal();
}

async function candidateApi(path, options) {
  // FE-07: delegate to the unified client transport (same pattern as
  // structure_source_picker._apiV2). ACPClient is resolved lazily at call
  // time so module load order (wizard.js before transport.js) keeps working.
  var opts = Object.assign({}, options || {});
  // The pre-migration bare fetch had NO timeout: timeoutMs 0 explicitly
  // disables the transport's own timer (no 8s default introduced here).
  opts.timeoutMs = 0;
  try {
    var result = await window.ACPClient.transport.request("/api/v2" + path, opts);
    // Legacy fallback: pre-migration `response.json().catch(() => ({}))`
    // mapped ANY non-JSON 2xx body (incl. 204 empty -> "") to {}. transport
    // returns text() for those; restore the same {} shape for consumers.
    if (result == null || typeof result !== "object") return {};
    return result;
  } catch (err) {
    // Legacy message adapters (transport output untouched; same pattern as
    // structure_source_picker._apiV1): pre-migration candidateApi built its
    // message from the parsed body — string detail verbatim, otherwise
    // JSON.stringify(detail || body) — NOT the transport's "loc: msg"
    // flattening / "HTTP N" fallback. Re-derive the exact legacy text from
    // the transport-attached err.body (err.status already set by transport;
    // err.body stays undefined when the error body is not valid JSON — the
    // legacy `.catch(() => ({}))` parse fallback is mirrored by `|| {}`).
    if (err && typeof err.status === "number") {
      var legacyBody = err.body || {};
      var detail = legacyBody.detail;
      err.message = (typeof detail === "string") ? detail : JSON.stringify(detail || legacyBody);
    }
    throw err;
  }
}

async function submitJobModal() {
  ensureWizardWorkflowValid();
  var trashedInputs = wizardStructures.filter(function(structure) {
    return structure && structure.usage_status === "trash";
  });
  if (trashedInputs.length) {
    renderLoadedStructuresList();
    throw new Error("有 " + trashedInputs.length + " 个已选候选被移入垃圾箱；请先'从本次输入移除'或'恢复并保留'。");
  }
  var notReadyInputs = wizardStructures.filter(function(s) {
    return s && (s.geometry_state === "pending" || s.geometry_state === "loading" || s.geometry_state === "failed");
  });
  if (notReadyInputs.length) {
    renderLoadedStructuresList();
    throw new Error(t("wizard.block_geometry_not_ready", { count: notReadyInputs.length }) || ("有 " + notReadyInputs.length + " 个输入几何未就绪，请等待加载完成或移除后再提交。"));
  }
  var versionChangedInputs = wizardStructures.filter(function(s) {
    return s && s.version_changed;
  });
  if (versionChangedInputs.length) {
    renderLoadedStructuresList();
    throw new Error(t("wizard.block_version_changed", { count: versionChangedInputs.length }) || ("有 " + versionChangedInputs.length + " 个输入版本已变化，请重新确认后再提交。"));
  }
  var stageWf = wizardState.workflow && wizardState.workflow.id;
  if (stageWf === "tsmode") {
    if (window.ACPTsmodeEditor && typeof window.ACPTsmodeEditor.open === "function") {
      window.ACPTsmodeEditor.open({});
    }
    return;
  }
  if (stageWf === "PESsearch") {
    await submitPESsearchTask();
    return;
  }
  // Downstream stage workflows submit ONE job from
  // the §8 artifact reference — no structure parsing happens for them.
  var _stageWfEntry = workflowCatalogCache.find(function(w) { return w.id === stageWf; });
  if (_stageWfEntry && _stageWfEntry.requires_source) {
    await submitStageWorkflowJob(stageWf);
    return;
  }

  if (!wizardStructures.length) {
    jobFormValidationFailed(t("modal.parse_first") || "\u8bf7\u5148\u89e3\u6790\u7ed3\u6784");
    return;
  }

  var wfEntry = workflowCatalogCache.find(function(w) { return w.id === wizardState.workflow.id; });
  if (wfEntry && wfEntry.status === "planned") {
    jobFormValidationFailed(t("modal.workflow_not_available") || "\u8be5\u5de5\u4f5c\u6d41\u5c1a\u672a\u63a5\u5165\u540e\u7aef\uff0c\u65e0\u6cd5\u63d0\u4ea4");
    return;
  }

  var workflow = wizardState.workflow.id;
  if (workflow === "irc") {
    if (wizardStructures.length !== 1) {
      window.alert("IRC 每次只能选择一个已验证的 TS 任务结果");
      return;
    }
    var selectedTsSource = String((wizardStructures[0].source_ref && wizardStructures[0].source_ref.source_id) || wizardStructures[0].source_id || "").trim();
    try {
      await api("/irc/ts-source?source_id=" + encodeURIComponent(selectedTsSource));
    } catch (error) {
      window.alert("所选结构不可用于 IRC：" + (error.message || String(error)));
      return;
    }
  }
  var nproc = parseInt(document.getElementById("modal-nproc").value, 10) || 4;
  var mem = ACPMemoryInput.read("modal-mem", "modal-mem-unit");
  if (!mem) return;
  var remark = String(document.getElementById("modal-remark").value || "").trim();
  // Task name = middle segment of the <molecule>_<task>_<remark> dir layout.
  // Client-side mirror of the server's _sanitize_component: drop path-hostile
  // chars, collapse whitespace runs to "_". Empty → field omitted from every
  // payload so the backend default (task_name := workflow id) applies.
  var taskName = String(document.getElementById("modal-task-name").value || "").trim()
    .replace(/[/\\:*?"<>|]/g, "")
    .replace(/\s+/g, "_")
    .replace(/^_+|_+$/g, "");
  var parallelism = Math.max(1, parseInt(document.getElementById("modal-parallelism").value, 10) || 1);
  var batchId = "batch_" + Date.now().toString(36);
  var firstMoleculeName = structureMoleculeName(wizardStructures[0]) || "mol_1";
  var censoStage = (wizardState.method.stages || {}).censo || {};
  var threshold = parseFloat(censoStage.refinement_threshold);
  if (isNaN(threshold) || threshold <= 0 || threshold > 1) threshold = 0.99;

  // NMR: single job carrying all candidates + the experimental spectrum.
  // DevDoc §11.1 — breaks the "one structure = one job" convention.
  if (workflow === "nmr") {
    var nmrStatus = document.getElementById("nmr-experiment-status");
    var nmrExperiment;
    if (typeof nmrExperimentMode !== "undefined" && nmrExperimentMode === "bruker") {
      // P3: Bruker raw-data zip uploaded via /uploads?parse=false.
      if (!nmrBrukerAsset || !nmrBrukerAsset.upload_id) {
        if (nmrStatus) nmrStatus.textContent = t("modal.nmr_bruker_required") || "需上传 Bruker zip";
        jobFormValidationFailed(t("modal.nmr_bruker_required") || "请先上传 Bruker zip");
        return;
      }
      nmrExperiment = {
        mode: "bruker",
        spectrum_asset_id: nmrBrukerAsset.upload_id,
        filename: nmrBrukerAsset.filename,
        project_id: nmrBrukerAsset.project_id,
      };
    } else {
      var expText = (document.getElementById("nmr-experiment-text").value || "").trim();
      if (!expText) {
        if (nmrStatus) nmrStatus.textContent = t("modal.nmr_spectrum_required") || "需实验谱";
        jobFormValidationFailed(t("modal.nmr_spectrum_required") || "NMR 工作流需要实验谱文本");
        return;
      }
      nmrExperiment = { mode: "assigned", content: expText };
    }
    var nmrCandidates = [];
    var chargeVal = parseInt(document.getElementById("modal-charge").value, 10) || 0;
    var multVal = parseInt(document.getElementById("modal-mult").value, 10) || 1;
    for (var ni = 0; ni < wizardStructures.length; ni++) {
      var ns = wizardStructures[ni];
      var np;
      if (ns.source_type === "smiles" && ns.smiles) {
        np = { source_type: "smiles", source: ns.smiles };
      } else if (ns.normalized_path) {
        np = { source_type: "structure_asset", asset_id: ns.asset_id, source: ns.normalized_path };
      } else if (ns.smiles) {
        np = { source_type: "smiles", source: ns.smiles };
      } else if (ns.xyz) {
        np = { source_type: "xyz_text", source: ns.xyz };
      } else {
        continue;
      }
      np.charge = chargeVal;
      np.multiplicity = multVal;
      nmrCandidates.push(np);
    }
    if (!nmrCandidates.length) {
      jobFormValidationFailed(t("modal.parse_first") || "请先解析结构");
      return;
    }
    var nmrCleanedLevels = {};
    var nmrWizStages = wizardState.method.stages || {};
    Object.keys(nmrWizStages).forEach(function(lid) {
      var st = nmrWizStages[lid];
      if (!st || !st.engine || st._disabled === true) return;
      nmrCleanedLevels[lid] = JSON.parse(JSON.stringify(st));
    });
    var nmrMethod = {
      schema_id: wizardState.workflow.schema_id || "nmr",
      profile_id: wizardState.method.profile_id,
      levels: nmrCleanedLevels,
    };
    var nmrPid = wizardState.method.profile_id;
    if (nmrPid && nmrPid !== "__custom__") nmrMethod.preset = nmrPid;
    // hoist nuclei + TMS + boltzmann_temp from the GIAO level for the runner
    var giaoaLevel = nmrCleanedLevels.giaoa || {};
    ["nuclei", "boltzmann_temp", "tms_shielding_h", "tms_shielding_c", "functional", "basis"].forEach(function(k) {
      if (giaoaLevel[k] !== undefined && giaoaLevel[k] !== "") nmrMethod[k] = giaoaLevel[k];
    });
    if (giaoaLevel.solvent && String(giaoaLevel.solvent_model || "cpcm").toLowerCase() !== "none") {
      nmrMethod.solvent = giaoaLevel.solvent;
    }
    var nmrBody = {
      workflow: "nmr",
      molecule_name: firstMoleculeName,
      remark: remark,
      input: {
        source_type: "candidates",
        candidates: nmrCandidates,
        experiment: nmrExperiment,
      },
      method: nmrMethod,
      resources: { nproc: nproc, mem: mem, parallelism: parallelism },
    };
    // P2: diastereomer enumeration (single candidate only). When the user
    // enables --enumerate, the backend expands the one candidate into its
    // full diastereomer set before the per-candidate pipeline runs.
    var enumToggle = document.getElementById("nmr-enumerate-toggle");
    if (enumToggle && enumToggle.checked && nmrCandidates.length === 1) {
      nmrBody.input.enumerate = true;
      var centersVal = (document.getElementById("nmr-enumerate-centers").value || "").trim();
      if (centersVal) nmrBody.input.stereocenters = centersVal;
    }
    var nmrProjectId = document.getElementById("modal-project-select").value;
    if (nmrProjectId) nmrBody.project_id = nmrProjectId;
    if (taskName) nmrBody.task_name = taskName;
    applyNodeSelectionToBody(nmrBody);
    try {
      var nmrResult = await api("/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(nmrBody),
      });
      if (nmrResult && nmrResult.__editCollected) return;
      _discardSavedWizardDraft();
      closeModal();
      await refreshJobs();
      await selectJob(nmrResult.job_id);
    } catch (err) {
      window.alert((t("modal.submit_failed") || "提交失败") + ": " + formatSubmitError(err));
    }
    return;
  }

  // Mechanism: single job carrying reactant + optional product / TS guess +
  // route coordinate plans. Breaks the "one structure = one job" convention
  // like NMR — reactant/product/ts_guess belong to ONE study, not N jobs.
  if (workflow === "mechanism") {
    var reactionState = ensureReactionDefinitionState();
    var chargeVal = parseInt(document.getElementById("modal-charge").value, 10) || 0;
    var multVal = parseInt(document.getElementById("modal-mult").value, 10) || 1;

    if (reactionState.locked !== true || !reactionState.lockedPayload) {
      window.alert(t("mech.definition.lock_required") || "机理任务提交前必须先确认并锁定反应定义");
      return;
    }
    var mechReactant = reactionState.lockedPayload.reactant || getMechanismStructurePayload(reactionState.reactant, chargeVal, multVal);
    if (!mechReactant) {
      window.alert(t("modal.parse_first") || "请先解析反应物结构");
      return;
    }
    var mechProduct = reactionState.lockedPayload.product || null;
    var mechTsGuess = reactionState.lockedPayload.ts_guess || null;

    var mechResolvedStrategy = (typeof mechStrategy !== "undefined" && mechStrategy)
      ? mechStrategy : "guided-scan";
    var mechSpecs = Array.isArray(mechCoordinatePlans) ? mechCoordinatePlans : [];
    var mechHasDriveCoords = hasMechanismDriveCoordinate(mechSpecs);
    var mechPid = wizardState.method.profile_id;
    var mechSelectedProfile = (mechPid && mechPid !== "__custom__")
      ? getMethodProfileById(mechPid)
      : null;
    if (mechSelectedProfile) syncMechanismPanelFromProfile(mechSelectedProfile);

    if (mechResolvedStrategy === "rph-reverse" && !mechProduct) {
      window.alert(t("mech.product_required") || "rph-reverse 需要提供产物结构");
      return;
    }
    if (mechResolvedStrategy === "direct-ts" && !mechTsGuess) {
      window.alert(t("mech.ts_guess_required") || "direct-ts 需要先提供 TS 初猜结构");
      return;
    }
    if (mechResolvedStrategy === "direct-ts" && mechSpecs.length && !mechHasDriveCoords) {
      window.alert(t("mech.drive_required") || "direct-ts 提交的反应坐标中至少需要一个包含原子编号的 drive 坐标");
      return;
    }
    if (mechResolvedStrategy === "guided-scan" || mechResolvedStrategy === "rph-reverse") {
      if (!mechSpecs.length) {
        window.alert(t("mech.coords_required") || "该机制策略需要至少一个反应坐标；请先添加 drive 坐标");
        return;
      }
      if (!mechHasDriveCoords) {
        window.alert(t("mech.drive_required") || "guided-scan / rph-reverse 至少需要一个包含原子编号的 drive 坐标");
        return;
      }
    }

    var mechRoutes = buildMechanismRoutes();
    if (mechResolvedStrategy === "direct-ts" && !mechRoutes.length) {
      window.alert(t("mech.route_build_failed") || "无法为 direct-ts 构建有效的 TS 路线，请确认反应物结构已正确解析");
      return;
    }

    var mechCleanedLevels = {};
    var mechWizStages = wizardState.method.stages || {};
    Object.keys(mechWizStages).forEach(function(lid) {
      var st = mechWizStages[lid];
      if (!st || !st.engine || st._disabled === true) return;
      var copy = JSON.parse(JSON.stringify(st));
      delete copy._disabled;
      mechCleanedLevels[lid] = copy;
    });

    if (!mechStudyId) mechStudyId = generateMechanismStudyId();
    var mechMethod = {
      schema_id: wizardState.workflow.schema_id || "mechanism",
      mechanism_schema_version: 2,
      profile_id: wizardState.method.profile_id,
      study_id: mechStudyId,
      conformer_mode: mechConformerMode || "censo-lite",
      max_elementary_steps: mechMaxSteps >= 1 ? mechMaxSteps : 3,
      promotion_policy: mechPromotionPolicy || "all_confirmed",
      scan_points: mechScanPoints >= 5 ? mechScanPoints : 21,
      irc_points: mechIrcPoints >= 4 ? mechIrcPoints : 30,
      levels: mechCleanedLevels,
    };
    if (mechPid && mechPid !== "__custom__") {
      mechMethod.preset = mechPid;
    } else {
      if (typeof mechStrategy !== "undefined" && mechStrategy) mechMethod.strategy = mechStrategy;
      if (typeof mechFidelity !== "undefined" && mechFidelity) mechMethod.fidelity = mechFidelity;
    }

    var mechBody = {
      workflow: "mechanism",
      molecule_name: firstMoleculeName,
      remark: remark,
      input: {
        source_type: "mechanism",
        reactant: mechReactant,
        product: mechProduct,
        ts_guess: mechTsGuess,
        routes: mechRoutes,
      },
      method: mechMethod,
      resources: { nproc: nproc, mem: mem, parallelism: parallelism },
    };
    var mechProjectId = document.getElementById("modal-project-select").value;
    if (mechProjectId) mechBody.project_id = mechProjectId;
    if (taskName) mechBody.task_name = taskName;
    applyNodeSelectionToBody(mechBody);
    try {
      var mechResult = await api("/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(mechBody),
      });
      // Mechanism study link retired — write endpoints return 410 Gone.
      _discardSavedWizardDraft();
      closeModal();
      await refreshJobs();
      await selectJob(mechResult.job_id);
    } catch (err) {
      window.alert((t("modal.submit_failed") || "提交失败") + ": " + formatSubmitError(err));
    }
    return;
  }

  // BatchOptimize is a UI-level batch: every parsed structure becomes its
  // own scheduler job.  batch_id/index/total link the independent jobs for
  // progress display without merging their lifecycle or result directories.
  var batchBodies = [];
  for (var i = 0; i < wizardStructures.length; i++) {
    var s = wizardStructures[i];
    var moleculeName = String(s.name || ("mol_" + (i + 1))).trim() || ("mol_" + (i + 1));

    // Scan: validate the coordinate fields against THIS structure (the
    // final submitted atom order — a preview atom_count never stands in
    // for another input) before any body is queued for submission.
    var scanParts = null;
    if (workflow === "scan") {
      scanParts = buildScanWizardBodyParts(scanSelectionFieldValues(), s);
      if (!scanParts.ok) {
        var scanMsg = (t("modal.scan_invalid") || "Invalid scan parameters") +
          " (" + moleculeName + "): " + scanWizardErrorMessage(scanParts.code);
        setScanSelectionError(scanParts.code);
        jobFormValidationFailed(scanMsg);
        return;
      }
    }

    var inputPayload;
    if (scanParts) {
      inputPayload = { source_type: "xyz_text", source: String(s.xyz || s.xyz_text || "") };
    } else if (s.source_type === "smiles" && s.smiles) {
      inputPayload = { source_type: "smiles", source: s.smiles };
    } else if (s.normalized_path) {
      inputPayload = { source_type: "structure_asset", asset_id: s.asset_id, source: s.normalized_path };
    } else if (s.smiles) {
      inputPayload = { source_type: "smiles", source: s.smiles };
    } else if (s.xyz) {
      inputPayload = { source_type: "xyz_text", source: s.xyz };
    } else {
      continue;
    }
    inputPayload.charge = parseInt(document.getElementById("modal-charge").value, 10) || 0;
    inputPayload.multiplicity = parseInt(document.getElementById("modal-mult").value, 10) || 1;
    if (s.source_ref) inputPayload.source_ref = s.source_ref;

    var cleanedLevels = {};
    var wizStages = wizardState.method.stages || {};
    Object.keys(wizStages).forEach(function(lid) {
      var st = wizStages[lid];
      if (!st || !st.engine || st._disabled === true) return;
      var copy = JSON.parse(JSON.stringify(st));
      delete copy._disabled;
      cleanedLevels[lid] = copy;
    });

    var methodPayload = {
      schema_id: wizardState.workflow.schema_id || "",
      profile_id: wizardState.method.profile_id,
      levels: cleanedLevels
    };
    if (workflow === "irc") {
      var tsSourceId = String((s.source_ref && s.source_ref.source_id) || s.source_id || "").trim();
      if (!tsSourceId || tsSourceId.indexOf("job_") !== 0) {
        throw new Error("IRC 只能选择已完成 TS 计算任务的正式结果结构");
      }
      inputPayload = { source_id: tsSourceId, directions: ["forward", "reverse"] };
      var ircSettings = cleanedLevels.irc || {};
      methodPayload = { maxpoints: ircSettings.maxpoints || 100, step: ircSettings.step || 0.1 };
    }
    if (wizardState.method.fields) methodPayload.fields = wizardState.method.fields;
    if (scanParts) {
      // 0-based wire coordinates (converted exactly once in the builder) +
      // the 1-based display mirror kept in the scan level for edit-draft
      // hydration round-trips.
      inputPayload.scan_coordinates = scanParts.input.scan_coordinates;
      methodPayload.scan_points = scanParts.method.scan_points;
      methodPayload.levels = methodPayload.levels || {};
      methodPayload.levels.scan = Object.assign({}, methodPayload.levels.scan || {}, scanParts.mirror);
    }
    if (workflow === "BatchOptimize") {
      applyBatchOptimizeMethodFields(methodPayload);
    }
    if (workflow === "Confsearch") {
      // Confsearch: hoist the protocol axis fields from the wizard's
      // confsearch level into the method top level (runner emits them as
      // CLI flags via confsearch_method_flags). profile_id doubles as the
      // protocol default when it names a protocol.
      var csStage = cleanedLevels.confsearch || {};
      if (csStage.protocol) methodPayload.protocol = csStage.protocol;
      if (csStage.confsearch_profile) methodPayload.confsearch_profile = csStage.confsearch_profile;
      if (csStage.refinement_policy) methodPayload.refinement_policy = csStage.refinement_policy;
      if (!methodPayload.protocol && wizardState.method.profile_id &&
          wizardState.method.profile_id !== "__custom__") {
        methodPayload.protocol = wizardState.method.profile_id;
      }
      if (cleanedLevels.censo && cleanedLevels.censo.refinement_threshold) {
        methodPayload.levels = methodPayload.levels || {};
        methodPayload.levels.refinement_threshold = cleanedLevels.censo.refinement_threshold;
      }
      var csSolvSources = [cleanedLevels.refinement_sp, cleanedLevels.dft_opt];
      for (var csvi = 0; csvi < csSolvSources.length; csvi++) {
        var csLv = csSolvSources[csvi];
        if (csLv && csLv.solvent && String(csLv.solvent_model || "none").toLowerCase() !== "none") {
          methodPayload.solvent = csLv.solvent;
          break;
        }
      }
    }
    if (workflow === "ensemble" || workflow === "energy" || workflow === "xtbmd_censo_energy") {
      var pid = wizardState.method.profile_id;
      if (pid && pid !== "__custom__") methodPayload.preset = pid;
      // energyLike: energy + xtbmd_censo_energy share the preset /
      // threshold / rank1-only semantics; an absent dft_opt panel maps
      // to --no-opt (cheap path) in both.
      var energyLike = (workflow === "energy" || workflow === "xtbmd_censo_energy");
      if (energyLike && !cleanedLevels.dft_opt) {
        // dft_opt panel disabled → cheap path (--no-opt semantics)
        methodPayload.no_opt = true;
      }
      if (energyLike) {
        methodPayload.threshold = threshold;
      }
      // rank1-only choice: for energy the CLI default is rank1-only and
      // the explicit choice is always submitted; for xtbmd_censo_energy
      // the CLI default is the full ensemble, so the checkbox value is
      // forwarded (runner emits --rank1-only only when true).
      if (energyLike) {
        methodPayload.rank1_only = !!wizardState.method.rank1_only;
      }
      if (workflow === "xtbmd_censo_energy") {
        // MD / batch-opt / ISOSTAT control group → method top level
        // (runner emits them as CLI flags). The field set is derived
        // from the workflow schema's control levels (xtb_md / xtb_opt /
        // isostat), i.e. the catalog FIELD_DEFINITIONS-backed fields —
        // new fields registered in the catalog flow through automatically
        // (E7: catalog is the single source of truth; advanced only
        // decides the rendering section, not whether the value is
        // submitted).
        var wfSchema = (methodCatalogCache.method_schemas || {})[wizardState.workflow.schema_id] || {};
        var ctrlLevelIds = ["xtb_md", "xtb_opt", "isostat"];
        var ctrlKeys = [];
        (wfSchema.method_levels || []).forEach(function(lvDef) {
          if (ctrlLevelIds.indexOf(lvDef.level_id) < 0) return;
          (lvDef.fields || []).forEach(function(fn) {
            if (ctrlKeys.indexOf(fn) < 0) ctrlKeys.push(fn);
          });
        });
        ctrlKeys.forEach(function(key) {
          for (var lid in cleanedLevels) {
            var lv = cleanedLevels[lid];
            if (lv && lv[key] !== undefined && lv[key] !== "") {
              methodPayload[key] = lv[key];
              break;
            }
          }
        });
      }
      // strip refinement_threshold from cleanedLevels.censo to avoid
      // duplication with the --threshold CLI path in the runner
      if (cleanedLevels.censo) {
        delete cleanedLevels.censo.refinement_threshold;
      }
      var solvSources = [cleanedLevels.refinement_sp, cleanedLevels.dft_opt];
      for (var svi = 0; svi < solvSources.length; svi++) {
        var lvSt = solvSources[svi];
        if (lvSt && lvSt.solvent && String(lvSt.solvent_model || "none").toLowerCase() !== "none") {
          methodPayload.solvent = lvSt.solvent;
          break;
        }
      }
    }

    var body = {
      workflow: workflow,
      molecule_name: moleculeName,
      remark: remark,
      input: inputPayload,
      method: methodPayload,
      resources: {
        nproc: nproc,
        mem: mem,
        parallelism: parallelism,
        batch_id: batchId,
        batch_index: i,
        batch_total: wizardStructures.length,
      }
    };
    var modalProjectId = document.getElementById("modal-project-select").value;
    if (modalProjectId) body.project_id = modalProjectId;
    if (taskName) body.task_name = taskName;
    applyNodeSelectionToBody(body);

    batchBodies.push(body);
  }
  try {
    await submitJobBatch(batchBodies);
    if (window.ACPJobEditor && ACPJobEditor.isCollecting()) return;
    _discardSavedWizardDraft();
    closeModal();
  } catch (error) {
    window.alert(t("modal.submit_failed", { message: formatSubmitError(error) }));
  }
  await refreshJobs();
}


// ── Method defaults initialization ──
async function initializeMethodDefaults() {
  await Promise.all([loadWorkflowCatalog(), loadMethodCatalog()]);
  if (wizardState.method.profile_id) { updateConfigCards(); return; }
  try {
    // Catalog-driven default: never trust a hardcoded/legacy workflow id.
    // resolveDefaultWorkflow() picks the current id when it is a visible
    // active entry, otherwise Confsearch (flagship conformer search),
    // otherwise the first active entry — so the first open always shows
    // the current-generation protocol schema.
    var wfEntry = resolveDefaultWorkflow(wizardState.workflow.id);
    if (wfEntry) {
      wizardState.workflow = {
        id: wfEntry.id,
        label: wfEntry.label,
        schema_id: canonicalMethodSchemaId(wfEntry.id, wfEntry.method_schema_id)
      };
    }
    var schemaId = (wfEntry && wizardState.workflow.schema_id) || "";
    var schema = (methodCatalogCache.method_schemas || {})[schemaId];
    if (schema) {
      wizardState.workflow.schema_id = schemaId;
      var p = pickDefaultProfile(schema);
      if (p) {
        wizardState.method.profile_id = p.profile_id;
        wizardState.method.profile_label = currentLang === "zh-CN"
          ? (p.label_zh || p.label)
          : p.label;
        wizardState.method.stages = JSON.parse(JSON.stringify(p.levels || {}));
        if (wizardState.workflow && wizardState.workflow.id === "mechanism") syncMechanismPanelFromProfile(p);
      } else {
        wizardState.method.profile_id = "";
        var firstLevel = (schema.method_levels || [])[0];
        wizardState.method.profile_label = firstLevel ? (firstLevel.label_zh || firstLevel.label || firstLevel.level_id) : "";
        wizardState.method.stages = {};
        if (wizardState.workflow && wizardState.workflow.id === "mechanism") syncMechanismPanelFromProfile(null);
      }
    }
    updateConfigCards();
  } catch (e) {
    console.error("method catalog init failed:", e);
  }

}

