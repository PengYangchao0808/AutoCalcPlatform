/* ACP 3.0 module test — tsmode-editor (Track A / A-16 · P05 TS Mode editor).
 *
 * Loads the module exactly as the classic-script Shell loader consumes it
 * (`window.ACPNextModules["tsmode-editor"] = { descriptor, factory, _internals }`)
 * inside the zero-dependency node vm harness with a fake DOM. Every request
 * goes through an injected request seam, so no test can reach the network (the
 * sandbox carries no fetch/XMLHttpRequest at all).
 *
 * Pinned contracts:
 *  1. exactly one ACPNextModules key, Shell-valid descriptor, no legacy global;
 *  2. four states (empty/loading/ready/error) + separate mode/busy sub-states;
 *  3. frequency sources: Hessian-less rows are disabled, one eligible source
 *     auto-selects, source switching reloads modes and resets the target;
 *  4. NATIVE mode index: rows carry ORCA's printed index (e.g. #6/#9/#13) and
 *     the selected index — never the row position — becomes source_mode_index;
 *  5. no fake modes: unavailable/empty/404 mode lists render no rows, keep the
 *     target empty and never submit a fabricated mode;
 *  6. invalid/empty/error paths: local validation blocks /preflight and /jobs;
 *     server reasons/advisories are rendered;
 *  7. preview payload is the frozen tsmode body (no method block, no invented
 *     keys) through the existing `POST /preflight`;
 *  8. submit is the existing `POST /jobs` tsmode branch, only after a feasible
 *     preview of the exact same plan (fingerprint + Idempotency-Key + receipt);
 *  9. generation/dispose guards: late sources/modes/preview results never paint;
 * 10. no second viewer, no direct network primitive, no task mutation.
 */
"use strict";

const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const MODULE_PATH = "js/modules/tsmode-editor/module.js";
  const API = "/api/v1";
  const JOB_ID = "freq-001";
  const DIGEST = "sha256:" + "a".repeat(64);

  const SOURCES = {
    job_id: JOB_ID,
    sources: [
      {
        entry_id: "alpha",
        item_id: null,
        label: "Alpha frequency",
        output_path: "WORK/04_FREQ/alpha.out",
        hess_path: "WORK/04_FREQ/alpha.hess",
        complete: true,
        hessian_available: true,
        imaginary_count: 3,
        atom_count: 3,
        mode_count: 4
      },
      {
        entry_id: "beta",
        item_id: null,
        label: "Beta frequency",
        output_path: "WORK/04_FREQ/beta.out",
        hess_path: "WORK/04_FREQ/beta.hess",
        complete: true,
        hessian_available: true,
        imaginary_count: 1,
        atom_count: 3,
        mode_count: 4
      },
      {
        entry_id: "gamma",
        item_id: null,
        label: "Gamma without Hessian",
        output_path: "WORK/04_FREQ/gamma.out",
        hess_path: null,
        complete: true,
        hessian_available: false,
        imaginary_count: 2,
        atom_count: 3,
        mode_count: 4
      }
    ],
    warnings: []
  };

  // Native ORCA indices are deliberately NON-contiguous: 6, 9, 13 (+ one real
  // mode 14). A positional implementation would emit 0/1/2 here.
  const MODES = {
    available: true,
    reason: null,
    threshold_cm1: 0,
    threshold_source: "fixed",
    modes: [
      { mode_index: 6, frequency_cm1: -120.5, imaginary: true, ir_intensity: 12.34, vectors: [[0, 0, 0], [0, 0, 0], [0, 0, 0]] },
      { mode_index: 9, frequency_cm1: -45.2, imaginary: true, ir_intensity: null, vectors: [[0, 0, 0], [0, 0, 0], [0, 0, 0]] },
      { mode_index: 13, frequency_cm1: -310.9, imaginary: true, ir_intensity: 2.5, vectors: [[0, 0, 0], [0, 0, 0], [0, 0, 0]] },
      { mode_index: 14, frequency_cm1: 450.23, imaginary: false, ir_intensity: 0.4, vectors: [[0, 0, 0], [0, 0, 0], [0, 0, 0]] }
    ],
    atom_count: 3,
    geometry_product_id: null,
    source: "product",
    imaginary_count: 3
  };

  const MODES_B = {
    available: true,
    reason: null,
    modes: [
      { mode_index: 2, frequency_cm1: -88.8, imaginary: true, ir_intensity: null, vectors: [] },
      { mode_index: 4, frequency_cm1: 220.1, imaginary: false, ir_intensity: null, vectors: [] }
    ],
    atom_count: 3,
    source: "historical_projection",
    imaginary_count: 1
  };

  const PREFLIGHT_OK = {
    schema_version: 1,
    feasible: true,
    reasons: [],
    advisories: [],
    plan_digest: DIGEST,
    would_create_task: false,
    would_reserve: false
  };

  const JOB_RECEIPT = {
    job_id: "20261031_001_tsmode",
    status: "queued",
    workflow: "tsmode",
    attempt: 1,
    plan_digest: DIGEST,
    idempotent_replay: false,
    result: "accepted"
  };

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function flush(n = 10) {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await tick();
    for (let i = 0; i < 6; i++) await Promise.resolve();
  }

  // ------------------------------------------------------------ fake DOM --

  function makeDom() {
    const docListeners = new Map();

    function makeEl(tag) {
      const listeners = new Map();
      const node = {
        tagName: String(tag).toUpperCase(),
        children: [],
        parentNode: null,
        className: "",
        textContent: "",
        hidden: false,
        disabled: false,
        value: "",
        checked: false,
        type: "",
        name: "",
        id: "",
        attrs: {},
        listeners,
        setAttribute(key, value) { this.attrs[key] = String(value); },
        getAttribute(key) { return key in this.attrs ? this.attrs[key] : null; },
        removeAttribute(key) { delete this.attrs[key]; },
        appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
        removeChild(child) {
          const index = this.children.indexOf(child);
          if (index >= 0) this.children.splice(index, 1);
          child.parentNode = null;
          return child;
        },
        replaceChildren(...nodes) {
          this.children.forEach((child) => { child.parentNode = null; });
          this.children = nodes.slice();
          nodes.forEach((child) => { child.parentNode = this; });
        },
        addEventListener(type, fn) {
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type).add(fn);
        },
        removeEventListener(type, fn) {
          if (listeners.has(type)) listeners.get(type).delete(fn);
        },
        fire(type, event) {
          const set = listeners.get(type);
          if (set) [...set].forEach((fn) => fn(Object.assign({ type, target: node }, event || {})));
        }
      };
      return node;
    }

    const document = {
      createElement: makeEl,
      addEventListener(type, fn) {
        if (!docListeners.has(type)) docListeners.set(type, new Set());
        docListeners.get(type).add(fn);
      },
      removeEventListener(type, fn) {
        if (docListeners.has(type)) docListeners.get(type).delete(fn);
      },
      dispatchEvent(event) {
        const set = docListeners.get(event && event.type);
        if (set) [...set].forEach((fn) => fn(event));
        return true;
      },
      documentElement: makeEl("html"),
      activeDocListeners() {
        let count = 0;
        docListeners.forEach((set) => { count += set.size; });
        return count;
      }
    };
    return { document };
  }

  function walk(node, out = []) {
    (node.children || []).forEach((child) => { out.push(child); walk(child, out); });
    return out;
  }

  function findAllAttr(root, attr) {
    return walk(root).filter((node) => node.getAttribute && node.getAttribute(attr) != null);
  }

  function findAll(root, attr, value) {
    return walk(root).filter((node) => node.getAttribute && node.getAttribute(attr) === value);
  }

  function find(root, attr, value) {
    return findAll(root, attr, value)[0] || null;
  }

  function subtreeText(node) {
    let text = node && node.textContent ? node.textContent : "";
    ((node && node.children) || []).forEach((child) => { text += " " + subtreeText(child); });
    return text;
  }

  function collectAttr(node, attr) {
    return walk(node)
      .map((child) => child.getAttribute && child.getAttribute(attr))
      .filter((value) => value != null);
  }

  // ------------------------------------------------------------- harness --

  function loadModule(sandbox) {
    loadScript(MODULE_PATH, sandbox);
    return sandbox.ACPNextModules["tsmode-editor"];
  }

  function defaultHandler(pathname) {
    if (/\/frequency-sources$/.test(pathname)) return SOURCES;
    if (/\/vibrations$/.test(pathname)) return MODES;
    if (pathname === API + "/preflight") return PREFLIGHT_OK;
    if (pathname === API + "/jobs") return JOB_RECEIPT;
    throw new Error("unexpected request path: " + pathname);
  }

  function makeContext(options) {
    options = options || {};
    const listeners = new Set();
    let snapshot = {
      projectId: options.projectId || null,
      taskRef: options.taskRef === null ? null : (options.taskRef || { jobId: JOB_ID }),
      structureRef: null,
      revision: 1
    };
    return {
      getSnapshot() { return snapshot; },
      subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
      setFocus(patch) {
        snapshot = Object.assign({}, snapshot, patch, { revision: snapshot.revision + 1 });
        listeners.forEach((fn) => fn(snapshot));
      },
      listenerCount() { return listeners.size; }
    };
  }

  function makeCommands() {
    const commands = {
      opened: [],
      openTask(payload) { commands.opened.push(payload); }
    };
    return commands;
  }

  function setup(options) {
    options = options || {};
    const dom = makeDom();
    const sandbox = makeSandbox({});
    const mod = loadModule(sandbox);
    const calls = [];
    const handler = options.handler || defaultHandler;
    function request(pathname, opts) {
      calls.push({ path: pathname, opts: opts || {} });
      try {
        return Promise.resolve(handler(pathname, opts || {}, calls));
      } catch (error) {
        return Promise.reject(error);
      }
    }
    const context = options.context === null ? null : (options.context ||
      makeContext({ taskRef: options.taskRef, projectId: options.projectId }));
    const commands = options.commands || makeCommands();
    const host = dom.document.createElement("div");
    const instance = mod.factory({ doc: dom.document, request, context, commands });
    return { dom, sandbox, mod, calls, context, commands, host, instance, request };
  }

  async function mount(ctx) {
    await ctx.instance.mount(ctx.host, { context: ctx.context, commands: ctx.commands });
    await flush();
  }

  function root(ctx) {
    return ctx.host.children[0] || null;
  }

  function stateOf(ctx) {
    return root(ctx).getAttribute("data-acp-module-state");
  }

  function attr(ctx, name) {
    return root(ctx).getAttribute(name);
  }

  function role(ctx, name) {
    const host = ctx.host;
    return find(host, "data-role", name);
  }

  function action(ctx, name) {
    return find(ctx.host, "data-action", name);
  }

  function sourceRows(ctx) {
    return findAllAttr(ctx.host, "data-source-entry");
  }

  function modeRows(ctx) {
    return findAllAttr(ctx.host, "data-mode-index");
  }

  function modeRowByIndex(ctx, index) {
    return modeRows(ctx).find((row) => row.getAttribute("data-mode-index") === String(index)) || null;
  }

  function modeTexOf(ctx) {
    return modeRows(ctx).map((row) => row.getAttribute("data-mode-index"));
  }

  function callsTo(ctx, suffix) {
    return ctx.calls.filter((call) => call.path === suffix);
  }

  function preflightCalls(ctx) {
    return callsTo(ctx, API + "/preflight");
  }

  function submitCalls(ctx) {
    return callsTo(ctx, API + "/jobs");
  }

  function lastPreflightBody(ctx) {
    const calls = preflightCalls(ctx);
    assert(calls.length > 0, "no preflight request was issued");
    return JSON.parse(calls[calls.length - 1].opts.body);
  }

  function lastSubmitBody(ctx) {
    const calls = submitCalls(ctx);
    assert(calls.length > 0, "no submit request was issued");
    return JSON.parse(calls[calls.length - 1].opts.body);
  }

  async function selectSource(ctx, entryId) {
    const row = sourceRows(ctx).find((item) => item.getAttribute("data-source-entry") === entryId);
    assert(!!row, "source row not rendered: " + entryId);
    row.fire("click");
    await flush();
  }

  async function selectMode(ctx, index) {
    const row = modeRowByIndex(ctx, index);
    assert(!!row, "mode row not rendered: #" + index);
    row.fire("click");
    await flush();
  }

  async function runPreview(ctx) {
    action(ctx, "preview").fire("click");
    await flush();
  }

  async function runSubmit(ctx) {
    action(ctx, "submit").fire("click");
    await flush();
  }

  async function mountWithSourceAndMode(ctx, entryId, modeIndex) {
    await mount(ctx);
    if (entryId) await selectSource(ctx, entryId);
    if (modeIndex != null) await selectMode(ctx, modeIndex);
  }

  // ------------------------------------------------------------- tests --

  await test("module: one ACPNextModules key, Shell-valid descriptor, no legacy global", async () => {
    const sandbox = makeSandbox({});
    loadScript("js/shell/module-registry.js", sandbox);
    const mod = loadModule(sandbox);
    assertEq(Object.keys(sandbox.ACPNextModules), ["tsmode-editor"], "exactly one module key");
    assertEq(typeof mod.factory, "function", "factory present");
    assertEq(Array.isArray(mod._internals.buildRequest), false, "internals expose functions, not state");
    assertEq(sandbox.ACPShell.registry.validateDescriptor(mod.descriptor), null,
      "descriptor must pass the Shell registry validator");
    assertEq(mod.descriptor.id, "tsmode-editor");
    assertEq(mod.descriptor.interfaceVersion, 1);
    assertEq(mod.descriptor.binds, ["taskRef"]);
    assertEq(mod.descriptor.hosts, ["acp-web"]);
    assertEq(mod.descriptor.error.titleKey, "next.tsmode.errorTitle");
    assert(!("ACPTsmodeEditor" in sandbox), "the legacy tsmode_editor.js global must not be defined");
    assert(!("tsmodeEditorState" in sandbox), "no legacy page state global");
    assertEq(typeof mod._internals.validateSelection, "function");
    assertEq(typeof mod._internals.normalizeModes, "function");
  });

  await test("mount: no focused task -> empty state, hint, and zero requests", async () => {
    const ctx = setup({ context: makeContext({ taskRef: null }) });
    await mount(ctx);
    assertEq(stateOf(ctx), "empty");
    assertEq(role(ctx, "overlay").hidden, false, "overlay is visible in the empty state");
    assertEq(subtreeText(role(ctx, "overlay-message")).indexOf("尚无可用频率源") !== -1, true);
    assertEq(subtreeText(role(ctx, "overlay-detail")).indexOf("未绑定任务") !== -1, true);
    assertEq(action(ctx, "retry").hidden, true, "no retry without a task");
    assertEq(action(ctx, "submit").disabled, true);
    assertEq(ctx.calls.length, 0, "mount without a task performs no request");
    assertEq(attr(ctx, "data-acp-task"), "");
  });

  await test("sources: loading -> ready, Hessian-less row disabled, no mode request before a choice", async () => {
    const gate = deferred();
    const ctx = setup({ handler: () => gate.promise });
    await mount(ctx);
    assertEq(stateOf(ctx), "loading", "deferred sources are a loading state");
    assertEq(role(ctx, "overlay").hidden, false);
    assertEq(subtreeText(role(ctx, "overlay-message")).indexOf("正在读取频率源") !== -1, true);

    gate.resolve(SOURCES);
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(role(ctx, "overlay").hidden, true, "overlay hidden once ready");
    assertEq(sourceRows(ctx).length, 3, "all discovered sources render");
    const gamma = sourceRows(ctx).find((row) => row.getAttribute("data-source-entry") === "gamma");
    assertEq(gamma.getAttribute("data-hessian"), "false");
    assertEq(gamma.disabled, true, "a source without a Hessian cannot be selected");
    assertEq(subtreeText(gamma).indexOf("缺少 Hessian") !== -1, true);
    assertEq(ctx.calls.filter((call) => /\/vibrations$/.test(call.path)).length, 0,
      "two eligible sources -> no auto-selection, hence no mode request");
    assertEq(attr(ctx, "data-acp-selected-entry"), "");

    gamma.fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-selected-entry"), "", "clicking a disabled row selects nothing");
    assertEq(ctx.calls.filter((call) => /\/vibrations$/.test(call.path)).length, 0);
  });

  await test("sources: a single eligible source auto-selects and loads its modes", async () => {
    const only = {
      job_id: JOB_ID,
      sources: [SOURCES.sources[0], SOURCES.sources[2]],
      warnings: []
    };
    const ctx = setup({ handler: (pathname) => (/\/frequency-sources$/.test(pathname) ? only : defaultHandler(pathname)) });
    await mount(ctx);
    assertEq(stateOf(ctx), "ready");
    assertEq(attr(ctx, "data-acp-selected-entry"), "alpha", "the only eligible source auto-selects");
    const vibe = ctx.calls.filter((call) => /\/vibrations$/.test(call.path));
    assertEq(vibe.length, 1);
    assert(vibe[0].path.indexOf("/jobs/freq-001/structure-viewer/entries/alpha/vibrations") !== -1,
      "modes are requested for the canonical entry id");
    assertEq(attr(ctx, "data-acp-modes-state"), "ready");
    assertEq(modeTexOf(ctx), ["6", "9", "13"], "only imaginary modes are offered, with native indices");
    assertEq(subtreeText(role(ctx, "mode-meta")).indexOf("共 4 个模式") !== -1, true);
    assertEq(subtreeText(role(ctx, "mode-meta")).indexOf("3 个虚频") !== -1, true);
  });

  await test("native mode index: selection and preview use ORCA's printed index, never row position", async () => {
    const ctx = setup();
    await mountWithSourceAndMode(ctx, "alpha", 13);
    assertEq(modeRows(ctx).length, 3);
    const positions = modeRows(ctx).map((row) => row.getAttribute("data-mode-index"));
    assertEq(positions, ["6", "9", "13"], "rows keep the payload order and native indices");
    assertEq(attr(ctx, "data-acp-selected-mode"), "13");
    const detail = role(ctx, "mode-detail");
    assertEq(detail.hidden, false);
    assertEq(subtreeText(detail).indexOf("原生模式序号") !== -1, true);
    const fields = walk(detail).filter((node) => node.getAttribute && node.getAttribute("data-mode-detail"));
    const byKey = {};
    fields.forEach((node) => { byKey[node.getAttribute("data-mode-detail")] = node.textContent; });
    assertEq(byKey["next.tsmode.mode_detail_index"], "13");
    assertEq(byKey["next.tsmode.mode_detail_frequency"], "-310.90 cm⁻¹");
    assertEq(byKey["next.tsmode.mode_detail_atoms"], "3");
    assertEq(byKey["next.tsmode.mode_detail_entry"], "alpha");
    assertEq(byKey["next.tsmode.mode_detail_source"], "normal_modes 产品");

    await runPreview(ctx);
    const body = lastPreflightBody(ctx);
    assertEq(body.workflow, "tsmode");
    assertEq(body.input.source_mode_index, 13, "native index 13, not position 2");
    assert(body.input.source_mode_index !== 2 && body.input.source_mode_index !== 0,
      "the position/row index must never leak into source_mode_index");

    // First row is native #6, which is not 0 either.
    await selectMode(ctx, 6);
    await runPreview(ctx);
    assertEq(lastPreflightBody(ctx).input.source_mode_index, 6, "native index 6, not position 0");
  });

  await test("source switch: modes reload, target/preview reset, summary follows the new entry", async () => {
    let modesPayload = MODES;
    const ctx = setup({
      handler: (pathname) => {
        if (/\/frequency-sources$/.test(pathname)) return SOURCES;
        if (/\/vibrations$/.test(pathname)) {
          return pathname.indexOf("/beta/vibrations") !== -1 ? MODES_B : modesPayload;
        }
        if (pathname === API + "/preflight") return PREFLIGHT_OK;
        if (pathname === API + "/jobs") return JOB_RECEIPT;
        throw new Error("unexpected path " + pathname);
      }
    });
    await mount(ctx);
    await selectSource(ctx, "alpha");
    await selectMode(ctx, 9);
    await runPreview(ctx);
    assertEq(attr(ctx, "data-acp-selected-entry"), "alpha");
    assertEq(attr(ctx, "data-acp-selected-mode"), "9");
    assertEq(subtreeText(role(ctx, "preview-status")).indexOf("预检通过") !== -1, true);

    await selectSource(ctx, "beta");
    assertEq(attr(ctx, "data-acp-selected-entry"), "beta");
    assertEq(attr(ctx, "data-acp-selected-mode"), "", "the old native target is dropped with its source");
    assertEq(modeTexOf(ctx), ["2"], "beta's imaginary modes replace alpha's");
    assertEq(attr(ctx, "data-acp-modes-state"), "ready");
    assertEq(subtreeText(role(ctx, "preview-status")).indexOf("尚未预检") !== -1, true,
      "switching the source invalidates the preview");
    assertEq(action(ctx, "submit").disabled, true);
    const vibr = ctx.calls.filter((call) => /\/vibrations$/.test(call.path));
    assertEq(vibr.length, 2);
    assert(vibr[1].path.indexOf("/beta/vibrations") !== -1);

    await selectMode(ctx, 2);
    await runPreview(ctx);
    assertEq(lastPreflightBody(ctx).input.entry_id, "beta");
    assertEq(lastPreflightBody(ctx).input.source_mode_index, 2);
    assertEq(subtreeText(role(ctx, "summary")).indexOf("beta") !== -1, true);
    modesPayload = MODES;
  });

  await test("invalid: no source / no mode / bad settings block preview and submit without a request", async () => {
    const ctx = setup();
    await mount(ctx);
    await runPreview(ctx);
    assertEq(preflightCalls(ctx).length, 0, "no source -> no preflight request");
    assertEq(role(ctx, "preview-status").textContent.indexOf("未通过本地校验") !== -1, true);
    assertEq(findAll(ctx.host, "data-error", "source_missing").length, 1);
    assertEq(findAll(ctx.host, "data-error", "mode_missing").length, 1);

    await selectSource(ctx, "alpha");
    await runPreview(ctx);
    assertEq(preflightCalls(ctx).length, 0, "no target mode -> no preflight request");
    assertEq(findAll(ctx.host, "data-error", "mode_missing").length, 1);

    await selectMode(ctx, 6);
    const maxField = find(ctx.host, "data-role", "max-iterations");
    maxField.value = "0";
    maxField.fire("input");
    await flush();
    await runPreview(ctx);
    assertEq(preflightCalls(ctx).length, 0, "invalid max_iterations blocks the preview");
    assertEq(findAll(ctx.host, "data-error", "max_iterations_invalid").length, 1);
    assertEq(action(ctx, "submit").disabled, true);

    maxField.value = "abc";
    maxField.fire("input");
    await flush();
    await runPreview(ctx);
    assertEq(findAll(ctx.host, "data-error", "max_iterations_invalid").length, 1);
    assertEq(preflightCalls(ctx).length, 0);

    maxField.value = "60";
    maxField.fire("input");
    await flush();
    await runPreview(ctx);
    assertEq(preflightCalls(ctx).length, 1, "valid settings finally reach /preflight");
    assertEq(action(ctx, "submit").disabled, false);
  });

  await test("empty + no-imaginary: no fabricated target and no submission", async () => {
    const emptySources = setup({ handler: (p) => (/\/frequency-sources$/.test(p) ? { job_id: JOB_ID, sources: [], warnings: [] } : defaultHandler(p)) });
    await mount(emptySources);
    assertEq(stateOf(emptySources), "empty");
    assertEq(subtreeText(role(emptySources, "overlay-detail")).indexOf("没有可发现的频率源") !== -1, true);
    assertEq(attr(emptySources, "data-acp-selected-mode"), "");
    assertEq(emptySources.calls.filter((call) => /\/vibrations$/.test(call.path)).length, 0);

    const realOnly = {
      available: true,
      modes: [
        { mode_index: 3, frequency_cm1: 100.1, imaginary: false, vectors: [] },
        { mode_index: 7, frequency_cm1: 300.2, imaginary: false, vectors: [] }
      ],
      atom_count: 3,
      source: "product",
      imaginary_count: 0
    };
    const ctx = setup({
      handler: (p) => {
        if (/\/frequency-sources$/.test(p)) return SOURCES;
        if (/\/vibrations$/.test(p)) return realOnly;
        return defaultHandler(p);
      }
    });
    await mountWithSourceAndMode(ctx, "alpha", null);
    assertEq(attr(ctx, "data-acp-modes-state"), "ready");
    assertEq(modeRows(ctx).length, 0, "non-imaginary modes are not offered as targets");
    assertEq(subtreeText(role(ctx, "mode-status")).indexOf("没有虚频模式") !== -1, true);
    await runPreview(ctx);
    assertEq(preflightCalls(ctx).length, 0);
    assertEq(findAll(ctx.host, "data-error", "mode_missing").length, 1);
  });

  await test("errors: source 404 -> error + retry; vibrations 404 keeps the target empty", async () => {
    let failSources = true;
    const ctx = setup({
      handler: (pathname) => {
        if (/\/frequency-sources$/.test(pathname)) {
          if (failSources) {
            const error = new Error("Job not found: freq-001");
            error.status = 404;
            error.body = { detail: "Job not found: freq-001" };
            throw error;
          }
          return SOURCES;
        }
        return defaultHandler(pathname);
      }
    });
    await mount(ctx);
    assertEq(stateOf(ctx), "error");
    assertEq(role(ctx, "overlay").hidden, false);
    assertEq(subtreeText(role(ctx, "overlay-message")).indexOf("频率源读取失败") !== -1, true);
    assertEq(subtreeText(role(ctx, "overlay-detail")).indexOf("任务不存在") !== -1, true);
    assertEq(action(ctx, "retry").hidden, false);
    assertEq(attr(ctx, "data-acp-selected-mode"), "");

    failSources = false;
    action(ctx, "retry").fire("click");
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(sourceRows(ctx).length, 3);

    const mismatch = setup({
      handler: (pathname) => {
        if (/\/frequency-sources$/.test(pathname)) return SOURCES;
        if (/\/vibrations$/.test(pathname)) {
          const error = new Error("Entry not found: alpha");
          error.status = 404;
          error.body = { detail: "Entry not found: alpha" };
          throw error;
        }
        return defaultHandler(pathname);
      }
    });
    await mount(mismatch);
    await selectSource(mismatch, "alpha");
    assertEq(attr(mismatch, "data-acp-modes-state"), "error");
    assertEq(modeRows(mismatch).length, 0, "a 404 mode list produces no rows");
    assertEq(subtreeText(role(mismatch, "mode-status")).indexOf("没有对应的结构查看器条目") !== -1, true);
    assertEq(attr(mismatch, "data-acp-selected-mode"), "");
    assertEq(action(mismatch, "submit").disabled, true);
    assertEq(submitCalls(mismatch).length, 0);
  });

  await test("preview: exact frozen tsmode body through POST /preflight, digest shown, submit enabled", async () => {
    const advisories = [{
      id: "source_not_verified",
      severity: "info",
      message: "source job is remote; the submit/prepare step will decide"
    }];
    const ctx = setup({
      handler: (pathname) => {
        if (pathname === API + "/preflight") {
          return Object.assign({}, PREFLIGHT_OK, { advisories: advisories });
        }
        return defaultHandler(pathname);
      }
    });
    await mount(ctx);
    await selectSource(ctx, "alpha");
    await selectMode(ctx, 9);
    await runPreview(ctx);

    const calls = preflightCalls(ctx);
    assertEq(calls.length, 1);
    assertEq(calls[0].opts.method, "POST");
    assertEq(calls[0].opts.headers["Content-Type"], "application/json");
    const body = JSON.parse(calls[0].opts.body);
    assertEq(body.workflow, "tsmode");
    assertEq(body.name, "TSMode_alpha_mode9");
    assertEq(Object.keys(body).sort(), ["input", "name", "workflow"], "no extra top-level fields");
    assertEq(Object.keys(body.input).sort(), [
      "allow_unverified_mapping", "entry_id", "final_frequency", "source_job_id", "source_mode_index"
    ], "only declared tsmode input keys");
    assertEq(body.input.source_job_id, JOB_ID);
    assertEq(body.input.entry_id, "alpha");
    assertEq(body.input.source_mode_index, 9);
    assertEq(body.input.allow_unverified_mapping, false, "unverified mapping stays opt-in");
    assertEq(body.input.final_frequency, true, "final frequency verification defaults on");
    assert(!("method" in body), "tsmode accepts no method block");
    assert(!("resources" in body), "this panel owns no resources block");
    assert(!("preflight_fingerprint" in body), "a preview carries no fingerprint");
    assert(!("recalc_hess" in body), "unset knobs are omitted, not defaulted");

    assertEq(subtreeText(role(ctx, "preview-status")).indexOf("预检通过") !== -1, true);
    assertEq(subtreeText(role(ctx, "preview-status")).indexOf(DIGEST.slice(0, 23)) !== -1, true);
    const advisoryItems = findAllAttr(ctx.host, "data-advisory");
    assertEq(advisoryItems.length, 1, "server advisories are rendered");
    assertEq(subtreeText(role(ctx, "advisories")).indexOf("submit/prepare") !== -1, true);
    assertEq(action(ctx, "submit").disabled, false);
    assertEq(submitCalls(ctx).length, 0, "a preview never submits");

    // Any config edit invalidates the preview.
    await selectMode(ctx, 13);
    assertEq(action(ctx, "submit").disabled, true, "a new target requires a new preview");
    assertEq(subtreeText(role(ctx, "preview-status")).indexOf("尚未预检") !== -1, true);
    await runPreview(ctx);
    assertEq(lastPreflightBody(ctx).input.source_mode_index, 13);
    assertEq(action(ctx, "submit").disabled, false);
  });

  await test("preview: infeasible reasons are listed, submit stays disabled, and no /jobs is sent", async () => {
    const ctx = setup({
      handler: (pathname) => {
        if (pathname === API + "/preflight") {
          return {
            schema_version: 1,
            feasible: false,
            reasons: [
              {
                code: "frequency_source_incomplete",
                message: "frequency entry 'alpha' has no Hessian; tsmode needs it to map the mode",
                field: "entry_id",
                rule: "frequency_hessian_missing"
              },
              { code: "input_invalid", message: "second reason" }
            ],
            advisories: [],
            plan_digest: null
          };
        }
        return defaultHandler(pathname);
      }
    });
    await mountWithSourceAndMode(ctx, "alpha", 6);
    await runPreview(ctx);
    assertEq(subtreeText(role(ctx, "preview-status")).indexOf("预检未通过") !== -1, true);
    const reasons = findAllAttr(ctx.host, "data-preflight-reason");
    assertEq(reasons.length, 2, "every server reason is rendered");
    assertEq(subtreeText(reasons[0]).indexOf("no Hessian") !== -1, true);
    assertEq(action(ctx, "submit").disabled, true);
    await runSubmit(ctx);
    assertEq(submitCalls(ctx).length, 0, "an infeasible preview can never submit");
    assertEq(attr(ctx, "data-acp-busy"), "none");
  });

  await test("settings: explicit unverified-mapping opt-in and optional knobs reach the wire body", async () => {
    const ctx = setup();
    await mountWithSourceAndMode(ctx, "alpha", 6);
    await runPreview(ctx);
    assertEq(lastPreflightBody(ctx).input.allow_unverified_mapping, false);
    assertEq(attr(ctx, "data-acp-allow-unverified"), "false");
    const warning = role(ctx, "unverified-warning");
    assertEq(warning.hidden, false, "the unverified-mapping warning is visible by default");

    const allow = find(ctx.host, "data-role", "allow-unverified");
    allow.checked = true;
    allow.fire("change");
    await flush();
    assertEq(attr(ctx, "data-acp-allow-unverified"), "true");
    assertEq(role(ctx, "unverified-warning").hidden, true, "the warning hides after the explicit opt-in");

    const finalFreq = find(ctx.host, "data-role", "final-frequency");
    finalFreq.checked = false;
    finalFreq.fire("change");
    const maxField = find(ctx.host, "data-role", "max-iterations");
    maxField.value = "40";
    maxField.fire("input");
    await flush();

    assertEq(action(ctx, "submit").disabled, true, "settings edits invalidate the old preview");
    await runPreview(ctx);
    const body = lastPreflightBody(ctx);
    assertEq(body.input.allow_unverified_mapping, true);
    assertEq(body.input.final_frequency, false);
    assertEq(body.input.max_iterations, 40);
    assertEq(Object.keys(body.input).sort(), [
      "allow_unverified_mapping", "entry_id", "final_frequency", "max_iterations",
      "source_job_id", "source_mode_index"
    ]);
    assertEq(find(ctx.host, "data-role", "recalc-hess"), null,
      "no recalc_hess control: the adapter drops the boolean form");
    assertEq(find(ctx.host, "data-role", "trust-radius"), null,
      "no trust_radius control: a float breaks the canonical plan digest");
    assertEq(find(ctx.host, "data-role", "retry-limit"), null,
      "no retry_limit control: 0 is coerced by the server adapter");
  });

  await test("submit: gated on the preview, fingerprint + Idempotency-Key, receipt and openTask", async () => {
    const ctx = setup();
    await mountWithSourceAndMode(ctx, "alpha", 13);
    await runSubmit(ctx);
    assertEq(submitCalls(ctx).length, 0, "no submit before a feasible preview");
    assertEq(role(ctx, "receipt").getAttribute("data-acp-receipt-state"), "error");
    assertEq(subtreeText(role(ctx, "receipt")).indexOf("预检通过后才能提交") !== -1, true);

    await runPreview(ctx);
    const previewBody = lastPreflightBody(ctx);
    await runSubmit(ctx);

    const calls = submitCalls(ctx);
    assertEq(calls.length, 1);
    assertEq(calls[0].opts.method, "POST");
    assertEq(calls[0].opts.headers["Content-Type"], "application/json");
    assert(calls[0].opts.headers["Idempotency-Key"].indexOf("next-tsmode-") === 0,
      "idempotency key is sent in the header");
    const body = lastSubmitBody(ctx);
    assertEq(body.preflight_fingerprint, DIGEST, "the preview digest binds the submit");
    const withoutFingerprint = Object.assign({}, body);
    delete withoutFingerprint.preflight_fingerprint;
    assertEq(withoutFingerprint, previewBody, "submit body is exactly the previewed plan");

    assertEq(attr(ctx, "data-acp-busy"), "none");
    const receipt = role(ctx, "receipt");
    assertEq(receipt.hidden, false);
    assertEq(receipt.getAttribute("data-acp-receipt-state"), "submitted");
    assertEq(subtreeText(receipt).indexOf("20261031_001_tsmode") !== -1, true);
    assertEq(subtreeText(receipt).indexOf("queued") !== -1, true);
    assertEq(subtreeText(receipt).indexOf(DIGEST.slice(0, 23)) !== -1, true);
    assertEq(role(ctx, "receipt-replay").hidden, true);
    assertEq(action(ctx, "open-task").hidden, false);

    action(ctx, "open-task").fire("click");
    assertEq(ctx.commands.opened.length, 1);
    assertEq(ctx.commands.opened[0].jobId, "20261031_001_tsmode");
    assertEq(ctx.commands.opened[0].attempt, 1);
    assertEq(ctx.commands.opened[0].source, "tsmode-editor");

    await runSubmit(ctx);
    assertEq(submitCalls(ctx).length, 1, "an already submitted plan is never posted twice");
    assertEq(subtreeText(role(ctx, "receipt")).indexOf("已提交") !== -1, true);
  });

  await test("submit errors: mapping gate, forbidden and preflight_stale are mapped readably", async () => {
    let mode = "mapping";
    const ctx = setup({
      handler: (pathname) => {
        if (pathname === API + "/preflight") return PREFLIGHT_OK;
        if (pathname === API + "/jobs") {
          if (mode === "mapping") {
            const error = new Error("mode_mapping_unsupported");
            error.status = 422;
            error.body = { detail: { error: "mode_mapping_unsupported", detail: "target resolution status needs opt-in" } };
            throw error;
          }
          if (mode === "forbidden") {
            const error = new Error("forbidden");
            error.status = 403;
            error.body = { detail: "当前身份无权提交任务" };
            throw error;
          }
          if (mode === "stale") {
            const error = new Error("preflight stale");
            error.status = 400;
            error.body = { detail: { code: "preflight_stale", message: "fingerprint mismatch" } };
            throw error;
          }
          return JOB_RECEIPT;
        }
        return defaultHandler(pathname);
      }
    });
    await mountWithSourceAndMode(ctx, "alpha", 6);
    await runPreview(ctx);
    await runSubmit(ctx);
    assertEq(role(ctx, "receipt").getAttribute("data-acp-receipt-state"), "error");
    assertEq(subtreeText(role(ctx, "receipt")).indexOf("mode_mapping_unsupported") !== -1, true);
    assertEq(subtreeText(role(ctx, "receipt")).indexOf("允许未验证映射") !== -1, true);
    assertEq(ctx.commands.opened.length, 0, "a rejected submit dispatches no navigation");

    mode = "forbidden";
    await runSubmit(ctx);
    assertEq(subtreeText(role(ctx, "receipt")).indexOf("无权提交") !== -1, true);
    assertEq(submitCalls(ctx).length, 2, "a failed submit may be retried by the user");

    mode = "stale";
    await runSubmit(ctx);
    assertEq(subtreeText(role(ctx, "receipt")).indexOf("重新预检") !== -1, true);
    assertEq(action(ctx, "submit").disabled, true, "a stale fingerprint clears the preview");

    mode = "ok";
    await runPreview(ctx);
    await runSubmit(ctx);
    assertEq(role(ctx, "receipt").getAttribute("data-acp-receipt-state"), "submitted");
  });

  await test("busy: double clicks never duplicate requests and both buttons disable", async () => {
    const previewGate = deferred();
    let previewCalls = 0;
    const ctx = setup({
      handler: (pathname) => {
        if (pathname === API + "/preflight") { previewCalls += 1; return previewGate.promise; }
        return defaultHandler(pathname);
      }
    });
    await mountWithSourceAndMode(ctx, "alpha", 6);
    action(ctx, "preview").fire("click");
    action(ctx, "preview").fire("click");
    await flush();
    assertEq(previewCalls, 1, "the second preview click while busy is ignored");
    assertEq(attr(ctx, "data-acp-busy"), "preview");
    assertEq(action(ctx, "preview").disabled, true);
    assertEq(action(ctx, "submit").disabled, true);
    assertEq(subtreeText(role(ctx, "preview-status")).indexOf("预检中") !== -1, true);
    previewGate.resolve(PREFLIGHT_OK);
    await flush();
    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(action(ctx, "submit").disabled, false);

    const submitGate = deferred();
    let submitCount = 0;
    const ctx2 = setup({
      handler: (pathname) => {
        if (pathname === API + "/preflight") return PREFLIGHT_OK;
        if (pathname === API + "/jobs") { submitCount += 1; return submitGate.promise; }
        return defaultHandler(pathname);
      }
    });
    await mountWithSourceAndMode(ctx2, "alpha", 6);
    await runPreview(ctx2);
    action(ctx2, "submit").fire("click");
    action(ctx2, "submit").fire("click");
    await flush();
    assertEq(submitCount, 1, "the second submit click while busy is ignored");
    assertEq(attr(ctx2, "data-acp-busy"), "submit");
    assertEq(subtreeText(action(ctx2, "submit")).indexOf("提交中") !== -1, true);
    submitGate.resolve(JOB_RECEIPT);
    await flush();
    assertEq(attr(ctx2, "data-acp-busy"), "none");
  });

  await test("generation/dispose: late sources, modes and preview results never paint", async () => {
    const sourcesGate = deferred();
    const ctx = setup({ handler: () => sourcesGate.promise });
    await mount(ctx);
    assertEq(stateOf(ctx), "loading");
    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0, "dispose removes the module DOM");
    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0, "dispose is idempotent");
    assertEq(ctx.context.listenerCount(), 0, "the context subscription is removed");
    assertEq(ctx.dom.document.activeDocListeners(), 0, "the acp:langchange listener is removed");
    sourcesGate.resolve(SOURCES);
    await flush();
    assertEq(ctx.host.children.length, 0, "a late sources result cannot rebuild the DOM");

    // Remount works on the same instance and really reloads.
    await ctx.instance.mount(ctx.host, { context: ctx.context, commands: ctx.commands });
    await flush();
    assertEq(stateOf(ctx), "ready", "remount reloads the sources");
    assertEq(ctx.calls.filter((call) => /\/frequency-sources$/.test(call.path)).length, 2);
    ctx.instance.dispose();

    // Late modes after dispose: no rows, no crash.
    const modesGate = deferred();
    const modesCtx = setup({
      handler: (pathname) => {
        if (/\/frequency-sources$/.test(pathname)) return SOURCES;
        if (/\/vibrations$/.test(pathname)) return modesGate.promise;
        return defaultHandler(pathname);
      }
    });
    await mount(modesCtx);
    const alpha = sourceRows(modesCtx).find((row) => row.getAttribute("data-source-entry") === "alpha");
    alpha.fire("click");
    await flush();
    assertEq(attr(modesCtx, "data-acp-modes-state"), "loading");
    modesCtx.instance.dispose();
    modesGate.resolve(MODES);
    await flush();
    assertEq(modesCtx.host.children.length, 0, "a late modes result cannot paint after dispose");

    // Late preview after dispose: no receipt and no submit.
    const previewGate = deferred();
    const previewCtx = setup({
      handler: (pathname) => {
        if (pathname === API + "/preflight") return previewGate.promise;
        return defaultHandler(pathname);
      }
    });
    await mountWithSourceAndMode(previewCtx, "alpha", 6);
    action(previewCtx, "preview").fire("click");
    await flush();
    assertEq(preflightCalls(previewCtx).length, 1);
    previewCtx.instance.dispose();
    previewGate.resolve(PREFLIGHT_OK);
    await flush();
    assertEq(previewCtx.host.children.length, 0);
    assertEq(submitCalls(previewCtx).length, 0, "a disposed panel never submits");
    assertEq(previewCtx.commands.opened.length, 0);
  });

  await test("context: a new task ref reloads sources and drops the old target/preview", async () => {
    const ctx = setup();
    await mount(ctx);
    await selectSource(ctx, "alpha");
    await selectMode(ctx, 9);
    await runPreview(ctx);
    assertEq(attr(ctx, "data-acp-selected-mode"), "9");

    ctx.context.setFocus({ taskRef: { jobId: "freq-002" } });
    await flush();
    assertEq(attr(ctx, "data-acp-task"), "freq-002");
    assertEq(attr(ctx, "data-acp-selected-entry"), "");
    assertEq(attr(ctx, "data-acp-selected-mode"), "");
    assertEq(action(ctx, "submit").disabled, true);
    const sourcesCalls = ctx.calls.filter((call) => /\/frequency-sources$/.test(call.path));
    assertEq(sourcesCalls.length, 2);
    assert(sourcesCalls[1].path.indexOf("/jobs/freq-002/frequency-sources") !== -1,
      "the new task is really reloaded");

    const before = ctx.calls.length;
    ctx.context.setFocus({ taskRef: { jobId: "freq-002" } });
    await flush();
    assertEq(ctx.calls.length, before, "an unchanged task ref does not refetch");
  });

  await test("i18n: static chrome carries next.tsmode keys with the zh fallback", async () => {
    const ctx = setup();
    await mount(ctx);
    const keys = collectAttr(root(ctx), "data-i18n");
    [
      "next.tsmode.title",
      "next.tsmode.source_title",
      "next.tsmode.mode_title",
      "next.tsmode.settings_title",
      "next.tsmode.review_title",
      "next.tsmode.preview",
      "next.tsmode.submit",
      "next.tsmode.allow_unverified_warn",
      "next.tsmode.mode_hint_native",
      "next.tsmode.note"
    ].forEach((key) => {
      assert(keys.indexOf(key) !== -1, "missing data-i18n key: " + key);
    });
    assertEq(subtreeText(find(ctx.host, "data-i18n", "next.tsmode.title")), "TS 模式优化编辑器");
    assertEq(subtreeText(find(ctx.host, "data-i18n", "next.tsmode.mode_title")).indexOf("原生模式序号") !== -1, true);

    ctx.dom.document.dispatchEvent({ type: "acp:langchange", detail: { lang: "en-US" } });
    await flush();
    assertEq(stateOf(ctx), "ready");
    assertEq(subtreeText(find(ctx.host, "data-i18n", "next.tsmode.title")), "TS 模式优化编辑器",
      "missing shell keys keep the local zh fallback instead of the bare key");
  });

  await test("no fake modes: unavailable/empty/malformed payloads never synthesize a mode", async () => {
    const internals = setup().mod._internals;

    const unavailable = internals.normalizeModes({ available: false, reason: "no_normal_modes", modes: [] });
    assertEq(unavailable.available, false);
    assertEq(unavailable.modes, []);
    assertEq(internals.imaginaryModes(unavailable.modes), []);

    const empty = internals.normalizeModes({ available: true, modes: [] });
    assertEq(empty.available, true);
    assertEq(empty.modes, []);

    const malformed = internals.normalizeModes({
      available: true,
      modes: [
        { mode_index: "not-a-number", frequency_cm1: -1, imaginary: true },
        { mode_index: -3, frequency_cm1: -2, imaginary: true },
        { mode_index: 6, frequency_cm1: -120.5, imaginary: true },
        { mode_index: 6, frequency_cm1: -120.5, imaginary: true },
        { nope: true }
      ]
    });
    assertEq(malformed.modes.length, 1, "invalid/duplicate native indices are dropped, not invented");
    assertEq(malformed.modes[0].modeIndex, 6);
    assertEq(internals.imaginaryModes(malformed.modes).map((mode) => mode.modeIndex), [6]);

    // UI-level: an unavailable payload shows the reason, no rows, no submission.
    const ctx = setup({
      handler: (pathname) => {
        if (/\/frequency-sources$/.test(pathname)) return SOURCES;
        if (/\/vibrations$/.test(pathname)) return { available: false, reason: "pending_fetch", modes: [] };
        return defaultHandler(pathname);
      }
    });
    await mountWithSourceAndMode(ctx, "alpha", null);
    assertEq(attr(ctx, "data-acp-modes-state"), "unavailable");
    assertEq(modeRows(ctx).length, 0);
    assertEq(attr(ctx, "data-acp-selected-mode"), "");
    assertEq(subtreeText(role(ctx, "mode-status")).indexOf("pending_fetch") !== -1, true);
    await runPreview(ctx);
    assertEq(preflightCalls(ctx).length, 0);
    assertEq(findAll(ctx.host, "data-error", "mode_missing").length, 1);
    assertEq(action(ctx, "submit").disabled, true);
  });

  await test("pure validation: error codes, source/mode identity and atom-count warning", async () => {
    const ctx = setup();
    const internals = ctx.mod._internals;
    const sources = internals.normalizeSources(SOURCES);
    assertEq(sources.sources.length, 3, "duplicates are dropped");
    assertEq(sources.sources[2].hessianAvailable, false);
    const modes = internals.normalizeModes(MODES);
    const alpha = sources.sources[0];

    function check(overrides) {
      return internals.validateSelection(Object.assign({
        jobId: JOB_ID,
        source: alpha,
        modes: { available: true, modes: modes.modes, atomCount: modes.atomCount },
        modeIndex: 6,
        settings: { maxIterations: "" },
        allowUnverifiedMapping: false
      }, overrides || {}));
    }
    function codes(result) {
      return result.errors.map((error) => error.code);
    }

    assertEq(check().ok, true);
    assertEq(check().warnings.map((warning) => warning.code), ["mapping_unverified"]);
    assertEq(check({ allowUnverifiedMapping: true }).warnings, []);

    assertEq(codes(check({ jobId: "" })), ["task_missing"]);
    assertEq(codes(check({ source: null })), ["source_missing"]);
    assertEq(codes(check({ source: sources.sources[2] })), ["hessian_missing"]);
    assertEq(codes(check({ modeIndex: null })), ["mode_missing"]);
    assertEq(codes(check({ modeIndex: 7 })), ["mode_unknown"], "7 is not among 6/9/13");
    assertEq(codes(check({ modeIndex: 14 })), ["mode_not_imaginary"], "a real mode cannot drive a TS optimization");
    const mismatch = check({ modeIndex: 6, modes: { available: true, modes: modes.modes, atomCount: 4 } });
    assertEq(mismatch.warnings.map((warning) => warning.code).indexOf("atom_count_mismatch") !== -1, true);
    assertEq(mismatch.ok, true, "an atom-count mismatch is a warning, not a local block");

    assertEq(codes(check({ settings: { maxIterations: "0" } })), ["max_iterations_invalid"]);
    assertEq(codes(check({ settings: { maxIterations: "2.5" } })), ["max_iterations_invalid"]);
    assertEq(codes(check({ settings: { maxIterations: "40" } })), []);

    const request = internals.buildRequest({
      jobId: JOB_ID,
      entryId: "alpha",
      modeIndex: 13,
      allowUnverifiedMapping: true,
      finalFrequency: true
    });
    assertEq(request.input.source_mode_index, 13);
    assertEq(request.input.allow_unverified_mapping, true);
    assertEq(request.input.final_frequency, true);
    assertEq(request.name, undefined, "no name without an explicit one");
    assertEq(internals.defaultJobName("alpha", 13), "TSMode_alpha_mode13");
    assertEq(internals.formatFrequency(-120.5), "-120.50");
    assertEq(internals.formatFrequency(450), "450.00");
  });

  await test("boundary pins: no second viewer, no direct network primitive, no task mutation", async () => {
    const source = fs.readFileSync(
      path.resolve(__dirname, "..", "modules", "tsmode-editor", "module.js"), "utf8");
    [
      "$3Dmol",
      "ACPVisualization",
      "scene_instance",
      "createViewer",
      "new Image(",
      "<canvas",
      "innerHTML",
      "fetch(",
      "XMLHttpRequest",
      "sendBeacon",
      "jobCommands",
      "ACPTsmodeEditor",
      "requestSubmit"
    ].forEach((token) => {
      assertEq(source.indexOf(token), -1, "module must not reference " + token);
    });
    assert(source.indexOf("ACPClient.transport.request") !== -1, "uses the existing client seam");
    assert(source.indexOf("frequency-sources") !== -1, "documents the sources endpoint");
    assert(source.indexOf("/vibrations") !== -1, "documents the modes endpoint");
    assert(source.indexOf("/preflight") !== -1, "documents the preview endpoint");
    assert(source.indexOf('"/jobs"') !== -1, "documents the existing submit endpoint");

    const ctx = setup();
    await mountWithSourceAndMode(ctx, "alpha", 6);
    await runPreview(ctx);
    const paths = ctx.calls.map((call) => call.path);
    assertEq(paths.some((p) => /\/tsmode/.test(p)), false, "no invented tsmode route is called");
    assertEq(paths.filter((p) => p === API + "/preflight").length, 1);
    assertEq(paths.filter((p) => p === API + "/jobs").length, 0);
    assertEq(ctx.calls.filter((call) => /cancel|rerun|pause|purge|edit/i.test(call.path)).length, 0,
      "no task mutation command is ever issued");
    assertEq(ctx.calls.filter((call) => /\/geometry$/.test(call.path)).length, 0,
      "no geometry is read (no second viewer)");
  });
};
