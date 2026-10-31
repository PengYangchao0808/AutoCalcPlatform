/* ACP 3.0 module test — research-record (A-54 / A-55 / A-56).
 *
 * Loads the REAL module as the classic script the Shell loader consumes
 * (`window.ACPNextModules["research-record"] = { descriptor, factory,
 * _internals }`) inside the zero-dependency node vm harness with a fake DOM.
 * Every request goes through an injected request seam, so no test can reach the
 * network (the sandbox has no fetch/XMLHttpRequest at all).
 *
 * Pinned contracts:
 *  1. exactly one ACPNextModules key, Shell-valid descriptor, no extra global;
 *  2. four states (empty/loading/ready/error), readable errors + retry;
 *  3. A-54: question/claim + evidence refs build the inspected bodies of
 *     POST /api/v2/research/proposals and POST /api/v2/research/records;
 *     no model field, no invented science/identity, no task submission;
 *  4. A-55: server claims / claim_statuses are rendered verbatim; verification
 *     and explanation live in separate containers; no local adjudication;
 *  5. A-56: GET /api/v2/research/records/{id}/evidence renders
 *     digest/binding/attempt from the server projection only;
 *  6. generation + dispose guards: a late read/submit never paints;
 *  7. source static gates: no bare fetch, no /jobs, no crypto digest;
 *  8. i18n keys used by the DOM resolve through the inline zh dictionary.
 */
"use strict";

const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const MODULE_PATH = "js/modules/research-record/module.js";
  const MODULE_ABS = path.resolve(__dirname, "..", "modules", "research-record", "module.js");
  const CSS_ABS = path.resolve(__dirname, "..", "..", "css", "modules", "research-record.css");
  const README_ABS = path.resolve(__dirname, "..", "modules", "research-record", "README.md");
  const RECORDS = "/api/v2/research/records";
  const PROPOSALS = "/api/v2/research/proposals";

  const DIGEST = (ch) => "sha256:" + String(ch).repeat(64);

  function deferred() {
    let resolve;
    let reject;
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
        value: "",
        hidden: false,
        disabled: false,
        type: "",
        title: "",
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
          if (set) [...set].forEach((fn) => fn(Object.assign({ type, target: node, preventDefault() {} }, event || {})));
        },
        activeListeners() {
          let count = 0;
          listeners.forEach((set) => { count += set.size; });
          return count;
        },
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
      dispatch(type) {
        const set = docListeners.get(type);
        if (set) [...set].forEach((fn) => fn({ type }));
      },
      documentElement: makeEl("html"),
      activeDocListeners() {
        let count = 0;
        docListeners.forEach((set) => { count += set.size; });
        return count;
      },
    };
    return { document, makeEl };
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

  function textOf(node) {
    let text = node.textContent || "";
    (node.children || []).forEach((child) => { text += " " + textOf(child); });
    return text;
  }

  function byRole(root, role) {
    return find(root, "data-role", role);
  }

  function moduleState(host) {
    const root = host.children[0];
    return root ? root.getAttribute("data-acp-module-state") : null;
  }

  function setValue(node, value, eventName) {
    node.value = String(value);
    if (eventName !== null) node.fire(eventName || "input", { target: node });
  }

  // ------------------------------------------------------------ transports --
  function makeRequest(handler) {
    const calls = [];
    return {
      calls,
      request(pathArg, opts) {
        const options = opts || {};
        calls.push({ path: pathArg, opts: options });
        try {
          return Promise.resolve(handler(pathArg, options, calls.length));
        } catch (error) {
          return Promise.reject(error);
        }
      },
    };
  }

  function httpError(status, detail) {
    const error = new Error((detail && detail.message) || "HTTP " + status);
    error.status = status;
    error.body = { detail: detail || { code: "http_error" } };
    return error;
  }

  function okProposal() {
    return { schema: "acp2.experience.proposal.v1", resource_id: "prop-rr-1", revision: 1, digest: DIGEST("a"), document: {} };
  }

  function okRecord(researchId) {
    return {
      schema: "acp2.experience.research-record.v1",
      resource_id: "res-" + (researchId || "rr-1"),
      revision: 1,
      digest: DIGEST("b"),
      document: { research_id: researchId || "res-rr-1" },
      decision: "accept",
      projection_digest: DIGEST("c"),
    };
  }

  // ------------------------------------------------------------- fixtures --
  const PROPOSAL_BINDING = {
    projectId: "proj-acp2",
    sessionSeed: "seed-rr-1",
    proposalId: "prop-rr-1",
    recipeDigest: DIGEST("1"),
    questionRef: "question-da-1",
    questionRevision: 2,
    round: 1,
    plan: {
      workflow: "singlepoint",
      input: { source_type: "xyz_text", source: "CCO" },
      method: { schema_id: "dft_singlepoint", levels: "r2SCAN-3c" },
      resources: { ncores: 4, memory: "2000MB" },
    },
    science: {
      capability_steps: [{ step_index: 0, capability: "single_point", version: "1" }],
      initial_inputs: [{ input_id: "in-1", sha256: DIGEST("2"), format: "xyz" }],
      method: "r2SCAN-3c",
      mapping: "manual-v1",
      charge: 0,
      multiplicity: 1,
      allowed_substitutions: [],
    },
    budget: {
      model_requests: 0,
      input_tokens: 0,
      output_tokens: 0,
      task_refs: 1,
      recipe_steps: 1,
      qc_invocations: 1,
      wall_seconds: 600,
      queue_seconds: 0,
    },
    requestBudget: {
      model_requests: 0,
      input_tokens: 0,
      output_tokens: 0,
      task_refs: 1,
      recipe_steps: 1,
      qc_invocations: 1,
      wall_seconds: 600,
      queue_seconds: 0,
    },
    idempotencyKey: "idem-rr-1",
  };

  const RECORD_BINDING = {
    projectId: "proj-acp2",
    sessionSeed: "seed-rr-1",
    researchId: "res-rr-1",
    proposalId: "prop-rr-1",
    approvalRef: "ap-rr-1",
    planDefinitionDigest: DIGEST("3"),
    round: 1,
    taskRef: {
      project_id: "proj-acp2",
      job_id: "20261031_001_singlepoint",
      attempt: 1,
      release_id: "release-2026.10.31",
    },
    editorRef: "principal-requester",
  };

  function evidenceView(overrides) {
    const view = {
      schema: "acp2.runtime.evidence-projection.v1",
      contract_version: "1.0.0",
      research: {
        principal_id: "principal-requester",
        project_id: "proj-acp2",
        research_id: "res-rr-1",
        session_id: "sess-rr-1",
        round: 1,
      },
      task_ref: {
        project_id: "proj-acp2",
        task_id: "20261031_001_singlepoint",
        job_id: "20261031_001_singlepoint",
        attempt: 1,
        release: { release_id: "release-2026.10.31", release_digest: DIGEST("4") },
      },
      report: {
        report_id: "report-rr-1",
        digest: DIGEST("5"),
        schema_version: 1,
        subject_id: "subject-rr-1",
      },
      execution_terminal_state: "completed",
      qualification: "complete",
      evidence_status: "complete",
      claims: [
        {
          claim_id: "claim.activation",
          status: "supported",
          validator_id: "check.activation",
          validator_version: "1",
          source_ref_count: 2,
          blocking_missing_checks: [],
        },
        {
          claim_id: "claim.connectivity",
          status: "inconclusive",
          validator_id: "",
          validator_version: "",
          source_ref_count: 1,
          blocking_missing_checks: ["irc_connectivity"],
        },
      ],
      overall_claim_status: "inconclusive",
      blocking_missing_checks: ["claim.connectivity/irc_connectivity"],
      evidence_refs: [
        { evidence_id: "art-abc", kind: "result_manifest", sha256: DIGEST("6"), relative_path: "RESULT/result_manifest.json" },
      ],
      limitations: ["single imaginary frequency is not IRC connectivity"],
      provenance_digest: DIGEST("7"),
      normal_reuse: false,
      projection_digest: DIGEST("8"),
    };
    return Object.assign(view, overrides || {});
  }

  // -------------------------------------------------------------- harness --
  function makeContext(options) {
    options = options || {};
    const listeners = new Set();
    let snapshot = {
      projectId: options.projectId || null,
      permission: "read",
      researchRef: options.researchId ? { recordId: options.researchId } : null,
      taskRef: options.taskRef || null,
      revision: 1,
    };
    return {
      getSnapshot() { return snapshot; },
      subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
      setFocus(patch) {
        snapshot = Object.assign({}, snapshot, patch, { revision: snapshot.revision + 1 });
        listeners.forEach((fn) => fn(snapshot));
      },
      listenerCount() { return listeners.size; },
    };
  }

  function loadModule(sandbox) {
    loadScript(MODULE_PATH, sandbox);
    return sandbox.ACPNextModules["research-record"];
  }

  async function setup(options) {
    options = options || {};
    const dom = makeDom();
    const sandbox = makeSandbox({});
    const entry = loadModule(sandbox);
    const request = options.request || makeRequest(options.responder || (() => evidenceView()));    const host = dom.makeEl("div");
    const deps = { doc: dom.document };
    if (options.noTransport) {
      deps.client = null;
    } else if (options.request === null) {
      deps.client = options.client || null;
    } else {
      deps.request = request.request;
      if (options.client) deps.client = options.client;
    }
    if (options.binding !== undefined) deps.binding = options.binding;
    if (options.now) deps.now = options.now;
    if (options.context !== undefined) deps.context = options.context;
    const instance = entry.factory(deps);
    const context = options.context === undefined ? null : options.context;
    await instance.mount(host, { context: context });
    await flush();
    return { dom, sandbox, entry, host, instance, request, calls: request.calls };
  }

  // --------------------------------------------------------------- tests --

  await test("research-record: one ACPNextModules key, Shell-valid descriptor, no extra global", async () => {
    const dom = makeDom();
    const sandbox = makeSandbox({});
    const before = new Set(Object.keys(sandbox));
    const entry = loadModule(sandbox);
    const added = Object.keys(sandbox).filter((key) => !before.has(key));
    assertEq(added, ["ACPNextModules"], "only ACPNextModules is added");
    assertEq(Object.keys(sandbox.ACPNextModules), ["research-record"], "one module key");
    assert(entry && entry.descriptor, "descriptor present");
    assertEq(entry.descriptor.id, "research-record");
    assertEq(entry.descriptor.interfaceVersion, 1);
    assert(/^\d+\.\d+\.\d+$/.test(entry.descriptor.version), "semver version");
    assertEq(entry.descriptor.hosts, ["acp-web"]);
    assertEq(entry.descriptor.supportedInstancing, "single");
    assert(entry.descriptor.provides.indexOf("research.evidence") !== -1, "provides evidence");
    assert(typeof entry.factory === "function", "factory present");
    assert(entry._internals && typeof entry._internals.buildRecordBody === "function", "internals exposed");
    assert(dom, "dom built");
  });

  await test("research-record: the real Shell registry accepts the descriptor", async () => {
    const sandbox = makeSandbox({});
    loadScript("js/shell/module-registry.js", sandbox);
    loadModule(sandbox);
    const result = sandbox.ACPShell.registry.collectFrom(sandbox.ACPNextModules);
    assertEq(result.registered, ["research-record"], "registered by the real registry");
    assertEq(result.rejected, [], "no descriptor rejection");
  });

  await test("research-record: source static gates (no fetch/jobs/crypto/local digest)", async () => {
    const source = fs.readFileSync(MODULE_ABS, "utf8");
    assert(source.indexOf("fetch(") === -1, "no fetch() call");
    assert(source.indexOf("XMLHttpRequest") === -1, "no XHR");
    assert(source.indexOf("crypto") === -1, "no crypto digest");
    assert(source.indexOf("createHash") === -1, "no node hash");
    assert(source.indexOf("/jobs\"") === -1 && source.indexOf("'/jobs") === -1, "no task route");
    assert(source.indexOf("/submit\"") === -1 && source.indexOf("'/submit") === -1, "no submit route");
    assert(source.indexOf("overall_claim_status === \"supported\"") === -1, "no local supported verdict");
    assert(source.indexOf("evidence_status === \"complete\"") === -1, "no local evidence/claim merge");
    assert(source.indexOf("data-acp-adjudication") !== -1, "server-only adjudication marker");
    assert(source.indexOf("ACPNextModules") !== -1, "classic registration");
  });

  await test("research-record: buildProposalBody emits the inspected /research/proposals body", async () => {
    const { entry } = await setup({ binding: null });
    const builder = entry._internals.buildProposalBody;
    const form = { question: "  Which path is lower?  ", claim: "  The endo path is lower.  ", target: "proposal" };
    const built = builder(PROPOSAL_BINDING, form, { nowIso: "2026-10-31T00:00:00.000Z" });
    assert(built.ok, "body built");
    const body = built.body;
    assertEq(Object.keys(body).sort(), ["budget", "expires_at", "idempotency_key", "plan", "proposal", "project_id", "session_seed"].filter((k) => k in body).sort());
    assertEq(body.project_id, "proj-acp2");
    assertEq(body.session_seed, "seed-rr-1");
    assertEq(body.plan.workflow, "singlepoint");
    assertEq(body.budget.wall_seconds, 600);
    assertEq(body.idempotency_key, "idem-rr-1");
    const proposal = body.proposal;
    assertEq(proposal.proposal_id, "prop-rr-1");
    assertEq(proposal.project_id, "proj-acp2");
    assertEq(proposal.question, "Which path is lower?");
    assertEq(proposal.hypothesis, "The endo path is lower.");
    assertEq(proposal.question_ref, "question-da-1");
    assertEq(proposal.question_revision, 2);
    assertEq(proposal.round, 1);
    assertEq(proposal.recipe_digest, DIGEST("1"));
    assertEq(proposal.decision, "proposed");
    assertEq(proposal.science.charge, 0);
    assertEq(proposal.budget.task_refs, 1);
    assert(!("evidence_refs" in proposal), "proposal carries no evidence refs");
    assert(JSON.stringify(body).indexOf("claim_status") === -1, "no claim status in proposal body");
    assert(!("model" in body) && !("model_id" in body) && !("messages" in body),
      "no model field in proposal body");
  });

  await test("research-record: buildProposalBody refuses missing question/claim/binding/science", async () => {
    const { entry } = await setup({ binding: null });
    const builder = entry._internals.buildProposalBody;
    const base = { question: "Q", claim: "C" };
    const noQuestion = builder(PROPOSAL_BINDING, { question: "", claim: "C" });
    assert(!noQuestion.ok);
    assert(noQuestion.problems.some((p) => p.code === "question_required"));
    const noClaim = builder(PROPOSAL_BINDING, { question: "Q", claim: "" });
    assert(noClaim.problems.some((p) => p.code === "claim_required"));
    const noBinding = builder(null, base);
    assert(noBinding.problems.some((p) => p.code === "no_binding"));
    const broken = JSON.parse(JSON.stringify(PROPOSAL_BINDING));
    delete broken.science.multiplicity;
    delete broken.budget.wall_seconds;
    broken.recipeDigest = "not-a-digest";
    const bad = builder(broken, base);
    assert(bad.problems.some((p) => p.code === "recipe_digest_required"));
    assert(bad.problems.some((p) => p.code === "science_required"));
    assert(bad.problems.some((p) => p.code === "budget_required"));
  });

  await test("research-record: buildRecordBody emits the inspected /research/records body", async () => {
    const { entry } = await setup({ binding: null });
    const builder = entry._internals.buildRecordBody;
    const refs = [
      { evidence_id: "art-abc", kind: "result_manifest", sha256: DIGEST("6"), relative_path: "RESULT/result_manifest.json" },
      { evidence_id: "", kind: "", sha256: "", relative_path: "" },
    ];
    const normalized = entry._internals.normalizeEvidence(evidenceView());
    assert(normalized.ok, "view normalized");
    const built = builder(
      RECORD_BINDING,
      { question: "Is the barrier lower?", claim: "Yes.", target: "record", decision: "accept" },
      refs,
      normalized.view,
      { nowIso: "2026-10-31T00:00:00.000Z" }
    );
    assert(built.ok, "body built");
    const body = built.body;
    assertEq(body.session_seed, "seed-rr-1");
    assertEq(body.project_id, "proj-acp2");
    assertEq(body.research_id, "res-rr-1");
    assertEq(body.proposal_ref, "prop-rr-1");
    assertEq(body.approval_ref, "ap-rr-1");
    assertEq(body.plan_definition_digest, DIGEST("3"));
    assertEq(body.round, 1);
    assertEq(body.decision, "accept");
    assertEq(body.task_refs.length, 1);
    assertEq(Object.keys(body.task_refs[0]).sort(), ["attempt", "job_id", "project_id", "release_id"]);
    assertEq(body.task_refs[0].release_id, "release-2026.10.31");
    assertEq(body.task_refs[0].attempt, 1);
    assertEq(body.evidence_refs.length, 1, "blank evidence row dropped");
    assertEq(body.evidence_refs[0].relative_path, "RESULT/result_manifest.json");
    assertEq(body.evidence_view.projection_digest, DIGEST("8"), "raw server view attached");
    assertEq(body.evidence_view.claims.length, 2, "raw view claims preserved");
    assertEq(body.human_edits.length, 2, "question/claim recorded as human provenance");
    assertEq(body.human_edits[0].field_path, "research.question");
    assertEq(body.human_edits[0].editor_ref, "principal-requester");
    assertEq(body.human_edits[0].edited_at, "2026-10-31T00:00:00.000Z");
    assert(!("question" in body), "record body has no question field");
    assert(!("hypothesis" in body), "record body has no hypothesis field");
    assert(!("claim_statuses" in body), "record body has no top-level claim statuses");
  });

  await test("research-record: buildRecordBody omits a foreign evidence view and refuses missing refs/decision", async () => {
    const { entry } = await setup({ binding: null });
    const builder = entry._internals.buildRecordBody;
    const foreign = entry._internals.normalizeEvidence(evidenceView({
      research: { principal_id: "p", project_id: "proj-other", research_id: "res-rr-1", session_id: "s", round: 1 },
    })).view;
    const built = builder(
      RECORD_BINDING,
      { question: "Q", claim: "", decision: "accept" },
      [{ evidence_id: "art-abc", kind: "result_manifest", sha256: DIGEST("6") }],
      foreign,
      { nowIso: "2026-10-31T00:00:00.000Z" }
    );
    assert(built.ok, "body still builds");
    assert(!("evidence_view" in built.body), "foreign view omitted");
    assertEq(built.viewMatched, false);

    const noDecision = builder(RECORD_BINDING, { question: "Q" },
      [{ evidence_id: "a", kind: "k", sha256: DIGEST("6") }]);
    assert(!noDecision.ok);
    assert(noDecision.problems.some((p) => p.code === "decision_required"));

    const noRefs = builder(RECORD_BINDING, { question: "Q", decision: "defer" }, []);
    assert(!noRefs.ok);
    assert(noRefs.problems.some((p) => p.code === "evidence_required"));

    const partialView = entry._internals.normalizeEvidence(evidenceView());
    const uncited = builder(RECORD_BINDING, { question: "Q", decision: "defer" },
      [{ evidence_id: "other-ref", kind: "result_manifest", sha256: DIGEST("9") }], partialView.view);
    assert(!uncited.ok, "view with uncited refs refused locally");
    assert(uncited.problems.some((p) => p.code === "evidence_view_refs_uncited"),
      "uncited view refs named");

    const noApproval = JSON.parse(JSON.stringify(RECORD_BINDING));
    delete noApproval.approvalRef;
    noApproval.planDefinitionDigest = "bad";
    const badBinding = builder(noApproval, { question: "Q", decision: "defer" },
      [{ evidence_id: "a", kind: "k", sha256: DIGEST("6") }]);
    assert(badBinding.problems.some((p) => p.code === "approval_required"));
    assert(badBinding.problems.some((p) => p.code === "plan_digest_required"));

    const boolBinding = JSON.parse(JSON.stringify(RECORD_BINDING));
    boolBinding.round = true;
    boolBinding.taskRef.attempt = true;
    const boolBad = builder(boolBinding, { question: "Q", decision: "defer" },
      [{ evidence_id: "a", kind: "k", sha256: DIGEST("6") }]);
    assert(boolBad.problems.some((p) => p.code === "round_required"), "boolean round refused");
    assert(boolBad.problems.some((p) => p.code === "task_ref_required"), "boolean attempt refused");
  });

  await test("research-record: normalizeEvidence keeps server values and claim_statuses is a read fallback", async () => {
    const { entry } = await setup({ binding: null });
    const normalized = entry._internals.normalizeEvidence(evidenceView());
    assert(normalized.ok, "view ok");
    assertEq(normalized.view.research.research_id, "res-rr-1");
    assertEq(normalized.view.task_ref.attempt, 1);
    assertEq(normalized.view.report.digest, DIGEST("5"));
    assertEq(normalized.view.evidence_status, "complete");
    assertEq(normalized.view.overall_claim_status, "inconclusive");
    assertEq(normalized.view.claims[0].status, "supported");
    assertEq(normalized.view.claims[1].status, "inconclusive");
    assertEq(normalized.view.limitations.length, 1);
    assertEq(normalized.view.evidence_refs[0].sha256, DIGEST("6"));
    assertEq(normalized.warnings, [], "no warnings for a complete view");

    const mapPayload = evidenceView();
    delete mapPayload.claims;
    mapPayload.claim_statuses = { "claim.one": "supported", "claim.two": "not_evaluated" };
    const mapView = entry._internals.normalizeEvidence(mapPayload);
    assert(mapView.ok, "claim_statuses fallback ok");
    assertEq(mapView.view.claims.map((c) => c.claim_id), ["claim.one", "claim.two"]);
    assertEq(mapView.view.claims.map((c) => c.status), ["supported", "not_evaluated"]);

    const broken = entry._internals.normalizeEvidence({ research: {}, task_ref: {}, report: {} });
    assert(!broken.ok, "incomplete view refused");
    assert(broken.problems.indexOf("claims|claim_statuses") !== -1);

    const wrongSchema = entry._internals.normalizeEvidence(evidenceView({ schema: "acp2.other.v1" }));
    assert(!wrongSchema.ok && wrongSchema.problems.indexOf("schema") !== -1, "foreign schema refused");

    const emptyClaims = evidenceView();
    emptyClaims.claims = [];
    emptyClaims.claim_statuses = { "claim.map": "refuted" };
    const emptyFallback = entry._internals.normalizeEvidence(emptyClaims);
    assertEq(emptyFallback.view.claims[0].claim_id, "claim.map", "empty claims fall back to the map");

    const unknown = entry._internals.normalizeEvidence(evidenceView({
      claims: [{ claim_id: "c", status: "fifth_state", source_ref_count: 0, blocking_missing_checks: [] }],
    }));
    assertEq(unknown.view.claims[0].status, "fifth_state", "unknown status kept verbatim");
    assert(unknown.warnings.some((w) => w.code === "claim_status_unknown"), "unknown status warned");
  });

  await test("research-record: evidencePath encodes the inspected GET route", async () => {
    const { entry } = await setup({ binding: null });
    const pathValue = entry._internals.evidencePath(
      "res rr/1", "proj acp2", "seed & 1", 2
    );
    assertEq(pathValue,
      "/api/v2/research/records/res%20rr%2F1/evidence?project_id=proj%20acp2&session_seed=seed%20%26%201&round=2");
    assertEq(entry._internals.evidencePath("res-1", "proj", "", null),
      "/api/v2/research/records/res-1/evidence?project_id=proj");
    assertEq(entry._internals.evidencePath("res-1", "", "", 0),
      "/api/v2/research/records/res-1/evidence?round=0");
  });

  await test("research-record: empty state with no binding/context and no network call", async () => {
    const env = await setup({ binding: null });
    assertEq(moduleState(env.host), "empty");
    const root = env.host.children[0];
    assertEq(byRole(root, "evidence-body").hidden, true);
    assert(textOf(root).indexOf("尚未读取") !== -1, "empty state visible");
    assertEq(env.calls.length, 0, "no request without a research id");
    assert(byRole(root, "question"), "manual question entry rendered");
    assert(byRole(root, "submit"), "submit rendered");
  });

  await test("research-record: loading → ready renders digest/binding/attempt from the projection", async () => {
    const env = await setup({ binding: PROPOSAL_BINDING });
    const root = env.host.children[0];
    const researchInput = byRole(root, "researchId");
    const seedInput = byRole(root, "sessionSeed");
    const roundInput = byRole(root, "evidenceRound");
    researchInput.value = "res-rr-1";
    seedInput.value = "seed-rr-1";
    roundInput.value = "1";
    byRole(root, "load-evidence").fire("click");
    assertEq(moduleState(env.host), "loading");
    await flush();
    assertEq(moduleState(env.host), "ready");
    assertEq(env.calls.length, 1);
    assertEq(env.calls[0].path,
      "/api/v2/research/records/res-rr-1/evidence?project_id=proj-acp2&session_seed=seed-rr-1&round=1");
    assertEq(env.calls[0].opts.method, "GET");
    const meta = textOf(byRole(root, "evidence-meta"));
    ["res-rr-1", "proj-acp2", "completed", "complete", "inconclusive",
      "report-rr-1", DIGEST("5"), DIGEST("8"), DIGEST("7")].forEach((needle) => {
      assert(meta.indexOf(needle) !== -1, "meta shows " + needle);
    });
    const binding = textOf(byRole(root, "evidence-binding"));
    ["20261031_001_singlepoint", "1", "release-2026.10.31", DIGEST("4")].forEach((needle) => {
      assert(binding.indexOf(needle) !== -1, "binding shows " + needle);
    });
    assertEq(byRole(root, "evidence-body").hidden, false);
  });

  await test("research-record: claims render server statuses verbatim with no combined verdict", async () => {
    const env = await setup({
      binding: RECORD_BINDING,
      responder: () => evidenceView(),
      context: makeContext({ projectId: "proj-acp2", researchId: "res-rr-1" }),
    });
    const root = env.host.children[0];
    await flush();
    assertEq(moduleState(env.host), "ready");
    const claims = byRole(root, "claims");
    const activation = walk(claims).find((n) => n.getAttribute && n.getAttribute("data-claim-id") === "claim.activation");
    const connectivity = walk(claims).find((n) => n.getAttribute && n.getAttribute("data-claim-id") === "claim.connectivity");
    assert(activation, "activation claim row");
    assert(connectivity, "connectivity claim row");
    assertEq(activation.getAttribute("data-claim-status"), "supported");
    assertEq(connectivity.getAttribute("data-claim-status"), "inconclusive");
    assert(textOf(activation).indexOf("check.activation") !== -1, "validator shown");
    assert(textOf(activation).indexOf("2") !== -1, "source ref count shown");
    assert(textOf(connectivity).indexOf("irc_connectivity") !== -1, "blocking check shown");
    assertEq(claims.getAttribute("data-acp-adjudication"), "server-only");
    assertEq(claims.getAttribute("data-acp-claims-source"), "server");
    const page = textOf(root);
    assert(page.indexOf("inconclusive（不确定）") !== -1, "overall status is server text");
    assert(page.indexOf("总体命题状态") !== -1, "overall claim status is a separate row");
    assert(page.indexOf("needs_review") === -1 && page.indexOf("verdict") === -1,
      "no locally computed verdict");
  });

  await test("research-record: verification and explanation are separate containers", async () => {
    const env = await setup({
      binding: RECORD_BINDING,
      responder: () => evidenceView(),
      context: makeContext({ projectId: "proj-acp2", researchId: "res-rr-1" }),
    });
    const root = env.host.children[0];
    const verification = find(root, "data-acp-section", "verification");
    const explanation = find(root, "data-acp-section", "explanation");
    assert(verification && explanation, "both containers");
    assert(textOf(verification).indexOf("claim.activation") !== -1, "claims in verification");
    assert(textOf(verification).indexOf("single imaginary frequency") === -1,
      "limitations are NOT in verification");
    assert(textOf(explanation).indexOf("single imaginary frequency") !== -1,
      "limitation in explanation");
    assert(textOf(explanation).indexOf("claim.connectivity/irc_connectivity") !== -1,
      "blocking check in explanation");
    assert(textOf(explanation).indexOf("模型解释不属于证据") !== -1, "model note in explanation");
  });

  await test("research-record: claim_statuses map payload still renders claim states", async () => {
    const mapPayload = evidenceView();
    delete mapPayload.claims;
    mapPayload.claim_statuses = { "claim.status_only": "refuted" };
    const env = await setup({
      binding: RECORD_BINDING,
      responder: () => mapPayload,
      context: makeContext({ projectId: "proj-acp2", researchId: "res-rr-1" }),
    });
    const root = env.host.children[0];
    const claim = walk(byRole(root, "claims")).find((n) => n.getAttribute && n.getAttribute("data-claim-status"));
    assert(claim, "claim row rendered from claim_statuses");
    assertEq(claim.getAttribute("data-claim-id"), "claim.status_only");
    assertEq(claim.getAttribute("data-claim-status"), "refuted");
    assert(textOf(claim).indexOf("refuted（反驳）") !== -1, "status label copied");
  });

  await test("research-record: unknown claim state is shown raw and never green/supported", async () => {
    const env = await setup({
      binding: RECORD_BINDING,
      responder: () => evidenceView({
        claims: [{ claim_id: "claim.drift", status: "fifth_state", source_ref_count: 0, blocking_missing_checks: [] }],
        overall_claim_status: "fifth_state",
      }),
      context: makeContext({ projectId: "proj-acp2", researchId: "res-rr-1" }),
    });
    const root = env.host.children[0];
    const claim = walk(byRole(root, "claims")).find((n) => n.getAttribute && n.getAttribute("data-claim-id") === "claim.drift");
    assert(claim, "claim row");
    assertEq(claim.getAttribute("data-claim-status"), "fifth_state");
    assert(textOf(claim).indexOf("fifth_state") !== -1, "raw status visible");
    assert(textOf(claim).indexOf("supported（支持）") === -1, "unknown status not upgraded");
    assert(textOf(byRole(root, "evidence-warnings")).indexOf("状态未知") !== -1, "warning visible");
  });

  await test("research-record: four states + readable error + retry", async () => {
    let attempt = 0;
    const env = await setup({
      binding: RECORD_BINDING,
      responder: () => {
        attempt += 1;
        if (attempt === 1) throw httpError(409, {
          code: "resource_not_ready",
          message: "the attempt has no receipt/task_result yet",
          params: { rule: "evidence_not_ready" },
          retryable: true,
        });
        return evidenceView();
      },
      context: makeContext({ projectId: "proj-acp2", researchId: "res-rr-1" }),
    });
    const root = env.host.children[0];
    await flush();
    assertEq(moduleState(env.host), "error");
    const stateText = textOf(byRole(root, "state"));
    assert(stateText.indexOf("evidence_not_ready") !== -1, "rule surfaced");
    assert(stateText.indexOf("resource_not_ready") !== -1, "code surfaced");
    assert(byRole(root, "retry").hidden === false, "retry visible");
    byRole(root, "retry").fire("click");
    assertEq(moduleState(env.host), "loading");
    await flush();
    assertEq(moduleState(env.host), "ready");
    assertEq(env.calls.length, 2);
    assert(byRole(root, "evidence-body").hidden === false);
  });

  await test("research-record: no transport renders an explicit error and performs no request", async () => {
    const dom = makeDom();
    const sandbox = makeSandbox({});
    const entry = loadModule(sandbox);
    const host = dom.makeEl("div");
    const instance = entry.factory({ doc: dom.document });
    await instance.mount(host, { context: makeContext({ projectId: "proj-acp2" }) });
    await flush();
    const root = host.children[0];
    // mount without a research id stays empty; provide one and ask for evidence
    byRole(root, "researchId").value = "res-rr-1";
    byRole(root, "sessionSeed").value = "seed-rr-1";
    byRole(root, "load-evidence").fire("click");
    await flush();
    assertEq(moduleState(host), "error");
    assert(textOf(byRole(root, "state")).indexOf("未注入请求通道") !== -1, "readable no-transport copy");
    instance.dispose();
  });

  await test("research-record: incomplete projection is an explicit error, never a half render", async () => {
    const env = await setup({
      binding: RECORD_BINDING,
      responder: () => ({ research: { research_id: "res-rr-1" } }),
      context: makeContext({ projectId: "proj-acp2", researchId: "res-rr-1" }),
    });
    const root = env.host.children[0];
    await flush();
    assertEq(moduleState(env.host), "error");
    const stateText = textOf(byRole(root, "state"));
    assert(stateText.indexOf("task_ref") !== -1, "missing task_ref named");
    assert(stateText.indexOf("report") !== -1, "missing report named");
    assertEq(byRole(root, "evidence-body").hidden, true, "no half-rendered evidence");
  });

  await test("research-record: generation guard drops a late view after a research switch", async () => {
    const pendingA = deferred();
    const pendingB = deferred();
    const context = makeContext({ projectId: "proj-acp2", researchId: "res-A" });
    const env = await setup({
      binding: PROPOSAL_BINDING,
      request: null,
      client: { transport: { request: (pathArg) => (pathArg.indexOf("/res-A/") !== -1 ? pendingA.promise : pendingB.promise) } },
      context: context,
    });
    const root = env.host.children[0];
    assertEq(moduleState(env.host), "loading", "A in flight");
    context.setFocus({ researchRef: { recordId: "res-B" } });
    await flush();
    assertEq(moduleState(env.host), "loading", "B in flight");
    pendingA.resolve(evidenceView({ research: { principal_id: "p", project_id: "proj-acp2", research_id: "res-A", session_id: "s", round: 1 } }));
    await flush();
    assertEq(moduleState(env.host), "loading", "late A must not paint");
    assertEq(byRole(root, "evidence-body").hidden, true, "no A content");
    pendingB.resolve(evidenceView({ research: { principal_id: "p", project_id: "proj-acp2", research_id: "res-B", session_id: "s", round: 1 } }));
    await flush();
    assertEq(moduleState(env.host), "ready");
    assert(textOf(byRole(root, "evidence-meta")).indexOf("res-B") !== -1, "B painted");
  });

  await test("research-record: dispose clears the host, unsubscribes and drops late results", async () => {
    const pending = deferred();
    const context = makeContext({ projectId: "proj-acp2", researchId: "res-rr-1" });
    const env = await setup({
      binding: PROPOSAL_BINDING,
      request: null,
      client: { transport: { request: () => pending.promise } },
      context: context,
    });
    const listenerCount = context.listenerCount();
    assertEq(listenerCount, 1, "context subscribed");
    env.instance.dispose();
    assertEq(context.listenerCount(), 0, "unsubscribed");
    assertEq(env.host.children.length, 0, "host cleared");
    assertEq(env.dom.document.activeDocListeners(), 0, "document listener removed");
    pending.resolve(evidenceView());
    await flush();
    assertEq(env.host.children.length, 0, "late result never paints");
    env.instance.dispose();
    assertEq(env.host.children.length, 0, "dispose is idempotent");
  });

  await test("research-record: a late submit after a selection switch never paints", async () => {
    const post = deferred();
    const calls = [];
    const context = makeContext({ projectId: "proj-acp2" });
    const env = await setup({
      binding: PROPOSAL_BINDING,
      request: null,
      client: {
        transport: {
          request: (pathArg, opts) => {
            calls.push({ path: pathArg, opts });
            if ((opts.method || "GET") === "POST") return post.promise;
            return new Promise(() => {}); // the new selection's evidence read stays in flight
          },
        },
      },
      context: context,
    });
    const root = env.host.children[0];
    setValue(byRole(root, "question"), "Q");
    setValue(byRole(root, "claim"), "C");
    byRole(root, "submit").fire("click");
    await flush(2);
    assertEq(calls.filter((call) => call.opts.method === "POST").length, 1, "POST sent");
    context.setFocus({ researchRef: { recordId: "res-other" } });
    await flush(2);
    post.resolve(okProposal());
    await flush(3);
    assertEq(byRole(root, "notice").hidden, true, "stale receipt never paints");
    assertEq(moduleState(env.host), "loading", "new selection owns the state");
    env.instance.dispose();
  });

  await test("research-record: proposal submit posts the inspected body and shows the receipt", async () => {
    const pending = deferred();
    const env = await setup({
      binding: PROPOSAL_BINDING,
      request: null,
      client: { transport: { request: (pathArg, opts) => { envCalls.push({ path: pathArg, opts }); return pending.promise; } } },
    });
    const envCalls = [];
    const root = env.host.children[0];
    setValue(byRole(root, "question"), "Which path is lower?");
    setValue(byRole(root, "claim"), "The endo path is lower.");
    assertEq(byRole(root, "target").value, "proposal", "proposal is the default target");
    byRole(root, "submit").fire("click");
    await flush(2);
    assertEq(envCalls.length, 1, "one POST");
    assertEq(envCalls[0].path, PROPOSALS);
    assertEq(envCalls[0].opts.method, "POST");
    const body = JSON.parse(envCalls[0].opts.body);
    assertEq(body.proposal.question, "Which path is lower?");
    assertEq(body.proposal.hypothesis, "The endo path is lower.");
    assertEq(body.plan.workflow, "singlepoint");
    assert(!("evidence_refs" in body), "proposal body has no evidence refs");
    assertEq(byRole(root, "submit").disabled, true, "busy disables submit");
    pending.resolve(okProposal());
    await flush();
    assertEq(byRole(root, "submit").disabled, false, "busy cleared");
    assert(textOf(byRole(root, "notice")).indexOf("prop-rr-1") !== -1, "receipt resource id");
    assert(textOf(byRole(root, "notice")).indexOf("版本 1") !== -1, "receipt revision");
    assertEq(byRole(root, "notice").getAttribute("data-kind"), "ok");
  });

  await test("research-record: a second submit while busy never sends a second POST", async () => {
    const pending = deferred();
    const calls = [];
    const env = await setup({
      binding: PROPOSAL_BINDING,
      request: null,
      client: { transport: { request: (pathArg, opts) => { calls.push({ path: pathArg, opts }); return pending.promise; } } },
    });
    const root = env.host.children[0];
    setValue(byRole(root, "question"), "Q");
    setValue(byRole(root, "claim"), "C");
    byRole(root, "submit").fire("click");
    byRole(root, "submit").fire("click");
    byRole(root, "submit").fire("click");
    await flush(2);
    assertEq(calls.length, 1, "only one in-flight submit");
    pending.resolve(okProposal());
    await flush();
  });

  await test("research-record: submit validation is readable and sends nothing", async () => {
    const env = await setup({ binding: PROPOSAL_BINDING });
    const root = env.host.children[0];
    byRole(root, "submit").fire("click");
    await flush();
    assertEq(env.calls.length, 0, "invalid form sends nothing");
    const errors = textOf(byRole(root, "form-errors"));
    assert(errors.indexOf("请填写研究问题") !== -1, "question error");
    assert(errors.indexOf("请填写主张") !== -1, "claim error");
    setValue(byRole(root, "question"), "Q");
    setValue(byRole(root, "claim"), "C");
    // A complete proposal binding lets the same form post a valid body; remount
    // with a deferred transport so the assertion is about that new submission.
    const pending = deferred();
    env.instance.dispose();
    // remount with a deferred transport for a clean assertion
    const dom = makeDom();
    const sandbox = makeSandbox({});
    const entry = loadModule(sandbox);
    const host = dom.makeEl("div");
    const sent = [];
    const instance = entry.factory({
      doc: dom.document,
      binding: PROPOSAL_BINDING,
      request: (pathArg, opts) => { sent.push({ path: pathArg, opts }); return pending.promise; },
    });
    await instance.mount(host, { context: null });
    await flush();
    const root2 = host.children[0];
    setValue(byRole(root2, "question"), "Q");
    setValue(byRole(root2, "claim"), "C");
    byRole(root2, "submit").fire("click");
    await flush(2);
    assertEq(sent.length, 1, "valid form posts");
    assertEq(sent[0].path, PROPOSALS);
    pending.resolve(okProposal());
    await flush();
    instance.dispose();
  });

  await test("research-record: record submit posts the inspected body with the server evidence view", async () => {
    const env = await setup({
      binding: RECORD_BINDING,
      responder: (pathArg, opts) => (opts.method === "POST" ? okRecord("res-rr-1") : evidenceView()),
      context: makeContext({ projectId: "proj-acp2", researchId: "res-rr-1" }),
      now: () => Date.parse("2026-10-31T00:00:00Z"),
    });
    const root = env.host.children[0];
    await flush();
    assertEq(moduleState(env.host), "ready");
    assertEq(byRole(root, "target").value, "record", "record binding selects record target");
    byRole(root, "use-projection-refs").fire("click");
    const rows = findAll(root, "data-ref-field", "sha256");
    assert(rows.length === 1 && rows[0].value === DIGEST("6"), "projection ref copied into the editor");
    setValue(byRole(root, "question"), "Is the barrier lower?");
    setValue(byRole(root, "claim"), "Yes.");
    setValue(byRole(root, "decision"), "accept", "change");
    const before = env.calls.length;
    byRole(root, "submit").fire("click");
    await flush();
    const post = env.calls.slice(before).find((call) => call.opts.method === "POST");
    assert(post, "POST sent");
    assertEq(post.path, RECORDS);
    const body = JSON.parse(post.opts.body);
    assertEq(body.decision, "accept");
    assertEq(body.proposal_ref, "prop-rr-1");
    assertEq(body.approval_ref, "ap-rr-1");
    assertEq(body.plan_definition_digest, DIGEST("3"));
    assertEq(body.task_refs.length, 1);
    assertEq(body.evidence_refs.length, 1);
    assertEq(body.evidence_refs[0].sha256, DIGEST("6"));
    assert(body.evidence_view && body.evidence_view.schema === "acp2.runtime.evidence-projection.v1",
      "raw server evidence view attached");
    assertEq(body.evidence_view.projection_digest, DIGEST("8"));
    assertEq(body.human_edits.length, 2);
    assertEq(body.human_edits[0].edited_at, "2026-10-31T00:00:00.000Z");
    assert(!("question" in body) && !("hypothesis" in body), "record body has no question/hypothesis");
    assert(textOf(byRole(root, "notice")).indexOf("res-rr-1") !== -1, "record receipt");
  });

  await test("research-record: record 409 is readable, refreshes evidence, never replays", async () => {
    let posts = 0;
    const env = await setup({
      binding: RECORD_BINDING,
      responder: (pathArg, opts) => {
        if ((opts.method || "GET") === "POST") {
          posts += 1;
          throw httpError(409, {
            code: "revision_conflict",
            message: "the evidence report digest differs from the record",
            params: { rule: "evidence_digest_mismatch" },
          });
        }
        return evidenceView();
      },
      context: makeContext({ projectId: "proj-acp2", researchId: "res-rr-1" }),
    });
    const root = env.host.children[0];
    await flush();
    byRole(root, "use-projection-refs").fire("click");
    setValue(byRole(root, "question"), "Q");
    setValue(byRole(root, "decision"), "accept", "change");
    byRole(root, "submit").fire("click");
    await flush();
    assertEq(posts, 1, "exactly one POST (no replay)");
    const notice = byRole(root, "notice");
    assertEq(notice.getAttribute("data-kind"), "conflict");
    assert(textOf(notice).indexOf("evidence_digest_mismatch") !== -1, "rule readable");
    assert(textOf(notice).indexOf("revision_conflict") !== -1, "code readable");
    // the evidence re-read happened (GET after the conflict)
    assert(env.calls.some((call) => (call.opts.method || "GET") === "GET" && call.path.indexOf("/evidence") !== -1),
      "evidence refreshed");
  });

  await test("research-record: 403 submit is readable and does not leak a raw stack", async () => {
    const env = await setup({
      binding: PROPOSAL_BINDING,
      responder: () => { throw httpError(403, { code: "access_denied", message: "no write access" }); },
    });
    const root = env.host.children[0];
    setValue(byRole(root, "question"), "Q");
    setValue(byRole(root, "claim"), "C");
    byRole(root, "submit").fire("click");
    await flush();
    const notice = textOf(byRole(root, "notice"));
    assert(notice.indexOf("无权") !== -1, "access copy");
    assert(notice.indexOf("Error:") === -1 && notice.indexOf("at ") === -1, "no raw stack");
  });

  await test("research-record: binding JSON applies, selects record target and rejects invalid JSON", async () => {
    const env = await setup({ binding: null });
    const root = env.host.children[0];
    const json = byRole(root, "binding-json");
    json.value = "{not json";
    byRole(root, "apply-binding").fire("click");
    assertEq(byRole(root, "notice").getAttribute("data-kind"), "error");
    assert(textOf(byRole(root, "notice")).indexOf("绑定 JSON 无效") !== -1, "invalid JSON readable");
    json.value = JSON.stringify(RECORD_BINDING);
    byRole(root, "apply-binding").fire("click");
    assertEq(byRole(root, "notice").getAttribute("data-kind"), "ok");
    assertEq(byRole(root, "target").value, "record", "record binding selected record target");
    const bindingText = textOf(byRole(root, "binding-grid"));
    assert(bindingText.indexOf("prop-rr-1") !== -1 && bindingText.indexOf("ap-rr-1") !== -1,
      "binding summary updated");
    json.value = "[]";
    byRole(root, "apply-binding").fire("click");
    assert(textOf(byRole(root, "notice")).indexOf("必须是对象") !== -1, "non-object rejected");
  });

  await test("research-record: i18n keys emitted by the DOM resolve through the inline dictionary", async () => {
    const env = await setup({
      binding: RECORD_BINDING,
      responder: () => evidenceView(),
      context: makeContext({ projectId: "proj-acp2", researchId: "res-rr-1" }),
    });
    const root = env.host.children[0];
    const keyed = walk(root).filter((node) => node.getAttribute && node.getAttribute("data-i18n"));
    assert(keyed.length >= 10, "module emits i18n keys");
    keyed.forEach((node) => {
      const key = node.getAttribute("data-i18n");
      assert(node.textContent !== key, "translated: " + key);
    });
    const placeholders = walk(root).filter((node) => node.getAttribute && node.getAttribute("data-i18n-ph"));
    assert(placeholders.length >= 2, "placeholders keyed");
    placeholders.forEach((node) => {
      const key = node.getAttribute("data-i18n-ph");
      assert(node.getAttribute("placeholder") !== key, "placeholder translated: " + key);
    });
    env.dom.document.dispatch("acp:langchange");
    await flush();
    assertEq(moduleState(env.host), "ready", "language change re-renders in place");
  });

  await test("research-record: CSS is module-scoped and token-driven", async () => {
    const css = fs.readFileSync(CSS_ABS, "utf8");
    assert(css.indexOf(".acp-rr") !== -1, "module class prefix");
    assert(!/^\s*(:root|html|body)\b/m.test(css), "no global selector override");
    assert(css.indexOf("@import") === -1, "no imports");
    assert(css.indexOf("var(--") !== -1, "uses shell tokens");
    assert(css.indexOf("[hidden]") !== -1, "hidden contract");
    assert(css.indexOf("@media") !== -1, "narrow-screen rule");
  });

  await test("research-record: README documents the three task ids, endpoints and integration gap", async () => {
    const readme = fs.readFileSync(README_ABS, "utf8");
    ["A-54", "A-55", "A-56", "/api/v2/research/proposals", "/api/v2/research/records",
      "/evidence", "deps.binding", "i18n"].forEach((needle) => {
      assert(readme.indexOf(needle) !== -1, "README mentions " + needle);
    });
  });
};
