/* ACP 3.0 module test — job-edit (Track A / A-10 · A-11 · A-12 · P03 修改参数后重算).
 *
 * Loads the module as the classic script the Shell loader consumes
 * (window.ACPNextModules["job-edit"] = { descriptor, factory, _internals })
 * inside the zero-dependency node vm harness with a fake DOM. Every request
 * goes through an injected request seam, so no test can reach the network
 * (the sandbox has no fetch/XMLHttpRequest at all).
 *
 * Pinned contracts:
 *  1. exactly one ACPNextModules key, Shell-valid descriptor, no extra global;
 *  2. four states (empty/loading/ready/error) + separate busy state;
 *  3. A-10: the draft backfill is the server payload verbatim — method /
 *     resources JSON, charge / multiplicity, names, tags, coverage, inputs,
 *     effective config; no locally guessed default (an unrecorded parameter
 *     stays blank and never enters the request);
 *  4. A-11: preview is the existing zero-side-effect
 *     POST /jobs/{id}/edit-recalculate/preview with only the changed sections
 *     and expected_source_revision; the server diff / warnings / blocking /
 *     fingerprint are rendered; no execution request is issued;
 *  5. A-12: execute requires a current preview, sends request_id +
 *     preview_fingerprint + expected_source_revision, shows the attempt+1
 *     receipt with the same-directory rerun contract and the edit-coverage
 *     warning; 409 source-revision / fingerprint conflicts and 422/403/404/5xx
 *     map to readable states without replaying a rejected write;
 *  6. busy / validation / generation / dispose guards: late results never
 *     paint, double clicks never duplicate requests, dispose is idempotent;
 *  7. no direct network primitive, no filesystem path construction, no second
 *     task / cache, no mutation endpoint other than the edit recalculation.
 */
"use strict";

const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const MODULE_PATH = "js/modules/job-edit/module.js";
  const CSS_PATH = "css/modules/job-edit.css";
  const API = "/api/v1";
  const JOB = "20261031_001_singlepoint_demo";
  const JOB_B = "20261031_002_singlepoint_other";
  const WATER = "3\nwater\nO 0.000 0.000 0.000\nH 0.758 0.586 0.000\nH -0.758 0.586 0.000\n";
  const REVISION = "sr_" + "a".repeat(24);
  const REVISION_B = "sr_" + "c".repeat(24);
  const FINGERPRINT = "pf_" + "b".repeat(32);

  const DRAFT_URL = API + "/jobs/" + JOB + "/edit-draft";
  const PREVIEW_URL = API + "/jobs/" + JOB + "/edit-recalculate/preview";
  const EXECUTE_URL = API + "/jobs/" + JOB + "/edit-recalculate";

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
  async function flush(n = 10) {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await tick();
    for (let i = 0; i < 6; i++) await Promise.resolve();
  }

  function httpError(status, detail) {
    const message = typeof detail === "string"
      ? detail
      : (detail && (detail.message || detail.error)) || ("HTTP " + status);
    const error = new Error(message);
    error.status = status;
    error.body = { detail: detail };
    return error;
  }

  function fail(status, detail) {
    return { __httpError: { status: status, detail: detail } };
  }

  function merge(base, overrides) {
    if (!overrides) return base;
    const out = Array.isArray(base) ? base.slice() : Object.assign({}, base);
    Object.keys(overrides).forEach((key) => {
      const value = overrides[key];
      if (Array.isArray(value)) {
        out[key] = value.slice();
        return;
      }
      if (value && typeof value === "object") {
        if (Object.keys(value).length === 0) {
          out[key] = {};
          return;
        }
        if (out[key] && typeof out[key] === "object" && !Array.isArray(out[key])) {
          out[key] = merge(out[key], value);
          return;
        }
      }
      out[key] = value;
    });
    return out;
  }

  /* ---------------------------------------------------------- fixtures -- */

  function draftFixture(overrides) {
    const base = {
      job_id: JOB,
      workflow: "singlepoint",
      workflow_status: "active",
      job_status: "completed",
      attempt: 1,
      source_revision: REVISION,
      editable_spec: {
        workflow: "singlepoint",
        input: {
          source_type: "xyz_text",
          source: WATER,
          xyz_text: WATER,
          charge: 0,
          multiplicity: 1
        },
        method: {
          schema_id: "dft_sp",
          levels: { sp: { functional: "wB97X-D4", basis: "def2-TZVP" } }
        },
        resources: { nproc: 8, mem: "16GB" },
        molecule_name: "water",
        task_name: "water sp",
        remark: "frozen remark",
        tags: ["demo"],
        node_tags: [],
        project_id: "proj-a",
        execution_mode: "local",
        target_node: null,
        config_path: null
      },
      method_projection: {
        schema_id: "dft_sp",
        levels: { sp: { engine: "orca", functional: "wB97X-D4", basis: "def2-TZVP" } }
      },
      input_refs: {
        mode: "original",
        original: {
          kind: "xyz_text",
          charge: 0,
          multiplicity: 1,
          materialized_input_xyz: true
        },
        structure_items: [{
          source_id: "edit-original_input:" + JOB + ":input_001",
          item_id: "input_001",
          name: "frozen water",
          atom_count: 3,
          charge: 0,
          multiplicity: 1,
          geometry_status: "available",
          xyz_text: WATER
        }],
        last_structure: null,
        previous_outputs: []
      },
      effective_config: { status: "unavailable", source: null, config: null },
      capabilities: {
        can_edit: true,
        can_in_place: true,
        can_new_job: true,
        disabled_reasons: []
      },
      preserved_fields: ["levels", "schema_id"],
      missing_fields: [],
      migration_hint: null,
      notes: []
    };
    return merge(base, overrides);
  }

  function defaultPreview(body) {
    return {
      ok: true,
      mode: (body && body.mode) || "in_place",
      workflow: (body && body.workflow) || "singlepoint",
      job_id: JOB,
      diff: [{
        path: "method.levels.sp.functional",
        kind: "method",
        old: "wB97X-D4",
        new: "r2SCAN-3c"
      }],
      warnings: ["资源已修改（resources.nproc）"],
      blocking_reasons: [],
      preview_fingerprint: FINGERPRINT,
      source_revision: REVISION
    };
  }

  function defaultReceipt(body) {
    return {
      job_id: JOB,
      attempt: 2,
      operation: (body && body.mode) === "new_job" ? "new_job" : "in_place",
      status: "queued",
      replayed: false,
      diff_summary: [{
        path: "method.levels.sp.functional",
        kind: "method",
        old: "wB97X-D4",
        new: "r2SCAN-3c"
      }]
    };
  }

  /* ---------------------------------------------------------- fake DOM -- */

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
        appendChild(child) {
          child.parentNode = this;
          this.children.push(child);
          return child;
        },
        removeChild(child) {
          const index = this.children.indexOf(child);
          if (index >= 0) this.children.splice(index, 1);
          child.parentNode = null;
          return child;
        },
        replaceChildren() {
          this.children.forEach((child) => { child.parentNode = null; });
          this.children = [];
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

  function find(root, attr, value) {
    return walk(root).find((node) => node.getAttribute && node.getAttribute(attr) === value) || null;
  }

  function findAll(root, attr, value) {
    return walk(root).filter((node) => node.getAttribute && node.getAttribute(attr) === value);
  }

  function collectAttr(node, attr) {
    const out = [];
    walk(node).forEach((child) => {
      const value = child.getAttribute && child.getAttribute(attr);
      if (value != null) out.push(value);
    });
    return out;
  }

  function subtreeText(node) {
    if (!node) return "";
    let text = node.textContent || "";
    (node.children || []).forEach((child) => { text += " " + subtreeText(child); });
    return text;
  }

  /* ----------------------------------------------------------- harness -- */

  function loadModule(sandbox) {
    loadScript(MODULE_PATH, sandbox);
    return sandbox.ACPNextModules["job-edit"];
  }

  function makeServer(options) {
    options = options || {};
    const calls = [];

    function resolveValue(value, fallback, body, pathname, opts) {
      function settle(result) {
        if (result && result.__httpError) {
          throw httpError(result.__httpError.status, result.__httpError.detail);
        }
        return result;
      }
      if (typeof value === "function") {
        return Promise.resolve()
          .then(() => value(body, pathname, opts, calls.length))
          .then(settle);
      }
      if (value === undefined) return Promise.resolve(fallback);
      return Promise.resolve().then(() => settle(value));
    }

    function request(pathname, opts) {
      opts = opts || {};
      calls.push({ path: pathname, opts: opts });
      let body = null;
      if (opts.body) body = JSON.parse(opts.body);
      if (/\/edit-draft$/.test(pathname)) {
        return resolveValue(options.draft, draftFixture(), body, pathname, opts);
      }
      if (/\/edit-recalculate\/preview$/.test(pathname)) {
        return resolveValue(options.preview, defaultPreview(body), body, pathname, opts);
      }
      if (/\/edit-recalculate$/.test(pathname)) {
        return resolveValue(options.execute, defaultReceipt(body), body, pathname, opts);
      }
      return Promise.reject(new Error("unexpected path: " + pathname));
    }

    return {
      calls,
      request,
      callsTo(pattern) {
        return calls.filter((call) => pattern.test(call.path));
      }
    };
  }

  function makeContext(options) {
    options = options || {};
    const listeners = new Set();
    let snapshot = {
      projectId: options.projectId || null,
      taskRef: options.jobId ? { jobId: options.jobId, attempt: options.attempt } : null,
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
    const sandbox = makeSandbox({ encodeURIComponent: encodeURIComponent });
    const entry = loadModule(sandbox);
    const server = options.server || makeServer(options.serverOptions || {});
    const hasContext = options.context !== null;
    const context = hasContext
      ? (options.context || makeContext({ jobId: options.jobId }))
      : null;
    const commands = options.commands || makeCommands();
    const host = dom.document.createElement("div");
    const deps = { doc: dom.document, request: server.request, context, commands };
    if (options.jobId) deps.jobId = options.jobId;
    const instance = entry.factory(deps);
    return { dom, sandbox, entry, server, client: server, context, commands, host, deps, instance };
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
    const node = root(ctx);
    return node ? node.getAttribute(name) : null;
  }

  function field(ctx, role) {
    return find(ctx.host, "data-role", role);
  }

  function action(ctx, name) {
    return find(ctx.host, "data-action", name);
  }

  function all(ctx, attrName, value) {
    return findAll(ctx.host, attrName, value);
  }

  async function click(ctx, name) {
    const node = action(ctx, name);
    assert(!!node, "missing action: " + name);
    node.fire("click");
    await flush();
  }

  async function setField(ctx, role, value) {
    const input = field(ctx, role);
    assert(!!input, "missing field: " + role);
    input.value = value;
    input.fire("input");
    await flush();
  }

  async function setMode(ctx, value) {
    const radios = all(ctx, "data-role", "mode");
    radios.forEach((radio) => { radio.checked = radio.value === value; });
    const target = radios.find((radio) => radio.value === value);
    assert(!!target, "missing mode radio: " + value);
    target.fire("change");
    await flush();
  }

  function draftCalls(ctx) {
    return ctx.server.callsTo(/\/edit-draft$/);
  }

  function previewCalls(ctx) {
    return ctx.server.callsTo(/\/edit-recalculate\/preview$/);
  }

  function executeCalls(ctx) {
    return ctx.server.callsTo(/\/edit-recalculate$/);
  }

  function lastBody(calls) {
    assert(calls.length > 0, "no request was issued");
    return JSON.parse(calls[calls.length - 1].opts.body);
  }

  function editedMethod(ctx) {
    const method = JSON.parse(field(ctx, "method-text").value);
    method.levels.sp.functional = "r2SCAN-3c";
    return method;
  }

  async function loadReady(options) {
    const ctx = setup(Object.assign({ jobId: JOB }, options || {}));
    await mount(ctx);
    assertEq(stateOf(ctx), "ready", "draft should be ready");
    return ctx;
  }

  function canonicalOf(ctx, value) {
    return ctx.entry._internals.canonicalJson(value);
  }

  /* ------------------------------------------------------------- tests -- */

  await test("module: one ACPNextModules key, Shell-valid descriptor, no legacy globals", async () => {
    const sandbox = makeSandbox({ encodeURIComponent: encodeURIComponent });
    loadScript("js/shell/module-registry.js", sandbox);
    const mod = loadModule(sandbox);
    assertEq(Object.keys(sandbox.ACPNextModules), ["job-edit"], "exactly one module key");
    assertEq(typeof mod.factory, "function", "factory present");
    assertEq(sandbox.ACPShell.registry.validateDescriptor(mod.descriptor), null,
      "descriptor must pass the Shell registry validator");
    assertEq(mod.descriptor.id, "job-edit");
    assertEq(mod.descriptor.interfaceVersion, 1);
    assertEq(mod.descriptor.hosts, ["acp-web"]);
    assertEq(mod.descriptor.binds, ["taskRef"]);
    assertEq(mod.descriptor.error.titleKey, "next.jobedit.errorTitle");
    assertEq(mod.descriptor.error.messageKey, "next.jobedit.errorMessage");
    assertEq(mod._internals.MODES, ["in_place", "new_job"]);
    assertEq(typeof mod._internals.buildPreviewBody, "function", "internals exposed for pure tests");
    assert(!("jobEditState" in sandbox), "legacy page global must not exist");
    assert(!("jobEditResetState" in sandbox), "legacy helper must not be redefined");
    assertEq(mod._internals.ROUTES.draft("a b"), "/api/v1/jobs/a%20b/edit-draft");
    assertEq(mod._internals.ROUTES.preview("j/1"),
      "/api/v1/jobs/j%2F1/edit-recalculate/preview");
    assertEq(mod._internals.ROUTES.execute("j/1"), "/api/v1/jobs/j%2F1/edit-recalculate");
  });

  await test("empty: no task focus -> empty state, disabled actions, zero requests", async () => {
    const ctx = setup({ context: null });
    await mount(ctx);
    assertEq(root(ctx).className, "acp-jobedit");
    assertEq(stateOf(ctx), "empty", "initial state is empty");
    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(attr(ctx, "data-acp-mode"), "none");
    assertEq(attr(ctx, "data-acp-coverage"), "blocked");
    assertEq(field(ctx, "job-ref").textContent.indexOf("未选择任务") !== -1, true, "no-focus hint");
    assertEq(field(ctx, "draft-status").textContent.indexOf("尚未载入") !== -1, true);
    assertEq(action(ctx, "load-draft").disabled, true, "no job id -> no draft load");
    assertEq(action(ctx, "preview").disabled, true);
    assertEq(action(ctx, "execute").disabled, true);
    assertEq(field(ctx, "empty-hint").hidden, false);
    assertEq(subtreeText(root(ctx)).indexOf("编辑重算") !== -1, true, "zh title fallback");
    assertEq(ctx.server.calls.length, 0, "mount performs no network call");
  });

  await test("A-10 backfill: fields are the server draft verbatim, no guessed defaults", async () => {
    const draft = draftFixture();
    const ctx = setup({ jobId: JOB, serverOptions: { draft: draft } });
    await mount(ctx);
    assertEq(stateOf(ctx), "ready", "draft streamed to ready");
    assertEq(draftCalls(ctx).length, 1, "one draft request");
    assertEq(draftCalls(ctx)[0].path, DRAFT_URL);
    assertEq(draftCalls(ctx)[0].opts.method, "GET");
    assertEq(attr(ctx, "data-acp-editable"), "true");
    assertEq(attr(ctx, "data-acp-coverage"), "editable");
    assertEq(attr(ctx, "data-acp-mode"), "in_place", "capability-derived default mode");
    assertEq(attr(ctx, "data-acp-dirty"), "false");

    assertEq(field(ctx, "field-workflow").textContent, "singlepoint");
    assertEq(field(ctx, "field-workflow_status").textContent, "active");
    assertEq(field(ctx, "field-job_status").textContent, "completed");
    assertEq(field(ctx, "field-attempt").textContent, "1");
    assertEq(field(ctx, "field-source_revision").textContent, REVISION);
    assertEq(field(ctx, "field-molecule_name").textContent, "water");
    assertEq(field(ctx, "field-project_id").textContent, "proj-a");
    assertEq(field(ctx, "field-execution_mode").textContent, "local");
    assertEq(field(ctx, "field-target_node").textContent, "未设置", "null stays unset, never invented");
    assertEq(field(ctx, "field-config_path").textContent, "未设置");
    assertEq(field(ctx, "field-tags").textContent, "demo");

    assertEq(field(ctx, "input-kind").textContent, "xyz_text");
    assertEq(field(ctx, "input-charge").textContent, "0");
    assertEq(field(ctx, "input-multiplicity").textContent, "1");
    assertEq(field(ctx, "input-materialized").textContent, "是");
    assertEq(field(ctx, "structures-count").textContent.indexOf("可复用结构 1 个") !== -1, true);
    assertEq(all(ctx, "data-structure-item", "1").length, 1);
    assertEq(all(ctx, "data-structure-item", "1")[0].getAttribute("data-atom-count"), "3");
    assertEq(field(ctx, "outputs-count").textContent.indexOf("可复用结构产物 0 个") !== -1, true);
    assertEq(field(ctx, "effective-config").textContent.indexOf("不可用") !== -1, true);

    assertEq(field(ctx, "method-text").value,
      JSON.stringify(draft.editable_spec.method, null, 2), "method JSON is exact");
    assertEq(field(ctx, "resources-text").value,
      JSON.stringify(draft.editable_spec.resources, null, 2), "resources JSON is exact");
    assertEq(field(ctx, "charge").value, "0");
    assertEq(field(ctx, "multiplicity").value, "1");
    assertEq(field(ctx, "task-name").value, "water sp");
    assertEq(field(ctx, "remark").value, "frozen remark");
    assertEq(field(ctx, "tags").value, "demo");
    assertEq(field(ctx, "projection").textContent.indexOf("\"functional\"") !== -1, true,
      "server method projection rendered read-only");

    assertEq(field(ctx, "coverage-state").textContent.indexOf("已登记通用编辑重算") !== -1, true);
    assertEq(field(ctx, "coverage-preserved").textContent, "保留方法字段 2 个。");
    assertEq(field(ctx, "migration-hint").hidden, true);
    assertEq(field(ctx, "notes").hidden, true);
    assertEq(field(ctx, "draft-status").textContent.indexOf("草稿已载入") !== -1, true);
    assertEq(field(ctx, "preview-status").textContent.indexOf("尚未预览") !== -1, true);
    assertEq(action(ctx, "preview").disabled, false);
    assertEq(action(ctx, "execute").disabled, true, "execute needs a preview");
    const modes = all(ctx, "data-role", "mode");
    assertEq(modes.find((radio) => radio.checked).value, "in_place");
    assertEq(modes.every((radio) => radio.disabled === false), true);
    assertEq(modes.length, 2);

    // Pure seam: untouched form -> only the revision-bound envelope is sent.
    const internals = ctx.entry._internals;
    const view = internals.readBackfill(draft);
    const form = internals.formFromDraft(draft);
    const built = internals.buildPreviewBody(view, form);
    assertEq(built.ok, true);
    assertEq(Object.keys(built.body).sort(), ["expected_source_revision", "mode", "workflow"]);
    assertEq(built.patch, {}, "an untouched draft sends no patch section");
  });

  await test("A-10 no-defaults: missing parameters stay blank and never enter a request", async () => {
    const draft = draftFixture();
    draft.editable_spec.input = {
      source_type: "xyz_text",
      source: WATER,
      xyz_text: WATER
    };
    draft.editable_spec.method = {};
    draft.editable_spec.resources = {};
    draft.editable_spec.molecule_name = "";
    draft.editable_spec.task_name = "";
    draft.editable_spec.remark = "";
    draft.editable_spec.tags = [];
    draft.editable_spec.project_id = null;
    draft.editable_spec.execution_mode = null;
    draft.editable_spec.target_node = null;
    draft.editable_spec.config_path = null;
    draft.method_projection = {};
    draft.input_refs.original = { kind: "xyz_text", materialized_input_xyz: false };
    draft.input_refs.structure_items = [];
    draft.input_refs.previous_outputs = [];
    draft.preserved_fields = [];
    draft.missing_fields = ["input.source"];
    const ctx = setup({ jobId: JOB, serverOptions: { draft: draft } });
    await mount(ctx);
    assertEq(field(ctx, "method-text").value, "{}", "empty method is the empty object, not a default");
    assertEq(field(ctx, "resources-text").value, "{}", "no nproc/mem default is invented");
    assertEq(field(ctx, "charge").value, "");
    assertEq(field(ctx, "multiplicity").value, "");
    assertEq(field(ctx, "task-name").value, "");
    assertEq(field(ctx, "field-project_id").textContent, "未设置");
    assertEq(field(ctx, "input-charge").textContent, "未设置");
    assertEq(field(ctx, "input-multiplicity").textContent, "未设置");
    assertEq(field(ctx, "input-materialized").textContent, "否");
    assertEq(field(ctx, "missing-fields").hidden, false);
    assertEq(field(ctx, "missing-fields").textContent, "提交依赖缺失：input.source");
    assertEq(field(ctx, "coverage-preserved").textContent, "保留方法字段 0 个。");

    const internals = ctx.entry._internals;
    const built = internals.buildPreviewBody(internals.readBackfill(draft), internals.formFromDraft(draft));
    assertEq(built.ok, true);
    assertEq(Object.keys(built.body).sort(), ["expected_source_revision", "mode", "workflow"]);
    assertEq(canonicalOf(ctx, built.patch), canonicalOf(ctx, {}));
  });

  await test("draft errors: 404, malformed payload, network failure, retry recovers", async () => {
    let mode = "notfound";
    const ctx = setup({
      jobId: JOB,
      serverOptions: {
        draft: () => {
          if (mode === "notfound") throw httpError(404, "Job not found: " + JOB);
          if (mode === "payload") return { job_id: JOB };
          if (mode === "network") throw new Error("socket closed");
          return draftFixture();
        }
      }
    });
    await mount(ctx);
    assertEq(stateOf(ctx), "error");
    assertEq(field(ctx, "overlay").hidden, false, "error overlay visible");
    assertEq(subtreeText(field(ctx, "overlay-detail")).indexOf("任务不存在") !== -1, true);
    assertEq(action(ctx, "retry").hidden, false);

    mode = "payload";
    await click(ctx, "retry");
    assertEq(stateOf(ctx), "error");
    assertEq(subtreeText(field(ctx, "overlay-detail")).indexOf("缺少必需字段") !== -1, true);

    mode = "network";
    await click(ctx, "retry");
    assertEq(subtreeText(field(ctx, "overlay-detail")).indexOf("socket closed") !== -1, true);

    mode = "ok";
    await click(ctx, "retry");
    assertEq(stateOf(ctx), "ready");
    assertEq(field(ctx, "overlay").hidden, true);
    assertEq(draftCalls(ctx).length, 4, "each retry is a fresh draft request");
  });

  await test("A-11 preview: changed-section body, change list, warnings, digest, no execution", async () => {
    const ctx = await loadReady();
    const method = editedMethod(ctx);
    await setField(ctx, "method-text", JSON.stringify(method, null, 2));
    assertEq(attr(ctx, "data-acp-dirty"), "true", "server-verbatim change marks the form dirty");
    assertEq(action(ctx, "preview").disabled, false);

    await click(ctx, "preview");
    const calls = previewCalls(ctx);
    assertEq(calls.length, 1, "one preview request");
    assertEq(calls[0].path, PREVIEW_URL);
    assertEq(calls[0].opts.method, "POST");
    assertEq(calls[0].opts.headers["Content-Type"], "application/json");
    const body = lastBody(calls);
    assertEq(canonicalOf(ctx, body), canonicalOf(ctx, {
      mode: "in_place",
      workflow: "singlepoint",
      expected_source_revision: REVISION,
      method: { schema_id: "dft_sp", levels: { sp: { functional: "r2SCAN-3c", basis: "def2-TZVP" } } }
    }), "only the changed method section is sent");
    ["request_id", "preview_fingerprint", "input", "resources", "task_name", "remark", "tags"]
      .forEach((key) => assertEq(key in body, false, "preview body must not carry " + key));

    assertEq(attr(ctx, "data-acp-preview-state"), "ok");
    assertEq(attr(ctx, "data-acp-busy"), "none");
    const rows = all(ctx, "data-diff-path", "method.levels.sp.functional");
    assertEq(rows.length, 1, "server change list rendered");
    assertEq(rows[0].getAttribute("data-diff-kind"), "method");
    const rowText = subtreeText(rows[0]);
    assert(rowText.indexOf("wB97X-D4") !== -1 && rowText.indexOf("r2SCAN-3c") !== -1,
      "old -> new rendered: " + rowText);
    assertEq(field(ctx, "preview-digest").textContent.indexOf(FINGERPRINT) !== -1, true,
      "preview fingerprint digest shown");
    assertEq(all(ctx, "data-preview-warning", "1").length, 1);
    assertEq(subtreeText(field(ctx, "warnings")).indexOf("资源已修改") !== -1, true);
    assertEq(field(ctx, "preview-status").textContent.indexOf("预览通过：1 项变更") !== -1, true);
    assertEq(field(ctx, "execute-status").textContent.indexOf("预览通过后才会启用执行") !== -1, true);
    assertEq(action(ctx, "execute").disabled, false, "a clean preview enables execute");
    assertEq(executeCalls(ctx).length, 0, "preview never executes");
  });

  await test("A-11 preview: unmodified draft -> empty server diff and empty-change note", async () => {
    const ctx = await loadReady({
      serverOptions: {
        preview: {
          ok: true,
          mode: "in_place",
          workflow: "singlepoint",
          job_id: JOB,
          diff: [],
          warnings: [],
          blocking_reasons: [],
          preview_fingerprint: FINGERPRINT,
          source_revision: REVISION
        }
      }
    });
    await click(ctx, "preview");
    const body = lastBody(previewCalls(ctx));
    assertEq(Object.keys(body).sort(), ["expected_source_revision", "mode", "workflow"]);
    assertEq(attr(ctx, "data-acp-preview-state"), "ok");
    assertEq(field(ctx, "diff").children.length, 0, "no fabricated diff rows");
    assertEq(field(ctx, "diff-empty").hidden, false);
    assertEq(field(ctx, "diff-empty").textContent.indexOf("无变更") !== -1, true);
    assertEq(field(ctx, "preview-status").textContent.indexOf("预览通过：0 项变更") !== -1, true);
    assertEq(action(ctx, "execute").disabled, false, "a no-op preview can still resubmit the config");
  });

  await test("A-11 preview: metadata and charge/multiplicity edits patch only those fields", async () => {
    const ctx = await loadReady({
      serverOptions: {
        preview: (body) => ({
          ok: true,
          mode: body.mode,
          workflow: body.workflow,
          job_id: JOB,
          diff: [],
          warnings: [],
          blocking_reasons: [],
          preview_fingerprint: FINGERPRINT,
          source_revision: REVISION
        })
      }
    });

    await setField(ctx, "task-name", "renamed task");
    await setField(ctx, "remark", "new remark");
    await setField(ctx, "tags", "demo, extra");
    await click(ctx, "preview");
    let body = lastBody(previewCalls(ctx));
    assertEq(body.task_name, "renamed task");
    assertEq(body.remark, "new remark");
    assertEq(body.tags, ["demo", "extra"]);
    ["input", "method", "resources"].forEach((key) => {
      assertEq(key in body, false, "metadata-only edit must not send " + key);
    });

    // Discard restores the exact frozen values and clears preview/dirty state.
    await click(ctx, "reset-form");
    assertEq(field(ctx, "task-name").value, "water sp");
    assertEq(field(ctx, "remark").value, "frozen remark");
    assertEq(field(ctx, "tags").value, "demo");
    assertEq(attr(ctx, "data-acp-dirty"), "false");
    assertEq(attr(ctx, "data-acp-preview-state"), "idle");

    await setField(ctx, "charge", "-1");
    await setField(ctx, "multiplicity", "2");
    await click(ctx, "preview");
    body = lastBody(previewCalls(ctx));
    assertEq(canonicalOf(ctx, body.input), canonicalOf(ctx, { charge: -1, multiplicity: 2 }));
    assertEq("method" in body, false);
    assertEq("resources" in body, false);
    assertEq("task_name" in body, false);

    // Reverting to the frozen values drops the input patch again.
    await setField(ctx, "charge", "0");
    await setField(ctx, "multiplicity", "1");
    await click(ctx, "preview");
    body = lastBody(previewCalls(ctx));
    assertEq("input" in body, false, "a reverted field never rides in the request");
    assertEq(attr(ctx, "data-acp-dirty"), "false");
  });

  await test("A-11 preview: server blocking reasons disable execution", async () => {
    const ctx = await loadReady({
      serverOptions: {
        preview: {
          ok: false,
          mode: "in_place",
          workflow: "singlepoint",
          job_id: JOB,
          diff: [{ path: "method.levels.sp.functional", kind: "method", old: "a", new: "b" }],
          warnings: [],
          blocking_reasons: ["任务状态 RUNNING 非终态；请先取消并等待终态后再原地重算"],
          preview_fingerprint: FINGERPRINT,
          source_revision: REVISION
        }
      }
    });
    await click(ctx, "preview");
    assertEq(attr(ctx, "data-acp-preview-state"), "blocked");
    assertEq(all(ctx, "data-preview-blocking", "1").length, 1);
    assertEq(subtreeText(field(ctx, "blocking")).indexOf("非终态") !== -1, true);
    assertEq(field(ctx, "preview-status").textContent.indexOf("预览被阻塞") !== -1, true);
    assertEq(action(ctx, "execute").disabled, true, "blocked preview cannot execute");
    assertEq(field(ctx, "preview-digest").textContent.indexOf(FINGERPRINT) !== -1, true,
      "fingerprint is still shown for the blocked preview");
    await click(ctx, "execute");
    assertEq(executeCalls(ctx).length, 0, "blocked preview never executes");
  });

  await test("A-11 preview errors: 422 body, 409 source-revision conflict marks the draft stale", async () => {
    let phase = "invalid";
    const ctx = await loadReady({
      serverOptions: {
        preview: (body) => {
          if (phase === "invalid") {
            throw httpError(422, "原地重算锁定工作流；请切换为「创建新任务」以更换工作流");
          }
          if (phase === "conflict") {
            throw httpError(409, {
              error: "source_revision_conflict",
              message: "源任务配置已变化，请刷新编辑草稿后重试",
              current: "sr_other"
            });
          }
          return defaultPreview(body);
        }
      }
    });
    await click(ctx, "preview");
    assertEq(attr(ctx, "data-acp-preview-state"), "error");
    assertEq(field(ctx, "preview-status").textContent.indexOf("参数未通过服务端校验") !== -1, true);
    assertEq(field(ctx, "preview-status").textContent.indexOf("锁定工作流") !== -1, true);
    assertEq(action(ctx, "execute").disabled, true);

    phase = "conflict";
    await click(ctx, "preview");
    assertEq(attr(ctx, "data-acp-draft-stale"), "true", "409 revision conflict invalidates the draft");
    assertEq(field(ctx, "preview-status").textContent.indexOf("源任务配置已变化") !== -1, true);
    assertEq(field(ctx, "draft-status").textContent.indexOf("请重新载入") !== -1, true);
    assertEq(action(ctx, "preview").disabled, true, "stale draft cannot preview");
    assertEq(action(ctx, "execute").disabled, true);

    phase = "ok";
    await click(ctx, "load-draft");
    assertEq(stateOf(ctx), "ready");
    assertEq(attr(ctx, "data-acp-draft-stale"), "false");
    await click(ctx, "preview");
    assertEq(attr(ctx, "data-acp-preview-state"), "ok");
  });

  await test("A-12 execute: request_id + fingerprint + revision, attempt+1 receipt, coverage, openTask", async () => {
    const ctx = await loadReady();
    const method = editedMethod(ctx);
    await setField(ctx, "method-text", JSON.stringify(method, null, 2));
    await click(ctx, "preview");
    assertEq(attr(ctx, "data-acp-preview-state"), "ok");
    const previewBody = lastBody(previewCalls(ctx));

    await click(ctx, "execute");
    const calls = executeCalls(ctx);
    assertEq(calls.length, 1, "one execute request");
    assertEq(calls[0].path, EXECUTE_URL);
    assertEq(calls[0].opts.method, "POST");
    const body = lastBody(calls);
    assertEq(typeof body.request_id, "string", "request_id required by the route");
    assert(body.request_id.length > 0);
    assertEq(body.preview_fingerprint, FINGERPRINT);
    assertEq(body.expected_source_revision, REVISION);
    const withoutIds = Object.assign({}, body);
    delete withoutIds.request_id;
    delete withoutIds.preview_fingerprint;
    assertEq(canonicalOf(ctx, withoutIds), canonicalOf(ctx, previewBody),
      "execute body equals the previewed body + ids");

    assertEq(attr(ctx, "data-acp-receipt-state"), "submitted");
    assertEq(attr(ctx, "data-acp-busy"), "none");
    const receipt = field(ctx, "receipt");
    assertEq(receipt.hidden, false, "receipt visible");
    const bodyText = subtreeText(field(ctx, "receipt-body"));
    assert(bodyText.indexOf(JOB) !== -1, "receipt job id: " + bodyText);
    assert(bodyText.indexOf("第 2 次尝试") !== -1, "attempt+1 receipt: " + bodyText);
    assert(bodyText.indexOf("queued") !== -1, "receipt status: " + bodyText);
    assert(bodyText.indexOf("原地重算") !== -1, "receipt operation: " + bodyText);
    const attemptText = subtreeText(field(ctx, "receipt-attempt"));
    assert(attemptText.indexOf("原第 1 次") !== -1 && attemptText.indexOf("第 2 次") !== -1,
      "same-directory attempt+1 contract: " + attemptText);
    assert(attemptText.indexOf("attempt+1") !== -1, "explicit attempt+1 contract: " + attemptText);
    assert(attemptText.indexOf("同目录") !== -1, "same task directory statement: " + attemptText);
    const coverageText = subtreeText(field(ctx, "receipt-coverage"));
    assert(coverageText.indexOf("编辑覆盖") !== -1, "coverage warning on the receipt: " + coverageText);
    assert(coverageText.indexOf("保留方法字段 2 个") !== -1, coverageText);
    assert(coverageText.indexOf("缺字段 0 个") !== -1, coverageText);
    assertEq(field(ctx, "receipt-diff").textContent, "本次提交变更 1 项。");
    assertEq(field(ctx, "receipt-replay").hidden, true);

    assertEq(ctx.commands.opened.length, 1, "receipt focuses the task once");
    assertEq(ctx.commands.opened[0], { jobId: JOB, attempt: 2, source: "job-edit" });
    assertEq(attr(ctx, "data-acp-draft-stale"), "true",
      "an in-place attempt+1 changes the source revision: the draft must be reloaded");
    assertEq(action(ctx, "execute").disabled, true, "a completed execution is not repeatable");
    assertEq(action(ctx, "preview").disabled, true, "stale draft cannot preview again");
  });

  await test("A-12 execute: gated by a current preview; editing after preview invalidates it", async () => {
    const ctx = await loadReady();
    await click(ctx, "execute");
    assertEq(executeCalls(ctx).length, 0, "no preview -> no execute request");
    assertEq(field(ctx, "execute-status").textContent.indexOf("预览通过后才能执行") !== -1, true);

    const method = editedMethod(ctx);
    await setField(ctx, "method-text", JSON.stringify(method, null, 2));
    await click(ctx, "preview");
    assertEq(attr(ctx, "data-acp-preview-state"), "ok");
    assertEq(action(ctx, "execute").disabled, false);

    await setField(ctx, "remark", "changed after preview");
    assertEq(attr(ctx, "data-acp-preview-state"), "idle", "an edit invalidates the preview");
    assertEq(attr(ctx, "data-acp-dirty"), "true");
    assertEq(action(ctx, "execute").disabled, true);
    await click(ctx, "execute");
    assertEq(executeCalls(ctx).length, 0, "stale preview never executes");
  });

  await test("A-12 idempotency: request_id is stable across a failed retry", async () => {
    let executes = 0;
    const ctx = await loadReady({
      serverOptions: {
        execute: (body) => {
          executes += 1;
          if (executes === 1) throw new Error("connection reset");
          return defaultReceipt(body);
        }
      }
    });
    const method = editedMethod(ctx);
    await setField(ctx, "method-text", JSON.stringify(method, null, 2));
    await click(ctx, "preview");

    await click(ctx, "execute");
    assertEq(attr(ctx, "data-acp-receipt-state"), "error");
    assertEq(subtreeText(field(ctx, "receipt")).indexOf("connection reset") !== -1, true);
    assertEq(attr(ctx, "data-acp-preview-state"), "ok", "a transport error keeps the preview valid");
    assertEq(action(ctx, "execute").disabled, false, "retry is allowed without a new preview");
    const firstId = lastBody(executeCalls(ctx)).request_id;

    await click(ctx, "execute");
    const second = lastBody(executeCalls(ctx));
    assertEq(second.request_id, firstId, "the retry reuses the same request_id (IDEMPOTENT)");
    assertEq(attr(ctx, "data-acp-receipt-state"), "submitted");
    assertEq(executeCalls(ctx).length, 2);
  });

  await test("A-12 idempotency: replayed receipt is flagged without a second execution", async () => {
    const ctx = await loadReady({
      serverOptions: {
        execute: (body) => Object.assign(defaultReceipt(body), { replayed: true })
      }
    });
    const method = editedMethod(ctx);
    await setField(ctx, "method-text", JSON.stringify(method, null, 2));
    await click(ctx, "preview");
    await click(ctx, "execute");
    assertEq(attr(ctx, "data-acp-receipt-state"), "submitted");
    assertEq(field(ctx, "receipt-replay").hidden, false);
    assertEq(subtreeText(field(ctx, "receipt-replay")).indexOf("幂等重放") !== -1, true);
    assertEq(subtreeText(field(ctx, "receipt-body")).indexOf("第 2 次尝试") !== -1, true);
    assertEq(ctx.commands.opened.length, 1);
  });

  await test("A-12 execute 409 preview-fingerprint conflict invalidates the preview", async () => {
    const ctx = await loadReady({
      serverOptions: {
        execute: () => fail(409, {
          error: "preview_fingerprint_conflict",
          message: "提交的配置与预览时不一致，请重新预览后提交"
        })
      }
    });
    const method = editedMethod(ctx);
    await setField(ctx, "method-text", JSON.stringify(method, null, 2));
    await click(ctx, "preview");
    await click(ctx, "execute");
    assertEq(attr(ctx, "data-acp-receipt-state"), "error");
    assertEq(subtreeText(field(ctx, "receipt")).indexOf("重新预览") !== -1, true);
    assertEq(attr(ctx, "data-acp-preview-state"), "idle", "fingerprint conflict clears the preview");
    assertEq(action(ctx, "execute").disabled, true);
    assertEq(action(ctx, "preview").disabled, false, "re-preview is possible");
    assertEq(ctx.commands.opened.length, 0, "no task navigation on a rejected write");
  });

  await test("A-12 execute error mapping: 422 / 403 / 409 / 500 / 404", async () => {
    let phase = "422";
    let executes = 0;
    const ctx = await loadReady({
      serverOptions: {
        execute: () => {
          executes += 1;
          if (phase === "422") throw httpError(422, "原地重算锁定工作流");
          if (phase === "403") throw httpError(403, "禁止");
          if (phase === "409") throw httpError(409, "request_id 已用于不同的提交负载");
          if (phase === "500") throw httpError(500, "boom");
          throw httpError(404, "Job not found");
        }
      }
    });
    const method = editedMethod(ctx);
    await setField(ctx, "method-text", JSON.stringify(method, null, 2));
    await click(ctx, "preview");

    await click(ctx, "execute");
    assertEq(subtreeText(field(ctx, "receipt")).indexOf("参数未通过服务端校验") !== -1, true);
    const firstId = lastBody(executeCalls(ctx)).request_id;

    phase = "403";
    await click(ctx, "execute");
    assertEq(subtreeText(field(ctx, "receipt")).indexOf("没有权限执行该操作") !== -1, true);

    phase = "409";
    await click(ctx, "execute");
    const conflictText = subtreeText(field(ctx, "receipt"));
    assertEq(conflictText.indexOf("冲突") !== -1, true);
    assertEq(conflictText.indexOf("request_id") !== -1, true);

    phase = "500";
    await click(ctx, "execute");
    assertEq(subtreeText(field(ctx, "receipt")).indexOf("服务端错误") !== -1, true);
    assertEq(executes, 4);
    const afterConflictId = lastBody(executeCalls(ctx)).request_id;
    assert(afterConflictId !== firstId,
      "a request_id conflict resets the idempotency key for the next attempt");

    phase = "404";
    await click(ctx, "execute");
    assertEq(subtreeText(field(ctx, "receipt")).indexOf("任务不存在") !== -1, true);
    assertEq(attr(ctx, "data-acp-draft-stale"), "true", "404 forces a draft reload");
    assertEq(action(ctx, "preview").disabled, true);
    assertEq(ctx.commands.opened.length, 0, "no navigation on failed writes");
  });

  await test("A-12 receipt: attempt mismatch is surfaced, never silently accepted", async () => {
    const ctx = await loadReady({
      serverOptions: {
        execute: (body) => Object.assign(defaultReceipt(body), { attempt: 7 })
      }
    });
    const method = editedMethod(ctx);
    await setField(ctx, "method-text", JSON.stringify(method, null, 2));
    await click(ctx, "preview");
    await click(ctx, "execute");
    const text = subtreeText(field(ctx, "receipt-attempt"));
    assert(text.indexOf("注意") !== -1, "mismatch warning: " + text);
    assert(text.indexOf("预期 2") !== -1, "expected attempt computed from draft+1: " + text);
    assert(text.indexOf("7") !== -1, "server attempt shown: " + text);
    assertEq(field(ctx, "receipt").hidden, false);
  });

  await test("modes: in_place gated off (non-terminal) -> new_job default with coverage reason", async () => {
    const draft = draftFixture({
      job_status: "running",
      capabilities: {
        can_edit: true,
        can_in_place: false,
        can_new_job: true,
        disabled_reasons: ["任务状态 RUNNING 非终态；请先取消并等待终态后再原地重算"]
      }
    });
    const ctx = setup({
      jobId: JOB,
      serverOptions: {
        draft: draft,
        execute: (body) => Object.assign(defaultReceipt(body), { attempt: 1, operation: "new_job" })
      }
    });
    await mount(ctx);
    assertEq(attr(ctx, "data-acp-mode"), "new_job", "capability-derived mode");
    const modes = all(ctx, "data-role", "mode");
    const inPlace = modes.find((radio) => radio.value === "in_place");
    const newJob = modes.find((radio) => radio.value === "new_job");
    assertEq(inPlace.disabled, true, "in_place is disabled by the server capability");
    assertEq(newJob.disabled, false);
    assertEq(all(ctx, "data-coverage-reason", "1").length, 1);
    assertEq(subtreeText(field(ctx, "coverage-reasons")).indexOf("非终态") !== -1, true);

    await click(ctx, "preview");
    assertEq(lastBody(previewCalls(ctx)).mode, "new_job");
    assertEq(field(ctx, "mode-hint").textContent.indexOf("创建一个新任务") !== -1, true);

    await click(ctx, "execute");
    const receiptText = subtreeText(field(ctx, "receipt-attempt"));
    assert(receiptText.indexOf("新任务从第 1 次尝试开始") !== -1, "new job attempt line: " + receiptText);
    assert(receiptText.indexOf("原任务第 1 次不受影响") !== -1, receiptText);
    assertEq(subtreeText(field(ctx, "receipt-body")).indexOf("创建新任务") !== -1, true);
  });

  await test("modes: fully blocked workflow -> coverage blocked, zero preview/execute calls", async () => {
    const draft = draftFixture({
      workflow: "optfreq",
      workflow_status: "retired",
      capabilities: {
        can_edit: false,
        can_in_place: false,
        can_new_job: false,
        disabled_reasons: ["工作流 optfreq：BatchOptimize --profile opt_freq（或 optimize + frequency）"]
      },
      migration_hint: "BatchOptimize --profile opt_freq（或 optimize + frequency）"
    });
    const ctx = setup({ jobId: JOB, serverOptions: { draft: draft } });
    await mount(ctx);
    assertEq(attr(ctx, "data-acp-editable"), "false");
    assertEq(attr(ctx, "data-acp-coverage"), "blocked");
    assertEq(field(ctx, "coverage-state").textContent.indexOf("未登记通用编辑重算") !== -1, true);
    assertEq(field(ctx, "migration-hint").hidden, false);
    assertEq(field(ctx, "migration-hint").textContent.indexOf("迁移建议") !== -1, true);
    assertEq(all(ctx, "data-coverage-reason", "1").length, 1);
    assertEq(action(ctx, "preview").disabled, true);
    assertEq(action(ctx, "execute").disabled, true);
    await click(ctx, "preview");
    await click(ctx, "execute");
    assertEq(previewCalls(ctx).length, 0, "blocked workflow never previews");
    assertEq(executeCalls(ctx).length, 0, "blocked workflow never executes");
    assertEq(attr(ctx, "data-acp-preview-state"), "invalid", "local validation blocks the request");
  });

  await test("busy: double clicks never duplicate requests, controls disable while running", async () => {
    const previewGate = deferred();
    const executeGate = deferred();
    const ctx = await loadReady({
      serverOptions: {
        preview: () => previewGate.promise,
        execute: () => executeGate.promise
      }
    });
    const method = editedMethod(ctx);
    await setField(ctx, "method-text", JSON.stringify(method, null, 2));

    action(ctx, "preview").fire("click");
    action(ctx, "preview").fire("click");
    await flush();
    assertEq(previewCalls(ctx).length, 1, "a second click while previewing is ignored");
    assertEq(attr(ctx, "data-acp-busy"), "preview");
    assertEq(action(ctx, "preview").disabled, true);
    assertEq(field(ctx, "preview-status").textContent.indexOf("预览中") !== -1, true);
    action(ctx, "execute").fire("click");
    await flush();
    assertEq(executeCalls(ctx).length, 0, "execute cannot start during a preview");

    previewGate.resolve(defaultPreview({ mode: "in_place" }));
    await flush();
    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(attr(ctx, "data-acp-preview-state"), "ok");

    action(ctx, "execute").fire("click");
    action(ctx, "execute").fire("click");
    await flush();
    assertEq(executeCalls(ctx).length, 1, "a second execute click is ignored");
    assertEq(attr(ctx, "data-acp-busy"), "execute");
    assertEq(field(ctx, "execute-status").textContent.indexOf("执行中") !== -1, true);
    executeGate.resolve(defaultReceipt({ mode: "in_place" }));
    await flush();
    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(ctx.commands.opened.length, 1);
  });

  await test("client validation: bad JSON / non-integer state / unavailable mode sends nothing", async () => {
    const ctx = await loadReady();
    const cases = [
      { role: "method-text", value: "{\"levels\":", error: "method_json" },
      { role: "method-text", value: "[]", error: "method_object" },
      { role: "resources-text", value: "[1]", error: "resources_object" },
      { role: "resources-text", value: "{oops", error: "resources_json" },
      { role: "charge", value: "abc", error: "charge_integer" },
      { role: "multiplicity", value: "abc", error: "multiplicity_integer" },
      { role: "multiplicity", value: "0", error: "multiplicity_positive" }
    ];
    for (const scenario of cases) {
      await setField(ctx, scenario.role, scenario.value);
      const codes = all(ctx, "data-error", scenario.error);
      assert(codes.length >= 1, "missing local error " + scenario.error);
      await click(ctx, "preview");
      assertEq(attr(ctx, "data-acp-preview-state"), "invalid",
        "invalid form blocks preview (" + scenario.error + ")");
      assertEq(previewCalls(ctx).length, 0,
        "no preview request for local error " + scenario.error);
    }
    await setField(ctx, "method-text", JSON.stringify({ schema_id: "dft_sp" }, null, 2));
    await setField(ctx, "resources-text", "{}");
    await setField(ctx, "charge", "0");
    await setField(ctx, "multiplicity", "1");
    await click(ctx, "preview");
    assertEq(previewCalls(ctx).length, 1, "a valid form previews once");
    assertEq(attr(ctx, "data-acp-preview-state"), "ok");
  });

  await test("guards: dispose removes DOM, drops late draft/preview/execute results, is idempotent", async () => {
    const draftGate = deferred();
    const previewGate = deferred();
    const executeGate = deferred();
    const ctx = setup({
      jobId: JOB,
      serverOptions: {
        draft: () => draftGate.promise,
        preview: () => previewGate.promise,
        execute: () => executeGate.promise
      }
    });
    await ctx.instance.mount(ctx.host, { context: ctx.context, commands: ctx.commands });
    await flush();
    assertEq(stateOf(ctx), "loading");
    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0, "dispose removes the module DOM");
    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0, "dispose is idempotent");
    assertEq(ctx.context.listenerCount(), 0, "context subscription removed");
    draftGate.resolve(draftFixture());
    await flush();
    assertEq(ctx.host.children.length, 0, "a late draft cannot rebuild the DOM");

    const ctx2 = setup({
      jobId: JOB,
      serverOptions: {
        preview: () => previewGate.promise,
        execute: () => executeGate.promise
      }
    });
    await mount(ctx2);
    const method = editedMethod(ctx2);
    await setField(ctx2, "method-text", JSON.stringify(method, null, 2));
    action(ctx2, "preview").fire("click");
    await flush();
    assertEq(previewCalls(ctx2).length, 1);
    ctx2.instance.dispose();
    previewGate.resolve(defaultPreview({ mode: "in_place" }));
    await flush();
    assertEq(ctx2.host.children.length, 0);
    assertEq(attr(ctx2, "data-acp-preview-state"), null, "no root attributes after dispose");
    assertEq(ctx2.commands.opened.length, 0, "no openTask from a disposed instance");
    assertEq(executeCalls(ctx2).length, 0);

    // Remount on the same instance re-reads the draft from scratch (no leaked
    // edit, preview or receipt state).
    const beforeRemount = draftCalls(ctx2).length;
    await ctx2.instance.mount(ctx2.host, {});
    await flush();
    assertEq(draftCalls(ctx2).length, beforeRemount + 1, "remount re-reads the draft");
    assertEq(stateOf(ctx2), "ready");
    assertEq(attr(ctx2, "data-acp-preview-state"), "idle");
    assertEq(field(ctx2, "method-text").value.indexOf("wB97X-D4") !== -1, true,
      "fresh draft backfill after remount");
    assertEq(field(ctx2, "method-text").value.indexOf("r2SCAN-3c"), -1,
      "the discarded edit never survives dispose/remount");
    assertEq(action(ctx2, "execute").disabled, true);
    assertEq(attr(ctx2, "data-acp-receipt-state"), "idle");
    ctx2.instance.dispose();
  });

  await test("guards: task focus change discards the old draft and a late preview never paints", async () => {
    const gate = deferred();
    let previewJob = null;
    const ctx = setup({
      context: makeContext({ jobId: JOB }),
      serverOptions: {
        draft: (body, pathname) => (pathname.indexOf(JOB_B) !== -1
          ? draftFixture({
            job_id: JOB_B,
            source_revision: REVISION_B,
            editable_spec: {
              method: { schema_id: "dft_sp", levels: { sp: { functional: "HF" } } }
            }
          })
          : draftFixture()),
        preview: () => gate.promise
      }
    });
    await mount(ctx);
    assertEq(field(ctx, "job-ref").textContent.indexOf(JOB) !== -1, true);
    const method = editedMethod(ctx);
    await setField(ctx, "method-text", JSON.stringify(method, null, 2));
    action(ctx, "preview").fire("click");
    await flush();
    previewJob = previewCalls(ctx)[0].path;
    assertEq(previewJob, PREVIEW_URL);

    ctx.context.setFocus({ taskRef: { jobId: JOB_B } });
    await flush();
    assertEq(field(ctx, "job-ref").textContent.indexOf(JOB_B) !== -1, true, "new focus painted");
    assertEq(field(ctx, "method-text").value.indexOf("HF") !== -1, true,
      "the new task's real params replace the old draft");
    assertEq(attr(ctx, "data-acp-preview-state"), "idle", "old preview state discarded");

    gate.resolve(defaultPreview({ mode: "in_place" }));
    await flush();
    assertEq(attr(ctx, "data-acp-preview-state"), "idle", "a late preview for the old task never paints");
    assertEq(action(ctx, "execute").disabled, true);
    assertEq(executeCalls(ctx).length, 0, "no execute after the focus changed");

    ctx.context.setFocus({ taskRef: null });
    await flush();
    assertEq(stateOf(ctx), "empty", "clearing the focus empties the panel");
    assertEq(field(ctx, "job-ref").textContent.indexOf("未选择任务") !== -1, true);
  });

  await test("guards: a focus attempt change reloads the draft and discards stale edits", async () => {
    let drafts = 0;
    const ctx = setup({
      context: makeContext({ jobId: JOB, attempt: 1 }),
      serverOptions: {
        draft: () => {
          drafts += 1;
          return draftFixture({
            attempt: drafts,
            source_revision: "sr_" + String(drafts).repeat(24)
          });
        }
      }
    });
    await mount(ctx);
    assertEq(field(ctx, "field-attempt").textContent, "1");
    const method = editedMethod(ctx);
    await setField(ctx, "method-text", JSON.stringify(method, null, 2));
    assertEq(attr(ctx, "data-acp-dirty"), "true");

    ctx.context.setFocus({ taskRef: { jobId: JOB, attempt: 2 } });
    await flush();
    assertEq(draftCalls(ctx).length, 2, "a new attempt reloads the draft");
    assertEq(field(ctx, "field-attempt").textContent, "2");
    assertEq(attr(ctx, "data-acp-dirty"), "false", "the stale edit is discarded");
    assertEq(field(ctx, "method-text").value.indexOf("r2SCAN-3c"), -1,
      "the discarded edit never survives the attempt reload");
    assertEq(stateOf(ctx), "ready");
    assertEq(action(ctx, "execute").disabled, true);
  });

  await test("suspend/resume: results are kept in state and painted on resume only", async () => {
    const gate = deferred();
    const ctx = await loadReady({ serverOptions: { preview: () => gate.promise } });
    const method = editedMethod(ctx);
    await setField(ctx, "method-text", JSON.stringify(method, null, 2));
    action(ctx, "preview").fire("click");
    await flush();
    assertEq(field(ctx, "preview-status").textContent.indexOf("预览中") !== -1, true);

    ctx.instance.suspend();
    gate.resolve(defaultPreview({ mode: "in_place" }));
    await flush();
    assertEq(field(ctx, "preview-status").textContent.indexOf("预览中") !== -1, true,
      "suspended panel performs no DOM writes");
    assertEq(attr(ctx, "data-acp-preview-state"), "running");

    ctx.instance.resume();
    await flush();
    assertEq(attr(ctx, "data-acp-preview-state"), "ok");
    assertEq(field(ctx, "preview-status").textContent.indexOf("预览通过") !== -1, true);
    assertEq(action(ctx, "execute").disabled, false);
  });

  await test("no client seam: explicit error and zero network, never a raw fetch fallback", async () => {
    const dom = makeDom();
    const sandbox = makeSandbox({ encodeURIComponent: encodeURIComponent });
    const entry = loadModule(sandbox);
    const instance = entry.factory({ doc: dom.document, jobId: JOB, commands: null });
    const host = dom.document.createElement("div");
    await instance.mount(host, {});
    await flush();
    const rootNode = host.children[0];
    assertEq(rootNode.getAttribute("data-acp-module-state"), "error");
    const detail = find(host, "data-role", "overlay-detail");
    assert(detail.textContent.indexOf("ACPClient.transport") !== -1,
      "missing client seam reported explicitly: " + detail.textContent);
    find(host, "data-action", "retry").fire("click");
    await flush();
    assertEq(host.children[0].getAttribute("data-acp-module-state"), "error");
    instance.dispose();
  });

  await test("boundary pins: injected seam only, no fetch/XHR/paths/second cache, three routes", async () => {
    const source = fs.readFileSync(path.resolve(__dirname, "../modules/job-edit/module.js"), "utf8");
    [
      "fetch(",
      "XMLHttpRequest",
      "sendBeacon",
      "localStorage",
      "sessionStorage",
      "jobCommands",
      "$3Dmol",
      "ACPVisualization",
      "scene_instance",
      "jobEditState"
    ].forEach((token) => {
      assertEq(source.indexOf(token), -1, "module must not reference " + token);
    });
    assert(source.indexOf("ACPClient.transport.request") !== -1, "uses the existing client seam");
    assert(source.indexOf("/edit-recalculate/preview") !== -1, "preview route documented");
    assert(source.indexOf("/edit-recalculate") !== -1, "execute route documented");
    assertEq((source.match(/edit-draft/g) || []).length > 0, true, "draft route documented");
    // No filesystem path vocabulary: the module never receives or builds a
    // work directory; only the three API routes are composed.
    ["work_dir", "run_root", "task_dir", "Path("].forEach((token) => {
      assertEq(source.indexOf(token), -1, "no filesystem path handling: " + token);
    });

    const ctx = await loadReady();
    const method = editedMethod(ctx);
    await setField(ctx, "method-text", JSON.stringify(method, null, 2));
    await click(ctx, "preview");
    await click(ctx, "execute");
    const paths = ctx.server.calls.map((call) => call.path);
    assertEq(paths.every((value) =>
      value === DRAFT_URL || value === PREVIEW_URL || value === EXECUTE_URL),
    true, "only the three documented edit routes are ever called: " + JSON.stringify(paths));
    assertEq(paths.filter((value) => /\/jobs\/[^/]+$/.test(value) && value !== EXECUTE_URL).length, 0,
      "no bare /jobs/{id} mutation is issued");
  });

  await test("i18n: static chrome carries keys, zh fallback applies, langchange re-renders", async () => {
    const ctx = await loadReady();
    const keys = collectAttr(root(ctx), "data-i18n");
    [
      "next.jobedit.title",
      "next.jobedit.subtitle",
      "next.jobedit.source_title",
      "next.jobedit.coverage_title",
      "next.jobedit.original_title",
      "next.jobedit.input_title",
      "next.jobedit.edit_title",
      "next.jobedit.method",
      "next.jobedit.resources",
      "next.jobedit.preview_title",
      "next.jobedit.preview",
      "next.jobedit.execute_title",
      "next.jobedit.execute",
      "next.jobedit.receipt",
      "next.jobedit.open_task",
      "next.jobedit.retry",
      "next.jobedit.reset_form"
    ].forEach((key) => {
      assert(keys.indexOf(key) !== -1, "missing data-i18n key: " + key);
    });
    assertEq(subtreeText(find(ctx.host, "data-i18n", "next.jobedit.title")), "编辑重算");
    assertEq(field(ctx, "draft-status").textContent.indexOf("草稿已载入") !== -1, true);

    ctx.dom.document.dispatchEvent({ type: "acp:langchange", detail: { lang: "en-US" } });
    await flush();
    assertEq(stateOf(ctx), "ready", "language change preserves the loaded draft");
    assertEq(subtreeText(find(ctx.host, "data-i18n", "next.jobedit.title")), "编辑重算",
      "missing shell keys keep the zh fallback instead of the bare key");
    assertEq(field(ctx, "coverage-state").textContent.indexOf("已登记") !== -1, true,
      "dynamic copy survives the language change");
  });

  await test("CSS: scoped to .acp-jobedit, reuses tokens, has the narrow fallback", async () => {
    const css = fs.readFileSync(path.resolve(__dirname, "../../css/modules/job-edit.css"), "utf8");
    assert(css.indexOf(".acp-jobedit") !== -1);
    assert(css.indexOf(".acp-jobedit-receipt[data-acp-receipt-state=\"error\"]") !== -1,
      "receipt error state is styled");
    assert(css.indexOf("@media (max-width: 720px)") !== -1, "narrow fallback present");
    assert(css.indexOf(":root") === -1, "the module defines no new tokens");
    assert(css.indexOf("var(--accent") !== -1, "reuses the shell tokens");
    assert(!/^\s*body\s*\{/m.test(css), "no global element selector");
    assert(css.indexOf(".acp-jobedit[data-acp-draft-stale=\"true\"]") !== -1,
      "stale draft warning is surfaced");
  });
};
