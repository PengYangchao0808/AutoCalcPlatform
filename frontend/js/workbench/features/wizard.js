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

// ── S3 草稿/表单 — extracted to features/wizard/draft-form.js (T19) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js, job_editor.js and bootstrap keep working.
function _wizardDraftName() {
  return window.ACPWizardDraftForm._wizardDraftName.apply(null, arguments);
}

function _wizardDraftErrorText() {
  return window.ACPWizardDraftForm._wizardDraftErrorText.apply(null, arguments);
}

function _saveWizardDraft() {
  return window.ACPWizardDraftForm._saveWizardDraft.apply(null, arguments);
}

function _scheduleWizardDraftAutosave() {
  return window.ACPWizardDraftForm._scheduleWizardDraftAutosave.apply(null, arguments);
}

function _discardSavedWizardDraft() {
  return window.ACPWizardDraftForm._discardSavedWizardDraft.apply(null, arguments);
}

function _captureWizardDraft() {
  return window.ACPWizardDraftForm._captureWizardDraft.apply(null, arguments);
}

function _restoreWizardDraft() {
  return window.ACPWizardDraftForm._restoreWizardDraft.apply(null, arguments);
}

function _revalidateDraftSources() {
  return window.ACPWizardDraftForm._revalidateDraftSources.apply(null, arguments);
}

function openModal() {
  return window.ACPWizardDraftForm.openModal.apply(null, arguments);
}

function closeModal() {
  return window.ACPWizardDraftForm.closeModal.apply(null, arguments);
}

function renderCreateReview() {
  return window.ACPWizardDraftForm.renderCreateReview.apply(null, arguments);
}

function renderStepCompactSummary() {
  return window.ACPWizardDraftForm.renderStepCompactSummary.apply(null, arguments);
}

function _syncCreateStepButton() {
  return window.ACPWizardDraftForm._syncCreateStepButton.apply(null, arguments);
}

function setCreateWizardStep() {
  return window.ACPWizardDraftForm.setCreateWizardStep.apply(null, arguments);
}

function closeJobModalGuarded() {
  return window.ACPWizardDraftForm.closeJobModalGuarded.apply(null, arguments);
}

function openWizardDrafts() {
  return window.ACPWizardDraftForm.openWizardDrafts.apply(null, arguments);
}

function startBlankWizardDraft() {
  return window.ACPWizardDraftForm.startBlankWizardDraft.apply(null, arguments);
}

function setWizardInputMode() {
  return window.ACPWizardDraftForm.setWizardInputMode.apply(null, arguments);
}

// ── S1 上传/解析 — extracted to features/wizard/upload-parse.js (T17) ──────
// The slice (state + functions) now lives in js/workbench/features/wizard/
// upload-parse.js, loaded immediately before this file. These same-name globals
// stay as thin delegates during migration so inline `onclick=`, bootstrap.js,
// job_editor.js and molecule-view.js callers keep working unchanged. The S1
// state (parseSeqToken / parseDebounceTimer / lastParsed*) is owned by that
// module and reached through its accessor API.
function scheduleParseStructures() {
  return window.ACPWizardUploadParse.scheduleParseStructures.apply(null, arguments);
}

function setDetectedFormatChip() {
  return window.ACPWizardUploadParse.setDetectedFormatChip.apply(null, arguments);
}

function renderPreviewWaiting() {
  return window.ACPWizardUploadParse.renderPreviewWaiting.apply(null, arguments);
}

function parseStructuresPreview() {
  return window.ACPWizardUploadParse.parseStructuresPreview.apply(null, arguments);
}

function handleParseResult() {
  return window.ACPWizardUploadParse.handleParseResult.apply(null, arguments);
}

function showParseStatus() {
  return window.ACPWizardUploadParse.showParseStatus.apply(null, arguments);
}

// ── S2 预览/校验 — extracted to features/wizard/preview-validate.js (T18) ──
// The structure-preview entry points + method validation now live in
// js/workbench/features/wizard/preview-validate.js (loaded immediately before
// this file). These same-name globals stay as thin delegates during migration
// so inline `onclick=`, bootstrap.js, job_editor.js, molecule-view.js and the
// S3 draft/form shell keep working unchanged. The preview/validate state
// (validation timer/result, hessian preview, batch-config preview) is owned by
// that module and reached through its factory API.
function renderStructurePreview() {
  return window.ACPWizardPreviewValidate.renderStructurePreview.apply(null, arguments);
}

function selectPreviewStructure() {
  return window.ACPWizardPreviewValidate.selectPreviewStructure.apply(null, arguments);
}

function _refreshWizardInputForm() {
  return window.ACPWizardPreviewValidate._refreshWizardInputForm.apply(null, arguments);
}

function escapeAttr() {
  return window.ACPWizardPreviewValidate.escapeAttr.apply(null, arguments);
}

function updateFooterSelectedCount() {
  return window.ACPWizardPreviewValidate.updateFooterSelectedCount.apply(null, arguments);
}

// Overlay controller for input list
function _openInputListOverlay() {
  return window.ACPWizardPreviewValidate._openInputListOverlay.apply(null, arguments);
}
function _closeInputListOverlay() {
  return window.ACPWizardPreviewValidate._closeInputListOverlay.apply(null, arguments);
}
function _toggleInputListOverlay() {
  return window.ACPWizardPreviewValidate._toggleInputListOverlay.apply(null, arguments);
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
  return window.ACPWizardPreviewValidate._togglePreviewAtomLabels.apply(null, arguments);
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
  return window.ACPWizardPreviewValidate.renderLoadedStructuresList.apply(null, arguments);
}

function updatePreviewActionState() {
  return window.ACPWizardPreviewValidate.updatePreviewActionState.apply(null, arguments);
}

function removeSelectedStructure() {
  return window.ACPWizardPreviewValidate.removeSelectedStructure.apply(null, arguments);
}

function clearParsedStructures() {
  return window.ACPWizardPreviewValidate.clearParsedStructures.apply(null, arguments);
}

function initPreviewViewer() {
  // T10: bounded scene instance — reuse while live, recreate only after
  // dispose. scene.create returns null when 3Dmol is missing/failed and
  // callers guard on the falsy handle (preview empty state preserved).
  if (previewViewer && !previewViewer.isDisposed()) return;
  var container = document.getElementById("structure-preview-3d");
  if (!container) return;
  previewViewer = ACPVisualization.scene.create({ container: container, options: { backgroundColor: "0x0e1419", antialias: true } });
}

function renderPreviewStructure3D() {
  return window.ACPWizardPreviewValidate.renderPreviewStructure3D.apply(null, arguments);
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

// ── S3 草稿/表单 — extracted to features/wizard/draft-form.js (T19) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js, job_editor.js and bootstrap keep working.
function updateTaskNamePlaceholder() {
  return window.ACPWizardDraftForm.updateTaskNamePlaceholder.apply(null, arguments);
}

// ── S3 草稿/表单 — extracted to features/wizard/draft-form.js (T19) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js, job_editor.js and bootstrap keep working.
function setNmrExperimentMode() {
  return window.ACPWizardDraftForm.setNmrExperimentMode.apply(null, arguments);
}

function uploadNmrBruker() {
  return window.ACPWizardDraftForm.uploadNmrBruker.apply(null, arguments);
}

// ── S3 草稿/表单 — extracted to features/wizard/draft-form.js (T19) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js, job_editor.js and bootstrap keep working.
function stageSummaryPart() {
  return window.ACPWizardDraftForm.stageSummaryPart.apply(null, arguments);
}

function renderWorkflowDetail() {
  return window.ACPWizardDraftForm.renderWorkflowDetail.apply(null, arguments);
}

function renderMethodDetail() {
  return window.ACPWizardDraftForm.renderMethodDetail.apply(null, arguments);
}

function refreshIrcSourceSummary() {
  return window.ACPWizardDraftForm.refreshIrcSourceSummary.apply(null, arguments);
}

function updateConfigCards() {
  return window.ACPWizardDraftForm.updateConfigCards.apply(null, arguments);
}

function syncBatchOptimizeProfileSummary() {
  return window.ACPWizardDraftForm.syncBatchOptimizeProfileSummary.apply(null, arguments);
}

function loadWorkflowCatalog() {
  return window.ACPWizardDraftForm.loadWorkflowCatalog.apply(null, arguments);
}

function loadMethodCatalog() {
  return window.ACPWizardDraftForm.loadMethodCatalog.apply(null, arguments);
}

function isS2CoordinateScan() {
  return window.ACPWizardDraftForm.isS2CoordinateScan.apply(null, arguments);
}

function updateS2InlineVisibility() {
  return window.ACPWizardDraftForm.updateS2InlineVisibility.apply(null, arguments);
}

function canonicalMethodSchemaId() {
  return window.ACPWizardDraftForm.canonicalMethodSchemaId.apply(null, arguments);
}

function pickDefaultProfile() {
  return window.ACPWizardDraftForm.pickDefaultProfile.apply(null, arguments);
}

function resolveDefaultWorkflow() {
  return window.ACPWizardDraftForm.resolveDefaultWorkflow.apply(null, arguments);
}

function ensureWizardWorkflowValid() {
  return window.ACPWizardDraftForm.ensureWizardWorkflowValid.apply(null, arguments);
}

function applyPendingNewTask() {
  return window.ACPWizardDraftForm.applyPendingNewTask.apply(null, arguments);
}

function configureStageSourcePanel() {
  return window.ACPWizardDraftForm.configureStageSourcePanel.apply(null, arguments);
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

// ── S4 提交/编辑重算 — extracted to features/wizard/submit-edit.js (T20) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js and job_editor.js keep working.
function submitStageWorkflowJob() {
  return window.ACPWizardSubmitEdit.submitStageWorkflowJob.apply(null, arguments);
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

// ── S3 草稿/表单 — extracted to features/wizard/draft-form.js (T19) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js, job_editor.js and bootstrap keep working.
function loadDefaultMethodProfile() {
  return window.ACPWizardDraftForm.loadDefaultMethodProfile.apply(null, arguments);
}

// ── S3 草稿/表单 — extracted to features/wizard/draft-form.js (T19) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js, job_editor.js and bootstrap keep working.
function openMethodConfig() {
  return window.ACPWizardDraftForm.openMethodConfig.apply(null, arguments);
}

// ── S4 提交/编辑重算 — extracted to features/wizard/submit-edit.js (T20) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js and job_editor.js keep working.
function jobFormValidationFailed() {
  return window.ACPWizardSubmitEdit.jobFormValidationFailed.apply(null, arguments);
}

// ── S4 提交/编辑重算 — extracted to features/wizard/submit-edit.js (T20) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js and job_editor.js keep working.
function submitJobBatch() {
  return window.ACPWizardSubmitEdit.submitJobBatch.apply(null, arguments);
}

// ── S4 提交/编辑重算 — extracted to features/wizard/submit-edit.js (T20) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js and job_editor.js keep working.
function buildPESProtocolFromMethod() {
  return window.ACPWizardSubmitEdit.buildPESProtocolFromMethod.apply(null, arguments);
}

// ── S4 提交/编辑重算 — extracted to features/wizard/submit-edit.js (T20) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js and job_editor.js keep working.
function submitPESsearchTask() {
  return window.ACPWizardSubmitEdit.submitPESsearchTask.apply(null, arguments);
}

// Single submit entry: the footer #modal-submit dispatches to the editor's
// check-and-submit chain while a draft is open, otherwise the normal create
// flow. The editor never adds a second submit button.
// ── S4 提交/编辑重算 — extracted to features/wizard/submit-edit.js (T20) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js and job_editor.js keep working.
function handleModalSubmit() {
  return window.ACPWizardSubmitEdit.handleModalSubmit.apply(null, arguments);
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

// ── S4 提交/编辑重算 — extracted to features/wizard/submit-edit.js (T20) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js and job_editor.js keep working.
function submitJobModal() {
  return window.ACPWizardSubmitEdit.submitJobModal.apply(null, arguments);
}


// ── Method defaults initialization ──
// ── S4 提交/编辑重算 — extracted to features/wizard/submit-edit.js (T20) ──
// Body moved; same-name globals stay as thin delegates so inline
// handlers, bootstrap.js and job_editor.js keep working.
function initializeMethodDefaults() {
  return window.ACPWizardSubmitEdit.initializeMethodDefaults.apply(null, arguments);
}

