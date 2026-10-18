/*
 * ACP Workbench — wizard slice S1: 上传/解析 (upload / parse).
 *
 * Extracted from features/wizard.js per plan todo 17 (boundary frozen by
 * task-16 evidence §1.2 / §2.2 / §3). This module is the SINGLE state owner
 * of the parse slice:
 *   parseSeqToken, parseDebounceTimer, lastParsedStructureInput,
 *   lastParsedUploadName
 * and the SINGLE parse entry is `parseStructuresPreview` (with
 * `scheduleParseStructures` as its debounced wrapper). The old global function
 * names stay callable through thin delegates left in features/wizard.js.
 *
 * Preserved contract (task-16 §3.3(a) / §4 P-4, P-6):
 *   - upload: POST {API_BASE}/uploads, FormData, no explicit Content-Type,
 *     timeoutMs:0 (no timeout) — body passthrough untouched.
 *   - text parse: POST {API_BASE}/structures/parse, JSON
 *     {content, format:"auto", filename}, timeoutMs:0, NO resp.ok check
 *     (a JSON error body resolves and flows through the ok:false path).
 *   - upload error text: `err.detail || "Upload failed"` — the literal stays
 *     at this call site exactly like the pre-migration code.
 *   - parse error text: `err.message`.
 *
 * H-3 fix (task-16 §7): `invalidatePendingParse()` bumps the parse token and
 * clears the debounce timer. `closeModal` (draft/form slice) now calls it, so
 * a parse that resolves after the wizard is closed can no longer write into a
 * session that is hidden or has been reopened — the late write is dropped by
 * the existing `myToken !== parseSeqToken` guards.
 *
 * Load order: after js/client/wizard-endpoints.js and
 * js/workbench/features/reaction-builder.js (page globals), before
 * workbench/features/wizard.js and bootstrap.js.
 */
window.ACPWizardUploadParse = (function () {
  "use strict";

  // ── Slice state (single owner) ────────────────────────────────────────────
  var parseSeqToken = 0;
  var parseDebounceTimer = null;
  // Content/name of the last parse whose result was actually applied. Null
  // means the current input has no applied parse result yet.
  var lastParsedStructureInput = null;
  var lastParsedUploadName = null;

  // ── H-3 guard ─────────────────────────────────────────────────────────────
  // Bump the token (invalidating any in-flight parse) and cancel the pending
  // debounce. Called by setWizardInputMode on every tab switch / modal open and
  // by closeModal so a late parse can never write into a later/closed session.
  function invalidatePendingParse() {
    parseSeqToken++;
    if (parseDebounceTimer) { clearTimeout(parseDebounceTimer); parseDebounceTimer = null; }
    return parseSeqToken;
  }

  function getParseSeqToken() { return parseSeqToken; }
  function getLastParsedStructureInput() { return lastParsedStructureInput; }
  function getLastParsedUploadName() { return lastParsedUploadName; }

  // openModal resets the applied-parse markers for a fresh session.
  function resetAppliedParse() {
    lastParsedStructureInput = null;
    lastParsedUploadName = null;
  }

  // Draft snapshot / restore consumers (draft-form slice).
  function setLastParsedStructureInput(value) { lastParsedStructureInput = value; }
  function setLastParsedUploadName(value) { lastParsedUploadName = value; }

  // ── S1 functions (moved verbatim from features/wizard.js) ─────────────────
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

  return {
    scheduleParseStructures: scheduleParseStructures,
    setDetectedFormatChip: setDetectedFormatChip,
    renderPreviewWaiting: renderPreviewWaiting,
    parseStructuresPreview: parseStructuresPreview,
    handleParseResult: handleParseResult,
    showParseStatus: showParseStatus,
    invalidatePendingParse: invalidatePendingParse,
    getParseSeqToken: getParseSeqToken,
    getLastParsedStructureInput: getLastParsedStructureInput,
    getLastParsedUploadName: getLastParsedUploadName,
    resetAppliedParse: resetAppliedParse,
    setLastParsedStructureInput: setLastParsedStructureInput,
    setLastParsedUploadName: setLastParsedUploadName,
  };
})();
