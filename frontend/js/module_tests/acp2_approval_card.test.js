/* ACP 2.0 module test — approval-card (A-57 / P22).
 *
 * Loads the real module in the zero-dependency vm sandbox and drives it through
 * a fake DOM, a routed fake transport and a fake WorkspaceContext. `fetch` is
 * never defined, so a direct network call cannot pass.
 *
 * Pinned contracts:
 *   - four states (empty / loading / ready / error) driven only by the server;
 *   - ready renders the SERVER budget, execution plan, science projection,
 *     plan_digest (and science digest when present), status/revision,
 *     requester/expiry — no local digest or budget is computed;
 *   - approve/reject/revoke POST ONLY { expected_revision, reason? }; the
 *     approver identity never appears in the payload;
 *   - actions are disabled while a decision is in flight;
 *   - a 409 refreshes the record revision AND the pending list, never replays
 *     the decision; an access refusal is surfaced and the record is re-read;
 *   - dispose clears the host, unsubscribes and drops a late response;
 *   - the module never calls a /jobs or /submit route and never uses `fetch`.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const MODULE_PATH = "js/modules/approval-card/module.js";
const MODULE_ABS = path.join(REPO_ROOT, "frontend", MODULE_PATH);
const APPROVALS = "/api/v2/research/approvals";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const flush = async (n = 12) => {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
    for (let i = 0; i < 6; i++) await Promise.resolve();
  };

  // --------------------------------------------------------------- fake DOM --
  function makeDom() {
    const docListeners = new Map();
    function makeEl(tag) {
      const listeners = new Map();
      return {
        tagName: String(tag).toUpperCase(),
        children: [],
        parentNode: null,
        className: "",
        textContent: "",
        value: "",
        hidden: false,
        disabled: false,
        type: "",
        title: "",
        attrs: {},
        listeners,
        setAttribute(k, v) { this.attrs[k] = String(v); },
        getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
        removeAttribute(k) { delete this.attrs[k]; },
        appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
        replaceChildren(...nodes) {
          this.children.forEach((c) => { c.parentNode = null; });
          this.children = nodes.slice();
          nodes.forEach((n) => { n.parentNode = this; });
        },
        addEventListener(type, fn) {
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type).add(fn);
        },
        removeEventListener(type, fn) {
          if (listeners.has(type)) listeners.get(type).delete(fn);
        },
        fire(type, ev) {
          const set = listeners.get(type);
          if (set) [...set].forEach((fn) => fn(Object.assign({ type, target: this, preventDefault() {} }, ev || {})));
        },
        activeListeners() {
          let n = 0;
          listeners.forEach((s) => { n += s.size; });
          return n;
        },
      };
    }
    const document = {
      createElement: (tag) => makeEl(tag),
      addEventListener(type, fn) {
        if (!docListeners.has(type)) docListeners.set(type, new Set());
        docListeners.get(type).add(fn);
      },
      removeEventListener(type, fn) {
        if (docListeners.has(type)) docListeners.get(type).delete(fn);
      },
      dispatch(type) {
        const set = docListeners.get(type);
        if (set) [...set].forEach((fn) => fn({ type }));
      },
      documentElement: makeEl("html"),
      activeDocListeners() {
        let n = 0;
        docListeners.forEach((s) => { n += s.size; });
        return n;
      },
    };
    return { document, makeEl };
  }

  function walk(node, out = []) {
    (node.children || []).forEach((child) => { out.push(child); walk(child, out); });
    return out;
  }

  function find(root, attr, value) {
    return walk(root).find((n) => n.getAttribute && n.getAttribute(attr) === value) || null;
  }

  function findAll(root, attr, value) {
    return walk(root).filter((n) => n.getAttribute && n.getAttribute(attr) === value);
  }

  function byText(root, text) {
    return walk(root).find((n) => typeof n.textContent === "string" && n.textContent.indexOf(text) !== -1) || null;
  }

  function byClass(root, cls) {
    return walk(root).find((n) => n.className && n.className.split(/\s+/).indexOf(cls) !== -1) || null;
  }

  function moduleState(host) {
    const root = host.children[0];
    return root ? root.getAttribute("data-acp-module-state") : null;
  }

  // ------------------------------------------------------------ fake context --
  function makeContext(projectId) {
    const subs = [];
    let snapshot = {
      projectId: projectId || null,
      researchRef: null,
      taskRef: null,
      structureRef: null,
      revision: 0,
    };
    return {
      getSnapshot: () => snapshot,
      subscribe(fn) {
        subs.push(fn);
        return () => {
          const idx = subs.indexOf(fn);
          if (idx >= 0) subs.splice(idx, 1);
        };
      },
      setResearch(ref) {
        snapshot = Object.assign({}, snapshot, { researchRef: ref, revision: snapshot.revision + 1 });
        subs.slice().forEach((fn) => fn(snapshot));
      },
      count: () => subs.length,
    };
  }

  // ----------------------------------------------------------- fake transport --
  function makeFetch(handler) {
    const calls = [];
    return {
      calls,
      request(pathArg, opts) {
        const options = opts || {};
        calls.push({ path: pathArg, opts: options });
        return Promise.resolve().then(() => handler(pathArg, options, calls));
      },
    };
  }

  function posts(calls) {
    return calls.filter((call) => String(call.opts.method || "").toUpperCase() === "POST");
  }

  function gets(calls) {
    return calls.filter((call) => String(call.opts.method || "GET").toUpperCase() === "GET");
  }

  function listCalls(calls) {
    return calls.filter((call) => call.path.indexOf("/research/approvals?status=pending") !== -1);
  }

  function httpError(status, detail) {
    const error = new Error((detail && detail.message) || "HTTP " + status);
    error.status = status;
    error.body = { detail: detail || { code: "http_error" } };
    return error;
  }

  function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  // ---------------------------------------------------------------- fixtures --
  function hex64(ch) {
    return "sha256:" + ch.repeat(64);
  }

  const PLAN_DIGEST = hex64("2");
  const SCIENCE_DIGEST = hex64("3");

  function approvalRecord(overrides) {
    return Object.assign({
      schema_version: 1,
      approval_ref: "ap_acp2_0001",
      proposal_ref: "prop-acp2-0001",
      proposal_digest: hex64("1"),
      plan_digest: PLAN_DIGEST,
      science_digest: SCIENCE_DIGEST,
      science_projection: {
        recipe_digest: hex64("4"),
        science: {
          capability_steps: [
            { step_index: 0, capability: "geometry_optimization" },
            { step_index: 1, capability: "single_point" },
          ],
          initial_inputs: [{ input_id: "in-1", format: "xyz" }],
          method: "r2SCAN-3c",
          mapping: "mapping-endo-v1",
          charge: 0,
          multiplicity: 1,
          allowed_substitutions: [],
        },
        budget: {
          model_requests: 12,
          input_tokens: 60000,
          output_tokens: 12000,
          task_refs: 1,
          recipe_steps: 2,
          qc_invocations: 1,
          wall_seconds: 1800,
          queue_seconds: 300,
        },
      },
      binding_status: "full",
      plan_definition: {
        workflow: "singlepoint",
        input: { source_type: "xyz_text" },
        method: { schema_id: "dft_singlepoint", functional: "r2SCAN-3c" },
        resources: { nproc: 4, memory: "2000MB" },
      },
      project_id: "proj-acp2",
      requester_ref: "principal-requester",
      approver_ref: null,
      budget: { model_requests: 24, wall_seconds: 3600, unit_basis: "per_scenario" },
      budget_digest: hex64("5"),
      allowed_change_range: { mode: "none" },
      status: "pending",
      state: "pending",
      expires_at: "2099-01-01T00:00:00+00:00",
      revision: 3,
      created_at: "2026-10-30T00:00:00+00:00",
      requested_at: "2026-10-30T00:00:00+00:00",
      updated_at: "2026-10-30T00:00:00+00:00",
      decided_at: null,
      reason: null,
      task_ref: null,
    }, overrides || {});
  }

  function approvedRecord(overrides) {
    return approvalRecord(Object.assign({
      status: "approved",
      state: "approved",
      revision: 4,
      approver_ref: "principal-approver",
      decided_at: "2026-10-30T01:00:00+00:00",
    }, overrides || {}));
  }

  // ------------------------------------------------------------- mount helper --
  function load() {
    const dom = makeDom();
    const sandbox = makeSandbox({ document: dom.document });
    loadScript(MODULE_PATH, sandbox);
    return { sandbox, dom };
  }

  async function mountCard(sandbox, dom, options) {
    options = options || {};
    const host = dom.makeEl("div");
    const deps = Object.assign({ doc: dom.document }, options.deps || {});
    const instance = sandbox.ACPNextModules["approval-card"].factory(deps);
    await instance.mount(host, { context: options.context || null, panel: { bodyEl: host } });
    return { instance, host };
  }

  // --------------------------------------------------------------------- tests --

  await test("approval-card: registers the ACPNextModules descriptor and lifecycle", async () => {
    const { sandbox } = load();
    const entry = sandbox.ACPNextModules["approval-card"];
    assert(!!entry, "registered module entry");
    assertEq(entry.descriptor.id, "approval-card", "descriptor id");
    assertEq(entry.descriptor.interfaceVersion, 1, "interface version");
    assert(/^\d+\.\d+\.\d+$/.test(entry.descriptor.version), "semver-ish version");
    assertEq(entry.descriptor.binds, ["researchRef"], "binds researchRef");
    assertEq(typeof entry.factory, "function", "factory function");
    const instance = entry.factory({ doc: makeDom().document });
    ["mount", "updateContext", "suspend", "resume", "dispose"].forEach((name) => {
      assertEq(typeof instance[name], "function", name + " lifecycle");
    });
  });

  await test("approval-card: empty state comes from an empty pending list", async () => {
    const { sandbox, dom } = load();
    const fetch = makeFetch(() => ({ approvals: [] }));
    const context = makeContext("proj-acp2");
    const { instance, host } = await mountCard(sandbox, dom, { context, deps: { fetch: fetch.request } });
    await flush();
    assertEq(moduleState(host), "empty", "empty state");
    assert(byText(host, "暂无待审批") !== null, "empty copy");
    const card = byClass(host, "acp-ac-card");
    assert(!!card && card.hidden === true, "card stays hidden");
    assertEq(fetch.calls.length, 1, "one pending query");
    assert(fetch.calls[0].path.indexOf(APPROVALS + "?status=pending") === 0, "pending query path");
    assert(fetch.calls[0].path.indexOf("project_id=proj-acp2") !== -1, "project scope in query");
    instance.dispose();
  });

  await test("approval-card: ready state renders server budget, plans, digests, status/revision, requester/expiry", async () => {
    const { sandbox, dom } = load();
    const record = approvalRecord();
    const fetch = makeFetch(() => ({ approvals: [record] }));
    const context = makeContext("proj-acp2");
    const { instance, host } = await mountCard(sandbox, dom, { context, deps: { fetch: fetch.request } });
    await flush();

    assertEq(moduleState(host), "ready", "ready state");
    assert(find(host, "data-field", "plan_digest").textContent === PLAN_DIGEST, "server plan_digest displayed");
    assert(find(host, "data-field", "science_digest").textContent === SCIENCE_DIGEST, "server science_digest displayed");
    assertEq(find(host, "data-field", "budget.model_requests").textContent, "24", "server budget value");
    assertEq(find(host, "data-field", "budget.wall_seconds").textContent, "3600", "server budget seconds");
    assertEq(find(host, "data-field", "budget.unit_basis").textContent, "per_scenario", "server budget basis");
    assertEq(find(host, "data-field", "plan_definition.workflow").textContent, "singlepoint", "execution plan workflow");
    assert(find(host, "data-field", "plan_definition.method").textContent.indexOf("r2SCAN-3c") !== -1, "execution plan method");
    assertEq(find(host, "data-field", "science.method").textContent, "r2SCAN-3c", "science method");
    assertEq(find(host, "data-field", "science.capability_steps").getAttribute("data-value"), "2", "science step count");
    assertEq(find(host, "data-field", "science_budget.model_requests").textContent, "12", "science plan budget");
    assertEq(find(host, "data-field", "revision").textContent, "3", "revision");
    assertEq(find(host, "data-field", "status").getAttribute("data-value"), "pending", "raw status");
    assert(byText(host, "待批准") !== null, "localized status");
    assertEq(find(host, "data-field", "requester_ref").textContent, "principal-requester", "requester");
    assertEq(find(host, "data-field", "expires_at").getAttribute("data-value"), record.expires_at, "expiry server value");
    assertEq(byClass(host, "acp-ac-badge").getAttribute("data-status"), "pending", "status badge");
    assert(!!find(host, "data-acp-role", "reason"), "optional reason input present");
    instance.dispose();
  });

  await test("approval-card: loading state while the pending list is in flight", async () => {
    const { sandbox, dom } = load();
    const pending = deferred();
    const fetch = makeFetch(() => pending.promise);
    const context = makeContext("proj-acp2");
    const { instance, host } = await mountCard(sandbox, dom, { context, deps: { fetch: fetch.request } });
    await flush(3);
    assertEq(moduleState(host), "loading", "loading state");
    assert(byText(host, "正在读取审批记录") !== null, "loading copy");
    pending.resolve({ approvals: [] });
    await flush();
    instance.dispose();
  });

  await test("approval-card: a failed read is an error state and retry reloads", async () => {
    const { sandbox, dom } = load();
    let attempts = 0;
    const fetch = makeFetch(() => {
      attempts += 1;
      if (attempts === 1) throw new TypeError("Failed to fetch");
      return { approvals: [approvalRecord()] };
    });
    const context = makeContext("proj-acp2");
    const { instance, host } = await mountCard(sandbox, dom, { context, deps: { fetch: fetch.request } });
    await flush();
    assertEq(moduleState(host), "error", "error state");
    assert(byText(host, "加载失败") !== null, "error copy");
    assert(byText(host, "Failed to fetch") !== null, "error detail surfaced");
    const retry = find(host, "data-action", "retry");
    assert(!!retry && retry.hidden === false, "retry visible");
    retry.fire("click");
    await flush();
    assertEq(moduleState(host), "ready", "retry recovers");
    assertEq(fetch.calls.length, 2, "retry issued exactly one more read");
    instance.dispose();
  });

  await test("approval-card: a missing transport is explicit and makes no network call", async () => {
    const { sandbox, dom } = load();
    const context = makeContext("proj-acp2");
    const { instance, host } = await mountCard(sandbox, dom, { context });
    await flush();
    assertEq(moduleState(host), "error", "error state without transport");
    assert(byText(host, "未配置请求通道") !== null, "transport copy");
    const approve = find(host, "data-action", "approve");
    assert(!!approve && approve.disabled === true, "actions disabled without transport");
    instance.dispose();
  });

  await test("approval-card: deps.client.transport.request is used when deps.fetch is absent", async () => {
    const { sandbox, dom } = load();
    const fetch = makeFetch(() => ({ approvals: [] }));
    const context = makeContext("proj-acp2");
    const { instance, host } = await mountCard(sandbox, dom, {
      context,
      deps: { client: { transport: { request: fetch.request } } },
    });
    await flush();
    assertEq(moduleState(host), "empty", "client transport drives the panel");
    assertEq(fetch.calls.length, 1, "client transport called once");
    instance.dispose();
  });

  await test("approval-card: approve posts only expected_revision + reason, never an approver identity", async () => {
    const { sandbox, dom } = load();
    let listRound = 0;
    const fetch = makeFetch((pathArg, opts) => {
      if (String(opts.method || "").toUpperCase() === "POST") {
        return approvedRecord({ revision: 4 });
      }
      listRound += 1;
      return { approvals: listRound === 1 ? [approvalRecord()] : [] };
    });
    const context = makeContext("proj-acp2");
    const { instance, host } = await mountCard(sandbox, dom, { context, deps: { fetch: fetch.request } });
    await flush();
    const reason = find(host, "data-acp-role", "reason");
    reason.value = "计划与预算一致，可以批准";
    find(host, "data-action", "approve").fire("click");
    await flush();

    const sent = posts(fetch.calls);
    assertEq(sent.length, 1, "exactly one decision POST");
    assertEq(sent[0].path, APPROVALS + "/ap_acp2_0001/approve", "approve path");
    const body = JSON.parse(sent[0].opts.body);
    assertEq(body, { expected_revision: 3, reason: "计划与预算一致，可以批准" }, "decision body");
    assertEq(Object.keys(body).sort(), ["expected_revision", "reason"], "only revision + reason keys");
    Object.keys(body).forEach((key) => {
      assert(!/approver|principal|actor|user|role|tenant|identity|author/i.test(key), "no identity field: " + key);
    });
    assert(sent[0].opts.body.indexOf("approver_ref") === -1, "no approver_ref in raw body");
    assertEq(moduleState(host), "ready", "still ready after the decision");
    assertEq(byClass(host, "acp-ac-badge").getAttribute("data-status"), "approved", "server status adopted");
    assertEq(find(host, "data-field", "revision").textContent, "4", "server revision adopted");
    assert(byText(host, "已批准") !== null, "decision notice");
    assert(find(host, "data-action", "approve").disabled === true, "approve disabled once approved");
    assert(find(host, "data-action", "revoke").disabled === false, "revoke enabled once approved");
    instance.dispose();
  });

  await test("approval-card: reject omits an empty reason", async () => {
    const { sandbox, dom } = load();
    const fetch = makeFetch((pathArg, opts) => {
      if (String(opts.method || "").toUpperCase() === "POST") {
        return approvalRecord({
          status: "rejected",
          state: "rejected",
          revision: 4,
          decided_at: "2026-10-30T01:00:00+00:00",
          approver_ref: "principal-approver",
        });
      }
      return { approvals: [approvalRecord()] };
    });
    const context = makeContext("proj-acp2");
    const { instance, host } = await mountCard(sandbox, dom, { context, deps: { fetch: fetch.request } });
    await flush();
    find(host, "data-action", "reject").fire("click");
    await flush();

    const sent = posts(fetch.calls);
    assertEq(sent.length, 1, "one reject POST");
    assertEq(sent[0].path, APPROVALS + "/ap_acp2_0001/reject", "reject path");
    const body = JSON.parse(sent[0].opts.body);
    assertEq(body, { expected_revision: 3 }, "blank reason is omitted");
    assertEq(Object.keys(body), ["expected_revision"], "no reason key");
    assertEq(byClass(host, "acp-ac-badge").getAttribute("data-status"), "rejected", "rejected status");
    assert(find(host, "data-action", "approve").disabled === true, "approve disabled after reject");
    assert(find(host, "data-action", "revoke").disabled === true, "revoke disabled after reject");
    instance.dispose();
  });

  await test("approval-card: revoke is offered for an approved record and posts expected_revision", async () => {
    const { sandbox, dom } = load();
    const record = approvedRecord({ revision: 2 });
    const fetch = makeFetch((pathArg, opts) => {
      if (String(opts.method || "").toUpperCase() === "POST") {
        return approvedRecord({
          status: "revoked",
          state: "revoked",
          revision: 3,
          reason: "计划被新版本取代",
        });
      }
      return record;
    });
    const { instance, host } = await mountCard(sandbox, dom, {
      deps: { approvalRef: "ap_acp2_0001", fetch: fetch.request },
    });
    await flush();
    assertEq(moduleState(host), "ready", "focus read ready");
    assert(find(host, "data-action", "approve").disabled === true, "approve disabled for approved");
    assert(find(host, "data-action", "revoke").disabled === false, "revoke enabled for approved");
    find(host, "data-acp-role", "reason").value = "计划被新版本取代";
    find(host, "data-action", "revoke").fire("click");
    await flush();

    const sent = posts(fetch.calls);
    assertEq(sent.length, 1, "one revoke POST");
    assertEq(sent[0].path, APPROVALS + "/ap_acp2_0001/revoke", "revoke path");
    assertEq(JSON.parse(sent[0].opts.body), { expected_revision: 2, reason: "计划被新版本取代" }, "revoke body");
    assertEq(byClass(host, "acp-ac-badge").getAttribute("data-status"), "revoked", "revoked status");
    assert(byText(host, "已撤销") !== null, "revoke notice");
    instance.dispose();
  });

  await test("approval-card: every decision is disabled while one is in flight", async () => {
    const { sandbox, dom } = load();
    const pendingPost = deferred();
    const fetch = makeFetch((pathArg, opts) => {
      if (String(opts.method || "").toUpperCase() === "POST") return pendingPost.promise;
      return { approvals: [approvalRecord()] };
    });
    const context = makeContext("proj-acp2");
    const { instance, host } = await mountCard(sandbox, dom, { context, deps: { fetch: fetch.request } });
    await flush();
    find(host, "data-action", "approve").fire("click");
    await flush(3);

    ["approve", "reject", "revoke", "refresh"].forEach((action) => {
      assert(find(host, "data-action", action).disabled === true, action + " disabled while busy");
    });
    assertEq(byClass(host, "acp-ac-actions").getAttribute("data-busy"), "true", "busy marker");
    assertEq(find(host, "data-acp-role", "reason").disabled, true, "reason input locked while busy");

    pendingPost.resolve(approvedRecord({ revision: 4 }));
    await flush();
    assertEq(byClass(host, "acp-ac-actions").getAttribute("data-busy"), "false", "busy released");
    assert(find(host, "data-action", "revoke").disabled === false, "revoke re-enabled");
    assert(find(host, "data-action", "approve").disabled === true, "approve disabled for approved");
    instance.dispose();
  });

  await test("approval-card: a 409 refreshes the revision and the pending list without replaying", async () => {
    const { sandbox, dom } = load();
    let listRound = 0;
    const fetch = makeFetch((pathArg, opts) => {
      const method = String(opts.method || "GET").toUpperCase();
      if (method === "POST") {
        throw httpError(409, {
          code: "revision_conflict",
          message: "expected revision 3, current 5",
          params: { rule: "revision_conflict" },
          retryable: true,
        });
      }
      if (pathArg.indexOf("?") === -1) {
        return approvedRecord({ revision: 5, approver_ref: "principal-other" });
      }
      listRound += 1;
      return { approvals: listRound === 1 ? [approvalRecord()] : [] };
    });
    const context = makeContext("proj-acp2");
    const { instance, host } = await mountCard(sandbox, dom, { context, deps: { fetch: fetch.request } });
    await flush();
    find(host, "data-action", "approve").fire("click");
    await flush();

    assertEq(posts(fetch.calls).length, 1, "the decision is never replayed");
    assertEq(JSON.parse(posts(fetch.calls)[0].opts.body).expected_revision, 3, "POST carried the observed revision");
    assert(gets(fetch.calls).some((call) => call.path === APPROVALS + "/ap_acp2_0001"), "record revision refreshed");
    assert(listCalls(fetch.calls).length >= 2, "pending list refreshed");
    assertEq(moduleState(host), "ready", "refreshed record is ready");
    assertEq(byClass(host, "acp-ac-badge").getAttribute("data-status"), "approved", "server status adopted");
    assertEq(find(host, "data-field", "revision").textContent, "5", "server revision adopted");
    const notice = byClass(host, "acp-ac-notice");
    assert(!!notice && notice.hidden === false, "conflict notice visible");
    assertEq(notice.getAttribute("data-kind"), "conflict", "conflict notice kind");
    assert(notice.textContent.indexOf("已刷新") !== -1, "conflict copy");
    assert(notice.textContent.indexOf("5") !== -1, "conflict names the refreshed revision");
    assert(find(host, "data-action", "approve").disabled === true, "approve disabled after refresh");
    assert(find(host, "data-action", "revoke").disabled === false, "revoke enabled after refresh");
    instance.dispose();
  });

  await test("approval-card: an access refusal is surfaced and the server record is kept", async () => {
    const { sandbox, dom } = load();
    const fetch = makeFetch((pathArg, opts) => {
      const method = String(opts.method || "GET").toUpperCase();
      if (method === "POST") {
        throw httpError(403, {
          code: "access_denied",
          message: "approval permission is required",
          params: { reason_code: "requester_approver_separation_required" },
          retryable: false,
        });
      }
      if (pathArg.indexOf("?") === -1) return approvalRecord();
      return { approvals: [approvalRecord()] };
    });
    const context = makeContext("proj-acp2");
    const { instance, host } = await mountCard(sandbox, dom, { context, deps: { fetch: fetch.request } });
    await flush();
    find(host, "data-action", "approve").fire("click");
    await flush();

    assertEq(posts(fetch.calls).length, 1, "refused decision posted once");
    const notice = byClass(host, "acp-ac-notice");
    assert(!!notice && notice.hidden === false, "access notice visible");
    assertEq(notice.getAttribute("data-kind"), "error", "access notice kind");
    assert(notice.textContent.indexOf("无权") !== -1, "access copy");
    assert(gets(fetch.calls).some((call) => call.path === APPROVALS + "/ap_acp2_0001"), "record re-read after refusal");
    assertEq(moduleState(host), "ready", "record still rendered");
    assertEq(byClass(host, "acp-ac-badge").getAttribute("data-status"), "pending", "server status unchanged");
    assertEq(find(host, "data-field", "revision").textContent, "3", "server revision unchanged");
    assert(find(host, "data-action", "approve").disabled === false, "approve offered again for manual retry");
    instance.dispose();
  });

  await test("approval-card: a selection switch discards the previous read", async () => {
    const { sandbox, dom } = load();
    const first = deferred();
    const fetch = makeFetch((pathArg) => {
      if (pathArg.indexOf("proposal_ref=P1") !== -1) return first.promise;
      if (pathArg.indexOf("proposal_ref=P2") !== -1) {
        return { approvals: [approvalRecord({ approval_ref: "ap_p2", proposal_ref: "prop-p2" })] };
      }
      return { approvals: [] };
    });
    const context = makeContext("proj-acp2");
    context.setResearch({ proposalId: "P1" });
    const { instance, host } = await mountCard(sandbox, dom, { context, deps: { fetch: fetch.request } });
    await flush(3);
    assertEq(moduleState(host), "loading", "first selection loading");

    context.setResearch({ proposalId: "P2" });
    await flush();
    assertEq(moduleState(host), "ready", "second selection ready");
    assertEq(find(host, "data-field", "proposal_ref").textContent, "prop-p2", "second proposal rendered");

    first.resolve({ approvals: [approvalRecord({ approval_ref: "ap_p1", proposal_ref: "prop-p1" })] });
    await flush();
    assertEq(find(host, "data-field", "proposal_ref").textContent, "prop-p2", "late first response ignored");
    assert(byText(host, "prop-p1") === null, "first proposal never painted");
    instance.dispose();
  });

  await test("approval-card: dispose clears the host, unsubscribes and drops a late response", async () => {
    const { sandbox, dom } = load();
    const pending = deferred();
    const fetch = makeFetch(() => pending.promise);
    const context = makeContext("proj-acp2");
    const { instance, host } = await mountCard(sandbox, dom, { context, deps: { fetch: fetch.request } });
    await flush(3);
    instance.dispose();
    assertEq(host.children.length, 0, "host cleared");
    assertEq(context.count(), 0, "context unsubscribed");
    assertEq(dom.document.activeDocListeners(), 0, "language listener removed");
    pending.resolve({ approvals: [approvalRecord()] });
    await flush();
    assertEq(host.children.length, 0, "late response did not remount");
  });

  await test("approval-card: source uses no direct fetch, no execution writes and computes no digest", async () => {
    const source = fs.readFileSync(MODULE_ABS, "utf8");
    assert(!/(^|[^.\w$])fetch\s*\(/.test(source), "no direct global fetch call");
    assert(source.indexOf("XMLHttpRequest") === -1, "no XMLHttpRequest");
    assert(source.indexOf("/jobs") === -1, "no /jobs endpoint");
    assert(source.indexOf("/submit") === -1, "no /submit endpoint");
    assert(source.indexOf("jobCommands") === -1, "no job command surface");
    assert(source.indexOf("crypto") === -1, "no crypto/digest computation");
    assert(source.indexOf("sha256:") === -1, "no locally built digest value");
    assert(source.indexOf("approver_principal") === -1, "approver principal is never referenced");
    assert(source.indexOf("expected_revision") !== -1, "decision body keeps expected_revision");
    assert(source.indexOf("/api/v2/research/approvals") !== -1, "approval endpoint is the only API base");
    assert(source.indexOf("ACPNextModules") !== -1, "classic registration only");
  });
};
