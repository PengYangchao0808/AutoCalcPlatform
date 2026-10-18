/*
 * ACP Workbench — wizard slice S4: 提交/编辑重算 (submit / edit-recalc).
 *
 * Extracted from features/wizard.js per plan todo 20 (boundary frozen by
 * task-16 evidence §1.5 S4 / §2.3 request+cleanup ownership / §3.3(c)(d)
 * payload tables / §4 preserved behaviors). This module is the SINGLE owner
 * of the submit slice:
 *   - the single submit entry `handleModalSubmit` (editor checkAndSubmit when
 *     active, otherwise the create-flow step gate and `submitJobModal`);
 *   - every workflow submit branch (`submitPESsearchTask` / stage chains via
 *     `submitStageWorkflowJob` / NMR / mechanism / the per-structure batch
 *     loop) and the batch fan-out `submitJobBatch`;
 *   - `jobFormValidationFailed` (409/422 + collecting-vs-alert branch);
 *   - `buildPESProtocolFromMethod` (PES payload builder);
 *   - `initializeMethodDefaults` (catalog-driven default profile bootstrap).
 *
 * Edit-recalc link interface: every branch funnels `POST /jobs` through the
 * page `api()` chokepoint (js/workbench/compat/api.js), which — while an ACP
 * job-editor session is active — routes the collected payload to
 * `ACPJobEditor.interceptJobCreate` instead of creating a duplicate task
 * (edit-recalc: runPreview → /edit-recalculate/preview, finalSubmit →
 * /edit-recalculate). No submit payload is changed here; no field is added;
 * a 202 is still not treated as completion.
 *
 * Load order: after js/workbench/compat/api.js, js/client/wizard-endpoints.js
 * and the S1/S2/S3 wizard slice modules, before features/wizard.js (whose
 * same-name delegates call into window.ACPWizardSubmitEdit) and bootstrap.js.
 */
window.ACPWizardSubmitEdit = (function () {
"use strict";

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

return {
  submitStageWorkflowJob: submitStageWorkflowJob,
  jobFormValidationFailed: jobFormValidationFailed,
  submitJobBatch: submitJobBatch,
  buildPESProtocolFromMethod: buildPESProtocolFromMethod,
  submitPESsearchTask: submitPESsearchTask,
  handleModalSubmit: handleModalSubmit,
  submitJobModal: submitJobModal,
  initializeMethodDefaults: initializeMethodDefaults,
};
})();
