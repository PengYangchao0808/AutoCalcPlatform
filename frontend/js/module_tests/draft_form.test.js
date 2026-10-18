/*
 * Behavior tests for wizard slice S3 草稿/表单
 * (frontend/js/workbench/features/wizard/draft-form.js) — plan todo 19.
 *
 * Pins:
 *  - the S3 entry points are exposed (single owner, thin delegates in wizard.js);
 *  - explicit 0 / false / null survive the capture → restore round-trip
 *    (`hasOwnProperty`, never `|| default`) and legacy draft snapshots (no
 *    mechanism / scanSelection / pesSelection) still hydrate;
 *  - H-2: `closeModal` cancels the pending autosave + bumps the draft epoch, and
 *    the timer callback re-checks epoch + modal visibility, so a late autosave
 *    scheduled by session A can never save a reopened session B's DOM;
 *  - positive control: a timer that is still valid does save (proves the H-2
 *    assertions are not trivially green).
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function makeEl(override) {
    const el = {
      style: { display: "flex" }, className: "", textContent: "", innerHTML: "",
      value: "", title: "", checked: false, hidden: false, dataset: {}, children: [],
      type: "text",
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
      prepend(c) { this.children.unshift(c); return c; },
      replaceChildren() { this.children = []; },
      querySelectorAll() { return []; },
      querySelector() { return null; },
      addEventListener() {},
      removeEventListener() {},
    };
    return Object.assign(el, override || {});
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));

  function build(opts) {
    opts = opts || {};
    const els = {};
    // Modal is visible by default; individual tests flip this.
    const modal = makeEl({ id: "job-modal", style: { display: "flex" } });
    els["job-modal"] = modal;
    els["wizard-draft-status"] = makeEl({ id: "wizard-draft-status" });
    if (opts.extraEls) Object.assign(els, opts.extraEls);
    if (opts.fieldEls) {
      Object.assign(els, opts.fieldEls);
      modal.querySelectorAll = () => Object.values(opts.fieldEls);
    }

    const calls = [];
    let timers = [];
    const fakeSetTimeout = (fn, ms) => { const t = { fn, ms }; timers.push(t); return t; };
    const fakeClearTimeout = (t) => {
      if (opts.clearTimeoutIneffective) return;
      timers = timers.filter((x) => x !== t);
    };

    const sandbox = makeSandbox({
      document: {
        getElementById: (id) => els[id] || (opts.anyEl === false ? null : makeEl({ id })),
        querySelectorAll: () => [],
        createElement: () => makeEl(),
      },
      setTimeout: fakeSetTimeout,
      clearTimeout: fakeClearTimeout,
      api: (path, o) => {
        calls.push({ path, opts: o });
        return Promise.resolve({ draft_id: "d1", revision: 1 });
      },
      t: (k) => k,
      currentLang: "en-US",
      wizardStructures: opts.wizardStructures || [],
      wizardSelectedStructureIndex: 0,
      wizardInputMode: "task",
      wizardUploadFile: null,
      pesSelectionState: { mode: "", selectionKind: "", coordinateKind: "", selectedAtoms: [], structureKey: "" },
      scanSelectionState: { selectionKind: "distance", selectedAtoms: [], structureKey: "" },
      reactionDefinition: null,
      mechProductStructure: null,
      mechTsGuessStructure: null,
      mechCoordinatePlans: [],
      mechStudyId: "",
      mechStrategy: "guided-scan",
      mechFidelity: "s3",
      mechConformerMode: "censo-lite",
      mechMaxSteps: 3,
      mechPromotionPolicy: "all_confirmed",
      mechScanPoints: 21,
      mechIrcPoints: 30,
      mechScanPointsDirty: false,
      mechIrcPointsDirty: false,
      cloneMechanismCoordinateSpec: (x) => JSON.parse(JSON.stringify(x)),
      CatalogUtils: { migrateLegacyGridInStates: () => [] },
      methodCatalogCache: { method_schemas: {}, method_meta: {} },
      workflowCatalogCache: [],
      updateScanKindButtons: () => {},
      createReactionDefinitionState: () => ({}),
      updateInputModeVisibility: () => {},
      scheduleNodeMatchingRefresh: () => {},
      resolveCurrentProfileLabel: () => "label",
      hookMechanismBuilder: () => {},
      candidateDetailsPanel: null,
      _closeInputListOverlay: () => {},
      customRender: opts.customRender || null,
    });
    sandbox.alert = () => {};
    sandbox.ACPWizardUploadParse = {
      getLastParsedStructureInput: () => opts.lastParsedStructureInput || null,
      getLastParsedUploadName: () => opts.lastParsedUploadName || null,
      setLastParsedStructureInput: (v) => { sandbox._lastStructureInput = v; },
      setLastParsedUploadName: (v) => { sandbox._lastUploadName = v; },
      invalidatePendingParse: () => { sandbox._parseInvalidated = true; },
    };
    if (opts.jobEditor) sandbox.ACPJobEditor = opts.jobEditor;

    loadScript("js/workbench/features/wizard/draft-form.js", sandbox);
    return {
      mod: sandbox.ACPWizardDraftForm,
      els, sandbox, calls, modal,
      runTimers() { const ts = timers.slice(); timers = []; ts.forEach((t) => t.fn()); },
      timerCount() { return timers.length; },
    };
  }

  await test("exposes the S3 entry points (single owner) and window state views", () => {
    const { mod, sandbox } = build();
    [
      "_saveWizardDraft", "_scheduleWizardDraftAutosave", "_discardSavedWizardDraft",
      "_captureWizardDraft", "_restoreWizardDraft", "_revalidateDraftSources",
      "openModal", "closeModal", "setWizardInputMode", "openWizardDrafts",
      "startBlankWizardDraft", "setCreateWizardStep", "closeJobModalGuarded",
      "updateConfigCards", "openMethodConfig", "loadDefaultMethodProfile",
      "resolveDefaultWorkflow", "canonicalMethodSchemaId", "pickDefaultProfile",
      "ensureWizardWorkflowValid", "configureStageSourcePanel",
    ].forEach((k) => assert(typeof mod[k] === "function", "missing " + k));
    assert(sandbox.wizardState && typeof sandbox.wizardState === "object", "window.wizardState view");
    assertEq(sandbox.createWizardStep, 1, "window.createWizardStep starts at 1");
    assertEq(sandbox.nmrExperimentMode, "text", "window.nmrExperimentMode view");
  });

  await test("explicit 0/false/null survive capture → restore (no falsy collapse)", () => {
    const fields = {
      "modal-task-name": makeEl({ id: "modal-task-name", value: "taskA" }),
      "modal-charge": makeEl({ id: "modal-charge", value: "0" }),
      "modal-mult": makeEl({ id: "modal-mult", value: "1" }),
      "flag": makeEl({ id: "flag", value: "false" }),
      "nullfield": makeEl({ id: "nullfield", value: null }),
      "toggle": makeEl({ id: "toggle", type: "checkbox", checked: false }),
    };
    const { mod, sandbox } = build({ fieldEls: fields });
    sandbox.wizardState.workflow = { id: "Confsearch", label: "Confsearch" };

    const captured = mod._captureWizardDraft();
    assertEq(captured.fields["modal-charge"], { value: "0" }, "0 captured as string");
    assertEq(captured.fields["flag"], { value: "false" }, "string false captured");
    assertEq(captured.fields["toggle"], { checked: false }, "unchecked captured as false");
    assert(captured.fields["nullfield"].value === null, "null value captured (not dropped)");

    // Mutate the live DOM, then restore the snapshot.
    fields["modal-charge"].value = "9";
    fields["modal-mult"].value = "5";
    fields["flag"].value = "true";
    fields["nullfield"].value = "dirty";
    fields["toggle"].checked = true;
    mod._restoreWizardDraft(captured);
    assertEq(fields["modal-charge"].value, "0", "explicit 0 restored");
    assertEq(fields["modal-mult"].value, "1", "explicit 1 restored");
    assertEq(fields["flag"].value, "false", "string false restored");
    assert(fields["nullfield"].value === null, "explicit null restored (not defaulted)");
    assertEq(fields["toggle"].checked, false, "explicit false restored (not defaulted)");
  });

  await test("legacy draft reader: old snapshot without mechanism/scan/pes still hydrates", () => {
    const fields = {
      "modal-task-name": makeEl({ id: "modal-task-name", value: "" }),
      "modal-charge": makeEl({ id: "modal-charge", value: "" }),
    };
    const { mod, sandbox } = build({ fieldEls: fields });
    // A pre-mechanism / pre-scan snapshot: only workflowState + flat fields.
    const legacy = {
      workflowState: { workflow: { id: "Confsearch", label: "Confsearch" }, method: { profile_id: "", profile_label: "", stages: {} } },
      fields: { "modal-task-name": { value: "legacy-task" }, "modal-charge": { value: "0" } },
      uploadFile: null,
      lastParsedStructureInput: "CCO",
      lastParsedUploadName: "old.xyz",
      restored: false,
    };
    mod._restoreWizardDraft(legacy);
    assertEq(fields["modal-task-name"].value, "legacy-task", "legacy flat field restored");
    assertEq(fields["modal-charge"].value, "0", "legacy explicit 0 restored");
    assertEq(sandbox.wizardState.workflow.id, "Confsearch", "workflowState hydrated");
    assertEq(sandbox._lastStructureInput, "CCO", "legacy parsed input mirrored to S1 owner");
    assertEq(sandbox._lastUploadName, "old.xyz", "legacy upload name mirrored to S1 owner");
    // scanSelection absent → defaults are preserved, not thrown away.
    assertEq(sandbox.scanSelectionState.selectedAtoms, [], "absent scanSelection leaves defaults");
  });

  await test("H-2: closeModal cancels the pending autosave + bumps the epoch", () => {
    const fields = { "modal-task-name": makeEl({ id: "modal-task-name", value: "A" }) };
    const { mod, modal, runTimers, timerCount, calls } = build({ fieldEls: fields });
    mod._scheduleWizardDraftAutosave();
    assertEq(timerCount(), 1, "autosave scheduled while modal visible");
    mod.closeModal();
    assertEq(timerCount(), 0, "closeModal cleared the pending timer");
    // Reopen as session B with different DOM, then let any captured timer fire.
    modal.style.display = "flex";
    fields["modal-task-name"].value = "B";
    runTimers();
    assertEq(calls.length, 0, "no draft write after close (session B DOM untouched)");
  });

  await test("H-2: a caught timer still re-checks the epoch (teardown forgot clearTimeout)", () => {
    const fields = { "modal-task-name": makeEl({ id: "modal-task-name", value: "A" }) };
    // clearTimeout is deliberately ineffective — the epoch guard must catch it.
    const { mod, modal, runTimers, calls } = build({
      fieldEls: fields, clearTimeoutIneffective: true,
    });
    mod._scheduleWizardDraftAutosave();     // timer captured with epoch E
    mod.closeModal();                       // epoch E+1, timer not actually cleared
    modal.style.display = "flex";           // session B reopened
    fields["modal-task-name"].value = "B";
    runTimers();                            // late fire with session B DOM
    assertEq(calls.length, 0, "stale epoch must drop the save");
  });

  await test("H-2: hidden modal at fire time drops the save", () => {
    const { mod, modal, runTimers, calls } = build();
    mod._scheduleWizardDraftAutosave();
    modal.style.display = "none";           // teardown hides without bumping epoch
    runTimers();
    assertEq(calls.length, 0, "hidden modal must drop the autosave");
  });

  await test("H-2 positive control: a still-valid timer does save (assertions are red-able)", async () => {
    const fields = { "modal-task-name": makeEl({ id: "modal-task-name", value: "live" }) };
    const { mod, runTimers, calls } = build({ fieldEls: fields });
    mod._scheduleWizardDraftAutosave();
    runTimers();
    await tick();
    assertEq(calls.length, 1, "valid timer performed exactly one save");
    assertEq(calls[0].path, "/drafts", "new draft POSTs /drafts");
    assertEq(calls[0].opts.method, "POST");
  });
};
