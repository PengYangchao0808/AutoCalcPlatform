/*
 * ACP Workbench — wizard slice S3: 草稿/表单 (draft / form).
 *
 * Extracted from features/wizard.js per plan todo 19 (boundary frozen by the
 * task-16 evidence §1.4 / §2.2 / §2.3; preview/validate halves were already
 * moved to preview-validate.js by todo 18). This module is the SINGLE state
 * owner of the draft/form slice:
 *   wizardState, wizardDraft, wizardDraftMeta, wizardDraftSaveQueue,
 *   wizardDraftAutosaveTimer, wizardDraftEpoch, createWizardStep,
 *   nmrExperimentMode, nmrBrukerAsset, ircSourcePreviewSequence
 * plus the read-only catalog/workflow helpers (resolveDefaultWorkflow,
 * canonicalMethodSchemaId, pickDefaultProfile, ensureWizardWorkflowValid) and
 * the shared method-config shell (`openMethodConfig` — 公共字段/工作流特有面板/
 * 预览请求接线/监听生命周期); the server preview + validation round-trips stay
 * in preview-validate.js (S2 factories wired through window.ACPWizardPreviewValidate).
 *
 * Preserved contracts (task-16 §4):
 *   - P-1 draft version / optimistic lock (`expected_revision`, epoch guard;
 *     stale POST deletes the orphan, stale PUT never deletes the user's draft).
 *   - P-2 draft source version revalidation (`version_changed`, 404).
 *   - P-3 explicit 0/false/null (`hasOwnProperty` restore, never collapse falsy).
 *   - P-10 batch_role_defaults.js is the only batch-role default source.
 *   - P-12 draft autosave 1500 ms + hidden-modal guard (H-2 hardened below).
 *   - legacy draft reader (`_restoreWizardDraft`) keeps old snapshots loadable.
 *
 * H-2 fix (task-16 §7): `_scheduleWizardDraftAutosave` now re-checks the draft
 * epoch and the modal visibility inside the timer callback, and `closeModal`
 * clears the timer + bumps the epoch. A late autosave scheduled by session A
 * therefore can never write the DOM of a reopened session B under A's meta.
 *
 * Load order: after js/workbench/features/wizard/preview-validate.js, before
 * workbench/features/wizard.js. The old global function names stay callable via
 * thin same-name delegates left in features/wizard.js.
 */
window.ACPWizardDraftForm = (function () {
  "use strict";

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

// Catalog caches (F-④-1 resolution, T22): this module is the SINGLE owner —
// declaration and sole writer. job_editor.js / reaction-builder.js / wizard
// slices consume the live global views below; no other file assigns them.
let workflowCatalogCache = [];
let methodCatalogCache = {};
Object.defineProperty(window, "workflowCatalogCache", {
  get: function() { return workflowCatalogCache; },
  set: function(value) { workflowCatalogCache = value; },
  configurable: true
});
Object.defineProperty(window, "methodCatalogCache", {
  get: function() { return methodCatalogCache; },
  set: function(value) { methodCatalogCache = value; },
  configurable: true
});

// Cross-file compatibility (migration): job_editor.js / reaction-builder.js /
// bootstrap.js read these bare globals. The module stays the single owner; the
// exported objects/properties are live views, not a second source of truth.
window.wizardState = wizardState;
Object.defineProperty(window, "createWizardStep", {
  get: function() { return createWizardStep; },
  set: function(value) { createWizardStep = value; },
  configurable: true
});

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
  var scheduledEpoch = wizardDraftEpoch;
  if (document.getElementById("job-modal").style.display === "none") return;
  if (wizardDraftAutosaveTimer) clearTimeout(wizardDraftAutosaveTimer);
  wizardDraftAutosaveTimer = setTimeout(function() {
    wizardDraftAutosaveTimer = null;
    // H-2: re-check at fire time. The modal may have closed (and possibly been
    // reopened as a NEW session) during the debounce; a late timer must never
    // capture a newer session's live DOM under the old draft meta.
    if (scheduledEpoch !== wizardDraftEpoch) return;
    var modal = document.getElementById("job-modal");
    if (!modal || modal.style.display === "none") return;
    if (window.ACPJobEditor && ACPJobEditor.isActive()) return;
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
    lastParsedStructureInput: window.ACPWizardUploadParse.getLastParsedStructureInput(),
    lastParsedUploadName: window.ACPWizardUploadParse.getLastParsedUploadName(),
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
  window.ACPWizardUploadParse.setLastParsedStructureInput(draft.lastParsedStructureInput == null ? null : draft.lastParsedStructureInput);
  window.ACPWizardUploadParse.setLastParsedUploadName(draft.lastParsedUploadName == null ? null : draft.lastParsedUploadName);
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
  window.ACPWizardUploadParse.resetAppliedParse();
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
  // H-2: end this draft session before hiding the modal — invalidate any
  // pending autosave (timer) and bump the epoch so an already-scheduled
  // callback can never later capture a freshly reopened session's live DOM.
  wizardDraftEpoch++;
  if (wizardDraftAutosaveTimer) clearTimeout(wizardDraftAutosaveTimer);
  wizardDraftAutosaveTimer = null;
  document.getElementById("job-modal").style.display = "none";
  // H-3: invalidate any in-flight parse on close so a late response cannot
  // write wizardStructures / wizardParseWarnings into a hidden or reopened
  // session (S1-owned guard).
  window.ACPWizardUploadParse.invalidatePendingParse();
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
  window.closeModal();
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
  // S1-owned guard: also used by closeModal so a late parse can never write
  // into a closed or reopened session (H-3).
  window.ACPWizardUploadParse.invalidatePendingParse();
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
    if (txt.trim() && txt !== window.ACPWizardUploadParse.getLastParsedStructureInput()) scheduleParseStructures();
  } else if (mode === "upload" && wizardUploadFile && wizardUploadFile.name !== window.ACPWizardUploadParse.getLastParsedUploadName()) {
    scheduleParseStructures();
  }
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
Object.defineProperty(window, "nmrExperimentMode", {
  get: function() { return nmrExperimentMode; },
  set: function(value) { nmrExperimentMode = value; },
  configurable: true
});
Object.defineProperty(window, "nmrBrukerAsset", {
  get: function() { return nmrBrukerAsset; },
  set: function(value) { nmrBrukerAsset = value; },
  configurable: true
});

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
  // H-4: a reopen rebuilds the level cards (mc-levels-container is emptied), so
  // any in-flight batch-config preview must be aborted before its promise can
  // write into the old detached preview node (S2 guard).
  window.ACPWizardPreviewValidate.abortBatchPreviews();
  ensureWizardWorkflowValid();
  // Draft semantics (P-fix §2⑤): every field handler mutates
  // wizardState.method directly, so snapshot it on open. Cancel/close
  // restores the snapshot; only the confirm button keeps the changes.
  var _methodDraft = JSON.parse(JSON.stringify(wizardState.method));
  function revertMethodDraft() {
    wizardState.method = _methodDraft;
    document.getElementById("method-config-modal").style.display = "none";
    // H-4: cancel/close aborts any in-flight batch-config preview (S2 guard).
    window.ACPWizardPreviewValidate.abortBatchPreviews();
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
    // Preview requests are debounced and only the latest is kept (the preview
    // timer/token state is owned by ACPWizardPreviewValidate, T18).

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

      var hessianPreview = window.ACPWizardPreviewValidate.createHessianPreview(function() {
        return { previewBox: previewBox, hint: hint, mode: mode, zh: zh, lvDef: lvDef, st: st };
      });
      function scheduleHessianPreview() {
        return hessianPreview.schedule.apply(null, arguments);
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

        // Server preview wiring (state + request/render logic owned by
        // ACPWizardPreviewValidate; the factory aborts the previous instance on
        // rebuild, and abortBatchPreviews() guards close/reopen — H-4).
        var _batchPreview = window.ACPWizardPreviewValidate.createBatchConfigPreview({
          previewFields: previewFields,
          getActiveRole: function() { return _batchRoleActive; },
          getRoles: function() { return _batchRoles; },
        });

        function _fetchBatchConfigPreview() {
          return _batchPreview.fetch.apply(null, arguments);
        }

        function _renderServerPreview(data) {
          return _batchPreview.renderServer.apply(null, arguments);
        }

        function _renderPreviewFailure(errMsg) {
          return _batchPreview.renderFailure.apply(null, arguments);
        }

        function _renderLocalSummary() {
          return _batchPreview.renderLocal.apply(null, arguments);
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

  var _validation = window.ACPWizardPreviewValidate.createValidation({ schema: schema, schemaId: schemaId });
  function scheduleValidate() {
    return _validation.schedule.apply(null, arguments);
  }

  function doValidate() {
    return _validation.validate.apply(null, arguments);
  }

  function refreshMethodValidationState() {
    return _validation.refresh.apply(null, arguments);
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

  return {
    _wizardDraftName: _wizardDraftName,
    _wizardDraftErrorText: _wizardDraftErrorText,
    _saveWizardDraft: _saveWizardDraft,
    _scheduleWizardDraftAutosave: _scheduleWizardDraftAutosave,
    _discardSavedWizardDraft: _discardSavedWizardDraft,
    _captureWizardDraft: _captureWizardDraft,
    _restoreWizardDraft: _restoreWizardDraft,
    _revalidateDraftSources: _revalidateDraftSources,
    openModal: openModal,
    closeModal: closeModal,
    renderCreateReview: renderCreateReview,
    renderStepCompactSummary: renderStepCompactSummary,
    _syncCreateStepButton: _syncCreateStepButton,
    setCreateWizardStep: setCreateWizardStep,
    closeJobModalGuarded: closeJobModalGuarded,
    openWizardDrafts: openWizardDrafts,
    startBlankWizardDraft: startBlankWizardDraft,
    setWizardInputMode: setWizardInputMode,
    updateTaskNamePlaceholder: updateTaskNamePlaceholder,
    setNmrExperimentMode: setNmrExperimentMode,
    uploadNmrBruker: uploadNmrBruker,
    stageSummaryPart: stageSummaryPart,
    renderWorkflowDetail: renderWorkflowDetail,
    renderMethodDetail: renderMethodDetail,
    refreshIrcSourceSummary: refreshIrcSourceSummary,
    updateConfigCards: updateConfigCards,
    syncBatchOptimizeProfileSummary: syncBatchOptimizeProfileSummary,
    loadWorkflowCatalog: loadWorkflowCatalog,
    loadMethodCatalog: loadMethodCatalog,
    isS2CoordinateScan: isS2CoordinateScan,
    updateS2InlineVisibility: updateS2InlineVisibility,
    canonicalMethodSchemaId: canonicalMethodSchemaId,
    pickDefaultProfile: pickDefaultProfile,
    resolveDefaultWorkflow: resolveDefaultWorkflow,
    ensureWizardWorkflowValid: ensureWizardWorkflowValid,
    applyPendingNewTask: applyPendingNewTask,
    configureStageSourcePanel: configureStageSourcePanel,
    loadDefaultMethodProfile: loadDefaultMethodProfile,
    openMethodConfig: openMethodConfig
  };
})();
