function setupEventListeners() {
  document.getElementById("lang-toggle").addEventListener("click", function() {
    currentLang = currentLang === "zh-CN" ? "en-US" : "zh-CN";
    localStorage.setItem("acp-lang", currentLang);
    applyI18n();
    renderNodeSelector();
    if (lastStatus) {
      const pill = statusPillMode(lastStatus);
      const node = document.getElementById("status-pill");
      node.className = "status-pill " + pill.className;
      node.textContent = pill.label;
      document.getElementById("server-summary").textContent = t("server.summary", {
        host: lastStatus.host || "127.0.0.1",
        port: lastStatus.port || "8765",
        queued: lastStatus.queue && typeof lastStatus.queue.queued === "number" ? lastStatus.queue.queued : 0,
        running: lastStatus.queue && typeof lastStatus.queue.running === "number" ? lastStatus.queue.running : 0
      });
    }
  });

  document.getElementById("project-select").addEventListener("change", async function(event) {
    selectedProjectId = event.target.value;
    if (event.target.selectedIndex >= 0 && event.target.options[event.target.selectedIndex]) {
      event.target.title = event.target.options[event.target.selectedIndex].textContent || "";
    }
    loadTaskViewPrefs(selectedProjectId);
    updateDeleteProjectButton();
    await refreshJobs();
    if (jobsCache.length) {
      await selectJob(jobsCache[0].id || jobsCache[0].job_id);
    } else {
      selectedJobId = "";
      lastSelectedJob = null;
      lastFileListing = [];
      folderCache = {};
      folderState = {};
      updateInfoPanel(null);
      renderFileTree([], "");
      renderLogPlaceholder();
      clearViewer();
    }
  });

  document.getElementById("modal-project-select").addEventListener("change", function() {
    updateModalProjectHint();
    renderResultsProjectOptions();
    // 目标项目联动: refresh immediately only while the filter still points
    // at "目标项目"; an explicit 项目 B / 全部项目 choice stays untouched.
    var projEl = document.getElementById("results-project");
    if (projEl && projEl.value === "target" && resultsLoaded) {
      loadStructureSources(true);
    } else if (!projEl && resultsPicker) {
      resultsPicker.setProject(resultsProjectTargetId());
    }
  });

  document.getElementById("btn-new-calc").addEventListener("click", openModal);
  document.getElementById("modal-close").addEventListener("click", closeJobModalGuarded);
  document.getElementById("modal-cancel").addEventListener("click", closeJobModalGuarded);
  document.getElementById("wizard-drafts-open").addEventListener("click", openWizardDrafts);
  document.getElementById("wizard-drafts-new").addEventListener("click", startBlankWizardDraft);
  document.getElementById("wizard-drafts-close").addEventListener("click", function() { document.getElementById("wizard-drafts-modal").style.display = "none"; });
  document.getElementById("wizard-drafts-modal").addEventListener("mousedown", function(event) {
    if (event.target.id === "wizard-drafts-modal") event.currentTarget.style.display = "none";
  });
  ["input", "change", "click"].forEach(function(kind) {
    document.addEventListener(kind, function(event) {
      var target = event.target;
      if (!target || !target.closest || !target.closest("#job-modal, #workflow-config-modal, #method-config-modal")) return;
      if (target.id === "job-modal" || target.id === "modal-close" || target.id === "modal-cancel" || target.id === "wizard-drafts-open" || target.id === "modal-submit") return;
      _scheduleWizardDraftAutosave();
    });
  });
  document.getElementById("modal-submit").addEventListener("click", handleModalSubmit);
  document.getElementById("modal-back").addEventListener("click", function() {
    if (window.ACPJobEditor && ACPJobEditor.isActive()) {
      ACPJobEditor.setStep(ACPJobEditor.currentStep() - 1);
      return;
    }
    setCreateWizardStep(createWizardStep - 1);
  });
  document.querySelectorAll("#create-stepper .create-step").forEach(function(btn) {
    btn.addEventListener("click", function() {
      var target = Number(btn.getAttribute("data-create-step"));
      if (window.ACPJobEditor && ACPJobEditor.isActive()) {
        if (target <= ACPJobEditor.currentStep()) ACPJobEditor.setStep(target);
        return;
      }
      if (target <= createWizardStep) setCreateWizardStep(target);
    });
  });
  var editCopyBtn = document.getElementById("edit-original-copy");
  if (editCopyBtn) {
    editCopyBtn.addEventListener("click", function() {
      if (window.ACPJobEditor) ACPJobEditor.copyOriginalToStructureInput();
    });
  }
  var previewResetBtn = document.getElementById("preview-reset-view");
  if (previewResetBtn) {
    previewResetBtn.addEventListener("click", function() {
      if (!previewViewer) return;
      frame3DViewer(previewViewer, { zoomFactor: PREVIEW_ZOOM_FACTOR });
    });
  }
  var previewExpandBtn = document.getElementById("preview-expand-view");
  if (previewExpandBtn) {
    previewExpandBtn.addEventListener("click", function() {
      var shell = document.getElementById("structure-preview");
      if (!shell) return;
      var expanded = shell.classList.toggle("preview-expanded");
      previewExpandBtn.setAttribute("aria-pressed", expanded ? "true" : "false");
      previewExpandBtn.setAttribute("data-i18n", expanded ? "edit.collapse_preview" : "edit.expand_preview");
      previewExpandBtn.textContent = t(expanded ? "edit.collapse_preview" : "edit.expand_preview");
      requestAnimationFrame(function() {
        if (previewViewer) frame3DViewer(previewViewer, { zoomFactor: PREVIEW_ZOOM_FACTOR });
      });
    });
  }
  document.querySelectorAll(".pes-function-btn").forEach(function(button) {
    button.addEventListener("click", function() {
      setPESSelectionKind(button.getAttribute("data-selection-kind"));
    });
  });
  var pesClearBtn = document.getElementById("pes-clear-selection");
  if (pesClearBtn) pesClearBtn.addEventListener("click", function() {
    pesSelectionState.selectedAtoms = [];
    if (typeof s2scanState !== "undefined" && s2scanState) {
      s2scanState.selectedAtoms = [];
      s2scanState.selA = null;
      s2scanState.selB = null;
    }
    renderPESPreviewSelection();
    updatePESSelectionSummary();
    updatePESConfigNote();
  });
  document.getElementById("job-modal").addEventListener("mousedown", function(event) {
    if (event.target.id === "job-modal") closeJobModalGuarded();
  });

  document.getElementById("btn-config-workflow").addEventListener("click", openWorkflowConfig);
  document.getElementById("btn-config-method").addEventListener("click", openMethodConfig);
  document.getElementById("wf-config-close").addEventListener("click", function() { document.getElementById("workflow-config-modal").style.display = "none"; });
  document.getElementById("wf-config-cancel").addEventListener("click", function() { document.getElementById("workflow-config-modal").style.display = "none"; });
  document.getElementById("wf-config-ok").addEventListener("click", function() {
    document.getElementById("workflow-config-modal").style.display = "none";
    updateConfigCards();
  });
  document.getElementById("mt-config-close").addEventListener("click", function() {
    if (typeof window._revertMethodDraft === "function") window._revertMethodDraft();
    else document.getElementById("method-config-modal").style.display = "none";
  });
  document.getElementById("mt-config-cancel").addEventListener("click", function() {
    if (typeof window._revertMethodDraft === "function") window._revertMethodDraft();
    else document.getElementById("method-config-modal").style.display = "none";
  });
  document.getElementById("mt-config-ok").addEventListener("click", function() {
    document.getElementById("method-config-modal").style.display = "none";
    updateConfigCards();
  });

  document.getElementById("modal-node-select").addEventListener("change", onNodeSelectChange);
  // T12 stage mirror: re-derive the node preselect when the source job id
  // changes (blur after edit). Guarded — the element exists in the modal.
  var stageSourceJobEl = document.getElementById("stage-source-job");
  if (stageSourceJobEl) {
    stageSourceJobEl.addEventListener("change", function() {
      preselectNodeFromSourceJob(this.value);
    });
  }

  document.getElementById("btn-new-project").addEventListener("click", openProjectModal);
  document.getElementById("btn-delete-project").addEventListener("click", openDeleteProjectModal);
  document.getElementById("project-modal-close").addEventListener("click", closeProjectModal);
  document.getElementById("project-modal-cancel").addEventListener("click", closeProjectModal);
  document.getElementById("project-modal-submit").addEventListener("click", submitProjectModal);
  document.getElementById("project-modal").addEventListener("mousedown", function(event) {
    if (event.target.id === "project-modal") closeProjectModal();
  });
  document.getElementById("project-modal-name").addEventListener("keydown", function(e) {
    if (e.key === "Enter") submitProjectModal();
  });

  document.getElementById("delete-project-modal-close").addEventListener("click", closeDeleteProjectModal);
  document.getElementById("delete-project-modal-cancel").addEventListener("click", closeDeleteProjectModal);
  document.getElementById("delete-project-modal-submit").addEventListener("click", submitDeleteProjectModal);
  document.getElementById("delete-project-modal").addEventListener("mousedown", function(event) {
    if (event.target.id === "delete-project-modal") closeDeleteProjectModal();
  });

  document.getElementById("delete-job-modal-close").addEventListener("click", closeDeleteJobModal);
  document.getElementById("delete-job-modal-cancel").addEventListener("click", closeDeleteJobModal);
  document.getElementById("delete-job-modal-submit").addEventListener("click", submitDeleteJobModal);
  document.getElementById("delete-job-modal").addEventListener("mousedown", function(event) {
    if (event.target.id === "delete-job-modal") closeDeleteJobModal();
  });

  document.getElementById("purge-modal-close").addEventListener("click", closePurgeModal);
  document.getElementById("purge-modal-cancel").addEventListener("click", closePurgeModal);
  document.getElementById("purge-modal-submit").addEventListener("click", submitPurgeModal);
  document.getElementById("purge-modal").addEventListener("mousedown", function(event) {
    if (event.target.id === "purge-modal") closePurgeModal();
  });
  document.getElementById("queue-batch-purge").addEventListener("click", openPurgeModal);
  document.getElementById("mobile-queue-batch-purge").addEventListener("click", openPurgeModal);

  document.getElementById("target-project-modal-close").addEventListener("click", closeTargetProjectModal);
  document.getElementById("target-project-modal-cancel").addEventListener("click", closeTargetProjectModal);
  document.getElementById("target-project-modal-submit").addEventListener("click", submitTargetProjectModal);
  document.getElementById("target-project-modal").addEventListener("mousedown", function(event) {
    if (event.target.id === "target-project-modal") closeTargetProjectModal();
  });

  // Detail drawer close handlers
  document.getElementById("detail-drawer-close").addEventListener("click", closeDetailDrawer);
  document.getElementById("detail-drawer-overlay").addEventListener("mousedown", function(event) {
    if (event.target.id === "detail-drawer-overlay") closeDetailDrawer();
  });

  // System & Resources drawer handlers
  document.getElementById("btn-sysres").addEventListener("click", openSysresDrawer);
  document.getElementById("sysres-drawer-close").addEventListener("click", closeSysresDrawer);
  document.getElementById("sysres-drawer-overlay").addEventListener("mousedown", function(event) {
    if (event.target.id === "sysres-drawer-overlay") closeSysresDrawer();
  });
  document.getElementById("sysres-refresh-nodes").addEventListener("click", loadNodes);
  document.getElementById("sysres-refresh-backends").addEventListener("click", loadBackends);

  var sourceTabs = Array.from(document.querySelectorAll(".input-mode-tab"));
  sourceTabs.forEach(function(tab, tabIndex) {
    tab.addEventListener("click", function() {
      setWizardInputMode(tab.getAttribute("data-input-mode"));
    });
    tab.addEventListener("keydown", function(event) {
      var nextIndex = null;
      if (event.key === "ArrowRight") nextIndex = (tabIndex + 1) % sourceTabs.length;
      if (event.key === "ArrowLeft") nextIndex = (tabIndex - 1 + sourceTabs.length) % sourceTabs.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = sourceTabs.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      sourceTabs[nextIndex].focus();
      setWizardInputMode(sourceTabs[nextIndex].getAttribute("data-input-mode"));
    });
  });

  document.querySelectorAll(".library-filter").forEach(function(button) {
    button.addEventListener("click", function() {
      setLibrarySourceGroup(button.getAttribute("data-source-group") || null);
    });
  });

  document.getElementById("modal-structure-input").addEventListener("input", scheduleParseStructures);
  if (document.getElementById("preview-remove-selected")) document.getElementById("preview-remove-selected").addEventListener("click", removeSelectedStructure);
  if (document.getElementById("preview-clear-all")) document.getElementById("preview-clear-all").addEventListener("click", clearParsedStructures);
  document.getElementById("preview-remove-current").addEventListener("click", removeSelectedStructure);
  document.getElementById("preview-view-coordinates").addEventListener("click", function() {
    var disclosure = document.getElementById("source-coordinate-disclosure");
    if (!disclosure) return;
    disclosure.style.display = "block";
    disclosure.open = true;
    var menu = document.getElementById("preview-more-menu");
    if (menu) menu.open = false;
  });
  document.getElementById("loaded-structures-clear").addEventListener("click", function() {
    if (wizardStructures.length && window.confirm("仅从本次任务移除全部 " + wizardStructures.length + " 个结构；不会删除来源任务结果。是否继续？")) clearParsedStructures();
  });
  document.getElementById("wizard-open-trash").addEventListener("click", function() {
    if (wizardInputMode !== "task") setWizardInputMode("task");
    _enterWizardTrashView();
  });
  document.getElementById("preview-rename-structure").addEventListener("click", function() {
    var structure = currentWizardStructure(); if (!structure) return;
    var value = window.prompt("重命名本次输入结构（不会修改来源结构）", structure.name || "");
    if (value === null) return;
    structure.name = value.trim() || structure.name || "mol_" + (wizardSelectedStructureIndex + 1);
    structure.input_modified = true; selectPreviewStructure(wizardSelectedStructureIndex, true);
  });
  document.getElementById("preview-edit-structure").addEventListener("click", function() {
    var structure = currentWizardStructure(); if (!structure) return;
    if (!structure.original_xyz) structure.original_xyz = structure.xyz || "";
    structure.input_modified = true;
    document.getElementById("modal-structure-input").value = structure.xyz || "";
    setWizardInputMode("structure");
    showParseStatus("已复制当前结构到结构输入；解析保存后仅更新本次任务副本。", false);
    document.getElementById("modal-structure-input").focus();
  });
  var resultsProject = document.getElementById("results-project");
  if (resultsProject) resultsProject.addEventListener("change", function() {
    loadStructureSources(true);
  });
  var resultsSearch = document.getElementById("results-search");
  if (resultsSearch) resultsSearch.addEventListener("input", scheduleResultsSearch);

  // T5: bind task-view toolbar controls (search, group, sort, filter)
  _bindTaskViewToolbar();

  // T9: batch ops toolbar
  document.getElementById("batch-add-tags").addEventListener("click", _batchAddTags);
  document.getElementById("batch-remove-tags").addEventListener("click", _batchRemoveTags);
  document.getElementById("batch-archive").addEventListener("click", _batchArchive);
  document.getElementById("batch-unarchive").addEventListener("click", _batchUnarchive);
  document.getElementById("batch-set-molecule").addEventListener("click", _batchSetMolecule);
  document.getElementById("batch-tag-input").addEventListener("keydown", function(e) { if (e.key === "Enter") _batchAddTags(); });
  document.getElementById("batch-molecule-input").addEventListener("keydown", function(e) { if (e.key === "Enter") _batchSetMolecule(); });

  // T9: archive toggle
  var archivedEl = document.getElementById("task-view-archived");
  if (archivedEl) archivedEl.addEventListener("change", function() {
    var next = _clonePrefs();
    next.archived = archivedEl.value;
    taskViewPrefs = next;
    saveTaskViewPrefs();
    refreshJobs();
  });

  // T9: export menu
  document.getElementById("task-view-export-btn").addEventListener("click", function(e) { e.stopPropagation(); _toggleExportMenu(); });
  document.getElementById("task-view-export-csv").addEventListener("click", _exportCSV);
  document.getElementById("task-view-export-json").addEventListener("click", _exportJSON);

  // T10: saved views menu
  document.getElementById("task-view-views-btn").addEventListener("click", function(e) { e.stopPropagation(); _toggleViewsMenu(); });
  document.getElementById("task-view-views-save").addEventListener("click", function(e) { e.stopPropagation(); _saveCurrentView(); });

  // T9: tag management modal
  document.getElementById("tag-mgmt-modal-close").addEventListener("click", _closeTagManagement);
  document.getElementById("tag-mgmt-modal-close-btn").addEventListener("click", _closeTagManagement);
  document.getElementById("tag-mgmt-modal").addEventListener("mousedown", function(event) {
    if (event.target.id === "tag-mgmt-modal") _closeTagManagement();
  });
  // T11: auto-tag rules tab
  document.getElementById("tag-mgmt-tab-tags").addEventListener("click", function() { _switchMgmtTab("tags"); });
  document.getElementById("tag-mgmt-tab-auto").addEventListener("click", function() { _switchMgmtTab("auto"); });
  document.getElementById("auto-tag-add-btn").addEventListener("click", function() {
    var pid = _getSelectedProjectId();
    if (pid) _addAutoTagRule(pid);
  });
  document.getElementById("auto-tag-apply-btn").addEventListener("click", _applyAutoTagRulesBackfill);

  var dropzone = document.getElementById("upload-dropzone");
  var fileInput = document.getElementById("upload-file-input");
  dropzone.addEventListener("click", function() { fileInput.click(); });
  fileInput.addEventListener("change", function() {
    if (fileInput.files.length) {
      var file = fileInput.files[0];
      if (!isAcceptedStructureFile(file)) {
        rejectUnsupportedFile(file);
        fileInput.value = "";
        return;
      }
      wizardUploadFile = file;
      var status = document.querySelector(".upload-dropzone span");
      if (status) status.textContent = wizardUploadFile.name;
      parseStructuresPreview();
    }
  });
  dropzone.addEventListener("dragover", function(e) {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
  dropzone.addEventListener("dragleave", function() { dropzone.classList.remove("dragover"); });
  dropzone.addEventListener("drop", function(e) {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files.length) {
      var file = e.dataTransfer.files[0];
      if (!isAcceptedStructureFile(file)) {
        rejectUnsupportedFile(file);
        return;
      }
      wizardUploadFile = file;
      var status = document.querySelector(".upload-dropzone span");
      if (status) status.textContent = wizardUploadFile.name;
      parseStructuresPreview();
    }
  });

  document.querySelectorAll(".canvas-tab").forEach(function(btn) {
    btn.addEventListener("click", function() { setViewerTab(btn.getAttribute("data-tab")); });
  });

  document.getElementById("btn-fit").addEventListener("click", function() {
    if (viewer) {
      frame3DViewer(viewer);
    }
  });

  document.getElementById("btn-reset-view").addEventListener("click", function() {
    restoreInitialView();
  });

  document.getElementById("style-preset-select").addEventListener("change", function() {
    molDoc.style = this.value;
    renderMolDoc();
  });

  document.getElementById("btn-bg-toggle").addEventListener("click", function() {
    syncBgIndex();
    bgIndex = (bgIndex + 1) % BG_COLORS.length;
    molDoc.bgColor = BG_COLORS[bgIndex];
    if (viewer) {
      viewer.setBackgroundColor(molDoc.bgColor);
      viewer.render();
    }
  });

  document.getElementById("btn-screenshot").addEventListener("click", function() {
    if (!viewer) return;
    const uri = viewer.pngURI();
    const a = document.createElement("a");
    a.href = uri;
    a.download = "acp_screenshot.png";
    a.click();
  });

  document.getElementById("btn-labels").addEventListener("click", function() {
    labelMode = labelMode === "none" ? "elem" : labelMode === "elem" ? "index" : "none";
    renderMolDoc();
  });

  document.getElementById("btn-mode-select").addEventListener("click", function() {
    setMode("select");
  });

  document.getElementById("btn-measure-distance").addEventListener("click", function() {
    molDoc.measureType = "distance";
    if (window.ACPGeometryStore) window.ACPGeometryStore.clearSelection();
    setMode("measure");
  });

  document.getElementById("btn-measure-angle").addEventListener("click", function() {
    molDoc.measureType = "angle";
    if (window.ACPGeometryStore) window.ACPGeometryStore.clearSelection();
    setMode("measure");
  });

  document.getElementById("btn-measure-dihedral").addEventListener("click", function() {
    molDoc.measureType = "dihedral";
    if (window.ACPGeometryStore) window.ACPGeometryStore.clearSelection();
    setMode("measure");
  });

  /* measure-results dropdown toggle */
  (function() {
    var btn = document.getElementById("btn-measure-list");
    var panel = document.getElementById("measure-dropdown-panel");
    if (btn && panel) {
      btn.addEventListener("click", function(e) {
        e.stopPropagation();
        panel.classList.toggle("open");
      });
      document.addEventListener("click", function(e) {
        if (!panel.contains(e.target) && e.target !== btn) {
          panel.classList.remove("open");
        }
      });
    }
  })();

  document.getElementById("frame-prev").addEventListener("click", function() { setFrame(molDoc.currentFrame - 1); });
  document.getElementById("frame-next").addEventListener("click", function() { setFrame(molDoc.currentFrame + 1); });
  document.getElementById("frame-slider").addEventListener("input", function() { setFrame(parseInt(this.value, 10) || 0); });
  document.getElementById("frame-play").addEventListener("click", toggleFramePlayback);

  document.querySelectorAll(".mobile-tab").forEach(function(tab) {
    tab.addEventListener("click", function() {
      const target = tab.getAttribute("data-mobile-panel");
      document.querySelectorAll(".mobile-tab").forEach(function(btn) { btn.classList.toggle("active", btn === tab); });
      document.querySelectorAll(".mobile-panel-body").forEach(function(body) { body.classList.toggle("active", body.getAttribute("data-mobile-body") === target); });
    });
  });

  document.addEventListener("keydown", function(e) {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;
    // Energy workspace shortcuts
    if (activeViewerTab === "energy" && energyGraphState.data) {
      if ((e.ctrlKey || e.metaKey) && (e.key === "r" || e.key === "R")) { e.preventDefault(); renderEnergyGraphWorkspace(lastSelectedJob); return; }
      if (e.key === "f" || e.key === "F") { energyGraphSetDomains("fit"); e.preventDefault(); return; }
      if (e.key === "b" || e.key === "B") { activatePlotTool(energyGraphState.viewMode === "box" ? "" : "box"); e.preventDefault(); return; }
      if (e.key === "v" || e.key === "V") { activatePlotTool(energyGraphState.viewMode === "pan" ? "" : "pan"); e.preventDefault(); return; }
      if (e.key === "r" || e.key === "R") { energyGraphSetDomains("reset"); e.preventDefault(); return; }
      if (e.key === "l" || e.key === "L") {
        energyGraphState.annotationMode = energyGraphState.annotationMode === "hide" ? "all" : "hide";
        var container = getEnergyViewerContainer();
        if (container) {
          var svg = container.querySelector("[data-energy-chart]");
          if (svg) { svg.classList.toggle("labels-hidden", energyGraphState.annotationMode === "hide"); }
          var labelsBtn = container.querySelector('[data-energy-action="labels"]');
          if (labelsBtn) labelsBtn.classList.toggle("active", energyGraphState.annotationMode !== "hide");
        }
        e.preventDefault(); return;
      }
      if (e.key === " " && !e.repeat && e.target.tagName !== "BUTTON") {
        e.preventDefault();
        energyGraphState.priorViewMode = energyGraphState.viewMode;
        activatePlotTool("pan");
        return;
      }
      if (e.key === "ArrowLeft") { var d = energyGraphState.data; var n = energyGraphCurrentNode(d); if (n) { var ns = d.nodes || []; var idx = ns.findIndex(function(item) { return String(item.id) === String(energyGraphState.selectedNodeId); }); if (idx < 0) idx = ns.findIndex(function(item) { return Number(item.frame_index) === Number(n.frame_index); }); if (idx > 0) { energyGraphState.liveFollow = false; energyGraphSelectFrame(ns[idx - 1].frame_index, undefined, ns[idx - 1].id); } } e.preventDefault(); return; }
      if (e.key === "ArrowRight") { var d2 = energyGraphState.data; var n2 = energyGraphCurrentNode(d2); if (n2) { var ns2 = d2.nodes || []; var idx2 = ns2.findIndex(function(item) { return String(item.id) === String(energyGraphState.selectedNodeId); }); if (idx2 < 0) idx2 = ns2.findIndex(function(item) { return Number(item.frame_index) === Number(n2.frame_index); }); if (idx2 >= 0 && idx2 < ns2.length - 1) { energyGraphState.liveFollow = false; energyGraphSelectFrame(ns2[idx2 + 1].frame_index, undefined, ns2[idx2 + 1].id); } } e.preventDefault(); return; }
    }
    switch (e.key) {
      case "f":
      case "F":
        if (viewer) { frame3DViewer(viewer); }
        break;
      case "r":
      case "R":
        restoreInitialView();
        break;
      case "Escape":
        closeSysresDrawer();
        closeDetailDrawer();
        if (window.ACPGeometryStore) window.ACPGeometryStore.clearSelection();
        renderMolDoc();
        renderMeasurementList();
        updateInfoPanel(lastSelectedJob);
        break;
      case "1":
        setMode("view");
        break;
      case "2":
        setMode("select");
        break;
      case "3":
        molDoc.measureType = "distance";
        setMode("measure");
        break;
      case "4":
        molDoc.measureType = "angle";
        setMode("measure");
        break;
      case "5":
        molDoc.measureType = "dihedral";
        setMode("measure");
        break;
    }
  });

  // Global delegated tooltip — bound ONCE at init (300ms show / 100ms hide)
  (function() {
    var plotTip = null;
    var tipShowTimer = null;
    var tipHideTimer = null;
    var currentTarget = null;
    function ensureTip() {
      if (plotTip) return plotTip;
      plotTip = document.createElement("div");
      plotTip.className = "plot-tip";
      document.body.appendChild(plotTip);
      return plotTip;
    }
    function showTip(el) {
      var tip = ensureTip();
      var text = el.getAttribute("data-tip") || "";
      if (!text) return;
      var keys = el.getAttribute("data-tip-keys") || "";
      var html = escapeHtml(text);
      if (keys) {
        html += '<span class="plot-tip-keys">' + keys.split("+").map(function(k) { return '<span class="plot-tip-key">' + escapeHtml(k.trim()) + '</span>'; }).join("") + '</span>';
      }
      tip.innerHTML = html;
      tip.classList.remove("show");
      var rect = el.getBoundingClientRect();
      var tipW = 240, tipH = 40;
      var left = rect.left + rect.width / 2 - tipW / 2;
      var top = rect.top - tipH - 8;
      if (top < 4) { top = rect.bottom + 8; }
      if (left < 4) left = 4;
      if (left + tipW > window.innerWidth - 4) left = window.innerWidth - tipW - 4;
      tip.style.left = left + "px";
      tip.style.top = top + "px";
      tip.style.maxWidth = tipW + "px";
      requestAnimationFrame(function() { tip.classList.add("show"); });
      if (el.hasAttribute("title")) {
        el.setAttribute("data-title", el.getAttribute("title"));
        el.removeAttribute("title");
      }
    }
    function hideTip() {
      if (tipShowTimer) { clearTimeout(tipShowTimer); tipShowTimer = null; }
      if (plotTip) plotTip.classList.remove("show");
      if (currentTarget && currentTarget.hasAttribute("data-title")) {
        currentTarget.setAttribute("title", currentTarget.getAttribute("data-title"));
        currentTarget.removeAttribute("data-title");
      }
      currentTarget = null;
    }
    document.addEventListener("mouseover", function(e) {
      var el = e.target.closest ? e.target.closest("[data-tip]") : null;
      if (!el) return;
      if (tipHideTimer) { clearTimeout(tipHideTimer); tipHideTimer = null; }
      if (el === currentTarget) return;
      hideTip();
      currentTarget = el;
      tipShowTimer = setTimeout(function() { tipShowTimer = null; showTip(el); }, 300);
    });
    document.addEventListener("mouseout", function(e) {
      var el = e.target.closest ? e.target.closest("[data-tip]") : null;
      if (!el) return;
      tipHideTimer = setTimeout(function() { tipHideTimer = null; hideTip(); }, 100);
    });
    document.addEventListener("mousedown", function() { hideTip(); }, true);
  })();

  document.addEventListener("keyup", function(e) {
    if (e.key === " " && activeViewerTab === "energy" && energyGraphState.viewMode === "pan") {
      e.preventDefault();
      activatePlotTool(energyGraphState.priorViewMode || "");
      energyGraphState.priorViewMode = "";
    }
  });

  setupMechanismSectionListeners();
}


// ── Bootstrap on DOMContentLoaded ──
window.addEventListener("DOMContentLoaded", async function() {
  applyI18n();
  applyAppSection(loadAppSection());
  renderLogPlaceholder();
  syncBgIndex();
  clearViewer();
  document.getElementById("style-preset-select").value = molDoc.style;
  setMode("view");

  async function safeLoad(name, fn) {
    try {
      await fn();
    } catch (e) {
      console.error(name + " failed:", e);
    }
  }

  // Bind interaction before any network request: the shell remains usable
  // even when a catalog, software discovery or a selected remote job stalls.
  setupEventListeners();
  setupStageBatchListeners();
  document.addEventListener("visibilitychange", function() { if (!document.hidden) startPolling(); });
  safeLoad("loadStatus", loadStatus);
  safeLoad("loadProjects", loadProjects);
  safeLoad("initializeMethodDefaults", initializeMethodDefaults);
  safeLoad("refreshJobs", refreshJobs).then(function() {
    if (jobsCache.length && !selectedJobId) {
      safeLoad("selectJob", function() { return selectJob(jobsCache[0].id || jobsCache[0].job_id); });
    }
  });
  startPolling(true);

  // --- panel layout: column resize handles + vertical splitters + collapse ---
  (function initLayout() {
    makeColResizeHandle(document.getElementById("left-resize-handle"), "left");
    makeColResizeHandle(document.getElementById("right-resize-handle"), "right");
    makeVSplitter(document.getElementById("v-splitter-left"), "left_log");
    makeVSplitter(document.getElementById("v-splitter-right"), "right_info");

    const btnL = document.getElementById("btn-toggle-left");
    const btnR = document.getElementById("btn-toggle-right");
    if (btnL) btnL.addEventListener("click", function(){ toggleSidebar("left"); });
    if (btnR) btnR.addEventListener("click", function(){ toggleSidebar("right"); });

    document.querySelectorAll(".panel-collapse-btn").forEach(function(btn) {
      btn.addEventListener("click", function() {
        const key = String(btn.getAttribute("data-panel-key") || "");
        const st = layoutState.panels[key];
        if (!st) return;
        st.collapsed = !st.collapsed;
        saveLayoutState();
        applyLayout();
      });
    });

    // collapsed panels are a single clickable row: click anywhere to expand
    document.querySelectorAll("[data-panel-key]").forEach(function(sec) {
      if (sec.classList.contains("panel-collapse-btn")) return; // buttons bound above
      sec.addEventListener("click", function(e) {
        if (!sec.classList.contains("collapsed")) return;
        if (e.target.closest("button")) return; // let refresh/other buttons work
        const key = String(sec.getAttribute("data-panel-key") || "");
        const st = layoutState.panels[key];
        if (!st) return;
        st.collapsed = false;
        saveLayoutState();
        applyLayout();
      });
    });

    // initial apply (restores persisted widths/collapse/heights; replaces the
    // old setWidth(loadWidth()) from the legacy initResize)
    applyLayout();

    // Global resize: single rAF-throttled path that calls applyLayout()
    // (which already calls viewer.resize/render when the viewer exists).
    // This ensures crossing the 1023px breakpoint clears stale inline
    // grid-template-columns without a page refresh.
    var _resizeRafId = 0;
    window.addEventListener("resize", function() {
      if (_resizeRafId) return;
      _resizeRafId = requestAnimationFrame(function() {
        _resizeRafId = 0;
        applyLayout();
      });
    });
  })();
});
