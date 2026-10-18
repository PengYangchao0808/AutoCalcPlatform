/*
 * Behavior tests for wizard slice S1 上传/解析
 * (frontend/js/workbench/features/wizard/upload-parse.js) — plan todo 17.
 *
 * Pins:
 *  - the S1 entry points and the state-owner accessor API exist;
 *  - upload path builds FormData (a single "file" field) and calls the frozen
 *    wizardEndpoints.uploadStructure adapter, applying structures/format and
 *    recording lastParsedUploadName;
 *  - upload error presentation `e.detail || "Upload failed"` is preserved for
 *    a 422-with-detail and for a detail-less network error;
 *  - text parse path passes the exact {content, format:"auto", filename}
 *    payload and honours the no-resp.ok semantics (a JSON error body resolves
 *    onto the ok:false presentation path);
 *  - H-3: a late parse that resolves after invalidatePendingParse() (called by
 *    closeModal / setWizardInputMode on reopen) is dropped and never writes
 *    the session or records the applied input.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const ELEMENT_IDS = [
    "modal-structure-input", "detected-format-chip", "parse-errors",
    "parse-status", "preview-3d-empty", "preview-structure-title",
    "source-coordinate-text", "charge-spin-row", "modal-charge",
    "modal-mult", "structure-preview",
  ];

  function makeEl(id) {
    return {
      id,
      style: {},
      className: "",
      textContent: "",
      innerHTML: "",
      value: "",
      title: "",
      classList: { toggle() {}, add() {}, remove() {} },
    };
  }

  function defer() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  function build(opts) {
    opts = opts || {};
    const els = {};
    ELEMENT_IDS.forEach((id) => { els[id] = makeEl(id); });
    const calls = [];
    const sandbox = makeSandbox({
      FormData: FormData,
      document: { getElementById: (id) => els[id] || null, querySelectorAll: () => [] },
      t: (key) => key,
      escapeHtml: (s) => String(s),
      wizardStructures: [],
      wizardParseWarnings: [],
      wizardInputMode: "structure",
      wizardUploadFile: null,
      wizardState: {},
      previewViewer: null,
      previewModel: null,
      renderStructurePreview: (...a) => calls.push(["renderStructurePreview", a]),
      _refreshWizardInputForm: (...a) => calls.push(["_refreshWizardInputForm", a]),
      updatePreviewActionState: () => calls.push(["updatePreviewActionState"]),
      resetPESSelection: () => calls.push(["resetPESSelection"]),
      clearPESPreviewSelectionShapes: () => {},
      isPESsearchTask: () => false,
      syncPESSelectionFromWizard: () => {},
      attachPESPreviewSelection: () => {},
      _scheduleWizardDraftAutosave: () => calls.push(["_scheduleWizardDraftAutosave"]),
      hookMechanismBuilder: () => {},
      ACPJobEditor: null,
    });
    sandbox.ACPClient = {
      wizardEndpoints: {
        uploadStructure: opts.uploadStructure || (() => Promise.resolve({ structures: [] })),
        parseStructure: opts.parseStructure || (() => Promise.resolve({ ok: true, structures: [] })),
      },
    };
    loadScript("js/workbench/features/wizard/upload-parse.js", sandbox);
    return { mod: sandbox.ACPWizardUploadParse, els, sandbox, calls };
  }

  await test("exposes the S1 entry points and the state-owner accessor API", () => {
    const { mod } = build();
    ["scheduleParseStructures", "setDetectedFormatChip", "renderPreviewWaiting",
      "parseStructuresPreview", "handleParseResult", "showParseStatus",
      "invalidatePendingParse", "getParseSeqToken", "getLastParsedStructureInput",
      "getLastParsedUploadName", "resetAppliedParse", "setLastParsedStructureInput",
      "setLastParsedUploadName",
    ].forEach((k) => assert(typeof mod[k] === "function", "missing " + k));
  });

  await test("upload: FormData passthrough + adapter call + applied result", async () => {
    let seenFd = null;
    const { mod, els, sandbox } = build({
      uploadStructure: (fd) => {
        seenFd = fd;
        return Promise.resolve({ ok: true, structures: [{ name: "a", charge: 0, multiplicity: 1 }], detected_format: "xyz" });
      },
    });
    sandbox.wizardInputMode = "upload";
    sandbox.wizardUploadFile = { name: "m.xyz" };
    await mod.parseStructuresPreview();
    assert(seenFd && typeof seenFd.getAll === "function", "FormData instance must be passed through");
    assertEq(seenFd.getAll("file").length, 1, "file field appended");
    assertEq(sandbox.wizardStructures.length, 1);
    assertEq(sandbox.wizardStructures[0].name, "a");
    assertEq(mod.getLastParsedUploadName(), "m.xyz");
    assertEq(els["detected-format-chip"].textContent, "parse.detected");
    assertEq(els["parse-status"].textContent, "parse.success");
    assertEq(els["parse-status"].className, "parse-status ok");
    assertEq(els["charge-spin-row"].style.display, "flex");
  });

  await test("upload 422: 'e.detail || Upload failed' presentation preserved", async () => {
    const { mod, els, sandbox } = build({
      uploadStructure: () => {
        const e = new Error("Upload failed");
        e.detail = "Unsupported structure format";
        e.status = 422;
        return Promise.reject(e);
      },
    });
    sandbox.wizardInputMode = "upload";
    sandbox.wizardUploadFile = { name: "m.bad" };
    await mod.parseStructuresPreview();
    assertEq(els["parse-status"].textContent, "Unsupported structure format");
    assertEq(els["parse-status"].className, "parse-status err");
    assertEq(sandbox.wizardStructures.length, 0);
  });

  await test("upload network error: detail-less falls back to 'Upload failed'", async () => {
    const { mod, els, sandbox } = build({
      uploadStructure: () => Promise.reject(new TypeError("fetch failed")),
    });
    sandbox.wizardInputMode = "upload";
    sandbox.wizardUploadFile = { name: "m.bad" };
    await mod.parseStructuresPreview();
    assertEq(els["parse-status"].textContent, "Upload failed");
    assertEq(els["parse-status"].className, "parse-status err");
  });

  await test("parse: exact JSON payload {content, format:'auto', filename}", async () => {
    let seenPayload = null;
    const { mod, els, sandbox } = build({
      parseStructure: (payload) => {
        seenPayload = payload;
        return Promise.resolve({ ok: true, structures: [{ name: "s", charge: 0, multiplicity: 1 }], detected_format: "xyz" });
      },
    });
    sandbox.wizardInputMode = "structure";
    els["modal-structure-input"].value = "  H2O  ";
    await mod.parseStructuresPreview();
    assertEq(seenPayload, { content: "  H2O  ", format: "auto", filename: "" });
    assertEq(mod.getLastParsedStructureInput(), "  H2O  ");
    assertEq(sandbox.wizardStructures[0].name, "s");
    assertEq(els["parse-status"].textContent, "parse.success");
  });

  await test("parse: no resp.ok check — JSON error body resolves to the ok:false path", async () => {
    const { mod, els, sandbox } = build({
      parseStructure: () => Promise.resolve({ ok: false, structures: [], errors: ["bad content"] }),
    });
    sandbox.wizardInputMode = "structure";
    els["modal-structure-input"].value = "x";
    await mod.parseStructuresPreview();
    assertEq(els["parse-errors"].style.display, "block");
    assertEq(els["parse-errors"].innerHTML, "<div>bad content</div>");
    assertEq(els["parse-status"].textContent, "parse.failed");
    assertEq(els["parse-status"].className, "parse-status err");
    assertEq(mod.getLastParsedStructureInput(), null, "ok:false must not mark the text applied");
  });

  await test("H-3: late parse after invalidatePendingParse() (close) does not write", async () => {
    const d = defer();
    const { mod, els, sandbox } = build({ uploadStructure: () => d.promise });
    sandbox.wizardInputMode = "upload";
    sandbox.wizardUploadFile = { name: "m.xyz" };
    const pending = mod.parseStructuresPreview();
    mod.invalidatePendingParse();
    d.resolve({ structures: [{ name: "late" }], detected_format: "xyz" });
    await pending;
    assertEq(sandbox.wizardStructures.length, 0, "late parse must not write into the closed session");
    assertEq(mod.getLastParsedUploadName(), null, "late parse must not record the applied upload");
    assertEq(els["parse-status"].textContent, "parse.parsing", "status stays at the pre-close value");
  });

  await test("H-3: close/reopen — a late parse from session A cannot overwrite session B", async () => {
    const a = defer();
    const b = defer();
    let calls = 0;
    const { mod, sandbox } = build({
      uploadStructure: () => { calls += 1; return calls === 1 ? a.promise : b.promise; },
    });
    sandbox.wizardInputMode = "upload";
    sandbox.wizardUploadFile = { name: "a.xyz" };
    const pA = mod.parseStructuresPreview();
    mod.invalidatePendingParse();
    sandbox.wizardUploadFile = { name: "b.xyz" };
    const pB = mod.parseStructuresPreview();
    b.resolve({ structures: [{ name: "B" }], detected_format: "xyz" });
    await pB;
    a.resolve({ structures: [{ name: "A" }], detected_format: "xyz" });
    await pA;
    assertEq(sandbox.wizardStructures.length, 1);
    assertEq(sandbox.wizardStructures[0].name, "B");
    assertEq(mod.getLastParsedUploadName(), "b.xyz");
  });
};
