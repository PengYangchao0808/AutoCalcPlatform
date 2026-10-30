/* UI-03A: agent-workspace read-only first slice (three cards).
 *
 * Runs the REAL `js/modules/agent-workspace/module.js` in the zero-dependency
 * vm harness with a fully mocked ACPClient — the sandbox `fetch` throws, so
 * any accidental network reach is an immediate failure.
 *
 * Pinned contracts:
 *   1. mount without researchRef/taskRef renders the explicit empty state;
 *   2. the summary card renders record fields (decision / approval_ref /
 *      plan digest / related tasks) from a mocked fetchRecord;
 *   3. the plan timeline maps the four node states (candidate / pending /
 *      submitted / result) from proposal decision + task status only —
 *      never inferred;
 *   4. a taskRef context update reloads the execution segment, and a late
 *      response from the superseded taskRef is discarded (generation guard);
 *   5. a view-model that fails validateViewModel renders the explicit
 *      error state, never a half-rendered evidence card;
 *   6. a `not_evaluated` claim never renders as pass/green;
 *   7. model explanation (.acp-model-note) and verified facts
 *      (.acp-verified-fact) stay structurally and visually separated;
 *   8. dispose() is idempotent, clears the host, and late writes are
 *      dropped;
 *   9. the __ACP_NEXT_AGENT_FIXTURE__ seam replaces network reads and marks
 *      the root data-acp-fixture="true" with a visible demo badge;
 *  10. navigation buttons carry data-cmd only (openTask/openStructure/
 *      openEvidence/retry) — no write/approval command exists in the DOM.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const DIGEST = (ch) => "sha256:" + ch.repeat(64);
  const PRODUCER = "0123456789abcdef0123456789abcdef01234567";

  function makeHost(id) {
    const listeners = {};
    return {
      id,
      innerHTML: "",
      addEventListener(type, handler) {
        (listeners[type] = listeners[type] || []).push(handler);
      },
      removeEventListener(type, handler) {
        const list = listeners[type] || [];
        const at = list.indexOf(handler);
        if (at !== -1) list.splice(at, 1);
      },
      contains() { return true; },
      listenerCount(type) { return (listeners[type] || []).length; },
    };
  }

  function setup(client) {
    const sandbox = makeSandbox({
      fetch: function () { throw new Error("fetch is forbidden in agent-workspace tests"); },
    });
    loadScript("js/workbench/contracts/experience_contract.js", sandbox);
    loadScript("js/modules/agent-workspace/module.js", sandbox);
    const entry = sandbox.window.ACPNextModules["agent-workspace"];
    if (!entry || typeof entry.factory !== "function") throw new Error("module not registered");
    return { sandbox, entry, instance: entry.factory({ client, commands: {} }) };
  }

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  function flush() {
    return new Promise((r) => setTimeout(r, 0));
  }

  function recordBody(overrides) {
    const body = {
      research_id: "res-da-1",
      project_id: "proj-a",
      round: 2,
      decision: "accept",
      plan_definition_digest: DIGEST("2"),
      step_identity_refs: [],
      task_refs: [{ project_id: "proj-a", job_id: "job-1", attempt: 1, release_id: "release-1" }],
      evidence_refs: [{
        evidence_id: "ev-1", kind: "result_manifest",
        sha256: DIGEST("5"), relative_path: "RESULT/result_manifest.json",
      }],
      missing_validation: [],
      human_edits: [],
      approval_ref: "approval-1",
      created_at: "2026-10-22T12:30:00Z",
      record_revision: 3,
    };
    return Object.assign(body, overrides || {});
  }

  function proposalBody(overrides) {
    const body = {
      proposal_id: "prop-da-001",
      project_id: "proj-a",
      question_ref: "question-1",
      question_revision: 0,
      reaction_ref: "reaction-1",
      reaction_revision: 0,
      hypothesis: "The endo path is lower in free energy.",
      recipe_digest: DIGEST("1"),
      plan_definition_digest: DIGEST("2"),
      science: {
        capability_steps: [
          { step_index: 0, capability: "optimize.ts", tool: "acp_submit", version: "1.0.0",
            parameters: { optimizer: "r2SCAN-3c" } },
        ],
        initial_inputs: [{ input_id: "input-ts-guess", sha256: DIGEST("a"), format: "xyz",
          role: "transition_state_guess" }],
        method: "r2SCAN-3c", mapping: "mapping-1", charge: 0, multiplicity: 1,
        allowed_substitutions: [],
      },
      budget: { model_requests: 1, input_tokens: 1, output_tokens: 1, task_refs: 1,
        recipe_steps: 1, qc_invocations: 1, wall_seconds: 1, queue_seconds: 0 },
      approval_ref: null,
      allowed_change_scope: { mode: "none", finite_substitutions: [] },
      round: 1,
      decision: "proposed",
      created_at: "2026-10-22T12:00:00Z",
      human_edits: [],
    };
    return Object.assign(body, overrides || {});
  }

  function statusBody(overrides) {
    return Object.assign({
      task_ref: { job_id: "job-1", attempt: 1, task_id: null },
      status: "RUNNING",
      revision: 4,
    }, overrides || {});
  }

  function viewBody(overrides) {
    const body = {
      snapshot_revision: 1,
      project_id: "proj-a",
      task_ref: { job_id: "job-1", attempt: 1, task_id: null },
      manifest_generation: 2,
      artifacts: [{
        artifact_id: "job-1/RESULT/optimized.ts.xyz",
        role: "optimized_transition_state",
        relative_path: "RESULT/optimized.ts.xyz",
        availability: "readable",
        generation: 2,
        sha256: null,
        size_bytes: 128,
        reason: null,
      }],
      claims: [{
        claim_id: "claim-1",
        statement: "Exactly one imaginary mode.",
        status: "supported",
        evidence_refs: ["ev-1"],
        reason: "validator passed",
      }],
      clarifications: [],
      provenance: {
        source: "results.manifest.read",
        producer_revision: PRODUCER,
        produced_at: "2026-10-22T12:20:00Z",
        digest: DIGEST("0"),
      },
      read_only: true,
      not_authoritative_for_science: true,
    };
    return Object.assign(body, overrides || {});
  }

  const SCOPE_CTX = { projectId: "proj-a", permission: "read" };

  function readClient(overrides) {
    return Object.assign({
      research: {
        fetchRecord: () => Promise.resolve(recordBody()),
        fetchProposal: () => Promise.resolve(proposalBody()),
      },
      science: {
        fetchTaskStatus: () => Promise.resolve(statusBody()),
        fetchView: () => Promise.resolve(viewBody()),
      },
    }, overrides || {});
  }

  await test("descriptor matches the frozen module contract", () => {
    const { entry } = setup(null);
    assertEq(entry.descriptor.id, "agent-workspace");
    assertEq(entry.descriptor.interfaceVersion, 1);
    assertEq(entry.descriptor.capabilities.provides,
      ["research.summary", "research.plan", "research.evidence"]);
    assertEq(entry.descriptor.supportedInstancing, "single");
    assertEq(entry.descriptor.error.titleKey, "agent.workspace.errorTitle");
  });

  await test("mount without researchRef/taskRef renders the explicit empty state", async () => {
    const { instance } = setup(readClient());
    const host = makeHost("h-empty");
    await instance.mount(host, SCOPE_CTX);
    const html = String(host.innerHTML);
    assert(html.indexOf('data-acp-module-state="empty"') !== -1, "root empty state");
    assert(html.indexOf("暂无研究记录") !== -1, "empty title rendered");
    assert(html.indexOf("acp-card-summary") === -1, "no cards in the whole-panel empty state");
    // connection semantics: no DSH/model session is a NORMAL badge, never a progress illusion
    assert(html.indexOf("只读 · 未连接模型") !== -1, "readonly badge shown");
    assert(html.indexOf("data-acp-fixture=\"false\"") !== -1, "no fixture marker");
    instance.dispose();
  });

  await test("summary card renders record fields from a mocked fetchRecord", async () => {
    const { instance } = setup(readClient());
    const host = makeHost("h-summary");
    await instance.mount(host, Object.assign({}, SCOPE_CTX, {
      researchRef: { recordId: "res-da-1" },
    }));
    const html = String(host.innerHTML);
    assert(html.indexOf("acp-card-summary") !== -1, "summary card present");
    assert(html.indexOf("accept") !== -1, "decision rendered");
    assert(html.indexOf("approval-1") !== -1, "approval_ref rendered");
    assert(html.indexOf("record r3") !== -1, "record_revision rendered");
    assert(html.indexOf("job-1 · attempt 1") !== -1, "related task chip rendered");
    assert(html.indexOf("打开任务") !== -1, "openTask navigation button rendered");
    assert(html.indexOf('data-cmd="openTask"') !== -1, "openTask carries data-cmd");
    // a record has no goal/input fields — they render as explicit missing, never invented
    assert(html.indexOf("—") !== -1, "missing fields render an explicit dash");
    instance.dispose();
  });

  await test("timeline maps the four node states from proposal decision + task status only", async () => {
    // candidate: proposed proposal without approval_ref
    const a = setup(readClient({
      research: {
        fetchRecord: () => Promise.reject(new Error("unused")),
        fetchProposal: () => Promise.resolve(proposalBody()),
      },
    }));
    const hostA = makeHost("h-cand");
    await a.instance.mount(hostA, Object.assign({}, SCOPE_CTX, {
      researchRef: { proposalId: "prop-da-001" },
    }));
    const htmlA = String(hostA.innerHTML);
    assert(htmlA.indexOf('data-state="is-candidate"') !== -1,
      "proposed proposal maps to is-candidate");
    a.instance.dispose();

    // pending: proposed proposal carrying an approval_ref (awaiting service approval)
    const b = setup(readClient({
      research: {
        fetchRecord: () => Promise.reject(new Error("unused")),
        fetchProposal: () => Promise.resolve(proposalBody({ approval_ref: "approval-1" })),
      },
    }));
    const hostB = makeHost("h-pending");
    await b.instance.mount(hostB, Object.assign({}, SCOPE_CTX, {
      researchRef: { proposalId: "prop-da-001" },
    }));
    const htmlB = String(hostB.innerHTML);
    assert(htmlB.indexOf('data-state="is-pending"') !== -1,
      "awaiting-approval proposal maps to is-pending");
    b.instance.dispose();

    // result + tone: rejected proposal → is-result fail; COMPLETED task → is-result pass
    const c = setup(readClient({
      research: {
        fetchRecord: () => Promise.reject(new Error("unused")),
        fetchProposal: () => Promise.resolve(proposalBody({ decision: "rejected" })),
      },
      science: {
        fetchTaskStatus: () => Promise.resolve(statusBody({ status: "COMPLETED" })),
        fetchView: () => Promise.resolve(viewBody()),
      },
    }));
    const hostC = makeHost("h-result");
    await c.instance.mount(hostC, Object.assign({}, SCOPE_CTX, {
      researchRef: { proposalId: "prop-da-001" },
      taskRef: { jobId: "job-1", attempt: 1 },
    }));
    const htmlC = String(hostC.innerHTML);
    assert(htmlC.indexOf('class="acp-timeline-item is-result" data-state="is-result" data-tone="fail"') !== -1,
      "rejected proposal maps to is-result fail tone");
    assert(htmlC.indexOf('class="acp-timeline-item is-result" data-state="is-result" data-tone="pass"') !== -1,
      "COMPLETED task maps to is-result pass tone");
    c.instance.dispose();

    // submitted: RUNNING task → is-submitted
    const d = setup(readClient());
    const hostD = makeHost("h-submitted");
    await d.instance.mount(hostD, Object.assign({}, SCOPE_CTX, {
      taskRef: { jobId: "job-1", attempt: 1 },
    }));
    const htmlD = String(hostD.innerHTML);
    assert(htmlD.indexOf('data-state="is-submitted"') !== -1,
      "RUNNING task maps to is-submitted");
    d.instance.dispose();

    // tool-call timeline is a disabled placeholder, never fake data
    const toolsMarker = 'data-acp-tools-placeholder="true"';
    [htmlA, htmlB, htmlC, htmlD].forEach((html) => {
      assert(html.indexOf(toolsMarker) !== -1, "tools placeholder present");
    });
  });

  await test("taskRef update refreshes status and a late superseded response is discarded", async () => {
    const first = deferred(), second = deferred();
    let call = 0;
    const { instance } = setup(readClient({
      science: {
        fetchTaskStatus: (ref) => {
          call += 1;
          assertEq(ref.jobId, "job-" + call, "calls follow context order");
          return call === 1 ? first.promise : second.promise;
        },
        // the view must echo the REQUESTED taskRef (server-authoritative
        // match) — a fixed job-1 body for a job-2 request would be a lie.
        fetchView: (ref) => Promise.resolve(viewBody({
          task_ref: { job_id: ref.jobId, attempt: ref.attempt, task_id: null },
        })),
      },
    }));
    const host = makeHost("h-stale");
    // mount starts the job-1 status fetch, which stays pending on purpose —
    // never await the mount promise here (it includes the pending fetch).
    instance.mount(host, Object.assign({}, SCOPE_CTX, {
      taskRef: { jobId: "job-1", attempt: 1 },
    }));
    // taskRef moves to job-2 before job-1 ever answered
    const update = instance.updateContext(Object.assign({}, SCOPE_CTX, {
      taskRef: { jobId: "job-2", attempt: 1 },
    }));
    second.resolve(statusBody({
      task_ref: { job_id: "job-2", attempt: 1, task_id: null }, status: "RUNNING",
    }));
    await update;
    await flush();
    let html = String(host.innerHTML);
    assert(html.indexOf("job-2") !== -1, "new taskRef rendered");
    assert(html.indexOf("job-1 · attempt") === -1, "old taskRef execution segment not rendered");
    // late response for the superseded taskRef arrives and must be dropped
    first.resolve(statusBody({
      task_ref: { job_id: "job-1", attempt: 1, task_id: null }, status: "FAILED",
    }));
    await flush();
    html = String(host.innerHTML);
    assert(html.indexOf("FAILED") === -1, "late superseded status discarded");
    assert(html.indexOf("job-2") !== -1, "current taskRef kept");
    instance.dispose();
  });

  await test("evidence card refuses a view-model that fails validateViewModel", async () => {
    const { instance } = setup(readClient({
      science: {
        fetchTaskStatus: () => Promise.resolve(statusBody()),
        // a fifth availability state + read_only=false — contract violations
        fetchView: () => Promise.resolve(viewBody({
          artifacts: [{
            artifact_id: "a1", role: "r", relative_path: "x.xyz",
            availability: "bogus-state", generation: 1, sha256: null, size_bytes: null, reason: null,
          }],
          read_only: false,
        })),
      },
    }));
    const host = makeHost("h-invalid");
    await instance.mount(host, Object.assign({}, SCOPE_CTX, {
      taskRef: { jobId: "job-1", attempt: 1 },
    }));
    await flush();
    const html = String(host.innerHTML);
    assert(html.indexOf("invalid_data") !== -1, "explicit contract-rejection error surfaced");
    assert(html.indexOf("acp-verified-fact") === -1, "no half-rendered verified facts");
    assert(html.indexOf("claim-1") === -1, "no claim rows from the rejected body");
    assert(html.indexOf('data-cmd="retry"') !== -1, "retry action offered");
    instance.dispose();
  });

  await test("a not_evaluated claim never renders as pass", async () => {
    const { instance } = setup(readClient({
      science: {
        fetchTaskStatus: () => Promise.resolve(statusBody()),
        fetchView: () => Promise.resolve(viewBody({
          claims: [{
            claim_id: "claim-ne",
            statement: "Free energy barrier evaluated.",
            status: "not_evaluated",
            evidence_refs: [],
            reason: "frequency missing",
          }],
        })),
      },
    }));
    const host = makeHost("h-ne");
    await instance.mount(host, Object.assign({}, SCOPE_CTX, {
      taskRef: { jobId: "job-1", attempt: 1 },
    }));
    await flush();
    const html = String(host.innerHTML);
    assert(html.indexOf('data-claim-status="not_evaluated"') !== -1, "claim row present");
    assert(html.indexOf("acp-tag--missing") !== -1, "rendered as missing chip");
    assert(html.indexOf("acp-tag--pass") === -1, "never a pass chip");
    assert(html.indexOf("acp-verified-fact") === -1, "not inside a verified-fact container");
    instance.dispose();
  });

  await test("model explanation and verified facts stay structurally separated", async () => {
    const sandboxFixture = {
      record: recordBody(),
      proposal: null,
      taskStatus: statusBody({ status: "COMPLETED" }),
      view: viewBody(),
      modelNote: "建议下一步做 IRC 验证连通性。",
    };
    const { sandbox, instance } = setup(null);
    sandbox.window.__ACP_NEXT_AGENT_FIXTURE__ = sandboxFixture;
    const host = makeHost("h-split");
    await instance.mount(host, Object.assign({}, SCOPE_CTX, {
      researchRef: { recordId: "res-da-1" },
      taskRef: { jobId: "job-1", attempt: 1 },
    }));
    await flush();
    const html = String(host.innerHTML);
    // model note segment: neutral dashed container + model chip only
    const noteAt = html.indexOf('class="acp-model-note"');
    assert(noteAt !== -1, "model note container present");
    const noteSeg = html.slice(noteAt, html.indexOf("</div>", noteAt));
    assert(noteSeg.indexOf("acp-tag--model") !== -1, "model chip inside model note");
    ["acp-tag--pass", "acp-tag--fail", "acp-tag--warn", "acp-verified-fact", "acp-i-shield"]
      .forEach((token) => {
        assert(noteSeg.indexOf(token) === -1, "no verification styling inside model note: " + token);
      });
    // verified fact segment: semantic color + shield, never a model chip
    const factAt = html.indexOf('class="acp-verified-fact is-pass"');
    assert(factAt !== -1, "verified fact container present");
    // slice the container body: from its class to the closing of the reason <p>
    const factSeg = html.slice(factAt, html.indexOf('data-claim-id', factAt));
    const factBody = html.slice(html.indexOf('data-claim-id', factAt),
      html.indexOf('data-claim-id', factAt) + 600);
    assert(factSeg.indexOf("acp-verified-fact") !== -1, "verified fact class in segment");
    assert(factBody.indexOf("acp-i-shield") !== -1, "shield mark inside verified fact");
    assert(factBody.indexOf("acp-tag--model") === -1, "no model chip inside verified fact");
    instance.dispose();
  });

  await test("dispose is idempotent, clears the host, and drops late writes", async () => {
    const pending = deferred();
    const { instance } = setup(readClient({
      science: {
        fetchTaskStatus: () => pending.promise,
        fetchView: () => Promise.resolve(viewBody()),
      },
    }));
    const host = makeHost("h-dispose");
    // never await the mount promise: it includes the deliberately pending
    // status fetch (an unawaited pending promise would exit node silently).
    instance.mount(host, Object.assign({}, SCOPE_CTX, {
      taskRef: { jobId: "job-1", attempt: 1 },
    }));
    assert(host.listenerCount("click") === 1, "one delegated listener");
    instance.dispose();
    instance.dispose(); // idempotent
    assertEq(host.innerHTML, "", "host cleared");
    assertEq(host.listenerCount("click"), 0, "listener removed");
    pending.resolve(statusBody()); // late write after dispose — must not throw
    await flush();
    assertEq(host.innerHTML, "", "late response dropped after dispose");
  });

  await test("fixture seam replaces network reads and marks the root as fixture", async () => {
    const { sandbox, instance } = setup(null); // no client at all
    sandbox.window.__ACP_NEXT_AGENT_FIXTURE__ = {
      record: recordBody(),
      proposal: null,
      taskStatus: statusBody(),
      view: viewBody(),
    };
    const host = makeHost("h-fixture");
    await instance.mount(host, Object.assign({}, SCOPE_CTX, {
      researchRef: { recordId: "res-da-1" },
      taskRef: { jobId: "job-1", attempt: 1 },
    }));
    await flush();
    const html = String(host.innerHTML);
    assert(html.indexOf('data-acp-fixture="true"') !== -1, "fixture marker on root");
    assert(html.indexOf('data-acp-demo-badge="true"') !== -1, "visible demo badge");
    assert(html.indexOf("演示数据（fixture）") !== -1, "demo badge copy");
    assert(html.indexOf("accept") !== -1, "summary rendered from fixture record");
    assert(html.indexOf("acp-card-evidence") !== -1, "evidence card rendered from fixture view");
    instance.dispose();
  });

  await test("no write or approval command exists anywhere in the DOM", async () => {
    const { instance } = setup(readClient());
    const host = makeHost("h-readonly");
    await instance.mount(host, Object.assign({}, SCOPE_CTX, {
      researchRef: { recordId: "res-da-1" },
      taskRef: { jobId: "job-1", attempt: 1 },
    }));
    await flush();
    const html = String(host.innerHTML);
    const allowed = ['data-cmd="openTask"', 'data-cmd="openStructure"',
      'data-cmd="openEvidence"', 'data-cmd="retry"'];
    const cmdRe = /data-cmd="([^"]+)"/g;
    let match;
    const seen = [];
    while ((match = cmdRe.exec(html)) !== null) seen.push(match[1]);
    assert(seen.length > 0, "navigation buttons exist");
    seen.forEach((cmd) => {
      assert(allowed.indexOf('data-cmd="' + cmd + '"') !== -1, "unexpected command: " + cmd);
    });
    // no write/approval verbs as commands; scan data-cmd values only —
    // a raw substring scan would collide with the legitimate
    // acp-tag--submitted / is-submitted timeline state classes.
    ["approve", "submit", "reject", "decide", "edit", "cancel", "purge"].forEach((verb) => {
      assert(seen.indexOf(verb) === -1, "no write command allowed: " + verb);
    });
    instance.dispose();
  });
};
