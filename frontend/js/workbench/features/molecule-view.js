
function currentFrame() { return molDoc.frames[molDoc.currentFrame] || null; }
function currentAtoms() { const frame = currentFrame(); return frame ? frame.atoms : []; }

function currentAtomById(acpId) {
  return currentAtoms().find(function(atom) { return atom.id === acpId; }) || null;
}

function renderedAtomById(id) {
  return (typeof id === "number" && id >= 0 && id < currentModelAtoms.length) ? currentModelAtoms[id] : null;
}

function formatSelectionIds() {
  var sel = (window.ACPGeometryStore) ? window.ACPGeometryStore.state.selection : [];
  return sel.slice().sort(function(a, b) { return a - b; }).join(", ");
}

function syncBgIndex() {
  const nextIndex = BG_COLORS.indexOf(molDoc.bgColor);
  bgIndex = nextIndex >= 0 ? nextIndex : 0;
}

function measurementValue(type, atomIds) {
  const atoms = atomIds.map(function(atomId) { return currentAtomById(atomId); });
  if (atoms.some(function(atom) { return !atom; })) return null;
  if (type === "distance") {
    const dx = atoms[0].x - atoms[1].x;
    const dy = atoms[0].y - atoms[1].y;
    const dz = atoms[0].z - atoms[1].z;
    return Math.sqrt(dx * dx + dy * dy + dz * dz);
  }
  if (type === "angle") {
    const v1 = sub3(atoms[0], atoms[1]);
    const v2 = sub3(atoms[2], atoms[1]);
    const denom = len3(v1) * len3(v2);
    if (!denom) return null;
    const cosA = dot3(v1, v2) / denom;
    return Math.acos(Math.max(-1, Math.min(1, cosA))) * 180 / Math.PI;
  }
  if (type === "dihedral") {
    const b0 = sub3(atoms[0], atoms[1]);
    const b1 = sub3(atoms[2], atoms[1]);
    const b2 = sub3(atoms[3], atoms[2]);
    const b1len = len3(b1);
    if (!b1len) return null;
    const b1u = { x: b1.x / b1len, y: b1.y / b1len, z: b1.z / b1len };
    const proj0 = dot3(b0, b1u);
    const proj2 = dot3(b2, b1u);
    const v = { x: b0.x - (proj0 * b1u.x), y: b0.y - (proj0 * b1u.y), z: b0.z - (proj0 * b1u.z) };
    const w = { x: b2.x - (proj2 * b1u.x), y: b2.y - (proj2 * b1u.y), z: b2.z - (proj2 * b1u.z) };
    if (!len3(v) || !len3(w)) return null;
    const x = dot3(v, w);
    const y = dot3(cross3(b1u, v), w);
    return Math.atan2(y, x) * 180 / Math.PI;
  }
  return null;
}

function formatMeasurementDisplay(type, value) {
  if (value === null || value === undefined || Number.isNaN(value)) return t("common.none");
  return type === "distance" ? value.toFixed(3) + " A" : value.toFixed(1) + " deg";
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}


// ── 3Dmol viewer and measurement overlays ──
// === Unified 3Dmol viewer framing (shared by ALL viewers) ===
function frame3DViewer(viewer3dmol, options) {
  return ACPVisualization.framing.frame3DViewer(viewer3dmol, options);
}
function waitForContainerStable(container, callback, options) {
  return ACPVisualization.framing.waitForContainerStable(container, callback, options);
}
function scheduleViewerFraming(container, framingState, getViewer, options) {
  return ACPVisualization.framing.scheduleViewerFraming(container, framingState, getViewer, options);
}
function disposeViewerFraming(framingState) {
  return ACPVisualization.framing.disposeViewerFraming(framingState);
}

function initViewer() {
  // T8: reuse the live bounded instance — repeated init must not leak a
  // second viewer/interval (scene dispose is idempotent; a disposed holder
  // is replaced with a fresh instance below).
  if (viewer && typeof viewer.isDisposed === "function" && !viewer.isDisposed()) return true;
  if (!ACPVisualization.mol3d.isAvailable()) {
    const empty = document.getElementById("viewer-empty");
    if (empty) {
      empty.style.display = "grid";
      var library = document.getElementById("viewer-library");
      empty.textContent = t(library && library.dataset.failed ? "viewer.load_failed" : "viewer.loading");
    }
    return false;
  }
  const container = document.getElementById("viewer-3d");
  // T8: the holder receives the RESTRICTED scene instance (never a native
  // 3Dmol viewer); scene.create owns the underlying viewer internally.
  viewer = ACPVisualization.scene.create({ container: container, options: { backgroundColor: molDoc.bgColor, antialias: true } });
  if (!viewer) return false;
  // Graceful degradation (audit F09): a lost WebGL context would otherwise
  // leave a silently blank canvas area. Capture-phase listening on the
  // container survives 3Dmol's canvas swaps (resize recreates the element).
  if (container && !container.dataset.contextLostHook) {
    container.dataset.contextLostHook = "1";
    container.addEventListener("webglcontextlost", function() {
      const empty = document.getElementById("viewer-empty");
      if (empty) {
        empty.style.display = "grid";
        empty.textContent = t("viewer.render_failed");
      }
    }, true);
  }
  // Resize handled by the global rAF-throttled listener → applyLayout → viewer.resize
  return true;
}

// The optional viewer must never delay DOMContentLoaded or task management.
var viewerLibraryScript = document.getElementById("viewer-library");
if (viewerLibraryScript) {
  viewerLibraryScript.addEventListener("load", function() {
    if (molDoc.frames.length) renderMolDoc({ zoomTo: true });
  });
  viewerLibraryScript.addEventListener("error", function() {
    viewerLibraryScript.dataset.failed = "true";
    if (molDoc.frames.length) initViewer();
  });
}

function showEmpty() {
  viewerModelLoaded = false;
  document.getElementById("viewer-empty").style.display = "grid";
  document.getElementById("viewer-coming-soon").style.display = "none";
}

function hideEmpty() {
  viewerModelLoaded = true;
  document.getElementById("viewer-empty").style.display = "none";
  document.getElementById("viewer-coming-soon").style.display = "none";
}

function applyStylePreset(name) {
  const preset = STYLE_PRESETS[name] || STYLE_PRESETS["ball-stick"];
  molDoc.style = STYLE_PRESETS[name] ? name : "ball-stick";
  viewer.setStyle({}, preset.style);
}

function getCurrentSphereScale() {
  const preset = STYLE_PRESETS[molDoc.style] || STYLE_PRESETS["ball-stick"];
  const sphereStyle = preset.style.sphere;
  if (sphereStyle && typeof sphereStyle.scale === "number") return sphereStyle.scale;
  return 0.25;
}

function applySelectionHighlight() {
  if (typeof _svRenderSelectionOverlay === "function") _svRenderSelectionOverlay();
}

function renderMeasurements() {
  var store = window.ACPGeometryStore;
  var measures = store ? store.state.measurements : [];
  measures.forEach(function(m) {
    var atoms = m.atoms.map(function(atomId) { return currentAtomById(atomId); });
    if (atoms.some(function(atom) { return !atom; })) return;
    if (m.value === null || m.value === undefined) return;
    if (m.type === "distance") {
      viewer.addCylinder({ dashed: true, start: { x: atoms[0].x, y: atoms[0].y, z: atoms[0].z }, end: { x: atoms[1].x, y: atoms[1].y, z: atoms[1].z }, radius: 0.05, color: "magenta", fromCap: 1, toCap: 1 });
      viewer.addLabel(formatMeasurementDisplay(m.type, m.value), { position: { x: (atoms[0].x + atoms[1].x) / 2, y: (atoms[0].y + atoms[1].y) / 2, z: (atoms[0].z + atoms[1].z) / 2 }, fontSize: 12, fontColor: "magenta", backgroundColor: "rgba(0,0,0,0.7)" });
      return;
    }
    if (m.type === "angle") {
      viewer.addCylinder({ dashed: true, start: { x: atoms[1].x, y: atoms[1].y, z: atoms[1].z }, end: { x: atoms[0].x, y: atoms[0].y, z: atoms[0].z }, radius: 0.04, color: "cyan", fromCap: 1, toCap: 1 });
      viewer.addCylinder({ dashed: true, start: { x: atoms[1].x, y: atoms[1].y, z: atoms[1].z }, end: { x: atoms[2].x, y: atoms[2].y, z: atoms[2].z }, radius: 0.04, color: "cyan", fromCap: 1, toCap: 1 });
      viewer.addLabel(formatMeasurementDisplay(m.type, m.value), { position: { x: atoms[1].x, y: atoms[1].y, z: atoms[1].z }, fontSize: 12, fontColor: "cyan", backgroundColor: "rgba(0,0,0,0.7)" });
      return;
    }
    viewer.addCylinder({ dashed: true, start: { x: atoms[0].x, y: atoms[0].y, z: atoms[0].z }, end: { x: atoms[1].x, y: atoms[1].y, z: atoms[1].z }, radius: 0.035, color: "orange", fromCap: 1, toCap: 1 });
    viewer.addCylinder({ dashed: true, start: { x: atoms[1].x, y: atoms[1].y, z: atoms[1].z }, end: { x: atoms[2].x, y: atoms[2].y, z: atoms[2].z }, radius: 0.035, color: "orange", fromCap: 1, toCap: 1 });
    viewer.addCylinder({ dashed: true, start: { x: atoms[2].x, y: atoms[2].y, z: atoms[2].z }, end: { x: atoms[3].x, y: atoms[3].y, z: atoms[3].z }, radius: 0.035, color: "orange", fromCap: 1, toCap: 1 });
    viewer.addLabel(formatMeasurementDisplay(m.type, m.value), { position: { x: (atoms[1].x + atoms[2].x) / 2, y: (atoms[1].y + atoms[2].y) / 2, z: (atoms[1].z + atoms[2].z) / 2 }, fontSize: 12, fontColor: "orange", backgroundColor: "rgba(0,0,0,0.7)" });
  });
  // Phase D: dashed overlays for composed SR atom pairs (active card only).
  if (labelMode !== "none") {
    currentAtoms().forEach(function(a) {
      const text = labelMode === "elem" ? a.elem : String(a.id);
      viewer.addLabel(text, { position: { x: a.x, y: a.y, z: a.z }, fontSize: 10, fontColor: molDoc.bgColor === "white" ? "black" : "white", backgroundColor: "rgba(0,0,0,0.5)", showBackground: true });
    });
  }
}

function setupClickableForCurrentModel() {
  if (!currentModel) return;
  currentModel.setClickable({}, true, function(atom, viewerInstance, event) {
    if (molDoc.mode === "view") return;
    if (typeof _svDispatchPick === "function") _svDispatchPick(atom, event);
  });
}

function renderMolDoc(options) {
  options = options || {};
  if (!viewer && !initViewer()) return;
  viewer.removeAllModels();
  viewer.removeAllShapes();
  viewer.removeAllLabels();
  currentModel = null;
  currentModelAtoms = [];
  const atoms = currentAtoms();
  if (!atoms.length) {
    showEmpty();
    viewer.setBackgroundColor(molDoc.bgColor);
    viewer.render();
    renderMeasurementList();
    return;
  }
  hideEmpty();
  currentModel = viewer.addModel(atomsToXYZ(atoms), "xyz");
  currentModelAtoms = (currentModel && currentModel.selectedAtoms({})) || [];
  viewer.setBackgroundColor(molDoc.bgColor);
  applyStylePreset(molDoc.style);
  // Invisible 3Dmol clickspheres enlarge picking without changing the visible style preset.
  viewer.addStyle({}, { clicksphere: { radius: Math.max(0.3, getCurrentSphereScale() * 1.5) } });
  applySelectionHighlight();
  renderMeasurements();
  setupClickableForCurrentModel();
  if (options.zoomTo) scheduleViewerFraming(document.getElementById("viewer-3d"), mainViewerFraming, function() { return viewer; });
  viewer.render();
  renderMeasurementList();
}

function restoreInitialView() {
  if (!viewer || !initialView) return;
  viewer.setView(initialView.slice());
  viewer.render();
}

function clearViewer() {
  // Vibration teardown (todo 30): cancel the rAF loop + clear arrows before
  // disposing the main viewer; handleTeardown is idempotent.
  if (window.ACPVibrationViewer && typeof window.ACPVibrationViewer.handleTeardown === "function") {
    window.ACPVibrationViewer.handleTeardown();
  }
  disposeViewerFraming(mainViewerFraming);
  stopFramePlayback();
  molDoc.frames = [];
  molDoc.currentFrame = 0;
  molDoc.meta.source = "";
  molDoc.meta.formula = "";
  if (window.ACPGeometryStore) window.ACPGeometryStore.reset();
  initialView = null;
  currentModel = null;
  currentModelAtoms = [];
  if (viewer) {
    viewer.removeAllModels();
    viewer.removeAllShapes();
    viewer.removeAllLabels();
    viewer.setBackgroundColor(molDoc.bgColor);
    viewer.render();
  }
  energyGraphDestroyViewer();
  energyGraphState.data = null;
  energyGraphState.dataRevision = "";
  renderMechanismPlaceholder();
  showEmpty();
  renderMeasurementList();
  updateFrameController();
}

async function setViewerTab(tab) {
  if (tab === "3d" || tab === "conformers") tab = "structure";
  activeViewerTab = tab;
  // Leaving the structure tab: tear down the vibration animation + arrows
  // (todo 30 — no running rAF loop or stale shapes may survive the switch).
  if (tab !== "structure" && window.ACPVibrationViewer &&
      typeof window.ACPVibrationViewer.handleTeardown === "function") {
    window.ACPVibrationViewer.handleTeardown();
  }
  document.querySelectorAll(".canvas-tab").forEach(function(btn) {
    btn.classList.toggle("active", btn.getAttribute("data-tab") === tab);
  });
  var canvasArea = document.querySelector(".canvas-area");
  if (canvasArea) canvasArea.classList.toggle("energy-mode", tab === "energy");
  var svLayout = document.getElementById("sv-layout");
  var comingSoon = document.getElementById("viewer-coming-soon");
  var empty = document.getElementById("viewer-empty");
  var frameController = document.getElementById("frame-controller");
  var mechanism = getMechanismViewerContainer();
  var energy = getEnergyViewerContainer();
  if (tab !== "energy") { if (energy) energy.style.display = "none"; energyGraphDestroyViewer(); }
  if (tab === "energy") {
    if (svLayout) svLayout.style.display = "none";
    comingSoon.style.display = "none";
    empty.style.display = "none";
    frameController.style.display = "none";
    if (mechanism) mechanism.style.display = "none";
    if (energy) energy.style.display = "block";
    await renderEnergyGraphWorkspace(lastSelectedJob);
    if (energyGraphState.data) {
      energyGraphUpdateSelection(true, true);
      energyGraphUpdateSelectionToolbar();
    }
    return;
  }
  if (tab === "structure") {
    if (svLayout) svLayout.style.display = "";
    if (mechanism) mechanism.style.display = "none";
    comingSoon.style.display = "none";
    empty.style.display = viewerModelLoaded ? "none" : "grid";
    frameController.style.display = molDoc.frames.length > 1 ? "flex" : "none";
    if (energyGraphState.data && energyGraphState.selectedNodeId && window.ACPStructureViewer && window.ACPStructureViewer.onEnergyNodeSelected) {
      var nodes = energyGraphState.data.nodes || [];
      var selNode = nodes.find(function(n) { return String(n.id) === String(energyGraphState.selectedNodeId); });
      if (selNode) {
        var entryMeta = { kind: String(energyGraphState.data.view_type || ""), frameIndex: Number(selNode.frame_index) };
        if (entryMeta.kind === "conformer") { entryMeta.key = String(selNode.id || "").replace(/^conf_/, ""); }
        var ann = (energyGraphState.data.annotations || []).find(function(a) { return Number(a.frame_index) === Number(selNode.frame_index) && ["ts", "intermediate"].indexOf(a.type) >= 0; });
        if (ann && ann.candidate_id) { entryMeta.kind = "pes"; entryMeta.candidateId = String(ann.candidate_id); }
        window.ACPStructureViewer.onEnergyNodeSelected(energyGraphState.jobId, entryMeta);
      }
    }
    return;
  }
  if (svLayout) svLayout.style.display = "";
  empty.style.display = "none";
  frameController.style.display = "none";
  if (tab === "path") {
    comingSoon.style.display = "none";
    if (mechanism) mechanism.style.display = "none";
    var rendered = await renderMechanismTab(lastSelectedJob, tab);
    if (rendered) {
      comingSoon.style.display = "none";
      return;
    }
  }
  renderMechanismPlaceholder();
  comingSoon.style.display = "grid";
}

function statusPillMode(body) {
  const queue = body && body.queue ? body.queue : {};
  if ((queue.failed || 0) > 0) return { label: t("status.degraded"), className: "warn" };
  const serviceStatus = body && String(body.status || "").toLowerCase();
  if (serviceStatus === "ok" || serviceStatus === "online") return { label: t("status.online"), className: "ok" };
  if (body && body.status) return { label: String(body.status).toUpperCase(), className: "warn" };
  return { label: t("status.unknown"), className: "err" };
}

function invalidateTaskViewMetadata() {
  ACPClient.taskView.invalidateAll();
  ACPClient.taskView.invalidateSummary();
  if (window.ACPClient && window.ACPClient.jobDetail) {
    ACPClient.jobDetail.invalidateAll();
  }
  _mergeSuggestionLoadedAt = 0;
}

function setWorkbenchLoadError(key, error, retry) {
  if (error) workbenchLoadErrors[key] = { message: error.message || String(error), retry: retry };
  else delete workbenchLoadErrors[key];
  var host = document.getElementById("workbench-load-errors");
  if (!host) return;
  host.replaceChildren();
  Object.keys(workbenchLoadErrors).forEach(function(name) {
    var entry = workbenchLoadErrors[name];
    var row = document.createElement("div");
    row.className = "node-error";
    row.appendChild(document.createTextNode(t("load.failed", { name: t("load." + name.toLowerCase()), message: entry.message }) + " "));
    var btn = document.createElement("button");
    btn.className = "mini-btn";
    btn.textContent = t("common.retry");
    btn.addEventListener("click", function() { Promise.resolve().then(entry.retry).catch(function() {}); });
    row.appendChild(btn);
    host.appendChild(row);
  });
}

async function loadStatus() {
  const pill = document.getElementById("status-pill");
  try {
    const body = await api("/status");
    lastStatus = body;
    const mode = statusPillMode(body);
    pill.className = "status-pill " + mode.className;
    pill.textContent = mode.label;
    pill.removeAttribute("data-i18n");
    pill.title = "";
    setWorkbenchLoadError("Status", null);
    document.getElementById("server-summary").textContent = t("server.summary", {
      host: body.host || "127.0.0.1",
      port: body.port || "8765",
      queued: body.queue && typeof body.queue.queued === "number" ? body.queue.queued : 0,
      running: body.queue && typeof body.queue.running === "number" ? body.queue.running : 0
    });
  } catch (e) {
    pill.className = "status-pill err";
    pill.textContent = t("status.offline");
    pill.removeAttribute("data-i18n");
    pill.title = e.message || String(e);
    setWorkbenchLoadError("Status", e, loadStatus);
    throw e;
  }
}

function renderProjects() {
  const select = document.getElementById("project-select");
  select.innerHTML = "";
  const optionAll = document.createElement("option");
  optionAll.value = "";
  optionAll.textContent = projectsCache.length ? t("project.all") : t("project.empty");
  select.appendChild(optionAll);
  projectsCache.forEach(function(project) {
    const option = document.createElement("option");
    option.value = String(project.project_id || "");
    option.textContent = project.name || String(project.project_id || "");
    if (option.value === selectedProjectId) option.selected = true;
    select.appendChild(option);
  });
  select.value = selectedProjectId;
  if (select.selectedIndex >= 0 && select.options[select.selectedIndex]) {
    select.title = select.options[select.selectedIndex].textContent || "";
  }
  updateDeleteProjectButton();
  renderResultsProjectOptions();
}

function resultsProjectTargetId() {
  var sel = document.getElementById("modal-project-select");
  return (sel && sel.value ? String(sel.value) : "") || "uncategorized";
}

function projectNameOf(projectId) {
  var pid = String(projectId || "");
  if (!pid) return t("results.project_all");
  var proj = projectsCache.find(function(p) { return String(p.project_id || "") === pid; });
  return (proj && (proj.name || proj.project_id)) || pid;
}

// Dynamic results filter: 目标项目 (modal submission target) / 全部项目 /
// one option per project. Rebuilt whenever projects or the target change.
function renderResultsProjectOptions() {
  var sel = document.getElementById("results-project");
  if (!sel) return;
  var prev = sel.value || "target";
  var targetId = resultsProjectTargetId();
  var html = '<option value="target">' +
    escapeHtml(t("results.project_target") + "（" + projectNameOf(targetId) + "）") +
    "</option>" +
    '<option value="all">' + escapeHtml(t("results.project_all")) + "</option>";
  projectsCache.forEach(function(p) {
    var pid = String(p.project_id || "");
    if (!pid) return;
    html += '<option value="' + escapeHtml(pid) + '">' + escapeHtml(p.name || pid) + "</option>";
  });
  sel.innerHTML = html;
  var valid = sel.querySelector('option[value="' + prev.replace(/"/g, '\\"') + '"]');
  sel.value = valid ? prev : "target";
}

function updateDeleteProjectButton() {
  const btn = document.getElementById("btn-delete-project");
  if (!btn) return;
  const canDelete = selectedProjectId && selectedProjectId !== "uncategorized";
  btn.disabled = !canDelete;
}

function renderModalProjects() {
  const select = document.getElementById("modal-project-select");
  select.innerHTML = "";
  if (!projectsCache.length) {
    const option = document.createElement("option");
    option.value = "uncategorized";
    option.textContent = "Uncategorized";
    select.appendChild(option);
    return;
  }
  projectsCache.forEach(function(project) {
    const option = document.createElement("option");
    option.value = String(project.project_id || "");
    const name = project.name || String(project.project_id || "");
    option.textContent = project.project_id === "uncategorized" ? (name + " " + (t("modal.project_default") || "(默认)")) : name;
    select.appendChild(option);
  });
}

function setModalProjectDefault() {
  const select = document.getElementById("modal-project-select");
  if (!select) return;
  let defaultId = selectedProjectId;
  if (!defaultId) {
    const fallback = projectsCache.find(function(p) { return p.project_id === "uncategorized"; });
    defaultId = fallback ? fallback.project_id : (projectsCache[0] && projectsCache[0].project_id ? projectsCache[0].project_id : "uncategorized");
  }
  select.value = defaultId;
  updateModalProjectHint();
}

function updateModalProjectHint() {
  const select = document.getElementById("modal-project-select");
  const hint = document.getElementById("modal-project-hint");
  if (!select || !hint) return;
  const selected = select.options[select.selectedIndex];
  if (selected && selected.value) {
    hint.textContent = (t("modal.project_current") || "将提交到项目") + ": " + selected.textContent;
    hint.style.display = "block";
  } else {
    hint.style.display = "none";
  }
}

function openProjectModal() {
  document.getElementById("project-modal-name").value = "";
  document.getElementById("project-modal-desc").value = "";
  const err = document.getElementById("project-modal-name-err");
  if (err) err.style.display = "none";
  document.getElementById("project-modal").style.display = "flex";
}

function closeProjectModal() {
  document.getElementById("project-modal").style.display = "none";
}

function openTargetProjectModal(action, job) {
  targetProjectAction = action || "move";
  targetProjectJobId = job ? (job.id || job.job_id || "") : "";
  if (!targetProjectJobId) return;
  const titleEl = document.getElementById("target-project-modal-title");
  const submitBtn = document.getElementById("target-project-modal-submit");
  if (titleEl) titleEl.textContent = t(action === "clone" ? "clone.title" : "move.title") || (action === "clone" ? "复制任务" : "移动任务");
  if (submitBtn) submitBtn.textContent = t(action === "clone" ? "clone.button" : "move.button") || (action === "clone" ? "复制" : "移动");
  const select = document.getElementById("target-project-select");
  select.innerHTML = "";
  if (!projectsCache.length) {
    const option = document.createElement("option");
    option.value = "uncategorized";
    option.textContent = "Uncategorized";
    select.appendChild(option);
  } else {
    projectsCache.forEach(function(project) {
      const option = document.createElement("option");
      option.value = String(project.project_id || "");
      option.textContent = project.name || String(project.project_id || "");
      if (option.value === selectedProjectId) option.selected = true;
      select.appendChild(option);
    });
  }
  document.getElementById("target-project-modal").style.display = "flex";
}

function closeTargetProjectModal() {
  document.getElementById("target-project-modal").style.display = "none";
  targetProjectJobId = "";
}

function openDeleteProjectModal() {
  const project = projectsCache.find(function(p) { return p.project_id === selectedProjectId; });
  const promptEl = document.getElementById("delete-project-prompt");
  if (promptEl && project) {
    promptEl.textContent = (t("project.delete_prompt") || "确定要删除项目") + " \"" + (project.name || project.project_id) + "\" 吗？";
  }
  document.getElementById("delete-project-data").checked = true;
  const err = document.getElementById("delete-project-err");
  if (err) err.style.display = "none";
  document.getElementById("delete-project-modal").style.display = "flex";
}

function closeDeleteProjectModal() {
  document.getElementById("delete-project-modal").style.display = "none";
}

async function submitDeleteProjectModal() {
  if (!selectedProjectId) return;
  const deleteData = document.getElementById("delete-project-data").checked;
  const errEl = document.getElementById("delete-project-err");
  const submitBtn = document.getElementById("delete-project-modal-submit");
  submitBtn.disabled = true;
  try {
    await api("/projects/" + encodeURIComponent(selectedProjectId) + "?delete_data=" + (deleteData ? "true" : "false"), {
      method: "DELETE"
    });
    closeDeleteProjectModal();
    selectedProjectId = "";
    await loadProjects();
    await refreshJobs();
  } catch (e) {
    if (errEl) {
      errEl.textContent = e.message;
      errEl.style.display = "block";
    }
  } finally {
    submitBtn.disabled = false;
  }
}

function openDeleteJobModal(job) {
  deleteJobId = job ? (job.id || job.job_id || "") : "";
  if (!deleteJobId) return;
  const jobName = displayTaskName(job) || deleteJobId;
  const promptEl = document.getElementById("delete-job-prompt");
  if (promptEl) {
    promptEl.textContent = (t("job.delete_prompt") || "确定要删除任务") + " \"" + jobName + "\" 吗？";
  }
  document.getElementById("delete-job-data").checked = true;
  const err = document.getElementById("delete-job-err");
  if (err) err.style.display = "none";
  document.getElementById("delete-job-modal").style.display = "flex";
}

// The task directory is the canonical user-facing task name.  The fallback
// chain keeps historical v1 records readable while their old spec.name value
// still contains the pre-v2 batch/random suffix.
function canonicalTaskName(job) {
  if (!job) return "";
  if (job.task_dir_name) return String(job.task_dir_name);
  if (job.work_dir) {
    const parts = String(job.work_dir).replace(/\\/g, "/").split("/");
    if (parts.length && parts[parts.length - 1]) return parts[parts.length - 1];
  }
  if (job.spec && job.spec.task_dir_name) return String(job.spec.task_dir_name);
  if (job.spec && job.spec.name) return String(job.spec.name);
  return job.id || job.job_id || "";
}

// Display name: prefer resolved_name (server-authoritative), then
// custom_name, then fall back to canonicalTaskName (disk/path-based).
// Used for user-facing labels; keep canonicalTaskName for pure-path uses.
function displayTaskName(job) {
  if (!job) return "";
  var rn = job.resolved_name || "";
  if (rn) return String(rn);
  var cn = job.custom_name || "";
  if (cn) return String(cn);
  return canonicalTaskName(job) || "";
}

function updateFileTreeTaskName(jobId) {
  const key = String(jobId || "");
  let job = key ? lastSelectedJob : null;
  if (!job || String(job.id || job.job_id || "") !== key) {
    job = jobsCache.find(function(entry) {
      return String(entry.id || entry.job_id || "") === key;
    }) || null;
  }
  const name = displayTaskName(job);
  document.querySelectorAll(".file-tree-task-name").forEach(function(el) {
    el.textContent = name || t("files.no_task");
    el.title = name || "";
    el.classList.toggle("empty", !name);
  });
}

function closeDeleteJobModal() {
  document.getElementById("delete-job-modal").style.display = "none";
  deleteJobId = "";
}

async function submitDeleteJobModal() {
  if (!deleteJobId) return;
  const deleteData = document.getElementById("delete-job-data").checked;
  const errEl = document.getElementById("delete-job-err");
  const submitBtn = document.getElementById("delete-job-modal-submit");
  submitBtn.disabled = true;
  try {
    await api("/jobs/" + encodeURIComponent(deleteJobId) + "?delete_data=" + (deleteData ? "true" : "false"), {
      method: "DELETE"
    });
    resetMechanismCachesForJob(deleteJobId);
    invalidateJobDetail(deleteJobId);
    closeDeleteJobModal();
    selectedJobId = "";
    lastSelectedJob = null;
    lastFileListing = [];
    folderCache = {};
    folderState = {};
    updateInfoPanel(null);
    renderFileTree([], "");
    renderLogPlaceholder();
    clearViewer();
    await refreshJobs();
  } catch (e) {
    if (errEl) {
      errEl.textContent = e.message;
      errEl.style.display = "block";
    }
  } finally {
    submitBtn.disabled = false;
  }
}

async function cancelJob(jobId) {
  if (!jobId) return;
  try {
    await ACPClient.jobCommands.cancel(jobId);
    resetMechanismCachesForJob(jobId);
    await refreshJobDetailAfterAction(jobId);
  } catch (e) {
    window.alert((t("job.cancel_failed") || "取消失败") + ": " + e.message);
  }
}


// ── XYZ frame parsers and geometry store ──
function parseEnergyFromComment(comment) {
  const m = comment.match(/(-?\d+\.\d+)/);
  return m ? parseFloat(m[1]) : null;
}

function parseMultiFrameXYZ(text) {
  const lines = String(text || "").trim().split(/\r?\n/);
  const frames = [];
  let i = 0;
  while (i < lines.length) {
    while (i < lines.length && !String(lines[i] || "").trim()) i += 1;
    if (i >= lines.length) break;
    const n = parseInt(String(lines[i] || "").trim(), 10);
    if (isNaN(n) || i + 1 + n >= lines.length) break;
    const comment = lines[i + 1] || "";
    const atoms = [];
    for (let j = 0; j < n; j += 1) {
      const parts = (lines[i + 2 + j] || "").trim().split(/\s+/);
      if (parts.length >= 4) atoms.push({ id: j, elem: parts[0], x: +parts[1], y: +parts[2], z: +parts[3] });
    }
    frames.push({ atoms: atoms, comment: comment, energy: parseEnergyFromComment(comment) });
    i += 2 + n;
  }
  return frames;
}

function atomsToXYZ(atoms) {
  let out = atoms.length + "\n\n";
  atoms.forEach(function(a) {
    out += a.elem + " " + a.x.toFixed(6) + " " + a.y.toFixed(6) + " " + a.z.toFixed(6) + "\n";
  });
  return out;
}

function framesToXYZ(frames) {
  return frames.map(function(frame) { return atomsToXYZ(frame.atoms); }).join("\n");
}

function parseXYZBlock(text) {
  const lines = text.trim().split("\n");
  const n = parseInt(lines[0], 10);
  if (isNaN(n) || lines.length < n + 2) return [];
  const atoms = [];
  for (let j = 0; j < n; j += 1) {
    const parts = lines[2 + j].trim().split(/\s+/);
    if (parts.length >= 4) {
      atoms.push({ id: j, elem: parts[0], x: +parts[1], y: +parts[2], z: +parts[3] });
    }
  }
  return atoms;
}

function parseMolTextToFrame(text, format) {
  if (!viewer && !initViewer()) return [];
  const model = viewer.addModel(text, format);
  if (!model) return [];
  const atoms = (model.selectedAtoms({}) || []).map(function(atom, idx) {
    return { id: idx, elem: atom.elem || atom.atom || "X", x: Number(atom.x || 0), y: Number(atom.y || 0), z: Number(atom.z || 0) };
  });
  viewer.removeModel(model);
  return atoms.length ? [{ atoms: atoms, comment: "", energy: null }] : [];
}

function countFormula(atoms) {
  const counts = {};
  atoms.forEach(function(a) { counts[a.elem] = (counts[a.elem] || 0) + 1; });
  return Object.entries(counts).map(function(entry) { return entry[0] + entry[1]; }).join("");
}

async function loadStructureFile(jobId, filePath) {
  try {
    const content = await api("/jobs/" + encodeURIComponent(jobId) + "/files/" + filePath.split("/").map(encodeURIComponent).join("/"));
    const ext = filePath.includes(".") ? filePath.split(".").pop().toLowerCase() : "";
    const frames = ext === "xyz" ? parseMultiFrameXYZ(content) : parseMolTextToFrame(content, ext === "mol" ? "mol" : "sdf");
    if (!frames.length) return;
    stopFramePlayback();
    molDoc.frames = frames;
    molDoc.currentFrame = 0;
    molDoc.meta.source = filePath;
    molDoc.meta.formula = countFormula(frames[0].atoms);
    if (window.ACPGeometryStore) {
      var a0 = frames[0].atoms;
      window.ACPGeometryStore.load({ entryId: filePath, symbols: a0.map(function(a){ return a.elem; }), coordinates: a0.map(function(a){ return [a.x, a.y, a.z]; }) });
    }
    renderMolDoc({ zoomTo: true });
    initialView = viewer ? viewer.getView().slice() : null;
    updateFrameController();
    updateInfoPanel(lastSelectedJob);
  } catch (e) {
    console.error("Failed to load structure:", e);
  }
}

async function loadRemoteStructureFile(jobId, filePath) {
  const previewUrl = "/jobs/" + encodeURIComponent(jobId) + "/remote-files/" + filePath.split("/").map(encodeURIComponent).join("/") + "/preview?mode=structure";
  const resp = await api(previewUrl);
  const content = (resp && typeof resp.content === "string") ? resp.content : String(resp);
  const ext = filePath.includes(".") ? filePath.split(".").pop().toLowerCase() : "";
  const frames = ext === "xyz" ? parseMultiFrameXYZ(content) : parseMolTextToFrame(content, ext === "mol" ? "mol" : "sdf");
  if (!frames.length) return;
  stopFramePlayback();
  molDoc.frames = frames;
  molDoc.currentFrame = 0;
  molDoc.meta.source = filePath;
  molDoc.meta.formula = countFormula(frames[0].atoms);
  if (window.ACPGeometryStore) {
    var a0 = frames[0].atoms;
    window.ACPGeometryStore.load({ entryId: filePath, symbols: a0.map(function(a){ return a.elem; }), coordinates: a0.map(function(a){ return [a.x, a.y, a.z]; }) });
  }
  renderMolDoc({ zoomTo: true });
  initialView = viewer ? viewer.getView().slice() : null;
  updateFrameController();
  updateInfoPanel(lastSelectedJob);
}

// Bridge for structure_viewer.js: load XYZ text directly into the main viewer.
// Called by ACPStructureViewer.loadSelectedGeometry() after fetching geometry.
window._svLoadXyzToViewer = function(xyzText, entryId) {
  try {
    if (!xyzText || typeof parseMultiFrameXYZ !== "function") return;
    var frames = parseMultiFrameXYZ(xyzText);
    if (!frames.length) return;
    stopFramePlayback();
    molDoc.frames = frames;
    molDoc.currentFrame = 0;
    molDoc.meta.source = entryId || "structure-viewer";
    molDoc.meta.formula = countFormula(frames[0].atoms);
    if (window.ACPGeometryStore) {
      var a0 = frames[0].atoms;
      window.ACPGeometryStore.load({ entryId: entryId || "structure-viewer", symbols: a0.map(function(a){ return a.elem; }), coordinates: a0.map(function(a){ return [a.x, a.y, a.z]; }) });
    }
    renderMolDoc({ zoomTo: true });
    initialView = viewer ? viewer.getView().slice() : null;
    updateFrameController();
    updateInfoPanel(lastSelectedJob);
  } catch (e) {
    console.error("Structure viewer geometry load failed:", e);
  }
};

// Bridge for structure_viewer.js: remove the outgoing task's geometry while
// the newly selected task's catalog/geometry is loading (or when it has none).
window._svClearViewerGeometry = function() {
  try {
    if (typeof stopFramePlayback === "function") stopFramePlayback();
    molDoc.frames = [];
    molDoc.currentFrame = 0;
    molDoc.meta.source = "";
    molDoc.meta.formula = "";
    if (window.ACPGeometryStore) window.ACPGeometryStore.reset();
    initialView = null;
    currentModel = null;
    currentModelAtoms = [];
    if (viewer) {
      viewer.removeAllModels();
      viewer.removeAllShapes();
      viewer.removeAllLabels();
      viewer.setBackgroundColor(molDoc.bgColor);
      viewer.render();
    }
    showEmpty();
    renderMeasurementList();
    updateFrameController();
    updateInfoPanel(lastSelectedJob);
  } catch (e) {
    console.error("Structure viewer geometry clear failed:", e);
  }
};

// Bridge for structure_editor.js: prefill the new-task wizard with an edited
// XYZ (todo 37). Opens the modal and fills the structure textarea, then the
// existing input-driven parse previews it. NEVER submits — the user confirms
// method/charge/multiplicity and clicks 提交 themselves (anti-pattern #29).
window._svPrefillNewCalculation = function(xyzText) {
  try {
    openModal();
    var ta = document.getElementById("modal-structure-input");
    if (ta && xyzText) {
      ta.value = xyzText;
      if (typeof scheduleParseStructures === "function") scheduleParseStructures();
    }
  } catch (e) {
    console.error("Wizard prefill failed:", e);
  }
};

function downloadRemoteFile(jobId, filePath) {
  if (!filePath || filePath.endsWith("/")) return;
  const url = API_BASE + "/jobs/" + encodeURIComponent(jobId) + "/remote-files/" + filePath.split("/").map(encodeURIComponent).join("/");
  const a = document.createElement("a");
  a.href = url;
  a.download = filePath.split("/").pop() || "download";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function downloadLocalFile(jobId, filePath) {
  if (!filePath || filePath.endsWith("/")) return;
  const url = API_BASE + "/jobs/" + encodeURIComponent(jobId) + "/files/" + filePath.split("/").map(encodeURIComponent).join("/");
  const a = document.createElement("a");
  a.href = url;
  a.download = filePath.split("/").pop() || "download";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function downloadRemoteArchive(jobId) {
  const url = API_BASE + "/jobs/" + encodeURIComponent(jobId) + "/remote-files/archive";
  const a = document.createElement("a");
  a.href = url;
  a.download = jobId + ".zip";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function handleSelectClick(atom, event) {
  var id = (typeof _svResolveAtomId === "function") ? _svResolveAtomId(atom) : null;
  if (id === null) return;
  var store = window.ACPGeometryStore;
  if (!store) return;
  if (event && event.shiftKey) {
    store.toggleSelection(id);
  } else {
    store.setSelection([id]);
  }
  updateInfoPanel(lastSelectedJob);
}

function sub3(a, b) { return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z }; }
function dot3(a, b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
function len3(a) { return Math.sqrt(dot3(a, a)); }
function cross3(a, b) { return { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x }; }

function doDistance(idA, idB) {
  if (window.ACPGeometryStore) window.ACPGeometryStore.addMeasurement("distance", [idA, idB]);
}

function doAngle(idA, idB, idC) {
  if (window.ACPGeometryStore) window.ACPGeometryStore.addMeasurement("angle", [idA, idB, idC]);
}

function doDihedral(idA, idB, idC, idD) {
  if (window.ACPGeometryStore) window.ACPGeometryStore.addMeasurement("dihedral", [idA, idB, idC, idD]);
}

function handleMeasureClick(atom) {
  var id = (typeof _svResolveAtomId === "function") ? _svResolveAtomId(atom) : null;
  if (id === null) return;
  var store = window.ACPGeometryStore;
  if (!store) return;
  if (store.isSelected(id)) {
    // Light hint — atom already in pick buffer
    return;
  }
  store.addToSelection(id);
  _updateMeasureStatus();
  var needed = molDoc.measureType === "distance" ? 2 : molDoc.measureType === "angle" ? 3 : 4;
  if (store.state.selection.length >= needed) {
    store.addMeasurement(molDoc.measureType, store.state.selection.slice());
    store.clearSelection();
    _updateMeasureStatus();
    renderMeasurementList();
    _svRenderMeasurementOverlays();
    if (typeof window !== "undefined" && window.ACPStructureViewer &&
        typeof window.ACPStructureViewer.openDrawer === "function") {
      window.ACPStructureViewer.openDrawer("measure");
    }
  }
}

// ── T7: Pick diagnostics ──────────────────────────────────────────────
window.__acpPickDiag = { events: [], record: function(evt){ this.events.push(evt); if (this.events.length > 200) this.events.shift(); }, dump: function(){ return this.events.slice(); }, clear: function(){ this.events = []; } };

// ── T2: Native atom index mapping (kills acpId) ──────────────────────
var _svHighlightedIndices = [];
var _svBadgeLabels = [];

window._svResolveAtomId = function(atom) {
  var store = window.ACPGeometryStore;
  if (!atom || typeof atom.index !== "number" || !Number.isInteger(atom.index)) {
    if (window.__acpPickDiag) window.__acpPickDiag.record({ event: "resolve_failed", index: atom && atom.index, revision: store ? store.state.revision : 0, mode: molDoc.mode });
    return null;
  }
  var maxId = store ? store.state.symbols.length : 0;
  if (atom.index < 0 || atom.index >= maxId) {
    if (window.__acpPickDiag) window.__acpPickDiag.record({ event: "resolve_failed", index: atom.index, revision: store ? store.state.revision : 0, mode: molDoc.mode });
    return null;
  }
  return atom.index;
};

// ── T3: Pick dispatch ────────────────────────────────────────────────
function _svDispatchPick(atom, event) {
  var id = window._svResolveAtomId(atom);
  if (id === null) return;
  var store = window.ACPGeometryStore;
  if (!store) return;
  var diag = { mode: molDoc.mode, renderedIndex: atom.index, canonicalId: id, revision: store.state.revision, outcome: "" };
  if (molDoc.mode === "select") {
    store.toggleSelection(id);
    diag.outcome = "toggled";
  } else if (molDoc.mode === "measure") {
    handleMeasureClick(atom);
    diag.outcome = "measured";
  }
  if (window.__acpPickDiag) window.__acpPickDiag.record(diag);
}
window._svDispatchPick = _svDispatchPick;

// ── T3: Test helper for browser integration tests ───────────────────
window.__acpTestPickAtom = function(index) {
  if (!currentModelAtoms || index < 0 || index >= currentModelAtoms.length) return { ok: false };
  var a = currentModelAtoms[index];
  var before = window.ACPGeometryStore ? window.ACPGeometryStore.state.revision : 0;
  _svDispatchPick(a, null);
  var store = window.ACPGeometryStore;
  return {
    ok: true,
    canonicalId: a.index,
    selectionLength: store ? store.state.selection.length : 0,
    measurementCount: store ? store.state.measurements.length : 0,
    revision: store ? store.state.revision : before
  };
};

// ── T4: Store-backed rendering (deferred model sync, incremental overlays) ─
function _svSyncGeometryToCanvas(opts) {
  opts = opts || {};
  var store = window.ACPGeometryStore;
  if (!store || !viewer) return;
  var snap = store.snapshot();
  if (!snap.symbols.length) return;
  var atoms = snap.atoms;
  // Apply edit preview coordinates when set
  if (snap.editPreview && Array.isArray(snap.editPreview.coordinates)) {
    atoms = snap.editPreview.coordinates.map(function(row, i) {
      return { id: i, elem: snap.symbols[i] || "X", x: row[0], y: row[1], z: row[2] };
    });
  }
  viewer.removeAllModels();
  viewer.removeAllShapes();
  viewer.removeAllLabels();
  currentModel = viewer.addModel(atomsToXYZ(atoms), "xyz");
  currentModelAtoms = (currentModel && currentModel.selectedAtoms({})) || [];
  viewer.setBackgroundColor(molDoc.bgColor);
  applyStylePreset(molDoc.style);
  viewer.addStyle({}, { clicksphere: { radius: Math.max(0.3, getCurrentSphereScale() * 1.5) } });
  setupClickableForCurrentModel();
  // Write coordinates back into molDoc frames for consistency
  if (molDoc.frames[molDoc.currentFrame]) {
    molDoc.frames[molDoc.currentFrame].atoms = atoms.map(function(a) {
      return { id: a.id, elem: a.elem, x: a.x, y: a.y, z: a.z };
    });
  }
  _svRenderSelectionOverlay();
  _svRenderMeasurementOverlays();
  viewer.render();
  if (opts.zoomTo) scheduleViewerFraming(document.getElementById("viewer-3d"), mainViewerFraming, function() { return viewer; });
}
window._svSyncGeometryToCanvas = _svSyncGeometryToCanvas;

function _svRenderSelectionOverlay() {
  var store = window.ACPGeometryStore;
  if (!store || !viewer || !currentModel) return;
  var sel = store.state.selection;
  var baseScale = getCurrentSphereScale();
  var highlightScale = baseScale + 0.12;
  var preset = STYLE_PRESETS[molDoc.style] || STYLE_PRESETS["ball-stick"];
  // Reset previously highlighted atoms to preset style
  for (var i = 0; i < _svHighlightedIndices.length; i++) {
    var idx = _svHighlightedIndices[i];
    if (sel.indexOf(idx) === -1) {
      currentModel.setStyle({ index: idx }, preset.style);
    }
  }
  // Highlight newly selected atoms
  var newHighlighted = [];
  for (var j = 0; j < sel.length; j++) {
    var atomIdx = sel[j];
    if (atomIdx >= 0 && atomIdx < currentModelAtoms.length) {
      currentModel.addStyle({ index: atomIdx }, { sphere: { scale: highlightScale, color: "yellow", opacity: 0.55 } });
      newHighlighted.push(atomIdx);
    }
  }
  _svHighlightedIndices = newHighlighted;
  // Pick-order badges (_svBadgeLabels holds opaque instance label tokens).
  // Remove previous badges
  for (var k = 0; k < _svBadgeLabels.length; k++) {
    viewer.removeLabel(_svBadgeLabels[k]);
  }
  _svBadgeLabels = [];
  for (var p = 0; p < sel.length; p++) {
    var aid = sel[p];
    var coord = (aid >= 0 && aid < currentModelAtoms.length) ? currentModelAtoms[aid] : null;
    if (!coord) continue;
    var lbl = viewer.addLabel(String(p + 1), {
      position: { x: coord.x, y: coord.y, z: coord.z },
      fontSize: 14, fontColor: "yellow", backgroundColor: "rgba(0,0,0,0.6)", showBackground: true
    });
    if (lbl) _svBadgeLabels.push(lbl);
  }
  viewer.render();
}
window._svRenderSelectionOverlay = _svRenderSelectionOverlay;

function _svRenderMeasurementOverlays() {
  _svRenderEditBar();
  viewer.removeAllShapes();
  viewer.removeAllLabels();
  renderMeasurements();
  renderMeasurementList();
  _updateMeasureStatus();
  _svRenderSelectionOverlay();
  viewer.render();
}
window._svRenderMeasurementOverlays = _svRenderMeasurementOverlays;

// ── T8: Measurement edit bar (canvas-bottom compact editor) ───────────
var _svEditBarState = { measurementId: null, dismissedId: null };

function _svEditBarAtomLabel(atomId) {
  var store = window.ACPGeometryStore;
  if (!store) return String(atomId);
  var symbols = store.state.symbols;
  var sym = (atomId >= 0 && atomId < symbols.length) ? symbols[atomId] : "?";
  return sym + (atomId + 1);
}

function _svEditBarSeparator(type) {
  return type === "distance" ? "\u2014" : "-";
}

function _svEditBarBuildLabel(m) {
  var sep = _svEditBarSeparator(m.type);
  return m.atoms.map(function(id) { return _svEditBarAtomLabel(id); }).join(sep);
}

function _svEditBarUnit(type) {
  return type === "distance" ? "\u00c5" : "\u00b0";
}

function _svEditBarRange(type) {
  if (type === "distance") return { min: 0.4, max: 5.0, step: 0.001, decimals: 3 };
  if (type === "angle") return { min: 1, max: 179, step: 0.1, decimals: 1 };
  return { min: -180, max: 180, step: 0.1, decimals: 1 };
}

function _svEditBarValidateRange(type, value) {
  var r = _svEditBarRange(type);
  if (type === "dihedral") {
    if (value < -180 || value > 180) return "\u76ee\u6807\u503c\u8d85\u51fa\u5141\u8bb8\u8303\u56f4 [-180, 180]\u00b0";
    return null;
  }
  if (value < r.min || value > r.max) return "\u76ee\u6807\u503c\u8d85\u51fa\u5141\u8bb8\u8303\u56f4 [" + r.min + ", " + r.max + "]" + _svEditBarUnit(type);
  return null;
}

function _svEditBarKind(type) {
  if (type === "distance") return "bond_length";
  if (type === "angle") return "bond_angle";
  return "dihedral";
}

function _svRenderEditBar() {
  var bar = document.getElementById("sv-measure-editbar");
  if (!bar) return;
  var store = window.ACPGeometryStore;
  if (!store) { bar.style.display = "none"; _svEditBarState.measurementId = null; return; }
  var measures = store.state.measurements;
  if (!measures.length) { bar.style.display = "none"; _svEditBarState.measurementId = null; _svEditBarState.dismissedId = null; return; }

  var m = measures[measures.length - 1];

  if (m.id === _svEditBarState.dismissedId) { bar.style.display = "none"; return; }

  _svEditBarState.measurementId = m.id;

  var labelEl = document.getElementById("sv-measure-editbar-label");
  var msgEl = document.getElementById("sv-measure-editbar-msg");
  var targetWrap = document.getElementById("sv-measure-editbar-target-wrap");
  var targetInput = document.getElementById("sv-measure-editbar-target");
  var unitEl = document.getElementById("sv-measure-editbar-unit");
  var previewBtn = document.getElementById("sv-measure-editbar-preview");
  var applyBtn = document.getElementById("sv-measure-editbar-apply");
  var cancelBtn = document.getElementById("sv-measure-editbar-cancel");
  if (!labelEl || !msgEl || !targetWrap || !targetInput || !unitEl || !previewBtn || !applyBtn || !cancelBtn) return;

  var label = _svEditBarBuildLabel(m);
  var currentValue = (m.value !== null && m.value !== undefined) ? m.value : null;
  var range = _svEditBarRange(m.type);
  var unit = _svEditBarUnit(m.type);
  var isEditable = m.editable !== false;

  labelEl.textContent = label + "  " + (currentValue !== null ? currentValue.toFixed(range.decimals) : "--") + " " + unit;

  var blockReason = null;
  if (window.ACPStructureViewer && typeof window.ACPStructureViewer.editBlockReason === "function") {
    var br = window.ACPStructureViewer.editBlockReason();
    if (br && br.blocked) blockReason = br;
  }
  var isBlocked = blockReason !== null;

  if (!isEditable) {
    var reasonMsg = m.message || "";
    if (!reasonMsg && m.reason && window.ACPStructureEditor && typeof window.ACPStructureEditor.reasonMessage === "function") {
      reasonMsg = window.ACPStructureEditor.reasonMessage(m.reason);
    }
    msgEl.textContent = reasonMsg;
    msgEl.className = "sv-measure-editbar-msg";
    msgEl.style.display = reasonMsg ? "" : "none";
    targetWrap.style.display = "none";
    previewBtn.style.display = "none";
    applyBtn.style.display = "none";
  } else if (isBlocked) {
    msgEl.textContent = blockReason.message || "\u5f53\u524d\u64cd\u4f5c\u4e0d\u53ef\u7528";
    msgEl.className = "sv-measure-editbar-msg sv-measure-editbar-msg-error";
    msgEl.style.display = "";
    targetWrap.style.display = "";
    targetInput.disabled = true;
    targetInput.value = currentValue !== null ? currentValue.toFixed(range.decimals) : "";
    targetInput.min = range.min;
    targetInput.max = range.max;
    targetInput.step = range.step;
    unitEl.textContent = unit;
    previewBtn.style.display = "";
    previewBtn.disabled = true;
    applyBtn.style.display = "";
    applyBtn.disabled = true;
  } else {
    msgEl.style.display = "none";
    msgEl.textContent = "";
    targetWrap.style.display = "";
    targetInput.disabled = false;
    targetInput.value = currentValue !== null ? currentValue.toFixed(range.decimals) : "";
    targetInput.min = range.min;
    targetInput.max = range.max;
    targetInput.step = range.step;
    unitEl.textContent = unit;
    previewBtn.style.display = "";
    previewBtn.disabled = false;
    applyBtn.style.display = "";
    applyBtn.disabled = false;
  }

  bar.style.display = "";
}

function _svEditBarHide() {
  var bar = document.getElementById("sv-measure-editbar");
  if (bar) bar.style.display = "none";
  _svEditBarState.dismissedId = _svEditBarState.measurementId;
  _svEditBarState.measurementId = null;
  var store = window.ACPGeometryStore;
  if (store) store.setEditPreview(null);
}

function _svEditBarPreview() {
  var store = window.ACPGeometryStore;
  if (!store || !_svEditBarState.measurementId) return;
  var m = null;
  var measures = store.state.measurements;
  for (var i = 0; i < measures.length; i++) {
    if (measures[i].id === _svEditBarState.measurementId) { m = measures[i]; break; }
  }
  if (!m || m.editable === false) return;

  var targetInput = document.getElementById("sv-measure-editbar-target");
  if (!targetInput) return;
  var target = parseFloat(targetInput.value);
  if (isNaN(target)) return;

  var rangeErr = _svEditBarValidateRange(m.type, target);
  if (rangeErr) {
    var msgEl = document.getElementById("sv-measure-editbar-msg");
    if (msgEl) { msgEl.textContent = rangeErr; msgEl.className = "sv-measure-editbar-msg sv-measure-editbar-msg-error"; msgEl.style.display = ""; }
    store.setEditPreview(null);
    return;
  }

  var editor = window.ACPStructureEditor;
  if (!editor) return;
  var kind = _svEditBarKind(m.type);
  var symbols = store.state.symbols;
  var coords = store.state.coordinates.map(function(row) { return row.slice(); });
  var graph = (editor.editorState && editor.editorState.graph) ||
    (typeof editor.buildGraphFromCurrentEntry === "function" ? editor.buildGraphFromCurrentEntry() : null);
  var edges = (graph && graph.edges) ? graph.edges : [];
  var result = null;
  if (kind === "bond_length") {
    result = editor.editBondLength(symbols, coords, edges, m.atoms[0], m.atoms[1], target, null);
  } else if (kind === "bond_angle") {
    result = editor.editBondAngle(symbols, coords, edges, m.atoms[0], m.atoms[1], m.atoms[2], target, null);
  } else if (kind === "dihedral") {
    result = editor.editDihedral(symbols, coords, edges, m.atoms[0], m.atoms[1], m.atoms[2], m.atoms[3], target, null);
  }
  if (result && result.ok && result.coords) {
    store.setEditPreview({ coordinates: result.coords, atomIds: m.atoms.slice(), note: "editbar-preview" });
    var msgEl2 = document.getElementById("sv-measure-editbar-msg");
    if (msgEl2) { msgEl2.style.display = "none"; msgEl2.textContent = ""; }
  } else if (result && result.reason) {
    var failMsg = (window.ACPStructureEditor && typeof window.ACPStructureEditor.reasonMessage === "function")
      ? window.ACPStructureEditor.reasonMessage(result.reason) : result.reason;
    var msgEl3 = document.getElementById("sv-measure-editbar-msg");
    if (msgEl3) { msgEl3.textContent = failMsg; msgEl3.className = "sv-measure-editbar-msg sv-measure-editbar-msg-error"; msgEl3.style.display = ""; }
    store.setEditPreview(null);
  }
}

function _svEditBarApply() {
  var store = window.ACPGeometryStore;
  if (!store || !_svEditBarState.measurementId) return;
  var m = null;
  var measures = store.state.measurements;
  for (var i = 0; i < measures.length; i++) {
    if (measures[i].id === _svEditBarState.measurementId) { m = measures[i]; break; }
  }
  if (!m || m.editable === false) return;

  var targetInput = document.getElementById("sv-measure-editbar-target");
  if (!targetInput) return;
  var target = parseFloat(targetInput.value);
  if (isNaN(target)) return;

  var rangeErr = _svEditBarValidateRange(m.type, target);
  if (rangeErr) {
    var msgEl = document.getElementById("sv-measure-editbar-msg");
    if (msgEl) { msgEl.textContent = rangeErr; msgEl.className = "sv-measure-editbar-msg sv-measure-editbar-msg-error"; msgEl.style.display = ""; }
    return;
  }

  var editor = window.ACPStructureEditor;
  if (!editor || typeof editor.applyMeasuredEdit !== "function") return;
  var kind = _svEditBarKind(m.type);
  var result = editor.applyMeasuredEdit(kind, m.atoms.slice(), target, m.id);
  if (result && result.ok) {
    store.setEditPreview(null);
    var msgEl2 = document.getElementById("sv-measure-editbar-msg");
    if (msgEl2) { msgEl2.style.display = "none"; msgEl2.textContent = ""; }
  } else if (result && result.reason) {
    var failMsg = (typeof editor.reasonMessage === "function") ? editor.reasonMessage(result.reason) : result.reason;
    var msgEl3 = document.getElementById("sv-measure-editbar-msg");
    if (msgEl3) { msgEl3.textContent = failMsg; msgEl3.className = "sv-measure-editbar-msg sv-measure-editbar-msg-error"; msgEl3.style.display = ""; }
  }
}

function _svEditBarCancel() {
  _svEditBarHide();
}

(function() {
  var previewBtn = document.getElementById("sv-measure-editbar-preview");
  var applyBtn = document.getElementById("sv-measure-editbar-apply");
  var cancelBtn = document.getElementById("sv-measure-editbar-cancel");
  if (previewBtn) previewBtn.addEventListener("click", _svEditBarPreview);
  if (applyBtn) applyBtn.addEventListener("click", _svEditBarApply);
  if (cancelBtn) cancelBtn.addEventListener("click", _svEditBarCancel);
})();

// ── T6: Interaction-state gating ─────────────────────────────────────
window._svRefreshInteractionState = function() {
  var blocked = false;
  var msg = "";
  if (window.ACPStructureViewer && typeof window.ACPStructureViewer.editBlockReason === "function") {
    var reason = window.ACPStructureViewer.editBlockReason();
    if (reason && reason.blocked) { blocked = true; msg = reason.message || ""; }
  }
  var ids = ["btn-measure-distance", "btn-measure-angle", "btn-measure-dihedral"];
  for (var i = 0; i < ids.length; i++) {
    var el = document.getElementById(ids[i]);
    if (!el) continue;
    el.disabled = blocked;
    el.title = blocked ? msg : "";
    if (blocked) el.classList.add("sv-blocked");
    else el.classList.remove("sv-blocked");
  }
};
window.addEventListener("acp:interaction-state", function() { window._svRefreshInteractionState(); });

// ── T1: Store subscription ───────────────────────────────────────────
(function() {
  var store = window.ACPGeometryStore;
  if (!store) return;
  var _raf = (typeof requestAnimationFrame === "function") ? requestAnimationFrame : function(fn) { setTimeout(fn, 0); };
  store.subscribe(function(snap, change) {
    if (change === "load") {
      _svSyncGeometryToCanvas({ zoomTo: true });
    } else if (change === "coordinates") {
      _raf(function() { _svSyncGeometryToCanvas(); });
    } else if (change === "selection") {
      _svRenderSelectionOverlay();
    } else if (change === "measurement") {
      _svRenderMeasurementOverlays();
    } else if (change === "preview") {
      _raf(function() { _svSyncGeometryToCanvas(); });
    }
  });
})();

