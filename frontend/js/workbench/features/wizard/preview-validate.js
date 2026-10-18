/*
 * ACP Workbench — wizard slice S2: 预览/校验 (preview / validate).
 *
 * Extracted from features/wizard.js per plan todo 18 (boundary frozen by
 * task-16 evidence §1.3 / §2.2 / §2.3 / §3; drift register DRIFT-16-A/B).
 * This module is the SINGLE owner of the preview/validate slice:
 *   - the structure preview (list render/selection, warnings/errors, and the
 *     3D preview REBUILD `renderPreviewStructure3D`);
 *   - the method validation timing + entry (`createValidation` — schedule /
 *     do / refresh), replacing the per-`openMethodConfig` closure locals
 *     `validateTimer` / `_lastValidationResult`;
 *   - the hessian preview (`createHessianPreview`), replacing the
 *     per-`buildHessianFieldRow` closure locals `hessianPreviewTimer` /
 *     `hessianPreviewToken`;
 *   - the batch-config preview (`createBatchConfigPreview`), replacing the
 *     per-`buildLevelCard` closure locals `_previewDebounceTimer` /
 *     `_previewAbortCtrl` / `_PREVIEW_DEBOUNCE_MS` / `_previewGeneration`.
 * The method-config shell in wizard.js keeps thin same-name delegates, so
 * every call site is unchanged during migration.
 *
 * The 3D viewer CREATION (`previewViewer = ACPVisualization.mol3d.create`) is
 * deliberately left in wizard.js: it is T10's migration scope. This slice only
 * owns the preview REBUILD, which drives the shared page-global `previewViewer`
 * (declared in reaction-builder.js) through the global `initPreviewViewer`.
 *
 * H-4 fix (task-16 §7): a late batch-config preview must never write into a
 * detached/reopened form. `createBatchConfigPreview` aborts the previously
 * registered instance, and `abortBatchPreviews()` bumps the generation and
 * aborts the in-flight request; wizard.js calls it on method-config open and
 * on cancel/close (revertMethodDraft).
 *
 * Load order: after js/client/wizard-endpoints.js,
 * js/workbench/compat/api.js and js/workbench/features/reaction-builder.js
 * (page globals), before workbench/features/wizard.js and bootstrap.js.
 */
window.ACPWizardPreviewValidate = (function () {
  "use strict";

  // ══ S2 structure preview (1281-1609 lift) ═════════════════════════════════

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

  // ══ S2 validation (method-config validate sub-block 7132-7330 lift) ══════
  // A per-`openMethodConfig` instance, exactly mirroring the original closure
  // locals `validateTimer` / `_lastValidationResult` (so a late response from
  // session A can never mutate session B's last result).
  function createValidation(ctx) {
    var timer = null;
    var last = null;

    function schedule() {
      if (timer) clearTimeout(timer);
      // The outer PES picker only selects structure atoms/bonds. Keep its
      // selection mode and read-only task badge synchronized with this shared
      // protocol dialog.
      if (typeof updatePESSelectionForProtocol === "function") updatePESSelectionForProtocol();
      timer = setTimeout(function() {
        validate().then(refresh);
      }, 400);
    }

    async function validate() {
      var levels = {};
      (ctx.schema.method_levels || []).forEach(function(lvDef) {
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
          schema_id: ctx.schemaId, levels: levels
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
        last = body;
        return body;
      } catch(e) { return { valid: false, errors: [e.message], warnings: [] }; }
    }

    function refresh() {
      var vr = last;
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
      (ctx.schema.method_levels || []).forEach(function(lvDef) {
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

    return { schedule: schedule, validate: validate, refresh: refresh };
  }

  // ══ S2 hessian preview (buildHessianFieldRow sub-block lift) ══════════════
  // Per-field-row instance (mirrors `hessianPreviewTimer` /
  // `hessianPreviewToken`). `getCtx()` returns the live form context:
  // { previewBox, hint, mode, zh, lvDef, st }.
  function createHessianPreview(getCtx) {
    var timer = null;
    var token = 0;

    function setVisible(visible) {
      var c = getCtx();
      if (c.previewBox) c.previewBox.style.display = visible ? "" : "none";
      if (c.hint) c.hint.style.display = visible ? "" : "none";
    }

    function renderLoading() {
      var c = getCtx();
      setVisible(c.mode === "auto");
      if (c.mode !== "auto") return;
      c.previewBox.innerHTML = "";
      var row = document.createElement("div");
      row.className = "mc-hessian-preview-row";
      row.textContent = c.zh ? "\u6b63\u5728\u89e3\u6790\u2026" : "Parsing\u2026";
      c.previewBox.appendChild(row);
    }

    function renderRows(results, summary) {
      var c = getCtx();
      var previewBox = c.previewBox;
      var zh = c.zh;
      setVisible(true);
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

    function schedule() {
      var c = getCtx();
      if (timer) clearTimeout(timer);
      renderLoading();
      if (c.mode !== "auto") return;
      timer = setTimeout(function() {
        timer = null;
        fetchPreview();
      }, 300);
    }

    function fetchPreview() {
      var c = getCtx();
      if (c.mode !== "auto") return;
      var myToken = ++token;
      var structures = (wizardStructures || []).map(function(s) {
        var out = { name: s.name || "" };
        if (Array.isArray(s.symbols) && s.symbols.length) out.symbols = s.symbols;
        else if (s.formula) out.formula = s.formula;
        return out;
      });
      if (!structures.length) {
        setVisible(true);
        c.previewBox.innerHTML = "";
        var hintRow = document.createElement("div");
        hintRow.className = "mc-hessian-preview-row";
        hintRow.textContent = c.zh ? "\u6dfb\u52a0\u7ed3\u6784\u540e\u53ef\u9884\u89c8\u81ea\u52a8 Hessian \u7b56\u7565\uff1b\u5f53\u524d\u8bbe\u7f6e\u4ecd\u53ef\u4fdd\u5b58\u3002" : "Add structures to preview auto Hessian strategy; current settings are still saved.";
        c.previewBox.appendChild(hintRow);
        return;
      }
      var body = {
        schema_id: wizardState.method.schema_id || "",
        level_id: c.lvDef.level_id,
        recalc_hess: c.st.opt_recalc_hess,
        structures: structures,
      };
      // R3(b): endpoint adapter — URL/body/timeout and the errors[]
      // .message-join error text are frozen inside
      // ACPClient.wizardEndpoints.hessianPreview (the legacy fetch had no
      // timeout and never surfaced the FastAPI detail).
      window.ACPClient.wizardEndpoints.hessianPreview(body).then(function(data) {
        if (myToken !== token) return;
        renderRows(data.results || [], data.summary || {});
      }).catch(function(err) {
        if (myToken !== token) return;
        c.previewBox.innerHTML = "";
        var errRow = document.createElement("div");
        errRow.className = "mc-hessian-preview-row";
        errRow.textContent = (c.zh ? "\u9884\u89c8\u5931\u8d25" : "Preview failed") + ": " + err.message;
        c.previewBox.appendChild(errRow);
      });
    }

    return { setVisible: setVisible, renderLoading: renderLoading, renderRows: renderRows, schedule: schedule, fetchPreview: fetchPreview };
  }

  // ══ S2 batch-config preview (buildLevelCard sub-block 6557-6668 lift) ════
  // H-4: the module keeps a registry of live instances so close/reopen and a
  // rebuild can abort the previous in-flight request (generation + abort).
  var _batchPreviews = [];

  function createBatchConfigPreview(ctx) {
    // Abort the previously registered instance so a rebuild / modal reopen can
    // never have a late response write into the detached preview node (H-4).
    abortBatchPreviews();
    var state = { timer: null, abortCtrl: null, gen: 0 };
    var debounceMs = ctx.debounceMs != null ? ctx.debounceMs : 400;

    function abort() {
      if (state.timer) { clearTimeout(state.timer); state.timer = null; }
      state.gen++;
      if (state.abortCtrl) { try { state.abortCtrl.abort(); } catch(_e) {} state.abortCtrl = null; }
    }

    function fetch() {
      var previewFields = ctx.previewFields;
      if (state.timer) { clearTimeout(state.timer); state.timer = null; }
      previewFields.setAttribute("data-stale", "true");
      state.timer = setTimeout(function() {
        state.timer = null;
        if (state.abortCtrl) { try { state.abortCtrl.abort(); } catch(_e) {} }
        var abortCtrl = null;
        if (typeof AbortController !== "undefined") { abortCtrl = new AbortController(); }
        state.abortCtrl = abortCtrl;
        var myGen = ++state.gen;
        var tmpPayload = {};
        try { applyBatchOptimizeMethodFields(tmpPayload); } catch(_e) { return; }
        // R3(b): endpoint adapter freezes URL/body/timeout and the
        // "HTTP <status>" error text; the AbortController orchestration
        // (stale-preview abort) stays here, signal forwarded as-is.
        window.ACPClient.wizardEndpoints
          .batchConfigPreview({ method: tmpPayload }, abortCtrl ? { signal: abortCtrl.signal } : undefined)
          .then(function(data) {
            if (myGen !== state.gen) return;
            if (data.schema !== "batch_optimize_preview_v1") return;
            ctx.previewFields.removeAttribute("data-stale");
            renderServer(data);
          })
          .catch(function(err) {
            if (err && err.name === "AbortError") return;
            if (myGen !== state.gen) return;
            renderFailure((err && err.message) || String(err));
          });
      }, debounceMs);
    }

    function renderServer(data) {
      var previewFields = ctx.previewFields;
      previewFields.innerHTML = "";
      var zh = currentLang === "zh-CN";
      var role = ctx.getActiveRole();
      var roleData = (data.roles || {})[role];
      if (!roleData) { renderLocal(); return; }
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

    function renderFailure(errMsg) {
      var previewFields = ctx.previewFields;
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
      renderLocal(false);
    }

    function renderLocal() {
      var previewFields = ctx.previewFields;
      var zh = currentLang === "zh-CN";
      var role = ctx.getActiveRole();
      var rd = (ctx.getRoles() || {})[role] || {};
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

    var instance = { fetch: fetch, renderServer: renderServer, renderFailure: renderFailure, renderLocal: renderLocal, abort: abort };
    _batchPreviews.push(instance);
    return instance;
  }

  function abortBatchPreviews() {
    var list = _batchPreviews;
    _batchPreviews = [];
    list.forEach(function(i) { i.abort(); });
  }

  return {
    // structure preview
    renderStructurePreview: renderStructurePreview,
    selectPreviewStructure: selectPreviewStructure,
    _refreshWizardInputForm: _refreshWizardInputForm,
    escapeAttr: escapeAttr,
    updateFooterSelectedCount: updateFooterSelectedCount,
    _openInputListOverlay: _openInputListOverlay,
    _closeInputListOverlay: _closeInputListOverlay,
    _toggleInputListOverlay: _toggleInputListOverlay,
    _togglePreviewAtomLabels: _togglePreviewAtomLabels,
    renderLoadedStructuresList: renderLoadedStructuresList,
    updatePreviewActionState: updatePreviewActionState,
    removeSelectedStructure: removeSelectedStructure,
    clearParsedStructures: clearParsedStructures,
    renderPreviewStructure3D: renderPreviewStructure3D,
    // validation
    createValidation: createValidation,
    // hessian preview
    createHessianPreview: createHessianPreview,
    // batch-config preview (+ H-4 lifecycle abort)
    createBatchConfigPreview: createBatchConfigPreview,
    abortBatchPreviews: abortBatchPreviews,
  };
})();
