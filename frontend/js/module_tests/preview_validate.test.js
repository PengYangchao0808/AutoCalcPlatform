/*
 * Behavior tests for wizard slice S2 预览/校验
 * (frontend/js/workbench/features/wizard/preview-validate.js) — plan todo 18.
 *
 * Pins:
 *  - the S2 entry points and the factory API exist;
 *  - validation (`createValidation`) calls the frozen validateMethod adapter
 *    with {schema_id, levels}, applies normalized_levels only while the modal
 *    is open, and renders errors/warnings from the last response;
 *  - the hessian preview (`createHessianPreview`) renders rows from the frozen
 *    hessianPreview adapter and shows the no-structures hint;
 *  - the batch-config preview (`createBatchConfigPreview`) calls the frozen
 *    batchConfigPreview adapter (schema-guarded) and renders the server summary;
 *  - H-4: after `abortBatchPreviews()` (close) or a rebuild, a late resolved
 *    batch-config preview response never writes into the (detached) preview
 *    node — proving no late preview write after close/reopen.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function makeEl() {
    return {
      style: {}, className: "", textContent: "", innerHTML: "", value: "", title: "",
      hidden: false, dataset: {}, children: [],
      classList: {
        _s: new Set(),
        add(c) { this._s.add(c); },
        remove(c) { this._s.delete(c); },
        toggle(c, f) {
          if (f === undefined) { if (this._s.has(c)) this._s.delete(c); else this._s.add(c); }
          else if (f) this._s.add(c); else this._s.delete(c);
        },
        contains(c) { return this._s.has(c); },
      },
      setAttribute(k, v) { this[k] = v; },
      removeAttribute(k) { delete this[k]; },
      appendChild(c) { this.children.push(c); return c; },
      querySelectorAll() { return []; },
      querySelector() { return null; },
      addEventListener() {},
    };
  }

  function defer() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));

  function build(opts) {
    opts = opts || {};
    const els = {};
    ["mc-validation-errors", "mc-validation-warnings", "method-config-modal"].forEach((id) => { els[id] = makeEl(); });
    els["method-config-modal"].style.display = "flex";
    const sandbox = makeSandbox({
      document: {
        getElementById: (id) => els[id] || null,
        querySelectorAll: () => [],
        createElement: () => makeEl(),
      },
      escapeHtml: (s) => String(s),
      t: (k) => k,
      currentLang: opts.currentLang || "en-US",
      wizardState: opts.wizardState || { method: { schema_id: "sid", stages: {} } },
      wizardStructures: opts.wizardStructures || [],
      applyBatchOptimizeMethodFields: opts.applyBatchOptimizeMethodFields || (() => {}),
      updatePESSelectionForProtocol: () => {},
    });
    sandbox.ACPClient = {
      wizardEndpoints: {
        validateMethod: opts.validateMethod || (() => Promise.resolve({ valid: true, errors: [], warnings: [] })),
        hessianPreview: opts.hessianPreview || (() => Promise.resolve({ results: [], summary: {} })),
        batchConfigPreview: opts.batchConfigPreview || (() => Promise.resolve({ schema: "batch_optimize_preview_v1", roles: {}, orca_summary: {} })),
      },
    };
    loadScript("js/workbench/features/wizard/preview-validate.js", sandbox);
    return { mod: sandbox.ACPWizardPreviewValidate, els, sandbox };
  }

  await test("exposes the S2 entry points and the factory API", () => {
    const { mod } = build();
    ["renderStructurePreview", "selectPreviewStructure", "_refreshWizardInputForm", "escapeAttr",
      "updateFooterSelectedCount", "_openInputListOverlay", "_closeInputListOverlay", "_toggleInputListOverlay",
      "_togglePreviewAtomLabels", "renderLoadedStructuresList", "updatePreviewActionState",
      "removeSelectedStructure", "clearParsedStructures", "renderPreviewStructure3D",
      "createValidation", "createHessianPreview", "createBatchConfigPreview", "abortBatchPreviews",
    ].forEach((k) => assert(typeof mod[k] === "function", "missing " + k));
  });

  await test("validation: exact {schema_id, levels} payload + normalized write-back while open", async () => {
    let seen = null;
    const ws = { method: { schema_id: "sid", stages: { opt: { engine: "orca", basis: "" } } } };
    const { mod, els } = build({
      wizardState: ws,
      validateMethod: (p) => { seen = p; return Promise.resolve({ normalized_levels: { opt: { basis: "def2-SVP" } }, warnings: ["migrated"] }); },
    });
    const v = mod.createValidation({ schema: { method_levels: [{ level_id: "opt", required: true }] }, schemaId: "sid" });
    const body = await v.validate();
    assertEq(seen, { schema_id: "sid", levels: { opt: { engine: "orca", basis: "" } } });
    assertEq(ws.method.stages.opt.basis, "def2-SVP", "normalized value written while modal open");
    v.refresh();
    assertEq(els["mc-validation-warnings"].style.display, "block");
    assert(els["mc-validation-warnings"].innerHTML.indexOf("migrated") >= 0);
    assertEq(els["mc-validation-errors"].style.display, "none");
    void body;
  });

  await test("validation: closed modal skips the normalized write-back (H-5 preserved)", async () => {
    const ws = { method: { schema_id: "sid", stages: { opt: { engine: "orca", basis: "" } } } };
    const { mod, els } = build({
      wizardState: ws,
      validateMethod: () => Promise.resolve({ normalized_levels: { opt: { basis: "def2-SVP" } } }),
    });
    els["method-config-modal"].style.display = "none";
    const v = mod.createValidation({ schema: { method_levels: [{ level_id: "opt" }] }, schemaId: "sid" });
    await v.validate();
    assertEq(ws.method.stages.opt.basis, "", "late validation must not mutate restored state");
  });

  await test("validation: refresh renders errors from the last response", async () => {
    const { mod, els } = build({
      validateMethod: () => Promise.resolve({ errors: ["Level 'opt' failed"], warnings: [] }),
    });
    const v = mod.createValidation({ schema: { method_levels: [{ level_id: "opt" }] }, schemaId: "sid" });
    await v.validate();
    v.refresh();
    assertEq(els["mc-validation-errors"].style.display, "block");
    assert(els["mc-validation-errors"].innerHTML.indexOf("Level 'opt' failed") >= 0);
    assertEq(els["mc-validation-warnings"].style.display, "none");
  });

  await test("hessian: no structures renders the hint row", async () => {
    const { mod } = build();
    const previewBox = makeEl(), hint = makeEl();
    const hp = mod.createHessianPreview(() => ({ previewBox: previewBox, hint: hint, mode: "auto", zh: false, lvDef: { level_id: "opt" }, st: { opt_recalc_hess: "auto" } }));
    await hp.fetchPreview();
    assertEq(previewBox.style.display, "");
    assert(previewBox.children.length === 1, "one hint row");
    assert(previewBox.children[0].textContent.indexOf("Add structures") >= 0);
  });

  await test("hessian: renders server rows via the frozen adapter", async () => {
    const { mod } = build({
      wizardStructures: [{ name: "a", symbols: ["O", "H", "H"] }],
      hessianPreview: () => Promise.resolve({ results: [{ name: "a", enabled: true, interval: 5, heavy_elements: ["O"] }], summary: { total: 1, enabled: 1 } }),
    });
    const previewBox = makeEl(), hint = makeEl();
    const hp = mod.createHessianPreview(() => ({ previewBox: previewBox, hint: hint, mode: "auto", zh: false, lvDef: { level_id: "opt" }, st: { opt_recalc_hess: "auto" } }));
    await hp.fetchPreview();
    await tick(0);
    assert(previewBox.children.length >= 2, "summary + at least one row");
    assert(previewBox.children[0].className === "mc-hessian-preview-summary");
    assertEq(previewBox.children[0].textContent.indexOf("1 structures"), 0);
  });

  await test("batch preview: renders server summary via the frozen adapter", async () => {
    const { mod } = build({
      batchConfigPreview: () => Promise.resolve({
        schema: "batch_optimize_preview_v1",
        roles: { int: { effective: { max_cycles: 100 } } },
        orca_summary: { int: ["Opt"] },
      }),
    });
    const previewFields = makeEl();
    const bp = mod.createBatchConfigPreview({ previewFields: previewFields, getActiveRole: () => "int", getRoles: () => ({ int: {} }), debounceMs: 0 });
    bp.fetch();
    await tick(10);
    assert(previewFields["data-stale"] === undefined, "data-stale cleared after a valid render");
    assert(previewFields.children.length >= 1, "server summary rendered");
  });

  await test("H-4: abortBatchPreviews() (close) drops a late batch-preview response", async () => {
    const d = defer();
    const { mod } = build({ batchConfigPreview: () => d.promise });
    const previewFields = makeEl();
    const bp = mod.createBatchConfigPreview({ previewFields: previewFields, getActiveRole: () => "int", getRoles: () => ({ int: {} }), debounceMs: 0 });
    bp.fetch();
    await tick(10); // request issued, still pending
    mod.abortBatchPreviews(); // simulates modal close / cancel
    d.resolve({ schema: "batch_optimize_preview_v1", roles: { int: { effective: { max_cycles: 1 } } }, orca_summary: {} });
    await tick(10);
    assertEq(previewFields["data-stale"], "true", "late response must not clear data-stale");
    assertEq(previewFields.children.length, 0, "late response must not write the (detached) preview node");
  });

  await test("H-4: a rebuilt instance aborts the previous, so session A cannot overwrite session B", async () => {
    const a = defer();
    let calls = 0;
    const { mod } = build({ batchConfigPreview: () => { calls += 1; return calls === 1 ? a.promise : Promise.resolve({ schema: "batch_optimize_preview_v1", roles: {}, orca_summary: {} }); } });
    const fieldsA = makeEl();
    const bpA = mod.createBatchConfigPreview({ previewFields: fieldsA, getActiveRole: () => "int", getRoles: () => ({ int: {} }), debounceMs: 0 });
    bpA.fetch();
    await tick(10); // A's request pending
    const fieldsB = makeEl();
    mod.createBatchConfigPreview({ previewFields: fieldsB, getActiveRole: () => "int", getRoles: () => ({ int: {} }), debounceMs: 0 }); // rebuild aborts A
    a.resolve({ schema: "batch_optimize_preview_v1", roles: { int: { effective: { max_cycles: 9 } } }, orca_summary: {} });
    await tick(10);
    assertEq(fieldsA.children.length, 0, "session A's late response must not write its detached node");
  });
};
